"""R8: the manager owns slot release; the parent keeps no defensive fallback.

MoonLadderStudios/MoonMind#1089 item 8. The manager-side verified-teardown
handoff (R2-R7) passes, so the parent-side defensive release is retired:

* a terminal ``child_state_changed`` records no ``release_slot`` external
  signal — the release decision stays with the ProviderProfileManager — and
  the version marker only as a deprecated retirement tombstone;
* the retired ``run-defensive-slot-release-1`` marker stays consumable via
  ``workflow.deprecate_patch`` so retained marker-only histories keep
  replaying (``fixtures/run_parent_defensive_release_marker_only.json``);
* the retired command history (``fixtures/run_parent_defensive_release_retired.json``,
  recorded on the pre-retirement build) defines the exact drainage-inventory
  target for the deployment cutover: histories containing a defensive
  ``release_slot`` initiation predate the retirement and drain on the previous
  worker cohort;
* the retired behavioral surface (``profile_assigned`` handler, defensive
  release helpers, child assignment notification) is gone.
"""

from __future__ import annotations

import asyncio
import gc
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import WorkflowHistory
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.converter import DataConverter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.temporal_activity_models import DependencyStatusSnapshotInput
from moonmind.workflows.temporal.workflows.run import (
    DEFAULT_ACTIVITY_CATALOG,
    MoonMindUserWorkflow,
)
from tests.helpers.temporal_visibility import register_deployment_search_attributes

FIXTURES = Path(__file__).parent.parent / "fixtures"
MARKER_ONLY_HISTORY = FIXTURES / "run_parent_defensive_release_marker_only.json"
RETIRED_COMMAND_HISTORY = FIXTURES / "run_parent_defensive_release_retired.json"
RETIRED_PATCH_ID = "run-defensive-slot-release-1"
RETIRED_MANAGER_ID = "provider-profile-manager:claude_code"


@pytest.fixture(autouse=True)
def _collect_closed_workflows():
    """Finalize abandoned unsandboxed coroutines outside workflow event loops."""
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


class _DependencySnapshot:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    @activity.defn(name="execution.dependency_status_snapshot")
    async def snapshot(self, payload: dict) -> dict:
        request = DependencyStatusSnapshotInput.model_validate(payload)
        self.calls.append(request.workflow_ids)
        return {
            workflow_id: {
                "state": "executing",
                "workflowType": "MoonMind.UserWorkflow",
            }
            for workflow_id in request.workflow_ids
        }


async def _wait_for_query(handle, query: str, **expected):
    async with asyncio.timeout(10):
        while True:
            result = await handle.query(query)
            if all(result.get(key) == value for key, value in expected.items()):
                return result
            await asyncio.sleep(0.05)


@asynccontextmanager
async def _parked_parent():
    """Production UserWorkflow parked at the dependency gate; signals stay live."""
    queue = f"parent-fallback-retirement-{uuid4()}"
    snapshot = _DependencySnapshot()
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
                    id=f"parent-fallback-retirement-{uuid4()}",
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
                    async with asyncio.timeout(10):
                        while not snapshot.calls:
                            await asyncio.sleep(0.05)
                    yield env, handle
                finally:
                    await handle.terminate(reason="fallback retirement test complete")


def _received_signals(history) -> list[str]:
    return [
        event.workflow_execution_signaled_event_attributes.signal_name
        for event in history.events
        if event.HasField("workflow_execution_signaled_event_attributes")
    ]


def _external_signals(history) -> list[tuple[str, str]]:
    return [
        (
            event.signal_external_workflow_execution_initiated_event_attributes.signal_name,
            event.signal_external_workflow_execution_initiated_event_attributes.workflow_execution.workflow_id,
        )
        for event in history.events
        if event.HasField(
            "signal_external_workflow_execution_initiated_event_attributes"
        )
    ]


async def _recorded_patches(history) -> dict[str, bool]:
    """Patch-marker id to deprecated flag for every recorded core_patch marker."""
    patches: dict[str, bool] = {}
    for event in history.events:
        if not event.HasField("marker_recorded_event_attributes"):
            continue
        attrs = event.marker_recorded_event_attributes
        if attrs.marker_name != "core_patch":
            continue
        payload = (
            await DataConverter.default.decode(attrs.details["patch-data"].payloads)
        )[0]
        patches[payload["id"]] = bool(payload.get("deprecated", False))
    return patches


async def _recorded_patch_ids(history) -> list[str]:
    return list((await _recorded_patches(history)).keys())


@pytest.mark.asyncio
async def test_retired_marker_only_history_replays() -> None:
    """A retained marker-only parent history replays on the retired build.

    The fixture recorded a terminal ``child_state_changed`` with the
    ``run-defensive-slot-release-1`` marker and no fallback command — the
    representative retained shape. ``deprecate_patch`` consumes the marker
    without recording a new one, so recoverable work survives retirement.
    """
    history = WorkflowHistory.from_json(
        "parent-fallback-marker-only", MARKER_ONLY_HISTORY.read_text()
    )
    assert "child_state_changed" in _received_signals(history)
    patches = await _recorded_patches(history)
    # The fixture is genuinely pre-retirement: the marker is non-deprecated.
    assert patches.get(RETIRED_PATCH_ID) is False
    assert ("release_slot", RETIRED_MANAGER_ID) not in _external_signals(history)

    await Replayer(
        workflows=[MoonMindUserWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
async def test_retired_command_history_defines_cutover_inventory() -> None:
    """The retired command history pins the exact drainage-inventory target.

    The fixture recorded the full pre-retirement fallback on the old build:
    the ``profile_assigned`` signal, the version marker, and the defensive
    ``release_slot`` initiation against the manager. Histories containing that
    initiation predate the retirement and drain on the previous worker cohort
    per the deployment update contract; new code never emits it (see
    ``test_terminal_child_state_emits_no_release_slot``). This test pins the
    fixture contents so the inventory target cannot drift silently.
    """
    history = WorkflowHistory.from_json(
        "parent-fallback-commands", RETIRED_COMMAND_HISTORY.read_text()
    )
    received = _received_signals(history)
    assert "profile_assigned" in received
    assert "child_state_changed" in received
    patches = await _recorded_patches(history)
    assert patches.get(RETIRED_PATCH_ID) is False
    assert ("release_slot", RETIRED_MANAGER_ID) in _external_signals(history)


@pytest.mark.asyncio
async def test_terminal_child_state_emits_no_release_slot() -> None:
    """A terminal child notification writes no fallback state on new histories."""
    async with _parked_parent() as (_env, handle):
        await handle.signal(
            "child_state_changed", args=["completed", "Agent finished."]
        )
        # Allow any fire-and-forget emission to land before asserting absence.
        await asyncio.sleep(2)
        history = await handle.fetch_history()
        assert ("release_slot", RETIRED_MANAGER_ID) not in _external_signals(history)
        assert "release_slot" not in [
            name for name, _ in _external_signals(history)
        ]
        # Retirement records the marker as deprecated (a tombstone, not a
        # fallback write): new histories never carry the live marker.
        patches = await _recorded_patches(history)
        assert patches.get(RETIRED_PATCH_ID) is True
        # The parent stays healthy and recoverable: still parked, still serving
        # queries, with its normal recorded commands intact.
        assert (await handle.query("get_status"))["state"] == "waiting_on_dependencies"
        await Replayer(
            workflows=[MoonMindUserWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)


def test_fallback_surface_retired() -> None:
    """The retired behavioral surface leaves no handler or helper behind."""
    assert not hasattr(MoonMindUserWorkflow, "profile_assigned")
    assert not hasattr(MoonMindUserWorkflow, "_release_slot_defensive")
