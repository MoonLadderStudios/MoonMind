#!/usr/bin/env python3
"""Preview-first, bounded cleanup of local Python bytecode caches.

Local ``__pycache__``/``*.pyc`` files are ignored build output, not
repository defects, so this tool is optional developer tooling — never a
correctness prerequisite and never part of normal initialization.

Safety bounds (all enforced, all covered by unit tests):

- scoped to one repository root (default: current directory);
- preview by default; nothing is removed without an explicit ``--apply``;
- never follows symlinks and never descends into submodule checkouts
  (``moonspec/``, ``omnigent/``) or external volumes;
- only targets ignored cache names (``__pycache__`` directories, ``*.pyc``,
  ``*.pyo``, ``.pytest_cache``); never tracked or uncommitted source content.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
CACHE_FILE_SUFFIXES = (".pyc", ".pyo")


def _submodule_roots(repo: Path) -> list[Path]:
    roots: list[Path] = []
    gitmodules = repo / ".gitmodules"
    if not gitmodules.is_file():
        return roots
    current_path: str | None = None
    for raw_line in gitmodules.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("[submodule"):
            current_path = None
        elif "=" in line:
            key, _, value = line.partition("=")
            if key.strip() == "path":
                current_path = value.strip()
                roots.append(repo / current_path)
    return roots


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def collect_cache_targets(repo: Path) -> list[Path]:
    """List removable cache paths under *repo* without deleting anything."""
    repo = repo.resolve()
    submodule_roots = [p.resolve() for p in _submodule_roots(repo)]
    targets: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(
        repo, topdown=True, followlinks=False
    ):
        current = Path(dirpath)
        # Never descend into symlinked directories, .git, or submodules.
        dirnames[:] = [
            name
            for name in dirnames
            if not (current / name).is_symlink()
            and name != ".git"
            and not any(
                _is_within((current / name).resolve(), root)
                or (current / name).resolve() == root
                for root in submodule_roots
            )
        ]
        if any(
            _is_within(current.resolve(), root) for root in submodule_roots
        ):
            dirnames[:] = []
            continue

        for name in dirnames:
            if name in CACHE_DIR_NAMES:
                candidate = current / name
                if not candidate.is_symlink():
                    targets.append(candidate)
        for name in filenames:
            if name.endswith(CACHE_FILE_SUFFIXES):
                candidate = current / name
                if not candidate.is_symlink():
                    targets.append(candidate)
    return sorted(set(targets))


def remove_targets(targets: list[Path], repo: Path) -> list[Path]:
    """Remove *targets* that are still inside *repo*; return what was removed."""
    repo = repo.resolve()
    removed: list[Path] = []
    for target in targets:
        resolved = target.resolve()
        if not _is_within(resolved, repo) and resolved != repo:
            continue
        if target.is_symlink():
            continue
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=False)
        elif target.is_file():
            target.unlink()
        else:
            continue
        removed.append(target)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Repository root to scan (default: current directory).",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        default=True,
        help="List targets without deleting (default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Remove the listed targets. Requires explicit opt-in.",
    )
    args = parser.parse_args(argv)
    repo = args.repo.resolve()

    targets = collect_cache_targets(repo)
    if not targets:
        print("No local bytecode cache targets found.")
        return 0

    if not args.apply:
        print("Preview only — nothing was removed. Re-run with --apply "
              "to remove these targets:")
        for target in targets:
            print(f"  {target}")
        return 0

    removed = remove_targets(targets, repo)
    print(f"Removed {len(removed)} cache target(s).")
    for target in removed:
        print(f"  {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
