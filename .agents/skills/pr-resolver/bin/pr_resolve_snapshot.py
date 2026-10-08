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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
for _package_root in (SCRIPT_DIR.parent / "lib", *SCRIPT_DIR.parents):
    if (_package_root / "pr_resolver_core").is_dir():
        sys.path.insert(0, str(_package_root))
        break

from pr_resolve_contract import EXIT_CODE_FAILED  # noqa: E402

from pr_resolver_core.code_hosts import (  # noqa: E402
    ensure_github_only_selector,
)
from pr_resolver_core.review_providers import (  # noqa: E402
    is_low_severity_only_finding,
    latest_review_reply,
    latest_review_request,
    resolve_automated_review_provider,
)

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
_MENTION_COMMAND_ONLY_COMMENT_PATTERN = re.compile(
    r"^@(codex|claude|gemini|jules|qodo|copilot)\s+"
    r"(review|address\s+that\s+feedback)\.?$",
    re.IGNORECASE,
)
_CODEX_REVIEW_GRACE_TIMEOUT_SECONDS = 600
_CODEX_REVIEW_GRACE_POLL_SECONDS = 60
_KNOWN_AUTOMATION_USERS = {
    "chatgpt-codex-connector",
    "gemini-code-assist",
    "github-actions",
}
_UTC = timezone.utc
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)
_NON_VISIBLE_COMMENT_REASONS = {
    "addressed_in_ledger",
    "command_comment",
    "empty_body",
    "thread_outdated",
    "thread_resolved",
}

def _env_flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _build_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    existing_parts = [part for part in env.get("PATH", "").split(":") if part]
    for fallback in _SYSTEM_PATH_FALLBACK.split(":"):
        if fallback not in existing_parts:
            existing_parts.append(fallback)
    env["PATH"] = ":".join(existing_parts) if existing_parts else _SYSTEM_PATH_FALLBACK
    return env


def _sibling_skill_file(
    script_dir: Path, skill_name: str, *relative_parts: str
) -> Path:
    """Resolve a required skill file from the active snapshot root."""

    return script_dir.parent.parent.joinpath(skill_name, *relative_parts)


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
    paginated=False,
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
            if output.strip() == "" and not paginated:
                return {}
            if paginated:
                return _decode_paginated_records(output)
            return json.loads(output)
        except FileNotFoundError:
            print(f"Command not found: {resolved_cmd[0]}", file=sys.stderr)
            sys.exit(1)
        except ValueError:
            print(
                f"Command returned invalid JSON: {' '.join(resolved_cmd)}",
                file=sys.stderr,
            )
            sys.exit(1)


def run_command_optional_with_error(
    cmd, *, paginated=False, records_key=None
) -> tuple[dict | list | None, str | None]:
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
    if output.strip() == "" and not paginated:
        return {}, None
    try:
        payload = (
            _decode_paginated_records(output, records_key=records_key)
            if paginated
            else json.loads(output)
        )
    except ValueError:
        return None, f"invalid JSON from command: {' '.join(resolved_cmd)}"
    if isinstance(payload, (dict, list)):
        return payload, None
    return None, f"unsupported JSON payload type from command: {' '.join(resolved_cmd)}"


def run_command_optional(
    cmd, *, paginated=False, records_key=None
) -> dict | list | None:
    payload, _ = run_command_optional_with_error(
        cmd, paginated=paginated, records_key=records_key
    )
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
    stripped = user[: -len("[bot]")] if user.endswith("[bot]") else user
    return user.endswith("[bot]") or stripped in _KNOWN_AUTOMATION_USERS

def _strip_bot_suffix(login: str | None) -> str:
    user = normalize_user(login)
    if user.endswith("[bot]"):
        return user[: -len("[bot]")]
    return user

def _is_gemini_code_assist_user(login: str | None) -> bool:
    return _strip_bot_suffix(login) == "gemini-code-assist"

def _utc_now() -> datetime:
    return datetime.now(_UTC)

def _is_gemini_no_feedback_comment(comment: dict, normalized_body: str) -> bool:
    if not _is_gemini_code_assist_user(comment.get("user")):
        return False
    if comment.get("type") not in {"issue_comment", "review"}:
        return False
    body = normalized_body.lower()
    return "no review comments" in body or "no feedback to provide" in body

def _parse_utc_timestamp(value: object) -> datetime | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_UTC)
    return parsed.astimezone(_UTC)

def _load_previous_codex_review_grace(snapshot_path: Path) -> dict | None:
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    comments_summary = payload.get("commentsSummary")
    if not isinstance(comments_summary, dict):
        return None
    grace = comments_summary.get("codexReviewGrace")
    return grace if isinstance(grace, dict) else None

def _comment_identity(comment: dict, *, head_commit_sha: str | None) -> str:
    parts = [
        str(head_commit_sha or "").strip(),
        str(comment.get("id") or "").strip(),
        str(comment.get("created_at") or comment.get("updated_at") or "").strip(),
        str(comment.get("user") or "").strip(),
        str(comment.get("type") or "").strip(),
    ]
    return "|".join(parts)

def _classify_comment_actionability(
    comment: dict,
    *,
    include_bot_review_comments: bool = False,
    head_commit_sha: str | None = None,
) -> tuple[bool, str]:
    """Determine whether a comment requires action.

    Actionability rules are intentionally simple and deterministic:
    - Ignore comments with empty bodies.
    - Ignore automated-review inline findings that carry only
      P2/medium-or-below severity: they end the Fix and Review Loop instead
      of triggering remediation or another review request. Only P0/critical
      or P1/high findings keep the loop going. The threshold applies only
      to bot-authored review comments from the latest automated review
      round; human issue comments, review bodies, and other discussion
      stay actionable even when they mention a low priority. Comments
      without an explicit severity marker stay actionable.
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
        if is_bot_user(comment.get("user") or "") and is_low_severity_only_finding(
            body
        ):
            return False, "low_severity_finding"
        if not include_bot_review_comments and is_bot_user(comment.get("user") or ""):
            return False, "bot_review_comment_excluded"
        return True, "actionable"

    if (
        comment_type == "issue_comment"
        and normalized_body
        and (
            _COMMAND_COMMENT_PATTERN.match(normalized_body)
            or _MENTION_COMMAND_ONLY_COMMENT_PATTERN.match(normalized_body)
        )
    ):
        return False, "command_comment"

    if _is_gemini_no_feedback_comment(comment, normalized_body):
        return False, "gemini_no_feedback_review"

    if _is_gemini_code_assist_user(comment.get("user")) and comment_type in {
        "issue_comment",
        "review",
    }:
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
    head_commit_sha: str | None = None,
) -> bool:
    actionable, _ = _classify_comment_actionability(
        comment,
        include_bot_review_comments=include_bot_review_comments,
        head_commit_sha=head_commit_sha,
    )
    return actionable

_LEDGER_CANDIDATE_PATHS = [
    Path("artifacts/pr_resolver_addressed_comments.json"),
    Path("var/pr_comments/comment-resolution-ledger.json"),
]
_ACCEPTED_DISPOSITIONS = {"addressed", "not-applicable"}
_DEFERRED_DISPOSITIONS = {"deferred", "deferred-with-reason", "unfixable"}

def _extract_ids_from_entries(
    entries: list,
    *,
    id_keys: tuple[str, ...] = ("id", "comment_id"),
    dispositions: set[str] | None = None,
) -> set[int]:
    """Extract comment IDs from a list of ledger entry dicts.

    Accepts both ``disposition`` and ``status`` field names, and both
    ``id`` and ``comment_id`` key names to maximise compatibility.
    """
    accepted = dispositions if dispositions is not None else _ACCEPTED_DISPOSITIONS
    ids: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        disposition = (
            str(entry.get("disposition") or entry.get("status") or "")
            .strip()
            .lower()
        )
        if disposition not in accepted:
            continue
        for key in id_keys:
            cid = entry.get(key)
            if isinstance(cid, int):
                ids.add(cid)
                break
    return ids

def _load_ledger_comment_ids(
    ledger_path: Path | None = None,
    *,
    dispositions: set[str] | None = None,
) -> set[int]:
    """Load comment IDs recorded with one of *dispositions* in a local ledger.

    Searches multiple candidate paths and accepts both array and object-wrapped
    ledger formats so that different agent skill outputs are all recognised.
    """
    candidates: list[Path] = []
    if ledger_path:
        candidates.append(ledger_path)
    candidates.extend(_LEDGER_CANDIDATE_PATHS)

    # Also glob var/pr_comments/ for any JSON files.
    pr_comments_dir = Path("var/pr_comments")
    if pr_comments_dir.is_dir():
        for p in sorted(pr_comments_dir.glob("*.json")):
            if p not in candidates:
                candidates.append(p)

    ids: set[int] = set()
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        # Array format: [{"id": ..., "disposition": ...}, ...]
        if isinstance(payload, list):
            ids.update(
                _extract_ids_from_entries(payload, dispositions=dispositions)
            )
        # Object format: {"resolutions": [...]} or {"comments": [...]}
        elif isinstance(payload, dict):
            for key in ("resolutions", "comments"):
                nested = payload.get(key)
                if isinstance(nested, list):
                    ids.update(
                        _extract_ids_from_entries(nested, dispositions=dispositions)
                    )
    return ids


def _load_addressed_comment_ids(ledger_path: Path | None = None) -> set[int]:
    """Comment IDs the ledger marks as addressed or not-applicable."""

    return _load_ledger_comment_ids(
        ledger_path,
        dispositions=_ACCEPTED_DISPOSITIONS,
    )


def _load_deferred_comment_ids(ledger_path: Path | None = None) -> set[int]:
    """Comment IDs the ledger explicitly deferred or reported as unfixable."""

    return _load_ledger_comment_ids(
        ledger_path,
        dispositions=_DEFERRED_DISPOSITIONS,
    )

def summarize_comments(
    comments: list[dict],
    *,
    include_bot_review_comments: bool = True,
    addressed_comment_ids: set[int] | None = None,
    deferred_comment_ids: set[int] | None = None,
    head_commit_sha: str | None = None,
) -> dict:
    review_comments = [c for c in comments if c.get("type") == "review_comment"]
    issue_comments = [c for c in comments if c.get("type") == "issue_comment"]
    review_bodies = [c for c in comments if c.get("type") == "review"]

    human_comments = [c for c in comments if not is_bot_user(c.get("user") or "")]
    bot_comments = [c for c in comments if is_bot_user(c.get("user") or "")]

    actionable_comments: list[dict] = []
    non_actionable_reason_counts: dict[str, int] = {}
    classified_comments: list[dict] = []

    resolved_ids = addressed_comment_ids or set()

    for comment in comments:
        cid = comment.get("id")
        if (
            isinstance(cid, int)
            and cid in resolved_ids
            and comment.get("type") != "review_comment"
        ):
            actionable = False
            reason = "addressed_in_ledger"
        else:
            actionable, reason = _classify_comment_actionability(
                comment,
                include_bot_review_comments=include_bot_review_comments,
                head_commit_sha=head_commit_sha,
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

    present_ids = {
        comment.get("id")
        for comment in comments
        if isinstance(comment.get("id"), int)
    }
    deferred_present = sorted(
        cid for cid in (deferred_comment_ids or set()) if cid in present_ids
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
        "deferredCommentIds": deferred_present,
        "hasDeferredComments": bool(deferred_present),
        "nonActionableReasonCounts": non_actionable_reason_counts,
        "classifiedComments": classified_comments,
    }

def apply_codex_review_grace(
    comments_summary: dict,
    comments: list[dict],
    *,
    head_commit_sha: str | None,
    previous_grace: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Annotate a Gemini-only review state with a bounded Codex wait window."""

    if comments_summary.get("hasActionableComments") is True:
        comments_summary["codexReviewGrace"] = {
            "active": False,
            "reason": "not_gemini_only",
        }
        return comments_summary

    classified_comments = comments_summary.get("classifiedComments")
    visible_comments: list[dict] = []
    if isinstance(classified_comments, list) and len(classified_comments) == len(comments):
        for comment, classified in zip(comments, classified_comments):
            if not isinstance(comment, dict) or not isinstance(classified, dict):
                continue
            reason = str(classified.get("reason") or "").strip()
            if reason in _NON_VISIBLE_COMMENT_REASONS:
                continue
            visible_comments.append(comment)
    else:
        visible_comments = [c for c in comments if isinstance(c, dict)]

    if len(visible_comments) != 1:
        comments_summary["codexReviewGrace"] = {
            "active": False,
            "reason": "not_gemini_only",
        }
        return comments_summary

    only_comment = visible_comments[0]
    if not _is_gemini_code_assist_user(only_comment.get("user")):
        comments_summary["codexReviewGrace"] = {
            "active": False,
            "reason": "not_gemini_only",
        }
        return comments_summary

    now_utc = (now or _utc_now()).astimezone(_UTC)
    key = _comment_identity(only_comment, head_commit_sha=head_commit_sha)
    started_at = None
    if isinstance(previous_grace, dict) and previous_grace.get("key") == key:
        started_at = _parse_utc_timestamp(previous_grace.get("startedAt"))
    if started_at is None:
        started_at = now_utc
    expires_at = started_at + timedelta(
        seconds=_CODEX_REVIEW_GRACE_TIMEOUT_SECONDS
    )
    remaining_seconds = max(0, int((expires_at - now_utc).total_seconds()))
    active = remaining_seconds > 0
    comments_summary["codexReviewGrace"] = {
        "active": active,
        "expired": not active,
        "reason": "gemini_only_review",
        "key": key,
        "startedAt": started_at.isoformat(),
        "expiresAt": expires_at.isoformat(),
        "timeoutSeconds": _CODEX_REVIEW_GRACE_TIMEOUT_SECONDS,
        "pollSeconds": _CODEX_REVIEW_GRACE_POLL_SECONDS,
        "remainingSeconds": remaining_seconds,
        "commentUser": only_comment.get("user"),
        "commentType": only_comment.get("type"),
    }
    return comments_summary

def _fetch_head_commit_timestamp(*, pr_repo: str | None, head_sha: str) -> datetime | None:
    repo = str(pr_repo or "").strip()
    sha = str(head_sha or "").strip()
    if not repo or not sha:
        return None
    payload = run_command_optional(["gh", "api", f"repos/{repo}/commits/{sha}"])
    if not isinstance(payload, dict):
        return None
    commit = payload.get("commit")
    if not isinstance(commit, dict):
        return None
    for key in ("committer", "author"):
        actor = commit.get(key)
        if isinstance(actor, dict):
            parsed = _parse_utc_timestamp(actor.get("date"))
            if parsed is not None:
                return parsed
    return None


def _fetch_review_collection(endpoint: str) -> list[dict]:
    """Read every page or fail with the actual collection error.

    Missing review evidence must never be converted into a pending review.
    The same contract applies to review and reaction completion evidence.
    """
    return run_command(
        ["gh", "api", "--paginate", endpoint],
        "Unable to collect automated review evidence; repair GitHub access or tooling and retry.",
        paginated=True,
    )


def _fetch_pull_request_reviews(*, pr_repo: str | None, pr_number: object) -> list[dict]:
    return _fetch_review_collection(
        f"repos/{pr_repo}/pulls/{pr_number}/reviews?per_page=100"
    )


def _fetch_comment_reactions(*, pr_repo: str | None, comment_id: object) -> list[dict]:
    return _fetch_review_collection(
        f"repos/{pr_repo}/issues/comments/{comment_id}/reactions?per_page=100"
    )


def _fetch_pr_reactions(*, pr_repo: str | None, pr_number: object) -> list[dict]:
    return _fetch_review_collection(
        f"repos/{pr_repo}/issues/{pr_number}/reactions?per_page=100"
    )


def build_automated_review_evidence(
    *,
    provider: object,
    require_fresh_review: bool,
    pr_repo: str | None,
    pr_number: object,
    head_sha: str,
    comments: list[dict],
    reviews: list[dict] | None = None,
    head_committed_at: datetime | None = None,
    reactions_for_request: list[dict] | None = None,
    reactions_for_pr: list[dict] | None = None,
) -> dict:
    """Collect portable evidence about the configured automated reviewer.

    The Skill owns this decision in every host: a review only counts for the
    current head when the provider identity submitted it against that exact
    commit, or answered the request for the unchanged head with a qualified
    clean comment or reaction. PR-level results must postdate the request.
    When the provider's latest answer to the request is a refusal (for example
    a usage limit), the request is reported failed rather than pending.
    """

    record = resolve_automated_review_provider(provider)
    if record is None or not require_fresh_review:
        return {
            "enabled": False,
            "provider": record.provider if record is not None else "",
            "reason": "review_loop_disabled",
        }

    normalized_head = str(head_sha or "").strip()
    if head_committed_at is None:
        head_committed_at = _fetch_head_commit_timestamp(
            pr_repo=pr_repo, head_sha=normalized_head
        )

    request = latest_review_request(
        record, comments, head_sha=normalized_head, not_before=head_committed_at
    )
    request_comment = request.comment if request is not None else None
    request_at = request.created_at if request is not None else None

    if reviews is None:
        reviews = _fetch_pull_request_reviews(pr_repo=pr_repo, pr_number=pr_number)

    provider_logins = set(record.reviewer_logins)
    provider_reviews: list[tuple[datetime | None, dict]] = []
    for review in reviews:
        user = review.get("user") if isinstance(review.get("user"), dict) else {}
        if _strip_bot_suffix(user.get("login")) not in provider_logins:
            continue
        if str(review.get("state") or "").upper() not in {
            "APPROVED",
            "COMMENTED",
            "CHANGES_REQUESTED",
        }:
            continue
        if _parse_utc_timestamp(review.get("submitted_at")) is None:
            continue
        provider_reviews.append(
            (_parse_utc_timestamp(review.get("submitted_at")), review)
        )
    provider_reviews.sort(key=lambda item: item[0] or _EPOCH_UTC)
    latest_provider_review = provider_reviews[-1][1] if provider_reviews else None

    fresh_review = None
    for submitted_at, review in provider_reviews:
        if request_at is not None and submitted_at <= request_at:
            continue
        commit_id = str(review.get("commit_id") or "").strip()
        if commit_id:
            if normalized_head and commit_id == normalized_head:
                fresh_review = review
            continue
        # GitHub omitted the reviewed commit: fall back to "submitted after the
        # request that was made for this head".
        if (
            request_at is not None
            and submitted_at is not None
            and submitted_at > request_at
        ):
            fresh_review = review

    completion_kind = ""
    completion_id = None
    completed_at = None
    if fresh_review is not None:
        completion_kind = "review"
        completion_id = fresh_review.get("id")
        completed_at = str(fresh_review.get("submitted_at") or "").strip() or None

    reply = None
    if fresh_review is None and request_comment is not None:
        reply = latest_review_reply(
            record,
            (
                comment
                for comment in comments
                if isinstance(comment, dict) and comment.get("type") == "issue_comment"
            ),
            requested_at=request_at,
            head_sha=normalized_head,
            request_comment_id=request_comment.get("id"),
        )
        if reply is not None and not reply.failure_class:
            completion_kind = "issue_comment"
            completion_id = reply.comment.get("id")
            completed_at = reply.comment.get("created_at")

    if not completion_kind and request_comment is not None:
        if reactions_for_request is None:
            reactions_for_request = _fetch_comment_reactions(
                pr_repo=pr_repo, comment_id=request_comment.get("id")
            )
        for reaction in reactions_for_request:
            user = reaction.get("user") if isinstance(reaction.get("user"), dict) else {}
            if _strip_bot_suffix(user.get("login")) not in provider_logins:
                continue
            if str(reaction.get("content") or "") not in record.clean_review_reactions:
                continue
            completion_kind = "reaction"
            completion_id = reaction.get("id")
            completed_at = str(reaction.get("created_at") or "").strip() or None
            break

        if not completion_kind:
            if reactions_for_pr is None:
                reactions_for_pr = _fetch_pr_reactions(
                    pr_repo=pr_repo, pr_number=pr_number
                )
            for reaction in reactions_for_pr:
                user = (
                    reaction.get("user")
                    if isinstance(reaction.get("user"), dict)
                    else {}
                )
                created_at = _parse_utc_timestamp(reaction.get("created_at"))
                if (
                    _strip_bot_suffix(user.get("login")) in provider_logins
                    and str(reaction.get("content") or "")
                    in record.clean_review_reactions
                    and created_at is not None
                    and request_at is not None
                    and created_at > request_at
                ):
                    completion_kind = "reaction"
                    completion_id = reaction.get("id")
                    completed_at = reaction.get("created_at")
                    break

    fresh = bool(completion_kind)
    # Without completion, a provider refusal as the latest answer ends the
    # request instead of leaving it pending forever.
    failure = reply if not fresh and reply is not None and reply.failure_class else None
    evidence: dict = {
        "enabled": True,
        "provider": record.provider,
        "command": record.command,
        "reviewerLogins": sorted(provider_logins),
        "headSha": normalized_head,
        "freshReviewForHead": fresh,
        "requestPending": (
            not fresh and request_comment is not None and failure is None
        ),
        "requestFailed": failure is not None,
        "requestFailure": (
            {
                "kind": "issue_comment",
                "id": failure.comment.get("id"),
                "failedAt": failure.comment.get("created_at"),
                "providerErrorClass": failure.failure_class,
            }
            if failure is not None
            else None
        ),
        "requestCommentId": request_comment.get("id") if request_comment else None,
        "requestedAt": request_at.isoformat() if request_at is not None else None,
        "headCommittedAt": (
            head_committed_at.isoformat() if head_committed_at is not None else None
        ),
        "completionKind": completion_kind or None,
        "completionId": completion_id,
        "completedAt": completed_at,
    }
    if latest_provider_review is not None:
        evidence["latestProviderReview"] = {
            "id": latest_provider_review.get("id"),
            "state": latest_provider_review.get("state"),
            "commitId": latest_provider_review.get("commit_id"),
            "submittedAt": latest_provider_review.get("submitted_at"),
        }
    return evidence


def build_progress_signature(
    *,
    head_sha: str,
    comments_summary: dict,
) -> str:
    """Stable signature of "what still needs work" for this head.

    The owning workflow compares consecutive signatures to detect a remediation
    loop that is not making progress.
    """

    actionable = sorted(
        str(item)
        for item in (comments_summary.get("actionableCommentIds") or [])
        if item is not None
    )
    deferred = sorted(
        str(item)
        for item in (comments_summary.get("deferredCommentIds") or [])
        if item is not None
    )
    parts = [
        str(head_sha or "").strip(),
        ",".join(actionable),
        ",".join(deferred),
    ]
    return "|".join(parts)


def _check_name(check: dict) -> str:
    return str(check.get("name") or check.get("context") or "Unknown Check").strip()

def _check_url(check: dict) -> str:
    return str(
        check.get("targetUrl")
        or check.get("target_url")
        or check.get("detailsUrl")
        or check.get("details_url")
        or ""
    ).strip()


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
    has_authoritative_failures = False
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
            has_authoritative_failures = True
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
        "hasAuthoritativeFailures": has_authoritative_failures,
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

# GitHub Actions platform failures that no change to the PR can fix. Only the
# platform's own wording counts, so an application error that merely mentions a
# server error stays a real CI failure.
_INFRASTRUCTURE_FAILURE_PATTERNS = (
    (
        "artifact_storage_quota",
        re.compile(r"artifact storage quota has been hit", re.IGNORECASE),
    ),
    (
        "runner_lost",
        re.compile(
            r"runner has received a shutdown signal"
            r"|runner: .+ lost communication with the server",
            re.IGNORECASE,
        ),
    ),
    (
        "github_service_error",
        re.compile(
            r"GitHub Actions has encountered an internal error"
            r"|Failed to (?:Create|Finalize)Artifact: .*\((?:500|502|503|504)\)",
            re.IGNORECASE,
        ),
    ),
)
# A job that failed only because an upstream job in the same run could not
# upload its artifact (for example a CI gate reading a result manifest).
_MISSING_UPSTREAM_ARTIFACT_PATTERN = re.compile(
    r"Unable to download artifact\(s\): Artifact not found", re.IGNORECASE
)
_GENERIC_EXIT_PATTERN = re.compile(
    r"^Process completed with exit code \d+\.?$", re.IGNORECASE
)
_ACTIONS_JOB_URL_PATTERN = re.compile(r"/actions/runs/(\d+)/job/(\d+)")


def _classify_failure_annotations(annotations: list[dict]) -> str | None:
    """Return the infrastructure kind when every failure annotation is one.

    ``missing_upstream_artifact`` is provisional: it only counts when another
    job in the same run has a confirmed infrastructure failure. A generic step
    exit is tolerated only beside that gate case; next to a platform failure it
    may be a real test failure followed by an ``if: always()`` upload, so the
    job fails closed.
    """

    messages = [
        str(item.get("message") or "").strip()
        for item in annotations
        if isinstance(item, dict)
        and str(item.get("annotation_level") or "").strip().lower() == "failure"
    ]
    kinds: set[str] = set()
    generic_exit = False
    for message in messages:
        if _GENERIC_EXIT_PATTERN.match(message):
            generic_exit = True
            continue
        kind = next(
            (
                name
                for name, pattern in _INFRASTRUCTURE_FAILURE_PATTERNS
                if pattern.search(message)
            ),
            None,
        )
        if kind is None and _MISSING_UPSTREAM_ARTIFACT_PATTERN.search(message):
            kind = "missing_upstream_artifact"
        if kind is None:
            return None
        kinds.add(kind)
    platform_kinds = sorted(kinds - {"missing_upstream_artifact"})
    if platform_kinds:
        return None if generic_exit else platform_kinds[0]
    if kinds:
        return "missing_upstream_artifact"
    return None


def summarize_ci_infrastructure(
    failed_check_runs: list[dict],
    *,
    fetch_annotations,
    fetch_run,
) -> dict:
    """Decide whether every failed check is a GitHub Actions platform failure.

    Fails closed: a check without failure annotations, outside GitHub Actions,
    or with any non-platform failure keeps the PR on the ``ci_failures`` path.
    """

    classified: list[dict] = []
    for check in failed_check_runs:
        match = _ACTIONS_JOB_URL_PATTERN.search(
            str(check.get("details_url") or check.get("detailsUrl") or "")
        )
        check_id = check.get("id")
        if match is None or check_id is None:
            return {"infrastructureOnly": False}
        annotations = fetch_annotations(check_id)
        kind = _classify_failure_annotations(
            annotations if isinstance(annotations, list) else []
        )
        if kind is None:
            return {"infrastructureOnly": False}
        message = next(
            (
                str(item.get("message") or "").strip()
                for item in annotations
                if str(item.get("annotation_level") or "").lower() == "failure"
                and not _GENERIC_EXIT_PATTERN.match(
                    str(item.get("message") or "").strip()
                )
            ),
            "",
        )
        classified.append(
            {
                "name": _check_name(check),
                "kind": kind,
                "message": message[:300],
                "runId": int(match.group(1)),
            }
        )

    platform_runs = {
        item["runId"]
        for item in classified
        if item["kind"] != "missing_upstream_artifact"
    }
    if not platform_runs or any(
        item["runId"] not in platform_runs for item in classified
    ):
        return {"infrastructureOnly": False}

    runs: list[dict] = []
    for run_id in sorted(platform_runs):
        run = fetch_run(run_id)
        if not isinstance(run, dict):
            return {"infrastructureOnly": False}
        try:
            run_attempt = int(run.get("run_attempt") or 1)
        except (TypeError, ValueError):
            return {"infrastructureOnly": False}
        runs.append(
            {
                "runId": run_id,
                "runAttempt": run_attempt,
                "status": str(run.get("status") or "").strip().lower(),
                "updatedAt": str(run.get("updated_at") or "").strip(),
                "kinds": sorted(
                    {item["kind"] for item in classified if item["runId"] == run_id}
                ),
            }
        )
    return {
        "infrastructureOnly": True,
        "infrastructureFailures": classified,
        "infrastructureRuns": runs,
    }


def _decode_paginated_records(output: str, *, records_key=None) -> list[dict]:
    """Flatten consecutive ``gh api --paginate`` response documents.

    The distribution-provided CLI predates ``--slurp``, so pages are decoded
    one after another rather than requiring a single document. Array endpoints
    supply records directly; object endpoints name their required records key.
    """

    decoder = json.JSONDecoder()
    records: list[dict] = []
    remaining = output.strip()
    if not remaining:
        raise ValueError("empty paginated response")
    while remaining:
        page, end = decoder.raw_decode(remaining)
        if records_key is not None:
            if not isinstance(page, dict):
                raise ValueError("expected an object on every page")
            page = page.get(records_key)
        if not isinstance(page, list) or any(
            not isinstance(item, dict) for item in page
        ):
            raise ValueError("expected an array of records on every page")
        records.extend(page)
        remaining = remaining[end:].strip()
    return records


def _fetch_check_run_annotations(*, pr_repo: str, check_id: object) -> list[dict]:
    """Every annotation page, or nothing; a partial view must not pass as complete."""

    cmd = _resolve_command(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{pr_repo}/check-runs/{check_id}/annotations?per_page=100",
        ]
    )
    try:
        completed = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            env=_build_subprocess_env(),
        )
        if completed.returncode != 0:
            return []
        return _decode_paginated_records(completed.stdout)
    except (OSError, ValueError):
        return []


def _fetch_actions_run(*, pr_repo: str, run_id: int) -> dict | None:
    payload = run_command_optional(["gh", "api", f"repos/{pr_repo}/actions/runs/{run_id}"])
    return payload if isinstance(payload, dict) else None


def _fetch_required_status_checks(
    *,
    pr_repo: str | None,
    base_branch: str | None,
) -> list[str] | None:
    repo = str(pr_repo or "").strip()
    branch = str(base_branch or "").strip()
    if not repo or not branch:
        return None
    # Branch metadata remains readable when protection/rules endpoints are
    # unavailable on an unprotected private repository. A 403 alone never
    # means there are no requirements; the explicit provider observation does.
    from urllib.parse import quote

    from pr_resolver_core.github_checks import required_check_contexts

    branch = quote(branch, safe="")
    branch_data = run_command_optional(["gh", "api", f"repos/{repo}/branches/{branch}"])
    if isinstance(branch_data, dict) and branch_data.get("protected") is False:
        return required_check_contexts(branch_data, None, None)
    payload = run_command_optional(
        ["gh", "api", f"repos/{repo}/branches/{branch}/protection"]
    )
    rules = run_command_optional(["gh", "api", f"repos/{repo}/rules/branches/{branch}"])
    return required_check_contexts(branch_data, payload, rules)


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
) -> list[dict] | None:
    repo = str(pr_repo or "").strip()
    sha = str(commit_sha or "").strip()
    if not repo or not sha:
        return None
    payload = run_command_optional(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/commits/{sha}/check-runs?filter=latest&per_page=100",
        ],
        paginated=True,
        records_key="check_runs",
    )
    return payload if isinstance(payload, list) else None


def _fetch_commit_statuses(
    *, pr_repo: str | None, commit_sha: str | None
) -> list[dict] | None:
    if not pr_repo or not commit_sha:
        return None
    payload = run_command_optional(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{pr_repo}/commits/{commit_sha}/statuses?per_page=100",
        ],
        paginated=True,
    )
    if not isinstance(payload, list):
        return None
    latest: dict[str, dict] = {}
    for status in payload:
        if isinstance(status, dict) and str(status.get("context") or "").strip():
            # The provider orders statuses newest first across pages.
            latest.setdefault(str(status["context"]).strip(), dict(status))
    return list(latest.values())


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
    parser.add_argument(
        "--review-provider",
        default=os.environ.get("PR_RESOLVER_REVIEW_PROVIDER", ""),
        help=(
            "Automated review provider that must review every head SHA "
            "(for example 'codex'). Empty or 'none' disables the review loop."
        ),
    )
    parser.add_argument(
        "--require-fresh-review",
        dest="require_fresh_review",
        action="store_true",
        default=_env_flag("PR_RESOLVER_REQUIRE_FRESH_REVIEW"),
        help="Require a fresh automated review for the current head SHA.",
    )
    parser.add_argument(
        "--no-require-fresh-review",
        dest="require_fresh_review",
        action="store_false",
        help="Do not require a fresh automated review for the current head SHA.",
    )
    args = parser.parse_args()
    snapshot_path = Path(args.snapshot_path)

    # GitHub-only resolver: reject GitLab input before any paid execution.
    # ValueError covers the portable UnsupportedCodeHostError; RuntimeError
    # covers the MoonMind GitLabToolError contract when moonmind is installed.
    try:
        ensure_github_only_selector(args.pr)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(EXIT_CODE_FAILED)

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
    required_checks = _fetch_required_status_checks(
        pr_repo=pr_repo, base_branch=pr_data.get("baseRefName")
    )

    # Build one authoritative HEAD observation from both provider surfaces.
    # Check-runs remain gating even on an unprotected branch. Legacy statuses
    # gate when required, or when requirements could not be established.
    head_sha = str(pr_data.get("headRefOid") or "").strip()
    fetched_runs = _fetch_commit_check_runs(pr_repo=pr_repo, commit_sha=head_sha)
    fetched_statuses = _fetch_commit_statuses(pr_repo=pr_repo, commit_sha=head_sha)
    head_check_runs = fetched_runs or []
    statuses = fetched_statuses or []
    from pr_resolver_core.github_checks import (
        head_ci_reported,
        partition_commit_statuses,
    )

    gating_statuses, advisory_statuses = partition_commit_statuses(
        statuses, required_checks
    )
    ci_summary = summarize_ci_checks([*head_check_runs, *gating_statuses])
    ci_summary["advisoryStatuses"] = advisory_statuses
    ci_summary["headShaVerified"] = head_sha
    head_non_sec = summarize_ci_checks(head_check_runs)["nonSecurityCheckCount"]
    ci_summary["headShaNonSecurityCheckCount"] = head_non_sec
    degraded = list(ci_summary["degradedReasons"])
    if (
        fetched_runs is not None
        and fetched_statuses is not None
        and head_ci_reported(
            head_check_runs, gating_statuses, advisory_statuses, required_checks
        )
    ):
        # HEAD reported a signal the merge gate also accepts (for example only
        # advisory status on an unprotected base): an empty gating set is a
        # clean signal, not a missing one.
        degraded = [r for r in degraded if r != "no_status_checks_reported"]
        if not degraded:
            ci_summary["signalQuality"] = "ok"
            ci_summary["hasFailures"] = bool(ci_summary["hasAuthoritativeFailures"])
    if fetched_runs is None or fetched_statuses is None:
        degraded.append(
            "head_checks_unavailable"
            if fetched_runs is None
            else "head_statuses_unavailable"
        )
        ci_summary["isRunning"] = True
    rollup_runs = [check for check in rollup_checks if not check.get("context")]
    if (
        summarize_ci_checks(rollup_runs)["nonSecurityCheckCount"] > 0
        and head_non_sec == 0
    ):
        degraded.append("rollup_stale_head_sha_has_no_non_security_checks")
        ci_summary["isRunning"] = True

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
            degraded.append("missing_required_checks")
            ci_summary["failedChecks"].append(
                {
                    "name": "Missing required checks",
                    "state": "MISSING_REQUIRED_CHECKS",
                    "url": "",
                }
            )

    ci_summary["degradedReasons"] = sorted(set(degraded))
    if degraded:
        ci_summary["signalQuality"] = "degraded"
        ci_summary["hasFailures"] = True

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

    # Distinguish a GitHub Actions platform outage from a failure the PR can
    # fix. Only authoritative failures on the exact head with a clean signal
    # qualify; everything else stays on the ``ci_failures`` path.
    ci_summary["infrastructureOnly"] = False
    if (
        pr_repo
        and ci_summary.get("hasAuthoritativeFailures")
        and ci_summary.get("signalQuality") == "ok"
        and int(ci_summary.get("headShaNonSecurityCheckCount") or 0) > 0
        # Infrastructure classification covers Actions check-runs only; any
        # failing gating legacy status is an independent, PR-owned failure.
        and not any(
            _check_state(status) in _FAILURE_CHECK_STATES
            for status in gating_statuses
        )
    ):
        ci_summary.update(
            summarize_ci_infrastructure(
                [
                    run
                    for run in head_check_runs
                    if _check_state(run) in _FAILURE_CHECK_STATES
                ],
                fetch_annotations=lambda check_id: _fetch_check_run_annotations(
                    pr_repo=pr_repo, check_id=check_id
                ),
                fetch_run=lambda run_id: _fetch_actions_run(
                    pr_repo=pr_repo, run_id=run_id
                ),
            )
        )

    # 3. Fetch Comments from the required sibling skill in this active snapshot.
    comments_script = _sibling_skill_file(
        SCRIPT_DIR,
        "fix-comments",
        "tools",
        "get_branch_pr_comments.py",
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
    automated_review = build_automated_review_evidence(
        provider=args.review_provider,
        require_fresh_review=bool(args.require_fresh_review),
        pr_repo=pr_repo,
        pr_number=pr_data.get("number"),
        head_sha=head_sha,
        comments=comments,
    )
    if automated_review.get("freshReviewForHead") is True:
        # GitHub publishes the review summary and inline findings separately.
        # Fetch the full inventory after observing completion, so a snapshot
        # collected while the review was running cannot authorize a clean exit.
        comments_data = run_command(
            comments_cmd, "Failed to retrieve completed review comments."
        )
        comments = (
            comments_data.get("comments", []) if isinstance(comments_data, dict) else []
        )
        if not isinstance(comments, list):
            comments = []
        # Comments/reactions have no reviewed commit. Revalidate the remote
        # head after completion and inventory collection before publishing them.
        completed_pr, _, _ = fetch_pr_data(args.pr)
        if (
            not head_sha
            or str(completed_pr.get("headRefOid") or "").strip() != head_sha
        ):
            print(
                "PR head changed during review collection; refresh the snapshot.",
                file=sys.stderr,
            )
            sys.exit(1)
    addressed_ids = _load_addressed_comment_ids()
    deferred_ids = _load_deferred_comment_ids()
    comments_summary = summarize_comments(
        comments,
        include_bot_review_comments=True,
        addressed_comment_ids=addressed_ids,
        deferred_comment_ids=deferred_ids,
        head_commit_sha=head_sha,
    )
    comments_summary = apply_codex_review_grace(
        comments_summary,
        comments,
        head_commit_sha=head_sha,
        previous_grace=_load_previous_codex_review_grace(snapshot_path),
    )

    # 4. Construct Snapshot
    snapshot = {
        "repository": pr_repo or "",
        "pr": pr_data,
        "ci": ci_summary,
        "commentsFetch": {
            "succeeded": isinstance(comments_data, dict)
            and isinstance(comments_data.get("comments"), list),
            "source": str(comments_script),
        },
        "comments": comments,
        "commentsSummary": comments_summary,
        "automatedReview": automated_review,
        "progressSignature": build_progress_signature(
            head_sha=head_sha,
            comments_summary=comments_summary,
        ),
    }

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
        "automated_review": {
            "enabled": automated_review.get("enabled"),
            "provider": automated_review.get("provider"),
            "freshReviewForHead": automated_review.get("freshReviewForHead"),
            "requestPending": automated_review.get("requestPending"),
            "requestFailed": automated_review.get("requestFailed"),
        },
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
