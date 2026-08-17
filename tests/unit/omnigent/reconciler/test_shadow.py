"""Shadow-mode comparison tests for the lifecycle reconciler.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

Shadow comparison must be available for production paths without becoming a
second orchestration source of truth (it never executes), and its diagnostics
must be bounded and free of provider/host/lease/credential/workspace identity.
"""

from __future__ import annotations

from moonmind.omnigent.reconciler import (
    DurablePhase,
    compare_shadow,
    reconcile,
)
from moonmind.omnigent.reconciler.shadow import LEGACY_ACTION_ALIASES
from tests.helpers.omnigent_reconciler import (
    FIXED_NOW,
    make_attempt,
    make_durable,
    make_intent,
    make_observations,
    session_obs,
)


def _terminal_decision():
    durable = make_durable(
        phase=DurablePhase.TURN_IN_FLIGHT,
        provider_session_id="prov-1",
        turn_attempt=make_attempt(),
    )
    from tests.helpers.omnigent_reconciler import frontier_obs, turn_obs

    obs = make_observations(
        provider_session=session_obs(raw_status="completed"),
        provider_turn=turn_obs(raw_status="completed", response_recorded=True),
        event_frontier=frontier_obs(terminal_status="completed"),
    )
    return reconcile(
        intent=make_intent(), durable=durable, observations=obs, now=FIXED_NOW
    )


def test_shadow_agreement():
    decision = _terminal_decision()
    comparison = compare_shadow(
        session_id="sess-1", legacy_action="record_terminal", decision=decision
    )
    assert comparison.agree is True
    assert comparison.legacy_recognized is True
    assert comparison.reconciler_action == "record_provider_terminal"


def test_shadow_divergence_is_visible():
    decision = _terminal_decision()
    comparison = compare_shadow(
        session_id="sess-1", legacy_action="wait", decision=decision
    )
    assert comparison.agree is False
    assert comparison.legacy_recognized is True
    assert "record_provider_terminal" in comparison.note


def test_shadow_unknown_legacy_action_is_divergence_not_error():
    decision = _terminal_decision()
    comparison = compare_shadow(
        session_id="sess-1", legacy_action="mystery_action", decision=decision
    )
    assert comparison.agree is False
    assert comparison.legacy_recognized is False


def test_shadow_log_dict_is_bounded_and_secret_free():
    decision = _terminal_decision()
    comparison = compare_shadow(
        session_id="sess-1", legacy_action="record_terminal", decision=decision
    )
    payload = comparison.to_log_dict()
    # Bounded, fixed field set only.
    assert set(payload) == {
        "schema",
        "sessionId",
        "legacyAction",
        "reconcilerAction",
        "reasonCodes",
        "agree",
        "legacyRecognized",
        "note",
    }
    # No forbidden identity keys leak into the diagnostic.
    forbidden = {
        "providerSessionId",
        "provider_session_id",
        "ownerToken",
        "owner_token",
        "hostId",
        "profileId",
        "credential",
        "workspace",
        "token",
    }
    assert not (set(payload) & forbidden)
    flat = str(payload)
    assert "owner-token" not in flat
    assert "prov-1" not in flat


def test_every_alias_maps_into_closed_vocabulary():
    from moonmind.omnigent.reconciler import DecisionAction

    for action in LEGACY_ACTION_ALIASES.values():
        assert isinstance(action, DecisionAction)
