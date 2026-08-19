"""Shared port-contract suite for Omnigent control-plane repositories.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

One behavioural contract per aggregate port, invoked against every adapter that
implements the port so an in-memory test double and the production SQLAlchemy
repository (on SQLite and PostgreSQL) are proven interchangeable behind the same
interface. The assertions deliberately avoid comparing storage-assigned
timestamps and only pin observable behaviour: append idempotency, dedup scope,
bounded reads, ordering, and per-reason counting.

Callers are responsible for provisioning the referenced canonical sessions
(observations and decisions carry a foreign key to ``omnigent_sessions``); the
contract only exercises the observation/decision repository surface.
"""

from __future__ import annotations

from datetime import datetime, timezone

from moonmind.omnigent.ports import (
    DecisionRepositoryPort,
    ObservationRepositoryPort,
)

def _at(seconds: int) -> datetime:
    return datetime(2024, 5, 1, 12, 0, seconds, tzinfo=timezone.utc)


async def run_observation_repository_contract(
    repo: ObservationRepositoryPort,
    *,
    session_a: str,
    session_b: str,
) -> None:
    """Assert the append-only observation index contract for one adapter."""

    first = await repo.append(
        observation_id="obs-1",
        session_id=session_a,
        observation_type="provider_event",
        source="provider",
        observed_at=_at(1),
        deduplication_key="dk-1",
    )
    assert first.observation_id == "obs-1"

    # Idempotent on (session_id, deduplication_key): the original record wins and
    # the second observation_id is never stored.
    duplicate = await repo.append(
        observation_id="obs-1-dupe",
        session_id=session_a,
        observation_type="provider_event",
        source="provider",
        observed_at=_at(9),
        deduplication_key="dk-1",
    )
    assert duplicate.observation_id == "obs-1"

    await repo.append(
        observation_id="obs-2",
        session_id=session_a,
        observation_type="snapshot",
        source="reconciler",
        observed_at=_at(2),
        deduplication_key="dk-2",
    )

    # Same deduplication_key under a different session is a distinct observation.
    await repo.append(
        observation_id="obs-3",
        session_id=session_b,
        observation_type="provider_event",
        source="provider",
        observed_at=_at(3),
        deduplication_key="dk-1",
    )

    all_a = await repo.list_for_session(session_a)
    assert [o.observation_id for o in all_a] == ["obs-1", "obs-2"]

    snapshots = await repo.list_for_session(session_a, observation_type="snapshot")
    assert [o.observation_id for o in snapshots] == ["obs-2"]

    bounded = await repo.list_for_session(session_a, limit=1)
    assert [o.observation_id for o in bounded] == ["obs-1"]

    latest = await repo.latest_for_session(session_a)
    assert latest is not None and latest.observation_id == "obs-2"

    latest_typed = await repo.latest_for_session(
        session_a, observation_types=["provider_event"]
    )
    assert latest_typed is not None and latest_typed.observation_id == "obs-1"

    assert await repo.latest_for_session("no-such-session") is None

    all_b = await repo.list_for_session(session_b)
    assert [o.observation_id for o in all_b] == ["obs-3"]


async def run_decision_repository_contract(
    repo: DecisionRepositoryPort,
    *,
    session_a: str,
    session_b: str,
) -> None:
    """Assert the append-only reconciliation-decision journal contract."""

    await repo.append(
        decision_id="dec-1",
        session_id=session_a,
        decision_code="advance",
        reason_code="ok",
    )
    await repo.append(
        decision_id="dec-2",
        session_id=session_a,
        decision_code="hold",
        reason_code="ambiguous",
    )
    await repo.append(
        decision_id="dec-3",
        session_id=session_a,
        decision_code="hold",
        reason_code="ambiguous",
    )
    await repo.append(
        decision_id="dec-4",
        session_id=session_b,
        decision_code="advance",
        reason_code="ok",
    )

    journal = await repo.list_for_session(session_a)
    assert [d.decision_id for d in journal] == ["dec-1", "dec-2", "dec-3"]

    latest = await repo.latest_for_session(session_a)
    assert latest is not None and latest.decision_id == "dec-3"

    assert await repo.latest_for_session("no-such-session") is None

    # The durable decision journal is the per-session/per-reason detection count.
    assert await repo.count_for_session_reason(session_a, "ambiguous") == 2
    assert await repo.count_for_session_reason(session_a, "ok") == 1
    assert await repo.count_for_session_reason(session_b, "ok") == 1
    assert await repo.count_for_session_reason(session_a, "never") == 0

    journal_b = await repo.list_for_session(session_b)
    assert [d.decision_id for d in journal_b] == ["dec-4"]


__all__ = [
    "run_decision_repository_contract",
    "run_observation_repository_contract",
]
