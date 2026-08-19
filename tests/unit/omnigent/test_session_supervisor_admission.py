"""MoonLadderStudios/MoonMind#3712 supervisor admission contract tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from moonmind.omnigent.session_supervisor_admission import (
    DISABLED_GENERATION,
    OMNIGENT_SESSION_SUPERVISOR_WORKFLOW_TYPE,
    SupervisorAdmissionRequest,
    SupervisorReadiness,
    SupervisorRolloutPolicy,
    evaluate_supervisor_admission,
    supervisor_rollout_policy_from_settings,
)


def _ready(generation: str = "gen-1") -> SupervisorReadiness:
    return SupervisorReadiness(
        deploymentGeneration=generation,
        supervisorWorkflowRegistered=True,
        compiledIntentReady=True,
        canonicalSchemaReady=True,
        exactArtifactConformancePassed=True,
        providerCapabilityReady=True,
        runtimeCapabilityReady=True,
        rollbackSupportActive=True,
        historicalReadSupportActive=True,
    )


def _policy(**overrides: object) -> SupervisorRolloutPolicy:
    base: dict[str, object] = {
        "enabled": True,
        "shadow": False,
        "generation": "gen-1",
    }
    base.update(overrides)
    return SupervisorRolloutPolicy(**base)


def _request(**overrides: object) -> SupervisorAdmissionRequest:
    base: dict[str, object] = {
        "owner_id": "owner-1",
        "execution_profile_ref": "exec-1",
        "launch_policy_ref": "launch-1",
        "provider_profile_id": "profile-1",
    }
    base.update(overrides)
    return SupervisorAdmissionRequest(**base)


def test_eligible_live_admission() -> None:
    snap = evaluate_supervisor_admission(
        policy=_policy(), readiness=_ready(), request=_request()
    )
    assert snap.admitted is True
    assert snap.mode == "live"
    assert snap.eligible is True
    assert snap.side_effects_allowed is True
    assert snap.shadow_recorded is False
    assert snap.generation == "gen-1"
    assert snap.workflow_type == OMNIGENT_SESSION_SUPERVISOR_WORKFLOW_TYPE
    assert snap.reason_code == "eligible"


def test_disabled_flag_blocks_admission() -> None:
    snap = evaluate_supervisor_admission(
        policy=_policy(enabled=False), readiness=_ready(), request=_request()
    )
    assert snap.admitted is False
    assert snap.mode == "denied"
    assert snap.reason_code == "supervisor_disabled"


def test_disabled_generation_blocks_admission() -> None:
    snap = evaluate_supervisor_admission(
        policy=_policy(generation=DISABLED_GENERATION),
        readiness=_ready(DISABLED_GENERATION),
        request=_request(),
    )
    assert snap.admitted is False
    assert snap.reason_code == "generation_disabled"


def test_generation_mismatch_between_policy_and_readiness() -> None:
    snap = evaluate_supervisor_admission(
        policy=_policy(generation="gen-2"),
        readiness=_ready("gen-1"),
        request=_request(),
    )
    assert snap.admitted is False
    assert snap.reason_code == "deployment_generation_mismatch"


def test_shadow_mode_records_but_never_acts() -> None:
    snap = evaluate_supervisor_admission(
        policy=_policy(shadow=True), readiness=_ready(), request=_request()
    )
    assert snap.eligible is True
    assert snap.admitted is False
    assert snap.mode == "shadow"
    assert snap.shadow_recorded is True
    # The absolute shadow invariant: no side effects even when fully eligible.
    assert snap.side_effects_allowed is False


def test_exact_owner_allowlist_rejects_substring() -> None:
    # "owner-1" is a substring of "owner-10" but must NOT match by substring.
    policy = _policy(allowed_owner_ids=frozenset({"owner-10"}))
    snap = evaluate_supervisor_admission(
        policy=policy, readiness=_ready(), request=_request(owner_id="owner-1")
    )
    assert snap.admitted is False
    assert snap.reason_code == "owner_not_allowlisted"

    ok = evaluate_supervisor_admission(
        policy=policy, readiness=_ready(), request=_request(owner_id="owner-10")
    )
    assert ok.admitted is True
    assert ok.canary_scoped is True


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("execution_profile_ref", "other", "execution_profile_not_allowlisted"),
        ("launch_policy_ref", "other", "launch_policy_not_allowlisted"),
        ("provider_profile_id", "other", "provider_profile_not_allowlisted"),
    ],
)
def test_each_canary_allowlist_is_exact(field: str, value: str, reason: str) -> None:
    allow = {
        "execution_profile_ref": {"allowed_execution_profile_refs": frozenset({"exec-1"})},
        "launch_policy_ref": {"allowed_launch_policy_refs": frozenset({"launch-1"})},
        "provider_profile_id": {"allowed_provider_profile_ids": frozenset({"profile-1"})},
    }[field]
    policy = _policy(**allow)
    snap = evaluate_supervisor_admission(
        policy=policy, readiness=_ready(), request=_request(**{field: value})
    )
    assert snap.admitted is False
    assert snap.reason_code == reason


@pytest.mark.parametrize(
    "ready_field,reason",
    [
        ("supervisor_workflow_registered", "supervisor_workflow_not_registered"),
        ("compiled_intent_ready", "compiled_intent_incomplete"),
        ("canonical_schema_ready", "canonical_schema_not_ready"),
        ("exact_artifact_conformance_passed", "exact_artifact_conformance_failed"),
        ("provider_capability_ready", "provider_capability_not_ready"),
        ("runtime_capability_ready", "runtime_capability_not_ready"),
        ("rollback_support_active", "rollback_support_inactive"),
        ("historical_read_support_active", "historical_read_support_inactive"),
    ],
)
def test_each_readiness_gate_fails_closed(ready_field: str, reason: str) -> None:
    kwargs = {
        "deploymentGeneration": "gen-1",
        "supervisorWorkflowRegistered": True,
        "compiledIntentReady": True,
        "canonicalSchemaReady": True,
        "exactArtifactConformancePassed": True,
        "providerCapabilityReady": True,
        "runtimeCapabilityReady": True,
        "rollbackSupportActive": True,
        "historicalReadSupportActive": True,
    }
    alias = {
        "supervisor_workflow_registered": "supervisorWorkflowRegistered",
        "compiled_intent_ready": "compiledIntentReady",
        "canonical_schema_ready": "canonicalSchemaReady",
        "exact_artifact_conformance_passed": "exactArtifactConformancePassed",
        "provider_capability_ready": "providerCapabilityReady",
        "runtime_capability_ready": "runtimeCapabilityReady",
        "rollback_support_active": "rollbackSupportActive",
        "historical_read_support_active": "historicalReadSupportActive",
    }[ready_field]
    kwargs[alias] = False
    snap = evaluate_supervisor_admission(
        policy=_policy(), readiness=SupervisorReadiness(**kwargs), request=_request()
    )
    assert snap.admitted is False
    assert snap.reason_code == reason


def test_unregistered_workflow_fails_closed_even_when_otherwise_ready() -> None:
    # Enabling the supervisor flags must not silently admit sessions while the
    # production MoonMind.OmnigentSession workflow is not yet registered/wired.
    readiness = SupervisorReadiness(
        deploymentGeneration="gen-1",
        supervisorWorkflowRegistered=False,
        compiledIntentReady=True,
        canonicalSchemaReady=True,
        exactArtifactConformancePassed=True,
        providerCapabilityReady=True,
        runtimeCapabilityReady=True,
        rollbackSupportActive=True,
        historicalReadSupportActive=True,
    )
    snap = evaluate_supervisor_admission(
        policy=_policy(), readiness=readiness, request=_request()
    )
    assert snap.admitted is False
    assert snap.mode == "denied"
    assert snap.reason_code == "supervisor_workflow_not_registered"


def test_empty_allowlists_mean_general_not_canary() -> None:
    snap = evaluate_supervisor_admission(
        policy=_policy(), readiness=_ready(), request=_request()
    )
    assert snap.canary_scoped is False
    assert snap.admitted is True


def test_policy_from_settings_parses_exact_csv_allowlists() -> None:
    flags = SimpleNamespace(
        omnigent_session_supervisor_enabled=True,
        omnigent_session_supervisor_shadow=False,
        omnigent_session_supervisor_generation="gen-7",
        omnigent_session_supervisor_allowed_owner_ids=" owner-a , owner-b ",
        omnigent_session_supervisor_allowed_execution_profile_refs="exec-a",
        omnigent_session_supervisor_allowed_launch_policy_refs="",
        omnigent_session_supervisor_allowed_provider_profile_ids="p1,p2",
    )
    policy = supervisor_rollout_policy_from_settings(flags)
    assert policy.enabled is True
    assert policy.generation == "gen-7"
    assert policy.allowed_owner_ids == frozenset({"owner-a", "owner-b"})
    assert policy.allowed_execution_profile_refs == frozenset({"exec-a"})
    assert policy.allowed_launch_policy_refs == frozenset()
    assert policy.allowed_provider_profile_ids == frozenset({"p1", "p2"})


def test_blank_generation_from_settings_fails_closed() -> None:
    flags = SimpleNamespace(
        omnigent_session_supervisor_enabled=True,
        omnigent_session_supervisor_shadow=False,
        omnigent_session_supervisor_generation="   ",
        omnigent_session_supervisor_allowed_owner_ids="",
        omnigent_session_supervisor_allowed_execution_profile_refs="",
        omnigent_session_supervisor_allowed_launch_policy_refs="",
        omnigent_session_supervisor_allowed_provider_profile_ids="",
    )
    policy = supervisor_rollout_policy_from_settings(flags)
    assert policy.generation == DISABLED_GENERATION
    snap = evaluate_supervisor_admission(
        policy=policy, readiness=_ready(), request=_request()
    )
    assert snap.reason_code == "generation_disabled"


def test_snapshot_records_generation_for_replay_safety() -> None:
    # An admitted snapshot freezes the generation so a later generation change
    # cannot reinterpret the already-admitted workflow.
    snap = evaluate_supervisor_admission(
        policy=_policy(generation="gen-1"), readiness=_ready("gen-1"), request=_request()
    )
    payload = snap.as_dict()
    assert payload["generation"] == "gen-1"
    assert payload["workflowType"] == OMNIGENT_SESSION_SUPERVISOR_WORKFLOW_TYPE
    assert payload["admitted"] is True
