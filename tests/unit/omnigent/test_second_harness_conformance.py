"""Phase 8: Prove architecture with a second harness (Pi).

Acceptance: adding pi-native requires only:
  catalog trust, Host Class or runtime pack, materializer descriptor,
  Agent Profile, support evidence
and NO change to:
  Temporal activity dispatch, generic host realizer, generic session driver

This test proves that planner, realizer registry, and host runtime are
harness-neutral – they handle Pi via data, not branches.
"""

import os
from datetime import UTC, datetime

import pytest

from moonmind.omnigent.harness_platform.agent_profile import OmnigentAgentProfileV2
from moonmind.omnigent.harness_platform.catalog import create_catalog_snapshot, HarnessImplementationIdentity, TrustState, classify_harness_trust
from moonmind.omnigent.harness_platform.credential_bindings import create_binding_set
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
from moonmind.omnigent.harness_platform.support import SupportKeyPayload, compute_support_combination_key
from moonmind.omnigent.harness_platform.materializers import get_materializer
from moonmind.omnigent.harness_platform.host_classes import get_host_class, get_launch_policy, get_opencode_host_image_ref
from moonmind.omnigent.realizers.registry import get_default_registry, reset_default_registry


def _make_pi_impl():
    return HarnessImplementationIdentity.model_validate(
        {"sourceKind": "core", "package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "c" * 64, "pluginEntryPoint": None}
    )


def _make_catalog(harness_id: str, digest: str):
    return create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "c" * 64,
        harnesses=[
            {
                "id": harness_id,
                "aliases": [],
                "label": harness_id,
                "implementation": {"sourceKind": "core", "package": "omnigent", "version": "1.0.0", "digest": digest, "pluginEntryPoint": None},
                "runtimeRequirements": {},
                "capabilities": {"integrationMode": "native-server", "authModel": "omnigent-provider-config", "interrupt": True, "streaming": True},
                "setupSteps": [],
            }
        ],
        observedAt=datetime.now(UTC),
    )


def test_pi_harness_via_generic_host_no_realizer_change():
    """Pi through omnigent-provider-config uses generic-omnigent-host@1 without code change."""
    harness_id = "pi-native"
    impl = _make_pi_impl()
    catalog = _make_catalog(harness_id, "sha256:" + "c" * 64)
    trust = classify_harness_trust(harnessId=harness_id, implementation=impl, trustState=TrustState.core_trusted)

    profile = OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {"kind": "upstream", "upstreamId": "pi-native-ui", "upstreamVersion": "1.0.0", "upstreamSnapshotDigest": "sha256:" + "d" * 64},
            "harness": {"id": harness_id, "catalogRef": catalog.catalogRef, "implementationRef": impl.implementation_ref()},
            "requirements": {"harness": {"required": ["interrupt"]}, "moonmind": {"required": []}, "host": {"required": []}},
            "credentialSlots": [{"id": "primary-model", "optional": False, "acceptedAuthModels": ["omnigent-provider-config"], "acceptedProviderIds": ["pi"]}],
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
    skills = ResolvedSkillSet.model_validate({"resolvedSkillSetRef": "artifact:test-pi", "resolvedSkillSetDigest": "sha256:" + "a" * 64, "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64})
    bs = create_binding_set(bindingSetId="pi-primary", version=1, bindings={"primary-model": {"providerProfileRef": "pi-profile", "materializerRef": "omnigent-provider-config@1"}})

    # Host Class: dedicated pi host (not placeholder standard) – requires real digest
    os.environ["OMNIGENT_PI_HOST_IMAGE_REF"] = "ghcr.io/moonladderstudios/omnigent-host-pi@sha256:" + "b" * 64
    hc = get_host_class("omnigent-pi@1")
    assert hc.declares_harness(harness_id, impl.implementation_ref())
    assert hc.supports_materializer("omnigent-provider-config@1")

    policy = get_launch_policy("omnigent-on-demand@1")
    # Compile via same planner path as opencode – no harness branch
    envelope = compile_execution_plan(
        agent_profile=profile,
        harness_catalog=catalog,
        trust_record=trust,
        resolved_skills=skills,
        credential_binding_set=bs,
        host_class_ref=hc.ref,
        launch_policy_ref=policy.ref,
        model_qualified_id="pi/model",
        model_effort=None,
        model_route_ref="pi",
        model_normalized_options={},
    )
    # Must select generic host realizer, not codex
    assert envelope.payload.executionRealizerRef == "generic-omnigent-host@1"
    assert envelope.payload.harnessId == harness_id

    # Verify realizer registry dispatches without harness branch
    registry = get_default_registry()
    realizer = registry.require(envelope.payload.executionRealizerRef)
    assert realizer.ref == "generic-omnigent-host@1"
    # The realizer's execute must be harness-neutral (no if harness == "pi")
    import inspect

    source = inspect.getsource(realizer.execute)
    # Must not contain per-harness branches for pi
    assert 'if harness == "pi' not in source
    assert 'elif harness == "opencode' not in source
    assert 'elif harness == "qwen' not in source


def test_host_owned_auth_materializer_for_static_connected():
    """Connected host with host-owned-auth uses static-connected + no secret."""
    mat = get_materializer("host-owned-auth@1")
    assert mat.supports_host_mode("static-connected")
    assert not mat.supports_host_mode("on-demand") or mat.supports_host_mode("on-demand") is False
    # host-owned-auth requires no secret roles
    assert mat.requiredSecretRoles == ()
    assert mat.target["kind"] == "host-owned-auth"

    # Validate that a static host can use it with pi-native
    impl_ref = _make_pi_impl().implementation_ref()
    from moonmind.omnigent.harness_platform.materializers import validate_binding_materializer

    validated = validate_binding_materializer(
        materializer_ref="host-owned-auth@1",
        harness_implementation_ref=impl_ref,
        host_mode="static-connected",
    )
    assert validated.materializerId == "host-owned-auth"

    # on-demand must fail for host-owned-auth
    with pytest.raises(Exception):
        validate_binding_materializer(
            materializer_ref="host-owned-auth@1",
            harness_implementation_ref=impl_ref,
            host_mode="on-demand",
        )


def test_second_harness_does_not_require_realizer_code_change():
    """Prove that adding Pi required only data, not code branches."""
    # Simulate adding a new harness without modifying realizer code:
    # Catalog + HostClass + materializer + Agent Profile are data.
    # The generic realizer and planner handle it via the same code path
    # as opencode-native.

    # Before: registry has generic-omnigent-host@1
    registry = get_default_registry()
    assert "generic-omnigent-host@1" in registry.list_refs()
    assert "codex-profile-bound@1" in registry.list_refs()

    # Adding pi does not add a new realizer method
    # The count of realizers remains 2
    assert len(registry.list_refs()) == 2

    # Planner without harness branches: check source for harness-specific if
    import inspect
    from moonmind.omnigent.harness_platform import planner as planner_module

    planner_source = inspect.getsource(planner_module.compile_execution_plan)
    assert 'harness == "pi' not in planner_source
    assert 'harness == "opencode' not in planner_source

    from moonmind.omnigent.realizers import generic_host as gh_module

    realizer_source = inspect.getsource(gh_module.GenericOmnigentHostRealizer.execute)
    assert 'harness == "pi' not in realizer_source
    assert 'harness == "opencode' not in realizer_source

    # Host runtime also harness-neutral
    from moonmind.omnigent import host_runtime as hr_module

    hr_source = inspect.getsource(hr_module.GenericOmnigentHostRuntime.realize)
    assert 'harness == "pi' not in hr_source


def test_pi_and_opencode_share_same_generic_session_driver():
    """Both harnesses use same OmnigentHttpClient session driver."""
    from pathlib import Path

    client_path = Path("moonmind/workflows/adapters/omnigent_client.py")
    content = client_path.read_text(encoding="utf-8")
    # Client has generic operations, not harness-specific methods
    assert "def create_session" in content
    assert "def post_event" in content
    assert "def stream_events" in content
    assert "def list_harnesses" in content
    assert "def get_host" in content
    assert "def get_host_model_options" in content

    # Must not have harness-specific methods
    assert "run_opencode_execution" not in content
    assert "run_qwen_execution" not in content
    assert "run_pi_execution" not in content

    # Verify session-create payload is harness-neutral
    lower = content.lower()
    # No harness-specific branching in generic driver
    assert 'harness == "opencode' not in lower
    assert 'harness == "qwen' not in lower
