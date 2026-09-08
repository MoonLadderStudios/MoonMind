"""Dependency waits confirm real pause boundaries and preserve existing histories."""
from __future__ import annotations

import asyncio
import gc
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.common import SearchAttributeKey, SearchAttributePair, TypedSearchAttributes
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.temporal_activity_models import DependencyStatusSnapshotInput
from moonmind.workflows.temporal.workflows.run import (
    DEFAULT_ACTIVITY_CATALOG,
    DEPENDENCY_RECONCILE_INTERVAL,
    RUN_CANONICAL_DEPENDENCY_PARAMETERS_PATCH,
    RUN_DEPENDENCY_PAUSE_SAFE_BOUNDARY_PATCH,
    MoonMindUserWorkflow,
)
from tests.helpers.temporal_visibility import register_deployment_search_attributes


@pytest.fixture(autouse=True)
def _collect_closed_workflows():
    """Finalize abandoned unsandboxed coroutines outside workflow event loops."""
    # Open-history replay and hard termination can leave coroutine cycles. Their
    # dependency-wait finally blocks issue commands; GC in the next workflow
    # thread would attach those commands to that unrelated workflow history,
    # including the same test's Replayer. Control collection until both close.
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        gc.collect()
        yield
    finally:
        try:
            gc.collect()
        finally:
            if was_enabled:
                gc.enable()


class DependencySnapshot:
    def __init__(self, state: str) -> None:
        self.state = state
        self.calls: list[list[str]] = []

    @activity.defn(name="execution.dependency_status_snapshot")
    async def snapshot(self, payload: dict) -> dict:
        request = DependencyStatusSnapshotInput.model_validate(payload)
        self.calls.append(request.workflow_ids)
        return {
            workflow_id: {
                "state": self.state,
                "workflowType": "MoonMind.UserWorkflow",
            }
            for workflow_id in request.workflow_ids
        }


async def _wait_for_query(handle, query: str, **expected):
    async with asyncio.timeout(5):
        while True:
            result = await handle.query(query)
            if all(result.get(key) == value for key, value in expected.items()):
                return result
            await asyncio.sleep(0.01)


@asynccontextmanager
async def _dependency_workflow(state: str, *, input_payload=None, wait_for_gate=True):
    """Keep the production workflow and activity invocation; supply only the snapshot."""
    queue = f"dependency-pause-{uuid4()}"
    snapshot = DependencySnapshot(state)
    route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
        "execution.dependency_status_snapshot"
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await register_deployment_search_attributes(env)
        with env.auto_time_skipping_disabled():
            async with (
                Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[MoonMindUserWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                Worker(
                    env.client,
                    task_queue=route.task_queue,
                    activities=[snapshot.snapshot],
                ),
            ):
                handle = await env.client.start_workflow(
                    MoonMindUserWorkflow.run,
                    input_payload or {
                        "workflow_type": "MoonMind.UserWorkflow",
                        "initial_parameters": {"task": {"dependsOn": ["prerequisite"]}},
                    },
                    id=f"dependency-pause-{uuid4()}",
                    task_queue=queue,
                    search_attributes=TypedSearchAttributes([
                        SearchAttributePair(
                            SearchAttributeKey.for_keyword("mm_owner_type"), "user"
                        ),
                        SearchAttributePair(
                            SearchAttributeKey.for_keyword("mm_owner_id"), str(uuid4())
                        ),
                    ]),
                )
                try:
                    if wait_for_gate:
                        await _wait_for_query(
                            handle, "get_status", state="waiting_on_dependencies"
                        )
                        async with asyncio.timeout(5):
                            while not snapshot.calls:
                                await asyncio.sleep(0.01)
                    yield env, handle, snapshot
                finally:
                    await handle.terminate(reason="Dependency pause boundary test complete")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,system_control",
    [("executing", False), ("", True), ("new_provider_state", True)],
)
async def test_dependency_wait_confirms_pause_and_replays(state, system_control):
    """No-argument and generation Updates reach safe points for incomplete input."""
    async with _dependency_workflow(state) as (env, handle, snapshot):
        for generation, action, expected in (
            (1, "Pause", {"safePoint": True, "paused": True}),
            (2, "Resume", {"safePoint": False, "resumed": True}),
            (3, "Pause", {"safePoint": True, "paused": True}),
        ):
            if system_control:
                await handle.execute_update(action, {"controlGeneration": generation})
                expected["controlGeneration"] = generation
            else:
                await handle.execute_update(action)
            await _wait_for_query(handle, "control_state", **expected)
            await _wait_for_query(
                handle,
                "get_status",
                state="waiting_on_dependencies",
                waiting_reason=("operator_paused" if action == "Pause" else "dependency_wait"),
            )
            if generation == 1:
                calls_before_pause = len(snapshot.calls)
                await env.sleep(DEPENDENCY_RECONCILE_INTERVAL + timedelta(seconds=1))
                assert len(snapshot.calls) == calls_before_pause

        # Completion signals retain their evidence without releasing the pause.
        await handle.signal("DependencyResolved", {
            "prerequisiteWorkflowId": "prerequisite",
            "terminalState": "completed",
            "closeStatus": "completed",
            "resolvedAt": "2026-09-05T00:00:00Z",
        })
        await _wait_for_query(handle, "control_state", safePoint=True, paused=True)
        assert (await handle.query("get_status"))["state"] == "waiting_on_dependencies"
        assert snapshot.calls[0] == ["prerequisite"]
        history = await handle.fetch_history()
        await Replayer(
            workflows=[MoonMindUserWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["", "new_provider_state", "failed", "canceled", "timed_out"])
async def test_canonical_dependencies_keep_non_success_waiting(state):
    async with _dependency_workflow(
        state, input_payload=_canonical_dependency_input()
    ) as (_env, handle, _snapshot):
        history = await handle.fetch_history()
        assert _scheduled_activities(history) == ["execution.dependency_status_snapshot"]
        assert (await handle.query("get_status"))["state"] == "waiting_on_dependencies"


@pytest.mark.asyncio
@pytest.mark.parametrize("dependencies", [None, [], ["prerequisite"]])
async def test_canonical_dependencies_ready_at_start(dependencies):
    payload = _canonical_dependency_input()
    if dependencies is None:
        payload["initial_parameters"]["workflow"].pop("dependsOn")
    else:
        payload["initial_parameters"]["workflow"]["dependsOn"] = dependencies
    async with _dependency_workflow(
        "completed", input_payload=payload, wait_for_gate=False
    ) as (_env, handle, snapshot):
        await _wait_for_query(handle, "get_status", state="planning")
        assert snapshot.calls == ([["prerequisite"]] if dependencies else [])
        assert _scheduled_activities(await handle.fetch_history())[-1] == "plan.generate"


@pytest.mark.asyncio
async def test_canonical_dependency_history_before_fix_replays(monkeypatch):
    """Do not insert dependency commands into histories that already began work."""
    patched = workflow.patched
    with monkeypatch.context() as legacy:
        legacy.setattr(workflow, "patched", lambda patch_id: (
            False if patch_id == RUN_CANONICAL_DEPENDENCY_PARAMETERS_PATCH
            else patched(patch_id)
        ))
        async with _dependency_workflow(
            "executing", input_payload=_canonical_dependency_input(), wait_for_gate=False
        ) as (_env, handle, snapshot):
            await _wait_for_query(handle, "get_status", state="planning")
            assert snapshot.calls == []
            history = await handle.fetch_history()
            assert _scheduled_activities(history) == ["plan.generate"]

    await Replayer(
        workflows=[MoonMindUserWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
async def test_dependency_pause_history_before_safe_boundary_patch_replays(monkeypatch):
    """Replay a minimal old dependency timer history with actual no-argument Updates."""
    patched = workflow.patched
    with monkeypatch.context() as legacy:
        legacy.setattr(
            workflow,
            "patched",
            lambda patch_id: (
                False
                if patch_id == RUN_DEPENDENCY_PAUSE_SAFE_BOUNDARY_PATCH
                else patched(patch_id)
            ),
        )
        async with _dependency_workflow("executing") as (_, handle, _snapshot):
            await handle.execute_update("Pause")
            assert not (await handle.query("control_state"))["safePoint"]
            await handle.execute_update("Resume")
            await _wait_for_query(handle, "control_state", resumed=True)
            history = await handle.fetch_history()

    # Use the unmodified patch mechanism: the missing marker selects old commands.
    await Replayer(
        workflows=[MoonMindUserWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


def _canonical_dependency_input():
    """Minimized worker input from the Fix and Review Loop dependency incident."""
    return json.loads((
        Path(__file__).parents[3]
        / "fixtures/temporal/workflow_dependencies/canonical_start.json"
    ).read_text())


def _scheduled_activities(history):
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime", [None, "omnigent", "codex_cli", "claude_code", "jules", "openclaw"]
)
async def test_canonical_dependencies_block_planning_and_child_launch(runtime):
    payload = _canonical_dependency_input()
    parameters = payload["initial_parameters"]
    if runtime is not None:
        parameters["targetRuntime"] = runtime
        parameters["workflow"]["runtime"] = {"mode": runtime}
    async with _dependency_workflow("executing", input_payload=payload) as (
        _env, handle, snapshot
    ):
        assert snapshot.calls == [["prerequisite"]]
        history = await handle.fetch_history()
        assert _scheduled_activities(history) == ["execution.dependency_status_snapshot"]
        assert not any(
            event.HasField("start_child_workflow_execution_initiated_event_attributes")
            for event in history.events
        )

        await handle.signal("DependencyResolved", {
            "prerequisiteWorkflowId": "prerequisite",
            "terminalState": "completed",
            "closeStatus": "completed",
            "resolvedAt": "2026-09-08T23:02:00Z",
        })
        await _wait_for_query(handle, "get_status", state="planning")
        history = await handle.fetch_history()
        assert _scheduled_activities(history)[:2] == [
            "execution.dependency_status_snapshot", "plan.generate"
        ]
        await Replayer(
            workflows=[MoonMindUserWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)
