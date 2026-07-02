from __future__ import annotations

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
    WorkflowCheckpointBranchArtifact,
    WorkflowCheckpointBranchGitBinding,
)
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    SOURCE_TRACEABILITY_ISSUES,
)
from moonmind.schemas.checkpoint_branch_models import (
    CheckpointBranchCreateModel,
    StepExecutionBranchMetadataModel,
)
from moonmind.schemas.temporal_models import StepExecutionManifestModel


@pytest_asyncio.fixture()
async def checkpoint_branch_session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/branches.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        session.add(
            TemporalExecutionCanonicalRecord(
                workflow_id="wf-1",
                run_id="run-1",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                entry="api",
            )
        )
        await session.commit()
        yield session

    await engine.dispose()


def _branch_payload(**overrides):
    payload = {
        "branchId": "cbr-1",
        "source": {
            "workflowId": "wf-1",
            "runId": "run-1",
            "logicalStepId": "implement",
            "sourceExecutionOrdinal": 2,
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoint/after",
            "checkpointDigest": "sha256:checkpoint",
        },
        "label": "Try focused fix",
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "runtimeContextPolicy": "fresh_agent_run",
        "gitRepository": "repo://moonmind",
        "gitBaseBranch": "main",
        "gitBaseCommit": "abc123",
        "gitWorkBranch": "mm/wf-1/implement/cbr-1-focused-fix",
        "createdBy": "MM-1088",
    }
    payload.update(overrides)
    return payload


def test_checkpoint_branch_requires_checkpoint_or_typed_source_state() -> None:
    payload = _branch_payload()
    payload["source"].pop("checkpointRef")

    with pytest.raises(ValidationError, match="checkpointRef or typed sourceStateRef"):
        CheckpointBranchCreateModel.model_validate(payload)

    payload["source"]["sourceStateKind"] = "provider_session"
    payload["source"]["sourceStateRef"] = "provider://session/1"

    model = CheckpointBranchCreateModel.model_validate(payload)

    assert model.source.source_state_kind == "provider_session"


def test_checkpoint_branch_child_fork_requires_parent_branch_and_turn() -> None:
    with pytest.raises(ValidationError, match="parent branch and turn"):
        CheckpointBranchCreateModel.model_validate(
            _branch_payload(branchKind="child_fork", parentBranchId="cbr-parent")
        )


def test_branch_manifest_metadata_is_optional_step_execution_lineage() -> None:
    manifest = StepExecutionManifestModel.model_validate(
        {
            "workflowId": "wf-1",
            "runId": "run-branch",
            "logicalStepId": "implement",
            "executionOrdinal": 3,
            "stepExecutionId": "wf-1:run-branch:implement:execution:3",
            "reason": "operator_requested",
            "status": "running",
            "branch": {
                "branchId": "cbr-1",
                "branchTurnId": "cbt-1",
                "rootCheckpointRef": "artifact://checkpoint/after",
                "parentBranchId": "cbr-parent",
                "parentTurnId": "cbt-parent",
                "gitWorkBranch": "mm/wf-1/implement/cbr-1-focused-fix",
            },
        }
    )

    assert manifest.branch == StepExecutionBranchMetadataModel(
        branchId="cbr-1",
        branchTurnId="cbt-1",
        rootCheckpointRef="artifact://checkpoint/after",
        parentBranchId="cbr-parent",
        parentTurnId="cbt-parent",
        gitWorkBranch="mm/wf-1/implement/cbr-1-focused-fix",
    )
    assert manifest.model_dump(by_alias=True)["branch"]["branchId"] == "cbr-1"


@pytest.mark.asyncio
async def test_checkpoint_branch_service_persists_branch_turn_artifact_and_git_binding(
    checkpoint_branch_session: AsyncSession,
) -> None:
    service = CheckpointBranchService(checkpoint_branch_session)

    branch = await service.create_branch(_branch_payload())
    turn = await service.create_turn(
        {
            "branchTurnId": "cbt-1",
            "branchId": branch.branch_id,
            "sourceCheckpointRef": branch.source_checkpoint_ref,
            "sourceCheckpointDigest": branch.source_checkpoint_digest,
            "instructionRef": "artifact://instructions/1",
            "instructionDigest": "sha256:instructions",
            "contextBundleRef": "artifact://context/1",
            "createdStepExecutionId": "wf-1:run-branch:implement:execution:1",
            "idempotencyKey": "MM-1088:cbr-1:turn-1",
            "status": "created",
        }
    )
    await service.record_git_binding(
        {
            "branch_id": branch.branch_id,
            "repository": "repo://moonmind",
            "base_branch": "main",
            "base_commit": "abc123",
            "work_branch": "mm/wf-1/implement/cbr-1-focused-fix",
            "head_commit": "def456",
            "publish_status": "unpublished",
        }
    )
    await service.record_artifact(
        branch_id=branch.branch_id,
        branch_turn_id=turn.branch_turn_id,
        artifact_ref="artifact://turn/manifest",
        artifact_kind="step_execution_manifest",
    )
    await checkpoint_branch_session.commit()

    assert SOURCE_TRACEABILITY_ISSUES == ("MM-1087", "MM-1088")
    assert branch.branch_id != turn.created_step_execution_id
    assert branch.workflow_id == "wf-1"
    assert branch.source_run_id == "run-1"
    assert branch.logical_step_id == "implement"
    assert branch.source_execution_ordinal == 2
    assert branch.source_checkpoint_boundary == "after_execution"
    assert branch.source_checkpoint_ref == "artifact://checkpoint/after"
    assert branch.source_checkpoint_digest == "sha256:checkpoint"
    assert branch.state.value == "created"
    assert turn.status.value == "created"

    git_binding = (
        await checkpoint_branch_session.execute(
            select(WorkflowCheckpointBranchGitBinding).where(
                WorkflowCheckpointBranchGitBinding.branch_id == branch.branch_id
            )
        )
    ).scalar_one()
    artifact = (
        await checkpoint_branch_session.execute(
            select(WorkflowCheckpointBranchArtifact).where(
                WorkflowCheckpointBranchArtifact.branch_id == branch.branch_id
            )
        )
    ).scalar_one()

    assert git_binding.work_branch == "mm/wf-1/implement/cbr-1-focused-fix"
    assert artifact.branch_turn_id == "cbt-1"
    assert artifact.artifact_ref == "artifact://turn/manifest"


@pytest.mark.asyncio
async def test_checkpoint_branch_service_preserves_child_lineage(
    checkpoint_branch_session: AsyncSession,
) -> None:
    service = CheckpointBranchService(checkpoint_branch_session)

    parent = await service.create_branch(_branch_payload(branchId="cbr-parent"))
    parent_turn = await service.create_turn(
        {
            "branchTurnId": "cbt-parent",
            "branchId": parent.branch_id,
            "sourceCheckpointRef": parent.source_checkpoint_ref,
            "instructionRef": "artifact://instructions/parent",
            "instructionDigest": "sha256:parent",
            "idempotencyKey": "MM-1088:cbr-parent:turn-1",
        }
    )
    child = await service.create_branch(
        _branch_payload(
            branchId="cbr-child",
            branchKind="child_fork",
            parentBranchId=parent.branch_id,
            parentTurnId=parent_turn.branch_turn_id,
        )
    )
    await checkpoint_branch_session.commit()

    assert child.parent_branch_id == "cbr-parent"
    assert child.parent_turn_id == "cbt-parent"
