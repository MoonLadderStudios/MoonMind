"""Shadow-mode comparison and bounded-diagnostics coverage.

Source issue: MoonLadderStudios/MoonMind#3702.
"""

from __future__ import annotations

from moonmind.omnigent.reconciler import (
    DecisionKind,
    EventFrontierObservation,
    ObservationSet,
    ProviderSessionObservation,
    shadow_compare,
)


def test_shadow_compare_agreement(make_intent, make_ready_durable, run, now):
    intent = make_intent()
    durable = make_ready_durable()
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(observed_at=now, raw_status="completed"),
        event_frontier=EventFrontierObservation(observed_at=now, terminal_event_seen=True),
    )
    decision = run(intent, durable, observations)
    comparison = shadow_compare("record_terminal", decision)
    assert comparison.agreement is True
    assert comparison.divergence_reason is None
    assert comparison.decision_kind == DecisionKind.RECORD_PROVIDER_TERMINAL


def test_shadow_compare_divergence(make_intent, make_ready_durable, run, now):
    intent = make_intent()
    durable = make_ready_durable()
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(observed_at=now, raw_status="running")
    )
    decision = run(intent, durable, observations)  # AWAIT_OBSERVATION
    comparison = shadow_compare("record_terminal", decision)
    assert comparison.agreement is False
    assert comparison.divergence_reason == "legacy_maps_to:record_provider_terminal"


def test_shadow_compare_unknown_legacy_action(make_intent, make_durable, run):
    intent = make_intent()
    decision = run(intent, make_durable())
    comparison = shadow_compare("do_something_bespoke", decision)
    assert comparison.agreement is False
    assert comparison.divergence_reason == "unknown_legacy_action"


def test_shadow_compare_is_case_insensitive(make_intent, make_durable, run):
    intent = make_intent()
    decision = run(intent, make_durable())  # ENSURE_PROFILE_LEASE
    assert shadow_compare("Ensure_Profile_Lease", decision).agreement is True


def test_diagnostics_are_bounded_and_secret_free(make_intent, make_ready_durable, run, now):
    """Diagnostics carry only enum codes and observation kind names, no secrets."""

    intent = make_intent()
    durable = make_ready_durable(
        provider_session_id="secret-provider-session",
        owner_token="secret-owner-token",
    )
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(
            observed_at=now,
            raw_status="running",
            provider_session_id="secret-provider-session",
            snapshot_digest="sha256:secret-digest",
        )
    )
    decision = run(intent, durable, observations)
    dumped = decision.diagnostics.model_dump_json()

    for secret in (
        "secret-provider-session",
        "secret-owner-token",
        "sha256:secret-digest",
    ):
        assert secret not in dumped

    # Present observations are recorded as kind names only.
    assert decision.diagnostics.present_observations == ("provider_session",)
    assert decision.diagnostics.provider_status_class is not None


def test_command_never_carries_observation_supplied_identity(
    make_intent, make_ready_durable, run, now
):
    """The command's provider session id comes from durable authority (invariant 11)."""

    intent = make_intent()
    durable = make_ready_durable(
        provider_session_attached=False,  # force ensure_provider_session
        provider_session_id=None,
    )
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(
            observed_at=now,
            raw_status="running",
            provider_session_id="attacker-supplied-session",
        )
    )
    decision = run(intent, durable, observations)
    assert decision.kind == DecisionKind.ENSURE_PROVIDER_SESSION
    assert decision.command is not None
    # Never trusts the observation-supplied identity.
    assert decision.command.provider_session_id is None
