"""Append-only reconciliation-decision repository port.

Source issue: MoonLadderStudios/MoonMind#3711.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

from moonmind.omnigent.control_plane.records import DecisionRecord


@runtime_checkable
class DecisionRepositoryPort(Protocol):
    """Narrow interface for the append-only reconciliation-decision journal.

    The durable decision journal *is* the per-session/per-reason detection
    persistence used by stuck-state escalation, so no separate counter exists.
    """

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
    ) -> DecisionRecord: ...

    async def list_for_session(
        self, session_id: str
    ) -> list[DecisionRecord]: ...

    async def latest_for_session(
        self, session_id: str
    ) -> Optional[DecisionRecord]: ...

    async def count_for_session_reason(
        self, session_id: str, reason_code: str
    ) -> int: ...


__all__ = ["DecisionRepositoryPort"]
