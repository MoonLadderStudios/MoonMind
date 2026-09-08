"""Live routing proof for checkpoint artifacts-fleet wiring (MoonMind#3949).

The catalog entry, queue name, and static AST walk in
``test_checkpoint_artifacts_fleet_wiring_3949.py`` pin the intended wiring
without executing it. These tests execute the real production routing
method — ``MoonMindCheckpointBranchTurnWorkflow._persistence_route_options``
— instead of a mock:

- ``temporal_boundary``: both patch branches of the real method run with
  ``workflow.patched`` stubbed, proving patched histories select the
  artifacts queue and pre-marker histories keep their recorded queue; a
  time-skipping Temporal server then records a patched execution of a
  probe workflow that calls the real method, asserts the
  recorded route and patch marker, and replays the history against the
  current build — proving the durable marker semantics a deployment sees.
  Every test in this module is owned by the temporal-boundary shard via
  tests/conftest.py; do not add ``pytest.mark.unit_fast`` here, it
  conflicts with that ownership.

Full success/failure/cancellation/retry journeys against a live
artifacts worker (database, sandbox, child workflows) remain integration
scope; the drain gate in ``moonmind.gates.checkpoint_compat_drain`` owns that sequencing.
"""

from __future__ import annotations

from typing import Any

import pytest
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from moonmind.workflows.temporal.activity_catalog import ARTIFACTS_TASK_QUEUE
    from moonmind.workflows.temporal.workflows import checkpoint_branch_turn as turn_module
    from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
        CHECKPOINT_BRANCH_ARTIFACT_FLEET_PATCH,
        MoonMindCheckpointBranchTurnWorkflow,
    )


@workflow.defn(name="MM3949CheckpointRouteProbe")
class _CheckpointRouteProbe:
    """Probe that executes the real production routing method in-sandbox."""

    @workflow.run
    async def run(self) -> dict[str, Any]:
        turn = MoonMindCheckpointBranchTurnWorkflow()
        return {"route": turn._persistence_route_options()}


def test_real_route_options_select_artifacts_queue_when_patched(monkeypatch):
    monkeypatch.setattr(
        turn_module.workflow, "patched", lambda _patch_id: True
    )
    assert MoonMindCheckpointBranchTurnWorkflow()._persistence_route_options() == {
        "task_queue": ARTIFACTS_TASK_QUEUE
    }


def test_real_route_options_keep_recorded_queue_when_unpatched(monkeypatch):
    monkeypatch.setattr(
        turn_module.workflow, "patched", lambda _patch_id: False
    )
    assert MoonMindCheckpointBranchTurnWorkflow()._persistence_route_options() == {}


def test_route_probe_uses_the_real_production_method():
    import inspect

    source = inspect.getsource(_CheckpointRouteProbe.run)
    assert "_persistence_route_options" in source
    assert "MoonMindCheckpointBranchTurnWorkflow" in source
    # The probe must not hard-code a queue: the queue comes from production.
    assert "mm.activity.artifacts" not in source
    assert "task_queue" not in source


@pytest.mark.temporal_boundary
@pytest.mark.asyncio
async def test_patched_execution_records_artifacts_route_and_marker():
    """Record a patched probe run, then replay it on the current build."""

    from temporalio.converter import DataConverter
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

    recorded_histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-mm3949-route",
            workflows=[_CheckpointRouteProbe],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _CheckpointRouteProbe.run,
                id="test-mm3949-route-probe",
                task_queue="test-mm3949-route",
            )
            result = await handle.result()
            assert result == {"route": {"task_queue": ARTIFACTS_TASK_QUEUE}}
            recorded_histories.append(await handle.fetch_history())

    assert len(recorded_histories) == 1
    # The durable patch marker is what lets replay distinguish new writes
    # from pre-cutover histories on the deployment.
    patch_ids: list[str] = []
    for event in recorded_histories[0].events:
        if event.HasField("marker_recorded_event_attributes"):
            attrs = event.marker_recorded_event_attributes
            if attrs.marker_name == "core_patch":
                payload = (
                    await DataConverter.default.decode(
                        attrs.details["patch-data"].payloads
                    )
                )[0]
                patch_ids.append(payload["id"])
    assert CHECKPOINT_BRANCH_ARTIFACT_FLEET_PATCH in patch_ids

    replayer = Replayer(
        workflows=[_CheckpointRouteProbe],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(recorded_histories[0])
