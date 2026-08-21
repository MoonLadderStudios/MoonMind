"""Effective native capability contract tests for MoonLadderStudios/MoonMind#3636."""

from datetime import UTC, datetime
from types import SimpleNamespace

from moonmind.omnigent.effective_capabilities import (
    CAPABILITY_NAMES,
    CAPABILITY_SCHEMA_VERSION,
    adapt_provider_capabilities,
    caller_capabilities_for_bridge,
    resolve_bridge_row_capabilities,
    resolve_effective_capabilities,
)
from moonmind.omnigent.harness_platform.attestation import (
    HostHarnessAttestation,
    compute_attestation_ref,
)
from moonmind.omnigent.harness_platform.capabilities import (
    ClassAdmissionDecision,
    ExactHostCapabilityDecision,
    compute_class_admission_ref,
    compute_exact_host_capability_decision_ref,
)
from moonmind.omnigent.harness_platform.catalog import HarnessImplementationIdentity
from moonmind.omnigent.harness_platform.execution_plan import (
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.runtime_binding import create_runtime_binding


def test_provider_capabilities_are_adapted_to_complete_canonical_namespace():
    adapted = adapt_provider_capabilities(
        {"sendFollowUp": True, "stop": True, "unknownProviderControl": True}
    )
    assert tuple(adapted) == CAPABILITY_NAMES
    assert adapted["sendMessage"] is True
    assert adapted["stopSession"] is True
    assert adapted["queueMessage"] is False
    assert "unknownProviderControl" not in adapted


def _authority(**overrides):
    value = {
        "agentProfileRef": "agent-profile://p/versions/7",
        "agentProfileDigest": "sha256:agent",
        "providerProfileId": "provider-1",
        "providerProfileGeneration": 4,
        "launchPolicyRef": "policy://launch/3",
        "policySnapshotRef": "artifact://policy",
        "policyDigest": "sha256:policy",
        "effectiveLaunchSnapshotRef": "artifact://launch",
        "sessionEpoch": 2,
        "authorityFresh": True,
    }
    value.update(overrides)
    return value


def _all(value=True):
    return {name: value for name in CAPABILITY_NAMES}


def _generic_harness_authority(*, session_id: str = "provider-session"):
    implementation = HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "a" * 64,
            "pluginEntryPoint": None,
        }
    )
    plan = create_execution_plan_envelope(
        {
            "endpointRef": "default",
            "agentProfileSnapshotRef": "agent-profile:sha256:" + "1" * 64,
            "harnessCatalogRef": "harness-catalog:sha256:" + "2" * 64,
            "harnessId": "opencode-native",
            "harnessImplementationRef": implementation.implementation_ref(),
            "agentSource": {"kind": "upstream", "upstreamId": "agent-1"},
            "credentialBindingSetRef": "credential-bindings:sha256:" + "3" * 64,
            "credentialBindings": {},
            "hostClassRef": "omnigent-native-standard@3",
            "launchPolicyRef": "omnigent-on-demand@1",
            "executionRealizerRef": "generic-omnigent-host@1",
            "model": {
                "qualifiedId": "opencode/test",
                "effort": None,
                "routeRef": "opencode-go",
                "normalizedOptions": {},
                "modelConfigDigest": "sha256:" + "4" * 64,
            },
            "resolvedSkills": {},
            "classAdmissionDecision": {
                "requiredSatisfied": ["interrupt"],
                "preferredSatisfied": [],
                "degraded": [],
                "unknown": [],
            },
            "runtimeValidationRequirements": ["exact-harness-implementation"],
            "workspaceIntentRef": "workspace-intent:sha256:" + "5" * 64,
            "policySnapshotRef": "policy:sha256:" + "6" * 64,
            "supportCombinationKey": "support:sha256:" + "7" * 64,
        }
    )
    attestation_values = {
        "hostId": "host-1",
        "hostClassRef": "omnigent-native-standard@3",
        "hostImageRef": "example.invalid/host@sha256:" + "8" * 64,
        "omnigentVersion": "1.0.0",
        "omnigentBuildDigest": "sha256:" + "9" * 64,
        "harnessId": "opencode-native",
        "harnessImplementation": implementation.model_dump(
            by_alias=True, mode="json"
        ),
        "runtimeDependencies": [],
        "configured": True,
        "capabilities": {"interrupt": True},
        "observedAt": datetime.now(UTC),
    }
    attestation = HostHarnessAttestation.model_validate(attestation_values)
    attestation_values["attestationRef"] = compute_attestation_ref(attestation)
    attestation = HostHarnessAttestation.model_validate(attestation_values)
    class_decision = ClassAdmissionDecision.model_validate(
        plan.payload.classAdmissionDecision
    )
    exact_decision = ExactHostCapabilityDecision.model_validate(
        {
            "classAdmissionRef": compute_class_admission_ref(class_decision),
            "exactHostAttested": True,
            "requiredSatisfied": ["interrupt"],
            "missingRequired": [],
            "degraded": [],
        }
    )
    decision_ref = compute_exact_host_capability_decision_ref(exact_decision)
    binding = create_runtime_binding(
        executionPlanRef=plan.planRef,
        providerLeases={},
        hostBindingRef="host-binding:1",
        hostLeaseRef="host-lease:1",
        hostLeaseGeneration=1,
        omnigentHostId="host-1",
        hostHarnessAttestationRef=attestation.attestationRef,
        exactHostCapabilityDecisionRef=decision_ref,
        omnigentSessionId=session_id,
    )
    return {
        "executionPlan": plan.model_dump(by_alias=True, mode="json"),
        "runtimeBinding": binding.model_dump(by_alias=True, mode="json"),
        "hostHarnessAttestation": attestation.model_dump(by_alias=True, mode="json"),
        "exactHostCapabilityDecision": exact_decision.model_dump(
            by_alias=True, mode="json"
        ),
    }


def _resolve(**overrides):
    values = dict(
        authority=_authority(),
        upstream_capabilities=_all(),
        profile_capabilities=_all(),
        launch_capabilities=_all(),
        state_capabilities=_all(),
        caller_capabilities=_all(),
        session_status="active",
    )
    values.update(overrides)
    return resolve_effective_capabilities(**values)


def test_versioned_resolver_covers_complete_operation_matrix():
    result = _resolve()
    assert result.schema_version == CAPABILITY_SCHEMA_VERSION
    assert tuple(result.decisions) == CAPABILITY_NAMES
    assert all(result.capabilities.values())
    assert len(result.authority_digest) == 64


def test_each_authority_independently_removes_capability_with_stable_reason():
    callers = _all()
    callers["resolveElicitation"] = False
    decision = _resolve(caller_capabilities=callers).decisions["resolveElicitation"]
    assert decision.allowed is False
    assert decision.reason == "caller_not_authorized"
    assert decision.upstream_supported is True


def test_missing_and_stale_immutable_authority_fail_closed():
    missing = _resolve(authority=_authority(agentProfileDigest=None))
    assert set(missing.capabilities.values()) == {False}
    assert set(missing.disabled_reasons.values()) == {"immutable_authority_missing"}
    stale = _resolve(authority=_authority(expectedProviderProfileGeneration=5))
    assert set(stale.disabled_reasons.values()) == {"provider_generation_stale"}


def test_terminal_session_preserves_reads_and_post_terminal_lifecycle():
    result = _resolve(session_status="completed")
    assert result.capabilities["viewTranscript"] is True
    assert result.capabilities["readResources"] is True
    assert result.capabilities["sendMessage"] is False
    assert result.decisions["sendMessage"].reason == "session_terminal"
    assert result.capabilities["harvestEvidence"] is True
    assert result.capabilities["cleanupSession"] is True


def test_missing_source_entry_cannot_be_inferred_or_granted():
    upstream = _all()
    del upstream["changeModel"]
    result = _resolve(upstream_capabilities=upstream)
    assert result.capabilities["changeModel"] is False
    assert result.decisions["changeModel"].reason == "upstream_unsupported"


def test_bridge_row_adapter_uses_only_execution_bound_authority():
    grants = _all()
    row = SimpleNamespace(
        status="active",
        provider_profile_id="provider-1",
        credential_generation=4,
        effective_launch_snapshot_json={
            "executionProfileRef": "agent-profile://p/versions/7",
            "executionProfileDigest": "sha256:agent",
            "launchPolicyRef": "policy://launch/3",
            "snapshotRef": "artifact://launch",
            "policyAuthority": {
                "snapshotRef": "artifact://policy",
                "policyDigest": "sha256:policy",
            },
        },
        metadata_={
            # A contradictory legacy map must not become parallel authority.
            "interventionCapabilities": _all(False),
            "capabilityAuthority": {
                "fresh": True,
                "providerProfileGeneration": 4,
                "upstream": grants,
                "agentProfile": grants,
                "launchPolicy": grants,
                "state": {"sessionEpoch": 2, "capabilities": grants},
            },
        },
    )
    result = resolve_bridge_row_capabilities(row, caller_capabilities=grants)
    assert all(result.capabilities.values())

    stale = resolve_bridge_row_capabilities(
        row, caller_capabilities=grants, expected_session_epoch=3
    )
    assert set(stale.disabled_reasons.values()) == {"session_epoch_stale"}


def test_bridge_row_adapter_fails_closed_without_capability_authority():
    row = SimpleNamespace(
        status="active",
        provider_profile_id="provider-1",
        credential_generation=4,
        effective_launch_snapshot_json={},
        metadata_={"interventionCapabilities": _all()},
    )
    result = resolve_bridge_row_capabilities(row, caller_capabilities=_all())
    assert set(result.disabled_reasons.values()) == {"immutable_authority_missing"}


def test_bridge_row_adapter_intersects_generic_plan_and_exact_host_authority():
    grants = _all()
    row = SimpleNamespace(
        status="active",
        omnigent_session_id="provider-session",
        omnigent_host_id="host-1",
        provider_profile_id="provider-1",
        credential_generation=4,
        effective_launch_snapshot_json={
            "executionProfileRef": "agent-profile://p/versions/7",
            "executionProfileDigest": "sha256:agent",
            "launchPolicyRef": "policy://launch/3",
            "snapshotRef": "artifact://launch",
            "executionRealizerRef": "generic-omnigent-host@1",
            "policyAuthority": {
                "snapshotRef": "artifact://policy",
                "policyDigest": "sha256:policy",
            },
        },
        metadata_={
            "harnessAuthority": _generic_harness_authority(),
            "capabilityAuthority": {
                "fresh": True,
                "providerProfileGeneration": 4,
                "upstream": grants,
                "agentProfile": grants,
                "launchPolicy": grants,
                "state": {"sessionEpoch": 2, "capabilities": grants},
            },
        },
    )

    result = resolve_bridge_row_capabilities(row, caller_capabilities=grants)

    assert all(result.capabilities.values())


def test_bridge_row_adapter_rejects_tampered_generic_harness_authority():
    grants = _all()
    harness_authority = _generic_harness_authority()
    harness_authority["hostHarnessAttestation"]["hostId"] = "host-2"
    row = SimpleNamespace(
        status="active",
        omnigent_session_id="provider-session",
        omnigent_host_id="host-1",
        provider_profile_id="provider-1",
        credential_generation=4,
        effective_launch_snapshot_json={
            "executionProfileRef": "agent-profile://p/versions/7",
            "executionProfileDigest": "sha256:agent",
            "launchPolicyRef": "policy://launch/3",
            "snapshotRef": "artifact://launch",
            "executionRealizerRef": "generic-omnigent-host@1",
            "policyAuthority": {
                "snapshotRef": "artifact://policy",
                "policyDigest": "sha256:policy",
            },
        },
        metadata_={
            "harnessAuthority": harness_authority,
            "capabilityAuthority": {
                "fresh": True,
                "providerProfileGeneration": 4,
                "upstream": grants,
                "agentProfile": grants,
                "launchPolicy": grants,
                "state": {"sessionEpoch": 2, "capabilities": grants},
            },
        },
    )

    result = resolve_bridge_row_capabilities(row, caller_capabilities=grants)

    assert set(result.capabilities.values()) == {False}
    assert set(result.disabled_reasons.values()) == {"harness_authority_invalid"}


def test_bridge_row_adapter_requires_authority_for_generic_realizer():
    grants = _all()
    row = SimpleNamespace(
        status="active",
        provider_profile_id="provider-1",
        credential_generation=4,
        effective_launch_snapshot_json={
            "executionProfileRef": "agent-profile://p/versions/7",
            "executionProfileDigest": "sha256:agent",
            "launchPolicyRef": "policy://launch/3",
            "snapshotRef": "artifact://launch",
            "executionRealizerRef": "generic-omnigent-host@1",
            "policyAuthority": {
                "snapshotRef": "artifact://policy",
                "policyDigest": "sha256:policy",
            },
        },
        metadata_={
            "capabilityAuthority": {
                "fresh": True,
                "providerProfileGeneration": 4,
                "upstream": grants,
                "agentProfile": grants,
                "launchPolicy": grants,
                "state": {"sessionEpoch": 2, "capabilities": grants},
            }
        },
    )

    result = resolve_bridge_row_capabilities(row, caller_capabilities=grants)

    assert set(result.disabled_reasons.values()) == {"harness_authority_invalid"}


def test_caller_authority_separates_owner_from_approver_and_viewer():
    owner = SimpleNamespace(id="owner", is_superuser=False)
    row = SimpleNamespace(metadata_={})
    owner_grants = caller_capabilities_for_bridge(row, owner)
    assert owner_grants["sendMessage"] is True
    assert owner_grants["viewTranscript"] is True
    assert owner_grants["resolveElicitation"] is False
    assert owner_grants["cleanupSession"] is False

    row.metadata_ = {
        "callerAuthorities": {
            "viewer": {
                "viewTranscript": True,
                "readResources": True,
            }
        }
    }
    viewer_grants = caller_capabilities_for_bridge(
        row, SimpleNamespace(id="viewer", is_superuser=False)
    )
    assert viewer_grants["viewTranscript"] is True
    assert viewer_grants["sendMessage"] is False
    assert viewer_grants["writeTerminal"] is False

    approver_grants = caller_capabilities_for_bridge(
        SimpleNamespace(metadata_={}),
        SimpleNamespace(id="admin", is_superuser=True),
    )
    assert approver_grants["resolveElicitation"] is True


def test_provider_control_adapter_preserves_reads_and_distinct_cleanup_authority():
    adapted = adapt_provider_capabilities(
        {"sendFollowUp": True, "clearSession": True, "terminalCleanup": False}
    )

    assert adapted["viewTranscript"] is True
    assert adapted["readResources"] is True
    assert adapted["viewTerminal"] is True
    assert adapted["viewSubagents"] is True
    assert adapted["sendMessage"] is True
    assert adapted["replaceSession"] is True
    assert adapted["cleanupSession"] is False

    cleanup = adapt_provider_capabilities({"terminalCleanup": True})
    assert cleanup["cleanupSession"] is True
    assert cleanup["replaceSession"] is False
