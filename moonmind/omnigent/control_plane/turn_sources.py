"""Closed, versioned source vocabulary for canonical turn commands.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane] Route all
continuations, remediation, checkpoints, and chat through canonical sessions).

Each follow-up instruction shares the same durable session/turn/command
authority. The ``lineage_kind`` (turn source) only changes authorization,
evidence, and policy framing — it never changes the fundamental command,
idempotency, fencing, observation, or terminality model.  The vocabulary is
closed and versioned so that an unknown source fails closed rather than being
interpreted as an open-ended string.

The set deliberately covers the producers enumerated in #3707 and includes the
initial bootstrap kind for the first turn.
"""

from __future__ import annotations

from typing import Final

# Versioned schema identifier for the vocabulary.  Stored alongside the source
# value in derived evidence where a future breaking expansion needs to be
# distinguished at read time.
TURN_SOURCE_SCHEMA: Final[str] = "moonmind.omnigent-turn-source.v1"
TURN_SOURCE_VERSION: Final[str] = "v1"

# Closed vocabulary required by #3707 — every production caller that can create
# or submit follow-up Omnigent work must map to one of these kinds.  ``initial``
# is the bootstrap turn; the remaining seven are the follow-up producers.
TURN_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {
        "initial",
        "repository_continuation",
        "remediation",
        "workflow_chat",
        "steering",
        "approval_response",
        "checkpoint_resume",
        "linked_branch",
    }
)

# Backwards-compatible alias set: values that older rows or callers may still
# emit but which canonically correspond to one of the closed kinds.  The alias
# table is *not* an extension of the vocabulary — it only exists so that
# strict validation can accept and normalize legacy storage without widening
# the new contract.
_TURN_SOURCE_ALIASES: Final[dict[str, str]] = {
    # generic controller-issued continuations predate the typed vocabulary
    "continuation": "repository_continuation",
    # legacy approval naming
    "approval": "approval_response",
    "elicitation": "approval_response",
    # branch naming variance
    "branch": "linked_branch",
    "linked": "linked_branch",
    # checkpoint naming variance
    "checkpoint": "checkpoint_resume",
    "checkpoint_branch": "checkpoint_resume",
}

# Heuristic classification for free-form ``command_type`` strings that have not
# yet been migrated to pass an explicit source.  The mapping is intentionally
# narrow and deterministic: ambiguous inputs collapse to
# ``repository_continuation`` rather than silently creating a new kind.
_COMMAND_TYPE_HINTS: Final[tuple[tuple[str, str], ...]] = (
    ("remediation", "remediation"),
    ("workflow_chat", "workflow_chat"),
    ("workflow-chat", "workflow_chat"),
    ("chat", "workflow_chat"),
    ("message", "workflow_chat"),
    ("steering", "steering"),
    ("steer", "steering"),
    ("interrupt", "steering"),
    ("approval_response", "approval_response"),
    ("approval", "approval_response"),
    ("elicitation", "approval_response"),
    ("checkpoint_resume", "checkpoint_resume"),
    ("checkpoint", "checkpoint_resume"),
    ("linked_branch", "linked_branch"),
    ("linked", "linked_branch"),
    ("repository_continuation", "repository_continuation"),
)


def is_valid_turn_source(value: str) -> bool:
    """Return True when ``value`` is exactly one of the closed kinds."""

    return value in TURN_SOURCE_KINDS


def normalize_turn_source(value: str) -> str:
    """Normalize an alias to its canonical kind.

    Unknown values are returned unchanged so that :func:`validate_turn_source`
    can fail closed with an actionable message rather than collapsing silently
    to a default.
    """

    candidate = str(value or "").strip()
    if not candidate:
        return candidate
    if candidate in TURN_SOURCE_KINDS:
        return candidate
    if candidate in _TURN_SOURCE_ALIASES:
        return _TURN_SOURCE_ALIASES[candidate]
    normalized = candidate.strip().lower().replace(".", "_").replace("-", "_")
    if normalized in TURN_SOURCE_KINDS:
        return normalized
    if normalized in _TURN_SOURCE_ALIASES:
        return _TURN_SOURCE_ALIASES[normalized]
    return candidate


def validate_turn_source(value: str) -> str:
    """Validate and normalize ``value``; raise ValueError when unknown."""

    normalized = normalize_turn_source(value)
    if normalized not in TURN_SOURCE_KINDS:
        allowed = ", ".join(sorted(TURN_SOURCE_KINDS))
        raise ValueError(
            f"unknown turn source {value!r} (normalized {normalized!r}); "
            f"expected one of: {allowed} ({TURN_SOURCE_SCHEMA})"
        )
    return normalized


def turn_source_for_command_type(command_type: str) -> str:
    """Derive a closed turn-source from a free-form command type.

    This is the migration bridge for callers that have not yet been updated to
    pass an explicit source.  Every production follow-up path should eventually
    call :func:`validate_turn_source` directly, but until then the heuristic
    preserves one canonical mapping so that no alternate authority is invented.
    """

    normalized = str(command_type or "").strip().lower().replace(".", "_").replace("-", "_")
    for hint, kind in _COMMAND_TYPE_HINTS:
        if hint in normalized:
            return kind
    # ``initial`` is only ever used for the bootstrap turn created when the
    # deterministic session itself is converged.  Generic follow-up work defaults
    # to repository continuation rather than inventing a new kind.
    return "repository_continuation"


__all__ = [
    "TURN_SOURCE_KINDS",
    "TURN_SOURCE_SCHEMA",
    "TURN_SOURCE_VERSION",
    "is_valid_turn_source",
    "normalize_turn_source",
    "turn_source_for_command_type",
    "validate_turn_source",
]
