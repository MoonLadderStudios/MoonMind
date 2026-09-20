"""A manager run the current build cannot replay is replaced, not endured.

``provider-profile-manager:<runtime>`` is a singleton whose history outlives
any one release. When a release cannot replay a recorded history the singleton
wedges in a ``WORKFLOW_TASK_FAILED_CAUSE_NON_DETERMINISTIC_ERROR`` loop and
every credential operation for that runtime fails, because the manager is the
one credential-capacity ledger.

The durable ledger, not the workflow history, decides whether a slot is spent,
so a wedged run can be replaced from the database. These tests pin the safety
contract around that replacement: it happens only on confirmed nondeterminism
evidence, only against the exact observed run, and never while the ledger says
a consumer still holds capacity or cannot be read at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.provider_profiles.manager_recovery import (
    MANAGER_UNREADABLE_LEDGER,
    MANAGER_HELD_LEASE_PRESENT,
    MANAGER_NOT_RUNNING,
    MANAGER_NOT_WEDGED,
    ProviderManagerUnavailableError,
    recover_wedged_provider_manager,
)

RUNTIME_ID = "opencode"
WORKFLOW_ID = "provider-profile-manager:opencode"
WEDGE_MESSAGE = (
    "[TMPRL1100] Nondeterminism error: Activity type of scheduled event "
    "'provider_profile.sync_slot_leases' does not match activity type of "
    "activity command 'provider_profile.verify_lease_holders'"
)


class _Event:
    """A minimal history event double shaped like the protobuf the SDK yields."""

    def __init__(self, field: str, *, cause: str = "", message: str = "") -> None:
        self._field = field
        self.workflow_task_failed_event_attributes = _FailedAttrs(cause, message)

    def HasField(self, name: str) -> bool:  # noqa: N802 - protobuf API shape
        return name == self._field


class _FailedAttrs:
    def __init__(self, cause: str, message: str) -> None:
        self.cause = _Cause(cause)
        self.failure = _Failure(message)


class _Cause:
    def __init__(self, name: str) -> None:
        self.name = name

    def __int__(self) -> int:
        # Deliberately unmappable to the SDK enum so the scan falls back to the
        # cause name, exactly as it does for an unknown server-side value.
        raise TypeError("test cause is not an enum value")

    def __str__(self) -> str:
        return self.name


class _Failure:
    def __init__(self, message: str) -> None:
        self.message = message


def _nondeterminism_tail(count: int = 3) -> list[Any]:
    events: list[Any] = [_Event("timer_fired_event_attributes")]
    for _ in range(count):
        events.append(
            _Event(
                "workflow_task_failed_event_attributes",
                cause="WORKFLOW_TASK_FAILED_CAUSE_NON_DETERMINISTIC_ERROR",
                message=WEDGE_MESSAGE,
            )
        )
    return events


class _Description:
    def __init__(self, status: str, run_id: str) -> None:
        self.status = _Cause(status)
        self.run_id = run_id


class _Handle:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def fetch_history_events(self, **_kwargs: Any):
        for event in self._events:
            yield event


class _Adapter:
    """Records exactly which Temporal RPCs recovery performed."""

    def __init__(
        self,
        *,
        status: str = "RUNNING",
        run_id: str = "wedged-run",
        events: list[Any] | None = None,
    ) -> None:
        self._status = status
        self._run_id = run_id
        self._events = events if events is not None else _nondeterminism_tail()
        self.terminated: list[tuple[str, str, str]] = []
        self.handle_run_ids: list[str | None] = []

    async def describe_workflow(self, workflow_id: str, **_kwargs: Any) -> Any:
        assert workflow_id == WORKFLOW_ID
        return _Description(self._status, self._run_id)

    async def get_workflow_handle(
        self, workflow_id: str, *, run_id: str | None = None
    ) -> Any:
        assert workflow_id == WORKFLOW_ID
        self.handle_run_ids.append(run_id)
        return _Handle(self._events)

    async def terminate_workflow(
        self, workflow_id: str, *, reason: str, run_id: str | None = None
    ) -> None:
        self.terminated.append((workflow_id, reason, run_id or ""))


def _probe(value: int | None):
    async def probe(runtime_id: str) -> int | None:
        assert runtime_id == RUNTIME_ID
        return value

    return probe


def _starter(started: list[str]):
    async def start_manager() -> str:
        started.append(WORKFLOW_ID)
        return WORKFLOW_ID

    return start_manager


@pytest.mark.asyncio
async def test_replaces_a_wedged_manager_when_the_ledger_shows_no_held_lease() -> None:
    """Confirmed nondeterminism plus a free ledger is a recoverable wedge."""
    adapter = _Adapter()
    started: list[str] = []

    recovery = await recover_wedged_provider_manager(
        adapter,
        runtime_id=RUNTIME_ID,
        start_manager=_starter(started),
        held_lease_probe=_probe(0),
    )

    assert recovery.recovered is True
    assert recovery.refusal == ""
    assert recovery.terminated_run_id == "wedged-run"
    assert recovery.nondeterminism_failures == 3
    # The original error survives the recovery that hides its symptom.
    assert "TMPRL1100" in recovery.evidence
    assert started == [WORKFLOW_ID]
    assert len(adapter.terminated) == 1
    workflow_id, reason, run_id = adapter.terminated[0]
    assert workflow_id == WORKFLOW_ID
    # Terminating by observed run ID cannot kill a healthy replacement that a
    # concurrent caller started between the describe and the terminate.
    assert run_id == "wedged-run"
    assert "TMPRL1100" in reason


@pytest.mark.asyncio
async def test_refuses_to_replace_a_manager_while_a_lease_is_held() -> None:
    """A held row means a live consumer; replacing revokes its authority."""
    adapter = _Adapter()
    started: list[str] = []

    recovery = await recover_wedged_provider_manager(
        adapter,
        runtime_id=RUNTIME_ID,
        start_manager=_starter(started),
        held_lease_probe=_probe(2),
    )

    assert recovery.recovered is False
    assert recovery.refusal == MANAGER_HELD_LEASE_PRESENT
    assert recovery.held_leases == 2
    assert adapter.terminated == []
    assert started == []


@pytest.mark.asyncio
async def test_refuses_to_replace_a_manager_when_the_ledger_is_unreadable() -> None:
    """Unavailable evidence is not evidence of free capacity."""
    adapter = _Adapter()
    started: list[str] = []

    recovery = await recover_wedged_provider_manager(
        adapter,
        runtime_id=RUNTIME_ID,
        start_manager=_starter(started),
        held_lease_probe=_probe(None),
    )

    assert recovery.recovered is False
    assert recovery.refusal == MANAGER_UNREADABLE_LEDGER
    assert recovery.held_leases is None
    assert adapter.terminated == []
    assert started == []


@pytest.mark.asyncio
async def test_refuses_when_the_tail_shows_a_completed_workflow_task() -> None:
    """A manager that is making progress is not a replay wedge."""
    events = _nondeterminism_tail(1)
    events.insert(1, _Event("workflow_task_completed_event_attributes"))
    adapter = _Adapter(events=[events[1], events[0]])
    started: list[str] = []

    recovery = await recover_wedged_provider_manager(
        adapter,
        runtime_id=RUNTIME_ID,
        start_manager=_starter(started),
        held_lease_probe=_probe(0),
    )

    assert recovery.recovered is False
    assert recovery.refusal == MANAGER_NOT_WEDGED
    assert adapter.terminated == []
    assert started == []


@pytest.mark.asyncio
async def test_refuses_when_the_manager_is_not_running() -> None:
    """A closed run needs a start, not a replacement."""
    adapter = _Adapter(status="COMPLETED")
    started: list[str] = []

    recovery = await recover_wedged_provider_manager(
        adapter,
        runtime_id=RUNTIME_ID,
        start_manager=_starter(started),
        held_lease_probe=_probe(0),
    )

    assert recovery.recovered is False
    assert recovery.refusal == MANAGER_NOT_RUNNING
    assert adapter.terminated == []


@pytest.mark.asyncio
async def test_unavailable_error_reports_the_refusal_and_the_evidence() -> None:
    """The operator-facing failure names what was observed, not a guess."""
    adapter = _Adapter()
    recovery = await recover_wedged_provider_manager(
        adapter,
        runtime_id=RUNTIME_ID,
        start_manager=_starter([]),
        held_lease_probe=_probe(1),
    )
    error = ProviderManagerUnavailableError(recovery)

    assert error.recovery is recovery
    assert MANAGER_HELD_LEASE_PRESENT in str(error)
    assert RUNTIME_ID in str(error)
