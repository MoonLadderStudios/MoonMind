"""Unit coverage for MM-1091 checkpoint branch API paths."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers.executions import (
    _checkpoint_branch_git_context,
    _get_service,
    router,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchArtifact,
    WorkflowCheckpointBranchGitBinding,
    WorkflowCheckpointBranchOperation,
    WorkflowCheckpointBranchTurn,
)


def _override_user_dependencies(app: FastAPI, user: SimpleNamespace) -> None:
    user_dependencies = {
        dep.call
        for route in router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if getattr(dep.call, "__name__", "") == "_current_user_fallback"
    }
    if not user_dependencies:
        user_dependencies = {get_current_user()}

    def _current_user() -> SimpleNamespace:
        return user

    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = _current_user


@pytest_asyncio.fixture
async def checkpoint_branch_client(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/checkpoint-branches.db"
    )
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    user = SimpleNamespace(
        id=uuid4(),
        email="checkpoint-branches@example.com",
        is_superuser=True,
        roles=[],
    )
    now = datetime.now(UTC)
    record = TemporalExecutionCanonicalRecord(
        workflow_id="mm:wf-branch",
        run_id="run-branch",
        namespace="default",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_id=str(user.id),
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.EXECUTING,
        entry="run",
        search_attributes={"mm_owner_id": str(user.id), "mm_owner_type": "user"},
        memo={
            "stepCheckpointRef": "artifact://checkpoints/after-implement",
            "latest_temporal_run_id": "run-branch",
            "repository": "MoonLadderStudios/MoonMind",
        },
        parameters={
            "git": {
                "repository": "MoonLadderStudios/MoonMind",
                "startingBranch": "feature/mm-1101-source",
                "baseCommit": "abc1234",
                "knownRefs": ["feature/mm-1101-source"],
                "currentRef": "feature/mm-1101-source",
                "resolvedBaseCommit": "abc1234",
            },
            "steps": [
                {
                    "logicalStepId": "implement",
                    "executionOrdinal": 2,
                    "checkpointRefsByBoundary": {
                        "after_execution": {
                            "artifactRef": "artifact://checkpoints/after-implement",
                            "checkpointDigest": "sha256:checkpointdigest",
                        }
                    },
                    "checkpointRef": "artifact://checkpoints/after-implement",
                    "checkpointDigest": "sha256:checkpointdigest",
                }
            ]
        },
        artifact_refs=[],
        created_at=now,
        updated_at=now,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        session.add(record)
        await session.commit()

    app = FastAPI()
    app.include_router(router)
    service = SimpleNamespace(describe_execution=AsyncMock(return_value=record))
    app.dependency_overrides[_get_service] = lambda: service

    async def _session_override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session_override
    _override_user_dependencies(app, user)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def checkpoint_branch_denied_client(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/checkpoint-branches-denied.db"
    )
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    owner_id = uuid4()
    user = SimpleNamespace(
        id=uuid4(),
        email="not-the-owner@example.com",
        is_superuser=False,
        roles=[],
    )
    now = datetime.now(UTC)
    record = TemporalExecutionCanonicalRecord(
        workflow_id="mm:wf-branch",
        run_id="run-branch",
        namespace="default",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_id=str(owner_id),
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.EXECUTING,
        entry="run",
        search_attributes={"mm_owner_id": str(owner_id), "mm_owner_type": "user"},
        memo={"stepCheckpointRef": "artifact://checkpoints/after-implement"},
        parameters={},
        artifact_refs=[],
        created_at=now,
        updated_at=now,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        session.add(record)
        await session.commit()

    app = FastAPI()
    app.include_router(router)
    service = SimpleNamespace(describe_execution=AsyncMock(return_value=record))
    app.dependency_overrides[_get_service] = lambda: service

    async def _session_override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session_override
    _override_user_dependencies(app, user)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()


async def _set_branch_head(
    client: AsyncClient,
    branch_id: str,
    step_execution_id: str = "mm:wf-branch:run:implement:execution:2",
) -> None:
    async for session in client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        result = await session.execute(
            select(WorkflowCheckpointBranch).where(
                WorkflowCheckpointBranch.branch_id == branch_id
            )
        )
        branch = result.scalar_one()
        branch.current_head_step_execution_id = step_execution_id
        await session.commit()


def test_checkpoint_branch_git_context_reads_workflow_shaped_payload() -> None:
    record = SimpleNamespace(
        parameters={
            "workflow": {
                "git": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "startingBranch": "feature/from-workflow",
                    "branch": "feature/work-branch",
                    "baseCommit": "abc1234",
                    "knownRefs": ["feature/from-workflow"],
                    "currentRef": "feature/from-workflow",
                    "resolvedBaseCommit": "abc1234def5678",
                }
            }
        },
        memo={},
        search_attributes={},
    )

    context = _checkpoint_branch_git_context(record)

    assert context["repository"] == "MoonLadderStudios/MoonMind"
    assert context["baseBranch"] == "feature/from-workflow"
    assert context["baseCommit"] == "abc1234"
    assert context["resolvedBaseCommit"] == "abc1234def5678"
    assert context["currentRef"] == "feature/from-workflow"
    assert context["knownRefs"] == {"feature/from-workflow"}


def _create_payload(idempotency_key: str = "mm-1091:create") -> dict[str, object]:
    return {
        "source": {
            "runId": "run-branch",
            "logicalStepId": "implement",
            "executionOrdinal": 2,
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoints/after-implement",
            "checkpointDigest": "sha256:checkpointdigest",
        },
        "label": "MM-1091 branch",
        "instructions": {"text": "Continue from the checkpoint."},
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "runtimeContextPolicy": "fresh_agent_run",
        "publishMode": "none",
        "idempotencyKey": idempotency_key,
    }


@pytest.mark.asyncio
async def test_checkpoint_branch_create_prepares_git_binding_before_launch(
    checkpoint_branch_client: AsyncClient,
) -> None:
    response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1101:create-prepared-binding"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["gitWorkBranch"].startswith("mm/mm-wf-branch/implement/cp-")
    assert body["gitWorkBranch"] != body["branchId"]

    async for session in checkpoint_branch_client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        binding = await session.get(WorkflowCheckpointBranchGitBinding, body["branchId"])
        turn = (
            await session.execute(
                select(WorkflowCheckpointBranchTurn).where(
                    WorkflowCheckpointBranchTurn.branch_id == body["branchId"]
                )
            )
        ).scalar_one()

    assert binding is not None
    assert binding.repository == "MoonLadderStudios/MoonMind"
    assert binding.base_branch == "feature/mm-1101-source"
    assert binding.base_commit == "abc1234"
    assert binding.work_branch == body["gitWorkBranch"]
    assert binding.binding_metadata["ownership"]["idempotencyKey"] == (
        "mm-1101:create-prepared-binding"
    )
    assert binding.binding_metadata["workspaceBaseline"]["workBranch"] == (
        body["gitWorkBranch"]
    )
    assert turn.status == "preparing"
    assert turn.git_work_branch == body["gitWorkBranch"]
    assert turn.git_binding_ref


@pytest.mark.asyncio
async def test_checkpoint_branch_api_lists_creates_details_turns_and_is_idempotent(
    checkpoint_branch_client: AsyncClient,
) -> None:
    checkpoints = await checkpoint_branch_client.get(
        "/api/executions/mm:wf-branch/checkpoints"
    )
    assert checkpoints.status_code == 200
    assert checkpoints.json()["items"][0]["checkpointRef"] == (
        "artifact://checkpoints/after-implement"
    )

    first = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload(),
    )
    second = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload(),
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["branchId"] == second.json()["branchId"]
    branch_id = first.json()["branchId"]

    detail = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}"
    )
    turns = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )
    branches = await checkpoint_branch_client.get(
        "/api/executions/mm:wf-branch/checkpoint-branches"
    )

    assert detail.status_code == 200
    assert turns.status_code == 200
    assert branches.status_code == 200
    assert detail.json()["branchId"] == branch_id
    assert len(turns.json()["items"]) == 1
    assert branches.json()["items"][0]["branchId"] == branch_id


@pytest.mark.asyncio
async def test_checkpoint_branch_api_launches_turn_with_context_bundle_evidence_and_replays(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1100:create-launch"),
    )
    branch_id = created.json()["branchId"]
    turns = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )
    branch_turn_id = turns.json()["items"][0]["branchTurnId"]
    payload = {
        "createdStepExecutionId": "mm:wf-branch:run-branch:implement:execution:3",
        "workspaceBaseline": {
            "kind": "git_patch",
            "baseCommit": "abc123",
            "patchRef": "artifact://patches/mm-1100",
        },
        "priorEvidenceRefs": ["artifact://manifest/previous"],
        "boundedSummaries": [
            {"label": "prior attempt", "summary": "Gate failed before launch."}
        ],
        "builderMetadata": {"version": "api-test-v1", "digest": "sha256:builder"},
        "runtimeRequestRef": "artifact://runtime/request/mm-1100",
        "runtimeResultRef": "artifact://runtime/result/mm-1100",
        "diagnosticsRef": "artifact://diagnostics/mm-1100",
    }

    first = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns/"
        f"{branch_turn_id}/launch",
        json=payload,
    )
    second = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns/"
        f"{branch_turn_id}/launch",
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    body = first.json()
    assert body["branchTurnId"] == branch_turn_id
    assert body["status"] == "running"
    assert body["createdStepExecutionId"] == (
        "mm:wf-branch:run-branch:implement:execution:3"
    )
    assert "checkpointRef" not in body
    assert body["contextBundleRef"].startswith(
        f"artifact://checkpoint-branch-turns/{branch_turn_id}/context-bundle/"
    )
    assert body["stepExecutionManifestRef"].startswith(
        f"artifact://checkpoint-branch-turns/{branch_turn_id}/step-execution-manifest/"
    )
    assert second.json()["contextBundleRef"] == body["contextBundleRef"]
    assert second.json()["stepExecutionManifestRef"] == body["stepExecutionManifestRef"]
    assert second.json()["diagnostics"]["launchIdempotencyKey"] == (
        f"mm:wf-branch:{branch_id}:{branch_turn_id}:launch"
    )

    async for session in checkpoint_branch_client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        artifact_rows = (
            await session.execute(
                select(WorkflowCheckpointBranchArtifact).where(
                    WorkflowCheckpointBranchArtifact.branch_turn_id == branch_turn_id
                )
            )
        ).scalars().all()
        operation_rows = (
            await session.execute(
                select(WorkflowCheckpointBranchOperation).where(
                    WorkflowCheckpointBranchOperation.operation
                    == "checkpoint_branch.turn.launch"
                )
            )
        ).scalars().all()

    assert len(artifact_rows) == 7
    assert {
        artifact.artifact_kind for artifact in artifact_rows
    } == {
        "runtime.branch.workspace_restore.json",
        "runtime.branch.git_binding.json",
        "runtime.branch_turn.context_bundle.json",
        "runtime.branch_turn.agent_request.json",
        "runtime.branch_turn.agent_result.json",
        "output.branch_turn.step_execution_manifest.json",
        "output.branch_turn.diagnostics.json",
    }
    assert len(operation_rows) == 1
    assert "checkpointRef" not in operation_rows[0].response_payload
    assert operation_rows[0].response_payload["contextBundle"]["branch"][
        "sourceCheckpointRef"
    ] == "artifact://checkpoints/after-implement"
    assert "rawLogs" not in operation_rows[0].response_payload["contextBundle"]


@pytest.mark.asyncio
async def test_checkpoint_branch_api_launch_requires_step_execution_id(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1100:create-launch-requires-step"),
    )
    branch_id = created.json()["branchId"]
    turns = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )
    branch_turn_id = turns.json()["items"][0]["branchTurnId"]

    launched = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns/"
        f"{branch_turn_id}/launch",
        json={"runtimeAgentRunId": "agent-run-1"},
    )

    assert launched.status_code == 422


@pytest.mark.asyncio
async def test_checkpoint_branch_api_launch_rejects_archived_branch(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1100:create-launch-archived"),
    )
    branch_id = created.json()["branchId"]
    turns = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )
    branch_turn_id = turns.json()["items"][0]["branchTurnId"]
    archived = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/archive",
        json={"idempotencyKey": "mm-1100:archive-before-launch"},
    )

    launched = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns/"
        f"{branch_turn_id}/launch",
        json={
            "createdStepExecutionId": "mm:wf-branch:run-branch:implement:execution:9",
            "diagnosticsRef": "artifact://diagnostics/archived",
        },
    )

    assert archived.status_code == 200
    assert launched.status_code == 409
    assert launched.json()["detail"]["code"] == "branch_turn_launch_rejected"


@pytest.mark.asyncio
async def test_checkpoint_branch_api_rejects_launch_raw_context_and_immutable_mutation(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1100:create-immutable"),
    )
    branch_id = created.json()["branchId"]
    turns = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )
    branch_turn_id = turns.json()["items"][0]["branchTurnId"]

    raw_context = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns/"
        f"{branch_turn_id}/launch",
        json={
            "createdStepExecutionId": "mm:wf-branch:run-branch:implement:execution:4",
            "workspaceBaseline": {"kind": "git_ref", "ref": "main"},
            "boundedSummaries": [{"label": "raw", "rawLogs": "do not persist"}],
            "diagnosticsRef": "artifact://diagnostics/raw",
        },
    )
    assert raw_context.status_code == 422

    launch_payload = {
        "createdStepExecutionId": "mm:wf-branch:run-branch:implement:execution:4",
        "workspaceBaseline": {"kind": "git_ref", "ref": "main"},
        "diagnosticsRef": "artifact://diagnostics/immutable",
    }
    launched = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns/"
        f"{branch_turn_id}/launch",
        json=launch_payload,
    )
    assert launched.status_code == 200

    async for session in checkpoint_branch_client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        turn = (
            await session.execute(
                select(WorkflowCheckpointBranchTurn).where(
                    WorkflowCheckpointBranchTurn.branch_turn_id == branch_turn_id
                )
            )
        ).scalar_one()
        turn.instruction_ref = "artifact://instructions/changed"
        await session.commit()

    replay_after_mutation = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns/"
        f"{branch_turn_id}/launch",
        json=launch_payload,
    )

    assert replay_after_mutation.status_code == 409
    assert replay_after_mutation.json()["detail"]["code"] == "immutable_launch_field"


@pytest.mark.asyncio
async def test_checkpoint_branch_publish_does_not_promote_and_archive_hides_active(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-publish"),
    )
    branch_id = created.json()["branchId"]

    published = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/publish",
        json={
            "mode": "pull_request",
            "repository": "Moon/Mind",
            "baseBranch": "main",
            "headBranch": "mm/mm-1091/checkpoint-branch",
            "provider": "github",
            "idempotencyKey": "mm-1091:publish",
        },
    )
    assert published.status_code == 200
    assert published.json()["publishStatus"] == "published"
    assert published.json()["state"] == "created"
    assert published.json()["promotedAt"] is None

    archived = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/archive",
        json={"reason": "No longer active", "idempotencyKey": "mm-1091:archive"},
    )
    publish_archived = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/publish",
        json={
            "mode": "branch",
            "repository": "Moon/Mind",
            "baseBranch": "main",
            "headBranch": "mm/mm-1091/archived",
            "provider": "github",
            "idempotencyKey": "mm-1091:publish-archived",
        },
    )
    active = await checkpoint_branch_client.get(
        "/api/executions/mm:wf-branch/checkpoint-branches"
    )
    all_branches = await checkpoint_branch_client.get(
        "/api/executions/mm:wf-branch/checkpoint-branches?active=false"
    )

    assert archived.status_code == 200
    assert archived.json()["state"] == "archived"
    assert publish_archived.status_code == 409
    assert publish_archived.json()["detail"]["code"] == "invalid_branch_state"
    assert active.json()["items"] == []
    assert all_branches.json()["items"][0]["branchId"] == branch_id


@pytest.mark.asyncio
async def test_checkpoint_branch_continue_fork_and_compare_are_typed_and_idempotent(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-branching"),
    )
    branch_id = created.json()["branchId"]

    continue_payload = {
        "label": "Continued branch",
        "instructions": {"text": "Continue this branch."},
        "workspacePolicy": "continue_from_previous_execution",
        "runtimeContextPolicy": "reuse_session_new_epoch",
        "idempotencyKey": "mm-1091:continue",
    }
    first_continue = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/continue",
        json=continue_payload,
    )
    second_continue = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/continue",
        json=continue_payload,
    )
    assert first_continue.status_code == 201
    assert second_continue.status_code == 201
    assert (
        first_continue.json()["branchTurnId"] == second_continue.json()["branchTurnId"]
    )
    async for session in checkpoint_branch_client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        branch = await session.get(WorkflowCheckpointBranch, branch_id)
        continued_turn = await session.get(
            WorkflowCheckpointBranchTurn, first_continue.json()["branchTurnId"]
        )

    assert branch is not None
    assert continued_turn is not None
    assert branch.git_work_branch == created.json()["gitWorkBranch"]
    assert continued_turn.git_work_branch == created.json()["gitWorkBranch"]

    fork_payload = {
        "label": "Forked branch",
        "instructions": {"text": "Fork this branch."},
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "runtimeContextPolicy": "fresh_agent_run",
        "idempotencyKey": "mm-1091:fork",
    }
    first_fork = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json=fork_payload,
    )
    second_fork = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json=fork_payload,
    )
    assert first_fork.status_code == 201
    assert second_fork.status_code == 201
    fork_id = first_fork.json()["branchId"]
    assert fork_id == second_fork.json()["branchId"]
    assert first_fork.json()["parentBranchId"] == branch_id

    first_compare = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id},
    )
    second_compare = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id},
    )
    assert first_compare.status_code == 200
    assert second_compare.status_code == 200
    comparison = first_compare.json()
    assert comparison["branchId"] == branch_id
    assert comparison["againstBranchId"] == fork_id
    assert comparison["summaryRef"].startswith("artifact://checkpoint-branch-comparisons/")
    assert comparison["summaryRef"] == second_compare.json()["summaryRef"]
    assert comparison["comparisonRecord"]["recordType"] == "checkpoint_branch_comparison"
    assert comparison["comparisonRecord"]["quality"] == {
        "branchGateVerdict": "unknown",
        "againstGateVerdict": "unknown",
    }
    assert comparison["comparisonRecord"]["artifactRefs"][
        "output.branch_comparison.metadata.json"
    ].startswith("artifact://checkpoint-branch-comparisons/")
    assert comparison["comparisonRecord"]["evidenceRefs"]["branchCheckpointRef"] == (
        "artifact://checkpoints/after-implement"
    )


@pytest.mark.asyncio
async def test_checkpoint_branch_promotion_requires_head_gate_side_effects_and_approval(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-promote"),
    )
    branch_id = created.json()["branchId"]
    await _set_branch_head(checkpoint_branch_client, branch_id)

    missing_approval = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "policyRequiresApproval": True,
            "idempotencyKey": "mm-1091:promote-missing-approval",
        },
    )
    assert missing_approval.status_code == 409
    assert missing_approval.json()["detail"]["code"] == "approval_required"

    promoted = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "approvalEvidence": {"artifactRef": "artifact://approval"},
            "policyRequiresApproval": True,
            "idempotencyKey": "mm-1091:promote",
        },
    )
    assert promoted.status_code == 200
    assert promoted.json()["state"] == "promoted"
    assert promoted.json()["currentHeadStepExecutionId"] == (
        "mm:wf-branch:run:implement:execution:2"
    )
    async for session in checkpoint_branch_client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        branch = (
            await session.execute(
                select(WorkflowCheckpointBranch).where(
                    WorkflowCheckpointBranch.branch_id == branch_id
                )
            )
        ).scalar_one()
        operation = (
            await session.execute(
                select(WorkflowCheckpointBranchOperation).where(
                    WorkflowCheckpointBranchOperation.operation
                    == "checkpoint_branch.promote"
                )
            )
        ).scalar_one()
        promotion_artifacts = (
            await session.execute(
                select(WorkflowCheckpointBranchArtifact).where(
                    WorkflowCheckpointBranchArtifact.branch_id == branch_id,
                    WorkflowCheckpointBranchArtifact.artifact_kind.in_(
                        {
                            "output.branch_promotion.record.json",
                            "output.branch_promotion.downstream_invalidation.json",
                        }
                    ),
                )
            )
        ).scalars().all()

    assert branch.promotion_evidence["acceptedOutputRefs"][
        "headStepExecutionId"
    ] == "mm:wf-branch:run:implement:execution:2"
    assert branch.promotion_evidence["gitEvidence"]["repository"] == (
        "MoonLadderStudios/MoonMind"
    )
    assert branch.promotion_evidence["downstreamInvalidation"]["status"] == (
        "not_required"
    )
    assert branch.promotion_evidence["policyEvidence"][
        "policyRequiresApproval"
    ] is True
    assert operation.response_payload["recordType"] == "checkpoint_branch_promotion"
    assert operation.response_payload["artifactRefs"][
        "output.branch_promotion.record.json"
    ].startswith("artifact://checkpoint-branch-promotions/")
    assert {
        artifact.artifact_kind for artifact in promotion_artifacts
    } == {
        "output.branch_promotion.record.json",
        "output.branch_promotion.downstream_invalidation.json",
    }

    head_mismatch = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:3",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "approvalEvidence": {"artifactRef": "artifact://approval"},
            "policyRequiresApproval": True,
            "idempotencyKey": "mm-1091:promote-head-mismatch",
        },
    )
    assert head_mismatch.status_code == 409
    assert head_mismatch.json()["detail"]["code"] == "checkpoint_head_mismatch"


@pytest.mark.asyncio
async def test_checkpoint_branch_api_fails_closed_for_invalid_source_provider_budget_and_refs(
    checkpoint_branch_client: AsyncClient,
) -> None:
    invalid_source = _create_payload("mm-1091:invalid-source")
    invalid_source["source"] = {
        **invalid_source["source"],  # type: ignore[index]
        "checkpointRef": "artifact://checkpoints/missing",
    }
    source_response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=invalid_source,
    )
    assert source_response.status_code == 409
    assert source_response.json()["detail"]["code"] == "checkpoint_invalid"

    digest_mismatch = _create_payload("mm-1091:digest")
    digest_mismatch["source"] = {
        **digest_mismatch["source"],  # type: ignore[index]
        "checkpointDigest": "sha256:not-the-checkpoint-digest",
    }
    digest_response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=digest_mismatch,
    )
    assert digest_response.status_code == 409
    assert digest_response.json()["detail"]["code"] == "checkpoint_digest_mismatch"

    workspace_policy_response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json={
            **_create_payload("mm-1091:workspace-policy"),
            "workspacePolicy": "continue_from_previous_execution",
            "publishMode": "branch",
            "gitWorkBranch": "mm/mm-1091/workspace-policy",
        },
    )
    assert workspace_policy_response.status_code == 409
    assert (
        workspace_policy_response.json()["detail"]["code"]
        == "workspace_policy_incompatible"
    )

    conflict_payload = _create_payload("mm-1091:idempotency-conflict")
    first_conflict = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=conflict_payload,
    )
    second_conflict = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json={**conflict_payload, "label": "Same idempotency key, different body"},
    )
    assert first_conflict.status_code == 201
    assert second_conflict.status_code == 409
    assert second_conflict.json()["detail"]["code"] == "idempotency_key_conflict"

    provider_response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json={
            **_create_payload("mm-1091:provider"),
            "runtimeContextPolicy": "external_provider_continuation",
        },
    )
    assert provider_response.status_code == 409
    assert (
        provider_response.json()["detail"]["code"]
        == "provider_continuation_unsupported"
    )

    budget_response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json={**_create_payload("mm-1091:budget"), "maxBudgetUsd": 0},
    )
    assert budget_response.status_code == 409
    assert budget_response.json()["detail"]["code"] == "budget_exhausted"

    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-protected-ref"),
    )
    protected_ref = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/publish",
        json={
            "mode": "branch",
            "repository": "Moon/Mind",
            "baseBranch": "main",
            "headBranch": "main",
            "provider": "github",
            "idempotencyKey": "mm-1091:protected-ref",
        },
    )
    protected_head_ref = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/publish",
        json={
            "mode": "branch",
            "repository": "Moon/Mind",
            "baseBranch": "main",
            "headBranch": "HEAD",
            "provider": "github",
            "idempotencyKey": "mm-1091:protected-head-ref",
        },
    )
    assert protected_ref.status_code == 409
    assert protected_ref.json()["detail"]["code"] == "protected_branch_ref"
    assert protected_head_ref.status_code == 409
    assert protected_head_ref.json()["detail"]["code"] == "protected_branch_ref"

    unsupported_provider = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/publish",
        json={
            "mode": "pull_request",
            "repository": "Moon/Mind",
            "baseBranch": "main",
            "headBranch": "mm/mm-1091/gitlab",
            "provider": "gitlab",
            "idempotencyKey": "mm-1091:unsupported-provider",
        },
    )
    assert unsupported_provider.status_code == 409
    assert unsupported_provider.json()["detail"]["code"] == (
        "provider_continuation_unsupported"
    )

    await _set_branch_head(checkpoint_branch_client, created.json()["branchId"])

    bad_gate = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "failed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "idempotencyKey": "mm-1091:bad-gate",
        },
    )
    assert bad_gate.status_code == 409
    assert bad_gate.json()["detail"]["reason"] == "gate_evidence_not_passing"

    unsafe_side_effects = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "unsafe"},
            "idempotencyKey": "mm-1091:unsafe-side-effects",
        },
    )
    assert unsafe_side_effects.status_code == 409
    assert unsafe_side_effects.json()["detail"]["reason"] == (
        "side_effect_disposition_required"
    )


@pytest.mark.asyncio
async def test_checkpoint_branch_api_fails_closed_for_non_owner(
    checkpoint_branch_denied_client: AsyncClient,
) -> None:
    response = await checkpoint_branch_denied_client.get(
        "/api/executions/mm:wf-branch/checkpoints"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "execution_not_found"


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_records_operation_payload(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-compare-ledger"),
    )
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for comparison."},
            "idempotencyKey": "mm-1091:fork-compare-ledger",
        },
    )
    compared = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/compare",
        params={"against": forked.json()["branchId"]},
    )
    assert compared.status_code == 200

    # The compare path is a read API, but the comparison record itself is durable
    # evidence referenced by clients and stored in the operation ledger.
    operation = None
    async for session in checkpoint_branch_client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        result = await session.execute(
            select(WorkflowCheckpointBranchOperation).where(
                WorkflowCheckpointBranchOperation.operation
                == "checkpoint_branch.compare"
            )
        )
        operation = result.scalar_one()
    assert operation is not None
    assert operation.response_payload["summaryRef"] == compared.json()["summaryRef"]
    assert operation.response_payload["quality"] == {
        "branchGateVerdict": "unknown",
        "againstGateVerdict": "unknown",
    }
    assert operation.response_payload["artifactRefs"][
        "output.branch_comparison.range_diff.patch"
    ].startswith("artifact://checkpoint-branch-comparisons/")


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_refreshes_when_branch_head_changes(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-compare-refresh"),
    )
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for comparison refresh."},
            "idempotencyKey": "mm-1091:fork-compare-refresh",
        },
    )
    branch_id = created.json()["branchId"]
    fork_id = forked.json()["branchId"]

    first_compare = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id},
    )
    assert first_compare.status_code == 200

    async for session in checkpoint_branch_client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        result = await session.execute(
            select(WorkflowCheckpointBranch).where(
                WorkflowCheckpointBranch.branch_id == branch_id
            )
        )
        branch = result.scalar_one()
        branch.current_head_checkpoint_ref = "artifact://checkpoints/after-review"
        branch.current_head_commit = "review-head"
        await session.commit()

    second_compare = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id},
    )
    assert second_compare.status_code == 200
    assert second_compare.json()["summaryRef"] != first_compare.json()["summaryRef"]
    assert second_compare.json()["comparisonRecord"]["summary"]["branchHeadCommit"] == (
        "review-head"
    )
    assert second_compare.json()["comparisonRecord"]["evidenceRefs"][
        "branchCheckpointRef"
    ] == "artifact://checkpoints/after-review"

    async for session in checkpoint_branch_client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        result = await session.execute(
            select(WorkflowCheckpointBranchOperation).where(
                WorkflowCheckpointBranchOperation.operation
                == "checkpoint_branch.compare"
            )
        )
        operations = result.scalars().all()
    assert len(operations) == 2
