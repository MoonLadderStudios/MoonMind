"""Router-level unit tests for recurring task endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api_service.api.routers import recurring_tasks as recurring_router
from api_service.db.models import (
    RecurringTaskRunOutcome,
    RecurringTaskRunTrigger,
    RecurringTaskScheduleType,
    RecurringTaskScopeType,
)


def _definition(**overrides):
    now = datetime.now(UTC)
    base = {
        "id": uuid4(),
        "name": "Daily Demo",
        "description": "description",
        "enabled": True,
        "schedule_type": RecurringTaskScheduleType.CRON,
        "cron": "0 9 * * *",
        "timezone": "UTC",
        "next_run_at": now,
        "last_scheduled_for": None,
        "last_dispatch_status": None,
        "last_dispatch_error": None,
        "owner_user_id": uuid4(),
        "scope_type": RecurringTaskScopeType.PERSONAL,
        "scope_ref": None,
        "target": {"kind": "queue_task", "job": {"type": "task", "payload": {}}},
        "policy": {},
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(**overrides):
    now = datetime.now(UTC)
    base = {
        "id": uuid4(),
        "definition_id": uuid4(),
        "scheduled_for": now,
        "trigger": RecurringTaskRunTrigger.SCHEDULE,
        "outcome": RecurringTaskRunOutcome.PENDING_DISPATCH,
        "dispatch_attempts": 0,
        "dispatch_after": now,
        "queue_job_id": None,
        "queue_job_type": None,
        "temporal_workflow_id": None,
        "temporal_run_id": None,
        "message": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_list_recurring_tasks_global_requires_operator() -> None:
    service = AsyncMock()
    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    with pytest.raises(HTTPException) as exc:
        await recurring_router.list_recurring_tasks(
            scope="global",
            limit=50,
            service=service,
            user=user,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_create_recurring_task_returns_serialized_definition() -> None:
    service = AsyncMock()
    definition = _definition()
    service.create_definition.return_value = definition
    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    response = await recurring_router.create_recurring_task(
        payload=recurring_router.CreateRecurringTaskRequest(
            name="Daily Demo",
            cron="0 9 * * *",
            timezone="UTC",
            target={
                "kind": "queue_task",
                "job": {
                    "type": "task",
                    "payload": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "task": {
                            "instructions": "Queue job",
                            "publish": {"mode": "none"},
                            "skill": {"id": "auto", "args": {}},
                        },
                    },
                },
            },
        ),
        service=service,
        user=user,
    )

    assert response.id == definition.id
    assert response.schedule_type == "cron"
    assert response.scope_type == "personal"
    service.create_definition.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_recurring_task_now_returns_run_row() -> None:
    service = AsyncMock()
    definition = _definition()
    run_row = _run(definition_id=definition.id)
    service.require_authorized_definition.return_value = definition
    service.create_manual_run.return_value = run_row
    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    response = await recurring_router.run_recurring_task_now(
        definition_id=definition.id,
        service=service,
        user=user,
    )

    assert response.definition_id == definition.id
    assert response.outcome == "pending_dispatch"
    service.create_manual_run.assert_awaited_once_with(definition)
