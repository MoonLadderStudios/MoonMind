"""Probe receipts require real Temporal completion evidence, never mock handlers."""

from types import SimpleNamespace

import pytest
from temporalio.api.history.v1 import HistoryEvent

from tools import worker_runtime_activity_probe as probe


def history():
    events = []
    for index, (name, queue) in enumerate(
        (name, "artifacts" if name.startswith("artifact.") else "integrations")
        for name in probe.ACTIVITIES
    ):
        scheduled = HistoryEvent(event_id=index * 2 + 1)
        scheduled.activity_task_scheduled_event_attributes.activity_type.name = name
        scheduled.activity_task_scheduled_event_attributes.task_queue.name = queue
        completed = HistoryEvent(event_id=index * 2 + 2)
        completed.activity_task_completed_event_attributes.scheduled_event_id = (
            scheduled.event_id
        )
        events.extend([scheduled, completed])
    final = HistoryEvent(event_id=9)
    final.workflow_execution_completed_event_attributes.SetInParent()
    events.append(final)
    return SimpleNamespace(events=events)


def test_probe_rejects_non_disposable_project():
    with pytest.raises(ValueError):
        probe.validate_inputs("moonmind")
    probe.validate_inputs("moonmind-test-worker-separate")


def test_completed_receipt_requires_both_production_queue_histories():
    receipt = probe.verify_history(history(), "artifacts", "integrations")
    assert receipt == [
        {
            "activityType": "artifact.create",
            "taskQueue": "artifacts",
            "completed": True,
        },
        {
            "activityType": "artifact.write_complete",
            "taskQueue": "artifacts",
            "completed": True,
        },
        {"activityType": "artifact.read", "taskQueue": "artifacts", "completed": True},
        {
            "activityType": "memory.evaluate_proposals",
            "taskQueue": "integrations",
            "completed": True,
        },
    ]


@pytest.mark.parametrize(
    "defect",
    ["missing_completion", "wrong_queue", "wrong_handler", "workflow_not_complete"],
)
def test_receipt_rejects_missing_or_wrong_evidence(defect):
    observed = history()
    if defect == "missing_completion":
        observed.events.pop(3)
    elif defect == "wrong_queue":
        observed.events[2].activity_task_scheduled_event_attributes.task_queue.name = (
            "test-driver"
        )
    elif defect == "wrong_handler":
        observed.events[
            2
        ].activity_task_scheduled_event_attributes.activity_type.name = (
            "fake.integration"
        )
    else:
        observed.events.pop()
    with pytest.raises(probe.ProbeFailure):
        probe.verify_history(observed, "artifacts", "integrations")


@pytest.mark.asyncio
async def test_empty_memory_payload_uses_real_registered_production_handler():
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalIntegrationActivities,
    )

    source = {
        "workflowId": "moonmind-test-probe",
        "runId": "test-run",
        "logicalStepId": "probe",
        "executionOrdinal": 1,
    }
    handler = TemporalIntegrationActivities(artifact_service=None)
    from moonmind.workflows.temporal.activity_runtime import _bind_activity_handler

    bound = _bind_activity_handler(
        handler,
        func=TemporalIntegrationActivities.memory_evaluate_proposals,
        activity_type="memory.evaluate_proposals",
    )
    result = await bound(probe.memory_request(source))
    assert result == {"decisionRefs": [], "decisions": []}


@pytest.mark.asyncio
async def test_driver_roundtrip_uses_real_activity_implementations(
    tmp_path, monkeypatch
):
    """Local wire/service proof; installed-worker execution is the live probe's job."""
    import dataclasses

    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.testing import ActivityEnvironment

    from moonmind.workflows.temporal.activity_runtime import (
        TemporalIntegrationActivities,
        _bind_activity_handler,
    )
    from tests.helpers.temporal_artifact_workers import artifact_workers

    env = ActivityEnvironment()
    env.info = dataclasses.replace(
        env.info, workflow_id="moonmind-test-probe", workflow_run_id="probe-run"
    )
    memory = _bind_activity_handler(
        TemporalIntegrationActivities(artifact_service=None),
        func=TemporalIntegrationActivities.memory_evaluate_proposals,
        activity_type="memory.evaluate_proposals",
    )
    calls = []
    async with artifact_workers(tmp_path, monkeypatch) as owners:
        handlers = {name: owners.bind(name) for name in probe.ACTIVITIES[:3]}
        handlers["memory.evaluate_proposals"] = memory

        async def execute(name, payload, **options):
            calls.append((name, options["task_queue"]))
            result = await env.run(handlers[name], payload)
            encoded = await pydantic_data_converter.encode([result])
            hints = [options["result_type"]] if "result_type" in options else None
            return (await pydantic_data_converter.decode(encoded, type_hints=hints))[0]

        monkeypatch.setattr(probe.workflow, "execute_activity", execute)
        monkeypatch.setattr(
            probe.workflow,
            "info",
            lambda: SimpleNamespace(
                workflow_id=env.info.workflow_id, run_id=env.info.workflow_run_id
            ),
        )
        result = await probe.ActivityProbe().run(
            {
                "artifacts": "production-artifacts",
                "integrations": "production-integrations",
            }
        )
    assert result == {
        "artifactSha256": probe.DIGEST,
        "artifactBytes": len(probe.CONTENT),
        "memoryDecisions": 0,
    }
    assert calls == [
        (
            name,
            (
                "production-artifacts"
                if name.startswith("artifact.")
                else "production-integrations"
            ),
        )
        for name in probe.ACTIVITIES
    ]


@pytest.mark.asyncio
async def test_cleanup_terminates_only_supplied_handle_when_cancel_fails():
    from unittest.mock import AsyncMock

    handle = SimpleNamespace(
        cancel=AsyncMock(side_effect=RuntimeError("failed")), terminate=AsyncMock()
    )
    assert await probe.stop_owned_workflow(handle) == "terminated"
    handle.terminate.assert_awaited_once_with(
        "Disposable worker activity probe cleanup"
    )


@pytest.mark.asyncio
async def test_cleanup_reports_uncertain_outcome():
    from unittest.mock import AsyncMock

    handle = SimpleNamespace(
        cancel=AsyncMock(side_effect=RuntimeError("failed")),
        terminate=AsyncMock(side_effect=RuntimeError("unavailable")),
    )
    assert await probe.stop_owned_workflow(handle) == "unconfirmed"


@pytest.mark.asyncio
async def test_cleanup_recognizes_already_closed_execution_without_mutating_it():
    from unittest.mock import AsyncMock

    from temporalio.client import WorkflowExecutionStatus

    handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(status=WorkflowExecutionStatus.COMPLETED)
        ),
        cancel=AsyncMock(),
        terminate=AsyncMock(),
    )
    assert await probe.stop_owned_workflow(handle) == "closed"
    handle.cancel.assert_not_awaited()
    handle.terminate.assert_not_awaited()
