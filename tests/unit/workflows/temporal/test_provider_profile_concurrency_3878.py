"""Concurrency conformance for MoonMind #3878.

Covers the bounded purpose-aware slice: configured capacity N admits at most
N shared execution leases for N in (1, 2, 4, 8, 16) without capacity-specific
branches, credentialless validation is single-flight shared refresh on any N,
and exclusive maintenance drains consumers and blocks new admission.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
)


def _make_workflow(runtime_id: str = "opencode") -> MoonMindProviderProfileManagerWorkflow:
    wf = MoonMindProviderProfileManagerWorkflow()
    wf._runtime_id = runtime_id
    return wf


def _make_profile(
    profile_id: str,
    capacity: int,
    *,
    credential_source: str | None = "none",
    enabled: bool = True,
) -> ProfileSlotState:
    return ProfileSlotState(
        profile_id=profile_id,
        max_parallel_runs=capacity,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=enabled,
        launch_ready=True,
        credential_source=credential_source,
    )


@pytest.mark.parametrize("capacity", [1, 2, 4, 8, 16])
def test_configured_capacity_admits_at_most_n_execution_leases(capacity: int):
    """MoonMind #3878 invariant 1 for every supported ceiling (no N branches)."""

    state = _make_profile("zen", capacity)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(capacity):
        assert state.reserve(f"run-{index}", now) is True
    assert state.available_slots == 0
    assert state.is_available() is False
    assert state.reserve("run-overflow", now) is False


def test_credentialless_profile_is_detected():
    wf = _make_workflow()
    assert wf._is_credentialless_profile(_make_profile("zen", 4)) is True
    assert (
        wf._is_credentialless_profile(
            _make_profile("oauth", 1, credential_source="oauth_volume")
        )
        is False
    )


@pytest.mark.asyncio
async def test_credentialless_validation_grants_on_capacity_n_with_active_executions():
    """MoonMind #3878 invariant 3: validation does not require capacity one."""

    wf = _make_workflow()
    profile = _make_profile("zen", 4)
    now = datetime(2026, 2, 1, tzinfo=timezone.utc)
    profile.reserve("run-0", now, purpose="execution_omnigent")
    profile.reserve("run-1", now, purpose="execution_omnigent")
    wf._profiles["zen"] = profile
    wf._sync_leases_to_db = None  # type: ignore[assignment]

    async def _no_persist() -> bool:
        return True

    wf._sync_leases_to_db = _no_persist  # type: ignore[method-assign]
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as mock_wf:
        mock_wf.now.return_value = now
        mock_wf.patched.return_value = False
        acquired = await wf.acquire_credential_maintenance_lease(
            {
                "requester_workflow_id": "revalidation-1",
                "runtime_id": "opencode",
                "execution_profile_ref": "zen",
                "purpose": "credential_validation",
            }
        )
    assert acquired["profile_id"] == "zen"
    assert "revalidation-1" in profile.current_leases
    # Executions keep running alongside shared validation refresh.
    assert "run-0" in profile.current_leases
    assert "run-1" in profile.current_leases


def test_credentialless_validation_is_single_flight():
    wf = _make_workflow()
    profile = _make_profile("zen", 4)
    now = datetime(2026, 2, 1, tzinfo=timezone.utc)
    profile.reserve("revalidation-1", now, purpose="credential_validation")
    assert wf._profile_validation_lease_holders(profile) == ["revalidation-1"]
    # Shared validation never blocks execution admission by itself.
    assert wf._profile_has_exclusive_maintenance_lease(profile) is False


def test_exclusive_maintenance_blocks_new_execution_admission():
    wf = _make_workflow()
    profile = _make_profile(
        "oauth", 1, credential_source="oauth_volume", enabled=False
    )
    profile.runtime_materialization_mode = "oauth_home"
    now = datetime(2026, 2, 1, tzinfo=timezone.utc)
    profile.reserve("oauth-session-1", now, purpose="oauth_reconnect")
    wf._profiles["oauth"] = profile
    assert wf._profile_has_exclusive_maintenance_lease(profile) is True
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as mock_wf:
        mock_wf.patched.return_value = True
        assert (
            wf._find_available_profile(execution_profile_ref="oauth") is None
        )


def test_execution_leases_do_not_block_each_other_below_ceiling():
    wf = _make_workflow()
    profile = _make_profile("zen", 4)
    wf._profiles["zen"] = profile
    now = datetime(2026, 2, 1, tzinfo=timezone.utc)
    profile.reserve("run-0", now, purpose="execution_omnigent")
    assert wf._profile_has_exclusive_maintenance_lease(profile) is False
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as mock_wf:
        mock_wf.patched.return_value = True
        assert wf._find_available_profile(execution_profile_ref="zen") is profile
