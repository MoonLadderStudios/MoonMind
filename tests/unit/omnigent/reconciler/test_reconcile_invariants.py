"""The 12 required reducer invariants for the pure lifecycle reconciler.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

Each test maps to a numbered invariant from the issue.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.reconciler import (
    DecisionAction,
    DesiredLifecycle,
    DurablePhase,
    ReconcilerContractError,
    TurnSubmissionState,
    reconcile,
)
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


def _run(*, intent=None, durable, observations=None, now=FIXED_NOW):
    return reconcile(
        intent=intent or make_intent(),
        durable=durable,
        observations=observations or make_observations(),
        now=now,
    )


def test_invariant_1_provider_event_is_observation_not_mutation():
    # A terminal event yields a *decision to record*, never an assumed mutation:
    # the decision carries the expected revision + fencing generation so the
    # durable write happens under concurrency control, elsewhere.
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
        revision=11,
        fencing_generation=4,
    )
    obs = make_observations(
        provider_turn=turn_obs(raw_status="completed", response_recorded=True),
        event_frontier=frontier_obs(terminal_status="completed"),
    )
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.RECORD_PROVIDER_TERMINAL
    assert decision.expected_revision == 11
    assert decision.expected_fencing_generation == 4


def test_invariant_2_lost_terminal_recovered_from_snapshot():
    # No terminal event on the frontier, but session snapshot is terminal and
    # the transcript response is recorded -> synthesize terminal (reproduces
    # #3698).
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    obs = make_observations(
        provider_session=session_obs(raw_status="completed"),
        provider_turn=turn_obs(raw_status="completed", response_recorded=True),
        event_frontier=frontier_obs(terminal_status=None),
    )
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT
    assert ("terminal_status", "completed") in decision.command.parameters


def test_invariant_3_idle_with_active_tool_call_not_terminal():
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    obs = make_observations(
        provider_session=session_obs(raw_status="idle"),
        provider_turn=turn_obs(
            raw_status="running", has_active_tool_call=True, response_recorded=False
        ),
    )
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.AWAIT_OBSERVATION
    assert "active_tool_call_not_terminal" in decision.reason_code_values


def test_invariant_4_attempt_terminal_distinct_from_session_terminal():
    # A failed *attempt* with retries remaining submits a new attempt rather
    # than sealing the canonical session.
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(
            attempt_number=1,
            submission_state=TurnSubmissionState.ATTEMPT_FAILED,
            retries_remaining=1,
        ),
    )
    obs = make_observations(
        provider_turn=turn_obs(raw_status="failed", response_recorded=True),
    )
    decision = _run(
        intent=make_intent(max_turn_attempts=2), durable=durable, observations=obs
    )
    assert decision.action is DecisionAction.SUBMIT_TURN
    assert "submit_retry_attempt" in decision.reason_code_values


def test_invariant_5_stale_observation_cannot_move_terminal_backward():
    # A late "running" observation after terminal is recorded keeps driving the
    # forward (harvest) path and never reverts to await/submit.
    durable = make_durable(
        phase=DurablePhase.TERMINAL_RECORDED,
        provider_session_id="prov-1",
        terminal_evidence=make_terminal_evidence(),
    )
    obs = make_observations(
        provider_session=session_obs(raw_status="running"),
        event_frontier=frontier_obs(terminal_status=None),
        evidence=evidence_obs(),
    )
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.HARVEST_EVIDENCE
    assert "late_nonterminal_after_terminal" in decision.reason_code_values


def test_invariant_6_unknown_provider_status_fails_closed_to_quarantine():
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    obs = make_observations(provider_session=session_obs(raw_status="frobnicated"))
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.QUARANTINE_AMBIGUOUS_STATE
    assert "unknown_provider_status" in decision.reason_code_values


def test_invariant_6_unknown_compatibility_fails_closed():
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    from tests.helpers.omnigent_reconciler import readiness_obs

    obs = make_observations(runtime_readiness=readiness_obs(raw_compatibility="weird"))
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.QUARANTINE_AMBIGUOUS_STATE
    assert "unknown_compatibility_vocabulary" in decision.reason_code_values


def test_invariant_7_ambiguous_submission_not_reissued():
    durable = make_durable(
        phase=DurablePhase.PROVIDER_SESSION_OPEN,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(submission_state=TurnSubmissionState.AMBIGUOUS),
    )
    decision = _run(durable=durable)
    assert decision.action is DecisionAction.AWAIT_OBSERVATION
    assert "ambiguous_submission_await" in decision.reason_code_values


def test_invariant_8_no_lease_release_while_consumer_active():
    durable = make_durable(
        phase=DurablePhase.CLEANUP_STARTED,
        host_lease_held=True,
        terminal_evidence=make_terminal_evidence(),
    )
    obs = make_observations(
        leases=lease_obs(active_consumers=1),
        host_runtime=host_obs(registered=True, runner_ready=True),
    )
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.AWAIT_OBSERVATION
    assert "lease_consumers_active" in decision.reason_code_values


def test_invariant_8_no_release_when_lease_unobserved_but_durably_owned():
    durable = make_durable(
        phase=DurablePhase.CLEANUP_STARTED,
        host_lease_held=True,
        terminal_evidence=make_terminal_evidence(),
    )
    # Leases observation absent (not fetched) while durable owns the host lease.
    decision = _run(durable=durable, observations=make_observations())
    assert decision.action is DecisionAction.AWAIT_OBSERVATION


def test_invariant_9_cleanup_requires_evidence_first():
    # From TERMINAL_RECORDED the only forward action is harvest_evidence; a
    # begin_cleanup can only follow EVIDENCE_HARVESTED.
    durable = make_durable(
        phase=DurablePhase.TERMINAL_RECORDED,
        provider_session_id="prov-1",
        terminal_evidence=make_terminal_evidence(),
    )
    decision = _run(durable=durable, observations=make_observations(evidence=evidence_obs()))
    assert decision.action is DecisionAction.HARVEST_EVIDENCE


def test_invariant_10_every_nonterminal_decision_bounds_next_step():
    # Sample a range of nonterminal phases and assert each bounds the next step.
    phases = [
        DurablePhase.PENDING,
        DurablePhase.PROFILE_LEASED,
        DurablePhase.HOST_READY,
        DurablePhase.PROVIDER_SESSION_OPEN,
        DurablePhase.TURN_IN_FLIGHT,
        DurablePhase.TERMINAL_RECORDED,
        DurablePhase.EVIDENCE_HARVESTED,
        DurablePhase.CLEANUP_STARTED,
    ]
    for phase in phases:
        durable = make_durable(
            phase=phase,
            provider_session_id="prov-1",
            turn_attempt=make_attempt(),
            terminal_evidence=make_terminal_evidence(),
        )
        decision = _run(durable=durable)
        if not decision.terminal:
            assert (
                decision.next_deadline is not None or decision.wait_authority is not None
            ), phase


def test_invariant_11_observation_identity_mismatch_quarantines():
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    obs = make_observations(
        provider_session=session_obs(provider_session_id="attacker-session")
    )
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.QUARANTINE_AMBIGUOUS_STATE
    assert "observation_identity_mismatch" in decision.reason_code_values


def test_invariant_11_intent_identity_mismatch_raises():
    with pytest.raises(ReconcilerContractError):
        reconcile(
            intent=make_intent(session_id="sess-1"),
            durable=make_durable(session_id="sess-OTHER"),
            observations=make_observations(),
            now=FIXED_NOW,
        )


def test_invariant_12_deterministic_for_equal_inputs():
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    obs = make_observations(
        provider_session=session_obs(raw_status="completed"),
        provider_turn=turn_obs(raw_status="completed", response_recorded=True),
        event_frontier=frontier_obs(terminal_status="completed"),
    )
    first = _run(durable=durable, observations=obs)
    second = _run(durable=durable, observations=obs)
    assert first == second


def test_runtime_incompatible_fails_nonretryable():
    from tests.helpers.omnigent_reconciler import readiness_obs

    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    obs = make_observations(runtime_readiness=readiness_obs(raw_compatibility="incompatible"))
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.FAIL_NONRETRYABLE
    assert decision.terminal is True
