#!/usr/bin/env python3
"""
PR Resolver Snapshot Script
Gathers PR metadata, CI status, and comments to decide the next fix action.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pr_resolve_contract import EXIT_CODE_FAILED

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

_SYSTEM_PATH_FALLBACK = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_PR_VIEW_FIELDS = (
    "number,title,url,isDraft,state,headRefName,headRefOid,baseRefName,mergeable,"
    "mergeStateStatus,reviewDecision,statusCheckRollup"
)
_COMMAND_COMMENT_PATTERN = re.compile(
    r"^/(review|gemini|qodo|jules|copilot|cc|re[-_ ]?run)\b",
    re.IGNORECASE,
)


def _build_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    existing_parts = [part for part in env.get("PATH", "").split(":") if part]
    for fallback in _SYSTEM_PATH_FALLBACK.split(":"):
        if fallback not in existing_parts:
            existing_parts.append(fallback)
    env["PATH"] = ":".join(existing_parts) if existing_parts else _SYSTEM_PATH_FALLBACK
    return env


def _resolve_command(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd
    executable = str(cmd[0])
    if "/" in executable:
        return [str(part) for part in cmd]
    raw_path = os.environ.get("PATH", "")
    fallback_path = (
        f"{raw_path}:{_SYSTEM_PATH_FALLBACK}" if raw_path else _SYSTEM_PATH_FALLBACK
    )
    resolved = shutil.which(executable, path=raw_path) or shutil.which(
        executable, path=fallback_path
    )
    if resolved:
        return [resolved, *[str(part) for part in cmd[1:]]]
    return [str(part) for part in cmd]


def _compact_error_details(stdout: str, stderr: str) -> str:
    return "\n".join(
        item.strip() for item in (stdout or "", stderr or "") if item.strip()
    )


def run_command(
    cmd,
    failure_hint="",
    max_attempts=3,
    initial_delay_seconds=1.0,
    max_delay_seconds=8.0,
):
    resolved_cmd = _resolve_command(cmd)
    env = _build_subprocess_env()
    for attempt in range(1, max_attempts + 1):
        try:
            completed = subprocess.run(
                resolved_cmd,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            output = completed.stdout
            stderr = completed.stderr
            if completed.returncode != 0:
                details = _compact_error_details(output, stderr)
                if attempt < max_attempts:
                    delay = min(
                        max_delay_seconds, initial_delay_seconds * (2 ** (attempt - 1))
                    )
                    print(
                        f"Retryable error on attempt {attempt}/{max_attempts} for command: {' '.join(resolved_cmd)}. Retrying in {delay:.1f}s...",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                print(
                    f"Command failed: {' '.join(resolved_cmd)}\n{failure_hint}\n{details}",
                    file=sys.stderr,
                )
                sys.exit(1)
            if output.strip() == "":
                return {}
            return json.loads(output)
        except FileNotFoundError:
            print(f"Command not found: {resolved_cmd[0]}", file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError:
            print(
                f"Command returned invalid JSON: {' '.join(resolved_cmd)}",
                file=sys.stderr,
            )
            sys.exit(1)


def run_command_optional_with_error(cmd) -> tuple[dict | list | None, str | None]:
    resolved_cmd = _resolve_command(cmd)
    try:
        completed = subprocess.run(
            resolved_cmd,
            text=True,
            capture_output=True,
            check=False,
            env=_build_subprocess_env(),
        )
    except OSError as exc:
        return None, str(exc)
    if completed.returncode != 0:
        details = _compact_error_details(completed.stdout, completed.stderr)
        return (
            None,
            f"command failed ({completed.returncode}): {' '.join(resolved_cmd)}"
            + (f"\n{details}" if details else ""),
        )
    output = completed.stdout
    if output.strip() == "":
        return {}, None
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None, f"invalid JSON from command: {' '.join(resolved_cmd)}"
    if isinstance(payload, (dict, list)):
        return payload, None
    return None, f"unsupported JSON payload type from command: {' '.join(resolved_cmd)}"


def run_command_optional(cmd) -> dict | list | None:
    payload, _ = run_command_optional_with_error(cmd)
    return payload


def _current_branch_name() -> str | None:
    resolved_cmd = _resolve_command(["git", "branch", "--show-current"])
    try:
        completed = subprocess.run(
            resolved_cmd,
            text=True,
            capture_output=True,
            check=False,
            env=_build_subprocess_env(),
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    branch = (completed.stdout or "").strip()
    if branch in {"", "HEAD"}:
        return None
    return branch


def _fetch_pr_data_from_selector(
    selector: str | None,
) -> tuple[dict | None, str | None]:
    cmd = ["gh", "pr", "view"]
    if selector:
        cmd.append(selector)
    cmd.extend(["--json", _PR_VIEW_FIELDS])
    payload, error = run_command_optional_with_error(cmd)
    if isinstance(payload, dict):
        return payload, None
    return None, error


def _discover_pr_number_from_head_branch(branch: str) -> str | None:
    payload = run_command_optional(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "all",
            "--head",
            branch,
            "--json",
            "number",
            "--limit",
            "1",
        ]
    )
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if not isinstance(first, dict):
        return None
    number = first.get("number")
    if number in {None, ""}:
        return None
    return str(number)


def fetch_pr_data(
    requested_pr_selector: str | None,
) -> tuple[dict | None, str | None, list[str]]:
    errors: list[str] = []
    current_branch = _current_branch_name() if not requested_pr_selector else None
    candidate_selectors: list[str | None] = []
    if requested_pr_selector:
        candidate_selectors.append(requested_pr_selector)
    else:
        candidate_selectors.append(None)
        if current_branch:
            candidate_selectors.append(current_branch)

    attempted_labels: set[str] = set()
    for selector in candidate_selectors:
        label = selector or "<default>"
        if label in attempted_labels:
            continue
        attempted_labels.add(label)
        pr_data, error = _fetch_pr_data_from_selector(selector)
        if pr_data:
            return pr_data, selector, errors
        if error:
            errors.append(f"{label}: {error}")

    if not requested_pr_selector and current_branch:
        discovered_selector = _discover_pr_number_from_head_branch(current_branch)
        if discovered_selector and discovered_selector not in attempted_labels:
            pr_data, error = _fetch_pr_data_from_selector(discovered_selector)
            if pr_data:
                return pr_data, discovered_selector, errors
            if error:
                errors.append(f"{discovered_selector}: {error}")

    return None, None, errors


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

    body = str(comment.get("body") or "")
    normalized_body = " ".join(body.strip().split())
    comment_type = comment.get("type")

    if comment_type == "review_comment":
        if comment.get("thread_resolved", False):
            return False, "thread_resolved"
        if comment.get("thread_outdated", False):
            return False, "thread_outdated"
        if not include_bot_review_comments and is_bot_user(comment.get("user") or ""):
            return False, "bot_review_comment_excluded"
        return True, "actionable"

    if (
        comment_type == "issue_comment"
        and normalized_body
        and _COMMAND_COMMENT_PATTERN.match(normalized_body)
    ):
        return False, "command_comment"

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
    parser.add_argument(
        "--snapshot-path",
        default="var/pr_resolver/snapshot.json",
        help="Snapshot path to write",
    )
    args = parser.parse_args()

    # 1. Fetch PR metadata with resilient selector fallback.
    pr_data, resolved_selector, pr_errors = fetch_pr_data(args.pr)
    if not isinstance(pr_data, dict):
        detail_lines = "\n".join(pr_errors[-3:]) if pr_errors else ""
        message = "Unable to resolve PR metadata. Ensure gh is authenticated and the PR exists."
        if detail_lines:
            message = f"{message}\n{detail_lines}"
        print(message, file=sys.stderr)
        sys.exit(EXIT_CODE_FAILED)

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

    snapshot_path = Path(args.snapshot_path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, indent=2))

    print(f"Snapshot written to {snapshot_path}")

    # Print a quick summary to stdout
    summary = {
        "pr_number": pr_data.get("number"),
        "pr_selector": resolved_selector or "<default>",
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
