"""SQLAlchemy-backed session repository (production adapter).

Implements :class:`SessionRepository` with database-enforced optimistic
concurrency: ``save`` updates the row only when the stored revision matches the
caller's ``expected_revision``, so two writers cannot clobber each other. The
same revision/fencing outcomes are proven against the in-memory adapter by the
shared contract suite.

This adapter owns its own small declarative model rather than importing the
legacy ``OmnigentBridgeSession`` ORM entity; the incremental migration will move
the canonical table behind this port in a later phase (issue
MoonLadderStudios/MoonMind#3711, Phase 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import Integer, String, Text, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from moonmind.omnigent.ports.sessions import (
    SessionRecord,
    SessionRevisionConflict,
)


class OmnigentSessionBase(DeclarativeBase):
    """Declarative base dedicated to the decomposed session repository."""


class OmnigentSessionRow(OmnigentSessionBase):
    __tablename__ = "omnigent_session_repository"

    bridge_session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    omnigent_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


@dataclass(frozen=True, slots=True)
class SqlAlchemySessionRepository:
    """``SessionRepository`` backed by an :class:`AsyncSession`."""

    session: AsyncSession

    async def get(self, bridge_session_id: str) -> SessionRecord | None:
        row = await self.session.get(OmnigentSessionRow, bridge_session_id)
        return _to_record(row) if row is not None else None

    async def create(
        self,
        bridge_session_id: str,
        *,
        status: str,
        omnigent_session_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> SessionRecord:
        existing = await self.session.get(OmnigentSessionRow, bridge_session_id)
        if existing is not None:
            raise SessionRevisionConflict(bridge_session_id, 0, existing.revision)
        row = OmnigentSessionRow(
            bridge_session_id=bridge_session_id,
            status=status,
            revision=1,
            omnigent_session_id=omnigent_session_id,
            metadata_json=json.dumps(dict(metadata or {})),
        )
        self.session.add(row)
        await self.session.flush()
        return _to_record(row)

    async def save(
        self, record: SessionRecord, *, expected_revision: int
    ) -> SessionRecord:
        result = await self.session.execute(
            update(OmnigentSessionRow)
            .where(
                OmnigentSessionRow.bridge_session_id == record.bridge_session_id,
                OmnigentSessionRow.revision == expected_revision,
            )
            .values(
                status=record.status,
                omnigent_session_id=record.omnigent_session_id,
                metadata_json=json.dumps(dict(record.metadata)),
                revision=expected_revision + 1,
            )
        )
        if result.rowcount == 0:
            current = await self.session.get(
                OmnigentSessionRow, record.bridge_session_id
            )
            actual = current.revision if current is not None else 0
            raise SessionRevisionConflict(
                record.bridge_session_id, expected_revision, actual
            )
        await self.session.flush()
        stored = await self.session.get(OmnigentSessionRow, record.bridge_session_id)
        return _to_record(stored)

    async def list_ids(self) -> list[str]:
        rows = await self.session.execute(select(OmnigentSessionRow.bridge_session_id))
        return [value for (value,) in rows.all()]


def _to_record(row: OmnigentSessionRow) -> SessionRecord:
    return SessionRecord(
        bridge_session_id=row.bridge_session_id,
        status=row.status,
        revision=row.revision,
        omnigent_session_id=row.omnigent_session_id,
        metadata=json.loads(row.metadata_json or "{}"),
    )


__all__ = [
    "OmnigentSessionBase",
    "OmnigentSessionRow",
    "SqlAlchemySessionRepository",
]
