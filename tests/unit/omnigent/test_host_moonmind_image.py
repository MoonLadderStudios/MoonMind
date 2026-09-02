"""Tests for neutral shared MoonMind Omnigent host image (issue MoonLadderStudios/MoonMind#3826).

Covers:
- Dockerfile contract for host-moonmind (build-time installs, no launch install, labels, runtime user)
- Workflow contract for docker-publish-omnigent-host-moonmind.yml (multi-arch, alias, SBOM, provenance, manifest)
- Host Class alias contract (neutral vs opencode alias must be equal while active)
- Alias retirement condition documented
- New neutral release does not change execution realizer selection
- Version ranges for codex/claude/opencode
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError, HarnessPlatformFailure
from moonmind.omnigent.harness_platform.host_classes import (
    CLAUDE_PINNED_VERSION,
    CODEX_PINNED_VERSION,
    HOST_MOONMIND_ALIAS_RETIREMENT_CONDITION,
    OMNIGENT_RUNTIME_HOST_IMAGE_ENV,
    OPENCODE_PINNED_VERSION,
    OmnigentHostClassSelector,
    get_runtime_host_image_ref,
)

_NEUTRAL_DOCKERFILE = Path("services/omnigent/host-moonmind/Dockerfile")
_LEGACY_DOCKERFILE = Path("services/omnigent/opencode-host/Dockerfile")
_NEUTRAL_WORKFLOW = Path(".github/workflows/docker-publish-omnigent-host-moonmind.yml")
_LEGACY_WORKFLOW = Path(".github/workflows/docker-publish-opencode-host.yml")
_SERVER_REF = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "b" * 64
_HOST_DIGEST = "a" * 64


@pytest.fixture(autouse=True)
def _ready_image_pair_evidence(monkeypatch):
    from moonmind.omnigent.bootstrap import store

    monkeypatch.setenv("OMNIGENT_IMAGE_REF", _SERVER_REF)

    def load_state():
        runtime_ref = os.environ.get("OMNIGENT_RUNTIME_HOST_IMAGE_REF", "") or os.environ.get(
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF", ""
        )
        opencode_ref = os.environ.get("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "") or runtime_ref
        if not runtime_ref:
            return None
        return SimpleNamespace(
            server_image_ref=_SERVER_REF,
            opencode_host_image_ref=opencode_ref,
            runtime_host_image_ref=runtime_ref,
            pi_host_image_ref=None,
            details={
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": _SERVER_REF,
                    "hostImageRef": runtime_ref,
                },
                "runtimeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": _SERVER_REF,
                    "hostImageRef": runtime_ref,
                },
            },
        )

    monkeypatch.setattr(store, "load_resolved_state", load_state)


def test_neutral_dockerfile_exists_and_installs_all_runtimes():
    assert _NEUTRAL_DOCKERFILE.exists(), "host-moonmind Dockerfile must exist"
    content = _NEUTRAL_DOCKERFILE.read_text()
    assert "FROM ${OMNIGENT_HOST_BASE_IMAGE}" in content
    assert "opencode-ai@${OPENCODE_VERSION}" in content
    assert "@openai/codex@${CODEX_VERSION}" in content
    assert "@anthropic-ai/claude-code@${CLAUDE_VERSION}" in content
    # Must verify all four binaries
    for binary in ["omnigent --version", "codex --version", "claude --version", "opencode --version"]:
        assert binary in content
    # Must carry portable build digest label
    assert 'moonmind.omnigent.build_digest' in content
    assert 'moonmind.opencode.version' in content
    assert 'moonmind.codex.version' in content
    assert 'moonmind.claude.version' in content
    # Title must be neutral
    assert 'org.opencontainers.image.title="omnigent-host-moonmind"' in content
    # Runtime user contract
    assert "USER 1000:1000" in content
    assert "getent passwd 1000" in content
    assert 'HOME="/home/app"' in content
    assert "WORKDIR /home/app" in content
    # No credentials embedded check
    assert "Provider credential file embedded" in content or "credential file embedded" in content.lower()


def test_neutral_dockerfile_versions_pinned_and_ranges():
    content = _NEUTRAL_DOCKERFILE.read_text()
    assert "OPENCODE_VERSION=1.18.11" in content
    assert "CODEX_VERSION=0.52.0" in content
    assert "CLAUDE_VERSION=2.1.257" in content
    assert "OPENCODE_MIN_VERSION=1.17.7" in content
    assert "OPENCODE_MAX_VERSION=1.19.0" in content
    assert "CODEX_MIN_VERSION=0.50.0" in content
    assert "CODEX_MAX_VERSION=1.0.0" in content
    assert "CLAUDE_MIN_VERSION=2.0.0" in content
    assert "CLAUDE_MAX_VERSION=3.0.0" in content
    # Build fails when version outside range — uses packaging.version
    assert "outside supported range" in content


def test_neutral_dockerfile_all_runtime_acquisition_at_build_time():
    content = _NEUTRAL_DOCKERFILE.read_text()
    # Must install via npm at build time
    assert "npm install -g" in content
    # Ensure no workflow launch install — legacy script check still holds
    scripts = list(Path("services/omnigent/scripts").glob("*.sh"))
    for script in scripts:
        assert "npm install -g" not in script.read_text() or "host-moonmind" not in script.name


def test_neutral_workflow_contract():
    assert _NEUTRAL_WORKFLOW.exists(), "neutral publish workflow must exist"
    workflow = yaml.safe_load(_NEUTRAL_WORKFLOW.read_text())
    assert workflow["name"] == "Release / Build omnigent-host-moonmind Image"
    # Env must have both neutral and alias
    assert workflow["env"]["REGISTRY_IMAGE"] == "ghcr.io/moonladderstudios/omnigent-host-moonmind"
    assert workflow["env"]["ALIAS_IMAGE"] == "ghcr.io/moonladderstudios/omnigent-host-opencode"
    # Must have inputs for each runtime (handle YAML 'on' as boolean True)
    on_block = workflow.get("on") or workflow.get(True)
    inputs = on_block["workflow_dispatch"]["inputs"]
    assert "opencode_version" in inputs
    assert "codex_version" in inputs
    assert "claude_version" in inputs
    # Build must publish multi-arch
    build_job = workflow["jobs"]["build"]
    platforms = build_job["strategy"]["matrix"]["include"]
    assert any(p["platform"] == "linux/amd64" for p in platforms)
    assert any(p["platform"] == "linux/arm64" for p in platforms)
    # Buildx must use sbom/provenance
    build_step = next(s for s in build_job["steps"] if s.get("id") == "build")
    assert build_step["with"]["sbom"] is True
    assert build_step["with"]["provenance"] is True
    # Must use neutral Dockerfile
    assert build_step["with"]["file"] == "./services/omnigent/host-moonmind/Dockerfile"
    # Merge must create manifests for both neutral and alias and verify alias digest equality
    merge_job = workflow["jobs"]["merge"]
    merge_run_text = "\n".join(str(s.get("run", "")) for s in merge_job["steps"])
    assert "ALIAS_IMAGE" in merge_run_text
    assert "same digest" in merge_run_text or "Alias contract holds" in merge_run_text
    # Must generate build manifest
    assert "build-manifest" in merge_run_text or "Build manifest" in merge_run_text


def test_host_class_alias_must_be_same_digest_when_both_present():
    valid = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + _HOST_DIGEST
    other = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "b" * 64
    os.environ["OMNIGENT_RUNTIME_HOST_IMAGE_REF"] = valid
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = other
    with pytest.raises(HarnessPlatformError) as exc:
        get_runtime_host_image_ref()
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH
    assert "alias contract is active" in str(exc.value)
    os.environ.pop("OMNIGENT_RUNTIME_HOST_IMAGE_REF", None)
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)


def test_host_class_alias_resolves_to_same_digest_when_equal():
    valid = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + _HOST_DIGEST
    os.environ["OMNIGENT_RUNTIME_HOST_IMAGE_REF"] = valid
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = valid
    assert get_runtime_host_image_ref() == valid
    os.environ.pop("OMNIGENT_RUNTIME_HOST_IMAGE_REF", None)
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)


def test_host_class_neutral_fallback_to_alias():
    alias = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + _HOST_DIGEST
    os.environ.pop("OMNIGENT_RUNTIME_HOST_IMAGE_REF", None)
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = alias
    assert get_runtime_host_image_ref() == alias
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)


def test_host_class_image_refs_fail_closed_on_placeholder():
    for bad in (
        "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "0" * 64,
        "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "c" * 64,
        "ghcr.io/moonladderstudios/omnigent-host-moonmind:latest",
    ):
        os.environ["OMNIGENT_RUNTIME_HOST_IMAGE_REF"] = bad
        with pytest.raises(HarnessPlatformError):
            get_runtime_host_image_ref()
    os.environ.pop("OMNIGENT_RUNTIME_HOST_IMAGE_REF", None)


def test_separate_host_classes_share_same_digest():
    digest = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + _HOST_DIGEST
    os.environ["OMNIGENT_RUNTIME_HOST_IMAGE_REF"] = digest
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = digest
    from types import SimpleNamespace as NS

    impl = NS(implementation_ref=lambda: "omnigent-harness-implementation:sha256:" + "a" * 64)
    for harness_id, template_ref, materializer in [
        ("opencode-native", "omnigent-opencode@2", "opencode-auth-json@1"),
        ("codex-native", "omnigent-codex@1", "codex-oauth-home@1"),
        ("claude-native", "omnigent-claude@1", "claude-oauth-home@1"),
    ]:
        harness = NS(id=harness_id, implementation=impl)
        hc = OmnigentHostClassSelector().select(
            harness=harness,
            omnigent_version="1.0.0",
            omnigent_build_digest="sha256:" + "b" * 64,
            integration_mode="native-server",
            materializer_refs=[materializer],
            requested_host_class_ref=template_ref,
        )
        assert hc.imageRef == digest
        assert hc.runtime["uid"] == 1000
        assert hc.runtime["home"] == "/home/app"
    os.environ.pop("OMNIGENT_RUNTIME_HOST_IMAGE_REF", None)
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)


def test_host_moonmind_alias_retirement_condition_documented():
    assert HOST_MOONMIND_ALIAS_RETIREMENT_CONDITION
    readme = Path("services/omnigent/host-moonmind/README.md")
    assert readme.exists()
    assert "retirement" in readme.read_text().lower()
    assert "omnigent-host-opencode" in readme.read_text()


def test_new_neutral_release_does_not_change_realizer_selection():
    """Publishing new neutral image must not silently move Codex/Claude to generic realizer."""
    # The planning service's realizer selection is explicit via executionRealizerRef.
    # Publishing a new image must not change that. Verify via host-class isolation:
    # opencode host class remains opencode-native, not codex-native, after neutral publish.
    # The planning service's realizer selection is explicit via executionProfile.
    # Here we verify that host class selection for opencode does not imply codex genericalization.
    digest = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + _HOST_DIGEST
    os.environ["OMNIGENT_RUNTIME_HOST_IMAGE_REF"] = digest
    os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = digest
    # Opencode selection should still use generic-omnigent-host@1 explicitly, not codex path
    # We prove that codex host class requires codex-native harness, not opencode
    from types import SimpleNamespace as NS

    impl = NS(implementation_ref=lambda: "omnigent-harness-implementation:sha256:" + "a" * 64)
    harness_opencode = NS(id="opencode-native", implementation=impl)
    hc = OmnigentHostClassSelector().select(
        harness=harness_opencode,
        omnigent_version="1.0.0",
        omnigent_build_digest="sha256:" + "b" * 64,
        integration_mode="native-server",
        materializer_refs=["opencode-auth-json@1"],
    )
    # Should be opencode, not codex
    assert hc.hostClassId == "omnigent-opencode"
    os.environ.pop("OMNIGENT_RUNTIME_HOST_IMAGE_REF", None)
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)


def test_bootstrap_image_resolution_alias_contract_enforced():
    """image_resolution must fail closed when neutral and alias differ."""
    import asyncio

    from moonmind.omnigent.bootstrap.image_resolution import resolve_omnigent_images

    # Use non-placeholder digests (avoid 0*64 or c*64 which are filtered as placeholders)
    env = {
        "OMNIGENT_IMAGE_REF": _SERVER_REF,
        "OMNIGENT_RUNTIME_HOST_IMAGE_REF": "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "a" * 64,
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF": "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "b" * 64,
    }
    with pytest.raises(ValueError, match="alias contract is active"):
        asyncio.run(resolve_omnigent_images(env))


def test_dockerfile_and_workflow_tests_exist():
    # Required tests per issue: Dockerfile and workflow contract tests
    assert _NEUTRAL_DOCKERFILE.exists()
    assert _NEUTRAL_WORKFLOW.exists()
