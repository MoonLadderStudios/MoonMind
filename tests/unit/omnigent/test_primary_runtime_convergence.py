"""Primary runtime convergence — shared image, runtime packs, OAuth materializers (MoonLadderStudios/MoonMind#3825).

Validates that Codex, Claude Code, and OpenCode converge on the generic
Omnigent plane with exact Host Class isolation and ownership-declared
credential handles.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.harness_platform.host_classes import (
    OMNIGENT_OPENCODE_HOST_IMAGE_ENV,
    OMNIGENT_RUNTIME_HOST_IMAGE_ENV,
    OmnigentHostClassSelector,
)
from moonmind.omnigent.harness_platform.materializers import (
    get_materializer,
    materialize_credential,
    materializer_ref_for_provider,
)


def _dummy_harness(harness_id: str, impl_ref: str = "omnigent-harness-implementation:sha256:" + "a" * 64):
    class Impl:
        def implementation_ref(self) -> str:
            return impl_ref

    class Harness:
        id = harness_id
        implementation = Impl()

    return Harness()


SHARED_DIGEST = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "b" * 64
BUILD_DIGEST = "sha256:" + "c" * 64


def test_claude_oauth_home_materializer_exists_with_profile_owned_state():
    mat = get_materializer("claude-oauth-home@1")
    assert mat.materializerId == "claude-oauth-home"
    assert mat.version == 1
    assert "claude-native" in mat.acceptedHarnessIds
    assert "oauth_volume" in mat.acceptedAuthModels
    assert mat.state.get("ownership") == "profile_owned"
    assert mat.state.get("mutable") is True
    assert mat.target["path"] == "/home/app/.claude"
    assert "on-demand" in mat.supportedHostModes
    assert "static-connected" in mat.supportedHostModes


def test_codex_oauth_home_now_declares_profile_owned():
    mat = get_materializer("codex-oauth-home@1")
    assert mat.state.get("ownership") == "profile_owned"


def test_opencode_auth_json_declares_run_owned():
    mat = get_materializer("opencode-auth-json@1")
    assert mat.state.get("ownership") == "run_owned"


def test_materialize_handle_declares_ownership():
    handle = materialize_credential(
        materializer_ref="codex-oauth-home@1",
        provider_profile_ref="codex_openai_oauth",
        provider_lease_ref="lease-1",
        credential_generation=7,
    )
    assert handle["ownership"] == "profile_owned"
    assert handle["materializerRef"] == "codex-oauth-home@1"
    assert handle["targetPath"] == "/home/app/.codex"

    handle_claude = materialize_credential(
        materializer_ref="claude-oauth-home@1",
        provider_profile_ref="claude_anthropic_oauth",
        provider_lease_ref="lease-2",
        credential_generation=4,
    )
    assert handle_claude["ownership"] == "profile_owned"
    assert handle_claude["targetPath"] == "/home/app/.claude"

    handle_opencode = materialize_credential(
        materializer_ref="opencode-auth-json@1",
        provider_profile_ref="opencode-go-default",
        provider_lease_ref="lease-3",
        credential_generation=1,
    )
    assert handle_opencode["ownership"] == "run_owned"


def test_materializer_ref_for_claude_provider():
    assert materializer_ref_for_provider("claude_code", "anthropic") == "claude-oauth-home@1"


def test_separate_host_classes_share_digest_without_conflating_support():
    env = {
        OMNIGENT_OPENCODE_HOST_IMAGE_ENV: SHARED_DIGEST,
        OMNIGENT_RUNTIME_HOST_IMAGE_ENV: SHARED_DIGEST,
    }
    selector = OmnigentHostClassSelector(environment=env)

    codex_harness = _dummy_harness("codex-native")
    claude_harness = _dummy_harness("claude-native")
    opencode_harness = _dummy_harness("opencode-native")

    codex_class = selector.select(
        harness=codex_harness,
        omnigent_version="0.10.0",
        omnigent_build_digest=BUILD_DIGEST,
        integration_mode="native-server",
        materializer_refs=["codex-oauth-home@1"],
    )
    claude_class = selector.select(
        harness=claude_harness,
        omnigent_version="0.10.0",
        omnigent_build_digest=BUILD_DIGEST,
        integration_mode="native-server",
        materializer_refs=["claude-oauth-home@1"],
    )
    opencode_class = selector.select(
        harness=opencode_harness,
        omnigent_version="0.10.0",
        omnigent_build_digest=BUILD_DIGEST,
        integration_mode="native-server",
        materializer_refs=["opencode-auth-json@1"],
    )

    # Same digest, different Host Class identity
    assert codex_class.imageRef == SHARED_DIGEST
    assert claude_class.imageRef == SHARED_DIGEST
    assert opencode_class.imageRef == SHARED_DIGEST
    assert codex_class.ref == "omnigent-codex@1"
    assert claude_class.ref == "omnigent-claude@1"
    assert opencode_class.ref == "omnigent-opencode@1"

    # No conflation: each class only supports its harness's materializer
    assert codex_class.supports_materializer("codex-oauth-home@1")
    assert not codex_class.supports_materializer("claude-oauth-home@1")
    assert not codex_class.supports_materializer("opencode-auth-json@1")

    assert claude_class.supports_materializer("claude-oauth-home@1")
    assert not claude_class.supports_materializer("codex-oauth-home@1")

    assert opencode_class.supports_materializer("opencode-auth-json@1")
    assert not opencode_class.supports_materializer("claude-oauth-home@1")


def test_shared_image_host_classes_fall_back_to_opencode_env():
    # Only OPENCODE variable set – transitional Compose path
    env = {OMNIGENT_OPENCODE_HOST_IMAGE_ENV: SHARED_DIGEST}
    selector = OmnigentHostClassSelector(environment=env)
    claude_harness = _dummy_harness("claude-native")
    claude_class = selector.select(
        harness=claude_harness,
        omnigent_version="0.10.0",
        omnigent_build_digest=BUILD_DIGEST,
        integration_mode="native-server",
        materializer_refs=["claude-oauth-home@1"],
    )
    assert claude_class.imageRef == SHARED_DIGEST


def test_planner_allows_generic_realizer_for_codex_canary():
    from moonmind.omnigent.harness_platform.catalog import (
        HarnessCatalogSnapshot,
        HarnessImplementationIdentity,
        HarnessTrustRecord,
    )
    from moonmind.omnigent.harness_platform.credential_bindings import create_binding_set
    from moonmind.omnigent.harness_platform.planner import compile_execution_plan
    from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet

    impl_ref = "omnigent-harness-implementation:sha256:" + "a" * 64
    catalog_ref = "omnigent-harness-catalog:sha256:" + "d" * 64
    # Build minimal catalog with codex-native
    catalog = HarnessCatalogSnapshot.model_validate(
        {
            "catalogRef": catalog_ref,
            "endpointRef": "default",
            "omnigentVersion": "0.10.0",
            "omnigentBuildDigest": BUILD_DIGEST,
            "harnesses": [
                {
                    "id": "codex-native",
                    "implementation": {
                        "digest": "a" * 64,
                        "version": "0.1.0",
                    },
                    "capabilities": {"integrationMode": "native-server"},
                }
            ],
            "observedAt": "2026-08-28T00:00:00Z",
        }
    )
    trust = HarnessTrustRecord.model_validate(
        {
            "harnessId": "codex-native",
            "implementationRef": impl_ref,
            "trustState": "trusted",
            "catalogRef": catalog_ref,
        }
    )

    agent_profile = {
        "profileId": "omnigent-codex-default",
        "version": 1,
        "digest": "sha256:" + "e" * 64,
        "endpointRef": "default",
        "harness": {
            "id": "codex-native",
            "catalogRef": catalog_ref,
            "implementationRef": impl_ref,
            "catalogDigest": "a" * 64,
        },
        "source": {"kind": "upstream", "upstreamId": "codex-native-ui", "upstreamVersion": "1"},
        "credentialSlots": [
            {"id": "primary", "acceptedAuthModels": ["oauth_volume"], "acceptedProviderIds": ["openai"], "optional": False}
        ],
        "requirements": {"harness": {"required": [], "preferred": []}, "moonmind": {"required": []}, "host": {"required": []}},
        "workspace": {"mutation": "allowed"},
        "capture": {},
        "tools": [],
    }
    binding_set = create_binding_set(
        binding_set_id="codex-1",
        bindings={"primary": {"materializerRef": "codex-oauth-home@1", "providerProfileRef": "codex_openai_oauth"}},
    )
    skills = ResolvedSkillSet.model_validate({"skills": [], "digest": "sha256:" + "f" * 64})
    # Register a host class for codex via selector env
    env = {
        OMNIGENT_OPENCODE_HOST_IMAGE_ENV: SHARED_DIGEST,
        OMNIGENT_RUNTIME_HOST_IMAGE_ENV: SHARED_DIGEST,
    }
    selector = OmnigentHostClassSelector(environment=env)
    codex_harness = _dummy_harness("codex-native", impl_ref)
    host_class = selector.select(
        harness=codex_harness,
        omnigent_version="0.10.0",
        omnigent_build_digest=BUILD_DIGEST,
        integration_mode="native-server",
        materializer_refs=["codex-oauth-home@1"],
    )
    # Canary generic realizer for Codex should be allowed
    plan = compile_execution_plan(
        agent_profile=agent_profile,
        harness_catalog=catalog,
        trust_record=trust,
        resolved_skills=skills,
        credential_binding_set=binding_set,
        host_class_ref=host_class.ref,
        host_class=host_class,
        launch_policy_ref="codex-on-demand@1",
        model_qualified_id=None,
        model_effort=None,
        model_route_ref=None,
        model_normalized_options={},
        execution_realizer_ref="generic-omnigent-host@1",
    )
    assert plan.payload.executionRealizerRef == "generic-omnigent-host@1"
