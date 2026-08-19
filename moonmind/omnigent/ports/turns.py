"""Turn-attempt repository port.

Source issue: MoonLadderStudios/MoonMind#3711.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from moonmind.omnigent.control_plane.records import (
    CasResult,
    TurnAttemptRecord,
    TURN_STATE_PREPARED,
)

_UNSET = object()


@runtime_checkable
class TurnRepositoryPort(Protocol):
    """Narrow interface for the turn-attempt aggregate.

    Owns per-turn request idempotency and the fenced turn-state machine. Never
    owns chat-binding authority (that stays on the session aggregate).
    """

    async def create(
        self,
        *,
        turn_attempt_id: str,
        session_id: str,
        idempotency_key: str,
        lineage_kind: str = "instruction",
        step_execution_id: Optional[str] = None,
        parent_turn_attempt_id: Optional[str] = None,
        remediation_of_turn_attempt_id: Optional[str] = None,
        instruction_digest: Optional[str] = None,
        provider_marker: Optional[str] = None,
        state: str = TURN_STATE_PREPARED,
    ) -> TurnAttemptRecord: ...

    async def get(
        self, turn_attempt_id: str
    ) -> Optional[TurnAttemptRecord]: ...

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[TurnAttemptRecord]: ...

    async def list_for_session(
        self, session_id: str
    ) -> list[TurnAttemptRecord]: ...

    async def count_for_session(self, session_id: str) -> int: ...

    async def compare_and_swap_turn(
        self,
        turn_attempt_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        state: Any = _UNSET,
        provider_turn_id: Any = _UNSET,
        provider_item_id: Any = _UNSET,
        terminal_state: Any = _UNSET,
        attempt_outcome: Any = _UNSET,
        terminal_evidence_ref: Any = _UNSET,
    ) -> CasResult: ...

    async def advance_state(
        self,
        turn_attempt_id: str,
        state: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        provider_turn_id: Optional[str] = None,
        provider_item_id: Optional[str] = None,
    ) -> TurnAttemptRecord: ...

    async def mark_terminal(
        self,
        turn_attempt_id: str,
        terminal_state: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        attempt_outcome: Optional[str] = None,
        terminal_evidence_ref: Optional[str] = None,
    ) -> TurnAttemptRecord: ...


__all__ = ["TurnRepositoryPort"]
