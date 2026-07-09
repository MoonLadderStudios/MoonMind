#!/usr/bin/env python3
"""Vendor the MoonSpec bundle projection into the repository as real files.

This is the canonical MoonSpec projection tool. It reads the bundle manifest
(``moonspec/bundle/moonspec.bundle.yaml``) and the consumer projection recipe
it names (``projections/moonmind.yaml``), then copies every mapped bundle file
into the repository as a plain, byte-identical file.

Vendored copies are committed. Provenance is the ``moonspec`` submodule
gitlink: the pinned submodule commit records exactly which bundle version the
vendored files came from, and ``--check`` (run in CI) fails when the vendored
files drift from the pinned bundle in either direction.

Do not hand-edit vendored files; change the MoonSpec repo, bump the
submodule, and re-run ``--write``.

Usage:
    python3 tools/sync_moonspec.py --check   # report drift, exit 1 if any
    python3 tools/sync_moonspec.py --write   # sync vendored files + prune
"""

from __future__ import annotations

import argparse
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "moonspec"

# Ownership of directory-mapping targets. Pruning stale files requires knowing
# whether MoonSpec owns the whole target directory or shares it with
# repo-native content. Every directory mapping in the projection recipe must
# have an entry here; an unknown target fails fast so a new upstream mapping
# forces a conscious ownership decision instead of silently deleting foreign
# files.
#   "full"        — every file under the target belongs to the projection.
#   "skill-dirs"  — only top-level directories named with the bundle's
#                   skillPrefix belong to the projection.
#   "file-prefix" — only files named with the bundle's command file prefix
#                   belong to the projection.
DIRECTORY_OWNERSHIP = {
    ".agents/skills": "skill-dirs",
    ".gemini/commands": "file-prefix",
    ".specify/scripts/bash": "full",
    ".specify/templates": "full",
    ".specify/templates/commands": "full",
}


@dataclass(frozen=True)
class PlannedFile:
    source: Path
    source_rel: Path
    target: Path


@dataclass(frozen=True)
class Projection:
    files: list[PlannedFile]
    directory_targets: dict[Path, str]
    unexpected_patterns: list[str]
    skill_prefix: str
    command_file_prefix: str


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def _resolve_inside(
    path: Path, root: Path, label: str, *, follow_final: bool = True
) -> Path:
    # follow_final=False keeps the final path component un-dereferenced so a
    # write target that is currently a symlink is replaced in place instead of
    # writing through the link into its target.
    resolved = (
        path.resolve(strict=False)
        if follow_final
        else path.parent.resolve(strict=False) / path.name
    )
    root_resolved = root.resolve(strict=False)
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"{label} escapes allowed root: {path}")
    return resolved


def _projection_recipe(source_root: Path, projection_name: str) -> dict[str, Any]:
    bundle_root = source_root / "bundle"
    manifest = _load_yaml(bundle_root / "moonspec.bundle.yaml")
    projections = manifest.get("projections")
    if not isinstance(projections, dict) or projection_name not in projections:
        raise ValueError(f"unknown MoonSpec projection {projection_name!r}")
    projection = projections[projection_name]
    if not isinstance(projection, dict) or not isinstance(projection.get("path"), str):
        raise ValueError(f"projection {projection_name!r} must define path")
    recipe_path = _resolve_inside(
        bundle_root / projection["path"], bundle_root, "projection path"
    )
    recipe = _load_yaml(recipe_path)
    recipe["_manifest"] = manifest
    recipe["_recipe_path"] = recipe_path
    return recipe


def _identity_prefixes(manifest: dict[str, Any]) -> tuple[str, str]:
    identity = manifest.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("bundle manifest must define identity")
    skill_prefix = identity.get("skillPrefix")
    command_prefix = identity.get("commandPrefix")
    if not isinstance(skill_prefix, str) or not skill_prefix:
        raise ValueError("bundle manifest identity must define skillPrefix")
    if not isinstance(command_prefix, str) or not command_prefix:
        raise ValueError("bundle manifest identity must define commandPrefix")
    return skill_prefix, command_prefix.lstrip("/")


def _plan(source_root: Path, projection_name: str, repo_root: Path) -> Projection:
    bundle_root = _resolve_inside(source_root / "bundle", source_root, "bundle root")
    recipe = _projection_recipe(source_root, projection_name)
    recipe_path = recipe["_recipe_path"]
    mappings = recipe.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError(f"{recipe_path} must define mappings")
    skill_prefix, command_file_prefix = _identity_prefixes(recipe["_manifest"])

    repo_root = repo_root.resolve(strict=False)
    files: list[PlannedFile] = []
    directory_targets: dict[Path, str] = {}
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise ValueError(f"{recipe_path} mappings must be mappings")
        mode = mapping.get("mode")
        to_rel = str(mapping.get("to", ""))
        source = _resolve_inside(
            bundle_root / str(mapping.get("from", "")), bundle_root, "source"
        )
        target = _resolve_inside(
            repo_root / to_rel, repo_root, "target", follow_final=False
        )
        if mode == "file":
            if not source.is_file():
                raise ValueError(f"mapped source file does not exist: {source}")
            files.append(
                PlannedFile(
                    source=source,
                    source_rel=source.relative_to(bundle_root),
                    target=target,
                )
            )
        elif mode == "directory":
            if not source.is_dir():
                raise ValueError(f"mapped source directory does not exist: {source}")
            ownership_key = to_rel.strip("/")
            ownership = DIRECTORY_OWNERSHIP.get(ownership_key)
            if ownership is None:
                raise ValueError(
                    f"directory mapping target {to_rel!r} has no ownership rule; "
                    "classify it in DIRECTORY_OWNERSHIP in tools/sync_moonspec.py "
                    "before syncing"
                )
            directory_targets[target] = ownership
            for child in sorted(source.rglob("*")):
                if not child.is_file():
                    continue
                target_file = _resolve_inside(
                    target / child.relative_to(source),
                    repo_root,
                    "target",
                    follow_final=False,
                )
                files.append(
                    PlannedFile(
                        source=child,
                        source_rel=child.relative_to(bundle_root),
                        target=target_file,
                    )
                )
        else:
            raise ValueError(f"unsupported projection mapping mode {mode!r}")

    unexpected = [
        str(pattern) for pattern in (recipe.get("unexpectedLegacy") or [])
    ]
    return Projection(
        files=files,
        directory_targets=directory_targets,
        unexpected_patterns=unexpected,
        skill_prefix=skill_prefix,
        command_file_prefix=command_file_prefix,
    )


def _is_projection_managed(
    plan: Projection, scope: Path, ownership: str, path: Path
) -> bool:
    relative = path.relative_to(scope)
    if ownership == "full":
        return True
    if ownership == "skill-dirs":
        return relative.parts[0].startswith(plan.skill_prefix)
    if ownership == "file-prefix":
        return len(relative.parts) == 1 and relative.parts[0].startswith(
            plan.command_file_prefix
        )
    raise ValueError(f"unknown ownership rule {ownership!r}")


def _stale_paths(plan: Projection, repo_root: Path) -> Iterator[Path]:
    expected = {item.target for item in plan.files}
    seen: set[Path] = set()
    for scope, ownership in sorted(plan.directory_targets.items()):
        if not scope.is_dir():
            continue
        for path in sorted(scope.rglob("*")):
            if path.is_dir() and not path.is_symlink():
                continue
            if path in expected:
                continue
            if _is_projection_managed(plan, scope, ownership, path):
                if path in seen:
                    continue
                seen.add(path)
                yield path
    for pattern in plan.unexpected_patterns:
        try:
            matches = sorted(repo_root.glob(pattern))
        except (ValueError, NotImplementedError):
            raise ValueError(f"invalid unexpectedLegacy pattern: {pattern!r}")
        for path in matches:
            if path.is_dir() and not path.is_symlink():
                continue
            _resolve_inside(path, repo_root, "unexpectedLegacy match")
            if path in seen:
                continue
            seen.add(path)
            yield path


def _expected_mode(item: PlannedFile) -> int:
    source_mode = stat.S_IMODE(item.source.stat().st_mode)
    if (
        item.target.suffix == ".sh"
        and ".specify" in item.target.parts
        and "scripts" in item.target.parts
        and "bash" in item.target.parts
    ):
        return source_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    return source_mode


def _target_matches(item: PlannedFile) -> bool:
    if item.target.is_symlink() or not item.target.is_file():
        return False
    target_mode = stat.S_IMODE(item.target.stat().st_mode)
    return (
        item.target.read_bytes() == item.source.read_bytes()
        and target_mode == _expected_mode(item)
    )


def _mode_label(mode: int) -> str:
    return f"{mode:06o}"[-6:]


def _drift(plan: Projection, repo_root: Path) -> list[str]:
    drift: list[str] = []
    for item in plan.files:
        rel = item.target.relative_to(repo_root)
        if item.target.is_symlink():
            drift.append(f"symlink (must be a vendored real file): {rel}")
        elif item.target.is_dir():
            drift.append(f"directory blocks vendored file: {rel}")
        elif not item.target.is_file():
            drift.append(f"missing: {rel}")
        else:
            if item.target.read_bytes() != item.source.read_bytes():
                drift.append(f"content differs from moonspec/bundle: {rel}")
            target_mode = stat.S_IMODE(item.target.stat().st_mode)
            expected_mode = _expected_mode(item)
            if target_mode != expected_mode:
                drift.append(
                    "mode differs from moonspec projection: "
                    f"{rel} ({_mode_label(target_mode)} != {_mode_label(expected_mode)})"
                )
    for path in _stale_paths(plan, repo_root):
        drift.append(f"stale projection-managed file: {path.relative_to(repo_root)}")
    return drift


def _write(plan: Projection, repo_root: Path) -> tuple[list[str], list[str]]:
    written: list[str] = []
    for item in plan.files:
        rel = str(item.target.relative_to(repo_root))
        if item.target.is_dir() and not item.target.is_symlink():
            raise ValueError(f"refusing to replace directory with file: {rel}")
        if _target_matches(item):
            continue
        item.target.parent.mkdir(parents=True, exist_ok=True)
        if item.target.is_symlink() or item.target.exists():
            item.target.unlink()
        item.target.write_bytes(item.source.read_bytes())
        item.target.chmod(_expected_mode(item))
        written.append(rel)

    removed: list[str] = []
    for path in _stale_paths(plan, repo_root):
        removed.append(str(path.relative_to(repo_root)))
        path.unlink()
    for scope in plan.directory_targets:
        if not scope.is_dir():
            continue
        for path in sorted(scope.rglob("*"), reverse=True):
            if path.is_dir() and not path.is_symlink():
                try:
                    path.rmdir()
                except OSError:
                    continue
    return written, removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Vendor the MoonSpec bundle projection as real files"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--projection", default="moonmind")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    source_root = args.source.resolve(strict=False)
    repo_root = args.repo_root.resolve(strict=False)
    try:
        if not (source_root / "bundle" / "moonspec.bundle.yaml").is_file():
            raise ValueError(
                f"MoonSpec bundle manifest not found under {source_root}; "
                "run: git submodule update --init moonspec"
            )
        plan = _plan(source_root, args.projection, repo_root)
        if args.write:
            written, removed = _write(plan, repo_root)
            if written or removed:
                print(
                    f"MoonSpec projection synced ({len(written)} written, "
                    f"{len(removed)} removed)"
                )
                for rel in written:
                    print(f"  wrote: {rel}")
                for rel in removed:
                    print(f"  removed: {rel}")
        drift = _drift(plan, repo_root)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if drift:
        print("MoonSpec projection drift detected:", file=sys.stderr)
        for item in drift:
            print(f"  {item}", file=sys.stderr)
        print("Run: python3 tools/sync_moonspec.py --write", file=sys.stderr)
        return 1

    print("MoonSpec projection is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
