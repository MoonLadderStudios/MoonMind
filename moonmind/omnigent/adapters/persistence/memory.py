"""In-memory repository adapters (test/dev doubles).

These implement the repository ports with the same revision/fencing semantics as
the production adapters and are the reference implementation the shared port
contract suite runs against alongside the SQLAlchemy adapter.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from moonmind.omnigent.domain.commands import Command
from moonmind.omnigent.domain.decisions import ReconcileDecision
from moonmind.omnigent.ports.observations import ObservationRecord
from moonmind.omnigent.ports.sessions import (
    SessionRecord,
    SessionRevisionConflict,
)
from moonmind.omnigent.ports.turns import TurnRecord


class InMemorySessionRepository:
    """Dict-backed :class:`SessionRepository` with optimistic concurrency."""

    def __init__(self) -> None:
        self._rows: dict[str, SessionRecord] = {}

    async def get(self, bridge_session_id: str) -> SessionRecord | None:
        return self._rows.get(bridge_session_id)

    async def create(
        self,
        bridge_session_id: str,
        *,
        status: str,
        omnigent_session_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> SessionRecord:
        if bridge_session_id in self._rows:
            existing = self._rows[bridge_session_id]
            raise SessionRevisionConflict(bridge_session_id, 0, existing.revision)
        record = SessionRecord(
            bridge_session_id=bridge_session_id,
            status=status,
            revision=1,
            omnigent_session_id=omnigent_session_id,
            metadata=dict(metadata or {}),
        )
        self._rows[bridge_session_id] = record
        return record

    async def save(
        self, record: SessionRecord, *, expected_revision: int
    ) -> SessionRecord:
        current = self._rows.get(record.bridge_session_id)
        if current is None:
            raise SessionRevisionConflict(record.bridge_session_id, expected_revision, 0)
        if current.revision != expected_revision:
            raise SessionRevisionConflict(
                record.bridge_session_id, expected_revision, current.revision
            )
        saved = replace(record, revision=current.revision + 1)
        self._rows[record.bridge_session_id] = saved
        return saved


class InMemoryTurnRepository:
    def __init__(self) -> None:
        self._rows: dict[str, list[TurnRecord]] = {}

    async def append(self, record: TurnRecord) -> TurnRecord:
        self._rows.setdefault(record.bridge_session_id, []).append(record)
        return record

    async def latest(self, bridge_session_id: str) -> TurnRecord | None:
        rows = self._rows.get(bridge_session_id)
        return rows[-1] if rows else None

    async def history(self, bridge_session_id: str) -> list[TurnRecord]:
        return list(self._rows.get(bridge_session_id, ()))


class InMemoryObservationRepository:
    def __init__(self) -> None:
        self._rows: dict[str, list[ObservationRecord]] = {}
        self._keys: set[str] = set()

    async def append(self, record: ObservationRecord) -> bool:
        if record.deduplication_key in self._keys:
            return False
        self._keys.add(record.deduplication_key)
        self._rows.setdefault(record.bridge_session_id, []).append(record)
        return True

    async def page(
        self, bridge_session_id: str, *, after: int = 0, limit: int = 100
    ) -> list[ObservationRecord]:
        rows = [
            row
            for row in self._rows.get(bridge_session_id, ())
            if row.sequence > after
        ]
        rows.sort(key=lambda row: row.sequence)
        return rows[:limit]


class InMemoryCommandLog:
    def __init__(self) -> None:
        self._rows: dict[str, list[Command]] = {}

    async def record(self, bridge_session_id: str, command: Command) -> None:
        self._rows.setdefault(bridge_session_id, []).append(command)

    async def pending(self, bridge_session_id: str) -> list[Command]:
        return list(self._rows.get(bridge_session_id, ()))


class InMemoryDecisionLog:
    def __init__(self) -> None:
        self._rows: dict[str, list[ReconcileDecision]] = {}

    async def record(
        self, bridge_session_id: str, decision: ReconcileDecision
    ) -> None:
        self._rows.setdefault(bridge_session_id, []).append(decision)

    async def history(self, bridge_session_id: str) -> list[ReconcileDecision]:
        return list(self._rows.get(bridge_session_id, ()))


__all__ = [
    "InMemoryCommandLog",
    "InMemoryDecisionLog",
    "InMemoryObservationRepository",
    "InMemorySessionRepository",
    "InMemoryTurnRepository",
]
