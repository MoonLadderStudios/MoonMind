"""Tests for trusted runtime-pack descriptors (issue #3827) and the shared
Codex/Claude Host Classes (#3826/#3828)."""

from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import (
    OMNIGENT_SHARED_HOST_IMAGE_ENV,
    OmnigentHostClassSelector,
    get_shared_host_image_ref,
)
from moonmind.omnigent.harness_platform.runtime_packs import (
    RUNTIME_PACK_SCHEMA_VERSION,
    RuntimePackDescriptor,
    get_runtime_pack,
    is_vendor_version_supported,
    pack_ref_for_harness,
    register_runtime_pack,
    runtime_dependencies_for_pack,
)

_SHARED_IMAGE_REF = (
    "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "e" * 64
)
_SHARED_IMAGE_REPOSITORY = "ghcr.io/moonladderstudios/omnigent-host-moonmind"
_SERVER_REF2 = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "b" * 64
_OPENCODE_IMAGE_REF = (
    "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "f" * 64
)


def _set_shared_image(monkeypatch, ref=_SHARED_IMAGE_REF) -> str:
    monkeypatch.setenv(OMNIGENT_SHARED_HOST_IMAGE_ENV, ref)
    return ref


def _harness(harness_id: str) -> SimpleNamespace:
    implementation = SimpleNamespace(
        implementation_ref=lambda: "omnigent-harness-implementation:sha256:" + "a" * 64
    )
    return SimpleNamespace(id=harness_id, implementation=implementation)


# ---- Registry contract ----


def test_builtin_packs_own_the_supported_trio():
    assert pack_ref_for_harness("codex-native") == "codex-native-pack@1"
    assert pack_ref_for_harness("claude-native") == "claude-native-pack@1"
    assert pack_ref_for_harness("opencode-native") == "opencode-native-pack@1"


def test_unknown_harness_has_no_pack():
    with pytest.raises(HarnessPlatformError) as exc:
        pack_ref_for_harness("not-a-harness")
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_RUNTIME_PACK_MISMATCH


def test_unknown_pack_ref_fails_closed():
    with pytest.raises(HarnessPlatformError) as exc:
        get_runtime_pack("codex-native-pack@99")
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_RUNTIME_PACK_MISMATCH


def test_pack_schema_and_credential_layout_contract():
    pack = get_runtime_pack("codex-native-pack@1")
    assert pack.schemaVersion == RUNTIME_PACK_SCHEMA_VERSION
    assert pack.credentialLayout.targetPath == "/home/app/.codex"
    assert pack.credentialLayout.writable is True
    assert pack.credentialLayout.ownerUid == 1000
    assert pack.readiness.kind == "vendor-version"
    assert pack.forbiddenAmbientEnvKeys
    # Cross-runtime credential isolation: a Codex row must reject the Claude
    # and OpenCode selectors, and vice versa.
    codex_forbidden = set(pack.forbiddenAmbientEnvKeys)
    assert "ANTHROPIC_API_KEY" in codex_forbidden
    claude = get_runtime_pack("claude-native-pack@1")
    claude_forbidden = set(claude.forbiddenAmbientEnvKeys)
    assert "OPENAI_API_KEY" in claude_forbidden
    assert "CLAUDE_CODE_OAUTH_TOKEN" in claude_forbidden
    opencode = get_runtime_pack("opencode-native-pack@1")
    opencode_forbidden = set(opencode.forbiddenAmbientEnvKeys)
    assert "OPENAI_API_KEY" in opencode_forbidden
    assert "ANTHROPIC_API_KEY" in opencode_forbidden


def test_vendor_version_range_is_exclusive_upper():
    opencode = get_runtime_pack("opencode-native-pack@1")
    assert is_vendor_version_supported(opencode, "1.17.7")
    assert is_vendor_version_supported(opencode, "1.18.11")
    assert not is_vendor_version_supported(opencode, "1.19.0")
    claude = get_runtime_pack("claude-native-pack@1")
    assert is_vendor_version_supported(claude, "2.1.257")
    assert not is_vendor_version_supported(claude, "3.0.0")
    codex = get_runtime_pack("codex-native-pack@1")
    assert is_vendor_version_supported(codex, "0.104.0")
    assert not is_vendor_version_supported(codex, "0.200.0")


def test_re_registering_a_changed_pack_fails():
    original = get_runtime_pack("codex-native-pack@1")
    payload = original.model_dump(by_alias=True, mode="json")
    payload["vendorRuntime"]["pinnedVersion"] = "9.9.9"
    changed = RuntimePackDescriptor.model_validate(payload)
    with pytest.raises(HarnessPlatformError):
        register_runtime_pack(changed)


def test_runtime_dependencies_declare_pinned_vendor_identity():
    pack = get_runtime_pack("claude-native-pack@1")
    deps = runtime_dependencies_for_pack(pack)
    assert deps == ({"name": "claude", "version": pack.vendorRuntime.pinnedVersion},)


# ---- Shared image env authority ----


def test_shared_host_image_ref_requires_digest_pin(monkeypatch):
    from moonmind.omnigent.bootstrap import store

    monkeypatch.setattr(store, "load_resolved_state", lambda: None)
    monkeypatch.delenv(OMNIGENT_SHARED_HOST_IMAGE_ENV, raising=False)
    with pytest.raises(HarnessPlatformError) as exc:
        get_shared_host_image_ref()
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH
    # Mutable tag never becomes launch authority.
    monkeypatch.setenv(
        OMNIGENT_SHARED_HOST_IMAGE_ENV,
        f"{_SHARED_IMAGE_REPOSITORY}:latest",
    )
    with pytest.raises(HarnessPlatformError):
        get_shared_host_image_ref()
    # Placeholder digest never becomes launch authority.
    monkeypatch.setenv(
        OMNIGENT_SHARED_HOST_IMAGE_ENV,
        f"{_SHARED_IMAGE_REPOSITORY}@sha256:{'0' * 64}",
    )
    with pytest.raises(HarnessPlatformError):
        get_shared_host_image_ref()


def test_persisted_state_can_supply_shared_image_ref(monkeypatch):
    from moonmind.omnigent.bootstrap import store

    monkeypatch.delenv(OMNIGENT_SHARED_HOST_IMAGE_ENV, raising=False)
    monkeypatch.setattr(
        store,
        "load_resolved_state",
        lambda: SimpleNamespace(shared_host_image_ref=_SHARED_IMAGE_REF),
    )
    assert get_shared_host_image_ref() == _SHARED_IMAGE_REF


# ---- Shared-image Host Class selection (#3828) ----


def test_codex_and_claude_host_classes_use_the_shared_digest(monkeypatch):
    _set_shared_image(monkeypatch)
    selector = OmnigentHostClassSelector()
    codex = selector.select(
        harness=_harness("codex-native"),
        omnigent_version="1.0.0",
        omnigent_build_digest="sha256:" + "b" * 64,
        integration_mode="native-server",
        materializer_refs=["codex-oauth-home@1"],
    )
    claude = selector.select(
        harness=_harness("claude-native"),
        omnigent_version="1.0.0",
        omnigent_build_digest="sha256:" + "b" * 64,
        integration_mode="native-server",
        materializer_refs=["claude-oauth-home@1"],
    )
    assert codex.ref == "omnigent-codex@1"
    assert claude.ref == "omnigent-claude@1"
    # One shared digest, separate Host Classes: image identity converges, and
    # harness support stays exact per class.
    assert codex.imageRef == _SHARED_IMAGE_REF
    assert claude.imageRef == _SHARED_IMAGE_REF
    assert {e.harnessId for e in codex.declaredHarnessImplementations} == {
        "codex-native"
    }
    assert {e.harnessId for e in claude.declaredHarnessImplementations} == {
        "claude-native"
    }
    # Runtime-pack-driven vendor dependencies land in the declared entry.
    codex_pack = get_runtime_pack("codex-native-pack@1")
    codex_dep = codex.declaredHarnessImplementations[0].runtimeDependencies[0]
    assert codex_dep == {
        "name": "codex",
        "version": codex_pack.vendorRuntime.pinnedVersion,
    }


@pytest.mark.parametrize(
    ("harness_id", "materializer_refs", "pack_ref"),
    (
        ("codex-native", ["claude-oauth-home@1"], "codex-native-pack@1"),
        ("claude-native", ["codex-oauth-home@1"], "claude-native-pack@1"),
        ("codex-native", ["opencode-auth-json@1"], "codex-native-pack@1"),
    ),
)
def test_shared_host_class_rejects_cross_harness_materializers(
    monkeypatch, harness_id, materializer_refs, pack_ref
):
    _set_shared_image(monkeypatch)
    selector = OmnigentHostClassSelector()
    with pytest.raises(HarnessPlatformError) as exc:
        selector.select(
            harness=_harness(harness_id),
            omnigent_version="1.0.0",
            omnigent_build_digest="sha256:" + "b" * 64,
            integration_mode="native-server",
            materializer_refs=materializer_refs,
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE


def test_shared_host_class_rejects_another_harness_by_request(
    monkeypatch,
):
    """One-harness admission: another installed CLI cannot be substituted."""
    from moonmind.omnigent.bootstrap import store

    # Give OpenCode its own valid dedicated image via persisted state so the
    # only way to get opencode-native onto the shared image would be
    # substitution — which must fail.
    monkeypatch.setattr(
        store,
        "load_resolved_state",
        lambda: SimpleNamespace(
            server_image_ref=_SERVER_REF2,
            opencode_host_image_ref=_OPENCODE_IMAGE_REF,
            pi_host_image_ref=None,
            details={
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": _SERVER_REF2,
                    "hostImageRef": _OPENCODE_IMAGE_REF,
                }
            },
        ),
    )
    _set_shared_image(monkeypatch)
    selector = OmnigentHostClassSelector()
    # Force the shared-image Codex class: opencode must not be admitted there.
    with pytest.raises(HarnessPlatformError) as exc:
        selector.select(
            harness=_harness("opencode-native"),
            omnigent_version="1.0.0",
            omnigent_build_digest="sha256:" + "b" * 64,
            integration_mode="native-server",
            materializer_refs=["opencode-auth-json@1"],
            requested_host_class_ref="omnigent-codex@1",
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE


def test_shared_host_class_requires_digest_pinned_env(monkeypatch):
    from moonmind.omnigent.bootstrap import store

    monkeypatch.setattr(store, "load_resolved_state", lambda: None)
    monkeypatch.delenv(OMNIGENT_SHARED_HOST_IMAGE_ENV, raising=False)
    selector = OmnigentHostClassSelector()
    with pytest.raises(HarnessPlatformError) as exc:
        selector.select(
            harness=_harness("codex-native"),
            omnigent_version="1.0.0",
            omnigent_build_digest="sha256:" + "b" * 64,
            integration_mode="native-server",
            materializer_refs=["codex-oauth-home@1"],
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE
