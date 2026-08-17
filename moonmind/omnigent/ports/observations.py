"""Observation repository port: durable, deduplicated provider observations.

Observations are normalized provider events. The repository owns durable
append-with-deduplication by canonical deduplication key; interpretation of the
observation is a domain concern, not this port's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    bridge_session_id: str
    sequence: int
    normalized_status: str
    deduplication_key: str
    payload: Mapping[str, Any]


@runtime_checkable
class ObservationRepository(Protocol):
    async def append(self, record: ObservationRecord) -> bool:
        """Append an observation. Return ``False`` if the key already exists."""
        ...

    async def page(
        self,
        bridge_session_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> Sequence[ObservationRecord]: ...


__all__ = ["ObservationRecord", "ObservationRepository"]
