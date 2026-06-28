#!/usr/bin/env python3
from __future__ import annotations

import argparse
import filecmp
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "vendor" / "moonspec"
LOCAL_UNEXPECTED = (
    ".specify/templates/constitution-template.md",
    ".specify/templates/commands/align.md",
    ".specify/templates/commands/breakdown.md",
    ".specify/templates/commands/implement.md",
    ".specify/templates/commands/plan.md",
    ".specify/templates/commands/specify.md",
    ".specify/templates/commands/tasks.md",
    ".specify/templates/commands/verify.md",
)


@dataclass(frozen=True)
class PlannedFile:
    source: Path
    source_rel: Path
    target: Path
    managed_root: Path


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def _projection_path(source_root: Path, projection_name: str) -> Path:
    bundle_root = source_root / "bundle"
    manifest = _load_yaml(bundle_root / "moonspec.bundle.yaml")
    projections = manifest.get("projections")
    if not isinstance(projections, dict) or projection_name not in projections:
        raise ValueError(f"unknown MoonSpec projection {projection_name!r}")
    projection = projections[projection_name]
    if not isinstance(projection, dict) or not isinstance(projection.get("path"), str):
        raise ValueError(f"projection {projection_name!r} must define path")
    return bundle_root / projection["path"]


def _planned_files(
    source_root: Path,
    projection_name: str,
) -> tuple[list[PlannedFile], list[str]]:
    bundle_root = source_root / "bundle"
    projection_path = _projection_path(source_root, projection_name)
    recipe = _load_yaml(projection_path)
    mappings = recipe.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError(f"{projection_path} must define mappings")

    files: list[PlannedFile] = []
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise ValueError(f"{projection_path} mappings must be mappings")
        mode = mapping.get("mode")
        source = bundle_root / str(mapping.get("from", ""))
        target = REPO_ROOT / str(mapping.get("to", ""))
        if mode == "file":
            if not source.is_file():
                raise ValueError(f"mapped source file does not exist: {source}")
            files.append(
                PlannedFile(
                    source=source,
                    source_rel=source.relative_to(bundle_root),
                    target=target,
                    managed_root=target,
                )
            )
        elif mode == "directory":
            if not source.is_dir():
                raise ValueError(f"mapped source directory does not exist: {source}")
            for child in sorted(source.rglob("*")):
                if child.is_file():
                    target_file = target / child.relative_to(source)
                    top_level = child.relative_to(source).parts[0]
                    files.append(
                        PlannedFile(
                            source=child,
                            source_rel=child.relative_to(bundle_root),
                            target=target_file,
                            managed_root=target / top_level,
                        )
                    )
        else:
            raise ValueError(f"unsupported projection mapping mode {mode!r}")

    unexpected = list(recipe.get("unexpectedLegacy") or []) + list(LOCAL_UNEXPECTED)
    return files, unexpected


def _header_for(path: Path, source_rel: Path) -> str:
    marker = (
        f"Generated from vendor/moonspec/bundle/{source_rel.as_posix()}; "
        "edit MoonSpec repo instead."
    )
    if path.suffix == ".md":
        return f"<!-- {marker} -->\n\n"
    return f"# {marker}\n"


def _with_header(path: Path, source_rel: Path, text: str) -> str:
    header = _header_for(path, source_rel)
    if path.suffix == ".md" and text.startswith("---\n"):
        marker = "\n---"
        end = text.find(marker, 4)
        if end != -1:
            insert_at = end + len(marker)
            if insert_at < len(text) and text[insert_at] == "\n":
                insert_at += 1
            return text[:insert_at] + header + text[insert_at:]
    if text.startswith("#!"):
        first_newline = text.find("\n")
        if first_newline != -1:
            return text[: first_newline + 1] + header + text[first_newline + 1 :]
    return header + text


def _expected_content(item: PlannedFile) -> bytes:
    raw = item.source.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    return _with_header(item.target, item.source_rel, text).encode("utf-8")


def _write_projection(files: list[PlannedFile]) -> None:
    for item in files:
        item.target.parent.mkdir(parents=True, exist_ok=True)
        item.target.write_bytes(_expected_content(item))


def _remove_stale(
    files: list[PlannedFile],
    unexpected_patterns: list[str],
) -> list[str]:
    expected_targets = {item.target.resolve() for item in files}
    managed_roots = {item.managed_root for item in files}
    removed: list[str] = []

    for root in sorted(managed_roots):
        if root.is_file():
            continue
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.resolve() not in expected_targets:
                path.unlink()
                removed.append(str(path.relative_to(REPO_ROOT)))
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass

    for pattern in unexpected_patterns:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if path.is_file():
                path.unlink()
                removed.append(str(path.relative_to(REPO_ROOT)))
    return removed


def _drift(files: list[PlannedFile], unexpected_patterns: list[str]) -> list[str]:
    drift: list[str] = []
    for item in files:
        if not item.target.is_file():
            drift.append(f"missing: {item.target.relative_to(REPO_ROOT)}")
            continue
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(_expected_content(item))
        try:
            if not filecmp.cmp(temp_path, item.target, shallow=False):
                drift.append(f"stale: {item.target.relative_to(REPO_ROOT)}")
        finally:
            temp_path.unlink(missing_ok=True)

    expected_targets = {item.target.resolve() for item in files}
    for root in {item.managed_root for item in files}:
        if root.is_file() or not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.resolve() not in expected_targets:
                drift.append(
                    f"unexpected projected file: {path.relative_to(REPO_ROOT)}"
                )

    for pattern in unexpected_patterns:
        matches = [
            path.relative_to(REPO_ROOT)
            for path in REPO_ROOT.glob(pattern)
            if path.is_file()
        ]
        for match in sorted(matches):
            drift.append(f"unexpected legacy file: {match}")
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync MoonSpec submodule projection")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--projection", default="moonmind")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    source_root = args.source.resolve()
    try:
        files, unexpected_patterns = _planned_files(source_root, args.projection)
        if args.write:
            _write_projection(files)
            _remove_stale(files, unexpected_patterns)
        drift = _drift(files, unexpected_patterns)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if drift:
        print("MoonSpec projection drift detected:", file=sys.stderr)
        for item in drift:
            print(f"  {item}", file=sys.stderr)
        if not args.write:
            print(
                "Run: python3 tools/sync_moonspec_submodule.py --write",
                file=sys.stderr,
            )
        return 1

    print("MoonSpec submodule projection is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
