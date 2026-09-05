"""Lease-mode durability, maintenance fairness, and validation under load.

Source issue: MoonLadderStudios/MoonMind#3879.

MoonLadderStudios/MoonMind#3878 established the three lease modes. What was
still missing was the authority and recovery contract around them: a pending
exclusive maintenance request that survives Continue-As-New with its owner and
its turn, a wait predicate that matches the real grant conditions instead of
waking on a condition that cannot become a grant, a release that cannot free
the *next* holder of the same deterministic owner ID, and a validation identity
complete enough that coalescing onto it is safe.

Every manager-boundary test here runs against the real workflow handlers and is
parametrized over the full deployment-selectable capacity range, so a rule that
only holds at one size fails here rather than in a deployment.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch as mock_patch

import pytest
from temporalio import exceptions

from moonmind.provider_profiles.lease_client import (
    MANAGER_ROLLOVER_ERROR_TYPE,
    CredentialLeasePurpose,
)
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    PROVIDER_CAPACITY_SCOPE_PATCH,
    CapacityScopeState,
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
)

#: The full deployment-selectable range this program must remain correct for.
CAPACITIES = [1, 2, 4, 8, 16]

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
PROFILE_ID = "opencode-go-default"
MANAGER_MODULE = (
    "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
)


class _BusyLoop(AssertionError):
    """Raised when a waiter wakes repeatedly without becoming grantable."""


class _FakeWorkflow:
    """A deterministic stand-in for the workflow module's blocking primitives.

    ``wait_condition`` suspends until the test wakes it, exactly like the real
    one suspends until the workflow makes progress. A waiter that keeps waking
    without being granted therefore shows up as repeated calls rather than as a
    test that quietly passes while production spins.
    """

    max_waits = 4

    def __init__(self, *, patched: tuple[str, ...] = (), now: datetime = NOW) -> None:
        self._patched = set(patched)
        self._now = now
        self.logger = logging.getLogger(__name__)
        self.wait_calls = 0
        self._wake = asyncio.Event()
        self.handlers_finished: Callable[[], bool] = lambda: True
        self.activity_results: list[Any] = []
        self.activity_calls: list[tuple[str, Any]] = []

    # -- workflow module surface used by the manager ------------------------

    def now(self) -> datetime:
        return self._now

    def patched(self, patch_id: str) -> bool:
        return patch_id in self._patched

    def all_handlers_finished(self) -> bool:
        return self.handlers_finished()

    async def wait_condition(self, predicate, timeout=None):
        self.wait_calls += 1
        if self.wait_calls > self.max_waits:
            raise _BusyLoop(
                f"waiter woke {self.wait_calls} times without becoming grantable"
            )
        while not predicate():
            self._wake.clear()
            await self._wake.wait()
        return True

    async def execute_activity(self, name, payload, **_kwargs):
        self.activity_calls.append((name, payload))
        if self.activity_results:
            return self.activity_results.pop(0)
        return {}

    # -- test controls ------------------------------------------------------

    def wake(self) -> None:
        self._wake.set()

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


def _manager() -> MoonMindProviderProfileManagerWorkflow:
    """A manager whose ledger and durability patches are both established."""

    wf = MoonMindProviderProfileManagerWorkflow()
    wf._runtime_id = "opencode"
    wf._purpose_aware_capacity_ledger = True
    wf._durable_maintenance_queue = True
    return wf


def _profile(
    capacity: int,
    *,
    credential_source: str = "oauth_volume",
    profile_id: str = PROFILE_ID,
) -> ProfileSlotState:
    return ProfileSlotState(
        profile_id=profile_id,
        max_parallel_runs=capacity,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        launch_ready=True,
        credential_source=credential_source,
        purpose_aware_capacity=True,
        capacity_scope_ref=f"provider-profile:{profile_id}",
        effective_limit=capacity,
    )


def _install(
    wf: MoonMindProviderProfileManagerWorkflow, profile: ProfileSlotState
) -> ProfileSlotState:
    wf._profiles[profile.profile_id] = profile
    return profile


def _maintenance_payload(
    owner: str,
    *,
    purpose: str = CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
    profile_id: str = PROFILE_ID,
    evidence_identity: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"workflowId": owner, "ownerIsWorkflow": False}
    if evidence_identity is not None:
        metadata["evidenceIdentity"] = evidence_identity
    return {
        "requester_workflow_id": owner,
        "owner_id": owner,
        "runtime_id": "opencode",
        "execution_profile_ref": profile_id,
        "purpose": purpose,
        "metadata": metadata,
    }


async def _settle() -> None:
    """Let a suspended handler reach its next await point."""

    for _ in range(4):
        await asyncio.sleep(0)


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# AC3: pending maintenance survives Continue-As-New with owner and order intact
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_pending_maintenance_rolls_over_with_owner_and_order(capacity: int) -> None:
    """A serialized waiter count loses exactly what fairness depends on."""

    wf = _manager()
    profile = _install(wf, _profile(capacity))
    for index, owner in enumerate(("repair-b", "repair-a", "repair-c")):
        wf._maintenance_queue_sequence += 1
        profile.enqueue_maintenance_waiter(
            owner,
            purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
            queue_order=wf._maintenance_queue_sequence,
            queued_at=(NOW + timedelta(seconds=index)).isoformat(),
        )

    rolled = wf._build_continue_as_new_input()

    successor = MoonMindProviderProfileManagerWorkflow()
    successor._purpose_aware_capacity_ledger = True
    successor._durable_maintenance_queue = True
    successor._runtime_id = "opencode"
    successor._restore_state(rolled)

    restored = successor._profiles[PROFILE_ID]
    assert [entry["ownerId"] for entry in restored.exclusive_maintenance_queue] == [
        "repair-b",
        "repair-a",
        "repair-c",
    ]
    assert restored.exclusive_maintenance_waiters == 3
    # The head still blocks new consumers, so the rollover cannot be a moment
    # when a busy profile quietly overtakes work that was already waiting.
    assert restored.is_available() is False
    # The sequence resumes above every restored order, so a request queued after
    # the rollover cannot be handed a position that is already taken.
    assert successor._maintenance_queue_sequence >= 3


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_a_waiter_without_an_owner_is_not_restored_as_an_admission_block(
    capacity: int,
) -> None:
    """The old bare count is unresumable, so it must not survive as a blocker."""

    wf = _manager()
    _install(wf, _profile(capacity))
    rolled = wf._build_continue_as_new_input()
    rolled["profiles"][0]["exclusive_maintenance_queue"] = [
        {"queueOrder": 1},  # a count-shaped entry with no owner
        {"ownerId": "repair-a", "purpose": "credential_repair", "queueOrder": 2},
    ]

    successor = MoonMindProviderProfileManagerWorkflow()
    successor._purpose_aware_capacity_ledger = True
    successor._durable_maintenance_queue = True
    successor._runtime_id = "opencode"
    successor._restore_state(rolled)

    restored = successor._profiles[PROFILE_ID]
    assert [entry["ownerId"] for entry in restored.exclusive_maintenance_queue] == [
        "repair-a"
    ]


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_rollover_detaches_the_handler_but_keeps_its_queued_request(
    capacity: int,
) -> None:
    """Either finish handlers before rollover or transfer them explicitly."""

    wf = _manager()
    profile = _install(wf, _profile(capacity))
    profile.reserve("agent-run-0", NOW, purpose="execution_omnigent")
    fake = _FakeWorkflow()

    with mock_patch(MANAGER_MODULE, fake):
        task = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(_maintenance_payload("repair-a"))
        )
        await _settle()
        assert profile.maintenance_queue_position("repair-a") == 0

        fake.handlers_finished = task.done
        rollover = asyncio.ensure_future(wf._detach_handlers_for_rollover())
        await _settle()
        fake.wake()
        with pytest.raises(exceptions.ApplicationError) as detached:
            await task
        fake.wake()
        await rollover

    assert detached.value.type == MANAGER_ROLLOVER_ERROR_TYPE
    assert detached.value.non_retryable is False
    # The request itself is durable: the reattaching client resumes this exact
    # entry rather than queueing a new one behind whoever arrived meanwhile.
    assert profile.maintenance_queue_position("repair-a") == 0
    rolled = wf._build_continue_as_new_input()["profiles"][0]
    assert [
        entry["ownerId"] for entry in rolled["exclusive_maintenance_queue"]
    ] == ["repair-a"]


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_reattaching_caller_keeps_its_place_in_line(capacity: int) -> None:
    """A caller retry must not send a request that already waited to the back."""

    wf = _manager()
    profile = _install(wf, _profile(capacity))
    profile.reserve("agent-run-0", NOW, purpose="execution_omnigent")
    fake = _FakeWorkflow()

    with mock_patch(MANAGER_MODULE, fake):
        first = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(_maintenance_payload("repair-a"))
        )
        await _settle()
        second = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(_maintenance_payload("repair-b"))
        )
        await _settle()
        assert profile.maintenance_queue_position("repair-a") == 0
        assert profile.maintenance_queue_position("repair-b") == 1

        # The first caller's Update is detached by a rollover and resubmitted.
        wf._rollover_requested = True
        fake.wake()
        with pytest.raises(exceptions.ApplicationError):
            await first
        with pytest.raises(exceptions.ApplicationError):
            await second
        wf._rollover_requested = False

        reattached = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(_maintenance_payload("repair-a"))
        )
        await _settle()
        assert profile.maintenance_queue_position("repair-a") == 0
        assert profile.maintenance_queue_position("repair-b") == 1

        await _cancel(reattached)


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_continuous_execution_cannot_starve_queued_maintenance(
    capacity: int,
) -> None:
    """Admission stops at the queue, not after the drain finishes."""

    wf = _manager()
    profile = _install(wf, _profile(capacity))
    holders = [f"agent-run-{index}" for index in range(capacity)]
    for holder in holders:
        assert profile.reserve(holder, NOW, purpose="execution_omnigent") is True
    fake = _FakeWorkflow()

    with mock_patch(MANAGER_MODULE, fake):
        task = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(_maintenance_payload("repair-a"))
        )
        await _settle()

        # Every running consumer finishes and immediately tries to come back.
        for holder in holders:
            profile.release(holder)
            assert profile.reserve(
                f"{holder}-replacement", NOW, purpose="execution_omnigent"
            ) is False
        fake.wake()
        granted = await task

    assert granted["already_held"] is False
    assert granted["lease_mode"] == "exclusive_maintenance"
    assert profile.exclusive_maintenance_queue == []


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_waiting_execution_acquisition_detaches_for_the_rollover(
    capacity: int,
) -> None:
    """A capacity waiter must not hold the manager's rollover open."""

    wf = _manager()
    profile = _install(wf, _profile(capacity, credential_source="none"))
    for index in range(capacity):
        profile.reserve(f"agent-run-{index}", NOW, purpose="execution_omnigent")
    fake = _FakeWorkflow()

    with mock_patch(MANAGER_MODULE, fake):
        task = asyncio.ensure_future(
            wf.acquire_slot_v2(
                {
                    "requester_workflow_id": "agent-run-waiting",
                    "runtime_id": "opencode",
                    "execution_profile_ref": PROFILE_ID,
                    "metadata": {"workflowId": "agent-run-waiting"},
                }
            )
        )
        await _settle()
        assert task.done() is False

        fake.handlers_finished = task.done
        rollover = asyncio.ensure_future(wf._detach_handlers_for_rollover())
        await _settle()
        fake.wake()
        with pytest.raises(exceptions.ApplicationError) as detached:
            await task
        fake.wake()
        await rollover

    assert detached.value.type == MANAGER_ROLLOVER_ERROR_TYPE
    # Nothing was granted, so the successor run's ledger is unchanged.
    assert "agent-run-waiting" not in profile.current_leases


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_an_abandoned_queue_head_does_not_wedge_the_profile(
    capacity: int,
) -> None:
    """A durable queue must not become a durable outage."""

    wf = _manager()
    profile = _install(wf, _profile(capacity))
    profile.reserve("agent-run-0", NOW, purpose="execution_omnigent")
    fake = _FakeWorkflow()

    with mock_patch(MANAGER_MODULE, fake):
        detached = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(_maintenance_payload("repair-a"))
        )
        await _settle()
        # The rollover detaches the request and its client never reattaches.
        wf._rollover_requested = True
        fake.wake()
        with pytest.raises(exceptions.ApplicationError):
            await detached
        wf._rollover_requested = False
        assert profile.maintenance_queue_position("repair-a") == 0
        assert profile.is_available() is False

        fake.advance(timedelta(hours=3))
        wf._evict_expired_leases()

    assert profile.exclusive_maintenance_queue == []
    # Admission is restored for everyone the abandoned head was blocking.
    assert profile.is_available() is True


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_a_waiter_that_cannot_state_when_it_queued_is_not_trusted(
    capacity: int,
) -> None:
    profile = _profile(capacity)
    profile.enqueue_maintenance_waiter(
        "repair-a",
        purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
        queue_order=1,
        queued_at="",
    )

    assert profile.evict_expired_maintenance_waiters(NOW, 5400) == ["repair-a"]
    assert profile.exclusive_maintenance_queue == []


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_a_waiter_still_inside_its_window_is_preserved(capacity: int) -> None:
    profile = _profile(capacity)
    profile.enqueue_maintenance_waiter(
        "repair-a",
        purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
        queue_order=1,
        queued_at=NOW.isoformat(),
    )

    assert (
        profile.evict_expired_maintenance_waiters(NOW + timedelta(minutes=5), 5400)
        == []
    )
    assert profile.maintenance_queue_position("repair-a") == 0


# ---------------------------------------------------------------------------
# AC4: the wait predicate is the grant condition
# ---------------------------------------------------------------------------


def _install_full_scope(
    wf: MoonMindProviderProfileManagerWorkflow, profile: ProfileSlotState
) -> CapacityScopeState:
    scope = CapacityScopeState(
        scope_ref=profile.capacity_scope_ref,
        runtime_id="opencode",
        configured_limit=1,
        effective_limit=1,
        cooldown_until=(NOW + timedelta(minutes=30)).isoformat(),
        backpressure_state="cooldown",
    )
    wf._scopes[scope.scope_ref] = scope
    return scope


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_full_scope_and_an_empty_profile_do_not_spin(capacity: int) -> None:
    """The old predicate woke on "no leases", which a full scope cannot grant."""

    wf = _manager()
    profile = _install(wf, _profile(capacity))
    _install_full_scope(wf, profile)
    fake = _FakeWorkflow(patched=(PROVIDER_CAPACITY_SCOPE_PATCH,))

    with mock_patch(MANAGER_MODULE, fake):
        task = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(
                _maintenance_payload(
                    "connect-a", purpose=CredentialLeasePurpose.OAUTH_CONNECT.value
                )
            )
        )
        await _settle()

        assert profile.current_leases == []
        assert (
            wf._maintenance_grant_blocker(
                profile_id=PROFILE_ID,
                requester_id="connect-a",
                scope_gated=True,
            )
            == "scope_unavailable"
        )
        # One wait, and it is still waiting: the predicate never claimed a
        # grant was possible while the shared scope was cooling down.
        assert fake.wait_calls == 1
        assert task.done() is False

        await _cancel(task)

    # A caller that gave up leaves no admission block behind.
    assert profile.exclusive_maintenance_queue == []
    assert profile.is_available() is True


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_full_scope_never_blocks_credential_repair_or_revocation(
    capacity: int,
) -> None:
    """Scope pressure is about the upstream resource repair does not spend."""

    wf = _manager()
    profile = _install(wf, _profile(capacity))
    _install_full_scope(wf, profile)
    fake = _FakeWorkflow(patched=(PROVIDER_CAPACITY_SCOPE_PATCH,))

    with mock_patch(MANAGER_MODULE, fake):
        repair = await wf.acquire_credential_maintenance_lease(
            _maintenance_payload(
                "repair-a", purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value
            )
        )

    assert repair["already_held"] is False
    assert repair["lease_mode"] == "exclusive_maintenance"
    assert fake.wait_calls == 0


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_profile_refresh_during_a_wait_is_not_a_stale_object(
    capacity: int,
) -> None:
    """The grant must land on the profile the manager currently publishes."""

    wf = _manager()
    profile = _install(wf, _profile(capacity))
    profile.reserve("agent-run-0", NOW, purpose="execution_omnigent")
    fake = _FakeWorkflow()

    with mock_patch(MANAGER_MODULE, fake):
        task = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(_maintenance_payload("repair-a"))
        )
        await _settle()

        # A DB refresh replaces the profile object the handler started with.
        refreshed = _profile(capacity)
        refreshed.exclusive_maintenance_queue = list(
            profile.exclusive_maintenance_queue
        )
        wf._profiles[PROFILE_ID] = refreshed
        fake.wake()
        granted = await task

    assert granted["profile_id"] == PROFILE_ID
    assert refreshed.current_leases == ["repair-a"]
    # The replaced object is not where authority was recorded.
    assert profile.current_leases == ["agent-run-0"]


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_canceled_maintainer_leaks_no_admission_block(capacity: int) -> None:
    wf = _manager()
    profile = _install(wf, _profile(capacity))
    profile.reserve("agent-run-0", NOW, purpose="execution_omnigent")
    fake = _FakeWorkflow()

    with mock_patch(MANAGER_MODULE, fake):
        task = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(_maintenance_payload("repair-a"))
        )
        await _settle()
        assert profile.exclusive_maintenance_waiters == 1

        await _cancel(task)

    assert profile.exclusive_maintenance_waiters == 0
    profile.release("agent-run-0")
    assert profile.is_available() is True


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_a_credentialless_profile_has_no_credential_resource_to_drain(
    capacity: int,
) -> None:
    """Incompatibility follows the resource, not "is anything running?"."""

    credential_bearing = _profile(capacity)
    credential_bearing.reserve("agent-run-0", NOW, purpose="execution_omnigent")
    credentialless = _profile(capacity, credential_source="none")
    credentialless.reserve("agent-run-0", NOW, purpose="execution_omnigent")

    assert credential_bearing.credential_consumer_leases == ["agent-run-0"]
    assert credentialless.credential_consumer_leases == []


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_two_handlers_for_one_owner_produce_one_lease(capacity: int) -> None:
    """A caller retry must not leave two releasers for one authority."""

    wf = _manager()
    profile = _install(wf, _profile(capacity))
    profile.reserve("agent-run-0", NOW, purpose="execution_omnigent")
    fake = _FakeWorkflow()

    with mock_patch(MANAGER_MODULE, fake):
        first = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(_maintenance_payload("repair-a"))
        )
        await _settle()
        retry = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(_maintenance_payload("repair-a"))
        )
        await _settle()
        # The retry reattaches to the one queued request rather than adding a
        # second waiter that would keep blocking admission after the grant.
        assert profile.exclusive_maintenance_waiters == 1

        profile.release("agent-run-0")
        fake.wake()
        results = [await first, await retry]

    assert sorted(result["already_held"] for result in results) == [False, True]
    assert profile.current_leases == ["repair-a"]
    assert profile.exclusive_maintenance_queue == []
    assert len({result["lease_fencing_generation"] for result in results}) == 1


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_held_maintenance_lease_survives_a_manager_restart(
    capacity: int,
) -> None:
    """A restart must restore the authority it granted, fence and identity."""

    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        DB_LEASE_PERSISTENCE_PATCH,
        DURABLE_LEASE_GRANT_PATCH,
        PROVIDER_INCREMENTAL_LEASE_PATCH,
    )

    identity = "opencode-model-catalog:v2:deadbeef"
    patches = (
        DB_LEASE_PERSISTENCE_PATCH,
        DURABLE_LEASE_GRANT_PATCH,
        PROVIDER_INCREMENTAL_LEASE_PATCH,
    )

    # A grant is recorded durably, then the manager is lost.
    wf = _manager()
    _install(wf, _profile(capacity, credential_source="none"))
    granting = _FakeWorkflow(patched=patches)
    with mock_patch(MANAGER_MODULE, granting):
        granted = await wf.acquire_credential_maintenance_lease(
            _maintenance_payload(
                "probe-a",
                purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
                evidence_identity=identity,
            )
        )
    persisted = granting.activity_calls[0][1]["leases"][0]

    successor = _manager()
    profile = _install(successor, _profile(capacity, credential_source="none"))
    restarted = _FakeWorkflow(patched=patches)
    restarted.activity_results.append(
        {
            "leases": [
                {
                    "workflow_id": persisted["workflow_id"],
                    "profile_id": persisted["profile_id"],
                    "leaseId": persisted["lease_id"],
                    "ownerId": persisted["owner_id"],
                    "purpose": persisted["purpose"],
                    "granted_at": NOW.isoformat(),
                    "fencingGeneration": persisted["fencing_generation"],
                    "safeMetadata": persisted["safe_metadata"],
                }
            ]
        }
    )

    with mock_patch(MANAGER_MODULE, restarted):
        assert await successor._load_leases_from_db() is True
        rejoin = await successor.acquire_credential_maintenance_lease(
            _maintenance_payload(
                "probe-a",
                purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
                evidence_identity=identity,
            )
        )
        stale_release = {
            "profile_id": PROFILE_ID,
            "requester_workflow_id": "probe-a",
            "fencing_generation": granted["lease_fencing_generation"] + 99,
        }
        await successor.release_slot(stale_release)

    assert profile.current_leases == ["probe-a"]
    assert profile.lease_evidence_identity("probe-a") == identity
    # The restored authority is the same grant, not a new one, so a maintainer
    # arriving after the restart stands down instead of re-probing.
    assert rejoin["already_held"] is True
    assert (
        rejoin["lease_fencing_generation"] == granted["lease_fencing_generation"]
    )
    # The sequence resumed above the restored generation, so the next grant
    # cannot reissue a number a stale release could still match.
    assert successor._lease_grant_sequence >= granted["lease_fencing_generation"]


# ---------------------------------------------------------------------------
# AC5: duplicate and stale release cannot free a replacement owner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_stale_release_cannot_free_a_replacement_holder(
    capacity: int,
) -> None:
    """Deterministic owner IDs are reused, so the release must be fenced."""

    wf = _manager()
    profile = _install(wf, _profile(capacity, credential_source="none"))
    fake = _FakeWorkflow()
    owner = "profile-lease:credential_repair:deadbeef"

    with mock_patch(MANAGER_MODULE, fake):
        first = await wf.acquire_credential_maintenance_lease(
            _maintenance_payload(owner)
        )
        stale_release = {
            "profile_id": PROFILE_ID,
            "requester_workflow_id": owner,
            "fencing_generation": first["lease_fencing_generation"],
        }
        await wf.release_slot(dict(stale_release))
        assert profile.current_leases == []

        replacement = await wf.acquire_credential_maintenance_lease(
            _maintenance_payload(owner)
        )
        assert (
            replacement["lease_fencing_generation"]
            != first["lease_fencing_generation"]
        )

        # The first releaser retries its signal after the owner ID was granted
        # again. It must not free the replacement holder's authority.
        await wf.release_slot(dict(stale_release))

    assert profile.current_leases == [owner]
    assert wf._profile_id_for_lease(owner) == PROFILE_ID


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_the_current_holder_can_still_release_itself(capacity: int) -> None:
    wf = _manager()
    profile = _install(wf, _profile(capacity, credential_source="none"))
    fake = _FakeWorkflow()
    owner = "profile-lease:credential_repair:deadbeef"

    with mock_patch(MANAGER_MODULE, fake):
        granted = await wf.acquire_credential_maintenance_lease(
            _maintenance_payload(owner)
        )
        await wf.release_slot(
            {
                "profile_id": PROFILE_ID,
                "requester_workflow_id": owner,
                "fencing_generation": granted["lease_fencing_generation"],
            }
        )

    assert profile.current_leases == []


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_release_from_an_in_flight_caller_without_a_fence_still_works(
    capacity: int,
) -> None:
    """A rollout must not leak capacity for leases already in flight."""

    wf = _manager()
    profile = _install(wf, _profile(capacity, credential_source="none"))
    fake = _FakeWorkflow()
    owner = "profile-lease:credential_repair:deadbeef"

    with mock_patch(MANAGER_MODULE, fake):
        await wf.acquire_credential_maintenance_lease(_maintenance_payload(owner))
        await wf.release_slot(
            {"profile_id": PROFILE_ID, "requester_workflow_id": owner}
        )

    assert profile.current_leases == []


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_exclusive_maintenance_waits_for_incompatible_consumers_to_stop(
    capacity: int,
) -> None:
    wf = _manager()
    profile = _install(wf, _profile(capacity))
    for index in range(capacity):
        profile.reserve(f"agent-run-{index}", NOW, purpose="execution_omnigent")
    fake = _FakeWorkflow()

    with mock_patch(MANAGER_MODULE, fake):
        task = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(_maintenance_payload("repair-a"))
        )
        await _settle()
        assert task.done() is False

        for index in range(capacity - 1):
            profile.release(f"agent-run-{index}")
        fake.wake()
        await _settle()
        assert task.done() is False, "one consumer still holds the credential"

        profile.release(f"agent-run-{capacity - 1}")
        fake.wake()
        granted = await task

    assert granted["lease_mode"] == "exclusive_maintenance"


# ---------------------------------------------------------------------------
# AC2: exactly one committed probe owns a complete validation identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_the_evidence_identity_is_recorded_on_the_lease_contract(
    capacity: int,
) -> None:
    wf = _manager()
    _install(wf, _profile(capacity, credential_source="none"))
    fake = _FakeWorkflow()
    identity = "opencode-model-catalog:v2:deadbeef"

    with mock_patch(MANAGER_MODULE, fake):
        granted = await wf.acquire_credential_maintenance_lease(
            _maintenance_payload(
                "probe-a",
                purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
                evidence_identity=identity,
            )
        )
        inspected = wf.inspect_credential_lease({"lease_id": "probe-a"})

    assert granted["lease_mode"] == "single_flight_validation"
    assert inspected["evidenceIdentity"] == identity
    assert wf._profiles[PROFILE_ID].lease_evidence_identity("probe-a") == identity


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_conflicting_evidence_identity_fails_closed(capacity: int) -> None:
    """An owner ID is authority for the exact evidence it was granted for."""

    wf = _manager()
    _install(wf, _profile(capacity, credential_source="none"))
    fake = _FakeWorkflow()

    with mock_patch(MANAGER_MODULE, fake):
        await wf.acquire_credential_maintenance_lease(
            _maintenance_payload(
                "probe-a",
                purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
                evidence_identity="opencode-model-catalog:v2:deadbeef",
            )
        )
        with pytest.raises(exceptions.ApplicationError) as conflict:
            await wf.acquire_credential_maintenance_lease(
                _maintenance_payload(
                    "probe-a",
                    purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
                    evidence_identity="opencode-model-catalog:v2:cafebabe",
                )
            )

    assert conflict.value.type == "ProviderProfileLeaseIdentityConflict"
    assert conflict.value.non_retryable is True


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_conflicting_purpose_for_the_same_owner_fails_closed(
    capacity: int,
) -> None:
    wf = _manager()
    _install(wf, _profile(capacity, credential_source="none"))
    fake = _FakeWorkflow()

    with mock_patch(MANAGER_MODULE, fake):
        await wf.acquire_credential_maintenance_lease(
            _maintenance_payload(
                "probe-a",
                purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
            )
        )
        with pytest.raises(exceptions.ApplicationError) as conflict:
            await wf.acquire_credential_maintenance_lease(
                _maintenance_payload(
                    "probe-a",
                    purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
                )
            )

    assert conflict.value.type == "ProviderProfileLeaseIdentityConflict"


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_joiner_stands_down_without_releasing_the_owners_lease(
    capacity: int,
) -> None:
    wf = _manager()
    profile = _install(wf, _profile(capacity, credential_source="none"))
    fake = _FakeWorkflow()
    payload = _maintenance_payload(
        "probe-a",
        purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
        evidence_identity="opencode-model-catalog:v2:deadbeef",
    )

    with mock_patch(MANAGER_MODULE, fake):
        owner = await wf.acquire_credential_maintenance_lease(dict(payload))
        joiner = await wf.acquire_credential_maintenance_lease(dict(payload))

    assert owner["already_held"] is False
    assert joiner["already_held"] is True
    # The joiner is handed the owner's generation, not a fresh one, so it can
    # never release authority it does not own.
    assert (
        joiner["lease_fencing_generation"] == owner["lease_fencing_generation"]
    )
    assert profile.current_leases == ["probe-a"]


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_an_abandoned_probe_is_recovered_rather_than_suppressed_forever(
    capacity: int,
) -> None:
    """Worker loss must not park a validation identity behind a dead holder."""

    wf = _manager()
    profile = _install(wf, _profile(capacity, credential_source="none"))
    fake = _FakeWorkflow()
    payload = _maintenance_payload(
        "probe-a",
        purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
        evidence_identity="opencode-model-catalog:v2:deadbeef",
    )

    with mock_patch(MANAGER_MODULE, fake):
        first = await wf.acquire_credential_maintenance_lease(dict(payload))
        # The worker running the probe is lost: no release ever arrives.
        fake.advance(timedelta(hours=2))
        assert wf._evict_expired_leases() == 1
        recovered = await wf.acquire_credential_maintenance_lease(dict(payload))

    assert first["already_held"] is False
    assert recovered["already_held"] is False
    assert (
        recovered["lease_fencing_generation"] != first["lease_fencing_generation"]
    )
    assert profile.current_leases == ["probe-a"]


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_failed_pending_grant_is_not_mistaken_for_completion(
    capacity: int,
) -> None:
    """Only a committed grant is authority to probe."""

    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        DB_LEASE_PERSISTENCE_PATCH,
        DURABLE_LEASE_GRANT_PATCH,
        PROVIDER_INCREMENTAL_LEASE_PATCH,
    )

    wf = _manager()
    profile = _install(wf, _profile(capacity, credential_source="none"))
    fake = _FakeWorkflow(
        patched=(
            DB_LEASE_PERSISTENCE_PATCH,
            DURABLE_LEASE_GRANT_PATCH,
            PROVIDER_INCREMENTAL_LEASE_PATCH,
        )
    )
    fake.activity_results.append({"error": "lease identity conflict"})
    payload = _maintenance_payload(
        "probe-a",
        purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
        evidence_identity="opencode-model-catalog:v2:deadbeef",
    )

    with mock_patch(MANAGER_MODULE, fake), pytest.raises(
        exceptions.ApplicationError
    ) as failure:
        await wf.acquire_credential_maintenance_lease(dict(payload))

    assert failure.value.type == "ProviderProfileLeasePersistenceFailed"
    # Nothing is held, so the next maintainer is a fresh owner rather than a
    # joiner standing down behind a probe that never started.
    assert profile.current_leases == []
    assert wf._profile_id_for_lease("probe-a") is None
    granted_row = fake.activity_calls[0][1]["leases"][0]
    assert granted_row["safe_metadata"] == {
        "evidenceIdentity": "opencode-model-catalog:v2:deadbeef"
    }
    assert granted_row["fencing_generation"] == 1
