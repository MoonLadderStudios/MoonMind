"""Canonical turn-source vocabulary for MoonLadderStudios/MoonMind#3707.

Source kind changes authorization, evidence, and policy. It does not change
the fundamental command, idempotency, fencing, observation, or terminality
model.

The vocabulary is closed and versioned so that new producers cannot invent a
source that bypasses the canonical turn-command boundary. When policy
authorizes same-session reuse, every instruction source preserves one canonical
session, one chat binding, one immutable execution plan, one current runtime
binding and fencing generation, one provider-session attachment, and distinct
turn-attempt identity and evidence for each instruction.
"""

from __future__ import annotations

# Versioned vocabulary identifier. Bumping the version requires an explicit
# migration path for persisted rows and in-flight histories.
TURN_SOURCE_VERSION = 1

TURN_SOURCE_INITIAL = "initial"
TURN_SOURCE_REPOSITORY_CONTINUATION = "repository_continuation"
TURN_SOURCE_REMEDIATION = "remediation"
TURN_SOURCE_WORKFLOW_CHAT = "workflow_chat"
TURN_SOURCE_STEERING = "steering"
TURN_SOURCE_APPROVAL_RESPONSE = "approval_response"
TURN_SOURCE_CHECKPOINT_RESUME = "checkpoint_resume"
TURN_SOURCE_LINKED_BRANCH = "linked_branch"

# Legacy continuation alias that predates the canonical repository_continuation
# name. Kept as a canonical member for rolling-upgrade compatibility so that
# rows written before the vocabulary landed still read as canonical without a
# backfill. New producers use ``repository_continuation``.
TURN_SOURCE_CONTINUATION = "continuation"

# Canonical closed set. Every follow-up producer must use one of these. The
# set covers the issue-required eight plus the legacy ``continuation`` alias
# so that existing durable rows remain canonical across the cutover.
TURN_SOURCES: frozenset[str] = frozenset(
    {
        TURN_SOURCE_INITIAL,
        TURN_SOURCE_REPOSITORY_CONTINUATION,
        TURN_SOURCE_CONTINUATION,
        TURN_SOURCE_REMEDIATION,
        TURN_SOURCE_WORKFLOW_CHAT,
        TURN_SOURCE_STEERING,
        TURN_SOURCE_APPROVAL_RESPONSE,
        TURN_SOURCE_CHECKPOINT_RESUME,
        TURN_SOURCE_LINKED_BRANCH,
    }
)

# Backwards-compatibility aliases that existed before #3707. They are
# accepted on read/write and normalized to the canonical member so historical
# rows, tests, and in-flight payloads remain valid while new code converges on
# the canonical names. ``continuation`` is intentionally *not* an alias
# because it is a canonical member for compatibility; its canonical
# normalized form is ``repository_continuation`` only via explicit mapping in
# ``normalize_turn_source`` for callers that ask for it.
_TURN_SOURCE_ALIASES: dict[str, str] = {
    "instruction": TURN_SOURCE_INITIAL,
    "checkpoint_resume": TURN_SOURCE_CHECKPOINT_RESUME,
    "approval": TURN_SOURCE_APPROVAL_RESPONSE,
    "linked_branch": TURN_SOURCE_LINKED_BRANCH,
}

# All values the persistence layer accepts (canonical + aliases) so that
# a rolling upgrade does not reject a row written by the previous version.
ACCEPTED_TURN_SOURCES: frozenset[str] = frozenset(
    TURN_SOURCES | frozenset(_TURN_SOURCE_ALIASES.keys()) | frozenset(_TURN_SOURCE_ALIASES.values())
)


def normalize_turn_source(value: str) -> str:
    """Return the canonical member for a supplied source.

    Accepted aliases are mapped to their canonical member. Canonical members
    are returned unchanged. Unknown values raise ``ValueError`` so that an
    invented source cannot bypass the turn-command boundary.
    """

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("turn source must be a non-empty string")
    lowered = raw.lower()
    # Canonical members are already lower-case; aliases cover legacy casing.
    if lowered in TURN_SOURCES:
        return lowered
    if lowered in _TURN_SOURCE_ALIASES:
        return _TURN_SOURCE_ALIASES[lowered]
    # Preserve exact canonical spelling for callers that already use it.
    if raw in TURN_SOURCES:
        return raw
    if raw in _TURN_SOURCE_ALIASES:
        return _TURN_SOURCE_ALIASES[raw]
    raise ValueError(f"unknown turn source {value!r}; expected one of {sorted(TURN_SOURCES)}")


def ensure_valid_turn_source(value: str) -> str:
    """Validate and normalize a turn source, failing closed on unknown values."""

    return normalize_turn_source(value)


def is_canonical_turn_source(value: str) -> bool:
    """Return True when ``value`` is a canonical member (not an alias)."""

    try:
        canonical = normalize_turn_source(value)
    except ValueError:
        return False
    return canonical in TURN_SOURCES and str(value).strip().lower() == canonical


__all__ = [
    "ACCEPTED_TURN_SOURCES",
    "TURN_SOURCE_APPROVAL_RESPONSE",
    "TURN_SOURCE_CHECKPOINT_RESUME",
    "TURN_SOURCE_INITIAL",
    "TURN_SOURCE_LINKED_BRANCH",
    "TURN_SOURCE_REMEDIATION",
    "TURN_SOURCE_REPOSITORY_CONTINUATION",
    "TURN_SOURCE_STEERING",
    "TURN_SOURCE_VERSION",
    "TURN_SOURCE_WORKFLOW_CHAT",
    "TURN_SOURCES",
    "ensure_valid_turn_source",
    "is_canonical_turn_source",
    "normalize_turn_source",
]
