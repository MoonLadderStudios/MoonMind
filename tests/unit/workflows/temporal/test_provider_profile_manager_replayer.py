"""Production replay and worker-boundary coverage for slot-manager cleanup."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from temporalio import activity, exceptions, workflow
from temporalio.client import WorkflowHistory
from temporalio.converter import DataConverter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.provider_profiles.lease_client import LeaseTransitionOutcome
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    ACTIVITY_TASK_QUEUE,
    LEASE_CLEANUP_REDRIVE_PATCH,
    LEASE_TOMBSTONE_PURGE_PATCH,
    LEASE_TRANSITION_CONTRACT_PATCH,
    WORKFLOW_NAME,
    MoonMindProviderProfileManagerWorkflow,
)


@workflow.defn(name=WORKFLOW_NAME)
class _PreRedriveManagerWorkflow(MoonMindProviderProfileManagerWorkflow):
    """Pre-fix command order for the #4363 opencode wedge boundary.

    MoonLadderStudios/MoonMind#4363 event 506: the recorded manager history
    schedules the redriven ``provider_profile.sync_slot_leases``
    ``request_cleanup`` activity where pre-fix code issues the periodic 60s
    timer. Overriding only the two redrive steps keeps every startup marker
    and every earlier command identical, so replaying a post-marker redrive
    history against this variant diverges exactly at the Activity-vs-Timer
    boundary the incident wedged on.
    """

    async def _deliver_cleanup_requests(self) -> None:
        return None

    async def _complete_direct_cleanup_obligations(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture_name",
    [
        "provider_profile_manager_pre_tombstone_purge.json",
        "provider_profile_manager_maintenance_before_purge.json",
    ],
    ids=["before-maintenance", "maintenance-without-purge"],
)
async def test_pre_tombstone_purge_manager_history_replays(fixture_name: str) -> None:
    """A manager must verify its held lease before its recorded timer.

    The sanitized production prefix predates maintenance durability. The second
    fixture was recorded with b40da19f7, which already had that marker but no
    cleanup activity. Both must preserve the recorded lease verification order.
    """
    fixture = Path(__file__).with_name("fixtures") / fixture_name
    history = WorkflowHistory.from_json(
        "provider-profile-manager:opencode", fixture.read_text()
    )
    await Replayer(
        workflows=[MoonMindProviderProfileManagerWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


class _ProfileActivities:
    def __init__(
        self,
        runtime_id: str,
        fail_cleanup: bool,
        max_lease_duration_seconds: int | None = None,
    ) -> None:
        self.runtime_id = runtime_id
        self.fail_cleanup = fail_cleanup
        self.max_lease_duration_seconds = max_lease_duration_seconds
        self.actions: list[str] = []
        self.cleaned = asyncio.Event()
        self.verified = asyncio.Event()
        self.released = asyncio.Event()
        self.redriven = asyncio.Event()

    @activity.defn(name="provider_profile.list")
    async def list_profiles(self, request: dict[str, Any]) -> dict[str, Any]:
        assert request == {"runtime_id": self.runtime_id}
        profile: dict[str, Any] = {
            "profile_id": "test-default",
            "runtime_id": self.runtime_id,
            "credential_source": "api_key",
            "runtime_materialization_mode": "env",
            "max_parallel_runs": 1,
            "enabled": True,
            "launch_ready": True,
            "is_default": True,
        }
        if self.max_lease_duration_seconds is not None:
            profile["max_lease_duration_seconds"] = (
                self.max_lease_duration_seconds
            )
        return {"profiles": [profile]}

    @activity.defn(name="provider_profile.sync_slot_leases")
    async def sync_leases(self, request: dict[str, Any]) -> dict[str, Any]:
        assert request["runtime_id"] == self.runtime_id
        action = request["action"]
        self.actions.append(action)
        if action == "purge_released":
            assert request["leases"] == [{"older_than_seconds": 30 * 24 * 3600}]
            self.cleaned.set()
            if self.fail_cleanup:
                raise exceptions.ApplicationError(
                    "cleanup unavailable", non_retryable=True
                )
        if action == "release_one":
            # MoonLadderStudios/MoonMind#3883: a release answers with an
            # explicit outcome. The manager frees capacity only for this one.
            self.released.set()
            return {
                "released": True,
                "outcome": LeaseTransitionOutcome.RELEASED.value,
            }
        if action == "request_cleanup":
            # MoonLadderStudios/MoonMind#1089: an expired lease keeps its
            # slot spent while the ledger records the durable cleanup
            # request. The second recorded request proves the redrive
            # re-issued the same stable claim on a later pass.
            if self.actions.count("request_cleanup") >= 2:
                self.redriven.set()
            return {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            }
        return {"leases": [], "synced": len(request.get("leases", []))}

    @activity.defn(name="provider_profile.pending_request_order")
    async def pending_order(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"orders": {owner: {} for owner in request["workflow_ids"]}}

    @activity.defn(name="provider_profile.verify_lease_holders")
    async def verify(self, request: dict[str, Any]) -> dict[str, Any]:
        self.verified.set()
        # A novel status label must not discard the positively verified owner.
        return {
            owner: {"running": True, "status": "NEW_STATUS"}
            for owner in request["workflow_ids"]
        }


@workflow.defn(name="Test.CleanupSlotRequester")
class _SlotRequester:
    def __init__(self) -> None:
        self.assignment: dict[str, Any] | None = None
        self.assignments: list[dict[str, Any]] = []
        self.stopped = False

    @workflow.signal
    def slot_assigned(self, payload: dict[str, Any]) -> None:
        self.assignment = payload
        self.assignments.append(payload)

    @workflow.signal
    def shutdown(self) -> None:
        self.stopped = True

    @workflow.query
    def assigned(self) -> dict[str, Any] | None:
        return self.assignment

    @workflow.query
    def assignment_count(self) -> int:
        return len(self.assignments)

    @workflow.run
    async def run(self) -> None:
        await workflow.wait_condition(lambda: self.stopped)


async def _wait_for_assignments(handle: Any, expected: int) -> None:
    """Poll the requester until it has received ``expected`` assignments."""

    while await handle.query(_SlotRequester.assignment_count) < expected:
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_id", ["opencode", "codex_cli", "claude_code"])
@pytest.mark.parametrize(
    "fail_cleanup", [False, True], ids=["cleanup-ok", "cleanup-failed"]
)
async def test_current_manager_cleans_then_grants_and_replays(
    runtime_id: str, fail_cleanup: bool
) -> None:
    """Default cleanup, including failure, preserves durable grant authority."""
    activities = _ProfileActivities(runtime_id, fail_cleanup)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-profile-cleanup",
            workflows=[MoonMindProviderProfileManagerWorkflow, _SlotRequester],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ), Worker(
            env.client,
            task_queue=ACTIVITY_TASK_QUEUE,
            activities=[
                activities.list_profiles,
                activities.sync_leases,
                activities.pending_order,
                activities.verify,
            ],
        ):
            manager = await env.client.start_workflow(
                MoonMindProviderProfileManagerWorkflow.run,
                {"runtime_id": runtime_id},
                id=f"provider-profile-manager:{runtime_id}",
                task_queue="test-profile-cleanup",
            )
            await asyncio.wait_for(activities.cleaned.wait(), timeout=15)
            requester = await env.client.start_workflow(
                _SlotRequester.run,
                id="test-slot-requester",
                task_queue="test-profile-cleanup",
            )
            # The omitted profile selector must choose the default profile.
            await manager.signal(
                "request_slot",
                {
                    "requester_workflow_id": requester.id,
                    "runtime_id": runtime_id,
                },
            )
            await asyncio.wait_for(activities.verified.wait(), timeout=15)
            # Activity observation does not order a separate workflow's signal
            # delivery. Wait for the recipient's authoritative assignment.
            async with asyncio.timeout(15):
                while (
                    assignment := await requester.query(_SlotRequester.assigned)
                ) is None:
                    await asyncio.sleep(0.01)
            assert assignment["profile_id"] == "test-default"
            assert assignment["fencing_generation"] > 0
            state = await manager.query("get_state")
            assert state["profiles"]["test-default"]["current_leases"] == [requester.id]
            assert "purge_released" in activities.actions
            # result() enables global time skipping. Keep the idle requester
            # alive until its shutdown has been processed too; otherwise waiting
            # on the manager can advance it to the test server's run timeout.
            with env.auto_time_skipping_disabled():
                await manager.signal("shutdown")
                await requester.signal(_SlotRequester.shutdown)
                assert (await manager.result())["status"] == "shutdown"
                await requester.result()
            history = await manager.fetch_history()

    # Assert the production command handoff, then replay the new history too.
    commands = []
    patch_ids = []
    for event in history.events:
        if event.HasField("marker_recorded_event_attributes"):
            attrs = event.marker_recorded_event_attributes
            if attrs.marker_name == "core_patch":
                payload = (
                    await DataConverter.default.decode(
                        attrs.details["patch-data"].payloads
                    )
                )[0]
                patch_ids.append(payload["id"])
        elif event.HasField("activity_task_scheduled_event_attributes"):
            attrs = event.activity_task_scheduled_event_attributes
            if attrs.activity_type.name == "provider_profile.sync_slot_leases":
                payload = (await DataConverter.default.decode(attrs.input.payloads))[0]
                commands.append(payload["action"])
        elif event.HasField(
            "signal_external_workflow_execution_initiated_event_attributes"
        ):
            attrs = event.signal_external_workflow_execution_initiated_event_attributes
            if attrs.signal_name == "slot_assigned":
                commands.append("slot_assigned")
    assert LEASE_TOMBSTONE_PURGE_PATCH in patch_ids
    assert (
        commands.index("purge_released")
        < commands.index("grant")
        < commands.index("slot_assigned")
    )
    await Replayer(
        workflows=[MoonMindProviderProfileManagerWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
async def test_lease_cleanup_redrive_marker_pins_redrive_order() -> None:
    """The #1089 redrive must be versioned independently of the transition contract.

    Pre-#4330 histories recorded only request-cleanup + retry-unresolved per
    loop. Post-#4330 histories add deliver-cleanup + complete-direct per loop.
    Without an independent marker the two generations are indistinguishable and
    replay wedges with Activity-vs-Timer nondeterminism, blocking the
    credential-maintenance lease and thus deployment requalification for
    opencode-go-default (no admissible execution evidence).
    """
    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        LEASE_CLEANUP_REDRIVE_PATCH,
    )

    runtime_id = "opencode"
    activities = _ProfileActivities(runtime_id, fail_cleanup=False)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-lease-cleanup-redrive",
            workflows=[MoonMindProviderProfileManagerWorkflow, _SlotRequester],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ), Worker(
            env.client,
            task_queue=ACTIVITY_TASK_QUEUE,
            activities=[
                activities.list_profiles,
                activities.sync_leases,
                activities.pending_order,
                activities.verify,
            ],
        ):
            manager = await env.client.start_workflow(
                MoonMindProviderProfileManagerWorkflow.run,
                {"runtime_id": runtime_id},
                id=f"provider-profile-manager:{runtime_id}",
                task_queue="test-lease-cleanup-redrive",
            )
            await asyncio.wait_for(activities.cleaned.wait(), timeout=15)
            with env.auto_time_skipping_disabled():
                await manager.signal("shutdown")
                assert (await manager.result())["status"] == "shutdown"
            history = await manager.fetch_history()

    patch_ids: list[str] = []
    for event in history.events:
        if event.HasField("marker_recorded_event_attributes"):
            attrs = event.marker_recorded_event_attributes
            if attrs.marker_name == "core_patch":
                payload = (
                    await DataConverter.default.decode(
                        attrs.details["patch-data"].payloads
                    )
                )[0]
                patch_ids.append(payload["id"])
    assert LEASE_CLEANUP_REDRIVE_PATCH in patch_ids
    await Replayer(
        workflows=[MoonMindProviderProfileManagerWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
async def test_lease_cleanup_redrive_replays_with_outstanding_obligation() -> None:
    """The redrive marker must pin a history that actually redrove cleanup.

    PR #4425 review (comment 4048923511): the marker-only test shuts the
    manager down with no leases, so no cleanup obligation ever exists and
    the redrive activities never appear in the recorded history — it cannot
    catch Activity-vs-Timer nondeterminism for the affected cohort (a
    manager with an outstanding cleanup obligation). This test grants a
    lease, lets it expire past a short max duration, waits until the
    redrive re-issues the stable claim on a later pass, and then requires
    the full history (marker plus repeated request_cleanup activities) to
    replay against the current workflow code.
    """
    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        LEASE_CLEANUP_REDRIVE_PATCH,
    )

    runtime_id = "opencode"
    activities = _ProfileActivities(
        runtime_id, fail_cleanup=False, max_lease_duration_seconds=60
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-lease-cleanup-redrive-obligation",
            workflows=[MoonMindProviderProfileManagerWorkflow, _SlotRequester],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ), Worker(
            env.client,
            task_queue=ACTIVITY_TASK_QUEUE,
            activities=[
                activities.list_profiles,
                activities.sync_leases,
                activities.pending_order,
                activities.verify,
            ],
        ):
            manager = await env.client.start_workflow(
                MoonMindProviderProfileManagerWorkflow.run,
                {"runtime_id": runtime_id},
                id=f"provider-profile-manager:{runtime_id}",
                task_queue="test-lease-cleanup-redrive-obligation",
            )
            await asyncio.wait_for(activities.cleaned.wait(), timeout=15)
            requester = await env.client.start_workflow(
                _SlotRequester.run,
                id="test-slot-requester-redrive",
                task_queue="test-lease-cleanup-redrive-obligation",
            )
            await manager.signal(
                "request_slot",
                {
                    "requester_workflow_id": requester.id,
                    "runtime_id": runtime_id,
                },
            )
            async with asyncio.timeout(15):
                while (
                    assignment := await requester.query(_SlotRequester.assigned)
                ) is None:
                    await asyncio.sleep(0.01)
            assert assignment["profile_id"] == "test-default"
            # The live holder keeps its slot while the lease expires, so the
            # manager owes a cleanup redrive instead of a release.
            await asyncio.wait_for(activities.redriven.wait(), timeout=120)
            state = await manager.query("get_state")
            assert requester.id in state["cleanup_requested_leases"]
            with env.auto_time_skipping_disabled():
                await manager.signal("shutdown")
                await requester.signal(_SlotRequester.shutdown)
                assert (await manager.result())["status"] == "shutdown"
                await requester.result()
            history = await manager.fetch_history()

    patch_ids: list[str] = []
    redrive_requests = 0
    for event in history.events:
        if event.HasField("marker_recorded_event_attributes"):
            attrs = event.marker_recorded_event_attributes
            if attrs.marker_name == "core_patch":
                payload = (
                    await DataConverter.default.decode(
                        attrs.details["patch-data"].payloads
                    )
                )[0]
                patch_ids.append(payload["id"])
        elif event.HasField("activity_task_scheduled_event_attributes"):
            attrs = event.activity_task_scheduled_event_attributes
            if attrs.activity_type.name == "provider_profile.sync_slot_leases":
                payload = (await DataConverter.default.decode(attrs.input.payloads))[0]
                if payload.get("action") == "request_cleanup":
                    redrive_requests += 1
    assert LEASE_CLEANUP_REDRIVE_PATCH in patch_ids
    assert redrive_requests >= 2, (
        "the recorded history must contain the initial cleanup request and "
        "at least one redrive of the same stable claim"
    )
    await Replayer(
        workflows=[MoonMindProviderProfileManagerWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
async def test_the_lease_transition_contract_replays_from_its_own_history() -> None:
    """The durable ordering contract is pinned by production replay.

    MoonLadderStudios/MoonMind#3883: the grant must commit before the slot is
    signalled, the release must reach the ledger before capacity is published,
    and the whole history must replay against the current workflow code. A
    release that never reaches an explicit outcome must not free the slot.
    """

    runtime_id = "opencode"
    activities = _ProfileActivities(runtime_id, fail_cleanup=False)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-lease-transition",
            workflows=[MoonMindProviderProfileManagerWorkflow, _SlotRequester],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ), Worker(
            env.client,
            task_queue=ACTIVITY_TASK_QUEUE,
            activities=[
                activities.list_profiles,
                activities.sync_leases,
                activities.pending_order,
                activities.verify,
            ],
        ):
            manager = await env.client.start_workflow(
                MoonMindProviderProfileManagerWorkflow.run,
                {"runtime_id": runtime_id},
                id=f"provider-profile-manager:{runtime_id}",
                task_queue="test-lease-transition",
            )
            await asyncio.wait_for(activities.cleaned.wait(), timeout=15)
            requester = await env.client.start_workflow(
                _SlotRequester.run,
                id="test-lease-transition-requester",
                task_queue="test-lease-transition",
            )
            await manager.signal(
                "request_slot",
                {
                    "requester_workflow_id": requester.id,
                    "runtime_id": runtime_id,
                },
            )
            await asyncio.wait_for(activities.verified.wait(), timeout=15)
            # Activity observation does not order a separate workflow's signal
            # delivery. Wait for the recipient's authoritative assignment.
            async with asyncio.timeout(15):
                while (
                    assignment := await requester.query(_SlotRequester.assigned)
                ) is None:
                    await asyncio.sleep(0.01)
            assert assignment["profile_id"] == "test-default"

            # The production recovery shape: a slot wait times out and the run
            # re-sends request_slot to a manager that still holds its lease.
            # The manager re-signals the held slot without rewriting the
            # runtime-wide snapshot (MoonLadderStudios/MoonMind#3883).
            await manager.signal(
                "request_slot",
                {
                    "requester_workflow_id": requester.id,
                    "runtime_id": runtime_id,
                },
            )
            await asyncio.wait_for(_wait_for_assignments(requester, 2), timeout=15)
            re_assignment = await requester.query(_SlotRequester.assigned)
            assert re_assignment == assignment

            await manager.signal(
                "release_slot",
                {
                    "profile_id": "test-default",
                    "requester_workflow_id": requester.id,
                    "fencing_generation": assignment["fencing_generation"],
                },
            )
            await asyncio.wait_for(activities.released.wait(), timeout=15)

            with env.auto_time_skipping_disabled():
                await manager.signal("shutdown")
                await requester.signal(_SlotRequester.shutdown)
                assert (await manager.result())["status"] == "shutdown"
                await requester.result()
            history = await manager.fetch_history()

    commands: list[str] = []
    patch_ids: list[str] = []
    for event in history.events:
        if event.HasField("marker_recorded_event_attributes"):
            attrs = event.marker_recorded_event_attributes
            if attrs.marker_name == "core_patch":
                payload = (
                    await DataConverter.default.decode(
                        attrs.details["patch-data"].payloads
                    )
                )[0]
                patch_ids.append(payload["id"])
        elif event.HasField("activity_task_scheduled_event_attributes"):
            attrs = event.activity_task_scheduled_event_attributes
            if attrs.activity_type.name == "provider_profile.sync_slot_leases":
                payload = (await DataConverter.default.decode(attrs.input.payloads))[0]
                commands.append(payload["action"])
        elif event.HasField(
            "signal_external_workflow_execution_initiated_event_attributes"
        ):
            attrs = event.signal_external_workflow_execution_initiated_event_attributes
            if attrs.signal_name == "slot_assigned":
                commands.append("slot_assigned")

    assert LEASE_TRANSITION_CONTRACT_PATCH in patch_ids
    # The grant is durable before the consumer is told it has capacity, and the
    # release reaches the ledger before the slot is published as reusable.
    assert (
        commands.index("grant")
        < commands.index("slot_assigned")
        < commands.index("release_one")
    )
    # The re-signal announced the same lease again and cost no durable write:
    # one grant, two assignments, one release.
    assert commands.count("slot_assigned") == 2
    assert commands.count("grant") == 1
    assert commands.count("release_one") == 1
    # No path in a new history rewrites the runtime-wide snapshot: not the
    # grant, not the re-signal drain, not the release, not the reclamation.
    assert "save" not in commands

    await Replayer(
        workflows=[MoonMindProviderProfileManagerWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
async def test_opencode_506_boundary_replays_patched_and_fails_prefix() -> None:
    """The #4363 opencode wedge boundary replays on patched code only.

    MoonLadderStudios/MoonMind#4363: the recorded
    ``provider-profile-manager:opencode`` history wedged at event 506
    because one workflow-task completion scheduled the redriven
    ``provider_profile.sync_slot_leases`` ``request_cleanup`` activity (the
    event 504 analogue) ahead of the periodic 60s timer (the event 506
    analogue), while pre-fix code issues the timer where the redrive
    activity now sits. The recorded production prefix is not checked in, so
    this test rebuilds that boundary live: an outstanding cleanup
    obligation past a short max duration, whose redrive re-issues the same
    stable claim on a later pass. It then requires:

    * the redrive marker plus both ``request_cleanup`` commands carrying one
      stable claim (same lease identity and fencing generation);
    * the redrive activity scheduled before the next periodic 60s timer —
      the 504-before-506 shape;
    * clean Replayer replay of the recorded history on current code;
    * Replayer failure against the pre-redrive command order, proving this
      test would have caught the wedge.
    """

    runtime_id = "opencode"
    activities = _ProfileActivities(
        runtime_id, fail_cleanup=False, max_lease_duration_seconds=60
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-opencode-506-boundary",
            workflows=[MoonMindProviderProfileManagerWorkflow, _SlotRequester],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ), Worker(
            env.client,
            task_queue=ACTIVITY_TASK_QUEUE,
            activities=[
                activities.list_profiles,
                activities.sync_leases,
                activities.pending_order,
                activities.verify,
            ],
        ):
            manager = await env.client.start_workflow(
                MoonMindProviderProfileManagerWorkflow.run,
                {"runtime_id": runtime_id},
                id=f"provider-profile-manager:{runtime_id}",
                task_queue="test-opencode-506-boundary",
            )
            await asyncio.wait_for(activities.cleaned.wait(), timeout=15)
            requester = await env.client.start_workflow(
                _SlotRequester.run,
                id="test-slot-requester-506",
                task_queue="test-opencode-506-boundary",
            )
            await manager.signal(
                "request_slot",
                {
                    "requester_workflow_id": requester.id,
                    "runtime_id": runtime_id,
                },
            )
            async with asyncio.timeout(15):
                while (
                    assignment := await requester.query(_SlotRequester.assigned)
                ) is None:
                    await asyncio.sleep(0.01)
            assert assignment["profile_id"] == "test-default"
            # The live holder keeps its slot while the lease expires, so the
            # manager owes a cleanup redrive instead of a release.
            await asyncio.wait_for(activities.redriven.wait(), timeout=120)
            state = await manager.query("get_state")
            assert requester.id in state["cleanup_requested_leases"]
            with env.auto_time_skipping_disabled():
                await manager.signal("shutdown")
                await requester.signal(_SlotRequester.shutdown)
                assert (await manager.result())["status"] == "shutdown"
                await requester.result()
            history = await manager.fetch_history()

    patch_ids: list[str] = []
    cleanup_claims: list[dict[str, Any]] = []
    cleanup_event_indices: list[int] = []
    periodic_timer_indices: list[int] = []
    for index, event in enumerate(history.events):
        if event.HasField("marker_recorded_event_attributes"):
            attrs = event.marker_recorded_event_attributes
            if attrs.marker_name == "core_patch":
                payload = (
                    await DataConverter.default.decode(
                        attrs.details["patch-data"].payloads
                    )
                )[0]
                patch_ids.append(payload["id"])
        elif event.HasField("activity_task_scheduled_event_attributes"):
            attrs = event.activity_task_scheduled_event_attributes
            if attrs.activity_type.name == "provider_profile.sync_slot_leases":
                payload = (await DataConverter.default.decode(attrs.input.payloads))[0]
                if payload.get("action") == "request_cleanup":
                    cleanup_claims.append(payload)
                    cleanup_event_indices.append(index)
        elif event.HasField("timer_started_event_attributes"):
            timer_attrs = event.timer_started_event_attributes
            timeout = timer_attrs.start_to_fire_timeout.ToTimedelta()
            if timeout.total_seconds() == 60:
                periodic_timer_indices.append(index)
    assert LEASE_CLEANUP_REDRIVE_PATCH in patch_ids
    assert len(cleanup_claims) >= 2, (
        "the recorded history must contain the initial cleanup request and "
        "at least one redrive of the same stable claim"
    )
    first_claim = cleanup_claims[0]["leases"][0]
    redrive_claim = cleanup_claims[1]["leases"][0]
    assert redrive_claim["lease_id"] == first_claim["lease_id"]
    assert redrive_claim["fencing_generation"] == first_claim["fencing_generation"]
    # The 504-before-506 shape: the redriven cleanup activity is scheduled
    # before the next periodic 60s timer.
    redrive_index = cleanup_event_indices[1]
    assert any(
        timer_index > redrive_index for timer_index in periodic_timer_indices
    ), "the redrive activity must precede the periodic 60s timer"
    await Replayer(
        workflows=[MoonMindProviderProfileManagerWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)

    # Pre-fix code issues the periodic timer where the recorded history has
    # the redrive activity, so replay must diverge at that boundary. The
    # definition check below keeps this from passing vacuously on a variant
    # the replayer refuses to load.
    definition = getattr(
        _PreRedriveManagerWorkflow, "__temporal_workflow_definition", None
    )
    assert definition is not None
    assert definition.name == WORKFLOW_NAME
    with pytest.raises(Exception):
        await Replayer(
            workflows=[_PreRedriveManagerWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)
