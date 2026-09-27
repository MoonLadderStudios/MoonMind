"""Build-time image-owned tools installer (MoonLadderStudios/MoonMind#4558).

The shared host image owns ``gh`` and ``moonmind``. The Dockerfile runs this
helper at build time against the single pin source
(``services/omnigent/tools/manifest.lock.json``); host launch never downloads,
installs, or copies shared tool binaries.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import stat
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "services/omnigent/moonmind-host/install_moonmind_tools.py"
REAL_MANIFEST = REPO_ROOT / "services/omnigent/tools/manifest.lock.json"
REAL_CLI = REPO_ROOT / "services/omnigent/scripts/moonmind-container-cli.py"
REAL_PROFILE = REPO_ROOT / "services/omnigent/scripts/moonmind-tools.sh"


def _helper():
    spec = importlib.util.spec_from_file_location(
        "install_moonmind_tools", HELPER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_tree(tmp_path: Path, *, version: str = "9.9.9"):
    """Manifest + archive + CLI fixture mirroring manifest.lock.json shape."""
    gh_binary = b"#!/bin/sh\necho \"gh version " + version.encode() + b" (test)\"\n"
    archive = tmp_path / "gh.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("gh/bin/gh")
        info.size = len(gh_binary)
        tar.addfile(info, io.BytesIO(gh_binary))
    cli = b"#!/bin/sh\necho \"usage: moonmind [options] (test cli)\"\n"
    cli_source = tmp_path / "moonmind-container-cli.py"
    cli_source.write_bytes(cli)
    manifest = {
        "schemaVersion": 1,
        "bundleVersion": f"gh-{version}-container-v1",
        "tools": [
            {
                "name": "gh",
                "version": version,
                "platforms": {
                    "linux/amd64": {
                        "url": archive.as_uri(),
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                        "executableSha256": hashlib.sha256(gh_binary).hexdigest(),
                        "archivePath": "gh/bin/gh",
                    }
                },
                "path": "bin/gh",
                "versionProbe": ["--version"],
            },
            {
                "name": "docker",
                "version": "container-v1",
                "sourcePath": "moonmind-container-cli.py",
                "platforms": {
                    "linux/amd64": {
                        "executableSha256": hashlib.sha256(cli).hexdigest()
                    }
                },
                "path": "bin/moonmind",
                "versionProbe": ["--help"],
            },
        ],
    }
    manifest_path = tmp_path / "manifest.lock.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, cli_source


def test_platform_selection_matches_manifest_keys() -> None:
    helper = _helper()
    assert helper.platform_key("x86_64") == "linux/amd64"
    assert helper.platform_key("aarch64") == "linux/arm64"
    assert helper.platform_key("arm64") == "linux/arm64"
    with pytest.raises(RuntimeError):
        helper.platform_key("riscv64")


def test_install_produces_image_owned_read_only_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = _helper()
    manifest_path, cli_source = _fixture_tree(tmp_path)
    monkeypatch.setattr(helper, "platform_key", lambda: "linux/amd64")
    output = tmp_path / "opt" / "moonmind-tools"
    profile_target = tmp_path / "etc-profile-d" / "moonmind-tools.sh"

    helper.install(
        manifest_path,
        output,
        cli_source=cli_source,
        profile_source=REAL_PROFILE,
        profile_target=profile_target,
    )

    gh = output / "bin" / "gh"
    moonmind = output / "bin" / "moonmind"
    assert gh.is_file() and moonmind.is_file()
    assert stat.S_IMODE(gh.stat().st_mode) == 0o555
    assert stat.S_IMODE(moonmind.stat().st_mode) == 0o555
    assert stat.S_IMODE(profile_target.stat().st_mode) == 0o444
    image_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert image_manifest["bundleVersion"] == "gh-9.9.9-container-v1"
    assert image_manifest["tools"][0] == {
        "name": "gh",
        "version": "9.9.9",
        "path": "bin/gh",
        "versionProbe": ["--version"],
    }
    assert image_manifest["tools"][1]["path"] == "bin/moonmind"


def test_install_rejects_archive_hash_mismatch(tmp_path: Path, monkeypatch) -> None:
    helper = _helper()
    manifest_path, cli_source = _fixture_tree(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["tools"][0]["platforms"]["linux/amd64"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(helper, "platform_key", lambda: "linux/amd64")
    with pytest.raises(RuntimeError, match="[Ss][Hh][Aa].*mismatch|hash"):
        helper.install(
            manifest_path,
            tmp_path / "out",
            cli_source=cli_source,
            profile_source=REAL_PROFILE,
            profile_target=tmp_path / "profile.sh",
        )


def test_install_rejects_cli_source_drift(tmp_path: Path, monkeypatch) -> None:
    helper = _helper()
    manifest_path, cli_source = _fixture_tree(tmp_path)
    cli_source.write_bytes(b"#!/usr/bin/env python3\nprint('tampered')\n")
    monkeypatch.setattr(helper, "platform_key", lambda: "linux/amd64")
    with pytest.raises(RuntimeError, match="[Ss][Hh][Aa].*mismatch|drift"):
        helper.install(
            manifest_path,
            tmp_path / "out",
            cli_source=cli_source,
            profile_source=REAL_PROFILE,
            profile_target=tmp_path / "profile.sh",
        )


def test_version_override_must_match_manifest(tmp_path: Path, monkeypatch) -> None:
    helper = _helper()
    manifest_path, cli_source = _fixture_tree(tmp_path, version="9.9.9")
    monkeypatch.setattr(helper, "platform_key", lambda: "linux/amd64")
    with pytest.raises(RuntimeError, match="[Dd]rift|does not match"):
        helper.install(
            manifest_path,
            tmp_path / "out",
            cli_source=cli_source,
            profile_source=REAL_PROFILE,
            profile_target=tmp_path / "profile.sh",
            gh_version_override="1.2.3",
        )


def test_real_manifest_and_cli_sources_are_installable_inputs() -> None:
    """The repository's actual pins stay usable: both arches pinned, CLI present."""
    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    gh = next(item for item in manifest["tools"] if item["name"] == "gh")
    assert set(gh["platforms"]) == {"linux/amd64", "linux/arm64"}
    docker_tool = next(item for item in manifest["tools"] if item["name"] == "docker")
    assert docker_tool["path"] == "bin/moonmind"
    assert REAL_CLI.is_file()
    assert REAL_PROFILE.is_file()
