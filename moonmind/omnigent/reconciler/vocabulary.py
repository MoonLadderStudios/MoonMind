"""Closed, versioned vocabularies for the Omnigent lifecycle reconciler.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

This module owns every *closed* enumeration the reducer reasons over:

* durable lifecycle phases and desired lifecycle;
* turn submission states;
* the closed decision action vocabulary;
* the stable reason-code vocabulary;
* normalization of externally-sourced provider status and compatibility tokens.

Provider status and compatibility normalization return an explicit
``UNKNOWN`` classification instead of guessing, so the reducer can fail closed
(invariant 6). No I/O is performed here.
"""

from __future__ import annotations

from enum import Enum

from moonmind.omnigent.reconciler.versions import REASON_CODE_VERSION


class DesiredLifecycle(str, Enum):
    """Operator intent target for the canonical session."""

    RUN = "run"
    TERMINATED = "terminated"


class DurablePhase(str, Enum):
    """Durable lifecycle phase of the canonical session.

    The ordered ladder (``PENDING`` -> ``CLOSED``) is monotonic: a stale or
    contradictory observation can never move the durable phase backward
    (invariant 5). ``QUARANTINED`` and ``FAILED`` are off-ladder holding states.
    """

    PENDING = "pending"
    PROFILE_LEASED = "profile_leased"
    HOST_READY = "host_ready"
    PROVIDER_SESSION_OPEN = "provider_session_open"
    TURN_IN_FLIGHT = "turn_in_flight"
    TERMINAL_RECORDED = "terminal_recorded"
    EVIDENCE_HARVESTED = "evidence_harvested"
    CLEANUP_STARTED = "cleanup_started"
    LEASES_RELEASED = "leases_released"
    CLOSED = "closed"
    QUARANTINED = "quarantined"
    FAILED = "failed"


# Monotonic rank of the on-ladder phases. Off-ladder holds are absent on
# purpose: they are not comparable positions on the forward ladder.
_PHASE_RANK: dict[DurablePhase, int] = {
    DurablePhase.PENDING: 0,
    DurablePhase.PROFILE_LEASED: 1,
    DurablePhase.HOST_READY: 2,
    DurablePhase.PROVIDER_SESSION_OPEN: 3,
    DurablePhase.TURN_IN_FLIGHT: 4,
    DurablePhase.TERMINAL_RECORDED: 5,
    DurablePhase.EVIDENCE_HARVESTED: 6,
    DurablePhase.CLEANUP_STARTED: 7,
    DurablePhase.LEASES_RELEASED: 8,
    DurablePhase.CLOSED: 9,
}


def phase_rank(phase: DurablePhase) -> int | None:
    """Return the monotonic ladder rank, or ``None`` for off-ladder holds."""

    return _PHASE_RANK.get(phase)


def is_terminal_recorded_or_beyond(phase: DurablePhase) -> bool:
    """True once canonical terminal evidence has been durably recorded."""

    rank = _PHASE_RANK.get(phase)
    return rank is not None and rank >= _PHASE_RANK[DurablePhase.TERMINAL_RECORDED]


class TurnSubmissionState(str, Enum):
    """Durable knowledge about the current turn attempt's submission.

    ``AMBIGUOUS`` is the "sent but delivery unconfirmed" state that must never
    be blindly re-issued (invariant 7).
    """

    NOT_SUBMITTED = "not_submitted"
    SUBMITTED = "submitted"
    AMBIGUOUS = "ambiguous"
    ATTEMPT_COMPLETED = "attempt_completed"
    ATTEMPT_FAILED = "attempt_failed"


class DecisionAction(str, Enum):
    """The closed decision vocabulary (issue #3702)."""

    NO_OP = "no_op"
    AWAIT_OBSERVATION = "await_observation"
    ENSURE_PROFILE_LEASE = "ensure_profile_lease"
    ENSURE_HOST = "ensure_host"
    ENSURE_PROVIDER_SESSION = "ensure_provider_session"
    SUBMIT_TURN = "submit_turn"
    RECORD_PROVIDER_TERMINAL = "record_provider_terminal"
    SYNTHESIZE_TERMINAL_FROM_SNAPSHOT = "synthesize_terminal_from_snapshot"
    HARVEST_EVIDENCE = "harvest_evidence"
    BEGIN_CLEANUP = "begin_cleanup"
    RELEASE_LEASES = "release_leases"
    RETRY_TRANSIENT_OBSERVATION = "retry_transient_observation"
    QUARANTINE_AMBIGUOUS_STATE = "quarantine_ambiguous_state"
    FAIL_NONRETRYABLE = "fail_nonretryable"


class ReasonCode(str, Enum):
    """Stable reason codes attached to every decision.

    These are a closed, versioned vocabulary (:data:`REASON_CODE_VERSION`).
    They are never parsed from untrusted input at runtime; they are only parsed
    back from persisted reconciler output (for example shadow comparison), where
    :func:`parse_reason_code` enforces the fail policy for unknown codes.
    """

    SESSION_ALREADY_CLOSED = "session_already_closed"
    SESSION_ALREADY_FAILED = "session_already_failed"
    QUARANTINE_UNRESOLVED = "quarantine_unresolved"
    OBSERVATION_IDENTITY_MISMATCH = "observation_identity_mismatch"
    UNKNOWN_PROVIDER_STATUS = "unknown_provider_status"
    UNKNOWN_COMPATIBILITY_VOCABULARY = "unknown_compatibility_vocabulary"
    RUNTIME_INCOMPATIBLE = "runtime_incompatible"
    NEED_PROFILE_LEASE = "need_profile_lease"
    NEED_HOST = "need_host"
    NEED_PROVIDER_SESSION = "need_provider_session"
    SUBMIT_FIRST_TURN = "submit_first_turn"
    SUBMIT_RETRY_ATTEMPT = "submit_retry_attempt"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    AMBIGUOUS_SUBMISSION_AWAIT = "ambiguous_submission_await"
    PROVIDER_TERMINAL_EVENT = "provider_terminal_event"
    SNAPSHOT_TERMINAL_EVIDENCE = "snapshot_terminal_evidence"
    IDLE_WITH_COMPLETED_WORK = "idle_with_completed_work"
    ACTIVE_TOOL_CALL_NOT_TERMINAL = "active_tool_call_not_terminal"
    TERMINAL_EVENT_PENDING_TOOL_CALL = "terminal_event_pending_tool_call"
    AWAITING_RESPONSE_RECORD = "awaiting_response_record"
    TURN_SNAPSHOT_ABSENT = "turn_snapshot_absent"
    TURN_SNAPSHOT_UNAVAILABLE = "turn_snapshot_unavailable"
    HARVEST_TERMINAL_EVIDENCE = "harvest_terminal_evidence"
    CLEANUP_AFTER_EVIDENCE = "cleanup_after_evidence"
    LEASE_CONSUMERS_ACTIVE = "lease_consumers_active"
    RELEASE_IDLE_LEASES = "release_idle_leases"
    SESSION_CLOSED_AFTER_RELEASE = "session_closed_after_release"
    LATE_NONTERMINAL_AFTER_TERMINAL = "late_nonterminal_after_terminal"
    OPERATOR_REQUESTED_TERMINATION = "operator_requested_termination"


def parse_reason_code(value: str) -> ReasonCode:
    """Parse a persisted reason-code string, failing on unknown codes."""

    try:
        return ReasonCode(value)
    except ValueError as exc:  # unknown reason-code vocabulary -> fail policy
        raise ValueError(
            f"Unknown reconciler reason code {value!r} "
            f"(version {REASON_CODE_VERSION})"
        ) from exc


# --- Provider status normalization -----------------------------------------

# Canonical provider status vocabulary. Anything outside these sets is UNKNOWN
# and must fail closed (invariant 6). These mirror the bridge event
# normalization vocabulary so the reconciler and the event boundary agree.
PROVIDER_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "canceled", "cancelled", "timed_out", "timeout", "stopped"}
)
PROVIDER_NONTERMINAL_STATUSES = frozenset(
    {"created", "launching", "provisioning", "running", "waiting", "idle"}
)
_PROVIDER_STATUS_ALIASES = {"cancelled": "canceled", "timeout": "timed_out"}


class StatusKind(str, Enum):
    TERMINAL = "terminal"
    NONTERMINAL = "nonterminal"
    UNKNOWN = "unknown"


def normalize_provider_status(raw: str) -> tuple[StatusKind, str]:
    """Classify and normalize a provider session/turn status token.

    Returns ``(StatusKind.UNKNOWN, canonical)`` for any token outside the closed
    vocabulary so the reducer can quarantine instead of guessing.
    """

    canonical = str(raw or "").strip().lower()
    canonical = _PROVIDER_STATUS_ALIASES.get(canonical, canonical)
    if canonical in PROVIDER_TERMINAL_STATUSES:
        return StatusKind.TERMINAL, canonical
    if canonical in PROVIDER_NONTERMINAL_STATUSES:
        return StatusKind.NONTERMINAL, canonical
    return StatusKind.UNKNOWN, canonical


# Turn/attempt status vocabulary is distinct from session status: an attempt can
# be terminal while the canonical session is not (invariant 4).
TURN_TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled", "cancelled"})
TURN_NONTERMINAL_STATUSES = frozenset(
    {"running", "active", "in_progress", "queued", "started"}
)


def normalize_turn_status(raw: str) -> tuple[StatusKind, str]:
    """Classify and normalize a provider *turn/attempt* status token."""

    canonical = str(raw or "").strip().lower()
    canonical = _PROVIDER_STATUS_ALIASES.get(canonical, canonical)
    if canonical in TURN_TERMINAL_STATUSES:
        return StatusKind.TERMINAL, canonical
    if canonical in TURN_NONTERMINAL_STATUSES:
        return StatusKind.NONTERMINAL, canonical
    return StatusKind.UNKNOWN, canonical


# Compatibility / runtime-readiness vocabulary.
COMPATIBILITY_TOKENS = frozenset(
    {"compatible", "pending", "degraded", "incompatible"}
)


def classify_compatibility(raw: str) -> str:
    """Return a known compatibility token or ``"unknown"`` for fail-closed."""

    canonical = str(raw or "").strip().lower()
    if canonical in COMPATIBILITY_TOKENS:
        return canonical
    return "unknown"


__all__ = [
    "COMPATIBILITY_TOKENS",
    "DecisionAction",
    "DesiredLifecycle",
    "DurablePhase",
    "PROVIDER_NONTERMINAL_STATUSES",
    "PROVIDER_TERMINAL_STATUSES",
    "ReasonCode",
    "StatusKind",
    "TURN_NONTERMINAL_STATUSES",
    "TURN_TERMINAL_STATUSES",
    "TurnSubmissionState",
    "classify_compatibility",
    "is_terminal_recorded_or_beyond",
    "normalize_provider_status",
    "normalize_turn_status",
    "parse_reason_code",
    "phase_rank",
]
