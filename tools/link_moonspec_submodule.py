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
GENERATED_MARKER = "Generated from moonspec/bundle/"


@dataclass(frozen=True)
class PlannedLink:
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


def _resolve_inside(
    path: Path,
    root: Path,
    label: str,
    *,
    follow_final: bool = True,
) -> Path:
    resolved = (
        path.resolve(strict=False)
        if follow_final
        else path.parent.resolve(strict=False) / path.name
    )
    root_resolved = root.resolve(strict=False)
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"{label} escapes allowed root: {path}")
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
    return _resolve_inside(bundle_root / projection["path"], bundle_root, "projection path")


def _planned_links(
    source_root: Path,
    projection_name: str,
    repo_root: Path = REPO_ROOT,
) -> tuple[list[PlannedLink], list[str]]:
    bundle_root = _resolve_inside(source_root / "bundle", source_root, "bundle root")
    projection_path = _projection_path(source_root, projection_name)
    recipe = _load_yaml(projection_path)
    mappings = recipe.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError(f"{projection_path} must define mappings")

    links: list[PlannedLink] = []
    repo_root = repo_root.resolve(strict=False)
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise ValueError(f"{projection_path} mappings must be mappings")
        mode = mapping.get("mode")
        source = _resolve_inside(bundle_root / str(mapping.get("from", "")), bundle_root, "source")
        target = _resolve_inside(
            repo_root / str(mapping.get("to", "")),
            repo_root,
            "target",
            follow_final=False,
        )
        if mode == "file":
            if not source.is_file():
                raise ValueError(f"mapped source file does not exist: {source}")
            links.append(
                PlannedLink(
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
                    relative_child = child.relative_to(source)
                    target_file = _resolve_inside(
                        target / relative_child,
                        repo_root,
                        "target",
                        follow_final=False,
                    )
                    links.append(
                        PlannedLink(
                            source=child.resolve(strict=False),
                            source_rel=child.relative_to(bundle_root),
                            target=target_file,
                            managed_root=target,
                        )
                    )
        else:
            raise ValueError(f"unsupported projection mapping mode {mode!r}")

    unexpected = list(recipe.get("unexpectedLegacy") or []) + list(LOCAL_UNEXPECTED)
    return links, unexpected


def _relative_link_target(source: Path, target: Path) -> str:
    return os.path.relpath(source, start=target.parent)


def _is_generated_projection(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    return GENERATED_MARKER in text


def _link_status(item: PlannedLink) -> str | None:
    if not item.target.exists() and not item.target.is_symlink():
        return f"missing: {item.target.relative_to(REPO_ROOT)}"
    if item.target.is_dir() and not item.target.is_symlink():
        return f"directory blocks projected file: {item.target.relative_to(REPO_ROOT)}"
    if not item.target.is_symlink():
        return f"not a symlink: {item.target.relative_to(REPO_ROOT)}"
    link_text = os.readlink(item.target)
    if Path(link_text).is_absolute():
        return f"absolute symlink: {item.target.relative_to(REPO_ROOT)}"
    resolved = (item.target.parent / link_text).resolve(strict=False)
    if resolved != item.source:
        return f"wrong target: {item.target.relative_to(REPO_ROOT)} -> {link_text}"
    return None


def _write_links(links: list[PlannedLink], replace_generated: bool) -> list[str]:
    changed: list[str] = []
    for item in links:
        item.target.parent.mkdir(parents=True, exist_ok=True)
        rel_target = _relative_link_target(item.source, item.target)
        if item.target.is_dir() and not item.target.is_symlink():
            raise ValueError(f"refusing to replace directory: {item.target.relative_to(REPO_ROOT)}")
        if item.target.is_symlink():
            if os.readlink(item.target) == rel_target:
                continue
            item.target.unlink()
        elif item.target.exists():
            if not replace_generated or not _is_generated_projection(item.target):
                raise ValueError(
                    "refusing to replace non-symlink without --replace-generated: "
                    f"{item.target.relative_to(REPO_ROOT)}"
                )
            item.target.unlink()
        item.target.symlink_to(rel_target)
        changed.append(str(item.target.relative_to(REPO_ROOT)))
    return changed


def _prune_stale_symlinks(
    links: list[PlannedLink],
    unexpected_patterns: list[str],
    bundle_root: Path,
    write: bool,
) -> list[str]:
    expected_targets = {item.target.resolve(strict=False) for item in links}
    managed_roots = {item.managed_root for item in links}
    stale: list[str] = []

    for root in sorted(managed_roots):
        if root.is_file() or not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_symlink() or path.resolve(strict=False) in expected_targets:
                continue
            target = path.resolve(strict=False)
            if target == bundle_root or bundle_root in target.parents:
                stale.append(str(path.relative_to(REPO_ROOT)))
                if write:
                    path.unlink()

    for pattern in unexpected_patterns:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if path.is_symlink():
                stale.append(str(path.relative_to(REPO_ROOT)))
                if write:
                    path.unlink()
    return stale


def _drift(
    links: list[PlannedLink],
    unexpected_patterns: list[str],
    bundle_root: Path,
    prune: bool,
) -> list[str]:
    drift = [status for item in links if (status := _link_status(item))]
    if prune:
        for path in _prune_stale_symlinks(links, unexpected_patterns, bundle_root, write=False):
            drift.append(f"stale symlink: {path}")
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Link MoonSpec submodule projection")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--projection", default="moonmind")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--replace-generated", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    source_root = args.source.resolve(strict=False)
    try:
        links, unexpected_patterns = _planned_links(source_root, args.projection)
        bundle_root = source_root / "bundle"
        if args.write:
            changed = _write_links(links, replace_generated=args.replace_generated)
            pruned = (
                _prune_stale_symlinks(
                    links,
                    unexpected_patterns,
                    bundle_root.resolve(strict=False),
                    write=True,
                )
                if args.prune
                else []
            )
            if changed or pruned:
                print(
                    "MoonSpec symlink projection updated "
                    f"({len(changed)} linked, {len(pruned)} pruned)"
                )
        drift = _drift(
            links,
            unexpected_patterns,
            bundle_root.resolve(strict=False),
            prune=args.prune,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if drift:
        print("MoonSpec symlink projection drift detected:", file=sys.stderr)
        for item in drift:
            print(f"  {item}", file=sys.stderr)
        if not args.write:
            print(
                "Run: python3 tools/link_moonspec_submodule.py --write --prune --replace-generated",
                file=sys.stderr,
            )
        return 1

    print("MoonSpec symlink projection is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
