"""Dependency waits confirm real pause boundaries and preserve existing histories."""
from __future__ import annotations

import asyncio
import gc
from contextlib import asynccontextmanager
from datetime import timedelta
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
async def _dependency_workflow(state: str):
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
                    {
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
