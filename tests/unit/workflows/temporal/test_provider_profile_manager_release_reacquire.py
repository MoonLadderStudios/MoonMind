"""A new admission must not inherit a lease whose release is still running."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest

from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    DB_LEASE_PERSISTENCE_PATCH,
    DURABLE_LEASE_GRANT_PATCH,
    PROVIDER_INCREMENTAL_LEASE_PATCH,
    SLOT_HANDOFF_RESERVATION_PATCH,
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
)


def _held_manager():
    wf = MoonMindProviderProfileManagerWorkflow()
    wf._runtime_id = "codex_cli"
    wf._startup_restored = True
    wf._lease_transition_contract = True
    wf._durable_maintenance_queue = True
    wf._durable_release_wakeup = True
    wf._owner_release_ordering = True
    wf._lease_grant_sequence = 1
    profile = ProfileSlotState(
        profile_id="codex-profile",
        max_parallel_runs=1,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        current_leases=["same-owner"],
        lease_metadata={"same-owner": {"fencingGeneration": 1}},
    )
    wf._profiles[profile.profile_id] = profile
    wf._index_lease(profile.profile_id, "same-owner", "same-owner")
    return wf, profile


@pytest.mark.asyncio
@pytest.mark.parametrize("drain_during_release", [False, True])
@pytest.mark.parametrize("release_outcome", ["released", "retryable"])
async def test_readmission_waits_for_inflight_release_before_regrant(
    drain_during_release: bool,
    release_outcome: str,
) -> None:
    wf, profile = _held_manager()
    release_started = asyncio.Event()
    allow_release = asyncio.Event()

    async def release_ledger(*_args, **_kwargs) -> str:
        release_started.set()
        await allow_release.wait()
        return release_outcome

    wf._remove_lease_from_db = AsyncMock(side_effect=release_ledger)
    wf._persist_lease_grant = AsyncMock(return_value=True)
    wf._signal_slot_assigned = AsyncMock()
    enabled = {
        DB_LEASE_PERSISTENCE_PATCH,
        DURABLE_LEASE_GRANT_PATCH,
        PROVIDER_INCREMENTAL_LEASE_PATCH,
        SLOT_HANDOFF_RESERVATION_PATCH,
    }
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as runtime:
        runtime.now.return_value = datetime(2026, 10, 7, tzinfo=timezone.utc)
        runtime.patched.side_effect = lambda marker: marker in enabled
        release_task = asyncio.create_task(
            wf.release_slot(
                {
                    "profile_id": profile.profile_id,
                    "requester_workflow_id": "same-owner",
                    "fencing_generation": 1,
                }
            )
        )
        try:
            await asyncio.wait_for(release_started.wait(), timeout=5)
            await wf.request_slot(
                {
                    "requester_workflow_id": "same-owner",
                    "runtime_id": "codex_cli",
                    "execution_profile_ref": profile.profile_id,
                }
            )
            if drain_during_release:
                await wf._drain_queue()
                wf._signal_slot_assigned.assert_not_awaited()
            # A concurrent retry of the release joins the original obligation;
            # neither the signal nor periodic retry issues another live write.
            await wf.release_slot(
                {
                    "profile_id": profile.profile_id,
                    "requester_workflow_id": "same-owner",
                    "fencing_generation": 1,
                }
            )
            await wf._retry_unresolved_releases()
            assert wf._remove_lease_from_db.await_count == 1
            # A periodic cleanup failure is weaker than the owner's release;
            # it must not replace that durable obligation or reopen admission.
            wf._expired_lease_candidates = Mock(
                return_value=[(profile.profile_id, "same-owner")]
            )
            wf._request_lease_cleanup = AsyncMock(return_value="retryable")
            await wf._request_cleanup_for_expired_leases()
            # A delayed cleanup callback from an earlier generation must not
            # replace the active release merely because its fence differs.
            wf._record_unresolved_release(
                "same-owner",
                profile_id="older-profile",
                fencing_generation=0,
                outcome="retryable",
                kind="cleanup_request",
            )
            assert wf._unresolved_releases["same-owner"]["kind"] == "owner_release"
            assert wf._lease_grant_is_pending("same-owner")
            snapshot = wf._build_continue_as_new_input()
            successor = MoonMindProviderProfileManagerWorkflow()
            successor._runtime_id = "codex_cli"
            successor._lease_transition_contract = True
            successor._owner_release_ordering = True
            successor._restore_state(snapshot)
            assert successor._lease_grant_is_pending("same-owner")
            assert [
                req.requester_workflow_id for req in successor._pending_requests
            ] == ["same-owner"]
            assert (
                wf.get_state()["unresolved_releases"]["same-owner"][
                    "fencing_generation"
                ]
                == 1
            )
        finally:
            allow_release.set()
            await asyncio.wait_for(release_task, timeout=5)

        if release_outcome == "retryable":
            # Redelivery after a failed write still belongs to the original
            # release and must not withdraw a later readmission request.
            await wf.release_slot(
                {
                    "profile_id": profile.profile_id,
                    "requester_workflow_id": "same-owner",
                    "fencing_generation": 1,
                }
            )
            assert [req.requester_workflow_id for req in wf._pending_requests] == [
                "same-owner"
            ]
            await wf._drain_queue()
            wf._signal_slot_assigned.assert_not_awaited()
            wf._remove_lease_from_db.side_effect = None
            wf._remove_lease_from_db.return_value = "released"
            await wf._retry_unresolved_releases()
        # A successful release has removed the old holder. Its delayed fenced
        # duplicate must not cancel the queued next admission or lose its fence.
        await wf.release_slot(
            {
                "profile_id": profile.profile_id,
                "requester_workflow_id": "same-owner",
                "fencing_generation": 1,
            }
        )
        assert wf._remove_lease_from_db.call_args.kwargs["fencing_generation"] == 1
        assert [req.requester_workflow_id for req in wf._pending_requests] == [
            "same-owner"
        ]
        await wf._drain_queue()

    wf._signal_slot_assigned.assert_awaited_once_with(
        "same-owner", "codex-profile", fencing_generation=2
    )
    wf._persist_lease_grant.assert_awaited_once()
    assert profile.current_leases == ["same-owner"]
    assert profile.lease_fencing_generation("same-owner") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("admission", ["signal", "direct"])
async def test_confirmed_cleanup_does_not_regrant_before_owner_release_finishes(
    admission: str,
) -> None:
    wf, profile = _held_manager()
    release_started = asyncio.Event()
    allow_release = asyncio.Event()
    acquisition_waiting = asyncio.Event()

    async def release_ledger(*_args, **_kwargs):
        release_started.set()
        await allow_release.wait()
        return "already_released"

    async def wait_condition(predicate, timeout=None):
        acquisition_waiting.set()
        while not predicate():
            await asyncio.sleep(0)

    wf._remove_lease_from_db = AsyncMock(side_effect=release_ledger)
    wf._persist_lease_grant = AsyncMock(return_value=True)
    wf._signal_slot_assigned = AsyncMock()
    wf._expired_lease_candidates = Mock(
        return_value=[(profile.profile_id, "same-owner")]
    )
    wf._request_lease_cleanup = AsyncMock(return_value="already_released")
    request = {
        "requester_workflow_id": "same-owner",
        "runtime_id": "codex_cli",
        "execution_profile_ref": profile.profile_id,
    }
    enabled = {
        DB_LEASE_PERSISTENCE_PATCH,
        DURABLE_LEASE_GRANT_PATCH,
        PROVIDER_INCREMENTAL_LEASE_PATCH,
        SLOT_HANDOFF_RESERVATION_PATCH,
    }
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as runtime:
        runtime.now.return_value = datetime(2026, 10, 7, tzinfo=timezone.utc)
        runtime.patched.side_effect = lambda marker: marker in enabled
        runtime.wait_condition.side_effect = wait_condition
        release_task = asyncio.create_task(
            wf.release_slot(
                {
                    **request,
                    "profile_id": profile.profile_id,
                    "fencing_generation": 1,
                }
            )
        )
        acquire_task = None
        try:
            await asyncio.wait_for(release_started.wait(), 5)
            await wf._request_cleanup_for_expired_leases()
            assert profile.current_leases == []
            # Exercise a confirmed cleanup path that can clear the durable
            # obligation while the original handler still awaits its result.
            wf._unresolved_releases.pop("same-owner", None)
            assert wf._lease_grant_is_pending("same-owner")
            if admission == "direct":
                acquire_task = asyncio.create_task(wf.acquire_slot(request))
                await asyncio.wait_for(acquisition_waiting.wait(), 5)
                assert not acquire_task.done()
            else:
                await wf.request_slot(request)
                await wf._drain_queue()
                wf._signal_slot_assigned.assert_not_awaited()
            wf._persist_lease_grant.assert_not_awaited()
            # The earlier cleanup wakeup may be consumed during persistence.
            wf._has_new_events = False
        finally:
            allow_release.set()
            await asyncio.wait_for(release_task, 5)
        assert wf._has_new_events
        if acquire_task is not None:
            grant = await asyncio.wait_for(acquire_task, 5)
            assert grant["already_held"] is False
            assert grant["lease_fencing_generation"] == 2
        else:
            await wf._drain_queue()
            wf._signal_slot_assigned.assert_awaited_once_with(
                "same-owner",
                profile.profile_id,
                fencing_generation=2,
            )
    assert profile.lease_fencing_generation("same-owner") == 2


@pytest.mark.asyncio
async def test_old_release_cannot_block_newer_grant_on_another_profile() -> None:
    from dataclasses import replace

    wf, profile = _held_manager()
    profile.release("same-owner")
    wf._unindex_lease("same-owner")
    replacement = replace(
        profile,
        profile_id="other-profile",
        current_leases=["same-owner"],
        lease_metadata={"same-owner": {"fencingGeneration": 2}},
    )
    wf._profiles[replacement.profile_id] = replacement
    wf._index_lease(replacement.profile_id, "same-owner", "same-owner")
    wf._lease_grant_sequence = 2
    wf._remove_lease_from_db = AsyncMock(return_value="conflict")
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as runtime:
        runtime.patched.return_value = True
        await wf.release_slot(
            {
                "profile_id": profile.profile_id,
                "requester_workflow_id": "same-owner",
                "fencing_generation": 1,
            }
        )
        wf._remove_lease_from_db.assert_not_awaited()
        result = await wf.acquire_slot(
            {
                "requester_workflow_id": "same-owner",
                "runtime_id": "codex_cli",
                "execution_profile_ref": replacement.profile_id,
            }
        )
    assert result["profile_id"] == replacement.profile_id
    assert result["already_held"] is True
    assert result["lease_fencing_generation"] == 2


@pytest.mark.asyncio
async def test_stale_release_of_missing_holder_does_not_block_future_admission() -> (
    None
):
    wf, profile = _held_manager()
    profile.release("same-owner")
    wf._unindex_lease("same-owner")
    wf._lease_grant_sequence = 2
    wf._remove_lease_from_db = AsyncMock(return_value="stale")
    wf._persist_lease_grant = AsyncMock(return_value=True)
    wf._signal_slot_assigned = AsyncMock()
    enabled = {
        DB_LEASE_PERSISTENCE_PATCH,
        DURABLE_LEASE_GRANT_PATCH,
        PROVIDER_INCREMENTAL_LEASE_PATCH,
        SLOT_HANDOFF_RESERVATION_PATCH,
    }
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as runtime:
        runtime.now.return_value = datetime(2026, 10, 7, tzinfo=timezone.utc)
        runtime.patched.side_effect = lambda marker: marker in enabled
        await wf.request_slot(
            {
                "requester_workflow_id": "same-owner",
                "runtime_id": "codex_cli",
                "execution_profile_ref": profile.profile_id,
            }
        )
        # Persistence has a later tombstone (generation 2), so this delayed
        # generation-1 release correctly returns stale rather than releasing it.
        await wf.release_slot(
            {
                "profile_id": profile.profile_id,
                "requester_workflow_id": "same-owner",
                "fencing_generation": 1,
            }
        )
        assert not wf._lease_grant_is_pending("same-owner")
        await wf._drain_queue()
        wf._expired_lease_candidates = Mock(
            return_value=[(profile.profile_id, "same-owner")]
        )
        wf._request_lease_cleanup = AsyncMock(return_value="retryable")
        await wf._request_cleanup_for_expired_leases()
        assert wf._unresolved_releases["same-owner"]["kind"] == "cleanup_request"
        assert wf._unresolved_releases["same-owner"]["fencing_generation"] == 3
    wf._signal_slot_assigned.assert_awaited_once_with(
        "same-owner",
        profile.profile_id,
        fencing_generation=3,
    )
