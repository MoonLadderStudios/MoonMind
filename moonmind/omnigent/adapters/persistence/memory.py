"""In-memory persistence adapters for the append-only control-plane aggregates.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

These adapters implement :class:`~moonmind.omnigent.ports.ObservationRepositoryPort`
and :class:`~moonmind.omnigent.ports.DecisionRepositoryPort` with the same
observable behaviour (append idempotency, bounded reads, ordering, per-reason
counting) as the production SQLAlchemy repositories in
:mod:`moonmind.omnigent.control_plane.repositories`. Both families are exercised
by the same shared port-contract suite
(``tests/helpers/omnigent_port_contracts.py``) so an in-memory test double and
the PostgreSQL adapter are proven interchangeable behind one interface.

Ordering mirrors the production repositories: observations order by
``(observed_at, observation_id)`` and decisions by ``(created_at, decision_id)``.
Each appended record is assigned a strictly increasing synthetic ``created_at``
so append order and the ``(timestamp, id)`` sort order agree without depending
on wall-clock time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any, Optional, Sequence

from moonmind.omnigent.control_plane.records import (
    DecisionRecord,
    ObservationRecord,
)

# Synthetic monotonic clock base for ``created_at`` assignment. A strictly
# increasing per-append offset guarantees append order equals the
# ``(created_at, id)`` sort order the production repositories use.
_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


class InMemoryObservationRepository:
    """In-memory append-only observation index.

    Appends are idempotent on ``(session_id, deduplication_key)``: a duplicate
    append returns the previously stored record unchanged.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, ObservationRecord] = {}
        self._dedup: dict[tuple[str, str], str] = {}
        self._seq = count()

    async def append(
        self,
        *,
        observation_id: str,
        session_id: str,
        observation_type: str,
        source: str,
        observed_at: datetime,
        deduplication_key: str,
        source_sequence: Optional[int] = None,
        source_digest: Optional[str] = None,
        payload_ref: Optional[str] = None,
        bounded_index: Optional[dict[str, Any]] = None,
    ) -> ObservationRecord:
        dedup_key = (session_id, deduplication_key)
        existing_id = self._dedup.get(dedup_key)
        if existing_id is not None:
            return self._by_id[existing_id]
        record = ObservationRecord(
            observation_id=observation_id,
            session_id=session_id,
            observation_type=observation_type,
            source=source,
            observed_at=observed_at,
            deduplication_key=deduplication_key,
            source_sequence=source_sequence,
            source_digest=source_digest,
            payload_ref=payload_ref,
            bounded_index=dict(bounded_index or {}),
            created_at=_EPOCH + timedelta(microseconds=next(self._seq)),
        )
        self._by_id[observation_id] = record
        self._dedup[dedup_key] = observation_id
        return record

    async def list_for_session(
        self,
        session_id: str,
        *,
        observation_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[ObservationRecord]:
        rows = [
            r
            for r in self._by_id.values()
            if r.session_id == session_id
            and (observation_type is None or r.observation_type == observation_type)
        ]
        rows.sort(key=lambda r: (r.observed_at, r.observation_id))
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def latest_for_session(
        self,
        session_id: str,
        *,
        observation_types: Optional[Sequence[str]] = None,
    ) -> Optional[ObservationRecord]:
        type_filter = set(observation_types) if observation_types is not None else None
        rows = [
            r
            for r in self._by_id.values()
            if r.session_id == session_id
            and (type_filter is None or r.observation_type in type_filter)
        ]
        if not rows:
            return None
        rows.sort(key=lambda r: (r.observed_at, r.observation_id), reverse=True)
        return rows[0]


class InMemoryDecisionRepository:
    """In-memory append-only reconciliation-decision journal."""

    def __init__(self) -> None:
        self._records: list[DecisionRecord] = []
        self._seq = count()

    async def append(
        self,
        *,
        decision_id: str,
        session_id: str,
        decision_code: str,
        input_state_digest: Optional[str] = None,
        observation_frontier_digest: Optional[str] = None,
        expected_revision: Optional[int] = None,
        fencing_generation: int = 0,
        reason_code: Optional[str] = None,
        resulting_command_id: Optional[str] = None,
        next_deadline: Optional[datetime] = None,
        product_visible_transition: Optional[str] = None,
        trace_ref: Optional[str] = None,
        diagnostics_ref: Optional[str] = None,
    ) -> DecisionRecord:
        record = DecisionRecord(
            decision_id=decision_id,
            session_id=session_id,
            decision_code=decision_code,
            input_state_digest=input_state_digest,
            observation_frontier_digest=observation_frontier_digest,
            expected_revision=expected_revision,
            fencing_generation=fencing_generation,
            reason_code=reason_code,
            resulting_command_id=resulting_command_id,
            next_deadline=next_deadline,
            product_visible_transition=product_visible_transition,
            trace_ref=trace_ref,
            diagnostics_ref=diagnostics_ref,
            created_at=_EPOCH + timedelta(microseconds=next(self._seq)),
        )
        self._records.append(record)
        return record

    async def list_for_session(self, session_id: str) -> list[DecisionRecord]:
        rows = [r for r in self._records if r.session_id == session_id]
        rows.sort(key=lambda r: (r.created_at, r.decision_id))
        return rows

    async def latest_for_session(
        self, session_id: str
    ) -> Optional[DecisionRecord]:
        rows = [r for r in self._records if r.session_id == session_id]
        if not rows:
            return None
        rows.sort(key=lambda r: (r.created_at, r.decision_id), reverse=True)
        return rows[0]

    async def count_for_session_reason(
        self, session_id: str, reason_code: str
    ) -> int:
        return sum(
            1
            for r in self._records
            if r.session_id == session_id and r.reason_code == reason_code
        )


__all__ = [
    "InMemoryDecisionRepository",
    "InMemoryObservationRepository",
]
