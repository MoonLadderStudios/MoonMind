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
#: Supported, actionable non-terminal product states emitted by the production
#: normalization boundary (approval/elicitation and intervention). The bridge
#: store accepts these as normal nonterminal statuses, so the reconciler must
#: model them explicitly instead of failing closed to ``UNKNOWN`` (invariant 6).
_INTERVENTION_STATUSES = frozenset({"awaiting_approval", "intervention_requested"})
_TERMINAL_SUCCESS_STATUSES = frozenset({"completed"})
#: Timeouts are a *system failure*, not a user cancellation. The existing
#: Omnigent bridge deliberately keeps ``timed_out`` distinct from cancellation
#: and maps it to failure; classifying it as cancelled would corrupt failure
#: classification and retry policy.
_TERMINAL_FAILURE_STATUSES = frozenset({"failed", "timed_out", "timeout"})
_TERMINAL_CANCELLED_STATUSES = frozenset({"canceled", "cancelled"})


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
    if normalized in _INTERVENTION_STATUSES:
        return ProviderStatusClass.INTERVENTION
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
    # Only report LEASES_RELEASED once *every* lease is settled. A partial
    # release (one lease released while the other is still HELD) must not be
    # reported as settled, or callers would treat a still-held host or profile
    # lease as released and break the monotonic phase view.
    any_released = (
        durable.profile_lease == LeaseState.RELEASED
        or durable.host_lease == LeaseState.RELEASED
    )
    if leases_settled and any_released:
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
    terminal_outcome: TerminalOutcome | None = None,
) -> ReconciliationDecision:
    """Build a decision, wiring durable authority and the bounded deadline.

    ``expected_revision`` / ``expected_fencing_generation`` always come from
    durable state, never from an observation or from intent (invariant 11), so
    the executor cannot ignore concurrency authority. Settled decisions carry no
    deadline; every other (nonterminal) decision carries a bounded deadline
    (invariant 10). ``terminal_outcome`` is carried on terminal
    recording/synthesis commands so the executor records the observed durable
    outcome without reimplementing reducer semantics.
    """

    command: CommandSpec | None = None
    if kind in COMMAND_DECISION_KINDS:
        command = CommandSpec(
            command_kind=kind,
            command_id=_command_id(durable, kind),
            attempt_id=durable.attempt_id if kind == DecisionKind.SUBMIT_TURN else None,
            provider_session_id=durable.provider_session_id,
            terminal_outcome=terminal_outcome,
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
        terminal_outcome: TerminalOutcome | None = None,
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
            terminal_outcome=terminal_outcome,
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

    # -- G0b. Intent / durable identity correlation ----------------------
    # The reducer combines policy and retry limits from the intent with
    # revision, fencing, lease, and command identity from the durable state. An
    # adapter that pairs intent for session B with durable state for session A
    # would otherwise authorize side effects on A under B's execution contract;
    # fail closed before evaluating provisioning or submission (invariant 11).
    if intent.session_id != durable.session_id:
        return decide(
            DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
            ReasonCode.SESSION_IDENTITY_MISMATCH,
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
            terminal_outcome=TerminalOutcome.CANCELLED,
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
            # Exhausting the attempt budget only happens after leases and the
            # provider session were acquired. Record a FAILURE terminal so the
            # owned post-terminal chain harvests evidence, cleans up, and
            # releases those resources instead of stopping at a settled dead end
            # that leaks the acquired leases and session.
            return decide(
                DecisionKind.RECORD_PROVIDER_TERMINAL,
                ReasonCode.MAX_TURN_ATTEMPTS_EXHAUSTED,
                product_visible=True,
                terminal_outcome=TerminalOutcome.FAILURE,
            )
        if durable.attempt_id is None:
            # Every attempt lacking a durable identity would receive the same
            # ``unknown_attempt`` idempotency id, so the executor could dedup a
            # legitimate later attempt as the first. Fail closed until a durable
            # attempt id exists rather than manufacturing a shared identity.
            return decide(
                DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
                ReasonCode.MISSING_ATTEMPT_IDENTITY,
            )
        return decide(
            DecisionKind.SUBMIT_TURN,
            ReasonCode.TURN_SUBMISSION_REQUIRED,
            product_visible=True,
        )
    if durable.submission == SubmissionState.IN_FLIGHT:
        # Delivery is ambiguous; never reissue the submit (invariant 7). If the
        # submit response was lost but an authoritative, correlated observation
        # now reports the matching turn, advance by consuming that evidence in
        # the terminal-detection path instead of waiting forever.
        ps = observations.provider_session
        if (
            ps is not None
            and ps.present
            and _observation_matches_session(durable, ps)
            and classify_provider_status(ps.raw_status) != ProviderStatusClass.UNKNOWN
        ):
            return _detect_terminal(intent, durable, observations, decide)
        return decide(
            DecisionKind.AWAIT_OBSERVATION, ReasonCode.SUBMISSION_DELIVERY_AMBIGUOUS
        )

    # -- G9. Submission accepted: detect terminal ------------------------
    return _detect_terminal(intent, durable, observations, decide)


def _observation_matches_session(durable, ps) -> bool:
    """Whether a provider-session observation correlates to the durable session.

    A snapshot that names a *different* provider session is not evidence about
    this session (invariant 11). When either side omits the id the observation is
    treated as correlated — absence is *not observed*, not a mismatch.
    """

    return not (
        ps.provider_session_id is not None
        and durable.provider_session_id is not None
        and ps.provider_session_id != durable.provider_session_id
    )


def _turn_matches_attempt(durable, pt) -> bool:
    """Whether a turn/transcript observation belongs to the current attempt.

    A delayed transcript from a *previous* turn carries a stale ``attempt_id``;
    it must not record the current attempt as terminal. Absent ids are treated
    as correlated (not observed), never as a mismatch.
    """

    return not (
        pt.attempt_id is not None
        and durable.attempt_id is not None
        and pt.attempt_id != durable.attempt_id
    )


def _detect_terminal(intent, durable, observations, decide):
    """Decide the next step while awaiting a terminal for an accepted turn.

    A provider event/snapshot is treated as an *observation*, not an unquestioned
    state mutation (invariant 1): the reducer only recommends recording a
    terminal, it never assumes durable state changed. Terminal recording is
    additionally gated on the snapshot/transcript correlating to the durable
    provider session and attempt identity, so a delayed terminal from a previous
    turn or another session cannot terminalize the current attempt.
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
    if not _observation_matches_session(durable, ps):
        # The snapshot names another provider session — it is not evidence about
        # this session. Wait for a correlated observation rather than recording a
        # terminal from an unrelated session.
        return decide(
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.AWAITING_CORRELATED_TERMINAL_EVIDENCE,
            evidence=(EvidenceRequirement.PROVIDER_TERMINAL_SNAPSHOT,),
        )

    status_class = classify_provider_status(ps.raw_status)

    if status_class == ProviderStatusClass.UNKNOWN:
        # Fail closed to observation; never map unknown vocabulary to success.
        return decide(
            DecisionKind.AWAIT_OBSERVATION, ReasonCode.UNKNOWN_PROVIDER_STATUS
        )

    if status_class == ProviderStatusClass.ACTIVE:
        return decide(DecisionKind.AWAIT_OBSERVATION, ReasonCode.PROVIDER_RUNNING)

    if status_class == ProviderStatusClass.INTERVENTION:
        # A supported, actionable product state (approval/elicitation or
        # intervention). Preserve it as product-visible instead of a generic
        # non-product-visible poll.
        return decide(
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.PROVIDER_INTERVENTION_REQUIRED,
            product_visible=True,
        )

    if status_class == ProviderStatusClass.IDLE:
        # Idle alone is not terminal when a tool call is still open (invariant 3).
        if ps.open_tool_call:
            return decide(
                DecisionKind.AWAIT_OBSERVATION, ReasonCode.IDLE_WITH_OPEN_TOOL_CALL
            )
        pt = observations.provider_turn
        if pt is not None and pt.turn_complete and _turn_matches_attempt(durable, pt):
            # Idle after completed work — recover the terminal from corroborating
            # transcript evidence (#3683), preserving the observed outcome so the
            # executor records the correct durable terminal.
            return decide(
                DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
                ReasonCode.TERMINAL_IDLE_SYNTHESIS,
                product_visible=True,
                evidence=(EvidenceRequirement.PROVIDER_TURN_TRANSCRIPT,),
                terminal_outcome=pt.outcome,
            )
        return decide(
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.IDLE_PENDING_TURN_EVIDENCE,
            evidence=(EvidenceRequirement.PROVIDER_TURN_TRANSCRIPT,),
        )

    # Provider status is an explicit terminal class; carry its outcome.
    observed_outcome = _CLASS_TO_OUTCOME[status_class]
    frontier = observations.event_frontier
    if frontier is not None and frontier.terminal_event_seen:
        return decide(
            DecisionKind.RECORD_PROVIDER_TERMINAL,
            ReasonCode.TERMINAL_EVENT_OBSERVED,
            product_visible=True,
            evidence=(EvidenceRequirement.PROVIDER_TERMINAL_SNAPSHOT,),
            terminal_outcome=observed_outcome,
        )
    # Terminal snapshot but the terminal event edge was missed — recover it from
    # snapshot evidence (#3698).
    return decide(
        DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
        ReasonCode.TERMINAL_SNAPSHOT_SYNTHESIS,
        product_visible=True,
        evidence=(EvidenceRequirement.PROVIDER_TERMINAL_SNAPSHOT,),
        terminal_outcome=observed_outcome,
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


def _consumers_confirmed_absent(durable, observations: ObservationSet) -> bool:
    """Whether fresh observations explicitly confirm no active lease consumer.

    ``None`` means *not observed*, not an observed negative, so an observation
    outage must not authorize releasing a still-consumed profile credential or
    host lease (invariant 8). Each currently-HELD lease requires a present
    observation reporting its consumer inactive before release is allowed.
    """

    if durable.profile_lease == LeaseState.HELD:
        obs = observations.profile_lease
        if obs is None or obs.consumer_active:
            return False
    if durable.host_lease == LeaseState.HELD:
        obs = observations.host_lease
        if obs is None or obs.consumer_active:
            return False
    return True


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
    # Harvest terminal evidence first. A partial/inconsistent durable update can
    # set ``evidence_harvested`` while the durable ``terminal_evidence_ref`` is
    # still missing; require the durable reference, not only the flag, before
    # advancing past this gate so cleanup/release cannot delete the authoritative
    # workspace before retrievable terminal evidence exists.
    if not durable.evidence_harvested or durable.terminal_evidence_ref is None:
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
        if not _consumers_confirmed_absent(durable, observations):
            # No consumer is currently *observed* active, but ``None`` means not
            # observed — a temporary observation outage must not release a lease
            # while its consumer may still be running. Require an explicit
            # observed-negative before authorizing release (invariant 8).
            return decide(
                DecisionKind.AWAIT_OBSERVATION,
                ReasonCode.AWAITING_LEASE_CONSUMER_CONFIRMATION,
                evidence=(EvidenceRequirement.LEASE_RELEASE_CONFIRMATION,),
            )
        return decide(
            DecisionKind.RELEASE_LEASES,
            stale_reason or ReasonCode.LEASE_RELEASE_REQUIRED,
            evidence=(EvidenceRequirement.LEASE_RELEASE_CONFIRMATION,),
        )

    # Cleanup completion gates closure independently of leases: a session whose
    # leases are already gone (partial executor update, or a session that never
    # required leases) must still not report SESSION_CLOSED while durable cleanup
    # is unfinished (invariant 9).
    if intent.requires_cleanup and not durable.cleanup_complete:
        return decide(
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.CLEANUP_INCOMPLETE_BEFORE_CLOSE,
        )

    # Fully settled.
    return decide(DecisionKind.NO_OP, stale_reason or ReasonCode.SESSION_CLOSED)


# ---------------------------------------------------------------------------
# Shadow-mode comparison
# ---------------------------------------------------------------------------

#: Maps the legacy execution path's action vocabulary onto the reconciler's
#: decision vocabulary. Used only to compare the two along the existing path; the
#: reconciler never becomes a second orchestration source of truth. One canonical
#: legacy action per decision kind — MoonMind's pre-release policy forbids keeping
#: multiple aliases for the same internal decision as a maintained contract.
LEGACY_ACTION_TO_DECISION_KIND: dict[str, DecisionKind] = {
    "noop": DecisionKind.NO_OP,
    "await": DecisionKind.AWAIT_OBSERVATION,
    "ensure_profile_lease": DecisionKind.ENSURE_PROFILE_LEASE,
    "ensure_host": DecisionKind.ENSURE_HOST,
    "ensure_session": DecisionKind.ENSURE_PROVIDER_SESSION,
    "submit_turn": DecisionKind.SUBMIT_TURN,
    "record_terminal": DecisionKind.RECORD_PROVIDER_TERMINAL,
    "synthesize_terminal": DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
    "harvest_evidence": DecisionKind.HARVEST_EVIDENCE,
    "begin_cleanup": DecisionKind.BEGIN_CLEANUP,
    "release_leases": DecisionKind.RELEASE_LEASES,
    "retry": DecisionKind.RETRY_TRANSIENT_OBSERVATION,
    "quarantine": DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
    "fail": DecisionKind.FAIL_NONRETRYABLE,
}

#: Fixed marker retained for an unrecognized legacy action. Never echo the raw
#: caller-supplied string into the persisted comparison record: it is unbounded
#: and could carry a session id, token, or error text.
_UNKNOWN_LEGACY_ACTION = "unknown"


def shadow_compare(
    legacy_action: str, decision: ReconciliationDecision
) -> ShadowComparison:
    """Compare a legacy action string against a reconciler decision.

    Returns a bounded, non-sensitive comparison record suitable for logging or
    persistence in shadow mode. It does not act on the divergence. Only a
    canonical recognized token (or a fixed ``unknown`` marker) is retained, so
    the record can never leak an oversized or sensitive raw action string.
    """

    normalized = legacy_action.strip().lower()
    mapped = LEGACY_ACTION_TO_DECISION_KIND.get(normalized)
    if mapped is None:
        return ShadowComparison(
            legacy_action=_UNKNOWN_LEGACY_ACTION,
            decision_kind=decision.kind,
            agreement=False,
            divergence_reason="unknown_legacy_action",
        )
    if mapped == decision.kind:
        return ShadowComparison(
            legacy_action=normalized,
            decision_kind=decision.kind,
            agreement=True,
        )
    return ShadowComparison(
        legacy_action=normalized,
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
