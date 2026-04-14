"""Unit tests for Temporal execution lifecycle API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from temporalio.service import RPCError, RPCStatusCode

from api_service.api.routers.executions import (
    _get_service,
    get_temporal_client,
    _serialize_execution,
    router,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import MoonMindWorkflowState, TemporalWorkflowType
from moonmind.config.settings import settings
from moonmind.workflows.temporal.service import ExecutionDependencySummary
from moonmind.workflows.temporal import (
    TemporalExecutionNotFoundError,
    TemporalExecutionValidationError,
)
from moonmind.schemas.temporal_models import (
    ExecutionProgressModel,
    StepLedgerSnapshotModel,
)


class _QueryHandle:
    def __init__(self, *, progress=None, ledger=None, error: Exception | None = None) -> None:
        self._progress = progress
        self._ledger = ledger
        self._error = error

    async def query(self, name: str):
        if self._error is not None:
            raise self._error
        if name == "get_progress":
            return self._progress
        if name == "get_step_ledger":
            return self._ledger
        raise AssertionError(f"Unexpected query name: {name}")


def _override_query_client(
    app: FastAPI,
    *,
    progress=None,
    ledger=None,
    error: Exception | None = None,
) -> SimpleNamespace:
    handle = _QueryHandle(progress=progress, ledger=ledger, error=error)
    client = SimpleNamespace(get_workflow_handle=Mock(return_value=handle))
    app.dependency_overrides[get_temporal_client] = lambda: client
    return client


def _override_user_dependencies(app: FastAPI, *, is_superuser: bool) -> SimpleNamespace:
    mock_user = SimpleNamespace(
        id=uuid4(),
        email="executions@example.com",
        is_active=True,
        is_superuser=is_superuser,
    )
    user_dependencies = {
        dep.call
        for route in router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if dep.call.__name__ == "_current_user_fallback"
    }
    if not user_dependencies:
        user_dependencies = {get_current_user()}

    def _current_user() -> SimpleNamespace:
        return mock_user

    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = _current_user
    return mock_user


def _build_execution_record(
    *,
    workflow_type: TemporalWorkflowType = TemporalWorkflowType.RUN,
    state: MoonMindWorkflowState = MoonMindWorkflowState.EXECUTING,
    owner_id: str = "user-123",
) -> SimpleNamespace:
    now = datetime.now(UTC)
    entry = (
        "manifest" if workflow_type is TemporalWorkflowType.MANIFEST_INGEST else "run"
    )
    return SimpleNamespace(
        namespace="moonmind",
        workflow_id="mm:wf-1",
        run_id="run-2",
        workflow_type=workflow_type,
        state=state,
        close_status=None,
        search_attributes={
            "mm_owner_id": owner_id,
            "mm_owner_type": "user" if owner_id != "system" else "system",
            "mm_entry": entry,
            "mm_repo": "Moon/Mind",
            "mm_continue_as_new_cause": "manual_rerun",
        },
        memo={
            "title": "Temporal task",
            "summary": "Waiting on review.",
            "continue_as_new_cause": "manual_rerun",
            "latest_temporal_run_id": "run-2",
        },
        artifact_refs=["art_123"],
        manifest_ref=(
            "art_manifest_1"
            if workflow_type is TemporalWorkflowType.MANIFEST_INGEST
            else None
        ),
        plan_ref=(
            "art_plan_1"
            if workflow_type is TemporalWorkflowType.MANIFEST_INGEST
            else None
        ),
        parameters=(
            {
                "requestedBy": {"type": "user", "id": "user-1"},
                "executionPolicy": {
                    "failurePolicy": "best_effort",
                    "maxConcurrency": 3,
                },
                "manifestNodes": [
                    {"nodeId": "node-a", "state": "ready"},
                    {"nodeId": "node-b", "state": "running"},
                ],
            }
            if workflow_type is TemporalWorkflowType.MANIFEST_INGEST
            else {}
        ),
        paused=False,
        waiting_reason=None,
        attention_required=False,
        created_at=now,
        started_at=now,
        updated_at=now,
        closed_at=None,
        owner_id=owner_id,
        owner_type="user" if owner_id != "system" else "system",
        entry=entry,
        integration_state=None,
    )


def _override_temporal_client(app: FastAPI) -> AsyncMock:
    client = AsyncMock()
    app.dependency_overrides[get_temporal_client] = lambda: client
    return client


@pytest.fixture
def client() -> Iterator[tuple[TestClient, AsyncMock, SimpleNamespace]]:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    user = _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        yield test_client, service, user

    app.dependency_overrides.clear()


def _client_with_service() -> Iterator[tuple[TestClient, AsyncMock]]:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        yield test_client, mock_service

    app.dependency_overrides.clear()


def test_list_executions_passes_temporal_filters_for_admin() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.list_executions.return_value = SimpleNamespace(
        items=[],
        next_page_token=None,
        count=0,
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)

    owner_id = uuid4()
    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "workflowType": "MoonMind.Run",
                "state": "executing",
                "entry": "run",
                "ownerType": "user",
                "ownerId": str(owner_id),
                "repo": "Moon/Mind",
                "integration": "github",
                "pageSize": 25,
                "nextPageToken": "token-123",
            },
        )

    assert response.status_code == 200
    kwargs = mock_service.list_executions.await_args.kwargs
    assert kwargs["workflow_type"] == "MoonMind.Run"
    assert kwargs["state"] == "executing"
    assert kwargs["entry"] == "run"
    assert kwargs["owner_type"] == "user"
    assert kwargs["owner_id"] == str(owner_id)
    assert kwargs["repo"] == "Moon/Mind"
    assert kwargs["integration"] == "github"
    assert kwargs["page_size"] == 25
    assert kwargs["next_page_token"] == "token-123"


def test_list_executions_rejects_non_admin_owner_type_override() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions", params={"ownerType": "system"})

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "execution_forbidden"
    mock_service.list_executions.assert_not_awaited()


def test_step_ledger_contract_models_serialize_using_public_aliases() -> None:
    progress = ExecutionProgressModel.model_validate(
        {
            "total": 1,
            "pending": 0,
            "ready": 0,
            "running": 0,
            "awaitingExternal": 0,
            "reviewing": 0,
            "succeeded": 1,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Prepare workspace",
            "updatedAt": "2026-04-07T12:00:00Z",
        }
    )
    snapshot = StepLedgerSnapshotModel.model_validate(
        {
            "workflowId": "wf-1",
            "runId": "run-1",
            "runScope": "latest",
            "steps": [
                {
                    "logicalStepId": "prepare",
                    "order": 1,
                    "title": "Prepare workspace",
                    "tool": {"type": "skill", "name": "repo.prepare", "version": "1"},
                    "dependsOn": [],
                    "status": "succeeded",
                    "waitingReason": None,
                    "attentionRequired": False,
                    "attempt": 1,
                    "startedAt": "2026-04-07T12:00:00Z",
                    "updatedAt": "2026-04-07T12:00:00Z",
                    "summary": "Workspace prepared",
                    "checks": [],
                    "refs": {
                        "childWorkflowId": None,
                        "childRunId": None,
                        "taskRunId": None,
                    },
                    "artifacts": {
                        "outputSummary": None,
                        "outputPrimary": None,
                        "runtimeStdout": None,
                        "runtimeStderr": None,
                        "runtimeMergedLogs": None,
                        "runtimeDiagnostics": None,
                        "providerSnapshot": None,
                    },
                    "lastError": None,
                }
            ],
        }
    )

    assert progress.model_dump(by_alias=True, mode="json")["awaitingExternal"] == 0
    dumped_snapshot = snapshot.model_dump(by_alias=True, mode="json")
    assert dumped_snapshot["runScope"] == "latest"
    assert dumped_snapshot["steps"][0]["logicalStepId"] == "prepare"


def test_list_executions_uses_owner_id_without_owner_type_for_non_admin() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.list_executions.return_value = SimpleNamespace(
        items=[],
        next_page_token=None,
        count=0,
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    mock_user = _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions")

    assert response.status_code == 200
    kwargs = mock_service.list_executions.await_args.kwargs
    assert kwargs["owner_id"] == str(mock_user.id)
    assert kwargs["owner_type"] is None


def test_list_executions_allows_explicit_user_owner_type_for_non_admin() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.list_executions.return_value = SimpleNamespace(
        items=[],
        next_page_token=None,
        count=0,
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    mock_user = _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions", params={"ownerType": "user"})

    assert response.status_code == 200
    kwargs = mock_service.list_executions.await_args.kwargs
    assert kwargs["owner_id"] == str(mock_user.id)
    assert kwargs["owner_type"] == "user"


def test_create_task_shaped_execution_rejects_invalid_required_capabilities() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "requiredCapabilities": 1,
                    "task": {
                        "instructions": "Ship the Temporal integration.",
                    },
                },
            },
        )

    assert response.status_code == 422
    assert (
        response.json()["detail"]["message"]
        == "payload.requiredCapabilities must be a JSON array of strings."
    )
    mock_service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_more_than_10_dependencies(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    
    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "instructions": "Ship the Temporal integration.",
                    "dependsOn": [f"dep-{i}" for i in range(11)]
                },
            },
        },
    )

    assert response.status_code == 422
    assert "payload.task.dependsOn can have a maximum of 10 items" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_more_than_50_steps(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    steps = [{"title": f"Step {i}", "instructions": f"Do step {i}."} for i in range(51)]
    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "instructions": "Too many steps.",
                    "steps": steps,
                },
            },
        },
    )

    assert response.status_code == 422
    assert "payload.task.steps can have a maximum of 50 items" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_dedupes_and_normalizes_dependencies(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "instructions": "Ship the Temporal integration.",
                    "dependsOn": ["dep-1", " dep-2 ", "", "dep-1", "dep-3"]
                },
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    kwargs = service.create_execution.call_args.kwargs
    assert kwargs["initial_parameters"]["task"]["dependsOn"] == ["dep-1", "dep-2", "dep-3"]


def test_create_task_shaped_execution_prefers_task_depends_on(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "dependsOn": ["legacy-dep"],
                "task": {
                    "instructions": "Ship the Temporal integration.",
                    "dependsOn": []
                },
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    kwargs = service.create_execution.call_args.kwargs
    assert "dependsOn" not in kwargs["initial_parameters"]["task"]


def test_create_task_shaped_execution_maps_instructions_and_tool_for_temporal(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "priority": 2,
            "maxAttempts": 4,
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "codex",
                "requiredCapabilities": ["git"],
                "task": {
                    "instructions": "Fix failing Temporal run.",
                    "runtime": {
                        "mode": "codex",
                        "model": "gpt-5-codex",
                        "effort": "high",
                    },
                    "skill": {
                        "id": "pr-resolver",
                        "args": {"repo": "MoonLadderStudios/MoonMind", "pr": "42"},
                    },
                    "git": {
                        "startingBranch": "feature/resolve-pr",
                        "targetBranch": "codex/pr-resolver",
                    },
                },
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    initial_parameters = called_kwargs["initial_parameters"]

    assert initial_parameters["instructions"] == "Fix failing Temporal run."
    assert initial_parameters["task"]["tool"]["type"] == "skill"
    assert initial_parameters["task"]["tool"]["name"] == "pr-resolver"
    assert initial_parameters["task"]["tool"]["version"] == "1.0"
    assert initial_parameters["task"]["inputs"] == {
        "repo": "MoonLadderStudios/MoonMind",
        "pr": "42",
    }
    assert initial_parameters["task"]["skill"] == {
        "name": "pr-resolver",
        "version": "1.0",
    }
    assert initial_parameters["task"]["git"] == {
        "startingBranch": "feature/resolve-pr",
        "targetBranch": "codex/pr-resolver",
    }


def test_create_task_shaped_execution_preserves_proposal_and_skill_intent(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "task": {
                    "instructions": "Improve managed-session proposals.",
                    "runtime": {"mode": "codex"},
                    "proposeTasks": True,
                    "proposalPolicy": {
                        "targets": ["project", "moonmind"],
                        "maxItems": {"project": 2, "moonmind": 1},
                        "minSeverityForMoonMind": "medium",
                        "defaultRuntime": "gemini_cli",
                    },
                    "skills": {
                        "sets": ["deployment-default", "proposal-quality"],
                        "include": [
                            {"name": "moonmind-doc-writer", "version": "2.3.0"}
                        ],
                        "exclude": ["legacy-proposer"],
                        "materializationMode": "hybrid",
                    },
                    "steps": [
                        {
                            "id": "review",
                            "instructions": "Review the proposal contract.",
                            "skills": {
                                "sets": ["docs-review"],
                                "include": [{"name": "architecture-review"}],
                            },
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]

    assert initial_parameters["proposeTasks"] is True
    assert initial_parameters["task"]["proposeTasks"] is True
    assert initial_parameters["task"]["proposalPolicy"] == {
        "targets": ["project", "moonmind"],
        "maxItems": {"project": 2, "moonmind": 1},
        "minSeverityForMoonMind": "medium",
        "defaultRuntime": "gemini_cli",
    }
    assert initial_parameters["task"]["skills"] == {
        "sets": ["deployment-default", "proposal-quality"],
        "include": [{"name": "moonmind-doc-writer", "version": "2.3.0"}],
        "exclude": ["legacy-proposer"],
        "materializationMode": "hybrid",
    }
    assert initial_parameters["task"]["steps"][0]["skills"] == {
        "sets": ["docs-review"],
        "include": [{"name": "architecture-review"}],
    }


def test_create_task_shaped_execution_rejects_invalid_proposal_policy(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "instructions": "Improve managed-session proposals.",
                    "proposalPolicy": {
                        "targets": ["side-channel"],
                    },
                },
            },
        },
    )

    assert response.status_code == 422
    assert "task.proposalPolicy.targets" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_accepts_provider_profile_alias() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.create_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                default_model="gpt-5-codex",
            )
        )
    )
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex",
                    "task": {
                        "instructions": "Fix failing Temporal run.",
                        "runtime": {
                            "mode": "codex",
                            "providerProfile": "codex-provider-profile",
                        },
                    },
                },
            },
        )

    assert response.status_code == 201
    initial_parameters = mock_service.create_execution.await_args.kwargs["initial_parameters"]
    assert initial_parameters["profileId"] == "codex-provider-profile"
    assert initial_parameters["model"] == "gpt-5-codex"
    assert initial_parameters["modelSource"] == "provider_profile_default"
    app.dependency_overrides.clear()


def test_create_task_shaped_execution_preserves_task_title_and_publish_overrides(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "title": "Fix login redirect",
                    "instructions": "Update OAuth callback behavior.",
                    "publish": {
                        "mode": "pr",
                        "prTitle": "PR: Ensure OAuth redirect is correct",
                        "prBody": "Adds integration tests and updates callback routing.",
                    },
                },
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    initial_parameters = called_kwargs["initial_parameters"]

    assert initial_parameters["task"]["title"] == "Fix login redirect"
    assert initial_parameters["task"]["publish"]["prTitle"] == "PR: Ensure OAuth redirect is correct"
    assert initial_parameters["task"]["publish"]["prBody"] == "Adds integration tests and updates callback routing."


def test_create_task_shaped_execution_defaults_runtime_into_parameters(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()
    monkeypatch.setattr(settings.workflow, "default_task_runtime", "codex_cli")

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "task": {
                    "title": "Resolve queued PR",
                    "instructions": "Run pr-resolver for the branch.",
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["targetRuntime"] == "codex_cli"
    assert initial_parameters["task"]["runtime"]["mode"] == "codex_cli"


def test_create_task_shaped_execution_preserves_steps_and_uses_step_title_defaults(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "task": {
                    "runtime": {
                        "mode": "gemini_cli",
                    },
                    "steps": [
                        {
                            "id": "tpl:demo:1.0.0:01",
                            "title": "Clarify the create-task recovery plan",
                            "instructions": "Audit the regression and list the missing controls.",
                            "skill": {
                                "id": "speckit-clarify",
                                "args": {"feature": "task-create"},
                                "requiredCapabilities": ["git", "github"],
                            },
                        },
                        {
                            "id": "tpl:demo:1.0.0:02",
                            "title": "Implement the restored builder",
                            "instructions": "Restore presets and multi-step submission.",
                        },
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    initial_parameters = called_kwargs["initial_parameters"]

    assert called_kwargs["title"] == "Clarify the create-task recovery plan"
    assert (
        called_kwargs["summary"]
        == "Audit the regression and list the missing controls."
    )
    assert initial_parameters["stepCount"] == 2
    assert initial_parameters["task"]["steps"] == [
        {
            "id": "tpl:demo:1.0.0:01",
            "title": "Clarify the create-task recovery plan",
            "instructions": "Audit the regression and list the missing controls.",
            "skill": {
                "id": "speckit-clarify",
                "args": {"feature": "task-create"},
                "requiredCapabilities": ["git", "github"],
            },
        },
        {
            "id": "tpl:demo:1.0.0:02",
            "title": "Implement the restored builder",
            "instructions": "Restore presets and multi-step submission.",
        },
    ]


def test_create_task_shaped_execution_rejects_pr_resolver_without_selector_or_instructions(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "runtime": {"mode": "gemini_cli"},
                    "tool": {
                        "type": "skill",
                        "name": "pr-resolver",
                        "version": "1.0",
                    },
                }
            },
        },
    )

    assert response.status_code == 422
    assert (
        response.json()["detail"]["message"]
        == "pr-resolver task requires payload.task.instructions, payload.task.inputs.pr, "
        "or payload.task.git.startingBranch."
    )
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_allows_pr_resolver_with_starting_branch(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "runtime": {"mode": "gemini_cli"},
                    "tool": {
                        "type": "skill",
                        "name": "pr-resolver",
                        "version": "1.0",
                    },
                    "git": {"startingBranch": "feature/resolve-pr"},
                }
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["title"] == "feature/resolve-pr"
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["task"]["title"] == "feature/resolve-pr"
    assert initial_parameters["task"]["git"] == {
        "startingBranch": "feature/resolve-pr"
    }


def test_create_task_shaped_execution_derives_pr_resolver_title_from_tool_inputs(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "runtime": {"mode": "gemini_cli"},
                    "tool": {
                        "type": "skill",
                        "name": "PR-Resolver",
                        "version": "1.0",
                        "inputs": {"startingBranch": "feature/from-tool-inputs"},
                    },
                }
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["title"] == "feature/from-tool-inputs"
    initial_parameters = called_kwargs["initial_parameters"]
    assert initial_parameters["task"]["title"] == "feature/from-tool-inputs"


def test_create_task_shaped_execution_once_schedule_sets_start_delay(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    scheduled_for = datetime.now(UTC) + timedelta(minutes=5)
    record = _build_execution_record(state=MoonMindWorkflowState.SCHEDULED)
    record.scheduled_for = scheduled_for
    service.create_execution.return_value = record

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "schedule": {
                    "mode": "once",
                    "scheduledFor": scheduled_for.isoformat(),
                },
                "task": {
                    "instructions": "Run this later",
                },
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["scheduled_for"] == scheduled_for
    start_delay = called_kwargs["start_delay"]
    assert start_delay is not None
    assert 200 <= start_delay.total_seconds() <= 300
    assert response.json()["scheduledFor"] is not None


def test_create_execution_surfaces_domain_validation_errors(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.side_effect = TemporalExecutionValidationError(
        "Unsupported workflow type: MoonMind.Unknown"
    )

    response = test_client.post(
        "/api/executions",
        json={"workflowType": "MoonMind.Unknown"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"


def test_create_execution_routes_directly_to_temporal(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    record = _build_execution_record()
    record.memo["title"] = "Test direct temporal"
    service.create_execution.return_value = record

    response = test_client.post(
        "/api/executions",
        json={"workflowType": "MoonMind.Run", "title": "Test direct temporal"},
    )

    assert response.status_code == 201
    assert response.json()["title"] == "Test direct temporal"
    service.create_execution.assert_awaited_once()


def test_create_execution_enforces_idempotency(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={"workflowType": "MoonMind.Run", "idempotencyKey": "idem-123"},
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["idempotency_key"] == "idem-123"


def test_list_executions_rejects_non_admin_cross_owner_queries(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.get("/api/executions", params={"ownerId": str(uuid4())})

    assert response.status_code == 403
    assert (
        response.json()["detail"]["message"]
        == "Cannot list executions for another user."
    )
    service.list_executions.assert_not_awaited()


def test_describe_execution_hides_foreign_workflow_visibility(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = SimpleNamespace(
        owner_id=str(uuid4()),
        workflow_id="mm:foreign",
    )

    response = test_client.get("/api/executions/mm:foreign")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "execution_not_found"
    assert str(user.id) != service.describe_execution.return_value.owner_id


def test_describe_execution_allows_search_attribute_owner_id_fallback(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    record = _build_execution_record(owner_id=str(user.id))
    record.owner_id = ""
    record.search_attributes["mm_owner_id"] = [str(user.id)]
    service.describe_execution.return_value = record

    response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    assert response.json()["workflowId"] == "mm:wf-1"


def test_describe_execution_source_temporal_uses_projection_fallback_when_sync_fails(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )

    async def _raise_sync_failure(*_args, **_kwargs):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr("api_service.api.routers.executions.RPCError", RuntimeError)
    monkeypatch.setattr(
        "api_service.core.sync.fetch_and_sync_execution",
        _raise_sync_failure,
    )

    response = test_client.get("/api/executions/mm:wf-1", params={"source": "temporal"})

    assert response.status_code == 200
    assert response.json()["workflowId"] == "mm:wf-1"
    assert service.describe_execution.await_args.kwargs["include_orphaned"] is True


def test_describe_execution_source_temporal_returns_503_when_no_fallback_record(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    service.describe_execution.side_effect = TemporalExecutionNotFoundError(
        "Workflow execution mm:wf-missing was not found"
    )

    async def _raise_sync_failure(*_args, **_kwargs):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr("api_service.api.routers.executions.RPCError", RuntimeError)
    monkeypatch.setattr(
        "api_service.core.sync.fetch_and_sync_execution",
        _raise_sync_failure,
    )

    response = test_client.get(
        "/api/executions/mm:wf-missing",
        params={"source": "temporal"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "temporal_unavailable"


def test_update_execution_invalid_update_name_returns_contract_error(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = SimpleNamespace(owner_id=str(user.id))
    service.update_execution.side_effect = TemporalExecutionValidationError(
        "Unsupported update name: UnknownUpdate"
    )

    response = test_client.post(
        "/api/executions/mm:test/update",
        json={"updateName": "UnknownUpdate"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_update_request"


def test_signal_execution_invalid_signal_name_returns_contract_error(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = SimpleNamespace(owner_id=str(user.id))
    service.signal_execution.side_effect = TemporalExecutionValidationError(
        "Unsupported signal name: UnknownSignal"
    )

    response = test_client.post(
        "/api/executions/mm:test/signal",
        json={"signalName": "UnknownSignal"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "signal_rejected"


def test_signal_execution_routes_send_message_and_serializes_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.AWAITING_EXTERNAL)
    record.memo["intervention_audit"] = [
        {
            "action": "send_message",
            "transport": "temporal_update",
            "summary": "Operator message sent.",
            "detail": "Continue with provider profiles.",
            "createdAt": "2026-03-31T01:02:03Z",
        }
    ]
    mock_service.describe_execution.return_value = record
    mock_service.signal_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/signal",
            json={
                "signalName": "SendMessage",
                "payload": {"message": "Continue with provider profiles."},
            },
        )

    assert response.status_code == 202
    called = mock_service.signal_execution.await_args.kwargs
    assert called["signal_name"] == "SendMessage"
    assert called["payload"] == {"message": "Continue with provider profiles."}
    body = response.json()
    assert body["actions"]["canReject"] is True
    assert body["actions"]["canSendMessage"] is True
    assert body["interventionAudit"][0]["action"] == "send_message"
    assert body["interventionAudit"][0]["detail"] == "Continue with provider profiles."


def test_cancel_execution_passes_reject_action_to_service() -> None:
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            state=MoonMindWorkflowState.AWAITING_EXTERNAL
        )
        rejected = _build_execution_record(state=MoonMindWorkflowState.CANCELED)
        rejected.close_status = "canceled"
        rejected.memo["intervention_audit"] = [
            {
                "action": "reject",
                "transport": "temporal_cancel",
                "summary": "Rejected by operator.",
                "createdAt": "2026-03-31T01:02:03Z",
            }
        ]
        service.cancel_execution.return_value = rejected

        response = test_client.post(
            "/api/executions/mm:wf-1/cancel",
            json={
                "action": "reject",
                "graceful": True,
                "reason": "Rejected by operator.",
            },
        )

        assert response.status_code == 202
        called = service.cancel_execution.await_args.kwargs
        assert called["action"] == "reject"
        assert called["graceful"] is True
        assert called["reason"] == "Rejected by operator."
        assert response.json()["interventionAudit"][0]["action"] == "reject"


def test_serialize_execution_treats_system_owner_id_as_system_owner_type() -> None:
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={},
        owner_id="system",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.Run"),
        state=MoonMindWorkflowState.INITIALIZING,
        workflow_id="wf-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        created_at="2026-03-06T00:00:00Z",
        started_at="2026-03-06T00:00:00Z",
        updated_at="2026-03-06T00:00:00Z",
        closed_at=None,
        integration_state=None,
    )

    payload = _serialize_execution(record)

    assert payload.owner_type == "system"
    assert payload.owner_id == "system"


def test_serialize_execution_surfaces_runtime_model_effort_from_parameters() -> None:
    """Ensure runtime/model/effort stored in record.parameters are surfaced."""
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={"title": "RT test", "summary": "OK"},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.Run"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="mm:rt-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        created_at="2026-03-19T00:00:00Z",
        started_at="2026-03-19T00:00:00Z",
        updated_at="2026-03-19T00:00:00Z",
        closed_at=None,
        integration_state=None,
        parameters={
            "targetRuntime": "codex",
            "model": "gpt-5-codex",
            "effort": "high",
        },
        paused=False,
        waiting_reason=None,
        attention_required=False,
    )

    payload = _serialize_execution(record)

    # Verify Python field values
    assert payload.target_runtime == "codex"
    assert payload.model == "gpt-5-codex"
    assert payload.effort == "high"

    # Verify JSON serialization uses camelCase aliases (what the frontend sees)
    dumped = payload.model_dump(by_alias=True)
    assert dumped["targetRuntime"] == "codex"
    assert dumped["model"] == "gpt-5-codex"
    assert dumped["effort"] == "high"


def test_serialize_execution_surfaces_runtime_from_nested_parameters_runtime_key() -> None:
    """Some payloads store mode under parameters.runtime.mode without top-level targetRuntime."""
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={"title": "Nested RT", "summary": "OK"},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.Run"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="mm:rt-nested",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        created_at="2026-03-19T00:00:00Z",
        started_at="2026-03-19T00:00:00Z",
        updated_at="2026-03-19T00:00:00Z",
        closed_at=None,
        integration_state=None,
        parameters={
            "runtime": {"mode": "gemini_cli", "model": "gemini-2.0"},
        },
        paused=False,
        waiting_reason=None,
        attention_required=False,
    )

    payload = _serialize_execution(record)

    assert payload.target_runtime == "gemini_cli"
    dumped = payload.model_dump(by_alias=True)
    assert dumped["targetRuntime"] == "gemini_cli"


def test_serialize_execution_surfaces_runtime_fields_from_task_runtime_payload() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "task": {
            "instructions": "Reconstruct this draft.",
            "runtime": {
                "mode": "claude_code",
                "model": "claude-3.7-sonnet",
                "effort": "low",
                "profileId": "profile:claude-default",
            },
        },
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["targetRuntime"] == "claude_code"
    assert dumped["model"] == "claude-3.7-sonnet"
    assert dumped["resolvedModel"] == "claude-3.7-sonnet"
    assert dumped["effort"] == "low"
    assert dumped["profileId"] == "profile:claude-default"


def test_serialize_execution_ignores_stale_waiting_reason_for_executing_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.memo = {
        "title": "Temporal task",
        "summary": "Launching agent...",
        "waiting_reason": "provider_profile_slot",
    }
    record.waiting_reason = None
    monkeypatch.setattr(settings.temporal_dashboard, "debug_fields_enabled", True)

    payload = _serialize_execution(
        record,
        user=SimpleNamespace(is_superuser=True, id=record.owner_id),
    )

    assert payload.state == "executing"
    assert payload.waiting_reason is None
    assert payload.debug_fields is not None
    assert payload.debug_fields.waiting_reason is None


def test_serialize_execution_surfaces_task_run_id_from_memo() -> None:
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={"title": "Task run", "summary": "OK", "taskRunId": "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d"},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.Run"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="mm:task-run-1",
        namespace="moonmind",
        run_id="temporal-run-1",
        artifact_refs=[],
        created_at="2026-03-19T00:00:00Z",
        started_at="2026-03-19T00:00:00Z",
        updated_at="2026-03-19T00:00:00Z",
        closed_at=None,
        integration_state=None,
        parameters={},
        paused=False,
        waiting_reason=None,
        attention_required=False,
    )

    payload = _serialize_execution(record)

    assert payload.task_run_id == "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d"
    dumped = payload.model_dump(by_alias=True)
    assert dumped["taskRunId"] == "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d"


def test_serialize_execution_surfaces_dependency_metadata() -> None:
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={
            "title": "Dependent task",
            "summary": "Waiting on dependencies.",
            "depends_on": ["mm:dep-1", "mm:dep-2"],
            "has_dependencies": True,
            "dependency_wait_occurred": True,
            "dependency_wait_duration_ms": 1500,
            "dependency_resolution": "success",
        },
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.Run"),
        state=MoonMindWorkflowState.WAITING_ON_DEPENDENCIES,
        workflow_id="mm:task-deps-1",
        namespace="moonmind",
        run_id="temporal-run-1",
        artifact_refs=[],
        created_at="2026-03-19T00:00:00Z",
        started_at="2026-03-19T00:00:00Z",
        updated_at="2026-03-19T00:00:00Z",
        closed_at=None,
        integration_state=None,
        parameters={"task": {"dependsOn": ["mm:dep-1", "mm:dep-2"]}},
        paused=False,
        waiting_reason=None,
        attention_required=False,
    )

    payload = _serialize_execution(record)

    dumped = payload.model_dump(by_alias=True)
    assert dumped["dependsOn"] == ["mm:dep-1", "mm:dep-2"]
    assert dumped["hasDependencies"] is True
    assert dumped["dependencyWaitOccurred"] is True
    assert dumped["dependencyWaitDurationMs"] == 1500
    assert dumped["dependencyResolution"] == "success"
    assert dumped["failedDependencyId"] is None


def test_serialize_execution_repository_ignores_mapping_values_and_uses_first_scalar() -> None:
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={
            "title": "Repo test",
            "summary": "OK",
            "repository": {"owner": "Moon", "name": "Mind"},
        },
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.Run"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="mm:repo-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        created_at="2026-03-31T00:00:00Z",
        started_at="2026-03-31T00:00:00Z",
        updated_at="2026-03-31T00:00:00Z",
        closed_at=None,
        integration_state=None,
        parameters={"repository": ["Moon/Mind", "Ignored/Repo"]},
        paused=False,
        waiting_reason=None,
        attention_required=False,
    )

    payload = _serialize_execution(record)

    assert payload.repository == "Moon/Mind"
    dumped = payload.model_dump(by_alias=True)
    assert dumped["repository"] == "Moon/Mind"


def test_describe_execution_exposes_task_and_temporal_run_identity() -> None:
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record()

        response = test_client.get("/api/executions/mm:wf-1")

        assert response.status_code == 200
        payload = response.json()
        assert payload["workflowId"] == "mm:wf-1"
        assert payload["taskId"] == "mm:wf-1"
        assert payload["runId"] == "run-2"
        assert payload["temporalRunId"] == "run-2"
        assert payload["latestRunView"] is True
        assert payload["continueAsNewCause"] == "manual_rerun"
        assert payload["stepsHref"] == "/api/executions/mm:wf-1/steps"


def test_describe_execution_includes_latest_run_progress() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        progress={
            "total": 3,
            "pending": 0,
            "ready": 1,
            "running": 1,
            "awaitingExternal": 0,
            "reviewing": 0,
            "succeeded": 1,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Run tests",
            "updatedAt": "2026-04-08T12:00:00Z",
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stepsHref"] == "/api/executions/mm:wf-1/steps"
    assert payload["progress"] == {
        "total": 3,
        "pending": 0,
        "ready": 1,
        "running": 1,
        "awaitingExternal": 0,
        "reviewing": 0,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
        "canceled": 0,
        "currentStepTitle": "Run tests",
        "updatedAt": "2026-04-08T12:00:00Z",
    }


def test_describe_execution_prefers_progress_query_run_id_when_newer_latest_run() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        progress={
            "runId": "run-99",
            "total": 3,
            "pending": 0,
            "ready": 1,
            "running": 1,
            "awaitingExternal": 0,
            "reviewing": 0,
            "succeeded": 1,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Run tests",
            "updatedAt": "2026-04-08T12:00:00Z",
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runId"] == "run-99"
    assert payload["temporalRunId"] == "run-99"
    assert payload["progress"] == {
        "total": 3,
        "pending": 0,
        "ready": 1,
        "running": 1,
        "awaitingExternal": 0,
        "reviewing": 0,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
        "canceled": 0,
        "currentStepTitle": "Run tests",
        "updatedAt": "2026-04-08T12:00:00Z",
    }
    assert "runId" not in payload["progress"]


def test_describe_execution_leaves_progress_null_when_query_fails() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(app, error=RuntimeError("query unavailable"))
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stepsHref"] == "/api/executions/mm:wf-1/steps"
    assert payload["progress"] is None


def test_describe_execution_steps_href_uses_configured_detail_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "detail_endpoint",
        "/gateway/api/executions/{workflowId}",
    )
    payload = _serialize_execution(_build_execution_record()).model_dump(by_alias=True)
    assert payload["stepsHref"] == "/gateway/api/executions/mm:wf-1/steps"


def test_describe_execution_does_not_query_progress_for_manifest_workflows() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        workflow_type=TemporalWorkflowType.MANIFEST_INGEST
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    query_client = _override_query_client(app, progress={"total": 99})
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["progress"] is None
    assert payload["stepsHref"] is None
    assert query_client.get_workflow_handle.call_count <= 1


def test_get_execution_steps_returns_latest_run_ledger() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={
            "workflowId": "mm:wf-1",
            "runId": "run-99",
            "runScope": "latest",
            "steps": [
                {
                    "logicalStepId": "run-tests",
                    "order": 1,
                    "title": "Run tests",
                    "tool": {"type": "skill", "name": "repo.run_tests", "version": "1"},
                    "dependsOn": [],
                    "status": "running",
                    "waitingReason": None,
                    "attentionRequired": False,
                    "attempt": 2,
                    "startedAt": "2026-04-08T12:00:00Z",
                    "updatedAt": "2026-04-08T12:01:00Z",
                    "summary": "Running pytest",
                    "checks": [],
                    "refs": {
                        "childWorkflowId": None,
                        "childRunId": None,
                        "taskRunId": "task-run-1",
                    },
                    "artifacts": {
                        "outputSummary": None,
                        "outputPrimary": None,
                        "runtimeStdout": "artifact://stdout",
                        "runtimeStderr": None,
                        "runtimeMergedLogs": None,
                        "runtimeDiagnostics": None,
                        "providerSnapshot": None,
                    },
                    "workload": {
                        "taskRunId": "task-run-1",
                        "stepId": "run-tests",
                        "attempt": 2,
                        "toolName": "container.run_workload",
                        "profileId": "local-python",
                        "imageRef": "python:3.12-slim",
                        "status": "succeeded",
                        "exitCode": 0,
                        "durationSeconds": 8.5,
                        "sessionContext": {
                            "sessionId": "session-1",
                            "sessionEpoch": 4,
                        },
                    },
                    "lastError": None,
                }
            ],
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflowId"] == "mm:wf-1"
    assert payload["runId"] == "run-99"
    assert payload["runScope"] == "latest"
    assert payload["steps"][0]["attempt"] == 2
    assert payload["steps"][0]["refs"]["taskRunId"] == "task-run-1"
    assert payload["steps"][0]["workload"]["profileId"] == "local-python"
    assert payload["steps"][0]["workload"]["imageRef"] == "python:3.12-slim"
    assert payload["steps"][0]["workload"]["sessionContext"] == {
        "sessionId": "session-1",
        "sessionEpoch": 4,
    }


def test_get_execution_steps_enriches_missing_agent_task_run_ids_once() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service

    def _ledger_step(
        *,
        logical_step_id: str,
        order: int,
        tool_type: str,
        child_workflow_id: str,
    ) -> dict[str, object]:
        return {
            "logicalStepId": logical_step_id,
            "order": order,
            "title": logical_step_id,
            "tool": {
                "type": tool_type,
                "name": (
                    "codex_cli"
                    if tool_type == "agent_runtime"
                    else "repo.run_tests"
                ),
                "version": "1",
            },
            "dependsOn": [],
            "status": "awaiting_external",
            "waitingReason": "Awaiting child workflow progress",
            "attentionRequired": False,
            "attempt": 1,
            "startedAt": "2026-04-08T12:00:00Z",
            "updatedAt": "2026-04-08T12:01:00Z",
            "summary": "Awaiting child workflow",
            "checks": [],
            "refs": {
                "childWorkflowId": child_workflow_id,
                "childRunId": None,
                "taskRunId": None,
            },
            "artifacts": {
                "outputSummary": None,
                "outputPrimary": None,
                "runtimeStdout": None,
                "runtimeStderr": None,
                "runtimeMergedLogs": None,
                "runtimeDiagnostics": None,
                "providerSnapshot": None,
            },
            "lastError": None,
        }

    _override_query_client(
        app,
        ledger={
            "workflowId": "mm:wf-1",
            "runId": "run-99",
            "runScope": "latest",
            "steps": [
                _ledger_step(
                    logical_step_id="delegate-agent",
                    order=1,
                    tool_type="agent_runtime",
                    child_workflow_id="mm:wf-1:agent:delegate-agent",
                ),
                _ledger_step(
                    logical_step_id="second-agent",
                    order=2,
                    tool_type="agent_runtime",
                    child_workflow_id="mm:wf-1:agent:second-agent",
                ),
                _ledger_step(
                    logical_step_id="run-tests",
                    order=3,
                    tool_type="skill",
                    child_workflow_id="mm:wf-1:tool:run-tests",
                ),
            ],
        },
    )
    _override_user_dependencies(app, is_superuser=True)
    to_thread_calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    async def _fake_to_thread(
        func: object, /, *args: object, **kwargs: object
    ) -> dict[str, str]:
        to_thread_calls.append((func, args, kwargs))
        return {
            "mm:wf-1:agent:delegate-agent": "task-run-1",
            "mm:wf-1:agent:second-agent": "task-run-2",
        }

    with patch(
        "api_service.api.routers.executions.asyncio.to_thread",
        new=_fake_to_thread,
    ):
        with TestClient(app) as test_client:
            response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 200
    payload = response.json()
    assert payload["steps"][0]["refs"]["taskRunId"] == "task-run-1"
    assert payload["steps"][1]["refs"]["taskRunId"] == "task-run-2"
    assert payload["steps"][2]["refs"]["taskRunId"] is None
    assert len(to_thread_calls) == 1
    assert to_thread_calls[0][1] == (
        (
            "mm:wf-1:agent:delegate-agent",
            "mm:wf-1:agent:second-agent",
        ),
    )


def test_get_execution_steps_returns_503_for_temporal_rpc_errors() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        error=RPCError("Connection failed", RPCStatusCode.UNAVAILABLE, None),
    )
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "temporal_unavailable"


def test_get_execution_steps_returns_500_for_invalid_ledger_payload() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(app, ledger={})
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "invalid_execution_query_payload"


def test_get_execution_steps_rejects_unsupported_workflow_types() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        workflow_type=TemporalWorkflowType.MANIFEST_INGEST
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(app, ledger={})
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_query"


def test_describe_execution_hydrates_provider_profile_metadata() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    record.parameters = {"profileId": "profile:gemini-default"}
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                provider_id="google",
                provider_label="Google",
            )
        )
    )
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["profileId"] == "profile:gemini-default"
    assert payload["providerId"] == "google"
    assert payload["providerLabel"] == "Google"
    app.dependency_overrides.clear()


def test_describe_execution_falls_back_to_managed_run_store_task_run_id(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    record = _build_execution_record(owner_id=str(user.id))
    record.memo = {"title": "Temporal task", "summary": "Waiting on review."}
    record.parameters = {}
    record.search_attributes = {
        "mm_owner_id": str(user.id),
        "mm_owner_type": "user",
        "mm_entry": "run",
    }
    service.describe_execution.return_value = record

    to_thread_calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    async def _fake_to_thread(
        func: object, /, *args: object, **kwargs: object
    ) -> dict[str, str]:
        to_thread_calls.append((func, args, kwargs))
        return {"mm:wf-1": "550e8400-e29b-41d4-a716-446655440000"}

    with patch(
        "api_service.api.routers.executions.asyncio.to_thread",
        new=_fake_to_thread,
    ):
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    assert (
        response.json()["taskRunId"] == "550e8400-e29b-41d4-a716-446655440000"
    )
    assert len(to_thread_calls) == 1
    assert to_thread_calls[0][1] == (("mm:wf-1",),)


def test_request_rerun_update_response_includes_continue_as_new_cause() -> None:
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record()
        service.update_execution.return_value = {
            "accepted": True,
            "applied": "continue_as_new",
            "message": "Rerun requested. Execution continued as new run.",
            "continue_as_new_cause": "manual_rerun",
        }

        response = test_client.post(
            "/api/executions/mm:wf-1/update",
            json={
                "updateName": "RequestRerun",
                "idempotencyKey": "rerun-1",
            },
        )

        assert response.status_code == 200
        assert response.json()["continueAsNewCause"] == "manual_rerun"


def test_task_editing_update_route_emits_attempt_and_result_metrics() -> None:
    metrics = Mock()
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record()
        service.update_execution.return_value = {
            "accepted": True,
            "applied": "next_safe_point",
            "message": "Inputs scheduled.",
        }

        with patch(
            "api_service.api.routers.executions.get_metrics_emitter",
            return_value=metrics,
        ):
            response = test_client.post(
                "/api/executions/mm:wf-1/update",
                json={
                    "updateName": "UpdateInputs",
                    "inputArtifactRef": "artifact://input/new",
                    "parametersPatch": {
                        "task": {"instructions": "Edited instructions."}
                    },
                },
            )

        assert response.status_code == 200
        metric_calls = [
            call
            for call in metrics.increment.call_args_list
            if call.args[0] == "temporal_task_editing.event"
        ]
        assert len(metric_calls) == 2
        assert metric_calls[0].kwargs["tags"] == {
            "event": "submit_attempt",
            "update_name": "UpdateInputs",
            "workflow_type": "MoonMind.Run",
            "state": "executing",
        }
        assert metric_calls[1].kwargs["tags"] == {
            "event": "submit_result",
            "update_name": "UpdateInputs",
            "workflow_type": "MoonMind.Run",
            "state": "executing",
            "result": "success",
            "applied": "next_safe_point",
        }


def test_task_editing_update_route_emits_failure_reason_metrics() -> None:
    metrics = Mock()
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record()
        service.update_execution.side_effect = TemporalExecutionValidationError(
            "Workflow state changed and rerun is no longer available."
        )

        with patch(
            "api_service.api.routers.executions.get_metrics_emitter",
            return_value=metrics,
        ):
            response = test_client.post(
                "/api/executions/mm:wf-1/update",
                json={"updateName": "RequestRerun"},
            )

        assert response.status_code == 422
        metric_calls = [
            call
            for call in metrics.increment.call_args_list
            if call.args[0] == "temporal_task_editing.event"
        ]
        assert metric_calls[1].kwargs["tags"] == {
            "event": "submit_result",
            "update_name": "RequestRerun",
            "workflow_type": "MoonMind.Run",
            "state": "executing",
            "result": "failure",
            "reason": "validation",
        }


def test_list_executions_preserves_logical_identity_fields() -> None:
    for test_client, service in _client_with_service():
        service.list_executions.return_value = SimpleNamespace(
            items=[_build_execution_record()],
            next_page_token="cursor-1",
            count=1,
        )

        response = test_client.get("/api/executions")

        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        assert payload["nextPageToken"] == "cursor-1"
        item = payload["items"][0]
        assert item["workflowId"] == "mm:wf-1"
        assert item["taskId"] == "mm:wf-1"
        assert item["runId"] == "run-2"
        assert item["temporalRunId"] == "run-2"
        assert item["latestRunView"] is True
        assert item["continueAsNewCause"] == "manual_rerun"


def test_describe_manifest_execution_exposes_bounded_manifest_fields() -> None:
    """Manifest ingest detail should expose refs, policy, and bounded counts."""

    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST
        )

        response = test_client.get("/api/executions/mm:wf-1")

        assert response.status_code == 200
        payload = response.json()
        assert payload["workflowType"] == "MoonMind.ManifestIngest"
        assert payload["manifestArtifactRef"] == "art_manifest_1"
        assert payload["planArtifactRef"] == "art_plan_1"
        assert payload["executionPolicy"]["maxConcurrency"] == 3
        assert payload["counts"]["ready"] == 1
        assert payload["counts"]["running"] == 1


def test_describe_execution_enriches_dependency_summaries_without_dunder_dict() -> None:
    for test_client, service in _client_with_service():
        record = _build_execution_record()
        record.parameters = {"task": {"dependsOn": ["mm:dep-1"]}}
        service.describe_execution.return_value = record
        service.enrich_dependency_summaries.side_effect = [
            [
                ExecutionDependencySummary(
                    workflow_id="mm:dep-1",
                    title="Dependency",
                    summary="done",
                    state="completed",
                    close_status="completed",
                    workflow_type="MoonMind.Run",
                )
            ],
            [
                ExecutionDependencySummary(
                    workflow_id="mm:dep-2",
                    title="Dependent",
                    summary="waiting",
                    state="executing",
                    close_status=None,
                    workflow_type="MoonMind.Run",
                )
            ],
        ]
        service.list_dependents.return_value = [
            SimpleNamespace(dependent_workflow_id="mm:dep-2")
        ]

        response = test_client.get("/api/executions/mm:wf-1")

        assert response.status_code == 200
        payload = response.json()
        assert payload["prerequisites"] == [
            {
                "workflowId": "mm:dep-1",
                "title": "Dependency",
                "summary": "done",
                "state": "completed",
                "closeStatus": "completed",
                "workflowType": "MoonMind.Run",
            }
        ]
        assert payload["dependents"] == [
            {
                "workflowId": "mm:dep-2",
                "title": "Dependent",
                "summary": "waiting",
                "state": "executing",
                "closeStatus": None,
                "workflowType": "MoonMind.Run",
            }
        ]


def test_manifest_update_route_passes_manifest_specific_fields() -> None:
    """Manifest-specific update requests should be forwarded unchanged to the service."""

    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST
        )
        service.update_execution.return_value = {
            "accepted": True,
            "applied": "next_safe_point",
            "message": "Manifest update accepted and will be applied at the next safe point.",
        }

        response = test_client.post(
            "/api/executions/mm:wf-1/update",
            json={
                "updateName": "UpdateManifest",
                "newManifestArtifactRef": "art_manifest_2",
                "mode": "REPLACE_FUTURE",
                "idempotencyKey": "manifest-update-1",
            },
        )

        assert response.status_code == 200
        called = service.update_execution.await_args.kwargs
        assert called["update_name"] == "UpdateManifest"
        assert called["new_manifest_artifact_ref"] == "art_manifest_2"
        assert called["mode"] == "REPLACE_FUTURE"


def test_manifest_status_route_returns_bounded_snapshot() -> None:
    """Manifest status route should return the service snapshot unchanged."""

    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST
        )
        service.describe_manifest_status.return_value = {
            "workflowId": "mm:wf-1",
            "state": "executing",
            "phase": "executing",
            "paused": False,
            "maxConcurrency": 3,
            "failurePolicy": "best_effort",
            "counts": {
                "pending": 0,
                "ready": 1,
                "running": 1,
                "completed": 0,
                "failed": 0,
                "canceled": 0,
            },
        }

        response = test_client.get("/api/executions/mm:wf-1/manifest-status")

        assert response.status_code == 200
        assert response.json()["counts"]["running"] == 1


def test_manifest_nodes_route_returns_page_payload() -> None:
    """Manifest node page route should preserve cursor and count fields."""

    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST
        )
        service.list_manifest_nodes.return_value = {
            "items": [
                {
                    "nodeId": "node-b",
                    "state": "running",
                    "workflowType": "MoonMind.Run",
                }
            ],
            "nextCursor": "cursor-1",
            "count": 1,
        }

        response = test_client.get(
            "/api/executions/mm:wf-1/manifest-nodes",
            params={"state": "running", "limit": 25},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert body["nextCursor"] == "cursor-1"
        assert body["items"][0]["nodeId"] == "node-b"
        assert body["items"][0]["workflowType"] == "MoonMind.Run"


def test_describe_execution_includes_actions_and_debug_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        state=MoonMindWorkflowState.AWAITING_EXTERNAL
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_task_editing_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "debug_fields_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["dashboardStatus"] == "awaiting_action"
    assert body["waitingReason"] == "Waiting on review."
    assert body["attentionRequired"] is True
    assert body["actions"]["canApprove"] is True
    assert body["actions"]["canResume"] is True
    assert body["actions"]["canCancel"] is True
    assert body["actions"]["canUpdateInputs"] is False
    assert body["debugFields"]["workflowId"] == "mm:wf-1"
    assert body["redirectPath"] == "/tasks/mm:wf-1?source=temporal"


def test_describe_execution_exposes_temporal_task_editing_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.input_ref = "artifact://input/current"
    record.plan_ref = "artifact://plan/current"
    record.parameters = {
        "targetRuntime": "codex_cli",
        "model": "gpt-5.4",
        "task": {"git": {"repository": "Moon/Mind"}},
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_task_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["workflowId"] == "mm:wf-1"
    assert body["workflowType"] == "MoonMind.Run"
    assert body["inputArtifactRef"] == "artifact://input/current"
    assert body["planArtifactRef"] == "artifact://plan/current"
    assert body["inputParameters"]["targetRuntime"] == "codex_cli"
    assert body["actions"]["canUpdateInputs"] is True
    assert body["actions"]["canRerun"] is False


def test_temporal_task_editing_actions_require_run_workflow_and_feature_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_task_editing_enabled", False)
    disabled_record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)

    disabled_actions = _serialize_execution(disabled_record).actions
    assert disabled_actions.can_update_inputs is False
    assert disabled_actions.disabled_reasons["canUpdateInputs"] == "temporal_task_editing_disabled"

    monkeypatch.setattr(settings.temporal_dashboard, "temporal_task_editing_enabled", True)
    manifest_record = _build_execution_record(
        workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
        state=MoonMindWorkflowState.COMPLETED,
    )

    manifest_actions = _serialize_execution(manifest_record).actions
    assert manifest_actions.can_rerun is False
    assert manifest_actions.disabled_reasons["canRerun"] == "unsupported_workflow_type"

    monkeypatch.setattr(settings.temporal_dashboard, "temporal_task_editing_enabled", False)
    disabled_manifest_actions = _serialize_execution(manifest_record).actions
    assert disabled_manifest_actions.can_rerun is False
    assert (
        disabled_manifest_actions.disabled_reasons["canRerun"]
        == "unsupported_workflow_type"
    )


def test_describe_execution_disables_actions_when_feature_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", False)
    monkeypatch.setattr(settings.temporal_dashboard, "debug_fields_enabled", False)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["canPause"] is False
    assert body["actions"]["disabledReasons"]["pause"] == "actions_disabled"
    assert body["debugFields"] is None


def test_action_endpoints_reject_requests_when_actions_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", False)

    with TestClient(app) as test_client:
        # Test update endpoint
        update_response = test_client.post(
            "/api/executions/mm:wf-1/update", json={"updateName": "RequestRerun"}
        )
        assert update_response.status_code == 403
        assert update_response.json()["detail"]["code"] == "actions_disabled"

        # Test signal endpoint
        signal_response = test_client.post(
            "/api/executions/mm:wf-1/signal", json={"signalName": "pause"}
        )
        assert signal_response.status_code == 403
        assert signal_response.json()["detail"]["code"] == "actions_disabled"

        # Test cancel endpoint
        cancel_response = test_client.post("/api/executions/mm:wf-1/cancel", json={})
        assert cancel_response.status_code == 403
        assert cancel_response.json()["detail"]["code"] == "actions_disabled"


def test_serialize_execution_canceled_state_uses_correct_spelling() -> None:
    """Regression: 'cancelled' (British) must not leak into the Literal('canceled') field."""
    from api_service.db.models import TemporalExecutionCloseStatus

    record = SimpleNamespace(
        close_status=TemporalExecutionCloseStatus.CANCELED,
        search_attributes={"mm_entry": "run"},
        memo={},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.Run"),
        state=MoonMindWorkflowState.CANCELED,
        workflow_id="mm:canceled-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        created_at="2026-03-24T00:00:00Z",
        started_at="2026-03-24T00:00:00Z",
        updated_at="2026-03-24T00:00:00Z",
        closed_at="2026-03-24T00:00:00Z",
        integration_state=None,
    )

    payload = _serialize_execution(record)

    assert payload.status == "canceled"
    assert payload.dashboard_status == "canceled"
    assert payload.temporal_status == "canceled"
