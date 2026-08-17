"""Turn repository port: persistence of per-turn (agent response) state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from moonmind.omnigent.domain.turn_state import TurnStatus


@dataclass(frozen=True, slots=True)
class TurnRecord:
    bridge_session_id: str
    turn_id: str
    status: TurnStatus
    sequence: int


@runtime_checkable
class TurnRepository(Protocol):
    async def append(self, record: TurnRecord) -> TurnRecord: ...

    async def latest(self, bridge_session_id: str) -> TurnRecord | None: ...

    async def history(self, bridge_session_id: str) -> Sequence[TurnRecord]: ...


__all__ = ["TurnRecord", "TurnRepository"]
