"""Replay both retired Python definitions through the canonical registration."""

import json
from pathlib import Path

import pytest
from temporalio import workflow
from temporalio.client import WorkflowHistory
from temporalio.converter import DataConverter
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner

from moonmind.workflows.temporal.workflow_registry import (
    workflow_fleet_workflow_classes,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal_boundary]
FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "fixtures/temporal/manifest_before_consolidation"
)


@pytest.mark.parametrize("entry", ["compilation", "compilation-default", "nodes"])
async def test_manifest_previous_definitions_replay_on_single_registered_class(entry):
    registered = [
        cls
        for cls in workflow_fleet_workflow_classes()
        if workflow._Definition.must_from_class(cls).name == "MoonMind.ManifestIngest"
    ]
    assert len(registered) == 1
    data = json.loads((FIXTURES / f"{entry}.json").read_text())
    history = WorkflowHistory.from_json(f"manifest-before-{entry}", data)
    commands = [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    if entry == "compilation":
        assert commands == ["manifest.compile", "manifest.write_summary"]
    elif entry == "compilation-default":
        assert commands == ["manifest.compile"]
        assert any(
            event.HasField("workflow_execution_failed_event_attributes")
            for event in history.events
        )
    else:
        assert {"manifest_read", "manifest_compile", "manifest_write_summary"}.issubset(
            commands
        )
        assert any(
            event.HasField("workflow_execution_update_completed_event_attributes")
            for event in history.events
        )
        assert any(
            event.HasField("start_child_workflow_execution_initiated_event_attributes")
            for event in history.events
        )
    assert "manifest-catalog-activities-v1" not in json.dumps(data)
    if entry != "compilation-default":
        summary_command = next(
            event.activity_task_scheduled_event_attributes
            for event in history.events
            if event.HasField("activity_task_scheduled_event_attributes")
            and event.activity_task_scheduled_event_attributes.activity_type.name
            in {"manifest.write_summary", "manifest_write_summary"}
        )
        (summary_input,) = await DataConverter.default.decode(
            summary_command.input.payloads
        )
        history = WorkflowHistory.from_json(summary_input["workflow_id"], data)
    await Replayer(
        workflows=registered, workflow_runner=UnsandboxedWorkflowRunner()
    ).replay_workflow(history)
