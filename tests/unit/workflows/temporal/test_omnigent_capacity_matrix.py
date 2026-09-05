"""Configured Provider Profile capacity behaves identically at every size.

Source issue: MoonLadderStudios/MoonMind#3878 (AC2, invariants 1, 3, 4, 8, 9).

``max_parallel_runs=2`` is not a product mode. Every test here is parametrized
over 1, 2, 4, 8 and 16 and contains no capacity-specific branch, so a rule that
only holds at one size fails here rather than in a deployment.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from moonmind.provider_profiles.lease_client import (
    CredentialLeaseMode,
    CredentialLeasePurpose,
    credential_lease_mode,
    credential_source_is_credentialless,
)
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    _ADAPTIVE_CAPACITY_RECOVERY_SECONDS,
    _MIN_ADAPTIVE_CAPACITY,
    ProfileSlotState,
)

#: The full deployment-selectable range this program must remain correct for.
CAPACITIES = [1, 2, 4, 8, 16]

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _credentialless_profile(capacity: int) -> ProfileSlotState:
    """A launch-ready OpenCode Zen-shaped profile: credential source ``none``."""

    return ProfileSlotState(
        profile_id="opencode-zen-free",
        max_parallel_runs=capacity,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        launch_ready=True,
        credential_source="none",
        purpose_aware_capacity=True,
    )


def _reserve_execution(profile: ProfileSlotState, lease_id: str) -> bool:
    return profile.reserve(
        lease_id,
        NOW,
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT.value,
    )


def _fill(profile: ProfileSlotState, count: int) -> list[str]:
    granted = []
    for index in range(count):
        lease_id = f"agent-run-{index}"
        assert _reserve_execution(profile, lease_id) is True
        granted.append(lease_id)
    return granted


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_configured_capacity_admits_exactly_n_execution_leases(capacity: int) -> None:
    """Invariant 1: ``N`` configured admits ``N``, and the ``N+1``st waits."""

    profile = _credentialless_profile(capacity)

    granted = _fill(profile, capacity)

    assert len(granted) == capacity
    assert profile.execution_lease_count == capacity
    assert profile.available_slots == 0
    assert profile.is_available() is False
    assert _reserve_execution(profile, "agent-run-overflow") is False


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_releasing_one_lease_frees_exactly_one_slot(capacity: int) -> None:
    profile = _credentialless_profile(capacity)
    granted = _fill(profile, capacity)

    assert profile.release(granted[0]) is True

    assert profile.available_slots == 1
    assert _reserve_execution(profile, "agent-run-next") is True
    assert profile.available_slots == 0


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_single_flight_validation_consumes_no_execution_slot(capacity: int) -> None:
    """Invariant 3: credentialless validation never requires capacity one."""

    profile = _credentialless_profile(capacity)
    _fill(profile, capacity)

    admitted = profile.reserve_unmetered(
        "opencode-model-catalog:deadbeef",
        NOW,
        purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
    )

    assert admitted is True
    # Execution accounting is untouched: the running work keeps its slots.
    assert profile.execution_lease_count == capacity
    assert profile.available_slots == 0
    assert (
        profile.lease_mode("opencode-model-catalog:deadbeef")
        is CredentialLeaseMode.SINGLE_FLIGHT_VALIDATION
    )


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_single_flight_validation_admits_one_holder_per_identity(
    capacity: int,
) -> None:
    profile = _credentialless_profile(capacity)
    identity = "opencode-model-catalog:deadbeef"

    first = profile.reserve_unmetered(
        identity, NOW, purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value
    )
    second = profile.reserve_unmetered(
        identity, NOW, purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value
    )

    assert first is True
    assert second is False
    # A different evidence identity is a different refresh and is never blocked.
    assert (
        profile.reserve_unmetered(
            "opencode-model-catalog:cafebabe",
            NOW,
            purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
        )
        is True
    )


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_validation_lease_expires_faster_than_an_execution_lease(
    capacity: int,
) -> None:
    """A lost validator must not stall refresh for an execution-length window."""

    profile = _credentialless_profile(capacity)
    profile.reserve_unmetered(
        "opencode-model-catalog:deadbeef",
        NOW,
        purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
    )
    _reserve_execution(profile, "agent-run-0")

    validation_limit = profile.lease_max_duration_seconds(
        "opencode-model-catalog:deadbeef"
    )
    execution_limit = profile.lease_max_duration_seconds("agent-run-0")

    assert validation_limit < execution_limit


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_exclusive_maintenance_blocks_new_consumers_while_it_waits(
    capacity: int,
) -> None:
    """Invariant 4: a busy profile cannot starve maintenance by replacing runs."""

    profile = _credentialless_profile(capacity)
    granted = _fill(profile, capacity)
    profile.release(granted[0])
    assert profile.available_slots == 1

    profile.enqueue_maintenance_waiter(
        "rotate-credential",
        purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
        queue_order=1,
        queued_at=NOW.isoformat(),
    )

    assert profile.is_available() is False
    assert _reserve_execution(profile, "agent-run-new") is False
    # The drain is real work in progress, not a stall: existing runs continue.
    assert profile.execution_lease_count == capacity - 1


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_exclusive_maintenance_holder_blocks_new_consumers(capacity: int) -> None:
    profile = _credentialless_profile(capacity)
    assert profile.reserve(
        "rotate-credential",
        NOW,
        purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
    )

    assert profile.exclusive_maintenance_lease_count == 1
    assert profile.is_available() is False
    assert _reserve_execution(profile, "agent-run-0") is False

    assert profile.release("rotate-credential") is True
    assert profile.is_available() is True
    assert _reserve_execution(profile, "agent-run-0") is True


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_rate_limiting_lowers_effective_admission_without_editing_the_ceiling(
    capacity: int,
) -> None:
    """Invariant 8: the configured ceiling is operator state, not runtime state."""

    profile = _credentialless_profile(capacity)

    lowered = profile.apply_rate_limit_backpressure(NOW)

    assert profile.configured_capacity == capacity
    assert lowered == max(_MIN_ADAPTIVE_CAPACITY, capacity // 2)
    assert profile.effective_capacity == lowered
    assert profile.available_slots == lowered


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_backpressure_never_evicts_work_already_admitted(capacity: int) -> None:
    """Lowering admission stops new runs; it does not kill running ones."""

    profile = _credentialless_profile(capacity)
    granted = _fill(profile, capacity)

    profile.apply_rate_limit_backpressure(NOW)

    assert profile.current_leases == granted
    assert profile.execution_lease_count == capacity
    assert profile.available_slots == 0
    assert _reserve_execution(profile, "agent-run-overflow") is False


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_backpressure_bottoms_out_at_one_before_withdrawal(capacity: int) -> None:
    """Halving trades concurrency first, and only then availability."""

    profile = _credentialless_profile(capacity)

    for _ in range(8):
        if profile.rate_limit_requires_withdrawal():
            break
        profile.apply_rate_limit_backpressure(NOW)
    else:  # pragma: no cover - a runaway loop is a real defect
        pytest.fail("backpressure never reached the withdrawal floor")

    assert profile.effective_capacity == _MIN_ADAPTIVE_CAPACITY
    assert profile.rate_limit_requires_withdrawal() is True


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_adaptive_capacity_recovers_one_slot_per_interval(capacity: int) -> None:
    profile = _credentialless_profile(capacity)
    profile.apply_rate_limit_backpressure(NOW)
    lowered = profile.effective_capacity

    # Too soon: recovery must be paced, not immediate.
    assert profile.recover_adaptive_capacity(NOW + timedelta(seconds=1)) is False
    assert profile.effective_capacity == lowered

    later = NOW + timedelta(seconds=_ADAPTIVE_CAPACITY_RECOVERY_SECONDS + 1)
    assert profile.recover_adaptive_capacity(later) is True
    assert lowered <= profile.effective_capacity <= capacity


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_adaptive_capacity_returns_to_the_configured_ceiling(capacity: int) -> None:
    profile = _credentialless_profile(capacity)
    profile.apply_rate_limit_backpressure(NOW)

    clock = NOW
    for _ in range(capacity + 2):
        clock += timedelta(seconds=_ADAPTIVE_CAPACITY_RECOVERY_SECONDS + 1)
        profile.recover_adaptive_capacity(clock)

    assert profile.adaptive_capacity_limit is None
    assert profile.effective_capacity == capacity


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_reducing_configured_capacity_never_evicts_active_workflows(
    capacity: int,
) -> None:
    """Invariant 9: reduction stops admission; it does not kill running work."""

    profile = _credentialless_profile(capacity)
    granted = _fill(profile, capacity)

    profile.max_parallel_runs = 1

    assert profile.current_leases == granted
    assert profile.available_slots == 0
    assert _reserve_execution(profile, "agent-run-overflow") is False
    # Admission resumes only once usage falls below the new effective limit.
    for lease_id in granted[1:]:
        profile.release(lease_id)
    assert profile.available_slots == 0
    profile.release(granted[0])
    assert profile.available_slots == 1


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_increasing_configured_capacity_admits_immediately(capacity: int) -> None:
    profile = _credentialless_profile(capacity)
    _fill(profile, capacity)
    assert profile.available_slots == 0

    profile.max_parallel_runs = capacity + 1

    assert profile.available_slots == 1
    assert _reserve_execution(profile, "agent-run-extra") is True


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_state_projection_distinguishes_configured_from_effective(
    capacity: int,
) -> None:
    """AC11: the operator can see which layer is actually limiting them."""

    profile = _credentialless_profile(capacity)
    profile.capacity_scope_ref = "opencode-zen:contributor-free"
    _fill(profile, min(capacity, 1))
    profile.apply_rate_limit_backpressure(NOW)

    payload = profile.to_dict()

    assert payload["configured_capacity"] == capacity
    assert payload["effective_capacity"] == profile.effective_capacity
    assert payload["execution_lease_count"] == min(capacity, 1)
    assert payload["capacity_scope_ref"] == "opencode-zen:contributor-free"
    assert payload["adaptive_capacity_limit"] == profile.adaptive_capacity_limit


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_pre_patch_history_keeps_its_original_accounting(capacity: int) -> None:
    """Replay safety: a history without the ledger must not change meaning."""

    legacy = ProfileSlotState(
        profile_id="opencode-zen-free",
        max_parallel_runs=capacity,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        launch_ready=True,
        credential_source="none",
        purpose_aware_capacity=False,
    )

    # Every lease counts against capacity, exactly as before the patch.
    assert legacy.reserve(
        "rotate-credential",
        NOW,
        purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
    )
    assert legacy.execution_lease_count == 1
    assert legacy.available_slots == capacity - 1
    # And no unmetered grant is possible without the ledger.
    assert (
        legacy.reserve_unmetered(
            "identity",
            NOW,
            purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
        )
        is False
    )
    # Rate limiting keeps its all-or-nothing withdrawal semantics.
    assert legacy.rate_limit_requires_withdrawal() is True
    assert legacy.effective_capacity == capacity


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_mutable_oauth_home_validation_stays_exclusive(capacity: int) -> None:
    """Invariant 5: only a credentialless profile earns single-flight validation."""

    oauth = ProfileSlotState(
        profile_id="codex-oauth",
        max_parallel_runs=capacity,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        launch_ready=True,
        credential_source="oauth_volume",
        purpose_aware_capacity=True,
    )

    assert oauth.credentialless is False
    assert (
        credential_lease_mode(
            purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION,
            credentialless=credential_source_is_credentialless(
                oauth.credential_source
            ),
        )
        is CredentialLeaseMode.EXCLUSIVE_MAINTENANCE
    )


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_unknown_persisted_purpose_falls_to_the_most_restrictive_mode(
    capacity: int,
) -> None:
    """An unrecognized purpose must never silently widen admission."""

    profile = _credentialless_profile(capacity)
    profile.current_leases.append("mystery")
    profile.lease_metadata["mystery"] = {"purpose": "purpose-from-the-future"}

    assert (
        profile.lease_mode("mystery") is CredentialLeaseMode.EXCLUSIVE_MAINTENANCE
    )
    assert profile.is_available() is False


# ---------------------------------------------------------------------------
# Manager Update boundary: AC1 and invariants 3/4/5 through the real handler
# ---------------------------------------------------------------------------


def _manager(*, ledger: bool = True):
    """A manager with one credentialless Zen profile at the given capacity."""

    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        MoonMindProviderProfileManagerWorkflow,
    )

    wf = MoonMindProviderProfileManagerWorkflow()
    wf._runtime_id = "opencode"
    wf._purpose_aware_capacity_ledger = ledger
    return wf


def _install_profile(wf, capacity: int, *, credential_source: str = "none") -> None:
    wf._profiles["opencode-zen-free"] = ProfileSlotState(
        profile_id="opencode-zen-free",
        max_parallel_runs=capacity,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        launch_ready=True,
        credential_source=credential_source,
        purpose_aware_capacity=wf._purpose_aware_capacity_ledger,
    )


def _validation_payload(owner: str = "opencode-model-catalog:deadbeef") -> dict:
    return {
        "requester_workflow_id": owner,
        "runtime_id": "opencode",
        "execution_profile_ref": "opencode-zen-free",
        "purpose": "credential_validation",
        "metadata": {"workflowId": owner},
    }


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_credentialless_validation_no_longer_requires_capacity_one(
    capacity: int,
) -> None:
    """AC1: the hard capacity-one gate is what blocked launch-readiness above one."""

    from unittest.mock import patch as mock_patch

    wf = _manager()
    _install_profile(wf, capacity)

    with mock_patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as mock_wf:
        mock_wf.now.return_value = NOW
        mock_wf.patched.return_value = False
        granted = await wf.acquire_credential_maintenance_lease(
            _validation_payload()
        )

    assert granted["profile_id"] == "opencode-zen-free"
    assert granted["already_held"] is False
    assert granted["lease_mode"] == "single_flight_validation"
    # It consumed no execution slot, so N concurrent runs are still admissible.
    assert wf._profiles["opencode-zen-free"].available_slots == capacity


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_second_validator_for_the_same_identity_stands_down(
    capacity: int,
) -> None:
    """Invariant 3: one probe per exact evidence identity, whatever the capacity."""

    from unittest.mock import patch as mock_patch

    wf = _manager()
    _install_profile(wf, capacity)

    with mock_patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as mock_wf:
        mock_wf.now.return_value = NOW
        mock_wf.patched.return_value = False
        first = await wf.acquire_credential_maintenance_lease(_validation_payload())
        second = await wf.acquire_credential_maintenance_lease(_validation_payload())

    assert first["already_held"] is False
    assert second["already_held"] is True
    assert second["lease_mode"] == "single_flight_validation"


@pytest.mark.parametrize("capacity", [2, 4, 8, 16])
@pytest.mark.asyncio
async def test_a_pre_patch_manager_still_rejects_capacity_above_one(
    capacity: int,
) -> None:
    """Replay safety: a history without the ledger keeps the exclusive gate."""

    from unittest.mock import patch as mock_patch

    from temporalio import exceptions

    wf = _manager(ledger=False)
    _install_profile(wf, capacity)

    with mock_patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as mock_wf:
        mock_wf.now.return_value = NOW
        mock_wf.patched.return_value = False
        with pytest.raises(
            exceptions.ApplicationError,
            match="credential maintenance requires exclusive profile capacity",
        ):
            await wf.acquire_credential_maintenance_lease(_validation_payload())


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_oauth_validation_stays_exclusive_at_the_manager_boundary(
    capacity: int,
) -> None:
    """Invariant 5: an OAuth home shares mutable state, so validation drains it."""

    import asyncio
    from unittest.mock import patch as mock_patch

    wf = _manager()
    _install_profile(wf, capacity, credential_source="oauth_volume")
    profile = wf._profiles["opencode-zen-free"]
    profile.reserve("agent-run-0", NOW, purpose="execution_omnigent")

    with mock_patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as mock_wf:
        mock_wf.now.return_value = NOW
        mock_wf.patched.return_value = False

        async def _never() -> None:
            await asyncio.sleep(3600)

        mock_wf.wait_condition = lambda *a, **k: _never()
        task = asyncio.ensure_future(
            wf.acquire_credential_maintenance_lease(_validation_payload())
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # It is waiting for the existing consumer to drain, and meanwhile it
        # blocks new consumers so a busy profile cannot starve maintenance.
        assert profile.exclusive_maintenance_waiters == 1
        assert profile.is_available() is False

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            _ = await task
