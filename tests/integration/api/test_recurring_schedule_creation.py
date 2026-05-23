"""Hermetic API contract tests for recurring schedule creation via executions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.executions import (
    _get_service,
    get_temporal_client,
    router,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.services.recurring_tasks_service import RecurringTaskValidationError

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _override_user_dependencies(app: FastAPI) -> SimpleNamespace:
    user = SimpleNamespace(
        id=uuid4(),
        email="recurring@example.com",
        is_active=True,
        is_superuser=False,
    )
    user_dependencies = {
        dep.call
        for route_entry in router.routes
        if route_entry.dependant is not None
        for dep in route_entry.dependant.dependencies
        if dep.call.__name__ == "_current_user_fallback"
    }
    if not user_dependencies:
        user_dependencies = {get_current_user()}

    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = lambda user=user: user
    return user


def _mock_service_override() -> AsyncMock:
    return AsyncMock()


def _empty_session_override() -> SimpleNamespace:
    return SimpleNamespace()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_service] = _mock_service_override
    app.dependency_overrides[get_temporal_client] = _mock_service_override
    app.dependency_overrides[get_async_session] = _empty_session_override
    _override_user_dependencies(app)
    return TestClient(app)


def test_executions_recurring_schedule_success_contract() -> None:
    definition_id = uuid4()
    next_run_at = datetime.now(UTC) + timedelta(hours=1)

    with _client() as client, patch(
        "api_service.services.recurring_tasks_service.RecurringTasksService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            return_value=SimpleNamespace(
                id=definition_id,
                name="Inline schedule",
                cron="0 * * * *",
                timezone="UTC",
                next_run_at=next_run_at,
            )
        )

        response = client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "task": {"instructions": "Run this on a schedule"},
                },
            },
        )

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["scheduled"] is True
    assert body["definitionId"] == str(definition_id)
    assert body["redirectPath"] == f"/schedules/{definition_id}"


def test_executions_recurring_schedule_validation_contract() -> None:
    with _client() as client, patch(
        "api_service.services.recurring_tasks_service.RecurringTasksService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            side_effect=RecurringTaskValidationError("target.kind is required")
        )

        response = client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "task": {"instructions": "Run this on a schedule"},
                },
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_recurring_task",
        "message": "target.kind is required",
    }
