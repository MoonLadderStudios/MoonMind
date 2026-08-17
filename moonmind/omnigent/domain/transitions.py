"""Pure session lifecycle transition rules.

The canonical transition table for a bridge session's coalesced status. A
terminal status is absorbing: once a session is ``completed``/``failed``/
``canceled``/``timed_out`` it never transitions again, which is the invariant
reconciliation relies on to make terminal evidence authoritative.
"""

from __future__ import annotations

from moonmind.omnigent.domain.session_state import (
    LIFECYCLE_STATUSES,
    STATUS_ACTIVE,
    STATUS_CREATING,
    STATUS_DECLARED,
    TERMINAL_STATUSES,
    coalesce_session_status,
    is_terminal_status,
)

# Allowed forward transitions between coalesced (non-normalized) statuses. Every
# non-terminal status may advance to ``active`` or to any terminal status; the
# lifecycle prefix (declared -> creating -> active) is ordered.
_ALLOWED: dict[str, frozenset[str]] = {
    STATUS_DECLARED: frozenset({STATUS_CREATING, STATUS_ACTIVE, *TERMINAL_STATUSES}),
    STATUS_CREATING: frozenset({STATUS_ACTIVE, *TERMINAL_STATUSES}),
    STATUS_ACTIVE: frozenset({STATUS_ACTIVE, *TERMINAL_STATUSES}),
}


def _coalesce_current(current: str) -> str:
    """Coalesce a current status that may be lifecycle, terminal, or normalized."""

    raw = str(current).strip().lower()
    if raw in LIFECYCLE_STATUSES or raw in TERMINAL_STATUSES:
        return raw
    return coalesce_session_status(raw)


def can_transition(current: str, target: str) -> bool:
    """Return whether a coalesced status may advance to ``target``.

    Terminal statuses are absorbing (no outgoing transitions). Re-entering the
    same non-terminal status is allowed (idempotent reconciliation). Unknown
    current statuses reject all transitions.
    """

    current_c = _coalesce_current(current)
    if is_terminal_status(current_c):
        return False
    if current_c == target:
        return True
    return target in _ALLOWED.get(current_c, frozenset())


def next_status(current: str, normalized: str) -> str:
    """Compute the next coalesced status from a normalized observation.

    Coalesces ``normalized`` and applies the terminal-absorbing invariant: a
    session already terminal keeps its terminal status regardless of later
    non-terminal observations.
    """

    current_c = _coalesce_current(current)
    if is_terminal_status(current_c):
        return current_c
    return coalesce_session_status(normalized)


__all__ = ["can_transition", "next_status"]
