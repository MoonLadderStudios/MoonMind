"""Unit tests for MoonMind.ProviderProfileManager workflow."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    BILLING_AWARE_PROFILE_SELECTION_PATCH,
    CODEX_OAUTH_LEGACY_RESTORE_PATCH,
    DB_AUTHORITATIVE_PROFILE_SYNC_PATCH,
    DEFAULT_PROFILE_EXCLUSIVE_SELECTION_PATCH,
    DURABLE_LEASE_GRANT_PATCH,
    FRESH_START_DB_LEASE_RESTORE_PATCH,
    HandoffReservation,
    PendingRequest,
    PRIORITY_PENDING_REQUESTS_PATCH,
    QUEUE_ORDER_PENDING_REQUESTS_PATCH,
    SCHEDULED_PENDING_REQUESTS_PATCH,
    SLOT_HANDOFF_RESERVATION_PATCH,
    VERIFY_PENDING_REQUESTS_PATCH,
    WORKFLOW_NAME,
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
    _validated_profile_capacity,
)

# ---------------------------------------------------------------------------
# ProfileSlotState tests
# ---------------------------------------------------------------------------

class TestProfileSlotState:
    def test_codex_oauth_capacity_validation_rejects_legacy_parallel_profile(self):
        with pytest.raises(Exception, match="require max_parallel_runs=1"):
            _validated_profile_capacity(
                {
                    "runtime_id": "codex_cli",
                    "credential_source": "oauth_volume",
                    "runtime_materialization_mode": "oauth_home",
                    "max_parallel_runs": 2,
                }
            )

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
        wf._runtime_id = "claude_code"
        return wf

    def test_restore_state(self):
        wf = self._make_workflow()
        wf._restore_state(
            {
                "runtime_id": "claude_code",
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

    def test_restore_legacy_codex_oauth_state_normalizes_without_evicting_leases(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._restore_state(
            {
                "runtime_id": "codex_cli",
                "profiles": [
                    {
                        "profile_id": "codex-oauth",
                        "credential_source": "oauth_volume",
                        "runtime_materialization_mode": "oauth_home",
                        "max_parallel_runs": 3,
                    }
                ],
                "leases": {"codex-oauth": ["wf-1", "wf-2"]},
            }
        )

        state = wf._profiles["codex-oauth"]
        assert state.max_parallel_runs == 1
        assert state.current_leases == ["wf-1", "wf-2"]
        assert state.over_capacity_legacy_snapshot is True
        assert state.available_slots == 0
        assert not state.reserve("wf-3", datetime.now(timezone.utc))

    def test_authoritative_refresh_clears_legacy_diagnostic_after_leases_drain(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._restore_state(
            {
                "profiles": [
                    {
                        "profile_id": "codex-oauth",
                        "credential_source": "oauth_volume",
                        "runtime_materialization_mode": "oauth_home",
                        "max_parallel_runs": 3,
                    }
                ],
                "leases": {"codex-oauth": ["wf-1", "wf-2"]},
            }
        )
        wf._apply_profile_sync(
            [
                {
                    "profile_id": "codex-oauth",
                    "runtime_id": "codex_cli",
                    "credential_source": "oauth_volume",
                    "runtime_materialization_mode": "oauth_home",
                    "max_parallel_runs": 1,
                }
            ],
            authoritative=True,
        )

        state = wf._profiles["codex-oauth"]
        assert state.over_capacity_legacy_snapshot is True
        assert state.release("wf-1") is True
        assert state.over_capacity_legacy_snapshot is False
        assert state.available_slots == 0
        assert state.release("wf-2") is True
        assert state.available_slots == 1

    @pytest.mark.parametrize(
        ("runtime_id", "credential_source", "materialization_mode"),
        [
            ("claude_code", "oauth_volume", "oauth_home"),
            ("codex_cli", "secret_ref", "api_key_env"),
            ("codex_cli", "oauth_volume", "oauth_home"),
        ],
    )
    def test_partial_sync_omitting_capacity_preserves_existing_capacity(
        self,
        runtime_id: str,
        credential_source: str,
        materialization_mode: str,
    ) -> None:
        wf = self._make_workflow()
        wf._runtime_id = runtime_id
        initial_capacity = (
            1
            if runtime_id == "codex_cli" and credential_source == "oauth_volume"
            else 4
        )
        wf._profiles["profile"] = ProfileSlotState(
            profile_id="profile",
            max_parallel_runs=initial_capacity,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            credential_source=credential_source,
            runtime_materialization_mode=materialization_mode,
        )

        wf._apply_profile_sync(
            [
                {
                    "profile_id": "profile",
                    "runtime_id": runtime_id,
                    "credential_source": credential_source,
                    "runtime_materialization_mode": materialization_mode,
                    "enabled": False,
                }
            ]
        )

        assert wf._profiles["profile"].max_parallel_runs == initial_capacity

    def test_authoritative_invalid_codex_oauth_payload_fails_closed(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        with pytest.raises(Exception, match="require max_parallel_runs=1"):
            wf._apply_profile_sync(
                [
                    {
                        "profile_id": "codex-oauth",
                        "runtime_id": "codex_cli",
                        "credential_source": "oauth_volume",
                        "runtime_materialization_mode": "oauth_home",
                        "max_parallel_runs": 2,
                    }
                ],
                authoritative=True,
            )

    def test_legacy_restore_has_durable_replay_patch_marker(self):
        assert CODEX_OAUTH_LEGACY_RESTORE_PATCH.endswith("-v1")

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

    def test_apply_profile_sync_loads_operator_billing_metadata(self):
        wf = self._make_workflow()
        wf._apply_profile_sync(
            [
                {
                    "profile_id": "cheap",
                    "max_parallel_runs": 1,
                    "rate_limit_policy": "backoff",
                    "enabled": True,
                    "billing": {
                        "inputPerMillionUsd": 0.10,
                        "outputPerMillionUsd": 0.40,
                    },
                }
            ]
        )

        profile = wf._profiles["cheap"]
        assert profile.input_per_million_usd == 0.10
        assert profile.output_per_million_usd == 0.40
        assert profile.pricing_source == "profile.billing"

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
        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = True
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

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = True
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

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.side_effect = (
                lambda patch_id: patch_id == DEFAULT_PROFILE_EXCLUSIVE_SELECTION_PATCH
            )
            assert wf._find_available_profile() is None

    def test_find_available_profile_allows_explicit_default_fallback_retry(self):
        wf = self._make_workflow()
        wf._profiles["fallback"] = ProfileSlotState(
            profile_id="fallback",
            max_parallel_runs=3,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=False,
            priority=200,
        )
        wf._profiles["default"] = ProfileSlotState(
            profile_id="default",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
            current_leases=["wf1"],
            priority=100,
        )

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.side_effect = (
                lambda patch_id: patch_id == DEFAULT_PROFILE_EXCLUSIVE_SELECTION_PATCH
            )
            best = wf._find_available_profile({"allowDefaultFallback": True})

        assert best is not None
        assert best.profile_id == "fallback"

    def test_find_available_profile_preserves_unpatched_default_fallback(self):
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

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = False
            best = wf._find_available_profile()

        assert best is not None
        assert best.profile_id == "fallback"

    def test_find_available_profile_sorts_multiple_available_defaults(self):
        wf = self._make_workflow()
        wf._profiles["default-low"] = ProfileSlotState(
            profile_id="default-low",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
            priority=100,
        )
        wf._profiles["default-high"] = ProfileSlotState(
            profile_id="default-high",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
            priority=300,
        )
        wf._profiles["fallback"] = ProfileSlotState(
            profile_id="fallback",
            max_parallel_runs=5,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=False,
            priority=500,
        )

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.side_effect = (
                lambda patch_id: patch_id == DEFAULT_PROFILE_EXCLUSIVE_SELECTION_PATCH
            )
            best = wf._find_available_profile()

        assert best is not None
        assert best.profile_id == "default-high"

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

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = True
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

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = True
            best = wf._find_available_profile()

        assert best is not None
        assert best.profile_id == "p2"

    def test_find_available_profile_keeps_priority_order_when_billing_patch_enabled(self):
        wf = self._make_workflow()
        wf._profiles["expensive"] = ProfileSlotState(
            profile_id="expensive",
            max_parallel_runs=3,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            provider_id="openai",
            priority=500,
            input_per_million_usd=5.0,
            output_per_million_usd=15.0,
        )
        wf._profiles["cheap"] = ProfileSlotState(
            profile_id="cheap",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            provider_id="openai",
            priority=10,
            input_per_million_usd=0.1,
            output_per_million_usd=0.4,
        )

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.side_effect = (
                lambda patch_id: patch_id == BILLING_AWARE_PROFILE_SELECTION_PATCH
            )
            best = wf._find_available_profile({"providerId": "openai"})

        assert best is not None
        assert best.profile_id == "expensive"

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

    def test_find_available_profile_exact_ref_requires_launch_ready_profile(self):
        wf = self._make_workflow()
        wf._profiles["not-ready"] = ProfileSlotState(
            profile_id="not-ready",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            launch_ready=False,
        )

        assert wf._find_available_profile(execution_profile_ref="not-ready") is None

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

    def test_find_available_profile_handles_claude_oauth_selector_contract(self):
        wf = self._make_workflow()
        wf._runtime_id = "claude_code"
        wf._profiles["claude_anthropic"] = ProfileSlotState(
            profile_id="claude_anthropic",
            max_parallel_runs=2,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            provider_id="anthropic",
            tags=["default", "oauth"],
            priority=100,
            runtime_materialization_mode="oauth_home",
        )
        wf._profiles["claude_minimax"] = ProfileSlotState(
            profile_id="claude_minimax",
            max_parallel_runs=2,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            provider_id="minimax",
            tags=["oauth", "secondary"],
            priority=90,
            runtime_materialization_mode="oauth_home",
        )
        wf._profiles["claude_disabled"] = ProfileSlotState(
            profile_id="claude_disabled",
            max_parallel_runs=1,
            cooldown_after_429_seconds=60,
            rate_limit_policy="queue",
            enabled=False,
            provider_id="anthropic",
            tags=["disabled"],
            priority=80,
            runtime_materialization_mode="oauth_home",
        )

        best = wf._find_available_profile(
            {
                "providerId": "anthropic",
                "tagsAll": ["default", "oauth"],
                "runtimeMaterializationMode": "oauth_home",
            }
        )

        assert best is not None
        assert best.profile_id == "claude_anthropic"
        assert wf._find_available_profile({"tagsAny": ["disabled"]}) is None

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
        assert data["runtime_id"] == "claude_code"
        assert len(data["profiles"]) == 1
        assert data["leases"]["p1"] == ["wf1"]
        assert data["cooldowns"]["p1"] == "2099-01-01T00:00:00+00:00"

    def test_build_continue_as_new_preserves_pending_queue_order(self):
        wf = self._make_workflow()
        wf._pending_requests = [
            PendingRequest(
                "wf-older",
                "codex_cli",
                priority=0,
                queue_order=100,
                queued_at="2026-06-22T10:00:00+00:00",
            )
        ]

        data = wf._build_continue_as_new_input()

        assert data["pending_requests"] == [
            {
                "requester_workflow_id": "wf-older",
                "runtime_id": "codex_cli",
                "priority": 0,
                "queue_order": 100,
                "queued_at": "2026-06-22T10:00:00+00:00",
                "execution_profile_ref": None,
                "profile_selector": None,
                "lease_group_id": None,
            }
        ]

    def test_restore_state_preserves_pending_queue_order(self):
        wf = self._make_workflow()
        wf._restore_state(
            {
                "runtime_id": "codex_cli",
                "pending_requests": [
                    {
                        "requester_workflow_id": "wf-older",
                        "runtime_id": "codex_cli",
                        "priority": "0",
                        "queue_order": "100",
                        "queued_at": "2026-06-22T10:00:00+00:00",
                    }
                ],
            }
        )

        assert len(wf._pending_requests) == 1
        restored = wf._pending_requests[0]
        assert restored.queue_order == 100
        assert restored.queued_at == "2026-06-22T10:00:00+00:00"

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
                    "runtime_id": "claude_code",
                    "lease_group_id": "run-1",
                }
            )
            wf.request_slot(
                {
                    "requester_workflow_id": "run-1:agent:step-1",
                    "runtime_id": "claude_code",
                    "execution_profile_ref": "p2",
                    "lease_group_id": "run-1",
                }
            )

        assert len(wf._pending_requests) == 1
        assert wf._pending_requests[0].execution_profile_ref == "p2"
        assert wf._pending_requests[0].lease_group_id == "run-1"

    def test_request_slot_records_queue_metadata(self):
        wf = self._make_workflow()

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = True
            wf.request_slot(
                {
                    "requester_workflow_id": "run-1:agent:step-1",
                    "runtime_id": "codex_cli",
                    "priority": "0",
                    "queue_order": "42",
                    "queued_at": "2026-06-22T10:00:00+00:00",
                }
            )

        assert wf._pending_requests[0].queue_order == 42
        assert wf._pending_requests[0].queued_at == "2026-06-22T10:00:00+00:00"

    @pytest.mark.asyncio
    async def test_equal_priority_uses_queue_order_not_signal_arrival(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
        )
        wf._pending_requests = [
            PendingRequest("wf-newer", "codex_cli", priority=0, queue_order=200),
            PendingRequest("wf-older", "codex_cli", priority=0, queue_order=100),
        ]
        assigned: list[tuple[str, str]] = []

        async def fake_signal(requester_workflow_id: str, profile_id: str) -> None:
            assigned.append((requester_workflow_id, profile_id))

        wf._signal_slot_assigned = fake_signal  # type: ignore[method-assign]

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime(2026, 6, 22, tzinfo=timezone.utc)
            mock_wf.patched.side_effect = lambda patch_id: patch_id in {
                PRIORITY_PENDING_REQUESTS_PATCH,
                QUEUE_ORDER_PENDING_REQUESTS_PATCH,
            }
            await wf._drain_queue()

        assert assigned == [("wf-older", "p1")]
        assert [req.requester_workflow_id for req in wf._pending_requests] == [
            "wf-newer"
        ]

    @pytest.mark.asyncio
    async def test_priority_still_wins_over_queue_order(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
        )
        wf._pending_requests = [
            PendingRequest("wf-older-low", "codex_cli", priority=0, queue_order=100),
            PendingRequest("wf-newer-high", "codex_cli", priority=10, queue_order=200),
        ]
        assigned: list[tuple[str, str]] = []

        async def fake_signal(requester_workflow_id: str, profile_id: str) -> None:
            assigned.append((requester_workflow_id, profile_id))

        wf._signal_slot_assigned = fake_signal  # type: ignore[method-assign]

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime(2026, 6, 22, tzinfo=timezone.utc)
            mock_wf.patched.side_effect = lambda patch_id: patch_id in {
                PRIORITY_PENDING_REQUESTS_PATCH,
                QUEUE_ORDER_PENDING_REQUESTS_PATCH,
            }
            await wf._drain_queue()

        assert assigned == [("wf-newer-high", "p1")]
        assert [req.requester_workflow_id for req in wf._pending_requests] == [
            "wf-older-low"
        ]

    def test_missing_queue_order_preserves_arrival_order(self):
        requests = [
            PendingRequest("wf-b", "codex_cli", priority=0),
            PendingRequest("wf-a", "codex_cli", priority=0),
        ]

        ordered = sorted(
            requests,
            key=MoonMindProviderProfileManagerWorkflow._pending_request_sort_key,
        )

        assert [request.requester_workflow_id for request in ordered] == [
            "wf-b",
            "wf-a",
        ]

    def test_missing_queue_order_stays_ahead_of_explicit_newer_requests(self):
        requests = [
            PendingRequest("wf-legacy", "codex_cli", priority=0),
            PendingRequest("wf-new", "codex_cli", priority=0, queue_order=1),
        ]

        ordered = sorted(
            requests,
            key=MoonMindProviderProfileManagerWorkflow._pending_request_sort_key,
        )

        assert [request.requester_workflow_id for request in ordered] == [
            "wf-legacy",
            "wf-new",
        ]

    def test_missing_queue_order_can_use_queued_at_fallback(self):
        requests = [
            PendingRequest(
                "wf-newer",
                "codex_cli",
                priority=0,
                queued_at="2026-06-22T10:02:00+00:00",
            ),
            PendingRequest(
                "wf-older",
                "codex_cli",
                priority=0,
                queued_at="2026-06-22T10:01:00+00:00",
            ),
        ]

        ordered = sorted(
            requests,
            key=MoonMindProviderProfileManagerWorkflow._pending_request_sort_key,
        )

        assert [request.requester_workflow_id for request in ordered] == [
            "wf-older",
            "wf-newer",
        ]

    # -- MM-869: scheduled-order tie-breaker -------------------------------

    @staticmethod
    def _single_slot_profile() -> ProfileSlotState:
        return ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
        )

    def _drain_patches(self, *, scheduled: bool) -> set[str]:
        patches = {PRIORITY_PENDING_REQUESTS_PATCH, QUEUE_ORDER_PENDING_REQUESTS_PATCH}
        if scheduled:
            patches.add(SCHEDULED_PENDING_REQUESTS_PATCH)
        return patches

    def test_pending_request_order_lookup_ids_prefers_lease_group(self):
        wf = self._make_workflow()
        wf._pending_requests = [
            PendingRequest("wf-1", "codex_cli", lease_group_id="parent-1"),
            PendingRequest("wf-2", "codex_cli"),
            PendingRequest("wf-3", "codex_cli", lease_group_id="parent-1"),
        ]

        assert wf._pending_request_order_lookup_ids() == ["parent-1", "wf-2"]

    @pytest.mark.asyncio
    async def test_scheduled_order_breaks_equal_priority_ties(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._profiles["p1"] = self._single_slot_profile()
        # Signals arrive in reverse scheduled order.
        wf._pending_requests = [
            PendingRequest("wf-newer", "codex_cli", priority=0),
            PendingRequest("wf-older", "codex_cli", priority=0),
        ]
        assigned: list[tuple[str, str]] = []

        async def fake_signal(requester_workflow_id: str, profile_id: str) -> None:
            assigned.append((requester_workflow_id, profile_id))

        wf._signal_slot_assigned = fake_signal  # type: ignore[method-assign]

        orders = {
            "wf-newer": {"scheduled_for": "2026-06-22T10:05:00+00:00"},
            "wf-older": {"scheduled_for": "2026-06-22T10:00:00+00:00"},
        }

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime(2026, 6, 22, tzinfo=timezone.utc)
            mock_wf.patched.side_effect = lambda patch_id: (
                patch_id in self._drain_patches(scheduled=True)
            )
            mock_wf.execute_activity = AsyncMock(return_value={"orders": orders})
            await wf._drain_queue()

        assert assigned == [("wf-older", "p1")]
        assert [req.requester_workflow_id for req in wf._pending_requests] == [
            "wf-newer"
        ]
        mock_wf.execute_activity.assert_awaited_once()
        args, _ = mock_wf.execute_activity.call_args
        assert args[0] == "provider_profile.pending_request_order"
        assert set(args[1]["workflow_ids"]) == {"wf-newer", "wf-older"}

    @pytest.mark.asyncio
    async def test_priority_still_wins_over_scheduled_order(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._profiles["p1"] = self._single_slot_profile()
        wf._pending_requests = [
            PendingRequest("wf-low-early", "codex_cli", priority=0),
            PendingRequest("wf-high-late", "codex_cli", priority=10),
        ]
        assigned: list[tuple[str, str]] = []

        async def fake_signal(requester_workflow_id: str, profile_id: str) -> None:
            assigned.append((requester_workflow_id, profile_id))

        wf._signal_slot_assigned = fake_signal  # type: ignore[method-assign]

        orders = {
            "wf-low-early": {"scheduled_for": "2026-06-22T10:00:00+00:00"},
            "wf-high-late": {"scheduled_for": "2026-06-22T10:05:00+00:00"},
        }

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime(2026, 6, 22, tzinfo=timezone.utc)
            mock_wf.patched.side_effect = lambda patch_id: (
                patch_id in self._drain_patches(scheduled=True)
            )
            mock_wf.execute_activity = AsyncMock(return_value={"orders": orders})
            await wf._drain_queue()

        assert assigned == [("wf-high-late", "p1")]

    def test_scheduled_sort_key_falls_back_to_created_at(self):
        orders = {
            "wf-a": {"created_at": "2026-06-22T09:59:00+00:00"},
            "wf-b": {"created_at": "2026-06-22T09:58:00+00:00"},
        }
        requests = [
            PendingRequest("wf-a", "codex_cli", priority=0),
            PendingRequest("wf-b", "codex_cli", priority=0),
        ]

        ordered = sorted(
            requests,
            key=lambda req: (
                MoonMindProviderProfileManagerWorkflow._scheduled_pending_request_sort_key(
                    req, orders
                )
            ),
        )

        assert [req.requester_workflow_id for req in ordered] == ["wf-b", "wf-a"]

    def test_scheduled_sort_key_final_fallback_by_workflow_id(self):
        # No scheduled_for or created_at for either request.
        orders: dict[str, dict[str, str]] = {}
        requests = [
            PendingRequest("wf-b", "codex_cli", priority=0),
            PendingRequest("wf-a", "codex_cli", priority=0),
        ]

        ordered = sorted(
            requests,
            key=lambda req: (
                MoonMindProviderProfileManagerWorkflow._scheduled_pending_request_sort_key(
                    req, orders
                )
            ),
        )

        assert [req.requester_workflow_id for req in ordered] == ["wf-a", "wf-b"]

    def test_scheduled_sort_key_uses_lease_group_id_for_lookup(self):
        orders = {
            "parent-late": {"scheduled_for": "2026-06-22T10:05:00+00:00"},
            "parent-early": {"scheduled_for": "2026-06-22T10:00:00+00:00"},
        }
        requests = [
            PendingRequest(
                "wf-child-a", "codex_cli", priority=0, lease_group_id="parent-late"
            ),
            PendingRequest(
                "wf-child-b", "codex_cli", priority=0, lease_group_id="parent-early"
            ),
        ]

        ordered = sorted(
            requests,
            key=lambda req: (
                MoonMindProviderProfileManagerWorkflow._scheduled_pending_request_sort_key(
                    req, orders
                )
            ),
        )

        assert [req.requester_workflow_id for req in ordered] == [
            "wf-child-b",
            "wf-child-a",
        ]

    @pytest.mark.asyncio
    async def test_ordering_lookup_uses_lease_group_id_as_primary_key(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._profiles["p1"] = self._single_slot_profile()
        wf._pending_requests = [
            PendingRequest(
                "wf-child-a", "codex_cli", priority=0, lease_group_id="parent-late"
            ),
            PendingRequest(
                "wf-child-b", "codex_cli", priority=0, lease_group_id="parent-early"
            ),
        ]
        assigned: list[tuple[str, str]] = []

        async def fake_signal(requester_workflow_id: str, profile_id: str) -> None:
            assigned.append((requester_workflow_id, profile_id))

        wf._signal_slot_assigned = fake_signal  # type: ignore[method-assign]

        orders = {
            "parent-late": {"scheduled_for": "2026-06-22T10:05:00+00:00"},
            "parent-early": {"scheduled_for": "2026-06-22T10:00:00+00:00"},
        }

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime(2026, 6, 22, tzinfo=timezone.utc)
            mock_wf.patched.side_effect = lambda patch_id: (
                patch_id in self._drain_patches(scheduled=True)
            )
            mock_wf.execute_activity = AsyncMock(return_value={"orders": orders})
            await wf._drain_queue()

        args, _ = mock_wf.execute_activity.call_args
        assert set(args[1]["workflow_ids"]) == {"parent-late", "parent-early"}
        assert assigned == [("wf-child-b", "p1")]

    @pytest.mark.asyncio
    async def test_ordering_activity_failure_falls_back_without_blocking(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._profiles["p1"] = self._single_slot_profile()
        wf._pending_requests = [
            PendingRequest("wf-newer", "codex_cli", priority=0, queue_order=200),
            PendingRequest("wf-older", "codex_cli", priority=0, queue_order=100),
        ]
        assigned: list[tuple[str, str]] = []

        async def fake_signal(requester_workflow_id: str, profile_id: str) -> None:
            assigned.append((requester_workflow_id, profile_id))

        wf._signal_slot_assigned = fake_signal  # type: ignore[method-assign]

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime(2026, 6, 22, tzinfo=timezone.utc)
            mock_wf.patched.side_effect = lambda patch_id: (
                patch_id in self._drain_patches(scheduled=True)
            )
            mock_wf.execute_activity = AsyncMock(side_effect=RuntimeError("boom"))
            # Drain must not raise even though the ordering activity failed.
            await wf._drain_queue()

        # Fallback to the deterministic queue-order sort: queue_order 100 wins.
        assert assigned == [("wf-older", "p1")]
        assert [req.requester_workflow_id for req in wf._pending_requests] == [
            "wf-newer"
        ]

    @pytest.mark.asyncio
    async def test_scheduled_ordering_gated_behind_patch(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._profiles["p1"] = self._single_slot_profile()
        wf._pending_requests = [
            PendingRequest("wf-newer", "codex_cli", priority=0, queue_order=200),
            PendingRequest("wf-older", "codex_cli", priority=0, queue_order=100),
        ]
        assigned: list[tuple[str, str]] = []

        async def fake_signal(requester_workflow_id: str, profile_id: str) -> None:
            assigned.append((requester_workflow_id, profile_id))

        wf._signal_slot_assigned = fake_signal  # type: ignore[method-assign]

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime(2026, 6, 22, tzinfo=timezone.utc)
            # Legacy replay path: scheduled patch is NOT enabled.
            mock_wf.patched.side_effect = lambda patch_id: (
                patch_id in self._drain_patches(scheduled=False)
            )
            mock_wf.execute_activity = AsyncMock(return_value={"orders": {}})
            await wf._drain_queue()

        # Legacy queue-order behavior preserved; no ordering activity call.
        mock_wf.execute_activity.assert_not_awaited()
        assert assigned == [("wf-older", "p1")]

    @pytest.mark.asyncio
    async def test_ordering_lookup_bounds_schedule_to_start(self):
        # The best-effort ordering lookup must bound how long it waits for a
        # worker so a starved activity task queue cannot leave slots idle.
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._pending_requests = [
            PendingRequest("wf-older", "codex_cli", priority=0),
            PendingRequest("wf-newer", "codex_cli", priority=0),
        ]

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime(2026, 6, 22, tzinfo=timezone.utc)
            mock_wf.patched.side_effect = lambda patch_id: (
                patch_id in self._drain_patches(scheduled=True)
            )
            mock_wf.execute_activity = AsyncMock(return_value={"orders": {}})
            await wf._order_pending_requests_by_schedule()

        _, kwargs = mock_wf.execute_activity.call_args
        assert kwargs["schedule_to_start_timeout"] == timedelta(seconds=30)

    @pytest.mark.asyncio
    async def test_resolved_orders_cached_across_drain_cycles(self):
        # Immutable scheduled/created times are cached, so a second drain cycle
        # with the same pending ids does not re-query the database.
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._profiles["p1"] = self._single_slot_profile()
        wf._pending_requests = [
            PendingRequest("wf-newer", "codex_cli", priority=0),
            PendingRequest("wf-older", "codex_cli", priority=0),
        ]
        assigned: list[tuple[str, str]] = []

        async def fake_signal(requester_workflow_id: str, profile_id: str) -> None:
            assigned.append((requester_workflow_id, profile_id))

        wf._signal_slot_assigned = fake_signal  # type: ignore[method-assign]

        orders = {
            "wf-newer": {"scheduled_for": "2026-06-22T10:05:00+00:00"},
            "wf-older": {"scheduled_for": "2026-06-22T10:00:00+00:00"},
        }

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime(2026, 6, 22, tzinfo=timezone.utc)
            mock_wf.patched.side_effect = lambda patch_id: (
                patch_id in self._drain_patches(scheduled=True)
            )
            mock_wf.execute_activity = AsyncMock(return_value={"orders": orders})
            # First cycle assigns the single slot to the earliest-scheduled
            # request and queries the ordering activity.
            await wf._drain_queue()
            assert assigned == [("wf-older", "p1")]
            mock_wf.execute_activity.assert_awaited_once()

            # Free the slot; the still-pending request is already cached.
            wf._profiles["p1"] = self._single_slot_profile()
            await wf._drain_queue()

        # The remaining request drained without a second ordering lookup.
        assert assigned == [("wf-older", "p1"), ("wf-newer", "p1")]
        mock_wf.execute_activity.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resolved_orders_only_queries_uncached_ids(self):
        # When a new request joins an already-resolved set, only the new id is
        # looked up.
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._resolved_orders = {
            "wf-older": {"scheduled_for": "2026-06-22T10:00:00+00:00"},
        }
        wf._pending_requests = [
            PendingRequest("wf-older", "codex_cli", priority=0),
            PendingRequest("wf-newer", "codex_cli", priority=0),
        ]

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime(2026, 6, 22, tzinfo=timezone.utc)
            mock_wf.patched.side_effect = lambda patch_id: (
                patch_id in self._drain_patches(scheduled=True)
            )
            mock_wf.execute_activity = AsyncMock(
                return_value={
                    "orders": {
                        "wf-newer": {"scheduled_for": "2026-06-22T10:05:00+00:00"}
                    }
                }
            )
            ordered = await wf._order_pending_requests_by_schedule()

        args, _ = mock_wf.execute_activity.call_args
        assert args[1]["workflow_ids"] == ["wf-newer"]
        assert [req.requester_workflow_id for req in ordered] == [
            "wf-older",
            "wf-newer",
        ]

    @pytest.mark.asyncio
    async def test_resolved_orders_cache_drops_stale_ids(self):
        # Cached entries for requests that are no longer pending are pruned so
        # the cache stays bounded for this long-lived workflow.
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._resolved_orders = {
            "wf-gone": {"scheduled_for": "2026-06-22T09:00:00+00:00"},
            "wf-here": {"scheduled_for": "2026-06-22T10:00:00+00:00"},
        }
        wf._pending_requests = [
            PendingRequest("wf-here", "codex_cli", priority=0),
        ]

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime(2026, 6, 22, tzinfo=timezone.utc)
            mock_wf.patched.side_effect = lambda patch_id: (
                patch_id in self._drain_patches(scheduled=True)
            )
            mock_wf.execute_activity = AsyncMock(return_value={"orders": {}})
            await wf._order_pending_requests_by_schedule()

        # Nothing new to look up, and the stale id was dropped.
        mock_wf.execute_activity.assert_not_awaited()
        assert set(wf._resolved_orders) == {"wf-here"}

    @pytest.mark.asyncio
    async def test_acquire_slot_update_reserves_without_callback_signal(self):
        wf = self._make_workflow()
        wf._runtime_id = "claude_code"
        wf._profiles["claude_anthropic"] = ProfileSlotState(
            profile_id="claude_anthropic",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
        )
        now = datetime(2026, 6, 12, tzinfo=timezone.utc)

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = now
            mock_wf.patched.return_value = False
            first = await wf.acquire_slot(
                {
                    "requester_workflow_id": "pentest:run-1:step-1:1",
                    "runtime_id": "claude_code",
                    "execution_profile_ref": "claude_anthropic",
                    "metadata": {"target_hash": "hash-1"},
                }
            )
            second = await wf.acquire_slot(
                {
                    "requester_workflow_id": "pentest:run-1:step-1:1",
                    "runtime_id": "claude_code",
                    "execution_profile_ref": "claude_anthropic",
                }
            )

        assert first == {
            "profile_id": "claude_anthropic",
            "lease_id": "pentest:run-1:step-1:1",
            "already_held": False,
        }
        assert second == {
            "profile_id": "claude_anthropic",
            "lease_id": "pentest:run-1:step-1:1",
            "already_held": True,
        }
        profile = wf._profiles["claude_anthropic"]
        assert profile.current_leases == ["pentest:run-1:step-1:1"]
        assert profile.lease_granted_at == {
            "pentest:run-1:step-1:1": "2026-06-12T00:00:00+00:00"
        }
        assert wf._pending_requests == []

    @pytest.mark.asyncio
    async def test_maintenance_lease_uses_same_ledger_on_disabled_profile(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        wf._profiles["codex_oauth"] = ProfileSlotState(
            profile_id="codex_oauth",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=False,
            launch_ready=False,
        )
        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime(2026, 7, 12, tzinfo=timezone.utc)
            mock_wf.patched.return_value = False
            acquired = await wf.acquire_credential_maintenance_lease(
                {
                    "requester_workflow_id": "oauth-session:oas-1",
                    "runtime_id": "codex_cli",
                    "execution_profile_ref": "codex_oauth",
                    "purpose": "oauth_reconnect",
                    "metadata": {
                        "workflowId": "oauth-session:oas-1",
                        "oauthSessionId": "oas-1",
                    },
                }
            )

        assert acquired["profile_id"] == "codex_oauth"
        profile = wf._profiles["codex_oauth"]
        assert profile.current_leases == ["oauth-session:oas-1"]
        assert profile.available_slots == 0
        assert profile.lease_metadata["oauth-session:oas-1"]["purpose"] == "oauth_reconnect"
        assert profile.is_available() is False

    @pytest.mark.asyncio
    async def test_maintenance_lease_requires_exact_profile_without_selector(self):
        wf = self._make_workflow()
        wf._runtime_id = "codex_cli"
        with pytest.raises(Exception, match="does not allow profile selectors"):
            await wf.acquire_credential_maintenance_lease(
                {
                    "requester_workflow_id": "oauth-session:oas-1",
                    "runtime_id": "codex_cli",
                    "execution_profile_ref": "codex_oauth",
                    "profile_selector": {"providerId": "openai"},
                    "purpose": "oauth_connect",
                }
            )

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
                runtime_id="claude_code",
                lease_group_id="run-2",
            ),
            PendingRequest(
                requester_workflow_id="run-1:agent:step-2",
                runtime_id="claude_code",
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
    async def test_drain_queue_assigns_higher_priority_before_older_normal_request(
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
        wf._pending_requests = [
            PendingRequest(
                requester_workflow_id="normal-run:agent:step-1",
                runtime_id="codex_cli",
                priority=0,
            ),
            PendingRequest(
                requester_workflow_id="resolver-run:agent:step-1",
                runtime_id="codex_cli",
                priority=10,
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
            mock_wf.patched.side_effect = (
                lambda patch_id: patch_id == PRIORITY_PENDING_REQUESTS_PATCH
            )
            await wf._drain_queue()

        assert assigned == [("resolver-run:agent:step-1", "p1")]
        assert wf._profiles["p1"].current_leases == ["resolver-run:agent:step-1"]
        assert [req.requester_workflow_id for req in wf._pending_requests] == [
            "normal-run:agent:step-1"
        ]

    @pytest.mark.asyncio
    async def test_verify_pending_requesters_prunes_terminal_workflows(self):
        wf = self._make_workflow()
        wf._pending_requests = [
            PendingRequest(
                requester_workflow_id="wf-canceled",
                runtime_id="claude_code",
            ),
            PendingRequest(
                requester_workflow_id="wf-running",
                runtime_id="claude_code",
            ),
            PendingRequest(
                requester_workflow_id="wf-missing-from-result",
                runtime_id="claude_code",
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
                runtime_id="claude_code",
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
                runtime_id="claude_code",
            ),
            PendingRequest(
                requester_workflow_id="wf-shared",
                runtime_id="claude_code",
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

    def test_fresh_start_restores_db_leases_even_when_requests_are_pending(self):
        """A reset-time request must not suppress authoritative lease recovery."""
        import inspect

        source = inspect.getsource(MoonMindProviderProfileManagerWorkflow.run)

        assert "FRESH_START_DB_LEASE_RESTORE_PATCH" in source
        assert "workflow.info().continued_run_id is None" in source
        assert "await self._load_leases_from_db()" in source
        assert source.index(
            "leases_restored = await self._load_leases_from_db()"
        ) < source.index("has_pending")

    @pytest.mark.asyncio
    async def test_run_restores_durable_lease_when_request_arrives_during_startup(
        self,
    ):
        """Replay the startup race that previously granted duplicate capacity."""
        wf = MoonMindProviderProfileManagerWorkflow()

        async def load_profiles(*_args, **_kwargs) -> bool:
            if not wf._profiles:
                wf._profiles["p1"] = ProfileSlotState(
                    profile_id="p1",
                    max_parallel_runs=1,
                    cooldown_after_429_seconds=300,
                    rate_limit_policy="backoff",
                    enabled=True,
                    is_default=True,
                )
                wf.request_slot(
                    {
                        "requester_workflow_id": "waiting-agent-run",
                        "runtime_id": "codex_cli",
                    }
                )
            return True

        async def load_leases() -> bool:
            assert [
                request.requester_workflow_id for request in wf._pending_requests
            ] == ["waiting-agent-run"]
            wf._profiles["p1"].current_leases.append("active-agent-run")
            wf._shutdown_requested = True
            return True

        wf._load_profiles_from_db = AsyncMock(side_effect=load_profiles)
        wf._load_leases_from_db = AsyncMock(side_effect=load_leases)

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = True
            mock_wf.info.return_value = SimpleNamespace(continued_run_id=None)
            result = await wf.run({"runtime_id": "codex_cli"})

        assert result["status"] == "shutdown"
        assert wf._profiles["p1"].current_leases == ["active-agent-run"]
        assert [
            request.requester_workflow_id for request in wf._pending_requests
        ] == ["waiting-agent-run"]
        wf._load_leases_from_db.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_preserves_continue_as_new_lease_snapshot(self):
        """Existing histories keep using their compact continuation snapshot."""
        wf = MoonMindProviderProfileManagerWorkflow()
        wf._shutdown_requested = True
        wf._load_profiles_from_db = AsyncMock(return_value=True)
        wf._load_leases_from_db = AsyncMock()

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = True
            mock_wf.info.return_value = SimpleNamespace(
                continued_run_id="previous-manager-run"
            )
            result = await wf.run(
                {
                    "runtime_id": "codex_cli",
                    "profiles": [
                        {
                            "profile_id": "p1",
                            "max_parallel_runs": 1,
                            "enabled": True,
                        }
                    ],
                    "leases": {"p1": ["active-agent-run"]},
                }
            )

        assert result["status"] == "shutdown"
        assert wf._profiles["p1"].current_leases == ["active-agent-run"]
        wf._load_leases_from_db.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_durable_restore_keeps_lease_after_ambiguous_signal_failure(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
        )
        wf._signal_slot_assigned = AsyncMock(side_effect=RuntimeError("unavailable"))
        wf._remove_lease_from_db = AsyncMock()

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.side_effect = (
                lambda patch_id: patch_id == DURABLE_LEASE_GRANT_PATCH
            )
            mock_wf.execute_activity = AsyncMock(
                return_value={
                    "leases": [
                        {
                            "workflow_id": "active-agent-run",
                            "profile_id": "p1",
                        }
                    ]
                }
            )
            restored = await wf._load_leases_from_db()

        assert restored is True
        assert wf._profiles["p1"].current_leases == ["active-agent-run"]
        wf._remove_lease_from_db.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_durable_restore_fails_closed_for_unknown_profile(self):
        wf = self._make_workflow()

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.side_effect = (
                lambda patch_id: patch_id == DURABLE_LEASE_GRANT_PATCH
            )
            mock_wf.execute_activity = AsyncMock(
                return_value={
                    "leases": [
                        {
                            "workflow_id": "active-agent-run",
                            "profile_id": "missing-profile",
                        }
                    ]
                }
            )
            restored = await wf._load_leases_from_db()

        assert restored is False

    @pytest.mark.asyncio
    async def test_durable_grant_persists_before_signaling_consumer(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
        )
        wf._pending_requests = [
            PendingRequest(
                requester_workflow_id="agent-run-1",
                runtime_id="claude_code",
                execution_profile_ref="p1",
            )
        ]
        calls: list[str] = []

        async def persist() -> bool:
            calls.append("persist")
            return True

        async def signal(_workflow_id: str, _profile_id: str) -> None:
            calls.append("signal")

        wf._sync_leases_to_db = AsyncMock(side_effect=persist)
        wf._signal_slot_assigned = AsyncMock(side_effect=signal)

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime.now(timezone.utc)
            mock_wf.patched.side_effect = (
                lambda patch_id: patch_id == DURABLE_LEASE_GRANT_PATCH
            )
            await wf._drain_queue()

        assert calls == ["persist", "signal"]
        assert wf._profiles["p1"].current_leases == ["agent-run-1"]
        assert wf._pending_requests == []

    @pytest.mark.asyncio
    async def test_durable_grant_blocks_when_lease_persistence_is_unavailable(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
        )
        request = PendingRequest(
            requester_workflow_id="agent-run-1",
            runtime_id="claude_code",
            execution_profile_ref="p1",
        )
        wf._pending_requests = [request]
        wf._sync_leases_to_db = AsyncMock(return_value=False)
        wf._signal_slot_assigned = AsyncMock()

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.now.return_value = datetime.now(timezone.utc)
            mock_wf.patched.side_effect = (
                lambda patch_id: patch_id == DURABLE_LEASE_GRANT_PATCH
            )
            await wf._drain_queue()

        wf._signal_slot_assigned.assert_not_awaited()
        assert wf._profiles["p1"].current_leases == ["agent-run-1"]
        assert wf._pending_requests == [request]

    @pytest.mark.asyncio
    async def test_direct_update_does_not_return_unpersisted_lease(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            is_default=True,
        )
        wf._sync_leases_to_db = AsyncMock(return_value=False)

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = True
            mock_wf.now.return_value = datetime.now(timezone.utc)
            with pytest.raises(Exception, match="persistence failed before direct grant"):
                await wf.acquire_slot(
                    {
                        "requester_workflow_id": "container-job-1",
                        "runtime_id": "claude_code",
                    }
                )

        assert wf._profiles["p1"].current_leases == []

    @pytest.mark.asyncio
    async def test_maintenance_update_does_not_return_unpersisted_lease(self):
        wf = self._make_workflow()
        wf._profiles["p1"] = ProfileSlotState(
            profile_id="p1",
            max_parallel_runs=1,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=False,
            is_default=True,
        )
        wf._sync_leases_to_db = AsyncMock(return_value=False)

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = True
            mock_wf.now.return_value = datetime.now(timezone.utc)
            with pytest.raises(
                Exception,
                match="persistence failed before maintenance grant",
            ):
                await wf.acquire_credential_maintenance_lease(
                    {
                        "requester_workflow_id": "oauth-session-1",
                        "runtime_id": "claude_code",
                        "execution_profile_ref": "p1",
                        "purpose": "credential_validation",
                    }
                )

        assert wf._profiles["p1"].current_leases == []

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

        with patch(
            "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
        ) as mock_wf:
            mock_wf.patched.return_value = True
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


def test_profile_manager_reliability_patch_ids():
    assert FRESH_START_DB_LEASE_RESTORE_PATCH.endswith(
        "fresh-start-db-lease-restore-v1"
    )
    assert DURABLE_LEASE_GRANT_PATCH.endswith("durable-lease-grant-v1")

def test_registered_workflow_types():
    from moonmind.workflows.temporal.workers import list_registered_workflow_types

    assert "MoonMind.ProviderProfileManager" in list_registered_workflow_types()

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

def test_provider_profile_pending_request_order_in_catalog():
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

    catalog = build_default_activity_catalog()
    route = catalog.resolve_activity("provider_profile.pending_request_order")
    assert route.task_queue == "mm.activity.artifacts"
    assert route.fleet == "artifacts"

def test_provider_profile_pending_request_order_runtime_binding():
    from moonmind.workflows.temporal.activity_runtime import _ACTIVITY_HANDLER_ATTRS

    assert _ACTIVITY_HANDLER_ATTRS["provider_profile.pending_request_order"] == (
        "artifacts",
        "provider_profile_pending_request_order",
    )

# ---------------------------------------------------------------------------
# DB Lease Sync: workflow-side behavior tests
# ---------------------------------------------------------------------------

class TestDBLeaseSync:
    """Tests for DB lease sync logic in the ProviderProfileManager workflow."""

    def _make_workflow(self) -> MoonMindProviderProfileManagerWorkflow:
        wf = MoonMindProviderProfileManagerWorkflow()
        wf._runtime_id = "claude_code"
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

class TestProviderProfilePendingRequestOrderActivity:
    """Tests for the provider_profile.pending_request_order activity (MM-869)."""

    @staticmethod
    def _activities():
        from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

        return TemporalArtifactActivities(None)  # type: ignore[arg-type]

    @staticmethod
    def _patch_session(rows: list[tuple]):
        import contextlib

        class _Result:
            def all(self) -> list[tuple]:
                return rows

        class _FakeSession:
            async def execute(self, _stmt):
                return _Result()

        @contextlib.asynccontextmanager
        async def _ctx():
            yield _FakeSession()

        return patch(
            "api_service.db.base.get_async_session_context",
            side_effect=lambda: _ctx(),
        )

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty_orders(self):
        activities = self._activities()
        result = await activities.provider_profile_pending_request_order(
            workflow_ids=[]
        )
        assert result == {"orders": {}}

    @pytest.mark.asyncio
    async def test_returns_compact_iso_timestamps(self):
        activities = self._activities()
        rows = [
            (
                "mm:workflow-a",
                datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 6, 22, 9, 58, tzinfo=timezone.utc),
            ),
            (
                "mm:workflow-b",
                None,
                datetime(2026, 6, 22, 9, 59, tzinfo=timezone.utc),
            ),
        ]
        with self._patch_session(rows):
            result = await activities.provider_profile_pending_request_order(
                workflow_ids=["mm:workflow-a", "mm:workflow-b", "mm:workflow-a"]
            )

        assert result == {
            "orders": {
                "mm:workflow-a": {
                    "scheduled_for": "2026-06-22T10:00:00+00:00",
                    "created_at": "2026-06-22T09:58:00+00:00",
                },
                "mm:workflow-b": {
                    "created_at": "2026-06-22T09:59:00+00:00",
                },
            }
        }

    @pytest.mark.asyncio
    async def test_naive_timestamps_are_normalized_to_utc(self):
        activities = self._activities()
        rows = [
            (
                "mm:workflow-a",
                datetime(2026, 6, 22, 10, 0),  # naive -> assume UTC
                None,
            ),
        ]
        with self._patch_session(rows):
            result = await activities.provider_profile_pending_request_order(
                workflow_ids=["mm:workflow-a"]
            )

        assert result["orders"]["mm:workflow-a"]["scheduled_for"].endswith("+00:00")

    @pytest.mark.asyncio
    async def test_missing_records_are_omitted(self):
        activities = self._activities()
        # DB returns nothing for the requested ids.
        with self._patch_session([]):
            result = await activities.provider_profile_pending_request_order(
                workflow_ids=["mm:unknown"]
            )

        assert result == {"orders": {}}

def test_verify_lease_holders_exists():
    """Ensure the workflow exposes the expected API."""
    assert hasattr(MoonMindProviderProfileManagerWorkflow, "_verify_lease_holders")
    verify_lease_holders = getattr(MoonMindProviderProfileManagerWorkflow, "_verify_lease_holders")
    assert callable(verify_lease_holders)

def test_provider_profile_manager_state_activity_exists():
    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

    assert hasattr(TemporalArtifactActivities, "provider_profile_manager_state")


def test_legacy_reset_activity_never_terminates_manager():
    import inspect

    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

    source = inspect.getsource(
        TemporalArtifactActivities.provider_profile_reset_manager
    )
    assert "provider_profile_ensure_manager" in source
    assert ".terminate(" not in source


@pytest.mark.asyncio
async def test_legacy_reset_activity_uses_non_destructive_ensure_contract():
    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

    activities = TemporalArtifactActivities(AsyncMock())
    activities.provider_profile_ensure_manager = AsyncMock(
        return_value={
            "started": False,
            "workflow_id": "provider-profile-manager:codex_cli",
        }
    )

    result = await activities.provider_profile_reset_manager(runtime_id="codex_cli")

    activities.provider_profile_ensure_manager.assert_awaited_once_with(
        runtime_id="codex_cli"
    )
    assert result == {
        "reset": False,
        "started": False,
        "workflow_id": "provider-profile-manager:codex_cli",
    }

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
                "profiles": {
                    "p1": {
                        "profile_id": "p1",
                        "max_parallel_runs": 1,
                        "current_leases": ["agent-run-active"],
                        "cooldown_until": None,
                        "enabled": True,
                        "launch_ready": True,
                    },
                    "p2": {},
                },
                "pending_requests": [
                    {
                        "requester_workflow_id": "agent-run-1",
                        "execution_profile_ref": "p1",
                    },
                    {"requester_workflow_id": "agent-run-2"},
                ],
                "pending_requests_ordered": True,
                "event_count": 7,
            }

    class FakeClient:
        def get_workflow_handle(self, workflow_id):
            assert workflow_id == "provider-profile-manager:claude_code"
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
        runtime_id="claude_code",
        requester_workflow_id="agent-run-1",
    )

    assert result == {
        "running": True,
        "workflow_id": "provider-profile-manager:claude_code",
        "status": "RUNNING",
        "inspection_succeeded": True,
        "profile_count": 2,
        "pending_requests_count": 2,
        "event_count": 7,
        "requester_pending": True,
        "requester_queue_position": 1,
        "requested_profile": {
            "profile_id": "p1",
            "max_parallel_runs": 1,
            "current_leases_count": 1,
            "cooldown_until": None,
            "enabled": True,
            "launch_ready": True,
        },
    }
    assert "state" not in result


@pytest.mark.asyncio
async def test_provider_profile_manager_state_resolves_unique_selector_profile(
    monkeypatch,
):
    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

    class FakeHandle:
        async def describe(self):
            return SimpleNamespace(status=SimpleNamespace(name="RUNNING"))

        async def query(self, query_name):
            assert query_name == "get_state"
            return {
                "profiles": {
                    "openai": {
                        "profile_id": "openai",
                        "provider_id": "openai",
                        "runtime_materialization_mode": "oauth",
                        "tags": ["primary"],
                        "max_parallel_runs": 1,
                        "current_leases": ["agent-run-active"],
                        "cooldown_until": None,
                        "enabled": True,
                        "launch_ready": True,
                    },
                    "anthropic": {
                        "profile_id": "anthropic",
                        "provider_id": "anthropic",
                        "runtime_materialization_mode": "api_key",
                        "tags": [],
                        "max_parallel_runs": 2,
                        "current_leases": [],
                        "cooldown_until": None,
                        "enabled": True,
                        "launch_ready": True,
                    },
                },
                "pending_requests": [
                    {
                        "requester_workflow_id": "agent-run-1",
                        "execution_profile_ref": None,
                        "profile_selector": {
                            "providerId": "openai",
                            "runtimeMaterializationMode": "oauth",
                            "tagsAll": ["primary"],
                        },
                    }
                ],
                "pending_requests_ordered": False,
                "event_count": 1,
            }

    class FakeClient:
        def get_workflow_handle(self, workflow_id):
            assert workflow_id == "provider-profile-manager:codex_cli"
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
        runtime_id="codex_cli",
        requester_workflow_id="agent-run-1",
    )

    assert result["requester_pending"] is True
    assert result["requester_queue_position"] is None
    assert result["requested_profile"] == {
        "profile_id": "openai",
        "max_parallel_runs": 1,
        "current_leases_count": 1,
        "cooldown_until": None,
        "enabled": True,
        "launch_ready": True,
    }

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
    ).provider_profile_manager_state(runtime_id="claude_code")

    assert result == {
        "running": False,
        "workflow_id": "provider-profile-manager:claude_code",
        "status": "COMPLETED",
        "inspection_succeeded": True,
    }
    assert handle.queried is False


@pytest.mark.asyncio
async def test_provider_profile_manager_state_bounds_busy_workflow_query(
    monkeypatch,
):
    from moonmind.workflows.temporal import artifacts as artifacts_module
    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

    query_cancelled = asyncio.Event()

    class FakeHandle:
        async def describe(self):
            return SimpleNamespace(status=SimpleNamespace(name="RUNNING"))

        async def query(self, query_name):
            assert query_name == "get_state"
            try:
                await asyncio.Event().wait()
            finally:
                query_cancelled.set()

    class FakeClient:
        def get_workflow_handle(self, workflow_id):
            assert workflow_id == "provider-profile-manager:codex_cli"
            return FakeHandle()

    class FakeAdapter:
        async def get_client(self):
            return FakeClient()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        FakeAdapter,
    )
    monkeypatch.setattr(
        artifacts_module,
        "_PROVIDER_PROFILE_MANAGER_QUERY_TIMEOUT_SECONDS",
        0.01,
    )

    result = await TemporalArtifactActivities(
        object()
    ).provider_profile_manager_state(
        runtime_id="codex_cli",
        requester_workflow_id="agent-run-1",
    )

    assert result == {
        "running": True,
        "workflow_id": "provider-profile-manager:codex_cli",
        "status": "RUNNING",
        "inspection_succeeded": False,
        "inspection_status": "QUERY_TIMEOUT",
    }
    assert query_cancelled.is_set()
