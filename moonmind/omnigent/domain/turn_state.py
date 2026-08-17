"""Canonical Omnigent turn (agent response) state vocabulary.

A turn is one provider response/agent exchange within a session. Turn state is a
projection of normalized statuses onto the lifecycle of a single response, kept
separate from session state so a session may outlive many terminal turns.
"""

from __future__ import annotations

from enum import Enum


class TurnStatus(str, Enum):
    """Lifecycle of a single Omnigent turn."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    INTERVENTION_REQUESTED = "intervention_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"


TERMINAL_TURN_STATUSES: frozenset[str] = frozenset(
    {
        TurnStatus.COMPLETED.value,
        TurnStatus.FAILED.value,
        TurnStatus.CANCELED.value,
        TurnStatus.TIMED_OUT.value,
    }
)


def is_terminal_turn_status(status: str | None) -> bool:
    """Return whether a turn status is terminal."""

    return str(status or "").strip().lower() in TERMINAL_TURN_STATUSES


__all__ = ["TERMINAL_TURN_STATUSES", "TurnStatus", "is_terminal_turn_status"]
