"""Regression coverage for the PR #3999 review findings (issue #3879).

Each test pins one reviewer comment to a behavioral contract so the
remediation cannot regress silently: scope-gated single-flight validation,
waiter withdrawal on client exhaustion, fencing high-water survival, scope
usage that ignores local-only maintenance, fenced signal grants, fail-closed
malformed generations, verified duplicate grants, identity-scoped
revalidation budgets, and rollover that waits out the persistence handoff.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch as mock_patch

import pytest

from moonmind.provider_profiles.lease_client import CredentialLeasePurpose
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    PROVIDER_CAPACITY_SCOPE_PATCH,
    CapacityScopeState,
    MoonMindProviderProfileManagerWorkflow,
    PendingRequest,
    ProfileSlotState,
)

CAPACITIES = [1, 2, 4]
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
PROFILE_ID = "opencode-go-default"
SIBLING_ID = "opencode-go-sibling"
MANAGER_MODULE = (
    "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
)


class _BusyLoop(AssertionError):
    """Raised when a waiter wakes repeatedly without becoming grantable."""


class _ExternalHandle:
    """Records one external workflow handle's signals."""

    def __init__(self) -> None:
        self.signals: list[tuple[str, dict[str, Any]]] = []

    async def signal(self, name: str, payload: dict[str, Any]) -> None:
        self.signals.append((name, dict(payload)))


class _ExternalHandle:
    """Records one external workflow handle's signals."""

    def __init__(self) -> None:
        self.signals: list[tuple[str, dict[str, Any]]] = []

    async def signal(self, name: str, payload: dict[str, Any]) -> None:
        self.signals.append((name, dict(payload)))


class _FakeWorkflow:
    """Deterministic stand-in for the workflow module's blocking primitives."""

    max_waits = 6

    def __init__(self, *, patched: tuple[str, ...] = (), now: datetime = NOW) -> None:
        self._patched = set(patched)
        self._now = now
        self.logger = logging.getLogger(__name__)
        self.wait_calls = 0
        self._wake = asyncio.Event()
        self.handlers_finished: Callable[[], bool] = lambda: True
        self.activity_results: list[Any] = []
        self.activity_calls: list[tuple[str, Any]] = []
        self.external_handles: dict[str, _ExternalHandle] = {}

    def now(self) -> datetime:
        return self._now

    def patched(self, patch_id: str) -> bool:
        return patch_id in self._patched

    def all_handlers_finished(self) -> bool:
        return self.handlers_finished()

    def get_external_workflow_handle(self, workflow_id: str) -> _ExternalHandle:
        return self.external_handles.setdefault(workflow_id, _ExternalHandle())

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

    def wake(self) -> None:
        self._wake.set()


def _manager() -> MoonMindProviderProfileManagerWorkflow:
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
    scope_ref: str | None = None,
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
        capacity_scope_ref=scope_ref or f"provider-profile:{profile_id}",
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
) -> dict[str, Any]:
    return {
        "requester_workflow_id": owner,
        "owner_id": owner,
        "runtime_id": "opencode",
        "execution_profile_ref": profile_id,
        "purpose": purpose,
        "metadata": {"workflowId": owner, "ownerIsWorkflow": False},
    }


async def _settle() -> None:
    for _ in range(4):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# P1-5028: single-flight validation waits on the shared provider scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_single_flight_validation_waits_for_shared_scope(
    capacity: int,
) -> None:
    """A catalog probe must not exceed a saturated shared provider ceiling."""

    wf = _manager()
    credentialless = _install(
        wf,
        _profile(
            capacity,
            credential_source="none",
            profile_id="credless",
            scope_ref="shared",
        ),
    )
    sibling = _install(
        wf, _profile(capacity, profile_id=SIBLING_ID, scope_ref="shared")
    )
    wf._scopes["shared"] = CapacityScopeState(
        scope_ref="shared", configured_limit=1, effective_limit=1
    )
    # The sibling's execution spends the single shared unit.
    assert sibling.reserve(
        "exec-1", NOW, purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT.value
    )
    fake = _FakeWorkflow(patched=(PROVIDER_CAPACITY_SCOPE_PATCH,))
    owner = "profile-lease:credential_validation:probe-1"

    with mock_patch(MANAGER_MODULE, fake):
        task = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(
                _maintenance_payload(
                    owner,
                    purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
                    profile_id=credentialless.profile_id,
                )
            )
        )
        await _settle()
        assert not task.done(), "probe granted while the shared scope is full"
        sibling.release("exec-1")
        fake.wake()
        granted = await asyncio.wait_for(task, timeout=5)

    assert granted["lease_mode"] == "single_flight_validation"
    assert granted["already_held"] is False


# ---------------------------------------------------------------------------
# P2-5035: scope usage ignores local-only maintenance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capacity", [2, 4])
def test_scope_usage_ignores_local_maintenance_leases(capacity: int) -> None:
    """Repair and revocation fill no shared provider scope."""

    wf = _manager()
    profile = _install(wf, _profile(capacity, scope_ref="shared"))
    # Insert ledger rows directly: holding an exclusive repair lease would
    # (by design) block further reservations, which is not what this counts.
    profile.current_leases.extend(["exec-1", "repair-1", "disconnect-1"])
    profile.lease_metadata.update(
        {
            "exec-1": {"purpose": CredentialLeasePurpose.EXECUTION_OMNIGENT.value},
            "repair-1": {"purpose": CredentialLeasePurpose.CREDENTIAL_REPAIR.value},
            "disconnect-1": {
                "purpose": CredentialLeasePurpose.OAUTH_DISCONNECT.value
            },
        }
    )
    assert wf._scope_active_units("shared") == 1
    # An unrecognized purpose fails closed and counts against the scope.
    profile.current_leases.append("mystery-1")
    profile.lease_metadata["mystery-1"] = {"purpose": "mystery"}
    assert wf._scope_active_units("shared") == 2


# ---------------------------------------------------------------------------
# P1-5040: a present malformed generation fails closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_malformed_fencing_generation_release_fails_closed(
    capacity: int,
) -> None:
    """A corrupted generation must not bypass the stale-release fence."""

    wf = _manager()
    profile = _install(wf, _profile(capacity, credential_source="none"))
    fake = _FakeWorkflow()
    owner = "profile-lease:credential_repair:deadbeef"

    with mock_patch(MANAGER_MODULE, fake):
        granted = await wf.acquire_credential_maintenance_lease(
            _maintenance_payload(owner)
        )
        assert granted["lease_fencing_generation"] > 0
        for malformed in ("not-a-number", {"nested": 1}, [1]):
            await wf.release_slot(
                {
                    "profile_id": PROFILE_ID,
                    "requester_workflow_id": owner,
                    "fencing_generation": malformed,
                }
            )
            assert profile.current_leases == [owner]
        # The exact grant still releases normally.
        await wf.release_slot(
            {
                "profile_id": PROFILE_ID,
                "requester_workflow_id": owner,
                "fencing_generation": granted["lease_fencing_generation"],
            }
        )

    assert profile.current_leases == []


# ---------------------------------------------------------------------------
# P1-5038: signal grants carry the fencing generation end to end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_drain_queue_grant_carries_fencing_generation(
    capacity: int,
) -> None:
    """The main AgentRun path must fence like the Update paths."""

    wf = _manager()
    profile = _install(wf, _profile(capacity))
    fake = _FakeWorkflow()
    wf._pending_requests.append(
        PendingRequest(
            requester_workflow_id="agent-run-7",
            runtime_id="opencode",
            execution_profile_ref=PROFILE_ID,
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT.value,
            lease_metadata={},
        )
    )

    with mock_patch(MANAGER_MODULE, fake):
        await wf._drain_queue()

    assert wf._pending_requests == []
    handle = fake.external_handles["agent-run-7"]
    assert len(handle.signals) == 1
    name, payload = handle.signals[0]
    assert name == "slot_assigned"
    assert payload["profile_id"] == PROFILE_ID
    generation = payload.get("fencing_generation")
    assert isinstance(generation, int) and generation > 0
    assert profile.lease_fencing_generation("agent-run-7") == generation

    # A delayed duplicate release quoting an older generation cannot free it,
    # while the holder quoting its own generation releases normally.
    with mock_patch(MANAGER_MODULE, fake):
        await wf.release_slot(
            {
                "profile_id": PROFILE_ID,
                "requester_workflow_id": "agent-run-7",
                "fencing_generation": generation - 1 if generation > 1 else generation + 1,
            }
        )
        assert profile.current_leases == ["agent-run-7"]
        await wf.release_slot(
            {
                "profile_id": PROFILE_ID,
                "requester_workflow_id": "agent-run-7",
                "fencing_generation": generation,
            }
        )
    assert profile.current_leases == []


@pytest.mark.asyncio
async def test_drain_queue_without_fencing_keeps_the_legacy_signal() -> None:
    """Pre-fencing histories keep the exact legacy reservation and signal."""

    wf = _manager()
    wf._durable_maintenance_queue = False
    profile = _install(wf, _profile(1))
    fake = _FakeWorkflow()
    wf._pending_requests.append(
        PendingRequest(
            requester_workflow_id="agent-run-9",
            runtime_id="opencode",
            execution_profile_ref=PROFILE_ID,
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT.value,
            lease_metadata={},
        )
    )

    with mock_patch(MANAGER_MODULE, fake):
        await wf._drain_queue()

    _, payload = fake.external_handles["agent-run-9"].signals[0]
    assert "fencing_generation" not in payload
    assert profile.lease_fencing_generation("agent-run-9") == 0


# ---------------------------------------------------------------------------
# P1-5046: rollover waits out the persistence handoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollover_detach_waits_for_uncommitted_grant_handoff() -> None:
    """Snapshotting mid-handoff would publish uncommitted authority."""

    wf = _manager()
    fake = _FakeWorkflow()
    wf._begin_grant_handoff("owner-in-handoff")

    with mock_patch(MANAGER_MODULE, fake):
        task = asyncio.ensure_future(wf._detach_handlers_for_rollover())
        await _settle()
        assert not task.done(), "rollover snapshotted an uncommitted grant"
        wf._end_grant_handoff("owner-in-handoff")
        fake.wake()
        await asyncio.wait_for(task, timeout=5)

    assert wf._rollover_requested is True


@pytest.mark.asyncio
async def test_rollover_detach_proceeds_without_handoffs() -> None:
    """The handoff wait adds no delay when nothing is in flight."""

    wf = _manager()
    fake = _FakeWorkflow()

    with mock_patch(MANAGER_MODULE, fake):
        await asyncio.wait_for(wf._detach_handlers_for_rollover(), timeout=5)

    assert wf._rollover_requested is True


# ---------------------------------------------------------------------------
# P1-5031: the withdraw signal removes only the waiter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_withdraw_maintenance_waiter_removes_only_the_waiter(
    capacity: int,
) -> None:
    """Giving up a turn must not free capacity the owner still holds."""

    wf = _manager()
    profile = _install(wf, _profile(capacity))
    assert profile.reserve(
        "agent-run-0", NOW, purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT.value
    )
    profile.enqueue_maintenance_waiter(
        "repair-gone",
        purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
        queue_order=1,
        queued_at=NOW.isoformat(),
    )
    assert profile.maintenance_queue_position("repair-gone") == 0

    wf.withdraw_maintenance_waiter(
        {"profile_id": PROFILE_ID, "requester_workflow_id": "repair-gone"}
    )
    assert profile.maintenance_queue_position("repair-gone") == -1
    assert profile.current_leases == ["agent-run-0"]

    # Unknown owners and missing fields are harmless no-ops.
    wf.withdraw_maintenance_waiter(
        {"profile_id": PROFILE_ID, "requester_workflow_id": "nobody"}
    )
    wf.withdraw_maintenance_waiter({"profile_id": PROFILE_ID})
    assert profile.current_leases == ["agent-run-0"]


# ---------------------------------------------------------------------------
# P1-5033: a fresh manager resumes above the persisted high-water mark
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_start_absorbs_persisted_high_water_mark() -> None:
    """Tombstoned generations must survive the loss of every live lease."""

    wf = _manager()
    _install(wf, _profile(2))
    fake = _FakeWorkflow()
    fake.activity_results.append(
        {
            "leases": [
                {
                    "workflow_id": "repair-live",
                    "profile_id": PROFILE_ID,
                    "purpose": CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
                    "fencingGeneration": 7,
                    "safeMetadata": {},
                }
            ],
            "max_fencing_generation": 12,
        }
    )

    with mock_patch(MANAGER_MODULE, fake):
        assert await wf._load_leases_from_db() is True

    assert wf._lease_grant_sequence == 12
    profile = wf._profiles[PROFILE_ID]
    assert profile.lease_fencing_generation("repair-live") == 7
    # The next grant resumes above the high-water mark, never reusing it.
    assert wf._next_fencing_generation() == 13
