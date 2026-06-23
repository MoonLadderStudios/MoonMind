"""Unit tests for recurring workflow scheduling service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call
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
    RecurringScheduleRuntimeSummary,
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
    adapter.describe_schedule = AsyncMock()
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
            assert call_kwargs["workflow_input"]["workflow_type"] == (
                "MoonMind.UserWorkflow"
            )
            assert "workflowType" not in call_kwargs["workflow_input"]
            assert call_kwargs["workflow_input"]["initial_parameters"]["task"][
                "instructions"
            ] == "Queue job"
            assert call_kwargs["workflow_input"]["initial_parameters"]["system"][
                "recurrence"
            ]["definitionId"] == str(definition.id)
            assert call_kwargs["search_attributes"] == {
                "mm_owner_type": "user",
                "mm_owner_id": str(definition.owner_user_id),
            }

async def test_create_definition_normalizes_snake_case_target_aliases(
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
                    "workflow_type": "MoonMind.UserWorkflow",
                    "initial_parameters": {
                        "task": {
                            "instructions": "Queue job",
                        },
                    },
                    "input_artifact_ref": "artifact://input/1",
                    "plan_artifact_ref": "artifact://plan/1",
                    "failure_policy": "fail_fast",
                },
                policy={},
            )

            assert definition.target["workflowType"] == "MoonMind.UserWorkflow"
            assert definition.target["initialParameters"]["task"][
                "instructions"
            ] == "Queue job"
            assert definition.target["inputArtifactRef"] == "artifact://input/1"
            assert definition.target["planArtifactRef"] == "artifact://plan/1"
            assert definition.target["failurePolicy"] == "fail_fast"
            assert "workflow_type" not in definition.target
            assert "initial_parameters" not in definition.target
            assert "input_artifact_ref" not in definition.target
            call_kwargs = mock_temporal_adapter.create_schedule.call_args.kwargs
            assert call_kwargs["workflow_input"]["input_artifact_ref"] == (
                "artifact://input/1"
            )
            assert call_kwargs["workflow_input"]["plan_artifact_ref"] == (
                "artifact://plan/1"
            )
            assert call_kwargs["workflow_input"]["failure_policy"] == "fail_fast"

async def test_create_definition_manifest_reads_action_options_from_initial_parameters(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=mock_temporal_adapter
            )
            definition = await service.create_definition(
                name="Manifest Plan",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="0 6 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=uuid4(),
                target={
                    "workflowType": "MoonMind.ManifestIngest",
                    "manifest_ref": "artifact://manifest/1",
                    "initialParameters": {
                        "action": "plan",
                        "options": {"dryRun": True, "maxDocs": 3},
                    },
                },
                policy={},
            )

            assert definition.target["manifestArtifactRef"] == "artifact://manifest/1"
            assert definition.target["action"] == "plan"
            assert definition.target["options"] == {"dryRun": True, "maxDocs": 3}
            assert "manifest_ref" not in definition.target
            call_kwargs = mock_temporal_adapter.create_schedule.call_args.kwargs
            assert call_kwargs["workflow_type"] == "MoonMind.ManifestIngest"
            assert call_kwargs["workflow_input"] == {
                "workflow_type": "MoonMind.ManifestIngest",
                "manifest_ref": "artifact://manifest/1",
                "action": "plan",
                "options": {"dryRun": True, "maxDocs": 3},
            }

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
            assert call_kwargs["workflow_type"] == "MoonMind.UserWorkflow"
            assert call_kwargs["workflow_input"]["workflow_type"] == (
                "MoonMind.UserWorkflow"
            )
            assert call_kwargs["workflow_input"]["initial_parameters"]["task"][
                "instructions"
            ] == "Queue job"

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

async def test_runtime_summary_uses_temporal_future_and_recent_actions(
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
            next_run = datetime(2026, 6, 24, 13, tzinfo=UTC)
            scheduled_for = datetime(2026, 6, 23, 13, tzinfo=UTC)
            mock_temporal_adapter.describe_schedule.return_value = SimpleNamespace(
                schedule=SimpleNamespace(state=SimpleNamespace(paused=False)),
                info=SimpleNamespace(
                    next_action_times=[next_run],
                    recent_actions=[
                        SimpleNamespace(
                            scheduled_at=scheduled_for,
                            started_at=scheduled_for,
                            action=SimpleNamespace(
                                workflow="MoonMind.UserWorkflow",
                                args=[],
                            ),
                        )
                    ],
                ),
            )

            summary = await service.runtime_summary_for_definition(definition)

            assert summary == RecurringScheduleRuntimeSummary(
                next_run_at=next_run,
                last_scheduled_for=scheduled_for,
                last_dispatch_status=RecurringWorkflowRunOutcome.ENQUEUED.value,
                last_dispatch_error=None,
            )

async def test_reconcile_repairs_existing_schedule_action_payload(
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
            mock_temporal_adapter.create_schedule.reset_mock()
            mock_temporal_adapter.update_schedule.reset_mock()
            mock_temporal_adapter.describe_schedule.return_value = SimpleNamespace(
                schedule=SimpleNamespace(
                    spec=SimpleNamespace(
                        cron_expressions=["0 6 * * *"],
                        time_zone_name="UTC",
                        jitter=timedelta(seconds=0),
                    ),
                    policy=SimpleNamespace(
                        overlap=SimpleNamespace(name="SKIP"),
                        catchup_window=timedelta(minutes=15),
                    ),
                    state=SimpleNamespace(paused=False, note="Daily Demo"),
                )
            )

            reconciled = await service.reconcile_schedules()

            assert reconciled == 1
            mock_temporal_adapter.update_schedule.assert_called_once()
            call_kwargs = mock_temporal_adapter.update_schedule.call_args.kwargs
            assert call_kwargs["definition_id"] == definition.id
            assert call_kwargs["cron_expression"] is None
            assert call_kwargs["workflow_type"] == "MoonMind.UserWorkflow"
            assert call_kwargs["workflow_input"]["workflow_type"] == (
                "MoonMind.UserWorkflow"
            )
            assert "workflowType" not in call_kwargs["workflow_input"]

async def test_reconcile_skips_update_when_metadata_and_action_match(
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
            _workflow_type, workflow_input = service._workflow_bundle_for_definition(
                definition
            )
            mock_temporal_adapter.create_schedule.reset_mock()
            mock_temporal_adapter.update_schedule.reset_mock()
            mock_temporal_adapter.describe_schedule.return_value = SimpleNamespace(
                schedule=SimpleNamespace(
                    spec=SimpleNamespace(
                        cron_expressions=["0 6 * * *"],
                        time_zone_name="UTC",
                        jitter=timedelta(seconds=0),
                    ),
                    policy=SimpleNamespace(
                        overlap=SimpleNamespace(name="SKIP"),
                        catchup_window=timedelta(minutes=15),
                    ),
                    state=SimpleNamespace(paused=False, note="Daily Demo"),
                    action=SimpleNamespace(
                        workflow="MoonMind.UserWorkflow",
                        args=[workflow_input],
                    ),
                )
            )

            reconciled = await service.reconcile_schedules()

            assert reconciled == 0
            mock_temporal_adapter.update_schedule.assert_not_called()

async def test_runtime_summaries_for_definitions_describes_concurrently(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=mock_temporal_adapter
            )
            first = SimpleNamespace(id=uuid4())
            second = SimpleNamespace(id=uuid4())
            first_summary = RecurringScheduleRuntimeSummary(last_dispatch_status="one")
            second_summary = RecurringScheduleRuntimeSummary(last_dispatch_status="two")
            service.runtime_summary_for_definition = AsyncMock(
                side_effect=[first_summary, second_summary]
            )

            summaries = await service.runtime_summaries_for_definitions(
                [first, second]
            )

            assert summaries == {
                first.id: first_summary,
                second.id: second_summary,
            }
            service.runtime_summary_for_definition.assert_has_awaits(
                [call(first), call(second)]
            )
