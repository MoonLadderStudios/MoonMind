"""Unit tests for the single versioned effective-capability resolver.

Issue MoonLadderStudios/MoonMind#3636. Exercises the capability matrix across
caller roles, session states, pinned-vs-mutable config, immutable-policy gates,
upstream support, and fail-closed on stale/missing immutable authority.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.capability_resolver import (
    CAPABILITY_SCHEMA_VERSION,
    CallerAuthority,
    CapabilityDecision,
    CapabilityDenied,
    DisabledReason,
    EffectiveCapabilities,
    ImmutableExecutionAuthority,
    NativeCapability,
    SessionRuntimeState,
    UpstreamCapabilityEvidence,
    fail_closed,
    resolve_effective_capabilities,
)


def _launch_snapshot(
    *,
    control_capabilities=("interrupt", "terminate", "clear_context"),
    repository_mutation=True,
    read_resources=True,
    allow_model_change=False,
    allow_effort_change=False,
    allow_goal_change=False,
):
    return {
        "snapshotRef": "omnigent-launch:sha256:deadbeef",
        "launchPolicyRef": "lp-1",
        "providerProfileId": "pp-1",
        "policyAuthority": {"policyDigest": "sha256:policy-1"},
        "controlCapabilities": list(control_capabilities),
        "repositoryMutation": repository_mutation,
        "followUpRetrieval": {"enabled": read_resources},
        "boundaries": {
            "session": {
                "allowModelChange": allow_model_change,
                "allowEffortChange": allow_effort_change,
                "allowGoalChange": allow_goal_change,
            }
        },
    }


def _authority(**kwargs) -> ImmutableExecutionAuthority:
    imm = ImmutableExecutionAuthority.from_evidence(
        launch_snapshot=_launch_snapshot(**kwargs),
        provider_profile_id="pp-1",
        provider_profile_generation=7,
        agent_profile={"digest": "sha256:agent-1", "version": 3},
    )
    assert imm is not None
    return imm


def _full_upstream() -> UpstreamCapabilityEvidence:
    return UpstreamCapabilityEvidence.from_mapping(
        {
            "sendMessage": True,
            "queueMessage": True,
            "interruptTurn": True,
            "resolveElicitation": True,
            "stopSession": True,
            "clearSession": True,
            "readResources": True,
            "uploadFiles": True,
            "mutateWorkspace": True,
            "createTerminal": True,
            "writeTerminal": True,
            "viewTerminal": True,
            "closeTerminal": True,
            "openBrowser": True,
            "viewSubAgents": True,
            "controlSubAgents": True,
            "changeModel": True,
            "changeEffort": True,
            "changeGoal": True,
        }
    )


def _active_session() -> SessionRuntimeState:
    return SessionRuntimeState(
        provider_bound=True,
        terminal=False,
        starting=False,
        active_turn_id="turn-1",
        elicitation_pending=True,
    )


# --- authority construction ------------------------------------------------


def test_authority_missing_without_launch_snapshot():
    assert ImmutableExecutionAuthority.from_evidence(launch_snapshot=None) is None


def test_authority_missing_without_policy_digest():
    snap = _launch_snapshot()
    snap["policyAuthority"] = {}
    assert ImmutableExecutionAuthority.from_evidence(launch_snapshot=snap) is None


def test_authority_missing_without_snapshot_ref():
    snap = _launch_snapshot()
    snap.pop("snapshotRef")
    assert ImmutableExecutionAuthority.from_evidence(launch_snapshot=snap) is None


# --- fail-closed ------------------------------------------------------------


def test_missing_authority_fails_closed_all_denied():
    eff = resolve_effective_capabilities(
        immutable=None,
        session=_active_session(),
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
    )
    assert eff.fail_closed_reason is DisabledReason.MISSING_AUTHORITY
    assert eff.read_only is True
    assert all(v is False for v in eff.manifest().values())
    # even transcript view is denied when authority cannot be proven
    assert eff.allows(NativeCapability.VIEW_TRANSCRIPT) is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"expected_policy_digest": "sha256:other"},
        {"expected_launch_snapshot_ref": "omnigent-launch:sha256:stale"},
        {"expected_agent_profile_digest": "sha256:other-agent"},
        {"expected_provider_profile_generation": 999},
    ],
)
def test_stale_authority_fails_closed(kwargs):
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=_active_session(),
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
        **kwargs,
    )
    assert eff.fail_closed_reason is DisabledReason.STALE_AUTHORITY
    assert eff.read_only is True


def test_matching_expected_authority_is_not_stale():
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=_active_session(),
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
        expected_policy_digest="sha256:policy-1",
        expected_launch_snapshot_ref="omnigent-launch:sha256:deadbeef",
        expected_agent_profile_digest="sha256:agent-1",
        expected_provider_profile_generation=7,
    )
    assert eff.fail_closed_reason is None
    assert eff.allows(NativeCapability.SEND_MESSAGE) is True


# --- caller authority matrix -----------------------------------------------


def test_owner_gets_full_authorized_matrix():
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=_active_session(),
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
    )
    assert eff.read_only is False
    assert eff.allows(NativeCapability.SEND_MESSAGE)
    assert eff.allows(NativeCapability.INTERRUPT_TURN)
    assert eff.allows(NativeCapability.RESOLVE_ELICITATION)
    assert eff.allows(NativeCapability.MUTATE_WORKSPACE)
    assert eff.allows(NativeCapability.STOP_SESSION)
    assert eff.allows(NativeCapability.CLEANUP_SESSION)
    assert eff.version == CAPABILITY_SCHEMA_VERSION


def test_read_only_viewer_can_view_not_mutate_or_approve():
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=_active_session(),
        caller=CallerAuthority.read_only_viewer(),
        upstream=_full_upstream(),
    )
    assert eff.read_only is True
    assert eff.allows(NativeCapability.VIEW_TRANSCRIPT)
    assert eff.allows(NativeCapability.VIEW_TERMINAL)
    # transcript visibility does NOT imply mutation / approval / lifecycle (AC6)
    assert not eff.allows(NativeCapability.SEND_MESSAGE)
    assert eff.decision(NativeCapability.SEND_MESSAGE).reason is (
        DisabledReason.REQUIRES_MUTATE_AUTHORITY
    )
    assert eff.decision(NativeCapability.RESOLVE_ELICITATION).reason is (
        DisabledReason.REQUIRES_APPROVAL_AUTHORITY
    )
    assert eff.decision(NativeCapability.CLEANUP_SESSION).reason is (
        DisabledReason.REQUIRES_LIFECYCLE_AUTHORITY
    )
    assert not eff.allows(NativeCapability.WRITE_TERMINAL)
    assert not eff.allows(NativeCapability.OPEN_BROWSER)


def test_approver_can_resolve_elicitation_but_not_send():
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=_active_session(),
        caller=CallerAuthority.approver(),
        upstream=_full_upstream(),
    )
    assert eff.allows(NativeCapability.RESOLVE_ELICITATION)
    assert not eff.allows(NativeCapability.SEND_MESSAGE)
    assert not eff.allows(NativeCapability.CLEANUP_SESSION)


def test_unauthorized_caller_denied_everything():
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=_active_session(),
        caller=CallerAuthority.unauthorized(),
        upstream=_full_upstream(),
    )
    assert all(v is False for v in eff.manifest().values())


# --- session state ----------------------------------------------------------


def test_terminal_session_read_only_but_transcript_visible():
    session = SessionRuntimeState(provider_bound=True, terminal=True)
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=session,
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
    )
    assert eff.read_only is True
    assert eff.allows(NativeCapability.VIEW_TRANSCRIPT)
    assert eff.allows(NativeCapability.READ_RESOURCES)
    assert not eff.allows(NativeCapability.SEND_MESSAGE)
    assert eff.decision(NativeCapability.SEND_MESSAGE).reason is (
        DisabledReason.SESSION_TERMINAL
    )
    # cleanup is a mutating lifecycle op and is not offered from terminal state
    assert eff.decision(NativeCapability.CLEANUP_SESSION).reason is (
        DisabledReason.SESSION_TERMINAL
    )


def test_not_provider_bound_denies_reads_and_writes():
    session = SessionRuntimeState(provider_bound=False)
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=session,
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
    )
    assert eff.decision(NativeCapability.VIEW_TRANSCRIPT).reason is (
        DisabledReason.SESSION_NOT_BOUND
    )


def test_interrupt_requires_active_turn():
    session = SessionRuntimeState(provider_bound=True, active_turn_id=None)
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=session,
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
    )
    assert not eff.allows(NativeCapability.INTERRUPT_TURN)
    assert eff.decision(NativeCapability.INTERRUPT_TURN).reason is (
        DisabledReason.NO_ACTIVE_TURN
    )


def test_resolve_elicitation_requires_pending_elicitation():
    session = SessionRuntimeState(
        provider_bound=True, active_turn_id="t", elicitation_pending=False
    )
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=session,
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
    )
    assert eff.decision(NativeCapability.RESOLVE_ELICITATION).reason is (
        DisabledReason.NO_ACTIVE_ELICITATION
    )


# --- immutable policy / pins -----------------------------------------------


def test_pinned_model_effort_goal_cannot_be_changed():
    eff = resolve_effective_capabilities(
        immutable=_authority(),  # pins default to off
        session=_active_session(),
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
    )
    for cap in (
        NativeCapability.CHANGE_MODEL,
        NativeCapability.CHANGE_EFFORT,
        NativeCapability.CHANGE_GOAL,
    ):
        assert not eff.allows(cap)
        assert eff.decision(cap).reason is DisabledReason.PINNED_BY_PROFILE


def test_unpinned_config_can_change_when_policy_allows():
    eff = resolve_effective_capabilities(
        immutable=_authority(
            allow_model_change=True,
            allow_effort_change=True,
            allow_goal_change=True,
        ),
        session=_active_session(),
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
    )
    assert eff.allows(NativeCapability.CHANGE_MODEL)
    assert eff.allows(NativeCapability.CHANGE_EFFORT)
    assert eff.allows(NativeCapability.CHANGE_GOAL)


def test_policy_without_control_capability_forbids_interrupt():
    eff = resolve_effective_capabilities(
        immutable=_authority(control_capabilities=("terminate",)),
        session=_active_session(),
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
    )
    assert eff.decision(NativeCapability.INTERRUPT_TURN).reason is (
        DisabledReason.POLICY_FORBIDS
    )
    assert eff.allows(NativeCapability.STOP_SESSION)


def test_workspace_mutation_forbidden_by_policy():
    eff = resolve_effective_capabilities(
        immutable=_authority(repository_mutation=False),
        session=_active_session(),
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
    )
    for cap in (
        NativeCapability.MUTATE_WORKSPACE,
        NativeCapability.CREATE_TERMINAL,
        NativeCapability.WRITE_TERMINAL,
        NativeCapability.CLOSE_TERMINAL,
    ):
        assert eff.decision(cap).reason is DisabledReason.POLICY_FORBIDS
    # read-only terminal viewing is still allowed
    assert eff.allows(NativeCapability.VIEW_TERMINAL)


def test_read_resources_forbidden_when_follow_up_retrieval_disabled():
    eff = resolve_effective_capabilities(
        immutable=_authority(read_resources=False),
        session=_active_session(),
        caller=CallerAuthority.owner(),
        upstream=_full_upstream(),
    )
    assert eff.decision(NativeCapability.READ_RESOURCES).reason is (
        DisabledReason.POLICY_FORBIDS
    )


# --- upstream support -------------------------------------------------------


def test_upstream_denial_is_distinct_from_policy_denial():
    upstream = UpstreamCapabilityEvidence.from_mapping({"sendMessage": True})
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=_active_session(),
        caller=CallerAuthority.owner(),
        upstream=upstream,
    )
    assert eff.allows(NativeCapability.SEND_MESSAGE)
    # not advertised by the upstream -> unsupported, not a MoonMind denial
    assert eff.decision(NativeCapability.INTERRUPT_TURN).reason is (
        DisabledReason.UNSUPPORTED_UPSTREAM
    )


def test_moonmind_owned_ops_do_not_require_upstream():
    upstream = UpstreamCapabilityEvidence.from_mapping({})
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=_active_session(),
        caller=CallerAuthority.owner(),
        upstream=upstream,
    )
    # view/harvest/cleanup/reconnect are MoonMind-owned; no upstream key needed
    assert eff.allows(NativeCapability.VIEW_TRANSCRIPT)
    assert eff.allows(NativeCapability.HARVEST_EVIDENCE)
    assert eff.allows(NativeCapability.CLEANUP_SESSION)
    assert eff.allows(NativeCapability.RECONNECT_SESSION)


# --- enforcement + manifest projection -------------------------------------


def test_require_enforces_server_side_regardless_of_manifest():
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=_active_session(),
        caller=CallerAuthority.read_only_viewer(),
        upstream=_full_upstream(),
    )
    eff.require(NativeCapability.VIEW_TRANSCRIPT)  # allowed, no raise
    with pytest.raises(CapabilityDenied) as excinfo:
        eff.require(NativeCapability.SEND_MESSAGE)
    assert excinfo.value.capability is NativeCapability.SEND_MESSAGE
    assert excinfo.value.reason is DisabledReason.REQUIRES_MUTATE_AUTHORITY


def test_manifest_and_disabled_reasons_are_browser_safe():
    eff = resolve_effective_capabilities(
        immutable=_authority(),
        session=_active_session(),
        caller=CallerAuthority.read_only_viewer(),
        upstream=_full_upstream(),
    )
    manifest = eff.manifest()
    reasons = eff.disabled_reasons()
    assert manifest["viewTranscript"] is True
    assert manifest["sendMessage"] is False
    assert reasons["sendMessage"] == "requires_mutate_authority"
    assert "viewTranscript" not in reasons  # allowed caps carry no reason
    # every denied capability has a stable reason
    for key, allowed in manifest.items():
        if not allowed:
            assert key in reasons


def test_capability_decision_invariants():
    with pytest.raises(ValueError):
        CapabilityDecision(True, DisabledReason.SESSION_TERMINAL)
    with pytest.raises(ValueError):
        CapabilityDecision(False, None)


def test_fail_closed_helper_denies_all():
    eff = fail_closed(DisabledReason.STALE_AUTHORITY)
    assert isinstance(eff, EffectiveCapabilities)
    assert eff.fail_closed_reason is DisabledReason.STALE_AUTHORITY
    assert all(not d.allowed for d in eff.decisions.values())
