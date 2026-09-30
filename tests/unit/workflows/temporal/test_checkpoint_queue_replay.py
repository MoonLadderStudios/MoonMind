"""Production CheckpointBranchTurn histories before the artifacts-fleet cutover."""

import json
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.converter import DataConverter
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner

from moonmind.workflows.temporal.workflow_registry import (
    workflow_fleet_workflow_classes,
)
from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
    CHECKPOINT_BRANCH_FINALIZATION_SAVE_PATCH,
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


FINALIZATION_SAVE_FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "fixtures/temporal/checkpoint_branch_finalization_save"
)


@pytest.mark.asyncio
async def test_failed_capture_before_finalization_save_resume_replays():
    """MoonLadderStudios/MoonMind#4016: the pre-resume failed-capture path replays.

    Before the branch resumed its checkpoint phase from the child's
    finalization save, an exhausted capture persisted a failed terminal with
    that save named for reconciliation. The retained history carries a
    complete verified save, so current code reaches the resume decision and
    must keep the recorded commands for an unpatched execution.
    """

    data = json.loads(
        (FINALIZATION_SAVE_FIXTURES / "failed_capture_before_resume.json").read_text()
    )
    history = WorkflowHistory.from_json("checkpoint-branch-failed-capture", data)
    scheduled = [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    assert scheduled == [
        "checkpoint_branch.turn.mark_running",
        "workspace.capture_checkpoint",
        "checkpoint_branch.turn.persist_terminal",
    ]
    markers = [
        event.marker_recorded_event_attributes
        for event in history.events
        if event.HasField("marker_recorded_event_attributes")
    ]
    patch_ids = [
        (await DataConverter.default.decode(marker.details["patch-data"].payloads))[0][
            "id"
        ]
        for marker in markers
        if marker.marker_name == "core_patch"
    ]
    assert CHECKPOINT_BRANCH_FINALIZATION_SAVE_PATCH not in patch_ids
    await Replayer(
        workflows=list(workflow_fleet_workflow_classes()),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
