"""Table-driven transition tests for the pure lifecycle reconciler.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

Covers the happy-path ladder plus contradictory / off-ladder combinations,
asserting the exact :class:`DecisionAction` for each legal state.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.reconciler import (
    DecisionAction,
    DesiredLifecycle,
    DurablePhase,
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


def _run(*, durable, observations=None):
    return reconcile(
        intent=make_intent(),
        durable=durable,
        observations=observations or make_observations(),
        now=FIXED_NOW,
    )


@pytest.mark.parametrize(
    "phase,expected",
    [
        (DurablePhase.PENDING, DecisionAction.ENSURE_PROFILE_LEASE),
        (DurablePhase.PROFILE_LEASED, DecisionAction.ENSURE_HOST),
        (DurablePhase.HOST_READY, DecisionAction.ENSURE_PROVIDER_SESSION),
    ],
)
def test_provisioning_ladder(phase, expected):
    decision = _run(durable=make_durable(phase=phase))
    assert decision.action is expected
    assert decision.command is not None
    assert decision.next_deadline == FIXED_NOW + __import__("datetime").timedelta(
        seconds=10
    )


def test_provider_session_open_submits_first_turn():
    durable = make_durable(
        phase=DurablePhase.PROVIDER_SESSION_OPEN,
        provider_session_id="prov-1",
        turn_attempt=None,
    )
    decision = _run(durable=durable)
    assert decision.action is DecisionAction.SUBMIT_TURN
    assert decision.changes_product_visible_state is True


def test_turn_in_flight_awaits_without_evidence():
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    decision = _run(durable=durable)
    assert decision.action is DecisionAction.AWAIT_OBSERVATION
    assert decision.next_deadline is not None


def test_turn_in_flight_records_provider_terminal_event():
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
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.RECORD_PROVIDER_TERMINAL
    assert ("terminal_status", "completed") in decision.command.parameters


def test_terminal_recorded_harvests_evidence():
    durable = make_durable(
        phase=DurablePhase.TERMINAL_RECORDED,
        provider_session_id="prov-1",
        terminal_evidence=make_terminal_evidence(),
    )
    decision = _run(durable=durable, observations=make_observations(evidence=evidence_obs()))
    assert decision.action is DecisionAction.HARVEST_EVIDENCE
    assert any(req.name == "terminal_evidence" for req in decision.evidence_requirements)


def test_evidence_harvested_begins_cleanup():
    durable = make_durable(
        phase=DurablePhase.EVIDENCE_HARVESTED,
        provider_session_id="prov-1",
        terminal_evidence=make_terminal_evidence(),
    )
    decision = _run(durable=durable)
    assert decision.action is DecisionAction.BEGIN_CLEANUP


def test_cleanup_started_releases_when_idle():
    durable = make_durable(
        phase=DurablePhase.CLEANUP_STARTED,
        host_lease_held=False,
        terminal_evidence=make_terminal_evidence(),
    )
    obs = make_observations(
        leases=lease_obs(active_consumers=0),
        host_runtime=host_obs(registered=False, runner_ready=False),
    )
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.RELEASE_LEASES


def test_leases_released_is_terminal_no_op():
    durable = make_durable(phase=DurablePhase.LEASES_RELEASED, cleanup_complete=True)
    decision = _run(durable=durable)
    assert decision.action is DecisionAction.NO_OP
    assert decision.terminal is True
    assert decision.next_deadline is None


@pytest.mark.parametrize(
    "phase,expected_terminal",
    [
        (DurablePhase.CLOSED, True),
        (DurablePhase.FAILED, True),
    ],
)
def test_off_ladder_terminal_states_are_no_op(phase, expected_terminal):
    decision = _run(durable=make_durable(phase=phase))
    assert decision.action is DecisionAction.NO_OP
    assert decision.terminal is expected_terminal


def test_quarantined_phase_stays_quarantined():
    decision = _run(durable=make_durable(phase=DurablePhase.QUARANTINED))
    assert decision.action is DecisionAction.QUARANTINE_AMBIGUOUS_STATE
    assert decision.wait_authority == "operator_review"
    assert decision.terminal is False


def test_operator_termination_records_canceled_terminal():
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        desired=DesiredLifecycle.TERMINATED,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    decision = _run(durable=durable)
    assert decision.action is DecisionAction.RECORD_PROVIDER_TERMINAL
    assert ("terminal_status", "canceled") in decision.command.parameters


def test_no_cleanup_intent_releases_after_evidence():
    durable = make_durable(
        phase=DurablePhase.EVIDENCE_HARVESTED,
        terminal_evidence=make_terminal_evidence(),
    )
    decision = reconcile(
        intent=make_intent(requires_cleanup=False),
        durable=durable,
        observations=make_observations(),
        now=FIXED_NOW,
    )
    assert decision.action is DecisionAction.RELEASE_LEASES


def test_ambiguous_submission_awaits_not_resubmit():
    durable = make_durable(
        phase=DurablePhase.PROVIDER_SESSION_OPEN,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(submission_state=TurnSubmissionState.AMBIGUOUS),
    )
    decision = _run(durable=durable)
    assert decision.action is DecisionAction.AWAIT_OBSERVATION
