"""Bounded Omnigent stuck-state detector and automated response policy.

Source: MoonLadderStudios/MoonMind#3708 ([Omnigent control plane 7/11]).

:func:`detect_stuck_state` is a *pure* function over durable records, bounded
observation-derived signals, a policy, and ``now``. It returns stable
:class:`StuckStateFinding` values; it performs no I/O and never mutates state.

Two issue invariants are structural here:

* **Lack of evidence is not a negative observation.** Signals are tri-state
  (``None`` = not observed, ``True``/``False`` = observed). The detector never
  asserts "provider is terminal" from an *absent* observation; it distinguishes
  a missing observation (which may itself trip a freshness finding) from an
  observed negative.
* **The detector never authorizes a provider mutation.** Every finding's
  :class:`ResponseAction` is one of ``RECONCILE`` (ask the canonical session
  workflow to reconcile under current fencing), ``QUARANTINE``, or ``OBSERVE``.
  There is intentionally no ``RESUBMIT_TURN`` / ``RELEASE_LEASE`` action: the
  first automated response is always a fenced reconciliation, never a blind
  duplicate provider mutation or a heuristic lease release
  (:func:`plan_response`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from .records import (
    CommandRecord,
    SessionRecord,
    COMMAND_STATE_CLAIMED,
    COMMAND_STATE_DELIVERY_UNKNOWN,
)


class StuckStateReason(str, Enum):
    """Stable, low-cardinality reason code for each detectable stuck state."""

    MOONMIND_ACTIVE_NO_RECENT_EVIDENCE = "moonmind_active_no_recent_evidence"
    PROVIDER_TERMINAL_MOONMIND_NONTERMINAL = "provider_terminal_moonmind_nonterminal"
    MOONMIND_TERMINAL_PROVIDER_ACTIVE = "moonmind_terminal_provider_active"
    ACTIVE_TURN_LIVENESS_ONLY = "active_turn_liveness_only"
    REPEATED_RECONCILIATION_NO_PROGRESS = "repeated_reconciliation_no_progress"
    HOST_LEASE_WITHOUT_SESSION_AUTHORITY = "host_lease_without_session_authority"
    PROFILE_LEASE_WITHOUT_CONSUMER = "profile_lease_without_consumer"
    CLEANUP_INCOMPLETE_PAST_DEADLINE = "cleanup_incomplete_past_deadline"
    COMPATIBILITY_UNKNOWN_AFTER_ADMISSION = "compatibility_unknown_after_admission"
    COMMAND_STUCK_CLAIMED_OR_DELIVERY_UNKNOWN = "command_stuck_claimed_or_delivery_unknown"
    LIVE_CONFORMANCE_EVIDENCE_STALE = "live_conformance_evidence_stale"


class ResponseAction(str, Enum):
    """The only responses the detector may authorize.

    ``RECONCILE`` requests a fenced reconciliation of the canonical session
    workflow; ``QUARANTINE`` parks the session and publishes redacted
    diagnostics; ``OBSERVE`` records the finding and surfaces remediation without
    any mutation. There is deliberately no direct provider-mutation action.
    """

    RECONCILE = "reconcile"
    QUARANTINE = "quarantine"
    OBSERVE = "observe"


@dataclass(frozen=True)
class StuckStatePolicy:
    """Bounded deadlines governing detection (all safely below a 6h timeout)."""

    event_staleness: timedelta = timedelta(minutes=10)
    snapshot_staleness: timedelta = timedelta(minutes=10)
    liveness_only_max: timedelta = timedelta(minutes=15)
    cleanup_deadline: timedelta = timedelta(minutes=30)
    conformance_max_age: timedelta = timedelta(hours=24)
    command_stuck_max: timedelta = timedelta(minutes=10)
    #: Consecutive no-progress reconciliations before the no-progress finding.
    no_progress_max: int = 3
    #: After this many detections of the same reason, ambiguity is persistent and
    #: the response escalates from RECONCILE to QUARANTINE.
    persistent_ambiguity_max: int = 3


@dataclass(frozen=True)
class SessionSignals:
    """Bounded observation-derived inputs. ``None`` means *not observed*.

    Every provider/host/lease flag is tri-state so the detector can distinguish
    a missing observation from an observed negative.
    """

    last_event_at: Optional[datetime] = None
    last_snapshot_at: Optional[datetime] = None
    #: Durable start of the active turn attempt. Absence of any observation is
    #: aged from this timestamp so a freshly activated turn with no observations
    #: yet does not immediately trip the freshness finding.
    active_turn_started_at: Optional[datetime] = None
    #: An active turn produced only liveness/heartbeat observations since this
    #: time (``None`` when the turn is producing substantive observations).
    liveness_only_since: Optional[datetime] = None
    provider_terminal: Optional[bool] = None
    provider_active: Optional[bool] = None
    host_lease_active: Optional[bool] = None
    host_lease_owns_session_authority: Optional[bool] = None
    profile_lease_active: Optional[bool] = None
    profile_lease_has_consumer: Optional[bool] = None
    #: Compatibility/actual-build state resolved after admission (``None`` =
    #: unknown, which after admission is itself a finding).
    compatibility_known: Optional[bool] = None
    admitted: bool = False
    active_command: Optional[CommandRecord] = None
    active_command_since: Optional[datetime] = None
    cleanup_started_at: Optional[datetime] = None
    conformance_evidence_at: Optional[datetime] = None
    conformance_runner_available: Optional[bool] = None
    #: Consecutive reconciliations without a revision or observation advance.
    consecutive_no_progress: int = 0
    #: How many times this session has already been found stuck for the same
    #: dominant reason (drives RECONCILE -> QUARANTINE escalation).
    prior_detection_count: int = 0


@dataclass(frozen=True)
class StuckStateFinding:
    """One detected stuck state with a stable reason and a safe response."""

    reason: StuckStateReason
    action: ResponseAction
    detail: str
    remediation: str


@dataclass(frozen=True)
class AutomatedResponse:
    """The single automated response derived from a set of findings.

    The first automated response is always a fenced reconciliation:
    ``expected_revision`` / ``expected_fencing_generation`` are copied from the
    durable session so the executor can only apply a decision authorized by the
    pure reconciler under current fencing. Persistent ambiguity escalates to a
    quarantine plus a redacted diagnostics payload; product reads and evidence
    stay available regardless.
    """

    reconcile: bool
    quarantine: bool
    expected_revision: int
    expected_fencing_generation: int
    reasons: tuple[StuckStateReason, ...]
    diagnostics: dict[str, object] = field(default_factory=dict)
    remediation: Optional[str] = None


def _stale(reference: Optional[datetime], now: datetime, budget: timedelta) -> bool:
    """True when ``reference`` is present and older than ``budget`` before now."""

    return reference is not None and (now - reference) >= budget


def detect_stuck_state(
    *,
    session: SessionRecord,
    signals: SessionSignals,
    now: datetime,
    policy: StuckStatePolicy = StuckStatePolicy(),
) -> list[StuckStateFinding]:
    """Return the stuck-state findings for one session, in priority order.

    Detection is bounded and conservative: it fires at the configured deadline
    and never while progress is occurring (fresh observations or an advancing
    revision suppress the freshness/no-progress findings).
    """

    findings: list[StuckStateFinding] = []
    nonterminal = session.terminal_state is None

    # 1. MoonMind active with no recent event or snapshot.
    if nonterminal and session.active_turn_attempt_id is not None:
        # Fresh evidence from *either* channel means the turn is progressing;
        # never fire the freshness finding while one channel is reporting, even
        # if the other channel is stale.
        fresh_event = signals.last_event_at is not None and not _stale(
            signals.last_event_at, now, policy.event_staleness
        )
        fresh_snapshot = signals.last_snapshot_at is not None and not _stale(
            signals.last_snapshot_at, now, policy.snapshot_staleness
        )
        # Age the absence from the most recent durable timestamp available: the
        # latest observation on either channel, else the turn's durable start.
        # A brand-new turn with no observations must not trip immediately, and
        # without any durable timestamp the finding stays conservative (no fire).
        reference = (
            signals.last_event_at
            or signals.last_snapshot_at
            or signals.active_turn_started_at
        )
        aged_out = _stale(reference, now, min(policy.event_staleness, policy.snapshot_staleness))
        if not (fresh_event or fresh_snapshot) and aged_out:
            findings.append(
                StuckStateFinding(
                    reason=StuckStateReason.MOONMIND_ACTIVE_NO_RECENT_EVIDENCE,
                    action=ResponseAction.RECONCILE,
                    detail="active session has no recent provider event or snapshot",
                    remediation="request a fenced reconcile to re-observe the provider snapshot",
                )
            )

    # 2. Provider terminal while MoonMind nonterminal (observed-positive only).
    if nonterminal and signals.provider_terminal is True:
        findings.append(
            StuckStateFinding(
                reason=StuckStateReason.PROVIDER_TERMINAL_MOONMIND_NONTERMINAL,
                action=ResponseAction.RECONCILE,
                detail="provider reports terminal but canonical session is nonterminal",
                remediation="reconcile to synthesize the missed terminal from snapshot evidence",
            )
        )

    # 3. MoonMind terminal while provider active (observed-positive only).
    if session.terminal_state is not None and signals.provider_active is True:
        findings.append(
            StuckStateFinding(
                reason=StuckStateReason.MOONMIND_TERMINAL_PROVIDER_ACTIVE,
                action=ResponseAction.RECONCILE,
                detail="canonical session terminal but provider still reports active",
                remediation="reconcile to confirm provider terminal and drive cleanup",
            )
        )

    # 4. Active turn with only liveness observations beyond policy.
    if nonterminal and _stale(signals.liveness_only_since, now, policy.liveness_only_max):
        findings.append(
            StuckStateFinding(
                reason=StuckStateReason.ACTIVE_TURN_LIVENESS_ONLY,
                action=ResponseAction.RECONCILE,
                detail="active turn has produced only liveness observations beyond policy",
                remediation="reconcile to re-observe substantive turn progress",
            )
        )

    # 5. Repeated reconciliation without revision or observation progress.
    if signals.consecutive_no_progress >= policy.no_progress_max:
        findings.append(
            StuckStateFinding(
                reason=StuckStateReason.REPEATED_RECONCILIATION_NO_PROGRESS,
                action=ResponseAction.RECONCILE,
                detail="reconciliation repeated without a revision or observation advance",
                remediation="reconcile once more; escalate to quarantine if still no progress",
            )
        )

    # 6. Host lease active without owning session authority (observed-positive).
    if signals.host_lease_active is True and signals.host_lease_owns_session_authority is False:
        findings.append(
            StuckStateFinding(
                reason=StuckStateReason.HOST_LEASE_WITHOUT_SESSION_AUTHORITY,
                action=ResponseAction.RECONCILE,
                detail="host lease is active but does not own current session authority",
                remediation="reconcile so the fenced cleanup authority reclaims the orphaned host lease",
            )
        )

    # 7. Provider Profile lease active without a credential consumer.
    if signals.profile_lease_active is True and signals.profile_lease_has_consumer is False:
        findings.append(
            StuckStateFinding(
                reason=StuckStateReason.PROFILE_LEASE_WITHOUT_CONSUMER,
                action=ResponseAction.RECONCILE,
                detail="Provider Profile lease is active with no credential consumer",
                remediation="reconcile so the fenced cleanup authority releases the orphaned profile lease",
            )
        )

    # 8. Cleanup started but not completed by deadline.
    cleanup_incomplete = (
        signals.cleanup_started_at is not None
        and _stale(signals.cleanup_started_at, now, policy.cleanup_deadline)
        and session.cleanup_state not in {"complete", "closed"}
    )
    if cleanup_incomplete:
        findings.append(
            StuckStateFinding(
                reason=StuckStateReason.CLEANUP_INCOMPLETE_PAST_DEADLINE,
                action=ResponseAction.RECONCILE,
                detail="cleanup started but did not complete by the deadline",
                remediation="reconcile so the janitor re-claims and completes cleanup under current fencing",
            )
        )

    # 9. Compatibility / actual-build state unknown after admission.
    if signals.admitted and signals.compatibility_known is None:
        findings.append(
            StuckStateFinding(
                reason=StuckStateReason.COMPATIBILITY_UNKNOWN_AFTER_ADMISSION,
                action=ResponseAction.RECONCILE,
                detail="compatibility/actual-build state is unknown after admission",
                remediation="reconcile to re-verify deployed-build compatibility",
            )
        )

    # 10. Command stuck in claimed or delivery-unknown state past policy.
    command = signals.active_command
    if command is not None and command.status in {
        COMMAND_STATE_CLAIMED,
        COMMAND_STATE_DELIVERY_UNKNOWN,
    }:
        if _stale(signals.active_command_since, now, policy.command_stuck_max):
            findings.append(
                StuckStateFinding(
                    reason=StuckStateReason.COMMAND_STUCK_CLAIMED_OR_DELIVERY_UNKNOWN,
                    action=ResponseAction.RECONCILE,
                    detail=f"command stuck in {command.status} state beyond policy",
                    remediation=(
                        "reconcile to confirm delivery at the authoritative boundary; "
                        "never blind-resubmit"
                    ),
                )
            )

    # 11. Live-conformance evidence expired or runner unavailable.
    conformance_stale = _stale(signals.conformance_evidence_at, now, policy.conformance_max_age)
    runner_down = signals.conformance_runner_available is False
    if conformance_stale or runner_down:
        findings.append(
            StuckStateFinding(
                reason=StuckStateReason.LIVE_CONFORMANCE_EVIDENCE_STALE,
                action=ResponseAction.OBSERVE,
                detail="live-conformance evidence is expired or its runner is unavailable",
                remediation="refresh protected-live conformance evidence or restore the verification runner",
            )
        )

    return findings


def plan_response(
    *,
    session: SessionRecord,
    findings: list[StuckStateFinding],
    prior_detection_count: int = 0,
    policy: StuckStatePolicy = StuckStatePolicy(),
) -> Optional[AutomatedResponse]:
    """Derive the single fenced automated response for a set of findings.

    Returns ``None`` when there is nothing to do. When findings exist, the first
    automated response is a fenced reconcile bound to the durable session's
    current revision and fencing generation. If the same ambiguity has persisted
    beyond ``policy.persistent_ambiguity_max`` detections, the response escalates
    to a quarantine plus redacted diagnostics. Findings whose only action is
    ``OBSERVE`` never mutate state — they surface remediation only.
    """

    if not findings:
        return None

    actionable = [f for f in findings if f.action != ResponseAction.OBSERVE]
    reasons = tuple(f.reason for f in findings)
    diagnostics: dict[str, object] = {
        "reasons": [f.reason.value for f in findings],
        "detection_count": prior_detection_count + 1,
    }
    remediation = findings[0].remediation

    if not actionable:
        # Only OBSERVE-class findings: surface remediation, mutate nothing.
        return AutomatedResponse(
            reconcile=False,
            quarantine=False,
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
            reasons=reasons,
            diagnostics=diagnostics,
            remediation=remediation,
        )

    escalate = prior_detection_count + 1 > policy.persistent_ambiguity_max
    return AutomatedResponse(
        reconcile=not escalate,
        quarantine=escalate,
        expected_revision=session.revision,
        expected_fencing_generation=session.fencing_generation,
        reasons=reasons,
        diagnostics=diagnostics,
        remediation=remediation,
    )


__all__ = [
    "StuckStateReason",
    "ResponseAction",
    "StuckStatePolicy",
    "SessionSignals",
    "StuckStateFinding",
    "AutomatedResponse",
    "detect_stuck_state",
    "plan_response",
]
