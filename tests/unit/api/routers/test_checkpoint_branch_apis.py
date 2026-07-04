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
    _scoped_operation_digest,
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
from api_service.services.checkpoint_branch_service import (
    build_branch_turn_launch_idempotency_key,
)
from moonmind.schemas.checkpoint_branch_models import CheckpointBranchTurnLaunchRequest


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
        client.app = app  # type: ignore[attr-defined]
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
    head_commit: str | None = None,
) -> None:
    async for session in client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        result = await session.execute(
            select(WorkflowCheckpointBranch).where(
                WorkflowCheckpointBranch.branch_id == branch_id
            )
        )
        branch = result.scalar_one()
        branch.current_head_step_execution_id = step_execution_id
        if head_commit is not None:
            branch.current_head_commit = head_commit
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


def test_checkpoint_branch_git_context_does_not_synthesize_known_refs() -> None:
    record = SimpleNamespace(
        parameters={
            "git": {
                "repository": "MoonLadderStudios/MoonMind",
                "startingBranch": "feature/from-workflow",
                "currentRef": "feature/from-workflow",
            }
        },
        memo={},
        search_attributes={},
    )

    context = _checkpoint_branch_git_context(record)

    assert context["baseBranch"] == "feature/from-workflow"
    assert context["currentRef"] == "feature/from-workflow"
    assert context["knownRefs"] == set()


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

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
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

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
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
async def test_checkpoint_branch_api_launch_rejects_unsupported_provider_continuation(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1104:create-provider-launch"),
    )
    branch_id = created.json()["branchId"]
    turns = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )
    branch_turn_id = turns.json()["items"][0]["branchTurnId"]
    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        turn = await session.get(WorkflowCheckpointBranchTurn, branch_turn_id)
        assert turn is not None
        turn.runtime_context_policy = "external_provider_continuation"
        await session.commit()

    launched = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns/"
        f"{branch_turn_id}/launch",
        json={
            "createdStepExecutionId": "mm:wf-branch:run-branch:implement:execution:4",
            "providerSessionId": "omnigent-session-1",
            "diagnosticsRef": "artifact://diagnostics/mm-1104",
        },
    )

    assert launched.status_code == 409
    assert launched.json()["detail"]["code"] == "provider_continuation_unsupported"


@pytest.mark.asyncio
async def test_checkpoint_branch_api_launch_rejects_branch_provider_continuation(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1104:create-provider-branch-launch"),
    )
    branch_id = created.json()["branchId"]
    turns = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )
    branch_turn_id = turns.json()["items"][0]["branchTurnId"]
    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        branch = await session.get(WorkflowCheckpointBranch, branch_id)
        turn = await session.get(WorkflowCheckpointBranchTurn, branch_turn_id)
        assert branch is not None
        assert turn is not None
        branch.runtime_context_policy = "external_provider_continuation"
        turn.runtime_context_policy = None
        await session.commit()

    launched = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns/"
        f"{branch_turn_id}/launch",
        json={
            "createdStepExecutionId": "mm:wf-branch:run-branch:implement:execution:5",
            "providerSessionId": "omnigent-session-branch",
            "diagnosticsRef": "artifact://diagnostics/mm-1104-branch",
        },
    )

    assert launched.status_code == 409
    assert launched.json()["detail"]["code"] == "provider_continuation_unsupported"


@pytest.mark.asyncio
async def test_checkpoint_branch_api_launch_replays_before_provider_continuation_check(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1104:create-provider-replay"),
    )
    branch_id = created.json()["branchId"]
    turns = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )
    branch_turn_id = turns.json()["items"][0]["branchTurnId"]
    payload = {
        "createdStepExecutionId": "mm:wf-branch:run-branch:implement:execution:6",
        "providerSessionId": "omnigent-session-replay",
        "diagnosticsRef": "artifact://diagnostics/mm-1104-replay",
    }
    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        branch = await session.get(WorkflowCheckpointBranch, branch_id)
        turn = await session.get(WorkflowCheckpointBranchTurn, branch_turn_id)
        assert branch is not None
        assert turn is not None
        branch.runtime_context_policy = "external_provider_continuation"
        turn.runtime_context_policy = "external_provider_continuation"
        turn.status = "running"
        turn.created_step_execution_id = str(payload["createdStepExecutionId"])
        turn.provider_session_id = str(payload["providerSessionId"])
        turn.diagnostics_ref = str(payload["diagnosticsRef"])
        launch_request = CheckpointBranchTurnLaunchRequest.model_validate(payload)
        launch_key = build_branch_turn_launch_idempotency_key(
            workflow_id="mm:wf-branch",
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
        )
        session.add(
            WorkflowCheckpointBranchOperation(
                workflow_id="mm:wf-branch",
                branch_id=branch_id,
                branch_turn_id=branch_turn_id,
                operation="checkpoint_branch.turn.launch",
                idempotency_key=launch_key,
                request_digest=_scoped_operation_digest(
                    launch_request,
                    scope={
                        "branchId": branch_id,
                        "branchTurnId": branch_turn_id,
                        "operation": "checkpoint_branch.turn.launch",
                    },
                ),
                response_payload={
                    "immutableLaunchFields": {
                        "instructionRef": turn.instruction_ref,
                        "instructionDigest": turn.instruction_digest,
                        "sourceCheckpointRef": turn.source_checkpoint_ref,
                        "sourceCheckpointDigest": turn.source_checkpoint_digest,
                        "sourceStateKind": turn.source_state_kind,
                        "sourceStateRef": turn.source_state_ref,
                        "sourceStateDigest": turn.source_state_digest,
                        "workspacePolicy": turn.workspace_policy,
                        "runtimeContextPolicy": turn.runtime_context_policy,
                    }
                },
            )
        )
        await session.commit()

    replayed = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns/"
        f"{branch_turn_id}/launch",
        json=payload,
    )

    assert replayed.status_code == 200
    assert replayed.json()["createdStepExecutionId"] == payload["createdStepExecutionId"]


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

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
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
    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
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
    unsupported_continue = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/continue",
        json={
            **continue_payload,
            "runtimeContextPolicy": "external_provider_continuation",
            "idempotencyKey": "mm-1104:continue-provider",
        },
    )
    assert unsupported_continue.status_code == 409
    assert (
        unsupported_continue.json()["detail"]["code"]
        == "provider_continuation_unsupported"
    )

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
    unsupported_fork = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json={
            **fork_payload,
            "runtimeContextPolicy": "external_provider_continuation",
            "idempotencyKey": "mm-1104:fork-provider",
        },
    )
    assert unsupported_fork.status_code == 409
    assert (
        unsupported_fork.json()["detail"]["code"]
        == "provider_continuation_unsupported"
    )
    await _set_branch_head(checkpoint_branch_client, branch_id)

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
    assert comparison["comparisonRecord"]["branchIds"] == [branch_id, fork_id]
    assert comparison["comparisonRecord"]["baseCheckpointRef"] == (
        "artifact://checkpoints/after-implement"
    )
    assert comparison["comparisonRecord"]["gateVerdictSummaries"] == {
        branch_id: "unknown",
        fork_id: "unknown",
    }
    assert comparison["comparisonRecord"]["boundedSummaryRefs"] == [
        comparison["summaryRef"]
    ]
    assert set(comparison["comparisonRecord"]["diffRefs"]) == {
        "branchDiffRef",
        "againstDiffRef",
        "rangeDiffRef",
    }
    assert comparison["comparisonRecord"]["artifactRefs"][
        "output.branch_comparison.metadata.json"
    ].startswith("artifact://checkpoint-branch-comparisons/")
    assert comparison["comparisonRecord"]["evidenceRefs"]["branchCheckpointRef"] == (
        "artifact://checkpoints/after-implement"
    )

    promoted = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "policyEvidence": {"freshHeadValidated": True},
            "idempotencyKey": "mm-1091:promote-before-recompare",
        },
    )
    assert promoted.status_code == 200

    compare_after_promotion = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id},
    )
    assert compare_after_promotion.status_code == 200
    promoted_comparison = compare_after_promotion.json()
    assert promoted_comparison["summaryRef"] != comparison["summaryRef"]
    assert promoted_comparison["comparisonRecord"]["quality"] == {
        "branchGateVerdict": "passed",
        "againstGateVerdict": "unknown",
    }

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        comparison_operations = (
            await session.execute(
                select(WorkflowCheckpointBranchOperation).where(
                    WorkflowCheckpointBranchOperation.operation
                    == "checkpoint_branch.compare"
                )
            )
        ).scalars().all()
        comparison_artifacts = (
            await session.execute(
                select(WorkflowCheckpointBranchArtifact).where(
                    WorkflowCheckpointBranchArtifact.branch_id == branch_id,
                    WorkflowCheckpointBranchArtifact.artifact_kind.like(
                        "output.branch_comparison.%"
                    ),
                )
            )
        ).scalars().all()

    assert len(comparison_operations) == 2
    assert len(comparison_artifacts) == 12


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
            "policyEvidence": {"freshHeadValidated": True},
            "policyRequiresApproval": True,
            "idempotencyKey": "mm-1091:promote-missing-approval",
        },
    )
    assert missing_approval.status_code == 409
    assert missing_approval.json()["detail"]["code"] == "approval_required"

    conflicting_accepted_refs = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "acceptedOutputRefs": {
                "headStepExecutionId": "mm:wf-branch:run:implement:execution:stale"
            },
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "policyEvidence": {"freshHeadValidated": True},
            "idempotencyKey": "mm-1091:promote-conflicting-accepted-refs",
        },
    )
    assert conflicting_accepted_refs.status_code == 409
    assert conflicting_accepted_refs.json()["detail"]["code"] == (
        "accepted_output_refs_mismatch"
    )

    promoted = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "approvalEvidence": {"artifactRef": "artifact://approval"},
            "policyEvidence": {"freshHeadValidated": True},
            "policyRequiresApproval": True,
            "idempotencyKey": "mm-1091:promote",
        },
    )
    assert promoted.status_code == 200
    assert promoted.json()["state"] == "promoted"
    assert promoted.json()["currentHeadStepExecutionId"] == (
        "mm:wf-branch:run:implement:execution:2"
    )
    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
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
                    == "checkpoint_branch.promote",
                    WorkflowCheckpointBranchOperation.idempotency_key
                    == "mm-1091:promote",
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
            "policyEvidence": {"freshHeadValidated": True},
            "policyRequiresApproval": True,
            "idempotencyKey": "mm-1091:promote-head-mismatch",
        },
    )
    assert head_mismatch.status_code == 409
    assert head_mismatch.json()["detail"]["code"] == "expected_head_mismatch"
    assert head_mismatch.json()["detail"]["reason"] == (
        "expected_head_step_execution_mismatch"
    )


@pytest.mark.asyncio
async def test_checkpoint_branch_promotion_rejects_unverifiable_head_checkpoint(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1103:create-unverifiable-head"),
    )
    branch_id = created.json()["branchId"]
    await _set_branch_head(checkpoint_branch_client, branch_id)

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        branch = (
            await session.execute(
                select(WorkflowCheckpointBranch).where(
                    WorkflowCheckpointBranch.branch_id == branch_id
                )
            )
        ).scalar_one()
        branch.current_head_checkpoint_ref = None
        await session.commit()

    promoted = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "policyEvidence": {"freshHeadValidated": True},
            "idempotencyKey": "mm-1103:promote-unverifiable-head",
        },
    )

    assert promoted.status_code == 409
    assert promoted.json()["detail"]["code"] == "checkpoint_invalidity"
    assert promoted.json()["detail"]["reason"] == "head_checkpoint_ref_required"


@pytest.mark.asyncio
async def test_checkpoint_branch_promotion_requires_fresh_and_expected_git_head(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1103:create-promote-head-validation"),
    )
    branch_id = created.json()["branchId"]
    await _set_branch_head(
        checkpoint_branch_client,
        branch_id,
        head_commit="branch-head-1",
    )

    missing_fresh_validation = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "expectedHeadCommit": "branch-head-1",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "idempotencyKey": "mm-1103:promote-missing-fresh-head",
        },
    )
    assert missing_fresh_validation.status_code == 409
    assert missing_fresh_validation.json()["detail"] == {
        "code": "expected_head_mismatch",
        "reason": "fresh_branch_head_validation_required",
    }

    missing_expected_commit = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "policyEvidence": {"freshHeadValidated": True},
            "idempotencyKey": "mm-1103:promote-missing-expected-commit",
        },
    )
    assert missing_expected_commit.status_code == 409
    assert missing_expected_commit.json()["detail"] == {
        "code": "expected_head_mismatch",
        "reason": "expected_head_commit_required",
    }

    stale_expected_commit = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "expectedHeadCommit": "stale-head",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "policyEvidence": {"freshHeadValidated": True},
            "idempotencyKey": "mm-1103:promote-stale-expected-commit",
        },
    )
    assert stale_expected_commit.status_code == 409
    assert stale_expected_commit.json()["detail"] == {
        "code": "expected_head_mismatch",
        "reason": "expected_head_commit_mismatch",
    }

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        branch = (
            await session.execute(
                select(WorkflowCheckpointBranch).where(
                    WorkflowCheckpointBranch.branch_id == branch_id
                )
            )
        ).scalar_one()
        branch.current_head_commit = None
        await session.commit()

    unknown_branch_head_commit = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "expectedHeadCommit": "client-observed-head",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "policyEvidence": {"freshHeadValidated": True},
            "idempotencyKey": "mm-1103:promote-unknown-branch-head-commit",
        },
    )
    assert unknown_branch_head_commit.status_code == 409
    assert unknown_branch_head_commit.json()["detail"] == {
        "code": "expected_head_mismatch",
        "reason": "expected_head_commit_mismatch",
    }


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

    service = checkpoint_branch_client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        _get_service
    ]()
    record = service.describe_execution.return_value
    original_known_refs = record.parameters["git"].get("knownRefs")
    record.parameters["git"].pop("knownRefs", None)
    try:
        unknown_ref_response = await checkpoint_branch_client.post(
            "/api/executions/mm:wf-branch/checkpoint-branches",
            json=_create_payload("mm-1101:missing-known-refs"),
        )
        assert unknown_ref_response.status_code == 409
        assert unknown_ref_response.json()["detail"]["code"] == "unknown_ref"
    finally:
        if original_known_refs is not None:
            record.parameters["git"]["knownRefs"] = original_known_refs

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
            "policyEvidence": {"freshHeadValidated": True},
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
            "policyEvidence": {"freshHeadValidated": True},
            "idempotencyKey": "mm-1091:unsafe-side-effects",
        },
    )
    assert unsafe_side_effects.status_code == 409
    assert unsafe_side_effects.json()["detail"]["reason"] == (
        "side_effect_disposition_required"
    )


@pytest.mark.asyncio
async def test_checkpoint_branch_promotion_rejection_persists_audit_without_advancing(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1103:create-rejected-promotion-audit"),
    )
    branch_id = created.json()["branchId"]
    await _set_branch_head(checkpoint_branch_client, branch_id)

    rejected = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "failed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "policyEvidence": {"freshHeadValidated": True},
            "idempotencyKey": "mm-1103:rejected-promotion-audit",
        },
    )

    assert rejected.status_code == 409
    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
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
                    WorkflowCheckpointBranchOperation.idempotency_key
                    == "mm-1103:rejected-promotion-audit"
                )
            )
        ).scalar_one()

    assert branch.state != "promoted"
    assert operation.operation == "checkpoint_branch.promote"
    assert operation.response_payload == {
        "outcome": "rejected",
        "code": "side_effect_policy_blocked",
        "reason": "gate_evidence_not_passing",
        "branchId": branch_id,
    }


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
    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
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
async def test_checkpoint_branch_compare_returns_only_bounded_artifact_refs(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1103:create-bounded-compare"),
    )
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for bounded comparison."},
            "idempotencyKey": "mm-1103:fork-bounded-compare",
        },
    )

    compared = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/compare",
        params={"against": forked.json()["branchId"]},
    )

    assert compared.status_code == 200
    body = compared.json()
    record = body["comparisonRecord"]
    artifact_refs = record["artifactRefs"]
    assert record["evidenceRefs"]["baseCheckpointRef"] == (
        "artifact://checkpoints/after-implement"
    )
    assert set(artifact_refs) >= {
        "output.branch_comparison.summary.json",
        "output.branch_comparison.metadata.json",
        "output.branch_comparison.left_diff.patch",
        "output.branch_comparison.right_diff.patch",
        "output.branch_comparison.range_diff.patch",
        "output.branch_comparison.diagnostics.json",
    }
    assert body["summaryRef"] == artifact_refs["output.branch_comparison.summary.json"]
    assert body["diagnosticsRefs"] == [
        artifact_refs["output.branch_comparison.diagnostics.json"]
    ]
    assert "diff" not in record
    assert "diagnostics" not in record
    assert "password=" not in str(body).lower()
    assert "token=" not in str(body).lower()


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_fails_closed_for_incompatible_checkpoint_lineage(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1103:create-incompatible-lineage-left"),
    )
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for incompatible lineage comparison."},
            "idempotencyKey": "mm-1103:fork-incompatible-lineage-right",
        },
    )

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        right = (
            await session.execute(
                select(WorkflowCheckpointBranch).where(
                    WorkflowCheckpointBranch.branch_id == forked.json()["branchId"]
                )
            )
        ).scalar_one()
        right.source_checkpoint_ref = "artifact://checkpoints/unrelated-base"
        right.source_checkpoint_digest = "sha256:unrelatedcheckpointdigest"
        await session.commit()

    compared = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/compare",
        params={"against": forked.json()["branchId"]},
    )

    assert compared.status_code == 409
    assert compared.json()["detail"]["code"] == "incompatible_checkpoint_lineage"


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_fails_closed_for_source_digest_mismatch(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1103:create-digest-lineage-left"),
    )
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for digest mismatch comparison."},
            "idempotencyKey": "mm-1103:fork-digest-lineage-right",
        },
    )

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        right = (
            await session.execute(
                select(WorkflowCheckpointBranch).where(
                    WorkflowCheckpointBranch.branch_id == forked.json()["branchId"]
                )
            )
        ).scalar_one()
        right.source_checkpoint_ref = created.json()["sourceCheckpointRef"]
        right.source_checkpoint_digest = "sha256:differentcheckpointdigest"
        await session.commit()

    compared = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/compare",
        params={"against": forked.json()["branchId"]},
    )

    assert compared.status_code == 409
    assert compared.json()["detail"] == {
        "code": "incompatible_checkpoint_lineage",
        "reason": "base_checkpoint_digest_mismatch",
    }


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_allows_fork_from_current_head(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1103:create-current-head-parent"),
    )
    branch_id = created.json()["branchId"]

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        branch = (
            await session.execute(
                select(WorkflowCheckpointBranch).where(
                    WorkflowCheckpointBranch.branch_id == branch_id
                )
            )
        ).scalar_one()
        branch.current_head_checkpoint_ref = "artifact://checkpoints/parent-head"
        await session.commit()

    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json={
            "label": "Forked from current head",
            "instructions": {"text": "Fork from the current checkpoint head."},
            "idempotencyKey": "mm-1103:fork-current-head-child",
        },
    )

    compared = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": forked.json()["branchId"]},
    )

    assert compared.status_code == 200
    assert compared.json()["comparisonRecord"]["baseCheckpointRef"] == {
        "branch": created.json()["sourceCheckpointRef"],
        "against": "artifact://checkpoints/parent-head",
    }


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

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
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

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
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
