"""Unit coverage for MM-1091 checkpoint branch API paths."""

from __future__ import annotations

import asyncio
import json
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
from api_service.auth_providers import get_current_user, get_current_user_optional
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
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
from api_service.services.checkpoint_branch_turn_execution import (
    CheckpointBranchTurnExecutionOwner,
    build_branch_turn_execution_identity,
)


def _record_like(user: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        workflow_id="mm:wf-branch",
        run_id="run-branch",
        namespace="default",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_id=str(user.id),
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.EXECUTING,
        search_attributes={"mm_owner_id": str(user.id), "mm_owner_type": "user"},
        memo={
            "stepCheckpointRef": "artifact://checkpoints/after-implement",
            "latest_temporal_run_id": "run-branch",
            "repository": "MoonLadderStudios/MoonMind",
        },
        parameters={
            "repository": {
                "provider": "git",
                "connectionRef": "repository-connection:git-default",
                "repository": {"name": "MoonLadderStudios/MoonMind"},
                "branch": {"name": "feature/mm-1101-source"},
            },
            "git": {
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
            ],
        },
    )


def _override_user_dependencies(app: FastAPI, user: SimpleNamespace) -> None:
    user_dependencies = {
        dep.call
        for route in router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if getattr(dep.call, "__name__", "") in {
            "_current_user_fallback",
            "_strict_current_user",
            "_optional_current_user",
        }
    }
    if not user_dependencies:
        user_dependencies = {get_current_user(), get_current_user_optional()}

    def _current_user() -> SimpleNamespace:
        return user

    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = _current_user


@pytest_asyncio.fixture
async def checkpoint_branch_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
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
            "repository": {
                "provider": "git",
                "connectionRef": "repository-connection:git-default",
                "repository": {"name": "MoonLadderStudios/MoonMind"},
                "branch": {"name": "feature/mm-1101-source"},
            },
            "git": {
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

    async def _claim_without_temporal(
        owner: CheckpointBranchTurnExecutionOwner,
        *,
        workflow_id: str,
        branch_id: str,
        branch_turn_id: str,
        intent,
    ) -> WorkflowCheckpointBranchTurn:
        """Keep router tests focused on HTTP/persistence, not Temporal dispatch."""

        operator_key = (
            intent.idempotency_key
            if hasattr(intent, "idempotency_key")
            else str(intent["idempotencyKey"])
        )
        turn = await owner._session.get(WorkflowCheckpointBranchTurn, branch_turn_id)
        assert turn is not None
        if turn.created_step_execution_id:
            return turn
        branch = await owner._session.get(WorkflowCheckpointBranch, branch_id)
        assert branch is not None
        identity = build_branch_turn_execution_identity(
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
            logical_step_id=branch.logical_step_id or "checkpoint-branch",
            ordinal=1,
        )
        turn.diagnostics = {
            **(turn.diagnostics or {}),
            "operatorLaunchIdempotencyKey": operator_key,
        }
        claimed = await CheckpointBranchService(owner._session).claim_turn_execution(
            workflow_id=workflow_id,
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
            launch_idempotency_key=build_branch_turn_launch_idempotency_key(
                workflow_id=workflow_id,
                branch_id=branch_id,
                branch_turn_id=branch_turn_id,
            ),
            context_bundle_ref=f"artifact://context/{branch_turn_id}",
            step_execution_manifest_ref=f"artifact://manifest/{branch_turn_id}",
            created_step_execution_id=identity.step_execution_id,
            runtime_agent_run_id=identity.agent_run_workflow_id,
            diagnostics_ref=f"artifact://diagnostics/{branch_turn_id}",
            agent_request_ref=f"artifact://request/{branch_turn_id}",
            execution_workflow_id=identity.workflow_id,
        )
        await owner._session.commit()
        return claimed

    monkeypatch.setattr(CheckpointBranchTurnExecutionOwner, "launch", _claim_without_temporal)

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


async def _accept_server_owned_branch_head(
    client: AsyncClient, branch_id: str
) -> None:
    """Model the durable owner reaching terminal verification handoff."""

    async for session in client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        branch = await session.get(WorkflowCheckpointBranch, branch_id)
        assert branch is not None
        turn = (
            await session.execute(
                select(WorkflowCheckpointBranchTurn).where(
                    WorkflowCheckpointBranchTurn.branch_id == branch_id,
                    WorkflowCheckpointBranchTurn.created_step_execution_id
                    == branch.current_head_step_execution_id,
                )
            )
        ).scalar_one()
        checkpoint_ref = f"artifact://accepted/{turn.branch_turn_id}"
        turn.status = "checking"
        branch.state = "active"
        branch.current_head_checkpoint_ref = checkpoint_ref
        branch.current_head_checkpoint_digest = "sha256:" + "a" * 64
        session.add(
            WorkflowCheckpointBranchArtifact(
                branch_id=branch_id,
                branch_turn_id=turn.branch_turn_id,
                artifact_kind="output.branch_turn.checkpoint.json",
                artifact_ref=checkpoint_ref,
            )
        )
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


def test_checkpoint_branch_git_context_reads_provider_repository_target() -> None:
    record = SimpleNamespace(
        parameters={
            "repository": {
                "provider": "git",
                "connectionRef": "repository-connection:git-default",
                "repository": {"name": "MoonLadderStudios/MoonMind"},
                "branch": {"name": "feature/provider-target"},
            }
        },
        memo={},
        search_attributes={},
    )

    context = _checkpoint_branch_git_context(record)

    assert context["repository"] == "MoonLadderStudios/MoonMind"
    assert context["baseBranch"] == "feature/provider-target"
    assert context["currentRef"] == "feature/provider-target"


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
async def test_checkpoint_branch_create_persists_exact_agent_profile_snapshot(
    checkpoint_branch_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = {
        "profileId": "agent-profile-1",
        "version": 3,
        "digest": "sha256:profile-v3",
        "providerProfileRef": "provider-profile-1",
        "model": "gpt-5.4",
    }
    resolver = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(
        "api_service.api.routers.executions.resolve_agent_profile_snapshot",
        resolver,
    )
    payload = _create_payload("mm-3517:create-profile")
    payload["providerProfileRef"] = "provider-profile-1"
    payload["agentProfile"] = {"profileId": "agent-profile-1", "version": 3}

    response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches", json=payload
    )

    assert response.status_code == 201
    branch_id = response.json()["branchId"]
    resolver.assert_awaited_once()
    assert resolver.await_args.kwargs["selection"] == {
        "profileId": "agent-profile-1",
        "version": 3,
        "providerProfileRef": "provider-profile-1",
    }
    assert resolver.await_args.kwargs["consumer_id"] == branch_id
    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        branch = await session.get(WorkflowCheckpointBranch, branch_id)
    assert branch is not None
    assert branch.diagnostics["runtimeSelection"]["agentProfileSnapshot"] == snapshot


@pytest.mark.asyncio
async def test_checkpoint_branch_create_rejects_profile_provider_conflict(
    checkpoint_branch_client: AsyncClient,
) -> None:
    payload = _create_payload("mm-3517:profile-conflict")
    payload["providerProfileRef"] = "provider-profile-1"
    payload["agentProfile"] = {
        "profileId": "agent-profile-1",
        "providerProfileRef": "provider-profile-2",
    }

    response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches", json=payload
    )

    assert response.status_code == 409


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
async def test_checkpoint_branch_api_create_dispatches_server_owned_turn_idempotently(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1100:create-launch"),
    )
    replayed = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1100:create-launch"),
    )
    branch_id = created.json()["branchId"]
    turns = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )
    body = turns.json()["items"][0]
    branch_turn_id = body["branchTurnId"]

    assert created.status_code == 201
    assert replayed.status_code == 201
    assert replayed.json()["branchId"] == branch_id
    assert body["branchTurnId"] == branch_turn_id
    assert body["status"] == "preparing"
    assert body["createdStepExecutionId"].startswith("checkpoint-branch-turn:")
    assert body["runtimeAgentRunId"].startswith("checkpoint-branch-agent:")
    assert "checkpointRef" not in body
    assert body["contextBundleRef"] == f"artifact://context/{branch_turn_id}"
    assert body["stepExecutionManifestRef"] == f"artifact://manifest/{branch_turn_id}"
    assert body["diagnostics"]["launchIdempotencyKey"] == (
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
    assert len(artifact_rows) == 7
    assert {
        artifact.artifact_kind for artifact in artifact_rows
    } == {
        "runtime.branch.workspace_restore.json",
        "runtime.branch.git_binding.json",
        "runtime.branch_turn.context_bundle.json",
        "runtime.branch_turn.agent_request.json",
        "input.branch_turn.instructions.md",
        "output.branch_turn.step_execution_manifest.json",
        "output.branch_turn.launch_diagnostics.json",
    }


@pytest.mark.asyncio
async def test_remediation_checkpoint_branch_repair_creates_fresh_branch_from_context(
    checkpoint_branch_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(
        id=uuid4(),
        email="checkpoint-branches@example.com",
        is_superuser=True,
        roles=[],
    )
    record = _record_like(user)
    link = SimpleNamespace(
        remediation_workflow_id="mm:remediation-1",
        remediation_run_id="run-remediation-1",
        target_workflow_id="mm:wf-branch",
        target_run_id="run-branch",
        context_artifact_ref="ctx-remediation-1",
        latest_action_summary=None,
    )
    checkpoint_branch_client.app.dependency_overrides[_get_service] = lambda: SimpleNamespace(
        describe_execution=AsyncMock(return_value=record),
        list_remediation_targets=AsyncMock(return_value=[link]),
    )

    class _ArtifactService:
        async def read(self, *, artifact_id: str, principal: str):
            assert artifact_id == "ctx-remediation-1"
            return (
                SimpleNamespace(
                    metadata_json={"artifact_type": "remediation.context"}
                ),
                json.dumps(
                    {
                        "schemaVersion": "v1",
                        "target": {
                            "workflowId": "mm:wf-branch",
                            "runId": "run-branch",
                        },
                        "selectedSteps": [
                            {
                                "logicalStepId": "implement",
                                "executionOrdinal": 2,
                                "checkpointBoundary": "after_execution",
                                "checkpointRef": "artifact://checkpoints/after-implement",
                                "checkpointDigest": "sha256:checkpointdigest",
                            }
                        ],
                    }
                ).encode(),
            )

    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda session: _ArtifactService(),
    )
    snapshot = {
        "profileId": "agent-profile-remediation",
        "version": 2,
        "digest": "sha256:remediation-profile-v2",
        "providerProfileRef": "provider-profile-remediation",
    }
    resolver = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(
        "api_service.api.routers.executions.resolve_agent_profile_snapshot",
        resolver,
    )

    repair_payload = {
        "checkpointRef": "artifact://checkpoints/after-implement",
        "instructions": {"text": "Repair with corrected instructions."},
        "idempotencyKey": "MM-1119:remediation-branch",
        "maxBudgetUsd": 2.0,
        "providerProfileRef": "provider-profile-remediation",
        "agentProfile": {"profileId": "agent-profile-remediation", "version": 2},
    }
    first = await checkpoint_branch_client.post(
        "/api/executions/mm:remediation-1/remediation/checkpoint-branches",
        json=repair_payload,
    )
    second = await checkpoint_branch_client.post(
        "/api/executions/mm:remediation-1/remediation/checkpoint-branches",
        json=repair_payload,
    )
    changed_agent_profile = await checkpoint_branch_client.post(
        "/api/executions/mm:remediation-1/remediation/checkpoint-branches",
        json={
            **repair_payload,
            "agentProfile": {
                "profileId": "agent-profile-remediation",
                "version": 3,
            },
        },
    )
    changed_provider_profile = await checkpoint_branch_client.post(
        "/api/executions/mm:remediation-1/remediation/checkpoint-branches",
        json={
            **repair_payload,
            "providerProfileRef": "provider-profile-other",
        },
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["branchId"] == first.json()["branchId"]
    assert first.json()["runtimeContextPolicy"] == "fresh_agent_run"
    assert changed_agent_profile.status_code == 409
    assert changed_agent_profile.json()["detail"]["code"] == (
        "idempotency_key_conflict"
    )
    assert changed_provider_profile.status_code == 409
    assert changed_provider_profile.json()["detail"]["code"] == (
        "idempotency_key_conflict"
    )

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        operation = (
            await session.execute(
                select(WorkflowCheckpointBranchOperation).where(
                    WorkflowCheckpointBranchOperation.idempotency_key
                    == "MM-1119:remediation-branch"
                )
            )
        ).scalar_one()
        branch = await session.get(WorkflowCheckpointBranch, first.json()["branchId"])
        turn = await session.get(
            WorkflowCheckpointBranchTurn,
            operation.branch_turn_id,
        )
        remediation_input = (
            await session.execute(
                select(WorkflowCheckpointBranchArtifact).where(
                    WorkflowCheckpointBranchArtifact.branch_id == branch.branch_id,
                    WorkflowCheckpointBranchArtifact.branch_turn_id
                    == operation.branch_turn_id,
                    WorkflowCheckpointBranchArtifact.artifact_kind
                    == "input.branch_turn.remediation_context.json",
                )
            )
        ).scalar_one()

    assert branch is not None
    assert turn is not None
    assert branch.workflow_id == "mm:wf-branch"
    assert branch.diagnostics["repairActionKind"] == (
        "checkpoint_branch.create_from_remediation_context"
    )
    assert branch.diagnostics["runtimeSelection"]["agentProfileSnapshot"] == snapshot
    assert branch.diagnostics["runtimeSelection"]["maxBudgetUsd"] == 2.0
    assert turn.diagnostics["remediationContextRef"] == "ctx-remediation-1"
    assert turn.diagnostics["maxBudgetUsd"] == 2.0
    assert remediation_input.artifact_ref == "ctx-remediation-1"
    assert operation.response_payload["runtimeSelection"]["agentProfileSnapshot"] == snapshot
    resolver.assert_awaited_once()
    assert operation.response_payload["remediation"] == {
        "workflowId": "mm:remediation-1",
        "runId": "run-remediation-1",
        "contextArtifactRef": "ctx-remediation-1",
        "checkpointRef": "artifact://checkpoints/after-implement",
        "actionKind": "checkpoint_branch.create_from_remediation_context",
        "runtimeContextPolicy": "fresh_agent_run",
    }


@pytest.mark.asyncio
async def test_remediation_checkpoint_branch_repair_resolves_recovery_boundary(
    checkpoint_branch_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(
        id=uuid4(),
        email="checkpoint-branches@example.com",
        is_superuser=True,
        roles=[],
    )
    record = _record_like(user)
    record.memo = {
        **record.memo,
        "recoveryCheckpointRef": "artifact://checkpoints/before-recovery",
    }
    link = SimpleNamespace(
        remediation_workflow_id="mm:remediation-recovery",
        remediation_run_id="run-remediation-1",
        target_workflow_id="mm:wf-branch",
        target_run_id="run-branch",
        context_artifact_ref="ctx-remediation-recovery",
        latest_action_summary=None,
    )
    checkpoint_branch_client.app.dependency_overrides[_get_service] = lambda: SimpleNamespace(
        describe_execution=AsyncMock(return_value=record),
        list_remediation_targets=AsyncMock(return_value=[link]),
    )

    class _ArtifactService:
        async def read(self, *, artifact_id: str, principal: str):
            return (
                SimpleNamespace(
                    metadata_json={"artifact_type": "remediation.context"}
                ),
                json.dumps(
                    {
                        "schemaVersion": "v1",
                        "target": {
                            "workflowId": "mm:wf-branch",
                            "runId": "run-branch",
                        },
                        "selectedSteps": [
                            {
                                "checkpointRef": "artifact://checkpoints/before-recovery",
                            }
                        ],
                    }
                ).encode(),
            )

    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda session: _ArtifactService(),
    )

    response = await checkpoint_branch_client.post(
        "/api/executions/mm:remediation-recovery/remediation/checkpoint-branches",
        json={
            "checkpointRef": "artifact://checkpoints/before-recovery",
            "instructions": {"text": "Repair from recovery checkpoint."},
            "idempotencyKey": "MM-1119:recovery-boundary",
        },
    )

    assert response.status_code == 201
    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        branch = await session.get(WorkflowCheckpointBranch, response.json()["branchId"])

    assert branch is not None
    assert branch.source_checkpoint_boundary == "before_recovery_restoration"


@pytest.mark.asyncio
async def test_remediation_checkpoint_branch_repair_rejects_empty_idempotency_key(
    checkpoint_branch_client: AsyncClient,
) -> None:
    response = await checkpoint_branch_client.post(
        "/api/executions/mm:remediation-1/remediation/checkpoint-branches",
        json={
            "checkpointRef": "artifact://checkpoints/after-implement",
            "instructions": {"text": "Repair with corrected instructions."},
            "idempotencyKey": "",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_remediation_checkpoint_branch_repair_rejects_empty_context_body(
    checkpoint_branch_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(
        id=uuid4(),
        email="checkpoint-branches@example.com",
        is_superuser=True,
        roles=[],
    )
    record = _record_like(user)
    link = SimpleNamespace(
        remediation_workflow_id="mm:remediation-empty-context",
        remediation_run_id="run-remediation-1",
        target_workflow_id="mm:wf-branch",
        target_run_id="run-branch",
        context_artifact_ref="ctx-remediation-empty",
    )
    checkpoint_branch_client.app.dependency_overrides[_get_service] = lambda: SimpleNamespace(
        describe_execution=AsyncMock(return_value=record),
        list_remediation_targets=AsyncMock(return_value=[link]),
    )

    class _ArtifactService:
        async def read(self, *, artifact_id: str, principal: str):
            return (
                SimpleNamespace(
                    metadata_json={"artifact_type": "remediation.context"}
                ),
                None,
            )

    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda session: _ArtifactService(),
    )

    response = await checkpoint_branch_client.post(
        "/api/executions/mm:remediation-empty-context/remediation/checkpoint-branches",
        json={
            "checkpointRef": "artifact://checkpoints/after-implement",
            "instructions": {"text": "Repair with corrected instructions."},
            "idempotencyKey": "MM-1119:empty-context",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "invalid_remediation_context",
        "reason": "empty_body",
    }


@pytest.mark.asyncio
async def test_remediation_checkpoint_branch_repair_fails_closed_for_unselected_checkpoint(
    checkpoint_branch_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(
        id=uuid4(),
        email="checkpoint-branches@example.com",
        is_superuser=True,
        roles=[],
    )
    record = _record_like(user)
    link = SimpleNamespace(
        remediation_workflow_id="mm:remediation-missing-checkpoint",
        remediation_run_id="run-remediation-1",
        target_workflow_id="mm:wf-branch",
        target_run_id="run-branch",
        context_artifact_ref="ctx-remediation-missing",
    )
    checkpoint_branch_client.app.dependency_overrides[_get_service] = lambda: SimpleNamespace(
        describe_execution=AsyncMock(return_value=record),
        list_remediation_targets=AsyncMock(return_value=[link]),
    )

    class _ArtifactService:
        async def read(self, *, artifact_id: str, principal: str):
            return (
                SimpleNamespace(
                    metadata_json={"artifact_type": "remediation.context"}
                ),
                json.dumps(
                    {
                        "schemaVersion": "v1",
                        "target": {
                            "workflowId": "mm:wf-branch",
                            "runId": "run-branch",
                        },
                        "selectedSteps": [],
                    }
                ).encode(),
            )

    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda session: _ArtifactService(),
    )

    response = await checkpoint_branch_client.post(
        "/api/executions/mm:remediation-missing-checkpoint/remediation/checkpoint-branches",
        json={
            "checkpointRef": "artifact://checkpoints/after-implement",
            "instructions": {"text": "Repair with corrected instructions."},
            "idempotencyKey": "MM-1119:missing-checkpoint",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "checkpoint_invalidity",
        "reason": "checkpoint_not_selected_in_remediation_context",
    }


@pytest.mark.asyncio
async def test_checkpoint_branch_api_launch_rejects_caller_runtime_authority(
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

    endpoint = (
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns/"
        f"{branch_turn_id}/launch"
    )
    for field, value in {
        "createdStepExecutionId": "caller-step",
        "runtimeAgentRunId": "caller-run",
        "providerSessionId": "caller-session",
        "workspaceBaseline": {"kind": "git_ref", "ref": "main"},
        "runtimeRequestRef": "artifact://caller/request",
        "runtimeResultRef": "artifact://caller/result",
        "diagnosticsRef": "artifact://caller/diagnostics",
        "checkpointRef": "artifact://caller/checkpoint",
        "hostRef": "host://caller",
        "leaseRef": "lease://caller",
    }.items():
        response = await checkpoint_branch_client.post(
            endpoint,
            json={"idempotencyKey": f"old-field-{field}", field: value},
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == "extra_forbidden"


@pytest.mark.asyncio
async def test_checkpoint_branch_api_serializes_concurrent_launch_and_recovers_preclaim(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1100:create-concurrent-launch"),
    )
    branch_id = created.json()["branchId"]
    turns = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )
    branch_turn_id = turns.json()["items"][0]["branchTurnId"]
    endpoint = (
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns/"
        f"{branch_turn_id}/launch"
    )
    launch_body = {"idempotencyKey": "mm-1100:explicit-launch"}

    first, raced = await asyncio.gather(
        checkpoint_branch_client.post(endpoint, json=launch_body),
        checkpoint_branch_client.post(endpoint, json=launch_body),
    )

    assert first.status_code == raced.status_code == 200
    assert first.json()["createdStepExecutionId"] == raced.json()[
        "createdStepExecutionId"
    ]
    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        operation = (
            await session.execute(
                select(WorkflowCheckpointBranchOperation).where(
                    WorkflowCheckpointBranchOperation.operation
                    == "checkpoint_branch.turn.launch",
                    WorkflowCheckpointBranchOperation.idempotency_key
                    == launch_body["idempotencyKey"],
                )
            )
        ).scalar_one()
        operation.response_payload = {
            "branchId": branch_id,
            "branchTurnId": branch_turn_id,
            "immutableLaunchFields": {},
            "claimState": "claimed",
        }
        await session.commit()

    recovered = await checkpoint_branch_client.post(endpoint, json=launch_body)
    assert recovered.status_code == 200
    assert recovered.json()["createdStepExecutionId"] == first.json()[
        "createdStepExecutionId"
    ]


@pytest.mark.asyncio
async def test_checkpoint_branch_api_rejects_nonfresh_runtime_policy_on_new_writes(
    checkpoint_branch_client: AsyncClient,
) -> None:
    payload = _create_payload("mm-1104:create-provider-launch")
    payload["runtimeContextPolicy"] = "external_provider_continuation"

    response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches", json=payload
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_checkpoint_branch_api_retains_historical_runtime_identity_reads(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1104:historical-read"),
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
        turn.provider_session_id = "historical-provider-session"
        await session.commit()

    response = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["providerSessionId"] == (
        "historical-provider-session"
    )


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
    assert published.json()["state"] == "preparing"
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
    create_payload = _create_payload("mm-1091:create-branching")
    create_payload["maxBudgetUsd"] = 5.0
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=create_payload,
    )
    branch_id = created.json()["branchId"]
    root_turns = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )
    root_turn_id = root_turns.json()["items"][0]["branchTurnId"]
    await _accept_server_owned_branch_head(checkpoint_branch_client, branch_id)

    continue_payload = {
        "label": "Continued branch",
        "instructions": {"text": "Continue this branch."},
        "workspacePolicy": "continue_from_previous_execution",
        "runtimeContextPolicy": "fresh_agent_run",
        "maxBudgetUsd": 4.0,
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
    assert continued_turn.parent_turn_id == root_turn_id
    assert continued_turn.diagnostics["maxBudgetUsd"] == 4.0
    assert branch.diagnostics["runtimeSelection"]["maxBudgetUsd"] == 5.0
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
    assert unsupported_continue.status_code == 422
    await _accept_server_owned_branch_head(checkpoint_branch_client, branch_id)

    fork_payload = {
        "label": "Forked branch",
        "instructions": {"text": "Fork this branch."},
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "runtimeContextPolicy": "fresh_agent_run",
        "maxBudgetUsd": 3.0,
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
    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        forked_branch = await session.get(WorkflowCheckpointBranch, fork_id)
    assert forked_branch is not None
    assert forked_branch.diagnostics["runtimeSelection"]["maxBudgetUsd"] == 3.0
    assert fork_id == second_fork.json()["branchId"]
    assert first_fork.json()["parentBranchId"] == branch_id
    assert first_fork.json()["parentTurnId"] == first_continue.json()["branchTurnId"]
    unsupported_fork = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json={
            **fork_payload,
            "runtimeContextPolicy": "external_provider_continuation",
            "idempotencyKey": "mm-1104:fork-provider",
        },
    )
    assert unsupported_fork.status_code == 422
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
    assert comparison["comparisonRecord"]["baseCheckpointRef"] == {
        "branch": "artifact://checkpoints/after-implement",
        "against": comparison["comparisonRecord"]["evidenceRefs"][
            "againstCheckpointRef"
        ],
    }
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
        f"artifact://accepted/{first_continue.json()['branchTurnId']}"
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
    assert provider_response.status_code == 422

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
async def test_checkpoint_branch_api_allows_operator_for_foreign_record(
    checkpoint_branch_denied_client: AsyncClient,
) -> None:
    # Single-user (#4351): the admitted operator reads every instance
    # execution without a human-owner lookup.
    response = await checkpoint_branch_denied_client.get(
        "/api/executions/mm:wf-branch/checkpoints"
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_records_operation_payload(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-compare-ledger"),
    )
    await _accept_server_owned_branch_head(
        checkpoint_branch_client, created.json()["branchId"]
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
    await _accept_server_owned_branch_head(
        checkpoint_branch_client, created.json()["branchId"]
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
    assert record["evidenceRefs"]["baseCheckpointRef"] == {
        "branch": "artifact://checkpoints/after-implement",
        "against": record["evidenceRefs"]["againstCheckpointRef"],
    }
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
    await _accept_server_owned_branch_head(
        checkpoint_branch_client, created.json()["branchId"]
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
    await _accept_server_owned_branch_head(
        checkpoint_branch_client, created.json()["branchId"]
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
    await _accept_server_owned_branch_head(checkpoint_branch_client, branch_id)

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
        checkpoint = (
            await session.execute(
                select(WorkflowCheckpointBranchArtifact).where(
                    WorkflowCheckpointBranchArtifact.branch_id == branch_id,
                    WorkflowCheckpointBranchArtifact.artifact_kind
                    == "output.branch_turn.checkpoint.json",
                )
            )
        ).scalar_one()
        checkpoint.artifact_ref = "artifact://checkpoints/parent-head"
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
    await _accept_server_owned_branch_head(
        checkpoint_branch_client, created.json()["branchId"]
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
    ].startswith("artifact://accepted/")

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


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_reports_objective_rubric_provenance_without_side_effects(
    checkpoint_branch_client: AsyncClient,
) -> None:
    """MoonLadderStudios/MoonMind#2215: explicit pair + objective read/report journey."""

    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-2215:create-provenance"),
    )
    branch_id = created.json()["branchId"]
    await _accept_server_owned_branch_head(checkpoint_branch_client, branch_id)
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for provenance comparison."},
            "idempotencyKey": "mm-2215:fork-provenance",
        },
    )
    fork_id = forked.json()["branchId"]

    compared = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={
            "against": fork_id,
            "objective": "Decide which candidate keeps the API contract fix.",
            "rubricId": "checkpoint-branch-gates",
        },
    )

    assert compared.status_code == 200
    body = compared.json()
    record = body["comparisonRecord"]
    assert record["objective"] == "Decide which candidate keeps the API contract fix."
    assert record["rubricId"] == "checkpoint-branch-gates"
    candidate_ids = {item["candidateId"] for item in record["candidates"]}
    assert candidate_ids == {branch_id, fork_id}
    assert all(
        item["workflowId"] == "mm:wf-branch" for item in record["candidates"]
    )
    assert record["evidenceRefs"]["branchCheckpointRef"].startswith("artifact://")
    assert record["evidenceRefs"]["againstCheckpointRef"].startswith("artifact://")
    assert body["diagnosticsRefs"]
    assert record["provenance"]["newStore"] is False
    assert record["provenance"]["newEngine"] is False
    assert (
        "checkpoint_branch.compare" in record["provenance"]["store"]
    )
    assert record["sideEffects"] == {
        "launchedNewWork": False,
        "changedDefaults": False,
        "deployed": False,
        "promotedBranch": False,
        "publishedCode": False,
        "newInferenceAuthorized": False,
    }
    assert "observedUsd" in record["costs"]["branch"]
    assert "estimatedUsd" in record["costs"]["branch"]
    assert record["costs"]["provenance"]
    assert record["measurementLimits"]


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_reports_no_winner_for_failed_candidate(
    checkpoint_branch_client: AsyncClient,
) -> None:
    """MoonLadderStudios/MoonMind#2215: failed samples stay observations with no winner."""

    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-2215:create-no-winner"),
    )
    branch_id = created.json()["branchId"]
    await _accept_server_owned_branch_head(checkpoint_branch_client, branch_id)
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for no-winner comparison."},
            "idempotencyKey": "mm-2215:fork-no-winner",
        },
    )
    fork_id = forked.json()["branchId"]

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        right = (
            await session.execute(
                select(WorkflowCheckpointBranch).where(
                    WorkflowCheckpointBranch.branch_id == fork_id
                )
            )
        ).scalar_one()
        right.state = "failed"
        await session.commit()

    compared = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={
            "against": fork_id,
            "objective": "Observe a failed candidate without declaring success.",
        },
    )

    assert compared.status_code == 200
    record = compared.json()["comparisonRecord"]
    assert record["winner"]["candidate"] == "none"
    assert record["winner"]["universalClaim"] is False
    assert record["winner"]["reason"]


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_resumes_same_report_for_same_objective(
    checkpoint_branch_client: AsyncClient,
) -> None:
    """MoonLadderStudios/MoonMind#2215: resuming a report reruns no candidate."""

    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-2215:create-resume"),
    )
    branch_id = created.json()["branchId"]
    await _accept_server_owned_branch_head(checkpoint_branch_client, branch_id)
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for resume comparison."},
            "idempotencyKey": "mm-2215:fork-resume",
        },
    )
    fork_id = forked.json()["branchId"]

    async def _compare(objective: str) -> dict:
        response = await checkpoint_branch_client.get(
            f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
            params={"against": fork_id, "objective": objective},
        )
        assert response.status_code == 200
        return response.json()

    first = await _compare("Which candidate is closer to the fix?")
    second = await _compare("Which candidate is closer to the fix?")
    assert second["summaryRef"] == first["summaryRef"]
    third = await _compare("A different explicit objective.")
    assert third["summaryRef"] != first["summaryRef"]
    assert third["comparisonRecord"]["objective"] == "A different explicit objective."


async def _count_compare_operations(client: AsyncClient) -> int:
    async for session in client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        result = await session.execute(
            select(WorkflowCheckpointBranchOperation).where(
                WorkflowCheckpointBranchOperation.operation
                == "checkpoint_branch.compare"
            )
        )
        return len(result.scalars().all())
    raise AssertionError("unreachable")


async def _count_branches(client: AsyncClient) -> int:
    async for session in client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        result = await session.execute(select(WorkflowCheckpointBranch))
        return len(result.scalars().all())
    raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_checkpoint_branch_comparison_preview_reports_bounded_preview_without_launch(
    checkpoint_branch_client: AsyncClient,
) -> None:
    """MoonLadderStudios/MoonMind#2215: previewing saved results launches nothing."""

    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-2215:create-preview"),
    )
    branch_id = created.json()["branchId"]
    await _accept_server_owned_branch_head(checkpoint_branch_client, branch_id)
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for preview comparison."},
            "idempotencyKey": "mm-2215:fork-preview",
        },
    )
    fork_id = forked.json()["branchId"]
    branches_before = await _count_branches(checkpoint_branch_client)
    operations_before = await _count_compare_operations(checkpoint_branch_client)

    previewed = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches/comparison-preview",
        json={
            "objective": "Compare two saved candidates.",
            "rubricId": "checkpoint-branch-gates",
            "allowNewRuns": False,
            "candidates": [
                {"candidateId": "left", "branchId": branch_id},
                {"candidateId": "right", "branchId": fork_id},
            ],
        },
    )

    assert previewed.status_code == 200
    preview = previewed.json()
    assert preview["selectedRunCount"] == 0
    assert preview["reuseExistingCount"] == 2
    assert preview["willLaunchNewWork"] is False
    assert preview["authorizesNewInference"] is False
    assert preview["noCartesianExpansion"] is True
    assert preview["creationPath"]
    assert await _count_branches(checkpoint_branch_client) == branches_before
    assert await _count_compare_operations(checkpoint_branch_client) == (
        operations_before
    )


@pytest.mark.asyncio
async def test_checkpoint_branch_comparison_preview_rejects_cartesian_expansion(
    checkpoint_branch_client: AsyncClient,
) -> None:
    """MoonLadderStudios/MoonMind#2215: no eager expansion across model/provider grids."""

    matrix = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches/comparison-preview",
        json={
            "objective": "Compare everything.",
            "allowNewRuns": True,
            "expandCartesian": True,
            "candidates": [
                {"candidateId": "left", "sourceIntentRef": "artifact://intent/a"},
                {"candidateId": "right", "sourceIntentRef": "artifact://intent/a"},
            ],
        },
    )
    assert matrix.status_code == 422

    oversized = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches/comparison-preview",
        json={
            "objective": "Compare too many candidates.",
            "allowNewRuns": False,
            "candidates": [
                {"candidateId": f"candidate-{index}"} for index in range(5)
            ],
        },
    )
    assert oversized.status_code == 422


@pytest.mark.asyncio
async def test_checkpoint_branch_comparison_preview_requires_bounded_configs_for_new_runs(
    checkpoint_branch_client: AsyncClient,
) -> None:
    """MoonLadderStudios/MoonMind#2215: requested runs need bounds, shared intent, budgets."""

    unbounded = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches/comparison-preview",
        json={
            "objective": "Request new candidates without bounds.",
            "allowNewRuns": True,
            "candidates": [
                {
                    "candidateId": "left",
                    "sourceIntentRef": "artifact://intent/shared",
                },
                {
                    "candidateId": "right",
                    "sourceIntentRef": "artifact://intent/shared",
                    "maxBudgetUsd": 2.0,
                },
            ],
        },
    )
    assert unbounded.status_code == 422

    diverged = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches/comparison-preview",
        json={
            "objective": "Request new candidates with diverged intent.",
            "allowNewRuns": True,
            "candidates": [
                {
                    "candidateId": "left",
                    "sourceIntentRef": "artifact://intent/one",
                    "maxBudgetUsd": 2.0,
                },
                {
                    "candidateId": "right",
                    "sourceIntentRef": "artifact://intent/two",
                    "maxBudgetUsd": 2.0,
                },
            ],
        },
    )
    assert diverged.status_code == 422

    branches_before = await _count_branches(checkpoint_branch_client)
    bounded = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches/comparison-preview",
        json={
            "objective": "Request two bounded candidates.",
            "allowNewRuns": True,
            "candidates": [
                {
                    "candidateId": "left",
                    "sourceIntentRef": "artifact://intent/shared",
                    "maxBudgetUsd": 2.0,
                },
                {
                    "candidateId": "right",
                    "sourceIntentRef": "artifact://intent/shared",
                    "maxBudgetUsd": 3.0,
                },
            ],
        },
    )
    assert bounded.status_code == 200
    preview = bounded.json()
    assert preview["selectedRunCount"] == 2
    assert preview["willLaunchNewWork"] is False
    assert preview["authorizesNewInference"] is False
    assert preview["costDeltas"]["totalMaxBudgetUsd"] == 5.0
    assert preview["privacyChanges"]
    assert preview["authorityChanges"]


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_uses_persisted_verifier_verdicts(
    checkpoint_branch_client: AsyncClient,
) -> None:
    """MoonLadderStudios/MoonMind#2215: persisted verification decides the observed winner."""

    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-2215:create-verifier-verdict"),
    )
    branch_id = created.json()["branchId"]
    await _accept_server_owned_branch_head(checkpoint_branch_client, branch_id)
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for verifier verdict comparison."},
            "idempotencyKey": "mm-2215:fork-verifier-verdict",
        },
    )
    fork_id = forked.json()["branchId"]

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        for target_id, verdict in (
            (branch_id, "FULLY_IMPLEMENTED"),
            (fork_id, "ADDITIONAL_WORK_NEEDED"),
        ):
            result = await session.execute(
                select(WorkflowCheckpointBranch).where(
                    WorkflowCheckpointBranch.branch_id == target_id
                )
            )
            branch = result.scalar_one()
            branch.latest_verification_verdict = verdict
            branch.latest_verification_ref = f"artifact://verdict/{target_id}"
        await session.commit()

    compared = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={
            "against": fork_id,
            "objective": "Prefer the verified candidate.",
        },
    )

    assert compared.status_code == 200
    record = compared.json()["comparisonRecord"]
    assert "gate_verdict_unavailable" not in record["missingEvidence"]
    assert record["winner"]["candidate"] == branch_id


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_refreshes_when_diagnostics_change(
    checkpoint_branch_client: AsyncClient,
) -> None:
    """MoonLadderStudios/MoonMind#2215: new cost evidence invalidates the cached report."""

    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-2215:create-diagnostics-refresh"),
    )
    branch_id = created.json()["branchId"]
    await _accept_server_owned_branch_head(checkpoint_branch_client, branch_id)
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for diagnostics refresh comparison."},
            "idempotencyKey": "mm-2215:fork-diagnostics-refresh",
        },
    )
    fork_id = forked.json()["branchId"]

    first_compare = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id, "objective": "Track cost evidence."},
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
        branch.diagnostics = {
            **(branch.diagnostics or {}),
            "observedCostUsd": 4.5,
        }
        await session.commit()

    second_compare = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id, "objective": "Track cost evidence."},
    )
    assert second_compare.status_code == 200
    assert second_compare.json()["summaryRef"] != first_compare.json()["summaryRef"]
    assert (
        second_compare.json()["comparisonRecord"]["costs"]["branch"]["observedUsd"]
        == 4.5
    )


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_reads_budget_from_runtime_selection(
    checkpoint_branch_client: AsyncClient,
) -> None:
    """MoonLadderStudios/MoonMind#2215: ordinary admission budgets appear in provenance."""

    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-2215:create-runtime-budget"),
    )
    branch_id = created.json()["branchId"]
    await _accept_server_owned_branch_head(checkpoint_branch_client, branch_id)
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for budget provenance comparison."},
            "idempotencyKey": "mm-2215:fork-runtime-budget",
        },
    )
    fork_id = forked.json()["branchId"]

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        result = await session.execute(
            select(WorkflowCheckpointBranch).where(
                WorkflowCheckpointBranch.branch_id == branch_id
            )
        )
        branch = result.scalar_one()
        branch.diagnostics = {
            **(branch.diagnostics or {}),
            "runtimeSelection": {
                **((branch.diagnostics or {}).get("runtimeSelection") or {}),
                "maxBudgetUsd": 7.5,
            },
        }
        await session.commit()

    compared = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id, "objective": "Track budget provenance."},
    )

    assert compared.status_code == 200
    assert (
        compared.json()["comparisonRecord"]["costs"]["branch"]["budgetBoundUsd"]
        == 7.5
    )


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_rejects_unsupported_rubric(
    checkpoint_branch_client: AsyncClient,
) -> None:
    """MoonLadderStudios/MoonMind#2215: unapplied rubrics cannot be claimed."""

    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-2215:create-rubric-reject"),
    )
    branch_id = created.json()["branchId"]
    await _accept_server_owned_branch_head(checkpoint_branch_client, branch_id)
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for rubric rejection."},
            "idempotencyKey": "mm-2215:fork-rubric-reject",
        },
    )
    fork_id = forked.json()["branchId"]

    compared = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id, "rubricId": "custom-quality-rubric"},
    )

    assert compared.status_code in {422, 409}


@pytest.mark.asyncio
async def test_checkpoint_branch_comparison_preview_honest_workspace_privacy(
    checkpoint_branch_client: AsyncClient,
) -> None:
    """MoonLadderStudios/MoonMind#2215: isolation is attested only when guaranteed."""

    previewed = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches/comparison-preview",
        json={
            "objective": "Request runs without an isolating workspace policy.",
            "allowNewRuns": True,
            "candidates": [
                {
                    "candidateId": "left",
                    "sourceIntentRef": "artifact://intent/shared",
                    "maxBudgetUsd": 2.0,
                },
                {
                    "candidateId": "right",
                    "sourceIntentRef": "artifact://intent/shared",
                    "maxBudgetUsd": 2.0,
                    "workspacePolicy": "continue_from_previous_execution",
                },
            ],
        },
    )

    assert previewed.status_code == 200
    assert (
        "isolated_workspace_per_requested_run"
        not in previewed.json()["privacyChanges"]
    )


@pytest.mark.asyncio
async def test_checkpoint_branch_comparison_preview_rejects_headless_branch_reuse(
    checkpoint_branch_client: AsyncClient,
) -> None:
    """MoonLadderStudios/MoonMind#2215: branches without completed results are not reusable."""

    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-2215:create-headless-reuse"),
    )
    branch_id = created.json()["branchId"]
    await _accept_server_owned_branch_head(checkpoint_branch_client, branch_id)
    second = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-2215:create-headless-reuse-second"),
    )
    second_id = second.json()["branchId"]

    async for session in checkpoint_branch_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        result = await session.execute(
            select(WorkflowCheckpointBranch).where(
                WorkflowCheckpointBranch.branch_id == second_id
            )
        )
        branch = result.scalar_one()
        branch.current_head_step_execution_id = None
        await session.commit()

    previewed = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches/comparison-preview",
        json={
            "objective": "Reuse a branch with no completed result.",
            "allowNewRuns": False,
            "candidates": [
                {"candidateId": "left", "branchId": branch_id},
                {"candidateId": "right", "branchId": second_id},
            ],
        },
    )

    assert previewed.status_code in {404, 409, 422}
