"""Recovery coordinator: bounded continuation preserving the same workspace.

On a recoverable failure the same authoritative workspace and immutable inputs
are preserved for a bounded number of continuation attempts before cleanup is
allowed to release authority (see the resilience principle in AGENTS.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from moonmind.omnigent.domain.session_state import is_terminal_status
from moonmind.omnigent.ports.sessions import SessionRepository


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    should_continue: bool
    attempts_remaining: int


class ManageRecovery:
    def __init__(self, sessions: SessionRepository, *, max_attempts: int = 3) -> None:
        self._sessions = sessions
        self._max_attempts = max_attempts

    async def evaluate(
        self, bridge_session_id: str, *, attempts_used: int
    ) -> RecoveryDecision:
        record = await self._sessions.get(bridge_session_id)
        remaining = max(0, self._max_attempts - attempts_used)
        if record is None or is_terminal_status(record.status):
            return RecoveryDecision(should_continue=False, attempts_remaining=remaining)
        return RecoveryDecision(
            should_continue=remaining > 0, attempts_remaining=remaining
        )


__all__ = ["ManageRecovery", "RecoveryDecision"]
