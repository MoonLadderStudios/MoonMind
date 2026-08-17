"""Decision log port: durable record of reconciliation decisions."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from moonmind.omnigent.domain.decisions import ReconcileDecision


@runtime_checkable
class DecisionLog(Protocol):
    async def record(
        self, bridge_session_id: str, decision: ReconcileDecision
    ) -> None: ...

    async def history(
        self, bridge_session_id: str
    ) -> Sequence[ReconcileDecision]: ...


__all__ = ["DecisionLog"]
