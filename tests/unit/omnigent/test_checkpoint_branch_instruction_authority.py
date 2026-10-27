"""Only the durable branch owner admits its recorded instruction artifact."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api_service.db.models import (
    Base,
    TemporalArtifactLink,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchTurn,
)
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
from api_service.services.checkpoint_branch_turn_execution import (
    CheckpointBranchTurnExecutionOwner,
    CheckpointBranchTurnLaunchError,
    build_branch_turn_execution_identity,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.artifact_access import access_for_test

pytestmark = pytest.mark.asyncio


@pytest.fixture(params=["accounts", "oidc", "header", "disabled"])
def auth_mode(request):
    return request.param


@pytest_asyncio.fixture
async def instruction_authority(tmp_path, monkeypatch, auth_mode):
    monkeypatch.setenv("AUTH_PROVIDER", str(auth_mode or ""))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/instructions.db")
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")

    def artifacts(session):
        return TemporalArtifactService(TemporalArtifactRepository(session), store=store)

    monkeypatch.setattr(
        "api_service.services.checkpoint_branch_turn_execution.get_checkpoint_branch_artifact_service",
        artifacts,
    )
    producer = str(uuid4())
    text = b"Keep the saved implementation and fix the remaining assertion."

    async def put(payload, *, linked=False):
        async with sessions() as session:
            service = artifacts(session)
            artifact, _ = await service.create(
                principal=producer,
                content_type="application/json" if linked else "text/markdown",
                sha256=hashlib.sha256(payload).hexdigest(),
                link=(
                    {
                        "namespace": "default",
                        "workflow_id": "source",
                        "run_id": "source-run",
                        "link_type": "checkpoint_branch.turn",
                    }
                    if linked
                    else None
                ),
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal=producer,
                payload=payload,
                content_type=artifact.content_type,
            )
            return f"artifact://{artifact.artifact_id}"

    ref = await put(text)
    identity = build_branch_turn_execution_identity(
        branch_id="branch",
        branch_turn_id="turn",
        logical_step_id="implement",
        ordinal=1,
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="turn",
        idempotencyKey="launch-turn",
        instructionRef=ref,
        stepExecution={
            "workflowId": identity.workflow_id,
            "runId": identity.semantic_run_id,
            "logicalStepId": identity.logical_step_id,
            "executionOrdinal": 1,
            "stepExecutionId": identity.step_execution_id,
            "reason": "checkpoint_branch",
            "runtimeContextPolicy": "fresh_agent_run",
            "preparedInputRefs": [ref],
            "branch": {"branchId": "branch", "branchTurnId": "turn"},
        },
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": "workspace",
                "relativePath": "repo",
            }
        },
    )
    manifest_ref = await put(
        json.dumps(
            {
                "agentExecutionRequest": request.model_dump(by_alias=True, mode="json"),
            }
        ).encode(),
        linked=True,
    )
    async with sessions() as session:
        session.add(
            TemporalExecutionCanonicalRecord(
                workflow_id="source",
                run_id="source-run",
                namespace="default",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                entry="api",
            )
        )
        await session.commit()
        service = CheckpointBranchService(session)
        await service.create_branch_graph(
            {
                "branchId": "branch",
                "branchTurnId": "turn",
                "label": "Instruction authority",
                "source": {
                    "workflowId": "source",
                    "runId": "source-run",
                    "logicalStepId": "implement",
                    "sourceExecutionOrdinal": 1,
                    "checkpointBoundary": "after_execution",
                    "checkpointRef": "artifact://source/checkpoint",
                },
                "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
                "runtimeContextPolicy": "fresh_agent_run",
                "instructionRef": ref,
                "instructionDigest": "sha256:" + hashlib.sha256(text).hexdigest(),
                "idempotencyKey": "create-turn",
            }
        )
        await service.claim_turn_execution(
            workflow_id="source",
            branch_id="branch",
            branch_turn_id="turn",
            context_bundle_ref="artifact://launch/context",
            step_execution_manifest_ref=manifest_ref,
            diagnostics_ref="artifact://launch/diagnostics",
            agent_request_ref="artifact://launch/request",
            launch_idempotency_key=build_branch_turn_launch_idempotency_key(
                workflow_id="source", branch_id="branch", branch_turn_id="turn"
            ),
            created_step_execution_id=identity.step_execution_id,
            runtime_agent_run_id=identity.agent_run_workflow_id,
            execution_workflow_id=identity.workflow_id,
        )
        await session.commit()
    yield SimpleNamespace(
        sessions=sessions,
        artifacts=artifacts,
        producer=producer,
        text=text,
        ref=ref,
        request=request,
        identity=identity,
        put=put,
    )
    await engine.dispose()


async def _dispatch_claimed(fixture):
    client = SimpleNamespace(start_workflow=AsyncMock())
    async with fixture.sessions() as session:
        owner = CheckpointBranchTurnExecutionOwner(
            session,
            principal=fixture.producer,
            client=client,
            access=access_for_test(fixture.producer),
        )
        await owner._start_claimed_turn(
            branch=await session.get(WorkflowCheckpointBranch, "branch"),
            turn=await session.get(WorkflowCheckpointBranchTurn, "turn"),
            binding=SimpleNamespace(base_commit="base"),
        )
    return client


@pytest.mark.parametrize(
    "auth_mode", ["accounts", "oidc", "header", "disabled"], indirect=True
)
async def test_claimed_owner_admits_instruction_before_dispatch_and_on_retry(
    instruction_authority, auth_mode,
):
    fixture = instruction_authority
    for _ in range(2):
        client = await _dispatch_claimed(fixture)
        client.start_workflow.assert_awaited_once()
    async with fixture.sessions() as session:
        links = (
            await session.scalars(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.artifact_id
                    == fixture.ref.removeprefix("artifact://"),
                    TemporalArtifactLink.workflow_id == fixture.identity.workflow_id,
                )
            )
        ).all()
        assert len(links) == 1
        assert links[0].run_id == fixture.identity.semantic_run_id
        artifact, body = await fixture.artifacts(session).read(
            artifact_id=fixture.ref.removeprefix("artifact://"),
            access=access_for_test(
                "service:omnigent-generic-host",
                admitted_principal=f"workflow:{fixture.identity.workflow_id}",
            ),
        )
        assert body == fixture.text
        assert artifact.created_by_principal == fixture.producer
        if auth_mode != "disabled":
            for scope in (None, "workflow:unrelated-turn"):
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await fixture.artifacts(session).read(
                        artifact_id=fixture.ref.removeprefix("artifact://"),
                        access=access_for_test(
                            "service:omnigent-generic-host", admitted_principal=scope
                        ),
                    )


@pytest.mark.parametrize(
    "changed",
    [
        "workflow",
        "run",
        "logical_step",
        "step",
        "ordinal",
        "branch",
        "turn",
        "ref",
        "prepared",
        "missing_step",
        "recorded_step",
        "recorded_owner",
        "recorded_child",
        "digest",
        "artifact_digest",
        "restricted",
        "quarantined",
    ],
)
async def test_admission_rejects_conflicting_recorded_authority_before_grant(
    instruction_authority,
    changed,
    monkeypatch,
):
    from api_service.db.models import TemporalArtifact, TemporalArtifactRedactionLevel

    fixture = instruction_authority
    request = fixture.request.model_copy(deep=True)
    step = request.step_execution
    assert step is not None
    updates = {
        "workflow": ("workflow_id", "foreign-workflow"),
        "run": ("run_id", "foreign-run"),
        "logical_step": ("logical_step_id", "foreign-step"),
        "step": ("step_execution_id", "foreign-step-execution"),
        "ordinal": ("execution_ordinal", 2),
        "prepared": ("prepared_input_refs", []),
    }
    if changed in updates:
        setattr(step, *updates[changed])
    elif changed == "branch":
        step.branch = {"branchId": "foreign-branch", "branchTurnId": "turn"}
    elif changed == "turn":
        step.branch = {"branchId": "branch", "branchTurnId": "foreign-turn"}
    elif changed == "ref":
        request.instruction_ref = await fixture.put(b"Unrelated instruction")
        step.prepared_input_refs = [request.instruction_ref]
    elif changed == "missing_step":
        request.step_execution = None
    manifest_ref = await fixture.put(
        json.dumps(
            {
                "agentExecutionRequest": request.model_dump(by_alias=True, mode="json"),
            }
        ).encode(),
        linked=True,
    )
    async with fixture.sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn")
        turn.step_execution_manifest_ref = manifest_ref
        if changed == "recorded_step":
            turn.created_step_execution_id = "foreign-created-step"
        elif changed == "recorded_owner":
            turn.diagnostics = {
                **turn.diagnostics,
                "executionWorkflowId": "foreign-owner",
            }
        elif changed == "recorded_child":
            turn.runtime_agent_run_id = "foreign-agent-run"
        elif changed == "digest":
            turn.instruction_digest = "sha256:" + "0" * 64
        artifact = await session.get(
            TemporalArtifact, fixture.ref.removeprefix("artifact://")
        )
        if changed == "artifact_digest":
            artifact.sha256 = "0" * 64
        elif changed == "restricted":
            artifact.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
        elif changed == "quarantined":
            artifact.metadata_json = {"quarantine": True}
        await session.commit()

    instruction_reads = []
    original_read = TemporalArtifactService.read

    async def read(service, **kwargs):
        if kwargs["artifact_id"] == fixture.ref.removeprefix("artifact://"):
            instruction_reads.append(kwargs)
        return await original_read(service, **kwargs)

    monkeypatch.setattr(TemporalArtifactService, "read", read)
    with pytest.raises(
        CheckpointBranchTurnLaunchError,
        match="instruction|restricted raw|quarantined|claimed turn manifest is invalid",
    ):
        await _dispatch_claimed(fixture)
    assert not instruction_reads
    async with fixture.sessions() as session:
        assert not (
            await session.scalars(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.artifact_id
                    == fixture.ref.removeprefix("artifact://")
                )
            )
        ).all()


@pytest.mark.parametrize(
    "auth_mode", ["accounts", "oidc", "header", "disabled"], indirect=True
)
async def test_retained_inflight_turn_recovers_only_recorded_instruction(
    instruction_authority, monkeypatch
):
    import dataclasses

    from temporalio.testing import ActivityEnvironment

    from moonmind.workflows.temporal.workflows import checkpoint_branch_turn as turns

    fixture = instruction_authority
    monkeypatch.setattr(turns, "async_session_maker", fixture.sessions)
    monkeypatch.setattr(
        turns, "get_checkpoint_branch_artifact_service", fixture.artifacts
    )
    environment = ActivityEnvironment()
    environment.info = dataclasses.replace(
        environment.info,
        workflow_id=fixture.identity.workflow_id,
        workflow_type=turns.WORKFLOW_NAME,
        workflow_run_id="actual-temporal-run",
    )
    payload = {
        "workflowId": "source",
        "branchId": "branch",
        "branchTurnId": "turn",
        "principal": fixture.producer,
        "sourceNamespace": "default",
        "sourceRunId": "source-run",
        "outcome": "failed",
        "agentResult": {"outputRefs": [fixture.ref]},
    }
    result = await environment.run(
        turns.persist_checkpoint_branch_turn_terminal, payload
    )
    assert (
        await environment.run(turns.persist_checkpoint_branch_turn_terminal, payload)
        == result
    )
    async with fixture.sessions() as session:
        artifact, links, pinned, _ = await fixture.artifacts(session).get_metadata(
            artifact_id=fixture.ref.removeprefix("artifact://"),
            access=access_for_test(
                "service:checkpoint-branch-turn",
                admitted_principal=f"workflow:{fixture.identity.workflow_id}",
            ),
        )
        assert pinned
        assert artifact.created_by_principal == fixture.producer
        assert (
            sum(link.workflow_id == fixture.identity.workflow_id for link in links) == 1
        )
        assert any(link.link_type == turns._RETENTION_LINK_TYPE for link in links)


@pytest.mark.parametrize(
    "auth_mode,changed",
    [
        (mode, changed)
        for mode in ("accounts", "oidc", "header", "disabled")
        for changed in (
            "unrecorded_ref", "missing_manifest", "manifest_ref", "manifest_step",
            "manifest_turn", "digest", "restricted", "quarantined", "activity_workflow",
        )
        # An unrelated ordinary output is checked by artifact read policy;
        # the durable instruction checks above it remain mode-independent.
        if changed != "unrecorded_ref" or mode != "disabled"
    ],
    indirect=["auth_mode"],
)
async def test_retained_instruction_rejects_missing_or_conflicting_authority(
    instruction_authority,
    monkeypatch,
    changed,
):
    import dataclasses

    from temporalio.testing import ActivityEnvironment

    from api_service.db.models import (
        TemporalArtifact,
        TemporalArtifactPin,
        TemporalArtifactRedactionLevel,
    )
    from moonmind.workflows.temporal.workflows import checkpoint_branch_turn as turns

    fixture = instruction_authority
    ref = fixture.ref
    request = fixture.request.model_copy(deep=True)
    if changed == "unrecorded_ref":
        ref = await fixture.put(b"Unrelated retained instruction")
    if changed == "manifest_ref":
        request.instruction_ref = await fixture.put(b"Other turn instructions")
        request.step_execution.prepared_input_refs = [request.instruction_ref]
    elif changed == "manifest_step":
        request.step_execution.step_execution_id = "other-step"
    elif changed == "manifest_turn":
        request.step_execution.branch = {
            "branchId": "branch",
            "branchTurnId": "other-turn",
        }
    manifest_ref = await fixture.put(
        json.dumps(
            {
                "agentExecutionRequest": request.model_dump(by_alias=True, mode="json"),
            }
        ).encode(),
        linked=True,
    )
    async with fixture.sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn")
        turn.step_execution_manifest_ref = (
            "artifact://art_missing" if changed == "missing_manifest" else manifest_ref
        )
        if changed == "digest":
            turn.instruction_digest = "sha256:" + "0" * 64
        artifact = await session.get(TemporalArtifact, ref.removeprefix("artifact://"))
        if changed == "restricted":
            artifact.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
        elif changed == "quarantined":
            artifact.metadata_json = {"quarantine": True}
        await session.commit()
    monkeypatch.setattr(turns, "async_session_maker", fixture.sessions)
    monkeypatch.setattr(
        turns, "get_checkpoint_branch_artifact_service", fixture.artifacts
    )
    environment = ActivityEnvironment()
    environment.info = dataclasses.replace(
        environment.info,
        workflow_id=(
            "foreign-workflow"
            if changed == "activity_workflow"
            else fixture.identity.workflow_id
        ),
        workflow_type=turns.WORKFLOW_NAME,
        workflow_run_id="actual-temporal-run",
    )
    payload = {
        "workflowId": "source",
        "branchId": "branch",
        "branchTurnId": "turn",
        "principal": fixture.producer,
        "sourceNamespace": "default",
        "sourceRunId": "source-run",
        "outcome": "failed",
        "agentResult": {"outputRefs": [ref]},
    }
    with pytest.raises(
        ValueError, match="authority|retainable|does not match launch claim"
    ):
        await environment.run(turns.persist_checkpoint_branch_turn_terminal, payload)
    async with fixture.sessions() as session:
        assert not (
            await session.scalars(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.artifact_id == ref.removeprefix("artifact://")
                )
            )
        ).all()
        assert not (
            await session.scalars(
                select(TemporalArtifactPin).where(
                    TemporalArtifactPin.artifact_id == ref.removeprefix("artifact://")
                )
            )
        ).all()


async def test_prepared_input_list_cannot_admit_another_instruction(
    instruction_authority, auth_mode,
):
    fixture = instruction_authority
    other_ref = await fixture.put(b"Another turn's private instructions")
    request = fixture.request.model_copy(deep=True)
    request.step_execution.prepared_input_refs.append(other_ref)
    manifest_ref = await fixture.put(
        json.dumps(
            {
                "agentExecutionRequest": request.model_dump(by_alias=True, mode="json"),
            }
        ).encode(),
        linked=True,
    )
    async with fixture.sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn")
        turn.step_execution_manifest_ref = manifest_ref
        await session.commit()
    await _dispatch_claimed(fixture)
    async with fixture.sessions() as session:
        assert not (
            await session.scalars(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.artifact_id
                    == other_ref.removeprefix("artifact://")
                )
            )
        ).all()
        if auth_mode != "disabled":
            with pytest.raises(TemporalArtifactAuthorizationError):
                await fixture.artifacts(session).read(
                    artifact_id=other_ref.removeprefix("artifact://"),
                    access=access_for_test(
                        "service:checkpoint-branch-turn",
                        admitted_principal=f"workflow:{fixture.identity.workflow_id}",
                    ),
                )


@pytest.mark.parametrize("changed", [None, "ref", "digest"])
async def test_local_instruction_preserves_validation_and_terminal_promotion(
    instruction_authority,
    tmp_path,
    monkeypatch,
    changed,
):
    import dataclasses

    from temporalio.testing import ActivityEnvironment

    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.workflows.temporal.workflows import checkpoint_branch_turn as turns

    fixture = instruction_authority
    local = LocalOmnigentArtifactGateway(root=tmp_path / "local-omnigent")
    local_ref = await local.write_bytes(
        request=fixture.request,
        name="instructions.md",
        payload=fixture.text,
        link_type="input.branch_turn.instructions.md",
    )
    monkeypatch.setattr(
        "moonmind.omnigent.bridge_artifacts.LocalOmnigentArtifactGateway", lambda: local
    )
    request = fixture.request.model_copy(deep=True)
    request.instruction_ref = local_ref
    request.step_execution.prepared_input_refs = [local_ref]
    manifest_ref = await fixture.put(
        json.dumps(
            {
                "agentExecutionRequest": request.model_dump(by_alias=True, mode="json"),
            }
        ).encode(),
        linked=True,
    )
    async with fixture.sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn")
        turn.instruction_ref = local_ref if changed != "ref" else local_ref + ".foreign"
        turn.step_execution_manifest_ref = manifest_ref
        if changed == "digest":
            turn.instruction_digest = "sha256:" + "0" * 64
        await session.commit()
        owner = CheckpointBranchTurnExecutionOwner(
            session,
            principal=fixture.producer,
            access=access_for_test(fixture.producer),
        )
        assert (
            await owner._read_ref(local_ref, field_name="instructionRef")
            == fixture.text
        )
    if changed is not None:
        with pytest.raises(CheckpointBranchTurnLaunchError, match="instruction"):
            await _dispatch_claimed(fixture)
        return
    await _dispatch_claimed(fixture)
    monkeypatch.setattr(turns, "async_session_maker", fixture.sessions)
    monkeypatch.setattr(
        turns, "get_checkpoint_branch_artifact_service", fixture.artifacts
    )
    environment = ActivityEnvironment()
    environment.info = dataclasses.replace(
        environment.info,
        workflow_id=fixture.identity.workflow_id,
        workflow_type=turns.WORKFLOW_NAME,
        workflow_run_id="actual-temporal-run",
    )
    result = await environment.run(
        turns.persist_checkpoint_branch_turn_terminal,
        {
            "workflowId": "source",
            "branchId": "branch",
            "branchTurnId": "turn",
            "principal": fixture.producer,
            "sourceNamespace": "default",
            "sourceRunId": "source-run",
            "outcome": "failed",
            "agentResult": {"outputRefs": [local_ref]},
        },
    )
    async with fixture.sessions() as session:
        _, result_bytes = await fixture.artifacts(session).read(
            artifact_id=result["agentResultRef"].removeprefix("artifact://"),
            access=access_for_test(
                "service:checkpoint-branch-turn", admitted_principal="workflow:source"
            ),
        )
        promoted_ref = json.loads(result_bytes)["outputRefs"][0]
        assert promoted_ref.startswith("artifact://art_")
        _, promoted_bytes = await fixture.artifacts(session).read(
            artifact_id=promoted_ref.removeprefix("artifact://"),
            access=access_for_test(
                "service:checkpoint-branch-turn", admitted_principal="workflow:source"
            ),
        )
        assert promoted_bytes == fixture.text
