#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "moonspec"
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
    target_rel: Path
    managed_root: Path


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_join(
    root: Path,
    raw: object,
    label: str,
    *,
    resolve_symlinks: bool = True,
) -> Path:
    rel = Path(str(raw or ""))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"{label} path escapes its root: {rel}")
    resolved = (root / rel).resolve() if resolve_symlinks else root / rel
    root_resolved = root.resolve()
    checked = resolved.resolve() if resolve_symlinks else resolved
    if not _is_relative_to(checked, root_resolved):
        raise ValueError(f"{label} path escapes its root: {rel}")
    return resolved


def _projection_path(source_root: Path, projection_name: str) -> Path:
    bundle_root = source_root / "bundle"
    manifest = _load_yaml(bundle_root / "moonspec.bundle.yaml")
    projections = manifest.get("projections")
    if not isinstance(projections, dict) or projection_name not in projections:
        raise ValueError(f"unknown MoonSpec projection {projection_name!r}")
    projection = projections[projection_name]
    if not isinstance(projection, dict) or not isinstance(projection.get("path"), str):
        raise ValueError(f"projection {projection_name!r} must define path")
    return _safe_join(bundle_root, projection["path"], "projection")


def _planned_files(
    source_root: Path,
    projection_name: str,
    repo_root: Path = REPO_ROOT,
) -> tuple[list[PlannedFile], list[str]]:
    repo_root = repo_root.resolve()
    source_root = source_root.resolve()
    bundle_root = (source_root / "bundle").resolve()
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
        source = _safe_join(bundle_root, mapping.get("from", ""), "source")
        target = _safe_join(
            repo_root,
            mapping.get("to", ""),
            "target",
            resolve_symlinks=False,
        )
        if mode == "file":
            if not source.is_file():
                raise ValueError(f"mapped source file does not exist: {source}")
            files.append(
                PlannedFile(
                    source=source,
                    source_rel=source.relative_to(bundle_root),
                    target=target,
                    target_rel=target.relative_to(repo_root),
                    managed_root=target,
                )
            )
        elif mode == "directory":
            if not source.is_dir():
                raise ValueError(f"mapped source directory does not exist: {source}")
            for child in sorted(source.rglob("*")):
                if child.is_file():
                    relative_child = child.relative_to(source)
                    target_file = target / relative_child
                    if not _is_relative_to(target_file, repo_root):
                        raise ValueError(
                            f"target path escapes repository root: {target_file}"
                        )
                    files.append(
                        PlannedFile(
                            source=child.resolve(),
                            source_rel=child.resolve().relative_to(bundle_root),
                            target=target_file,
                            target_rel=target_file.relative_to(repo_root),
                            managed_root=target,
                        )
                    )
        else:
            raise ValueError(f"unsupported projection mapping mode {mode!r}")

    unexpected = list(recipe.get("unexpectedLegacy") or []) + list(LOCAL_UNEXPECTED)
    return files, unexpected


def _expected_link_text(item: PlannedFile) -> str:
    return os.path.relpath(item.source, item.target.parent)


def _generated_marker(item: PlannedFile) -> bytes:
    return (
        f"Generated from moonspec/bundle/{item.source_rel.as_posix()}; "
        "edit MoonSpec repo instead."
    ).encode("utf-8")


def _is_generated_projection_file(item: PlannedFile) -> bool:
    if not item.target.is_file() or item.target.is_symlink():
        return False
    try:
        return _generated_marker(item) in item.target.read_bytes()
    except OSError:
        return False


def _link_points_to_bundle(path: Path, bundle_root: Path) -> bool:
    if not path.is_symlink():
        return False
    link_target = Path(os.readlink(path))
    if not link_target.is_absolute():
        link_target = path.parent / link_target
    resolved = link_target.resolve(strict=False)
    return _is_relative_to(resolved, bundle_root.resolve())


def _write_projection(files: list[PlannedFile], replace_generated: bool) -> list[str]:
    changed: list[str] = []
    for item in files:
        item.target.parent.mkdir(parents=True, exist_ok=True)
        link_text = _expected_link_text(item)
        if item.target.is_symlink():
            if os.readlink(item.target) == link_text:
                continue
            item.target.unlink()
        elif item.target.exists():
            if item.target.is_dir():
                raise ValueError(f"refusing to replace directory: {item.target_rel}")
            if not replace_generated or not _is_generated_projection_file(item):
                raise ValueError(
                    "refusing to replace non-symlink projection target without "
                    f"--replace-generated: {item.target_rel}"
                )
            item.target.unlink()
        item.target.symlink_to(link_text)
        changed.append(item.target_rel.as_posix())
    return changed


def _remove_stale_symlinks(
    files: list[PlannedFile],
    unexpected_patterns: list[str],
    repo_root: Path = REPO_ROOT,
    source_root: Path = DEFAULT_SOURCE,
) -> list[str]:
    repo_root = repo_root.resolve()
    bundle_root = (source_root.resolve() / "bundle").resolve()
    expected_targets = {item.target.resolve(strict=False) for item in files}
    managed_roots = {item.managed_root for item in files}
    removed: list[str] = []

    for root in sorted(managed_roots):
        if root.is_symlink() or root.is_file():
            continue
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if (
                path.is_symlink()
                and path.resolve(strict=False) not in expected_targets
                and _link_points_to_bundle(path, bundle_root)
            ):
                path.unlink()
                removed.append(path.relative_to(repo_root).as_posix())

    for pattern in unexpected_patterns:
        for path in sorted(repo_root.glob(pattern)):
            if path.is_symlink() and _link_points_to_bundle(path, bundle_root):
                path.unlink()
                removed.append(path.relative_to(repo_root).as_posix())
    return removed


def _drift(files: list[PlannedFile]) -> list[str]:
    drift: list[str] = []
    for item in files:
        if not item.target.exists() and not item.target.is_symlink():
            drift.append(f"missing: {item.target_rel}")
            continue
        if item.target.is_dir() and not item.target.is_symlink():
            drift.append(f"directory at projected file path: {item.target_rel}")
            continue
        if not item.target.is_symlink():
            if _is_generated_projection_file(item):
                drift.append(f"generated copy should be symlink: {item.target_rel}")
            else:
                drift.append(f"non-symlink projection target: {item.target_rel}")
            continue
        expected = _expected_link_text(item)
        actual = os.readlink(item.target)
        if actual != expected:
            drift.append(f"wrong symlink target: {item.target_rel}")
            continue
        if item.target.resolve(strict=False) != item.source.resolve():
            drift.append(f"unresolved symlink target: {item.target_rel}")
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Link MoonSpec submodule files into MoonMind runtime paths"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--projection", default="moonmind")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Remove stale symlinks that point into moonspec/bundle",
    )
    parser.add_argument(
        "--replace-generated",
        action="store_true",
        help="Replace old generated MoonSpec copies with file-level symlinks",
    )
    args = parser.parse_args(argv)

    source_root = args.source.resolve()
    try:
        files, unexpected_patterns = _planned_files(source_root, args.projection)
        if args.write:
            _write_projection(files, args.replace_generated)
        if args.prune:
            _remove_stale_symlinks(files, unexpected_patterns, source_root=source_root)
        drift = _drift(files)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if drift:
        print("MoonSpec symlink projection drift detected:", file=sys.stderr)
        for item in drift:
            print(f"  {item}", file=sys.stderr)
        if not args.write:
            print(
                "Run: python3 tools/link_moonspec_submodule.py --write "
                "--replace-generated --prune",
                file=sys.stderr,
            )
        return 1

    print("MoonSpec submodule symlink projection is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
