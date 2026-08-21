"""TDD for Phase 0: Correct false readiness and unsafe placeholders.

This test module encodes the desired architecture's Phase 0 acceptance criteria
BEFORE the production fix, so it will FAIL until the code is corrected:

1. OpenCode auth.json must use `key` not `apiKey` (pinned opencode-ai@1.18.x)
2. get_opencode_host_image_ref must fail closed when no real digest-pinned REF
3. omnigent-opencode@1 unavailable when no real image, no realizer, incompatible profile
4. No harness-specific branches in realizer dispatch (executionRealizerRef trusted)
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from moonmind.omnigent.harness_platform.materializers import (
    OPENCODE_PROVIDER_KEY,
    build_opencode_auth_json_bytes,
    materialize_opencode_auth_json,
    verify_opencode_auth_file,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError


def test_opencode_auth_json_uses_key_not_apiKey():
    """Issue §5 / Phase 0: pinned OpenCode uses `key`, not `apiKey`."""
    raw_key = "sk-opencode-phase0-test-1234567890abcdef"
    payload_bytes = build_opencode_auth_json_bytes(api_key=raw_key)
    payload = json.loads(payload_bytes)

    # Must use provider key "opencode-go"
    assert OPENCODE_PROVIDER_KEY == "opencode-go"
    assert OPENCODE_PROVIDER_KEY in payload

    entry = payload[OPENCODE_PROVIDER_KEY]
    # MUST be `key`, must NOT be `apiKey`
    assert "key" in entry, "opencode auth.json must use `key` per pinned opencode-ai@1.18.x"
    assert "apiKey" not in entry, "opencode auth.json must not use legacy `apiKey`"
    assert entry["key"] == raw_key
    assert entry["type"] == "api"

    # Also verify correct canonical form: {"opencode-go": {"type":"api","key":"..."}}
    # No other secret-carrying shape is acceptable
    expected = {OPENCODE_PROVIDER_KEY: {"type": "api", "key": raw_key}}
    assert payload == expected


def test_materialize_and_verify_uses_key():
    tmp = tempfile.mkdtemp()
    raw_key = "sk-opencode-phase0-verify-xyz789"
    handle = materialize_opencode_auth_json(
        api_key=raw_key,
        provider_profile_ref="opencode-go-default",
        provider_lease_ref="lease:phase0",
        credential_generation=3,
        host_root=tmp,
    )
    # Handle must be secret-free
    assert raw_key not in json.dumps(handle)

    # File content must use `key`
    target = Path(tmp) / "home/app/.local/share/opencode/auth.json"
    data = json.loads(target.read_bytes())
    assert data[OPENCODE_PROVIDER_KEY]["key"] == raw_key
    assert "apiKey" not in data[OPENCODE_PROVIDER_KEY]

    # Verify helper must also check `key`, not `apiKey`
    verify_opencode_auth_file(host_root=tmp, expected_api_key=raw_key)

    # Cleanup
    from moonmind.omnigent.harness_platform.materializers import cleanup_opencode_auth
    cleanup_opencode_auth(host_root=tmp)


def test_get_opencode_host_image_ref_requires_real_digest():
    """Phase 0: must fail closed when only mutable tag or no digest-pinned REF.

    Do not synthesize a fake digest from image:tag. A Host Class becomes
    launchable only after deployment has resolved and recorded a real OCI digest.
    """
    # Save env
    env_keys = ["OMNIGENT_OPENCODE_HOST_IMAGE_REF", "OMNIGENT_OPENCODE_HOST_IMAGE", "OMNIGENT_OPENCODE_HOST_IMAGE_TAG"]
    saved = {k: os.environ.get(k) for k in env_keys}
    try:
        for k in env_keys:
            os.environ.pop(k, None)

        # No env at all -> must raise, not synthesize
        from moonmind.omnigent.harness_platform.host_classes import get_opencode_host_image_ref
        with pytest.raises(HarnessPlatformError) as exc:
            get_opencode_host_image_ref()
        assert "digest-pinned" in str(exc.value).lower() or "omnigent_harness_build_mismatch" in exc.value.code.lower() or "harness_build_mismatch" in str(exc.value.code).lower()

        # Mutable tag via REF must fail closed
        os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = "ghcr.io/moonladderstudios/omnigent-host-opencode:latest"
        with pytest.raises(HarnessPlatformError):
            get_opencode_host_image_ref()

        # Placeholder digest fails closed
        os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "0" * 64
        with pytest.raises(HarnessPlatformError):
            get_opencode_host_image_ref()

        os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "c" * 64
        with pytest.raises(HarnessPlatformError):
            get_opencode_host_image_ref()

        # Real digest-pinned passes
        valid = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "a" * 64
        os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = valid
        assert get_opencode_host_image_ref() == valid

    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_host_class_unavailable_without_real_image():
    """omnigent-opencode@1 unavailable when no real image digest exists."""
    env_keys = ["OMNIGENT_OPENCODE_HOST_IMAGE_REF", "OMNIGENT_OPENCODE_HOST_IMAGE", "OMNIGENT_OPENCODE_HOST_IMAGE_TAG"]
    saved = {k: os.environ.get(k) for k in env_keys}
    try:
        for k in env_keys:
            os.environ.pop(k, None)

        # After fixing host_classes, get_opencode_host_class should raise or
        # indicate unavailable when image not configured.
        from moonmind.omnigent.harness_platform.host_classes import get_opencode_host_class, get_host_class
        # Should fail closed or return unavailable - either raising or having placeholder
        # The contract: attempting to compile a plan with omnigent-opencode@1 when no real image
        # should fail with HOST_CLASS_UNAVAILABLE or HARNESS_BUILD_MISMATCH, not silently use synthetic.

        # We test via planner: compiling with omnigent-opencode@1 should fail when no real image
        from datetime import UTC, datetime
        from moonmind.omnigent.harness_platform.catalog import create_catalog_snapshot, classify_harness_trust, HarnessImplementationIdentity, TrustState
        from moonmind.omnigent.harness_platform.agent_profile import OmnigentAgentProfileV2
        from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
        from moonmind.omnigent.harness_platform.credential_bindings import create_binding_set
        from moonmind.omnigent.harness_platform.planner import compile_execution_plan

        def make_impl(digest="sha256:" + "a" * 64):
            return HarnessImplementationIdentity.model_validate(
                {"sourceKind": "core", "package": "omnigent", "version": "1.0.0", "digest": digest, "pluginEntryPoint": None}
            )

        catalog = create_catalog_snapshot(
            endpointRef="default",
            omnigentVersion="1.0.0",
            omnigentBuildDigest="sha256:" + "b" * 64,
            sourceDigest="sha256:" + "c" * 64,
            harnesses=[
                {
                    "id": "opencode-native",
                    "aliases": ["opencode"],
                    "label": "OpenCode",
                    "implementation": {"sourceKind": "core", "package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "a" * 64, "pluginEntryPoint": None},
                    "runtimeRequirements": {},
                    "capabilities": {"integrationMode": "native-server", "authModel": "own-auth", "interrupt": True, "streaming": True},
                    "setupSteps": [],
                }
            ],
            observedAt=datetime.now(UTC),
        )
        impl = make_impl()
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
        bs = create_binding_set(bindingSetId="opencode-go-primary", version=1, bindings={"primary-model": {"providerProfileRef": "opencode-go-default", "materializerRef": "opencode-auth-json@1"}})

        # This should fail when image not configured - either at get_host_class or compile
        with pytest.raises((HarnessPlatformError, ValueError)):
            compile_execution_plan(
                agent_profile=profile,
                harness_catalog=catalog,
                trust_record=trust,
                resolved_skills=skills,
                credential_binding_set=bs,
                host_class_ref="omnigent-opencode@1",
                launch_policy_ref="omnigent-on-demand@1",
                model_qualified_id="opencode/test-model",
                model_effort=None,
                model_route_ref="opencode-go",
                model_normalized_options={},
            )

    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
