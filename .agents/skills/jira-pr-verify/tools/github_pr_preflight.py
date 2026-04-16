#!/usr/bin/env python3
"""Verify GitHub PR access through gh for jira-pr-verify runs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any


def _run(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preflight GitHub PR access using the runtime gh authentication."
    )
    parser.add_argument("--repo", required=True, help="Repository in owner/name form.")
    parser.add_argument("--pr", required=True, help="Pull request number.")
    args = parser.parse_args()

    checks = [
        _run(["gh", "auth", "status", "--hostname", "github.com"]),
        _run(
            [
                "gh",
                "repo",
                "view",
                args.repo,
                "--json",
                "nameWithOwner,viewerPermission,isPrivate",
            ]
        ),
        _run(
            [
                "gh",
                "pr",
                "view",
                str(args.pr),
                "--repo",
                args.repo,
                "--json",
                "number,title,state,url,headRefName,baseRefName",
            ]
        ),
    ]
    ok = all(check["returncode"] == 0 for check in checks)
    print(json.dumps({"ok": ok, "repo": args.repo, "pr": str(args.pr), "checks": checks}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
