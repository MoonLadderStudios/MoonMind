#!/usr/bin/env python3
"""MM-917 static guard for removed MM-901 capability semantics."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]

_CAPABILITY = "capability"
_VERSION = "version"
_RUNTIME = "runtime"

BANNED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "runtime-camel",
        re.compile(
            re.escape(
                _RUNTIME
                + _CAPABILITY[:1].upper()
                + _CAPABILITY[1:]
                + _VERSION[:1].upper()
                + _VERSION[1:]
            )
        ),
    ),
    (
        "generic-camel",
        re.compile(re.escape(_CAPABILITY + _VERSION[:1].upper() + _VERSION[1:])),
    ),
    (
        "runtime-words",
        re.compile(
            rf"\b{_RUNTIME}\s+{_CAPABILITY}\s+{_VERSION}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "generic-words",
        re.compile(rf"\b{_CAPABILITY}\s+{_VERSION}\b", re.IGNORECASE),
    ),
    (
        "hyphenated",
        re.compile(rf"\b{_CAPABILITY}-{_VERSION}\b", re.IGNORECASE),
    ),
    (
        "snake",
        re.compile(rf"\b{_CAPABILITY}_{_VERSION}\b", re.IGNORECASE),
    ),
    (
        "plural-camel",
        re.compile(
            re.escape(_CAPABILITY + _VERSION[:1].upper() + _VERSION[1:] + "s")
        ),
    ),
    (
        "plural-words",
        re.compile(rf"\b{_CAPABILITY}\s+{_VERSION}s\b", re.IGNORECASE),
    ),
)

SCANNED_SUFFIXES = {
    ".cjs",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

EXCLUDED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "artifacts",
    "artifacts-root-owned-readonly",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "var",
}


@dataclass(frozen=True)
class Finding:
    pattern: str
    path: Path
    line_number: int
    line: str


def _is_scanned_file(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
        return False
    if relative.parts[:3] == (".agents", "skills", "local"):
        return False
    return path.is_file() and path.suffix in SCANNED_SUFFIXES


def iter_scanned_files(root: Path = REPO_ROOT) -> Iterable[Path]:
    for path in root.rglob("*"):
        if _is_scanned_file(path, root):
            yield path


def check_removed_capability_semantics(root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_scanned_files(root):
        relative_path = path.relative_to(root)
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if not any(pattern.search(content) for _, pattern in BANNED_PATTERNS):
            continue
        lines = content.splitlines()
        for line_number, line in enumerate(lines, start=1):
            for name, pattern in BANNED_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        Finding(
                            pattern=name,
                            path=relative_path,
                            line_number=line_number,
                            line=line.strip(),
                        )
                    )
                    break
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to scan.",
    )
    args = parser.parse_args(argv)

    findings = check_removed_capability_semantics(args.root.resolve())
    if not findings:
        print("MM-917 removed capability semantics check passed.")
        return 0

    print("MM-917 removed capability semantics check failed:")
    for finding in findings:
        print(
            f"{finding.path}:{finding.line_number}: {finding.pattern}: "
            f"removed semantic pattern :: {finding.line}"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
