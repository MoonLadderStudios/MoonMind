"""Unit tests for recurring workflow scheduling service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    RecurringWorkflowDefinition,
    RecurringWorkflowRun,
    RecurringWorkflowRunOutcome,
)
from api_service.services.recurring_workflows_service import (
    RecurringWorkflowsService,
    RecurringWorkflowValidationError,
)

pytestmark = [pytest.mark.asyncio]

@asynccontextmanager
async def recurring_db(tmp_path: Path):
    db_path = tmp_path / "recurring_workflows.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async_session_maker = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield async_session_maker
    finally:
        await engine.dispose()

@pytest.fixture
def mock_temporal_adapter():
    adapter = MagicMock()
    adapter.create_schedule = AsyncMock(return_value="mm-schedule:id")
    adapter.update_schedule = AsyncMock()
    adapter.pause_schedule = AsyncMock()
    adapter.unpause_schedule = AsyncMock()
    adapter.trigger_schedule = AsyncMock()
    adapter.delete_schedule = AsyncMock()
    return adapter

async def test_create_definition_creates_temporal_schedule(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=mock_temporal_adapter
            )
            definition = await service.create_definition(
                name="Daily Demo",
                description="Nightly schedule",
                enabled=True,
                schedule_type="cron",
                cron="0 6 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=uuid4(),
                target={
                    "workflowType": "MoonMind.UserWorkflow",
                    "initialParameters": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "targetRuntime": "codex",
                        "task": {
                            "instructions": "Queue job",
                            "publish": {"mode": "none"},
                            "skill": {"id": "auto", "args": {}},
                        },
                    },
                },
                policy={"misfireGraceSeconds": 300},
            )

            assert definition.schedule_type.value == "cron"
            assert definition.next_run_at is not None
            assert definition.next_run_at.hour == 6
            assert definition.next_run_at.minute == 0
            assert definition.version == 1

            mock_temporal_adapter.create_schedule.assert_called_once()
            call_kwargs = mock_temporal_adapter.create_schedule.call_args.kwargs
            assert call_kwargs["definition_id"] == definition.id
            assert call_kwargs["cron_expression"] == "0 6 * * *"
            assert call_kwargs["timezone"] == "UTC"
            assert call_kwargs["workflow_type"] == "MoonMind.UserWorkflow"
            assert call_kwargs["workflow_input"]["workflowType"] == "MoonMind.UserWorkflow"
            assert call_kwargs["workflow_input"]["initialParameters"]["task"][
                "instructions"
            ] == "Queue job"
            assert call_kwargs["workflow_input"]["initialParameters"]["system"][
                "recurrence"
            ]["definitionId"] == str(definition.id)

async def test_create_definition_rejects_invalid_policy(tmp_path: Path, mock_temporal_adapter) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringWorkflowsService(session, temporal_client_adapter=mock_temporal_adapter)
            with pytest.raises(RecurringWorkflowValidationError):
                await service.create_definition(
                    name="Invalid Policy",
                    description=None,
                    enabled=True,
                    schedule_type="cron",
                    cron="0 6 * * *",
                    timezone="UTC",
                    scope_type="personal",
                    scope_ref=None,
                    owner_user_id=uuid4(),
                    target={
                        "workflowType": "MoonMind.UserWorkflow",
                        "initialParameters": {
                            "repository": "MoonLadderStudios/MoonMind",
                            "targetRuntime": "codex",
                            "task": {
                                "instructions": "Queue job",
                                "publish": {"mode": "none"},
                                "skill": {"id": "auto", "args": {}},
                            },
                        },
                    },
                    policy={"catchup": {"mode": "invalid"}},
                )

async def test_target_workflow_type_housekeeping_is_rejected(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringWorkflowsService(session, temporal_client_adapter=mock_temporal_adapter)
            with pytest.raises(RecurringWorkflowValidationError):
                await service.create_definition(
                    name="Housekeeping",
                    description=None,
                    enabled=True,
                    schedule_type="cron",
                    cron="* * * * *",
                    timezone="UTC",
                    scope_type="personal",
                    scope_ref=None,
                    owner_user_id=uuid4(),
                    target={
                        "workflowType": "MoonMind.Housekeeping",
                        "initialParameters": {"action": "prune_artifacts"},
                    },
                    policy={},
                )

async def test_update_definition_updates_temporal_schedule(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=mock_temporal_adapter
            )
            definition = await service.create_definition(
                name="Daily Demo",
                description="Nightly schedule",
                enabled=True,
                schedule_type="cron",
                cron="0 6 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=uuid4(),
                target={
                    "workflowType": "MoonMind.UserWorkflow",
                    "initialParameters": {
                        "task": {
                            "instructions": "Queue job",
                        },
                    },
                },
                policy={},
            )

            mock_temporal_adapter.create_schedule.assert_called_once()
            mock_temporal_adapter.update_schedule.assert_not_called()

            # Now update the definition
            updated = await service.update_definition(
                definition,
                name="Updated Name",
                enabled=False,
                cron="0 12 * * *",
            )

            assert updated.name == "Updated Name"
            assert updated.enabled is False
            assert updated.cron == "0 12 * * *"
            
            mock_temporal_adapter.pause_schedule.assert_called_once_with(definition_id=definition.id)
            mock_temporal_adapter.update_schedule.assert_called_once()
            call_kwargs = mock_temporal_adapter.update_schedule.call_args.kwargs
            assert call_kwargs["definition_id"] == definition.id
            assert call_kwargs["cron_expression"] == "0 12 * * *"
            assert call_kwargs["enabled"] is False

async def test_create_manual_run_triggers_temporal_schedule(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=mock_temporal_adapter
            )
            definition = await service.create_definition(
                name="Daily Demo",
                description="Nightly schedule",
                enabled=True,
                schedule_type="cron",
                cron="0 6 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=uuid4(),
                target={
                    "workflowType": "MoonMind.UserWorkflow",
                    "initialParameters": {
                        "task": {
                            "instructions": "Queue job",
                        },
                    },
                },
                policy={},
            )

            run = await service.create_manual_run(definition)

            mock_temporal_adapter.trigger_schedule.assert_called_once_with(definition_id=definition.id)
            assert run.outcome == RecurringWorkflowRunOutcome.ENQUEUED
            assert run.message == "Triggered via Temporal Schedule"
