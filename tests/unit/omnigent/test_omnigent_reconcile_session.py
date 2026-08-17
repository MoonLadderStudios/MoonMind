"""Tests for the ReconcileSession application use case.

Covers normal-create, running, terminal, retry (idempotent re-observation), and
degraded optional-resource behavior over the in-memory session repository
(MoonLadderStudios/MoonMind#3711).
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.adapters.persistence.memory import InMemorySessionRepository
from moonmind.omnigent.application.reconcile_session import (
    ReconcileSession,
    UnsupportedObservationError,
)
from moonmind.omnigent.domain.commands import RecordTerminalStatus, ReleaseHost


def test_decide_running_event_maps_to_active() -> None:
    use_case = ReconcileSession(InMemorySessionRepository())
    decision = use_case.decide("declared", {"type": "response.in_progress"})
    assert decision.next_status == "active"
    assert decision.is_terminal is False
    assert decision.commands == ()


def test_decide_terminal_emits_record_and_release_commands() -> None:
    use_case = ReconcileSession(InMemorySessionRepository())
    decision = use_case.decide("active", {"type": "response.completed"})
    assert decision.next_status == "completed"
    assert decision.is_terminal is True
    kinds = [type(c) for c in decision.commands]
    assert RecordTerminalStatus in kinds and ReleaseHost in kinds


def test_decide_terminal_is_absorbing() -> None:
    use_case = ReconcileSession(InMemorySessionRepository())
    # Already-failed session ignores a later running observation.
    decision = use_case.decide("failed", {"type": "response.in_progress"})
    assert decision.next_status == "failed"
    assert decision.is_terminal is True


def test_decide_unrecognized_event_raises() -> None:
    use_case = ReconcileSession(InMemorySessionRepository())
    with pytest.raises(UnsupportedObservationError):
        use_case.decide("active", {"type": "totally.unknown.event"})


def test_decide_optional_resource_drift_degrades() -> None:
    use_case = ReconcileSession(InMemorySessionRepository())
    decision = use_case.decide("active", {"type": "resource.unknown_thing"})
    assert decision.is_terminal is False
    assert decision.diagnostic is not None
    assert "optional_resource" in decision.diagnostic


def test_decide_payload_status_fallthrough() -> None:
    use_case = ReconcileSession(InMemorySessionRepository())
    decision = use_case.decide(
        "creating", {"type": "session.status", "status": "running"}
    )
    assert decision.next_status == "active"


@pytest.mark.asyncio
async def test_reconcile_persists_status_change() -> None:
    repo = InMemorySessionRepository()
    await repo.create("s1", status="declared")
    use_case = ReconcileSession(repo)

    result = await use_case.reconcile("s1", {"type": "response.in_progress"})
    assert result.changed is True
    assert result.record.status == "active"
    assert result.record.revision == 2
    # Commands are bound to the concrete session id.
    assert all(
        getattr(c, "bridge_session_id", "s1") == "s1" for c in result.decision.commands
    )


@pytest.mark.asyncio
async def test_reconcile_terminal_binds_commands_to_session() -> None:
    repo = InMemorySessionRepository()
    await repo.create("s1", status="active")
    use_case = ReconcileSession(repo)

    result = await use_case.reconcile("s1", {"type": "response.completed"})
    assert result.record.status == "completed"
    ids = {c.bridge_session_id for c in result.decision.commands}
    assert ids == {"s1"}


@pytest.mark.asyncio
async def test_reconcile_idempotent_when_no_change() -> None:
    repo = InMemorySessionRepository()
    await repo.create("s1", status="active")
    use_case = ReconcileSession(repo)

    # A heartbeat keeps the session active: no revision bump, no write.
    result = await use_case.reconcile("s1", {"type": "session.heartbeat"})
    assert result.changed is False
    assert result.record.revision == 1


@pytest.mark.asyncio
async def test_reconcile_unknown_session_raises() -> None:
    use_case = ReconcileSession(InMemorySessionRepository())
    with pytest.raises(LookupError):
        await use_case.reconcile("nope", {"type": "response.completed"})
