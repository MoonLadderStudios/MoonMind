#!/usr/bin/env python3
"""Post a GitHub PR comment with gh after scanning for secret-like content."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9_*]+"),
    re.compile(r"github_pat_[A-Za-z0-9_*]+"),
    re.compile(r"ATATT[A-Za-z0-9_\-]+"),
    re.compile(r"AIza[A-Za-z0-9_\-]+"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(token|password)\s*="),
    re.compile(r"(?i)\bAuthorization\s*:"),
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post a PR comment through gh after a secret-pattern scan."
    )
    parser.add_argument("--repo", required=True, help="Repository in owner/name form.")
    parser.add_argument("--pr", required=True, help="Pull request number.")
    parser.add_argument("--body-file", required=True, help="Markdown comment file.")
    args = parser.parse_args()

    body_path = Path(args.body_file)
    body = body_path.read_text(encoding="utf-8")
    for pattern in SECRET_PATTERNS:
        if pattern.search(body):
            print(
                f"Refusing to post comment: body matches secret pattern {pattern.pattern!r}.",
                file=sys.stderr,
            )
            return 2

    completed = subprocess.run(
        [
            "gh",
            "pr",
            "comment",
            str(args.pr),
            "--repo",
            args.repo,
            "--body-file",
            str(body_path),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
