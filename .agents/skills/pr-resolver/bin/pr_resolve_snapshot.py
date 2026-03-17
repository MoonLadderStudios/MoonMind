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

_RUNNING_CHECK_STATES = {"IN_PROGRESS", "QUEUED", "PENDING", "WAITING", "REQUESTED"}
_FAILURE_CHECK_STATES = {
    "FAILURE",
    "FAILED",
    "ERROR",
    "CANCELLED",
    "TIMED_OUT",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
    "STALE",
}


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


def run_command_optional(cmd) -> dict | list | None:
    try:
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError:
        return None
    except OSError:
        return None
    if output.strip() == "":
        return {}
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    return None


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
    - Treat issue comments and review bodies as actionable.
    - Treat review comments as actionable except resolved/outdated threads and
      bot-authored comments (unless explicitly enabled).
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
        if not include_bot_review_comments and is_bot_user(comment.get("user") or ""):
            return False, "bot_review_comment_excluded"
        return True, "actionable"

    if is_bot_user(comment.get("user")):
        return False, "bot_comment_excluded"

    if comment_type in {"issue_comment", "review"}:
        return True, "actionable"

    return False, "unsupported_type"


def _is_comment_actionable(
    comment: dict,
    *,
    include_bot_review_comments: bool = False,
) -> bool:
    actionable, _ = _classify_comment_actionability(
        comment,
        include_bot_review_comments=include_bot_review_comments,
    )
    return actionable


def summarize_comments(
    comments: list[dict],
    *,
    include_bot_review_comments: bool = True,
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
        "includeBotReviewComments": include_bot_review_comments,
        "humanCommentCount": len(human_comments),
        "botCommentCount": len(bot_comments),
        "hasActionableComments": len(actionable_comments) > 0,
        "actionableCommentIds": [c.get("id") for c in actionable_comments],
        "nonActionableReasonCounts": non_actionable_reason_counts,
        "classifiedComments": classified_comments,
    }


def _check_name(check: dict) -> str:
    return str(check.get("name") or check.get("context") or "Unknown Check").strip()


def _check_url(check: dict) -> str:
    return str(check.get("targetUrl") or check.get("detailsUrl") or "").strip()


def _check_state(check: dict) -> str:
    state = str(check.get("state") or "").strip().upper()
    status = str(check.get("status") or "").strip().upper()
    conclusion = str(check.get("conclusion") or "").strip().upper()

    if state:
        return state
    if status == "COMPLETED" and conclusion:
        return conclusion
    return conclusion or status


def _is_security_check(check: dict) -> bool:
    name = _check_name(check).upper()
    workflow = (
        str(check.get("workflowName") or check.get("workflow_name") or "")
        .strip()
        .upper()
    )
    app_node = check.get("app")
    app_slug = ""
    if isinstance(app_node, dict):
        app_slug = str(app_node.get("slug") or "").strip().lower()

    if app_slug == "github-advanced-security":
        return True
    if name == "CODEQL":
        return True
    if name.startswith("ANALYZE ("):
        return True
    if workflow == "CODEQL":
        return True
    if name.startswith("ANALYZE (") and workflow == "CODEQL":
        return True
    return False


def summarize_ci_checks(checks: list[dict]) -> dict:
    is_running = False
    has_failures = False
    failed_checks: list[dict] = []
    degraded_reasons: list[str] = []
    security_check_count = 0
    non_security_check_count = 0
    check_names: list[str] = []

    for check in checks:
        name = _check_name(check)
        check_names.append(name)
        state = _check_state(check)
        is_security = _is_security_check(check)
        if is_security:
            security_check_count += 1
        else:
            non_security_check_count += 1

        if state in _RUNNING_CHECK_STATES:
            is_running = True
        elif state in _FAILURE_CHECK_STATES:
            has_failures = True
            failed_checks.append(
                {
                    "name": name,
                    "state": state,
                    "url": _check_url(check),
                }
            )

    if len(checks) == 0:
        degraded_reasons.append("no_status_checks_reported")

    signal_quality = "ok" if len(degraded_reasons) == 0 else "degraded"
    if signal_quality != "ok":
        has_failures = True

    return {
        "isRunning": is_running,
        "hasFailures": has_failures,
        "failedChecks": failed_checks,
        "totalCheckCount": len(checks),
        "securityCheckCount": security_check_count,
        "nonSecurityCheckCount": non_security_check_count,
        "checkNames": check_names,
        "signalQuality": signal_quality,
        "degradedReasons": degraded_reasons,
        "requiredChecksKnown": False,
        "requiredChecks": [],
        "missingRequiredChecks": [],
    }


def _fetch_required_status_checks(
    *,
    pr_repo: str | None,
    base_branch: str | None,
) -> list[str] | None:
    repo = str(pr_repo or "").strip()
    branch = str(base_branch or "").strip()
    if not repo or not branch:
        return None
    payload = run_command_optional(
        ["gh", "api", f"repos/{repo}/branches/{branch}/protection"]
    )
    if not isinstance(payload, dict):
        return None
    required = payload.get("required_status_checks")
    if required is None:
        return []
    if not isinstance(required, dict):
        return []
    contexts = required.get("contexts")
    if not isinstance(contexts, list):
        return []
    return [str(item).strip() for item in contexts if str(item).strip()]


def _fetch_previous_commit_sha(
    *,
    pr_repo: str | None,
    pr_number: object,
    head_sha: str | None,
) -> str | None:
    repo = str(pr_repo or "").strip()
    number = str(pr_number or "").strip()
    normalized_head = str(head_sha or "").strip()
    if not repo or not number:
        return None

    payload = run_command_optional(
        ["gh", "api", f"repos/{repo}/pulls/{number}/commits?per_page=100"]
    )
    if not isinstance(payload, list):
        return None

    shas: list[str] = []
    for commit in payload:
        if not isinstance(commit, dict):
            continue
        sha = str(commit.get("sha") or "").strip()
        if sha:
            shas.append(sha)

    if len(shas) < 2:
        return None
    if normalized_head and shas[-1] == normalized_head:
        return shas[-2]
    if normalized_head:
        for index, sha in enumerate(shas):
            if sha == normalized_head and index > 0:
                return shas[index - 1]
    return shas[-2]


def _fetch_commit_check_runs(
    *, pr_repo: str | None, commit_sha: str | None
) -> list[dict]:
    repo = str(pr_repo or "").strip()
    sha = str(commit_sha or "").strip()
    if not repo or not sha:
        return []
    payload = run_command_optional(
        ["gh", "api", f"repos/{repo}/commits/{sha}/check-runs"]
    )
    if not isinstance(payload, dict):
        return []
    check_runs = payload.get("check_runs")
    if not isinstance(check_runs, list):
        return []
    result: list[dict] = []
    for entry in check_runs:
        if isinstance(entry, dict):
            result.append(entry)
    return result


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
            "number,title,url,isDraft,state,headRefName,headRefOid,baseRefName,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup",
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

    rollup = pr_data.get("statusCheckRollup", [])
    rollup_checks: list[dict] = []
    if isinstance(rollup, list):
        for check in rollup:
            if isinstance(check, dict):
                rollup_checks.append(check)
    ci_summary = summarize_ci_checks(rollup_checks)

    required_checks = _fetch_required_status_checks(
        pr_repo=pr_repo, base_branch=pr_data.get("baseRefName")
    )
    if required_checks is not None:
        present_check_names = set(ci_summary.get("checkNames") or [])
        missing_required = sorted(
            [
                check_name
                for check_name in required_checks
                if check_name not in present_check_names
            ]
        )
        ci_summary["requiredChecksKnown"] = True
        ci_summary["requiredChecks"] = required_checks
        ci_summary["missingRequiredChecks"] = missing_required
        if len(missing_required) > 0:
            ci_summary["hasFailures"] = True
            ci_summary["signalQuality"] = "degraded"
            degraded = list(ci_summary.get("degradedReasons") or [])
            degraded.append("missing_required_checks")
            ci_summary["degradedReasons"] = sorted(dict.fromkeys(degraded))
            ci_summary["failedChecks"].append(
                {
                    "name": "Missing required checks",
                    "state": "MISSING_REQUIRED_CHECKS",
                    "url": "",
                }
            )

    # --- HEAD SHA cross-check ---------------------------------------------------
    # The GraphQL statusCheckRollup may include stale checks from a previous
    # commit when CI for the newest commit hasn't started yet.  Cross-validate
    # by fetching REST API check-runs for the exact HEAD SHA and comparing
    # against the rollup.
    head_sha = str(pr_data.get("headRefOid") or "").strip()
    if head_sha and pr_repo:
        head_check_runs = _fetch_commit_check_runs(
            pr_repo=pr_repo, commit_sha=head_sha
        )
        head_summary = summarize_ci_checks(head_check_runs) if head_check_runs else {
            "nonSecurityCheckCount": 0,
            "isRunning": False,
            "hasFailures": False,
        }
        head_non_sec = int(head_summary.get("nonSecurityCheckCount", 0))
        rollup_non_sec = int(ci_summary.get("nonSecurityCheckCount", 0))

        if rollup_non_sec > 0 and head_non_sec == 0:
            # Rollup reports non-security checks but REST API has none for the
            # actual HEAD — the rollup is stale.  Mark CI as running so the
            # resolver waits instead of merging.
            ci_summary["isRunning"] = True
            ci_summary["signalQuality"] = "degraded"
            degraded = list(ci_summary.get("degradedReasons") or [])
            degraded.append("rollup_stale_head_sha_has_no_non_security_checks")
            ci_summary["degradedReasons"] = sorted(dict.fromkeys(degraded))
        elif head_non_sec > 0:
            # REST API has check-runs for the HEAD — use the HEAD summary as
            # the authoritative source for running / failure state.
            ci_summary["isRunning"] = bool(head_summary.get("isRunning"))
            ci_summary["hasFailures"] = bool(head_summary.get("hasFailures"))
            head_failed = head_summary.get("failedChecks") or []
            if head_failed:
                ci_summary["failedChecks"] = head_failed
        ci_summary["headShaVerified"] = head_sha
        ci_summary["headShaNonSecurityCheckCount"] = head_non_sec

    previous_sha = _fetch_previous_commit_sha(
        pr_repo=pr_repo,
        pr_number=pr_data.get("number"),
        head_sha=pr_data.get("headRefOid"),
    )
    if previous_sha:
        previous_check_runs = _fetch_commit_check_runs(
            pr_repo=pr_repo, commit_sha=previous_sha
        )
        if previous_check_runs:
            previous_summary = summarize_ci_checks(previous_check_runs)
            ci_summary["previousCommitSha"] = previous_sha
            ci_summary["previousCommitNonSecurityCheckCount"] = previous_summary.get(
                "nonSecurityCheckCount", 0
            )
            if (
                int(previous_summary.get("nonSecurityCheckCount", 0)) > 0
                and int(ci_summary.get("nonSecurityCheckCount", 0)) == 0
            ):
                ci_summary["hasFailures"] = True
                ci_summary["signalQuality"] = "degraded"
                degraded = list(ci_summary.get("degradedReasons") or [])
                degraded.append(
                    "head_missing_non_security_checks_seen_on_previous_commit"
                )
                ci_summary["degradedReasons"] = sorted(dict.fromkeys(degraded))
                ci_summary["failedChecks"].append(
                    {
                        "name": "CI signal continuity",
                        "state": "MISSING_NON_SECURITY_CHECKS_ON_HEAD",
                        "url": "",
                    }
                )

    # 3. Fetch Comments
    # .agents/skills/fix-comments/tools/get_branch_pr_comments.py should be in the root of the project
    comments_script = Path(
        ".agents/skills/fix-comments/tools/get_branch_pr_comments.py"
    )
    comments_data = {}
    if not comments_script.exists():
        print(
            f"Error: required comments helper not found: {comments_script}",
            file=sys.stderr,
        )
        sys.exit(1)
    comments_cmd = [sys.executable, str(comments_script), "--compact"]
    if pr_repo:
        comments_cmd.extend(["--repo", pr_repo])
    if args.pr:
        comments_cmd.extend(["--pr", args.pr])
    comments_data = run_command(comments_cmd, "Failed to retrieve PR comments.")

    comments = (
        comments_data.get("comments", []) if isinstance(comments_data, dict) else []
    )
    if not isinstance(comments, list):
        comments = []
    comments_summary = summarize_comments(comments, include_bot_review_comments=True)

    # 4. Construct Snapshot
    snapshot = {
        "pr": pr_data,
        "ci": ci_summary,
        "commentsFetch": {
            "succeeded": True,
            "source": str(comments_script),
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
