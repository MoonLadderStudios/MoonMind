"""Durable stuck-state observation and response boundary.

Source: MoonLadderStudios/MoonMind#3708.

The pure detector in :mod:`stuck_state` never owns side effects.  This module is
the activity/service boundary that records its findings, journals one fenced and
idempotent reconcile request, dispatches only that request, and quarantines
persistent ambiguity only after a redacted diagnostic artifact exists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Optional, Protocol

from . import metrics, spans
from .records import (
    COMMAND_STATE_CLAIMED,
    COMMAND_STATE_PENDING,
    ControlPlaneOutcome,
    DecisionRecord,
    ObservationRecord,
    SessionRecord,
    compute_digest,
)
from .repositories import ControlPlaneRepositories
from .stuck_state import (
    AutomatedResponse,
    SessionSignals,
    StuckStateFinding,
    StuckStatePolicy,
    StuckStateReason,
    detect_stuck_state,
    plan_response,
)


_EVENT_OBSERVATION_TYPES = (
    "event",
    "event_frontier",
    "event_batch",
    "provider_event",
    "provider_event_batch",
)
_SNAPSHOT_OBSERVATION_TYPES = ("snapshot", "provider_snapshot")
_LIVENESS_OBSERVATION_TYPES = ("heartbeat", "liveness", "provider_liveness")
_TERMINAL_PROVIDER_STATES = frozenset(
    {
        "completed",
        "complete",
        "success",
        "succeeded",
        "failed",
        "error",
        "errored",
        "canceled",
        "cancelled",
        "timed_out",
        "timeout",
    }
)
_ACTIVE_PROVIDER_STATES = frozenset(
    {"active", "running", "working", "in_progress", "queued", "starting", "idle"}
)
_DETECTION_BUCKET = timedelta(minutes=10)
logger = logging.getLogger("moonmind.omnigent.control_plane.stuck_state")


def _increment_metric(name: str, **labels: str) -> None:
    """Keep auxiliary telemetry failures outside lifecycle authority."""

    try:
        metrics.increment(name, **labels)
    except Exception:
        pass


def _observe_metric(name: str, value: float, **labels: str) -> None:
    try:
        metrics.observe(name, max(0.0, value), **labels)
    except Exception:
        pass


def _elapsed_seconds(now: datetime, reference: datetime) -> float:
    normalized_now = now.replace(tzinfo=UTC) if now.tzinfo is None else now
    normalized_reference = (
        reference.replace(tzinfo=UTC) if reference.tzinfo is None else reference
    )
    return (normalized_now - normalized_reference).total_seconds()


class ReconcileDispatcher(Protocol):
    """Dispatch the already-journaled request to the canonical session owner."""

    async def request_reconcile(
        self,
        *,
        session_id: str,
        workflow_id: str,
        request_id: str,
        reason_code: str,
        expected_revision: str,
        expected_fencing_generation: str,
    ) -> None: ...


class DiagnosticPublisher(Protocol):
    """Persist one restricted, redacted diagnostic artifact."""

    async def publish(
        self,
        *,
        session: SessionRecord,
        decision_id: str,
        payload: dict[str, object],
    ) -> str: ...


@dataclass(frozen=True)
class StuckStateInspection:
    session: SessionRecord
    findings: tuple[StuckStateFinding, ...]
    response: Optional[AutomatedResponse]


@dataclass
class StuckStateSweepResult:
    scanned: int = 0
    findings_recorded: int = 0
    reconcile_requests: int = 0
    delivery_unknown: int = 0
    quarantined: int = 0
    observation_only: int = 0
    conflicts: int = 0
    failures: int = 0

    def to_dict(self) -> dict[str, int]:
        """Return a compact workflow-history-safe summary without identities."""

        return {
            "scanned": self.scanned,
            "findingsRecorded": self.findings_recorded,
            "reconcileRequests": self.reconcile_requests,
            "deliveryUnknown": self.delivery_unknown,
            "quarantined": self.quarantined,
            "observationOnly": self.observation_only,
            "conflicts": self.conflicts,
            "failures": self.failures,
        }


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _provider_state(
    snapshot: Optional[ObservationRecord], event: Optional[ObservationRecord]
) -> tuple[Optional[bool], Optional[bool]]:
    raw_status: Optional[str] = None
    if snapshot is not None:
        provider = _mapping(snapshot.bounded_index.get("providerSession"))
        candidate = provider.get("rawStatus")
        if candidate is not None:
            raw_status = str(candidate).strip().lower()
    terminal_event_seen = False
    running_event_seen = False
    if event is not None:
        frontier = _mapping(event.bounded_index.get("eventFrontier"))
        terminal_event_seen = frontier.get("terminalEventSeen") is True
        running_event_seen = frontier.get("runningEventAfterCursor") is True
    if terminal_event_seen or raw_status in _TERMINAL_PROVIDER_STATES:
        return True, False
    if running_event_seen or raw_status in _ACTIVE_PROVIDER_STATES:
        return False, True
    if raw_status:
        _increment_metric(metrics.UNKNOWN_PROVIDER_STATUS)
    return None, None


def _lease_signals(
    snapshot: Optional[ObservationRecord],
) -> tuple[Optional[bool], Optional[bool], Optional[bool], Optional[bool]]:
    if snapshot is None:
        return None, None, None, None
    host = _mapping(snapshot.bounded_index.get("hostLease"))
    profile = _mapping(snapshot.bounded_index.get("profileLease"))
    host_active = host.get("held") if isinstance(host.get("held"), bool) else None
    host_owner = (
        host.get("consumerActive")
        if isinstance(host.get("consumerActive"), bool)
        else None
    )
    profile_active = (
        profile.get("held") if isinstance(profile.get("held"), bool) else None
    )
    profile_consumer = (
        profile.get("consumerActive")
        if isinstance(profile.get("consumerActive"), bool)
        else None
    )
    return host_active, host_owner, profile_active, profile_consumer


def _parse_datetime(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _consecutive_no_progress(
    session: SessionRecord,
    decisions: list[DecisionRecord],
) -> int:
    count = 0
    frontier: Optional[str] = None
    for decision in decisions:
        if decision.expected_revision != session.revision:
            break
        if frontier is None:
            frontier = decision.observation_frontier_digest
        elif decision.observation_frontier_digest != frontier:
            break
        count += 1
    return count


async def inspect_stuck_state(
    repos: ControlPlaneRepositories,
    *,
    session_id: str,
    now: datetime,
    policy: StuckStatePolicy = StuckStatePolicy(),
) -> Optional[StuckStateInspection]:
    """Load bounded durable evidence and evaluate one canonical session."""

    with spans.omnigent_span(spans.OBSERVATION_LOAD, observation_source="durable_index"):
        session = await repos.sessions.get(session_id)
        if session is None or session.historical_read_state == "quarantined":
            return None
        active_turn = (
            await repos.turn_attempts.get(session.active_turn_attempt_id)
            if session.active_turn_attempt_id is not None
            else None
        )
        latest_event = await repos.observations.latest_for_session(
            session_id, observation_types=_EVENT_OBSERVATION_TYPES
        )
        latest_snapshot = await repos.observations.latest_for_session(
            session_id, observation_types=_SNAPSHOT_OBSERVATION_TYPES
        )
        latest_liveness = await repos.observations.latest_for_session(
            session_id, observation_types=_LIVENESS_OBSERVATION_TYPES
        )
        active_command = await repos.commands.active_for_session(session_id)
        cleanup = await repos.cleanup.get(session_id)
        recent_decisions = await repos.decisions.recent_for_session(
            session_id, limit=max(policy.no_progress_max, 1)
        )

    provider_terminal, provider_active = _provider_state(latest_snapshot, latest_event)
    host_active, host_owner, profile_active, profile_consumer = _lease_signals(
        latest_snapshot
    )
    cleanup_started_at = None
    if cleanup is not None and cleanup.state == "claimed":
        cleanup_started_at = cleanup.updated_at or cleanup.created_at
    substantive_at = max(
        (
            item.observed_at
            for item in (latest_event, latest_snapshot)
            if item is not None
        ),
        default=None,
    )
    liveness_only_since = None
    if latest_liveness is not None and (
        substantive_at is None or latest_liveness.observed_at > substantive_at
    ):
        liveness_only_since = substantive_at or latest_liveness.observed_at
    compatibility = (
        _mapping(latest_snapshot.bounded_index.get("compatibility"))
        if latest_snapshot is not None
        else {}
    )
    compatibility_known: Optional[bool]
    if isinstance(compatibility.get("runtimeReady"), bool):
        compatibility_known = bool(compatibility["runtimeReady"])
    elif session.compatibility_ref is not None:
        compatibility_known = True
    else:
        compatibility_known = None
    metadata = session.metadata or {}
    conformance_at = _parse_datetime(metadata.get("protected_live_evidence_at"))
    runner_available = metadata.get("conformance_runner_available")
    if not isinstance(runner_available, bool):
        runner_available = None
    signals = SessionSignals(
        last_event_at=latest_event.observed_at if latest_event is not None else None,
        last_snapshot_at=(
            latest_snapshot.observed_at if latest_snapshot is not None else None
        ),
        active_turn_started_at=(active_turn.created_at if active_turn is not None else None),
        liveness_only_since=liveness_only_since,
        provider_terminal=provider_terminal,
        provider_active=provider_active,
        host_lease_active=host_active,
        host_lease_owns_session_authority=host_owner,
        profile_lease_active=profile_active,
        profile_lease_has_consumer=profile_consumer,
        compatibility_known=compatibility_known,
        admitted=session.provider_session_ref is not None,
        active_command=active_command,
        active_command_since=(
            active_command.updated_at or active_command.created_at
            if active_command is not None
            else None
        ),
        cleanup_started_at=cleanup_started_at,
        conformance_evidence_at=conformance_at,
        conformance_runner_available=runner_available,
        consecutive_no_progress=_consecutive_no_progress(session, recent_decisions),
    )
    if liveness_only_since is not None:
        _observe_metric(
            metrics.LIVENESS_ONLY_DURATION,
            _elapsed_seconds(now, liveness_only_since),
        )
    if cleanup_started_at is not None:
        _observe_metric(
            metrics.CLEANUP_LAG,
            _elapsed_seconds(now, cleanup_started_at),
        )
    if host_active is True and host_owner is False:
        _increment_metric(metrics.ORPHANED_LEASES, lease_scope="host")
    if profile_active is True and profile_consumer is False:
        _increment_metric(metrics.ORPHANED_LEASES, lease_scope="provider_profile")
    _increment_metric(
        metrics.DEPLOYED_BUILD_COMPATIBILITY,
        status=(
            "ok"
            if compatibility_known is True
            else "drift"
            if compatibility_known is False
            else "unknown"
        ),
    )
    _increment_metric(
        metrics.EXACT_IMAGE_CONFORMANCE,
        status="ok" if session.image_manifest_ref is not None else "unknown",
    )
    _increment_metric(
        metrics.PROVIDER_VERIFICATION_RUNNER_HEALTH,
        status=(
            "ok"
            if runner_available is True
            else "degraded"
            if runner_available is False
            else "unknown"
        ),
    )
    with spans.omnigent_span(
        spans.STUCK_STATE_INSPECT,
        durable_state=session.reconciled_state or session.desired_state,
        observed_state=session.observed_state,
        expected_revision=session.revision,
        fencing_generation_ordinal=session.fencing_generation,
    ):
        findings = detect_stuck_state(
            session=session, signals=signals, now=now, policy=policy
        )
    prior_count = 0
    if findings:
        prior_count = await repos.decisions.count_for_session_reason(
            session_id, findings[0].reason.value
        )
    response = plan_response(
        session=session,
        findings=findings,
        prior_detection_count=prior_count,
        policy=policy,
    )
    return StuckStateInspection(session, tuple(findings), response)


class StuckStateReconciliationService:
    """Periodically converge stuck canonical sessions without provider guesses."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
        dispatcher: ReconcileDispatcher,
        diagnostic_publisher: DiagnosticPublisher,
        policy: StuckStatePolicy = StuckStatePolicy(),
    ) -> None:
        self._session_factory = session_factory
        self._dispatcher = dispatcher
        self._diagnostics = diagnostic_publisher
        self._policy = policy

    async def sweep(
        self, *, now: Optional[datetime] = None, limit: int = 100
    ) -> StuckStateSweepResult:
        observed_now = now or datetime.now(UTC)
        result = StuckStateSweepResult()
        async with self._session_factory() as db:
            repos = ControlPlaneRepositories.bind(db)
            candidates = await repos.sessions.list_reconciliation_candidates(limit=limit)
        for candidate in candidates:
            result.scanned += 1
            try:
                await self._reconcile_one(candidate.session_id, observed_now, result)
            except Exception:
                # One provider/artifact/storage failure must not starve every
                # later candidate in the bounded batch. The durable schedule
                # retries failed candidates on its next tick.
                result.failures += 1
                logger.warning(
                    "Omnigent stuck-state candidate inspection failed",
                    exc_info=True,
                )
        return result

    async def validate_reconcile_request(
        self,
        *,
        session_id: str,
        workflow_id: str,
        request_id: str,
        reason_code: str,
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> dict[str, object]:
        """Validate a workflow-accepted request against canonical DB authority.

        The command must still be the claimed journal entry created by this
        detector. A delivery-unknown command is deliberately rejected until a
        later provider observation advances beyond it, so this boundary can
        never turn ambiguous delivery into a blind retry.
        """

        async with self._session_factory() as db:
            repos = ControlPlaneRepositories.bind(db)
            session = await repos.sessions.load_for_update(session_id)
            command = await repos.commands.get(request_id)
            decision = await repos.decisions.for_resulting_command(request_id)
            if session is None or session.moonmind_workflow_id != workflow_id:
                raise ValueError("reconcile request does not own canonical workflow scope")
            if (
                session.revision != expected_revision
                or session.fencing_generation != expected_fencing_generation
            ):
                raise ValueError("reconcile request lost revision or fencing authority")
            if (
                command is None
                or command.session_id != session_id
                or command.command_type != "request_reconcile"
                or command.expected_session_revision != expected_revision
                or command.fencing_generation != expected_fencing_generation
                or decision is None
                or decision.session_id != session_id
                or decision.reason_code != reason_code
                or decision.expected_revision != expected_revision
                or decision.fencing_generation != expected_fencing_generation
            ):
                raise ValueError("reconcile request does not match its durable command")
            if command.status != COMMAND_STATE_CLAIMED:
                raise ValueError(
                    "reconcile command is not freshly claimed; delivery ambiguity requires observation"
                )
        return {
            "accepted": True,
            "expectedRevision": expected_revision,
            "expectedFencingGeneration": expected_fencing_generation,
        }

    async def _inspect(
        self, session_id: str, now: datetime
    ) -> Optional[StuckStateInspection]:
        async with self._session_factory() as db:
            repos = ControlPlaneRepositories.bind(db)
            return await inspect_stuck_state(
                repos,
                session_id=session_id,
                now=now,
                policy=self._policy,
            )

    @staticmethod
    def _bucket(now: datetime) -> int:
        seconds = max(1, int(_DETECTION_BUCKET.total_seconds()))
        return int(now.timestamp()) // seconds

    @staticmethod
    def _identity(
        *, session: SessionRecord, reason: str, now: datetime, bucketed: bool
    ) -> str:
        payload: list[object] = [
            "omnigent-stuck-state/v1",
            session.session_id,
            reason,
            session.revision,
            session.fencing_generation,
        ]
        if bucketed:
            payload.append(StuckStateReconciliationService._bucket(now))
        return compute_digest(payload)[:40]

    @staticmethod
    def _diagnostic_payload(
        inspection: StuckStateInspection, now: datetime
    ) -> dict[str, object]:
        response = inspection.response
        assert response is not None
        session = inspection.session
        return {
            "schemaVersion": "moonmind.omnigent-stuck-state-diagnostic/v1",
            "issue": "MoonLadderStudios/MoonMind#3708",
            "sessionId": session.session_id,
            "observedAt": now.isoformat(),
            "state": {
                "desired": session.desired_state,
                "durable": session.reconciled_state or session.desired_state,
                "observed": session.observed_state,
                "revision": session.revision,
                "fencingGeneration": session.fencing_generation,
            },
            "reasons": [finding.reason.value for finding in inspection.findings],
            "detectionCount": response.diagnostics.get("detection_count"),
            "remediation": response.remediation,
        }

    async def _record_observation_and_decision(
        self,
        inspection: StuckStateInspection,
        *,
        now: datetime,
        decision_id: str,
        decision_code: str,
        diagnostics_ref: Optional[str] = None,
        resulting_command_id: Optional[str] = None,
    ) -> tuple[bool, bool]:
        """Persist evidence and report authority validity plus insert status."""

        session = inspection.session
        reason = inspection.findings[0].reason.value
        detection_identity = self._identity(
            session=session, reason=reason, now=now, bucketed=True
        )
        response_class = (
            "quarantine"
            if inspection.response and inspection.response.quarantine
            else "reconcile"
            if inspection.response and inspection.response.reconcile
            else "observe"
        )
        async with self._session_factory() as db:
            repos = ControlPlaneRepositories.bind(db)
            current = await repos.sessions.load_for_update(session.session_id)
            if current is None or (
                current.revision != session.revision
                or current.fencing_generation != session.fencing_generation
                or current.historical_read_state == "quarantined"
            ):
                await db.rollback()
                return False, False
            existing = await repos.decisions.get(decision_id)
            await repos.observations.append(
                observation_id=f"ost_{detection_identity}",
                session_id=session.session_id,
                observation_type="stuck_state",
                source="omnigent_stuck_state_detector",
                observed_at=now,
                deduplication_key=f"stuck-state:{detection_identity}",
                source_digest=compute_digest(
                    [finding.reason.value for finding in inspection.findings]
                ),
                bounded_index={
                    "reason": reason,
                    "response": response_class,
                    "detectionCount": inspection.response.diagnostics.get(
                        "detection_count"
                    )
                    if inspection.response
                    else 0,
                },
            )
            await repos.decisions.append(
                decision_id=decision_id,
                session_id=session.session_id,
                decision_code=decision_code,
                expected_revision=session.revision,
                fencing_generation=session.fencing_generation,
                reason_code=reason,
                resulting_command_id=resulting_command_id,
                next_deadline=session.next_reconciliation_deadline,
                diagnostics_ref=diagnostics_ref,
            )
            await db.commit()
            return True, existing is None

    async def _reconcile_one(
        self, session_id: str, now: datetime, result: StuckStateSweepResult
    ) -> None:
        inspection = await self._inspect(session_id, now)
        if inspection is None or not inspection.findings or inspection.response is None:
            return
        response = inspection.response
        session = inspection.session
        reason = inspection.findings[0].reason.value
        decision_identity = self._identity(
            session=session, reason=reason, now=now, bucketed=True
        )
        decision_id = f"odc_{decision_identity}"

        if response.quarantine:
            async with self._session_factory() as db:
                existing = await ControlPlaneRepositories.bind(db).decisions.get(
                    decision_id
                )
            diagnostic_ref = existing.diagnostics_ref if existing is not None else None
            if diagnostic_ref is None:
                diagnostic_ref = await self._diagnostics.publish(
                    session=session,
                    decision_id=decision_id,
                    payload=self._diagnostic_payload(inspection, now),
                )
            authority_valid, recorded = await self._record_observation_and_decision(
                inspection,
                now=now,
                decision_id=decision_id,
                decision_code="quarantine_ambiguous_state",
                diagnostics_ref=diagnostic_ref,
            )
            if not authority_valid:
                result.conflicts += 1
                return
            async with self._session_factory() as db:
                repos = ControlPlaneRepositories.bind(db)
                cas = await repos.sessions.compare_and_swap_session(
                    session.session_id,
                    expected_revision=session.revision,
                    expected_fencing_generation=session.fencing_generation,
                    reconciled_state="quarantined",
                    historical_read_state="quarantined",
                    next_reconciliation_deadline=None,
                    last_decision_ref=decision_id,
                )
                if cas.outcome is not ControlPlaneOutcome.APPLIED:
                    await db.rollback()
                    result.conflicts += 1
                    return
                await db.commit()
            _increment_metric(metrics.QUARANTINED_AMBIGUITY)
            result.findings_recorded += int(recorded)
            result.quarantined += 1
            return

        decision_code = (
            "stuck_state_reconcile_requested"
            if response.reconcile
            else "stuck_state_observed"
        )
        command_identity = (
            self._identity(session=session, reason=reason, now=now, bucketed=False)
            if response.reconcile
            else None
        )
        command_id = f"ocm_{command_identity}" if command_identity is not None else None
        authority_valid, recorded = await self._record_observation_and_decision(
            inspection,
            now=now,
            decision_id=decision_id,
            decision_code=decision_code,
            resulting_command_id=command_id,
        )
        if not authority_valid:
            result.conflicts += 1
            return
        if recorded:
            result.findings_recorded += 1
        if not response.reconcile:
            result.observation_only += int(recorded)
            return

        assert command_identity is not None and command_id is not None
        claim_token = f"stuck-state:{command_identity}"
        async with self._session_factory() as db:
            repos = ControlPlaneRepositories.bind(db)
            command = await repos.commands.record(
                command_id=command_id,
                session_id=session.session_id,
                command_type="request_reconcile",
                idempotency_key=f"omnigent-stuck-reconcile:{command_identity}",
                payload_digest=compute_digest(
                    {
                        "reason": reason,
                        "expectedRevision": response.expected_revision,
                        "expectedFencingGeneration": response.expected_fencing_generation,
                    }
                ),
                expected_session_revision=response.expected_revision,
                fencing_generation=response.expected_fencing_generation,
                owner_class="stuck_state_detector",
            )
            if command.status not in {COMMAND_STATE_PENDING, COMMAND_STATE_CLAIMED}:
                await db.commit()
                return
            claim = await repos.commands.claim_command(
                command_id,
                owner_class="stuck_state_detector",
                claim_token=claim_token,
            )
            if claim.outcome is not ControlPlaneOutcome.APPLIED:
                await db.commit()
                return
            await db.commit()

        try:
            with spans.omnigent_span(
                spans.COMMAND_EXECUTE,
                command_class="request_reconcile",
                reason_code=reason,
                expected_revision=response.expected_revision,
                fencing_generation_ordinal=response.expected_fencing_generation,
            ):
                await self._dispatcher.request_reconcile(
                    session_id=session.session_id,
                    workflow_id=session.moonmind_workflow_id,
                    request_id=command_id,
                    reason_code=reason,
                    expected_revision=str(response.expected_revision),
                    expected_fencing_generation=str(
                        response.expected_fencing_generation
                    ),
                )
        except Exception:
            delivery = ControlPlaneOutcome.DELIVERY_UNKNOWN
            result.delivery_unknown += 1
        else:
            delivery = ControlPlaneOutcome.APPLIED
            result.reconcile_requests += 1
        async with self._session_factory() as db:
            repos = ControlPlaneRepositories.bind(db)
            await repos.commands.record_command_delivery(
                command_id,
                owner_class="stuck_state_detector",
                claim_token=claim_token,
                outcome=delivery,
            )
            await db.commit()
        if StuckStateReason.REPEATED_RECONCILIATION_NO_PROGRESS in {
            finding.reason for finding in inspection.findings
        }:
            _increment_metric(metrics.REPEATED_NO_PROGRESS_DECISIONS)


__all__ = [
    "DiagnosticPublisher",
    "ReconcileDispatcher",
    "StuckStateInspection",
    "StuckStateReconciliationService",
    "StuckStateSweepResult",
    "inspect_stuck_state",
]
