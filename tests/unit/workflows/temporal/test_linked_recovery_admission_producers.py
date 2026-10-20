"""New-start admission is owned by producers, not operator parameters."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from temporalio.client import Schedule, ScheduleActionStartWorkflow, ScheduleSpec

from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.hard_switch_cutover import (
    LEGACY_USER_WORKFLOW_TYPE,
    admit_user_workflow_input,
)

_REAL_START_WORKFLOW = TemporalClientAdapter.start_workflow

USER_WORKFLOW = "MoonMind.UserWorkflow"
DEFINITION_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def _spoofed_input():
    return {
        "linked_recovery_retired": False,
        "initial_parameters": {
            "linked_recovery_retired": False,
            "task": {"instructions": "Keep the original task"},
        },
        "remediation_loop_continuation": {"linkedRecoveryRetired": False},
    }


@pytest.mark.parametrize("payload", [None, {}, _spoofed_input()])
def test_user_admission_copies_payload_and_overrides_operator_opt_out(payload):
    original = deepcopy(payload)
    admitted = admit_user_workflow_input(USER_WORKFLOW, payload)
    assert admitted == {**(payload or {}), "linked_recovery_retired": True}
    assert admitted is not payload
    assert payload == original


@pytest.mark.parametrize("payload", [None, {}, _spoofed_input()])
@pytest.mark.parametrize(
    "workflow_type", [LEGACY_USER_WORKFLOW_TYPE, "MoonMind.MergeAutomation"]
)
def test_other_workflow_admission_leaves_payload_unchanged(workflow_type, payload):
    original = deepcopy(payload)
    assert admit_user_workflow_input(workflow_type, payload) is payload
    assert payload == original


@pytest.mark.asyncio
@pytest.mark.parametrize("producer", ["start", "create_schedule", "replace_schedule_action"])
@pytest.mark.parametrize("workflow_type", [USER_WORKFLOW, "MoonMind.MergeAutomation"])
@pytest.mark.parametrize("payload", [None, _spoofed_input()])
async def test_admission_at_client_producer_boundary(producer, workflow_type, payload, monkeypatch):
    # The suite guards live starts globally; retain real adapter behavior with an injected client.
    monkeypatch.setattr(TemporalClientAdapter, "start_workflow", _REAL_START_WORKFLOW)
    original = deepcopy(payload)
    handle = MagicMock()
    handle.id = "workflow-id"
    handle.result_run_id = "run-id"
    handle.describe = AsyncMock()
    handle.update = AsyncMock()
    client = MagicMock()
    client.start_workflow = AsyncMock(return_value=handle)
    client.create_schedule = AsyncMock(return_value=handle)
    client.get_schedule_handle.return_value = handle
    adapter = TemporalClientAdapter(client=client)
    adapter._get_task_queue = MagicMock(return_value="test-queue")

    if producer == "start":
        await adapter.start_workflow(
            workflow_type=workflow_type, workflow_id="workflow-id", input_args=payload,
        )
        actual_args = list(client.start_workflow.call_args.args[1:])
    elif producer == "create_schedule":
        await adapter.create_schedule(
            definition_id=DEFINITION_ID, cron_expression="0 0 * * *",
            workflow_type=workflow_type, workflow_input=payload,
        )
        actual_args = client.create_schedule.call_args.args[1].action.args
    else:
        await adapter.update_schedule(
            definition_id=DEFINITION_ID, workflow_type=workflow_type,
            workflow_input=payload,
        )
        update = await handle.update.call_args.args[0](
            SimpleNamespace(description=SimpleNamespace(schedule=MagicMock()))
        )
        actual_args = update.schedule.action.args

    expected = (
        {**(payload or {}), "linked_recovery_retired": True}
        if workflow_type == USER_WORKFLOW else payload
    )
    assert actual_args == ([expected] if expected is not None else [])
    assert payload == original
    if workflow_type == USER_WORKFLOW and payload is not None:
        assert actual_args[0] is not payload
        assert actual_args[0]["initial_parameters"]["linked_recovery_retired"] is False
        assert actual_args[0]["remediation_loop_continuation"]["linkedRecoveryRetired"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", [{"cron_expression": "0 12 * * *"}, {"enabled": False}])
async def test_schedule_timing_only_edits_preserve_unversioned_durable_action(edit):
    payload = {"initial_parameters": {"task": {"instructions": "Already queued"}}}
    action = ScheduleActionStartWorkflow(
        USER_WORKFLOW, payload, id="old-workflow", task_queue="old-queue",
    )
    schedule = Schedule(action=action, spec=ScheduleSpec(cron_expressions=["0 0 * * *"]))
    handle = MagicMock()
    handle.describe = AsyncMock()
    handle.update = AsyncMock()
    client = MagicMock()
    client.get_schedule_handle.return_value = handle
    adapter = TemporalClientAdapter(client=client)
    adapter._get_task_queue = MagicMock(return_value="test-queue")

    await adapter.update_schedule(definition_id=DEFINITION_ID, **edit)
    update = await handle.update.call_args.args[0](
        SimpleNamespace(description=SimpleNamespace(schedule=schedule))
    )

    assert update.schedule.action is action
    assert update.schedule.action.args == [payload]
    assert "linked_recovery_retired" not in update.schedule.action.args[0]
