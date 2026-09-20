#!/usr/bin/env python3
"""Redact secrets from collected backend-matrix diagnostics (MoonMind#4371).

Scope: only files under the caller-supplied diagnostics directory (a
test-owned output root such as /tmp/pytest-<suite>). Never walks the
repository, home directory, or unrelated host files. Never dumps the
environment.

Replacements cover GitHub tokens, bearer credentials, long hex/base64
secret-like tokens, and explicit provider credential assignments, while
leaving ordinary test output (node IDs, durations, paths) intact.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{8,}"), "***REDACTED-GITHUB-TOKEN***"),
    (re.compile(r"github_pat_[A-Za-z0-9_]+"), "***REDACTED-GITHUB-TOKEN***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/=]{8,}"), r"\1***REDACTED***"),
    (
        re.compile(
            r"(?i)(api[_-]?key|secret|token|password|private[_-]?key)\s*[:=]\s*"
            r"(['\"]?)([A-Za-z0-9\-._~+/=]{8,})\2"
        ),
        r"\1=***REDACTED***",
    ),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{8,}"), "***REDACTED-SLACK-TOKEN***"),
    (re.compile(r"sk-(live|test)-[A-Za-z0-9]{8,}"), "***REDACTED-API-KEY***"),
)

TEXT_SUFFIXES = {".log", ".txt", ".json", ".jsonl", ".xml", ".yaml", ".yml"}


def redact_text(text: str) -> tuple[str, int]:
    total = 0
    for pattern, replacement in PATTERNS:
        text, count = pattern.subn(replacement, text)
        total += count
    return text, total


def redact_directory(root: Path) -> dict[str, int]:
    root = root.resolve()
    files_scanned = 0
    files_redacted = 0
    replacements = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != "collection-status.txt":
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        files_scanned += 1
        redacted, count = redact_text(original)
        if count:
            path.write_text(redacted, encoding="utf-8")
            files_redacted += 1
            replacements += count
    return {
        "files_scanned": files_scanned,
        "files_redacted": files_redacted,
        "replacements": replacements,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostics-dir", required=True)
    parser.add_argument("--status-file", default="")
    args = parser.parse_args(argv)
    root = Path(args.diagnostics_dir)
    if not root.is_dir():
        print(f"diagnostics dir not found: {root}", flush=True)
        return 0
    stats = redact_directory(root)
    line = (
        f"redaction: scanned={stats['files_scanned']} "
        f"redacted={stats['files_redacted']} "
        f"replacements={stats['replacements']} (scope: {root})"
    )
    print(line, flush=True)
    if args.status_file:
        try:
            with open(args.status_file, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            print(f"warning: cannot append redaction status: {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
