from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import stat
import tarfile

import pytest

from services.omnigent.tools import init_omnigent_tools as initializer


def _fixture_lock(tmp_path: Path, *, sha256: str | None = None) -> Path:
    executable = b"#!/bin/sh\necho fixture-tool 1.0\n"
    archive = tmp_path / "fixture.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("fixture/bin/fixture-tool")
        info.size = len(executable)
        tar.addfile(info, io.BytesIO(executable))
    digest = sha256 or hashlib.sha256(archive.read_bytes()).hexdigest()
    lock = {
        "schemaVersion": 1,
        "bundleVersion": "test-1",
        "tools": [
            {
                "name": "fixture-tool",
                "version": "1.0",
                "platforms": {
                    "linux/amd64": {
                        "url": archive.as_uri(),
                        "sha256": digest,
                        "archivePath": "fixture/bin/fixture-tool",
                    }
                },
                "path": "bin/fixture-tool",
                "versionProbe": ["--version"],
            }
        ],
    }
    path = tmp_path / "manifest.lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    return path


def test_initializer_publishes_verified_read_only_bundle_atomically(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(initializer, "_platform_key", lambda: "linux/amd64")
    output = tmp_path / "output"

    initializer.initialize(_fixture_lock(tmp_path), output)

    bundle = output / "bundle"
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["bundleVersion"] == "test-1"
    assert manifest["tools"][0]["path"] == "bin/fixture-tool"
    assert (output / "bin").readlink() == Path("bundle/bin")
    assert stat.S_IMODE((bundle / "bin/fixture-tool").stat().st_mode) == 0o555
    assert stat.S_IMODE((bundle / "manifest.json").stat().st_mode) == 0o444
    assert not list(output.glob(".attempt-*"))


def test_initializer_is_idempotent_and_repairs_manifest_mismatch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(initializer, "_platform_key", lambda: "linux/amd64")
    output = tmp_path / "output"
    lock_path = _fixture_lock(tmp_path)
    initializer.initialize(lock_path, output)
    initializer.initialize(lock_path, output)

    manifest_path = output / "bundle/manifest.json"
    manifest_path.chmod(0o644)
    manifest_path.write_text("{}\n", encoding="utf-8")
    initializer.initialize(lock_path, output)
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["bundleVersion"] == "test-1"


def test_runtime_manifest_reports_missing_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(initializer, "_platform_key", lambda: "linux/arm64")

    with pytest.raises(RuntimeError, match="no pinned artifact for linux/arm64"):
        initializer.initialize(_fixture_lock(tmp_path), tmp_path / "output")


def test_initializer_accepts_matching_concurrent_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(initializer, "_platform_key", lambda: "linux/amd64")
    output = tmp_path / "output"
    lock_path = _fixture_lock(tmp_path)
    winner = tmp_path / "winner-output"
    initializer.initialize(lock_path, winner)

    def publish_winner_then_fail(source, destination):
        shutil.copytree(winner / "bundle", destination)
        raise OSError("destination was published concurrently")

    monkeypatch.setattr(initializer.os, "replace", publish_winner_then_fail)
    initializer.initialize(lock_path, output)

    assert json.loads((output / "bundle/manifest.json").read_text())["bundleVersion"] == "test-1"


def test_initializer_rejects_bad_artifact_hash_without_publishing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(initializer, "_platform_key", lambda: "linux/amd64")
    output = tmp_path / "output"

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        initializer.initialize(_fixture_lock(tmp_path, sha256="0" * 64), output)

    assert not (output / "bundle").exists()
    assert not list(output.glob(".attempt-*"))


def test_initializer_rejects_volume_version_manifest_mismatch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OMNIGENT_TOOL_BUNDLE_VERSION", "different-version")
    monkeypatch.setattr(initializer, "_platform_key", lambda: "linux/amd64")

    with pytest.raises(RuntimeError, match="version does not match"):
        initializer.initialize(_fixture_lock(tmp_path), tmp_path / "output")
