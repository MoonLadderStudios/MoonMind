"""Unit tests for Temporal execution lifecycle API endpoints."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import AsyncMock, Mock, call, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.service import RPCError, RPCStatusCode

from api_service.api.routers.executions import (
    _get_service,
    _artifact_id_from_ref,
    _build_original_task_input_snapshot_payload,
    _merge_task_preserving_artifact_instructions,
    _recovery_not_available_reason,
    _reuse_original_task_input_snapshot_from_source,
    _task_input_snapshot_descriptor_from_record,
    get_temporal_client,
    _serialize_execution,
    router,
    update_execution as update_execution_route,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalArtifact,
    TemporalArtifactEncryption,
    TemporalArtifactRedactionLevel,
    TemporalArtifactLink,
    TemporalArtifactRetentionClass,
    TemporalArtifactStorageBackend,
    TemporalArtifactStatus,
    TemporalArtifactUploadMode,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionRecord,
    TemporalWorkflowType,
)
from api_service.services.recurring_tasks_service import RecurringTaskValidationError
from moonmind.config.settings import settings
from moonmind.workflows.temporal.service import ExecutionDependencySummary
from moonmind.workflows.temporal import (
    TemporalExecutionNotFoundError,
    TemporalExecutionValidationError,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
from moonmind.schemas.temporal_models import (
    ExecutionMergeAutomationResolverChildModel,
    ExecutionProgressModel,
    StepLedgerSnapshotModel,
    UpdateExecutionRequest,
)
from moonmind.workflows.temporal.service import TemporalExecutionService


class _ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _ExecuteResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarRows:
        return _ScalarRows(self._rows)

    def scalar_one_or_none(self) -> object | None:
        return self._rows[0] if self._rows else None


def _artifact_session(rows: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(execute=AsyncMock(return_value=_ExecuteResult(rows)))


class _SnapshotReuseSession:
    def __init__(
        self,
        *,
        canonical: TemporalExecutionCanonicalRecord | None = None,
        existing_link: object | None = None,
    ) -> None:
        self._canonical = canonical
        self._existing_link = existing_link
        self.added: list[object] = []
        self.get = AsyncMock(return_value=canonical)

    async def execute(self, _statement: object) -> _ExecuteResult:
        rows = [self._existing_link] if self._existing_link is not None else []
        return _ExecuteResult(rows)

    def add(self, value: object) -> None:
        self.added.append(value)

def _completed_attachment_artifact(
    artifact_id: str,
    *,
    content_type: str = "image/png",
    size_bytes: int = 10,
    created_by_principal: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        artifact_id=artifact_id,
        status=TemporalArtifactStatus.COMPLETE,
        content_type=content_type,
        size_bytes=size_bytes,
        created_by_principal=created_by_principal,
    )

def _mm639_authored_task_payload() -> dict[str, Any]:
    return {
        "title": "MM-639 durable snapshot",
        "instructions": "Preserve the original authored task input for MM-639.",
        "inputAttachments": [
            {
                "artifactId": "art-objective",
                "filename": "objective.png",
                "contentType": "image/png",
                "sizeBytes": 123,
            }
        ],
        "runtime": {
            "mode": "codex_cli",
            "model": "gpt-5.4",
            "effort": "medium",
            "profileId": "profile-codex",
        },
        "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}},
        "git": {
            "repository": "MoonLadderStudios/MoonMind",
            "branch": "feature/mm-639",
        },
        "dependencies": ["MM-638"],
        "appliedStepTemplates": [
            {
                "slug": "jira-orchestrate",
                "version": "1.0.0",
                "inputs": {"issueKey": "MM-639"},
                "stepIds": ["step-1", "step-2"],
                "composition": {
                    "slug": "jira-orchestrate",
                    "includes": [
                        {"slug": "jira-fetch", "version": "1.0.0"},
                    ],
                },
            }
        ],
        "authoredPresets": [
            {
                "presetSlug": "jira-orchestrate",
                "presetVersion": "1.0.0",
                "inputBindings": {"issueKey": "MM-639"},
            }
        ],
        "steps": [
            {
                "id": "step-1",
                "title": "Fetch issue",
                "instructions": "Fetch Jira issue MM-639.",
                "dependsOn": [],
                "templateStepId": "tpl:jira-orchestrate:fetch",
                "presetProvenance": {
                    "presetSlug": "jira-orchestrate",
                    "presetVersion": "1.0.0",
                },
            },
            {
                "id": "step-2",
                "title": "Implement",
                "instructions": "Implement MM-639.",
                "dependsOn": ["step-1"],
                "inputAttachments": [
                    {
                        "artifactId": "art-step",
                        "filename": "step.png",
                        "contentType": "image/png",
                        "sizeBytes": 456,
                    }
                ],
                "presetProvenance": {
                    "presetSlug": "jira-orchestrate",
                    "presetVersion": "1.0.0",
                    "sourceStepId": "tpl:jira-orchestrate:implement",
                },
                "detachedFromPreset": True,
            },
        ],
    }

class _QueryHandle:
    def __init__(
        self,
        *,
        progress=None,
        ledger=None,
        summary=None,
        error: Exception | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self._progress = progress
        self._ledger = ledger
        self._summary = summary
        self._error = error
        self._delay_seconds = delay_seconds

    async def query(self, name: str):
        if self._delay_seconds > 0:
            await asyncio.sleep(self._delay_seconds)
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
    delay_seconds: float = 0,
) -> SimpleNamespace:
    handles: dict[str, _QueryHandle] = {}

    def get_workflow_handle(workflow_id: str) -> _QueryHandle:
        if workflow_id not in handles:
            handles[workflow_id] = _QueryHandle(
                progress=progress,
                ledger=ledger,
                summary=summary,
                error=error,
                delay_seconds=delay_seconds,
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

def _empty_session_override() -> SimpleNamespace:
    return SimpleNamespace()

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

def test_list_executions_temporal_query_includes_target_runtime_filter() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scope": "tasks",
                "targetRuntime": "codex_cli",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert 'WorkflowType="MoonMind.Run"' in query
    assert 'mm_entry="user_workflow"' in query
    assert 'mm_target_runtime="codex_cli"' in query
    temporal_client.list_workflows.assert_called_once()
    assert (
        temporal_client.list_workflows.call_args.kwargs["query"]
        == temporal_client.count_workflows.await_args.kwargs["query"]
    )

def test_list_executions_temporal_query_includes_canonical_state_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scope": "tasks",
                "stateIn": "completed,failed",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert 'WorkflowType="MoonMind.Run"' in query
    assert 'mm_entry="user_workflow"' in query
    # ``completed`` and ``failed`` are terminal states; the executions list
    # filter resolves them to the Temporal ``ExecutionStatus`` so closed
    # workflows whose ``mm_state`` search attribute was never updated still
    # match the user's selection.
    assert 'ExecutionStatus="Completed"' in query
    assert 'ExecutionStatus="Failed"' in query
    assert 'ExecutionStatus="Terminated"' in query
    assert 'ExecutionStatus="TimedOut"' in query
    assert 'mm_state="completed"' not in query
    assert 'mm_state="failed"' not in query


def test_list_executions_temporal_query_anchors_non_terminal_state_to_running_status() -> None:
    """Selecting ``AWAITING TASK`` must not match closed workflows whose
    ``mm_state`` search attribute was left at ``waiting_on_dependencies``
    when they were canceled or failed.
    """

    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scope": "tasks",
                "stateIn": "waiting_on_dependencies",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert 'mm_state="waiting_on_dependencies"' in query
    assert 'ExecutionStatus="Running"' in query
    assert 'ExecutionStatus="Failed"' not in query
    assert 'ExecutionStatus="Canceled"' not in query


def test_list_executions_temporal_query_mixes_terminal_and_non_terminal_state_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scope": "tasks",
                "stateIn": "waiting_on_dependencies,canceled",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert 'mm_state="waiting_on_dependencies"' in query
    assert 'ExecutionStatus="Running"' in query
    assert 'ExecutionStatus="Canceled"' in query


def test_list_executions_temporal_query_supports_repeated_canonical_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params=[
                ("source", "temporal"),
                ("scope", "tasks"),
                ("targetRuntimeIn", "codex_cli"),
                ("targetRuntimeIn", "claude_code"),
                ("targetRuntimeIn", ""),
                ("repoIn", "Moon/Mind,moon/sidecar"),
                ("repoIn", "Moon/Mind"),
            ],
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert '(mm_target_runtime="codex_cli" OR mm_target_runtime="claude_code")' in query
    assert '(mm_repo="Moon/Mind" OR mm_repo="moon/sidecar")' in query

def test_list_executions_temporal_query_ignores_empty_canonical_state_for_legacy_state() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params=[
                ("source", "temporal"),
                ("scope", "tasks"),
                ("state", "completed"),
                ("stateIn", ""),
            ],
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert 'ExecutionStatus="Completed"' in query
    assert 'mm_state="completed"' not in query

def test_list_executions_temporal_query_rejects_contradictory_canonical_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(),
        list_workflows=Mock(),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scope": "tasks",
                "stateIn": "completed",
                "stateNotIn": "canceled",
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_execution_query",
        "message": "Cannot combine stateIn and stateNotIn.",
    }
    temporal_client.count_workflows.assert_not_called()

def test_list_executions_temporal_query_includes_canonical_runtime_skill_and_repo_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scope": "tasks",
                "targetRuntimeIn": "codex_cli,claude_code",
                "targetSkillIn": "moonspec-implement",
                "repoIn": "Moon/Mind",
                "repoExact": "owner/repo",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert '(mm_target_runtime="codex_cli" OR mm_target_runtime="claude_code")' in query
    assert 'mm_target_skill="moonspec-implement"' in query
    assert 'mm_repo="owner/repo"' in query
    assert 'mm_repo="Moon/Mind"' not in query

def test_list_executions_temporal_query_prefers_canonical_filters_over_legacy_exact_params() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scope": "tasks",
                "state": "executing",
                "stateIn": "completed",
                "repo": "legacy/repo",
                "repoExact": "owner/repo",
                "targetRuntime": "codex_cli",
                "targetRuntimeIn": "claude_code",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert (
        '(ExecutionStatus="Completed")' in query
        or 'ExecutionStatus="Completed"' in query
    )
    assert 'mm_state="executing"' not in query
    assert 'mm_repo="owner/repo"' in query
    assert 'mm_repo="legacy/repo"' not in query
    assert 'mm_target_runtime="codex_cli"' in query
    assert 'mm_target_runtime="claude_code"' not in query

def test_list_executions_temporal_query_includes_canonical_date_bounds() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scope": "tasks",
                "scheduledFrom": "2026-05-01",
                "scheduledTo": "2026-05-05",
                "createdFrom": "2026-05-02",
                "createdTo": "2026-05-06",
                "finishedFrom": "2026-05-03",
                "finishedTo": "2026-05-07",
                "scheduledBlank": "exclude",
                "finishedBlank": "exclude",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert "mm_scheduled_for IS NOT NULL" in query
    assert 'mm_scheduled_for>="2026-05-01T00:00:00Z"' in query
    assert 'mm_scheduled_for<="2026-05-05T23:59:59.999999Z"' in query
    assert 'StartTime>="2026-05-02T00:00:00Z"' in query
    assert 'StartTime<="2026-05-06T23:59:59.999999Z"' in query
    assert "CloseTime IS NOT NULL" in query
    assert 'CloseTime>="2026-05-03T00:00:00Z"' in query
    assert 'CloseTime<="2026-05-07T23:59:59.999999Z"' in query

def test_list_executions_temporal_query_includes_blank_date_filter_semantics() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scope": "tasks",
                "scheduledFrom": "2026-05-01",
                "scheduledBlank": "include",
                "finishedBlank": "include",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert '(mm_scheduled_for IS NULL OR (mm_scheduled_for>="2026-05-01T00:00:00Z"))' in query
    assert "CloseTime IS NULL" in query

def test_list_executions_temporal_query_supports_sort_and_text_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scope": "tasks",
                "repoContains": "Moon",
                "workflowIdContains": "wf-",
                "titleContains": "release",
                "sort": "createdAt",
                "sortDir": "asc",
            },
        )

    assert response.status_code == 200
    count_query = temporal_client.count_workflows.await_args.kwargs["query"]
    list_query = temporal_client.list_workflows.call_args.kwargs["query"]
    assert 'mm_repo LIKE "%Moon%"' in count_query
    assert 'WorkflowId LIKE "%wf-%"' in count_query
    assert 'mm_title LIKE "%release%"' in count_query
    assert "ORDER BY" not in count_query
    assert list_query.endswith("ORDER BY StartTime ASC")


def test_list_executions_temporal_query_uses_workflow_id_text_filter() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scope": "tasks",
                "workflowIdContains": "wf-",
                "sort": "workflowId",
            },
        )

    assert response.status_code == 200
    count_query = temporal_client.count_workflows.await_args.kwargs["query"]
    list_query = temporal_client.list_workflows.call_args.kwargs["query"]
    assert 'WorkflowId LIKE "%wf-%"' in count_query
    assert list_query.endswith("ORDER BY WorkflowId DESC")


def test_execution_sort_fields_do_not_expose_task_id_alias() -> None:
    from api_service.api.routers import executions as executions_module

    assert "workflowId" in executions_module._EXECUTION_SORT_FIELDS
    assert "taskId" not in executions_module._EXECUTION_SORT_FIELDS

def test_list_executions_temporal_query_rejects_invalid_filter_bounds() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)
    temporal_client = SimpleNamespace(count_workflows=AsyncMock(), list_workflows=Mock())
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        invalid_blank = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scheduledBlank": "maybe",
            },
        )
        invalid_range = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "createdFrom": "2026-05-06",
                "createdTo": "2026-05-01",
            },
        )
        invalid_sort = test_client.get(
            "/api/executions",
            params={"source": "temporal", "sort": "workflowType"},
        )

    assert invalid_blank.status_code == 422
    assert invalid_blank.json()["detail"]["code"] == "invalid_execution_query"
    assert "include, exclude" in invalid_blank.json()["detail"]["message"]
    assert invalid_range.status_code == 422
    assert "createdFrom must be before or equal to createdTo" in invalid_range.json()["detail"]["message"]
    assert invalid_sort.status_code == 422
    assert "sort must be one of" in invalid_sort.json()["detail"]["message"]
    temporal_client.count_workflows.assert_not_called()

def test_execution_facets_exclude_requested_facet_filter_and_keep_task_scope() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=False)

    async def _memo():
        return {}

    workflow = SimpleNamespace(
        search_attributes={
            "mm_target_runtime": ["claude_code"],
            "mm_state": "executing",
        },
        memo=_memo,
    )

    class _WorkflowIterator:
        current_page = [workflow]
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(
            side_effect=[SimpleNamespace(count=7), SimpleNamespace(count=0)]
        ),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions/facets",
            params={
                "source": "temporal",
                "facet": "targetRuntime",
                "stateIn": "executing",
                "targetRuntimeIn": "codex_cli",
            },
        )

    assert response.status_code == 200
    base_query = temporal_client.list_workflows.call_args.kwargs["query"]
    assert 'WorkflowType="MoonMind.Run"' in base_query
    assert 'mm_entry="user_workflow"' in base_query
    assert "mm_owner_id=" in base_query
    assert 'mm_state="executing"' in base_query
    assert "mm_target_runtime" not in base_query
    body = response.json()
    assert body["facet"] == "targetRuntime"
    assert body["items"] == [{"value": "claude_code", "label": "Claude Code", "count": 7}]
    assert body["blankCount"] == 0
    assert body["source"] == "authoritative"

def test_execution_status_facet_counts_static_status_values_with_task_scope() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=False)
    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=1)),
        list_workflows=Mock(),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions/facets",
            params={"source": "temporal", "facet": "status", "pageSize": 2},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [
        {"value": "scheduled", "label": "Scheduled", "count": 1},
        {"value": "initializing", "label": "Initializing", "count": 1},
    ]
    first_count_query = temporal_client.count_workflows.await_args_list[0].kwargs["query"]
    assert 'WorkflowType="MoonMind.Run"' in first_count_query
    assert 'mm_entry="user_workflow"' in first_count_query
    assert "mm_owner_id=" in first_count_query
    assert body["truncated"] is True

def test_execution_status_facet_supports_real_pagination() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=False)
    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=1)),
        list_workflows=Mock(),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        first_page = test_client.get(
            "/api/executions/facets",
            params={"source": "temporal", "facet": "status", "pageSize": 2},
        )
        second_page = test_client.get(
            "/api/executions/facets",
            params={
                "source": "temporal",
                "facet": "status",
                "pageSize": 2,
                "nextPageToken": first_page.json()["nextPageToken"],
            },
        )

    assert first_page.status_code == 200
    assert first_page.json()["nextPageToken"] == base64.b64encode(b"2").decode("utf-8")
    assert second_page.status_code == 200
    assert second_page.json()["items"] == [
        {
            "value": "waiting_on_dependencies",
            "label": "Waiting On Dependencies",
            "count": 1,
        },
        {"value": "planning", "label": "Planning", "count": 1},
    ]
    assert second_page.json()["nextPageToken"] == base64.b64encode(b"4").decode("utf-8")
    temporal_client.list_workflows.assert_not_called()

def test_execution_facets_reject_malformed_next_page_token() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=False)
    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions/facets",
            params={
                "source": "temporal",
                "facet": "targetRuntime",
                "nextPageToken": "not base64",
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_execution_query",
        "message": "nextPageToken must be a valid base64 token.",
    }
    temporal_client.list_workflows.assert_not_called()

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

def test_create_task_shaped_execution_rejects_explicit_skill_step_without_skill_payload(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    """MM-569: explicit `type: skill` steps must require a skill sub-payload."""
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "instructions": "Run a skill step without a payload.",
                    "steps": [
                        {
                            "id": "missing-skill",
                            "title": "Missing skill payload",
                            "type": "skill",
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert (
        "payload.task.steps[0].skill is required for Skill steps"
        in response.json()["detail"]["message"]
    )
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_preserves_empty_skill_args(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    """MM-569: empty `skill.args` dictionaries must be preserved through normalization,
    matching the tool-step `inputs` behavior so downstream contract validation sees
    the same shape regardless of step type."""
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "instructions": "Run a skill step without args.",
                    "steps": [
                        {
                            "id": "skill-empty-args",
                            "title": "Skill with empty args",
                            "type": "skill",
                            "skill": {"id": "noop", "args": {}},
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201, response.text
    create_kwargs = service.create_execution.await_args.kwargs
    initial_parameters = create_kwargs["initial_parameters"]
    normalized_steps = initial_parameters["task"]["steps"]
    assert len(normalized_steps) == 1
    assert normalized_steps[0]["skill"] == {"id": "noop", "args": {}}


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

    assert response.status_code == 201, response.json()
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

    assert response.status_code == 201, response.json()
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

def test_create_task_shaped_execution_allows_jira_orchestrate_pr_publish(
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
                "publishMode": "pr",
                "task": {
                    "instructions": "Run Jira Orchestrate for THOR-352.",
                    "tool": {"type": "skill", "name": "jira-orchestrate"},
                    "skill": {"id": "jira-orchestrate"},
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "pr"},
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


def test_create_task_shaped_execution_allows_jira_orchestrate_first_step_skill_pr_publish(
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
                "publishMode": "pr",
                "task": {
                    "instructions": "Run Jira Orchestrate for THOR-352.",
                    "tool": {"type": "skill", "name": "jira-issue-updater"},
                    "skill": {"id": "jira-issue-updater"},
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "pr"},
                    "steps": [
                        {
                            "id": "tpl:jira-orchestrate:1.0.0:01",
                            "title": "Move Jira issue",
                            "instructions": "Transition THOR-352 to In Progress.",
                            "skill": {"id": "jira-issue-updater", "args": {}},
                        }
                    ],
                    "appliedStepTemplates": [
                        {
                            "slug": "jira-orchestrate",
                            "version": "1.0.0",
                            "stepIds": ["tpl:jira-orchestrate:1.0.0:01"],
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201, response.json()
    service.create_execution.assert_awaited_once()
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "pr"
    assert initial_parameters["task"]["publish"]["mode"] == "pr"
    assert initial_parameters["task"]["skill"]["name"] == "jira-issue-updater"


def test_create_task_shaped_execution_allows_pr_publish_for_jira_updater(
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
                "publishMode": "pr",
                "task": {
                    "title": (
                        "Change Jira issue MM-657 to status In Progress before "
                        "implementation starts."
                    ),
                    "instructions": (
                        "Change Jira issue MM-657 to status In Progress before "
                        "implementation starts."
                    ),
                    "tool": {"type": "skill", "name": "jira-issue-updater"},
                    "skill": {"id": "jira-issue-updater"},
                    "runtime": {"mode": "claude_code"},
                    "publish": {"mode": "pr"},
                },
            },
        },
    )

    assert response.status_code == 201, response.json()
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "pr"
    assert initial_parameters["task"]["publish"]["mode"] == "pr"


def test_create_task_shaped_execution_defaults_jira_updater_publish_none(
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
                "publishMode": "pr",
                "task": {
                    "title": "Change Jira issue MM-657 to status In Progress.",
                    "instructions": "Change Jira issue MM-657 to status In Progress.",
                    "tool": {"type": "skill", "name": "jira-issue-updater"},
                    "runtime": {"mode": "claude_code"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "none"
    assert initial_parameters["task"]["publish"]["mode"] == "none"


def test_create_task_shaped_execution_allows_jira_orchestrate_publish_none(
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
                "publishMode": "none",
                "task": {
                    "instructions": "Run Jira Orchestrate for THOR-352.",
                    "tool": {"type": "skill", "name": "jira-orchestrate"},
                    "skill": {"id": "jira-orchestrate"},
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "none"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "none"
    assert initial_parameters["task"]["publish"]["mode"] == "none"
    assert initial_parameters["requiredCapabilities"] == []

def test_create_task_shaped_execution_preserves_report_output_contract(
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
                "reportOutput": {
                    "enabled": True,
                    "required": True,
                    "reportType": "integration_test_report",
                    "title": "Integration test report",
                },
                "task": {
                    "instructions": "Run the integration test suite.",
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "none"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["reportOutput"] == {
        "enabled": True,
        "required": True,
        "reportType": "integration_test_report",
        "title": "Integration test report",
    }
    assert initial_parameters["task"]["reportOutput"] == initial_parameters[
        "reportOutput"
    ]
    task_instructions = initial_parameters["task"]["instructions"]
    assert "MoonMind report output contract" in task_instructions
    assert "answer that request directly in the final report body" in task_instructions


def test_create_task_report_output_defaults_primary_path_to_markdown_suffix(
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
                "reportOutput": {
                    "enabled": True,
                    "required": True,
                    "reportType": "agent_run_report",
                    "primaryPath": "reports/final-report",
                },
                "task": {
                    "instructions": "Generate a report.",
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "none"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert (
        initial_parameters["reportOutput"]["primaryPath"]
        == "reports/final-report.md"
    )
    assert (
        "Also write the same report to `reports/final-report.md`"
        in initial_parameters["task"]["instructions"]
    )


def test_create_task_report_output_rejects_primary_path_over_limit_after_suffix(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    primary_path = "a" * 512

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "reportOutput": {
                    "enabled": True,
                    "required": True,
                    "reportType": "agent_run_report",
                    "primaryPath": primary_path,
                },
                "task": {
                    "instructions": "Generate a report.",
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "none"},
                },
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["message"] == (
        "reportOutput.primaryPath must be 512 characters or fewer."
    )
    service.create_execution.assert_not_awaited()


def test_create_task_report_output_preserves_explicit_primary_path_suffix(
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
                "reportOutput": {
                    "enabled": True,
                    "required": True,
                    "reportType": "agent_run_report",
                    "primaryPath": "reports/final-report.txt",
                },
                "task": {
                    "instructions": "Generate a report.",
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "none"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert (
        initial_parameters["reportOutput"]["primaryPath"]
        == "reports/final-report.txt"
    )


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
                "selectedSteps": None,
                "currentTargetState": None,
                "allowedActions": None,
                "evidenceDegraded": None,
                "unavailableEvidenceClasses": None,
                "liveObservation": None,
                "lockOutcome": None,
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
        "selectedSteps": None,
        "currentTargetState": None,
        "allowedActions": None,
        "evidenceDegraded": None,
        "unavailableEvidenceClasses": None,
        "liveObservation": None,
        "lockOutcome": None,
        "approvalState": None,
        "createdAt": now.isoformat().replace("+00:00", "Z"),
        "updatedAt": now.isoformat().replace("+00:00", "Z"),
    }
    service.list_remediation_targets.assert_awaited_once_with("mm:remediation-1")
    service.list_remediations_for_target.assert_not_called()

def test_list_remediations_for_remediation_returns_rich_operator_metadata(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    now = datetime.now(UTC)
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )
    service.list_remediation_targets.return_value = [
        SimpleNamespace(
            remediation_workflow_id="mm:remediation-rich",
            remediation_run_id="run-remediation-rich",
            target_workflow_id="mm:target-rich",
            target_run_id="run-target-rich",
            mode="snapshot_then_follow",
            authority_mode="approval_gated",
            status="awaiting_approval",
            active_lock_scope="target_execution",
            active_lock_holder="mm:remediation-rich",
            latest_action_summary="Proposed session interrupt",
            outcome="precondition_failed",
            context_artifact_ref="art_context_rich",
            selected_steps=["collect-context", "repair-runtime"],
            current_target_state="awaiting_external",
            allowed_actions=["inspect_context", "request_approval"],
            evidence_degraded=True,
            unavailable_evidence_classes=["runtime_stderr", "provider_snapshot"],
            live_observation={
                "status": "active",
                "label": "Live observation active",
                "sequenceCursor": "stdout:42",
                "reconnectState": "reconnected",
                "epoch": "run-target-rich:2",
                "fallbackReason": "Durable context remains authoritative.",
                "rawPath": "/var/lib/moonmind/raw-context.json",
            },
            lock_outcome={
                "state": "conflict",
                "holder": "mm:remediation-rich",
                "releasedAt": None,
            },
            approval_state={
                "requestId": "approval-rich",
                "actionKind": "session_interrupt",
                "riskTier": "high",
                "preconditions": "Target run is still awaiting an external session.",
                "blastRadius": "One managed runtime session.",
                "decision": "pending",
                "canDecide": True,
                "auditRef": "audit-rich",
            },
            created_at=now,
            updated_at=now,
        )
    ]

    response = test_client.get(
        "/api/executions/mm:remediation-rich/remediations",
        params={"direction": "outbound"},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["selectedSteps"] == ["collect-context", "repair-runtime"]
    assert item["currentTargetState"] == "awaiting_external"
    assert item["allowedActions"] == ["inspect_context", "request_approval"]
    assert item["evidenceDegraded"] is True
    assert item["unavailableEvidenceClasses"] == [
        "runtime_stderr",
        "provider_snapshot",
    ]
    assert item["liveObservation"] == {
        "status": "active",
        "label": "Live observation active",
        "sequenceCursor": "stdout:42",
        "reconnectState": "reconnected",
        "epoch": "run-target-rich:2",
        "fallbackReason": "Durable context remains authoritative.",
    }
    assert item["lockOutcome"] == {
        "state": "conflict",
        "holder": "mm:remediation-rich",
        "releasedAt": None,
    }
    assert item["approvalState"] == {
        "requestId": "approval-rich",
        "actionKind": "session_interrupt",
        "riskTier": "high",
        "preconditions": "Target run is still awaiting an external session.",
        "blastRadius": "One managed runtime session.",
        "decision": "pending",
        "decisionActor": None,
        "decisionAt": None,
        "canDecide": True,
        "auditRef": "audit-rich",
    }
    assert "/var/lib/moonmind/raw-context.json" not in json.dumps(item)
    service.list_remediation_targets.assert_awaited_once_with("mm:remediation-rich")
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
                        "branch": "codex/pr-resolver",
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
        "branch": "codex/pr-resolver",
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

    assert "proposeTasks" not in initial_parameters
    assert "proposalPolicy" not in initial_parameters
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


def test_serialize_execution_exposes_proposal_outcome_summary() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.PROPOSALS)
    record.memo["proposals"] = {
        "requested": True,
        "generatedCount": 2,
        "submittedCount": 2,
        "deliveredCount": 1,
        "validationErrors": [
            {"code": "proposal_missing_task", "message": "proposal skipped: [REDACTED]"}
        ],
        "deliveryFailures": [
            {
                "provider": "jira",
                "code": "delivery_failed",
                "message": "delivery failed: [REDACTED]",
            }
        ],
        "externalLinks": [
            {
                "provider": "jira",
                "externalKey": "MM-901",
                "externalUrl": "https://jira.example/browse/MM-901",
            }
        ],
        "dedupUpdates": [
            {
                "provider": "github",
                "externalKey": "42",
                "created": False,
                "duplicateSource": "existing-open-issue",
            }
        ],
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["state"] == "proposals"
    assert payload["dashboardStatus"] == "running"
    assert payload["proposalSummary"]["deliveredCount"] == 1
    assert payload["proposalSummary"]["externalLinks"][0]["externalKey"] == "MM-901"
    assert payload["proposalOutcomes"][0]["provider"] == "jira"
    assert (
        payload["proposalOutcomes"][0]["externalUrl"]
        == "https://jira.example/browse/MM-901"
    )


def test_serialize_execution_deduplicates_proposal_outcomes_by_external_key() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.PROPOSALS)
    record.memo["proposals"] = {
        "externalLinks": [
            {
                "provider": "jira",
                "externalKey": "MM-901",
                "externalUrl": "https://jira.example/browse/MM-901",
            }
        ],
        "dedupUpdates": [
            {
                "provider": "jira",
                "externalKey": "MM-901",
                "created": False,
                "duplicateSource": "existing-open-issue",
            }
        ],
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["proposalOutcomes"] == [
        {
            "provider": "jira",
            "externalKey": "MM-901",
            "externalUrl": "https://jira.example/browse/MM-901",
            "deliveryStatus": "updated",
            "created": False,
            "duplicateSource": "existing-open-issue",
        }
    ]

def test_serialize_execution_includes_failed_proposal_outcomes() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.COMPLETED)
    record.memo["proposals"] = {
        "deliveryFailures": [
            {
                "provider": "jira",
                "externalKey": "MM-902",
                "code": "delivery_failed",
                "message": "delivery failed: [REDACTED]",
            }
        ],
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["proposalOutcomes"] == [
        {
            "provider": "jira",
            "externalKey": "MM-902",
            "code": "delivery_failed",
            "message": "delivery failed: [REDACTED]",
            "deliveryStatus": "failed",
        }
    ]


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
                                "args": {"feature": "workflow-start"},
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
                "args": {"feature": "workflow-start"},
                "requiredCapabilities": ["git", "github"],
            },
        },
        {
            "id": "tpl:demo:1.0.0:02",
            "title": "Implement the restored builder",
            "instructions": "Restore presets and multi-step submission.",
        },
    ]

def test_create_task_shaped_execution_preserves_recursive_preset_metadata(
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
                    "title": "Compile recursive presets",
                    "instructions": "Run the compiled task.",
                    "runtime": {"mode": "codex_cli"},
                    "publish": {"mode": "pr"},
                    "jira": {"issueKey": "MM-630"},
                    "authoredPresets": [
                        {
                            "presetSlug": "root-preset",
                            "presetVersion": "1.0.0",
                            "includePath": ["root-preset@1.0.0"],
                        },
                        {
                            "presetSlug": "child-preset",
                            "presetVersion": "1.0.0",
                            "alias": "checks",
                            "inputMapping": {"target": "recursive presets"},
                            "includePath": [
                                "root-preset@1.0.0",
                                "checks:child-preset@1.0.0",
                            ],
                        },
                    ],
                    "appliedStepTemplates": [
                        {
                            "slug": "root-preset",
                            "version": "1.0.0",
                            "stepIds": [
                                "tpl:root-preset:1.0.0:01",
                                "tpl:child-preset:1.0.0:01",
                            ],
                            "composition": {
                                "slug": "root-preset",
                                "version": "1.0.0",
                                "path": ["root-preset@1.0.0"],
                                "stepIds": [
                                    "tpl:root-preset:1.0.0:01",
                                    "tpl:child-preset:1.0.0:01",
                                ],
                                "includes": [
                                    {
                                        "slug": "child-preset",
                                        "version": "1.0.0",
                                        "alias": "checks",
                                        "path": [
                                            "root-preset@1.0.0",
                                            "checks:child-preset@1.0.0",
                                        ],
                                        "stepIds": ["tpl:child-preset:1.0.0:01"],
                                    }
                                ],
                            },
                        }
                    ],
                    "steps": [
                        {
                            "id": "tpl:root-preset:1.0.0:01",
                            "title": "Prepare task",
                            "instructions": "Prepare the task context.",
                            "source": {
                                "kind": "preset-derived",
                                "presetSlug": "root-preset",
                                "presetVersion": "1.0.0",
                                "includePath": ["root-preset@1.0.0"],
                                "originalStepId": "prepare-task",
                            },
                        },
                        {
                            "id": "tpl:child-preset:1.0.0:01",
                            "title": "Run checks",
                            "instructions": "Run recursive preset checks.",
                            "source": {
                                "kind": "preset-derived",
                                "presetSlug": "child-preset",
                                "presetVersion": "1.0.0",
                                "includePath": [
                                    "root-preset@1.0.0",
                                    "checks:child-preset@1.0.0",
                                ],
                                "originalStepId": "run-checks",
                            },
                        },
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    task = service.create_execution.await_args.kwargs["initial_parameters"]["task"]
    assert task["steps"][0]["source"]["presetSlug"] == "root-preset"
    assert task["steps"][0]["source"]["originalStepId"] == "prepare-task"
    assert task["steps"][1]["source"]["includePath"] == [
        "root-preset@1.0.0",
        "checks:child-preset@1.0.0",
    ]
    assert task["steps"][1]["source"]["originalStepId"] == "run-checks"
    assert [preset["presetSlug"] for preset in task["authoredPresets"]] == [
        "root-preset",
        "child-preset",
    ]
    assert task["authoredPresets"][1]["inputMapping"] == {
        "target": "recursive presets"
    }
    assert task["appliedStepTemplates"][0]["composition"]["includes"][0]["alias"] == (
        "checks"
    )
    assert task["runtime"] == {"mode": "codex_cli"}
    assert task["publish"] == {"mode": "pr"}


def test_create_task_shaped_execution_preserves_manual_and_preset_step_order(
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
                    "instructions": "Run mixed task.",
                    "authoredPresets": [
                        {
                            "presetSlug": "parent-flow",
                            "presetVersion": "1.0.0",
                            "includePath": ["parent-flow@1.0.0"],
                        }
                    ],
                    "steps": [
                        {
                            "id": "manual-before",
                            "type": "skill",
                            "instructions": "Manual before.",
                            "skill": {"id": "auto"},
                        },
                        {
                            "id": "tpl:parent-flow:1.0.0:01:abcdef12",
                            "type": "skill",
                            "instructions": "Preset one.",
                            "skill": {"id": "auto"},
                            "source": {
                                "kind": "preset-derived",
                                "presetSlug": "parent-flow",
                                "presetVersion": "1.0.0",
                                "includePath": ["parent-flow@1.0.0"],
                                "originalStepId": "preset-derived-1",
                            },
                        },
                        {
                            "id": "tpl:parent-flow:1.0.0:02:abcdef12",
                            "type": "skill",
                            "instructions": "Preset two.",
                            "skill": {"id": "auto"},
                            "source": {
                                "kind": "preset-derived",
                                "presetSlug": "parent-flow",
                                "presetVersion": "1.0.0",
                                "includePath": ["parent-flow@1.0.0"],
                                "originalStepId": "preset-derived-2",
                            },
                        },
                        {
                            "id": "manual-after",
                            "type": "skill",
                            "instructions": "Manual after.",
                            "skill": {"id": "auto"},
                        },
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    task = service.create_execution.await_args.kwargs["initial_parameters"]["task"]
    assert [step["id"] for step in task["steps"]] == [
        "manual-before",
        "tpl:parent-flow:1.0.0:01:abcdef12",
        "tpl:parent-flow:1.0.0:02:abcdef12",
        "manual-after",
    ]
    assert task["steps"][0].get("source") in (None, {"kind": "manual"})
    assert task["steps"][3].get("source") in (None, {"kind": "manual"})
    assert task["steps"][1]["source"]["originalStepId"] == "preset-derived-1"
    assert task["steps"][2]["source"]["originalStepId"] == "preset-derived-2"


def test_create_task_shaped_execution_preserves_detached_edited_step_source(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    detached_source = {
        "kind": "detached",
        "presetSlug": "quality-flow",
        "presetVersion": "1.0.0",
        "includePath": ["root-flow@1.0.0", "quality-flow@1.0.0"],
        "originalStepId": "lint-target",
    }
    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "task": {
                    "instructions": "Run edited preset step.",
                    "steps": [
                        {
                            "id": "edited-lint-step",
                            "type": "skill",
                            "instructions": "Edited lint instructions.",
                            "skill": {"id": "auto"},
                            "source": detached_source,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    task = service.create_execution.await_args.kwargs["initial_parameters"]["task"]
    assert task["steps"][0]["instructions"] == "Edited lint instructions."
    assert task["steps"][0]["source"] == detached_source

def test_create_task_shaped_execution_does_not_fabricate_manual_preset_metadata(
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
                    "title": "Manual task",
                    "instructions": "Run one manual step.",
                    "steps": [
                        {
                            "id": "manual-1",
                            "title": "Manual step",
                            "instructions": "Do the manual work.",
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    task = service.create_execution.await_args.kwargs["initial_parameters"]["task"]
    assert "authoredPresets" not in task
    assert "appliedStepTemplates" not in task
    assert "source" not in task["steps"][0]

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

def test_create_task_shaped_execution_inherits_caller_runtime(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.create_execution.return_value = _build_execution_record()
    service.describe_execution.return_value = SimpleNamespace(
        workflow_id="mm:parent-task",
        owner_id=str(user.id),
        parameters={
            "targetRuntime": "codex",
            "model": "gpt-5.4",
            "effort": "high",
            "task": {
                "runtime": {
                    "executionProfileRef": "codex_default",
                }
            },
        },
        memo={},
        search_attributes={},
    )

    response = test_client.post(
        "/api/executions",
        headers={
            "X-MoonMind-Task-Workflow-Id": "mm:parent-task",
            "X-MoonMind-Task-Run-Identifier": "task-run-1",
        },
        json={
            "type": "task",
            "payload": {
                "runtimeInheritance": "caller",
                "repository": "MoonLadderStudios/MoonMind",
                "requiredCapabilities": ["gh"],
                "task": {
                    "title": "feature/example",
                    "instructions": "Resolve PR #42 on branch `feature/example`.",
                    "skill": {"name": "pr-resolver", "version": "1.0"},
                    "inputs": {"repo": "MoonLadderStudios/MoonMind", "pr": "42"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["targetRuntime"] == "codex_cli"
    assert initial_parameters["model"] == "gpt-5.4"
    assert initial_parameters["effort"] == "high"
    runtime = initial_parameters["task"]["runtime"]
    assert runtime == {
        "mode": "codex_cli",
        "model": "gpt-5.4",
        "effort": "high",
        "executionProfileRef": "codex_default",
    }


def test_create_task_shaped_execution_rejects_caller_inheritance_for_user(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "runtimeInheritance": "caller",
                "task": {
                    "title": "feature/example",
                    "instructions": "Resolve PR #42 on branch `feature/example`.",
                    "skill": {"name": "pr-resolver", "version": "1.0"},
                    "inputs": {"repo": "MoonLadderStudios/MoonMind", "pr": "42"},
                },
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "runtime_inheritance_requires_task_principal",
        "message": 'runtimeInheritance="caller" requires a task-scoped principal.',
    }
    service.create_execution.assert_not_awaited()


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
    assert initial_parameters["task"]["steps"][0]["id"] == "step-1"

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
    assert step_payload["id"] == "step-1"
    assert step_payload["inputAttachments"] == [
        {
            "artifactId": "art_01STEPINPUT000000000000",
            "filename": "step.png",
            "contentType": "image/png",
            "sizeBytes": 20,
        }
    ]
    assert "input_attachments" not in step_payload

def test_create_task_shaped_execution_preserves_canonical_mm627_task_shape(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            SimpleNamespace(
                artifact_id="art_01MM627OBJECTIVE000000000",
                status=TemporalArtifactStatus.COMPLETE,
                content_type="image/png",
                size_bytes=10,
            ),
            SimpleNamespace(
                artifact_id="art_01MM627STEP00000000000000",
                status=TemporalArtifactStatus.COMPLETE,
                content_type="image/png",
                size_bytes=20,
            ),
        ]
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "task": {
                    "title": "MM-627 canonical task payload",
                    "instructions": "Preserve the submitted task exactly.",
                    "dependsOn": ["mm:dep-1"],
                    "runtime": {
                        "mode": "codex",
                        "model": "gpt-5-codex",
                        "effort": "high",
                    },
                    "publish": {"mode": "pr", "baseBranch": "main"},
                    "git": {"branch": "feature/mm-627"},
                    "inputAttachments": [
                        {
                            "artifactId": "art_01MM627OBJECTIVE000000000",
                            "filename": "objective.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                    "steps": [
                        {
                            "id": "step-1",
                            "title": "Inspect step",
                            "instructions": "Inspect the step screenshot.",
                            "source": {
                                "kind": "jira",
                                "issueKey": "MM-627",
                            },
                            "storyOutput": {"mode": "jira"},
                            "jiraOrchestration": {
                                "issueKey": "MM-627",
                                "preset": "jira-orchestrate",
                            },
                            "inputAttachments": [
                                {
                                    "artifactId": "art_01MM627STEP00000000000000",
                                    "filename": "step.png",
                                    "contentType": "image/png",
                                    "sizeBytes": 20,
                                }
                            ],
                        }
                    ],
                    "storyOutput": {"mode": "jira"},
                    "authoredPresets": [
                        {"slug": "jira-orchestrate", "version": "2026-05-08"}
                    ],
                    "appliedStepTemplates": [
                        {
                            "slug": "jira-implementation",
                            "version": "2026-05-08",
                            "stepIds": ["step-1"],
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
    task = initial_parameters["task"]
    assert task["git"] == {"branch": "feature/mm-627"}
    assert task["runtime"] == {
        "mode": "codex_cli",
        "model": "gpt-5-codex",
        "effort": "high",
    }
    assert task["publish"]["mode"] == "pr"
    assert task["dependsOn"] == ["mm:dep-1"]
    assert task["storyOutput"] == {"mode": "jira"}
    assert task["authoredPresets"] == [
        {"slug": "jira-orchestrate", "version": "2026-05-08"}
    ]
    assert task["appliedStepTemplates"] == [
        {
            "slug": "jira-implementation",
            "version": "2026-05-08",
            "stepIds": ["step-1"],
        }
    ]
    assert task["inputAttachments"][0]["artifactId"] == "art_01MM627OBJECTIVE000000000"
    assert task["steps"][0]["id"] == "step-1"
    assert task["steps"][0]["source"] == {"kind": "jira", "issueKey": "MM-627"}
    assert task["steps"][0]["inputAttachments"][0]["artifactId"] == (
        "art_01MM627STEP00000000000000"
    )

@pytest.mark.parametrize(
    "task_payload",
    [
        {"instructions": "Run task.", "git": {"targetBranch": "feature/legacy"}},
        {"instructions": "Run task.", "targetBranch": "feature/legacy"},
    ],
)
def test_create_task_shaped_execution_rejects_legacy_target_branch_aliases(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    task_payload: dict[str, Any],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "task": task_payload,
            },
        },
    )

    assert response.status_code == 422
    assert "targetBranch" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_rejects_non_string_repository(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": {"owner": "Moon", "name": "Mind"},
                "targetRuntime": "codex",
                "task": {"instructions": "Run task."},
            },
        },
    )

    assert response.status_code == 422
    assert "repository" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_rejects_attachment_declared_for_multiple_targets(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            SimpleNamespace(
                artifact_id="art_01MM627DUPLICATE00000000",
                status=TemporalArtifactStatus.COMPLETE,
                content_type="image/png",
                size_bytes=10,
            )
        ]
    )

    attachment = {
        "artifactId": "art_01MM627DUPLICATE00000000",
        "filename": "same.png",
        "contentType": "image/png",
        "sizeBytes": 10,
    }
    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "task": {
                    "instructions": "Run task.",
                    "inputAttachments": [attachment],
                    "steps": [
                        {
                            "id": "step-1",
                            "instructions": "Run step.",
                            "inputAttachments": [attachment],
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "declared more than once" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_duplicate_attachment_declaration_for_same_target(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            SimpleNamespace(
                artifact_id="art_01MM627DUPLICATE00000001",
                status=TemporalArtifactStatus.COMPLETE,
                content_type="image/png",
                size_bytes=10,
            )
        ]
    )

    attachment = {
        "artifactId": "art_01MM627DUPLICATE00000001",
        "filename": "same.png",
        "contentType": "image/png",
        "sizeBytes": 10,
    }
    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "task": {
                    "instructions": "Run task.",
                    "inputAttachments": [attachment, attachment],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "declared more than once" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


@pytest.mark.parametrize(
    ("artifact_status", "message_fragment"),
    [
        (TemporalArtifactStatus.PENDING_UPLOAD, "pending_upload"),
        (TemporalArtifactStatus.FAILED, "failed"),
        (TemporalArtifactStatus.DELETED, "deleted"),
    ],
)
def test_create_task_shaped_execution_rejects_unfinalized_input_attachment_refs(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    artifact_status: TemporalArtifactStatus,
    message_fragment: str,
) -> None:
    """MM-628: binary input refs must be finalized before execution creation."""

    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    artifact_id = f"art_01MM628{artifact_status.value.upper():0<18}"[:30]
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            SimpleNamespace(
                artifact_id=artifact_id,
                status=artifact_status,
                content_type="image/png",
                size_bytes=10,
                created_by_principal=str(_user.id),
            )
        ]
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "task": {
                    "instructions": "Review binary input.",
                    "inputAttachments": [
                        {
                            "artifactId": artifact_id,
                            "filename": "input.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert message_fragment in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_missing_input_attachment_artifact(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session([])

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "task": {
                    "instructions": "Review binary input.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01MM628MISSING000000000",
                            "filename": "input.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "was not found" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_other_users_completed_input_attachment_ref(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MM-628: another user's completed artifact cannot be attached to a new execution."""

    test_client, service, user = client
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak")
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    artifact_id = "art_01MM628WRONGOWNER0000000"
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            _completed_attachment_artifact(
                artifact_id,
                created_by_principal=f"other-{user.id}",
            )
        ]
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "task": {
                    "instructions": "Review binary input.",
                    "inputAttachments": [
                        {
                            "artifactId": artifact_id,
                            "filename": "input.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "not authorized" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_service_owned_attachment_for_user(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MM-628: service ownership does not make an artifact attachable by any user."""

    test_client, service, _user = client
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak")
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    artifact_id = "art_01MM628SERVICEOWNER0000"
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            _completed_attachment_artifact(
                artifact_id,
                created_by_principal="service:artifact-generator",
            )
        ]
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "task": {
                    "instructions": "Review binary input.",
                    "inputAttachments": [
                        {
                            "artifactId": artifact_id,
                            "filename": "input.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "not authorized" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


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

def test_create_task_shaped_recurring_schedule_normalizes_proposal_intent(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override
    next_run_at = datetime.now(UTC) + timedelta(hours=1)

    with patch(
        "api_service.services.recurring_tasks_service.RecurringTasksService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(),
                name="Inline schedule",
                cron="0 * * * *",
                timezone="UTC",
                next_run_at=next_run_at,
            )
        )

        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "proposeTasks": True,
                    "proposalPolicy": {"targets": ["moonmind"]},
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "task": {
                        "instructions": "Run this on a schedule",
                        "proposeTasks": True,
                        "proposalPolicy": {
                            "targets": ["project"],
                            "defaultRuntime": "gemini_cli",
                        },
                    },
                },
            },
        )

    assert response.status_code == 201, response.json()
    target = service.create_definition.await_args.kwargs["target"]
    stored_payload = target["job"]["payload"]
    assert "proposeTasks" not in stored_payload
    assert "proposalPolicy" not in stored_payload
    assert stored_payload["task"]["proposeTasks"] is True
    assert stored_payload["task"]["proposalPolicy"] == {
        "targets": ["project"],
        "defaultRuntime": "gemini_cli",
    }

def test_create_task_shaped_recurring_schedule_uses_root_proposal_fallbacks(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override
    next_run_at = datetime.now(UTC) + timedelta(hours=1)

    with patch(
        "api_service.services.recurring_tasks_service.RecurringTasksService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(),
                name="Inline schedule",
                cron="0 * * * *",
                timezone="UTC",
                next_run_at=next_run_at,
            )
        )

        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "proposeTasks": True,
                    "proposalPolicy": {"targets": ["moonmind"]},
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "task": {
                        "instructions": "Run this on a schedule",
                    },
                },
            },
        )

    assert response.status_code == 201, response.json()
    target = service.create_definition.await_args.kwargs["target"]
    stored_payload = target["job"]["payload"]
    assert "proposeTasks" not in stored_payload
    assert "proposalPolicy" not in stored_payload
    assert stored_payload["task"]["proposeTasks"] is True
    assert stored_payload["task"]["proposalPolicy"] == {"targets": ["moonmind"]}


def test_create_task_shaped_recurring_schedule_passes_metadata_and_response(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override
    definition_id = uuid4()
    next_run_at = datetime.now(UTC) + timedelta(hours=1)
    policy = {"overlap": {"mode": "skip"}}

    with patch(
        "api_service.services.recurring_tasks_service.RecurringTasksService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            return_value=SimpleNamespace(
                id=definition_id,
                name="Nightly workflow",
                cron="0 2 * * *",
                timezone="America/New_York",
                next_run_at=next_run_at,
            )
        )

        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "schedule": {
                        "mode": "recurring",
                        "name": "Nightly workflow",
                        "description": "Run overnight",
                        "enabled": False,
                        "cron": "0 2 * * *",
                        "timezone": "America/New_York",
                        "scopeType": "personal",
                        "policy": policy,
                    },
                    "task": {
                        "instructions": "Run this on a schedule",
                    },
                },
            },
        )

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["scheduled"] is True
    assert body["definitionId"] == str(definition_id)
    assert body["name"] == "Nightly workflow"
    assert body["cron"] == "0 2 * * *"
    assert body["timezone"] == "America/New_York"
    assert body["redirectPath"] == f"/schedules/{definition_id}"
    called_kwargs = service.create_definition.await_args.kwargs
    assert called_kwargs["description"] == "Run overnight"
    assert called_kwargs["enabled"] is False
    assert called_kwargs["scope_type"] == "personal"
    assert called_kwargs["policy"] == policy


def test_create_task_shaped_recurring_schedule_preserves_missing_policy(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override
    next_run_at = datetime.now(UTC) + timedelta(hours=1)

    with patch(
        "api_service.services.recurring_tasks_service.RecurringTasksService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(),
                name="Inline schedule",
                cron="0 * * * *",
                timezone="UTC",
                next_run_at=next_run_at,
            )
        )

        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "task": {
                        "instructions": "Run this on a schedule",
                    },
                },
            },
        )

    assert response.status_code == 201, response.json()
    assert service.create_definition.await_args.kwargs["policy"] is None


def test_create_task_shaped_recurring_schedule_rejects_global_scope_for_non_operator(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.services.recurring_tasks_service.RecurringTasksService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock()

        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                        "scopeType": "global",
                    },
                    "task": {
                        "instructions": "Run this on a schedule",
                    },
                },
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "operator_role_required",
        "message": "Operator privileges are required for global schedules.",
    }
    service.create_definition.assert_not_awaited()


def test_create_task_shaped_recurring_schedule_validation_maps_to_422(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.services.recurring_tasks_service.RecurringTasksService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            side_effect=RecurringTaskValidationError("target.kind is required")
        )

        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "task": {
                        "instructions": "Run this on a schedule",
                    },
                },
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_recurring_task",
        "message": "target.kind is required",
    }

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


def test_task_submission_snapshot_uses_input_artifact_for_stripped_step_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    record = _build_execution_record(has_task_input_snapshot=False)
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

    captured: dict[str, object] = {}

    async def _persist_snapshot(**kwargs) -> str:
        captured.update(kwargs)
        target_record = kwargs["record"]
        target_record.memo = {
            **dict(target_record.memo or {}),
            "task_input_snapshot_ref": "art_snapshot_hydrated_create",
            "task_input_snapshot_version": 1,
            "task_input_snapshot_source_kind": "create",
        }
        return "art_snapshot_hydrated_create"

    persist_mock = AsyncMock(side_effect=_persist_snapshot)
    monkeypatch.setattr(
        "api_service.api.routers.executions._persist_original_task_input_snapshot_from_parameters",
        persist_mock,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex_cli",
                    "inputArtifactRef": "art-full-input",
                    "task": {
                        "instructions": "Top level stays inline.",
                        "runtime": {"mode": "codex_cli"},
                        "steps": [
                            {
                                "id": "step-1",
                                "instructions": "Top level stays inline.",
                            },
                            {"id": "step-2", "title": "Stripped later step"},
                        ],
                    },
                },
            },
        )

    assert response.status_code == 201
    persist_mock.assert_awaited_once()
    assert captured["parameters"]["task"]["steps"][1] == {
        "id": "step-2",
        "title": "Stripped later step",
    }
    assert captured["input_artifact_ref"] == "art-full-input"
    assert captured["source_kind"] == "create"
    session.commit.assert_awaited_once()


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
    _override_user_dependencies(app, is_superuser=True)
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

def test_serialize_execution_leaves_immediate_run_unscheduled() -> None:
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

    assert payload.scheduled_for is None
    assert payload.created_at == created_at

def test_serialize_execution_falls_back_to_updated_at_without_scheduled_time() -> None:
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
    assert payload.scheduled_for is None

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

def test_serialize_execution_falls_back_to_resolved_model_alias() -> None:
    """When `params['model']` is missing, the resolvedModel alias should populate
    both `model` and `resolvedModel` so the workflow detail UI displays consistently."""
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "targetRuntime": "codex_cli",
        "resolvedModel": "gpt-5-codex",
        "modelSource": "runtime_default",
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["model"] == "gpt-5-codex"
    assert dumped["resolvedModel"] == "gpt-5-codex"
    assert dumped["modelSource"] == "runtime_default"

def test_serialize_execution_falls_back_to_requested_model_when_resolved_missing() -> None:
    """If only the user-requested model is recorded, surface it on the detail
    page so the Model fact is rendered consistently."""
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "targetRuntime": "codex_cli",
        "requestedModel": "gpt-5-codex",
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["model"] == "gpt-5-codex"
    assert dumped["resolvedModel"] == "gpt-5-codex"
    assert dumped["requestedModel"] == "gpt-5-codex"

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
    assert payload.agent_run_id == "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d"
    dumped = payload.model_dump(by_alias=True)
    assert "taskRunId" not in dumped
    assert dumped["agentRunId"] == "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d"

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

def test_describe_execution_exposes_workflow_and_run_identity() -> None:
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record()

        response = test_client.get("/api/executions/mm:wf-1")

        assert response.status_code == 200
        payload = response.json()
        assert payload["workflowId"] == "mm:wf-1"
        assert "taskId" not in payload
        assert payload["runId"] == "run-2"
        assert "temporalRunId" not in payload
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
            "status": "running",
            "detailHref": (
                "/workflows/resolver%3Amm%3Awf-1%3Apr%3A1614%3Ahead%3Aabc123%3A1"
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
            detail_href=f"/workflows/{workflow_id}",
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
    assert "temporalRunId" not in payload
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

def test_describe_execution_bounds_slow_live_progress_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        progress={"total": 99},
        delay_seconds=0.2,
    )
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "live_query_timeout_seconds",
        0.01,
    )
    monkeypatch.setattr(
        settings.temporal,
        "temporal_authoritative_read_enabled",
        False,
    )

    with (
        patch(
            "api_service.api.routers.executions._hydrate_execution_report_projection",
            new_callable=AsyncMock,
            side_effect=lambda execution, **_kwargs: execution,
        ),
        patch(
            "api_service.api.routers.executions._resolve_task_run_ids_from_managed_store",
            return_value={},
        ),
        TestClient(app) as test_client,
    ):
        started = time.perf_counter()
        response = test_client.get("/api/executions/mm:wf-1")
        elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 0.15
    assert response.json()["progress"] is None

def test_describe_execution_skips_live_progress_query_for_terminal_runs() -> None:
    from api_service.db.models import TemporalExecutionCloseStatus

    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.close_status = TemporalExecutionCloseStatus.FAILED
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        progress={
            "total": 99,
            "failed": 99,
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    async def passthrough_report_projection(execution, **_kwargs):
        return execution

    with (
        patch(
            "api_service.api.routers.executions._load_execution_progress",
            new_callable=AsyncMock,
        ) as load_progress,
        patch(
            "api_service.api.routers.executions._enrich_execution_merge_automation",
            new_callable=AsyncMock,
            side_effect=lambda execution, **_kwargs: execution,
        ) as enrich_merge_automation,
        patch(
            "api_service.api.routers.executions._hydrate_execution_report_projection",
            side_effect=passthrough_report_projection,
        ),
        TestClient(app) as test_client,
    ):
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["temporalStatus"] == "failed"
    assert payload["closeStatus"] == "failed"
    assert payload["progress"] is None
    load_progress.assert_not_awaited()
    enrich_merge_automation.assert_awaited_once()

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
    assert "taskRunId" not in payload["steps"][0]["refs"]
    assert "taskRunId" not in payload["steps"][0]["workload"]
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
    assert "taskRunId" not in payload["steps"][0]["refs"]
    assert "taskRunId" not in payload["steps"][1]["refs"]
    assert "taskRunId" not in payload["steps"][2]["refs"]
    assert len(to_thread_calls) == 1
    assert to_thread_calls[0][1] == (
        (
            "mm:wf-1:agent:delegate-agent",
            "mm:wf-1:agent:second-agent",
        ),
    )


def _step_execution_manifest_payload(
    *,
    artifact_ref: str,
    attempt: int,
    status: str = "succeeded",
) -> dict[str, object]:
    return {
        "schemaVersion": "v1",
        "stepExecutionId": f"mm:wf-1:run-99:implement:execution:{attempt}",
        "workflowId": "mm:wf-1",
        "runId": "run-99",
        "logicalStepId": "implement",
        "executionOrdinal": attempt,
        "executionScope": "run",
        "lineage": {
            "sourceWorkflowId": "mm:source",
            "sourceRunId": "source-run",
            "sourceLogicalStepId": "implement",
            "sourceExecutionOrdinal": attempt,
            "relationship": "recover_from_failed_step",
            "lineageExecutionOrdinal": attempt + 1,
        },
        "reason": "recover_from_failed_step" if attempt > 1 else "initial_execution",
        "status": status,
        "terminalDisposition": "accepted" if status == "succeeded" else "retryable",
        "startedAt": "2026-05-19T10:00:00Z",
        "updatedAt": "2026-05-19T10:01:00Z",
        "input": {"preparedInputRef": f"art-input-{attempt}"},
        "context": {"contextBundleRef": f"art-context-{attempt}"},
        "workspace": {
            "workspacePolicy": "continue_from_previous_execution",
            "baselineRef": f"art-workspace-{attempt}",
            "gitDisposition": "candidate",
        },
        "execution": {
            "childWorkflowId": f"child-{attempt}",
            "childRunId": f"child-run-{attempt}",
            "taskRunId": f"task-run-{attempt}",
        },
        "outputs": {
            "summary": f"Attempt {attempt} summary",
            "outputSummaryRef": f"art-summary-{attempt}",
            "outputPrimaryRef": f"art-output-{attempt}",
        },
        "checks": [
            {
                "kind": "quality_gate",
                "status": "passed" if status == "succeeded" else "failed",
                "artifactRef": f"art-check-{attempt}",
            }
        ],
        "sideEffects": {
            "gitDisposition": "candidate",
            "publicationRef": f"art-publish-{attempt}",
        },
        "dependencyEffects": {"invalidatedStepRefs": [artifact_ref]},
        "budget": {"budgetRef": f"art-budget-{attempt}"},
    }


def test_get_execution_step_executions_returns_bounded_manifest_history() -> None:
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
                    "logicalStepId": "implement",
                    "order": 1,
                    "title": "Implement",
                    "tool": {"type": "skill", "name": "jira-implement", "version": "1"},
                    "dependsOn": [],
                    "status": "succeeded",
                    "waitingReason": None,
                    "attentionRequired": False,
                    "attempt": 2,
                    "startedAt": "2026-05-19T10:00:00Z",
                    "updatedAt": "2026-05-19T10:01:00Z",
                    "summary": "Done",
                    "checks": [],
                    "refs": {
                        "childWorkflowId": None,
                        "childRunId": None,
                        "taskRunId": None,
                        "latestStepExecutionManifestRef": "art-attempt-2",
                        "stepExecutionManifestRefs": ["art-attempt-1", "art-attempt-2"],
                    },
                    "artifacts": {},
                    "lastError": None,
                }
            ],
        },
    )
    user = _override_user_dependencies(app, is_superuser=True)

    async def _read_artifact(**kwargs):
        artifact_id = kwargs["artifact_id"]
        payload = _step_execution_manifest_payload(
            artifact_ref=artifact_id,
            attempt=1 if artifact_id == "art-attempt-1" else 2,
        )
        return SimpleNamespace(artifact_id=artifact_id), json.dumps(payload).encode()

    artifact_service = SimpleNamespace(read=AsyncMock(side_effect=_read_artifact))
    app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get(
                "/api/executions/mm:wf-1/steps/implement/attempts"
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflowId"] == "mm:wf-1"
    assert payload["runId"] == "run-99"
    assert payload["logicalStepId"] == "implement"
    assert [item["executionOrdinal"] for item in payload["attempts"]] == [1, 2]
    assert payload["attempts"][1]["manifestRefs"] == {
        "manifestArtifactRef": "art-attempt-2"
    }
    assert payload["attempts"][1]["runtimeChildRefs"] == {
        "childWorkflowId": "child-2",
        "childRunId": "child-run-2",
        "taskRunId": "task-run-2",
    }
    assert payload["attempts"][1]["workspacePolicy"] == (
        "continue_from_previous_execution"
    )
    assert payload["attempts"][1]["gitDisposition"] == "candidate"
    assert payload["attempts"][1]["qualityGateVerdict"] == "passed"
    assert "summary" not in payload["attempts"][1]["outputRefs"]
    assert artifact_service.read.await_args_list[0] == call(
        artifact_id="art-attempt-1",
        principal=str(user.id),
        allow_restricted_raw=True,
    )


def test_get_execution_step_execution_returns_bounded_detail_refs() -> None:
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
                    "logicalStepId": "implement",
                    "order": 1,
                    "title": "Implement",
                    "tool": {"type": "skill", "name": "jira-implement", "version": "1"},
                    "dependsOn": [],
                    "status": "succeeded",
                    "waitingReason": None,
                    "attentionRequired": False,
                    "attempt": 2,
                    "startedAt": "2026-05-19T10:00:00Z",
                    "updatedAt": "2026-05-19T10:01:00Z",
                    "summary": "Done",
                    "checks": [],
                    "refs": {
                        "childWorkflowId": None,
                        "childRunId": None,
                        "taskRunId": None,
                        "latestStepExecutionManifestRef": "art-attempt-2",
                        "stepExecutionManifestRefs": ["art-attempt-1", "art-attempt-2"],
                    },
                    "artifacts": {},
                    "lastError": None,
                }
            ],
        },
    )
    _override_user_dependencies(app, is_superuser=True)
    payload = _step_execution_manifest_payload(
        artifact_ref="art-attempt-2",
        attempt=2,
    )
    artifact_service = SimpleNamespace(
        read=AsyncMock(
            side_effect=[
                (
                    SimpleNamespace(artifact_id="art-attempt-1"),
                    json.dumps(
                        _step_execution_manifest_payload(
                            artifact_ref="art-attempt-1",
                            attempt=1,
                        )
                    ).encode(),
                ),
                (
                    SimpleNamespace(artifact_id="art-attempt-2"),
                    json.dumps(payload).encode(),
                ),
            ]
        )
    )
    app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get(
                "/api/executions/mm:wf-1/steps/implement/attempts/2"
            )

    assert response.status_code == 200
    body = response.json()
    assert body["executionOrdinal"] == 2
    assert body["sourceExecutionOrdinal"] == 2
    assert body["lineage"]["relationship"] == "recover_from_failed_step"
    assert body["inputRefs"] == {"preparedInputRef": "art-input-2"}
    assert body["contextRefs"] == {"contextBundleRef": "art-context-2"}
    assert body["workspaceRefs"] == {
        "baselineRef": "art-workspace-2",
    }
    assert body["executionRefs"] == {
        "childWorkflowId": "child-2",
        "childRunId": "child-run-2",
        "taskRunId": "task-run-2",
    }
    assert body["checkRefs"] == [{"artifactRef": "art-check-2"}]
    assert body["sideEffectRefs"] == {"publicationRef": "art-publish-2"}
    assert body["dependencyEffectRefs"] == {
        "invalidatedStepRefs": ["art-attempt-2"]
    }
    assert "outputs" not in body


def test_get_execution_step_executions_preserves_artifact_authorization() -> None:
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
                    "logicalStepId": "implement",
                    "order": 1,
                    "title": "Implement",
                    "tool": {},
                    "dependsOn": [],
                    "status": "failed",
                    "waitingReason": None,
                    "attentionRequired": False,
                    "attempt": 1,
                    "startedAt": "2026-05-19T10:00:00Z",
                    "updatedAt": "2026-05-19T10:01:00Z",
                    "summary": None,
                    "checks": [],
                    "refs": {
                        "childWorkflowId": None,
                        "childRunId": None,
                        "taskRunId": None,
                        "latestStepExecutionManifestRef": "art-attempt-1",
                        "stepExecutionManifestRefs": ["art-attempt-1"],
                    },
                    "artifacts": {},
                    "lastError": None,
                }
            ],
        },
    )
    _override_user_dependencies(app, is_superuser=True)
    artifact_service = SimpleNamespace(
        read=AsyncMock(side_effect=TemporalArtifactAuthorizationError())
    )
    app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get(
                "/api/executions/mm:wf-1/steps/implement/attempts"
            )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "step_execution_manifest_unauthorized"

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

def test_get_execution_steps_returns_503_for_slow_temporal_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={"workflowId": "mm:wf-1", "runId": "run-99", "steps": []},
        delay_seconds=0.2,
    )
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "live_query_timeout_seconds",
        0.01,
    )

    with TestClient(app) as test_client:
        started = time.perf_counter()
        response = test_client.get("/api/executions/mm:wf-1/steps")
        elapsed = time.perf_counter() - started

    assert response.status_code == 503
    assert elapsed < 0.15
    assert response.json()["detail"]["code"] == "temporal_unavailable"

def test_get_execution_steps_falls_back_to_stored_task_steps_when_temporal_query_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    record.memo = {
        **record.memo,
        "summary": "Executing plan step 2/2: moonspec-implement",
    }
    record.parameters = {
        "task": {
            "steps": [
                {
                    "id": "fetch-issue",
                    "title": "Fetch issue",
                    "type": "tool",
                    "tool": {"id": "jira.get_issue", "version": "1.0.0"},
                },
                {
                    "id": "implement",
                    "title": "Implement issue",
                    "type": "skill",
                    "skill": {"id": "moonspec-implement"},
                    "dependsOn": ["fetch-issue"],
                },
            ],
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={"workflowId": "mm:wf-1", "runId": "run-99", "steps": []},
        delay_seconds=0.2,
    )
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "live_query_timeout_seconds",
        0.01,
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflowId"] == "mm:wf-1"
    assert payload["runId"] == "run-2"
    assert [step["logicalStepId"] for step in payload["steps"]] == [
        "fetch-issue",
        "implement",
    ]
    assert payload["steps"][0]["tool"] == {
        "type": "tool",
        "name": "jira.get_issue",
        "version": "1.0.0",
    }
    assert payload["steps"][1]["tool"]["name"] == "moonspec-implement"
    assert payload["steps"][1]["dependsOn"] == ["fetch-issue"]
    assert payload["steps"][1]["status"] == "running"
    assert payload["steps"][1]["attempt"] == 1

def test_get_execution_steps_fallback_prefers_structured_step_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    record.memo = {
        **record.memo,
        "summary": "Executing plan step 1/2: fetch-issue",
        "mm_current_step_order": 2,
    }
    record.parameters = {
        "task": {
            "steps": [
                {
                    "id": "fetch-issue",
                    "title": "Fetch issue",
                    "type": "tool",
                    "tool": {"id": "jira.get_issue", "version": "1.0.0"},
                },
                {
                    "id": "implement",
                    "title": "Implement issue",
                    "type": "skill",
                    "skill": {"id": "moonspec-implement"},
                    "dependsOn": ["fetch-issue"],
                },
            ],
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={"workflowId": "mm:wf-1", "runId": "run-99", "steps": []},
        delay_seconds=0.2,
    )
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "live_query_timeout_seconds",
        0.01,
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 200
    payload = response.json()
    # The structured memo field wins over the stale summary string.
    assert payload["steps"][1]["status"] == "running"
    assert payload["steps"][0]["status"] == "ready"

def test_get_execution_steps_fallback_preserves_independent_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    record.memo = {
        **record.memo,
        "summary": "Executing plan step 1/3: alpha",
    }
    record.parameters = {
        "task": {
            "steps": [
                {
                    "id": "alpha",
                    "title": "Alpha",
                    "type": "tool",
                    "tool": {"id": "tool.alpha"},
                },
                {
                    "id": "beta",
                    "title": "Beta",
                    "type": "tool",
                    "tool": {"id": "tool.beta"},
                },
                {
                    "id": "gamma",
                    "title": "Gamma",
                    "type": "tool",
                    "tool": {"id": "tool.gamma"},
                },
            ],
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={"workflowId": "mm:wf-1", "runId": "run-99", "steps": []},
        delay_seconds=0.2,
    )
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "live_query_timeout_seconds",
        0.01,
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 200
    payload = response.json()
    # No step declared ``dependsOn`` so the fallback must not fabricate a chain
    # — independent steps should remain runnable in parallel.
    assert payload["steps"][0]["dependsOn"] == []
    assert payload["steps"][1]["dependsOn"] == []
    assert payload["steps"][2]["dependsOn"] == []
    assert payload["steps"][0]["status"] == "running"
    assert payload["steps"][1]["status"] == "ready"
    assert payload["steps"][2]["status"] == "ready"

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
    assert "taskRunId" not in response.json()
    assert response.json()["agentRunId"] == "550e8400-e29b-41d4-a716-446655440000"
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
            "/workflows/mm:rerun-created?source=temporal"
        )
        assert service.describe_execution.await_args_list[-1].args == (
            "mm:rerun-created",
        )


@pytest.mark.asyncio
async def test_request_rerun_update_flushes_snapshot_reuse_before_serializing_response(
    tmp_path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path}/rerun_update_response.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            service = TemporalExecutionService(session)
            service._client_adapter.start_workflow = AsyncMock(
                return_value=SimpleNamespace(run_id="run-source")
            )
            service._client_adapter.cancel_workflow = AsyncMock()
            service._client_adapter.update_workflow = AsyncMock()

            user = SimpleNamespace(
                id=uuid4(),
                email="rerun@example.com",
                is_active=True,
                is_superuser=False,
            )
            created = await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=user.id,
                title="Rerun source",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={"task": {"instructions": "Do the work."}},
                idempotency_key=None,
            )
            source_workflow_id = created.workflow_id
            await service.cancel_execution(
                workflow_id=source_workflow_id,
                reason="terminal source",
                graceful=True,
            )

            session.add(
                TemporalArtifact(
                    artifact_id="art_snapshot_route_flush",
                    storage_key="tests/art_snapshot_route_flush.json",
                    storage_backend=TemporalArtifactStorageBackend.S3,
                    encryption=TemporalArtifactEncryption.NONE,
                    status=TemporalArtifactStatus.COMPLETE,
                    retention_class=TemporalArtifactRetentionClass.LONG,
                    redaction_level=TemporalArtifactRedactionLevel.NONE,
                    upload_mode=TemporalArtifactUploadMode.SINGLE_PUT,
                    metadata_json={},
                )
            )
            source_records = []
            for record_type in (
                TemporalExecutionCanonicalRecord,
                TemporalExecutionRecord,
            ):
                source_record = await session.get(record_type, source_workflow_id)
                assert source_record is not None
                source_record.memo = {
                    **dict(source_record.memo or {}),
                    "task_input_snapshot_ref": "art_snapshot_route_flush",
                    "task_input_snapshot_version": 1,
                    "task_input_snapshot_source_kind": "create",
                }
                source_record.artifact_refs = [
                    *list(source_record.artifact_refs or []),
                    "art_snapshot_route_flush",
                ]
                source_records.append(source_record)
            await session.commit()
            for source_record in source_records:
                await session.refresh(source_record)

            response = await update_execution_route(
                workflow_id=source_workflow_id,
                payload=UpdateExecutionRequest(updateName="RequestRerun"),
                response=Response(),
                service=service,
                session=session,
                user=user,
                _actions_enabled=None,
            )

        assert response.accepted is True
        assert response.execution.workflow_id != source_workflow_id
        assert response.execution.redirect_path == (
            f"/workflows/{response.execution.workflow_id}?source=temporal"
        )
    finally:
        await engine.dispose()


def test_request_rerun_update_snapshot_hydrates_instructions_from_input_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    source_record = _build_execution_record(has_task_input_snapshot=False)
    rerun_record = _build_execution_record(has_task_input_snapshot=False)
    rerun_record.workflow_id = "mm:rerun-created"
    rerun_record.run_id = "run-rerun"
    rerun_record.input_ref = "art-full-input"
    rerun_record.parameters = {
        "repository": "Moon/Mind",
        "targetRuntime": "codex_cli",
        "task": {
            "title": "Hydrated rerun",
            "steps": [
                {"id": "step-1", "title": "First"},
                {"id": "step-2", "title": "Second"},
            ],
        },
    }
    service.describe_execution.side_effect = [source_record, rerun_record]
    service.update_execution.return_value = {
        "accepted": True,
        "applied": "continue_as_new",
        "message": "Rerun requested. New execution created.",
        "continue_as_new_cause": "manual_rerun",
        "workflow_id": "mm:rerun-created",
    }
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    user = _override_user_dependencies(app, is_superuser=True)
    session = AsyncMock()
    app.dependency_overrides[get_async_session] = lambda: session
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_task_editing_enabled", True
    )
    artifact_payload = {
        "repository": "Moon/Mind",
        "targetRuntime": "codex_cli",
        "task": {
            "title": "Hydrated rerun",
            "instructions": "Top-level rerun instructions.",
            "steps": [
                {
                    "id": "step-1",
                    "title": "First",
                    "instructions": "First step instructions.",
                },
                {
                    "id": "step-2",
                    "title": "Second",
                    "instructions": "Second step instructions.",
                },
            ],
        },
    }
    artifact_service = SimpleNamespace(
        read=AsyncMock(
            return_value=(
                SimpleNamespace(artifact_id="art-full-input"),
                json.dumps(artifact_payload).encode("utf-8"),
            )
        )
    )
    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda _session: artifact_service,
    )
    captured_task_payload: dict[str, object] = {}

    async def _persist_snapshot(**kwargs) -> str:
        captured_task_payload.update(kwargs["task_payload"])
        target_record = kwargs["record"]
        target_record.memo = {
            **dict(target_record.memo or {}),
            "task_input_snapshot_ref": "art_snapshot_hydrated",
            "task_input_snapshot_version": 1,
            "task_input_snapshot_source_kind": "rerun",
        }
        return "art_snapshot_hydrated"

    persist_mock = AsyncMock(side_effect=_persist_snapshot)
    monkeypatch.setattr(
        "api_service.api.routers.executions._persist_original_task_input_snapshot",
        persist_mock,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/update",
            json={
                "updateName": "RequestRerun",
                "idempotencyKey": "rerun-1",
                "inputArtifactRef": "artifact://input/art-full-input",
                "parametersPatch": rerun_record.parameters,
            },
        )

    assert response.status_code == 200
    artifact_service.read.assert_awaited_once_with(
        artifact_id="art-full-input",
        principal=str(user.id),
        allow_restricted_raw=True,
    )
    persist_mock.assert_awaited_once()
    assert captured_task_payload["instructions"] == "Top-level rerun instructions."
    steps = captured_task_payload["steps"]
    assert steps[0]["instructions"] == "First step instructions."
    assert steps[1]["instructions"] == "Second step instructions."
    session.commit.assert_awaited_once()


def test_task_input_snapshot_artifact_id_strips_input_prefix_without_scheme() -> None:
    assert _artifact_id_from_ref("input/art-full-input") == "art-full-input"
    assert _artifact_id_from_ref("artifact://input/art-full-input") == "art-full-input"


def test_task_input_snapshot_merge_preserves_step_deletions() -> None:
    merged = _merge_task_preserving_artifact_instructions(
        {
            "steps": [
                {"id": "step-1", "title": "First", "instructions": "Original first"},
                {"id": "step-2", "title": "Second", "instructions": "Original second"},
            ]
        },
        {"steps": [{"id": "step-1", "title": "First edited"}]},
    )

    assert merged["steps"] == [
        {"id": "step-1", "title": "First edited", "instructions": "Original first"}
    ]


def test_original_task_input_snapshot_payload_preserves_mm639_authored_fields() -> None:
    task_payload = _mm639_authored_task_payload()

    payload = _build_original_task_input_snapshot_payload(
        source_kind="create",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex_cli",
            "requiredCapabilities": ["git", "jira"],
        },
        task_payload=task_payload,
        attachment_refs=[
            {
                "artifactId": "art-objective",
                "targetKind": "objective",
            },
            {
                "artifactId": "art-step",
                "targetKind": "step",
                "stepId": "step-2",
                "stepOrdinal": 1,
            },
        ],
    )

    authored = payload["draft"]["authoredTaskInput"]
    assert authored["traceability"]["jiraIssueKey"] == "MM-639"
    assert authored["objective"]["instructions"] == (
        "Preserve the original authored task input for MM-639."
    )
    assert authored["objective"]["inputAttachments"][0]["artifactId"] == (
        "art-objective"
    )
    assert authored["runtime"] == task_payload["runtime"]
    assert authored["publish"] == task_payload["publish"]
    assert authored["repository"] == "MoonLadderStudios/MoonMind"
    assert authored["branch"] == "feature/mm-639"
    assert authored["dependencyDeclarations"] == ["MM-638"]
    assert authored["presetApplicationMetadata"] == task_payload[
        "appliedStepTemplates"
    ]
    assert authored["pinnedPresetBindings"] == task_payload["authoredPresets"]
    assert authored["includeTreeSummary"] == [
        {
            "presetSlug": "jira-orchestrate",
            "presetVersion": "1.0.0",
            "includedSlug": "jira-fetch",
            "includedVersion": "1.0.0",
        }
    ]
    assert authored["finalSubmittedOrder"] == [
        {"stepId": "step-1", "ordinal": 0},
        {"stepId": "step-2", "ordinal": 1},
    ]
    assert authored["perStepProvenance"][1] == {
        "stepId": "step-2",
        "ordinal": 1,
        "presetProvenance": task_payload["steps"][1]["presetProvenance"],
    }
    assert authored["detachmentState"] == [
        {"stepId": "step-2", "ordinal": 1, "detached": True}
    ]
    assert authored["steps"][1]["inputAttachments"][0]["artifactId"] == "art-step"
    assert payload["attachmentRefs"][1]["stepId"] == "step-2"


def test_missing_attachment_aware_snapshot_descriptor_is_degraded_explicitly() -> None:
    record = _build_execution_record(
        has_task_input_snapshot=False,
    )
    record.parameters = {
        "task": {
            "instructions": "Attachment-aware task without a snapshot.",
            "inputAttachments": [
                {
                    "artifactId": "art-objective",
                    "filename": "objective.png",
                    "contentType": "image/png",
                }
            ],
        }
    }

    descriptor = _task_input_snapshot_descriptor_from_record(record)

    assert descriptor.available is False
    assert descriptor.reconstruction_mode == "degraded_read_only"
    assert descriptor.disabled_reasons["draft"] == (
        "original_task_input_snapshot_missing"
    )
    assert descriptor.disabled_reasons["attachments"] == (
        "original_task_input_snapshot_missing"
    )


def test_missing_legacy_attachment_ref_snapshot_descriptor_is_degraded() -> None:
    record = _build_execution_record(
        has_task_input_snapshot=False,
    )
    record.parameters = {
        "task": {
            "instructions": "Legacy attachment-aware task without a snapshot.",
            "attachmentRefs": [
                {
                    "artifactRef": "artifact://input/objective-image",
                    "filename": "objective.png",
                    "contentType": "image/png",
                }
            ],
            "steps": [
                {
                    "id": "inspect",
                    "attachmentRefs": [
                        {
                            "artifactRef": "artifact://input/step-image",
                            "filename": "step.png",
                            "contentType": "image/png",
                        }
                    ],
                }
            ],
        }
    }

    descriptor = _task_input_snapshot_descriptor_from_record(record)

    assert descriptor.available is False
    assert descriptor.reconstruction_mode == "degraded_read_only"
    assert descriptor.disabled_reasons["draft"] == (
        "original_task_input_snapshot_missing"
    )
    assert descriptor.disabled_reasons["attachments"] == (
        "original_task_input_snapshot_missing"
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
        assert "taskId" not in item
        assert item["runId"] == "run-2"
        assert "temporalRunId" not in item
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
    assert body["redirectPath"] == "/workflows/mm:wf-1?source=temporal"

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
    assert body["actions"]["canEditForRerun"] is False
    assert body["actions"]["canRerun"] is False

def test_describe_execution_exposes_edit_for_rerun_for_failed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        state=MoonMindWorkflowState.FAILED
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_task_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["canUpdateInputs"] is False
    assert body["actions"]["canEditForRerun"] is True
    assert body["actions"]["canRerun"] is True

def test_describe_execution_exposes_failed_step_recovery_distinct_from_lifecycle_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.memo = {
        **record.memo,
        "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
        "resume_failed_step_id": "implement",
        "resume_completed_step_refs": ["artifact://completed/plan"],
        "recovery_workspace_checkpoint_ref": "artifact://workspace/before-implement",
        "resume_plan_digest": "sha256:resume-plan",
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
    assert body["actions"]["canResume"] is False
    assert body["actions"]["canRecoverFromFailedStep"] is True
    assert body["resume"]["available"] is True
    assert (
        body["resume"]["checkpointRef"]
        == "artifact://resume-checkpoints/source/checkpoint-v1"
    )
    assert body["resume"]["failedStepId"] == "implement"
    assert body["resume"]["sourceRunId"] == "run-2"


def test_describe_execution_exposes_target_attachment_and_recovery_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.parameters = {
        "task": {
            "instructions": "Review the screenshot.",
            "attachmentRefs": [
                {
                    "artifactRef": "artifact://input/objective-image",
                    "filename": "objective.png",
                    "contentType": "image/png",
                    "sizeBytes": 12345,
                }
            ],
            "steps": [
                {
                    "id": "inspect",
                    "title": "Inspect screenshot",
                    "attachmentRefs": [
                        {
                            "artifactRef": "artifact://input/step-image",
                            "filename": "step.png",
                            "contentType": "image/png",
                        }
                    ],
                }
            ],
        },
        "targetDiagnostics": {
            "targets": [
                {
                    "targetKind": "objective",
                    "refs": [
                        {
                            "refKind": "attachment_manifest",
                            "artifactRef": "artifact://diagnostics/input-manifest",
                        }
                    ],
                },
                {
                    "targetKind": "step",
                    "stepId": "inspect",
                    "failures": [
                        {
                            "phase": "materialization",
                            "message": "Attachment download failed before step execution.",
                            "evidenceRef": "artifact://diagnostics/prepare",
                        },
                        {
                            "phase": "unknown-provider-phase",
                            "message": "Provider returned an unrecognized phase.",
                        }
                    ],
                },
            ],
            "degradedReason": "step_attachment_missing",
        },
        "recoverySource": {
            "sourceWorkflowId": "mm:source",
            "sourceRunId": "run-source",
            "preservedSteps": [
                {
                    "logicalStepId": "prepare",
                    "title": "Prepare context",
                    "sourceExecutionOrdinal": 1,
                    "sourceWorkflowId": "mm:source",
                    "sourceRunId": "run-source",
                }
            ],
        },
    }
    record.memo = {
        **record.memo,
        "recovery_checkpoint_ref": "artifact://resume/checkpoint",
        "resume_failed_step_id": "inspect",
        "recovery_workspace_checkpoint_ref": "artifact://workspace/checkpoint",
        "resume_plan_digest": "sha256:plan",
        "resume_completed_step_refs": ["artifact://completed/prepare"],
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
    diagnostics = response.json()["targetDiagnostics"]
    assert diagnostics["degradedReason"] == "step_attachment_missing"
    objective = diagnostics["targets"][0]
    assert objective["targetKind"] == "objective"
    assert objective["label"] == "Task objective"
    assert objective["attachments"][0]["artifactRef"] == "artifact://input/objective-image"
    assert objective["refs"][0]["artifactRef"] == "artifact://diagnostics/input-manifest"
    step = diagnostics["targets"][1]
    assert step["targetKind"] == "step"
    assert step["stepId"] == "inspect"
    assert step["label"] == "Inspect screenshot"
    assert step["attachments"][0]["filename"] == "step.png"
    assert step["failures"][0]["phase"] == "materialization"
    assert step["failures"][1]["phase"] == "degraded"
    assert diagnostics["recovery"]["resumed"] is True
    assert diagnostics["recovery"]["sourceWorkflowId"] == "mm:source"
    assert diagnostics["recovery"]["sourceRunId"] == "run-source"
    assert diagnostics["recovery"]["checkpointRef"] == "artifact://resume/checkpoint"
    assert diagnostics["recovery"]["preservedSteps"][0]["logicalStepId"] == "prepare"

def test_describe_execution_distinguishes_empty_step_attachment_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.parameters = {
        "task": {
            "instructions": "Review the screenshot.",
            "inputAttachments": [
                {
                    "artifactId": "art-objective",
                    "filename": "objective.png",
                    "contentType": "image/png",
                }
            ],
            "steps": [
                {
                    "id": "inspect",
                    "title": "Inspect screenshot",
                    "instructions": "Inspect without a step attachment.",
                }
            ],
        }
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
    targets = response.json()["targetDiagnostics"]["targets"]
    objective = next(target for target in targets if target["targetKind"] == "objective")
    step = next(target for target in targets if target["targetKind"] == "step")
    assert objective["attachments"][0]["artifactRef"] == "art-objective"
    assert step["stepId"] == "inspect"
    assert step["attachments"] == []


def test_describe_execution_preserves_generated_context_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.parameters = {
        "task": {"instructions": "Review prepared context."},
        "targetDiagnostics": {
            "targets": [
                {
                    "targetKind": "objective",
                    "refs": [
                        {
                            "refKind": "generated_context",
                            "artifactRef": "artifact://context/objective",
                        }
                    ],
                }
            ]
        },
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
    refs = response.json()["targetDiagnostics"]["targets"][0]["refs"]
    assert refs == [
        {
            "refKind": "generated_context",
            "artifactRef": "artifact://context/objective",
            "path": None,
        }
    ]


def test_describe_execution_preserves_target_semantics_for_alias_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.parameters = {
        "task": {
            "instructions": "Review aliased attachments.",
            "input_attachments": [
                {
                    "artifact_ref": "artifact://input/objective",
                    "filename": "objective.png",
                    "content_type": "image/png",
                }
            ],
            "steps": [
                {
                    "step_id": "inspect",
                    "title": "Inspect screenshot",
                    "input_attachments": [
                        {
                            "artifact_ref": "artifact://input/step",
                            "filename": "step.png",
                            "content_type": "image/png",
                        }
                    ],
                }
            ],
        },
        "target_diagnostics": {
            "targets": [
                {
                    "target_kind": "objective",
                    "refs": [
                        {
                            "ref_kind": "attachment_manifest",
                            "artifact_ref": "artifact://manifest/objective",
                        }
                    ],
                },
                {
                    "target_kind": "step",
                    "step_id": "inspect",
                    "refs": [
                        {
                            "ref_kind": "generated_context",
                            "artifact_ref": "artifact://context/step",
                        }
                    ],
                },
            ]
        },
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
    targets = response.json()["targetDiagnostics"]["targets"]
    objective = next(target for target in targets if target["targetKind"] == "objective")
    step = next(target for target in targets if target["targetKind"] == "step")
    assert objective["attachments"][0]["artifactRef"] == "artifact://input/objective"
    assert objective["refs"][0]["artifactRef"] == "artifact://manifest/objective"
    assert step["stepId"] == "inspect"
    assert step["attachments"][0]["artifactRef"] == "artifact://input/step"
    assert step["refs"][0]["artifactRef"] == "artifact://context/step"


def test_describe_execution_surfaces_failed_step_execution_recovery_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.parameters = {
        "task": {"instructions": "Resume failed while executing step."},
        "targetDiagnostics": {
            "recovery": {
                "resumed": True,
                "sourceWorkflowId": "mm:source",
                "sourceRunId": "run-source",
                "failedRecoveryPhase": "failed_step_execution",
            }
        },
    }
    record.memo = {
        **record.memo,
        "recovery_checkpoint_ref": "artifact://resume/checkpoint",
        "resume_failed_step_id": "inspect",
        "recovery_workspace_checkpoint_ref": "artifact://workspace/checkpoint",
        "resume_plan_digest": "sha256:plan",
        "resume_completed_step_refs": ["artifact://completed/prepare"],
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
    recovery = response.json()["targetDiagnostics"]["recovery"]
    assert recovery["sourceWorkflowId"] == "mm:source"
    assert recovery["sourceRunId"] == "run-source"
    assert recovery["failedRecoveryPhase"] == "failed_step_execution"


def test_describe_execution_prefers_diagnostics_failed_phase_over_disabled_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.parameters = {
        "task": {"instructions": "Resume failed while executing step."},
        "targetDiagnostics": {
            "recovery": {
                "resumed": True,
                "sourceWorkflowId": "mm:source",
                "sourceRunId": "run-source",
                "failedRecoveryPhase": "failed_step_execution",
            }
        },
    }
    record.memo = {
        **record.memo,
        "resume_failed_step_id": "inspect",
        "resume_completed_step_refs": ["artifact://completed/prepare"],
        "recovery_workspace_checkpoint_ref": "artifact://workspace/checkpoint",
        "resume_plan_digest": "sha256:plan",
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_task_editing_enabled", True
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["resume"]["disabledReason"] == "recovery_checkpoint_missing"
    assert (
        body["targetDiagnostics"]["recovery"]["failedRecoveryPhase"]
        == "failed_step_execution"
    )


def test_describe_execution_omits_recovery_for_routine_recovery_action_gating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "task": {
            "instructions": "Review the screenshot.",
            "attachmentRefs": [
                {
                    "artifactRef": "artifact://input/objective-image",
                    "filename": "objective.png",
                    "contentType": "image/png",
                }
            ],
        }
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
    assert body["resume"]["disabledReason"] == "state_not_eligible"
    assert body["targetDiagnostics"]["targets"][0]["targetKind"] == "objective"
    assert body["targetDiagnostics"]["recovery"] is None


@pytest.mark.parametrize(
    ("memo_updates", "expected_reason"),
    [
        (
            {
                "resume_failed_step_id": "implement",
                "resume_completed_step_refs": ["artifact://completed/plan"],
                "recovery_workspace_checkpoint_ref": "artifact://workspace/before-implement",
                "resume_plan_digest": "sha256:resume-plan",
            },
            "recovery_checkpoint_missing",
        ),
        (
            {
                "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
                "resume_completed_step_refs": ["artifact://completed/plan"],
                "recovery_workspace_checkpoint_ref": "artifact://workspace/before-implement",
                "resume_plan_digest": "sha256:resume-plan",
            },
            "failed_step_identity_missing",
        ),
        (
            {
                "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
                "resume_failed_step_id": "implement",
                "recovery_workspace_checkpoint_ref": "artifact://workspace/before-implement",
                "resume_plan_digest": "sha256:resume-plan",
            },
            "completed_step_refs_missing",
        ),
        (
            {
                "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
                "resume_failed_step_id": "implement",
                "resume_completed_step_refs": ["artifact://completed/plan"],
                "resume_plan_digest": "sha256:resume-plan",
            },
            "workspace_checkpoint_missing",
        ),
        (
            {
                "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
                "resume_failed_step_id": "implement",
                "resume_completed_step_refs": ["artifact://completed/plan"],
                "recovery_workspace_checkpoint_ref": "artifact://workspace/before-implement",
            },
            "plan_identity_missing",
        ),
    ],
)
def test_describe_execution_requires_complete_recovery_evidence(
    monkeypatch: pytest.MonkeyPatch,
    memo_updates: dict[str, object],
    expected_reason: str,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.memo = {**record.memo, **memo_updates}
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
    assert body["actions"]["canRecoverFromFailedStep"] is False
    assert body["resume"]["available"] is False
    assert body["resume"]["disabledReason"] == expected_reason


def test_describe_execution_rejects_stale_recovery_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.memo = {
        **record.memo,
        "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
        "resume_failed_step_id": "implement",
        "resume_completed_step_refs": ["artifact://completed/plan"],
        "recovery_workspace_checkpoint_ref": "artifact://workspace/before-implement",
        "resume_plan_digest": "sha256:resume-plan",
        "resume_evidence_stale": True,
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
    assert body["actions"]["canRecoverFromFailedStep"] is False
    assert body["actions"]["disabledReasons"]["canRecoverFromFailedStep"] == "stale_recovery_evidence"
    assert body["resume"]["available"] is False
    assert body["resume"]["disabledReason"] == "stale_recovery_evidence"


def test_failed_step_recovery_submission_rejects_stale_recovery_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    canonical = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    canonical.memo = {
        **canonical.memo,
        "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
        "resume_evidence_stale": True,
    }
    mock_service.describe_execution.return_value = canonical

    class Session:
        async def get(self, model, key):
            return canonical

        async def commit(self):
            return None

    artifact_service = SimpleNamespace(read=AsyncMock())
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: Session()
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda _session: artifact_service,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/recover-from-failed-step",
            json={"idempotencyKey": "resume-1"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "stale_recovery_evidence"
    artifact_service.read.assert_not_awaited()
    mock_service.create_failed_step_recovery_execution.assert_not_awaited()


@pytest.mark.parametrize(
    ("payload_fields", "expected_fields"),
    [
        (
            {
                "task": {"instructions": "change the task"},
                "runtime": {"model": "gpt-5.4"},
            },
            ["runtime", "task"],
        ),
        (
            {
                "instructions": "changed",
                "steps": [{"id": "new-step"}],
                "attachments": ["artifact://new"],
                "inputAttachments": [{"artifactRef": "artifact://new"}],
            },
            ["attachments", "inputAttachments", "instructions", "steps"],
        ),
        (
            {
                "publishMode": "draft-pr",
                "branch": "feature/new",
                "startingBranch": "main",
                "targetBranch": "main",
                "presets": ["runtime"],
                "dependencies": ["mm:upstream"],
            },
            [
                "branch",
                "dependencies",
                "presets",
                "publishMode",
                "startingBranch",
                "targetBranch",
            ],
        ),
        (
            {
                "model": "gpt-5.4",
                "requestedModel": "gpt-5.4",
                "effort": "high",
                "parametersPatch": {"task": {"instructions": "changed"}},
                "inputArtifactRef": "artifact://input/new",
                "planArtifactRef": "artifact://plan/new",
                "manifestArtifactRef": "artifact://manifest/new",
            },
            [
                "effort",
                "inputArtifactRef",
                "manifestArtifactRef",
                "model",
                "parametersPatch",
                "planArtifactRef",
                "requestedModel",
            ],
        ),
    ],
)
def test_failed_step_recovery_request_rejects_edited_task_payload_fields(
    payload_fields: dict[str, object],
    expected_fields: list[str],
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        state=MoonMindWorkflowState.FAILED
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = _empty_session_override
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/recover-from-failed-step",
            json={
                "idempotencyKey": "resume-1",
                **payload_fields,
            },
        )

    assert response.status_code == 400
    body = response.json()["detail"]
    assert body["code"] == "recovery_payload_not_allowed"
    assert body["fields"] == expected_fields


def test_failed_step_recovery_hydrates_checkpoint_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    canonical = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    canonical.memo = {
        **canonical.memo,
        "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
        "task_input_snapshot_ref": "artifact://snapshot/source",
    }
    mock_service.describe_execution.return_value = canonical
    mock_service.create_failed_step_recovery_execution.return_value = {
        "accepted": True,
        "applied": "created_resumed_execution",
        "source": {"workflowId": canonical.workflow_id, "runId": canonical.run_id},
        "execution": {
            "workflowId": "mm:resumed",
            "runId": "run-resumed",
            "detailHref": "/workflows/mm:resumed",
        },
        "relationship": "Recovered from failed step",
        "recoveryCheckpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
    }

    checkpoint_payload = {
        "schemaVersion": "v1",
        "source": {"workflowId": canonical.workflow_id, "runId": canonical.run_id},
        "taskInputSnapshotRef": "artifact://snapshot/source",
        "planRef": "artifact://plan/source",
        "planDigest": "sha256:resume-plan",
        "failedStep": {
            "logicalStepId": "implement",
            "order": 2,
            "attempt": 1,
        },
        "preservedSteps": [
            {
                "logicalStepId": "plan",
                "order": 1,
                "status": "succeeded",
                "sourceExecutionOrdinal": 1,
                "artifacts": {"summary": "artifact://completed/plan"},
                "stateCheckpointRef": "artifact://workspace/before-implement",
            }
        ],
        "recoveryWorkspace": {
            "branch": "feature/resume",
            "commit": "abc123",
            "checkpointRef": "artifact://workspace/before-implement",
        },
    }
    artifact_service = SimpleNamespace(
        read=AsyncMock(
            return_value=(SimpleNamespace(), json.dumps(checkpoint_payload).encode())
        )
    )

    class Session:
        async def get(self, model, key):
            return canonical

        async def commit(self):
            return None

    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: Session()
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda _session: artifact_service,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/recover-from-failed-step",
            json={"idempotencyKey": "resume-1"},
        )

    assert response.status_code == 201
    artifact_service.read.assert_awaited_once()
    call_kwargs = mock_service.create_failed_step_recovery_execution.await_args.kwargs
    assert call_kwargs["checkpoint_payload"] == checkpoint_payload
    assert call_kwargs["recovery_checkpoint_ref"] is None


def test_failed_step_recovery_reports_checkpoint_authorization_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    canonical = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    canonical.memo = {
        **canonical.memo,
        "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
        "task_input_snapshot_ref": "artifact://snapshot/source",
    }
    mock_service.describe_execution.return_value = canonical
    artifact_service = SimpleNamespace(
        read=AsyncMock(side_effect=TemporalArtifactAuthorizationError("denied"))
    )

    class Session:
        async def get(self, model, key):
            return canonical

        async def commit(self):
            return None

    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: Session()
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda _session: artifact_service,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/recover-from-failed-step",
            json={"idempotencyKey": "resume-1"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "checkpoint_unauthorized"
    mock_service.create_failed_step_recovery_execution.assert_not_awaited()


def test_recovery_not_available_reason_prioritizes_mismatch_over_missing_plan() -> None:
    reason = _recovery_not_available_reason(
        ValueError("Recovery checkpoint plan identity does not match source execution.")
    )

    assert reason == "checkpoint_inconsistent"

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
    assert manifest_actions.can_edit_for_rerun is False
    assert (
        manifest_actions.disabled_reasons["canEditForRerun"]
        == "unsupported_workflow_type"
    )
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

    assert actions.can_edit_for_rerun is False
    assert (
        actions.disabled_reasons["canEditForRerun"]
        == "original_task_input_snapshot_missing"
    )
    assert actions.can_rerun is False
    assert (
        actions.disabled_reasons["canRerun"]
        == "original_task_input_snapshot_missing"
    )


def test_mm644_failed_task_edit_for_rerun_requires_authoritative_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_task_editing_enabled", True)
    eligible = _build_execution_record(state=MoonMindWorkflowState.FAILED)

    eligible_body = _serialize_execution(eligible).model_dump(by_alias=True)

    assert eligible_body["taskInputSnapshot"]["available"] is True
    assert eligible_body["taskInputSnapshot"]["artifactRef"] == "art_snapshot_1"
    assert eligible_body["taskInputSnapshot"]["reconstructionMode"] == "authoritative"
    assert eligible_body["actions"]["canEditForRerun"] is True

    missing_snapshot = _build_execution_record(
        state=MoonMindWorkflowState.FAILED,
        has_task_input_snapshot=False,
    )
    missing_body = _serialize_execution(missing_snapshot).model_dump(by_alias=True)

    assert missing_body["taskInputSnapshot"]["available"] is False
    assert missing_body["actions"]["canEditForRerun"] is False
    assert (
        missing_body["actions"]["disabledReasons"]["canEditForRerun"]
        == "original_task_input_snapshot_missing"
    )


def test_mm644_rerun_snapshot_payload_records_source_lineage() -> None:
    payload = _build_original_task_input_snapshot_payload(
        source_kind="rerun",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "task": {
                "instructions": "MM-644 edited retry instructions.",
                "recovery": {
                    "kind": "edited_full_retry",
                    "sourceWorkflowId": "mm:failed-source",
                    "sourceRunId": "run-source",
                },
            },
        },
        task_payload={
            "instructions": "MM-644 edited retry instructions.",
            "recovery": {
                "kind": "edited_full_retry",
                "sourceWorkflowId": "mm:failed-source",
                "sourceRunId": "run-source",
            },
        },
        source_workflow_id="mm:failed-source",
        source_run_id="run-source",
    )

    assert payload["source"] == {
        "kind": "rerun",
        "sourceWorkflowId": "mm:failed-source",
        "sourceRunId": "run-source",
    }
    assert payload["draft"]["task"]["recovery"]["kind"] == "edited_full_retry"


@pytest.mark.asyncio
async def test_exact_rerun_reuses_source_task_input_snapshot_lineage() -> None:
    source = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    source.memo = {
        **source.memo,
        "task_input_snapshot_ref": "artifact://snapshot/source",
        "task_input_snapshot_version": 1,
        "task_input_snapshot_source_kind": "create",
    }
    target = TemporalExecutionRecord(
        workflow_id="mm:rerun",
        run_id="run-rerun",
        namespace="moonmind",
        workflow_type=TemporalWorkflowType.RUN,
        memo={},
        artifact_refs=[],
    )
    canonical = TemporalExecutionCanonicalRecord(
        workflow_id="mm:rerun",
        run_id="run-rerun",
        namespace="moonmind",
        workflow_type=TemporalWorkflowType.RUN,
        memo={},
        artifact_refs=[],
    )
    session = _SnapshotReuseSession(canonical=canonical)

    snapshot_ref = await _reuse_original_task_input_snapshot_from_source(
        session=session,
        source_record=source,
        target_record=target,
    )

    assert snapshot_ref == "artifact://snapshot/source"
    for record in (target, canonical):
        assert record.memo["task_input_snapshot_ref"] == "artifact://snapshot/source"
        assert record.memo["task_input_snapshot_version"] == 1
        assert record.memo["task_input_snapshot_source_kind"] == "rerun"
        assert record.artifact_refs == ["artifact://snapshot/source"]
    session.get.assert_awaited_once_with(
        TemporalExecutionCanonicalRecord,
        "mm:rerun",
    )
    assert len(session.added) == 1
    link = session.added[0]
    assert isinstance(link, TemporalArtifactLink)
    assert link.artifact_id == "artifact://snapshot/source"
    assert link.namespace == "moonmind"
    assert link.workflow_id == "mm:rerun"
    assert link.run_id == "run-rerun"
    assert link.link_type == "input.original_snapshot"


@pytest.mark.asyncio
async def test_exact_rerun_reuses_snapshot_defaults_invalid_version() -> None:
    source = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    source.memo = {
        **source.memo,
        "task_input_snapshot_ref": "artifact://snapshot/source",
        "task_input_snapshot_version": "2026-05-13T00:00:00Z",
        "task_input_snapshot_source_kind": "create",
    }
    target = TemporalExecutionCanonicalRecord(
        workflow_id="mm:rerun",
        run_id="run-rerun",
        namespace="moonmind",
        workflow_type=TemporalWorkflowType.RUN,
        memo={},
        artifact_refs=[],
    )
    session = _SnapshotReuseSession()

    snapshot_ref = await _reuse_original_task_input_snapshot_from_source(
        session=session,
        source_record=source,
        target_record=target,
    )

    assert snapshot_ref == "artifact://snapshot/source"
    assert target.memo["task_input_snapshot_version"] == 1
    assert len(session.added) == 1


def test_terminal_task_editing_actions_reject_parameter_fallback_without_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_task_editing_enabled", True)
    record = _build_execution_record(
        state=MoonMindWorkflowState.FAILED,
        has_task_input_snapshot=False,
    )
    record.parameters = {
        "requestType": "task",
        "repository": "Moon/Mind",
        "targetRuntime": "codex_cli",
        "task": {
            "instructions": "Run Jira Orchestrate for MM-501.",
            "steps": [
                {
                    "id": "step-1",
                    "title": "First step",
                    "instructions": "Do the first step.",
                }
            ],
        },
    }

    actions = _serialize_execution(record).actions

    assert actions.can_update_inputs is False
    assert (
        actions.disabled_reasons["canUpdateInputs"]
        == "original_task_input_snapshot_missing"
    )
    assert actions.can_edit_for_rerun is False
    assert actions.can_rerun is False
    assert (
        actions.disabled_reasons["canEditForRerun"]
        == "original_task_input_snapshot_missing"
    )
    assert (
        actions.disabled_reasons["canRerun"]
        == "original_task_input_snapshot_missing"
    )


def test_terminal_task_editing_actions_reject_title_only_parameter_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_task_editing_enabled", True)
    record = _build_execution_record(
        state=MoonMindWorkflowState.FAILED,
        has_task_input_snapshot=False,
    )
    record.parameters = {
        "requestType": "task",
        "repository": "Moon/Mind",
        "targetRuntime": "codex_cli",
        "task": {
            "steps": [
                {
                    "id": "step-1",
                    "title": "Title without reconstructable instructions",
                }
            ],
        },
    }

    actions = _serialize_execution(record).actions

    assert actions.can_edit_for_rerun is False
    assert actions.can_rerun is False
    assert (
        actions.disabled_reasons["canEditForRerun"]
        == "original_task_input_snapshot_missing"
    )
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
