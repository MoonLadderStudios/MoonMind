#!/usr/bin/env python3
"""Resolve, authenticate and promote the separate repository-security image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "python:3.12-slim-bookworm"
REPOSITORIES = {
    "opengrep": "opengrep/opengrep",
    "gitleaks": "gitleaks/gitleaks",
    "trivy": "aquasecurity/trivy",
}
ARCHITECTURES = ("amd64", "arm64")
ROOT = Path(__file__).resolve().parents[2]
# Package the runners' imports, without schema-package reexports or the app.
RUNNER_FILES = (
    "security/repository_scanner_runner.py",
    "security/repository_scanners.py",
    "security/scanner_feeds.py",
    "security/image_assessment_runner.py",
    "security/image_assessments.py",
    "security/assessments.py",
    "schemas/repository_scanner_models.py",
    "schemas/agent_runtime_models.py",
    "schemas/managed_session_models.py",
    "schemas/container_job_models.py",
    "schemas/workload_models.py",
    "schemas/workspace_locator_models.py",
    "schemas/temporal_payload_policy.py",
    "schemas/_validation.py",
    "provider_profiles/model_tiers.py",
    "utils/logging.py",
)


class ImageError(RuntimeError):
    """A required release, verification or qualification input is unavailable."""


def command(*args) -> str:
    return subprocess.check_output([str(a) for a in args], text=True).strip()


def download(url: str, destination: Path) -> None:
    headers = {"User-Agent": "MoonMind-repository-security-image"}
    api_token = os.environ.get("GH_TOKEN", "")
    # Never forward the CI API token to release download redirects.
    if url.startswith("https://api.github.com/") and api_token:
        headers["Authorization"] = "Bearer " + api_token
    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=60
            ) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output)
            return
        except urllib.error.HTTPError as exc:
            # These upstream releases are public. A repository-scoped CI token
            # can be refused even when anonymous acquisition is available.
            if exc.code in {401, 403} and "Authorization" in headers and attempt < 2:
                headers.pop("Authorization")
                exc.close()
                continue
            if attempt == 2:
                detail = str(exc.reason)
                try:
                    payload = json.loads(exc.read(4096))
                    if isinstance(payload, dict):
                        detail = str(payload.get("message", detail))
                except (ValueError, OSError):
                    # Keep the HTTP reason when an upstream error body is unreadable.
                    pass
                finally:
                    exc.close()
                if api_token:
                    detail = detail.replace(api_token, "[redacted]")
                raise ImageError(
                    f"Upstream download failed: {url}: HTTP {exc.code}: {detail[:500]}"
                ) from exc
            exc.close()
            time.sleep(attempt + 1)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def fetch_json(url: str):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "response.json"
        download(url, path)
        return json.loads(path.read_text())


def resolve_inputs(
    *, opengrep="", gitleaks="", trivy="", base_image=DEFAULT_BASE
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]*", base_image):
        raise ImageError("base image must be a container image reference")
    inputs = {"schemaVersion": 1, "baseImage": base_image, "tools": {}}
    for tool, override in (
        ("opengrep", opengrep),
        ("gitleaks", gitleaks),
        ("trivy", trivy),
    ):
        repo = REPOSITORIES[tool]
        version = override if override.startswith("v") else "v" + override
        if override and not re.fullmatch(
            r"v\d+\.\d+\.\d+(?:-[A-Za-z0-9._-]+)?", version
        ):
            raise ImageError(f"{tool}: invalid release version")
        selector = "tags/" + version if override else "latest"
        release = fetch_json(f"https://api.github.com/repos/{repo}/releases/{selector}")
        if release.get("draft") or (not override and release.get("prerelease")):
            raise ImageError(f"{tool}: default must be a stable release")
        tag = release["tag_name"]
        if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[A-Za-z0-9._-]+)?", tag) or (
            override and tag != version
        ):
            raise ImageError(f"{tool}: unexpected release tag {tag!r}")
        available = {asset["name"]: asset for asset in release["assets"]}

        def asset(name, *, available=available, tool=tool, tag=tag, repo=repo):
            if name not in available:
                raise ImageError(
                    f"{tool} {tag}: required upstream asset missing: {name}"
                )
            data = available[name]
            expected = f"https://github.com/{repo}/releases/download/{tag}/{name}"
            if data["browser_download_url"] != expected:
                raise ImageError(f"{tool}: asset URL is outside the selected release")
            return {
                key: data[key]
                for key in ("name", "id", "browser_download_url", "digest")
                if key in data
            }

        selected = {}
        for arch in ARCHITECTURES:
            if tool == "opengrep":
                name = "opengrep_manylinux_" + ("x86" if arch == "amd64" else "aarch64")
                selected[arch] = {
                    "binary": asset(name),
                    "signature": asset(name + ".sig"),
                    "certificate": asset(name + ".cert"),
                }
            elif tool == "gitleaks":
                name = f"gitleaks_{tag[1:]}_linux_{'x64' if arch == 'amd64' else 'arm64'}.tar.gz"
                selected[arch] = {
                    "binary": asset(name),
                    "checksums": asset(f"gitleaks_{tag[1:]}_checksums.txt"),
                }
            else:
                name = f"trivy_{tag[1:]}_Linux-{'64bit' if arch == 'amd64' else 'ARM64'}.tar.gz"
                selected[arch] = {"binary": asset(name)}
                if name + ".sigstore.json" in available:
                    selected[arch]["bundle"] = asset(name + ".sigstore.json")
                else:
                    selected[arch].update(
                        signature=asset(name + ".sig"), certificate=asset(name + ".pem")
                    )
        inputs["tools"][tool] = {
            "repository": repo,
            "version": tag,
            "releaseId": release["id"],
            "assets": selected,
        }
    return inputs


def verify_signature(tool: str, tag: str, binary: Path, material: dict) -> None:
    repo = REPOSITORIES[tool]
    # Upstream uses release branches for Opengrep, tag refs for Trivy.
    ref = (
        rf"refs/(?:tags/{re.escape(tag)}|heads/release-{re.escape(tag)})"
        if tool == "opengrep"
        else rf"refs/tags/{re.escape(tag)}"
    )
    identity = rf"^https://github.com/{repo}/\.github/workflows/[^/]+\.ya?ml@{ref}$"
    args = [
        "cosign",
        "verify-blob",
        "--certificate-identity-regexp",
        identity,
        "--certificate-oidc-issuer",
        "https://token.actions.githubusercontent.com",
    ]
    if "bundle" in material and tool == "trivy":
        args += ["--bundle", material["bundle"]]
    else:
        args += [
            "--signature",
            material["signature"],
            "--certificate",
            material["certificate"],
        ]
    command(*args, binary)


def verify_checksum(binary: Path, checksums: str) -> None:
    actual = hashlib.sha256(binary.read_bytes()).hexdigest()
    matches = [
        line.split()[0]
        for line in checksums.splitlines()
        if len(line.split()) == 2 and line.split()[1].lstrip("*") == binary.name
    ]
    if matches != [actual]:
        raise ImageError(f"checksum missing or invalid for {binary.name}")


def install_binary(tool: str, archive: Path, destination: Path) -> None:
    target = destination / tool
    if tool == "opengrep":
        shutil.copyfile(archive, target)
    else:
        with tarfile.open(archive) as source:
            selected = [
                member for member in source if member.name == tool and member.isfile()
            ]
            if len(selected) != 1:
                raise ImageError(
                    f"{tool}: archive must contain one regular {tool} binary"
                )
            with source.extractfile(selected[0]) as stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output)
    target.chmod(0o755)


def stage_runner(destination: Path) -> None:
    package = destination / "moonmind"
    package.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "moonmind/__init__.py", package / "__init__.py")
    for relative in RUNNER_FILES:
        target = destination / "moonmind" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "moonmind" / relative, target)


def prepare(inputs: dict, arch: str, destination: Path, evidence: Path) -> None:
    if arch not in ARCHITECTURES:
        raise ImageError("only Linux amd64 and arm64 are supported")
    evidence.mkdir(parents=True, exist_ok=True)
    binaries, notices = destination / "bin", destination / "notices"
    binaries.mkdir(parents=True, exist_ok=True)
    notices.mkdir(exist_ok=True)
    inventory = {"architecture": arch, "baseImage": inputs["baseImage"], "tools": {}}
    for tool, release in inputs["tools"].items():
        downloads = evidence / tool
        downloads.mkdir(exist_ok=True)
        material = {}
        identities = {}
        for role, asset in release["assets"][arch].items():
            path = downloads / asset["name"]
            download(asset["browser_download_url"], path)
            digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            if asset.get("digest") and asset["digest"] != digest:
                raise ImageError(
                    f"{tool}: upstream asset digest mismatch: {asset['name']}"
                )
            material[role], identities[role] = path, {**asset, "resolvedDigest": digest}
        if tool in ("opengrep", "trivy"):
            verify_signature(tool, release["version"], material["binary"], material)
            verification = "cosign"
        else:
            verify_checksum(material["binary"], material["checksums"].read_text())
            verification = "upstream-https-checksums (upstream publishes no signature)"
        # Nothing enters the build context until its verification succeeds.
        install_binary(tool, material["binary"], binaries)
        license_files = ["LICENSE"]
        if tool == "opengrep":
            license_files.append("COPYRIGHT")
        if tool == "trivy":
            license_files.append("NOTICE")
        for name in license_files:
            download(
                f"https://raw.githubusercontent.com/{release['repository']}/{release['version']}/{name}",
                notices / f"{tool}-{name}",
            )
        if tool != "opengrep":
            with tarfile.open(material["binary"]) as archive:
                for index, member in enumerate(archive):
                    if member.isfile() and Path(member.name).name.upper() in {
                        "LICENSE",
                        "NOTICE",
                        "COPYRIGHT",
                        "COPYING",
                    }:
                        with archive.extractfile(member) as source, (
                            notices / f"{tool}-archive-{index}-{Path(member.name).name}"
                        ).open("wb") as output:
                            shutil.copyfileobj(source, output)
        inventory["tools"][tool] = {
            "version": release["version"],
            "releaseId": release["releaseId"],
            "assets": identities,
            "verification": verification,
            "source": f"https://github.com/{release['repository']}/archive/refs/tags/{release['version']}.tar.gz",
        }
    # Retain corresponding Opengrep source alongside the publication artifacts.
    download(
        inventory["tools"]["opengrep"]["source"], evidence / "opengrep-source.tar.gz"
    )
    stage_runner(destination / "python")
    (destination / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    (notices / "sources.json").write_text(
        json.dumps({t: d["source"] for t, d in inventory["tools"].items()}, indent=2)
        + "\n"
    )


def verification_negatives(inputs: dict, arch: str, evidence: Path) -> None:
    """Exercise real verifier rejection while preserving the acquired originals."""
    results = []
    for tool in ("opengrep", "trivy"):
        release = inputs["tools"][tool]
        material = {
            role: evidence / tool / asset["name"]
            for role, asset in release["assets"][arch].items()
        }
        for case in (
            "tampered-artifact",
            "missing-material",
            "invalid-material",
            "wrong-identity",
        ):
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                selected = dict(material)
                tag = release["version"]
                if case == "tampered-artifact":
                    path = work / "tampered"
                    path.write_bytes(material["binary"].read_bytes() + b"tampered")
                    selected["binary"] = path
                elif case in ("missing-material", "invalid-material"):
                    role = "bundle" if "bundle" in selected else "signature"
                    path = work / "verification-material"
                    if case == "invalid-material":
                        path.write_text("invalid verification material")
                    selected[role] = path
                else:
                    tag = "v0.0.0-wrong-identity"
                try:
                    verify_signature(tool, tag, selected["binary"], selected)
                except subprocess.CalledProcessError as error:
                    results.append(
                        {
                            "tool": tool,
                            "case": case,
                            "rejected": True,
                            "exitCode": error.returncode,
                        }
                    )
                else:
                    raise ImageError(f"{tool}: {case} unexpectedly verified")
                (evidence / "verification-negatives.json").write_text(
                    json.dumps(results, indent=2) + "\n"
                )


def promote(records: list[dict], image: str) -> None:
    if len(records) != 2 or {r.get("architecture") for r in records} != set(
        ARCHITECTURES
    ):
        raise ImageError("promotion requires one qualified image per architecture")
    for record in records:
        if record.get("qualification") != "passed" or not re.fullmatch(
            r"[a-f0-9]{64}", record.get("sbomSha256", "")
        ):
            raise ImageError(
                "qualification or retained SBOM missing; working default unchanged"
            )
        if not re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", record.get("image", "")):
            raise ImageError(
                "qualification must identify the published candidate digest"
            )
    versions = [
        {t: d["version"] for t, d in r["inventory"]["tools"].items()} for r in records
    ]
    if versions[0] != versions[1]:
        raise ImageError("architecture builds resolved different tool versions")
    # One manifest write, after every check; no default tag is used for staging.
    command(
        "docker",
        "buildx",
        "imagetools",
        "create",
        "--tag",
        image + ":latest",
        *(r["image"] for r in records),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    resolve = commands.add_parser("resolve")
    for tool in REPOSITORIES:
        resolve.add_argument("--" + tool + "-version", default="")
    resolve.add_argument("--base-image", default=DEFAULT_BASE)
    resolve.add_argument("--output", type=Path, required=True)
    stage = commands.add_parser("prepare")
    stage.add_argument("--inputs", type=Path, required=True)
    stage.add_argument("--architecture", choices=ARCHITECTURES, required=True)
    stage.add_argument("--destination", type=Path, required=True)
    stage.add_argument("--evidence", type=Path, required=True)
    negative = commands.add_parser("verification-negatives")
    negative.add_argument("--inputs", type=Path, required=True)
    negative.add_argument("--architecture", choices=ARCHITECTURES, required=True)
    negative.add_argument("--evidence", type=Path, required=True)
    publish = commands.add_parser("promote")
    publish.add_argument("--records", type=Path, nargs="+", required=True)
    publish.add_argument("--image", required=True)
    args = parser.parse_args()
    if args.action == "resolve":
        result = resolve_inputs(
            opengrep=args.opengrep_version,
            gitleaks=args.gitleaks_version,
            trivy=args.trivy_version,
            base_image=args.base_image,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    elif args.action == "prepare":
        prepare(
            json.loads(args.inputs.read_text()),
            args.architecture,
            args.destination,
            args.evidence,
        )
    elif args.action == "verification-negatives":
        verification_negatives(
            json.loads(args.inputs.read_text()), args.architecture, args.evidence
        )
    else:
        promote([json.loads(path.read_text()) for path in args.records], args.image)


if __name__ == "__main__":
    main()
