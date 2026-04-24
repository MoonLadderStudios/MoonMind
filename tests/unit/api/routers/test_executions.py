"""Unit tests for Temporal execution lifecycle API endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock, Mock, call, patch
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
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalArtifactEncryption,
    TemporalArtifactStatus,
    TemporalWorkflowType,
)
from moonmind.config.settings import settings
from moonmind.workflows.temporal.service import ExecutionDependencySummary
from moonmind.workflows.temporal import (
    TemporalExecutionNotFoundError,
    TemporalExecutionValidationError,
)
from moonmind.schemas.temporal_models import (
    ExecutionMergeAutomationResolverChildModel,
    ExecutionProgressModel,
    StepLedgerSnapshotModel,
)


class _ScalarRows:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def all(self) -> list[SimpleNamespace]:
        return self._rows


class _ExecuteResult:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarRows:
        return _ScalarRows(self._rows)


class _QueryHandle:
    def __init__(
        self,
        *,
        progress=None,
        ledger=None,
        summary=None,
        error: Exception | None = None,
    ) -> None:
        self._progress = progress
        self._ledger = ledger
        self._summary = summary
        self._error = error

    async def query(self, name: str):
        if self._error is not None:
            raise self._error
        if name == "get_progress":
            return self._progress
        if name == "get_step_ledger":
            return self._ledger
        if name == "summary":
            return self._summary
        raise AssertionError(f"Unexpected query name: {name}")


def _override_query_client(
    app: FastAPI,
    *,
    progress=None,
    ledger=None,
    summary=None,
    error: Exception | None = None,
) -> SimpleNamespace:
    handles: dict[str, _QueryHandle] = {}

    def get_workflow_handle(workflow_id: str) -> _QueryHandle:
        if workflow_id not in handles:
            handles[workflow_id] = _QueryHandle(
                progress=progress,
                ledger=ledger,
                summary=summary,
                error=error,
            )
        return handles[workflow_id]

    client = SimpleNamespace(get_workflow_handle=Mock(side_effect=get_workflow_handle))
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
    has_task_input_snapshot: bool = True,
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
            **(
                {
                    "task_input_snapshot_ref": "art_snapshot_1",
                    "task_input_snapshot_version": 1,
                    "task_input_snapshot_source_kind": "create",
                }
                if workflow_type is TemporalWorkflowType.RUN
                and has_task_input_snapshot
                else {}
            ),
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
    user = _override_user_dependencies(app, is_superuser=True)

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
    user = _override_user_dependencies(app, is_superuser=True)

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


def test_create_task_shaped_execution_rejects_attachments_when_policy_disabled(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", False)

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "instructions": "Review the uploaded screenshot.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000000",
                            "filename": "wireframe.png",
                            "contentType": "image/png",
                            "sizeBytes": 128,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "attachment policy is disabled" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_unknown_attachment_fields(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "instructions": "Review the uploaded screenshot.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000000",
                            "filename": "wireframe.png",
                            "contentType": "image/png",
                            "sizeBytes": 128,
                            "caption": "unsupported future field",
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "unsupported fields" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_unsupported_runtime_with_attachments(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    execute = AsyncMock(
        return_value=_ExecuteResult(
            [
                SimpleNamespace(
                    artifact_id="art_01IMAGEINPUT0000000000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=128,
                )
            ]
        )
    )
    test_client.app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        execute=execute
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "targetRuntime": "unsupported_runtime",
                "task": {
                    "instructions": "Review the uploaded screenshot.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000000",
                            "filename": "wireframe.png",
                            "contentType": "image/png",
                            "sizeBytes": 128,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "Unsupported targetRuntime" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_fetches_unique_attachments_in_one_query(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    execute = AsyncMock(
        return_value=_ExecuteResult(
            [
                SimpleNamespace(
                    artifact_id="art_01OBJECTIVEINPUT00000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=10,
                ),
                SimpleNamespace(
                    artifact_id="art_01STEPINPUT000000000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=20,
                ),
            ]
        )
    )
    test_client.app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        execute=execute
    )
    execute = AsyncMock(
        return_value=_ExecuteResult(
            [
                SimpleNamespace(
                    artifact_id="art_01IMAGEINPUT0000000000001",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=128,
                ),
                SimpleNamespace(
                    artifact_id="art_01IMAGEINPUT0000000000002",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/webp",
                    size_bytes=256,
                ),
            ]
        )
    )
    test_client.app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        execute=execute
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "instructions": "Review uploaded screenshots.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000001",
                            "filename": "one.png",
                            "contentType": "image/png",
                            "sizeBytes": 128,
                        },
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000002",
                            "filename": "two.webp",
                            "contentType": "image/webp",
                            "sizeBytes": 256,
                        },
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    execute.assert_awaited_once()
    service.create_execution.assert_awaited_once()


def test_create_task_shaped_execution_rejects_svg_attachment_type(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    monkeypatch.setattr(
        settings.workflow,
        "agent_job_attachment_allowed_content_types",
        ("image/png", "image/jpeg", "image/webp", "image/svg+xml"),
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "instructions": "Review the uploaded screenshot.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000000",
                            "filename": "wireframe.svg",
                            "contentType": "image/svg+xml",
                            "sizeBytes": 128,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "image/svg+xml is not supported" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_attachment_policy_limits(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_max_count", 1)

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "instructions": "Review uploaded screenshots.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000001",
                            "filename": "one.png",
                            "contentType": "image/png",
                            "sizeBytes": 128,
                        },
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000002",
                            "filename": "two.png",
                            "contentType": "image/png",
                            "sizeBytes": 128,
                        },
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "too many input attachments" in response.json()["detail"]["message"]
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


def test_create_task_shaped_execution_applies_default_publish_mode(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "default_publish_mode", "pr")
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "task": {
                    "instructions": "Fix the failing workflow.",
                    "runtime": {"mode": "codex"},
                },
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "pr"
    assert initial_parameters["task"]["publish"]["mode"] == "pr"


def test_create_task_shaped_execution_prefers_task_publish_mode_alias_over_top_publish(
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
                "publish": {
                    "mode": "branch",
                    "commitMessage": "Top-level publish details",
                },
                "task": {
                    "instructions": "Fix the failing workflow.",
                    "runtime": {"mode": "codex"},
                    "publish_mode": "none",
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "none"
    assert initial_parameters["task"]["publish"] == {
        "mode": "none",
        "commitMessage": "Top-level publish details",
    }


def test_create_task_shaped_execution_rejects_falsy_non_string_publish_mode(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "publishMode": False,
                "task": {
                    "instructions": "Fix the failing workflow.",
                    "runtime": {"mode": "codex"},
                },
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["message"] == (
        "publish.mode must be one of: branch, none, pr"
    )
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_preserves_remediation_payload(
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
                    "instructions": "Investigate the failed run.",
                    "runtime": {"mode": "codex"},
                    "remediation": {
                        "target": {"workflowId": "mm:target-workflow"},
                        "mode": "snapshot_then_follow",
                        "authorityMode": "observe_only",
                        "trigger": {"type": "manual"},
                    },
                },
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    kwargs = service.create_execution.call_args.kwargs
    assert kwargs["initial_parameters"]["task"]["remediation"] == {
        "target": {"workflowId": "mm:target-workflow"},
        "mode": "snapshot_then_follow",
        "authorityMode": "observe_only",
        "trigger": {"type": "manual"},
    }


def test_create_task_shaped_execution_preserves_malformed_remediation_for_service_validation(
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
                    "instructions": "Investigate the failed run.",
                    "runtime": {"mode": "codex"},
                    "remediation": "mm:target-workflow",
                },
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    kwargs = service.create_execution.call_args.kwargs
    assert kwargs["initial_parameters"]["task"]["remediation"] == "mm:target-workflow"


def test_create_remediation_convenience_route_expands_to_task_create_contract(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions/mm:target-workflow/remediation",
        json={
            "repository": "MoonLadderStudios/MoonMind",
            "instructions": "Investigate the target execution.",
            "runtime": {"mode": "codex"},
            "remediation": {
                "mode": "snapshot",
                "authorityMode": "observe_only",
                "trigger": {"type": "manual"},
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    kwargs = service.create_execution.call_args.kwargs
    assert kwargs["workflow_type"] == "MoonMind.Run"
    assert kwargs["initial_parameters"]["task"]["instructions"] == (
        "Investigate the target execution."
    )
    assert kwargs["initial_parameters"]["task"]["runtime"] == {"mode": "codex_cli"}
    assert kwargs["initial_parameters"]["task"]["remediation"] == {
        "target": {"workflowId": "mm:target-workflow"},
        "mode": "snapshot",
        "authorityMode": "observe_only",
        "trigger": {"type": "manual"},
    }
    assert kwargs["initial_parameters"]["publishMode"] == "pr"
    assert kwargs["initial_parameters"]["task"]["publish"]["mode"] == "pr"


def test_create_remediation_convenience_route_uses_top_level_overrides(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    scheduled_for = datetime.now(UTC) + timedelta(minutes=5)
    record = _build_execution_record(state=MoonMindWorkflowState.SCHEDULED)
    record.scheduled_for = scheduled_for
    service.create_execution.return_value = record

    response = test_client.post(
        "/api/executions/mm:target-workflow/remediation",
        json={
            "repository": "MoonLadderStudios/MoonMind",
            "priority": 7,
            "maxAttempts": 5,
            "schedule": {
                "mode": "once",
                "scheduledFor": scheduled_for.isoformat(),
            },
            "instructions": "Top-level instructions",
            "runtime": {"mode": "codex"},
            "publish_mode": "none",
            "remediation": {
                "mode": "snapshot",
                "authorityMode": "observe_only",
                "trigger": {"type": "manual"},
            },
            "task": {
                "instructions": "Nested instructions",
                "runtime": {"mode": "jules"},
                "remediation": {
                    "mode": "snapshot_then_follow",
                    "authorityMode": "limited_write",
                },
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    initial_parameters = called_kwargs["initial_parameters"]
    assert called_kwargs["scheduled_for"] == scheduled_for
    assert initial_parameters["priority"] == 7
    assert initial_parameters["maxAttempts"] == 5
    assert initial_parameters["task"]["instructions"] == "Top-level instructions"
    assert initial_parameters["task"]["runtime"] == {"mode": "codex_cli"}
    assert initial_parameters["task"]["remediation"] == {
        "target": {"workflowId": "mm:target-workflow"},
        "mode": "snapshot",
        "authorityMode": "observe_only",
        "trigger": {"type": "manual"},
    }
    assert initial_parameters["publishMode"] == "none"
    assert initial_parameters["task"]["publish"]["mode"] == "none"


def test_create_remediation_convenience_route_rejects_malformed_remediation(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions/mm:target-workflow/remediation",
        json={
            "repository": "MoonLadderStudios/MoonMind",
            "instructions": "Investigate the target execution.",
            "remediation": "mm:target-workflow",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_execution_request",
        "message": "task.remediation must be an object",
    }
    service.create_execution.assert_not_awaited()


def test_list_remediations_for_target_returns_compact_inbound_links(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    now = datetime.now(UTC)
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )
    service.list_remediations_for_target.return_value = [
        SimpleNamespace(
            remediation_workflow_id="mm:remediation-1",
            remediation_run_id="run-remediation-1",
            target_workflow_id="mm:target-workflow",
            target_run_id="run-target",
            mode="snapshot_then_follow",
            authority_mode="approval_gated",
            status="awaiting_approval",
            active_lock_scope="target_execution",
            active_lock_holder="mm:remediation-1",
            latest_action_summary="Proposed session interrupt",
            outcome=None,
            context_artifact_ref="art_context",
            created_at=now,
            updated_at=now,
        )
    ]

    response = test_client.get(
        "/api/executions/mm:target-workflow/remediations",
        params={"direction": "inbound"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "direction": "inbound",
        "items": [
            {
                "remediationWorkflowId": "mm:remediation-1",
                "remediationRunId": "run-remediation-1",
                "targetWorkflowId": "mm:target-workflow",
                "targetRunId": "run-target",
                "mode": "snapshot_then_follow",
                "authorityMode": "approval_gated",
                "status": "awaiting_approval",
                "activeLockScope": "target_execution",
                "activeLockHolder": "mm:remediation-1",
                "latestActionSummary": "Proposed session interrupt",
                "resolution": None,
                "contextArtifactRef": "art_context",
                "approvalState": {
                    "requestId": "mm:remediation-1:approval",
                    "actionKind": None,
                    "riskTier": None,
                    "preconditions": None,
                    "blastRadius": None,
                    "decision": "pending",
                    "decisionActor": None,
                    "decisionAt": None,
                    "canDecide": True,
                    "auditRef": None,
                },
                "createdAt": now.isoformat().replace("+00:00", "Z"),
                "updatedAt": now.isoformat().replace("+00:00", "Z"),
            }
        ],
    }
    service.list_remediations_for_target.assert_awaited_once_with("mm:target-workflow")
    service.list_remediation_targets.assert_not_called()


def test_list_remediations_for_remediation_returns_compact_outbound_links(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    now = datetime.now(UTC)
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )
    service.list_remediation_targets.return_value = [
        SimpleNamespace(
            remediation_workflow_id="mm:remediation-1",
            remediation_run_id="run-remediation-1",
            target_workflow_id="mm:target-workflow",
            target_run_id="run-target",
            mode="snapshot",
            authority_mode="observe_only",
            status="created",
            active_lock_scope=None,
            active_lock_holder=None,
            latest_action_summary=None,
            outcome="resolved",
            context_artifact_ref=None,
            created_at=now,
            updated_at=now,
        )
    ]

    response = test_client.get(
        "/api/executions/mm:remediation-1/remediations",
        params={"direction": "outbound"},
    )

    assert response.status_code == 200
    assert response.json()["direction"] == "outbound"
    assert response.json()["items"][0] == {
        "remediationWorkflowId": "mm:remediation-1",
        "remediationRunId": "run-remediation-1",
        "targetWorkflowId": "mm:target-workflow",
        "targetRunId": "run-target",
        "mode": "snapshot",
        "authorityMode": "observe_only",
        "status": "created",
        "activeLockScope": None,
        "activeLockHolder": None,
        "latestActionSummary": None,
        "resolution": "resolved",
        "contextArtifactRef": None,
        "approvalState": None,
        "createdAt": now.isoformat().replace("+00:00", "Z"),
        "updatedAt": now.isoformat().replace("+00:00", "Z"),
    }
    service.list_remediation_targets.assert_awaited_once_with("mm:remediation-1")
    service.list_remediations_for_target.assert_not_called()


def test_list_remediations_rejects_unknown_direction(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )

    response = test_client.get(
        "/api/executions/mm:target-workflow/remediations",
        params={"direction": "sideways"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_remediation_direction"


def test_record_remediation_approval_decision_calls_trusted_service(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )
    service.record_remediation_approval_decision.return_value = {
        "accepted": True,
        "workflowId": "mm:remediation-1",
        "requestId": "approval-1",
        "decision": "approved",
    }

    response = test_client.post(
        "/api/executions/mm:remediation-1/remediation/approvals/approval-1",
        json={"decision": "approved", "comment": "Reviewed."},
    )

    assert response.status_code == 200
    assert response.json() == {
        "accepted": True,
        "workflowId": "mm:remediation-1",
        "requestId": "approval-1",
        "decision": "approved",
    }
    service.record_remediation_approval_decision.assert_awaited_once_with(
        remediation_workflow_id="mm:remediation-1",
        request_id="approval-1",
        decision="approved",
        comment="Reviewed.",
        actor=user.email,
    )


def test_record_remediation_approval_decision_rejects_unknown_decision(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )

    response = test_client.post(
        "/api/executions/mm:remediation-1/remediation/approvals/approval-1",
        json={"decision": "maybe"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == (
        "invalid_remediation_approval_decision"
    )
    service.record_remediation_approval_decision.assert_not_awaited()


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


def test_create_task_shaped_execution_preserves_merge_automation_request(
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
                "targetRuntime": "codex_cli",
                "publishMode": "pr",
                "mergeAutomation": {
                    "enabled": True,
                    "mergeMethod": "squash",
                    "fallbackPollSeconds": 60,
                },
                "task": {
                    "instructions": "Implement and publish a pull request.",
                    "runtime": {"mode": "codex_cli"},
                    "publish": {"mode": "pr"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "pr"
    assert initial_parameters["task"]["publish"]["mode"] == "pr"
    assert initial_parameters["mergeAutomation"] == {
        "enabled": True,
        "mergeMethod": "squash",
        "fallbackPollSeconds": 60,
    }


def test_create_task_shaped_execution_preserves_nested_merge_automation_request(
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
                "targetRuntime": "codex_cli",
                "task": {
                    "instructions": "Implement and publish a pull request.",
                    "runtime": {"mode": "codex_cli"},
                    "publish": {
                        "mode": "pr",
                        "mergeAutomation": {
                            "enabled": True,
                            "mergeMethod": "rebase",
                        },
                    },
                    "mergeAutomation": {
                        "enabled": True,
                        "automatedReview": "optional",
                    },
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["task"]["mergeAutomation"] == {
        "enabled": True,
        "automatedReview": "optional",
    }
    assert initial_parameters["task"]["publish"]["mergeAutomation"] == {
        "enabled": True,
        "mergeMethod": "rebase",
    }


def test_serialize_execution_exposes_merge_automation_selection() -> None:
    record = _build_execution_record()
    record.parameters = {
        "publishMode": "pr",
        "task": {
            "publish": {
                "mode": "pr",
                "mergeAutomation": {"enabled": True},
            },
        },
    }

    payload = _serialize_execution(record)

    assert payload.merge_automation_selected is True
    assert payload.model_dump(by_alias=True)["mergeAutomationSelected"] is True


def test_serialize_execution_exposes_snake_case_publish_merge_automation() -> None:
    record = _build_execution_record()
    record.parameters = {
        "publishMode": "pr",
        "publish": {
            "mode": "pr",
            "merge_automation": {"enabled": True},
        },
    }

    payload = _serialize_execution(record)

    assert payload.merge_automation_selected is True
    assert payload.model_dump(by_alias=True)["mergeAutomationSelected"] is True


def test_serialize_execution_defaults_merge_automation_selection_to_false() -> None:
    record = _build_execution_record()
    record.parameters = {"publishMode": "pr", "mergeAutomation": {"enabled": False}}

    payload = _serialize_execution(record)

    assert payload.merge_automation_selected is False
    assert payload.model_dump(by_alias=True)["mergeAutomationSelected"] is False


def test_create_task_shaped_execution_preserves_story_output_contract(
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
                    "title": "Break down task proposal design",
                    "instructions": "Extract stories from docs/Tasks/TaskProposalSystem.md.",
                    "storyOutput": {
                        "mode": "jira",
                        "jira": {
                            "projectKey": "MM",
                            "issueTypeId": "10001",
                            "issueTypeName": "Story",
                            "dependencyMode": "linear_blocker_chain",
                            "labels": ["moonmind"],
                        },
                    },
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs["initial_parameters"]
    assert initial_parameters["storyOutput"] == {
        "mode": "jira",
        "jira": {
            "projectKey": "MM",
            "issueTypeId": "10001",
            "issueTypeName": "Story",
            "dependencyMode": "linear_blocker_chain",
            "labels": ["moonmind"],
        },
    }
    assert initial_parameters["task"]["storyOutput"] == initial_parameters["storyOutput"]


def test_create_task_shaped_execution_defaults_partial_story_output_mode(
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
                    "title": "Break down task proposal design",
                    "instructions": "Extract stories from docs/Tasks/TaskProposalSystem.md.",
                    "storyOutput": {
                        "jira": {
                            "projectKey": "MM",
                            "issueTypeId": "10001",
                        },
                    },
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs["initial_parameters"]
    assert initial_parameters["storyOutput"] == {
        "mode": "jira",
        "jira": {
            "projectKey": "MM",
            "issueTypeId": "10001",
        },
    }


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


def test_create_task_shaped_execution_forwards_input_attachments(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MM-367: objective and step attachment refs reach MoonMind.Run parameters."""

    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    execute = AsyncMock(
        return_value=_ExecuteResult(
            [
                SimpleNamespace(
                    artifact_id="art_01OBJECTIVEINPUT00000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=10,
                ),
                SimpleNamespace(
                    artifact_id="art_01STEPINPUT000000000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=20,
                ),
            ]
        )
    )
    test_client.app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        execute=execute
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "task": {
                    "instructions": "Inspect submitted screenshots.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01OBJECTIVEINPUT00000000",
                            "filename": "same-name.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                    "steps": [
                        {
                            "instructions": "Inspect the step screenshot.",
                            "inputAttachments": [
                                {
                                    "artifactId": "art_01STEPINPUT000000000000",
                                    "filename": "same-name.png",
                                    "contentType": "image/png",
                                    "sizeBytes": 20,
                                }
                            ],
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
    assert initial_parameters["task"]["inputAttachments"] == [
        {
            "artifactId": "art_01OBJECTIVEINPUT00000000",
            "filename": "same-name.png",
            "contentType": "image/png",
            "sizeBytes": 10,
        }
    ]
    assert initial_parameters["task"]["steps"][0]["inputAttachments"] == [
        {
            "artifactId": "art_01STEPINPUT000000000000",
            "filename": "same-name.png",
            "contentType": "image/png",
            "sizeBytes": 20,
        }
    ]


def test_create_task_shaped_execution_normalizes_snake_case_input_attachments(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MM-367: router normalization accepts Pydantic field-name aliases."""

    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    execute = AsyncMock(
        return_value=_ExecuteResult(
            [
                SimpleNamespace(
                    artifact_id="art_01OBJECTIVEINPUT00000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=10,
                ),
                SimpleNamespace(
                    artifact_id="art_01STEPINPUT000000000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=20,
                ),
            ]
        )
    )
    test_client.app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        execute=execute
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "task": {
                    "instructions": "Inspect submitted screenshots.",
                    "input_attachments": [
                        {
                            "artifactId": "art_01OBJECTIVEINPUT00000000",
                            "filename": "objective.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                    "steps": [
                        {
                            "instructions": "Inspect the step screenshot.",
                            "input_attachments": [
                                {
                                    "artifactId": "art_01STEPINPUT000000000000",
                                    "filename": "step.png",
                                    "contentType": "image/png",
                                    "sizeBytes": 20,
                                }
                            ],
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
    assert initial_parameters["task"]["inputAttachments"] == [
        {
            "artifactId": "art_01OBJECTIVEINPUT00000000",
            "filename": "objective.png",
            "contentType": "image/png",
            "sizeBytes": 10,
        }
    ]
    step_payload = initial_parameters["task"]["steps"][0]
    assert step_payload["inputAttachments"] == [
        {
            "artifactId": "art_01STEPINPUT000000000000",
            "filename": "step.png",
            "contentType": "image/png",
            "sizeBytes": 20,
        }
    ]
    assert "input_attachments" not in step_payload


def test_create_task_shaped_execution_rejects_embedded_attachment_data(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    """MM-367: task-shaped submit rejects inline image payloads in refs."""

    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "task": {
                    "instructions": "Inspect submitted screenshot.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01INLINEINPUT0000000000",
                            "filename": "inline.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                            "dataUrl": "data:image/png;base64,AAAA",
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"
    assert "unsupported fields" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


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


def test_create_execution_persists_task_input_snapshot_for_direct_run_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    record = _build_execution_record(has_task_input_snapshot=False)
    record.parameters = {
        "repository": "Moon/Mind",
        "targetRuntime": "codex_cli",
        "task": {
            "title": "Direct run",
            "instructions": "Implement the persisted direct run.",
        },
    }
    service.create_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)
    session = AsyncMock()
    app.dependency_overrides[get_async_session] = lambda: session
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_task_editing_enabled", True
    )

    async def _persist_snapshot(**kwargs) -> str:
        assert kwargs["payload"] == {
            "repository": "Moon/Mind",
            "targetRuntime": "codex_cli",
            "requiredCapabilities": [],
        }
        assert kwargs["task_payload"] == {
            "title": "Direct run",
            "instructions": "Implement the persisted direct run.",
        }
        assert kwargs["source_kind"] == "create"
        target_record = kwargs["record"]
        target_record.memo = {
            **dict(target_record.memo or {}),
            "task_input_snapshot_ref": "art_snapshot_direct",
            "task_input_snapshot_version": 1,
            "task_input_snapshot_source_kind": "create",
        }
        return "art_snapshot_direct"

    persist_mock = AsyncMock(side_effect=_persist_snapshot)
    monkeypatch.setattr(
        "api_service.api.routers.executions._persist_original_task_input_snapshot",
        persist_mock,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "workflowType": "MoonMind.Run",
                "title": "Direct run",
                "initialParameters": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex_cli",
                    "task": {
                        "title": "Conflicting retry",
                        "instructions": "Do not snapshot the replay payload.",
                    },
                },
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["taskInputSnapshot"]["available"] is True
    assert body["taskInputSnapshot"]["artifactRef"] == "art_snapshot_direct"
    assert body["actions"]["canUpdateInputs"] is True
    persist_mock.assert_awaited_once()
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(record)


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


def test_describe_execution_rolls_back_session_when_temporal_sync_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    user = _override_user_dependencies(app, is_superuser=False)
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )
    service.list_dependents.return_value = []
    service.enrich_dependency_summaries.return_value = []
    app.dependency_overrides[_get_service] = lambda: service
    _override_query_client(app, progress={"total": 0})

    session = AsyncMock()
    session.commit.side_effect = RuntimeError("db flush failed")

    async def _override_session():
        yield session

    async def _sync_success(*_args, **_kwargs):
        return None

    app.dependency_overrides[get_async_session] = _override_session
    monkeypatch.setattr(
        "api_service.core.sync.fetch_and_sync_execution",
        _sync_success,
    )

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions/mm:wf-1", params={"source": "temporal"}
        )

    assert response.status_code == 200
    session.rollback.assert_awaited_once()
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
    user = _override_user_dependencies(app, is_superuser=True)
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


def test_signal_execution_routes_skip_dependency_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.WAITING_ON_DEPENDENCIES)
    record.memo["intervention_audit"] = [
        {
            "action": "skip_dependency_wait",
            "transport": "temporal_update",
            "summary": "Dependency wait skipped by operator.",
            "createdAt": "2026-03-31T01:02:03Z",
        }
    ]
    mock_service.describe_execution.return_value = record
    mock_service.signal_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    user = _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/signal",
            json={"signalName": "SkipDependencyWait", "payload": {}},
        )

    assert response.status_code == 202
    called = mock_service.signal_execution.await_args.kwargs
    assert called["signal_name"] == "SkipDependencyWait"
    assert called["payload"] == {}
    body = response.json()
    assert body["actions"]["canSkipDependencyWait"] is False
    assert body["interventionAudit"][0]["action"] == "skip_dependency_wait"


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


def test_cancel_execution_authorizes_projection_only_child_target() -> None:
    for test_client, service in _client_with_service():
        child = _build_execution_record(state=MoonMindWorkflowState.AWAITING_SLOT)
        child.workflow_id = (
            "resolver:mm:parent:pr:1634:head:"
            "5ed0c032789b901b99da93eaa4877de6609fdf35:1"
        )
        canceled = _build_execution_record(state=MoonMindWorkflowState.CANCELED)
        canceled.workflow_id = child.workflow_id
        canceled.close_status = "canceled"
        service.describe_cancel_target_execution.return_value = child
        service.cancel_execution.return_value = canceled

        response = test_client.post(
            f"/api/executions/{child.workflow_id}/cancel",
            json={"graceful": True, "reason": "stop child"},
        )

        assert response.status_code == 202
        service.describe_cancel_target_execution.assert_awaited_once_with(
            child.workflow_id
        )
        called = service.cancel_execution.await_args.kwargs
        assert called["workflow_id"] == child.workflow_id
        assert called["reason"] == "stop child"
        assert called["graceful"] is True


def test_cancel_execution_authorizes_projection_only_nested_parent(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    child = _build_execution_record(
        state=MoonMindWorkflowState.AWAITING_SLOT,
        owner_id="",
    )
    child.workflow_id = "mm:parent:agent:child-1"
    child.search_attributes = {}
    child.owner_type = None

    parent = _build_execution_record(owner_id=str(user.id))
    parent.workflow_id = "mm:parent"

    canceled = _build_execution_record(state=MoonMindWorkflowState.CANCELED)
    canceled.workflow_id = child.workflow_id
    canceled.close_status = "canceled"

    service.describe_cancel_target_execution.return_value = child
    service.describe_execution.return_value = parent
    service.cancel_execution.return_value = canceled

    response = test_client.post(
        f"/api/executions/{child.workflow_id}/cancel",
        json={"graceful": True, "reason": "stop nested child"},
    )

    assert response.status_code == 202
    service.describe_execution.assert_awaited_once_with(
        "mm:parent",
        include_orphaned=True,
    )
    called = service.cancel_execution.await_args.kwargs
    assert called["workflow_id"] == child.workflow_id
    assert called["reason"] == "stop nested child"


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


def test_serialize_execution_uses_created_at_for_immediate_schedule() -> None:
    created_at = datetime(2026, 3, 6, 0, 0, tzinfo=UTC)
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.Run"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="wf-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        scheduled_for=None,
        created_at=created_at,
        started_at=created_at,
        updated_at=created_at,
        closed_at=None,
        integration_state=None,
    )

    payload = _serialize_execution(record)

    assert payload.scheduled_for == created_at
    assert payload.created_at == created_at


def test_serialize_execution_falls_back_to_updated_at_for_created_at() -> None:
    updated_at = datetime(2026, 3, 6, 0, 0, tzinfo=UTC)
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.Run"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="wf-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        scheduled_for=None,
        created_at=None,
        updated_at=updated_at,
        closed_at=None,
        integration_state=None,
    )

    payload = _serialize_execution(record)

    assert payload.created_at == updated_at
    assert payload.scheduled_for == updated_at


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


def test_serialize_execution_surfaces_task_template_slug_as_primary_skill() -> None:
    record = _build_execution_record(
        state=MoonMindWorkflowState.WAITING_ON_DEPENDENCIES
    )
    record.parameters = {
        "targetRuntime": "codex_cli",
        "task": {
            "title": "Run Jira Orchestrate for MM-501",
            "instructions": "Use the existing Jira Orchestrate workflow.",
            "taskTemplate": {
                "slug": "jira-orchestrate",
                "version": "1.0.0",
            },
        },
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["targetSkill"] == "jira-orchestrate"
    assert dumped["taskSkills"] == ["jira-orchestrate"]
    assert dumped["skillRuntime"]["selectedSkills"] == ["jira-orchestrate"]


def test_serialize_execution_surfaces_applied_template_slug_as_primary_skill() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "targetRuntime": "codex_cli",
        "task": {
            "instructions": "Run the applied preset.",
            "appliedStepTemplates": [
                {
                    "slug": "jira-orchestrate",
                    "version": "1.0.0",
                    "stepIds": ["tpl:jira-orchestrate:1"],
                }
            ],
        },
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["targetSkill"] == "jira-orchestrate"
    assert dumped["taskSkills"] == ["jira-orchestrate"]


def test_serialize_execution_uses_latest_applied_template_as_primary_skill() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "targetRuntime": "codex_cli",
        "task": {
            "instructions": "Run the latest applied preset.",
            "appliedStepTemplates": [
                {
                    "slug": "initial-preset",
                    "version": "1.0.0",
                    "stepIds": ["tpl:initial-preset:1"],
                },
                {
                    "slug": "latest-preset",
                    "version": "1.0.0",
                    "stepIds": ["tpl:latest-preset:1"],
                },
            ],
        },
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["targetSkill"] == "latest-preset"
    assert dumped["taskSkills"] == ["latest-preset"]
    assert dumped["skillRuntime"]["selectedSkills"] == ["latest-preset"]


def test_serialize_execution_surfaces_compact_skill_runtime_metadata() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "resolvedSkillsetRef": "artifact:resolved-skills-1",
        "task": {
            "instructions": "Inspect skill runtime evidence.",
            "skills": {
                "sets": ["operator-default"],
                "include": [{"name": "pr-resolver", "version": "1.2.0"}],
                "materializationMode": "hybrid",
            },
        },
        "skillsMaterialized": {
            "activeSkills": ["pr-resolver"],
            "skills": [
                {
                    "name": "pr-resolver",
                    "version": "1.2.0",
                    "source_kind": "deployment",
                    "content_ref": "artifact:skill-body-1",
                    "content_digest": "sha256:abc",
                    "body": "FULL SKILL BODY SHOULD NOT LEAK",
                }
            ],
            "materializationMode": "hybrid",
            "visiblePath": ".agents/skills",
            "backingPath": "../skills_active",
            "readOnly": True,
            "manifestPath": "artifact:manifest-1",
            "promptIndexRef": "artifact:prompt-index-1",
            "activationSummaryRef": "artifact:activation-summary-1",
        },
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["taskSkills"] == ["operator-default", "pr-resolver"]
    skill_runtime = dumped["skillRuntime"]
    assert skill_runtime["resolvedSkillsetRef"] == "artifact:resolved-skills-1"
    assert skill_runtime["selectedSkills"] == ["pr-resolver"]
    assert skill_runtime["selectedVersions"][0] == {
        "name": "pr-resolver",
        "version": "1.2.0",
        "sourceKind": "deployment",
        "sourcePath": None,
        "contentRef": "artifact:skill-body-1",
        "contentDigest": "sha256:abc",
    }
    assert skill_runtime["sourceProvenance"][0] == {
        "name": "pr-resolver",
        "sourceKind": "deployment",
        "sourcePath": None,
    }
    assert skill_runtime["materializationMode"] == "hybrid"
    assert skill_runtime["visiblePath"] == ".agents/skills"
    assert skill_runtime["backingPath"] == "../skills_active"
    assert skill_runtime["readOnly"] is True
    assert skill_runtime["manifestRef"] == "artifact:manifest-1"
    assert skill_runtime["promptIndexRef"] == "artifact:prompt-index-1"
    assert skill_runtime["activationSummaryRef"] == "artifact:activation-summary-1"
    assert skill_runtime["lifecycleIntent"] == {
        "source": "proposal",
        "selectors": ["operator-default", "pr-resolver"],
        "resolvedSkillsetRef": "artifact:resolved-skills-1",
        "resolutionMode": "snapshot-reuse",
        "explanation": "Execution reuses the resolved skill snapshot unless explicit re-resolution is requested.",
    }
    assert "FULL SKILL BODY SHOULD NOT LEAK" not in str(dumped["skillRuntime"])


def test_serialize_execution_preserves_direct_skill_source_provenance() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "task": {"instructions": "Inspect skill runtime evidence."},
        "skillsMaterialized": {
            "selectedSkills": ["pr-resolver", "fix-ci"],
            "selectedVersions": [
                {"name": "pr-resolver", "version": "1.2.0"},
                {
                    "name": "fix-ci",
                    "version": "2.0.0",
                    "source_kind": "deployment",
                    "source_path": ".agents/skills/fix-ci",
                },
            ],
            "sourceProvenance": [
                {
                    "name": "pr-resolver",
                    "sourceKind": "repo",
                    "sourcePath": ".agents/skills/pr-resolver",
                }
            ],
        },
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["skillRuntime"]["sourceProvenance"] == [
        {
            "name": "pr-resolver",
            "sourceKind": "repo",
            "sourcePath": ".agents/skills/pr-resolver",
        },
        {
            "name": "fix-ci",
            "sourceKind": "deployment",
            "sourcePath": ".agents/skills/fix-ci",
        },
    ]


def test_serialize_execution_accepts_snake_case_skill_materialization_metadata() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "task": {
            "instructions": "Inspect skill runtime evidence.",
            "skills": {"materialization_mode": "hybrid"},
        },
        "skillsMaterialized": {
            "activeSkills": ["pr-resolver"],
            "visible_path": ".agents/skills",
            "backing_path": "../skills_active",
            "read_only": False,
            "manifest_ref": "artifact:manifest-1",
            "prompt_index_ref": "artifact:prompt-index-1",
            "activation_summary_ref": "artifact:activation-summary-1",
        },
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)
    skill_runtime = payload["skillRuntime"]

    assert skill_runtime["materializationMode"] == "hybrid"
    assert skill_runtime["visiblePath"] == ".agents/skills"
    assert skill_runtime["backingPath"] == "../skills_active"
    assert skill_runtime["readOnly"] is False
    assert skill_runtime["manifestRef"] == "artifact:manifest-1"
    assert skill_runtime["promptIndexRef"] == "artifact:prompt-index-1"
    assert skill_runtime["activationSummaryRef"] == "artifact:activation-summary-1"


def test_serialize_execution_surfaces_skill_lifecycle_intent_for_schedule_defaults() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.SCHEDULED)
    record.parameters = {
        "task": {
            "instructions": "Run this later.",
            "skills": {"sets": ["nightly"], "materializationMode": "hybrid"},
        },
        "skillLifecycleIntent": {
            "source": "schedule",
            "resolutionMode": "selector-based",
            "explanation": "Scheduled run resolves selected skills when it starts.",
        },
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    lifecycle = payload["skillRuntime"]["lifecycleIntent"]
    assert lifecycle["source"] == "schedule"
    assert lifecycle["selectors"] == ["nightly"]
    assert lifecycle["resolutionMode"] == "selector-based"
    assert lifecycle["explanation"] == "Scheduled run resolves selected skills when it starts."


def test_serialize_execution_marks_lifecycle_defaults_as_inherited_defaults() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.SCHEDULED)
    record.parameters = {
        "task": {"instructions": "Run this later."},
        "skillLifecycleIntent": {"source": "schedule"},
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    lifecycle = payload["skillRuntime"]["lifecycleIntent"]
    assert lifecycle["source"] == "schedule"
    assert lifecycle["selectors"] == []
    assert lifecycle["resolvedSkillsetRef"] is None
    assert lifecycle["resolutionMode"] == "inherited-defaults"
    assert lifecycle["explanation"] == "Execution inherits deployment skill defaults explicitly."


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


def test_describe_execution_includes_live_merge_automation_summary() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    record.parameters = {
        "publishMode": "pr",
        "mergeAutomation": {"enabled": True},
    }
    record.memo = {
        **record.memo,
        "merge_automation": {
            "enabled": True,
            "status": "awaiting_child",
            "childWorkflowId": "merge-automation:mm:wf-1:pr:1614:head:abc123",
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        progress={
            "total": 1,
            "pending": 0,
            "ready": 0,
            "running": 0,
            "awaitingExternal": 1,
            "reviewing": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": None,
            "updatedAt": "2026-04-08T12:00:00Z",
        },
        summary={
            "status": "waiting",
            "prNumber": 1614,
            "prUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/1614",
            "latestHeadSha": "abc123",
            "blockers": [
                {
                    "kind": "checks_failed",
                    "summary": "Required checks are failing.",
                    "source": "github",
                    "retryable": True,
                }
            ],
            "resolverChildWorkflowIds": [
                "resolver:mm:wf-1:pr:1614:head:abc123:1"
            ],
            "artifactRefs": {
                "gateSnapshots": ["gate-artifact"],
                "resolverAttempts": None,
            },
        },
        ledger={
            "workflowId": "resolver:mm:wf-1:pr:1614:head:abc123:1",
            "runId": "resolver-run",
            "runScope": "latest",
            "steps": [
                {
                    "logicalStepId": "node-1",
                    "order": 1,
                    "title": "codex_cli",
                    "tool": {"type": "agent_runtime", "name": "codex_cli"},
                    "dependsOn": [],
                    "status": "running",
                    "attempt": 1,
                    "updatedAt": "2026-04-08T12:00:00Z",
                    "refs": {"taskRunId": "resolver-task-run"},
                    "artifacts": {},
                    "checks": [],
                }
            ],
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    merge_automation = response.json()["mergeAutomation"]
    assert merge_automation["workflowId"] == "merge-automation:mm:wf-1:pr:1614:head:abc123"
    assert merge_automation["status"] == "waiting"
    assert merge_automation["blockers"][0]["summary"] == "Required checks are failing."
    assert merge_automation["artifactRefs"]["gateSnapshots"] == ["gate-artifact"]
    assert merge_automation["artifactRefs"]["resolverAttempts"] == []
    assert merge_automation["resolverChildren"] == [
        {
            "workflowId": "resolver:mm:wf-1:pr:1614:head:abc123:1",
            "taskRunId": "resolver-task-run",
            "status": "running",
            "detailHref": (
                "/tasks/resolver%3Amm%3Awf-1%3Apr%3A1614%3Ahead%3Aabc123%3A1"
                "?source=temporal"
            ),
        }
    ]


def test_describe_execution_queries_resolver_children_concurrently() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    record.parameters = {
        "publishMode": "pr",
        "mergeAutomation": {"enabled": True},
    }
    record.memo = {
        **record.memo,
        "merge_automation": {
            "enabled": True,
            "status": "awaiting_child",
            "childWorkflowId": "merge-automation:mm:wf-1:pr:1614:head:abc123",
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    resolver_ids = [
        "resolver:mm:wf-1:pr:1614:head:abc123:1",
        "resolver:mm:wf-1:pr:1614:head:abc123:2",
        "resolver:mm:wf-1:pr:1614:head:abc123:3",
    ]
    _override_query_client(
        app,
        progress={"total": 1},
        summary={
            "status": "waiting",
            "resolverChildWorkflowIds": resolver_ids,
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    started: list[str] = []
    all_started = asyncio.Event()

    async def fake_child_observability(
        *,
        temporal_client,
        workflow_id: str,
    ) -> ExecutionMergeAutomationResolverChildModel:
        started.append(workflow_id)
        if len(started) == len(resolver_ids):
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1)
        return ExecutionMergeAutomationResolverChildModel(
            workflow_id=workflow_id,
            status="running",
            detail_href=f"/tasks/{workflow_id}",
        )

    with patch(
        "api_service.api.routers.executions._resolver_child_observability",
        side_effect=fake_child_observability,
    ):
        with TestClient(app) as test_client:
            response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    assert started == resolver_ids
    assert [
        child["workflowId"]
        for child in response.json()["mergeAutomation"]["resolverChildren"]
    ] == resolver_ids


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


def test_describe_execution_includes_report_projection_when_latest_report_artifacts_exist() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(return_value=None),
        rollback=AsyncMock(),
    )
    _override_temporal_client(app)
    user = _override_user_dependencies(app, is_superuser=True)

    primary_artifact = SimpleNamespace(
        artifact_id='art-primary',
        status=TemporalArtifactStatus.COMPLETE,
        sha256='sha-primary',
        size_bytes=128,
        content_type='text/markdown',
        encryption=TemporalArtifactEncryption.NONE,
        metadata_json={
            'report_type': 'security_pentest_report',
            'report_scope': 'final',
            'finding_counts': {'total': 3},
            'severity_counts': {'high': 1},
        },
    )
    summary_artifact = SimpleNamespace(
        artifact_id='art-summary',
        status=TemporalArtifactStatus.COMPLETE,
        sha256='sha-summary',
        size_bytes=64,
        content_type='application/json',
        encryption=TemporalArtifactEncryption.NONE,
        metadata_json={
            'report_type': 'security_pentest_report',
            'report_scope': 'final',
        },
    )
    artifact_service = SimpleNamespace(
        list_for_execution=AsyncMock(side_effect=[[primary_artifact], [summary_artifact]])
    )

    with patch(
        'api_service.api.routers.executions.get_temporal_artifact_service',
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get('/api/executions/mm:wf-1')

    assert response.status_code == 200
    payload = response.json()
    assert artifact_service.list_for_execution.await_args_list == [
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.primary',
            latest_only=True,
        ),
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.summary',
            latest_only=True,
        ),
    ]
    assert payload['reportProjection'] == {
        'hasReport': True,
        'latestReportRef': {
            'artifact_ref_v': 1,
            'artifact_id': 'art-primary',
        },
        'latestReportSummaryRef': {
            'artifact_ref_v': 1,
            'artifact_id': 'art-summary',
        },
        'reportType': 'security_pentest_report',
        'reportStatus': 'final',
        'findingCounts': {'total': 3},
        'severityCounts': {'high': 1},
    }


def test_describe_execution_report_projection_degrades_safely_when_no_report_exists() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(return_value=None),
        rollback=AsyncMock(),
    )
    _override_temporal_client(app)
    user = _override_user_dependencies(app, is_superuser=True)

    artifact_service = SimpleNamespace(list_for_execution=AsyncMock(return_value=[]))

    with patch(
        'api_service.api.routers.executions.get_temporal_artifact_service',
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get('/api/executions/mm:wf-1')

    assert response.status_code == 200
    payload = response.json()
    assert artifact_service.list_for_execution.await_args_list == [
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.primary',
            latest_only=True,
        ),
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.summary',
            latest_only=True,
        ),
    ]
    assert payload['reportProjection'] == {'hasReport': False}


def test_describe_execution_report_projection_ignores_incomplete_report_artifacts() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(return_value=None),
        rollback=AsyncMock(),
    )
    _override_temporal_client(app)
    user = _override_user_dependencies(app, is_superuser=True)

    pending_primary = SimpleNamespace(
        artifact_id='art-primary-pending',
        status=TemporalArtifactStatus.PENDING_UPLOAD,
        sha256='sha-primary-pending',
        size_bytes=256,
        content_type='text/markdown',
        encryption=TemporalArtifactEncryption.NONE,
        metadata_json={'report_type': 'security_pentest_report', 'report_scope': 'final'},
    )
    pending_summary = SimpleNamespace(
        artifact_id='art-summary-pending',
        status=TemporalArtifactStatus.PENDING_UPLOAD,
        sha256='sha-summary-pending',
        size_bytes=64,
        content_type='application/json',
        encryption=TemporalArtifactEncryption.NONE,
        metadata_json={'report_type': 'security_pentest_report', 'report_scope': 'final'},
    )
    artifact_service = SimpleNamespace(
        list_for_execution=AsyncMock(side_effect=[[pending_primary], [pending_summary]])
    )

    with patch(
        'api_service.api.routers.executions.get_temporal_artifact_service',
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get('/api/executions/mm:wf-1')

    assert response.status_code == 200
    payload = response.json()
    assert artifact_service.list_for_execution.await_args_list == [
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.primary',
            latest_only=True,
        ),
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.summary',
            latest_only=True,
        ),
    ]
    assert payload['reportProjection'] == {'hasReport': False}


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
        ),
        rollback=AsyncMock(),
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


def test_request_rerun_update_redirects_response_to_created_rerun_execution() -> None:
    for test_client, service in _client_with_service():
        source_record = _build_execution_record()
        rerun_record = _build_execution_record()
        rerun_record.workflow_id = "mm:rerun-created"
        rerun_record.run_id = "run-rerun"
        rerun_record.memo = {
            **rerun_record.memo,
            "latest_temporal_run_id": "run-rerun",
        }
        service.describe_execution.side_effect = [source_record, rerun_record]
        service.update_execution.return_value = {
            "accepted": True,
            "applied": "continue_as_new",
            "message": "Rerun requested. New execution created.",
            "continue_as_new_cause": "manual_rerun",
            "workflow_id": "mm:rerun-created",
        }

        response = test_client.post(
            "/api/executions/mm:wf-1/update",
            json={
                "updateName": "RequestRerun",
                "idempotencyKey": "rerun-1",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["execution"]["workflowId"] == "mm:rerun-created"
        assert body["execution"]["redirectPath"] == (
            "/tasks/mm:rerun-created?source=temporal"
        )
        assert service.describe_execution.await_args_list[-1].args == (
            "mm:rerun-created",
        )


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
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_task_editing_enabled", True
    )
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
    assert body["actions"]["canBypassDependencies"] is False
    assert body["actions"]["canUpdateInputs"] is False
    assert body["debugFields"]["workflowId"] == "mm:wf-1"
    assert body["redirectPath"] == "/tasks/mm:wf-1?source=temporal"


def test_describe_execution_exposes_dependency_bypass_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        state=MoonMindWorkflowState.WAITING_ON_DEPENDENCIES
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["canBypassDependencies"] is True
    assert "canBypassDependencies" not in body["actions"]["disabledReasons"]


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


def test_temporal_task_editing_actions_require_original_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_task_editing_enabled", True)
    record = _build_execution_record(
        state=MoonMindWorkflowState.COMPLETED,
        has_task_input_snapshot=False,
    )

    actions = _serialize_execution(record).actions

    assert actions.can_rerun is False
    assert (
        actions.disabled_reasons["canRerun"]
        == "original_task_input_snapshot_missing"
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
