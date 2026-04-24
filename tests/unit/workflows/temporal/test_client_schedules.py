"""Unit tests for Temporal Schedule CRUD on TemporalClientAdapter.

Covers T010 (create_schedule), T016 (describe/update/pause/unpause/trigger/delete),
and T017 (SDK error wrapping).
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from temporalio.client import ScheduleOverlapPolicy, ScheduleUpdate

from moonmind.workflows.temporal.client import (
    MANAGED_SESSION_RECONCILE_SCHEDULE_ID,
    MANAGED_SESSION_RECONCILE_WORKFLOW_ID_TEMPLATE,
    TemporalClientAdapter,
)
from moonmind.workflows.temporal.schedule_errors import (
    ScheduleAlreadyExistsError,
    ScheduleNotFoundError,
    ScheduleOperationError,
)

_TEST_UUID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_SCHEDULE_ID = f"mm-schedule:{_TEST_UUID}"

def _make_adapter(client: Any) -> TemporalClientAdapter:
    """Create an adapter with a pre-injected mock client and worker topology."""
    adapter = TemporalClientAdapter(client=client)
    # Pre-set task queue so _get_task_queue() doesn't require settings
    adapter._workflow_topology = SimpleNamespace(task_queues=["mm.workflow"])
    return adapter

def _mock_schedule_handle(*, describe_side_effect: Exception | None = None) -> MagicMock:
    """Build a mock ScheduleHandle with async methods."""
    handle = MagicMock()
    if describe_side_effect:
        handle.describe = AsyncMock(side_effect=describe_side_effect)
    else:
        handle.describe = AsyncMock(return_value=MagicMock())
    handle.pause = AsyncMock()
    handle.unpause = AsyncMock()
    handle.trigger = AsyncMock()
    handle.delete = AsyncMock()
    handle.update = AsyncMock()
    return handle

# ---------------------------------------------------------------------------
# T010: create_schedule
# ---------------------------------------------------------------------------

class TestCreateSchedule:
    """DOC-REQ-001: create Temporal Schedule via SDK."""

    @pytest.mark.asyncio
    async def test_creates_schedule_with_correct_id(self) -> None:
        mock_handle = MagicMock()
        mock_handle.id = _SCHEDULE_ID
        mock_client = MagicMock()
        mock_client.create_schedule = AsyncMock(return_value=mock_handle)

        adapter = _make_adapter(mock_client)
        result = await adapter.create_schedule(
            definition_id=_TEST_UUID,
            cron_expression="0 0 * * *",
            workflow_type="MoonMind.Run",
        )

        assert result == _SCHEDULE_ID
        mock_client.create_schedule.assert_awaited_once()
        call_args = mock_client.create_schedule.call_args
        assert call_args[0][0] == _SCHEDULE_ID  # first positional arg
        
        schedule_arg = call_args[0][1]
        assert schedule_arg.action.id == f"mm:{_TEST_UUID}:{{{{.ScheduleTime}}}}"

    @pytest.mark.asyncio
    async def test_overlap_policy_passed_through(self) -> None:

        mock_handle = MagicMock()
        mock_handle.id = _SCHEDULE_ID
        mock_client = MagicMock()
        mock_client.create_schedule = AsyncMock(return_value=mock_handle)

        adapter = _make_adapter(mock_client)
        await adapter.create_schedule(
            definition_id=_TEST_UUID,
            cron_expression="0 0 * * *",
            overlap_mode="allow",
            workflow_type="MoonMind.Run",
        )

        schedule_arg = mock_client.create_schedule.call_args[0][1]
        assert schedule_arg.policy.overlap == ScheduleOverlapPolicy.ALLOW_ALL

    @pytest.mark.asyncio
    async def test_catchup_policy_passed_through(self) -> None:

        mock_handle = MagicMock()
        mock_handle.id = _SCHEDULE_ID
        mock_client = MagicMock()
        mock_client.create_schedule = AsyncMock(return_value=mock_handle)

        adapter = _make_adapter(mock_client)
        await adapter.create_schedule(
            definition_id=_TEST_UUID,
            cron_expression="0 0 * * *",
            catchup_mode="all",
            workflow_type="MoonMind.Run",
        )

        schedule_arg = mock_client.create_schedule.call_args[0][1]
        assert schedule_arg.policy.catchup_window == timedelta(days=365)

class TestManagedSessionReconcileSchedule:
    @pytest.mark.asyncio
    async def test_creates_managed_session_reconcile_schedule_when_missing(self) -> None:
        mock_created_handle = MagicMock()
        mock_created_handle.id = MANAGED_SESSION_RECONCILE_SCHEDULE_ID
        mock_existing_handle = _mock_schedule_handle(
            describe_side_effect=Exception("not found")
        )
        mock_existing_handle.update = AsyncMock(side_effect=Exception("not found"))
        mock_client = MagicMock()
        mock_client.get_schedule_handle.return_value = mock_existing_handle
        mock_client.create_schedule = AsyncMock(return_value=mock_created_handle)

        adapter = _make_adapter(mock_client)
        result = await adapter.ensure_managed_session_reconcile_schedule(
            cron_expression="*/5 * * * *"
        )

        assert result == MANAGED_SESSION_RECONCILE_SCHEDULE_ID
        mock_client.create_schedule.assert_awaited_once()
        schedule_id, schedule = mock_client.create_schedule.call_args[0]
        assert schedule_id == MANAGED_SESSION_RECONCILE_SCHEDULE_ID
        assert schedule.action.workflow == "MoonMind.ManagedSessionReconcile"
        assert schedule.action.id == MANAGED_SESSION_RECONCILE_WORKFLOW_ID_TEMPLATE
        assert schedule.action.task_queue == "mm.workflow"
        assert schedule.action.static_summary == "Managed session reconcile"
        assert schedule.spec.cron_expressions == ["*/5 * * * *"]
        assert schedule.policy.overlap == ScheduleOverlapPolicy.SKIP

    @pytest.mark.asyncio
    async def test_managed_session_reconcile_schedule_defaults_and_disabled_state(
        self,
    ) -> None:
        mock_created_handle = MagicMock()
        mock_created_handle.id = MANAGED_SESSION_RECONCILE_SCHEDULE_ID
        mock_existing_handle = _mock_schedule_handle(
            describe_side_effect=Exception("not found")
        )
        mock_existing_handle.update = AsyncMock(side_effect=Exception("not found"))
        mock_client = MagicMock()
        mock_client.get_schedule_handle.return_value = mock_existing_handle
        mock_client.create_schedule = AsyncMock(return_value=mock_created_handle)

        adapter = _make_adapter(mock_client)
        result = await adapter.ensure_managed_session_reconcile_schedule(
            enabled=False
        )

        assert result == MANAGED_SESSION_RECONCILE_SCHEDULE_ID
        schedule_id, schedule = mock_client.create_schedule.call_args[0]
        assert schedule_id == MANAGED_SESSION_RECONCILE_SCHEDULE_ID
        assert schedule.action.id == MANAGED_SESSION_RECONCILE_WORKFLOW_ID_TEMPLATE
        assert schedule.spec.cron_expressions == ["*/10 * * * *"]
        assert schedule.spec.time_zone_name == "UTC"
        assert schedule.state.paused is True
        assert schedule.state.note == "Managed Codex session reconcile and orphan sweep"

    @pytest.mark.asyncio
    async def test_updates_existing_managed_session_reconcile_schedule(self) -> None:
        mock_existing_handle = _mock_schedule_handle()
        mock_client = MagicMock()
        mock_client.get_schedule_handle.return_value = mock_existing_handle
        mock_client.create_schedule = AsyncMock()

        adapter = _make_adapter(mock_client)
        result = await adapter.ensure_managed_session_reconcile_schedule()

        assert result == MANAGED_SESSION_RECONCILE_SCHEDULE_ID
        mock_existing_handle.update.assert_awaited_once()
        mock_client.create_schedule.assert_not_awaited()
        updater = mock_existing_handle.update.call_args[0][0]
        update = await updater(MagicMock())
        assert isinstance(update, ScheduleUpdate)
        assert update.schedule.action.workflow == "MoonMind.ManagedSessionReconcile"

    @pytest.mark.asyncio
    async def test_jitter_passed_through(self) -> None:
        mock_handle = MagicMock()
        mock_handle.id = _SCHEDULE_ID
        mock_client = MagicMock()
        mock_client.create_schedule = AsyncMock(return_value=mock_handle)

        adapter = _make_adapter(mock_client)
        await adapter.create_schedule(
            definition_id=_TEST_UUID,
            cron_expression="0 0 * * *",
            jitter_seconds=30,
            workflow_type="MoonMind.Run",
        )

        schedule_arg = mock_client.create_schedule.call_args[0][1]
        assert schedule_arg.spec.jitter == timedelta(seconds=30)

    @pytest.mark.asyncio
    async def test_disabled_creates_paused_schedule(self) -> None:
        mock_handle = MagicMock()
        mock_handle.id = _SCHEDULE_ID
        mock_client = MagicMock()
        mock_client.create_schedule = AsyncMock(return_value=mock_handle)

        adapter = _make_adapter(mock_client)
        await adapter.create_schedule(
            definition_id=_TEST_UUID,
            cron_expression="0 0 * * *",
            enabled=False,
            workflow_type="MoonMind.Run",
        )

        schedule_arg = mock_client.create_schedule.call_args[0][1]
        assert schedule_arg.state.paused is True

    @pytest.mark.asyncio
    async def test_already_exists_raises(self) -> None:
        mock_client = MagicMock()
        mock_client.create_schedule = AsyncMock(
            side_effect=Exception("Schedule already exists")
        )

        adapter = _make_adapter(mock_client)
        with pytest.raises(ScheduleAlreadyExistsError):
            await adapter.create_schedule(
                definition_id=_TEST_UUID,
                cron_expression="0 0 * * *",
                workflow_type="MoonMind.Run",
            )

    @pytest.mark.asyncio
    async def test_unknown_error_raises_operation_error(self) -> None:
        mock_client = MagicMock()
        mock_client.create_schedule = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )

        adapter = _make_adapter(mock_client)
        with pytest.raises(ScheduleOperationError, match="connection lost"):
            await adapter.create_schedule(
                definition_id=_TEST_UUID,
                cron_expression="0 0 * * *",
                workflow_type="MoonMind.Run",
            )

# ---------------------------------------------------------------------------
# T016: describe, update, pause, unpause, trigger, delete
# ---------------------------------------------------------------------------

class TestUpdateSchedule:
    """DOC-REQ-002: update schedule."""

    @pytest.mark.asyncio
    async def test_calls_handle_update_with_all_params(self) -> None:
        handle = _mock_schedule_handle()
        mock_client = MagicMock()
        mock_client.get_schedule_handle = MagicMock(return_value=handle)

        # Mock the input to the update callback
        mock_update_input = MagicMock()
        mock_schedule = MagicMock()
        mock_schedule.spec.cron_expressions = ["0 0 * * *"]
        mock_schedule.spec.time_zone_name = "UTC"
        mock_schedule.spec.jitter = timedelta(seconds=0)

        mock_schedule.policy.overlap.name = "SKIP"
        mock_schedule.policy.catchup_window = timedelta(minutes=15)

        mock_schedule.state.paused = False
        mock_schedule.state.note = ""
        mock_update_input.schedule = mock_schedule

        adapter = _make_adapter(mock_client)
        await adapter.update_schedule(
            definition_id=_TEST_UUID,
            cron_expression="0 12 * * *",
            timezone="America/New_York",
            overlap_mode="allow",
            catchup_mode="none",
            jitter_seconds=60,
            enabled=False,
            note="Updated note",
        )
        handle.update.assert_awaited_once()

        # Get the callback passed to update()
        update_callback = handle.update.call_args[0][0]

        # Call it with our mock input
        updated_schedule = await update_callback(mock_update_input)

        assert updated_schedule.spec.cron_expressions == ["0 12 * * *"]
        assert updated_schedule.spec.time_zone_name == "America/New_York"
        assert updated_schedule.spec.jitter == timedelta(seconds=60)
        assert updated_schedule.policy.overlap == ScheduleOverlapPolicy.ALLOW_ALL
        assert updated_schedule.policy.catchup_window == timedelta(seconds=0)
        assert updated_schedule.state.paused is True
        assert updated_schedule.state.note == "Updated note"

    @pytest.mark.asyncio
    async def test_update_preserves_unprovided_fields(self) -> None:
        handle = _mock_schedule_handle()
        mock_client = MagicMock()
        mock_client.get_schedule_handle = MagicMock(return_value=handle)

        mock_update_input = MagicMock()
        mock_schedule = MagicMock()
        mock_schedule.spec.cron_expressions = ["0 0 * * *"]
        mock_schedule.spec.time_zone_name = "UTC"
        mock_schedule.spec.jitter = timedelta(seconds=30)

        mock_schedule.policy.overlap.name = "SKIP"
        mock_schedule.policy.catchup_window = timedelta(days=365) # catchup_mode = "all"

        mock_schedule.state.paused = True # enabled = False
        mock_schedule.state.note = "Original note"
        mock_update_input.schedule = mock_schedule

        adapter = _make_adapter(mock_client)
        await adapter.update_schedule(definition_id=_TEST_UUID)

        handle.update.assert_awaited_once()
        update_callback = handle.update.call_args[0][0]
        updated_schedule = await update_callback(mock_update_input)

        # Verify fields are preserved
        assert updated_schedule.spec.cron_expressions == ["0 0 * * *"]
        assert updated_schedule.spec.time_zone_name == "UTC"
        assert updated_schedule.spec.jitter == timedelta(seconds=30)
        assert updated_schedule.policy.overlap.name == "SKIP"
        assert updated_schedule.policy.catchup_window == timedelta(days=365)
        assert updated_schedule.state.paused is True
        assert updated_schedule.state.note == "Original note"

class TestDescribeSchedule:
    """DOC-REQ-002: describe schedule."""

    @pytest.mark.asyncio
    async def test_returns_description(self) -> None:
        expected = MagicMock(name="ScheduleDescription")
        handle = _mock_schedule_handle()
        handle.describe.return_value = expected

        mock_client = MagicMock()
        mock_client.get_schedule_handle = MagicMock(return_value=handle)

        adapter = _make_adapter(mock_client)
        result = await adapter.describe_schedule(definition_id=_TEST_UUID)
        assert result is expected

    @pytest.mark.asyncio
    async def test_not_found_raises(self) -> None:
        handle = _mock_schedule_handle(
            describe_side_effect=Exception("Schedule not found")
        )
        mock_client = MagicMock()
        mock_client.get_schedule_handle = MagicMock(return_value=handle)

        adapter = _make_adapter(mock_client)
        with pytest.raises(ScheduleNotFoundError):
            await adapter.describe_schedule(definition_id=_TEST_UUID)

class TestPauseSchedule:
    """DOC-REQ-002: pause schedule."""

    @pytest.mark.asyncio
    async def test_calls_handle_pause(self) -> None:
        handle = _mock_schedule_handle()
        mock_client = MagicMock()
        mock_client.get_schedule_handle = MagicMock(return_value=handle)

        adapter = _make_adapter(mock_client)
        await adapter.pause_schedule(definition_id=_TEST_UUID)
        handle.pause.assert_awaited_once()

class TestUnpauseSchedule:
    """DOC-REQ-002: unpause schedule."""

    @pytest.mark.asyncio
    async def test_calls_handle_unpause(self) -> None:
        handle = _mock_schedule_handle()
        mock_client = MagicMock()
        mock_client.get_schedule_handle = MagicMock(return_value=handle)

        adapter = _make_adapter(mock_client)
        await adapter.unpause_schedule(definition_id=_TEST_UUID)
        handle.unpause.assert_awaited_once()

class TestTriggerSchedule:
    """DOC-REQ-002: trigger schedule."""

    @pytest.mark.asyncio
    async def test_calls_handle_trigger(self) -> None:
        handle = _mock_schedule_handle()
        mock_client = MagicMock()
        mock_client.get_schedule_handle = MagicMock(return_value=handle)

        adapter = _make_adapter(mock_client)
        await adapter.trigger_schedule(definition_id=_TEST_UUID)
        handle.trigger.assert_awaited_once()

class TestDeleteSchedule:
    """DOC-REQ-002: delete schedule."""

    @pytest.mark.asyncio
    async def test_calls_handle_delete(self) -> None:
        handle = _mock_schedule_handle()
        mock_client = MagicMock()
        mock_client.get_schedule_handle = MagicMock(return_value=handle)

        adapter = _make_adapter(mock_client)
        await adapter.delete_schedule(definition_id=_TEST_UUID)
        handle.delete.assert_awaited_once()

# ---------------------------------------------------------------------------
# T017: SDK error wrapping
# ---------------------------------------------------------------------------

class TestErrorWrapping:
    """DOC-REQ-007: adapter wraps SDK exceptions."""

    @pytest.mark.asyncio
    async def test_pause_not_found_wraps(self) -> None:
        handle = _mock_schedule_handle(
            describe_side_effect=Exception("Schedule not found")
        )
        mock_client = MagicMock()
        mock_client.get_schedule_handle = MagicMock(return_value=handle)

        adapter = _make_adapter(mock_client)
        with pytest.raises(ScheduleNotFoundError):
            await adapter.pause_schedule(definition_id=_TEST_UUID)

    @pytest.mark.asyncio
    async def test_trigger_operation_error_wraps(self) -> None:
        handle = _mock_schedule_handle()
        handle.trigger = AsyncMock(side_effect=RuntimeError("server unavailable"))

        mock_client = MagicMock()
        mock_client.get_schedule_handle = MagicMock(return_value=handle)

        adapter = _make_adapter(mock_client)
        with pytest.raises(ScheduleOperationError, match="server unavailable"):
            await adapter.trigger_schedule(definition_id=_TEST_UUID)

    @pytest.mark.asyncio
    async def test_delete_not_found_wraps(self) -> None:
        handle = _mock_schedule_handle(
            describe_side_effect=Exception("not found in namespace")
        )
        mock_client = MagicMock()
        mock_client.get_schedule_handle = MagicMock(return_value=handle)

        adapter = _make_adapter(mock_client)
        with pytest.raises(ScheduleNotFoundError):
            await adapter.delete_schedule(definition_id=_TEST_UUID)
