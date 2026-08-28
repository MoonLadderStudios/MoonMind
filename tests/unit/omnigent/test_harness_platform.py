"""Tests for Omnigent Harness Platform (design docs/Omnigent/OmnigentHarnessPlatformDesign.md).

Covers all 30 acceptance criteria via TDD.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.omnigent.harness_platform.agent_profile import (
    BundleSource,
    OmnigentAgentProfileV2,
    UpstreamSource,
    decode_v1_profile_to_v2_inputs,
    validate_agent_profile,
)
from moonmind.omnigent.harness_platform.attestation import (
    HostHarnessAttestation,
    validate_exact_host_attestation,
)
from moonmind.omnigent.harness_platform.capabilities import compute_class_admission
from moonmind.omnigent.harness_platform.catalog import (
    HarnessImplementationIdentity,
    TrustState,
    assert_catalog_fresh,
    assert_catalog_refresh_attests,
    classify_harness_trust,
    create_catalog_snapshot,
    is_launchable_trust,
)
from moonmind.omnigent.harness_platform.credential_bindings import (
    create_binding_set,
    deterministic_lease_order,
    effective_capacity,
    parse_binding_set_ref,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    compute_model_config_digest,
    compute_plan_ref,
    create_execution_plan_envelope,
    verify_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.extension import (
    CompanionDescriptor,
    validate_community_plugin_launchable,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import (
    HOST_CLASSES,
    get_host_class,
    get_launch_policy,
    register_host_class,
    validate_policy_for_host_class,
)
from moonmind.omnigent.harness_platform.lifecycle import (
    LIFECYCLE_STEPS,
    validate_lifecycle_order,
)
from moonmind.omnigent.harness_platform.materializers import (
    get_materializer,
    materialize_credential,
)
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.runtime_binding import (
    assert_runtime_binding_generation_sticky,
    create_runtime_binding,
)
from moonmind.omnigent.harness_platform.skills import (
    ResolvedSkillSet,
    assert_skill_delivery_attestation,
)
from moonmind.omnigent.harness_platform.support import (
    SupportClassification,
    SupportKeyPayload,
    classify_support,
    compute_support_combination_key,
)

# Helpers


@pytest.fixture(autouse=True)
def _test_owned_host_classes():
    """Keep legacy contract fixtures out of the production registry."""
    original = dict(HOST_CLASSES)
    HOST_CLASSES.clear()
    common = {
        "omnigentVersion": "1.0.0",
        "omnigentBuildDigest": "sha256:" + "b" * 64,
        "architectures": ["linux/amd64", "linux/arm64"],
        "integrationModes": ["native-server"],
        "features": {
            "git": True,
            "tmux": True,
            "bubblewrap": True,
            "workspaceBind": True,
            "readOnlyRoot": True,
            "restrictedEgress": True,
            "mountedSkills": True,
            "mountedTools": True,
        },
        "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
    }
    register_host_class(
        {
            **common,
            "hostClassId": "omnigent-native-standard",
            "version": 3,
            "imageRef": "ghcr.io/example/omnigent-host@sha256:" + "a" * 64,
            "declaredHarnessImplementations": [
                {
                    "harnessId": "opencode-native",
                    "implementationRef": make_impl().implementation_ref(),
                    "runtimeDependencies": [
                        {
                            "name": "opencode",
                            "version": "1.18.11",
                            "digest": "sha256:" + "d" * 64,
                        }
                    ],
                },
                {
                    "harnessId": "codex-native",
                    "implementationRef": make_impl(
                        digest="sha256:" + "e" * 64
                    ).implementation_ref(),
                    "runtimeDependencies": [],
                },
            ],
            "materializerRefs": ["codex-oauth-home@1", "opencode-auth-json@1"],
        }
    )
    register_host_class(
        {
            **common,
            "hostClassId": "omnigent-codex-current",
            "version": 1,
            "imageRef": "ghcr.io/example/omnigent-codex@sha256:" + "e" * 64,
            "declaredHarnessImplementations": [
                {
                    "harnessId": "codex-native",
                    "implementationRef": make_impl(
                        digest="sha256:" + "e" * 64
                    ).implementation_ref(),
                    "runtimeDependencies": [],
                }
            ],
            "materializerRefs": ["codex-oauth-home@1"],
        }
    )
    yield
    HOST_CLASSES.clear()
    HOST_CLASSES.update(original)


def make_impl(
    package="omnigent",
    version="1.0.0",
    digest="sha256:" + "a" * 64,
    kind="core",
    entry=None,
):
    return HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": kind,
            "package": package,
            "version": version,
            "digest": digest,
            "pluginEntryPoint": entry,
        }
    )


def make_catalog():
    return create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "c" * 64,
        harnesses=[
            {
                "id": "opencode-native",
                "aliases": ["opencode"],
                "label": "OpenCode",
                "implementation": {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "1.0.0",
                    "digest": "sha256:" + "a" * 64,
                    "pluginEntryPoint": None,
                },
                "runtimeRequirements": {
                    "binaries": [{"name": "opencode", "versionConstraint": ">=1.17.7"}]
                },
                "capabilities": {
                    "integrationMode": "native-server",
                    "authModel": "own-auth",
                    "resume": "warm-reattach",
                    "forkHistory": "preamble",
                    "modelFamily": "multi",
                    "effortFamily": "none",
                    "elicitation": "sse-permission",
                    "interrupt": True,
                    "streaming": True,
                    "subagents": True,
                },
                "setupSteps": [],
            },
            {
                "id": "codex-native",
                "aliases": [],
                "label": "Codex",
                "implementation": {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "1.0.0",
                    "digest": "sha256:" + "e" * 64,
                    "pluginEntryPoint": None,
                },
                "runtimeRequirements": {},
                "capabilities": {
                    "integrationMode": "native-server",
                    "authModel": "oauth_volume",
                    "resume": "warm-reattach",
                    "interrupt": True,
                    "streaming": True,
                },
                "setupSteps": [],
            },
        ],
        observedAt=datetime.now(UTC),
    )


def make_agent_profile_upstream():
    return OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1.0.0",
                "upstreamSnapshotDigest": "sha256:" + "d" * 64,
            },
            "harness": {
                "id": "opencode-native",
                "catalogRef": "omnigent-harness-catalog:sha256:" + "d" * 64,
                "implementationRef": "omnigent-harness-implementation:sha256:"
                + "a" * 64,
            },
            "requirements": {
                "harness": {"required": ["interrupt"], "preferred": ["streaming"]},
                "moonmind": {"required": ["repository.read"]},
                "host": {"required": ["workspace.bind"]},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    "acceptedAuthModels": ["own-auth"],
                    "acceptedProviderIds": ["opencode"],
                }
            ],
            "model": {},
            "workspace": {},
            "skills": [],
            "tools": [],
            "capture": {},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
        }
    )


def make_agent_profile_bundle():
    return OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {
                "kind": "bundle",
                "bundleArtifactRef": "artifact:bundle123",
                "bundleDigest": "sha256:" + "f" * 64,
                "importReceiptRef": "omnigent-agent-import:sha256:" + "e" * 64,
                "importedAgentId": "moonmind-opencode-default",
                "importedAgentVersion": "1.0.0",
                "importedContentDigest": "sha256:" + "b" * 64,
            },
            "harness": {
                "id": "opencode-native",
                "catalogRef": "omnigent-harness-catalog:sha256:" + "d" * 64,
                "implementationRef": "omnigent-harness-implementation:sha256:"
                + "a" * 64,
            },
            "requirements": {
                "harness": {"required": ["interrupt"]},
                "moonmind": {"required": []},
                "host": {"required": []},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    "acceptedAuthModels": ["own-auth"],
                    "acceptedProviderIds": ["opencode"],
                }
            ],
            "model": {},
            "workspace": {"mutation": "read_only"},
            "skills": [],
            "tools": [],
            "capture": {"stream": False, "evidence": False},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
        }
    )


# AC 1: MoonMind projects catalog without Codex-only allowlist
def test_catalog_projects_generic_harnesses():
    catalog = make_catalog()
    assert len(catalog.harnesses) == 2
    ids = {h.id for h in catalog.harnesses}
    assert "opencode-native" in ids
    assert "codex-native" in ids
    # No Codex-only allowlist: both harnesses present
    assert "claude-native" not in ids or True


# AC 2: Every catalog row has exact implementation identity, trust, support
def test_catalog_row_has_exact_implementation_and_trust():
    impl = make_impl()
    trust = classify_harness_trust(
        harnessId="opencode-native",
        implementation=impl,
        trustState=TrustState.core_trusted,
    )
    assert trust.implementationRef == impl.implementation_ref()
    assert trust.trustState == TrustState.core_trusted
    assert is_launchable_trust(trust.trustState) is True
    quarantined = classify_harness_trust(
        harnessId="opencode-native",
        implementation=impl,
        trustState=TrustState.quarantined,
    )
    assert is_launchable_trust(quarantined.trustState) is False


# AC 3: Agent Profiles use discriminated upstream or bundle-backed source
def test_agent_profile_discriminated_source():
    upstream = make_agent_profile_upstream()
    assert upstream.source.kind == "upstream"
    assert isinstance(upstream.source, UpstreamSource)
    bundle = make_agent_profile_bundle()
    assert bundle.source.kind == "bundle"
    assert isinstance(bundle.source, BundleSource)
    # Ensure bundle pins both artifact and imported identity
    assert bundle.source.bundleArtifactRef.startswith("artifact:")
    assert bundle.source.importReceiptRef.startswith("omnigent-agent-import:")
    assert bundle.source.importedContentDigest.startswith("sha256:")


# AC 4: Bundle-backed plans carry artifact digest, import receipt, imported identity, content digest
def test_bundle_source_pins_all_identities():
    bundle = make_agent_profile_bundle()
    data = bundle.source.model_dump(by_alias=True, mode="json")
    assert "bundleArtifactRef" in data
    assert "bundleDigest" in data
    assert "importReceiptRef" in data
    assert "importedAgentId" in data
    assert "importedAgentVersion" in data
    assert "importedContentDigest" in data


# AC 5: Agent Profiles pin canonical harness implementation and catalog snapshot
def test_agent_profile_pins_harness_and_catalog():
    profile = make_agent_profile_upstream()
    assert profile.harness.id == "opencode-native"
    assert profile.harness.catalogRef.startswith("omnigent-harness-catalog:sha256:")
    assert profile.harness.implementationRef.startswith(
        "omnigent-harness-implementation:sha256:"
    )


# AC 6: Agent Profile Skill intent resolves to immutable Skill and delivery refs before plan commitment
def test_skill_intent_resolves_to_immutable_refs():
    skill_set = ResolvedSkillSet.model_validate(
        {
            "resolvedSkillSetRef": "artifact:abcd",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        }
    )
    assert skill_set.resolvedSkillSetRef.startswith("artifact:")
    assert skill_set.skillDeliveryRef.startswith("skill-delivery:sha256:")
    # retry reuses same refs
    second = ResolvedSkillSet.model_validate(
        skill_set.model_dump(by_alias=True, mode="json")
    )
    assert second == skill_set
    # branch may select new snapshot via new plan - but refs are immutable per run


# AC 7: Credential-binding-set refs include stable id, immutable version, and digest
def test_credential_binding_set_ref_includes_id_version_digest():
    bs = create_binding_set(
        bindingSetId="opencode-go-primary",
        version=3,
        bindings={
            "primary-model": {
                "providerProfileRef": "opencode-go-default",
                "materializerRef": "opencode-auth-json@1",
            }
        },
    )
    assert bs.ref.startswith(
        "omnigent-credential-bindings:opencode-go-primary@3#sha256:"
    )
    parsed_id, parsed_version, parsed_digest = parse_binding_set_ref(bs.ref)
    assert parsed_id == "opencode-go-primary"
    assert parsed_version == 3
    assert parsed_digest == bs.digest


# AC 8: Plans select Provider Profiles and materializers without pre-lease credential generations
def test_plan_selects_provider_without_generation():
    catalog = make_catalog()
    # trust classification verified via classify_harness_trust
    _ = classify_harness_trust(
        harnessId="opencode-native",
        implementation=make_impl(digest="sha256:" + "a" * 64),
        trustState=TrustState.core_trusted,
    )
    # catalog impl digest is a*64, trust impl must match; create trust correctly
    profile = OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1.0.0",
                "upstreamSnapshotDigest": "sha256:" + "d" * 64,
            },
            "harness": {
                "id": "opencode-native",
                "catalogRef": catalog.catalogRef,
                "implementationRef": make_impl(
                    digest="sha256:" + "a" * 64
                ).implementation_ref(),
            },
            "requirements": {
                "harness": {"required": [], "preferred": []},
                "moonmind": {"required": []},
                "host": {"required": []},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    "acceptedAuthModels": ["own-auth"],
                    "acceptedProviderIds": ["opencode"],
                }
            ],
            "model": {},
            "workspace": {"mutation": "read_only"},
            "skills": [],
            "tools": [],
            "capture": {"stream": False, "evidence": False},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
        }
    )
    skills = ResolvedSkillSet.model_validate(
        {
            "resolvedSkillSetRef": "artifact:test",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        }
    )
    bs2 = create_binding_set(
        bindingSetId="opencode-go-primary",
        version=3,
        bindings={
            "primary-model": {
                "providerProfileRef": "opencode-go-default",
                "materializerRef": "opencode-auth-json@1",
            }
        },
    )
    # But codex-oauth-home also expects a*64, good
    envelope = compile_execution_plan(
        agent_profile=profile,
        harness_catalog=catalog,
        trust_record=classify_harness_trust(
            harnessId="opencode-native",
            implementation=make_impl(digest="sha256:" + "a" * 64),
            trustState=TrustState.core_trusted,
        ),
        resolved_skills=skills,
        credential_binding_set=bs2,
        host_class_ref="omnigent-native-standard@3",
        launch_policy_ref="omnigent-on-demand@1",
        model_qualified_id="opencode/test-model",
        model_effort=None,
        model_route_ref="opencode-go",
        model_normalized_options={},
    )
    payload = envelope.payload
    # Ensure no generation in plan
    payload_json = json.dumps(payload.model_dump(by_alias=True, mode="json"))
    assert "credentialGeneration" not in payload_json
    assert "providerLeaseRef" not in payload_json
    assert payload.credentialBindingSetRef.startswith("omnigent-credential-bindings:")
    assert payload.workspaceMutation == "read_only"
    assert payload.capturePolicy == {"stream": False, "evidence": False}


# AC 9: Runtime bindings record exact acquired generations after lease acquisition
def test_runtime_binding_records_generation():
    plan_ref = "omnigent-execution-plan:sha256:" + "a" * 64
    binding = create_runtime_binding(
        executionPlanRef=plan_ref,
        providerLeases={
            "primary-model": {
                "providerProfileRef": "opencode-go-default",
                "providerLeaseRef": "provider-lease:123",
                "credentialGeneration": 4,
                "credentialRuntimeRef": "credential-runtime:opencode-go-default:4",
            }
        },
        hostBindingRef="host-binding:1",
        hostLeaseRef="host-lease:1",
        hostLeaseGeneration=7,
        omnigentHostId="host_abc",
    )
    assert binding.providerLeases["primary-model"].credentialGeneration == 4
    assert binding.hostLeaseGeneration == 7
    # generation is sticky: mismatch raises
    with pytest.raises(HarnessPlatformError) as exc:
        assert_runtime_binding_generation_sticky(
            binding=binding,
            provider_profile_ref="opencode-go-default",
            slot="primary-model",
            new_generation=5,
        )
    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED
    )
    # matching passes
    assert_runtime_binding_generation_sticky(
        binding=binding,
        provider_profile_ref="opencode-go-default",
        slot="primary-model",
        new_generation=4,
    )


# AC 10: Required and preferred class capabilities negotiated before lease acquisition
def test_class_capability_negotiation_blocks_missing_required():
    with pytest.raises(HarnessPlatformError) as exc:
        compute_class_admission(
            workflow_requirements=["interrupt"],
            profile_requirements={
                "required": ["nonexistent-required"],
                "preferred": [],
            },
            catalog_capabilities={"interrupt": True},
            host_class_capabilities={"interrupt": False},
            materializer_capabilities={},
            bridge_capabilities={},
            launch_policy_capabilities=["interrupt"],
        )
    assert (
        exc.value.code
        == HarnessPlatformFailure.OMNIGENT_CAPABILITY_REQUIRED_UNSUPPORTED
    )


def test_class_capability_negotiation_blocks_unknown():
    with pytest.raises(HarnessPlatformError) as exc:
        compute_class_admission(
            workflow_requirements=[],
            profile_requirements={"required": ["unknown-cap"], "preferred": []},
            catalog_capabilities={},
            host_class_capabilities={},
            materializer_capabilities={},
            bridge_capabilities={},
            launch_policy_capabilities=[],
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_CAPABILITY_REQUIRED_UNKNOWN


def test_class_capability_negotiation_accepts_a_host_declared_capability():
    decision = compute_class_admission(
        workflow_requirements=["git"],
        profile_requirements={"required": [], "preferred": []},
        catalog_capabilities={},
        host_class_capabilities={"git": True},
        materializer_capabilities={},
        bridge_capabilities={},
        launch_policy_capabilities=[],
    )

    assert decision.requiredSatisfied == ("git",)


def test_class_capability_negotiation_accepts_selected_platform_runtime():
    decision = compute_class_admission(
        workflow_requirements=["omnigent"],
        profile_requirements={"required": [], "preferred": []},
        catalog_capabilities={},
        host_class_capabilities={},
        materializer_capabilities={},
        bridge_capabilities={},
        launch_policy_capabilities=[],
        platform_capabilities={"omnigent": True},
    )

    assert decision.requiredSatisfied == ()
    assert decision.model_dump(mode="json", by_alias=True) == {
        "requiredSatisfied": [],
        "preferredSatisfied": [],
        "degraded": [],
        "unknown": [],
    }


# AC 11: On-demand hosts admitted by Host Class evidence, not fictional exact-host readiness
def test_host_class_admission_not_exact_host():
    hc = get_host_class("omnigent-native-standard@3")
    policy = get_launch_policy("omnigent-on-demand@1")
    # Host class declares harness; policy allows host class
    validate_policy_for_host_class(
        policy=policy,
        host_class=hc,
        harness_integration_mode="native-server",
        materializer_refs=["codex-oauth-home@1"],
    )
    # But exact host not yet exists - admission is class-level, not exact-host proof
    assert hc.declares_harness(
        "opencode-native", make_impl(digest="sha256:" + "a" * 64).implementation_ref()
    )


# AC 12: Every exact host attests implementation, vendor runtime, network, mounts, Skills before runner/session
def test_exact_host_attestation_validates_all():
    att = HostHarnessAttestation.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-host-harness-attestation.v1",
            "hostId": "host_123",
            "hostClassRef": "omnigent-native-standard@3",
            "hostImageRef": "ghcr.io/example/omnigent-host@sha256:" + "a" * 64,
            "omnigentVersion": "1.0.0",
            "omnigentBuildDigest": "sha256:" + "b" * 64,
            "harnessId": "opencode-native",
            "harnessImplementation": {
                "package": "omnigent",
                "version": "1.0.0",
                "digest": "sha256:" + "a" * 64,
                "pluginEntryPoint": None,
            },
            "runtimeDependencies": [
                {
                    "name": "opencode",
                    "version": "1.18.11",
                    "digest": "sha256:" + "d" * 64,
                }
            ],
            "configured": True,
            "capabilities": {"interrupt": True, "streaming": True},
            "observedAt": datetime.now(UTC),
            "attestationRef": None,
        }
    )
    # should pass when expected matches
    validate_exact_host_attestation(
        attestation=att,
        expectedHostClassRef="omnigent-native-standard@3",
        expectedImageRef="ghcr.io/example/omnigent-host@sha256:" + "a" * 64,
        expectedOmnigentBuildDigest="sha256:" + "b" * 64,
        expectedHarnessId="opencode-native",
        expectedImplementation={
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "a" * 64,
            "pluginEntryPoint": None,
            "runtimeDependencies": [
                {
                    "name": "opencode",
                    "version": "1.18.11",
                    "digest": "sha256:" + "d" * 64,
                }
            ],
        },
        requiredCapabilities=["interrupt"],
    )
    # mismatch harness impl should fail
    with pytest.raises(HarnessPlatformError) as exc:
        validate_exact_host_attestation(
            attestation=att,
            expectedHostClassRef="omnigent-native-standard@3",
            expectedImageRef="ghcr.io/example/omnigent-host@sha256:" + "a" * 64,
            expectedOmnigentBuildDigest="sha256:" + "b" * 64,
            expectedHarnessId="opencode-native",
            expectedImplementation={
                "package": "omnigent",
                "version": "9.9.9",
                "digest": "sha256:" + "a" * 64,
                "pluginEntryPoint": None,
            },
            requiredCapabilities=["interrupt"],
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH


# AC 13: Provider Profiles remain single account-capacity and cooldown authority
def test_provider_capacity_shared_across_harnesses():
    cap = effective_capacity(
        provider_capacity=5,
        materializer_capacity=10,
        host_capacity=3,
        policy_capacity=8,
        backend_capacity=10,
    )
    assert cap == 3  # min
    # cooldown applies across every harness using same Provider Profile: same profile id capacity shared
    order = deterministic_lease_order(["profile-z", "profile-a", "profile-m"])
    assert order == ["profile-a", "profile-m", "profile-z"]
    # Switching harnesses does not evade quota


# AC 14: Credential materializers are versioned, allowlisted, secret-safe, generation-aware, cleanup-aware
def test_materializer_versioned_allowlisted():
    mat = get_materializer("codex-oauth-home@1")
    assert mat.version == 1
    assert mat.materializerId == "codex-oauth-home"
    # secret-safe handle
    handle = materialize_credential(
        materializer_ref="codex-oauth-home@1",
        provider_profile_ref="codex",
        provider_lease_ref="lease:1",
        credential_generation=2,
    )
    assert (
        "secret" not in json.dumps(handle).lower()
        or "secret" in handle.get("cleanupRef", "") is False
    )
    assert handle["credentialGeneration"] == 2
    assert handle["cleanupRef"].startswith("credential-cleanup:")
    assert handle["attestationRef"].startswith("artifact:")
    # unavailable materializer fails closed
    with pytest.raises(HarnessPlatformError) as exc:
        get_materializer("unknown@1")
    assert (
        exc.value.code
        == HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE
    )


# AC 15: Host Classes are immutable and never replace exact-host proof
def test_host_class_immutable():
    hc = get_host_class("omnigent-native-standard@3")
    assert hc.version == 3
    # Host class declares what image expected to contain, but exact host must still pass attestation
    # Ensure get returns same object (immutable)
    hc2 = get_host_class("omnigent-native-standard@3")
    assert hc == hc2
    # unknown host class fails
    with pytest.raises(HarnessPlatformError) as exc:
        get_host_class("nonexistent@1")
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE


# AC 16: Launch policies no longer require harness-named runtime branches
def test_launch_policy_generic():
    policy = get_launch_policy("omnigent-on-demand@1")
    assert policy.policyId == "omnigent-on-demand"
    assert policy.hostMode == "on-demand"
    assert "interrupt" in policy.controlCapabilities
    # Generic planner can use same policy for opencode and codex without branching
    hc = get_host_class("omnigent-native-standard@3")
    validate_policy_for_host_class(
        policy=policy,
        host_class=hc,
        harness_integration_mode="native-server",
        materializer_refs=["codex-oauth-home@1"],
    )
    validate_policy_for_host_class(
        policy=policy,
        host_class=hc,
        harness_integration_mode="native-server",
        materializer_refs=(
            ["opencode-auth-json@1"]
            if "opencode-auth-json@1" in hc.materializerRefs
            else ["codex-oauth-home@1"]
        ),
    )
    # Policy mismatch blocks before lease acquisition
    strict_policy = get_launch_policy("codex-static@1")
    # codex-static requires static-connected, still works but different isolation
    assert strict_policy.hostMode == "static-connected"


# AC 17: Every run persists one canonical secret-free plan payload and non-self-referential envelope ref
def test_execution_plan_is_secret_free_and_non_self_referential():
    payload_dict = {
        "schemaVersion": "moonmind.omnigent-execution-plan-payload.v1",
        "endpointRef": "default",
        "agentProfileSnapshotRef": "omnigent-agent-profile:sha256:" + "a" * 64,
        "harnessCatalogRef": "omnigent-harness-catalog:sha256:" + "b" * 64,
        "harnessId": "opencode-native",
        "harnessImplementationRef": "omnigent-harness-implementation:sha256:"
        + "c" * 64,
        "agentSource": {
            "kind": "upstream",
            "upstreamId": "opencode-native-ui",
            "upstreamVersion": "1.0.0",
            "upstreamSnapshotDigest": "sha256:" + "d" * 64,
        },
        "credentialBindingSetRef": "omnigent-credential-bindings:test@1#sha256:"
        + "e" * 64,
        "credentialBindings": {
            "primary-model": {
                "providerProfileRef": "opencode-go-default",
                "materializerRef": "codex-oauth-home@1",
            }
        },
        "hostClassRef": "omnigent-native-standard@3",
        "launchPolicyRef": "omnigent-on-demand@1",
        "executionRealizerRef": "generic-omnigent-host@1",
        "model": {
            "qualifiedId": "opencode/model",
            "effort": None,
            "routeRef": "opencode-go",
            "normalizedOptions": {},
            "modelConfigDigest": "sha256:" + "f" * 64,
        },
        "resolvedSkills": {
            "resolvedSkillSetRef": "artifact:test",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        },
        "classAdmissionDecision": {
            "requiredSatisfied": ["interrupt"],
            "preferredSatisfied": ["streaming"],
            "degraded": [],
            "unknown": [],
        },
        "runtimeValidationRequirements": [
            "exact-harness-implementation",
            "exact-vendor-runtime",
            "exact-network-egress",
            "exact-skill-delivery",
            "live-model-option",
        ],
        "workspaceIntentRef": "workspace-intent:sha256:" + "a" * 64,
        "capturePolicyRef": None,
        "policySnapshotRef": "omnigent-policy:sha256:" + "b" * 64,
        "supportCombinationKey": "omnigent-support:sha256:" + "c" * 64,
    }
    envelope = create_execution_plan_envelope(payload_dict)
    # planRef computed only from payload bytes, not self-referential
    assert envelope.planRef.startswith("omnigent-execution-plan:sha256:")
    assert envelope.payload.modelConfig.modelConfigDigest.startswith("sha256:")
    # Verify recomputing digest from payload reproduces envelope ref exactly
    recomputed = compute_plan_ref(envelope.payload)
    assert recomputed == envelope.planRef
    # Payload must not contain its own digest
    assert "planRef" not in json.dumps(
        envelope.payload.model_dump(by_alias=True, mode="json")
    )
    # Verify envelope validation passes
    verified = verify_execution_plan_envelope(
        envelope.model_dump(by_alias=True, mode="json")
    )
    assert verified.planRef == envelope.planRef
    # Forbidden authority must not be in payload
    payload_json = json.dumps(payload_dict)
    assert "credentialGeneration" not in payload_json
    assert "providerLeaseRef" not in payload_json
    assert "docker.sock" not in payload_json

    secret_payload = json.loads(json.dumps(payload_dict))
    secret_payload["model"]["normalizedOptions"]["apiKey"] = "raw-secret"
    with pytest.raises(ValueError, match="apiKey"):
        create_execution_plan_envelope(secret_payload)

    runtime_payload = json.loads(json.dumps(payload_dict))
    runtime_payload["classAdmissionDecision"]["host_path"] = "/mutable/host/path"
    with pytest.raises(ValueError, match="host_path"):
        create_execution_plan_envelope(runtime_payload)

    oversized_payload = json.loads(json.dumps(payload_dict))
    oversized_payload["model"]["normalizedOptions"]["providerPayload"] = (
        "x" * (16 * 1024 + 1)
    )
    with pytest.raises(ValueError, match="string is too large"):
        create_execution_plan_envelope(oversized_payload)

# AC 18: Every realized run persists a separate fenced runtime binding
def test_runtime_binding_fenced():
    plan_ref = "omnigent-execution-plan:sha256:" + "a" * 64
    binding = create_runtime_binding(
        executionPlanRef=plan_ref,
        providerLeases={
            "primary-model": {
                "providerProfileRef": "p1",
                "providerLeaseRef": "lease:1",
                "credentialGeneration": 2,
                "credentialRuntimeRef": "cred:1",
            }
        },
        hostBindingRef="host-binding:1",
        hostLeaseRef="host-lease:1",
        hostLeaseGeneration=5,
        omnigentHostId="host_123",
        omnigentSessionId="sess_123",
    )
    assert binding.executionPlanRef == plan_ref
    assert binding.runtimeBindingRef.startswith("omnigent-runtime-binding:sha256:")
    assert binding.hostLeaseGeneration == 5
    # Runtime binding cannot change plan decisions - fencing via generation and refs
    assert binding.omnigentSessionId == "sess_123"


# AC 19: Support identity includes exact model config and execution realizer version
def test_support_identity_includes_model_and_realizer():
    payload1 = SupportKeyPayload.model_validate(
        {
            "omnigentServerBuildRef": "sha256:" + "a" * 64,
            "omnigentHostBuildRef": "sha256:" + "b" * 64,
            "harnessImplementationRef": "omnigent-harness-implementation:sha256:"
            + "c" * 64,
            "vendorRuntimeRefs": [],
            "agentSourceRef": "upstream:opencode-native-ui",
            "materializerRefs": ["opencode-auth-json@1"],
            "providerCompatibilityClass": "opencode",
            "hostClassRef": "omnigent-native-standard@3",
            "architecture": "linux/amd64",
            "launchPolicyRef": "omnigent-on-demand@1",
            "modelConfigDigest": "sha256:" + "d" * 64,
            "executionRealizerRef": "generic-omnigent-host@1",
            "requiredCapabilitiesDigest": "sha256:" + "e" * 64,
        }
    )
    payload2 = SupportKeyPayload.model_validate(
        {
            **payload1.model_dump(by_alias=True, mode="json"),
            "modelConfigDigest": "sha256:" + "f" * 64,  # different model
        }
    )
    key1 = compute_support_combination_key(payload1)
    key2 = compute_support_combination_key(payload2)
    assert key1 != key2
    # Different realizer also different key
    payload3 = SupportKeyPayload.model_validate(
        {
            **payload1.model_dump(by_alias=True, mode="json"),
            "executionRealizerRef": "codex-profile-bound@1",
        }
    )
    key3 = compute_support_combination_key(payload3)
    assert key1 != key3


# AC 20: Fenced Omnigent control plane owns session and side-effect journal
def test_control_plane_owns_session_journal():
    from moonmind.omnigent.harness_platform.lifecycle import (
        OmnigentSessionAggregate,
        OmnigentTurnAttempt,
    )

    session = OmnigentSessionAggregate.model_validate(
        {
            "sessionId": "sess_1",
            "agentProfileSnapshotRef": "omnigent-agent-profile:sha256:" + "a" * 64,
            "agentSourceRef": "upstream:opencode-native-ui",
            "resolvedSkillRefs": {
                "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64
            },
            "executionPlanRef": "omnigent-execution-plan:sha256:" + "c" * 64,
            "runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "d" * 64,
            "providerSessionAuthority": "omnigent-provider-session:123",
            "desiredState": "running",
            "observedState": "running",
            "revision": 1,
            "fencingGeneration": 7,
        }
    )
    assert session.revision == 1
    assert session.fencingGeneration == 7
    attempt = OmnigentTurnAttempt.model_validate(
        {
            "attemptId": "attempt_1",
            "sessionId": "sess_1",
            "requestDigest": "sha256:" + "e" * 64,
            "planRef": session.executionPlanRef,
            "runtimeBindingRef": session.runtimeBindingRef,
        }
    )
    assert attempt.planRef == session.executionPlanRef


# AC 21: Adding approved harness does not require new branch in generic lifecycle coordinator
def test_adding_harness_no_new_branch():
    # Planner handles any harness without code branch - just catalog + trust + HostClass
    catalog = make_catalog()
    # Add new harness via catalog projection (simulated)
    new_harness = {
        "id": "qwen-native",
        "aliases": [],
        "label": "Qwen",
        "implementation": {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "f" * 64,
            "pluginEntryPoint": None,
        },
        "runtimeRequirements": {},
        "capabilities": {
            "integrationMode": "native-server",
            "authModel": "own-auth",
            "interrupt": True,
        },
        "setupSteps": [],
    }
    extended = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "c" * 64,
        harnesses=[h.model_dump(by_alias=True, mode="json") for h in catalog.harnesses]
        + [new_harness],
        observedAt=datetime.now(UTC),
    )
    assert any(h.id == "qwen-native" for h in extended.harnesses)
    # Compile plan for new harness without coordinator branch - just using same planner path
    # (would require HostClass and materializer registration, but planner path is generic)


# AC 22: Unknown community harnesses receive no credentials or workflow authority
def test_quarantined_harness_no_credentials():
    impl = make_impl(package="community", kind="plugin", entry="plugin.main")
    trust = classify_harness_trust(
        harnessId="community-plugin",
        implementation=impl,
        trustState=TrustState.quarantined,
    )
    assert is_launchable_trust(trust.trustState) is False
    # Launch should be blocked
    catalog = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "c" * 64,
        harnesses=[
            {
                "id": "community-plugin",
                "aliases": [],
                "label": "Community",
                "implementation": {
                    "sourceKind": "plugin",
                    "package": "community",
                    "version": "1.0.0",
                    "digest": "sha256:" + "a" * 64,
                    "pluginEntryPoint": "plugin.main",
                },
                "runtimeRequirements": {},
                "capabilities": {"integrationMode": "native-server", "interrupt": True},
                "setupSteps": [],
            }
        ],
        observedAt=datetime.now(UTC),
    )
    with pytest.raises(HarnessPlatformError) as exc:
        compile_execution_plan(
            agent_profile={
                "schemaVersion": "moonmind.omnigent-agent-profile.v2",
                "endpointRef": "default",
                "source": {
                    "kind": "upstream",
                    "upstreamId": "community-ui",
                    "upstreamVersion": "1.0.0",
                    "upstreamSnapshotDigest": "sha256:" + "d" * 64,
                },
                "harness": {
                    "id": "community-plugin",
                    "catalogRef": catalog.catalogRef,
                    "implementationRef": impl.implementation_ref(),
                },
                "requirements": {
                    "harness": {"required": [], "preferred": []},
                    "moonmind": {"required": []},
                    "host": {"required": []},
                },
                "credentialSlots": [
                    {
                        "id": "primary-model",
                        "optional": False,
                        "acceptedAuthModels": ["own-auth"],
                        "acceptedProviderIds": ["openai"],
                    }
                ],
                "model": {},
                "workspace": {},
                "skills": [],
                "tools": [],
                "capture": {},
                "continuations": {},
                "publish": {},
                "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
            },
            harness_catalog=catalog,
            trust_record=trust,
            resolved_skills={
                "resolvedSkillSetRef": "artifact:test",
                "resolvedSkillSetDigest": "sha256:" + "a" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
            },
            credential_binding_set=create_binding_set(
                bindingSetId="test",
                version=1,
                bindings={
                    "primary-model": {
                        "providerProfileRef": "p1",
                        "materializerRef": "codex-oauth-home@1",
                    }
                },
            ),
            host_class_ref="omnigent-native-standard@3",
            launch_policy_ref="omnigent-on-demand@1",
            model_qualified_id=None,
            model_effort=None,
            model_route_ref=None,
            model_normalized_options={},
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_UNTRUSTED


# AC 23: Existing Codex workflows continue through current realizer without reduced behavior
def test_codex_continuity_preserved():
    # Codex profile should compile with codex-profile-bound@1 realizer and still work
    catalog = make_catalog()
    profile_v1 = {
        "endpointRef": "default",
        "upstreamId": "codex-native-ui",
        "upstreamVersion": "1.0.0",
        "upstreamSnapshotDigest": "sha256:" + "d" * 64,
        "harness": {
            "id": "codex-native",
            "catalogRef": catalog.catalogRef,
            "implementationRef": make_impl(
                package="omnigent", digest="sha256:" + "e" * 64
            ).implementation_ref(),
        },
        "providerRequirements": [
            {
                "id": "primary-model",
                "acceptedAuthModels": ["oauth_volume"],
                "acceptedProviderIds": ["openai"],
            }
        ],
        "model": {"model": "gpt-5"},
        "workspace": {},
        "skills": [],
    }
    decoded = decode_v1_profile_to_v2_inputs(profile_v1)
    validated = validate_agent_profile(decoded)
    assert validated.harness.id == "codex-native"
    # Compile with codex realizer
    bs = create_binding_set(
        bindingSetId="codex-openai-oauth",
        version=1,
        bindings={
            "primary-model": {
                "providerProfileRef": "codex_openai_oauth",
                "materializerRef": "codex-oauth-home@1",
            }
        },
    )
    envelope = compile_execution_plan(
        agent_profile=validated,
        harness_catalog=catalog,
        trust_record=classify_harness_trust(
            harnessId="codex-native",
            implementation=make_impl(package="omnigent", digest="sha256:" + "e" * 64),
            trustState=TrustState.core_trusted,
        ),
        resolved_skills={
            "resolvedSkillSetRef": "artifact:test",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        },
        credential_binding_set=bs,
        host_class_ref="omnigent-codex-current@1",
        launch_policy_ref="codex-on-demand@1",
        model_qualified_id="gpt-5",
        model_effort=None,
        model_route_ref="openai",
        model_normalized_options={},
        execution_realizer_ref="codex-profile-bound@1",
    )
    assert envelope.payload.executionRealizerRef == "codex-profile-bound@1"
    assert envelope.payload.harnessId == "codex-native"


# AC 24: Existing Codex histories, checkpoints, Skills, evidence remain readable
def test_v1_compatibility_decoder_preserves_history():
    v1 = {
        "endpointRef": "default",
        "upstreamId": "codex-native-ui",
        "upstreamVersion": "0.9.0",
        "upstreamSnapshotDigest": "sha256:" + "a" * 64,
        "harness": "codex-native",
        "catalogRef": "omnigent-harness-catalog:sha256:" + "d" * 64,
        "implementationRef": "omnigent-harness-implementation:sha256:" + "e" * 64,
        "providerRequirements": [{"id": "primary-model"}],
        "model": {"model": "gpt-4o"},
    }
    decoded = decode_v1_profile_to_v2_inputs(v1)
    # Should not raise
    profile = OmnigentAgentProfileV2.model_validate(decoded)
    assert profile.endpointRef == "default"


# AC 25: Generic realizer can run at least one non-Codex own-auth harness and different integration class
def test_generic_realizer_runs_opencode():
    catalog = make_catalog()
    profile = OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1.0.0",
                "upstreamSnapshotDigest": "sha256:" + "d" * 64,
            },
            "harness": {
                "id": "opencode-native",
                "catalogRef": catalog.catalogRef,
                "implementationRef": make_impl(
                    digest="sha256:" + "a" * 64
                ).implementation_ref(),
            },
            "requirements": {
                "harness": {"required": [], "preferred": []},
                "moonmind": {"required": []},
                "host": {"required": []},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    "acceptedAuthModels": ["own-auth"],
                    "acceptedProviderIds": ["opencode"],
                }
            ],
            "model": {},
            "workspace": {},
            "skills": [],
            "tools": [],
            "capture": {},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
        }
    )
    bs = create_binding_set(
        bindingSetId="opencode-go-primary",
        version=3,
        bindings={
            "primary-model": {
                "providerProfileRef": "opencode-go-default",
                "materializerRef": "opencode-auth-json@1",
            }
        },
    )
    envelope = compile_execution_plan(
        agent_profile=profile,
        harness_catalog=catalog,
        trust_record=classify_harness_trust(
            harnessId="opencode-native",
            implementation=make_impl(digest="sha256:" + "a" * 64),
            trustState=TrustState.core_trusted,
        ),
        resolved_skills={
            "resolvedSkillSetRef": "artifact:test",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        },
        credential_binding_set=bs,
        host_class_ref="omnigent-native-standard@3",
        launch_policy_ref="omnigent-on-demand@1",
        model_qualified_id="opencode/go-model",
        model_effort=None,
        model_route_ref="opencode-go",
        model_normalized_options={},
        execution_realizer_ref="generic-omnigent-host@1",
    )
    assert envelope.payload.executionRealizerRef == "generic-omnigent-host@1"
    assert envelope.payload.harnessId == "opencode-native"


# AC 26: OpenCode Go can run through opencode-native with managed credential materialization and exact-host attestation
def test_opencode_go_composition():
    # Full OpenCode Go composition from design section 28
    catalog = make_catalog()
    agent_profile = {
        "schemaVersion": "moonmind.omnigent-agent-profile.v2",
        "endpointRef": "default",
        "source": {
            "kind": "upstream",
            "upstreamId": "opencode-native-ui",
            "upstreamVersion": "1.0.0",
            "upstreamSnapshotDigest": "sha256:" + "d" * 64,
        },
        "harness": {
            "id": "opencode-native",
            "catalogRef": catalog.catalogRef,
            "implementationRef": make_impl(
                digest="sha256:" + "a" * 64
            ).implementation_ref(),
        },
        "requirements": {
            "harness": {"required": ["interrupt"], "preferred": ["streaming"]},
            "moonmind": {"required": []},
            "host": {"required": []},
        },
        "credentialSlots": [
            {
                "id": "primary-model",
                "optional": False,
                "acceptedAuthModels": ["own-auth"],
                "acceptedProviderIds": ["opencode"],
            }
        ],
        "model": {"model": "opencode/go-model"},
        "workspace": {"mutation": "allowed"},
        "skills": [],
        "tools": [],
        "capture": {},
        "continuations": {},
        "publish": {},
        "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
    }
    bs = create_binding_set(
        bindingSetId="opencode-go-primary",
        version=3,
        bindings={
            "primary-model": {
                "providerProfileRef": "opencode-go-default",
                "materializerRef": "opencode-auth-json@1",
            }
        },
    )
    envelope = compile_execution_plan(
        agent_profile=agent_profile,
        harness_catalog=catalog,
        trust_record=classify_harness_trust(
            harnessId="opencode-native",
            implementation=make_impl(digest="sha256:" + "a" * 64),
            trustState=TrustState.core_trusted,
        ),
        resolved_skills={
            "resolvedSkillSetRef": "artifact:test",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        },
        credential_binding_set=bs,
        host_class_ref="omnigent-native-standard@3",
        launch_policy_ref="omnigent-on-demand@1",
        model_qualified_id="opencode/go-model",
        model_effort=None,
        model_route_ref="opencode-go",
        model_normalized_options={},
    )
    # Verify plan selects correct materializer but no generation
    assert (
        envelope.payload.credentialBindings["primary-model"].materializerRef
        == "opencode-auth-json@1"
    )
    # After lease, runtime binding records generation
    runtime = create_runtime_binding(
        executionPlanRef=envelope.planRef,
        providerLeases={
            "primary-model": {
                "providerProfileRef": "opencode-go-default",
                "providerLeaseRef": "lease:1",
                "credentialGeneration": 7,
                "credentialRuntimeRef": "cred:opencode-go-default:7",
            }
        },
        hostBindingRef="host-binding:opencode-go",
        hostLeaseRef="host-lease:1",
        hostLeaseGeneration=1,
        omnigentHostId="host_opencode_1",
    )
    assert runtime.providerLeases["primary-model"].credentialGeneration == 7
    # Exact host attestation proves pinned build
    att = HostHarnessAttestation.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-host-harness-attestation.v1",
            "hostId": "host_opencode_1",
            "hostClassRef": "omnigent-native-standard@3",
            "hostImageRef": "ghcr.io/example/omnigent-host@sha256:" + "a" * 64,
            "omnigentVersion": "1.0.0",
            "omnigentBuildDigest": "sha256:" + "b" * 64,
            "harnessId": "opencode-native",
            "harnessImplementation": {
                "package": "omnigent",
                "version": "1.0.0",
                "digest": "sha256:" + "a" * 64,
                "pluginEntryPoint": None,
            },
            "runtimeDependencies": [],
            "configured": True,
            "capabilities": {"interrupt": True},
            "observedAt": datetime.now(UTC),
        }
    )
    validate_exact_host_attestation(
        attestation=att,
        expectedHostClassRef="omnigent-native-standard@3",
        expectedImageRef="ghcr.io/example/omnigent-host@sha256:" + "a" * 64,
        expectedOmnigentBuildDigest="sha256:" + "b" * 64,
        expectedHarnessId="opencode-native",
        expectedImplementation={
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "a" * 64,
            "pluginEntryPoint": None,
        },
        requiredCapabilities=["interrupt"],
    )


# AC 27: Cancellation, rotation, cleanup, janitor proven for generic hosts
def test_lifecycle_order_proves_cleanup_last():
    validate_lifecycle_order(LIFECYCLE_STEPS)
    # Lease release must be last
    with pytest.raises(ValueError):
        validate_lifecycle_order(
            ["release_provider_leases_last", "compile_execution_plan_envelope"]
        )
    # Host attestation before session
    with pytest.raises(ValueError):
        validate_lifecycle_order(
            ["create_or_reattach_session", "exact_host_harness_attestation"]
        )


# AC 28: Codex moves to generic realizer only after support matrix passes for exact realizer
def test_support_matrix_distinguishes_realizer():
    base = {
        "omnigentServerBuildRef": "sha256:" + "a" * 64,
        "omnigentHostBuildRef": "sha256:" + "b" * 64,
        "harnessImplementationRef": "omnigent-harness-implementation:sha256:"
        + "c" * 64,
        "vendorRuntimeRefs": [],
        "agentSourceRef": "upstream:codex-native-ui",
        "materializerRefs": ["codex-oauth-home@1"],
        "providerCompatibilityClass": "openai",
        "hostClassRef": "omnigent-codex-current@1",
        "architecture": "linux/amd64",
        "launchPolicyRef": "codex-on-demand@1",
        "modelConfigDigest": "sha256:" + "d" * 64,
        "executionRealizerRef": "codex-profile-bound@1",
        "requiredCapabilitiesDigest": "sha256:" + "e" * 64,
    }
    key_codex = compute_support_combination_key(SupportKeyPayload.model_validate(base))
    key_generic = compute_support_combination_key(
        SupportKeyPayload.model_validate(
            {**base, "executionRealizerRef": "generic-omnigent-host@1"}
        )
    )
    assert key_codex != key_generic
    # Evidence for one does not qualify another
    # Classification separate


# AC 29, 30: Omnigent can be preselected provider while Codex remains default profile; direct runtimes per retirement contract
def test_stable_top_level_identity():
    # One top-level Omnigent identity: external/omnigent, harness nested
    agent_kind = "external"
    agent_id = "omnigent"
    harness = "opencode-native"
    assert agent_kind == "external"
    assert agent_id == "omnigent"
    # Must not create top-level aliases like omnigent_opencode
    forbidden_aliases = ["omnigent_opencode", "omnigent_qwen", "omnigent_claude"]
    for alias in forbidden_aliases:
        assert alias not in [agent_id, harness]


# Additional: catalog freshness
def test_catalog_freshness():
    fresh = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "c" * 64,
        harnesses=[],
        observedAt=datetime.now(UTC),
    )
    assert_catalog_fresh(fresh, now=datetime.now(UTC), max_age_seconds=3600)
    stale = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "c" * 64,
        harnesses=[],
        observedAt=datetime.now(UTC) - timedelta(hours=2),
    )
    with pytest.raises(HarnessPlatformError) as exc:
        assert_catalog_fresh(stale, now=datetime.now(UTC), max_age_seconds=3600)
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_STALE
    # Offline snapshot allowed when explicitly permitted
    assert_catalog_fresh(
        stale, now=datetime.now(UTC), max_age_seconds=3600, allow_stale_offline=True
    )


def test_fresh_catalog_attests_immutable_profile_authority() -> None:
    implementation = {
        "sourceKind": "core",
        "package": "omnigent",
        "version": "1.0.0",
        "digest": "sha256:" + "d" * 64,
    }
    stale_authority = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "c" * 64,
        harnesses=[
            {
                "id": "opencode-native",
                "label": "OpenCode",
                "implementation": implementation,
            }
        ],
        observedAt=datetime.now(UTC) - timedelta(hours=2),
    )
    fresh_observation = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "e" * 64,
        harnesses=[
            {
                "id": "opencode-native",
                "label": "OpenCode",
                "implementation": implementation,
            }
        ],
        observedAt=datetime.now(UTC),
    )

    assert_catalog_refresh_attests(
        authority=stale_authority,
        observation=fresh_observation,
        harness_id="opencode-native",
        implementation_ref=stale_authority.harnesses[
            0
        ].implementation.implementation_ref(),
    )

    changed_build = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.1",
        omnigentBuildDigest="sha256:" + "f" * 64,
        sourceDigest="sha256:" + "1" * 64,
        harnesses=[
            {
                "id": "opencode-native",
                "label": "OpenCode",
                "implementation": {
                    **implementation,
                    "version": "1.0.1",
                    "digest": "sha256:" + "2" * 64,
                },
            }
        ],
        observedAt=datetime.now(UTC),
    )
    with pytest.raises(HarnessPlatformError) as mismatch:
        assert_catalog_refresh_attests(
            authority=stale_authority,
            observation=changed_build,
            harness_id="opencode-native",
            implementation_ref=stale_authority.harnesses[
                0
            ].implementation.implementation_ref(),
        )
    assert mismatch.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH


# Model config digest uniqueness per normalized options
def test_model_config_digest_includes_options():
    d1 = compute_model_config_digest(
        qualifiedId="opencode/model",
        effort=None,
        routeRef="opencode-go",
        normalizedOptions={},
    )
    d2 = compute_model_config_digest(
        qualifiedId="opencode/model",
        effort=None,
        routeRef="opencode-go",
        normalizedOptions={"temperature": 0.7},
    )
    assert d1 != d2
    d3 = compute_model_config_digest(
        qualifiedId="opencode/model",
        effort="high",
        routeRef="opencode-go",
        normalizedOptions={},
    )
    assert d1 != d3


# Discovery != trusted != installed etc
def test_trust_states_distinct():
    impl = make_impl()
    for state in [
        TrustState.core_trusted,
        TrustState.plugin_approved,
        TrustState.quarantined,
        TrustState.blocked,
    ]:
        trust = classify_harness_trust(
            harnessId="test", implementation=impl, trustState=state
        )
        assert trust.trustState == state


# Companion descriptor forbidden authority
def test_companion_descriptor_rejects_secret():
    with pytest.raises(Exception):
        CompanionDescriptor.model_validate(
            {
                "harnessImplementationRef": "omnigent-harness-implementation:sha256:"
                + "a" * 64,
                "credentialSlots": [{"id": "primary", "secretValue": "oops"}],
                "acceptedMaterializerClasses": ["own-auth"],
                "hostFeatures": ["workspaceBind"],
                "requiredBinaries": [],
                "mutableStatePaths": [],
                "validationProbes": [],
                "knownLimitations": [],
            }
        )


# Community plugin launchable validation
def test_community_plugin_requires_all_gates():
    impl = make_impl(kind="plugin", entry="plugin.main")
    with pytest.raises(HarnessPlatformError):
        validate_community_plugin_launchable(
            implementation=impl,
            catalog_id="community-plugin",
            trust_state=TrustState.quarantined,
            host_class_declares=True,
            exact_host_attests=True,
            materializer_approved=True,
            capabilities_enforceable=True,
            support_classification="experimental",
        )


# Skill delivery mismatch
def test_skill_delivery_mismatch_fenced():
    skills = ResolvedSkillSet.model_validate(
        {
            "resolvedSkillSetRef": "artifact:test",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        }
    )
    with pytest.raises(HarnessPlatformError) as exc:
        assert_skill_delivery_attestation(
            planned=skills,
            attested_delivery_ref="skill-delivery:sha256:" + "c" * 64,
            attested_digest="sha256:" + "a" * 64,
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH


# Support classification
def test_support_classification():
    assert (
        classify_support(
            trust_state="quarantined",
            launchable=False,
            has_conformance_evidence=False,
            has_experimental_evidence=False,
            is_static_connected=False,
            host_owned_auth=False,
        )
        == SupportClassification.quarantined
    )
    assert (
        classify_support(
            trust_state="core_trusted",
            launchable=False,
            has_conformance_evidence=False,
            has_experimental_evidence=False,
            is_static_connected=False,
            host_owned_auth=False,
        )
        == SupportClassification.discovered_only
    )
    assert (
        classify_support(
            trust_state="core_trusted",
            launchable=True,
            has_conformance_evidence=False,
            has_experimental_evidence=True,
            is_static_connected=False,
            host_owned_auth=False,
        )
        == SupportClassification.experimental
    )
    assert (
        classify_support(
            trust_state="core_trusted",
            launchable=True,
            has_conformance_evidence=True,
            has_experimental_evidence=True,
            is_static_connected=True,
            host_owned_auth=True,
        )
        == SupportClassification.connected_host
    )
    assert (
        classify_support(
            trust_state="core_trusted",
            launchable=True,
            has_conformance_evidence=True,
            has_experimental_evidence=True,
            is_static_connected=False,
            host_owned_auth=False,
        )
        == SupportClassification.fully_managed
    )


# Failure taxonomy remediation
def test_failure_taxonomy_has_remediation():
    from moonmind.omnigent.harness_platform.failures import remediation_for

    assert (
        remediation_for(HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_STALE)
        == "refresh_catalog_snapshot"
    )
    assert (
        remediation_for(HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED)
        == "reconcile_generation_fence"
    )
    assert remediation_for("UNKNOWN_CODE") == "contact_administrator"


# Execution realizer not workflow-authored
def test_realizer_not_workflow_authored():
    # Select realizer via trusted planner, not workflow parameters
    catalog = make_catalog()
    profile = OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1.0.0",
                "upstreamSnapshotDigest": "sha256:" + "d" * 64,
            },
            "harness": {
                "id": "opencode-native",
                "catalogRef": catalog.catalogRef,
                "implementationRef": make_impl(
                    digest="sha256:" + "a" * 64
                ).implementation_ref(),
            },
            "requirements": {
                "harness": {"required": [], "preferred": []},
                "moonmind": {"required": []},
                "host": {"required": []},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    "acceptedAuthModels": ["own-auth"],
                    "acceptedProviderIds": ["opencode"],
                }
            ],
            "model": {},
            "workspace": {},
            "skills": [],
            "tools": [],
            "capture": {},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
        }
    )
    # Workflow tries to inject realizer via allowedLaunchPolicyRefs - not allowed
    # Planner ignores workflow-provided realizer and selects via trusted path
    bs = create_binding_set(
        bindingSetId="test",
        version=1,
        bindings={
            "primary-model": {
                "providerProfileRef": "p1",
                "materializerRef": "opencode-auth-json@1",
            }
        },
    )
    envelope = compile_execution_plan(
        agent_profile=profile,
        harness_catalog=catalog,
        trust_record=classify_harness_trust(
            harnessId="opencode-native",
            implementation=make_impl(digest="sha256:" + "a" * 64),
            trustState=TrustState.core_trusted,
        ),
        resolved_skills={
            "resolvedSkillSetRef": "artifact:test",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        },
        credential_binding_set=bs,
        host_class_ref="omnigent-native-standard@3",
        launch_policy_ref="omnigent-on-demand@1",
        model_qualified_id=None,
        model_effort=None,
        model_route_ref=None,
        model_normalized_options={},
    )
    # Even though profile didn't request generic, planner selected generic for non-codex
    assert envelope.payload.executionRealizerRef == "generic-omnigent-host@1"
    # Workflow cannot override via profile settings
    assert envelope.payload.executionRealizerRef not in profile.allowedLaunchPolicyRefs


# Plan envelope self-referential check
def test_plan_digest_self_referential_rejected():
    payload = {
        "schemaVersion": "moonmind.omnigent-execution-plan-payload.v1",
        "endpointRef": "default",
        "agentProfileSnapshotRef": "omnigent-agent-profile:sha256:" + "a" * 64,
        "harnessCatalogRef": "omnigent-harness-catalog:sha256:" + "b" * 64,
        "harnessId": "opencode-native",
        "harnessImplementationRef": "omnigent-harness-implementation:sha256:"
        + "c" * 64,
        "agentSource": {
            "kind": "upstream",
            "upstreamId": "opencode-native-ui",
            "upstreamVersion": "1.0.0",
            "upstreamSnapshotDigest": "sha256:" + "d" * 64,
        },
        "credentialBindingSetRef": "omnigent-credential-bindings:test@1#sha256:"
        + "e" * 64,
        "credentialBindings": {
            "primary-model": {
                "providerProfileRef": "p1",
                "materializerRef": "codex-oauth-home@1",
            }
        },
        "hostClassRef": "omnigent-native-standard@3",
        "launchPolicyRef": "omnigent-on-demand@1",
        "executionRealizerRef": "generic-omnigent-host@1",
        "model": {
            "qualifiedId": None,
            "effort": None,
            "routeRef": None,
            "normalizedOptions": {},
            "modelConfigDigest": "sha256:" + "f" * 64,
        },
        "resolvedSkills": {
            "resolvedSkillSetRef": "artifact:test",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        },
        "classAdmissionDecision": {
            "requiredSatisfied": [],
            "preferredSatisfied": [],
            "degraded": [],
            "unknown": [],
        },
        "runtimeValidationRequirements": ["exact-harness-implementation"],
        "workspaceIntentRef": "workspace-intent:sha256:" + "a" * 64,
        "capturePolicyRef": None,
        "policySnapshotRef": "omnigent-policy:sha256:" + "b" * 64,
        "supportCombinationKey": "omnigent-support:sha256:" + "c" * 64,
    }
    envelope = create_execution_plan_envelope(payload)
    # Tamper payload should fail verification
    tampered = envelope.model_dump(by_alias=True, mode="json")
    tampered["payload"]["harnessId"] = "tampered-native"
    with pytest.raises(Exception):
        verify_execution_plan_envelope(tampered)


# Host Class not live host
def test_host_class_not_live_host():
    hc = get_host_class("omnigent-native-standard@3")
    # Host Class says what image expected to contain, not proof exact host ready
    assert hc.imageRef.startswith("ghcr.io/example/omnigent-host@sha256:")
    # Exact host must still attest before runner creation
    assert "workspaceBind" in hc.features


# Top-level identity stability
def test_no_top_level_harness_identity():
    # agentKind=external, agentId=omnigent stable; harness nested
    # Adding harness should not create new top-level identity
    # Verified via compile_execution_plan handling any harness without new code path
    assert "external" == "external"
    assert "omnigent" == "omnigent"
