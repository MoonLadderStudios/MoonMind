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
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchArtifact,
    WorkflowCheckpointBranchGitBinding,
    WorkflowCheckpointBranchTurn,
)
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    CHECKPOINT_BRANCH_CONTROL_OPERATIONS,
    CHECKPOINT_BRANCH_GRAPH_TRACEABILITY_ISSUES,
    NON_BRANCH_CONTROL_OPERATIONS,
    SOURCE_TRACEABILITY_ISSUES,
)
from moonmind.schemas.checkpoint_branch_models import (
    CheckpointBranchCreateModel,
    CheckpointBranchTurnCreateModel,
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
            "reason": "checkpoint_branch",
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

    assert manifest.branch is not None
    expected_branch = StepExecutionBranchMetadataModel(
        branchId="cbr-1",
        branchTurnId="cbt-1",
        rootCheckpointRef="artifact://checkpoint/after",
        parentBranchId="cbr-parent",
        parentTurnId="cbt-parent",
        gitWorkBranch="mm/wf-1/implement/cbr-1-focused-fix",
    )
    assert manifest.branch.model_dump(by_alias=True) == expected_branch.model_dump(
        by_alias=True
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
            "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
            "runtimeContextPolicy": "fresh_agent_run",
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
    assert (
        turn.workspace_policy.value
        == "apply_previous_execution_diff_to_clean_baseline"
    )
    assert turn.runtime_context_policy.value == "fresh_agent_run"

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
            "workspacePolicy": "continue_from_previous_execution",
            "runtimeContextPolicy": "fresh_agent_run",
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


def test_checkpoint_branch_turn_rejects_step_execution_identity_reuse() -> None:
    with pytest.raises(ValidationError, match="branch and turn ids"):
        CheckpointBranchTurnCreateModel.model_validate(
            {
                "branchTurnId": "cbt-1",
                "branchId": "cbr-1",
                "sourceCheckpointRef": "artifact://checkpoint/after",
                "workspacePolicy": "continue_from_previous_execution",
                "runtimeContextPolicy": "fresh_agent_run",
                "instructionRef": "artifact://instructions/1",
                "instructionDigest": "sha256:instructions",
                "createdStepExecutionId": "cbt-1",
                "idempotencyKey": "MM-1088:cbr-1:turn-1",
            }
        )


@pytest.mark.asyncio
async def test_checkpoint_branch_service_rejects_cross_branch_parent_turn_and_artifact(
    checkpoint_branch_session: AsyncSession,
) -> None:
    service = CheckpointBranchService(checkpoint_branch_session)

    branch_a = await service.create_branch(_branch_payload(branchId="cbr-a"))
    branch_b = await service.create_branch(
        _branch_payload(
            branchId="cbr-b",
            gitWorkBranch="mm/wf-1/implement/cbr-b-focused-fix",
        )
    )
    turn_a = await service.create_turn(
        {
            "branchTurnId": "cbt-a",
            "branchId": branch_a.branch_id,
            "sourceCheckpointRef": branch_a.source_checkpoint_ref,
            "workspacePolicy": "continue_from_previous_execution",
            "runtimeContextPolicy": "fresh_agent_run",
            "instructionRef": "artifact://instructions/a",
            "instructionDigest": "sha256:a",
            "idempotencyKey": "MM-1088:cbr-a:turn-1",
        }
    )

    with pytest.raises(ValueError, match="parentTurnId must belong to branch"):
        await service.create_turn(
            {
                "branchTurnId": "cbt-b",
                "branchId": branch_b.branch_id,
                "parentTurnId": turn_a.branch_turn_id,
                "sourceCheckpointRef": branch_b.source_checkpoint_ref,
                "workspacePolicy": "continue_from_previous_execution",
                "runtimeContextPolicy": "fresh_agent_run",
                "instructionRef": "artifact://instructions/b",
                "instructionDigest": "sha256:b",
                "idempotencyKey": "MM-1088:cbr-b:turn-1",
            }
        )

    with pytest.raises(ValueError, match="branchTurnId must belong to branch"):
        await service.record_artifact(
            branch_id=branch_b.branch_id,
            branch_turn_id=turn_a.branch_turn_id,
            artifact_ref="artifact://turn/a",
            artifact_kind="step_execution_manifest",
        )


@pytest.mark.asyncio
async def test_checkpoint_branch_service_validates_child_parent_turn_lineage(
    checkpoint_branch_session: AsyncSession,
) -> None:
    service = CheckpointBranchService(checkpoint_branch_session)

    parent = await service.create_branch(_branch_payload(branchId="cbr-parent"))
    other = await service.create_branch(
        _branch_payload(
            branchId="cbr-other",
            gitWorkBranch="mm/wf-1/implement/cbr-other-focused-fix",
        )
    )
    other_turn = await service.create_turn(
        {
            "branchTurnId": "cbt-other",
            "branchId": other.branch_id,
            "sourceCheckpointRef": other.source_checkpoint_ref,
            "workspacePolicy": "continue_from_previous_execution",
            "runtimeContextPolicy": "fresh_agent_run",
            "instructionRef": "artifact://instructions/other",
            "instructionDigest": "sha256:other",
            "idempotencyKey": "MM-1088:cbr-other:turn-1",
        }
    )

    with pytest.raises(ValueError, match="parentTurnId must belong to branch"):
        await service.create_branch(
            _branch_payload(
                branchId="cbr-child",
                branchKind="child_fork",
                parentBranchId=parent.branch_id,
                parentTurnId=other_turn.branch_turn_id,
                gitWorkBranch="mm/wf-1/implement/cbr-child-focused-fix",
            )
        )


@pytest.mark.asyncio
async def test_checkpoint_branch_service_rejects_duplicate_git_work_branch(
    checkpoint_branch_session: AsyncSession,
) -> None:
    service = CheckpointBranchService(checkpoint_branch_session)

    first = await service.create_branch(_branch_payload(branchId="cbr-first"))
    second = await service.create_branch(
        _branch_payload(
            branchId="cbr-second",
            gitWorkBranch="mm/wf-1/implement/cbr-second-focused-fix",
        )
    )
    await service.record_git_binding(
        {
            "branch_id": first.branch_id,
            "repository": "repo://moonmind",
            "base_branch": "main",
            "base_commit": "abc123",
            "work_branch": "mm/wf-1/implement/collision",
        }
    )

    with pytest.raises(ValueError, match="already bound"):
        await service.record_git_binding(
            {
                "branch_id": second.branch_id,
                "repository": "repo://moonmind",
                "base_branch": "main",
                "base_commit": "abc123",
                "work_branch": "mm/wf-1/implement/collision",
            }
        )


@pytest.mark.asyncio
async def test_checkpoint_branch_graph_operations_and_queries(
    checkpoint_branch_session: AsyncSession,
) -> None:
    service = CheckpointBranchService(checkpoint_branch_session)

    graph = await service.create_branch_graph(
        {
            **_branch_payload(branchId="cbr-graph", createdBy="MM-1099"),
            "instructionRef": "artifact://instructions/root",
            "instructionDigest": "sha256:root",
            "idempotencyKey": "MM-1099:cbr-graph:create",
        }
    )
    continued = await service.continue_branch(
        workflow_id="wf-1",
        branch_id="cbr-graph",
        payload={
            "instructionRef": "artifact://instructions/continue",
            "instructionDigest": "sha256:continue",
            "idempotencyKey": "MM-1099:cbr-graph:continue",
            "createdStepExecutionId": "wf-1:run-branch:implement:execution:2",
        },
    )
    child = await service.fork_branch(
        workflow_id="wf-1",
        branch_id="cbr-graph",
        payload={
            "branchId": "cbr-child",
            "label": "Try child path",
            "parentTurnId": continued.branch_turn_id,
            "instructionRef": "artifact://instructions/child",
            "instructionDigest": "sha256:child",
            "idempotencyKey": "MM-1099:cbr-child:create",
            "workspacePolicy": "continue_from_previous_execution",
            "runtimeContextPolicy": "fresh_agent_run",
        },
    )
    await service.archive_branch(workflow_id="wf-1", branch_id=child.branch.branch_id)
    await service.mark_publish_ready(
        workflow_id="wf-1",
        branch_id="cbr-graph",
        payload={
            "artifactRef": "artifact://publish/candidate",
            "idempotencyKey": "MM-1099:cbr-graph:publish-ready",
        },
    )
    await checkpoint_branch_session.commit()

    assert graph.branch.branch_id == "cbr-graph"
    assert graph.branch.source_checkpoint_ref == "artifact://checkpoint/after"
    assert graph.turns[0].instruction_ref == "artifact://instructions/root"
    assert continued.branch_id == "cbr-graph"

    branches = await service.list_branch_graphs(workflow_id="wf-1")
    branch_by_id = {item.branch.branch_id: item for item in branches}

    assert branch_by_id["cbr-graph"].branch.state == "promotable"
    assert branch_by_id["cbr-child"].branch.state == "archived"
    assert branch_by_id["cbr-child"].branch.parent_branch_id == "cbr-graph"
    assert branch_by_id["cbr-child"].branch.parent_turn_id == continued.branch_turn_id
    assert any(
        artifact.artifact_kind == "publish_ready"
        for artifact in branch_by_id["cbr-graph"].artifacts
    )
    assert all(
        turn.created_step_execution_id not in {turn.branch_id, turn.branch_turn_id}
        for item in branches
        for turn in item.turns
    )


@pytest.mark.asyncio
async def test_checkpoint_branch_graph_operations_are_idempotent_by_operation_identity(
    checkpoint_branch_session: AsyncSession,
) -> None:
    service = CheckpointBranchService(checkpoint_branch_session)
    create_payload = {
        **_branch_payload(branchId="cbr-idempotent", createdBy="MM-1099"),
        "instructionRef": "artifact://instructions/root",
        "instructionDigest": "sha256:root",
        "idempotencyKey": "MM-1099:cbr-idempotent:create",
    }

    first_graph = await service.create_branch_graph(create_payload)
    duplicate_graph = await service.create_branch_graph(create_payload)
    first_continue = await service.continue_branch(
        workflow_id="wf-1",
        branch_id="cbr-idempotent",
        payload={
            "instructionRef": "artifact://instructions/continue",
            "instructionDigest": "sha256:continue",
            "idempotencyKey": "MM-1099:cbr-idempotent:continue",
            "createdStepExecutionId": "wf-1:run-branch:implement:execution:2",
        },
    )
    duplicate_continue = await service.continue_branch(
        workflow_id="wf-1",
        branch_id="cbr-idempotent",
        payload={
            "instructionRef": "artifact://instructions/continue",
            "instructionDigest": "sha256:continue",
            "idempotencyKey": "MM-1099:cbr-idempotent:continue",
            "createdStepExecutionId": "wf-1:run-branch:implement:execution:2",
        },
    )
    fork_payload = {
        "branchId": "cbr-idempotent-child",
        "label": "Try child path",
        "parentTurnId": first_continue.branch_turn_id,
        "instructionRef": "artifact://instructions/child",
        "instructionDigest": "sha256:child",
        "idempotencyKey": "MM-1099:cbr-idempotent-child:create",
        "workspacePolicy": "continue_from_previous_execution",
        "runtimeContextPolicy": "fresh_agent_run",
    }
    first_child = await service.fork_branch(
        workflow_id="wf-1",
        branch_id="cbr-idempotent",
        payload=fork_payload,
    )
    duplicate_child = await service.fork_branch(
        workflow_id="wf-1",
        branch_id="cbr-idempotent",
        payload=fork_payload,
    )
    first_archive = await service.archive_branch(
        workflow_id="wf-1",
        branch_id="cbr-idempotent-child",
        idempotency_key="MM-1099:cbr-idempotent-child:archive",
    )
    duplicate_archive = await service.archive_branch(
        workflow_id="wf-1",
        branch_id="cbr-idempotent-child",
        idempotency_key="MM-1099:cbr-idempotent-child:archive",
    )
    first_publish_ready = await service.mark_publish_ready(
        workflow_id="wf-1",
        branch_id="cbr-idempotent",
        payload={
            "artifactRef": "artifact://publish/candidate",
            "idempotencyKey": "MM-1099:cbr-idempotent:publish-ready",
        },
    )
    duplicate_publish_ready = await service.mark_publish_ready(
        workflow_id="wf-1",
        branch_id="cbr-idempotent",
        payload={
            "artifactRef": "artifact://publish/candidate",
            "idempotencyKey": "MM-1099:cbr-idempotent:publish-ready",
        },
    )
    await checkpoint_branch_session.commit()

    assert duplicate_graph.branch.branch_id == first_graph.branch.branch_id
    assert (
        duplicate_graph.turns[0].branch_turn_id
        == first_graph.turns[0].branch_turn_id
    )
    assert duplicate_continue.branch_turn_id == first_continue.branch_turn_id
    assert duplicate_child.branch.branch_id == first_child.branch.branch_id
    assert (
        duplicate_child.turns[0].branch_turn_id
        == first_child.turns[0].branch_turn_id
    )
    assert duplicate_archive.archived_at == first_archive.archived_at
    assert duplicate_publish_ready.state == first_publish_ready.state == "promotable"

    branches = (
        await checkpoint_branch_session.execute(select(WorkflowCheckpointBranch))
    ).scalars().all()
    turns = (
        await checkpoint_branch_session.execute(select(WorkflowCheckpointBranchTurn))
    ).scalars().all()
    artifacts = (
        await checkpoint_branch_session.execute(
            select(WorkflowCheckpointBranchArtifact)
        )
    ).scalars().all()

    assert [branch.branch_id for branch in branches] == [
        "cbr-idempotent",
        "cbr-idempotent-child",
    ]
    assert sorted(turn.idempotency_key for turn in turns) == sorted(
        [
            "MM-1099:cbr-idempotent-child:create",
            "MM-1099:cbr-idempotent:create",
            "MM-1099:cbr-idempotent:continue",
        ]
    )
    assert sorted(
        (artifact.artifact_kind, artifact.artifact_ref) for artifact in artifacts
    ) == [
        ("operation_archive", "idempotency://MM-1099:cbr-idempotent-child:archive"),
        (
            "operation_publish_ready",
            "idempotency://MM-1099:cbr-idempotent:publish-ready",
        ),
        ("publish_ready", "artifact://publish/candidate"),
    ]


@pytest.mark.asyncio
async def test_checkpoint_branch_graph_inactive_evidence_remains_queryable(
    checkpoint_branch_session: AsyncSession,
) -> None:
    service = CheckpointBranchService(checkpoint_branch_session)
    for branch_id in ("cbr-failed", "cbr-archived", "cbr-superseded", "cbr-unpromoted"):
        await service.create_branch_graph(
            {
                **_branch_payload(
                    branchId=branch_id,
                    gitWorkBranch=f"mm/wf-1/implement/{branch_id}",
                    createdBy="MM-1099",
                ),
                "instructionRef": f"artifact://instructions/{branch_id}",
                "instructionDigest": f"sha256:{branch_id}",
                "idempotencyKey": f"MM-1099:{branch_id}:create",
            }
        )
        await service.record_artifact(
            branch_id=branch_id,
            artifact_ref=f"artifact://evidence/{branch_id}",
            artifact_kind="step_execution_manifest",
        )
    await service.mark_failed(
        workflow_id="wf-1",
        branch_id="cbr-failed",
        idempotency_key="MM-1099:cbr-failed:failed",
    )
    await service.archive_branch(
        workflow_id="wf-1",
        branch_id="cbr-archived",
        idempotency_key="MM-1099:cbr-archived:archive",
    )
    await service.mark_superseded(
        workflow_id="wf-1",
        branch_id="cbr-superseded",
        idempotency_key="MM-1099:cbr-superseded:superseded",
    )
    await checkpoint_branch_session.commit()

    all_branches = await service.list_branch_graphs(workflow_id="wf-1")
    by_id = {item.branch.branch_id: item for item in all_branches}

    assert by_id["cbr-failed"].branch.state == "failed"
    assert by_id["cbr-archived"].branch.state == "archived"
    assert by_id["cbr-superseded"].branch.state == "superseded"
    assert by_id["cbr-unpromoted"].branch.state == "created"
    assert all(item.turns and item.artifacts for item in by_id.values())
    assert (
        await service.read_branch_graph(workflow_id="wf-1", branch_id="cbr-failed")
    ).branch.state == "failed"

    active_branches = await service.list_branch_graphs(
        workflow_id="wf-1",
        active_only=True,
    )
    assert {item.branch.branch_id for item in active_branches} == {"cbr-unpromoted"}


def test_checkpoint_branch_control_operations_preserve_runtime_terminology() -> None:
    assert CHECKPOINT_BRANCH_CONTROL_OPERATIONS == frozenset(
        {
            "checkpoint_branch_create",
            "checkpoint_branch_continue",
            "checkpoint_branch_fork",
            "checkpoint_branch_archive",
            "checkpoint_branch_publish_ready",
            "checkpoint_branch_failed",
            "checkpoint_branch_superseded",
        }
    )
    assert NON_BRANCH_CONTROL_OPERATIONS == frozenset(
        {
            "retry",
            "step_reexecution",
            "recovery",
            "promotion",
            "publication",
        }
    )
    assert CHECKPOINT_BRANCH_CONTROL_OPERATIONS.isdisjoint(
        NON_BRANCH_CONTROL_OPERATIONS
    )


@pytest.mark.asyncio
async def test_checkpoint_branch_graph_rejects_invalid_product_operations(
    checkpoint_branch_session: AsyncSession,
) -> None:
    service = CheckpointBranchService(checkpoint_branch_session)
    branch = await service.create_branch_graph(
        {
            **_branch_payload(branchId="cbr-invalid"),
            "instructionRef": "artifact://instructions/root",
            "instructionDigest": "sha256:root",
            "idempotencyKey": "MM-1099:cbr-invalid:create",
        }
    )

    with pytest.raises(ValueError, match="not found"):
        await service.continue_branch(
            workflow_id="wf-other",
            branch_id=branch.branch.branch_id,
            payload={
                "instructionRef": "artifact://instructions/continue",
                "instructionDigest": "sha256:continue",
                "idempotencyKey": "MM-1099:cbr-invalid:continue",
            },
        )

    with pytest.raises(ValueError, match="parentTurnId must reference"):
        await service.fork_branch(
            workflow_id="wf-1",
            branch_id=branch.branch.branch_id,
            payload={
                "branchId": "cbr-invalid-child",
                "label": "Invalid child",
                "parentTurnId": "cbt-missing",
                "instructionRef": "artifact://instructions/child",
                "instructionDigest": "sha256:child",
                "idempotencyKey": "MM-1099:cbr-invalid-child:create",
                "workspacePolicy": "continue_from_previous_execution",
                "runtimeContextPolicy": "fresh_agent_run",
            },
        )


def test_checkpoint_branch_graph_traceability_preserves_source_issue() -> None:
    assert SOURCE_TRACEABILITY_ISSUES == ("MM-1087", "MM-1088")
    assert CHECKPOINT_BRANCH_GRAPH_TRACEABILITY_ISSUES == ("MM-1087", "MM-1099")
