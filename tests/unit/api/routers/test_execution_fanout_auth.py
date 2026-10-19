"""Mounted execution routes keep machine fan-out separate from operator auth."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers import executions, temporal_artifacts
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.single_user_conversion import SCHEMA_REVISION
from moonmind.config.settings import settings
from moonmind.security import auth_modes_4120
from moonmind.security.execution_fanout_capabilities import (
    mint_execution_fanout_capability,
)
from tests.unit.api.routers.test_executions import (
    _build_execution_record,
    _ExecuteResult,
)

_PARENT_ID = "mm:fanout-parent"
_CHILD_ID = "mm:fanout-child"


def _capability_headers(
    *, expired: bool = False, secret: str | None = None
) -> dict[str, str]:
    token = mint_execution_fanout_capability(
        secret=secret or str(settings.security.JWT_SECRET_KEY),
        parent_workflow_id=_PARENT_ID,
        agent_run_id="agent-run-parent",
        step_id="step-parent",
        session_id="session-parent",
        runtime_id="codex_cli",
        source_kind="omnigent",
        lifetime_seconds=300,
        **({"now": 1} if expired else {}),
    )
    return {
        "X-MoonMind-Execution-Fanout": "v1",
        "Authorization": f"Bearer {token}",
    }


def _child_request() -> dict:
    return {
        "type": "workflow",
        "payload": {
            "runtimeInheritance": "caller",
            "parentWorkflowId": _PARENT_ID,
            "workflow": {
                "instructions": "Perform this child task.",
                "idempotencyKey": "fanout-parent:child",
            },
        },
    }


@pytest.fixture
def fanout_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal, "temporal_authoritative_read_enabled", False)

    async def get_record(model, _key):
        if model is User:
            raise AssertionError("Machine capabilities must not fetch a User")

    session = SimpleNamespace(
        get=AsyncMock(side_effect=get_record),
        execute=AsyncMock(return_value=_ExecuteResult([])),
        add=Mock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    parent = SimpleNamespace(
        workflow_id=_PARENT_ID,
        owner_id="system",
        owner_type="system",
        parameters={"targetRuntime": "codex_cli"},
        memo={},
        search_attributes={},
    )
    child = _build_execution_record(owner_id="system")
    child.workflow_id = _CHILD_ID
    child.parameters = {
        "parentWorkflowId": _PARENT_ID,
        "agentRunId": "agent-run-child",
    }

    async def describe(workflow_id, **_kwargs):
        return parent if workflow_id == _PARENT_ID else child

    service = AsyncMock()
    service.describe_execution.side_effect = describe
    service.describe_cancel_target_execution.return_value = child
    service.create_execution.return_value = child
    service.cancel_execution.return_value = child
    artifact_service = AsyncMock()
    artifact_service.list_authorized_collection.return_value = ([], False)
    temporal_client = Mock()
    temporal_client.get_workflow_handle.return_value.query = AsyncMock(
        return_value=None
    )

    app = FastAPI()
    app.include_router(executions.router)
    app.include_router(temporal_artifacts.router)
    # Auth and fan-out resolution remain the production dependencies. Only
    # storage and external Temporal services are substituted at their edges.
    app.dependency_overrides[get_async_session] = lambda: session
    app.dependency_overrides[executions._get_service] = lambda: service
    app.dependency_overrides[executions.get_temporal_client] = lambda: temporal_client
    app.dependency_overrides[temporal_artifacts._get_temporal_artifact_service] = (
        lambda: artifact_service
    )
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            session=session,
            service=service,
            artifact_service=artifact_service,
            child=child,
        )


def _assert_no_user_lookup(session: SimpleNamespace) -> None:
    assert all(call.args[0] is not User for call in session.get.await_args_list)


def _assert_no_writes(context: SimpleNamespace) -> None:
    context.service.create_execution.assert_not_awaited()
    context.service.cancel_execution.assert_not_awaited()
    context.session.add.assert_not_called()
    context.session.flush.assert_not_awaited()
    context.session.commit.assert_not_awaited()


def test_disabled_mode_fanout_creates_child_without_account_lookup(fanout_client):
    response = fanout_client.client.post(
        "/api/executions", headers=_capability_headers(), json=_child_request()
    )

    assert response.status_code == 201, response.text
    fanout_client.service.create_execution.assert_awaited_once()
    creation = fanout_client.service.create_execution.await_args.kwargs
    assert creation["owner_id"] == "system"
    assert creation["owner_type"] == "system"
    assert creation["initial_parameters"]["parentWorkflowId"] == _PARENT_ID
    assert creation["initial_parameters"]["targetRuntime"] == "codex_cli"
    _assert_no_user_lookup(fanout_client.session)


@pytest.mark.parametrize("operation", ["describe", "cancel"])
def test_disabled_mode_fanout_controls_own_child_without_account_lookup(
    fanout_client, operation
):
    if operation == "describe":
        response = fanout_client.client.get(
            f"/api/executions/{_CHILD_ID}", headers=_capability_headers()
        )
        assert response.status_code == 200, response.text
        fanout_client.service.cancel_execution.assert_not_awaited()
    else:
        response = fanout_client.client.post(
            f"/api/executions/{_CHILD_ID}/cancel", headers=_capability_headers()
        )
        assert response.status_code == 202, response.text
        fanout_client.service.cancel_execution.assert_awaited_once()
        assert (
            fanout_client.service.cancel_execution.await_args.kwargs["workflow_id"]
            == _CHILD_ID
        )
    assert response.json()["workflowId"] == _CHILD_ID
    _assert_no_user_lookup(fanout_client.session)


@pytest.mark.parametrize(
    ("operation", "credential", "code"),
    [
        ("create", "missing", "execution_fanout_capability_required"),
        ("describe", "forged", "invalid_execution_fanout_capability"),
        ("cancel", "expired", "invalid_execution_fanout_capability"),
    ],
)
def test_fanout_credentials_are_verified_before_execution_access(
    fanout_client, operation, credential, code
):
    headers = _capability_headers(expired=credential == "expired")
    if credential == "missing":
        headers.pop("Authorization")
    elif credential == "forged":
        headers = _capability_headers(secret="untrusted-fanout-signing-key")
    if operation == "create":
        response = fanout_client.client.post(
            "/api/executions", headers=headers, json=_child_request()
        )
    elif operation == "describe":
        response = fanout_client.client.get(
            f"/api/executions/{_CHILD_ID}", headers=headers
        )
    else:
        response = fanout_client.client.post(
            f"/api/executions/{_CHILD_ID}/cancel", headers=headers
        )

    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == code
    fanout_client.service.describe_execution.assert_not_awaited()
    fanout_client.service.describe_cancel_target_execution.assert_not_awaited()
    _assert_no_writes(fanout_client)
    _assert_no_user_lookup(fanout_client.session)


def test_fanout_create_rejects_parent_scope_mismatch_before_writes(fanout_client):
    payload = _child_request()
    payload["payload"]["parentWorkflowId"] = "mm:another-parent"
    response = fanout_client.client.post(
        "/api/executions", headers=_capability_headers(), json=payload
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "execution_fanout_parent_mismatch"
    fanout_client.service.describe_execution.assert_not_awaited()
    _assert_no_writes(fanout_client)
    _assert_no_user_lookup(fanout_client.session)


@pytest.mark.parametrize("operation", ["describe", "cancel"])
def test_fanout_hides_unrelated_children_before_writes(fanout_client, operation):
    if operation == "describe":
        fanout_client.child.parameters["parentWorkflowId"] = "mm:another-parent"
        response = fanout_client.client.get(
            f"/api/executions/{_CHILD_ID}", headers=_capability_headers()
        )
    else:
        fanout_client.child.owner_id = "another-owner"
        response = fanout_client.client.post(
            f"/api/executions/{_CHILD_ID}/cancel", headers=_capability_headers()
        )

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "execution_not_found"
    fanout_client.service.describe_cancel_target_execution.assert_not_awaited()
    _assert_no_writes(fanout_client)
    _assert_no_user_lookup(fanout_client.session)


@pytest.mark.parametrize("with_capability", [False, True])
def test_fanout_marker_cannot_admit_an_artifact_operator(
    fanout_client, with_capability
):
    headers = _capability_headers()
    if not with_capability:
        headers.pop("Authorization")
    response = fanout_client.client.get("/api/artifacts/collection", headers=headers)

    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "auth_required"
    fanout_client.artifact_service.list_authorized_collection.assert_not_awaited()
    _assert_no_writes(fanout_client)
    _assert_no_user_lookup(fanout_client.session)


def test_unmarked_disabled_operator_still_uses_local_admission(fanout_client):
    operator = User(
        id=UUID("00000000-0000-0000-0000-000000000000"),
        email="operator@example.com",
        is_active=True,
        is_superuser=False,
    )
    fanout_client.session.get.side_effect = None
    fanout_client.session.get.return_value = operator

    response = fanout_client.client.get("/api/artifacts/collection")

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    fanout_client.session.get.assert_awaited_once()
    assert fanout_client.session.get.await_args.args[0] is User
    assert operator.is_superuser is True
    assert (
        fanout_client.artifact_service.list_authorized_collection.await_args.kwargs[
            "principal"
        ]
        == str(operator.id)
    )


def test_unmarked_disabled_request_still_fails_on_identity_store_outage(fanout_client):
    response = fanout_client.client.get(f"/api/executions/{_CHILD_ID}")

    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "unavailable"
    fanout_client.session.get.assert_awaited_once()
    assert fanout_client.session.get.await_args.args[0] is User
    fanout_client.service.describe_execution.assert_not_awaited()
    _assert_no_writes(fanout_client)


def test_completed_conversion_admits_account_free_artifact_operator(
    fanout_client, monkeypatch
):
    operator_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", operator_id)
    fanout_client.session.get.side_effect = None
    fanout_client.session.get.return_value = None
    fanout_client.session.execute.return_value = _ExecuteResult(
        [
            SimpleNamespace(
                status="complete",
                result_json={
                    "schema_revision": SCHEMA_REVISION,
                    "published": True,
                    "decision": {"disposition": "fresh_init"},
                },
            )
        ]
    )

    response = fanout_client.client.get("/api/artifacts/collection")

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    fanout_client.session.get.assert_awaited_once_with(User, UUID(operator_id))
    fanout_client.session.execute.assert_awaited_once()
    assert (
        fanout_client.artifact_service.list_authorized_collection.await_args.kwargs[
            "principal"
        ]
        == operator_id
    )
    _assert_no_writes(fanout_client)
