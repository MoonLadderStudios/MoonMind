"""Sequence-level invariants for the pure reconciler.

Proves, for representative event/snapshot sequences: at-most-once logical
submission, monotonic durable lifecycle state, eventual terminal convergence, no
premature lease release, and stable decisions across restart/replay.

Source issue: MoonLadderStudios/MoonMind#3702.
"""

from __future__ import annotations

from datetime import datetime

from moonmind.omnigent.reconciler import (
    DecisionKind,
    DurableSessionState,
    EventFrontierObservation,
    EvidenceObservation,
    LINEAR_PHASE_ORDER,
    LeaseObservation,
    LeaseState,
    ObservationSet,
    ProviderSessionObservation,
    SubmissionState,
    TerminalOutcome,
    current_phase,
    reconcile,
)

_TERMINAL_DECISIONS = {
    DecisionKind.RECORD_PROVIDER_TERMINAL,
    DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
}


def _world_observations(durable: DurableSessionState, now: datetime) -> ObservationSet:
    """Supply the authoritative observations available at the current state."""

    kwargs: dict = {}
    if durable.submission == SubmissionState.ACCEPTED and durable.terminal_outcome is None:
        kwargs["provider_session"] = ProviderSessionObservation(
            observed_at=now, raw_status="completed"
        )
        kwargs["event_frontier"] = EventFrontierObservation(
            observed_at=now, terminal_event_seen=True
        )
    if durable.terminal_outcome is not None and not durable.evidence_harvested:
        kwargs["evidence"] = EvidenceObservation(
            observed_at=now, terminal_evidence_available=True
        )
    if durable.terminal_outcome is not None:
        # Post-terminal, the world confirms no lease consumer is active so the
        # reducer may release once cleanup completes (invariant 8 requires an
        # observed negative, not merely an absent observation).
        kwargs["profile_lease"] = LeaseObservation(
            observed_at=now,
            held=durable.profile_lease == LeaseState.HELD,
            consumer_active=False,
        )
        kwargs["host_lease"] = LeaseObservation(
            observed_at=now,
            held=durable.host_lease == LeaseState.HELD,
            consumer_active=False,
        )
    return ObservationSet(**kwargs)


def _advance(durable: DurableSessionState, decision, *, confirm_submit: bool) -> DurableSessionState:
    """Apply a decision like a deterministic executor would (revision bumps)."""

    update: dict = {"revision": durable.revision + 1}
    kind = decision.kind
    if kind == DecisionKind.ENSURE_PROFILE_LEASE:
        update["profile_lease"] = LeaseState.HELD
    elif kind == DecisionKind.ENSURE_HOST:
        update["host_lease"] = LeaseState.HELD
    elif kind == DecisionKind.ENSURE_PROVIDER_SESSION:
        update["provider_session_attached"] = True
        update["provider_session_id"] = "provider-session-1"
        # A durable attempt identity is assigned alongside the session so the
        # first turn can be submitted with a stable idempotency identity.
        update["attempt_id"] = "attempt-1"
    elif kind == DecisionKind.SUBMIT_TURN:
        update["turn_attempts"] = durable.turn_attempts + 1
        update["submission"] = (
            SubmissionState.ACCEPTED if confirm_submit else SubmissionState.IN_FLIGHT
        )
    elif kind in _TERMINAL_DECISIONS:
        update["terminal_outcome"] = TerminalOutcome.SUCCESS
    elif kind == DecisionKind.HARVEST_EVIDENCE:
        update["evidence_harvested"] = True
        update["terminal_evidence_ref"] = "evref-1"
    elif kind == DecisionKind.BEGIN_CLEANUP:
        update["cleanup_started"] = True
        update["cleanup_complete"] = True
    elif kind == DecisionKind.RELEASE_LEASES:
        update["profile_lease"] = LeaseState.RELEASED
        update["host_lease"] = LeaseState.RELEASED
    return durable.model_copy(update=update)


def _drive(intent, durable, now, *, confirm_submit=True, max_steps=32):
    decisions = []
    phases = [current_phase(durable)]
    release_preconditions = []
    for _ in range(max_steps):
        observations = _world_observations(durable, now)
        decision = reconcile(
            intent=intent, durable=durable, observations=observations, now=now
        )
        decisions.append(decision)
        if decision.kind == DecisionKind.RELEASE_LEASES:
            release_preconditions.append(
                (durable.terminal_outcome, durable.cleanup_complete)
            )
        if decision.kind == DecisionKind.NO_OP:
            break
        durable = _advance(durable, decision, confirm_submit=confirm_submit)
        phases.append(current_phase(durable))
    else:  # pragma: no cover - guards against a non-converging loop
        raise AssertionError("reconciler did not converge")
    return decisions, phases, durable, release_preconditions


def test_happy_path_converges_to_terminal(make_intent, make_durable, now):
    intent = make_intent()
    durable = make_durable()
    decisions, phases, final, _ = _drive(intent, durable, now)

    assert decisions[-1].kind == DecisionKind.NO_OP
    assert final.terminal_outcome == TerminalOutcome.SUCCESS
    # Cleanup completion is distinct from task completion but both hold at the end.
    assert final.cleanup_complete is True


def test_monotonic_lifecycle(make_intent, make_durable, now):
    intent = make_intent()
    _, phases, _, _ = _drive(intent, make_durable(), now)
    indices = [LINEAR_PHASE_ORDER[phase] for phase in phases]
    assert indices == sorted(indices)
    assert indices[0] == 0  # INITIALIZING
    assert indices[-1] == max(LINEAR_PHASE_ORDER.values())  # CLOSED


def test_at_most_once_submission(make_intent, make_durable, now):
    intent = make_intent()
    decisions, _, final, _ = _drive(intent, make_durable(), now)
    submit_decisions = [d for d in decisions if d.kind == DecisionKind.SUBMIT_TURN]
    assert len(submit_decisions) == 1
    assert final.turn_attempts == 1


def test_ambiguous_submission_never_reissues(make_intent, make_durable, now):
    """When submission stays in flight, the reducer waits and never re-submits."""

    # Allow a retry so the idempotent-command-id check exercises a resubmit path.
    intent = make_intent(max_turn_attempts=2)
    durable = make_durable(
        profile_lease=LeaseState.HELD,
        host_lease=LeaseState.HELD,
        provider_session_attached=True,
        provider_session_id="provider-session-1",
        attempt_id="attempt-1",
    )
    # First reconcile submits.
    first = reconcile(
        intent=intent, durable=durable, observations=ObservationSet(), now=now
    )
    assert first.kind == DecisionKind.SUBMIT_TURN
    submit_command_id = first.command.command_id
    exact_replay = reconcile(
        intent=intent, durable=durable, observations=ObservationSet(), now=now
    )
    assert exact_replay.command.command_id == submit_command_id

    # Executor issued the command but delivery is ambiguous.
    durable = _advance(durable, first, confirm_submit=False)
    assert durable.submission == SubmissionState.IN_FLIGHT

    # Subsequent reconciles must not reissue the submit.
    for _ in range(3):
        again = reconcile(
            intent=intent, durable=durable, observations=ObservationSet(), now=now
        )
        assert again.kind == DecisionKind.AWAIT_OBSERVATION
        assert again.command is None



def test_command_identity_changes_when_revision_authority_advances(
    make_intent, make_durable, now
):
    """MoonLadderStudios/MoonMind#3705 never revives a stale pending command."""

    intent = make_intent()
    first = reconcile(
        intent=intent,
        durable=make_durable(revision=7),
        observations=ObservationSet(),
        now=now,
    )
    advanced = reconcile(
        intent=intent,
        durable=make_durable(revision=8),
        observations=ObservationSet(),
        now=now,
    )

    assert first.kind == advanced.kind == DecisionKind.ENSURE_PROFILE_LEASE
    assert first.command.command_id != advanced.command.command_id


def test_no_premature_lease_release(make_intent, make_durable, now):
    intent = make_intent()
    _, _, _, release_preconditions = _drive(intent, make_durable(), now)
    assert release_preconditions, "expected a lease release to occur"
    for terminal_outcome, cleanup_complete in release_preconditions:
        assert terminal_outcome is not None
        assert cleanup_complete is True


def test_stable_across_restart_and_replay(make_intent, make_durable, now):
    """Re-driving from scratch yields the identical decision sequence (invariant 12)."""

    intent = make_intent()
    first, _, _, _ = _drive(intent, make_durable(), now)
    second, _, _, _ = _drive(intent, make_durable(), now)
    assert [d.kind for d in first] == [d.kind for d in second]
    assert first == second


def test_deterministic_single_step(make_intent, make_ready_durable, now):
    intent = make_intent()
    durable = make_ready_durable()
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(observed_at=now, raw_status="running")
    )
    a = reconcile(intent=intent, durable=durable, observations=observations, now=now)
    b = reconcile(intent=intent, durable=durable, observations=observations, now=now)
    assert a == b
