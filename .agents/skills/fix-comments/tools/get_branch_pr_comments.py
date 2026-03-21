#!/usr/bin/env python3
"""
Retrieve all comments for the pull request associated with the current branch.

This script resolves the branch PR via `gh pr view` and then delegates comment
retrieval to tools/get_pr_comments.py.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def is_retryable_command_error(output: str) -> bool:
    message = output.lower()
    return any(
        needle in message
        for needle in (
            "error connecting to api.github.com",
            "connection reset",
            "connection refused",
            "connection timed out",
            "timed out",
            "name resolution",
            "temporary failure",
            "network is unreachable",
            "getaddrinfo",
            "ssl: ",
            "tls",
        )
    )


def sanitized_command(command: list[str]) -> str:
    sanitized: list[str] = []
    hide_next = False
    for argument in command:
        if hide_next:
            sanitized.append("***")
            hide_next = False
            continue

        if argument in {"--token", "-t"}:
            sanitized.append(argument)
            hide_next = True
            continue

        if argument.startswith("--token="):
            sanitized.append("--token=***")
            continue

        sanitized.append(argument)

    return " ".join(shlex.quote(argument) for argument in sanitized)


def run_json_command(
    command: list[str],
    failure_hint: str,
    max_attempts: int = 3,
    initial_delay_seconds: float = 1.0,
    max_delay_seconds: float = 8.0,
) -> Any:
    output: str | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
            )
            output = completed.stdout
            stderr = completed.stderr
            if completed.returncode != 0:
                details = "\n".join(
                    item.strip() for item in (output or "", stderr or "") if item.strip()
                )
                retryable = is_retryable_command_error(details)
                if attempt >= max_attempts or not retryable:
                    error_text = details or f"Command exited with {completed.returncode}"
                    raise RuntimeError(f"{failure_hint}\n{error_text}")

                delay = min(
                    max_delay_seconds, initial_delay_seconds * (2 ** (attempt - 1))
                )
                eprint(
                    f"Retryable error on attempt {attempt}/{max_attempts} for command `{sanitized_command(command)}`: "
                    f"{details}. Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
                continue
        except FileNotFoundError as exc:
            raise RuntimeError(f"Required command not found: {command[0]}") from exc

    if output is None:
        raise RuntimeError(
            f"{failure_hint}\nNo output received after multiple attempts."
        )

    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Command returned invalid JSON for `{sanitized_command(command)}`."
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
    try:
        payload = run_json_command(
            command,
            "Unable to resolve pull request metadata. Ensure gh is authenticated and the branch has an open PR.",
        )
    except RuntimeError as original_error:
        discovered_number = discover_pr_number_from_head_branch(selector)
        if discovered_number is None:
            raise
        fallback_command = [
            "gh",
            "pr",
            "view",
            str(discovered_number),
            "--json",
            "number,title,url,headRefName,baseRefName",
        ]
        try:
            payload = run_json_command(
                fallback_command,
                "Unable to resolve pull request metadata from discovered PR number.",
            )
        except RuntimeError:
            raise original_error

    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected payload while resolving PR metadata.")
    if "number" not in payload:
        raise RuntimeError("Pull request metadata did not include a PR number.")

    return payload


def discover_pr_number_from_head_branch(selector: str | None) -> int | None:
    branch = (selector or "").strip()
    if not branch:
        return None
    if branch.isdigit() or branch.startswith("http://") or branch.startswith("https://"):
        return None

    payload = run_json_command(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--head",
            branch,
            "--json",
            "number",
            "--limit",
            "1",
        ],
        "Unable to discover pull request by branch head.",
    )
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if not isinstance(first, dict):
        return None
    number = first.get("number")
    if number in {None, ""}:
        return None
    try:
        return int(number)
    except (TypeError, ValueError):
        return None


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
        output_path = Path(args.output).resolve()
        if not str(output_path).startswith(str(Path.cwd())):
            raise PermissionError("Output path must be within the current directory")
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
