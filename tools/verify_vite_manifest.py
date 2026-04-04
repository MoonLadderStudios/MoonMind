#!/usr/bin/env python3
"""Verify the shared Mission Control Vite manifest contract (CI / local).

Asserts frontend/vite.config.ts defines exactly one shared ``mission-control``
entrypoint and that its manifest-recorded files exist under
api_service/static/task_dashboard/dist/.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_ENTRYPOINT = "mission-control"


def expected_entrypoints() -> list[str]:
    vite_config = ROOT / "frontend" / "vite.config.ts"
    if not vite_config.is_file():
        print(f"Vite config not found: {vite_config}", file=sys.stderr)
        sys.exit(1)
    text = vite_config.read_text(encoding="utf-8")
    keys = re.findall(r"'([a-z0-9-]+)'\s*:\s*resolve\s*\(", text)
    if not keys:
        print(
            "Could not parse rollup input keys from frontend/vite.config.ts",
            file=sys.stderr,
        )
        sys.exit(1)
    names = sorted(set(keys))
    if names != [SHARED_ENTRYPOINT]:
        print(
            "Expected frontend/vite.config.ts to define exactly one shared "
            f"Mission Control entrypoint: {SHARED_ENTRYPOINT!r}. Found: {names!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    return names


def _file_refs(meta: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    js = meta.get("file")
    if isinstance(js, str):
        out.append(("js", js))
    for css in meta.get("css") or []:
        if isinstance(css, str):
            out.append(("css", css))
    return out


def main() -> int:
    names = expected_entrypoints()
    manifest_path = (
        ROOT
        / "api_service"
        / "static"
        / "task_dashboard"
        / "dist"
        / ".vite"
        / "manifest.json"
    )
    if not manifest_path.is_file():
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dist_root = manifest_path.parent.parent
    errors: list[str] = []

    for name in names:
        key = f"entrypoints/{name}.tsx"
        if key not in manifest:
            errors.append(f"Missing manifest key: {key}")
            continue
        meta = manifest[key]
        if not isinstance(meta, dict):
            errors.append(f"Manifest entry {key!r} must be an object")
            continue
        for label, relpath in _file_refs(meta):
            path = dist_root / relpath
            if not path.is_file():
                errors.append(
                    f"Missing {label} file for {key} (expected {path.relative_to(ROOT)})"
                )

    for key, meta in manifest.items():
        if not isinstance(meta, dict):
            continue
        rel = meta.get("file")
        if isinstance(rel, str):
            path = dist_root / rel
            if not path.is_file():
                errors.append(f"Missing chunk for manifest key {key!r}: {rel}")

    if errors:
        print("Vite manifest verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        f"OK: shared Mission Control entrypoint {SHARED_ENTRYPOINT!r}; manifest {manifest_path.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
