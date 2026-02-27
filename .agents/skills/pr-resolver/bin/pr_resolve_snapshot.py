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


def normalize_user(login: str | None) -> str:
    return (login or "").lower().strip()


def is_bot_user(login: str | None) -> bool:
    user = normalize_user(login)
    return user.endswith("[bot]") or user == "github-actions[bot]"


def _classify_comment_actionability(
    comment: dict,
    *,
    include_bot_review_comments: bool = False,
) -> tuple[bool, str]:
    """Determine whether a comment requires action.

    Actionability rules are intentionally simple and deterministic:
    - Ignore comments with empty bodies.
    - Ignore review comments only when explicitly marked resolved/outdated.
    - Treat issue comments, review comments, and review bodies as actionable,
      regardless of whether they were authored by a bot or human.
    - Ignore unsupported/unknown comment types.
    """
    if not (comment.get("body") or "").strip():
        return False, "empty_body"

    comment_type = comment.get("type")

    if comment_type == "review_comment":
        if comment.get("thread_resolved", False):
            return False, "thread_resolved"
        if comment.get("thread_outdated", False):
            return False, "thread_outdated"
        if (
            not include_bot_review_comments
            and is_bot_user(comment.get("user") or "")
        ):
            return False, "bot_review_comment"
        return True, "actionable"

    if comment_type in {"issue_comment", "review"}:
        return True, "actionable"

    return False, "unsupported_type"


def _is_comment_actionable(
    comment: dict,
    *,
    include_bot_review_comments: bool = False,
) -> bool:
    actionable, _reason = _classify_comment_actionability(
        comment,
        include_bot_review_comments=include_bot_review_comments,
    )
    return actionable


def summarize_comments(
    comments: list[dict],
    *,
    include_bot_review_comments: bool = False,
) -> dict:
    review_comments = [c for c in comments if c.get("type") == "review_comment"]
    issue_comments = [c for c in comments if c.get("type") == "issue_comment"]
    review_bodies = [c for c in comments if c.get("type") == "review"]

    human_comments = [c for c in comments if not is_bot_user(c.get("user") or "")]
    bot_comments = [c for c in comments if is_bot_user(c.get("user") or "")]

    actionable_comments: list[dict] = []
    non_actionable_reason_counts: dict[str, int] = {}
    classified_comments: list[dict] = []

    for comment in comments:
        actionable, reason = _classify_comment_actionability(
            comment,
            include_bot_review_comments=include_bot_review_comments,
        )
        if actionable:
            actionable_comments.append(comment)
        else:
            non_actionable_reason_counts[reason] = (
                non_actionable_reason_counts.get(reason, 0) + 1
            )

        classified_comments.append(
            {
                "id": comment.get("id"),
                "type": comment.get("type"),
                "user": comment.get("user"),
                "url": comment.get("url"),
                "path": comment.get("path"),
                "line": comment.get("line"),
                "actionable": actionable,
                "reason": reason,
            }
        )

    return {
        "classificationVersion": 2,
        "total": len(comments),
        "reviewCommentCount": len(review_comments),
        "issueCommentCount": len(issue_comments),
        "reviewBodyCount": len(review_bodies),
        "actionableCommentCount": len(actionable_comments),
        "humanCommentCount": len(human_comments),
        "botCommentCount": len(bot_comments),
        "includeBotReviewComments": include_bot_review_comments,
        "hasActionableComments": len(actionable_comments) > 0,
        "actionableCommentIds": [c.get("id") for c in actionable_comments],
        "nonActionableReasonCounts": non_actionable_reason_counts,
        "classifiedComments": classified_comments,
    }


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

    comments = (
        comments_data.get("comments", []) if isinstance(comments_data, dict) else []
    )
    if not isinstance(comments, list):
        comments = []
    comments_summary = summarize_comments(comments, include_bot_review_comments=True)

    # 4. Construct Snapshot
    snapshot = {
        "pr": pr_data,
        "ci": {
            "isRunning": ci_is_running,
            "hasFailures": ci_has_failures,
            "failedChecks": failed_checks,
        },
        "comments": comments,
        "commentsSummary": comments_summary,
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
        "actionable_comment_count": comments_summary.get("actionableCommentCount", 0),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
