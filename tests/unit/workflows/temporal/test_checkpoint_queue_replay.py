"""Production CheckpointBranchTurn histories before the artifacts-fleet cutover."""

import json
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner

from moonmind.workflows.temporal.workflow_registry import (
    workflow_fleet_workflow_classes,
)

pytestmark = pytest.mark.temporal_boundary

FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "fixtures/temporal/checkpoint_before_artifacts_fleet"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["success", "canceled", "rejected"])
async def test_pre_artifacts_fleet_commands_replay_on_registered_workflow(scenario):
    data = json.loads((FIXTURES / f"{scenario}.json").read_text())
    history = WorkflowHistory.from_json(f"checkpoint-before-{scenario}", data)
    persistence = [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
        and event.activity_task_scheduled_event_attributes.activity_type.name.startswith(
            "checkpoint_branch.turn."
        )
    ]
    assert persistence
    assert all(
        item.task_queue.name.startswith("checkpoint-branch-turn-")
        for item in persistence
    )
    names = [item.activity_type.name for item in persistence]
    assert "checkpoint_branch.turn.mark_running" in names
    assert "checkpoint_branch.turn.persist_terminal" in names
    if scenario == "rejected":
        assert "checkpoint_branch.turn.persist_terminal_rejection" in names
    assert "checkpoint-branch-artifact-fleet-v1" not in json.dumps(data)
    await Replayer(
        workflows=list(workflow_fleet_workflow_classes()),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
