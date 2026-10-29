"""Tests for OpenCode on the shared Omnigent host.

Covers:
- Digest-pinned shared host image expectations
- Host Class omnigent-opencode@1 vs Codex preservation
- opencode-auth-json@1 materializer filesystem, permissions, secret-free, generation fencing
- Forbidden ambient env rejection
- OMNIGENT_OPENCODE_HOST_IMAGE_REF fail-closed behavior
- Provider profile opencode-go mapping
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from moonmind.omnigent.harness_platform import (
    HarnessPlatformError,
    HarnessPlatformFailure,
    build_opencode_auth_json_bytes,
    get_host_class,
    get_opencode_host_image_ref,
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
from moonmind.omnigent.harness_platform.runtime_packs import get_runtime_pack

_RELEASE_WORKFLOW = Path(".github/workflows/docker-publish-moonmind-host.yml")
_SERVER_REF = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "b" * 64


@pytest.fixture(autouse=True)
def _ready_image_pair_evidence(monkeypatch):
    """Provide exact compatibility evidence for tests selecting OpenCode."""

    from moonmind.omnigent.bootstrap import store

    monkeypatch.setenv("OMNIGENT_IMAGE_REF", _SERVER_REF)

    def load_state():
        host_ref = os.environ.get("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "")
        if not host_ref:
            return None
        return SimpleNamespace(
            server_image_ref=_SERVER_REF,
            opencode_host_image_ref=host_ref,
            pi_host_image_ref=None,
            details={
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": _SERVER_REF,
                    "hostImageRef": host_ref,
                    "hostBuildDigest": "sha256:" + "b" * 64,
                    "hostVersion": "1.0.0",
                }
            },
        )

    monkeypatch.setattr(store, "load_resolved_state", load_state)


def test_shared_host_release_verifies_offline_opencode_startup():
    workflow = yaml.safe_load(_RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    # The published digest must prove it starts `opencode serve` offline from
    # the warm plugin npm cache before it can become launch authority.
    verify_steps = [
        step
        for step in workflow["jobs"]["build"]["steps"]
        if "verify-warm-plugin-cache.sh" in str(step.get("run", ""))
    ]
    assert len(verify_steps) == 1
    assert "${{ steps.build.outputs.digest }}" in verify_steps[0]["run"]
    verify_script = Path(
        "services/omnigent/moonmind-host/verify-warm-plugin-cache.sh"
    ).read_text(encoding="utf-8")
    assert "--network none" in verify_script
    assert "/opt/moonmind/opencode-npm-cache" in verify_script
    assert "prefer-offline=true" in verify_script


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
        integration_mode="native-server",
        materializer_refs=[materializer_ref],
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


@pytest.mark.parametrize("provider", ["openrouter", "vendor.v2_test", "x"])
def test_generic_opencode_materializer_mapping(provider):
    assert materializer_ref_for_provider("opencode", provider) == "opencode-auth-json@1"
    with pytest.raises(HarnessPlatformError):
        materializer_ref_for_provider("codex_cli", provider)


@pytest.mark.parametrize("provider", ["", "UPPER", "a/b", "a b", "-flag", "a\n"])
def test_opencode_rejects_unsafe_provider_ids(provider):
    with pytest.raises(HarnessPlatformError):
        materializer_ref_for_provider("opencode", provider)
    with pytest.raises(HarnessPlatformError):
        build_opencode_auth_json_bytes(api_key="test-key", provider_key=provider)


def test_opencode_free_route_rejects_key_materialization():
    with pytest.raises(HarnessPlatformError):
        build_opencode_auth_json_bytes(
            api_key="test-key", provider_key=OPENCODE_BUILTIN_PROVIDER_KEY
        )


def test_production_registry_has_no_synthetic_host_classes():
    assert HOST_CLASSES == {}
    with pytest.raises(HarnessPlatformError):
        get_host_class("omnigent-codex-current@1")
    with pytest.raises(HarnessPlatformError):
        get_host_class("omnigent-native-standard@3")


def test_opencode_pack_pin_matches_host_class_pin():
    pack = get_runtime_pack("opencode-native-pack@1")
    assert pack.vendorRuntime.pinnedVersion == OPENCODE_PINNED_VERSION


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
    monkeypatch.setattr(
        store,
        "load_resolved_state",
        lambda: SimpleNamespace(
            server_image_ref=_SERVER_REF,
            opencode_host_image_ref=valid,
            details={
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": _SERVER_REF,
                    "hostImageRef": valid,
                }
            },
        ),
    )
    assert get_opencode_host_image_ref() == valid
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)


@pytest.mark.parametrize(
    "provider_key",
    (OPENCODE_PROVIDER_KEY, "openrouter", "vendor.v2_test", "x"),
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


def test_shared_image_installs_all_supported_harnesses_and_offline_plugin_cache():
    dockerfile = Path("services/omnigent/moonmind-host/Dockerfile")
    content = dockerfile.read_text()
    assert '"@openai/codex@${CODEX_CLI_VERSION}"' in content
    assert '"@anthropic-ai/claude-code@${CLAUDE_CLI_VERSION}"' in content
    assert '"opencode-ai@${OPENCODE_VERSION}"' in content
    # Should verify opencode version
    assert "1.18.11" in content
    # OpenCode installs `@opencode-ai/plugin@<version>` into each fresh config
    # directory on its first `serve`; the image warms exactly that closure at
    # build time and proves it resolves offline so no launch downloads from npm.
    assert "ARG OPENCODE_PLUGIN_NPM_CACHE=/opt/moonmind/opencode-npm-cache" in content
    assert '{"dependencies":{"@opencode-ai/plugin":"%s"}}' in content
    assert (
        '--cache "${OPENCODE_PLUGIN_NPM_CACHE}" --no-audit --no-fund --offline'
        in content
    )
    assert (
        'test -f "${verify}/node_modules/@opencode-ai/plugin/package.json"' in content
    )
    assert 'chown -R 1000:1000 "${OPENCODE_PLUGIN_NPM_CACHE}"' in content
    assert (
        'moonmind.opencode.plugin_npm_cache="${OPENCODE_PLUGIN_NPM_CACHE}"' in content
    )
    # Runtime identity is a MoonMind numeric contract, not an upstream
    # provider-owned username that may disappear between Omnigent releases.
    assert "USER 1000:1000" in content
    assert "getent passwd 1000" in content
    assert 'ENV HOME="/home/app"' in content


def test_opencode_agent_file_tools_can_read_the_active_skill_snapshot():
    """The agent is told to read its selected Skill from the read-only mount."""

    from moonmind.omnigent.settings import OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR

    spec = yaml.safe_load(
        Path("services/omnigent/agents/opencode-native-ui/config.yaml").read_text()
    )
    sandbox = spec["os_env"]["sandbox"]

    # ``sandbox.type: none`` file tools reach only cwd plus declared grants;
    # a read grant admits reads and never writes of the snapshot.
    assert OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR in sandbox.get("read_paths", [])
    assert OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR not in sandbox.get("write_paths", [])
