from __future__ import annotations

from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.executions import _get_service, router
from api_service.auth_providers import get_current_user
from moonmind.workflows.temporal import TemporalExecutionValidationError


@pytest.fixture
def client() -> Iterator[tuple[TestClient, AsyncMock, SimpleNamespace]]:
    app = FastAPI()
    app.include_router(router)

    service = AsyncMock()

    async def _service_override():
        return service

    app.dependency_overrides[_get_service] = _service_override

    user = SimpleNamespace(
        id=uuid4(),
        email="executions@example.com",
        is_active=True,
        is_superuser=False,
    )
    app.dependency_overrides[get_current_user()] = lambda user=user: user

    with TestClient(app) as test_client:
        yield test_client, service, user

    app.dependency_overrides.clear()


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
    assert response.json() == {
        "detail": {
            "code": "invalid_execution_request",
            "message": "Unsupported workflow type: MoonMind.Unknown",
        }
    }
    assert service.create_execution.await_args.kwargs["workflow_type"] == "MoonMind.Unknown"


def test_list_executions_rejects_non_admin_cross_owner_queries(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.get(
        "/api/executions",
        params={"ownerId": str(uuid4())},
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "code": "execution_forbidden",
            "message": "Cannot list executions for another user.",
        }
    }
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
    assert response.json() == {
        "detail": {
            "code": "execution_not_found",
            "message": "Workflow execution mm:foreign was not found",
        }
    }
    assert str(user.id) != service.describe_execution.return_value.owner_id


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
    assert response.json() == {
        "detail": {
            "code": "invalid_update_request",
            "message": "Unsupported update name: UnknownUpdate",
        }
    }
    assert service.update_execution.await_args.kwargs["update_name"] == "UnknownUpdate"


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
    assert response.json() == {
        "detail": {
            "code": "signal_rejected",
            "message": "Unsupported signal name: UnknownSignal",
        }
    }
    assert service.signal_execution.await_args.kwargs["signal_name"] == "UnknownSignal"
