"""Unit tests for Temporal Schedule CRUD on TemporalClientAdapter.

Covers T010 (create_schedule), T016 (describe/update/pause/unpause/trigger/delete),
and T017 (SDK error wrapping).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from moonmind.workflows.temporal.client import TemporalClientAdapter
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

    @pytest.mark.asyncio
    async def test_overlap_policy_passed_through(self) -> None:
        from temporalio.client import ScheduleOverlapPolicy

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
    async def test_jitter_passed_through(self) -> None:
        from datetime import timedelta

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
