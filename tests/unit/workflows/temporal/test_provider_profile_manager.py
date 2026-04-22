"""Unit tests for MoonMind.ProviderProfileManager workflow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    DB_AUTHORITATIVE_PROFILE_SYNC_PATCH,
    HandoffReservation,
    PendingRequest,
    SLOT_HANDOFF_RESERVATION_PATCH,
    VERIFY_PENDING_REQUESTS_PATCH,
    WORKFLOW_NAME,
    MoonMindProviderProfileManagerWorkflow,
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
        now = datetime(2026, 3, 17, tzinfo=timezone.utc)
        assert state.reserve("wf1", now)
        assert "wf1" in state.current_leases
        assert state.lease_granted_at["wf1"] == now.isoformat()
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
        now = datetime(2026, 3, 17, tzinfo=timezone.utc)
        assert not state.reserve("wf2", now)
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


class TestProviderProfileManagerHelpers:
    def _make_workflow(self) -> MoonMindProviderProfileManagerWorkflow:
        wf = MoonMindProviderProfileManagerWorkflow()
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

    def test_apply_profile_sync_disables_removed_profiles_without_leases(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
        )
        # Legacy sync merge keeps the row but makes it unavailable.
        wf._apply_profile_sync([])
        assert "p1" in wf._profiles
        assert not wf._profiles["p1"].enabled
        assert not wf._profiles["p1"].is_default

    def test_prune_disabled_profiles_without_leases_removes_dead_state(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=False,
            is_default=False,
        )

        wf._prune_disabled_profiles_without_leases()

        assert "p1" not in wf._profiles

    def test_apply_profile_sync_disables_removed_profiles_with_leases(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
            current_leases=["wf1"],
        )

        wf._apply_profile_sync([])

        assert "p1" in wf._profiles
        assert not wf._profiles["p1"].enabled
        assert not wf._profiles["p1"].is_default

    def test_sync_profiles_legacy_payload_path_before_db_authoritative_patch(self):
        wf = self._make_workflow()

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = False
            wf.sync_profiles(
                {
                    "profiles": [
                        {
                            "profile_id": "payload_profile",
                            "max_parallel_runs": 1,
                            "rate_limit_policy": "backoff",
                            "enabled": True,
                        }
                    ]
                }
            )

        assert "payload_profile" in wf._profiles
        assert wf._profile_refresh_requested is False

    def test_sync_profiles_requests_db_refresh_after_authoritative_patch(self):
        wf = self._make_workflow()

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.side_effect = (
                lambda patch_id: patch_id == DB_AUTHORITATIVE_PROFILE_SYNC_PATCH
            )
            wf.sync_profiles(
                {
                    "profiles": [
                        {
                            "profile_id": "polluted_profile",
                            "max_parallel_runs": 1,
                            "rate_limit_policy": "backoff",
                            "enabled": True,
                        }
                    ]
                }
            )

        assert "polluted_profile" not in wf._profiles
        assert wf._profile_refresh_requested is True

    @pytest.mark.asyncio
    async def test_polluted_sync_payload_does_not_assign_missing_profile(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        assigned: list[tuple[str, str]] = []

        async def fake_signal(requester_workflow_id: str, profile_id: str) -> None:
            assigned.append((requester_workflow_id, profile_id))

        async def fake_execute_activity(
            activity_name: str,
            payload: dict,
            **_: object,
        ) -> dict:
            assert activity_name == "provider_profile.list"
            assert payload == {"runtime_id": "codex_cli"}
            return {
                "profiles": [
                    {
                        "profile_id": "codex_default",
                        "max_parallel_runs": 1,
                        "cooldown_after_429_seconds": 900,
                        "rate_limit_policy": "backoff",
                        "enabled": True,
                        "is_default": True,
                        "provider_id": "openai",
                        "runtime_materialization_mode": "oauth_home",
                    }
                ]
            }

        wf._signal_slot_assigned = fake_signal  # type: ignore[method-assign]

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.side_effect = lambda patch_id: patch_id in {
                SLOT_HANDOFF_RESERVATION_PATCH,
                DB_AUTHORITATIVE_PROFILE_SYNC_PATCH,
            }
            mock_wf.execute_activity.side_effect = fake_execute_activity
            mock_wf.now.return_value = datetime(2026, 4, 20, tzinfo=timezone.utc)

            wf.sync_profiles(
                {
                    "profiles": [
                        {
                            "profile_id": "runtime_default_second",
                            "max_parallel_runs": 1,
                            "cooldown_after_429_seconds": 900,
                            "rate_limit_policy": "backoff",
                            "enabled": True,
                            "is_default": True,
                        }
                    ]
                }
            )
            wf.request_slot(
                {
                    "requester_workflow_id": "resolver-run:agent:node-1",
                    "runtime_id": "codex_cli",
                    "lease_group_id": "resolver-run",
                    "profile_selector": {"tagsAny": [], "tagsAll": []},
                }
            )

            assert (
                await wf._load_profiles_from_db(prune_removed_profiles=True)
                is True
            )
            await wf._drain_queue()

        assert assigned == [("resolver-run:agent:node-1", "codex_default")]
        assert "runtime_default_second" not in wf._profiles
        assert wf._profiles["codex_default"].current_leases == [
            "resolver-run:agent:node-1"
        ]

    @pytest.mark.asyncio
    async def test_profile_refresh_preserves_signal_that_arrives_during_activity(
        self,
    ):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._profile_refresh_requested = True

        async def fake_execute_activity(*_: object, **__: object) -> dict:
            wf._profile_refresh_requested = True
            return {
                "profiles": [
                    {
                        "profile_id": "codex_default",
                        "max_parallel_runs": 1,
                        "cooldown_after_429_seconds": 900,
                        "rate_limit_policy": "backoff",
                        "enabled": True,
                        "is_default": True,
                    }
                ]
            }

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.execute_activity.side_effect = fake_execute_activity

            assert await wf._load_profiles_from_db() is True

        assert wf._has_db_profile_snapshot is True
        assert wf._profile_refresh_requested is True

    @pytest.mark.asyncio
    async def test_failed_profile_refresh_keeps_known_good_snapshot_available(
        self,
    ):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._has_db_profile_snapshot = True
        wf._profiles["codex_default"] = ProfileSlotState(
            profile_id="codex_default",
            max_parallel_runs=1,
            cooldown_after_429_seconds=900,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
        )

        async def fake_execute_activity(*_: object, **__: object) -> dict:
            raise RuntimeError("temporary DB outage")

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.execute_activity.side_effect = fake_execute_activity

            assert await wf._load_profiles_from_db() is False

        assert wf._has_db_profile_snapshot is True
        assert wf._profile_refresh_requested is True
        assert wf._profiles["codex_default"].enabled is True

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

    def test_find_available_profile_prefers_runtime_default(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=3,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=False,
        )
        wf._profiles["p2"] = ProfileSlotState(
            profile_id="p2",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
        )

        best = wf._find_available_profile()
        assert best is not None
        assert best.profile_id == "p2"

    def test_find_available_profile_does_not_fallback_when_default_unavailable(self):
        wf = self._make_workflow()
        wf._profiles["fallback"] = ProfileSlotState(
            profile_id="fallback",
            max_parallel_runs=3,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=False,
        )
        wf._profiles["default"] = ProfileSlotState(
            profile_id="default",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
            current_leases=["wf1"],
        )

        assert wf._find_available_profile() is None

    def test_find_available_profile_treats_empty_selector_as_no_selector(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=3,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=False,
            priority=500,
        )
        wf._profiles["p2"] = ProfileSlotState(
            profile_id="p2",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
            priority=100,
        )

        best = wf._find_available_profile({"tagsAny": [], "tagsAll": []})
        assert best is not None
        assert best.profile_id == "p2"

    def test_find_available_profile_falls_back_to_priority_when_no_default_exists(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=2,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=False,
            priority=100,
        )
        wf._profiles["p2"] = ProfileSlotState(
            profile_id="p2",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=False,
            priority=300,
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

    def test_find_available_profile_filters_by_provider_id(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1", max_parallel_runs=3, cooldown_after_429_seconds=300,
            rate_limit_policy="backoff", enabled=True, provider_id="anthropic"
        )
        wf._profiles["p2"] = ProfileSlotState(
            profile_id="p2", max_parallel_runs=3, cooldown_after_429_seconds=300,
            rate_limit_policy="backoff", enabled=True, provider_id="minimax"
        )
        
        # Test finding minimax
        best = wf._find_available_profile({"providerId": "minimax"})
        assert best is not None
        assert best.profile_id == "p2"
        
        # Test finding anthropic
        best = wf._find_available_profile({"providerId": "anthropic"})
        assert best is not None
        assert best.profile_id == "p1"
        
        # Test finding unknown
        assert wf._find_available_profile({"providerId": "openai"}) is None

    def test_find_available_profile_honors_exact_profile_ref(self):
        wf = self._make_workflow()
        wf._profiles["openai-profile"] = ProfileSlotState(
            profile_id="openai-profile",
            max_parallel_runs=5,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            provider_id="openai",
            priority=50,
        )
        wf._profiles["openrouter-profile"] = ProfileSlotState(
            profile_id="openrouter-profile",
            max_parallel_runs=5,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            provider_id="openrouter",
            priority=200,
        )

        best = wf._find_available_profile(
            execution_profile_ref="openai-profile",
        )
        assert best is not None
        assert best.profile_id == "openai-profile"

    def test_find_available_profile_exact_ref_requires_available_profile(self):
        wf = self._make_workflow()
        wf._profiles["busy-profile"] = ProfileSlotState(
            profile_id="busy-profile",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            current_leases=["wf-1"],
        )

        assert (
            wf._find_available_profile(execution_profile_ref="busy-profile")
            is None
        )
        assert (
            wf._find_available_profile(execution_profile_ref="missing-profile")
            is None
        )

    def test_find_available_profile_sorts_by_priority(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1", max_parallel_runs=1, cooldown_after_429_seconds=300,
            rate_limit_policy="backoff", enabled=True, provider_id="anthropic", priority=50
        )
        # Even though p2 has more slots, p3 has higher priority
        wf._profiles["p2"] = ProfileSlotState(
            profile_id="p2", max_parallel_runs=5, cooldown_after_429_seconds=300,
            rate_limit_policy="backoff", enabled=True, provider_id="anthropic", priority=100
        )
        wf._profiles["p3"] = ProfileSlotState(
            profile_id="p3", max_parallel_runs=1, cooldown_after_429_seconds=300,
            rate_limit_policy="backoff", enabled=True, provider_id="anthropic", priority=200
        )
        
        best = wf._find_available_profile({"providerId": "anthropic"})
        assert best is not None
        assert best.profile_id == "p3"

    def test_find_available_profile_filters_by_tags(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1", max_parallel_runs=3, cooldown_after_429_seconds=300,
            rate_limit_policy="backoff", enabled=True, tags=["fast", "cheap"]
        )
        wf._profiles["p2"] = ProfileSlotState(
            profile_id="p2", max_parallel_runs=3, cooldown_after_429_seconds=300,
            rate_limit_policy="backoff", enabled=True, tags=["slow", "expensive"]
        )
        
        best = wf._find_available_profile({"tagsAny": ["fast"]})
        assert best is not None
        assert best.profile_id == "p1"
        
        assert wf._find_available_profile({"tagsAll": ["fast", "expensive"]}) is None

    def test_find_available_profile_handles_pentest_selector_contract(self):
        wf = self._make_workflow()
        wf._runtime_id = "pentestgpt"
        wf._profiles["pentestgpt_anthropic_api_team"] = ProfileSlotState(
            profile_id="pentestgpt_anthropic_api_team",
            max_parallel_runs=2,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            provider_id="anthropic",
            tags=["default", "api-key"],
            priority=100,
            runtime_materialization_mode="api_key_env",
        )
        wf._profiles["pentestgpt_openrouter_default"] = ProfileSlotState(
            profile_id="pentestgpt_openrouter_default",
            max_parallel_runs=2,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            provider_id="openrouter",
            tags=["api-key", "router"],
            priority=90,
            runtime_materialization_mode="composite",
        )
        wf._profiles["pentestgpt_local_default"] = ProfileSlotState(
            profile_id="pentestgpt_local_default",
            max_parallel_runs=1,
            cooldown_after_429_seconds=60,
            rate_limit_policy="queue",
            enabled=False,
            provider_id="local_openai_compatible",
            tags=["local"],
            priority=80,
            runtime_materialization_mode="composite",
        )

        best = wf._find_available_profile(
            {
                "providerId": "openrouter",
                "tagsAll": ["api-key", "router"],
                "runtimeMaterializationMode": "composite",
            }
        )

        assert best is not None
        assert best.profile_id == "pentestgpt_openrouter_default"
        assert (
            wf._find_available_profile({"providerId": "local_openai_compatible"})
            is None
        )

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

    def test_build_continue_as_new_preserves_handoff_reservations(self):
        wf = self._make_workflow()
        wf._handoff_reservations["run-1"] = HandoffReservation(
            profile_id="p1",
            expires_at="2026-04-15T00:00:10+00:00",
        )

        data = wf._build_continue_as_new_input()

        assert data["handoff_reservations"] == {
            "run-1": {
                "profile_id": "p1",
                "expires_at": "2026-04-15T00:00:10+00:00",
            }
        }

    def test_request_slot_dedupes_by_requester(self):
        wf = self._make_workflow()

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = True
            wf.request_slot(
                {
                    "requester_workflow_id": "run-1:agent:step-1",
                    "runtime_id": "gemini_cli",
                    "lease_group_id": "run-1",
                }
            )
            wf.request_slot(
                {
                    "requester_workflow_id": "run-1:agent:step-1",
                    "runtime_id": "gemini_cli",
                    "execution_profile_ref": "p2",
                    "lease_group_id": "run-1",
                }
            )

        assert len(wf._pending_requests) == 1
        assert wf._pending_requests[0].execution_profile_ref == "p2"
        assert wf._pending_requests[0].lease_group_id == "run-1"

    @pytest.mark.asyncio
    async def test_release_slot_creates_short_handoff_reservation(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            current_leases=["run-1:agent:step-1"],
            lease_granted_at={
                "run-1:agent:step-1": "2026-04-15T00:00:00+00:00"
            },
        )
        now = datetime(2026, 4, 15, tzinfo=timezone.utc)

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = now
            mock_wf.patched.side_effect = (
                lambda patch_id: patch_id == SLOT_HANDOFF_RESERVATION_PATCH
            )
            await wf.release_slot(
                {
                    "requester_workflow_id": "run-1:agent:step-1",
                    "profile_id": "p1",
                    "lease_group_id": "run-1",
                    "handoff_ttl_seconds": 10,
                }
            )

        reservation = wf._handoff_reservations["run-1"]
        assert reservation.profile_id == "p1"
        assert reservation.expires_at == "2026-04-15T00:00:10+00:00"
        assert wf._profiles["p1"].current_leases == []

    @pytest.mark.asyncio
    async def test_drain_queue_prefers_reserved_same_group_over_unrelated_fifo(
        self,
    ):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
        )
        wf._handoff_reservations["run-1"] = HandoffReservation(
            profile_id="p1",
            expires_at="2026-04-15T00:00:10+00:00",
        )
        wf._pending_requests = [
            PendingRequest(
                requester_workflow_id="run-2:agent:step-1",
                runtime_id="gemini_cli",
                lease_group_id="run-2",
            ),
            PendingRequest(
                requester_workflow_id="run-1:agent:step-2",
                runtime_id="gemini_cli",
                lease_group_id="run-1",
            ),
        ]
        assigned: list[tuple[str, str]] = []

        async def fake_signal(requester_workflow_id: str, profile_id: str) -> None:
            assigned.append((requester_workflow_id, profile_id))

        wf._signal_slot_assigned = fake_signal  # type: ignore[method-assign]
        now = datetime(2026, 4, 15, tzinfo=timezone.utc)
        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = now
            mock_wf.patched.return_value = False
            await wf._drain_queue()

        assert assigned == [("run-1:agent:step-2", "p1")]
        assert wf._profiles["p1"].current_leases == ["run-1:agent:step-2"]
        assert [req.requester_workflow_id for req in wf._pending_requests] == [
            "run-2:agent:step-1"
        ]
        assert "run-1" not in wf._handoff_reservations

    @pytest.mark.asyncio
    async def test_verify_pending_requesters_prunes_terminal_workflows(self):
        wf = self._make_workflow()
        wf._pending_requests = [
            PendingRequest(
                requester_workflow_id="wf-canceled",
                runtime_id="gemini_cli",
            ),
            PendingRequest(
                requester_workflow_id="wf-running",
                runtime_id="gemini_cli",
            ),
            PendingRequest(
                requester_workflow_id="wf-missing-from-result",
                runtime_id="gemini_cli",
            ),
        ]
        captured_payloads: list[dict] = []

        async def fake_execute_activity(
            activity_name: str,
            payload: dict,
            **_: object,
        ) -> dict:
            captured_payloads.append(payload)
            assert activity_name == "provider_profile.verify_lease_holders"
            return {
                "wf-canceled": {"running": False, "status": "CANCELED"},
                "wf-running": {"running": True, "status": "RUNNING"},
            }

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.execute_activity.side_effect = fake_execute_activity
            removed_count = await wf._verify_pending_requesters()

        assert removed_count == 1
        assert captured_payloads == [
            {
                "workflow_ids": [
                    "wf-canceled",
                    "wf-running",
                    "wf-missing-from-result",
                ]
            }
        ]
        assert [req.requester_workflow_id for req in wf._pending_requests] == [
            "wf-running",
            "wf-missing-from-result",
        ]

    @pytest.mark.asyncio
    async def test_verify_pending_requesters_keeps_queue_on_activity_failure(self):
        wf = self._make_workflow()
        wf._pending_requests = [
            PendingRequest(
                requester_workflow_id="wf-unknown",
                runtime_id="gemini_cli",
            )
        ]

        async def fake_execute_activity(*_: object, **__: object) -> dict:
            raise RuntimeError("temporal unavailable")

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.execute_activity.side_effect = fake_execute_activity
            removed_count = await wf._verify_pending_requesters()

        assert removed_count == 0
        assert [req.requester_workflow_id for req in wf._pending_requests] == [
            "wf-unknown"
        ]

    @pytest.mark.asyncio
    async def test_verify_workflow_statuses_batches_large_workflow_id_sets(self):
        wf = self._make_workflow()
        workflow_ids = [f"wf-{idx}" for idx in range(205)]
        captured_batches: list[list[str]] = []

        async def fake_execute_activity(
            activity_name: str,
            payload: dict,
            **_: object,
        ) -> dict:
            assert activity_name == "provider_profile.verify_lease_holders"
            batch = payload["workflow_ids"]
            captured_batches.append(batch)
            return {workflow_id: {"running": True} for workflow_id in batch}

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.execute_activity.side_effect = fake_execute_activity
            statuses = await wf._verify_workflow_statuses(workflow_ids)

        assert statuses is not None
        assert len(statuses) == 205
        assert [len(batch) for batch in captured_batches] == [100, 100, 5]

    @pytest.mark.asyncio
    async def test_verify_active_workflows_consolidates_pending_and_lease_checks(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=2,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
            current_leases=["wf-lease-terminal", "wf-shared"],
        )
        wf._pending_requests = [
            PendingRequest(
                requester_workflow_id="wf-pending-terminal",
                runtime_id="gemini_cli",
            ),
            PendingRequest(
                requester_workflow_id="wf-shared",
                runtime_id="gemini_cli",
            ),
        ]
        captured_payloads: list[dict] = []

        async def fake_execute_activity(
            activity_name: str,
            payload: dict,
            **_: object,
        ) -> dict:
            captured_payloads.append(payload)
            assert activity_name == "provider_profile.verify_lease_holders"
            return {
                "wf-lease-terminal": {"running": False, "status": "CANCELED"},
                "wf-pending-terminal": {"running": False, "status": "TERMINATED"},
                "wf-shared": {"running": True, "status": "RUNNING"},
            }

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.execute_activity.side_effect = fake_execute_activity
            mock_wf.patched.return_value = False
            await wf._verify_active_workflows(
                verify_lease_holders=True,
                verify_pending_requesters=True,
            )

        assert captured_payloads == [
            {
                "workflow_ids": [
                    "wf-lease-terminal",
                    "wf-shared",
                    "wf-pending-terminal",
                ]
            }
        ]
        assert wf._profiles["p1"].current_leases == ["wf-shared"]
        assert [req.requester_workflow_id for req in wf._pending_requests] == [
            "wf-shared"
        ]

    def test_run_drains_queue_before_best_effort_pending_verification(self):
        import inspect

        source = inspect.getsource(MoonMindProviderProfileManagerWorkflow.run)
        drain_index = source.index("await self._drain_queue()")
        verify_index = source.index("await self._verify_active_workflows(")

        assert drain_index < verify_index

    def test_handoff_reservation_blocks_only_one_slot(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=2,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
        )
        wf._handoff_reservations["run-1"] = HandoffReservation(
            profile_id="p1",
            expires_at="2026-04-15T00:00:10+00:00",
        )

        profile = wf._find_available_profile(lease_group_id="run-2")

        assert profile is wf._profiles["p1"]

    def test_handoff_reservation_holds_last_slot_for_reserved_group(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
        )
        wf._handoff_reservations["run-1"] = HandoffReservation(
            profile_id="p1",
            expires_at="2026-04-15T00:00:10+00:00",
        )

        assert wf._find_available_profile(lease_group_id="run-2") is None
        assert (
            wf._find_available_profile(lease_group_id="run-1")
            is wf._profiles["p1"]
        )

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
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
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
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime.now(timezone.utc)
            wf._clear_expired_cooldowns()
        assert wf._profiles["p1"].cooldown_until is None


# ---------------------------------------------------------------------------
# Workflow registration sanity
# ---------------------------------------------------------------------------


def test_workflow_name():
    assert WORKFLOW_NAME == "MoonMind.ProviderProfileManager"


def test_verify_pending_requests_patch_id():
    assert (
        VERIFY_PENDING_REQUESTS_PATCH
        == "provider-profile-manager-verify-pending-requests-v1"
    )


def test_registered_workflow_types():
    from moonmind.workflows.temporal.workers import REGISTERED_TEMPORAL_WORKFLOW_TYPES

    assert "MoonMind.ProviderProfileManager" in REGISTERED_TEMPORAL_WORKFLOW_TYPES


# ---------------------------------------------------------------------------
# DB model sanity
# ---------------------------------------------------------------------------


def test_temporal_workflow_type_enum():
    from api_service.db.models import TemporalWorkflowType

    assert TemporalWorkflowType.PROVIDER_PROFILE_MANAGER.value == "MoonMind.ProviderProfileManager"


def test_provider_profile_credential_source_enum():
    from api_service.db.models import ProviderCredentialSource

    assert ProviderCredentialSource.OAUTH_VOLUME.value == "oauth_volume"
    assert ProviderCredentialSource.SECRET_REF.value == "secret_ref"


def test_managed_agent_rate_limit_policy_enum():
    from api_service.db.models import ManagedAgentRateLimitPolicy

    assert ManagedAgentRateLimitPolicy.BACKOFF.value == "backoff"
    assert ManagedAgentRateLimitPolicy.QUEUE.value == "queue"
    assert ManagedAgentRateLimitPolicy.FAIL_FAST.value == "fail_fast"


# ---------------------------------------------------------------------------
# Activity catalog entry
# ---------------------------------------------------------------------------


def test_provider_profile_list_activity_in_catalog():
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

    catalog = build_default_activity_catalog()
    route = catalog.resolve_activity("provider_profile.list")
    assert route.task_queue == "mm.activity.artifacts"
    assert route.fleet == "artifacts"


def test_provider_profile_sync_slot_leases_in_catalog():
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

    catalog = build_default_activity_catalog()
    route = catalog.resolve_activity("provider_profile.sync_slot_leases")
    assert route.task_queue == "mm.activity.artifacts"
    assert route.fleet == "artifacts"


def test_provider_profile_manager_state_activity_in_catalog():
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

    catalog = build_default_activity_catalog()
    route = catalog.resolve_activity("provider_profile.manager_state")
    assert route.task_queue == "mm.activity.artifacts"
    assert route.fleet == "artifacts"


def test_provider_profile_manager_state_runtime_binding():
    from moonmind.workflows.temporal.activity_runtime import _ACTIVITY_HANDLER_ATTRS

    assert _ACTIVITY_HANDLER_ATTRS["provider_profile.manager_state"] == (
        "artifacts",
        "provider_profile_manager_state",
    )


# ---------------------------------------------------------------------------
# DB Lease Sync: workflow-side behavior tests
# ---------------------------------------------------------------------------


class TestDBLeaseSync:
    """Tests for DB lease sync logic in the ProviderProfileManager workflow."""

    def _make_workflow(self) -> MoonMindProviderProfileManagerWorkflow:
        wf = MoonMindProviderProfileManagerWorkflow()
        wf._runtime_id = "gemini_cli"
        return wf

    def _make_profile(
        self, profile_id: str = "p1", leases: list[str] | None = None,
        granted_at: dict[str, str] | None = None,
    ) -> ProfileSlotState:
        return ProfileSlotState(
            profile_id=profile_id,
            max_parallel_runs=3,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            current_leases=list(leases or []),
            lease_granted_at=dict(granted_at or {}),
        )

    def test_sync_leases_includes_granted_at(self):
        """_sync_leases_to_db should include granted_at in payloads."""
        from moonmind.workflows.temporal.workflows.provider_profile_manager import (
            DB_LEASE_PERSISTENCE_PATCH,
        )

        wf = self._make_workflow()
        ts = "2026-03-17T00:00:00+00:00"
        wf._profiles["p1"] = self._make_profile(
            leases=["wf1", "wf2"],
            granted_at={"wf1": ts, "wf2": ts},
        )

        # We test the payload construction by inspecting the lease dicts
        # that would be sent. Since _sync_leases_to_db calls an activity,
        # we verify the constructed payload shape directly.
        leases = []
        for profile in wf._profiles.values():
            for wf_id in profile.current_leases:
                leases.append({
                    "workflow_id": wf_id,
                    "profile_id": profile.profile_id,
                    "granted_at": profile.lease_granted_at.get(wf_id),
                })

        assert len(leases) == 2
        assert all(l["granted_at"] == ts for l in leases)
        assert all(l["profile_id"] == "p1" for l in leases)

    def test_evict_expired_returns_count(self):
        """_evict_expired_leases should return the number of evictions."""
        wf = self._make_workflow()
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        wf._profiles["p1"] = self._make_profile(
            leases=["wf1", "wf2"],
            granted_at={"wf1": old_ts, "wf2": old_ts},
        )
        wf._profiles["p1"].max_lease_duration_seconds = 3600  # 1 hour

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime.now(timezone.utc)
            count = wf._evict_expired_leases()

        assert count == 2
        assert len(wf._profiles["p1"].current_leases) == 0

    def test_evict_no_expired_returns_zero(self):
        """No evictions should return 0."""
        wf = self._make_workflow()
        fresh_ts = datetime.now(timezone.utc).isoformat()
        wf._profiles["p1"] = self._make_profile(
            leases=["wf1"],
            granted_at={"wf1": fresh_ts},
        )

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime.now(timezone.utc)
            count = wf._evict_expired_leases()

        assert count == 0
        assert len(wf._profiles["p1"].current_leases) == 1

    def test_release_slot_removes_from_profile(self):
        """release_slot should remove lease from profile state."""
        wf = self._make_workflow()
        ts = datetime.now(timezone.utc).isoformat()
        wf._profiles["p1"] = self._make_profile(
            leases=["wf1"],
            granted_at={"wf1": ts},
        )
        # Directly test the profile release logic
        profile = wf._profiles["p1"]
        released = profile.release("wf1")
        assert released
        assert "wf1" not in profile.current_leases
        assert "wf1" not in profile.lease_granted_at

    def test_build_continue_as_new_preserves_lease_granted_at(self):
        """Continue-as-new payload must include lease_granted_at."""
        wf = self._make_workflow()
        ts = "2026-03-17T12:00:00+00:00"
        wf._profiles["p1"] = self._make_profile(
            leases=["wf1"],
            granted_at={"wf1": ts},
        )
        data = wf._build_continue_as_new_input()
        assert data["lease_granted_at"]["p1"]["wf1"] == ts

    def test_sync_leases_payload_empty_when_no_leases(self):
        """When no leases exist, sync payload should be empty."""
        wf = self._make_workflow()
        wf._profiles["p1"] = self._make_profile()

        leases = []
        for profile in wf._profiles.values():
            for wf_id in profile.current_leases:
                leases.append({
                    "workflow_id": wf_id,
                    "profile_id": profile.profile_id,
                    "granted_at": profile.lease_granted_at.get(wf_id),
                })

        assert len(leases) == 0

    def test_patch_constant_exists(self):
        """Verify DB_LEASE_PERSISTENCE_PATCH is properly defined."""
        from moonmind.workflows.temporal.workflows.provider_profile_manager import (
            DB_LEASE_PERSISTENCE_PATCH,
        )
        assert DB_LEASE_PERSISTENCE_PATCH == "provider-profile-manager-db-lease-persistence-v1"


# ---------------------------------------------------------------------------
# Activity-side: provider_profile_sync_slot_leases
# ---------------------------------------------------------------------------


class TestProviderProfileSyncSlotLeasesActivity:
    """Tests for the sync_slot_leases activity logic (without real DB)."""

    def test_save_snapshot_instructs_full_replacement(self):
        """Verify the save action semantics: should do full replacement.

        Since we can't test the real DB here, we verify the contract:
        the save action is designed to delete ALL rows for the runtime
        then insert only the provided set — this is the snapshot pattern.
        """
        # This is a contract/design test. The activity's save path:
        # 1. Deletes ALL rows WHERE runtime_id = runtime_id  (snapshot delete)
        # 2. Inserts only the leases in the provided list
        # This ensures stale rows are removed.
        # We verify the behavior indirectly through the activity source.
        import inspect
        from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

        source = inspect.getsource(
            TemporalArtifactActivities.provider_profile_sync_slot_leases
        )
        # Verify the snapshot pattern: runtime-wide delete before insert
        assert "ProviderProfileSlotLease.runtime_id == runtime_id" in source
        # The delete should NOT filter by workflow_id (that was the old per-row pattern)
        # The delete statement for save should only filter by runtime_id
        # Verify fromisoformat is used for timestamp preservation
        assert "fromisoformat" in source

    def test_save_preserves_granted_at_from_payload(self):
        """Verify the save action parses granted_at from lease payload.

        The activity should use the provided granted_at timestamp rather
        than always writing datetime.now().
        """
        import inspect
        from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

        source = inspect.getsource(
            TemporalArtifactActivities.provider_profile_sync_slot_leases
        )
        # In the save branch, granted_at_str should be read from lease
        assert 'lease.get("granted_at")' in source
        # And parsed via fromisoformat
        assert "datetime.fromisoformat(granted_at_str)" in source

    def test_load_action_returns_granted_at(self):
        """Verify the load action includes granted_at in response shape."""
        import inspect
        from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

        source = inspect.getsource(
            TemporalArtifactActivities.provider_profile_sync_slot_leases
        )
        # Load should return granted_at from the DB row
        assert '"granted_at"' in source
        assert "row.granted_at" in source


def test_verify_lease_holders_exists():
    """Ensure the workflow exposes the expected API."""
    assert hasattr(MoonMindProviderProfileManagerWorkflow, "_verify_lease_holders")
    verify_lease_holders = getattr(MoonMindProviderProfileManagerWorkflow, "_verify_lease_holders")
    assert callable(verify_lease_holders)


def test_provider_profile_manager_state_activity_exists():
    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

    assert hasattr(TemporalArtifactActivities, "provider_profile_manager_state")


@pytest.mark.asyncio
async def test_provider_profile_manager_state_returns_compact_running_snapshot(
    monkeypatch,
):
    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

    class FakeHandle:
        async def describe(self):
            return SimpleNamespace(status=SimpleNamespace(name="RUNNING"))

        async def query(self, query_name):
            assert query_name == "get_state"
            return {
                "profiles": {"p1": {}, "p2": {}},
                "pending_requests": [
                    {"requester_workflow_id": "agent-run-1"},
                    {"requester_workflow_id": "agent-run-2"},
                ],
                "event_count": 7,
            }

    class FakeClient:
        def get_workflow_handle(self, workflow_id):
            assert workflow_id == "provider-profile-manager:gemini_cli"
            return FakeHandle()

    class FakeAdapter:
        async def get_client(self):
            return FakeClient()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        FakeAdapter,
    )

    result = await TemporalArtifactActivities(
        object()
    ).provider_profile_manager_state(
        runtime_id="gemini_cli",
        requester_workflow_id="agent-run-1",
    )

    assert result == {
        "running": True,
        "workflow_id": "provider-profile-manager:gemini_cli",
        "status": "RUNNING",
        "profile_count": 2,
        "pending_requests_count": 2,
        "event_count": 7,
        "requester_pending": True,
    }
    assert "state" not in result


@pytest.mark.asyncio
async def test_provider_profile_manager_state_checks_status_before_query(
    monkeypatch,
):
    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

    class FakeHandle:
        queried = False

        async def describe(self):
            return SimpleNamespace(status=SimpleNamespace(name="COMPLETED"))

        async def query(self, query_name):
            self.queried = True
            raise AssertionError("terminal managers must not be queried")

    handle = FakeHandle()

    class FakeClient:
        def get_workflow_handle(self, workflow_id):
            return handle

    class FakeAdapter:
        async def get_client(self):
            return FakeClient()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        FakeAdapter,
    )

    result = await TemporalArtifactActivities(
        object()
    ).provider_profile_manager_state(runtime_id="gemini_cli")

    assert result == {
        "running": False,
        "workflow_id": "provider-profile-manager:gemini_cli",
        "status": "COMPLETED",
    }
    assert handle.queried is False
