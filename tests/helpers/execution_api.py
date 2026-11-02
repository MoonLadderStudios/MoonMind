"""Shared execution API fixtures, without importing a collected test module."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from fastapi import FastAPI, Request

from api_service.api.routers import executions as executions_module
from api_service.api.routers.executions import get_temporal_client, router
from api_service.auth_providers import get_instance_access, get_instance_access_optional
from api_service.db.models import MoonMindWorkflowState, TemporalWorkflowType
from tests.artifact_access import access_for_test


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


def _override_user_dependencies(
    app: FastAPI,
    *,
    is_superuser: bool,
    roles: list[str] | None = None,
) -> SimpleNamespace:
    mock_user = SimpleNamespace(
        id=uuid4(),
        email="executions@example.com",
        is_active=True,
        is_superuser=is_superuser,
        roles=list(roles or []),
    )
    user_dependencies = {
        dep.call
        for route in router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if dep.call.__name__
        in {
            "_current_user_fallback",
            "_strict_current_user",
            "_optional_current_user",
            "get_instance_access",
            "get_instance_access",
            "get_instance_access_optional",
        }
    }
    if not user_dependencies:
        user_dependencies = {get_instance_access, get_instance_access_optional}

    def _current_user() -> SimpleNamespace:
        return mock_user

    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = _current_user

    async def fixture_create_access(request: Request):
        if request.headers.get("X-MoonMind-Execution-Fanout"):
            return await executions_module._execution_request_artifact_access(
                request, user=None
            )
        return access_for_test("operator")

    app.dependency_overrides[executions_module._execution_request_artifact_access] = (
        fixture_create_access
    )
    app.dependency_overrides[executions_module.get_artifact_access] = (
        lambda: access_for_test("operator")
    )
    return mock_user


def _empty_session_override() -> SimpleNamespace:
    return SimpleNamespace()


def _build_execution_record(
    *,
    workflow_type: TemporalWorkflowType = TemporalWorkflowType.USER_WORKFLOW,
    state: MoonMindWorkflowState = MoonMindWorkflowState.EXECUTING,
    owner_id: str = "user-123",
    has_workflow_input_snapshot: bool = True,
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
                if workflow_type is TemporalWorkflowType.USER_WORKFLOW
                and has_workflow_input_snapshot
                else {}
            ),
        },
        artifact_refs=["art_123"],
        finish_outcome_code=None,
        finish_summary_json=None,
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
