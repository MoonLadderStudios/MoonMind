#!/usr/bin/env python3
"""Install image-owned Omnigent helper tools at shared-host image build time.

MoonLadderStudios/MoonMind#4558: the shared host image owns ``gh`` and the
non-secret MoonMind container CLI at ``/opt/moonmind-tools/bin``. The single
pin source is ``services/omnigent/tools/manifest.lock.json``; an explicit
``GH_VERSION`` override must match the manifest or the build fails instead of
publishing drifted tools. Host launch never downloads, installs, or copies
shared tool binaries.

This helper runs inside ``docker build`` (see
``services/omnigent/moonmind-host/Dockerfile``). It is not part of the runtime
image contract beyond the files it installs.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath


MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
PROBE_TIMEOUT_SECONDS = 30

_PLATFORM_MACHINES = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def platform_key(machine: str | None = None) -> str:
    """Map a kernel machine name to a manifest platform key."""

    arch = _PLATFORM_MACHINES.get((machine or platform.machine()).lower())
    if not arch:
        raise RuntimeError(
            f"unsupported tool-bundle platform: {machine or platform.machine()}"
        )
    return f"linux/{arch}"


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _download(url: str, destination: Path, expected_sha256: str) -> None:
    digest = hashlib.sha256()
    total = 0
    with (
        urllib.request.urlopen(url, timeout=60) as response,
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
    member_path = PurePosixPath(archive_path)
    if member_path.is_absolute() or ".." in member_path.parts or not member_path.parts:
        raise ValueError(f"archive member must be a safe relative path: {archive_path!r}")
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


def _run_probe(executable: Path, args: list[str], *, tool: str) -> str:
    try:
        completed = subprocess.run(
            [str(executable), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if isinstance(exc, PermissionError):
            # Sandbox filesystems (noexec /tmp) refuse direct script exec while
            # still allowing interpreter reads; the build filesystem is
            # exec-capable. Retry through /bin/sh so the version assertion
            # itself is still exercised. File modes are asserted separately.
            try:
                completed = subprocess.run(
                    ["/bin/sh", str(executable), *args],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=PROBE_TIMEOUT_SECONDS,
                    check=False,
                    text=True,
                )
            except (OSError, subprocess.SubprocessError) as retry_exc:
                raise RuntimeError(
                    f"tool probe failed for {tool}: {retry_exc}"
                ) from retry_exc
            if completed.returncode != 0:
                raise RuntimeError(
                    f"tool probe failed for {tool} (exit {completed.returncode})"
                )
            return completed.stdout or ""
        raise RuntimeError(f"tool probe failed for {tool}: {exc}") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"tool probe failed for {tool} (exit {completed.returncode})")
    return completed.stdout or ""


def install(
    manifest_path: Path | str,
    output_root: Path | str,
    *,
    cli_source: Path | str,
    profile_source: Path | str,
    profile_target: Path | str,
    gh_version_override: str | None = None,
    arch_machine: str | None = None,
    run_probes: bool = True,
) -> dict:
    """Install gh + moonmind from manifest pins into an image-owned layout."""

    manifest_file = Path(manifest_path)
    output = Path(output_root)
    cli_file = Path(cli_source)
    lock = _read_json(manifest_file)
    tools = lock.get("tools")
    if not isinstance(tools, list):
        raise RuntimeError(f"{manifest_file} has no tool list")
    by_name = {
        str(item.get("name") or ""): item
        for item in tools
        if isinstance(item, dict) and str(item.get("name") or "")
    }
    gh_entry = by_name.get("gh")
    docker_entry = by_name.get("docker")
    if not isinstance(gh_entry, dict) or not isinstance(docker_entry, dict):
        raise RuntimeError(f"{manifest_file} must pin gh and docker tools")

    gh_version = str(gh_entry.get("version") or "").strip()
    if not gh_version:
        raise RuntimeError(f"{manifest_file} gh entry has no version")
    if gh_version_override and gh_version_override.strip() != gh_version:
        raise RuntimeError(
            "configured gh version does not match the pinned manifest "
            f"(override {gh_version_override.strip()} != manifest {gh_version})"
        )
    expected_bundle = f"gh-{gh_version}-container-v1"
    if str(lock.get("bundleVersion") or "") != expected_bundle:
        raise RuntimeError(
            f"{manifest_file} bundleVersion does not match gh {gh_version}"
        )

    key = platform_key() if arch_machine is None else platform_key(arch_machine)
    gh_platforms = gh_entry.get("platforms")
    if not isinstance(gh_platforms, dict) or key not in gh_platforms:
        raise RuntimeError(f"gh has no pinned artifact for {key}")
    selected = gh_platforms[key]
    if not isinstance(selected, dict):
        raise RuntimeError(f"gh platform entry for {key} is malformed")
    for field in ("url", "sha256", "executableSha256", "archivePath"):
        if not selected.get(field):
            raise RuntimeError(f"gh platform entry for {key} is missing {field}")

    bin_dir = output / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="moonmind-tools-"))
    try:
        archive = tmp_dir / "gh.tar.gz"
        _download(str(selected["url"]), archive, str(selected["sha256"]))
        gh_out = bin_dir / "gh"
        _extract_executable(archive, str(selected["archivePath"]), gh_out)
        digest = hashlib.sha256(gh_out.read_bytes()).hexdigest()
        if digest != str(selected["executableSha256"]):
            raise RuntimeError("SHA-256 mismatch for installed gh executable")
        gh_out.chmod(0o555)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    docker_platforms = docker_entry.get("platforms")
    if not isinstance(docker_platforms, dict) or key not in docker_platforms:
        raise RuntimeError(f"docker tool has no pinned entry for {key}")
    expected_cli_sha = str(
        (docker_platforms[key] or {}).get("executableSha256") or ""
    ).strip()
    if not expected_cli_sha:
        raise RuntimeError(f"docker tool entry for {key} has no executable digest")
    cli_bytes = cli_file.read_bytes()
    if hashlib.sha256(cli_bytes).hexdigest() != expected_cli_sha:
        raise RuntimeError(
            "SHA-256 drift for moonmind-container-cli.py: "
            "update manifest.lock.json in the same change"
        )
    moonmind_out = bin_dir / "moonmind"
    moonmind_out.write_bytes(cli_bytes)
    moonmind_out.chmod(0o555)

    image_manifest = {
        "schemaVersion": lock.get("schemaVersion", 1),
        "bundleVersion": expected_bundle,
        "tools": [
            {
                "name": str(item.get("name") or ""),
                "version": str(item.get("version") or ""),
                "path": str(item.get("path") or ""),
                "versionProbe": list(item.get("versionProbe") or []),
            }
            for item in tools
        ],
    }
    manifest_out = output / "manifest.json"
    manifest_out.write_text(
        json.dumps(image_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest_out.chmod(0o444)

    profile_text = Path(profile_source).read_text(encoding="utf-8")
    target = Path(profile_target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(profile_text, encoding="utf-8")
    target.chmod(0o444)

    # Image-owned and unwritable by the runtime user: directories keep
    # traversal/search bits, files keep their explicit modes.
    for directory in sorted(
        (path for path in output.rglob("*") if path.is_dir()),
        reverse=True,
    ):
        directory.chmod(0o555)
    output.chmod(0o555)

    if run_probes:
        gh_probe_args = gh_entry.get("versionProbe")
        if (
            not isinstance(gh_probe_args, list)
            or not gh_probe_args
            or any(not isinstance(arg, str) or not arg for arg in gh_probe_args)
        ):
            raise RuntimeError("gh entry has no valid version probe")
        observed = _run_probe(bin_dir / "gh", list(gh_probe_args), tool="gh")
        if gh_version not in observed:
            raise RuntimeError(
                f"installed gh does not report pinned version {gh_version}"
            )
        cli_probe_args = docker_entry.get("versionProbe")
        if (
            not isinstance(cli_probe_args, list)
            or not cli_probe_args
            or any(not isinstance(arg, str) or not arg for arg in cli_probe_args)
        ):
            raise RuntimeError("docker tool entry has no valid version probe")
        observed_cli = _run_probe(bin_dir / "moonmind", list(cli_probe_args), tool="moonmind")
        lowered = observed_cli.lower()
        if not observed_cli.strip() or not (
            "usage:" in lowered or "moonmind" in lowered or len(observed_cli) > 20
        ):
            raise RuntimeError("installed moonmind probe returned no usable output")

    return image_manifest


def main() -> None:
    manifest = Path(os.environ.get("MANIFEST_PATH", "/tmp/manifest.lock.json"))
    output = Path(os.environ.get("OUTPUT_ROOT", "/opt/moonmind-tools"))
    cli = Path(os.environ.get("CLI_SOURCE", "/tmp/moonmind-container-cli.py"))
    profile_source = Path(
        os.environ.get("PROFILE_SOURCE", "/tmp/moonmind-tools.sh")
    )
    profile_target = Path(
        os.environ.get("PROFILE_TARGET", "/etc/profile.d/moonmind-tools.sh")
    )
    override = (os.environ.get("GH_VERSION") or "").strip() or None
    installed = install(
        manifest,
        output,
        cli_source=cli,
        profile_source=profile_source,
        profile_target=profile_target,
        gh_version_override=override,
    )
    print(f"installed image-owned tools bundle {installed['bundleVersion']}")


if __name__ == "__main__":
    main()
