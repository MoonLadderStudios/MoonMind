"""Offer durably released Provider Profile capacity without a periodic delay."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    DB_LEASE_PERSISTENCE_PATCH,
    DURABLE_LEASE_GRANT_PATCH,
    DURABLE_RELEASE_WAKEUP_PATCH,
    HANDLERS_AWAIT_RESTORE_PATCH,
    LEASE_TRANSITION_CONTRACT_PATCH,
    VERIFY_PENDING_REQUESTS_PATCH,
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
)

NOW = datetime(2026, 10, 2, 21, 50, tzinfo=timezone.utc)
PROFILE_ID = "codex-test-profile"


def _run_input(*, held: bool = False, waiting: bool = False) -> dict:
    return {
        "runtime_id": "codex_cli",
        "profiles": [
            {
                "profile_id": PROFILE_ID,
                "max_parallel_runs": 1,
                "enabled": True,
                "credential_source": "api_key",
                "runtime_materialization_mode": "env",
            }
        ],
        "leases": {PROFILE_ID: ["first-run"]} if held else {},
        "lease_granted_at": {PROFILE_ID: {"first-run": NOW.isoformat()}},
        "pending_requests": (
            [{"requester_workflow_id": "next-run", "runtime_id": "codex_cli"}]
            if waiting
            else []
        ),
    }


def _manager_ports(wf: MoonMindProviderProfileManagerWorkflow) -> None:
    wf._load_leases_from_db = AsyncMock(return_value=True)
    wf._request_cleanup_for_expired_leases = AsyncMock()
    wf._retry_unresolved_releases = AsyncMock()
    wf._persist_lease_grant = AsyncMock(return_value=True)


def _runtime(mock_workflow, *, wakeup_enabled: bool) -> None:
    enabled = {
        DB_LEASE_PERSISTENCE_PATCH,
        DURABLE_LEASE_GRANT_PATCH,
        HANDLERS_AWAIT_RESTORE_PATCH,
        LEASE_TRANSITION_CONTRACT_PATCH,
        VERIFY_PENDING_REQUESTS_PATCH,
    }
    if wakeup_enabled:
        enabled.add(DURABLE_RELEASE_WAKEUP_PATCH)
    mock_workflow.patched.side_effect = lambda marker: marker in enabled
    mock_workflow.now.return_value = NOW
    mock_workflow.info.return_value = SimpleNamespace(
        continued_run_id=None,
        get_current_history_length=lambda: 0,
        is_continue_as_new_suggested=lambda: False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("wakeup_enabled", [False, True], ids=["retained", "new"])
async def test_request_arriving_during_manager_work_is_drained_without_timer(
    wakeup_enabled: bool,
) -> None:
    wf = MoonMindProviderProfileManagerWorkflow()
    _manager_ports(wf)
    assigned: list[str] = []
    periodic_timeouts = 0

    async def receive_request(**_kwargs) -> None:
        if not wf._event_count:
            await wf.request_slot(
                {"requester_workflow_id": "next-run", "runtime_id": "codex_cli"}
            )

    async def assigned_slot(requester_id, _profile_id, **_kwargs) -> None:
        assigned.append(requester_id)
        wf._shutdown_requested = True

    async def wait_condition(predicate, timeout=None) -> None:
        nonlocal periodic_timeouts
        if not predicate():
            periodic_timeouts += 1
            assert periodic_timeouts == 1
            raise TimeoutError

    wf._verify_active_workflows = AsyncMock(side_effect=receive_request)
    wf._signal_slot_assigned = AsyncMock(side_effect=assigned_slot)
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as mock_workflow:
        _runtime(mock_workflow, wakeup_enabled=wakeup_enabled)
        mock_workflow.wait_condition.side_effect = wait_condition
        await wf.run(_run_input())

    assert assigned == ["next-run"]
    assert wf._profiles[PROFILE_ID].current_leases == ["next-run"]
    assert periodic_timeouts == (0 if wakeup_enabled else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("wakeup_enabled", [False, True], ids=["retained", "new"])
async def test_release_finishing_after_initial_wakeup_drains_queued_request(
    wakeup_enabled: bool,
) -> None:
    wf = MoonMindProviderProfileManagerWorkflow()
    _manager_ports(wf)
    release_started = asyncio.Event()
    allow_release = asyncio.Event()
    release_task = None
    assigned: list[str] = []
    periodic_timeouts = 0

    async def ledger_release(*_args, **_kwargs) -> str:
        release_started.set()
        await allow_release.wait()
        return "released"

    async def receive_release(**_kwargs) -> None:
        nonlocal release_task
        if release_task is None:
            release_task = asyncio.create_task(
                wf.release_slot(
                    {"profile_id": PROFILE_ID, "requester_workflow_id": "first-run"}
                )
            )
            await release_started.wait()

    async def assigned_slot(requester_id, _profile_id, **_kwargs) -> None:
        assigned.append(requester_id)
        wf._shutdown_requested = True

    async def wait_condition(predicate, timeout=None) -> None:
        nonlocal periodic_timeouts
        # Consume the signal's initial wakeup while its durable release is
        # still in flight. The slot must remain spent at that boundary.
        if not predicate() and not allow_release.is_set():
            assert wf._profiles[PROFILE_ID].current_leases == ["first-run"]
            assert assigned == []
            allow_release.set()
            await release_task
        if not predicate():
            periodic_timeouts += 1
            assert periodic_timeouts == 1
            raise TimeoutError

    wf._remove_lease_from_db = AsyncMock(side_effect=ledger_release)
    wf._verify_active_workflows = AsyncMock(side_effect=receive_release)
    wf._signal_slot_assigned = AsyncMock(side_effect=assigned_slot)
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as mock_workflow:
        _runtime(mock_workflow, wakeup_enabled=wakeup_enabled)
        mock_workflow.wait_condition.side_effect = wait_condition
        await wf.run(_run_input(held=True, waiting=True))
        await release_task

    assert assigned == ["next-run"]
    assert wf._profiles[PROFILE_ID].current_leases == ["next-run"]
    assert periodic_timeouts == (0 if wakeup_enabled else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("wakeup_enabled", [False, True], ids=["retained", "new"])
@pytest.mark.parametrize(
    "outcome", ["released", "already_released", "retryable", "stale", "conflict"]
)
async def test_only_confirmed_durable_release_wakes_after_persistence(
    wakeup_enabled: bool,
    outcome: str,
) -> None:
    wf = MoonMindProviderProfileManagerWorkflow()
    wf._runtime_id = "codex_cli"
    wf._durable_release_wakeup = wakeup_enabled
    wf._profiles[PROFILE_ID] = ProfileSlotState(
        profile_id=PROFILE_ID,
        max_parallel_runs=1,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        current_leases=["first-run"],
    )
    release_started = asyncio.Event()
    allow_release = asyncio.Event()

    async def ledger_release(*_args, **_kwargs) -> str:
        release_started.set()
        await allow_release.wait()
        return outcome

    wf._remove_lease_from_db = AsyncMock(side_effect=ledger_release)
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
    ) as mock_workflow:
        _runtime(mock_workflow, wakeup_enabled=wakeup_enabled)
        release_task = asyncio.create_task(
            wf._release_slot_durably(
                profile_id=PROFILE_ID, requester_id="first-run", payload={}
            )
        )
        await release_started.wait()
        assert wf._profiles[PROFILE_ID].available_slots == 0
        assert wf._has_new_events is False
        allow_release.set()
        await release_task

    released = outcome in {"released", "already_released"}
    assert wf._profiles[PROFILE_ID].available_slots == int(released)
    assert wf._has_new_events is (wakeup_enabled and released)
    assert bool(wf._unresolved_releases) is (not released)
