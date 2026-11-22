"""Tests for OpenCode on the shared Omnigent host.

Covers:
- Digest-pinned shared host image expectations
- Host Class omnigent-opencode@2 (the only OpenCode class) vs Codex preservation
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
    get_shared_host_image_ref,
)
from moonmind.omnigent.harness_platform.host_classes import (
    HOST_CLASSES,
    OmnigentHostClassSelector,
)
from moonmind.omnigent.harness_platform.materializers import (
    OPENCODE_BUILTIN_PROVIDER_KEY,
    OPENCODE_PROVIDER_KEY,
    materializer_ref_for_provider,
)

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


def _use_shared_opencode_image(monkeypatch) -> str:
    """Install a bootstrap-qualified shared host image for OpenCode selection."""

    from moonmind.omnigent.bootstrap import store

    monkeypatch.setattr(store, "load_resolved_state", _shared_opencode_state)
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", _SHARED_HOST_REF)
    return _SHARED_HOST_REF


def test_opencode_host_class_is_dedicated(monkeypatch):
    shared = _use_shared_opencode_image(monkeypatch)
    hc = _opencode_host_class()
    assert hc.ref == "omnigent-opencode@2"
    # Must be the real digest-pinned shared REF, not a fabricated placeholder
    assert hc.imageRef == get_shared_host_image_ref()
    assert hc.imageRef == shared
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
    # The image build chooses the OpenCode release; the Host Class names the
    # binary and exact-host attestation records the installed release.
    dep = hc.declaredHarnessImplementations[0].runtimeDependencies[0]
    assert dep == {"name": "opencode"}


def test_opencode_host_class_accepts_credentialless_zen(monkeypatch):
    _use_shared_opencode_image(monkeypatch)

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


_SHARED_HOST_REF = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + (
    "e" * 64
)
_OTHER_SHARED_HOST_REF = (
    "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "f" * 64
)


def _shared_opencode_state(
    *,
    opencode_ref: str = _SHARED_HOST_REF,
    status: str = "ready",
    shared_evidence: dict | None = None,
):
    details = {
        "opencodeHostCompatibility": {
            "status": status,
            "failureCode": (
                None
                if status == "ready"
                else "omnigent_host_bootstrap_contract_missing"
            ),
            "serverImageRef": _SERVER_REF,
            "hostImageRef": opencode_ref,
            "hostBuildDigest": "sha256:" + "b" * 64,
            "hostVersion": "1.0.0",
        },
        "hostImageProvenance": {
            ref: {"buildDigest": "sha256:" + "b" * 64, "version": "1.0.0"}
            for ref in (_SHARED_HOST_REF, _OTHER_SHARED_HOST_REF)
        },
    }
    if shared_evidence is not None:
        details["sharedHostOpencodeCompatibility"] = shared_evidence
    return SimpleNamespace(
        server_image_ref=_SERVER_REF,
        opencode_host_image_ref=opencode_ref,
        shared_host_image_ref=_SHARED_HOST_REF,
        pi_host_image_ref=None,
        details=details,
    )


def _select_registered_opencode_host_class(monkeypatch, state, shared_ref):
    from moonmind.omnigent.bootstrap import store
    from moonmind.omnigent.harness_platform.harness_registry import harness_registration

    monkeypatch.setattr(store, "load_resolved_state", lambda: state)
    implementation = SimpleNamespace(
        implementation_ref=lambda: "omnigent-harness-implementation:sha256:" + "a" * 64
    )
    harness = SimpleNamespace(id="opencode-native", implementation=implementation)
    return OmnigentHostClassSelector(
        environment={
            "OMNIGENT_IMAGE_REF": _SERVER_REF,
            "OMNIGENT_SHARED_HOST_IMAGE_REF": shared_ref,
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF": state.opencode_host_image_ref,
        }
    ).select(
        harness=harness,
        omnigent_version="1.0.0",
        integration_mode="native-server",
        materializer_refs=["opencode-auth-json@1"],
        requested_host_class_ref=harness_registration("opencode-native").hostClassRef,
    )


def test_fresh_opencode_work_selects_the_shared_image_host_class(monkeypatch):
    from moonmind.omnigent.harness_platform.harness_registry import harness_registration

    registration = harness_registration("opencode-native")
    assert registration.hostClassRef == "omnigent-opencode@2"
    # The execution profile identity is unchanged, so recorded histories and
    # replay keep decoding the same target.
    assert registration.executionTargetRef == "omnigent-opencode@1"

    host_class = _select_registered_opencode_host_class(
        monkeypatch, _shared_opencode_state(), _SHARED_HOST_REF
    )

    assert host_class.ref == "omnigent-opencode@2"
    assert host_class.imageRef == _SHARED_HOST_REF
    assert {e.harnessId for e in host_class.declaredHarnessImplementations} == {
        "opencode-native"
    }


def test_shared_image_opencode_host_class_keeps_the_bootstrap_quarantine(
    monkeypatch,
):
    with pytest.raises(HarnessPlatformError) as exc:
        _select_registered_opencode_host_class(
            monkeypatch, _shared_opencode_state(status="blocked"), _SHARED_HOST_REF
        )

    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE
    assert "omnigent-opencode@2" in str(exc.value)
    assert "quarantined" in str(exc.value)
    assert "omnigent_host_bootstrap_contract_missing" in str(exc.value)


def test_distinct_shared_image_needs_its_own_opencode_verdict(monkeypatch):
    # The OpenCode image verdict cannot admit a different shared image.
    with pytest.raises(HarnessPlatformError) as exc:
        _select_registered_opencode_host_class(
            monkeypatch, _shared_opencode_state(), _OTHER_SHARED_HOST_REF
        )
    assert "compatibility evidence does not match" in str(exc.value)

    shared_evidence = {
        "status": "blocked",
        "failureCode": "omnigent_host_bootstrap_contract_missing",
        "serverImageRef": _SERVER_REF,
        "hostImageRef": _OTHER_SHARED_HOST_REF,
    }
    with pytest.raises(HarnessPlatformError) as exc:
        _select_registered_opencode_host_class(
            monkeypatch,
            _shared_opencode_state(shared_evidence=shared_evidence),
            _OTHER_SHARED_HOST_REF,
        )
    assert "quarantined" in str(exc.value)

    shared_evidence = {**shared_evidence, "status": "ready", "failureCode": None}
    host_class = _select_registered_opencode_host_class(
        monkeypatch,
        _shared_opencode_state(shared_evidence=shared_evidence),
        _OTHER_SHARED_HOST_REF,
    )
    assert host_class.ref == "omnigent-opencode@2"
    assert host_class.imageRef == _OTHER_SHARED_HOST_REF


@pytest.mark.parametrize("recorded_host", [False, True])
def test_removed_opencode_v1_host_class_fails_closed(monkeypatch, recorded_host):
    """A recorded or requested @1 is refused with guidance before image lookup."""

    from moonmind.omnigent.bootstrap import store

    monkeypatch.setattr(
        store,
        "load_resolved_state",
        lambda: pytest.fail("a removed Host Class must not resolve images"),
    )
    implementation = SimpleNamespace(
        implementation_ref=lambda: "omnigent-harness-implementation:sha256:" + "a" * 64
    )
    harness = SimpleNamespace(id="opencode-native", implementation=implementation)
    recorded = (
        {
            "host_image_ref": "ghcr.io/moonladderstudios/omnigent-host-opencode"
            "@sha256:" + "a" * 64,
            "omnigent_host_build_digest": "sha256:" + "b" * 64,
        }
        if recorded_host
        else {}
    )
    with pytest.raises(HarnessPlatformError) as exc:
        OmnigentHostClassSelector(environment={}).select(
            harness=harness,
            omnigent_version="1.0.0",
            integration_mode="native-server",
            materializer_refs=["opencode-auth-json@1"],
            requested_host_class_ref="omnigent-opencode@1",
            **recorded,
        )

    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE
    assert "omnigent-opencode@1 was removed" in str(exc.value)


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
    # Each CLI follows the newest release this base's Omnigent supports.
    assert "vendor_cli_releases.py --omnigent-bounds" in content
    assert '"codex=${CODEX_CLI_VERSION}"' in content
    assert '"claude=${CLAUDE_CLI_VERSION}"' in content
    assert '"opencode=${OPENCODE_VERSION}"' in content
    # The plugin SDK is warmed at the OpenCode release actually installed.
    assert "jq -er .opencode /opt/moonmind/vendor-cli-versions.json" in content
    # SDK-backed directory plugins resolve the current SDK closure offline.
    assert "ARG OPENCODE_PLUGIN_NPM_CACHE=/opt/moonmind/opencode-npm-cache" in content
    assert '{"dependencies":{"@opencode/plugin":"%s"}}' in content
    assert (
        '--cache "${OPENCODE_PLUGIN_NPM_CACHE}" --no-audit --no-fund --offline'
        in content
    )
    assert (
        'test -f "${verify}/node_modules/@opencode/plugin/package.json"' in content
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
