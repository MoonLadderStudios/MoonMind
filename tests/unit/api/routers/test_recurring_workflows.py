"""Router-level unit tests for recurring workflow endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from api_service.api.routers import recurring_workflows as recurring_router
from api_service.db.models import (
    RecurringWorkflowRunOutcome,
    RecurringWorkflowRunTrigger,
    RecurringWorkflowScheduleType,
    RecurringWorkflowScopeType,
)
from api_service.services.recurring_workflows_service import (
    RecurringScheduleRuntimeSummary,
    RecurringWorkflowValidationError,
)

def _definition(**overrides):
    now = datetime.now(UTC)
    base = {
        "id": uuid4(),
        "name": "Daily Demo",
        "description": "description",
        "enabled": True,
        "schedule_type": RecurringWorkflowScheduleType.CRON,
        "cron": "0 9 * * *",
        "timezone": "UTC",
        "next_run_at": now,
        "last_scheduled_for": None,
        "last_dispatch_status": None,
        "last_dispatch_error": None,
        "owner_user_id": uuid4(),
        "scope_type": RecurringWorkflowScopeType.PERSONAL,
        "scope_ref": None,
        "target": {
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {
                "workflow": {"instructions": "Test recurring workflow fixture."}
            },
        },
        "policy": {},
        "temporal_schedule_id": None,
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
        "trigger": RecurringWorkflowRunTrigger.SCHEDULE,
        "outcome": RecurringWorkflowRunOutcome.PENDING_DISPATCH,
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


def test_recurring_workflow_validation_error_maps_to_422() -> None:
    exc = recurring_router._map_error(
        RecurringWorkflowValidationError("target.workflowType is required")
    )

    assert exc.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert exc.detail == {
        "code": "invalid_recurring_workflow",
        "message": "target.workflowType is required",
    }

@pytest.mark.asyncio
async def test_list_recurring_workflows_global_requires_operator() -> None:
    service = AsyncMock()
    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    with pytest.raises(HTTPException) as exc:
        await recurring_router.list_recurring_workflows(
            scope="global",
            limit=50,
            service=service,
            user=user,
        )

    assert exc.value.status_code == 403

@pytest.mark.asyncio
async def test_list_recurring_workflows_uses_runtime_schedule_summary() -> None:
    service = AsyncMock()
    stale_next_run = datetime(2026, 6, 23, 13, tzinfo=UTC)
    live_next_run = datetime(2026, 6, 24, 13, tzinfo=UTC)
    last_scheduled_for = datetime(2026, 6, 23, 13, tzinfo=UTC)
    definition = _definition(next_run_at=stale_next_run)
    service.list_definitions.return_value = [definition]
    service.runtime_summaries_for_definitions.return_value = {
        definition.id: RecurringScheduleRuntimeSummary(
            next_run_at=live_next_run,
            last_scheduled_for=last_scheduled_for,
            last_dispatch_status="enqueued",
            last_dispatch_error=None,
        )
    }
    user = SimpleNamespace(id=definition.owner_user_id, is_superuser=False)

    response = await recurring_router.list_recurring_workflows(
        scope="personal",
        limit=50,
        service=service,
        user=user,
    )

    assert response.items[0].next_run_at == live_next_run
    assert response.items[0].last_scheduled_for == last_scheduled_for
    assert response.items[0].last_dispatch_status == "enqueued"
    service.runtime_summaries_for_definitions.assert_awaited_once_with([definition])

@pytest.mark.asyncio
async def test_create_recurring_workflow_returns_serialized_definition() -> None:
    service = AsyncMock()
    definition = _definition(temporal_schedule_id="mm-schedule:test-definition")
    service.create_definition.return_value = definition
    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    response = await recurring_router.create_recurring_workflow(
        payload=recurring_router.CreateRecurringWorkflowRequest(
            name="Daily Demo",
            cron="0 9 * * *",
            timezone="UTC",
            target={
                "workflowType": "MoonMind.UserWorkflow",
                "initialParameters": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "task": {
                        "instructions": "Queue job",
                        "publish": {"mode": "none"},
                        "skill": {"id": "auto", "args": {}},
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
    assert response.temporal_schedule_id == "mm-schedule:test-definition"
    service.create_definition.assert_awaited_once()

@pytest.mark.asyncio
async def test_run_recurring_workflow_now_returns_run_row() -> None:
    service = AsyncMock()
    definition = _definition()
    run_row = _run(definition_id=definition.id)
    service.require_authorized_definition.return_value = definition
    service.create_manual_run.return_value = run_row
    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    response = await recurring_router.run_recurring_workflow_now(
        definition_id=definition.id,
        service=service,
        user=user,
    )

    assert response.definition_id == definition.id
    assert response.outcome == "pending_dispatch"
    service.create_manual_run.assert_awaited_once_with(definition)
