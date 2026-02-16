#!/usr/bin/env python3
"""
Retrieve all comments for the pull request associated with the current branch.

This script resolves the branch PR via `gh pr view` and then delegates comment
retrieval to tools/get_pr_comments.py.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def run_json_command(command: list[str], failure_hint: str) -> Any:
    try:
        output = subprocess.check_output(command, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{failure_hint}\n{exc.output.strip()}") from exc

    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Command returned invalid JSON: {' '.join(command)}"
        ) from exc


def detect_current_branch() -> str | None:
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    if not branch or branch == "HEAD":
        return None
    return branch


def resolve_pr_metadata(selector: str | None) -> dict[str, Any]:
    command = ["gh", "pr", "view"]
    if selector:
        command.append(selector)
    command.extend(["--json", "number,title,url,headRefName,baseRefName"])
    payload = run_json_command(
        command,
        "Unable to resolve pull request metadata. Ensure gh is authenticated and the branch has an open PR.",
    )

    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected payload while resolving PR metadata.")
    if "number" not in payload:
        raise RuntimeError("Pull request metadata did not include a PR number.")

    return payload


def fetch_comments(
    pr_number: int,
    repo: str | None,
    token: str | None,
    include_empty_reviews: bool,
    exclude_reviews: bool,
) -> dict[str, Any]:
    script_path = Path(__file__).resolve().with_name("get_pr_comments.py")
    if not script_path.exists():
        raise RuntimeError(f"Missing helper script: {script_path}")

    command = [sys.executable, str(script_path), str(pr_number), "--compact"]
    if repo:
        command.extend(["--repo", repo])
    if token:
        command.extend(["--token", token])
    if include_empty_reviews:
        command.append("--include-empty-reviews")
    if exclude_reviews:
        command.append("--exclude-reviews")

    payload = run_json_command(command, "Failed to retrieve PR comments.")
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected payload while loading PR comments.")

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retrieve all PR comments for the current branch (or selected PR)."
    )
    parser.add_argument(
        "--pr",
        help="Optional PR selector for `gh pr view` (number, URL, or branch). Defaults to current branch PR.",
    )
    parser.add_argument(
        "--repo",
        help="Optional owner/repo override passed to get_pr_comments.py.",
    )
    parser.add_argument(
        "--token",
        help="Optional GitHub token override passed to get_pr_comments.py.",
    )
    parser.add_argument(
        "--include-empty-reviews",
        action="store_true",
        help="Include review entries even if review body is empty.",
    )
    parser.add_argument(
        "--exclude-reviews",
        action="store_true",
        help="Exclude top-level review body comments (keeps issue + inline review comments).",
    )
    parser.add_argument(
        "--output",
        help="Write JSON output to this path instead of stdout.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact JSON output.",
    )
    args = parser.parse_args()

    current_branch = detect_current_branch()
    selector = args.pr or current_branch
    if not selector:
        raise RuntimeError(
            "Unable to determine current branch. Pass --pr <number|url|branch> explicitly."
        )

    pr_metadata = resolve_pr_metadata(selector)
    pr_number = int(pr_metadata["number"])

    comments_payload = fetch_comments(
        pr_number=pr_number,
        repo=args.repo,
        token=args.token,
        include_empty_reviews=args.include_empty_reviews,
        exclude_reviews=args.exclude_reviews,
    )

    result: dict[str, Any] = {
        "branch": current_branch,
        "pr": {
            "number": pr_number,
            "title": pr_metadata.get("title"),
            "url": pr_metadata.get("url"),
            "head_ref": pr_metadata.get("headRefName"),
            "base_ref": pr_metadata.get("baseRefName"),
        },
        "repository": comments_payload.get("repository"),
        "comment_count": comments_payload.get("comment_count"),
        "comments": comments_payload.get("comments", []),
    }

    json_output = json.dumps(
        result,
        indent=None if args.compact else 2,
        separators=(",", ":") if args.compact else None,
    )

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json_output + "\n", encoding="utf-8")
        eprint(f"Wrote {result.get('comment_count', 0)} comments to {output_path}")
    else:
        print(json_output)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        eprint(f"Error: {exc}")
        raise SystemExit(1)
