"""Phase 1: Plan persistence and realizer dispatch."""

import os
import pytest
from datetime import UTC, datetime

from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.catalog import create_catalog_snapshot, classify_harness_trust, HarnessImplementationIdentity, TrustState
from moonmind.omnigent.harness_platform.agent_profile import OmnigentAgentProfileV2
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
from moonmind.omnigent.harness_platform.credential_bindings import create_binding_set
from moonmind.omnigent.harness_platform.stores import InMemoryExecutionPlanStore
from moonmind.omnigent.realizers.registry import OmnigentExecutionRealizerRegistry, get_default_registry, reset_default_registry
from moonmind.omnigent.realizers.codex_profile_bound import CodexProfileBoundRealizer
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer


def _make_catalog(harness_id="opencode-native", digest="sha256:" + "a" * 64):
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
                "capabilities": {"integrationMode": "native-server", "authModel": "own-auth", "interrupt": True, "streaming": True},
                "setupSteps": [],
            }
        ],
        observedAt=datetime.now(UTC),
    )


def test_plan_persisted_and_retries_load_same_plan():
    import asyncio

    async def run():
        catalog = _make_catalog()
        impl = HarnessImplementationIdentity.model_validate({"sourceKind": "core", "package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "a" * 64, "pluginEntryPoint": None})
        trust = classify_harness_trust(harnessId="opencode-native", implementation=impl, trustState=TrustState.core_trusted)
        profile = OmnigentAgentProfileV2.model_validate(
            {
                "schemaVersion": "moonmind.omnigent-agent-profile.v2",
                "endpointRef": "default",
                "source": {"kind": "upstream", "upstreamId": "opencode-native-ui", "upstreamVersion": "1.0.0", "upstreamSnapshotDigest": "sha256:" + "d" * 64},
                "harness": {"id": "opencode-native", "catalogRef": catalog.catalogRef, "implementationRef": impl.implementation_ref()},
                "requirements": {"harness": {"required": [], "preferred": []}, "moonmind": {"required": []}, "host": {"required": []}},
                "credentialSlots": [{"id": "primary-model", "optional": False, "acceptedAuthModels": ["own-auth"], "acceptedProviderIds": ["opencode"]}],
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
        skills = ResolvedSkillSet.model_validate({"resolvedSkillSetRef": "artifact:test", "resolvedSkillSetDigest": "sha256:" + "a" * 64, "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64})
        bs = create_binding_set(bindingSetId="test", version=1, bindings={"primary-model": {"providerProfileRef": "p1", "materializerRef": "opencode-auth-json@1"}})
        # Need opencode image env for host class
        os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "a" * 64
        store = InMemoryExecutionPlanStore()

        envelope1 = compile_execution_plan(
            agent_profile=profile,
            harness_catalog=catalog,
            trust_record=trust,
            resolved_skills=skills,
            credential_binding_set=bs,
            host_class_ref="omnigent-opencode@1",
            launch_policy_ref="omnigent-on-demand@1",
            model_qualified_id="opencode/model",
            model_effort=None,
            model_route_ref="opencode-go",
            model_normalized_options={},
        )
        persisted1 = await store.persist(envelope1)
        # Retry compiles same plan – should load same ref via store (no duplicate persist)
        persisted2 = await store.load_or_compile(compile_fn=compile_execution_plan, compile_kwargs=dict(
            agent_profile=profile,
            harness_catalog=catalog,
            trust_record=trust,
            resolved_skills=skills,
            credential_binding_set=bs,
            host_class_ref="omnigent-opencode@1",
            launch_policy_ref="omnigent-on-demand@1",
            model_qualified_id="opencode/model",
            model_effort=None,
            model_route_ref="opencode-go",
            model_normalized_options={},
        ))
        assert persisted1.planRef == persisted2.planRef
        assert persisted1 == persisted2
        # Load by ref
        loaded = await store.load(persisted1.planRef)
        assert loaded == persisted1

    import asyncio as _asyncio
    _asyncio.run(run())
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)


def test_workflow_cannot_author_realizer():
    catalog = _make_catalog(harness_id="codex-native", digest="sha256:" + "e" * 64)
    impl = HarnessImplementationIdentity.model_validate({"sourceKind": "core", "package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "e" * 64, "pluginEntryPoint": None})
    trust = classify_harness_trust(harnessId="codex-native", implementation=impl, trustState=TrustState.core_trusted)
    profile = OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {"kind": "upstream", "upstreamId": "codex-native-ui", "upstreamVersion": "1.0.0", "upstreamSnapshotDigest": "sha256:" + "d" * 64},
            "harness": {"id": "codex-native", "catalogRef": catalog.catalogRef, "implementationRef": impl.implementation_ref()},
            "requirements": {"harness": {"required": []}, "moonmind": {"required": []}, "host": {"required": []}},
            "credentialSlots": [{"id": "primary-model", "optional": False, "acceptedAuthModels": ["oauth_volume"], "acceptedProviderIds": ["openai"]}],
            "model": {},
            "workspace": {},
            "skills": [],
            "tools": [],
            "capture": {},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": ["codex-on-demand@1"],
        }
    )
    skills = ResolvedSkillSet.model_validate({"resolvedSkillSetRef": "artifact:test", "resolvedSkillSetDigest": "sha256:" + "a" * 64, "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64})
    bs = create_binding_set(bindingSetId="codex", version=1, bindings={"primary-model": {"providerProfileRef": "p1", "materializerRef": "codex-oauth-home@1"}})
    # Workflow tries to author generic realizer for codex – must fail closed (trusted planner only)
    # Trusted for codex-native is codex-profile-bound@1, workflow cannot force generic
    with pytest.raises(Exception) as exc:
        compile_execution_plan(
            agent_profile=profile,
            harness_catalog=catalog,
            trust_record=trust,
            resolved_skills=skills,
            credential_binding_set=bs,
            host_class_ref="omnigent-codex-current@1",
            launch_policy_ref="codex-on-demand@1",
            model_qualified_id="gpt-5",
            model_effort=None,
            model_route_ref="openai",
            model_normalized_options={},
            execution_realizer_ref="generic-omnigent-host@1",  # workflow-authored
        )
    assert "realizer" in str(exc.value).lower()

    # Without workflow authoring, planner selects trusted codex-profile-bound@1
    envelope2 = compile_execution_plan(
        agent_profile=profile,
        harness_catalog=catalog,
        trust_record=trust,
        resolved_skills=skills,
        credential_binding_set=bs,
        host_class_ref="omnigent-codex-current@1",
        launch_policy_ref="codex-on-demand@1",
        model_qualified_id="gpt-5",
        model_effort=None,
        model_route_ref="openai",
        model_normalized_options={},
    )
    assert envelope2.payload.executionRealizerRef == "codex-profile-bound@1"


def test_realizer_registry_no_fallback():
    reset_default_registry()
    registry = OmnigentExecutionRealizerRegistry()
    registry.register(CodexProfileBoundRealizer())
    registry.register(GenericOmnigentHostRealizer())

    assert registry.require("codex-profile-bound@1").ref == "codex-profile-bound@1"
    assert registry.require("generic-omnigent-host@1").ref == "generic-omnigent-host@1"

    # Must not fallback: generic failure does not select codex
    with pytest.raises(Exception) as exc:
        registry.require("nonexistent@1")
    assert "unavailable" in str(exc.value).lower()

    # Ensure no if harness branches in registry
    import inspect

    source = inspect.getsource(registry.require)
    assert 'harness == "opencode' not in source
    assert 'harness == "codex' not in source
