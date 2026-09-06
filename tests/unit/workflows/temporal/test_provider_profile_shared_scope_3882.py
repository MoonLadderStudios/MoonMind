"""Shared-scope accounting acceptance for MoonLadderStudios/MoonMind#3882.

Reproductions through real manager boundaries (admission, signals, restore,
sync) for the finished shared-scope contract: joint profile+scope admission,
idempotent whole-transition backpressure, validated report ownership, and
evidence-driven recovery.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from moonmind.provider_profiles.lease_client import CredentialLeasePurpose
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    CapacityScopeState,
    MoonMindProviderProfileManagerWorkflow,
    PendingRequest,
    ProfileSlotState,
)

MANAGER_MODULE = (
    "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
)
NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
EXECUTION = CredentialLeasePurpose.EXECUTION_DIRECT.value


def _manager() -> MoonMindProviderProfileManagerWorkflow:
    wf = MoonMindProviderProfileManagerWorkflow()
    wf._runtime_id = "codex_cli"
    wf._scopes_authoritative = True
    return wf


def _profile(
    profile_id: str,
    max_runs: int,
    scope_ref: str,
    *,
    purpose_aware: bool = True,
    credential_source: str | None = None,
) -> ProfileSlotState:
    return ProfileSlotState(
        profile_id=profile_id,
        max_parallel_runs=max_runs,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        is_default=True,
        capacity_scope_ref=scope_ref,
        effective_limit=max_runs,
        purpose_aware_capacity=purpose_aware,
        credential_source=credential_source,
    )


def _scope(scope_ref: str, configured: int, effective: int) -> CapacityScopeState:
    return CapacityScopeState(
        scope_ref=scope_ref,
        runtime_id="codex_cli",
        configured_limit=configured,
        effective_limit=effective,
    )


def _execution_metadata(scope_ref: str, generation: int = 1) -> dict:
    return {
        "purpose": EXECUTION,
        "capacityScopeRef": scope_ref,
        "scopeGeneration": generation,
    }


class _FakeWorkflow:
    """Minimal temporal workflow surface for direct handler tests."""

    def __init__(self, now: datetime, *, patched: bool = True):
        self._now = now
        self._patched = patched

    def patched(self, _patch_id: str) -> bool:
        return self._patched

    def now(self) -> datetime:
        return self._now

    @property
    def logger(self):
        import logging

        return logging.getLogger("test-provider-profile-manager")

    def info(self):
        raise RuntimeError("no workflow info outside a workflow")


def _run_with_fake(wf, now: datetime, *, patched: bool = True):
    return patch(MANAGER_MODULE, _FakeWorkflow(now, patched=patched))


def _grant(
    profile: ProfileSlotState,
    lease_id: str,
    scope_ref: str,
    now: datetime,
    *,
    purpose: str = EXECUTION,
) -> None:
    assert profile.reserve(
        lease_id,
        now,
        purpose=purpose,
        metadata={
            "purpose": purpose,
            "capacityScopeRef": scope_ref,
            "scopeGeneration": 1,
        },
    )


# ---------------------------------------------------------------------------
# AC1: joint admission under a shared ceiling
# ---------------------------------------------------------------------------


class TestJointAdmission:
    def test_two_profiles_share_one_ceiling_of_ten(self):
        wf = _manager()
        wf._scopes["shared"] = _scope("shared", 10, 10)
        for pid in ("a", "b"):
            wf._profiles[pid] = _profile(pid, 8, "shared")
        with _run_with_fake(wf, NOW):
            admitted = 0
            for i in range(16):
                target = wf._profiles["a" if i % 2 == 0 else "b"]
                if wf._profile_admitted_by_capacity(target):
                    _grant(target, f"wf-{i}", "shared", NOW)
                    admitted += 1
            assert admitted == 10
            assert wf._scope_active_units("shared") == 10
            assert wf._profile_admitted_by_capacity(wf._profiles["a"]) is False
            assert wf._profile_admitted_by_capacity(wf._profiles["b"]) is False

    def test_final_unit_race_admits_exactly_one(self):
        wf = _manager()
        wf._scopes["shared"] = _scope("shared", 10, 10)
        for pid in ("a", "b"):
            wf._profiles[pid] = _profile(pid, 8, "shared")
        with _run_with_fake(wf, NOW):
            for i in range(9):
                target = wf._profiles["a" if i % 2 == 0 else "b"]
                assert wf._profile_admitted_by_capacity(target) is True
                _grant(target, f"wf-{i}", "shared", NOW)
            # One unit remains: the first contender wins, the second loses,
            # and neither side can admit past the shared ceiling.
            assert wf._profile_admitted_by_capacity(wf._profiles["a"]) is True
            _grant(wf._profiles["a"], "wf-race-a", "shared", NOW)
            assert wf._profile_admitted_by_capacity(wf._profiles["b"]) is False
            assert wf._scope_active_units("shared") == 10

    def test_retry_for_existing_holder_counts_no_new_unit(self):
        wf = _manager()
        wf._scopes["shared"] = _scope("shared", 10, 10)
        wf._profiles["a"] = _profile("a", 8, "shared")
        with _run_with_fake(wf, NOW):
            _grant(wf._profiles["a"], "wf-1", "shared", NOW)
            # A retry resolves to the held lease instead of consuming
            # another unit of the shared allowance.
            wf._rebuild_lease_indexes()
            assert wf._profile_id_for_lease("wf-1") == "a"
            assert wf._scope_active_units("shared") == 1


# ---------------------------------------------------------------------------
# AC2: validation/maintenance agreement, repair never deadlocks
# ---------------------------------------------------------------------------


class TestPurposeAccountingAgreement:
    def test_profile_and_scope_counts_agree(self):
        wf = _manager()
        wf._scopes["shared"] = _scope("shared", 10, 10)
        # Credentialless validation is admitted beside executions under its
        # non-mutating probe contract; repair never spends shared units.
        wf._profiles["a"] = _profile("a", 8, "shared", credential_source="none")
        profile = wf._profiles["a"]
        with _run_with_fake(wf, NOW):
            _grant(profile, "exec-1", "shared", NOW)
            _grant(
                profile,
                "val-1",
                "shared",
                NOW,
                purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION.value,
            )
            _grant(
                profile,
                "repair-1",
                "shared",
                NOW,
                purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
            )
            # Validation spends the shared upstream resource on both layers;
            # repair spends neither, so a saturated scope never blocks a
            # required credential repair.
            assert profile.capacity_consuming_lease_count() == 2
            assert wf._scope_active_units("shared") == 2
            assert profile.execution_lease_count == 1

    def test_repair_does_not_consume_a_full_scope(self):
        wf = _manager()
        wf._scopes["shared"] = _scope("shared", 2, 2)
        wf._profiles["a"] = _profile("a", 8, "shared")
        profile = wf._profiles["a"]
        with _run_with_fake(wf, NOW):
            _grant(profile, "exec-1", "shared", NOW)
            _grant(profile, "exec-2", "shared", NOW)
            assert wf._profile_admitted_by_capacity(profile) is False
            _grant(
                profile,
                "repair-1",
                "shared",
                NOW,
                purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR.value,
            )
            # The repair holds no shared unit: scope usage is unchanged and
            # the scope still reports exactly its execution load.
            assert wf._scope_active_units("shared") == 2


# ---------------------------------------------------------------------------
# AC3: idempotent, ownership-validated backpressure
# ---------------------------------------------------------------------------


class TestIdempotentBackpressure:
    def _admitting_manager(self):
        wf = _manager()
        wf._scopes["shared"] = _scope("shared", 8, 8)
        wf._profiles["a"] = _profile("a", 8, "shared")
        wf._profiles["b"] = _profile("b", 8, "shared")
        return wf

    def test_same_429_twice_changes_nothing_second_time(self):
        wf = self._admitting_manager()
        payload = {
            "profile_id": "a",
            "retry_after_seconds": 100,
            "report_id": "attempt-1",
            "requester_workflow_id": "wf-1",
            "capacity_scope_ref": "shared",
        }
        with _run_with_fake(wf, NOW):
            wf.report_cooldown(dict(payload))
            scope = wf._scopes["shared"]
            profile = wf._profiles["a"]
            assert scope.effective_limit == 4
            assert profile.adaptive_capacity_limit == 4
            first_profile_deadline = profile.cooldown_until
            first_scope_deadline = scope.cooldown_until
            first_decrease = scope.last_decrease_at
            wf.report_cooldown(dict(payload))
            assert scope.effective_limit == 4
            assert profile.adaptive_capacity_limit == 4
            assert profile.cooldown_until == first_profile_deadline
            assert scope.cooldown_until == first_scope_deadline
            assert scope.last_decrease_at == first_decrease

    def test_legacy_profile_halves_legacy_limit_once(self):
        wf = _manager()
        wf._scopes["shared"] = _scope("shared", 8, 8)
        wf._profiles["legacy"] = _profile(
            "legacy", 8, "shared", purpose_aware=False
        )
        payload = {
            "profile_id": "legacy",
            "retry_after_seconds": 100,
            "report_id": "attempt-9",
        }
        with _run_with_fake(wf, NOW):
            wf.report_cooldown(dict(payload))
            assert wf._profiles["legacy"].effective_limit == 4
            assert wf._profiles["legacy"].adaptive_capacity_limit is None
            wf.report_cooldown(dict(payload))
            assert wf._profiles["legacy"].effective_limit == 4
            assert wf._scopes["shared"].effective_limit == 4

    def test_wrong_scope_report_is_ignored(self):
        wf = self._admitting_manager()
        with _run_with_fake(wf, NOW):
            wf.report_cooldown(
                {
                    "profile_id": "a",
                    "retry_after_seconds": 100,
                    "report_id": "forged",
                    "capacity_scope_ref": "some-other-scope",
                }
            )
            assert wf._scopes["shared"].effective_limit == 8
            assert wf._profiles["a"].adaptive_capacity_limit is None
            assert wf._profiles["a"].cooldown_until is None
            assert "some-other-scope" not in wf._scopes

    def test_stale_generation_report_is_ignored(self):
        wf = self._admitting_manager()
        wf._scopes["shared"].generation = 3
        with _run_with_fake(wf, NOW):
            wf.report_cooldown(
                {
                    "profile_id": "a",
                    "retry_after_seconds": 100,
                    "report_id": "stale",
                    "capacity_scope_ref": "shared",
                    "scope_generation": 2,
                }
            )
            assert wf._scopes["shared"].effective_limit == 8
            assert wf._profiles["a"].adaptive_capacity_limit is None

    def test_sibling_in_scope_cannot_evade_the_limit(self):
        wf = self._admitting_manager()
        with _run_with_fake(wf, NOW):
            wf.report_cooldown(
                {
                    "profile_id": "a",
                    "retry_after_seconds": 100,
                    "report_id": "storm-1",
                }
            )
            # The shared scope halved even though only profile A reported,
            # so profile B is still bound by the joint allowance.
            assert wf._scopes["shared"].effective_limit == 4
            assert wf._profiles["b"].adaptive_capacity_limit is None


# ---------------------------------------------------------------------------
# AC4: Retry-After extends only; disabled never auto-recovers
# ---------------------------------------------------------------------------


class TestCooldownAndDisabled:
    def test_retry_after_never_shortens_profile_deadline(self):
        wf = _manager()
        wf._scopes["shared"] = _scope("shared", 8, 8)
        wf._profiles["a"] = _profile("a", 8, "shared")
        with _run_with_fake(wf, NOW):
            wf.report_cooldown(
                {
                    "profile_id": "a",
                    "retry_after_seconds": 500,
                    "report_id": "long",
                }
            )
            long_deadline = wf._profiles["a"].cooldown_until
            wf.report_cooldown(
                {
                    "profile_id": "a",
                    "retry_after_seconds": 10,
                    "report_id": "short",
                }
            )
            assert wf._profiles["a"].cooldown_until == long_deadline
            assert wf._scopes["shared"].cooldown_until == long_deadline

    def test_credential_error_never_shortens_profile_deadline(self):
        wf = _manager()
        wf._scopes["shared"] = _scope("shared", 8, 8)
        wf._profiles["a"] = _profile("a", 8, "shared")
        with _run_with_fake(wf, NOW):
            wf.report_cooldown(
                {
                    "profile_id": "a",
                    "failure_class": "credential_revoked",
                    "cooldown_seconds": 900,
                }
            )
            deadline = wf._profiles["a"].cooldown_until
            assert deadline is not None
            # The shared scope is untouched by a profile-credential error.
            assert wf._scopes["shared"].effective_limit == 8
            assert wf._scopes["shared"].cooldown_until is None
            wf.report_cooldown(
                {
                    "profile_id": "a",
                    "failure_class": "credential_revoked",
                    "cooldown_seconds": 5,
                }
            )
            assert wf._profiles["a"].cooldown_until == deadline

    def test_disabled_scope_ignores_success_and_recovery(self):
        wf = _manager()
        wf._scopes["shared"] = CapacityScopeState(
            scope_ref="shared",
            configured_limit=8,
            effective_limit=2,
            backpressure_state="disabled",
        )
        with _run_with_fake(wf, NOW):
            wf.report_provider_success(
                {"capacity_scope_ref": "shared", "observation_id": "obs-1"}
            )
            assert wf._scopes["shared"].last_success_at is None
            wf._recover_scope_capacity(NOW + timedelta(seconds=3600))
            assert wf._scopes["shared"].effective_limit == 2
            assert wf._scopes["shared"].backpressure_state == "disabled"


# ---------------------------------------------------------------------------
# AC5: evidence-driven, interval-bounded, ceiling-capped recovery
# ---------------------------------------------------------------------------


class TestEvidenceDrivenRecovery:
    def _reduced_scope(self, **overrides):
        base = {
            "scope_ref": "shared",
            "configured_limit": 8,
            "effective_limit": 4,
            "backpressure_state": "reduced",
            "last_decrease_at": (NOW - timedelta(seconds=900)).isoformat(),
            "healthy_since": (NOW - timedelta(seconds=800)).isoformat(),
        }
        base.update(overrides)
        return CapacityScopeState(**base)

    def test_recovery_steps_with_fresh_evidence_then_waits(self):
        wf = _manager()
        wf._scopes["shared"] = self._reduced_scope(
            last_success_at=(NOW - timedelta(seconds=700)).isoformat()
        )
        wf._recover_scope_capacity(NOW)
        assert wf._scopes["shared"].effective_limit == 5
        # Interval-bounded: an immediate second pass must not step again.
        wf._recover_scope_capacity(NOW + timedelta(seconds=10))
        assert wf._scopes["shared"].effective_limit == 5
        wf._recover_scope_capacity(NOW + timedelta(seconds=301))
        assert wf._scopes["shared"].effective_limit == 6

    def test_recovery_never_exceeds_configured_ceiling(self):
        wf = _manager()
        wf._scopes["shared"] = self._reduced_scope(
            effective_limit=8,
            last_success_at=(NOW - timedelta(seconds=700)).isoformat(),
        )
        wf._recover_scope_capacity(NOW)
        assert wf._scopes["shared"].effective_limit == 8

    def test_stale_evidence_predating_decrease_heals_nothing(self):
        wf = _manager()
        wf._scopes["shared"] = self._reduced_scope(
            last_success_at=(NOW - timedelta(seconds=1000)).isoformat()
        )
        wf._recover_scope_capacity(NOW)
        assert wf._scopes["shared"].effective_limit == 4

    def test_out_of_order_duplicate_success_applies_once(self):
        wf = _manager()
        wf._scopes["shared"] = self._reduced_scope()
        with _run_with_fake(wf, NOW):
            assert (
                wf._record_scope_success(
                    wf._scopes["shared"],
                    NOW,
                    observation_key="shared\x001\x00obs-7",
                )
                is True
            )
            assert (
                wf._record_scope_success(
                    wf._scopes["shared"],
                    NOW,
                    observation_key="shared\x001\x00obs-7",
                )
                is False
            )
            assert wf._scopes["shared"].last_success_at == NOW.isoformat()

    def test_completed_holder_reclaim_is_evidence_failed_is_not(self):
        wf = _manager()
        wf._scopes["shared"] = self._reduced_scope()
        wf._profiles["a"] = _profile("a", 8, "shared")
        _grant(wf._profiles["a"], "wf-done", "shared", NOW)
        _grant(wf._profiles["a"], "wf-failed", "shared", NOW)
        with _run_with_fake(wf, NOW):
            assert (
                wf._reclaim_terminal_leases(
                    {
                        "wf-done": {"running": False, "status": "COMPLETED"},
                        "wf-failed": {"running": False, "status": "FAILED"},
                    }
                )
                is True
            )
            assert wf._scopes["shared"].last_success_at == NOW.isoformat()
        wf2 = _manager()
        wf2._scopes["shared"] = self._reduced_scope()
        wf2._profiles["a"] = _profile("a", 8, "shared")
        _grant(wf2._profiles["a"], "wf-failed", "shared", NOW)
        with _run_with_fake(wf2, NOW):
            wf2._reclaim_terminal_leases(
                {"wf-failed": {"running": False, "status": "FAILED"}}
            )
            assert wf2._scopes["shared"].last_success_at is None

    @pytest.mark.asyncio
    async def test_classified_release_is_evidence_unclassified_is_not(self):
        wf = _manager()
        wf._scopes["shared"] = self._reduced_scope()
        wf._profiles["a"] = _profile("a", 8, "shared")
        _grant(wf._profiles["a"], "wf-ok", "shared", NOW)
        _grant(wf._profiles["a"], "wf-plain", "shared", NOW)
        wf._lease_transition_contract = False
        wf._durable_maintenance_queue = False
        with _run_with_fake(wf, NOW, patched=False):
            await wf.release_slot(
                {
                    "profile_id": "a",
                    "requester_workflow_id": "wf-ok",
                    "result_class": "succeeded",
                }
            )
            assert wf._scopes["shared"].last_success_at == NOW.isoformat()
            wf._scopes["shared"].last_success_at = None
            await wf.release_slot(
                {"profile_id": "a", "requester_workflow_id": "wf-plain"}
            )
            assert wf._scopes["shared"].last_success_at is None


# ---------------------------------------------------------------------------
# AC6: reassignment, restart, races, malformed state preserve ownership
# ---------------------------------------------------------------------------


class TestOwnershipPreservation:
    def test_scope_move_keeps_old_units_until_release(self):
        wf = _manager()
        wf._scopes["old"] = _scope("old", 10, 10)
        wf._scopes["new"] = _scope("new", 10, 10)
        wf._profiles["a"] = _profile("a", 8, "old")
        with _run_with_fake(wf, NOW):
            _grant(wf._profiles["a"], "wf-1", "old", NOW)
            _grant(wf._profiles["a"], "wf-2", "old", NOW)
            assert wf._scope_active_units("old") == 2
            # The operator moves the profile while work is active.
            wf._apply_profile_sync(
                [
                    {
                        "profile_id": "a",
                        "max_parallel_runs": 8,
                        "capacity_scope_ref": "new",
                    }
                ],
                authoritative=True,
            )
            # Existing units stay on the old allowance; new grants spend
            # the new one.
            assert wf._scope_active_units("old") == 2
            assert wf._scope_active_units("new") == 0
            _grant(wf._profiles["a"], "wf-3", "new", NOW)
            assert wf._scope_active_units("new") == 1
            assert wf._scope_active_units("old") == 2

    def test_continue_as_new_round_trip_preserves_authority(self):
        wf = _manager()
        wf._scopes["shared"] = CapacityScopeState(
            scope_ref="shared",
            configured_limit=10,
            effective_limit=6,
            backpressure_state="reduced",
            last_decrease_at=(NOW - timedelta(seconds=900)).isoformat(),
            last_success_at=(NOW - timedelta(seconds=700)).isoformat(),
        )
        wf._profiles["a"] = _profile("a", 8, "shared")
        wf._profiles["b"] = _profile("b", 8, "shared")
        with _run_with_fake(wf, NOW):
            _grant(wf._profiles["a"], "wf-1", "shared", NOW)
            _grant(wf._profiles["b"], "wf-2", "shared", NOW)
            wf._seen_rate_limit_reports.append("a\x00shared\x001\x00r1\x00wf-1")
            wf._seen_success_observations.append("shared\x001\x00obs-1")
            payload = wf._build_continue_as_new_input()
        successor = _manager()
        successor._purpose_aware_capacity_ledger = True
        with _run_with_fake(successor, NOW):
            successor._restore_state(payload)
            scope = successor._scopes["shared"]
            assert scope.configured_limit == 10
            assert scope.effective_limit == 6
            assert scope.backpressure_state == "reduced"
            assert scope.last_success_at == (NOW - timedelta(seconds=700)).isoformat()
            assert successor._seen_rate_limit_reports == ["a\x00shared\x001\x00r1\x00wf-1"]
            assert successor._seen_success_observations == ["shared\x001\x00obs-1"]
            assert successor._scope_active_units("shared") == 2
            # The stamped grant identity survived the rollover, so the
            # duplicate 429 still deduplicates against restored state.
            successor.report_cooldown(
                {
                    "profile_id": "a",
                    "retry_after_seconds": 100,
                    "report_id": "r1",
                    "requester_workflow_id": "wf-1",
                    "capacity_scope_ref": "shared",
                }
            )
            assert successor._scopes["shared"].effective_limit == 6

    def test_reduce_below_active_usage_blocks_grants_not_work(self):
        wf = _manager()
        wf._scopes["shared"] = _scope("shared", 10, 10)
        wf._profiles["a"] = _profile("a", 8, "shared")
        wf._profiles["b"] = _profile("b", 8, "shared")
        with _run_with_fake(wf, NOW):
            for i in range(6):
                _grant(wf._profiles["a" if i % 2 == 0 else "b"], f"wf-{i}", "shared", NOW)
            wf._apply_scope_sync(
                [
                    {
                        "scope_ref": "shared",
                        "runtime_id": "codex_cli",
                        "configured_limit": 4,
                    }
                ]
            )
            scope = wf._scopes["shared"]
            assert scope.configured_limit == 4
            assert scope.effective_limit == 4
            # Existing work is untouched; new grants are refused.
            assert wf._scope_active_units("shared") == 6
            assert wf._profile_admitted_by_capacity(wf._profiles["a"]) is False

    def test_missing_scope_with_active_leases_is_provisional(self):
        wf = _manager()
        wf._profiles["a"] = _profile("a", 8, "shared")
        wf._profiles["b"] = _profile("b", 8, "shared")
        with _run_with_fake(wf, NOW):
            _grant(wf._profiles["a"], "wf-1", "shared", NOW)
            scope = wf._ensure_scope("shared", runtime_id="codex_cli")
            assert scope.provisional is True
            assert scope.configured_limit == 8
            assert scope.effective_limit == 1
            # New grants cannot be admitted against the guessed allowance
            # while the authoritative row is missing.
            assert wf._profile_admitted_by_capacity(wf._profiles["b"]) is False
            assert any(
                entry.get("kind") == "scope_synthesized"
                for entry in wf._lease_index_conflicts
            )
            # Automatic recovery stays off the provisional scope.
            wf._recover_scope_capacity(NOW + timedelta(seconds=3600))
            assert scope.effective_limit == 1
            # The authoritative sync adopts the real limits and clears the
            # provisional marker.
            wf._apply_scope_sync(
                [
                    {
                        "scope_ref": "shared",
                        "runtime_id": "codex_cli",
                        "configured_limit": 10,
                        "effective_limit": 10,
                    }
                ]
            )
            assert scope.provisional is False
            assert scope.configured_limit == 10
            assert scope.effective_limit == 10
            assert wf._profile_admitted_by_capacity(wf._profiles["b"]) is True

    def test_malformed_scope_entries_do_not_break_restore(self):
        wf = _manager()
        wf._profiles["a"] = _profile("a", 8, "shared")
        payload = {
            "runtime_id": "codex_cli",
            "profiles": [
                {
                    "profile_id": "a",
                    "max_parallel_runs": 8,
                    "capacity_scope_ref": "shared",
                }
            ],
            "leases": {},
            "lease_granted_at": {},
            "lease_metadata": {},
            "cooldowns": {},
            "pending_requests": [],
            "scopes": [
                {
                    "scope_ref": "shared",
                    "configured_limit": 10,
                    "effective_limit": 10,
                    "generation": "not-a-number",
                    "cooldown_until": "not-a-date",
                    "backpressure_state": "exploding",
                },
                {"scope_ref": "", "configured_limit": 10},
                {"configured_limit": 10},
                "not-a-dict",
            ],
        }
        with _run_with_fake(wf, NOW):
            wf._restore_state(payload)
            scope = wf._scopes["shared"]
            assert scope.configured_limit == 10
            assert scope.generation == 1
            assert scope.cooldown_until is None
            assert scope.backpressure_state == "healthy"


# ---------------------------------------------------------------------------
# Safe operator view
# ---------------------------------------------------------------------------


class TestSafeScopeView:
    def test_scope_view_matches_admission_without_identities(self):
        wf = _manager()
        wf._scopes["shared"] = _scope("shared", 10, 8)
        wf._profiles["a"] = _profile("a", 8, "shared")
        with _run_with_fake(wf, NOW):
            _grant(wf._profiles["a"], "wf-1", "shared", NOW)
            wf._pending_requests.append(
                PendingRequest(
                    requester_workflow_id="wf-waiting",
                    runtime_id="codex_cli",
                    execution_profile_ref="a",
                )
            )
            view = wf.get_state()["scopes"]["shared"]
            assert view["configured_limit"] == 10
            assert view["effective_limit"] == 8
            assert view["active_units"] == wf._scope_active_units("shared") == 1
            assert view["queued"] == 1
            assert view["cooldown_active"] is False
            assert "wf-1" not in str(view)
