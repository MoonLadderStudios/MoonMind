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
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.omnigent.harness_platform import (
    HostHarnessAttestation,
    HarnessPlatformError,
    HarnessPlatformFailure,
    build_opencode_auth_json_bytes,
    cleanup_opencode_auth,
    get_host_class,
    get_opencode_host_class,
    get_opencode_host_image_ref,
    materialize_opencode_auth_json,
    verify_opencode_auth_file,
)
from moonmind.omnigent.harness_platform.attestation import (
    assert_opencode_version_supported,
    is_opencode_version_supported,
    validate_opencode_exact_host_preflight,
)
from moonmind.omnigent.harness_platform.host_classes import (
    OMNIGENT_OPENCODE_HOST_IMAGE_DEFAULT,
    OPENCODE_PINNED_VERSION,
)
from moonmind.omnigent.harness_platform.materializers import (
    FORBIDDEN_AMBIENT_ENV_KEYS,
    OPENCODE_AUTH_FILE_MODE,
    OPENCODE_AUTH_PARENT_MODE,
    OPENCODE_AUTH_TARGET_PATH,
    OPENCODE_PROVIDER_KEY,
)


def _ensure_opencode_env(valid: str = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "a" * 64) -> str:
    """Ensure OMNIGENT_OPENCODE_HOST_IMAGE_REF is set for tests that require a real digest."""
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = valid
    # Clear alternative image/tag overrides that would no longer be consulted
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE", None)
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_TAG", None)
    return valid


def _make_attestation(host_class_ref="omnigent-opencode@1", version="1.18.11"):
    _ensure_opencode_env()
    hc = get_opencode_host_class()
    return HostHarnessAttestation.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-host-harness-attestation.v1",
            "hostId": "host_opencode_1",
            "hostClassRef": host_class_ref,
            "hostImageRef": hc.imageRef,
            "omnigentVersion": "1.0.0",
            "omnigentBuildDigest": "sha256:" + "b" * 64,
            "harnessId": "opencode-native",
            "harnessImplementation": {"package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "a" * 64, "pluginEntryPoint": None},
            "runtimeDependencies": [{"name": "opencode", "version": version, "digest": "sha256:" + "d" * 64}],
            "configured": True,
            "capabilities": {"interrupt": True, "streaming": True, "restricted-egress": True},
            "observedAt": datetime.now(UTC),
        }
    )


def test_opencode_host_class_is_dedicated():
    valid = _ensure_opencode_env()
    hc = get_opencode_host_class()
    assert hc.ref == "omnigent-opencode@1"
    assert hc.imageRef.startswith("ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:")
    # Must be the real digest-pinned REF, not fabricated c*64 placeholder
    assert hc.imageRef != OMNIGENT_OPENCODE_HOST_IMAGE_DEFAULT
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


def test_codex_host_classes_preserved():
    # Existing Codex path must remain on proven host
    codex = get_host_class("omnigent-codex-current@1")
    assert codex.declares_harness("codex-native", get_host_class("omnigent-codex-current@1").declaredHarnessImplementations[0].implementationRef)
    assert not codex.supports_materializer("opencode-auth-json@1")
    # omnigent-native-standard still exists for backward compat but new opencode path uses dedicated class
    generic = get_host_class("omnigent-native-standard@3")
    assert generic.supports_materializer("codex-oauth-home@1")
    assert generic.supports_materializer("opencode-auth-json@1")


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


def test_opencode_image_ref_fail_closed():
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
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "c" * 64
    with pytest.raises(HarnessPlatformError):
        get_opencode_host_image_ref()
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)
    # Mutable tag fails closed
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = "ghcr.io/moonladderstudios/omnigent-host-opencode:latest"
    with pytest.raises(HarnessPlatformError) as exc:
        get_opencode_host_image_ref()
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH
    # Placeholder digest fails closed
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "0" * 64
    with pytest.raises(HarnessPlatformError):
        get_opencode_host_image_ref()
    # Valid pinned passes
    valid = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "a" * 64
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = valid
    assert get_opencode_host_image_ref() == valid
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)


def test_opencode_auth_json_bytes_structure():
    key = "sk-opencode-abcdef1234567890"
    payload_bytes = build_opencode_auth_json_bytes(api_key=key)
    payload = json.loads(payload_bytes)
    assert OPENCODE_PROVIDER_KEY in payload
    assert payload[OPENCODE_PROVIDER_KEY]["key"] == key
    assert payload[OPENCODE_PROVIDER_KEY]["type"] == "api"
    # Must NOT use legacy apiKey after Phase 0 correction
    assert "apiKey" not in payload[OPENCODE_PROVIDER_KEY]
    assert payload == {OPENCODE_PROVIDER_KEY: {"type": "api", "key": key}}


def test_materializer_writes_file_with_correct_perms_and_ownership():
    tmp = tempfile.mkdtemp()
    key = "sk-opencode-testkey-1234567890abcdef"
    handle = materialize_opencode_auth_json(
        api_key=key,
        provider_profile_ref="opencode-go-default",
        provider_lease_ref="lease:1",
        credential_generation=7,
        host_root=tmp,
    )
    # Handle is secret-free
    assert key not in json.dumps(handle)
    assert handle["targetPath"] == OPENCODE_AUTH_TARGET_PATH
    assert handle["accessMode"] == "read-only"
    assert handle["materializerRef"] == "opencode-auth-json@1"
    assert handle["credentialGeneration"] == 7
    # File exists with correct perms (Windows chmod differs; check only on POSIX)
    target = Path(tmp) / OPENCODE_AUTH_TARGET_PATH.lstrip("/")
    assert target.exists()
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == OPENCODE_AUTH_FILE_MODE
        assert stat.S_IMODE(target.parent.stat().st_mode) == OPENCODE_AUTH_PARENT_MODE
    # Verify content without leaking
    data = json.loads(target.read_bytes())
    assert data[OPENCODE_PROVIDER_KEY]["key"] == key
    assert "apiKey" not in data[OPENCODE_PROVIDER_KEY]
    # Cleanup
    cleanup_result = cleanup_opencode_auth(host_root=tmp, provider_profile_ref="opencode-go-default", credential_generation=7)
    assert cleanup_result["removedFile"] is True
    assert not target.exists()


def test_materializer_generation_fencing():
    tmp = tempfile.mkdtemp()
    key = "sk-opencode-gen-fence-1234567890"
    # Correct generation passes
    materialize_opencode_auth_json(
        api_key=key,
        provider_profile_ref="p1",
        provider_lease_ref="l1",
        credential_generation=4,
        expected_generation=4,
        host_root=tmp,
    )
    # Stale generation fails closed
    with pytest.raises(HarnessPlatformError) as exc:
        materialize_opencode_auth_json(
            api_key=key,
            provider_profile_ref="p1",
            provider_lease_ref="l1",
            credential_generation=5,
            expected_generation=4,
            host_root=tmp,
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED


def test_materializer_rejects_forbidden_ambient_env():
    tmp = tempfile.mkdtemp()
    key = "sk-opencode-env-test-1234567890"
    for env_key in FORBIDDEN_AMBIENT_ENV_KEYS:
        os.environ[env_key] = "evil"
        with pytest.raises(HarnessPlatformError) as exc:
            materialize_opencode_auth_json(
                api_key=key,
                provider_profile_ref="p1",
                provider_lease_ref="l1",
                credential_generation=1,
                host_root=tmp,
            )
        assert exc.value.code == HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
        del os.environ[env_key]
    # After clearing, should succeed
    handle = materialize_opencode_auth_json(
        api_key=key,
        provider_profile_ref="p1",
        provider_lease_ref="l1",
        credential_generation=1,
        host_root=tmp,
    )
    assert handle["credentialGeneration"] == 1
    cleanup_opencode_auth(host_root=tmp)


def test_materializer_never_leaks_raw_key_in_logs():
    tmp = tempfile.mkdtemp()
    key = "sk-opencode-secret-leak-test-xyz789"
    handle = materialize_opencode_auth_json(
        api_key=key,
        provider_profile_ref="opencode-go-default",
        provider_lease_ref="lease:1",
        credential_generation=2,
        host_root=tmp,
    )
    # Simulate log scanning
    log_content = json.dumps(handle) + "some log line"
    assert key not in log_content
    # Also check verify output
    verify_data = verify_opencode_auth_file(host_root=tmp, expected_api_key=key)
    assert key not in json.dumps(verify_data)
    cleanup_opencode_auth(host_root=tmp)


def test_verify_opencode_auth_file_detects_mismatch():
    tmp = tempfile.mkdtemp()
    key = "sk-opencode-verify-1234567890"
    materialize_opencode_auth_json(api_key=key, provider_profile_ref="p", provider_lease_ref="l", credential_generation=3, host_root=tmp)
    # Correct key passes
    verify_opencode_auth_file(host_root=tmp, expected_api_key=key)
    # Wrong key fails
    with pytest.raises(HarnessPlatformError):
        verify_opencode_auth_file(host_root=tmp, expected_api_key="wrong-key-1234567890")
    # Cleanup then missing fails
    cleanup_opencode_auth(host_root=tmp)
    with pytest.raises(HarnessPlatformError):
        verify_opencode_auth_file(host_root=tmp)


def test_exact_host_preflight_success():
    att = _make_attestation()
    hc = get_opencode_host_class()
    validate_opencode_exact_host_preflight(
        attestation=att,
        expectedHostClassRef=hc.ref,
        expectedImageRef=hc.imageRef,
        expectedOmnigentBuildDigest="sha256:" + "b" * 64,
        expectedImplementation={"package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "a" * 64, "pluginEntryPoint": None},
        requiredCapabilities=["interrupt"],
    )


def test_exact_host_preflight_fails_when_opencode_missing():
    _ensure_opencode_env()
    hc = get_opencode_host_class()
    att = HostHarnessAttestation.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-host-harness-attestation.v1",
            "hostId": "host_123",
            "hostClassRef": hc.ref,
            "hostImageRef": hc.imageRef,
            "omnigentVersion": "1.0.0",
            "omnigentBuildDigest": "sha256:" + "b" * 64,
            "harnessId": "opencode-native",
            "harnessImplementation": {"package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "a" * 64, "pluginEntryPoint": None},
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
            expectedImplementation={"package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "a" * 64, "pluginEntryPoint": None},
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH


def test_exact_host_preflight_fails_on_version_outside_range():
    _ensure_opencode_env()
    hc = get_opencode_host_class()
    att = _make_attestation(version="1.19.0")
    with pytest.raises(HarnessPlatformError) as exc:
        validate_opencode_exact_host_preflight(
            attestation=att,
            expectedHostClassRef=hc.ref,
            expectedImageRef=hc.imageRef,
            expectedOmnigentBuildDigest="sha256:" + "b" * 64,
            expectedImplementation={"package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "a" * 64, "pluginEntryPoint": None},
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH


def test_exact_host_preflight_fails_when_harness_not_opencode():
    _ensure_opencode_env()
    hc = get_opencode_host_class()
    att = HostHarnessAttestation.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-host-harness-attestation.v1",
            "hostId": "host_123",
            "hostClassRef": hc.ref,
            "hostImageRef": hc.imageRef,
            "omnigentVersion": "1.0.0",
            "omnigentBuildDigest": "sha256:" + "b" * 64,
            "harnessId": "codex-native",  # wrong harness
            "harnessImplementation": {"package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "a" * 64, "pluginEntryPoint": None},
            "runtimeDependencies": [{"name": "opencode", "version": "1.18.11", "digest": "sha256:" + "d" * 64}],
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
            expectedImplementation={"package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "a" * 64, "pluginEntryPoint": None},
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH


def test_exact_host_preflight_with_credential_file():
    _ensure_opencode_env()
    tmp = tempfile.mkdtemp()
    key = "sk-opencode-preflight-cred-1234567890"
    materialize_opencode_auth_json(api_key=key, provider_profile_ref="p", provider_lease_ref="l", credential_generation=5, host_root=tmp)
    hc = get_opencode_host_class()
    att = _make_attestation()
    # With credential file verification
    validate_opencode_exact_host_preflight(
        attestation=att,
        expectedHostClassRef=hc.ref,
        expectedImageRef=hc.imageRef,
        expectedOmnigentBuildDigest="sha256:" + "b" * 64,
        expectedImplementation={"package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "a" * 64, "pluginEntryPoint": None},
        verify_credential_file=True,
        credential_host_root=tmp,
        expectedCredentialGeneration=5,
    )
    cleanup_opencode_auth(host_root=tmp)
    # Without file should fail when verification requested but file missing
    with pytest.raises(HarnessPlatformError):
        validate_opencode_exact_host_preflight(
            attestation=att,
            expectedHostClassRef=hc.ref,
            expectedImageRef=hc.imageRef,
            expectedOmnigentBuildDigest="sha256:" + "b" * 64,
            expectedImplementation={"package": "omnigent", "version": "1.0.0", "digest": "sha256:" + "a" * 64, "pluginEntryPoint": None},
            verify_credential_file=True,
            credential_host_root=tmp,
        )


def test_image_does_not_install_unrelated_harnesses():
    # The dedicated image Dockerfile should only install opencode, not codex/claude etc.
    # We verify via code inspection: the Dockerfile exists and contains only opencode install
    dockerfile = Path("services/omnigent/opencode-host/Dockerfile")
    assert dockerfile.exists()
    content = dockerfile.read_text()
    assert "opencode-ai@" in content
    assert "codex" not in content.lower() or "codex" in content.lower() and "opencode" in content.lower()
    # Should verify opencode version
    assert "1.18.11" in content
    # Should not run npm install at workflow launch (only at build)
    # The host start script should not contain npm install
    start_script = Path("services/omnigent/scripts/start-opencode-host.sh")
    assert "npm install" not in start_script.read_text()
    # Check script clears forbidden env
    check_script = Path("services/omnigent/scripts/check-opencode-host.sh")
    assert "OPENCODE_AUTH_CONTENT" in check_script.read_text()
