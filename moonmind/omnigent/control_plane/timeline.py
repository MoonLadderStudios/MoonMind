"""Durable operator session timeline projection for the Omnigent control plane.

Source: MoonLadderStudios/MoonMind#3708 ([Omnigent control plane 7/11]).

:func:`build_timeline` derives one authorized, bounded, secret-free diagnostic
projection for a single canonical session from its durable records
(:class:`SessionRecord`, turn attempts, observations, commands, and decisions,
plus optional cleanup authority). It is a **projection, not a second lifecycle
authority**: it only reads durable records and never mutates state or calls a
live provider/host/workspace resource, so the timeline still explains a session
after its live resources are cleaned up (issue acceptance criterion "timeline
data remains available after live resources are removed").

Every emitted field is bounded and safe: refs and digests are passed through a
guard that drops anything resembling a credential, presigned URL, or host path
(:func:`safe_timeline_ref`), and no prompt, transcript, diff, or provider credential is
ever included. Trace/artifact links are server-authored relative URLs built from
opaque, validated ids.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Sequence

from .records import (
    CleanupAuthorityRecord,
    CommandRecord,
    DecisionRecord,
    ObservationRecord,
    SessionRecord,
    TurnAttemptRecord,
    COMMAND_STATE_CLAIMED,
    COMMAND_STATE_DELIVERY_UNKNOWN,
)
from .spans import _FORBIDDEN_VALUE_PATTERNS, MAX_ATTRIBUTE_VALUE_LEN

# Opaque identifiers safe to embed in a server-authored URL path segment.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class TimelineStatus(str, Enum):
    """Bounded, operator-facing explanation class for the session's state.

    This is *why* the session is where it is, derived from durable records — not
    a second lifecycle vocabulary. It answers the issue requirement to explain
    why a session is waiting, retrying, quarantined, terminal, or
    cleanup-incomplete.
    """

    LAUNCHING = "launching"
    RUNNING = "running"
    DELIVERY_UNKNOWN = "delivery_unknown"
    AWAITING_OBSERVATION = "awaiting_observation"
    RETRYING = "retrying"
    TERMINAL = "terminal"
    CLEANUP_INCOMPLETE = "cleanup_incomplete"
    QUARANTINED = "quarantined"
    CLOSED = "closed"


def safe_timeline_ref(value: Optional[str]) -> Optional[str]:
    """Return ``value`` if it is a bounded, secret-free ref, else ``None``.

    A ref that looks like a credential, presigned URL, host path, or unbounded
    payload is dropped rather than surfaced to an operator browser.
    """

    if value is None:
        return None
    text = str(value)
    if len(text) > MAX_ATTRIBUTE_VALUE_LEN:
        return None
    for pattern in _FORBIDDEN_VALUE_PATTERNS:
        if pattern.search(text):
            return None
    return text


def _safe_link(kind: str, ref: Optional[str]) -> Optional[str]:
    """Build a server-authored relative URL from an opaque, validated id.

    Only links to routes that are actually registered are emitted; an operator
    link must never resolve to a 404. Artifact and intent refs map to the
    registered artifact metadata route ``/api/artifacts/{artifact_id}`` (see
    :mod:`api_service.api.routers.temporal_artifacts`). ``trace`` refs have no
    registered destination in this phase, so the link is omitted rather than
    pointed at a nonexistent ``/api/omnigent/traces`` route.
    """

    safe = safe_timeline_ref(ref)
    if safe is None or not _SAFE_ID.match(safe):
        return None
    if kind in {"artifact", "intent"}:
        return f"/api/artifacts/{safe}"
    return None


@dataclass(frozen=True)
class LeaseObservationSummary:
    """Bounded durable-lease authority summary (no profile/host identity)."""

    profile_generation: Optional[int] = None
    host_lease_generation: Optional[int] = None
    credential_generation: Optional[int] = None
    host_bound: bool = False


@dataclass(frozen=True)
class DecisionSummary:
    """Bounded summary of the most recent reconciliation decision."""

    decision_code: str
    reason_code: Optional[str]
    fencing_generation: int
    next_deadline: Optional[datetime]
    product_visible_transition: Optional[str]
    trace_link: Optional[str] = None
    diagnostics_link: Optional[str] = None


@dataclass(frozen=True)
class CommandSummary:
    """Bounded summary of the active or delivery-unknown command, if any."""

    command_type: str
    status: str
    delivery_ambiguous: bool
    fencing_generation: int


@dataclass(frozen=True)
class SessionTimeline:
    """One bounded, durable operator-facing session timeline projection."""

    session_id: str
    provider: str

    # compiled intent
    intent_ref: Optional[str]
    intent_digest: Optional[str]
    execution_plan_ref: Optional[str]
    execution_plan_digest: Optional[str]
    runtime_binding_ref: Optional[str]
    runtime_binding_revision: Optional[int]
    runtime_binding_fencing_generation: Optional[int]
    runtime_binding_state: Optional[str]

    # canonical session state / authority
    desired_state: str
    durable_state: str
    observed_state: Optional[str]
    reconciled_state: Optional[str]
    revision: int
    fencing_generation: int

    # turn attempt
    active_turn_attempt_state: Optional[str]
    turn_attempt_count: int

    # provider frontier / snapshot
    provider_event_cursor: Optional[str]
    snapshot_frontier: Optional[str]
    last_snapshot_at: Optional[datetime]
    last_snapshot_digest: Optional[str]
    last_event_at: Optional[datetime]

    # leases / host
    leases: LeaseObservationSummary

    # reconciliation
    last_decision: Optional[DecisionSummary]
    next_reconciliation_deadline: Optional[datetime]

    # command
    active_command: Optional[CommandSummary]

    # terminal / cleanup
    terminal_state: Optional[str]
    terminal_evidence_ref: Optional[str]
    cleanup_state: str
    janitor_state: Optional[str]
    workspace_publication_state: Optional[str]

    # compatibility
    compatibility_ref: Optional[str]
    image_manifest_ref: Optional[str]

    # explanation
    status: TimelineStatus
    status_detail: Optional[str]

    # safe links
    trace_link: Optional[str]
    log_link: Optional[str]
    terminal_evidence_link: Optional[str]
    intent_link: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        """Return the machine-readable, JSON-safe timeline document."""

        def _iso(value: Optional[datetime]) -> Optional[str]:
            return value.isoformat() if value is not None else None

        decision: Optional[dict[str, Any]] = None
        if self.last_decision is not None:
            decision = {
                "decisionCode": self.last_decision.decision_code,
                "reasonCode": self.last_decision.reason_code,
                "fencingGeneration": self.last_decision.fencing_generation,
                "nextDeadline": _iso(self.last_decision.next_deadline),
                "productVisibleTransition": self.last_decision.product_visible_transition,
                "traceLink": self.last_decision.trace_link,
                "diagnosticsLink": self.last_decision.diagnostics_link,
            }
        command: Optional[dict[str, Any]] = None
        if self.active_command is not None:
            command = {
                "commandType": self.active_command.command_type,
                "status": self.active_command.status,
                "deliveryAmbiguous": self.active_command.delivery_ambiguous,
                "fencingGeneration": self.active_command.fencing_generation,
            }
        return {
            "sessionId": self.session_id,
            "provider": self.provider,
            "intent": {"ref": self.intent_ref, "digest": self.intent_digest, "link": self.intent_link},
            "executionPlan": {
                "ref": self.execution_plan_ref,
                "digest": self.execution_plan_digest,
            },
            "runtimeBinding": {
                "ref": self.runtime_binding_ref,
                "revision": self.runtime_binding_revision,
                "fencingGeneration": self.runtime_binding_fencing_generation,
                "state": self.runtime_binding_state,
            },
            "state": {
                "desired": self.desired_state,
                "durable": self.durable_state,
                "observed": self.observed_state,
                "reconciled": self.reconciled_state,
                "revision": self.revision,
                "fencingGeneration": self.fencing_generation,
            },
            "turnAttempt": {
                "state": self.active_turn_attempt_state,
                "count": self.turn_attempt_count,
            },
            "provider_frontier": {
                "eventCursor": self.provider_event_cursor,
                "snapshotFrontier": self.snapshot_frontier,
                "lastSnapshotAt": _iso(self.last_snapshot_at),
                "lastSnapshotDigest": self.last_snapshot_digest,
                "lastEventAt": _iso(self.last_event_at),
            },
            "leases": {
                "profileGeneration": self.leases.profile_generation,
                "hostLeaseGeneration": self.leases.host_lease_generation,
                "credentialGeneration": self.leases.credential_generation,
                "hostBound": self.leases.host_bound,
            },
            "lastDecision": decision,
            "nextReconciliationDeadline": _iso(self.next_reconciliation_deadline),
            "activeCommand": command,
            "terminal": {
                "state": self.terminal_state,
                "evidenceRef": self.terminal_evidence_ref,
                "evidenceLink": self.terminal_evidence_link,
            },
            "cleanup": {"state": self.cleanup_state, "janitorState": self.janitor_state},
            "workspacePublicationState": self.workspace_publication_state,
            "compatibility": {
                "ref": self.compatibility_ref,
                "imageManifestRef": self.image_manifest_ref,
            },
            "explanation": {"status": self.status.value, "detail": self.status_detail},
            "links": {"trace": self.trace_link, "logs": self.log_link},
        }


def _latest(records: Sequence[Any], key: str) -> Optional[Any]:
    """Return the record with the greatest ``key`` timestamp, or ``None``."""

    best = None
    best_ts: Optional[datetime] = None
    for record in records:
        ts = getattr(record, key, None)
        if ts is None:
            continue
        if best_ts is None or ts > best_ts:
            best, best_ts = record, ts
    return best


def _classify_status(
    session: SessionRecord,
    active_command: Optional[CommandRecord],
    cleanup: Optional[CleanupAuthorityRecord],
) -> tuple[TimelineStatus, Optional[str]]:
    """Explain the session's current state from durable records only."""

    meta = session.metadata or {}
    if meta.get("quarantined") or session.historical_read_state == "quarantined":
        return TimelineStatus.QUARANTINED, "session quarantined pending operator remediation"

    if active_command is not None and active_command.status == COMMAND_STATE_DELIVERY_UNKNOWN:
        return (
            TimelineStatus.DELIVERY_UNKNOWN,
            "a provider side effect may have occurred; awaiting reconciliation confirmation",
        )

    if session.terminal_state is not None:
        cleanup_done = session.cleanup_state in {"complete", "closed"}
        if cleanup is not None and cleanup.state != "complete":
            return (
                TimelineStatus.CLEANUP_INCOMPLETE,
                "session terminal but cleanup authority has not completed",
            )
        if not cleanup_done:
            return (
                TimelineStatus.CLEANUP_INCOMPLETE,
                "session terminal but cleanup is not marked complete",
            )
        return TimelineStatus.CLOSED, "session terminal and cleanup complete"

    if active_command is not None and active_command.status == COMMAND_STATE_CLAIMED:
        return TimelineStatus.RUNNING, "command claimed and executing"

    prior = (session.metadata or {}).get("last_reason_code")
    if isinstance(prior, str) and prior.startswith("retry"):
        return TimelineStatus.RETRYING, "reconciler is retrying a transient observation"

    if session.active_turn_attempt_id is None and session.provider_session_ref is None:
        return TimelineStatus.LAUNCHING, "session provisioning provider/host authority"

    if session.observed_state is None:
        return TimelineStatus.AWAITING_OBSERVATION, "no independent provider observation yet"

    return TimelineStatus.RUNNING, "turn in flight"


def build_timeline(
    *,
    session: SessionRecord,
    turn_attempts: Sequence[TurnAttemptRecord] = (),
    observations: Sequence[ObservationRecord] = (),
    commands: Sequence[CommandRecord] = (),
    decisions: Sequence[DecisionRecord] = (),
    cleanup: Optional[CleanupAuthorityRecord] = None,
    turn_attempt_count: Optional[int] = None,
    trace_link: Optional[str] = None,
    log_link: Optional[str] = None,
) -> SessionTimeline:
    """Project one bounded operator session timeline from durable records.

    The projection reads only the passed records; it performs no live-resource
    I/O, so it survives provider/host/workspace cleanup. Every ref/digest is
    passed through :func:`safe_timeline_ref`, and trace/artifact links are built only
    from opaque validated ids, so no credential, presigned URL, host path, or
    unbounded payload can appear.

    ``turn_attempt_count`` lets a caller supply an authoritative database count
    when it only fetched the active turn attempt (bounded query) rather than the
    full turn-attempt history; when omitted the count falls back to the length of
    the passed ``turn_attempts`` sequence.
    """

    # Active turn attempt.
    active_turn: Optional[TurnAttemptRecord] = None
    if session.active_turn_attempt_id is not None:
        for turn in turn_attempts:
            if turn.turn_attempt_id == session.active_turn_attempt_id:
                active_turn = turn
                break

    # Latest snapshot and event observations (bounded, source-typed).
    snapshot_obs = _latest(
        [o for o in observations if o.observation_type in {"snapshot", "provider_snapshot"}],
        "observed_at",
    )
    event_obs = _latest(
        [
            o
            for o in observations
            if o.observation_type
            in {
                "event",
                "event_frontier",
                "event_batch",
                "provider_event",
                "provider_event_batch",
            }
        ],
        "observed_at",
    )

    # Active / delivery-unknown command (delivery-ambiguity takes precedence).
    active_command: Optional[CommandRecord] = None
    for command in commands:
        if command.status == COMMAND_STATE_DELIVERY_UNKNOWN:
            active_command = command
            break
    if active_command is None:
        for command in commands:
            if command.status == COMMAND_STATE_CLAIMED:
                active_command = command
                break

    # Most recent decision.
    last_decision_record = _latest(list(decisions), "created_at")
    decision_summary: Optional[DecisionSummary] = None
    if last_decision_record is not None:
        decision_summary = DecisionSummary(
            decision_code=last_decision_record.decision_code,
            reason_code=last_decision_record.reason_code,
            fencing_generation=last_decision_record.fencing_generation,
            next_deadline=last_decision_record.next_deadline,
            product_visible_transition=last_decision_record.product_visible_transition,
            trace_link=safe_timeline_ref(trace_link),
            diagnostics_link=_safe_link("artifact", last_decision_record.diagnostics_ref),
        )

    command_summary: Optional[CommandSummary] = None
    if active_command is not None:
        command_summary = CommandSummary(
            command_type=active_command.command_type,
            status=active_command.status,
            delivery_ambiguous=active_command.delivery_ambiguous,
            fencing_generation=active_command.fencing_generation,
        )

    status, detail = _classify_status(session, active_command, cleanup)

    meta = session.metadata or {}
    workspace_state = meta.get("workspace_publication_state")
    janitor_state = cleanup.state if cleanup is not None else None

    return SessionTimeline(
        session_id=session.session_id,
        provider=session.provider,
        intent_ref=safe_timeline_ref(session.intent_ref),
        intent_digest=safe_timeline_ref(session.intent_digest),
        execution_plan_ref=safe_timeline_ref(meta.get("executionPlanRef")),
        execution_plan_digest=safe_timeline_ref(meta.get("executionPlanDigest")),
        runtime_binding_ref=safe_timeline_ref(meta.get("runtimeBindingRef")),
        runtime_binding_revision=(
            int(meta["runtimeBindingRevision"])
            if meta.get("runtimeBindingRevision") is not None
            else None
        ),
        runtime_binding_fencing_generation=(
            int(meta["runtimeBindingFencingGeneration"])
            if meta.get("runtimeBindingFencingGeneration") is not None
            else None
        ),
        runtime_binding_state=(
            str(meta["runtimeBindingState"])
            if meta.get("runtimeBindingState") is not None
            else None
        ),
        desired_state=session.desired_state,
        durable_state=session.reconciled_state or session.desired_state,
        observed_state=session.observed_state,
        reconciled_state=session.reconciled_state,
        revision=session.revision,
        fencing_generation=session.fencing_generation,
        active_turn_attempt_state=active_turn.state if active_turn is not None else None,
        turn_attempt_count=(
            turn_attempt_count if turn_attempt_count is not None else len(turn_attempts)
        ),
        provider_event_cursor=safe_timeline_ref(session.provider_event_cursor),
        snapshot_frontier=safe_timeline_ref(session.snapshot_frontier),
        last_snapshot_at=getattr(snapshot_obs, "observed_at", None),
        last_snapshot_digest=safe_timeline_ref(
            getattr(snapshot_obs, "source_digest", None)
        ),
        last_event_at=getattr(event_obs, "observed_at", None),
        leases=LeaseObservationSummary(
            profile_generation=session.provider_profile_generation,
            host_lease_generation=session.host_lease_generation,
            credential_generation=session.credential_generation,
            host_bound=session.host_binding_ref is not None,
        ),
        last_decision=decision_summary,
        next_reconciliation_deadline=session.next_reconciliation_deadline,
        active_command=command_summary,
        terminal_state=session.terminal_state,
        terminal_evidence_ref=safe_timeline_ref(session.terminal_evidence_ref),
        cleanup_state=session.cleanup_state,
        janitor_state=janitor_state,
        workspace_publication_state=workspace_state if isinstance(workspace_state, str) else None,
        compatibility_ref=safe_timeline_ref(session.compatibility_ref),
        image_manifest_ref=safe_timeline_ref(session.image_manifest_ref),
        status=status,
        status_detail=detail,
        trace_link=safe_timeline_ref(trace_link),
        log_link=safe_timeline_ref(log_link),
        terminal_evidence_link=_safe_link("artifact", session.terminal_evidence_ref),
        intent_link=_safe_link("intent", session.intent_ref),
    )


__all__ = [
    "TimelineStatus",
    "LeaseObservationSummary",
    "DecisionSummary",
    "CommandSummary",
    "SessionTimeline",
    "safe_timeline_ref",
    "build_timeline",
]
