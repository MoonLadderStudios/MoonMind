"""Fan-out batch-target guards for isolated multi-repository batches.

MoonLadderStudios/MoonMind#1657: children admitted through the portable
repository-batch path carry ``batchDigest`` plus a ``batchTarget`` naming
exactly one explicit repository authority. These tests prove the execution
API enforces that shape additively: legacy payloads without batch fields
keep their behavior, while batch children require an explicit connection,
reject wildcard expansion, and refuse credential material.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.executions import (
    _get_service,
    get_temporal_client,
    router,
)
from moonmind.config.settings import settings
from moonmind.security.execution_fanout_capabilities import (
    mint_execution_fanout_capability,
)
from tests.unit.api.routers.test_executions import (
    _build_execution_record,
    _override_user_dependencies,
)


@pytest.fixture
def batch_client() -> Iterator[tuple[TestClient, AsyncMock, SimpleNamespace]]:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()

    def _override_batch_service():  # noqa: ANN202 - FastAPI dependency override
        return service

    def _override_batch_temporal_client():  # noqa: ANN202 - FastAPI dependency override
        return AsyncMock()

    app.dependency_overrides[_get_service] = _override_batch_service
    app.dependency_overrides[get_temporal_client] = _override_batch_temporal_client
    user = _override_user_dependencies(app, is_superuser=False)
    # Fan-out tests authenticate via the execution-scoped bearer; the
    # session fallback must not deny the request before that boundary.
    for dependency in tuple(app.dependency_overrides):
        if getattr(dependency, "__name__", "") in {
            "_current_user_fallback",
            "_strict_current_user",
            "_optional_current_user",
        }:
            app.dependency_overrides[dependency] = lambda: None
    with TestClient(app) as test_client:
        yield test_client, service, user
    app.dependency_overrides.clear()


def _fanout_token(*, parent_workflow_id: str = "mm:batch-parent") -> str:
    return mint_execution_fanout_capability(
        secret=str(settings.security.JWT_SECRET_KEY),
        parent_workflow_id=parent_workflow_id,
        agent_run_id="agent-run-batch",
        step_id="step-batch",
        session_id="session-batch",
        runtime_id="codex_cli",
        source_kind="omnigent",
        lifetime_seconds=300,
    )


def _fanout_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_fanout_token()}",
        "X-MoonMind-Execution-Fanout": "v1",
    }


def _parent_record(user: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        workflow_id="mm:batch-parent",
        owner_id=user.id,
        owner_type="user",
        parameters={
            "targetRuntime": "codex",
            "model": "gpt-5.4",
            "effort": "high",
            "workflow": {"runtime": {}},
        },
        memo={},
        search_attributes={},
    )


def _batch_task_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "runtimeInheritance": "caller",
        "repository": {
            "provider": "git",
            "connectionRef": "repository-connection:git-default",
            "repository": {"name": "acme/one"},
            "branch": {"name": "main"},
        },
        "task": {
            "goal": "Apply the batch task to acme/one.",
            "instructions": "Apply the batch task to acme/one.",
            "skill": {"name": "pr-resolver"},
            "inputs": {"repo": "acme/one", "pr": 1},
            "publish": {"mode": "auto"},
            "idempotencyKey": "repository-batch:acme_one:sha256:abc",
        },
        "idempotencyKey": "repository-batch:acme_one:sha256:abc",
        "batchDigest": "sha256:approved",
        "batchTarget": {
            "targetRef": "https://github.com#acme/one",
            "endpoint": "https://github.com",
            "repository": "acme/one",
            "connectionRef": "repository-connection:git-default",
            "branch": "main",
            "operation": "pr",
        },
    }
    payload.update(overrides)
    return payload


def test_batch_child_with_explicit_authority_is_accepted(
    batch_client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = batch_client
    service.describe_execution.return_value = _parent_record(user)
    service.create_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )
    response = test_client.post(
        "/api/executions",
        headers=_fanout_headers(),
        json={"type": "task", "payload": _batch_task_payload()},
    )
    assert response.status_code == 201, response.json()
    assert service.create_execution.await_count == 1
    _, kwargs = service.create_execution.call_args
    initial = kwargs.get("initial_parameters") or {}
    assert initial.get("batchDigest") == "sha256:approved"
    assert isinstance(initial.get("batchTarget"), dict)
    assert initial["batchTarget"]["repository"] == "acme/one"


def test_batch_child_without_explicit_connection_is_rejected(
    batch_client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = batch_client
    service.describe_execution.return_value = _parent_record(user)
    payload = _batch_task_payload()
    assert isinstance(payload["batchTarget"], dict)
    payload["batchTarget"] = dict(payload["batchTarget"], connectionRef="  ")
    response = test_client.post(
        "/api/executions",
        headers=_fanout_headers(),
        json={"type": "task", "payload": payload},
    )
    assert response.status_code == 422
    assert "connectionRef" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_batch_child_with_wildcard_repository_is_rejected(
    batch_client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = batch_client
    service.describe_execution.return_value = _parent_record(user)
    payload = _batch_task_payload()
    assert isinstance(payload["batchTarget"], dict)
    payload["batchTarget"] = dict(payload["batchTarget"], repository="acme/*")
    response = test_client.post(
        "/api/executions",
        headers=_fanout_headers(),
        json={"type": "task", "payload": payload},
    )
    assert response.status_code == 422
    assert "wildcard" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_batch_child_with_credential_material_is_rejected(
    batch_client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = batch_client
    service.describe_execution.return_value = _parent_record(user)
    payload = _batch_task_payload()
    assert isinstance(payload["batchTarget"], dict)
    payload["batchTarget"] = {
        **payload["batchTarget"],
        "tokenBundle": {"acme/one": "ghp_example"},
    }
    response = test_client.post(
        "/api/executions",
        headers=_fanout_headers(),
        json={"type": "task", "payload": payload},
    )
    assert response.status_code == 422
    assert "credential" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_batch_child_with_nested_credential_material_is_rejected(
    batch_client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = batch_client
    service.describe_execution.return_value = _parent_record(user)
    for nested in (
        {"repositoryTarget": {"token": "ghp_example"}},
        {"repositoryTarget": {"secret": "shhh"}},
        {"token": "ghp_example"},
        {"password": "hunter2"},
    ):
        payload = _batch_task_payload()
        assert isinstance(payload["batchTarget"], dict)
        payload["batchTarget"] = {**payload["batchTarget"], **nested}
        response = test_client.post(
            "/api/executions",
            headers=_fanout_headers(),
            json={"type": "task", "payload": payload},
        )
        assert response.status_code == 422, nested
        assert "credential" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_legacy_fanout_child_without_batch_fields_is_unchanged(
    batch_client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = batch_client
    service.describe_execution.return_value = _parent_record(user)
    service.create_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )
    payload = _batch_task_payload()
    del payload["batchDigest"]
    del payload["batchTarget"]
    response = test_client.post(
        "/api/executions",
        headers=_fanout_headers(),
        json={"type": "task", "payload": payload},
    )
    assert response.status_code == 201, response.json()


def test_batch_target_must_match_executable_repository(
    batch_client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = batch_client
    service.describe_execution.return_value = _parent_record(user)
    payload = _batch_task_payload()
    assert isinstance(payload["batchTarget"], dict)
    payload["batchTarget"] = dict(payload["batchTarget"], repository="evil/other")
    response = test_client.post(
        "/api/executions",
        headers=_fanout_headers(),
        json={"type": "task", "payload": payload},
    )
    assert response.status_code == 422
    assert "batchTarget" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_fanout_capability_can_cancel_owned_child(
    batch_client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = batch_client
    parent = _parent_record(user)
    child = _build_execution_record(owner_id=str(user.id))
    child.workflow_id = "mm:child-1"
    child.parameters = {"parentWorkflowId": "mm:batch-parent"}
    child.owner_id = str(user.id)
    child.owner_type = "user"

    async def _describe(workflow_id: str, *args: Any, **kwargs: Any) -> Any:
        if workflow_id == "mm:batch-parent":
            return parent
        return child

    service.describe_execution.side_effect = _describe
    service.describe_cancel_target_execution.return_value = child
    service.cancel_execution.return_value = child
    response = test_client.post(
        "/api/executions/mm:child-1/cancel",
        headers=_fanout_headers(),
        json={"reason": "repository batch cancel"},
    )
    assert response.status_code == 202, response.json()
    service.cancel_execution.assert_awaited_once()
