"""Rescheduling reaches the registered parent and performs the requested step."""

from datetime import UTC, datetime, timedelta

import pytest
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner

from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.integration.workflows.temporal.workflows.test_run import (
    _running_user_workflow,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def test_create_deferred_reschedule_verify():
    context = {}
    async with _running_user_workflow(
        request={
            "workflowType": "MoonMind.UserWorkflow",
            "scheduled_for": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        },
        workflow_id="test-deferred-reschedule",
        context=context,
    ) as (handle, calls):
        assert (await handle.query("get_status"))["state"] == "scheduled"
        assert calls["children"] == []
        adapter = TemporalClientAdapter(client=context["client"])
        await adapter.send_reschedule_signal(
            handle.id, datetime.now(UTC) - timedelta(minutes=30)
        )
        assert (await handle.result())["status"] == "success"
        assert len(calls["children"]) == 1
        assert calls["terminal"][-1]["state"] == "completed"
        description = await handle.describe()
        assert description.close_time - description.start_time < timedelta(hours=1)
        await Replayer(
            workflows=[MoonMindUserWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(await handle.fetch_history())
