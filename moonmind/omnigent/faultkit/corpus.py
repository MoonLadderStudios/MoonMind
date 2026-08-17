"""Initial fault scenarios lifted from escaped incidents.

Owned by MoonLadderStudios/MoonMind#3709.

Each escaped reliability incident is represented here as a *generalized*
invariant plus a minimized declarative fault scenario, following the incident
ingestion contract:

1. a stable generalized invariant;
2. a minimized declarative fault scenario;
3. the source incident or PR reference;
4. the expected decision and classification;
5. any workspace/artifact fixture needed for hermetic replay (none of these
   need one);
6. an operational signal that would detect the failure class.

Every scenario here is *safe* under a correct reconciler: running it produces
zero invariant violations. That is the point -- these incidents can no longer
escape once the invariant is generalized into the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from moonmind.omnigent.faultkit.commands import LogicalCommand as LC
from moonmind.omnigent.faultkit.scenario import (
    CANONICAL_SCENARIO_SCHEMA_VERSION,
    ResponseMode,
    Scenario,
    ScenarioStep,
    SideEffectKind as SE,
)


@dataclass(frozen=True)
class IncidentScenario:
    """One incident ingested into the model-based reliability suite."""

    slug: str
    invariant: str
    source_reference: str
    expected_decision: str
    expected_classification: str
    operational_signal: str
    scenario: Scenario
    workspace_fixture: str | None = None

    def to_manifest(self) -> dict[str, Any]:
        return {
            "schemaVersion": "moonmind.omnigent-fault-incident/v1",
            "issue": "MoonLadderStudios/MoonMind#3709",
            "slug": self.slug,
            "generalizedInvariant": self.invariant,
            "sourceReference": self.source_reference,
            "expectedDecision": self.expected_decision,
            "expectedClassification": self.expected_classification,
            "operationalSignal": self.operational_signal,
            "workspaceFixture": self.workspace_fixture,
            "scenario": self.scenario.to_mapping(),
        }


def _scenario(seed: int, slug: str, steps: list[ScenarioStep]) -> Scenario:
    return Scenario(
        schema_version=CANONICAL_SCENARIO_SCHEMA_VERSION,
        seed=seed,
        steps=tuple(steps),
        name=slug,
        metadata={"corpus": "faultkit-initial"},
    )


def _s(on: LC, **kwargs) -> ScenarioStep:  # type: ignore[no-untyped-def]
    return ScenarioStep(on=on, **kwargs)


# -- initial scenarios ---------------------------------------------------------


def _missed_terminal_edge() -> IncidentScenario:
    steps = [
        _s(LC.ENSURE_SESSION, side_effect=SE.CREATED),
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED),
        _s(LC.READ_EVENTS, emit=({"type": "turn.running", "id": "r1"},), disconnect=True),
        # Terminal SSE edge is dropped; the snapshot advances to idle/completed.
        _s(LC.OBSERVE_SNAPSHOT, snapshot={"sessionState": "idle", "turnState": "completed"}),
    ]
    return IncidentScenario(
        slug="omnigent-fault-missed-terminal-edge",
        invariant="eventual_convergence",
        source_reference="MoonLadderStudios/MoonMind#3698",
        expected_decision="derive_terminal_from_reattached_snapshot",
        expected_classification="completed",
        operational_signal="heartbeat_timeout_followed_by_idle_snapshot",
        scenario=_scenario(3698, "omnigent-fault-missed-terminal-edge", steps),
    )


def _idle_completion_vocabulary() -> IncidentScenario:
    steps = [
        _s(LC.ENSURE_SESSION, side_effect=SE.CREATED),
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED),
        # Provider reports completion using the "idle" session vocabulary.
        _s(LC.OBSERVE_SNAPSHOT, snapshot={"sessionState": "idle", "turnState": "completed"}),
    ]
    return IncidentScenario(
        slug="omnigent-fault-idle-completion-vocabulary",
        invariant="distinct_terminality",
        source_reference="MoonLadderStudios/MoonMind#3683",
        expected_decision="map_idle_completion_to_terminal_turn",
        expected_classification="completed",
        operational_signal="idle_session_with_completed_turn",
        scenario=_scenario(3683, "omnigent-fault-idle-completion-vocabulary", steps),
    )


def _stale_state_rollback() -> IncidentScenario:
    steps = [
        _s(LC.ENSURE_SESSION, side_effect=SE.CREATED),
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED),
        _s(LC.OBSERVE_SNAPSHOT, snapshot={"sessionState": "idle", "turnState": "completed"}),
        # A stale snapshot arrives after the terminal frontier; must not roll back.
        _s(LC.OBSERVE_SNAPSHOT, snapshot={"sessionState": "running", "turnState": "running"}),
    ]
    return IncidentScenario(
        slug="omnigent-fault-stale-state-rollback",
        invariant="monotonic_authority",
        source_reference="MoonLadderStudios/MoonMind#3665",
        expected_decision="ignore_stale_snapshot_after_terminal",
        expected_classification="completed",
        operational_signal="post_terminal_running_snapshot",
        scenario=_scenario(3665, "omnigent-fault-stale-state-rollback", steps),
    )


def _remediation_authority_loss() -> IncidentScenario:
    steps = [
        _s(LC.ENSURE_SESSION, side_effect=SE.CREATED),
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED),
        _s(LC.OBSERVE_SNAPSHOT, snapshot={"sessionState": "idle", "turnState": "completed"}),
        _s(LC.HOST_REPLACE, side_effect=SE.REPLACED),
        # A former-generation remediation write must be fenced.
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED, generation=1, turn="stale"),
    ]
    return IncidentScenario(
        slug="omnigent-fault-remediation-authority-loss",
        invariant="fencing_safety",
        source_reference="MoonLadderStudios/MoonMind#3684",
        expected_decision="fence_former_generation_remediation",
        expected_classification="system_error",
        operational_signal="former_generation_write_attempt",
        scenario=_scenario(3684, "omnigent-fault-remediation-authority-loss", steps),
    )


def _image_authority_drift() -> IncidentScenario:
    steps = [
        _s(LC.ENSURE_SESSION, side_effect=SE.CREATED),
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED),
        # Provider returns an unknown/newer schema (image authority drift).
        _s(LC.OBSERVE_SNAPSHOT, response=ResponseMode.UNKNOWN_SCHEMA),
        _s(LC.OBSERVE_SNAPSHOT, snapshot={"sessionState": "idle", "turnState": "completed"}),
    ]
    return IncidentScenario(
        slug="omnigent-fault-image-authority-drift",
        invariant="compatibility_safety",
        source_reference="MoonLadderStudios/MoonMind#3694",
        expected_decision="quarantine_unknown_schema_then_reconcile",
        expected_classification="integration_error",
        operational_signal="unknown_provider_schema_version",
        scenario=_scenario(3694, "omnigent-fault-image-authority-drift", steps),
    )


def _websocket_missing() -> IncidentScenario:
    steps = [
        _s(LC.ENSURE_SESSION, side_effect=SE.CREATED),
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED),
        # WebSocket/SSE transport is unavailable; the read fails.
        _s(LC.READ_EVENTS, emit=({"type": "turn.running", "id": "r1"},), response=ResponseMode.ERROR),
        # Reconcile from the authoritative snapshot instead.
        _s(LC.OBSERVE_SNAPSHOT, snapshot={"sessionState": "idle", "turnState": "completed"}),
    ]
    return IncidentScenario(
        slug="omnigent-fault-websocket-missing",
        invariant="eventual_convergence",
        source_reference="MoonLadderStudios/MoonMind#3697",
        expected_decision="reconcile_from_snapshot_when_stream_unavailable",
        expected_classification="completed",
        operational_signal="event_stream_transport_failure",
        scenario=_scenario(3697, "omnigent-fault-websocket-missing", steps),
    )


def _duplicate_binding() -> IncidentScenario:
    steps = [
        _s(LC.ENSURE_SESSION, side_effect=SE.CREATED),
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED, turn="1"),
        # A duplicate/scoped binding re-submits the same turn identity.
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED, turn="1"),
        _s(LC.OBSERVE_SNAPSHOT, snapshot={"sessionState": "idle", "turnState": "completed"}),
    ]
    return IncidentScenario(
        slug="omnigent-fault-duplicate-binding",
        invariant="at_most_once_submission",
        source_reference="MoonLadderStudios/MoonMind#3696,MoonLadderStudios/MoonMind#3685",
        expected_decision="skip_duplicate_binding_submission",
        expected_classification="completed",
        operational_signal="duplicate_turn_binding",
        scenario=_scenario(3696, "omnigent-fault-duplicate-binding", steps),
    )


def _first_message_response_lost() -> IncidentScenario:
    steps = [
        _s(LC.ENSURE_SESSION, side_effect=SE.CREATED),
        # First-message dispatch is accepted but the response is lost.
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED, response=ResponseMode.DROP),
        _s(LC.RECONCILE, snapshot={"sessionState": "idle", "turnState": "completed"}),
    ]
    return IncidentScenario(
        slug="omnigent-fault-first-message-response-lost",
        invariant="no_blind_ambiguity_retry",
        source_reference="MoonLadderStudios/MoonMind#3709",
        expected_decision="reconcile_ambiguous_submission_not_resubmit",
        expected_classification="completed",
        operational_signal="accepted_turn_without_receipt",
        scenario=_scenario(370901, "omnigent-fault-first-message-response-lost", steps),
    )


def _cleanup_racing_continuation() -> IncidentScenario:
    steps = [
        _s(LC.ENSURE_SESSION, side_effect=SE.CREATED),
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED),
        _s(LC.OBSERVE_SNAPSHOT, snapshot={"sessionState": "idle", "turnState": "completed"}),
        _s(LC.HOST_REPLACE, side_effect=SE.REPLACED),
        # A stale cleanup races the replacement continuation's newer resources.
        _s(LC.CLEANUP, generation=3),
    ]
    return IncidentScenario(
        slug="omnigent-fault-cleanup-racing-continuation",
        invariant="cleanup_safety",
        source_reference="MoonLadderStudios/MoonMind#3709",
        expected_decision="skip_cleanup_of_replacement_generation",
        expected_classification="system_error",
        operational_signal="cleanup_targets_newer_generation",
        scenario=_scenario(370902, "omnigent-fault-cleanup-racing-continuation", steps),
    )


def _lease_replacement_old_host_alive() -> IncidentScenario:
    steps = [
        _s(LC.ENSURE_SESSION, side_effect=SE.CREATED),
        _s(LC.LEASE_ACQUIRE),
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED),
        # Lease "expires" while the old host still reports alive (a consumer).
        _s(LC.LEASE_EXPIRE, fault="lease_expired"),
        _s(LC.LEASE_REPLACE, side_effect=SE.REPLACED),
        # A former-generation write from the old host must be fenced.
        _s(LC.SUBMIT_TURN, side_effect=SE.ACCEPTED, generation=1, turn="stale"),
        _s(LC.OBSERVE_SNAPSHOT, snapshot={"sessionState": "idle", "turnState": "completed"}),
    ]
    return IncidentScenario(
        slug="omnigent-fault-lease-replacement-old-host-alive",
        invariant="lease_safety",
        source_reference="MoonLadderStudios/MoonMind#3709",
        expected_decision="defer_lease_release_and_fence_old_host",
        expected_classification="system_error",
        operational_signal="lease_expiry_with_active_consumer",
        scenario=_scenario(370903, "omnigent-fault-lease-replacement-old-host-alive", steps),
    )


def initial_incident_scenarios() -> list[IncidentScenario]:
    """The required initial scenarios seeded into the framework."""
    return [
        _missed_terminal_edge(),
        _idle_completion_vocabulary(),
        _stale_state_rollback(),
        _remediation_authority_loss(),
        _image_authority_drift(),
        _websocket_missing(),
        _duplicate_binding(),
        _first_message_response_lost(),
        _cleanup_racing_continuation(),
        _lease_replacement_old_host_alive(),
    ]


__all__ = ["IncidentScenario", "initial_incident_scenarios"]
