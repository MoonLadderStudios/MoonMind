"""Domain decisions: the typed outcome of a reconciliation.

A :class:`ReconcileDecision` is what the domain concludes from an observation and
the current session state: the next coalesced status, whether the session is now
terminal, an optional failure class, and the ordered commands an adapter should
execute. It is the pure boundary between "interpret provider events" and "perform
side effects".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from moonmind.omnigent.domain.commands import Command
from moonmind.schemas.agent_runtime_models import FailureClass


@dataclass(frozen=True, slots=True)
class ReconcileDecision:
    """Typed result of reconciling one observation against session state."""

    next_status: str
    is_terminal: bool
    failure_class: FailureClass | None = None
    commands: Sequence[Command] = field(default_factory=tuple)
    diagnostic: str | None = None


__all__ = ["ReconcileDecision"]
