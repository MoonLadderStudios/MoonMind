"""Manage the lifecycle of a single turn (agent response) over ports."""

from __future__ import annotations

from moonmind.omnigent.domain.turn_state import TurnStatus, is_terminal_turn_status
from moonmind.omnigent.ports.turns import TurnRecord, TurnRepository


class ManageTurn:
    """Append turn records and answer terminal/liveness questions."""

    def __init__(self, turns: TurnRepository) -> None:
        self._turns = turns

    async def begin(self, bridge_session_id: str, turn_id: str) -> TurnRecord:
        latest = await self._turns.latest(bridge_session_id)
        sequence = (latest.sequence + 1) if latest else 1
        return await self._turns.append(
            TurnRecord(
                bridge_session_id=bridge_session_id,
                turn_id=turn_id,
                status=TurnStatus.RUNNING,
                sequence=sequence,
            )
        )

    async def has_open_turn(self, bridge_session_id: str) -> bool:
        latest = await self._turns.latest(bridge_session_id)
        if latest is None:
            return False
        return not is_terminal_turn_status(latest.status.value)


__all__ = ["ManageTurn"]
