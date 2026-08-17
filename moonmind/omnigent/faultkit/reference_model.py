"""Compact reference state machine, independent of the production reconciler.

Owned by MoonLadderStudios/MoonMind#3709.

This module models the *canonical* Omnigent session lifecycle as an oracle. It
reads the provider's authoritative ground truth (durable side effects and the
latest observed snapshot) and derives what the correct convergent state must be,
regardless of dropped responses, duplicated events, or reordered observations.

It deliberately does **not** call the production reconciler or repositories. It
knows only the allowed transitions, so a divergence between the reconciler under
test and this oracle is evidence of a reconciliation defect, not a shared bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SessionState(str, Enum):
    ABSENT = "absent"
    CREATED = "created"
    IDLE = "idle"
    RUNNING = "running"
    TERMINAL = "terminal"


class TurnState(str, Enum):
    NONE = "none"
    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CleanupState(str, Enum):
    NONE = "none"
    PENDING = "pending"
    DONE = "done"


class TerminalKind(str, Enum):
    """Distinct terminal owners; these are never conflated (property #6)."""

    NONE = "none"
    TURN = "turn"
    SESSION = "session"
    AGENT_RUN = "agent_run"
    WORKFLOW = "workflow"
    CLEANUP = "cleanup"
    REMEDIATION = "remediation"


_TERMINAL_TURN_STATES = {TurnState.COMPLETED, TurnState.FAILED}

#: Allowed forward transitions for the turn-attempt lifecycle.
_ALLOWED_TURN_TRANSITIONS: dict[TurnState, set[TurnState]] = {
    TurnState.NONE: {TurnState.ACCEPTED},
    TurnState.ACCEPTED: {TurnState.RUNNING, TurnState.COMPLETED, TurnState.FAILED},
    TurnState.RUNNING: {TurnState.COMPLETED, TurnState.FAILED},
    TurnState.COMPLETED: set(),
    TurnState.FAILED: set(),
}


@dataclass
class SessionView:
    """Canonical durable view shared by the oracle and the reconciler."""

    session_state: SessionState = SessionState.ABSENT
    turn_state: TurnState = TurnState.NONE
    session_revision: int = 0
    turn_revision: int = 0
    generation: int = 0
    lease_held: bool = False
    lease_consumers: int = 0
    cleanup_state: CleanupState = CleanupState.NONE
    terminal_kind: TerminalKind = TerminalKind.NONE
    terminal_evidence_retained: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "sessionState": self.session_state.value,
            "turnState": self.turn_state.value,
            "sessionRevision": self.session_revision,
            "turnRevision": self.turn_revision,
            "generation": self.generation,
            "leaseHeld": self.lease_held,
            "leaseConsumers": self.lease_consumers,
            "cleanupState": self.cleanup_state.value,
            "terminalKind": self.terminal_kind.value,
            "terminalEvidenceRetained": self.terminal_evidence_retained,
        }


def turn_transition_allowed(current: TurnState, nxt: TurnState) -> bool:
    if current == nxt:
        return True
    return nxt in _ALLOWED_TURN_TRANSITIONS.get(current, set())


@dataclass
class ReferenceModel:
    """The oracle: derives the correct convergent state from provider truth."""

    view: SessionView = field(default_factory=SessionView)

    def observe_snapshot_truth(self, snapshot: dict[str, Any]) -> None:
        """Advance the oracle from an authoritative provider snapshot."""
        session = str(snapshot.get("sessionState", ""))
        turn = str(snapshot.get("turnState", ""))
        if session in {"created", "idle"} and self.view.session_state in {
            SessionState.ABSENT,
            SessionState.CREATED,
        }:
            self.view.session_state = SessionState.IDLE
        if session == "running":
            self.view.session_state = SessionState.RUNNING
        if turn == "completed":
            self._advance_turn(TurnState.COMPLETED)
        elif turn == "failed":
            self._advance_turn(TurnState.FAILED)
        elif turn == "running":
            self._advance_turn(TurnState.RUNNING)

    def observe_accept_truth(self) -> None:
        """A durable accepted-turn side effect occurred (provider ground truth)."""
        self._advance_turn(TurnState.ACCEPTED)
        if self.view.session_state in {SessionState.ABSENT, SessionState.CREATED}:
            self.view.session_state = SessionState.IDLE

    def observe_created_truth(self) -> None:
        if self.view.session_state == SessionState.ABSENT:
            self.view.session_state = SessionState.CREATED

    def observe_deleted_truth(self) -> None:
        # Live resource removed, but terminal evidence remains readable.
        self.view.terminal_evidence_retained = True

    def finalize(self) -> SessionView:
        """Collapse to the expected terminal, retaining evidence when due."""
        if self.view.turn_state in _TERMINAL_TURN_STATES:
            self.view.session_state = SessionState.TERMINAL
            self.view.terminal_kind = (
                TerminalKind.SESSION
                if self.view.turn_state == TurnState.COMPLETED
                else TerminalKind.SESSION
            )
            self.view.terminal_evidence_retained = True
        return self.view

    def _advance_turn(self, nxt: TurnState) -> None:
        if turn_transition_allowed(self.view.turn_state, nxt):
            if nxt != self.view.turn_state:
                self.view.turn_state = nxt
                self.view.turn_revision += 1


__all__ = [
    "SessionState",
    "TurnState",
    "CleanupState",
    "TerminalKind",
    "SessionView",
    "ReferenceModel",
    "turn_transition_allowed",
]
