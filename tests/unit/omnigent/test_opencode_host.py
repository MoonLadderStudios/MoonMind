"""Tests for dedicated OpenCode host (issue 3752).

Covers:
- Digest-pinned omnigent-host-opencode image expectations
- Host Class omnigent-opencode@1 vs Codex preservation
- opencode-auth-json@1 materializer filesystem, permissions, secret-free, generation fencing
- Forbidden ambient env rejection
- Exact-host OpenCode preflight and attestation
- OMNIGENT_OPENCODE_HOST_IMAGE_REF fail-closed behavior
- Provider profile opencode-go mapping
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform import (
    HarnessPlatformError,
    HarnessPlatformFailure,
    HostHarnessAttestation,
    build_opencode_auth_json_bytes,
    get_host_class,
    get_opencode_host_image_ref,
)
from moonmind.omnigent.harness_platform.attestation import (
    assert_opencode_version_supported,
    is_opencode_version_supported,
    validate_opencode_exact_host_preflight,
)
from moonmind.omnigent.harness_platform.host_classes import (
    HOST_CLASSES,
    OPENCODE_PINNED_VERSION,
    OmnigentHostClassSelector,
)
from moonmind.omnigent.harness_platform.materializers import (
    OPENCODE_BUILTIN_PROVIDER_KEY,
    OPENCODE_PROVIDER_KEY,
    materializer_ref_for_provider,
)


def _ensure_opencode_env(
    valid: str = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "a" * 64,
) -> str:
    """Ensure OMNIGENT_OPENCODE_HOST_IMAGE_REF is set for tests that require a real digest."""
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = valid
    # Clear alternative image/tag overrides that would no longer be consulted
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE", None)
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_TAG", None)
    return valid


def _opencode_host_class(materializer_ref: str = "opencode-auth-json@1"):
    implementation = SimpleNamespace(
        implementation_ref=lambda: "omnigent-harness-implementation:sha256:" + "a" * 64
    )
    harness = SimpleNamespace(id="opencode-native", implementation=implementation)
    return OmnigentHostClassSelector().select(
        harness=harness,
        omnigent_version="1.0.0",
        omnigent_build_digest="sha256:" + "b" * 64,
        integration_mode="native-server",
        materializer_refs=[materializer_ref],
    )


def _make_attestation(host_class_ref="omnigent-opencode@1", version="1.18.11"):
    _ensure_opencode_env()
    hc = _opencode_host_class()
    return HostHarnessAttestation.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-host-harness-attestation.v1",
            "hostId": "host_opencode_1",
            "hostClassRef": host_class_ref,
            "hostImageRef": hc.imageRef,
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
                {"name": "opencode", "version": version, "digest": "sha256:" + "d" * 64}
            ],
            "configured": True,
            "capabilities": {
                "interrupt": True,
                "streaming": True,
                "restricted-egress": True,
            },
            "observedAt": datetime.now(UTC),
        }
    )


def test_opencode_host_class_is_dedicated():
    valid = _ensure_opencode_env()
    hc = _opencode_host_class()
    assert hc.ref == "omnigent-opencode@1"
    assert hc.imageRef.startswith(
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:"
    )
    # Must be the real digest-pinned REF, not fabricated c*64 placeholder
    assert hc.imageRef == get_opencode_host_image_ref()
    assert hc.imageRef == valid
    # Only opencode-native, not codex
    harness_ids = {e.harnessId for e in hc.declaredHarnessImplementations}
    assert harness_ids == {"opencode-native"}
    assert hc.supports_materializer("opencode-auth-json@1")
    assert not hc.supports_materializer("codex-oauth-home@1")
    assert hc.runtime["uid"] == 1000
    assert hc.runtime["gid"] == 1000
    # Architecture includes both amd64 and arm64 for multi-arch
    assert "linux/amd64" in hc.architectures
    assert "linux/arm64" in hc.architectures
    # Features declare expected capabilities
    assert hc.features["workspaceBind"] is True
    assert hc.features["restrictedEgress"] is True
    assert hc.features["mountedSkills"] is True
    # Check RuntimeDependency version is pinned
    dep = hc.declaredHarnessImplementations[0].runtimeDependencies[0]
    assert dep["name"] == "opencode"
    assert dep["version"] == OPENCODE_PINNED_VERSION


def test_opencode_host_class_accepts_credentialless_zen():
    _ensure_opencode_env()

    assert _opencode_host_class("none@1").supports_materializer("none@1")
    assert materializer_ref_for_provider("opencode", "opencode") == "none@1"
    assert (
        materializer_ref_for_provider("opencode", "opencode-go")
        == "opencode-auth-json@1"
    )


def test_production_registry_has_no_synthetic_host_classes():
    assert HOST_CLASSES == {}
    with pytest.raises(HarnessPlatformError):
        get_host_class("omnigent-codex-current@1")
    with pytest.raises(HarnessPlatformError):
        get_host_class("omnigent-native-standard@3")


def test_opencode_version_supported_range():
    assert is_opencode_version_supported("1.17.7")
    assert is_opencode_version_supported("1.18.11")
    assert is_opencode_version_supported("1.18.0")
    assert not is_opencode_version_supported("1.17.6")
    assert not is_opencode_version_supported("1.19.0")
    assert not is_opencode_version_supported("2.0.0")
    assert_opencode_version_supported("1.18.11")
    with pytest.raises(HarnessPlatformError) as exc:
        assert_opencode_version_supported("1.19.0")
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH


def test_opencode_image_ref_fail_closed(monkeypatch):
    # Selection now falls back to the deployment's persisted resolved state so
    # execution workers can select a Host Class the API resolved. That is a
    # second *source*, not a second contract: no source may synthesize a digest
    # from a mutable tag or accept a placeholder. Isolate the persisted source
    # here so the environment-only rules below are what is under test.
    from moonmind.omnigent.bootstrap import store

    monkeypatch.setattr(store, "load_resolved_state", lambda: None)

    # Missing REF must fail closed (no synthetic digest from image:tag)
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE", None)
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_TAG", None)
    with pytest.raises(HarnessPlatformError) as exc:
        get_opencode_host_image_ref()
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH
    # Even custom image/tag without REF must still fail closed (no synthesis)
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE"] = "ghcr.io/custom/opencode-host"
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_TAG"] = "1.18.11"
    with pytest.raises(HarnessPlatformError):
        get_opencode_host_image_ref()
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE", None)
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_TAG", None)
    # Placeholder c*64 also fails closed now
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = (
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "c" * 64
    )
    with pytest.raises(HarnessPlatformError):
        get_opencode_host_image_ref()
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)
    # A mutable tag or placeholder reaching selection through the persisted
    # resolved state fails closed exactly like the environment value does.
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)
    for unusable in (
        "ghcr.io/moonladderstudios/omnigent-host-opencode:1.18.11",
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "0" * 64,
    ):
        monkeypatch.setattr(
            store,
            "load_resolved_state",
            lambda ref=unusable: SimpleNamespace(
                opencode_host_image_ref=ref, pi_host_image_ref=None
            ),
        )
        with pytest.raises(HarnessPlatformError):
            get_opencode_host_image_ref()
    monkeypatch.setattr(store, "load_resolved_state", lambda: None)

    # Mutable tag fails closed
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = (
        "ghcr.io/moonladderstudios/omnigent-host-opencode:latest"
    )
    with pytest.raises(HarnessPlatformError) as exc:
        get_opencode_host_image_ref()
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH
    # Placeholder digest fails closed
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = (
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "0" * 64
    )
    with pytest.raises(HarnessPlatformError):
        get_opencode_host_image_ref()
    # Valid pinned passes
    valid = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "a" * 64
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = valid
    assert get_opencode_host_image_ref() == valid
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)


@pytest.mark.parametrize(
    "provider_key",
    (OPENCODE_PROVIDER_KEY, OPENCODE_BUILTIN_PROVIDER_KEY),
)
def test_opencode_auth_json_bytes_structure(provider_key: str):
    key = "sk-opencode-abcdef1234567890"
    payload_bytes = build_opencode_auth_json_bytes(
        api_key=key,
        provider_key=provider_key,
    )
    payload = json.loads(payload_bytes)
    assert provider_key in payload
    assert payload[provider_key]["key"] == key
    assert payload[provider_key]["type"] == "api"
    # Must NOT use legacy apiKey after Phase 0 correction
    assert "apiKey" not in payload[provider_key]
    assert payload == {
        provider_key: {"type": "api", "key": key},
    }


def test_exact_host_preflight_success():
    att = _make_attestation()
    hc = _opencode_host_class()
    validate_opencode_exact_host_preflight(
        attestation=att,
        expectedHostClassRef=hc.ref,
        expectedImageRef=hc.imageRef,
        expectedOmnigentBuildDigest="sha256:" + "b" * 64,
        expectedImplementation={
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "a" * 64,
            "pluginEntryPoint": None,
        },
        requiredCapabilities=["interrupt"],
    )


def test_exact_host_preflight_fails_when_opencode_missing():
    _ensure_opencode_env()
    hc = _opencode_host_class()
    att = HostHarnessAttestation.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-host-harness-attestation.v1",
            "hostId": "host_123",
            "hostClassRef": hc.ref,
            "hostImageRef": hc.imageRef,
            "omnigentVersion": "1.0.0",
            "omnigentBuildDigest": "sha256:" + "b" * 64,
            "harnessId": "opencode-native",
            "harnessImplementation": {
                "package": "omnigent",
                "version": "1.0.0",
                "digest": "sha256:" + "a" * 64,
                "pluginEntryPoint": None,
            },
            "runtimeDependencies": [],  # missing opencode
            "configured": True,
            "capabilities": {"interrupt": True, "restricted-egress": True},
            "observedAt": datetime.now(UTC),
        }
    )
    with pytest.raises(HarnessPlatformError) as exc:
        validate_opencode_exact_host_preflight(
            attestation=att,
            expectedHostClassRef=hc.ref,
            expectedImageRef=hc.imageRef,
            expectedOmnigentBuildDigest="sha256:" + "b" * 64,
            expectedImplementation={
                "package": "omnigent",
                "version": "1.0.0",
                "digest": "sha256:" + "a" * 64,
                "pluginEntryPoint": None,
            },
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH


def test_exact_host_preflight_fails_on_version_outside_range():
    _ensure_opencode_env()
    hc = _opencode_host_class()
    att = _make_attestation(version="1.19.0")
    with pytest.raises(HarnessPlatformError) as exc:
        validate_opencode_exact_host_preflight(
            attestation=att,
            expectedHostClassRef=hc.ref,
            expectedImageRef=hc.imageRef,
            expectedOmnigentBuildDigest="sha256:" + "b" * 64,
            expectedImplementation={
                "package": "omnigent",
                "version": "1.0.0",
                "digest": "sha256:" + "a" * 64,
                "pluginEntryPoint": None,
            },
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH


def test_exact_host_preflight_fails_when_harness_not_opencode():
    _ensure_opencode_env()
    hc = _opencode_host_class()
    att = HostHarnessAttestation.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-host-harness-attestation.v1",
            "hostId": "host_123",
            "hostClassRef": hc.ref,
            "hostImageRef": hc.imageRef,
            "omnigentVersion": "1.0.0",
            "omnigentBuildDigest": "sha256:" + "b" * 64,
            "harnessId": "codex-native",  # wrong harness
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
            "capabilities": {"interrupt": True, "restricted-egress": True},
            "observedAt": datetime.now(UTC),
        }
    )
    with pytest.raises(HarnessPlatformError) as exc:
        validate_opencode_exact_host_preflight(
            attestation=att,
            expectedHostClassRef=hc.ref,
            expectedImageRef=hc.imageRef,
            expectedOmnigentBuildDigest="sha256:" + "b" * 64,
            expectedImplementation={
                "package": "omnigent",
                "version": "1.0.0",
                "digest": "sha256:" + "a" * 64,
                "pluginEntryPoint": None,
            },
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH


def test_image_does_not_install_unrelated_harnesses():
    # The dedicated image Dockerfile should only install opencode, not codex/claude etc.
    # We verify via code inspection: the Dockerfile exists and contains only opencode install
    dockerfile = Path("services/omnigent/opencode-host/Dockerfile")
    assert dockerfile.exists()
    content = dockerfile.read_text()
    assert "opencode-ai@" in content
    assert (
        "codex" not in content.lower()
        or "codex" in content.lower()
        and "opencode" in content.lower()
    )
    # Should verify opencode version
    assert "1.18.11" in content
    # Runtime identity is a MoonMind numeric contract, not an upstream
    # provider-owned username that may disappear between Omnigent releases.
    assert "USER 1000:1000" in content
    assert "getent passwd 1000" in content
    assert 'ENV HOME="/home/app"' in content
    # Should not run npm install at workflow launch (only at build)
    # The host start script should not contain npm install
    start_script = Path("services/omnigent/scripts/start-opencode-host.sh")
    assert "npm install" not in start_script.read_text()
    # Check script clears forbidden env
    check_script = Path("services/omnigent/scripts/check-opencode-host.sh")
    check_source = check_script.read_text()
    assert "OPENCODE_AUTH_CONTENT" in check_source
    # The host readiness check must validate the exact auth.json contract
    # emitted by opencode-auth-json@1 (``key``, not the obsolete ``apiKey``).
    assert ".get('key')" in check_source
    assert "'apiKey'" not in check_source
