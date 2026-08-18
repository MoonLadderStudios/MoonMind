"""Pure Omnigent lifecycle reducer.

Source issue: MoonLadderStudios/MoonMind#3702.

``reconcile(intent, durable, observations, now)`` converts immutable intent,
current durable state, and authoritative observations into exactly one
:class:`ReconciliationDecision`. It performs **no** database, network,
filesystem, Docker, artifact, logging, telemetry, or Temporal call, and it never
mutates its inputs. It is deterministic: equal inputs always produce an equal
decision (invariant 12).

The reducer encodes the twelve required invariants from the issue. Each guard is
annotated with the invariant number it enforces.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .contracts import (
    COMMAND_DECISION_KINDS,
    CommandSpec,
    CompiledSessionIntent,
    DecisionDiagnostics,
    DecisionKind,
    DesiredLifecycle,
    DurableSessionState,
    EvidenceRequirement,
    KNOWN_COMPATIBILITY_VERSIONS,
    LINEAR_PHASE_ORDER,
    LeaseState,
    ObservationSet,
    ProviderStatusClass,
    ReasonCode,
    RECONCILER_CONTRACT_VERSION,
    ReconciliationDecision,
    SETTLED_DECISION_KINDS,
    SessionLifecyclePhase,
    ShadowComparison,
    SubmissionState,
    TerminalOutcome,
)

# ---------------------------------------------------------------------------
# Provider status vocabulary
#
# Generalizes the terminal/non-terminal vocabulary that lives in
# ``moonmind/omnigent/bridge_events.py`` (the #3683 vocabulary). Kept as data
# here so the pure reducer never imports the bridge modules (which pull in
# security/artifact helpers). Unknown statuses fall through to
# ``ProviderStatusClass.UNKNOWN`` and fail closed (invariant 6).
# ---------------------------------------------------------------------------

_ACTIVE_STATUSES = frozenset(
    {"created", "launching", "provisioning", "running", "waiting", "queued", "in_progress"}
)
_IDLE_STATUSES = frozenset({"idle"})
_TERMINAL_SUCCESS_STATUSES = frozenset({"completed"})
_TERMINAL_FAILURE_STATUSES = frozenset({"failed"})
_TERMINAL_CANCELLED_STATUSES = frozenset({"canceled", "cancelled", "timed_out", "timeout"})


def classify_provider_status(raw_status: str) -> ProviderStatusClass:
    """Classify a raw provider status string into a typed class.

    Unknown vocabulary maps to :attr:`ProviderStatusClass.UNKNOWN` — it is never
    silently mapped to success (invariant 6).
    """

    normalized = raw_status.strip().lower()
    if normalized in _ACTIVE_STATUSES:
        return ProviderStatusClass.ACTIVE
    if normalized in _IDLE_STATUSES:
        return ProviderStatusClass.IDLE
    if normalized in _TERMINAL_SUCCESS_STATUSES:
        return ProviderStatusClass.TERMINAL_SUCCESS
    if normalized in _TERMINAL_FAILURE_STATUSES:
        return ProviderStatusClass.TERMINAL_FAILURE
    if normalized in _TERMINAL_CANCELLED_STATUSES:
        return ProviderStatusClass.TERMINAL_CANCELLED
    return ProviderStatusClass.UNKNOWN


_CLASS_TO_OUTCOME: dict[ProviderStatusClass, TerminalOutcome] = {
    ProviderStatusClass.TERMINAL_SUCCESS: TerminalOutcome.SUCCESS,
    ProviderStatusClass.TERMINAL_FAILURE: TerminalOutcome.FAILURE,
    ProviderStatusClass.TERMINAL_CANCELLED: TerminalOutcome.CANCELLED,
}

_TERMINAL_CLASSES = frozenset(_CLASS_TO_OUTCOME)


# ---------------------------------------------------------------------------
# Derived lifecycle phase (used for the monotonic-lifecycle invariant/tests)
# ---------------------------------------------------------------------------


def current_phase(durable: DurableSessionState) -> SessionLifecyclePhase:
    """Derive the current lifecycle phase from durable authority fields.

    The phase is *derived*, not stored, so there is a single source of truth for
    lifecycle state (Simplicity Gate).
    """

    if durable.failed:
        return SessionLifecyclePhase.FAILED
    if durable.quarantined:
        return SessionLifecyclePhase.QUARANTINED

    if durable.terminal_outcome is None:
        if durable.submission == SubmissionState.ACCEPTED:
            return SessionLifecyclePhase.TURN_IN_FLIGHT
        if durable.provider_session_attached:
            return SessionLifecyclePhase.PROVIDER_SESSION_READY
        if durable.host_lease == LeaseState.HELD:
            return SessionLifecyclePhase.HOST_READY
        if durable.profile_lease == LeaseState.HELD:
            return SessionLifecyclePhase.PROFILE_LEASE_HELD
        return SessionLifecyclePhase.INITIALIZING

    leases_settled = (
        durable.profile_lease != LeaseState.HELD
        and durable.host_lease != LeaseState.HELD
    )
    if leases_settled and durable.cleanup_complete:
        return SessionLifecyclePhase.CLOSED
    if durable.profile_lease == LeaseState.RELEASED or durable.host_lease == LeaseState.RELEASED:
        return SessionLifecyclePhase.LEASES_RELEASED
    if durable.cleanup_started:
        return SessionLifecyclePhase.CLEANUP_STARTED
    if durable.evidence_harvested:
        return SessionLifecyclePhase.EVIDENCE_HARVESTED
    return SessionLifecyclePhase.TERMINAL_RECORDED


# ---------------------------------------------------------------------------
# Decision construction helpers
# ---------------------------------------------------------------------------


def _command_id(durable: DurableSessionState, kind: DecisionKind) -> str:
    """Deterministic idempotency identity for a side-effect command.

    ``submit_turn`` is scoped by the current attempt so re-running the reducer on
    an unchanged durable state yields the identical command id (at-most-once
    submission, invariant 7). Other commands are scoped by session + fencing
    generation + kind, which is stable until the durable state advances.
    """

    if kind == DecisionKind.SUBMIT_TURN:
        token = durable.attempt_id or "unknown_attempt"
    else:
        token = kind.value
    return f"{durable.session_id}:g{durable.fencing_generation}:{token}"


def _decision(
    *,
    kind: DecisionKind,
    reason: ReasonCode,
    intent: CompiledSessionIntent,
    durable: DurableSessionState,
    observations: ObservationSet,
    now: datetime,
    product_visible: bool = False,
    evidence: tuple[EvidenceRequirement, ...] = (),
) -> ReconciliationDecision:
    """Build a decision, wiring durable authority and the bounded deadline.

    ``expected_revision`` / ``expected_fencing_generation`` always come from
    durable state, never from an observation or from intent (invariant 11), so
    the executor cannot ignore concurrency authority. Settled decisions carry no
    deadline; every other (nonterminal) decision carries a bounded deadline
    (invariant 10).
    """

    command: CommandSpec | None = None
    if kind in COMMAND_DECISION_KINDS:
        command = CommandSpec(
            command_kind=kind,
            command_id=_command_id(durable, kind),
            attempt_id=durable.attempt_id if kind == DecisionKind.SUBMIT_TURN else None,
            provider_session_id=durable.provider_session_id,
        )

    next_deadline: datetime | None
    if kind in SETTLED_DECISION_KINDS:
        next_deadline = None
    else:
        next_deadline = now + timedelta(seconds=intent.reconcile_interval_seconds)

    provider_status_class: ProviderStatusClass | None = None
    if observations.provider_session is not None:
        provider_status_class = classify_provider_status(
            observations.provider_session.raw_status
        )

    return ReconciliationDecision(
        kind=kind,
        reason_code=reason,
        expected_revision=durable.revision,
        expected_fencing_generation=durable.fencing_generation,
        command=command,
        next_deadline=next_deadline,
        evidence_requirements=evidence,
        changes_product_visible_state=product_visible,
        diagnostics=DecisionDiagnostics(
            present_observations=observations.present_observation_kinds(),
            provider_status_class=provider_status_class,
        ),
    )


# ---------------------------------------------------------------------------
# The reducer
# ---------------------------------------------------------------------------


def reconcile(
    *,
    intent: CompiledSessionIntent,
    durable: DurableSessionState,
    observations: ObservationSet,
    now: datetime,
) -> ReconciliationDecision:
    """Compute the single next :class:`ReconciliationDecision`.

    Pure and deterministic: no side effects, no I/O, no hidden clock. ``now`` is
    supplied by the caller and only feeds the bounded next deadline.
    """

    def decide(
        kind: DecisionKind,
        reason: ReasonCode,
        *,
        product_visible: bool = False,
        evidence: tuple[EvidenceRequirement, ...] = (),
    ) -> ReconciliationDecision:
        return _decision(
            kind=kind,
            reason=reason,
            intent=intent,
            durable=durable,
            observations=observations,
            now=now,
            product_visible=product_visible,
            evidence=evidence,
        )

    # -- G0. Version / compatibility fail-closed --------------------------
    # The typed models reject unknown *fields* and unknown schema *versions* at
    # construction (extra="forbid", Literal["v1"]). This guard additionally
    # fails closed when a caller bypasses validation (e.g. model_construct) so
    # the boundary policy is explicit and testable (invariant 6).
    for obj in (intent, durable, observations):
        if getattr(obj, "schema_version", None) != RECONCILER_CONTRACT_VERSION:
            return decide(
                DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
                ReasonCode.UNKNOWN_INPUT_VERSION,
            )

    # -- G1. Sticky durable meta-terminal states -------------------------
    if durable.failed:
        return decide(DecisionKind.FAIL_NONRETRYABLE, ReasonCode.SESSION_FAILED)
    if durable.quarantined:
        return decide(
            DecisionKind.QUARANTINE_AMBIGUOUS_STATE, ReasonCode.SESSION_QUARANTINED
        )

    # -- G2. Compatibility observation gate (invariant 6) ----------------
    comp = observations.compatibility
    if comp is not None:
        if comp.compatibility_version not in KNOWN_COMPATIBILITY_VERSIONS:
            return decide(
                DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
                ReasonCode.UNKNOWN_COMPATIBILITY_VERSION,
            )
        if not comp.runtime_ready:
            return decide(
                DecisionKind.AWAIT_OBSERVATION, ReasonCode.RUNTIME_NOT_READY
            )

    # -- G3. Terminal already recorded: post-terminal chain --------------
    # (invariants 5, 8, 9)
    if durable.terminal_outcome is not None:
        return _post_terminal(intent, durable, observations, decide)

    # -- G4. Desired cancellation records a cancelled terminal -----------
    if durable.desired == DesiredLifecycle.CANCEL:
        return decide(
            DecisionKind.RECORD_PROVIDER_TERMINAL,
            ReasonCode.DESIRED_CANCELLATION,
            product_visible=True,
        )

    # -- G5. Provider Profile lease --------------------------------------
    if intent.requires_profile_lease and durable.profile_lease != LeaseState.HELD:
        return decide(
            DecisionKind.ENSURE_PROFILE_LEASE, ReasonCode.PROFILE_LEASE_REQUIRED
        )

    # -- G6. Host --------------------------------------------------------
    if intent.requires_host and durable.host_lease != LeaseState.HELD:
        return decide(DecisionKind.ENSURE_HOST, ReasonCode.HOST_REQUIRED)

    # -- G7. Provider session --------------------------------------------
    if not durable.provider_session_attached:
        return decide(
            DecisionKind.ENSURE_PROVIDER_SESSION, ReasonCode.PROVIDER_SESSION_REQUIRED
        )

    # -- G8. Turn submission (at-most-once, invariant 7) -----------------
    if durable.submission == SubmissionState.NOT_SUBMITTED:
        if durable.turn_attempts >= intent.max_turn_attempts:
            return decide(
                DecisionKind.FAIL_NONRETRYABLE,
                ReasonCode.MAX_TURN_ATTEMPTS_EXHAUSTED,
            )
        return decide(
            DecisionKind.SUBMIT_TURN,
            ReasonCode.TURN_SUBMISSION_REQUIRED,
            product_visible=True,
        )
    if durable.submission == SubmissionState.IN_FLIGHT:
        # Delivery is ambiguous; never reissue the submit (invariant 7). Wait for
        # an observation that lets the executor durably confirm acceptance.
        return decide(
            DecisionKind.AWAIT_OBSERVATION, ReasonCode.SUBMISSION_DELIVERY_AMBIGUOUS
        )

    # -- G9. Submission accepted: detect terminal ------------------------
    return _detect_terminal(intent, durable, observations, decide)


def _detect_terminal(intent, durable, observations, decide):
    """Decide the next step while awaiting a terminal for an accepted turn.

    A provider event/snapshot is treated as an *observation*, not an unquestioned
    state mutation (invariant 1): the reducer only recommends recording a
    terminal, it never assumes durable state changed.
    """

    ps = observations.provider_session
    if ps is None:
        return decide(
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.AWAITING_PROVIDER_SNAPSHOT,
            evidence=(EvidenceRequirement.PROVIDER_TERMINAL_SNAPSHOT,),
        )
    if not ps.present:
        # Observed-negative: the durable session claims an attachment but the
        # provider reports none. This contradiction is ambiguous — fail closed.
        return decide(
            DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
            ReasonCode.PROVIDER_SESSION_MISSING,
        )

    status_class = classify_provider_status(ps.raw_status)

    if status_class == ProviderStatusClass.UNKNOWN:
        # Fail closed to observation; never map unknown vocabulary to success.
        return decide(
            DecisionKind.AWAIT_OBSERVATION, ReasonCode.UNKNOWN_PROVIDER_STATUS
        )

    if status_class == ProviderStatusClass.ACTIVE:
        return decide(DecisionKind.AWAIT_OBSERVATION, ReasonCode.PROVIDER_RUNNING)

    if status_class == ProviderStatusClass.IDLE:
        # Idle alone is not terminal when a tool call is still open (invariant 3).
        if ps.open_tool_call:
            return decide(
                DecisionKind.AWAIT_OBSERVATION, ReasonCode.IDLE_WITH_OPEN_TOOL_CALL
            )
        pt = observations.provider_turn
        if pt is not None and pt.turn_complete:
            # Idle after completed work — recover the terminal from corroborating
            # transcript evidence (#3683).
            return decide(
                DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
                ReasonCode.TERMINAL_IDLE_SYNTHESIS,
                product_visible=True,
                evidence=(EvidenceRequirement.PROVIDER_TURN_TRANSCRIPT,),
            )
        return decide(
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.IDLE_PENDING_TURN_EVIDENCE,
            evidence=(EvidenceRequirement.PROVIDER_TURN_TRANSCRIPT,),
        )

    # Provider status is an explicit terminal class.
    frontier = observations.event_frontier
    if frontier is not None and frontier.terminal_event_seen:
        return decide(
            DecisionKind.RECORD_PROVIDER_TERMINAL,
            ReasonCode.TERMINAL_EVENT_OBSERVED,
            product_visible=True,
            evidence=(EvidenceRequirement.PROVIDER_TERMINAL_SNAPSHOT,),
        )
    # Terminal snapshot but the terminal event edge was missed — recover it from
    # snapshot evidence (#3698).
    return decide(
        DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
        ReasonCode.TERMINAL_SNAPSHOT_SYNTHESIS,
        product_visible=True,
        evidence=(EvidenceRequirement.PROVIDER_TERMINAL_SNAPSHOT,),
    )


def _consumers_active(observations: ObservationSet) -> bool:
    """Whether a credential or host consumer is still observed (invariant 8)."""

    if observations.profile_lease is not None and observations.profile_lease.consumer_active:
        return True
    if observations.host_lease is not None and observations.host_lease.consumer_active:
        return True
    if observations.host is not None and observations.host.runner_ready:
        return True
    return False


def _post_terminal(intent, durable, observations, decide):
    """Harvest, cleanup, and lease-release chain after a terminal is recorded.

    Enforces:
      * invariant 5 — a stale/contradictory observation cannot move a terminal
        session backward;
      * invariant 8 — leases are not released while a consumer is still observed
        or durably owned;
      * invariant 9 — cleanup completion is distinct from task completion and
        never erases the recorded terminal outcome or evidence.
    """

    ps = observations.provider_session
    if ps is not None and ps.present:
        status_class = classify_provider_status(ps.raw_status)
        if status_class == ProviderStatusClass.ACTIVE:
            # A late running observation after terminal is stale — ignore it and
            # keep progressing toward cleanup; do not reopen the session.
            return _post_terminal_chain(
                intent,
                durable,
                observations,
                decide,
                stale_reason=ReasonCode.IGNORED_STALE_RUNNING_AFTER_TERMINAL,
            )
        if status_class in _TERMINAL_CLASSES:
            observed_outcome = _CLASS_TO_OUTCOME[status_class]
            if observed_outcome != durable.terminal_outcome:
                # Two different terminal outcomes is a genuine contradiction.
                return decide(
                    DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
                    ReasonCode.CONTRADICTORY_TERMINAL_OUTCOME,
                )

    return _post_terminal_chain(
        intent, durable, observations, decide, stale_reason=None
    )


def _post_terminal_chain(intent, durable, observations, decide, *, stale_reason):
    # Harvest terminal evidence first.
    if not durable.evidence_harvested:
        ev = observations.evidence
        if ev is None:
            return decide(
                DecisionKind.AWAIT_OBSERVATION,
                stale_reason or ReasonCode.AWAITING_EVIDENCE,
                evidence=(EvidenceRequirement.TERMINAL_EVIDENCE_ARTIFACT,),
            )
        if not ev.terminal_evidence_available:
            return decide(
                DecisionKind.RETRY_TRANSIENT_OBSERVATION,
                ReasonCode.EVIDENCE_NOT_YET_AVAILABLE,
                evidence=(EvidenceRequirement.TERMINAL_EVIDENCE_ARTIFACT,),
            )
        return decide(
            DecisionKind.HARVEST_EVIDENCE,
            stale_reason or ReasonCode.EVIDENCE_HARVEST_REQUIRED,
            evidence=(EvidenceRequirement.TERMINAL_EVIDENCE_ARTIFACT,),
        )

    # Then cleanup (distinct from task completion, invariant 9).
    if intent.requires_cleanup and not durable.cleanup_started:
        return decide(
            DecisionKind.BEGIN_CLEANUP,
            stale_reason or ReasonCode.CLEANUP_REQUIRED,
            evidence=(EvidenceRequirement.CLEANUP_EVIDENCE,),
        )

    # Then release leases last, after cleanup and once no consumer remains.
    leases_held = (
        durable.profile_lease == LeaseState.HELD
        or durable.host_lease == LeaseState.HELD
    )
    if leases_held:
        if _consumers_active(observations):
            return decide(
                DecisionKind.AWAIT_OBSERVATION, ReasonCode.LEASE_CONSUMERS_ACTIVE
            )
        if intent.requires_cleanup and not durable.cleanup_complete:
            return decide(
                DecisionKind.AWAIT_OBSERVATION,
                ReasonCode.CLEANUP_INCOMPLETE_BEFORE_RELEASE,
            )
        return decide(
            DecisionKind.RELEASE_LEASES,
            stale_reason or ReasonCode.LEASE_RELEASE_REQUIRED,
            evidence=(EvidenceRequirement.LEASE_RELEASE_CONFIRMATION,),
        )

    # Fully settled.
    return decide(DecisionKind.NO_OP, stale_reason or ReasonCode.SESSION_CLOSED)


# ---------------------------------------------------------------------------
# Shadow-mode comparison
# ---------------------------------------------------------------------------

#: Maps the legacy execution path's action vocabulary onto the reconciler's
#: decision vocabulary. Used only to compare the two along the existing path; the
#: reconciler never becomes a second orchestration source of truth.
LEGACY_ACTION_TO_DECISION_KIND: dict[str, DecisionKind] = {
    "noop": DecisionKind.NO_OP,
    "await": DecisionKind.AWAIT_OBSERVATION,
    "poll": DecisionKind.AWAIT_OBSERVATION,
    "ensure_profile_lease": DecisionKind.ENSURE_PROFILE_LEASE,
    "acquire_profile_lease": DecisionKind.ENSURE_PROFILE_LEASE,
    "ensure_host": DecisionKind.ENSURE_HOST,
    "launch_host": DecisionKind.ENSURE_HOST,
    "ensure_session": DecisionKind.ENSURE_PROVIDER_SESSION,
    "create_session": DecisionKind.ENSURE_PROVIDER_SESSION,
    "submit_turn": DecisionKind.SUBMIT_TURN,
    "post_first_message": DecisionKind.SUBMIT_TURN,
    "record_terminal": DecisionKind.RECORD_PROVIDER_TERMINAL,
    "synthesize_terminal": DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
    "reconcile_terminal_snapshot": DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
    "harvest": DecisionKind.HARVEST_EVIDENCE,
    "harvest_evidence": DecisionKind.HARVEST_EVIDENCE,
    "cleanup": DecisionKind.BEGIN_CLEANUP,
    "begin_cleanup": DecisionKind.BEGIN_CLEANUP,
    "release_leases": DecisionKind.RELEASE_LEASES,
    "retry": DecisionKind.RETRY_TRANSIENT_OBSERVATION,
    "quarantine": DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
    "fail": DecisionKind.FAIL_NONRETRYABLE,
}


def shadow_compare(
    legacy_action: str, decision: ReconciliationDecision
) -> ShadowComparison:
    """Compare a legacy action string against a reconciler decision.

    Returns a bounded, non-sensitive comparison record suitable for logging or
    persistence in shadow mode. It does not act on the divergence.
    """

    mapped = LEGACY_ACTION_TO_DECISION_KIND.get(legacy_action.strip().lower())
    if mapped is None:
        return ShadowComparison(
            legacy_action=legacy_action,
            decision_kind=decision.kind,
            agreement=False,
            divergence_reason="unknown_legacy_action",
        )
    if mapped == decision.kind:
        return ShadowComparison(
            legacy_action=legacy_action,
            decision_kind=decision.kind,
            agreement=True,
        )
    return ShadowComparison(
        legacy_action=legacy_action,
        decision_kind=decision.kind,
        agreement=False,
        divergence_reason=f"legacy_maps_to:{mapped.value}",
    )


__all__ = [
    "classify_provider_status",
    "current_phase",
    "reconcile",
    "shadow_compare",
    "LEGACY_ACTION_TO_DECISION_KIND",
]
