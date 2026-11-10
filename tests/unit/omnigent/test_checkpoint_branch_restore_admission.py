"""Branch restoration delegates only validated saved-work inputs."""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.checkpoint_branch_turn_execution import (
    CheckpointBranchTurnExecutionOwner,
    CheckpointBranchTurnLaunchError,
)
from moonmind.schemas.temporal_models import StepExecutionCheckpointModel
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from tests.unit.omnigent import (
    test_checkpoint_branch_instruction_authority as branch_fixtures,
)
from tests.unit.omnigent.test_checkpoint_branch_turn_execution import (
    _valid_source_checkpoint,
)

# Register the existing durable graph fixtures in this module's pytest scope.
auth_mode = branch_fixtures.auth_mode
instruction_authority = branch_fixtures.instruction_authority


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "digest", "quarantine", "foreign"])
async def test_restore_admission_delegates_verified_checkpoint_closure(tmp_path, fault):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    producer = str(uuid4())
    destination = ArtifactAccessContext.for_execution("branch-destination")
    source_access = ArtifactAccessContext.for_execution("source-workflow")
    try:
        async with sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "blobs"),
            )

            async def put(payload, kind="application/json", workflow="source-workflow"):
                row, _ = await service.create(
                    principal=producer,
                    content_type=kind,
                    link={
                        "namespace": "default",
                        "workflow_id": workflow,
                        "run_id": "source-run",
                        "link_type": "output.test",
                    },
                )
                await service.write_complete(
                    artifact_id=row.artifact_id,
                    principal=producer,
                    payload=payload,
                    content_type=kind,
                )
                return (
                    row,
                    "artifact://" + row.artifact_id,
                    "sha256:" + hashlib.sha256(payload).hexdigest(),
                )

            archive, archive_ref, archive_digest = await put(
                b"saved Git bytes",
                workflow="foreign" if fault == "foreign" else "source-workflow",
            )
            head, head_ref, head_digest = await put(b"head")
            unrelated, _, _ = await put(b"unrelated")
            descriptor = json.dumps(
                {
                    "source": {"workflowId": "source-workflow", "runId": "source-run"},
                    "workspace": {
                        "archiveRef": archive_ref,
                        "archiveDigest": archive_digest,
                    },
                }
            ).encode()
            saved, saved_ref, saved_digest = await put(
                descriptor,
                "application/vnd.moonmind.step-execution-checkpoint+json;version=1",
            )
            checkpoint = StepExecutionCheckpointModel.model_validate_json(
                _valid_source_checkpoint()[0]
            )
            checkpoint.omnigent = checkpoint.omnigent.model_copy(
                update={
                    "external_state_ref": head_ref,
                    "external_state_digest": head_digest,
                    "head_ref": head_ref,
                    "head_digest": head_digest,
                    "workspace_checkpoint_ref": saved_ref,
                    "context_refs": [saved_ref],
                    "workspace_checkpoint_digest": (
                        "sha256:" + "0" * 64 if fault == "digest" else saved_digest
                    ),
                }
            )
            if fault == "quarantine":
                archive.metadata_json = {"quarantine": True}
                await session.commit()
            owner = CheckpointBranchTurnExecutionOwner(
                session,
                principal=producer,
                access=source_access,
                artifact_service=service,
                client=SimpleNamespace(),
            )
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.read(artifact_id=saved.artifact_id, access=destination)
            if fault:
                expected = {
                    "digest": CheckpointBranchTurnLaunchError,
                    "quarantine": TemporalArtifactStateError,
                    "foreign": TemporalArtifactAuthorizationError,
                }[fault]
                with pytest.raises(expected):
                    await owner._admit_restore_inputs(
                        checkpoint=checkpoint,
                        destination_workflow_id="branch-destination",
                        namespace="default",
                    )
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await service.read(
                        artifact_id=saved.artifact_id, access=destination
                    )
            else:
                for existing in (None, service):
                    await owner._admit_restore_inputs(
                        checkpoint=checkpoint,
                        destination_workflow_id="branch-destination",
                        namespace="default",
                        artifact_service=existing,
                    )
                for row in (archive, head, saved):
                    await service.read(artifact_id=row.artifact_id, access=destination)
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await service.read(
                        artifact_id=unrelated.artifact_id, access=destination
                    )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_claimed_turn_admits_only_its_recorded_launch_inputs(
    instruction_authority,
):
    from api_service.db.models import WorkflowCheckpointBranchTurn

    fixture = instruction_authority
    context = await fixture.put(b'{"kind":"context"}', linked=True)
    checkpoint = await fixture.put(b'{"kind":"checkpoint"}', linked=True)
    unrelated = await fixture.put(b'{"kind":"unrelated"}', linked=True)
    request = fixture.request.model_copy(
        update={
            "input_refs": [fixture.ref, context, checkpoint, unrelated],
        }
    )
    manifest = await fixture.put(
        json.dumps(
            {
                "agentExecutionRequest": request.model_dump(by_alias=True, mode="json"),
            }
        ).encode(),
        linked=True,
    )
    async with fixture.sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn")
        turn.context_bundle_ref = context
        turn.source_checkpoint_ref = checkpoint
        turn.step_execution_manifest_ref = manifest
        await session.commit()
    client = await branch_fixtures._dispatch_claimed(fixture)
    client.start_workflow.assert_awaited_once()
    destination = ArtifactAccessContext.for_execution(fixture.identity.workflow_id)
    async with fixture.sessions() as session:
        service = fixture.artifacts(session)
        for ref in (fixture.ref, context, checkpoint):
            await service.read(
                artifact_id=ref.removeprefix("artifact://"), access=destination
            )
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=unrelated.removeprefix("artifact://"), access=destination
            )


@pytest.mark.asyncio
async def test_retry_rejects_corrupt_destination_before_restore_grants(
    instruction_authority,
):
    from unittest.mock import AsyncMock

    from api_service.db.models import (
        TemporalExecutionCanonicalRecord,
        WorkflowCheckpointBranch,
        WorkflowCheckpointBranchTurn,
    )
    from moonmind.omnigent.control_plane.records import ControlPlaneOutcome
    from tests.artifact_access import access_for_test

    fixture = instruction_authority
    async with fixture.sessions() as session:
        branch = await session.get(WorkflowCheckpointBranch, "branch")
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn")
        source = await session.get(TemporalExecutionCanonicalRecord, "source")
        turn.diagnostics = {
            **turn.diagnostics,
            "executionWorkflowId": "unrelated-workflow",
        }
        await session.commit()
        commands = SimpleNamespace(
            claim_with_repositories=AsyncMock(
                return_value=SimpleNamespace(
                    outcome=ControlPlaneOutcome.ALREADY_APPLIED
                )
            ),
        )
        owner = CheckpointBranchTurnExecutionOwner(
            session,
            principal=fixture.producer,
            access=access_for_test(fixture.producer),
            client=SimpleNamespace(start_workflow=AsyncMock()),
            turn_command_service=commands,
        )
        owner._load_graph_authority = AsyncMock(
            return_value=(branch, turn, SimpleNamespace(), source)
        )
        owner._validate_source_authority = AsyncMock(
            return_value=(
                StepExecutionCheckpointModel.model_validate_json(
                    _valid_source_checkpoint()[0]
                ),
                SimpleNamespace(),
                {},
            )
        )
        owner._admit_restore_inputs = AsyncMock()
        with pytest.raises(CheckpointBranchTurnLaunchError, match="recorded turn"):
            await owner.launch(
                workflow_id="source",
                branch_id="branch",
                branch_turn_id="turn",
                intent={"idempotencyKey": "retry"},
            )
        owner._admit_restore_inputs.assert_not_awaited()
        owner._client.start_workflow.assert_not_awaited()
