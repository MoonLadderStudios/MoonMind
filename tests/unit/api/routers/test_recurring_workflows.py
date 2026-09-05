"""Router-level unit tests for recurring workflow endpoints."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers import recurring_workflows as recurring_router
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    RecurringWorkflowDefinition,
    RecurringWorkflowRunOutcome,
    RecurringWorkflowRunTrigger,
    RecurringWorkflowScheduleType,
    RecurringWorkflowScopeType,
)
from api_service.services.recurring_workflows_service import (
    RecurringScheduleRuntimeSummary,
    RecurringWorkflowConflictError,
    RecurringWorkflowsService,
    RecurringWorkflowValidationError,
)

LIST_DEFAULTS = {
    "cursor": None,
    "sort": "updatedAt",
    "sort_dir": "desc",
    "schedule": "",
    "state": "",
    "target": "",
    "repository": "",
    "cadence": "",
    "next_run": "",
    "last_scheduled": "",
    "dispatch": "",
    "updated": "",
}

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


def test_recurring_workflow_version_conflict_maps_to_409() -> None:
    exc = recurring_router._map_error(
        RecurringWorkflowConflictError("refresh and retry")
    )

    assert exc.status_code == status.HTTP_409_CONFLICT
    assert exc.detail == {
        "code": "recurring_workflow_version_conflict",
        "message": "refresh and retry",
    }


@pytest.mark.asyncio
async def test_list_recurring_workflows_global_requires_operator() -> None:
    service = AsyncMock()
    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    with pytest.raises(HTTPException) as exc:
        await recurring_router.list_recurring_workflows(
            scope="global",
            limit=50,
            **LIST_DEFAULTS,
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
    service.count_definitions.return_value = 1
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
        **LIST_DEFAULTS,
        service=service,
        user=user,
    )

    assert response.items[0].next_run_at == live_next_run
    assert response.items[0].last_scheduled_for == last_scheduled_for
    assert response.items[0].last_dispatch_status == "enqueued"
    assert response.items[0].permissions.can_edit is True
    assert response.items[0].permissions.can_run_now is True
    assert response.items[0].permissions.can_delete is True
    assert response.items[0].actions == response.items[0].permissions
    assert response.count == 1
    assert response.next_page_token is None
    assert response.active_count == 1
    service.runtime_summaries_for_definitions.assert_awaited_once_with([definition])
    service.list_definitions.assert_any_await(
        scope="personal",
        user_id=definition.owner_user_id,
        limit=50,
        offset=0,
    )
    service.list_definitions.assert_any_await(
        scope="personal",
        user_id=definition.owner_user_id,
        limit=500,
        offset=0,
    )

@pytest.mark.asyncio
async def test_list_recurring_workflows_filters_sorts_and_returns_opaque_cursor() -> None:
    service = AsyncMock()
    owner_id = uuid4()
    early = _definition(
        owner_user_id=owner_id,
        name="Nightly Repo A",
        target={
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {"repository": "MoonLadderStudios/MoonMind"},
        },
        updated_at=datetime(2026, 6, 20, 10, tzinfo=UTC),
    )
    late = _definition(
        owner_user_id=owner_id,
        name="Nightly Repo B",
        target={
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {"repository": "MoonLadderStudios/MoonMind"},
        },
        updated_at=datetime(2026, 6, 21, 10, tzinfo=UTC),
    )
    other = _definition(
        owner_user_id=owner_id,
        name="Other schedule",
        target={
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {"repository": "example/other"},
        },
    )
    service.list_definitions.return_value = [other, early, late]
    service.runtime_summaries_for_definitions.return_value = {}
    user = SimpleNamespace(id=owner_id, is_superuser=False)

    first_page = await recurring_router.list_recurring_workflows(
        scope="personal",
        limit=1,
        cursor=None,
        sort="updatedAt",
        sort_dir="asc",
        schedule="nightly",
        state="active",
        target="UserWorkflow",
        repository="MoonMind",
        cadence="",
        next_run="",
        last_scheduled="",
        dispatch="",
        updated="",
        service=service,
        user=user,
    )

    assert first_page.count == 2
    assert [item.name for item in first_page.items] == ["Nightly Repo A"]
    assert first_page.next_page_token

    second_page = await recurring_router.list_recurring_workflows(
        scope="personal",
        limit=1,
        cursor=first_page.next_page_token,
        sort="updatedAt",
        sort_dir="asc",
        schedule="nightly",
        state="active",
        target="UserWorkflow",
        repository="MoonMind",
        cadence="",
        next_run="",
        last_scheduled="",
        dispatch="",
        updated="",
        service=service,
        user=user,
    )

    assert second_page.count == 2
    assert [item.name for item in second_page.items] == ["Nightly Repo B"]
    assert second_page.next_page_token is None

@pytest.mark.asyncio
async def test_create_recurring_workflow_returns_serialized_definition() -> None:
    service = AsyncMock()
    user = SimpleNamespace(id=uuid4(), is_superuser=False)
    definition = _definition(
        owner_user_id=user.id,
        temporal_schedule_id="mm-schedule:test-definition",
    )
    service.create_definition.return_value = definition

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
    assert response.permissions.can_edit is True
    assert response.permissions.can_run_now is True
    assert response.permissions.can_delete is True
    assert response.permissions.disabled_reasons == {}
    service.create_definition.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_recurring_workflow_serializes_action_permissions() -> None:
    service = AsyncMock()
    user = SimpleNamespace(id=uuid4(), is_superuser=False)
    definition = _definition(owner_user_id=user.id)
    service.require_authorized_definition.return_value = definition
    service.runtime_summary_for_definition.return_value = None

    response = await recurring_router.get_recurring_workflow(
        definition_id=definition.id,
        service=service,
        user=user,
    )

    assert response.permissions.can_edit is True
    assert response.permissions.can_run_now is True
    assert response.permissions.can_delete is True
    assert response.actions == response.permissions

@pytest.mark.asyncio
async def test_global_recurring_workflow_action_permissions_require_operator() -> None:
    definition = _definition(
        scope_type=RecurringWorkflowScopeType.GLOBAL,
        owner_user_id=None,
    )
    operator = SimpleNamespace(id=uuid4(), is_superuser=True)
    viewer = SimpleNamespace(id=uuid4(), is_superuser=False)

    operator_permissions = recurring_router._action_permissions_for_definition(
        definition,
        user=operator,
    )
    viewer_permissions = recurring_router._action_permissions_for_definition(
        definition,
        user=viewer,
    )

    assert operator_permissions.can_edit is True
    assert operator_permissions.can_run_now is True
    assert viewer_permissions.can_edit is False
    assert viewer_permissions.can_run_now is False
    assert viewer_permissions.can_delete is False
    assert viewer_permissions.disabled_reasons["canEdit"] == (
        "Operator privileges are required to manage global schedules."
    )
    assert viewer_permissions.disabled_reasons["canDelete"] == (
        "Operator privileges are required to manage global schedules."
    )

@pytest.mark.asyncio
async def test_run_recurring_workflow_now_returns_run_row() -> None:
    service = AsyncMock()
    definition = _definition()
    run_row = _run(
        definition_id=definition.id,
        temporal_workflow_id="workflow-from-schedule",
    )
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
    assert response.started_at is None
    service.create_manual_run.assert_awaited_once_with(definition)

@pytest.mark.asyncio
async def test_delete_recurring_workflow_deletes_authorized_definition() -> None:
    service = AsyncMock()
    definition = _definition()
    service.require_authorized_definition.return_value = definition
    user = SimpleNamespace(id=definition.owner_user_id, is_superuser=False)

    response = await recurring_router.delete_recurring_workflow(
        definition_id=definition.id,
        service=service,
        user=user,
    )

    assert response.status_code == 204
    service.require_authorized_definition.assert_awaited_once_with(
        definition_id=definition.id,
        user_id=user.id,
        can_manage_global=False,
    )
    service.delete_definition.assert_awaited_once_with(definition)

@pytest.mark.asyncio
async def test_list_recurring_workflow_runs_hydrates_actual_start_time() -> None:
    service = AsyncMock()
    definition = _definition()
    started_at = datetime(2026, 6, 24, 2, 0, 2, tzinfo=UTC)
    run_row = _run(
        definition_id=definition.id,
        temporal_workflow_id="workflow-from-schedule",
    )
    service.require_authorized_definition.return_value = definition
    service.list_runs.return_value = [run_row]
    service.started_at_by_workflow_id.return_value = {
        "workflow-from-schedule": started_at
    }
    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    response = await recurring_router.list_recurring_workflow_runs(
        definition_id=definition.id,
        limit=200,
        service=service,
        user=user,
    )

    assert response.items[0].temporal_workflow_id == "workflow-from-schedule"
    assert response.items[0].started_at == started_at
    service.started_at_by_workflow_id.assert_awaited_once()


# ---------------------------------------------------------------------------
# MoonLadderStudios/MoonMind#3788 — the recurring-workflows routes are a launch
# authoring boundary: the target they persist is what a later schedule action
# launches, so a mismatched runtime/Provider Profile pair must be rejected here
# with the same 409 contract the execution-submission path raises.
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _mm3788_session(tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'recurring_routes.db'}", future=True
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_maker() as session:
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
            yield session
    finally:
        await engine.dispose()


def _mm3788_service(session) -> RecurringWorkflowsService:
    adapter = MagicMock()
    adapter.create_schedule = AsyncMock(return_value="mm-schedule:id")
    adapter.update_schedule = AsyncMock()
    adapter.pause_schedule = AsyncMock()
    adapter.unpause_schedule = AsyncMock()
    adapter.delete_schedule = AsyncMock()
    adapter.resolve_workflow_task_queue = MagicMock(return_value="mm.workflow.user.v2")
    return RecurringWorkflowsService(session, temporal_client_adapter=adapter)


def _mm3788_route_target(*, target_runtime: str, profile_id: str) -> dict:
    return {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": {
            "targetRuntime": target_runtime,
            "workflow": {
                "instructions": "Run the nightly resolver.",
                "runtime": {"mode": target_runtime, "providerProfileRef": profile_id},
            },
        },
    }


def _mm3788_create_payload(target: dict) -> recurring_router.CreateRecurringWorkflowRequest:
    return recurring_router.CreateRecurringWorkflowRequest(
        name="MM-3788 schedule",
        cron="0 6 * * *",
        timezone="UTC",
        scopeType="personal",
        target=target,
        policy={},
    )


async def _mm3788_create_owned(service, user):
    return await recurring_router.create_recurring_workflow(
        payload=_mm3788_create_payload(
            _mm3788_route_target(
                target_runtime="claude_code", profile_id="claude_minimax_team"
            )
        ),
        service=service,
        user=user,
    )


@pytest.mark.asyncio
async def test_mm3788_create_route_rejects_a_cross_runtime_provider_profile(
    tmp_path: Path,
) -> None:
    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    async with _mm3788_session(tmp_path) as session:
        service = _mm3788_service(session)

        with pytest.raises(HTTPException) as excinfo:
            await recurring_router.create_recurring_workflow(
                payload=_mm3788_create_payload(
                    _mm3788_route_target(
                        target_runtime="claude_code",
                        profile_id="codex_minimax_team",
                    )
                ),
                service=service,
                user=user,
            )

        assert excinfo.value.status_code == status.HTTP_409_CONFLICT
        detail = excinfo.value.detail
        assert detail["code"] == "provider_profile_runtime_mismatch"
        assert detail["profileId"] == "codex_minimax_team"
        assert detail["profileRuntime"] == "codex_cli"
        assert detail["selectedRuntime"] == "claude_code"
        assert "codex_minimax_team" in detail["message"]
        assert "claude_code" in detail["message"]
        # Nothing was persisted by the rejected request.
        await session.rollback()
        stored = (
            await session.execute(select(RecurringWorkflowDefinition))
        ).scalars().all()
        assert stored == []


@pytest.mark.asyncio
async def test_mm3788_create_route_accepts_a_profile_owned_by_the_target_runtime(
    tmp_path: Path,
) -> None:
    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    async with _mm3788_session(tmp_path) as session:
        created = await _mm3788_create_owned(_mm3788_service(session), user)

        assert created.target["initialParameters"]["workflow"]["runtime"][
            "providerProfileRef"
        ] == "claude_minimax_team"
        stored = (
            await session.execute(select(RecurringWorkflowDefinition))
        ).scalars().all()
        assert [item.id for item in stored] == [created.id]


@pytest.mark.asyncio
async def test_create_route_preserves_missing_execution_configuration_error(
    tmp_path: Path,
) -> None:
    """Omnigent selection rejects missing authority before schedule persistence."""

    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    async with _mm3788_session(tmp_path) as session:
        service = _mm3788_service(session)
        with pytest.raises(HTTPException) as excinfo:
            await recurring_router.create_recurring_workflow(
                payload=_mm3788_create_payload(
                    _mm3788_route_target(
                        target_runtime="omnigent", profile_id="codex_minimax_team"
                    )
                ),
                service=service,
                user=user,
            )

        assert excinfo.value.status_code == status.HTTP_409_CONFLICT
        assert excinfo.value.detail["code"] == "profile_execution_configuration_required"
        assert excinfo.value.detail["profileId"] == "codex_minimax_team"
        service._adapter.create_schedule.assert_not_awaited()
        # Match the failed request's session cleanup before reading durable state.
        await session.rollback()
        stored = (
            await session.execute(select(RecurringWorkflowDefinition))
        ).scalars().all()
        assert stored == []


@pytest.mark.asyncio
async def test_mm3788_update_route_rejects_a_cross_runtime_provider_profile(
    tmp_path: Path,
) -> None:
    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    async with _mm3788_session(tmp_path) as session:
        created = await _mm3788_create_owned(_mm3788_service(session), user)
        definition_id = created.id
        stored_target = dict(created.target)

        with pytest.raises(HTTPException) as excinfo:
            await recurring_router.update_recurring_workflow(
                definition_id=definition_id,
                payload=recurring_router.UpdateRecurringWorkflowRequest(
                    name="Renamed by a rejected edit",
                    target=_mm3788_route_target(
                        target_runtime="claude_code",
                        profile_id="codex_minimax_team",
                    ),
                    version=created.version,
                ),
                service=_mm3788_service(session),
                user=user,
            )

        assert excinfo.value.status_code == status.HTTP_409_CONFLICT
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
        # The stored target survived the rejected edit unchanged.
        await session.rollback()
        reloaded = await session.get(
            RecurringWorkflowDefinition, definition_id, populate_existing=True
        )
        assert reloaded.target == stored_target
        assert reloaded.name == "MM-3788 schedule"


@pytest.mark.asyncio
async def test_mm3788_update_route_accepts_a_profile_owned_by_the_target_runtime(
    tmp_path: Path,
) -> None:
    user = SimpleNamespace(id=uuid4(), is_superuser=False)

    async with _mm3788_session(tmp_path) as session:
        created = await _mm3788_create_owned(_mm3788_service(session), user)

        updated = await recurring_router.update_recurring_workflow(
            definition_id=created.id,
            payload=recurring_router.UpdateRecurringWorkflowRequest(
                name="Renamed by an accepted edit",
                target=_mm3788_route_target(
                    target_runtime="claude_code",
                    profile_id="claude_minimax_team",
                ),
                version=created.version,
            ),
            service=_mm3788_service(session),
            user=user,
        )

        assert updated.name == "Renamed by an accepted edit"
        assert updated.target["initialParameters"]["workflow"]["runtime"][
            "providerProfileRef"
        ] == "claude_minimax_team"
