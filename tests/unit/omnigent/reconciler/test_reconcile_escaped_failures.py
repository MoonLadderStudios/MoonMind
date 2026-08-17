"""Escaped-failure replay tests for the pure lifecycle reconciler.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

These reproduce the escaped incidents cited by the issue (#3698, #3683) and the
retry / late-event / cleanup-race edges as *generalized invariant tests* rather
than bespoke handler branches.
"""

from __future__ import annotations

from moonmind.omnigent.reconciler import (
    DecisionAction,
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


def _run(*, intent=None, durable, observations):
    return reconcile(
        intent=intent or make_intent(),
        durable=durable,
        observations=observations,
        now=FIXED_NOW,
    )


def test_missed_terminal_edge_recovers_via_snapshot_3698():
    # #3698: the terminal SSE edge was never delivered. The frontier shows no
    # terminal event, but a periodic snapshot proves the provider session is
    # completed and the response is recorded -> synthesize the terminal.
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    obs = make_observations(
        provider_session=session_obs(raw_status="completed"),
        provider_turn=turn_obs(
            raw_status="completed", has_active_tool_call=False, response_recorded=True
        ),
        event_frontier=frontier_obs(terminal_status=None),
    )
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT
    assert "snapshot_terminal_evidence" in decision.reason_code_values


def test_provider_idle_after_completed_work_3683():
    # #3683: provider reports `idle` after work is done. Idle alone is not
    # terminal, but with a recorded response and no open tool call it is
    # completed work -> synthesize `completed`.
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    obs = make_observations(
        provider_session=session_obs(raw_status="idle"),
        provider_turn=turn_obs(
            raw_status="running", has_active_tool_call=False, response_recorded=True
        ),
        event_frontier=frontier_obs(terminal_status=None),
    )
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT
    assert ("terminal_status", "completed") in decision.command.parameters
    assert "idle_with_completed_work" in decision.reason_code_values


def test_idle_without_recorded_response_is_not_terminal():
    # The inverse guard for #3683: idle with no recorded response must wait.
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    obs = make_observations(
        provider_session=session_obs(raw_status="idle"),
        provider_turn=turn_obs(
            raw_status="running", has_active_tool_call=False, response_recorded=False
        ),
    )
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.AWAIT_OBSERVATION


def test_retry_after_durable_side_effect_before_response_recorded():
    # A durable submission side effect occurred but delivery is unconfirmed
    # (AMBIGUOUS). The reconciler must NOT reissue the submission; it waits to
    # reconcile from a snapshot (invariant 7).
    durable = make_durable(
        phase=DurablePhase.PROVIDER_SESSION_OPEN,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(submission_state=TurnSubmissionState.AMBIGUOUS),
    )
    decision = _run(durable=durable, observations=make_observations())
    assert decision.action is DecisionAction.AWAIT_OBSERVATION
    assert "ambiguous_submission_await" in decision.reason_code_values


def test_late_running_event_after_terminal_evidence_is_ignored():
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


def test_provider_terminal_while_cleanup_and_lease_release_incomplete():
    # Terminal recorded but a consumer/host is still active: cleanup proceeds
    # only through harvest -> cleanup, and lease release is withheld while a
    # consumer is observed.
    durable = make_durable(
        phase=DurablePhase.CLEANUP_STARTED,
        host_lease_held=True,
        terminal_evidence=make_terminal_evidence(),
    )
    obs = make_observations(
        leases=lease_obs(active_consumers=2),
        host_runtime=host_obs(registered=True, runner_ready=True),
    )
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.AWAIT_OBSERVATION
    assert "lease_consumers_active" in decision.reason_code_values


def test_terminal_event_with_open_tool_call_defers():
    # A terminal event contradicted by an open tool call must not be recorded.
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    obs = make_observations(
        provider_turn=turn_obs(raw_status="running", has_active_tool_call=True),
        event_frontier=frontier_obs(terminal_status="completed", has_pending_tool_call=True),
    )
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.AWAIT_OBSERVATION
    assert "terminal_event_pending_tool_call" in decision.reason_code_values


def test_transient_snapshot_unavailable_retries():
    from moonmind.omnigent.reconciler import Observation

    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    obs = make_observations(provider_turn=Observation.negative(source="provider_turn"))
    decision = _run(durable=durable, observations=obs)
    assert decision.action is DecisionAction.RETRY_TRANSIENT_OBSERVATION
    assert "turn_snapshot_unavailable" in decision.reason_code_values
