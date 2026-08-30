"""The closed, versioned vocabulary of canonical Omnigent turn sources.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 7/11]).

Every instruction that reaches an existing Omnigent session names exactly one
source. The source changes authorization, evidence, and policy; it never changes
the command, idempotency, fencing, observation, or terminality model. The
vocabulary is therefore closed and versioned: an unrecognized source fails
closed instead of being coerced by substring matching.
"""

from __future__ import annotations

from enum import Enum

from .records import OmnigentControlPlaneError


#: Bumped when the closed source vocabulary itself changes.
TURN_SOURCE_VOCABULARY_VERSION = 1

#: The durable ``omnigent_turn_attempts.lineage_kind`` column width.
TURN_SOURCE_MAX_LENGTH = 32


class TurnSource(str, Enum):
    """Closed vocabulary of instruction sources for one canonical turn."""

    #: The first instruction that establishes the canonical session.
    INITIAL = "initial"
    #: Bounded same-session continuation driven by missing repository output.
    REPOSITORY_CONTINUATION = "repository_continuation"
    #: Typed remediation of a prior attempt within the same authority.
    REMEDIATION = "remediation"
    #: A native Workflow Chat message (HTTP or WebSocket).
    WORKFLOW_CHAT = "workflow_chat"
    #: Interrupt/stop/clear style steering of an in-flight turn.
    STEERING = "steering"
    #: A human or controller response to a pending elicitation/approval.
    APPROVAL_RESPONSE = "approval_response"
    #: Resume of a durable checkpoint into the same canonical session.
    CHECKPOINT_RESUME = "checkpoint_resume"
    #: Work submitted by a linked branch workflow. A linked branch that policy
    #: sends to a *new* canonical session records its own ``INITIAL`` turn there;
    #: this source is for a linked branch that targets an existing session.
    LINKED_BRANCH = "linked_branch"


TURN_SOURCES: frozenset[str] = frozenset(member.value for member in TurnSource)

#: Every value fits the durable column; asserted here so a rename cannot silently
#: truncate durable lineage authority.
assert all(len(value) <= TURN_SOURCE_MAX_LENGTH for value in TURN_SOURCES)


class UnknownTurnSourceError(OmnigentControlPlaneError):
    """Raised when a turn names a source outside the closed vocabulary."""

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(
            f"{value!r} is not a supported canonical turn source "
            f"(supported: {sorted(TURN_SOURCES)})"
        )


def coerce_turn_source(value: object) -> TurnSource:
    """Return the closed :class:`TurnSource` for ``value`` or fail closed."""

    if isinstance(value, TurnSource):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TURN_SOURCES:
            return TurnSource(normalized)
    raise UnknownTurnSourceError(value)


__all__ = [
    "TURN_SOURCES",
    "TURN_SOURCE_MAX_LENGTH",
    "TURN_SOURCE_VOCABULARY_VERSION",
    "TurnSource",
    "UnknownTurnSourceError",
    "coerce_turn_source",
]
