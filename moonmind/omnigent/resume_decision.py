"""One typed decision boundary for resuming an existing Omnigent session.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 7/11]).

Checkpoint recovery, turn admission, and the session supervisor previously
expressed "can this instruction reuse the prior session?" three different ways:
a two-value recovery-mode enum, untyped ``recoveryAction`` string literals, and
implicit success/failure. This module owns the single closed vocabulary all of
them share.

The module is deliberately a leaf: it imports nothing from the control-plane
package (which pulls in SQLAlchemy models) so provider-neutral checkpoint code,
Temporal activities, and the control plane can all depend on it without an
import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Sequence


#: Bumped when the closed decision vocabulary itself changes.
RESUME_DECISION_VOCABULARY_VERSION = 1

#: Bound the reason list so a decision stays compact enough for Temporal history.
MAX_REASON_CODES = 20
MAX_REASON_CODE_LENGTH = 120


class SessionResumeDecision(str, Enum):
    """Closed outcome vocabulary for reusing an existing canonical session.

    * ``LIVE_REATTACH`` -- current runtime authority is intact; the instruction
      attaches to the running provider session.
    * ``COLD_RESTORE`` -- runtime authority is gone but artifact-backed
      checkpoint/workspace evidence can rebuild an equivalent session.
    * ``BRANCH_REQUIRED`` -- immutable execution authority changed; a branch
      gets its own canonical session and the prior session is never mutated.
    * ``NEW_SESSION_REQUIRED`` -- the prior session cannot be branched from
      either (no branch-capable evidence, or it is durably terminal), so the
      caller must admit a brand new session.
    * ``RESUME_UNAVAILABLE`` -- authority evidence is missing or ambiguous; fail
      closed rather than guessing.
    """

    LIVE_REATTACH = "live_reattach"
    COLD_RESTORE = "cold_restore"
    BRANCH_REQUIRED = "branch_required"
    NEW_SESSION_REQUIRED = "new_session_required"
    RESUME_UNAVAILABLE = "resume_unavailable"


SESSION_RESUME_DECISIONS: frozenset[str] = frozenset(
    member.value for member in SessionResumeDecision
)

#: Decisions that keep working inside the *same* canonical session.
SAME_SESSION_DECISIONS: frozenset[SessionResumeDecision] = frozenset(
    {SessionResumeDecision.LIVE_REATTACH, SessionResumeDecision.COLD_RESTORE}
)

#: Decisions that require a *different* canonical session before any mutation.
NEW_AUTHORITY_DECISIONS: frozenset[SessionResumeDecision] = frozenset(
    {
        SessionResumeDecision.BRANCH_REQUIRED,
        SessionResumeDecision.NEW_SESSION_REQUIRED,
    }
)


class UnknownResumeDecisionError(ValueError):
    """Raised when a value is outside the closed resume-decision vocabulary."""

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(
            f"{value!r} is not a supported session resume decision "
            f"(supported: {sorted(SESSION_RESUME_DECISIONS)})"
        )


def coerce_resume_decision(value: object) -> SessionResumeDecision:
    """Return the closed decision for ``value`` or fail closed."""

    if isinstance(value, SessionResumeDecision):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in SESSION_RESUME_DECISIONS:
            return SessionResumeDecision(normalized)
    raise UnknownResumeDecisionError(value)


def _bounded_reasons(reasons: Iterable[str] | None) -> tuple[str, ...]:
    if not reasons:
        return ()
    bounded: list[str] = []
    for reason in reasons:
        text = str(reason).strip()[:MAX_REASON_CODE_LENGTH]
        if text and text not in bounded:
            bounded.append(text)
        if len(bounded) >= MAX_REASON_CODES:
            break
    return tuple(bounded)


@dataclass(frozen=True, slots=True)
class SessionResumeOutcome:
    """A resume decision plus its bounded, non-sensitive rationale."""

    decision: SessionResumeDecision
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision", coerce_resume_decision(self.decision)
        )
        object.__setattr__(
            self, "reason_codes", _bounded_reasons(self.reason_codes)
        )

    @property
    def same_session(self) -> bool:
        """True when the instruction may keep using the prior canonical session."""

        return self.decision in SAME_SESSION_DECISIONS

    @property
    def requires_new_authority(self) -> bool:
        """True when a branch or brand-new canonical session is required."""

        return self.decision in NEW_AUTHORITY_DECISIONS

    def as_payload(self) -> dict[str, object]:
        """Return the compact, history-safe projection of this decision."""

        return {
            "recoveryAction": self.decision.value,
            "reasonCodes": list(self.reason_codes),
        }

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, object] | None
    ) -> "SessionResumeOutcome":
        """Rehydrate a decision from its compact projection, failing closed."""

        if not isinstance(payload, Mapping):
            raise UnknownResumeDecisionError(payload)
        raw_reasons = payload.get("reasonCodes")
        reasons: Sequence[object]
        if isinstance(raw_reasons, (list, tuple)):
            reasons = raw_reasons
        else:
            reasons = ()
        return cls(
            decision=coerce_resume_decision(payload.get("recoveryAction")),
            reason_codes=tuple(str(item) for item in reasons),
        )


__all__ = [
    "MAX_REASON_CODES",
    "MAX_REASON_CODE_LENGTH",
    "NEW_AUTHORITY_DECISIONS",
    "RESUME_DECISION_VOCABULARY_VERSION",
    "SAME_SESSION_DECISIONS",
    "SESSION_RESUME_DECISIONS",
    "SessionResumeDecision",
    "SessionResumeOutcome",
    "UnknownResumeDecisionError",
    "coerce_resume_decision",
]
