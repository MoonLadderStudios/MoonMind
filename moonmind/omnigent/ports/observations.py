"""Append-only observation index repository port.

Source issue: MoonLadderStudios/MoonMind#3711.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Protocol, Sequence, runtime_checkable

from moonmind.omnigent.control_plane.records import ObservationRecord


@runtime_checkable
class ObservationRepositoryPort(Protocol):
    """Narrow interface for the append-only bounded observation index.

    Appends are idempotent on ``(session_id, deduplication_key)`` and reads are
    bounded so an operator diagnostic never materializes the full observation
    history for a long-running session.
    """

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
    ) -> ObservationRecord: ...

    async def list_for_session(
        self,
        session_id: str,
        *,
        observation_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[ObservationRecord]: ...

    async def latest_for_session(
        self,
        session_id: str,
        *,
        observation_types: Optional[Sequence[str]] = None,
    ) -> Optional[ObservationRecord]: ...


__all__ = ["ObservationRepositoryPort"]
