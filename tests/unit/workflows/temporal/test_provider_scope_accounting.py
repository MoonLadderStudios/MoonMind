"""Shared-scope accounting, idempotent backpressure and evidence-driven recovery.

Source: MoonLadderStudios/MoonMind#3882.

Every case here drives the real manager boundary — the `report_cooldown` and
`release_slot` signal handlers, the `AcquireSlot` update, `_drain_queue`, the
authoritative profile/scope sync, and the Continue-As-New round trip — rather
than calling an arithmetic helper directly. The findings this work closes were
all reachable only through those boundaries: a helper that halves once looks
correct until the caller halves the profile again after it returns.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.provider_profiles.lease_client import CredentialLeasePurpose
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    PROVIDER_SCOPE_ACCOUNTING_PATCH,
    CapacityScopeState,
    MoonMindProviderProfileManagerWorkflow,
    PendingRequest,
    ProfileSlotState,
    lease_capacity_cost,
)

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


def _make_manager(*, scope_accounting: bool = True) -> MoonMindProviderProfileManagerWorkflow:
    manager = MoonMindProviderProfileManagerWorkflow()
    manager._runtime_id = "opencode"
    manager._purpose_aware_leases = True
    manager._purpose_aware_capacity_ledger = True
    manager._durable_maintenance_queue = True
    manager._scope_accounting = scope_accounting
    return manager


def _add_profile(
    manager: MoonMindProviderProfileManagerWorkflow,
    profile_id: str,
    *,
    max_parallel_runs: int,
    scope_ref: str,
    credential_source: str = "api_key",
) -> ProfileSlotState:
    profile = ProfileSlotState(
        profile_id=profile_id,
        max_parallel_runs=max_parallel_runs,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        capacity_scope_ref=scope_ref,
        effective_limit=max_parallel_runs,
        credential_source=credential_source,
        purpose_aware_capacity=manager._purpose_aware_capacity_ledger,
    )
    manager._profiles[profile_id] = profile
    return profile


def _add_scope(
    manager: MoonMindProviderProfileManagerWorkflow,
    scope_ref: str,
    *,
    configured: int,
    effective: int | None = None,
    **kwargs,
) -> CapacityScopeState:
    scope = CapacityScopeState(
        scope_ref=scope_ref,
        runtime_id="opencode",
        configured_limit=configured,
        effective_limit=configured if effective is None else effective,
        **kwargs,
    )
    manager._scopes[scope_ref] = scope
    return scope


class _WorkflowClock:
    """A deterministic stand-in for the ``workflow`` module in the manager."""

    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def now(self) -> datetime:
        return self._now


def _patched_workflow(clock: _WorkflowClock):
    """Patch the manager's ``workflow`` module with every marker enabled."""

    patcher = patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    )
    mock_wf = patcher.start()
    mock_wf.patched.return_value = True
    mock_wf.now.side_effect = clock.now

    async def _wait_condition(predicate, timeout=None):
        while not predicate():
            await asyncio.sleep(0)

    mock_wf.wait_condition = _wait_condition
    mock_wf.info.return_value = SimpleNamespace(
        workflow_id="provider-profile-manager:opencode",
        run_id="run-1",
        task_queue="agent-runtime",
        continued_run_id=None,
    )
    mock_wf.logger = None
    return patcher, mock_wf


@pytest.fixture()
def clock() -> _WorkflowClock:
    return _WorkflowClock()


@pytest.fixture()
def workflow_module(clock):
    patcher, mock_wf = _patched_workflow(clock)
    try:
        yield mock_wf
    finally:
        patcher.stop()


# ---------------------------------------------------------------------------
# One unit-accounting function (remaining plan 1)
# ---------------------------------------------------------------------------


class TestOneUnitAccountingFunction:
    @pytest.mark.parametrize(
        ("purpose", "profile_units", "scope_units"),
        [
            (CredentialLeasePurpose.EXECUTION_DIRECT.value, 1, 1),
            (CredentialLeasePurpose.EXECUTION_OMNIGENT.value, 1, 1),
            (CredentialLeasePurpose.CREDENTIAL_VALIDATION.value, 0, 1),
            (CredentialLeasePurpose.OAUTH_CONNECT.value, 0, 1),
            (CredentialLeasePurpose.OAUTH_RECONNECT.value, 0, 1),
            (CredentialLeasePurpose.CREDENTIAL_REPAIR.value, 0, 0),
            (CredentialLeasePurpose.OAUTH_DISCONNECT.value, 0, 0),
        ],
    )
    def test_each_purpose_has_one_explicit_trusted_cost(
        self, purpose, profile_units, scope_units
    ):
        cost = lease_capacity_cost(purpose, purpose_aware=True)
        assert (cost.profile_units, cost.scope_units) == (profile_units, scope_units)

    def test_an_unclassifiable_purpose_fails_closed_on_both_ledgers(self):
        cost = lease_capacity_cost("something-new", purpose_aware=True)
        assert (cost.profile_units, cost.scope_units) == (1, 1)

    def test_a_pre_ledger_history_charges_one_unit_everywhere(self):
        cost = lease_capacity_cost(
            CredentialLeasePurpose.CREDENTIAL_REPAIR.value, purpose_aware=False
        )
        assert (cost.profile_units, cost.scope_units) == (1, 1)

    def test_profile_and_scope_admission_read_the_same_units(self, workflow_module):
        """The finding: profile admission counted every lease, scope counted units.

        A profile at capacity 2 holding one execution and one credential repair
        used to be full on the profile ledger and half-empty on the scope
        ledger. Whichever limit was consulted first decided admission.
        """

        manager = _make_manager()
        _add_scope(manager, "shared", configured=4)
        profile = _add_profile(manager, "p1", max_parallel_runs=2, scope_ref="shared")
        assert profile.reserve(
            "wf-exec", NOW, purpose=CredentialLeasePurpose.EXECUTION_DIRECT.value
        )
        assert profile.reserve_unmetered(
            "wf-repair",
            NOW,
            purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
        )

        assert manager._profile_execution_units(profile) == 1
        assert manager._scope_active_units("shared") == 1
        assert manager._profile_admitted_by_capacity(profile) is True


# ---------------------------------------------------------------------------
# AC1: two profiles of 8 under a shared ceiling of 10
# ---------------------------------------------------------------------------


class TestSharedCeilingAdmission:
    def _shared_ceiling_manager(self):
        manager = _make_manager()
        _add_scope(manager, "shared-10", configured=10)
        _add_profile(manager, "a", max_parallel_runs=8, scope_ref="shared-10")
        _add_profile(manager, "b", max_parallel_runs=8, scope_ref="shared-10")
        return manager

    @pytest.mark.asyncio
    async def test_two_profiles_of_eight_admit_at_most_ten_execution_units(
        self, workflow_module
    ):
        manager = self._shared_ceiling_manager()
        manager._grant_lease_to_db = AsyncMock(return_value=True)
        manager._sync_leases_to_db = AsyncMock(return_value=True)

        admitted = []
        for index in range(16):
            profile_id = "a" if index % 2 == 0 else "b"
            profile = manager._profiles[profile_id]
            if not manager._profile_admitted_by_capacity(profile):
                continue
            result = await manager.acquire_slot(
                {
                    "requester_workflow_id": f"wf-{index}",
                    "runtime_id": "opencode",
                    "execution_profile_ref": profile_id,
                }
            )
            admitted.append(result["profile_id"])

        assert len(admitted) == 10
        assert manager._scope_active_units("shared-10") == 10
        # Neither profile reached its own ceiling of 8; the shared scope is what
        # stopped them, which is the whole point of the joint limit.
        assert manager._profiles["a"].execution_lease_count < 8
        assert manager._profiles["b"].execution_lease_count < 8

    @pytest.mark.asyncio
    async def test_the_final_shared_unit_is_granted_to_exactly_one_racing_request(
        self, workflow_module
    ):
        """AC1: the final-unit race, run through the real allocator.

        Durable persistence is an await inside the grant, so two grants really
        can interleave. Reserving before that await is what makes the second
        requester observe the scope as full.
        """

        manager = self._shared_ceiling_manager()
        for index in range(9):
            profile = manager._profiles["a" if index % 2 == 0 else "b"]
            assert profile.reserve(f"wf-held-{index}", NOW)
        manager._rebuild_lease_indexes()
        assert manager._scope_active_units("shared-10") == 9

        async def _slow_grant(**_kwargs):
            await asyncio.sleep(0)
            return True

        manager._grant_lease_to_db = AsyncMock(side_effect=_slow_grant)
        manager._sync_leases_to_db = AsyncMock(return_value=True)
        manager._signal_slot_assigned = AsyncMock()

        manager._pending_requests = [
            PendingRequest(
                requester_workflow_id="wf-race-queued",
                runtime_id="opencode",
                execution_profile_ref="a",
                purpose=CredentialLeasePurpose.EXECUTION_DIRECT.value,
            )
        ]

        drain = asyncio.create_task(manager._drain_queue())
        update = asyncio.create_task(
            manager.acquire_slot(
                {
                    "requester_workflow_id": "wf-race-update",
                    "runtime_id": "opencode",
                    "execution_profile_ref": "b",
                }
            )
        )
        await drain
        # The losing acquisition parks on the manager's wait condition rather
        # than taking an eleventh unit.
        await asyncio.sleep(0)
        assert manager._scope_active_units("shared-10") == 10
        update.cancel()
        try:
            await update
        except asyncio.CancelledError:
            pass
        assert manager._scope_active_units("shared-10") == 10

    @pytest.mark.asyncio
    async def test_a_retry_after_a_release_is_admitted_without_exceeding_the_ceiling(
        self, workflow_module
    ):
        manager = self._shared_ceiling_manager()
        manager._grant_lease_to_db = AsyncMock(return_value=True)
        manager._sync_leases_to_db = AsyncMock(return_value=True)
        manager._remove_lease_from_db = AsyncMock(return_value=True)

        for index in range(10):
            profile = manager._profiles["a" if index % 2 == 0 else "b"]
            assert profile.reserve(f"wf-held-{index}", NOW)
        manager._rebuild_lease_indexes()

        first = await asyncio.wait_for(
            asyncio.shield(
                asyncio.create_task(
                    _first_pass(manager, "wf-retry", "a"),
                )
            ),
            timeout=1,
        )
        assert first is None

        await manager.release_slot(
            {"requester_workflow_id": "wf-held-0", "profile_id": "a"}
        )
        assert manager._scope_active_units("shared-10") == 9

        granted = await manager.acquire_slot(
            {
                "requester_workflow_id": "wf-retry",
                "runtime_id": "opencode",
                "execution_profile_ref": "a",
            }
        )
        assert granted["already_held"] is False
        assert manager._scope_active_units("shared-10") == 10


async def _first_pass(manager, requester_id: str, profile_id: str):
    """Run one acquisition pass and report whether it was granted."""

    task = asyncio.create_task(
        manager.acquire_slot(
            {
                "requester_workflow_id": requester_id,
                "runtime_id": "opencode",
                "execution_profile_ref": profile_id,
            }
        )
    )
    await asyncio.sleep(0)
    if task.done():
        return task.result()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return None


# ---------------------------------------------------------------------------
# AC2: validation/maintenance agreement, credential repair not deadlocked
# ---------------------------------------------------------------------------


class TestMaintenanceAccounting:
    @pytest.mark.asyncio
    async def test_validation_spends_scope_but_not_the_profile_execution_ceiling(
        self, workflow_module
    ):
        manager = _make_manager()
        _add_scope(manager, "shared", configured=2)
        profile = _add_profile(
            manager,
            "p1",
            max_parallel_runs=2,
            scope_ref="shared",
            credential_source="none",
        )
        assert profile.reserve_unmetered(
            "wf-validate",
            NOW,
            purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
        )

        assert manager._profile_execution_units(profile) == 0
        assert manager._scope_active_units("shared") == 1
        # Both ledgers describe the same lease and neither contradicts the
        # other: the profile still has both execution slots, the shared
        # allowance has one unit left.
        assert profile.available_slots == 2
        assert manager._profile_admitted_by_capacity(profile) is True

    def test_credential_repair_is_not_deadlocked_by_a_saturated_scope(
        self, workflow_module
    ):
        manager = _make_manager()
        scope = _add_scope(manager, "shared", configured=1)
        scope.cooldown_until = (NOW + timedelta(seconds=600)).isoformat()
        scope.backpressure_state = "cooldown"
        profile = _add_profile(manager, "p1", max_parallel_runs=1, scope_ref="shared")
        assert profile.reserve("wf-exec", NOW)

        assert manager._scope_active_units("shared") == 1
        assert manager._profile_scope_available(profile) is False
        # Repair spends neither ledger, so a full and cooling-down scope is
        # never what stops a broken credential from being fixed.
        assert (
            manager._maintenance_consumes_scope(
                CredentialLeasePurpose.CREDENTIAL_REPAIR.value
            )
            is False
        )
        assert profile.reserve_unmetered(
            "wf-repair",
            NOW,
            purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
        )
        assert manager._scope_active_units("shared") == 1


# ---------------------------------------------------------------------------
# AC3: idempotent, validated backpressure
# ---------------------------------------------------------------------------


class TestRateLimitReportIdempotency:
    def _manager_with_reduced_scope(self):
        manager = _make_manager()
        _add_scope(manager, "shared", configured=8)
        _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        _add_profile(manager, "p2", max_parallel_runs=8, scope_ref="shared")
        return manager

    def test_delivering_the_same_429_twice_changes_nothing_the_second_time(
        self, workflow_module, clock
    ):
        """The finding: the scope helper returned early, the caller did not.

        ``_apply_scope_rate_limit`` deduplicated the scope half and returned,
        after which ``report_cooldown`` unconditionally halved the profile's
        effective limit and reset its cooldown. A redelivered 429 therefore
        cost the profile half its concurrency every single time.
        """

        manager = self._manager_with_reduced_scope()
        payload = {
            "profile_id": "p1",
            "failure_class": "rate_limit",
            "retry_after_seconds": 120,
            "report_id": "report-1",
        }
        manager.report_cooldown(dict(payload))

        scope = manager._scopes["shared"]
        profile = manager._profiles["p1"]
        first = (
            scope.effective_limit,
            scope.cooldown_until,
            scope.backpressure_state,
            profile.admission_limit,
            profile.cooldown_until,
        )
        assert first[0] == 4
        assert first[3] == 4

        clock.advance(5)
        manager.report_cooldown(dict(payload))

        assert (
            scope.effective_limit,
            scope.cooldown_until,
            scope.backpressure_state,
            profile.admission_limit,
            profile.cooldown_until,
        ) == first

    def test_a_report_naming_a_scope_the_profile_does_not_use_is_refused(
        self, workflow_module
    ):
        manager = self._manager_with_reduced_scope()
        _add_scope(manager, "other", configured=8)
        before = manager._scopes["other"].to_dict()

        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 120,
                "report_id": "report-x",
                "capacity_scope_ref": "other",
            }
        )

        assert manager._scopes["other"].to_dict() == before
        assert manager._scopes["shared"].effective_limit == 8
        assert manager._profiles["p1"].admission_limit == 8

    def test_a_report_quoting_a_replaced_generation_is_refused(self, workflow_module):
        manager = self._manager_with_reduced_scope()
        manager._scopes["shared"].generation = 3

        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 120,
                "report_id": "report-stale",
                "scope_generation": 2,
            }
        )

        assert manager._scopes["shared"].effective_limit == 8
        assert manager._profiles["p1"].admission_limit == 8

    def test_an_out_of_order_report_cannot_halve_the_current_capacity(
        self, workflow_module, clock
    ):
        manager = self._manager_with_reduced_scope()
        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 60,
                "report_id": "report-current",
                "observed_at": NOW.isoformat(),
            }
        )
        assert manager._scopes["shared"].effective_limit == 4

        clock.advance(30)
        manager.report_cooldown(
            {
                "profile_id": "p2",
                "failure_class": "rate_limit",
                "retry_after_seconds": 60,
                "report_id": "report-delayed",
                # Observed before the reduction currently in force: this
                # describes capacity that no longer exists.
                "observed_at": (NOW - timedelta(seconds=30)).isoformat(),
            }
        )
        assert manager._scopes["shared"].effective_limit == 4

    def test_a_duplicate_delayed_past_retention_is_refused_as_stale(
        self, workflow_module, clock
    ):
        """Retention is bounded without letting a delayed duplicate halve again."""

        manager = self._manager_with_reduced_scope()
        payload = {
            "profile_id": "p1",
            "failure_class": "rate_limit",
            "retry_after_seconds": 60,
            "report_id": "report-1",
            "observed_at": NOW.isoformat(),
        }
        manager.report_cooldown(dict(payload))
        assert manager._scopes["shared"].effective_limit == 4

        # Long enough that the retained identity has been pruned by age.
        clock.advance(7200)
        manager._clear_expired_cooldowns()
        manager.report_cooldown(dict(payload))

        assert manager._scopes["shared"].effective_limit == 4

    def test_a_sibling_profile_in_the_same_scope_cannot_evade_the_limit(
        self, workflow_module, clock
    ):
        manager = self._manager_with_reduced_scope()
        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 600,
                "report_id": "report-1",
            }
        )
        profile_b = manager._profiles["p2"]
        # p2's own limit is untouched, but the allowance it draws from is not.
        assert profile_b.admission_limit == 8
        assert manager._profile_scope_available(profile_b) is False

    def test_a_non_rate_limit_failure_stays_profile_local(self, workflow_module):
        manager = self._manager_with_reduced_scope()
        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "credential_invalid",
                "cooldown_seconds": 120,
            }
        )
        assert manager._scopes["shared"].effective_limit == 8
        assert manager._profiles["p1"].cooldown_until is not None
        assert manager._profiles["p2"].cooldown_until is None


# ---------------------------------------------------------------------------
# AC4: Retry-After extends only; disabled stays disabled
# ---------------------------------------------------------------------------


class TestCooldownExtension:
    def test_a_short_retry_after_never_shortens_a_longer_deadline(
        self, workflow_module, clock
    ):
        manager = _make_manager()
        _add_scope(manager, "shared", configured=8)
        _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")

        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 900,
                "report_id": "long",
            }
        )
        long_scope_deadline = manager._scopes["shared"].cooldown_until
        long_profile_deadline = manager._profiles["p1"].cooldown_until

        clock.advance(10)
        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 5,
                "report_id": "short",
            }
        )

        assert manager._scopes["shared"].cooldown_until == long_scope_deadline
        assert manager._profiles["p1"].cooldown_until == long_profile_deadline

    def test_retry_after_is_bounded_before_it_is_applied(self, workflow_module):
        manager = _make_manager()
        _add_scope(manager, "shared", configured=8)
        _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")

        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 10**9,
                "report_id": "absurd",
            }
        )

        deadline = datetime.fromisoformat(manager._scopes["shared"].cooldown_until)
        assert deadline <= NOW + timedelta(seconds=3600)

    def test_a_disabled_scope_is_not_re_enabled_by_automatic_recovery(
        self, workflow_module, clock
    ):
        manager = _make_manager()
        scope = _add_scope(
            manager,
            "shared",
            configured=8,
            effective=2,
            backpressure_state="disabled",
        )
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        scope.record_provider_success(NOW)
        scope.success_evidence = 5
        scope.healthy_since = (NOW - timedelta(hours=4)).isoformat()

        clock.advance(4 * 3600)
        manager._clear_expired_cooldowns()

        assert scope.backpressure_state == "disabled"
        assert scope.effective_limit == 2
        assert manager._profile_scope_available(profile) is False

    def test_a_disabled_scope_does_not_accumulate_recovery_evidence(self):
        scope = CapacityScopeState(
            scope_ref="shared",
            configured_limit=8,
            effective_limit=2,
            backpressure_state="disabled",
        )
        scope.record_provider_success(NOW)
        assert scope.success_evidence == 0


# ---------------------------------------------------------------------------
# AC5: recovery requires evidence, is interval-bounded and ceiling-bounded
# ---------------------------------------------------------------------------


class TestEvidenceDrivenRecovery:
    def _reduced(self, manager):
        return _add_scope(
            manager,
            "shared",
            configured=8,
            effective=4,
            backpressure_state="probing",
            last_decrease_at=(NOW - timedelta(hours=2)).isoformat(),
            healthy_since=(NOW - timedelta(hours=2)).isoformat(),
        )

    def test_elapsed_time_alone_never_raises_a_reduced_limit(
        self, workflow_module, clock
    ):
        """The finding: recovery treated elapsed wall-clock as provider health."""

        manager = _make_manager()
        scope = self._reduced(manager)
        _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")

        for _ in range(5):
            clock.advance(3600)
            manager._clear_expired_cooldowns()

        assert scope.effective_limit == 4
        assert scope.success_evidence == 0

    @pytest.mark.asyncio
    async def test_a_classified_provider_success_is_what_permits_one_step(
        self, workflow_module, clock
    ):
        manager = _make_manager()
        scope = self._reduced(manager)
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        manager._grant_lease_to_db = AsyncMock(return_value=True)
        manager._remove_lease_from_db = AsyncMock(return_value=True)
        assert profile.reserve("wf-1", NOW, metadata={"capacityScopeRef": "shared"})
        manager._rebuild_lease_indexes()

        await manager.release_slot(
            {
                "requester_workflow_id": "wf-1",
                "profile_id": "p1",
                "provider_outcome": "success",
            }
        )
        assert scope.success_evidence == 1

        clock.advance(600)
        manager._clear_expired_cooldowns()
        assert scope.effective_limit == 5
        # The evidence is spent by the step it justified.
        assert scope.success_evidence == 0

    def test_recovery_is_interval_bounded_even_with_evidence(
        self, workflow_module, clock
    ):
        manager = _make_manager()
        scope = self._reduced(manager)
        _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")

        manager.report_provider_success({"profile_id": "p1"})
        clock.advance(600)
        manager._clear_expired_cooldowns()
        assert scope.effective_limit == 5

        manager.report_provider_success({"profile_id": "p1"})
        clock.advance(30)
        manager._clear_expired_cooldowns()
        assert scope.effective_limit == 5

    def test_recovery_never_exceeds_the_configured_ceiling(
        self, workflow_module, clock
    ):
        manager = _make_manager()
        scope = _add_scope(
            manager,
            "shared",
            configured=5,
            effective=4,
            backpressure_state="probing",
            healthy_since=(NOW - timedelta(hours=2)).isoformat(),
        )
        _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")

        for _ in range(6):
            manager.report_provider_success({"profile_id": "p1"})
            clock.advance(600)
            manager._clear_expired_cooldowns()

        assert scope.effective_limit == 5
        assert scope.backpressure_state == "healthy"

    def test_a_scope_under_cooldown_does_not_recover(self, workflow_module, clock):
        manager = _make_manager()
        scope = self._reduced(manager)
        scope.cooldown_until = (NOW + timedelta(hours=1)).isoformat()
        scope.backpressure_state = "cooldown"
        _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")

        manager.report_provider_success({"profile_id": "p1"})
        clock.advance(600)
        manager._clear_expired_cooldowns()

        assert scope.effective_limit == 4

    def test_an_unrecognized_recovery_policy_gets_no_automatic_increase(
        self, workflow_module, clock
    ):
        manager = _make_manager()
        scope = self._reduced(manager)
        scope.recovery_policy_ref = "some-unknown-policy@9"
        _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")

        manager.report_provider_success({"profile_id": "p1"})
        clock.advance(3600)
        manager._clear_expired_cooldowns()

        assert scope.effective_limit == 4

    def test_the_profile_limit_also_recovers_only_against_evidence(
        self, workflow_module, clock
    ):
        """Both ledgers recover on the same grounds, or they disagree again."""

        manager = _make_manager()
        self._reduced(manager)
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        profile.apply_rate_limit_backpressure(NOW)
        assert profile.admission_limit == 4

        clock.advance(3600)
        assert manager._recover_adaptive_capacity() == 0
        assert profile.admission_limit == 4

        manager.report_provider_success({"profile_id": "p1"})
        assert manager._recover_adaptive_capacity() == 1
        assert profile.admission_limit == 5
        # The evidence is spent; the next step needs its own.
        clock.advance(3600)
        assert manager._recover_adaptive_capacity() == 0
        assert profile.admission_limit == 5

    def test_a_pre_marker_history_keeps_time_based_profile_recovery(
        self, workflow_module, clock
    ):
        manager = _make_manager(scope_accounting=False)
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        profile.apply_rate_limit_backpressure(NOW)
        assert profile.admission_limit == 4

        clock.advance(3600)
        assert manager._recover_adaptive_capacity() == 1
        assert profile.admission_limit == 5

    def test_a_new_reduction_discards_evidence_from_the_previous_limit(
        self, workflow_module, clock
    ):
        manager = _make_manager()
        scope = self._reduced(manager)
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        manager.report_provider_success({"profile_id": "p1"})
        assert scope.success_evidence == 1
        assert profile.provider_success_evidence == 1

        clock.advance(30)
        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 60,
                "report_id": "report-1",
            }
        )

        assert scope.success_evidence == 0
        assert profile.provider_success_evidence == 0

    def test_recovery_does_not_write_profile_limits(self, workflow_module, clock):
        """Two writers stepping one number is how the limits came to disagree."""

        manager = _make_manager()
        self._reduced(manager)
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        profile.apply_rate_limit_backpressure(NOW)
        assert profile.admission_limit == 4

        manager.report_provider_success({"profile_id": "p1"})
        clock.advance(600)
        manager._recover_scope_capacity(clock.now())

        assert profile.admission_limit == 4
        assert profile.effective_limit == 4


# ---------------------------------------------------------------------------
# AC6: unit ownership across reassignment, rollover and malformed state
# ---------------------------------------------------------------------------


class TestUnitOwnership:
    def test_reassigning_a_profile_does_not_move_its_active_units(
        self, workflow_module
    ):
        """The finding: scope membership was re-derived from profile metadata.

        An operator repointing a busy profile used to transfer every in-flight
        unit onto the new allowance in one refresh, over-counting the new scope
        and leaving the old one looking free.
        """

        manager = _make_manager()
        _add_scope(manager, "old", configured=4)
        _add_scope(manager, "new", configured=4)
        profile = _add_profile(manager, "p1", max_parallel_runs=4, scope_ref="old")
        for index in range(3):
            assert profile.reserve(
                f"wf-{index}",
                NOW,
                metadata=manager._grant_metadata(
                    {}, fencing_generation=index + 1, profile=profile
                ),
            )
        manager._rebuild_lease_indexes()
        assert manager._scope_active_units("old") == 3

        manager._apply_profile_sync(
            [
                {
                    "profile_id": "p1",
                    "max_parallel_runs": 4,
                    "capacity_scope_ref": "new",
                    "enabled": True,
                }
            ],
            authoritative=True,
        )

        assert profile.capacity_scope_ref == "new"
        assert manager._scope_active_units("old") == 3
        assert manager._scope_active_units("new") == 0

    @pytest.mark.asyncio
    async def test_a_new_grant_after_reassignment_lands_on_the_new_allowance(
        self, workflow_module
    ):
        manager = _make_manager()
        _add_scope(manager, "old", configured=4)
        _add_scope(manager, "new", configured=4)
        profile = _add_profile(manager, "p1", max_parallel_runs=4, scope_ref="old")
        assert profile.reserve(
            "wf-old",
            NOW,
            metadata=manager._grant_metadata({}, fencing_generation=1, profile=profile),
        )
        manager._rebuild_lease_indexes()
        profile.capacity_scope_ref = "new"

        manager._grant_lease_to_db = AsyncMock(return_value=True)
        manager._sync_leases_to_db = AsyncMock(return_value=True)
        await manager.acquire_slot(
            {
                "requester_workflow_id": "wf-new",
                "runtime_id": "opencode",
                "execution_profile_ref": "p1",
            }
        )

        assert manager._scope_active_units("old") == 1
        assert manager._scope_active_units("new") == 1

    def test_continue_as_new_round_trips_scope_and_report_state(self, workflow_module):
        manager = _make_manager()
        _add_scope(manager, "shared", configured=8)
        _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 300,
                "report_id": "report-1",
            }
        )
        payload = manager._build_continue_as_new_input()

        successor = _make_manager()
        successor._restore_state(payload)

        assert successor._scopes["shared"].effective_limit == 4
        assert (
            successor._scopes["shared"].cooldown_until
            == manager._scopes["shared"].cooldown_until
        )
        assert successor._profiles["p1"].admission_limit == 4
        assert successor._profiles["p1"].provider_success_evidence == 0
        assert successor._scopes["shared"].success_evidence == 0

        # The same report redelivered to the successor is still a duplicate.
        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as successor_wf:
            successor_wf.patched.return_value = True
            successor_wf.now.return_value = NOW + timedelta(seconds=1)
            successor_wf.logger = None
            successor.report_cooldown(
                {
                    "profile_id": "p1",
                    "failure_class": "rate_limit",
                    "retry_after_seconds": 300,
                    "report_id": "report-1",
                }
            )
        assert successor._scopes["shared"].effective_limit == 4

    def test_continue_as_new_round_trips_recovery_evidence(self, workflow_module):
        manager = _make_manager()
        _add_scope(manager, "shared", configured=8, effective=4)
        _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        manager._profiles["p1"].apply_rate_limit_backpressure(NOW)
        manager.report_provider_success({"profile_id": "p1"})

        successor = _make_manager()
        successor._restore_state(manager._build_continue_as_new_input())

        assert successor._profiles["p1"].provider_success_evidence == 1
        assert successor._scopes["shared"].success_evidence == 1
        assert successor._profiles["p1"].admission_limit == 4

    def test_a_rollover_payload_with_legacy_report_ids_still_restores(
        self, workflow_module
    ):
        manager = _make_manager()
        manager._restore_state(
            {
                "runtime_id": "opencode",
                "profiles": [
                    {
                        "profile_id": "p1",
                        "max_parallel_runs": 4,
                        "capacity_scope_ref": "provider-profile:p1",
                        "enabled": True,
                    }
                ],
                "seen_rate_limit_reports": ["bare-report-id"],
            }
        )
        assert "bare-report-id" in manager._seen_rate_limit_reports

    def test_a_missing_shared_scope_is_reconciliation_required(self, workflow_module):
        """The finding: a missing scope was synthesized from profile maxima."""

        manager = _make_manager()
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")

        assert manager._resolve_admission_scope(profile) is None
        assert manager._profile_admitted_by_capacity(profile) is False
        assert "shared" not in manager._scopes
        assert manager._profile_refresh_requested is True

    def test_a_profiles_own_default_scope_is_still_derivable(self, workflow_module):
        manager = _make_manager()
        profile = _add_profile(
            manager, "p1", max_parallel_runs=8, scope_ref="provider-profile:p1"
        )

        scope = manager._resolve_admission_scope(profile)
        assert scope is not None
        assert scope.configured_limit == 8
        assert manager._profile_admitted_by_capacity(profile) is True

    def test_an_authoritative_sync_without_the_shared_row_fails_closed(
        self, workflow_module
    ):
        manager = _make_manager()
        scope = _add_scope(manager, "shared", configured=10, effective=6)
        scope.backpressure_state = "reduced"
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")

        manager._apply_scope_sync([])

        assert "shared" in manager._scopes_requiring_reconciliation
        assert manager._profile_scope_available(profile) is False
        # The shared allowance was not quietly reset to the member's ceiling.
        assert manager._scopes["shared"].configured_limit == 10

    def test_an_authoritative_sync_restores_a_reconciled_scope(self, workflow_module):
        manager = _make_manager()
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        assert manager._profile_admitted_by_capacity(profile) is False

        manager._apply_scope_sync(
            [
                {
                    "scope_ref": "shared",
                    "runtime_id": "opencode",
                    "configured_limit": 10,
                    "effective_limit": 10,
                    "generation": 2,
                    "backpressure_state": "healthy",
                }
            ]
        )

        assert "shared" not in manager._scopes_requiring_reconciliation
        assert manager._profile_admitted_by_capacity(profile) is True
        assert manager._scopes["shared"].generation == 2

    def test_an_authoritative_sync_preserves_a_disabled_scope(self, workflow_module):
        manager = _make_manager()
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")

        manager._apply_scope_sync(
            [
                {
                    "scope_ref": "shared",
                    "runtime_id": "opencode",
                    "configured_limit": 8,
                    "effective_limit": 8,
                    "backpressure_state": "disabled",
                }
            ]
        )

        assert manager._scopes["shared"].backpressure_state == "disabled"
        assert manager._profile_admitted_by_capacity(profile) is False

    @pytest.mark.parametrize(
        "mutation",
        [
            {"configured_limit": 0},
            {"effective_limit": 0},
            {"backpressure_state": "who-knows"},
            {"cooldown_until": "not-a-timestamp"},
        ],
    )
    def test_malformed_scope_state_refuses_admission_instead_of_failing_open(
        self, workflow_module, mutation
    ):
        manager = _make_manager()
        scope = _add_scope(manager, "shared", configured=8)
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        for key, value in mutation.items():
            setattr(scope, key, value)

        assert manager._profile_admitted_by_capacity(profile) is False
        assert "shared" in manager._scopes_requiring_reconciliation

    def test_a_reduced_scope_still_blocks_new_grants_without_evicting_work(
        self, workflow_module
    ):
        manager = _make_manager()
        _add_scope(manager, "shared", configured=10)
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        for index in range(6):
            assert profile.reserve(
                f"wf-{index}",
                NOW,
                metadata=manager._grant_metadata(
                    {}, fencing_generation=index + 1, profile=profile
                ),
            )
        manager._rebuild_lease_indexes()

        manager._apply_scope_sync(
            [
                {
                    "scope_ref": "shared",
                    "runtime_id": "opencode",
                    "configured_limit": 4,
                    "effective_limit": 4,
                }
            ]
        )

        assert len(profile.current_leases) == 6
        assert manager._profile_admitted_by_capacity(profile) is False


# ---------------------------------------------------------------------------
# Remaining plan 7: workflow-authored capacity and unit-cost overrides
# ---------------------------------------------------------------------------


class TestWorkflowAuthoredOverridesAreRefused:
    """Capacity is audited operator policy; a signal may only report against it."""

    def test_a_signal_cannot_invent_a_cheaper_lease_purpose(self, workflow_module):
        manager = _make_manager()
        _add_scope(manager, "shared", configured=4)
        _add_profile(manager, "p1", max_parallel_runs=4, scope_ref="shared")

        with pytest.raises(Exception, match="Unsupported credential lease purpose"):
            manager.request_slot(
                {
                    "requester_workflow_id": "wf-1",
                    "runtime_id": "opencode",
                    "purpose": "free_execution",
                }
            )

    def test_a_signal_cannot_raise_a_configured_limit(self, workflow_module):
        manager = _make_manager()
        scope = _add_scope(manager, "shared", configured=4)
        profile = _add_profile(manager, "p1", max_parallel_runs=4, scope_ref="shared")

        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 60,
                "report_id": "report-1",
                "configured_limit": 99,
                "effective_limit": 99,
                "max_parallel_runs": 99,
                "unit_cost": 0,
            }
        )

        assert scope.configured_limit == 4
        assert scope.effective_limit == 2
        assert profile.max_parallel_runs == 4

    def test_lease_metadata_from_a_caller_cannot_forge_a_scope_binding(
        self, workflow_module
    ):
        manager = _make_manager()
        _add_scope(manager, "shared", configured=4)
        _add_scope(manager, "other", configured=99)
        profile = _add_profile(manager, "p1", max_parallel_runs=4, scope_ref="shared")

        metadata = manager._safe_lease_metadata(
            {"metadata": {"capacityScopeRef": "other", "scopeGeneration": 42}}
        )
        assert metadata == {}
        stamped = manager._grant_metadata(
            metadata, fencing_generation=1, profile=profile
        )
        assert stamped["capacityScopeRef"] == "shared"
        assert stamped["scopeGeneration"] == 1


# ---------------------------------------------------------------------------
# Remaining plan 8: one safe projection from the accounting owner
# ---------------------------------------------------------------------------


class TestCapacityScopeProjection:
    def test_the_projection_agrees_with_the_allocator(self, workflow_module):
        manager = _make_manager()
        _add_scope(manager, "shared", configured=10)
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        _add_profile(manager, "p2", max_parallel_runs=8, scope_ref="shared")
        for index in range(3):
            assert profile.reserve(
                f"wf-{index}",
                NOW,
                metadata=manager._grant_metadata(
                    {}, fencing_generation=index + 1, profile=profile
                ),
            )
        manager._pending_requests = [
            PendingRequest(
                requester_workflow_id="wf-queued",
                runtime_id="opencode",
                execution_profile_ref="p2",
            )
        ]
        manager._rebuild_lease_indexes()

        state = manager.get_state()
        projection = state["capacity_scopes"]["shared"]

        assert projection["configured_limit"] == 10
        assert projection["effective_limit"] == 10
        assert projection["active_units"] == manager._scope_active_units("shared") == 3
        assert projection["queued_requests"] == 1
        assert projection["cooldown_until"] is None
        assert projection["backpressure_state"] == "healthy"
        assert projection["reconciliation_required"] is False
        assert projection["member_profile_ids"] == ["p1", "p2"]
        # The per-profile view reads the same limit admission applies.
        assert state["profiles"]["p1"]["effective_limit"] == profile.admission_limit

    def test_the_projection_reports_a_scope_awaiting_reconciliation(
        self, workflow_module
    ):
        manager = _make_manager()
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        assert manager._profile_admitted_by_capacity(profile) is False

        projection = manager.get_state()["capacity_scopes"]["shared"]
        assert projection["reconciliation_required"] is True
        assert projection["member_profile_ids"] == ["p1"]


# ---------------------------------------------------------------------------
# Remaining plan 6: adapted allowance state survives a manager reset
# ---------------------------------------------------------------------------


class TestAdaptedScopeStateIsDurable:
    """A reduction is authority over a provider, not a workflow-local number.

    Keeping it only in workflow history means a manager that is reset or
    replaced resumes granting at the full configured ceiling into a provider
    that is still rate-limiting.
    """

    @pytest.mark.asyncio
    async def test_a_reduction_is_published_to_the_authoritative_row(
        self, workflow_module
    ):
        manager = _make_manager()
        _add_scope(manager, "shared", configured=8)
        _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 300,
                "report_id": "report-1",
            }
        )
        assert manager._scopes_pending_persist == {"shared"}

        recorded: list[dict] = []

        async def _execute_activity(name, payload, **_kwargs):
            recorded.append({"name": name, **payload})
            return {"persisted": True}

        workflow_module.execute_activity = _execute_activity
        await manager._persist_adapted_scope_state()

        assert len(recorded) == 1
        published = recorded[0]
        assert published["name"] == "provider_profile.sync_capacity_scope"
        assert published["scope_ref"] == "shared"
        assert published["effective_limit"] == 4
        assert published["backpressure_state"] == "cooldown"
        assert published["cooldown_until"] == manager._scopes["shared"].cooldown_until
        # Configured capacity is operator policy and is never authored here.
        assert "configured_limit" not in published
        assert manager._scopes_pending_persist == set()

    @pytest.mark.asyncio
    async def test_a_failed_publish_keeps_the_scope_dirty_for_the_next_pass(
        self, workflow_module
    ):
        manager = _make_manager()
        _add_scope(manager, "shared", configured=8)
        _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 300,
                "report_id": "report-1",
            }
        )

        async def _failing_activity(*_args, **_kwargs):
            raise RuntimeError("activity unavailable")

        workflow_module.execute_activity = _failing_activity
        await manager._persist_adapted_scope_state()

        assert manager._scopes_pending_persist == {"shared"}
        # The in-workflow reduction still holds while the write is retried.
        assert manager._scopes["shared"].effective_limit == 4

    @pytest.mark.asyncio
    async def test_a_pre_marker_history_publishes_nothing(self, workflow_module):
        manager = _make_manager(scope_accounting=False)
        manager._scopes_pending_persist.add("shared")

        async def _unexpected(*_args, **_kwargs):
            raise AssertionError("pre-marker histories record no scope publish")

        workflow_module.execute_activity = _unexpected
        await manager._persist_adapted_scope_state()


# ---------------------------------------------------------------------------
# Replay compatibility for histories recorded before the marker
# ---------------------------------------------------------------------------


class TestPreMarkerHistories:
    def test_a_pre_marker_history_keeps_its_raw_lease_count_gate(
        self, workflow_module
    ):
        manager = _make_manager(scope_accounting=False)
        _add_scope(manager, "shared", configured=4)
        profile = _add_profile(manager, "p1", max_parallel_runs=2, scope_ref="shared")
        assert profile.reserve("wf-exec", NOW)
        assert profile.reserve_unmetered(
            "wf-repair",
            NOW,
            purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
        )

        assert manager._profile_execution_units(profile) == 2
        assert manager._profile_admitted_by_capacity(profile) is False

    def test_a_pre_marker_history_still_synthesizes_a_missing_scope(
        self, workflow_module
    ):
        manager = _make_manager(scope_accounting=False)
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")

        scope = manager._resolve_admission_scope(profile)
        assert scope is not None
        assert scope.configured_limit == 8

    def test_a_pre_marker_history_keeps_elapsed_time_recovery(
        self, workflow_module, clock
    ):
        manager = _make_manager(scope_accounting=False)
        scope = _add_scope(
            manager,
            "shared",
            configured=8,
            effective=4,
            healthy_since=(NOW - timedelta(hours=2)).isoformat(),
        )
        manager._recover_scope_capacity(clock.now())
        assert scope.effective_limit == 5

    def test_the_consolidated_limit_reproduces_a_pre_marker_admitted_limit(
        self, workflow_module
    ):
        """Consolidating the two limits must not move a replayed decision.

        A history recorded before the marker halved ``effective_limit`` and
        never touched ``adaptive_capacity_limit``. Reading only the adaptive
        owner would report the full ceiling and admit where that history
        recorded a refusal.
        """

        manager = _make_manager(scope_accounting=False)
        _add_scope(manager, "shared", configured=8)
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")
        # Exactly the state the pre-marker scoped cooldown path left behind.
        profile.effective_limit = 4
        assert profile.adaptive_capacity_limit is None

        assert profile.admission_limit == 4
        for index in range(4):
            assert profile.reserve(f"wf-{index}", NOW)
        assert manager._profile_admitted_by_capacity(profile) is False

    def test_a_pre_marker_history_keeps_its_unvalidated_cooldown_transition(
        self, workflow_module, clock
    ):
        manager = _make_manager(scope_accounting=False)
        _add_scope(manager, "shared", configured=8)
        profile = _add_profile(manager, "p1", max_parallel_runs=8, scope_ref="shared")

        manager.report_cooldown(
            {
                "profile_id": "p1",
                "failure_class": "rate_limit",
                "retry_after_seconds": 600,
                "report_id": "report-1",
                # A pre-marker history trusted the caller-supplied ref.
                "capacity_scope_ref": "caller-named",
            }
        )

        assert manager._scopes["caller-named"].effective_limit == 1
        assert profile.effective_limit == 4

    def test_the_marker_id_is_stable(self):
        assert (
            PROVIDER_SCOPE_ACCOUNTING_PATCH
            == "provider-profile-manager-scope-accounting-v1"
        )
