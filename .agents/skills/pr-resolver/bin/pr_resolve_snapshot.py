#!/usr/bin/env python3
"""
PR Resolver Snapshot Script
Gathers PR metadata, CI status, and comments to decide the next fix action.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


def run_command(
    cmd,
    failure_hint="",
    max_attempts=3,
    initial_delay_seconds=1.0,
    max_delay_seconds=8.0,
):
    for attempt in range(1, max_attempts + 1):
        try:
            output = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
            if output.strip() == "":
                return {}
            return json.loads(output)
        except subprocess.CalledProcessError as e:
            if attempt < max_attempts:
                delay = min(
                    max_delay_seconds, initial_delay_seconds * (2 ** (attempt - 1))
                )
                print(
                    f"Retryable error on attempt {attempt}/{max_attempts} for command: {' '.join(cmd)}. Retrying in {delay:.1f}s...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            print(
                f"Command failed: {' '.join(cmd)}\n{failure_hint}\n{e.output}",
                file=sys.stderr,
            )
            sys.exit(1)
        except json.JSONDecodeError:
            print(f"Command returned invalid JSON: {' '.join(cmd)}", file=sys.stderr)
            sys.exit(1)


def infer_repo_from_pr_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc == "":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    if owner and repo:
        return f"{owner}/{repo}"
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Snapshot PR state for pr-resolver skill"
    )
    parser.add_argument("--pr", help="Optional PR selector (number, URL, or branch)")
    args = parser.parse_args()

    # 1. Fetch PR Metadata
    pr_cmd = ["gh", "pr", "view"]
    if args.pr:
        pr_cmd.append(args.pr)
    pr_cmd.extend(
        [
            "--json",
            "number,title,url,isDraft,state,headRefName,baseRefName,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup",
        ]
    )

    pr_data = run_command(
        pr_cmd, "Ensure gh is authenticated and the branch has an open PR."
    )
    pr_repo = infer_repo_from_pr_url(pr_data.get("url"))
    if not pr_repo:
        pr_repo_data = run_command(
            ["gh", "repo", "view", "--json", "nameWithOwner"],
            "Unable to resolve repository for comment fetch. Install/update gh and authenticate.",
        )
        if isinstance(pr_repo_data, dict):
            pr_repo = pr_repo_data.get("nameWithOwner")

    # 2. Evaluate CI Status
    ci_is_running = False
    ci_has_failures = False
    failed_checks = []

    rollup = pr_data.get("statusCheckRollup", [])
    if isinstance(rollup, list):
        for check in rollup:
            state = check.get("state", "").upper()
            status = check.get("status", "").upper()
            conclusion = check.get("conclusion", "").upper()

            combined_state = state or conclusion or status

            if combined_state in {"IN_PROGRESS", "QUEUED", "PENDING"}:
                ci_is_running = True
            elif combined_state in {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"}:
                ci_has_failures = True
                name = check.get("name") or check.get("context") or "Unknown Check"
                failed_checks.append(
                    {
                        "name": name,
                        "state": combined_state,
                        "url": check.get("targetUrl", ""),
                    }
                )

    # 3. Fetch Comments
    # .agents/skills/fix-comments/tools/get_branch_pr_comments.py should be in the root of the project
    comments_script = Path(
        ".agents/skills/fix-comments/tools/get_branch_pr_comments.py"
    )
    comments_data = {}
    if comments_script.exists():
        comments_cmd = [sys.executable, str(comments_script), "--compact"]
        if pr_repo:
            comments_cmd.extend(["--repo", pr_repo])
        if args.pr:
            comments_cmd.extend(["--pr", args.pr])
        comments_data = run_command(comments_cmd, "Failed to retrieve PR comments.")
    else:
        print(
            f"Warning: {comments_script} not found. Skipping comments fetch.",
            file=sys.stderr,
        )

    # 4. Construct Snapshot
    snapshot = {
        "pr": pr_data,
        "ci": {
            "isRunning": ci_is_running,
            "hasFailures": ci_has_failures,
            "failedChecks": failed_checks,
        },
        "comments": comments_data.get("comments", []),
    }

    # Save to artifacts/pr_resolver_snapshot.json
    artifacts_dir = Path("artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = artifacts_dir / "pr_resolver_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, indent=2))

    print(f"Snapshot written to {snapshot_path}")

    # Print a quick summary to stdout
    summary = {
        "pr_number": pr_data.get("number"),
        "mergeable": pr_data.get("mergeable"),
        "mergeStateStatus": pr_data.get("mergeStateStatus"),
        "reviewDecision": pr_data.get("reviewDecision"),
        "ci": snapshot["ci"],
        "comment_count": len(snapshot["comments"]),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
