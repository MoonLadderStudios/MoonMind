"""Bounded, side-effect-free validation for artifact-backed Omnigent bundles."""
from __future__ import annotations

import io
import json
import re
import tarfile
import zipfile
from typing import Any

MAX_BUNDLE_BYTES = 50 * 1024 * 1024
MAX_MEMBERS = 512
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
_MANIFEST_NAMES = {"omnigent-agent.json", "manifest.json"}
_FORBIDDEN_NAMES = {
    "compose.yml", "compose.yaml", "containerfile",
    "docker-compose.yml", "docker-compose.yaml",
}
_SECRET_PATTERN = re.compile(
    rb"(?:ghp_|github_pat_|AKIA|AIza|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"(?:token|password|client_secret)\s*[:=]\s*[\"']?[^\s\"']{8,})",
    re.IGNORECASE,
)


class BundleValidationError(ValueError):
    """A bundle cannot cross the managed import boundary."""


def _safe_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = normalized.split("/")
    if not normalized or normalized.startswith("/") or ".." in parts:
        raise BundleValidationError(f"unsafe bundle path: {name}")
    basename = parts[-1].lower()
    if basename in _FORBIDDEN_NAMES or basename.startswith("dockerfile"):
        raise BundleValidationError(f"host launch file is forbidden: {name}")
    return normalized


def _validate_manifest(payload: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleValidationError("bundle manifest must be valid JSON") from exc
    if not isinstance(manifest, dict):
        raise BundleValidationError("bundle manifest must be an object")
    if manifest.get("schemaVersion") != "omnigent.agent-bundle.v1":
        raise BundleValidationError("unsupported bundle manifest schemaVersion")
    if not isinstance(manifest.get("harness"), str) or not manifest["harness"].strip():
        raise BundleValidationError("bundle manifest requires harness")
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) and item for item in capabilities
    ):
        raise BundleValidationError("bundle manifest requires string capabilities")
    forbidden = {"dockerfile", "hostPath", "privileged", "credentials", "secrets", "setupCommand"}
    if forbidden.intersection(manifest):
        raise BundleValidationError("bundle manifest contains unmanaged runtime authority")
    return manifest


def validate_agent_bundle(data: bytes, content_type: str) -> dict[str, Any]:
    """Inspect a zip/tar bundle with strict count and expansion limits."""
    if not data or len(data) > MAX_BUNDLE_BYTES:
        raise BundleValidationError("bundle size is empty or exceeds the limit")
    members: list[tuple[str, bytes, int]] = []
    stream = io.BytesIO(data)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS:
                raise BundleValidationError("bundle contains too many files")
            if sum(info.file_size for info in infos) > MAX_EXPANDED_BYTES:
                raise BundleValidationError("expanded bundle exceeds the limit")
            for info in infos:
                name = _safe_name(info.filename)
                if info.is_dir():
                    continue
                # Unix file type bits: reject symlinks and executable files.
                mode = info.external_attr >> 16
                if (mode & 0o170000) == 0o120000 or mode & 0o111:
                    raise BundleValidationError(f"links and executable files are forbidden: {name}")
                members.append((name, archive.read(info), info.file_size))
    else:
        stream.seek(0)
        try:
            archive_context = tarfile.open(fileobj=stream, mode="r:*")
        except tarfile.TarError as exc:
            raise BundleValidationError(
                f"unsupported or malformed bundle content: {content_type}"
            ) from exc
        with archive_context as archive:
            infos = archive.getmembers()
            if len(infos) > MAX_MEMBERS:
                raise BundleValidationError("bundle contains too many files")
            if sum(info.size for info in infos) > MAX_EXPANDED_BYTES:
                raise BundleValidationError("expanded bundle exceeds the limit")
            for info in infos:
                name = _safe_name(info.name)
                if info.isdir():
                    continue
                if not info.isfile() or info.mode & 0o111:
                    raise BundleValidationError(f"links and executable files are forbidden: {name}")
                source = archive.extractfile(info)
                members.append((name, source.read() if source else b"", info.size))
    if sum(size for _, _, size in members) > MAX_EXPANDED_BYTES:
        raise BundleValidationError("expanded bundle exceeds the limit")
    manifests = [payload for name, payload, _ in members if name.split("/")[-1] in _MANIFEST_NAMES]
    if len(manifests) != 1:
        raise BundleValidationError("bundle requires exactly one manifest")
    if any(_SECRET_PATTERN.search(payload) for _, payload, _ in members):
        raise BundleValidationError("bundle contains secret-like material")
    manifest = _validate_manifest(manifests[0])
    return {
        "schemaVersion": manifest["schemaVersion"],
        "harness": manifest["harness"],
        "capabilities": sorted(set(manifest["capabilities"])),
        "license": manifest.get("license"),
        "fileCount": len(members),
        "expandedBytes": sum(size for _, _, size in members),
    }
