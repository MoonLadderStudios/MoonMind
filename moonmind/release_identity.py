"""Content identity for an image-owned, coherent MoonMind release.

This standard-library module also runs in Docker builds and host update tools.
Development source overlays explicitly invalidate immutable release authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

RELEASE_FILE = ".moonmind-release.json"
RELEASE_ROOTS = (
    "moonmind",
    "api_service",
    "services",
    ".agents",
    "pr_resolver_core",
    "config",
    "docs",
    "tools",
    "omnigent",
    "init_db",
    "release",
    "pyproject.toml",
    "poetry.lock",
    "package.json",
    "package-lock.json",
)


def build_release(root: Path, revision: str = "") -> dict:
    digest = hashlib.sha256()
    # The verified source revision distinguishes semantic build changes even
    # when they do not alter a copied runtime file (for example build tooling).
    digest.update(revision.encode() + b"\0")
    count = 0
    for name in RELEASE_ROOTS:
        source = root / name
        paths = [source] if source.is_file() else sorted(source.rglob("*"))
        for path in paths:
            if not path.is_file() or any(
                part in {"__pycache__", ".git", "node_modules"} for part in path.parts
            ):
                continue
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
            count += 1
    if not count:
        raise ValueError("A release must contain executable inputs")
    return {
        "schemaVersion": "moonmind-release/v1",
        "digest": "sha256:" + digest.hexdigest(),
        "sourceRevision": revision or None,
        "fileCount": count,
        "roots": list(RELEASE_ROOTS),
    }


def source_overlaid(root: Path, mountinfo: str | None = None) -> bool:
    if mountinfo is None:
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text()
        except OSError:
            return True  # No evidence of image-owned storage on this host.
    source_roots = [root.resolve() / name for name in RELEASE_ROOTS]
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) < 6:
            continue
        mount = Path(fields[4].replace("\\040", " ").replace("\\134", "\\"))
        if mount == Path("/"):
            continue
        if any(
            mount == source or mount in source.parents or source in mount.parents
            for source in source_roots
        ):
            return True
    return False


def installed_release(root: Path | None = None) -> dict | None:
    root = root or Path(__file__).resolve().parent.parent
    try:
        payload = json.loads((root / RELEASE_FILE).read_text())
    except (OSError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schemaVersion") != "moonmind-release/v1"
        or re.fullmatch(r"sha256:[0-9a-f]{64}", str(payload.get("digest"))) is None
        or payload.get("roots") != list(RELEASE_ROOTS)
        or source_overlaid(root)
    ):
        return None
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--revision", default="")
    args = parser.parse_args()
    (args.root / RELEASE_FILE).write_text(
        json.dumps(build_release(args.root, args.revision), sort_keys=True) + "\n"
    )
