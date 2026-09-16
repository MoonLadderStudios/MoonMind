#!/usr/bin/env python3
"""Verify clean-checkout hygiene without changing dependency authority.

Compares the commits recorded in the parent repository (``git ls-tree``)
against ``.gitmodules`` metadata and the actual submodule HEADs
(``git submodule status``), using only the documented initialization path::

    git submodule update --init --checkout -- moonspec omnigent

The check never advances dependencies (no ``--remote``), never force-resets
the caller's working tree, and never attributes local modifications to the
committed repository without a reproduction. For a fully disposable
reproduction, clone the exact revision elsewhere and run this tool with
``--repo`` pointing at the clone::

    git clone https://github.com/MoonLadderStudios/MoonMind.git /tmp/mm-clean
    cd /tmp/mm-clean && git checkout <revision>
    git submodule update --init --checkout -- moonspec omnigent
    python /tmp/mm-clean/tools/verify_clean_checkout.py --repo /tmp/mm-clean
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


EXPECTED_SUBMODULES = ("moonspec", "omnigent")

_GITMODULES_SECTION_RE = re.compile(r'\[submodule\s+"([^"]+)"\]')


def parse_gitmodules(text: str) -> dict[str, dict[str, str]]:
    """Parse .gitmodules text into {name: {path, url, branch?}}."""
    modules: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = _GITMODULES_SECTION_RE.match(line)
        if match:
            current = match.group(1)
            modules[current] = {}
            continue
        if current is None or "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        modules[current][key.strip()] = value.strip()
    return modules


def parse_ls_tree(text: str) -> dict[str, str]:
    """Parse `git ls-tree HEAD` output into {path: sha} for gitlinks."""
    gitlinks: dict[str, str] = {}
    for line in text.splitlines():
        # e.g. "160000 commit ab4e4101... \tmoonspec"
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "160000" and parts[1] == "commit":
            gitlinks[parts[3]] = parts[2]
    return gitlinks


def parse_submodule_status(text: str) -> dict[str, dict[str, object]]:
    """Parse `git submodule status` into {path: {sha, initialized}}."""
    status: dict[str, dict[str, object]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        flag = line[0]
        rest = line[1:].split()
        if len(rest) < 2:
            continue
        sha, path = rest[0], rest[1]
        status[path] = {"sha": sha, "initialized": flag != "-"}
    return status


def compare_state(
    gitlinks: dict[str, str],
    modules: dict[str, dict[str, str]],
    status: dict[str, dict[str, object]],
) -> list[str]:
    """Return human-readable problems; empty means reproducible and clean."""
    problems: list[str] = []
    module_paths = {info.get("path", name) for name, info in modules.items()}

    for name, info in sorted(modules.items()):
        path = info.get("path", name)
        if path not in gitlinks:
            problems.append(
                f"stale .gitmodules entry [{name}] (path {path!r}): "
                "no matching gitlink in the committed tree"
            )
        if "branch" in info:
            problems.append(
                f".gitmodules entry [{name}] sets branch={info['branch']!r}: "
                "recorded-commit checkout and --remote tracking are different "
                "operations; remove the branch setting"
            )

    for path in sorted(gitlinks):
        if path not in module_paths:
            problems.append(
                f"tracked submodule {path!r} has no .gitmodules entry: "
                "resolve its source authority explicitly"
            )

    for path in sorted(set(gitlinks) & module_paths):
        recorded = gitlinks[path]
        actual = status.get(path)
        if actual is None or not actual.get("initialized"):
            problems.append(
                f"submodule {path!r} is not initialized: run "
                "`git submodule update --init --checkout -- "
                + " ".join(EXPECTED_SUBMODULES) + "`"
            )
        elif actual.get("sha") != recorded:
            problems.append(
                f"submodule {path!r} HEAD {actual.get('sha')} does not match "
                f"recorded gitlink {recorded}: checkout drift, not a defect "
                "in the committed repository"
            )
    return problems


def _run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def verify_repo(repo: Path, revision: str = "HEAD") -> list[str]:
    modules_text = (repo / ".gitmodules").read_text(encoding="utf-8")
    modules = parse_gitmodules(modules_text)
    gitlinks = parse_ls_tree(_run_git(repo, "ls-tree", revision))
    try:
        status = parse_submodule_status(
            _run_git(repo, "submodule", "status", "--", *EXPECTED_SUBMODULES)
        )
    except RuntimeError:
        status = {}
    return compare_state(gitlinks, modules, status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Repository root to verify (default: current directory).",
    )
    parser.add_argument(
        "--revision",
        default="HEAD",
        help="Revision whose recorded gitlinks to check (default: HEAD).",
    )
    args = parser.parse_args(argv)

    try:
        problems = verify_repo(args.repo, args.revision)
    except (OSError, RuntimeError) as exc:
        print(f"clean-checkout verification failed: {exc}", file=sys.stderr)
        return 2

    if problems:
        print("clean-checkout state is NOT reproducible:")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "Reproduce in a disposable clone of the exact revision with "
            "`git submodule update --init --checkout -- moonspec omnigent`; "
            "do not use --remote and do not force-reset a developer checkout."
        )
        return 1
    print("clean-checkout state is reproducible: .gitmodules and recorded "
          "gitlinks agree for moonspec and omnigent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
