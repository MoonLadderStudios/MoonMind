#!/usr/bin/env python3
"""Publish the pinned Omnigent CLI bundle into a version-owned volume."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request


MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
PROBE_TIMEOUT_SECONDS = 10
PLATFORM_MACHINES = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _platform_key() -> str:
    machine = PLATFORM_MACHINES.get(platform.machine().lower())
    if os.name != "posix" or not machine:
        raise RuntimeError(f"unsupported tool-bundle platform: {os.name}/{platform.machine()}")
    return f"linux/{machine}"


def _safe_relative_path(value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"tool path must be a safe relative path: {value!r}")
    return Path(*path.parts)


def _download(url: str, destination: Path, expected_sha256: str) -> None:
    digest = hashlib.sha256()
    total = 0
    with (
        urllib.request.urlopen(url, timeout=30) as response,
        destination.open("wb") as output,
    ):
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ARTIFACT_BYTES:
                raise RuntimeError(f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes")
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != expected_sha256:
        raise RuntimeError(f"SHA-256 mismatch for {url}")


def _extract_executable(archive: Path, archive_path: str, destination: Path) -> None:
    with tarfile.open(archive, mode="r:gz") as bundle:
        member = bundle.getmember(archive_path)
        if not member.isfile():
            raise RuntimeError(f"archive member is not a regular file: {archive_path}")
        source = bundle.extractfile(member)
        if source is None:
            raise RuntimeError(f"cannot read archive member: {archive_path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source, destination.open("wb") as output:
            shutil.copyfileobj(source, output)
    destination.chmod(0o555)


def _runtime_manifest(lock: dict, platform_key: str) -> dict:
    tools = []
    for tool in lock.get("tools", []):
        selected = tool["platforms"][platform_key]
        tools.append(
            {
                "name": tool["name"],
                "version": tool["version"],
                "platform": platform_key,
                "sha256": selected["sha256"],
                "path": tool["path"],
                "versionProbe": tool["versionProbe"],
            }
        )
    return {
        "schemaVersion": lock["schemaVersion"],
        "bundleVersion": lock["bundleVersion"],
        "tools": tools,
    }


def _manifest_bytes(manifest: dict) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _validate_completed(bundle: Path, manifest: dict) -> None:
    manifest_path = bundle / "manifest.json"
    if not manifest_path.is_file() or manifest_path.read_bytes() != _manifest_bytes(manifest):
        raise RuntimeError("completed tool bundle manifest does not match pinned manifest")
    for tool in manifest["tools"]:
        executable = bundle / _safe_relative_path(tool["path"])
        mode = stat.S_IMODE(executable.stat().st_mode)
        if not executable.is_file() or mode != 0o555:
            raise RuntimeError(f"invalid executable permissions for {tool['path']}: {mode:o}")
        subprocess.run(
            [str(executable), *tool["versionProbe"]],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT_SECONDS,
        )


def initialize(lock_path: Path, output_root: Path) -> None:
    lock = _read_json(lock_path)
    expected_bundle_version = os.environ.get("OMNIGENT_TOOL_BUNDLE_VERSION")
    if expected_bundle_version and lock.get("bundleVersion") != expected_bundle_version:
        raise RuntimeError(
            "configured tool bundle version does not match the pinned manifest"
        )
    platform_key = _platform_key()
    manifest = _runtime_manifest(lock, platform_key)
    completed = output_root / "bundle"
    if completed.exists():
        _validate_completed(completed, manifest)
        return

    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".attempt-", dir=output_root))
    try:
        for index, tool in enumerate(lock["tools"]):
            selected = tool["platforms"].get(platform_key)
            if selected is None:
                raise RuntimeError(f"{tool['name']} has no pinned artifact for {platform_key}")
            archive = staging / f"artifact-{index}.tar.gz"
            _download(selected["url"], archive, selected["sha256"])
            executable = staging / _safe_relative_path(tool["path"])
            _extract_executable(archive, selected["archivePath"], executable)
            archive.unlink()
            subprocess.run(
                [str(executable), *tool["versionProbe"]],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PROBE_TIMEOUT_SECONDS,
            )
        (staging / "manifest.json").write_bytes(_manifest_bytes(manifest))
        (staging / "manifest.json").chmod(0o444)
        directories = (path for path in staging.rglob("*") if path.is_dir())
        for directory in sorted(directories, reverse=True):
            directory.chmod(0o555)
        staging.chmod(0o555)
        os.replace(staging, completed)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


if __name__ == "__main__":
    initialize(
        Path(
            os.environ.get(
                "OMNIGENT_TOOLS_LOCK",
                "/opt/moonmind-tools-init/manifest.lock.json",
            )
        ),
        Path(os.environ.get("OMNIGENT_TOOLS_OUTPUT", "/output")),
    )
