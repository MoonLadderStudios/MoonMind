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
    ManagedAgentProviderProfile,
    OmnigentAgentProfile,
    OmnigentAgentProfileVersion,
    OmnigentOAuthHostBindingRecord,
    RecurringWorkflowDefinition,
    RecurringWorkflowRun,
    RecurringWorkflowRunOutcome,
)
from api_service.services.provider_profile_runtime import (
    ProviderProfileRuntimeMismatchError,
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


@pytest.mark.parametrize("explicit_configuration", [False, True])
async def test_create_definition_compiles_agent_profile_snapshot_separately(
    explicit_configuration,
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
    default_resolver = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(
        "api_service.services.recurring_workflows_service.resolve_default_agent_profile_snapshot",
        default_resolver,
    )
    plan_binding = {
        "planRef": "omnigent-execution-plan:sha256:" + "b" * 64,
        "planDigest": "sha256:" + "b" * 64,
        "planArtifactRef": "art_plan",
        "taskInputSnapshotRef": "art_task",
        "taskInputSnapshotDigest": "sha256:" + "c" * 64,
    }
    compile_plan = AsyncMock(
        return_value=SimpleNamespace(
            binding=SimpleNamespace(
                model_dump=lambda **_kwargs: dict(plan_binding)
            ),
            artifact_refs=("art_profile", "art_skills", "art_plan"),
            resolved_skillset_ref="art_skills",
        )
    )
    monkeypatch.setattr(
        "api_service.services.omnigent_execution_plan_service."
        "compile_and_persist_execution_plan",
        compile_plan,
    )
    monkeypatch.setattr(
        "api_service.services.omnigent_execution_plan_service."
        "persist_json_artifact",
        AsyncMock(return_value=("art_task", "sha256:" + "c" * 64)),
    )

    async with recurring_db(tmp_path) as session_maker, session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="codex-openai-oauth",
                runtime_id="codex_cli",
                provider_id="openai",
            )
        )
        await session.flush()
        service = RecurringWorkflowsService(
            session,
            temporal_client_adapter=mock_temporal_adapter,
            artifact_service=SimpleNamespace(),
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
                        "runtime": {"mode": "omnigent", "profileId": "codex-openai-oauth"},
                    },
                },
            },
            policy=None,
            agent_profile_selection={
                "profileId": "omnigent-bootstrap-default",
                "providerProfileRef": "codex-openai-oauth",
            } if explicit_configuration else None,
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
    assert initial_parameters["omnigentExecutionPlan"] == plan_binding
    assert initial_parameters["resolvedSkillsetRef"] == "art_skills"
    assert compile_plan.await_count == 1
    if not explicit_configuration:
        assert default_resolver.await_args.kwargs["provider_profile_ref"] == "codex-openai-oauth"
        resolver.assert_not_awaited()
    scheduled_parameters = mock_temporal_adapter.create_schedule.await_args.kwargs[
        "workflow_input"
    ]["initial_parameters"]
    assert scheduled_parameters["agentProfileSnapshot"] == snapshot
    assert scheduled_parameters["omnigent"] == initial_parameters["omnigent"]
    assert scheduled_parameters["omnigentExecutionPlan"] == plan_binding


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


async def test_update_definition_cannot_replace_recorded_omnigent_plan(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    plan = {
        "planRef": "omnigent-execution-plan:sha256:" + "1" * 64,
        "planDigest": "sha256:" + "1" * 64,
        "planArtifactRef": "art_plan",
        "taskInputSnapshotRef": "art_task",
        "taskInputSnapshotDigest": "sha256:" + "2" * 64,
    }
    async with recurring_db(tmp_path) as session_maker, session_maker() as session:
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=mock_temporal_adapter
        )
        definition = await service.create_definition(
            name="Immutable Omnigent schedule",
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
                    "omnigentExecutionPlan": plan,
                },
            },
            policy={},
        )

        with pytest.raises(
            RecurringWorkflowValidationError, match="replacement plan"
        ):
            await service.update_definition(
                definition,
                target={
                    "workflowType": "MoonMind.UserWorkflow",
                    "initialParameters": {"targetRuntime": "omnigent"},
                },
            )


async def test_managed_refresh_recompiles_recorded_omnigent_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecurringWorkflowsService(AsyncMock(spec=AsyncSession))
    definition = SimpleNamespace(
        target={
            "initialParameters": {
                "omnigentExecutionPlan": {
                    "planRef": "omnigent-execution-plan:sha256:" + "3" * 64
                }
            }
        }
    )
    refresh = AsyncMock(return_value=True)
    monkeypatch.setattr(
        service,
        "_refresh_omnigent_execution_plan_target",
        refresh,
    )

    assert await service._refresh_managed_bootstrap_target(definition) is True
    refresh.assert_awaited_once()


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


# ---------------------------------------------------------------------------
# MoonLadderStudios/MoonMind#3788 — a recurring schedule persists the target
# whose initialParameters a later schedule action launches, so the
# runtime-owned Provider Profile invariant is enforced before that target is
# stored, not only on the direct execution-submission path.
# ---------------------------------------------------------------------------


def _mm3788_target(
    *,
    target_runtime: str,
    profile_id: str,
    profile_key: str = "providerProfileRef",
    in_runtime_block: bool = True,
) -> dict[str, object]:
    runtime_block: dict[str, object] = {"mode": target_runtime}
    initial_parameters: dict[str, object] = {
        "targetRuntime": target_runtime,
        "workflow": {
            "instructions": "Run the nightly resolver.",
            "runtime": runtime_block,
        },
    }
    if in_runtime_block:
        runtime_block[profile_key] = profile_id
    else:
        initial_parameters[profile_key] = profile_id
    return {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": initial_parameters,
    }


async def _mm3788_create(
    service: RecurringWorkflowsService,
    *,
    target: dict[str, object],
    owner_user_id,
    name: str = "MM-3788 schedule",
) -> RecurringWorkflowDefinition:
    return await service.create_definition(
        name=name,
        description=None,
        enabled=True,
        schedule_type="cron",
        cron="0 6 * * *",
        timezone="UTC",
        scope_type="personal",
        scope_ref=None,
        owner_user_id=owner_user_id,
        target=target,
        policy=None,
    )


async def test_mm3788_create_definition_rejects_provider_profile_from_another_runtime(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    async with recurring_db(tmp_path) as session_maker, session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="codex_minimax_team",
                runtime_id="codex_cli",
                provider_id="minimax",
            )
        )
        await session.flush()
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=mock_temporal_adapter
        )

        with pytest.raises(ProviderProfileRuntimeMismatchError) as excinfo:
            await _mm3788_create(
                service,
                target=_mm3788_target(
                    target_runtime="claude_code", profile_id="codex_minimax_team"
                ),
                owner_user_id=uuid4(),
            )

        assert excinfo.value.detail == {
            "code": "provider_profile_runtime_mismatch",
            "message": (
                "Provider Profile 'codex_minimax_team' belongs to runtime "
                "'codex_cli' and cannot be used with runtime 'claude_code'."
            ),
            "profileId": "codex_minimax_team",
            "profileRuntime": "codex_cli",
            "selectedRuntime": "claude_code",
        }
        # Nothing was persisted and no Temporal schedule was created.
        mock_temporal_adapter.create_schedule.assert_not_awaited()
        stored = (
            await session.execute(select(RecurringWorkflowDefinition))
        ).scalars().all()
        assert stored == []


async def test_mm3788_create_definition_accepts_provider_profile_owned_by_the_runtime(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    async with recurring_db(tmp_path) as session_maker, session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="claude_minimax_team",
                runtime_id="claude_code",
                provider_id="minimax",
            )
        )
        await session.flush()
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=mock_temporal_adapter
        )

        definition = await _mm3788_create(
            service,
            target=_mm3788_target(
                target_runtime="claude_code", profile_id="claude_minimax_team"
            ),
            owner_user_id=uuid4(),
        )

        runtime_block = definition.target["initialParameters"]["workflow"]["runtime"]
        assert runtime_block["providerProfileRef"] == "claude_minimax_team"
        mock_temporal_adapter.create_schedule.assert_awaited_once()


async def test_mm3788_create_definition_rejects_a_legacy_runtime_alias_mismatch(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    """Canonical runtime IDs decide the comparison, not the authored spelling."""

    async with recurring_db(tmp_path) as session_maker, session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="claude_minimax_team",
                runtime_id="claude_code",
                provider_id="minimax",
            )
        )
        await session.flush()
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=mock_temporal_adapter
        )

        with pytest.raises(ProviderProfileRuntimeMismatchError) as excinfo:
            await _mm3788_create(
                service,
                # ``profileId`` on initialParameters is the alias the raw target
                # JSON editor produces; ``codex`` normalizes to ``codex_cli``.
                target=_mm3788_target(
                    target_runtime="codex",
                    profile_id="claude_minimax_team",
                    profile_key="profileId",
                    in_runtime_block=False,
                ),
                owner_user_id=uuid4(),
            )

        assert excinfo.value.selected_runtime == "codex_cli"
        assert excinfo.value.profile_runtime == "claude_code"
        mock_temporal_adapter.create_schedule.assert_not_awaited()


async def test_mm3788_create_definition_leaves_omnigent_compatibility_to_selection(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    """``omnigent`` is a facade: the profile stays owned by a managed runtime."""

    async with recurring_db(tmp_path) as session_maker, session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="codex_minimax_team",
                runtime_id="codex_cli",
                provider_id="minimax",
            )
        )
        await session.flush()
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=mock_temporal_adapter
        )

        with pytest.raises(RecurringWorkflowValidationError, match="authenticated actor"):
            await _mm3788_create(
                service,
                target=_mm3788_target(
                    target_runtime="omnigent", profile_id="codex_minimax_team"
                ),
                owner_user_id=uuid4(),
            )
        mock_temporal_adapter.create_schedule.assert_not_awaited()


async def test_mm3788_update_definition_rejects_a_cross_runtime_target(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    async with recurring_db(tmp_path) as session_maker, session_maker() as session:
        session.add_all(
            [
                ManagedAgentProviderProfile(
                    profile_id="codex_minimax_team",
                    runtime_id="codex_cli",
                    provider_id="minimax",
                ),
                ManagedAgentProviderProfile(
                    profile_id="claude_minimax_team",
                    runtime_id="claude_code",
                    provider_id="minimax",
                ),
            ]
        )
        await session.flush()
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=mock_temporal_adapter
        )
        owned_target = _mm3788_target(
            target_runtime="claude_code", profile_id="claude_minimax_team"
        )
        definition = await _mm3788_create(
            service, target=owned_target, owner_user_id=uuid4()
        )
        definition_id = definition.id
        stored_target = dict(definition.target)

        with pytest.raises(ProviderProfileRuntimeMismatchError) as excinfo:
            await service.update_definition(
                definition,
                name="Renamed by a rejected edit",
                target=_mm3788_target(
                    target_runtime="claude_code", profile_id="codex_minimax_team"
                ),
                expected_version=definition.version,
            )

        assert excinfo.value.detail["profileId"] == "codex_minimax_team"
        assert excinfo.value.detail["profileRuntime"] == "codex_cli"
        assert excinfo.value.detail["selectedRuntime"] == "claude_code"
        mock_temporal_adapter.update_schedule.assert_not_awaited()
        # The rejected edit left the stored definition untouched.
        await session.rollback()
        reloaded = await session.get(
            RecurringWorkflowDefinition,
            definition_id,
            populate_existing=True,
        )
        assert reloaded.target == stored_target
        assert reloaded.name == "MM-3788 schedule"


async def test_mm3788_update_definition_accepts_a_target_owned_by_its_runtime(
    tmp_path: Path, mock_temporal_adapter
) -> None:
    async with recurring_db(tmp_path) as session_maker, session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="claude_minimax_team",
                runtime_id="claude_code",
                provider_id="minimax",
            )
        )
        await session.flush()
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=mock_temporal_adapter
        )
        definition = await _mm3788_create(
            service,
            target=_mm3788_target(
                target_runtime="claude_code", profile_id="claude_minimax_team"
            ),
            owner_user_id=uuid4(),
        )

        updated = await service.update_definition(
            definition,
            target=_mm3788_target(
                target_runtime="claude_code",
                profile_id="claude_minimax_team",
                profile_key="profileId",
                in_runtime_block=False,
            ),
            expected_version=definition.version,
        )

        parameters = updated.target["initialParameters"]
        assert parameters["profileId"] == "claude_minimax_team"
        mock_temporal_adapter.update_schedule.assert_awaited_once()
