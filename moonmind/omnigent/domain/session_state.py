"""Canonical Omnigent session status vocabulary and coalescence.

Single source of truth for the bridge session status model documented in
``docs/Omnigent/OmnigentBridge.md`` §7.1. ``bridge_store.coalesce_bridge_status``
delegates here so the terminal / lifecycle / non-terminal vocabulary and the
``timed_out`` vs ``failed`` distinction are defined exactly once.
"""

from __future__ import annotations

from enum import Enum

from moonmind.omnigent.domain.compatibility import canonicalize_provider_status

# Bridge lifecycle states owned by the bridge before the provider reports a
# normalized status (§7.1). ``active`` is the coalesced non-terminal value.
STATUS_DECLARED = "declared"
STATUS_CREATING = "creating"
STATUS_ACTIVE = "active"

LIFECYCLE_STATUSES: frozenset[str] = frozenset(
    {STATUS_DECLARED, STATUS_CREATING, STATUS_ACTIVE}
)

# Terminal normalized statuses pass straight through to the session status.
# ``timed_out`` is kept distinct from ``failed`` (§7.1/§17).
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "canceled", "timed_out"}
)

# Non-terminal normalized statuses; all coalesce to ``active`` (§7.1).
NON_TERMINAL_NORMALIZED_STATUSES: frozenset[str] = frozenset(
    {
        "created",
        "launching",
        "provisioning",
        "running",
        "waiting",
        "idle",
        "awaiting_approval",
        "intervention_requested",
    }
)


class SessionStatus(str, Enum):
    """Canonical coalesced bridge session status values."""

    DECLARED = STATUS_DECLARED
    CREATING = STATUS_CREATING
    ACTIVE = STATUS_ACTIVE
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"


def is_terminal_status(status: str | None) -> bool:
    """Return whether a coalesced/normalized status is terminal."""

    return str(status or "").strip().lower() in TERMINAL_STATUSES


def coalesce_session_status(value: str) -> str:
    """Coalesce a normalized/lifecycle status into a bridge session status.

    Non-terminal normalized statuses collapse to ``active``; terminal statuses
    pass through unchanged; bridge lifecycle states pass through. ``timed_out``
    is kept distinct from ``failed`` (§7.1/§17). Provider-native aliases are
    canonicalized first. Unknown values fail fast rather than silently degrading
    (Compatibility Policy).
    """

    raw = canonicalize_provider_status(value)
    if raw in TERMINAL_STATUSES:
        return raw
    if raw in LIFECYCLE_STATUSES:
        return raw
    if raw in NON_TERMINAL_NORMALIZED_STATUSES:
        return STATUS_ACTIVE
    raise ValueError(
        f"Unsupported normalized status for bridge coalescence: {value!r}"
    )


__all__ = [
    "LIFECYCLE_STATUSES",
    "NON_TERMINAL_NORMALIZED_STATUSES",
    "STATUS_ACTIVE",
    "STATUS_CREATING",
    "STATUS_DECLARED",
    "SessionStatus",
    "TERMINAL_STATUSES",
    "coalesce_session_status",
    "is_terminal_status",
]
