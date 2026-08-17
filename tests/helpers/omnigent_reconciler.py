"""Builders for Omnigent lifecycle reconciler unit tests.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

Compact, deterministic factories that default to the "happy" values for each
domain object so individual tests only override the field under test. A fixed
clock keeps every reconcile deterministic.
"""

from __future__ import annotations

from datetime import datetime, timezone

from moonmind.omnigent.reconciler import (
    CompiledSessionIntent,
    DurableSessionState,
    EventFrontier,
    EvidenceAvailability,
    HostRuntimeState,
    LeaseObservation,
    Observation,
    ObservationSet,
    ProviderSessionSnapshot,
    ProviderTurnSnapshot,
    RuntimeReadiness,
    TerminalEvidence,
    TurnAttempt,
    DesiredLifecycle,
    DurablePhase,
    TurnSubmissionState,
)

FIXED_NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)


def make_intent(**overrides) -> CompiledSessionIntent:
    params = dict(
        session_id="sess-1",
        provider="codex",
        agent_name="stock-coder",
        max_turn_attempts=1,
    )
    params.update(overrides)
    return CompiledSessionIntent(**params)


def make_durable(**overrides) -> DurableSessionState:
    params = dict(
        session_id="sess-1",
        revision=7,
        owner_token="owner-token",
        fencing_generation=3,
        desired=DesiredLifecycle.RUN,
        phase=DurablePhase.PENDING,
    )
    params.update(overrides)
    return DurableSessionState(**params)


def make_attempt(
    *,
    attempt_id: str = "attempt-1",
    attempt_number: int = 1,
    submission_state: TurnSubmissionState = TurnSubmissionState.SUBMITTED,
    retries_remaining: int = 0,
) -> TurnAttempt:
    return TurnAttempt(
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        submission_state=submission_state,
        retries_remaining=retries_remaining,
    )


def make_terminal_evidence(status: str = "completed", source: str = "provider_event"):
    return TerminalEvidence(status=status, source=source, recorded=True)


def session_obs(
    *,
    provider_session_id: str = "prov-1",
    raw_status: str = "running",
    digest: str = "digest-a",
    cursor: int = 1,
) -> Observation[ProviderSessionSnapshot]:
    return Observation.present(
        ProviderSessionSnapshot(
            provider_session_id=provider_session_id,
            raw_status=raw_status,
            snapshot_digest=digest,
            cursor=cursor,
        ),
        observed_at=FIXED_NOW,
        source="provider_session_snapshot",
    )


def turn_obs(
    *,
    attempt_id: str = "attempt-1",
    raw_status: str = "running",
    has_active_tool_call: bool = False,
    response_recorded: bool = False,
    transcript_digest: str = "t-digest",
) -> Observation[ProviderTurnSnapshot]:
    return Observation.present(
        ProviderTurnSnapshot(
            attempt_id=attempt_id,
            raw_status=raw_status,
            has_active_tool_call=has_active_tool_call,
            response_recorded=response_recorded,
            transcript_digest=transcript_digest,
        ),
        observed_at=FIXED_NOW,
        source="provider_turn_snapshot",
    )


def frontier_obs(
    *,
    last_cursor: int = 1,
    terminal_status: str | None = None,
    has_pending_tool_call: bool = False,
) -> Observation[EventFrontier]:
    return Observation.present(
        EventFrontier(
            last_cursor=last_cursor,
            terminal_status=terminal_status,
            has_pending_tool_call=has_pending_tool_call,
        ),
        observed_at=FIXED_NOW,
        source="event_frontier",
    )


def host_obs(
    *, registered: bool = True, runner_ready: bool = True
) -> Observation[HostRuntimeState]:
    return Observation.present(
        HostRuntimeState(registered=registered, runner_ready=runner_ready),
        observed_at=FIXED_NOW,
        source="host_runtime",
    )


def lease_obs(
    *,
    profile_lease_held: bool = True,
    host_lease_held: bool = True,
    active_consumers: int = 0,
) -> Observation[LeaseObservation]:
    return Observation.present(
        LeaseObservation(
            profile_lease_held=profile_lease_held,
            host_lease_held=host_lease_held,
            active_consumers=active_consumers,
        ),
        observed_at=FIXED_NOW,
        source="leases",
    )


def evidence_obs(
    *, artifact_available: bool = True, terminal_evidence_available: bool = True
) -> Observation[EvidenceAvailability]:
    return Observation.present(
        EvidenceAvailability(
            artifact_available=artifact_available,
            terminal_evidence_available=terminal_evidence_available,
        ),
        observed_at=FIXED_NOW,
        source="evidence",
    )


def readiness_obs(
    *, raw_compatibility: str = "compatible", ready: bool = True
) -> Observation[RuntimeReadiness]:
    return Observation.present(
        RuntimeReadiness(raw_compatibility=raw_compatibility, ready=ready),
        observed_at=FIXED_NOW,
        source="runtime_readiness",
    )


def make_observations(**overrides) -> ObservationSet:
    return ObservationSet(**overrides)


__all__ = [
    "FIXED_NOW",
    "evidence_obs",
    "frontier_obs",
    "host_obs",
    "lease_obs",
    "make_attempt",
    "make_durable",
    "make_intent",
    "make_observations",
    "make_terminal_evidence",
    "readiness_obs",
    "session_obs",
    "turn_obs",
]
