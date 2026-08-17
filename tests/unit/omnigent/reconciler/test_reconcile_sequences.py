"""Sequence properties for the pure lifecycle reconciler.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

A tiny test-only executor applies each decision's action to the durable state so
we can assert emergent properties over representative event/snapshot sequences:
at-most-once logical submission, monotonic durable lifecycle, eventual terminal
convergence, no premature lease release, and stability across replay.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

from moonmind.omnigent.reconciler import (
    DecisionAction,
    DurablePhase,
    TurnSubmissionState,
    reconcile,
)
from moonmind.omnigent.reconciler.vocabulary import phase_rank
from tests.helpers.omnigent_reconciler import (
    FIXED_NOW,
    evidence_obs,
    frontier_obs,
    host_obs,
    lease_obs,
    make_attempt,
    make_durable,
    make_intent,
    make_observations,
    make_terminal_evidence,
    session_obs,
    turn_obs,
)

# Test-only executor: maps a decision action to the next durable phase, mirroring
# what the real side-effecting orchestrator would persist. The reducer itself
# performs no mutation.
_PHASE_AFTER: dict[DecisionAction, DurablePhase] = {
    DecisionAction.ENSURE_PROFILE_LEASE: DurablePhase.PROFILE_LEASED,
    DecisionAction.ENSURE_HOST: DurablePhase.HOST_READY,
    DecisionAction.ENSURE_PROVIDER_SESSION: DurablePhase.PROVIDER_SESSION_OPEN,
    DecisionAction.SUBMIT_TURN: DurablePhase.TURN_IN_FLIGHT,
    DecisionAction.RECORD_PROVIDER_TERMINAL: DurablePhase.TERMINAL_RECORDED,
    DecisionAction.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT: DurablePhase.TERMINAL_RECORDED,
    DecisionAction.HARVEST_EVIDENCE: DurablePhase.EVIDENCE_HARVESTED,
    DecisionAction.BEGIN_CLEANUP: DurablePhase.CLEANUP_STARTED,
    DecisionAction.RELEASE_LEASES: DurablePhase.LEASES_RELEASED,
}


def _apply(durable, decision, submission_count):
    """Advance durable state as the external executor would, deterministically."""

    updates: dict = {"revision": durable.revision + 1}
    action = decision.action
    if action is DecisionAction.SUBMIT_TURN:
        submission_count += 1
        updates["provider_session_id"] = durable.provider_session_id or "prov-1"
        updates["turn_attempt"] = make_attempt(
            attempt_id=f"attempt-{submission_count}",
            attempt_number=submission_count,
            submission_state=TurnSubmissionState.SUBMITTED,
        )
    if action is DecisionAction.ENSURE_PROVIDER_SESSION:
        updates["provider_session_id"] = "prov-1"
    if action in {
        DecisionAction.RECORD_PROVIDER_TERMINAL,
        DecisionAction.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
    }:
        updates["terminal_evidence"] = make_terminal_evidence()
    if action is DecisionAction.ENSURE_PROFILE_LEASE:
        updates["profile_lease_held"] = True
    if action is DecisionAction.ENSURE_HOST:
        updates["host_lease_held"] = True
    if action is DecisionAction.RELEASE_LEASES:
        updates["host_lease_held"] = False
        updates["profile_lease_held"] = False
        updates["cleanup_complete"] = True
    next_phase = _PHASE_AFTER.get(action)
    if next_phase is not None:
        updates["phase"] = next_phase
    updates["last_decision_action"] = action.value
    return dataclasses.replace(durable, **updates), submission_count


def _observations_for(durable):
    """Provide the observations that make the current phase progress."""

    phase = durable.phase
    if phase is DurablePhase.TURN_IN_FLIGHT:
        return make_observations(
            provider_session=session_obs(raw_status="completed"),
            provider_turn=turn_obs(raw_status="completed", response_recorded=True),
            event_frontier=frontier_obs(terminal_status="completed"),
        )
    if phase in {DurablePhase.TERMINAL_RECORDED, DurablePhase.EVIDENCE_HARVESTED}:
        return make_observations(evidence=evidence_obs())
    if phase is DurablePhase.CLEANUP_STARTED:
        return make_observations(
            leases=lease_obs(active_consumers=0),
            host_runtime=host_obs(registered=False, runner_ready=False),
        )
    return make_observations()


def _drive_to_convergence(durable, intent, *, max_steps=40):
    submission_count = 0
    actions: list[DecisionAction] = []
    now = FIXED_NOW
    ranks: list[int] = []
    for _ in range(max_steps):
        decision = reconcile(
            intent=intent,
            durable=durable,
            observations=_observations_for(durable),
            now=now,
        )
        actions.append(decision.action)
        rank = phase_rank(durable.phase)
        if rank is not None:
            ranks.append(rank)
        if decision.terminal:
            break
        durable, submission_count = _apply(durable, decision, submission_count)
        now = now + timedelta(seconds=1)
    return durable, actions, submission_count, ranks


def test_full_sequence_converges_and_is_monotonic():
    intent = make_intent()
    durable = make_durable(phase=DurablePhase.PENDING)
    final, actions, submissions, ranks = _drive_to_convergence(durable, intent)

    # Eventual terminal convergence.
    assert final.phase is DurablePhase.LEASES_RELEASED
    assert actions[-1] is DecisionAction.NO_OP

    # At-most-once logical submission across the whole sequence.
    assert actions.count(DecisionAction.SUBMIT_TURN) == 1
    assert submissions == 1

    # Monotonic durable lifecycle (ladder rank never decreases).
    assert ranks == sorted(ranks)


def test_no_premature_lease_release_until_consumer_gone():
    intent = make_intent()
    durable = make_durable(
        phase=DurablePhase.CLEANUP_STARTED,
        host_lease_held=True,
        terminal_evidence=make_terminal_evidence(),
    )
    # While a consumer is observed, release is withheld.
    busy = reconcile(
        intent=intent,
        durable=durable,
        observations=make_observations(
            leases=lease_obs(active_consumers=1),
            host_runtime=host_obs(registered=True, runner_ready=True),
        ),
        now=FIXED_NOW,
    )
    assert busy.action is DecisionAction.AWAIT_OBSERVATION
    # Once the consumer is gone, release proceeds.
    idle = reconcile(
        intent=intent,
        durable=durable,
        observations=make_observations(
            leases=lease_obs(active_consumers=0, host_lease_held=False),
            host_runtime=host_obs(registered=False, runner_ready=False),
        ),
        now=FIXED_NOW,
    )
    assert idle.action is DecisionAction.RELEASE_LEASES


def test_stable_decisions_across_restart_and_replay():
    # Re-running the reducer from the same durable state (a restart/replay)
    # yields an identical decision, including deterministic command identity.
    intent = make_intent()
    durable = make_durable(
        phase=DurablePhase.PROVIDER_SESSION_OPEN,
        provider_session_id="prov-1",
        turn_attempt=None,
    )
    obs = make_observations()
    first = reconcile(intent=intent, durable=durable, observations=obs, now=FIXED_NOW)
    replay = reconcile(intent=intent, durable=durable, observations=obs, now=FIXED_NOW)
    assert first == replay
    assert first.command == replay.command


def test_at_most_once_submission_under_ambiguous_replay():
    # An ambiguous submission never advances to a second submission on replay.
    intent = make_intent()
    durable = make_durable(
        phase=DurablePhase.PROVIDER_SESSION_OPEN,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(submission_state=TurnSubmissionState.AMBIGUOUS),
    )
    for _ in range(5):
        decision = reconcile(
            intent=intent,
            durable=durable,
            observations=make_observations(),
            now=FIXED_NOW,
        )
        assert decision.action is not DecisionAction.SUBMIT_TURN
