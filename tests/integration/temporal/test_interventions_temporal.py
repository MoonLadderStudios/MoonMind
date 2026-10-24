"""Operator lifecycle controls remain observable without an interactive runtime."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.integration.workflows.temporal.workflows.test_run import (
    _running_user_workflow,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def _deferred_request():
    return {
        "workflowType": "MoonMind.UserWorkflow",
        "scheduled_for": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
    }


async def test_workflow_pause_resume():
    async with _running_user_workflow(
        request=_deferred_request(), workflow_id="test-pause-resume"
    ) as (handle, calls):
        assert (await handle.query("get_status"))["state"] == "scheduled"
        await handle.execute_update("Pause")
        await handle.signal(
            "reschedule", (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        )
        status = await handle.query("get_status")
        assert status["paused"] is True
        assert (await handle.query("control_state"))["safePoint"] is True
        assert calls["children"] == []
        await handle.execute_update("Resume")
        assert (await handle.result())["status"] == "success"
        assert len(calls["children"]) == 1
        assert calls["terminal"][-1]["state"] == "completed"
        assert (await handle.query("get_progress"))["completed"] == 1


async def test_cancel_while_paused_starts_no_child_and_keeps_diagnostics():
    async with _running_user_workflow(
        request=_deferred_request(), workflow_id="test-pause-cancel"
    ) as (handle, calls):
        await handle.execute_update("Pause")
        assert (await handle.query("get_status"))["paused"] is True
        await handle.execute_update("Cancel")
        assert (await handle.result())["status"] == "canceled"
        assert calls["children"] == []
        assert calls["terminal"][-1]["state"] == "canceled"
        assert (await handle.query("get_status"))["state"] == "canceled"
        assert (await handle.query("get_step_ledger"))["steps"] == []
