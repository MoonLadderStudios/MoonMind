"""Executable non-goal guardrails for MoonLadderStudios/MoonMind#3801."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from api_service.db.models import ProviderProfileSlotLease
from moonmind.provider_profiles.model_tiers import ProviderModelEffortTier
from moonmind.workflows.executions.model_resolver import resolve_model_effort
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
    SlotAcquirePayload,
    SlotRequestPayload,
)


@pytest.mark.parametrize(
    ("runtime_id", "provider_effort"),
    [
        ("codex_cli", "openai-adaptive-reasoning-v9"),
        ("claude_code", "anthropic-thinking-budget-32k"),
    ],
)
def test_tier_effort_is_an_opaque_runtime_specific_string(
    runtime_id: str,
    provider_effort: str,
) -> None:
    tier = ProviderModelEffortTier.model_validate(
        {
            "label": "Provider native",
            "model": f"{runtime_id}-model",
            "effort": provider_effort,
        }
    ).model_dump(mode="json")

    resolved = resolve_model_effort(
        runtime_id=runtime_id,
        profile={
            "profile_id": f"{runtime_id}-profile",
            "enabled": True,
            "auth_state": "connected",
            "model_tiers": [tier],
            "default_model_tier": 1,
        },
        requested_model_tier=1,
        env={},
    )

    assert tier["effort"] == provider_effort
    assert resolved.effort == provider_effort
    assert resolved.effort_source == "requested_tier"
    assert resolved.as_metadata()["resolvedEffort"] == provider_effort


def test_slot_lease_contract_and_persistence_have_no_tier_capacity_scope() -> None:
    request_fields = set(SlotRequestPayload.__annotations__)
    acquire_fields = set(SlotAcquirePayload.__annotations__)
    persisted_fields = {
        column.name for column in ProviderProfileSlotLease.__table__.columns
    }

    assert not {field for field in request_fields if "tier" in field.lower()}
    assert not {field for field in acquire_fields if "tier" in field.lower()}
    assert not {field for field in persisted_fields if "tier" in field.lower()}


@pytest.mark.asyncio
async def test_different_tiers_share_one_provider_profile_capacity_pool() -> None:
    """Guardrail: tier choice does not create a separate capacity pool.

    Production AgentRun._ensure_manager_and_signal sends SlotRequestPayload
    without a model_tier key; tier-to-profile resolution happens upstream.
    This test drives two conceptual tier choices through the real profile-
    owned ledger path and asserts they contend for the same slot.
    """

    manager = MoonMindProviderProfileManagerWorkflow()
    manager._runtime_id = "codex_cli"
    manager._profiles["profile"] = ProfileSlotState(
        profile_id="profile",
        max_parallel_runs=1,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        is_default=True,
        model_tiers=[
            {"label": "Plan", "model": "small", "effort": "provider-plan"},
            {"label": "Build", "model": "large", "effort": "provider-build"},
        ],
        default_model_tier=1,
    )
    manager._signal_slot_assigned = AsyncMock()
    # Mock persistence boundary so active patched durable-lease path can run.
    manager._sync_leases_to_db = AsyncMock(return_value=True)  # type: ignore[method-assign]

    # Verify the contract has no tier field — tier is resolved before slot request.
    assert "model_tier" not in SlotRequestPayload.__annotations__
    assert "modelTier" not in SlotRequestPayload.__annotations__

    # Demonstrate that both tier 1 and tier 2 would resolve through same profile.
    # In production, AgentRun resolves requested_model_tier to a tier definition
    # but still signals the same execution_profile_ref; slot ledger is profile-owned.
    for tier_index in (1, 2):
        resolved = resolve_model_effort(
            runtime_id="codex_cli",
            profile={
                "profile_id": "profile",
                "enabled": True,
                "auth_state": "connected",
                "model_tiers": manager._profiles["profile"].model_tiers,
                "default_model_tier": manager._profiles["profile"].default_model_tier,
            },
            requested_model_tier=tier_index,
            env={},
        )
        assert resolved.effort in ("provider-plan", "provider-build")

    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as temporal_workflow:
        temporal_workflow.now.return_value = datetime(
            2026, 8, 30, tzinfo=timezone.utc
        )
        # Exercise the active patch set, including deduplication, ordering,
        # and durable lease persistence, not the legacy fallback.
        temporal_workflow.patched.return_value = True
        # Both tier choices result in the same profile-owned slot request.
        manager.request_slot(
            {
                "requester_workflow_id": "run-tier-1",
                "runtime_id": "codex_cli",
                "execution_profile_ref": "profile",
            }
        )
        manager.request_slot(
            {
                "requester_workflow_id": "run-tier-2",
                "runtime_id": "codex_cli",
                "execution_profile_ref": "profile",
            }
        )
        await manager._drain_queue()

    manager._signal_slot_assigned.assert_awaited_once_with("run-tier-1", "profile")
    profile = manager._profiles["profile"]
    assert profile.current_leases == ["run-tier-1"]
    assert profile.available_slots == 0
    assert [request.requester_workflow_id for request in manager._pending_requests] == [
        "run-tier-2"
    ]
    # Verify persistence was attempted on the active path.
    assert manager._sync_leases_to_db.await_count >= 1


@pytest.mark.asyncio
async def test_tier_policy_refresh_does_not_change_profile_slot_leasing() -> None:
    manager = MoonMindProviderProfileManagerWorkflow()
    manager._runtime_id = "codex_cli"
    manager._profiles["profile"] = ProfileSlotState(
        profile_id="profile",
        max_parallel_runs=2,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        is_default=True,
        current_leases=["existing-run"],
        lease_granted_at={"existing-run": "2026-08-30T00:00:00+00:00"},
        model_tiers=[
            {"label": "Original", "model": "original", "effort": "native"}
        ],
        default_model_tier=1,
    )

    manager._apply_profile_sync(
        [
            {
                "profile_id": "profile",
                "runtime_id": "codex_cli",
                "enabled": True,
                "is_default": True,
                "model_tiers": [
                    {"label": "Plan", "model": "small", "effort": "fast"},
                    {"label": "Build", "model": "large", "effort": "deep"},
                ],
                "default_model_tier": 2,
            }
        ],
        authoritative=True,
    )

    # Assert lease preservation immediately after refresh, before any
    # acquire/release that could mask a cleared lease.
    profile_after_sync = manager._profiles["profile"]
    assert "existing-run" in profile_after_sync.current_leases, (
        "policy refresh must preserve active leases"
    )
    assert profile_after_sync.lease_granted_at.get("existing-run") == "2026-08-30T00:00:00+00:00"
    assert profile_after_sync.max_parallel_runs == 2
    assert profile_after_sync.default_model_tier == 2

    manager._sync_leases_to_db = AsyncMock(return_value=True)  # type: ignore[method-assign]
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as temporal_workflow:
        temporal_workflow.now.return_value = datetime(
            2026, 8, 30, 1, tzinfo=timezone.utc
        )
        temporal_workflow.patched.return_value = True
        acquired = await manager.acquire_slot(
            {
                "requester_workflow_id": "new-run",
                "runtime_id": "codex_cli",
                "execution_profile_ref": "profile",
            }
        )
        await manager.release_slot(
            {
                "requester_workflow_id": "existing-run",
                "profile_id": "profile",
            }
        )

    assert acquired == {
        "profile_id": "profile",
        "lease_id": "new-run",
        "already_held": False,
        "lease_mode": "shared_execution",
    }
    profile = manager._profiles["profile"]
    assert profile.max_parallel_runs == 2
    assert profile.default_model_tier == 2
    assert profile.current_leases == ["new-run"]
    assert profile.available_slots == 1
