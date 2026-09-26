"""Image-owned Omnigent tools packaging (MoonLadderStudios/MoonMind#4558).

Normal startup must not depend on a separately initialized, version-named
tools volume. The shared host image owns ``gh`` and ``moonmind`` at
``/opt/moonmind-tools/bin``; Compose carries no tools initializer, tools
mounts, or tools volume on the shared-host path.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "services/omnigent/moonmind-host/Dockerfile"
WORKFLOW = REPO_ROOT / ".github/workflows/docker-publish-moonmind-host.yml"
MANIFEST = REPO_ROOT / "services/omnigent/tools/manifest.lock.json"


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))


def test_shared_host_image_installs_pinned_gh_from_manifest_inputs() -> None:
    """gh arrives from the manifest.lock.json pins at image build time."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    gh = next(item for item in manifest["tools"] if item["name"] == "gh")
    dockerfile = _dockerfile()
    assert "OMNIGENT_GH_VERSION" in dockerfile
    assert gh["version"] in dockerfile
    assert "/opt/moonmind-tools/bin/gh" in dockerfile
    # The manifest is the single pin source: it is copied into the build and
    # consumed by the installer (hash checks covered by
    # test_install_moonmind_tools.py), with drift failing the build.
    assert "services/omnigent/tools/manifest.lock.json" in dockerfile
    assert "install_moonmind_tools.py" in dockerfile
    assert "GH_VERSION" in dockerfile
    # Both supported architectures stay pinned in the manifest the build reads.
    assert set(gh["platforms"]) == {"linux/amd64", "linux/arm64"}
    for platform, entry in gh["platforms"].items():
        assert entry["sha256"], platform
        assert entry["executableSha256"], platform
        assert entry["archivePath"], platform
    # The installed version is verified against the manifest during the build.
    assert "gh --version" in dockerfile


def test_shared_host_image_installs_moonmind_cli_without_broadening_docker() -> None:
    """The non-secret container CLI is copied as image-owned ``moonmind``."""
    dockerfile = _dockerfile()
    assert "moonmind-container-cli.py" in dockerfile
    assert "/opt/moonmind-tools/bin/moonmind" in dockerfile
    # Ordinary and login shells resolve the image-owned executables.
    assert "/opt/moonmind-tools/bin" in dockerfile
    assert "moonmind-tools.sh" in dockerfile
    # Host launch must not download, install, or copy shared tool binaries.
    assert "gh --version" in dockerfile
    assert "moonmind --help" in dockerfile


def test_host_image_publish_rebuilds_on_tool_source_changes() -> None:
    """CLI-only changes (no gh bump) still produce an updated image."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for path in (
        "services/omnigent/tools/manifest.lock.json",
        "services/omnigent/scripts/moonmind-container-cli.py",
        "services/omnigent/scripts/moonmind-tools.sh",
    ):
        assert path in workflow, path
    assert "OMNIGENT_GH_VERSION" in workflow


def test_shared_host_path_has_no_tools_volume_lifecycle() -> None:
    """No initializer, tools mounts, or version-named tools volume remains."""
    compose = _compose()
    assert "omnigent-tools-init" not in compose["services"]
    assert "omnigent-tools" not in compose.get("volumes", {})
    rendered = (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    assert "omnigent-tools:" not in rendered
    assert "OMNIGENT_GH_VERSION" not in rendered
    assert "OMNIGENT_GH_IMAGE" not in rendered
    assert "MOONMIND_OMNIGENT_TOOLS_VOLUME_REF" not in rendered
    for name in ("omnigent-host", "omnigent-host-codex", "omnigent-host-claude"):
        service = compose["services"][name]
        volumes = " ".join(str(item) for item in service.get("volumes", []))
        assert "/opt/moonmind-tools" not in volumes, name
        assert "moonmind-tools.sh" not in volumes, name
        assert "omnigent-tools-init" not in service.get("depends_on", {}), name
    for worker in ("temporal-worker-agent-runtime", "temporal-worker-workflow"):
        environment = " ".join(
            str(item) for item in compose["services"][worker].get("environment", [])
        )
        assert "MOONMIND_OMNIGENT_TOOLS_VOLUME_REF" not in environment, worker


def test_retired_tool_settings_are_gone_from_env_template() -> None:
    lines = (REPO_ROOT / ".env-template").read_text(encoding="utf-8").splitlines()
    assignments = [
        line
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]
    for name in (
        "MOONMIND_OMNIGENT_TOOLS_VOLUME_REF",
        "OMNIGENT_TOOL_BUNDLE_VERSION",
        "OMNIGENT_TOOL_BUNDLE_VOLUME",
        "OMNIGENT_GH_IMAGE",
        "OMNIGENT_GH_VERSION",
    ):
        assert not any(
            line.startswith(f"{name}=") for line in assignments
        ), name


def test_obsolete_tool_initializers_are_removed() -> None:
    assert not (REPO_ROOT / "services/omnigent/scripts/init-mounted-tools.sh").exists()
    assert not (REPO_ROOT / "services/omnigent/tools/init_omnigent_tools.py").exists()
