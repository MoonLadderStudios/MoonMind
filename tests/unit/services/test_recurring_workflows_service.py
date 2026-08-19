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
    OmnigentAgentProfile,
    OmnigentAgentProfileVersion,
    OmnigentOAuthHostBindingRecord,
    RecurringWorkflowDefinition,
    RecurringWorkflowRun,
    RecurringWorkflowRunOutcome,
)
from api_service.services.recurring_workflows_service import (
    RecurringScheduleRuntimeSummary,
    RecurringWorkflowConflictError,
    RecurringWorkflowsService,
    RecurringWorkflowValidationError,
)
from moonmind.workflows.temporal.client import ScheduleTriggerResult
from moonmind.workflows.temporal.schedule_mapping import make_scheduled_workflow_id_base
from moonmind.workflows.temporal.schedule_errors import ScheduleNotFoundError

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
    adapter.trigger_schedule = AsyncMock(return_value=ScheduleTriggerResult())
    adapter.delete_schedule = AsyncMock()
    adapter.describe_schedule = AsyncMock()
    adapter.resolve_workflow_task_queue = MagicMock(return_value="mm.workflow.user.v2")
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


async def test_create_definition_compiles_agent_profile_snapshot_separately(
    tmp_path: Path,
    mock_temporal_adapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": "omnigent-bootstrap-default",
        "version": 1,
        "digest": "sha256:" + "a" * 64,
        "providerProfileRef": "codex-openai-oauth",
        "executionProfileRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@1",
        "agentId": "upstream-codex-agent",
        "document": {
            "model": {"settings": {}},
            "rag": {},
            "capture": {"stream": True},
            "workspace": {"mutation": "allowed"},
        },
    }
    resolver = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(
        "api_service.services.recurring_workflows_service.resolve_agent_profile_snapshot",
        resolver,
    )

    async with recurring_db(tmp_path) as session_maker, session_maker() as session:
        service = RecurringWorkflowsService(
            session,
            temporal_client_adapter=mock_temporal_adapter,
        )
        definition = await service.create_definition(
            name="Profile schedule",
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
                    "targetRuntime": "omnigent",
                    "omnigent": {
                        "executionTargetRef": "omnigent-codex@1",
                        "launchPolicyRef": "codex-on-demand@1",
                    },
                    "workflow": {
                        "instructions": "Run the selected profile.",
                        "runtime": {"mode": "omnigent"},
                    },
                },
            },
            policy=None,
            agent_profile_selection={
                "profileId": "omnigent-bootstrap-default",
                "providerProfileRef": "codex-openai-oauth",
            },
            actor=SimpleNamespace(id=uuid4()),
        )

    initial_parameters = definition.target["initialParameters"]
    assert initial_parameters["agentProfileSnapshot"] == snapshot
    assert initial_parameters["omnigent"] == {
        "executionTargetRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@1",
    }
    assert "agentProfileRef" not in initial_parameters["omnigent"]
    assert "executionProfileRef" not in initial_parameters["omnigent"]
    scheduled_parameters = mock_temporal_adapter.create_schedule.await_args.kwargs[
        "workflow_input"
    ]["initial_parameters"]
    assert scheduled_parameters["agentProfileSnapshot"] == snapshot
    assert scheduled_parameters["omnigent"] == initial_parameters["omnigent"]


async def test_started_at_by_workflow_id_orders_duplicate_rows_deterministically() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = SimpleNamespace(all=lambda: [])
    service = RecurringWorkflowsService(session)

    assert await service.started_at_by_workflow_id(["wf-1"]) == {}

    statement = session.execute.await_args.args[0]
    assert "ORDER BY temporal_executions.started_at ASC" in str(statement)


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
            workflow_type, workflow_input = service._workflow_bundle_for_definition(
                definition
            )
            triggered_at = datetime.now(UTC)
            mock_temporal_adapter.describe_schedule.return_value = SimpleNamespace(
                schedule=SimpleNamespace(
                    action=SimpleNamespace(
                        workflow=workflow_type,
                        id=make_scheduled_workflow_id_base(definition.id),
                        args=[workflow_input],
                        task_queue="mm.workflow.user.v2",
                    )
                )
            )
            mock_temporal_adapter.trigger_schedule.return_value = ScheduleTriggerResult(
                scheduled_at=triggered_at,
                started_at=triggered_at,
                workflow_id="workflow-from-trigger",
                run_id="run-from-trigger",
            )

            run = await service.create_manual_run(definition)

            mock_temporal_adapter.trigger_schedule.assert_called_once_with(definition_id=definition.id)
            assert run.outcome == RecurringWorkflowRunOutcome.ENQUEUED
            assert run.scheduled_for.replace(tzinfo=UTC) == triggered_at
            assert run.temporal_workflow_id == "workflow-from-trigger"
            assert run.temporal_run_id == "run-from-trigger"
            assert run.message == "Triggered Temporal workflow workflow-from-trigger"
            assert definition.last_scheduled_for.replace(tzinfo=UTC) == triggered_at
            assert definition.last_dispatch_status == "enqueued"
            mock_temporal_adapter.update_schedule.assert_not_called()

async def test_create_manual_run_repairs_legacy_task_queue_before_trigger(
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
            workflow_type, workflow_input = service._workflow_bundle_for_definition(
                definition
            )
            mock_temporal_adapter.update_schedule.reset_mock()
            mock_temporal_adapter.describe_schedule.return_value = SimpleNamespace(
                schedule=SimpleNamespace(
                    action=SimpleNamespace(
                        workflow=workflow_type,
                        args=[workflow_input],
                        task_queue="mm.workflow",
                    )
                )
            )

            await service.create_manual_run(definition)

            mock_temporal_adapter.update_schedule.assert_called_once()
            call_kwargs = mock_temporal_adapter.update_schedule.call_args.kwargs
            assert call_kwargs["definition_id"] == definition.id
            assert call_kwargs["workflow_type"] == workflow_type
            assert call_kwargs["workflow_input"] == workflow_input
            mock_temporal_adapter.trigger_schedule.assert_called_once_with(
                definition_id=definition.id
            )

async def test_delete_definition_deletes_temporal_schedule_and_db_row(
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

            await service.delete_definition(definition)

            mock_temporal_adapter.delete_schedule.assert_called_once_with(
                definition_id=definition.id
            )
            result = await session.execute(
                select(RecurringWorkflowDefinition).where(
                    RecurringWorkflowDefinition.id == definition.id
                )
            )
            assert result.scalars().first() is None

async def test_delete_definition_removes_db_row_when_temporal_schedule_is_missing(
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
            mock_temporal_adapter.delete_schedule.side_effect = ScheduleNotFoundError(
                "missing"
            )

            await service.delete_definition(definition)

            result = await session.execute(
                select(RecurringWorkflowDefinition).where(
                    RecurringWorkflowDefinition.id == definition.id
                )
            )
            assert result.scalars().first() is None

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


async def test_managed_bootstrap_policy_cutover_refreshes_schedule_action(
    tmp_path: Path,
    mock_temporal_adapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_digest = "sha256:" + "3" * 64
    new_digest = "sha256:" + "4" * 64
    old_snapshot = {
        "profileId": "omnigent-bootstrap-default",
        "version": 2,
        "digest": old_digest,
        "providerProfileRef": "codex_openai_oauth",
        "executionProfileRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@3",
        "document": {"model": {}, "capture": {}, "rag": {}, "publish": {}},
    }
    new_snapshot = {
        **old_snapshot,
        "version": 3,
        "digest": new_digest,
        "launchPolicyRef": "codex-on-demand@4",
    }
    refresh_calls: list[dict[str, object]] = []

    async def refresh_snapshot(
        session,
        *,
        parameters,
        consumer_type,
        consumer_id,
        user,
        replace_existing_usage,
    ):
        refresh_calls.append(
            {
                "consumerType": consumer_type,
                "consumerId": consumer_id,
                "replaceExistingUsage": replace_existing_usage,
            }
        )
        return {
            **dict(parameters),
            "agentProfile": {
                "profileId": "omnigent-bootstrap-default",
                "version": 3,
                "digest": new_digest,
            },
            "agentProfileSnapshot": new_snapshot,
            "omnigent": {
                "executionTargetRef": "omnigent-codex@1",
                "launchPolicyRef": "codex-on-demand@4",
            },
        }

    monkeypatch.setattr(
        "api_service.services.recurring_workflows_service."
        "refresh_managed_bootstrap_snapshot",
        refresh_snapshot,
    )

    async with recurring_db(tmp_path) as session_maker, session_maker() as session:
        service = RecurringWorkflowsService(
            session,
            temporal_client_adapter=mock_temporal_adapter,
        )
        definition = await service.create_definition(
            name="Daily Dependabot Resolver",
            description=None,
            enabled=True,
            schedule_type="cron",
            cron="0 13 * * *",
            timezone="UTC",
            scope_type="personal",
            scope_ref=None,
            owner_user_id=uuid4(),
            target={
                "workflowType": "MoonMind.UserWorkflow",
                "initialParameters": {
                    "targetRuntime": "omnigent",
                    "agentProfile": {
                        "profileId": "omnigent-bootstrap-default",
                        "version": 2,
                        "digest": old_digest,
                    },
                    "agentProfileSnapshot": old_snapshot,
                    "omnigent": {
                        "executionTargetRef": "omnigent-codex@1",
                        "launchPolicyRef": "codex-on-demand@3",
                    },
                },
                "agentProfile": {
                    "profileId": "omnigent-bootstrap-default",
                    "version": 2,
                    "digest": old_digest,
                },
                "agentProfileSnapshot": old_snapshot,
            },
            policy={},
        )
        session.add(
            OmnigentAgentProfile(
                profile_id="omnigent-bootstrap-default",
                display_name="Codex via Omnigent",
                visibility="workspace",
                state="active",
                active_version=3,
                default_for_runtime=True,
            )
        )
        session.add(
            OmnigentAgentProfileVersion(
                profile_id="omnigent-bootstrap-default",
                version=3,
                digest=new_digest,
                document={
                    "execution": {
                        "allowedLaunchPolicyRefs": ["codex-on-demand@4"],
                    }
                },
                validation_result={"ready": True},
            )
        )
        binding = OmnigentOAuthHostBindingRecord(
            binding_ref="codex-openai-oauth-host",
            provider_profile_id="codex_openai_oauth",
            endpoint_ref="default",
            harness="codex-native",
            credential_mount_template_json={
                "authVolumeRef": {
                    "providerProfileId": "codex_openai_oauth",
                    "runtimeId": "codex_cli",
                    "providerId": "openai",
                    "volumeRef": "codex_auth_volume",
                    "credentialGeneration": 1,
                    "ownerUserId": "user-1",
                },
                "targetPath": "/home/app/.codex",
                "accessMode": "read_write",
                "runtimeUid": 1000,
                "runtimeGid": 1000,
            },
            launch_policy_ref="codex-on-demand@3",
        )
        session.add(binding)
        await session.commit()

        old_workflow_type, old_workflow_input = (
            service._workflow_bundle_for_definition(definition)
        )
        mock_temporal_adapter.create_schedule.reset_mock()
        mock_temporal_adapter.update_schedule.reset_mock()
        mock_temporal_adapter.describe_schedule.return_value = SimpleNamespace(
            schedule=SimpleNamespace(
                action=SimpleNamespace(
                    workflow=old_workflow_type,
                    id=make_scheduled_workflow_id_base(definition.id),
                    args=[old_workflow_input],
                    task_queue="mm.workflow.user.v2",
                )
            )
        )

        assert await service.refresh_managed_bootstrap_schedules() == 0
        mock_temporal_adapter.update_schedule.assert_not_called()
        assert refresh_calls == []

        binding.launch_policy_ref = "codex-on-demand@4"
        await session.commit()

        assert await service.refresh_managed_bootstrap_schedules() == 1

        await session.refresh(definition)
        assert definition.version == 2
        assert definition.target["agentProfile"]["version"] == 3
        assert definition.target["initialParameters"]["omnigent"] == {
            "executionTargetRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@4",
        }
        assert refresh_calls == [
            {
                "consumerType": "schedule",
                "consumerId": str(definition.id),
                "replaceExistingUsage": True,
            }
        ]
        update = mock_temporal_adapter.update_schedule.call_args.kwargs
        assert update["workflow_input"]["initial_parameters"]["omnigent"][
            "launchPolicyRef"
        ] == "codex-on-demand@4"


async def test_managed_bootstrap_refresh_contains_individual_failures(
    tmp_path: Path,
    mock_temporal_adapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with recurring_db(tmp_path) as session_maker, session_maker() as session:
        service = RecurringWorkflowsService(
            session,
            temporal_client_adapter=mock_temporal_adapter,
        )
        definitions = []
        for name in ("Broken", "Healthy"):
            definitions.append(
                await service.create_definition(
                    name=name,
                    description=None,
                    enabled=True,
                    schedule_type="cron",
                    cron="0 13 * * *",
                    timezone="UTC",
                    scope_type="personal",
                    scope_ref=None,
                    owner_user_id=uuid4(),
                    target={
                        "workflowType": "MoonMind.UserWorkflow",
                        "initialParameters": {"task": {"instructions": name}},
                    },
                    policy={},
                )
            )
        definition_ids = [definition.id for definition in definitions]

        visited: list[object] = []

        async def refresh_target(definition):
            visited.append(definition.id)
            if definition.id == definition_ids[0]:
                raise RecurringWorkflowValidationError("missing usage row")
            return True

        monkeypatch.setattr(service, "_refresh_managed_bootstrap_target", refresh_target)
        monkeypatch.setattr(
            service,
            "_ensure_schedule_action_current",
            AsyncMock(),
        )

        assert await service.refresh_managed_bootstrap_schedules() == 1
        assert set(visited) == set(definition_ids)


async def test_update_definition_rejects_stale_target_version(
    tmp_path: Path,
    mock_temporal_adapter,
) -> None:
    async with recurring_db(tmp_path) as session_maker, session_maker() as session:
        service = RecurringWorkflowsService(
            session,
            temporal_client_adapter=mock_temporal_adapter,
        )
        original_target = {
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {"omnigent": {"launchPolicyRef": "policy@1"}},
        }
        current_target = {
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {"omnigent": {"launchPolicyRef": "policy@2"}},
        }
        definition = await service.create_definition(
            name="Daily Resolver",
            description=None,
            enabled=True,
            schedule_type="cron",
            cron="0 13 * * *",
            timezone="UTC",
            scope_type="personal",
            scope_ref=None,
            owner_user_id=uuid4(),
            target=original_target,
            policy={},
        )
        definition.target = current_target
        definition.version = 2
        await session.commit()
        mock_temporal_adapter.update_schedule.reset_mock()

        with pytest.raises(RecurringWorkflowConflictError, match="refresh and retry"):
            await service.update_definition(
                definition,
                target=original_target,
                expected_version=1,
            )

        assert definition.target == current_target
        mock_temporal_adapter.update_schedule.assert_not_called()


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
                        id=make_scheduled_workflow_id_base(definition.id),
                        args=[workflow_input],
                        task_queue="mm.workflow.user.v2",
                    ),
                )
            )

            reconciled = await service.reconcile_schedules()

            assert reconciled == 0
            mock_temporal_adapter.update_schedule.assert_not_called()

async def test_reconcile_repairs_schedule_action_task_queue(
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
                        task_queue="mm.workflow",
                    ),
                )
            )

            reconciled = await service.reconcile_schedules()

            assert reconciled == 1
            mock_temporal_adapter.update_schedule.assert_called_once()
            call_kwargs = mock_temporal_adapter.update_schedule.call_args.kwargs
            assert call_kwargs["definition_id"] == definition.id
            assert call_kwargs["workflow_type"] == "MoonMind.UserWorkflow"
            assert call_kwargs["workflow_input"] == workflow_input

async def test_reconcile_repairs_literal_schedule_time_action_id(
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
                        id=f"mm:{definition.id}:{{{{.ScheduleTime}}}}",
                        args=[workflow_input],
                        task_queue="mm.workflow.user.v2",
                    ),
                )
            )

            reconciled = await service.reconcile_schedules()

            assert reconciled == 1
            mock_temporal_adapter.update_schedule.assert_called_once()
            call_kwargs = mock_temporal_adapter.update_schedule.call_args.kwargs
            assert call_kwargs["definition_id"] == definition.id
            assert call_kwargs["workflow_type"] == "MoonMind.UserWorkflow"
            assert call_kwargs["workflow_input"] == workflow_input

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
