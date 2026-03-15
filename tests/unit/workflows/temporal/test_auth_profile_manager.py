"""Unit tests for MoonMind.AuthProfileManager workflow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from moonmind.workflows.temporal.workflows.auth_profile_manager import (
    WORKFLOW_NAME,
    MoonMindAuthProfileManagerWorkflow,
    ProfileSlotState,
)


# ---------------------------------------------------------------------------
# ProfileSlotState tests
# ---------------------------------------------------------------------------


class TestProfileSlotState:
    def test_available_slots_enabled(self):
        state = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=3,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
        )
        assert state.available_slots == 3
        assert state.is_available()

    def test_available_slots_disabled(self):
        state = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=3,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=False,
        )
        assert state.available_slots == 0
        assert not state.is_available()

    def test_available_slots_with_leases(self):
        state = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=2,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            current_leases=["wf1"],
        )
        assert state.available_slots == 1
        assert state.is_available()

    def test_available_slots_at_capacity(self):
        state = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            current_leases=["wf1"],
        )
        assert state.available_slots == 0
        assert not state.is_available()

    def test_cooldown_makes_unavailable(self):
        state = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=3,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            cooldown_until="2099-01-01T00:00:00+00:00",
        )
        assert not state.is_available()

    def test_reserve_success(self):
        state = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=2,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
        )
        assert state.reserve("wf1")
        assert "wf1" in state.current_leases
        assert state.available_slots == 1

    def test_reserve_fails_at_capacity(self):
        state = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            current_leases=["wf1"],
        )
        assert not state.reserve("wf2")
        assert "wf2" not in state.current_leases

    def test_release_success(self):
        state = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            current_leases=["wf1"],
        )
        assert state.release("wf1")
        assert "wf1" not in state.current_leases

    def test_release_nonexistent(self):
        state = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
        )
        assert not state.release("wf_unknown")

    def test_to_dict(self):
        state = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=2,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            current_leases=["wf1"],
            cooldown_until="2099-01-01T00:00:00+00:00",
        )
        d = state.to_dict()
        assert d["profile_id"] == "p1"
        assert d["current_leases"] == ["wf1"]
        assert d["cooldown_until"] == "2099-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Workflow helper tests (no Temporal runtime needed)
# ---------------------------------------------------------------------------


class TestAuthProfileManagerHelpers:
    def _make_workflow(self) -> MoonMindAuthProfileManagerWorkflow:
        wf = MoonMindAuthProfileManagerWorkflow()
        wf._runtime_id = "gemini_cli"
        return wf

    def test_restore_state(self):
        wf = self._make_workflow()
        wf._restore_state(
            {
                "runtime_id": "gemini_cli",
                "profiles": [
                    {
                        "profile_id": "p1",
                        "max_parallel_runs": 2,
                        "cooldown_after_429_seconds": 300,
                        "rate_limit_policy": "backoff",
                        "enabled": True,
                    },
                    {
                        "profile_id": "p2",
                        "max_parallel_runs": 1,
                        "rate_limit_policy": "queue",
                        "enabled": True,
                    },
                ],
                "leases": {"p1": ["wf1"]},
                "cooldowns": {"p2": "2099-01-01T00:00:00+00:00"},
            }
        )
        assert len(wf._profiles) == 2
        assert wf._profiles["p1"].current_leases == ["wf1"]
        assert wf._profiles["p2"].cooldown_until == "2099-01-01T00:00:00+00:00"

    def test_apply_profile_sync_adds_new(self):
        wf = self._make_workflow()
        wf._apply_profile_sync(
            [
                {
                    "profile_id": "p1",
                    "max_parallel_runs": 3,
                    "rate_limit_policy": "backoff",
                    "enabled": True,
                }
            ]
        )
        assert "p1" in wf._profiles
        assert wf._profiles["p1"].max_parallel_runs == 3

    def test_apply_profile_sync_updates_existing(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            current_leases=["wf1"],
        )
        wf._apply_profile_sync(
            [
                {
                    "profile_id": "p1",
                    "max_parallel_runs": 5,
                    "rate_limit_policy": "queue",
                    "enabled": True,
                }
            ]
        )
        assert wf._profiles["p1"].max_parallel_runs == 5
        assert wf._profiles["p1"].rate_limit_policy == "queue"
        # Leases should be preserved across sync.
        assert wf._profiles["p1"].current_leases == ["wf1"]

    def test_apply_profile_sync_disables_removed(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
        )
        # Sync with empty list — p1 should be disabled but not deleted.
        wf._apply_profile_sync([])
        assert "p1" in wf._profiles
        assert not wf._profiles["p1"].enabled

    def test_find_available_profile_picks_most_free(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            current_leases=["wf1"],
        )
        wf._profiles["p2"] = ProfileSlotState(
            profile_id="p2",
            max_parallel_runs=3,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
        )
        best = wf._find_available_profile()
        assert best is not None
        assert best.profile_id == "p2"

    def test_find_available_profile_none_available(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            current_leases=["wf1"],
        )
        assert wf._find_available_profile() is None

    def test_build_continue_as_new_input(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=2,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            current_leases=["wf1"],
            cooldown_until="2099-01-01T00:00:00+00:00",
        )
        data = wf._build_continue_as_new_input()
        assert data["runtime_id"] == "gemini_cli"
        assert len(data["profiles"]) == 1
        assert data["leases"]["p1"] == ["wf1"]
        assert data["cooldowns"]["p1"] == "2099-01-01T00:00:00+00:00"

    def test_clear_expired_cooldowns(self):
        wf = self._make_workflow()
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            cooldown_until=past,
        )
        wf._profiles["p2"] = ProfileSlotState(
            profile_id="p2",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            cooldown_until=future,
        )

        # Mock workflow.now() to return current time.
        with patch(
            "moonmind.workflows.temporal.workflows.auth_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime.now(timezone.utc)
            wf._clear_expired_cooldowns()

        assert wf._profiles["p1"].cooldown_until is None
        assert wf._profiles["p2"].cooldown_until is not None

    def test_clear_expired_cooldowns_invalid_iso(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            cooldown_until="not-a-date",
        )
        with patch(
            "moonmind.workflows.temporal.workflows.auth_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime.now(timezone.utc)
            wf._clear_expired_cooldowns()
        assert wf._profiles["p1"].cooldown_until is None


# ---------------------------------------------------------------------------
# Workflow registration sanity
# ---------------------------------------------------------------------------


def test_workflow_name():
    assert WORKFLOW_NAME == "MoonMind.AuthProfileManager"


def test_registered_workflow_types():
    from moonmind.workflows.temporal.workers import REGISTERED_TEMPORAL_WORKFLOW_TYPES

    assert "MoonMind.AuthProfileManager" in REGISTERED_TEMPORAL_WORKFLOW_TYPES


# ---------------------------------------------------------------------------
# DB model sanity
# ---------------------------------------------------------------------------


def test_temporal_workflow_type_enum():
    from api_service.db.models import TemporalWorkflowType

    assert TemporalWorkflowType.AUTH_PROFILE_MANAGER.value == "MoonMind.AuthProfileManager"


def test_managed_agent_auth_mode_enum():
    from api_service.db.models import ManagedAgentAuthMode

    assert ManagedAgentAuthMode.OAUTH.value == "oauth"
    assert ManagedAgentAuthMode.API_KEY.value == "api_key"


def test_managed_agent_rate_limit_policy_enum():
    from api_service.db.models import ManagedAgentRateLimitPolicy

    assert ManagedAgentRateLimitPolicy.BACKOFF.value == "backoff"
    assert ManagedAgentRateLimitPolicy.QUEUE.value == "queue"
    assert ManagedAgentRateLimitPolicy.FAIL_FAST.value == "fail_fast"


# ---------------------------------------------------------------------------
# Activity catalog entry
# ---------------------------------------------------------------------------


def test_auth_profile_list_activity_in_catalog():
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

    catalog = build_default_activity_catalog()
    route = catalog.resolve_activity("auth_profile.list")
    assert route.task_queue == "mm.activity.artifacts"
    assert route.fleet == "artifacts"
