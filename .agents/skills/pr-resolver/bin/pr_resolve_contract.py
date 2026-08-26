#!/usr/bin/env python3
"""Shared contracts and retry policy helpers for pr-resolver scripts."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

RESULT_SCHEMA_VERSION = 2

FULL_REMEDIATION_REASONS = {
    "actionable_comments",
    "ci_failures",
    "merge_conflicts",
}

FINALIZE_ONLY_RETRY_REASONS = {
    "automated_review_wait",
    "ci_running",
    "codex_review_grace_wait",
    "comments_unavailable",
    "ci_signal_degraded",
    "external_state_transient",
    "merge_pending",
    "snapshot_refresh_failed",
}

REVIEW_REQUEST_REASONS = {
    "fresh_review_required_after_remediation",
}

NON_RETRYABLE_REASONS = {
    "comment_policy_not_enforced",
    "deferred_comments",
    "merge_not_ready",
    "pr_not_found",
    "publish_unavailable",
    "already_merged",
}

EXIT_CODE_MERGED = 0
EXIT_CODE_BLOCKED = 2
EXIT_CODE_ATTEMPTS_EXHAUSTED = 3
EXIT_CODE_FAILED = 4
EXIT_CODE_REVIEW_CLEAN = 5

# Finish modes. "merge" is the full resolver contract: the run is not complete
# until the PR is merged. "fix_only" stops at the merge gate: the resolver keeps
# remediating comments, CI, and conflicts, but reports `review_clean` instead of
# merging once nothing is left to address.
FINISH_MODE_MERGE = "merge"
FINISH_MODE_FIX_ONLY = "fix_only"
FINISH_MODES = (FINISH_MODE_MERGE, FINISH_MODE_FIX_ONLY)

MERGE_AUTOMATION_DISPOSITION_MERGED = "merged"
MERGE_AUTOMATION_DISPOSITION_ALREADY_MERGED = "already_merged"
MERGE_AUTOMATION_DISPOSITION_REVIEW_CLEAN = "review_clean"
MERGE_AUTOMATION_DISPOSITION_REENTER_GATE = "reenter_gate"
MERGE_AUTOMATION_DISPOSITION_REQUEST_REVIEW = "request_review"
MERGE_AUTOMATION_DISPOSITION_MANUAL_REVIEW = "manual_review"
MERGE_AUTOMATION_DISPOSITION_FAILED = "failed"

def normalize_finish_mode(value: Any) -> str:
    """Return a supported finish mode, defaulting to the full merge contract."""

    candidate = normalize_text(value).lower()
    if candidate == FINISH_MODE_FIX_ONLY:
        return FINISH_MODE_FIX_ONLY
    if candidate in {"", FINISH_MODE_MERGE}:
        return FINISH_MODE_MERGE
    raise ValueError(
        f"unsupported finish mode '{candidate}'; expected one of {list(FINISH_MODES)}"
    )

def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()

def normalize_text(value: Any) -> str:
    return str(value or "").strip()

def current_execution_ref() -> str | None:
    return normalize_text(os.getenv("MOONMIND_STEP_EXECUTION_ID")) or None


def _validated_head_sha(snapshot: dict[str, Any]) -> str:
    pr = snapshot.get("pr") if isinstance(snapshot.get("pr"), dict) else {}
    head_sha = normalize_text(pr.get("headRefOid"))
    if len(head_sha) < 7 or len(head_sha) > 64 or any(
        char not in "0123456789abcdefABCDEF" for char in head_sha
    ):
        raise ValueError("gated continuation requires a valid head SHA")
    return head_sha


def build_review_request_continuation(
    snapshot: dict[str, Any],
    *,
    reason: str,
    execution_ref: str,
) -> dict[str, Any]:
    """Build the typed request for one fresh automated review of this head.

    The Skill only names the *configured* provider; it never supplies comment
    text.  The owning workflow translates the provider into the exact request
    command, performs the side effect, and owns the durable wait.
    """
    if not execution_ref:
        raise ValueError("gated continuation requires an execution reference")
    head_sha = _validated_head_sha(snapshot)
    automated_review = (
        snapshot.get("automatedReview")
        if isinstance(snapshot.get("automatedReview"), dict)
        else {}
    )
    provider = normalize_text(automated_review.get("provider")).lower()
    if automated_review.get("enabled") is not True or not provider:
        raise ValueError("review request requires an enabled review provider")
    payload: dict[str, Any] = {
        "schemaVersion": "gated-continuation/v2",
        "gateType": "merge_automation",
        "action": "request_review",
        "provider": provider,
        "reason": normalize_text(reason) or "fresh_review_required_after_remediation",
        "executionRef": execution_ref,
        "headSha": head_sha,
    }
    signature = normalize_text(snapshot.get("progressSignature"))
    if signature:
        payload["progressSignature"] = signature
    return payload


def build_gated_continuation(
    snapshot: dict[str, Any],
    *,
    reason: str,
    execution_ref: str,
) -> dict[str, Any]:
    """Build the typed handoff from the Skill's already-recorded gate state."""
    if normalize_text(reason) in REVIEW_REQUEST_REASONS:
        return build_review_request_continuation(
            snapshot,
            reason=reason,
            execution_ref=execution_ref,
        )
    comments = (
        snapshot.get("commentsSummary")
        if isinstance(snapshot.get("commentsSummary"), dict)
        else {}
    )
    grace = (
        comments.get("codexReviewGrace")
        if isinstance(comments.get("codexReviewGrace"), dict)
        else {}
    )
    if not execution_ref:
        raise ValueError("gated continuation requires an execution reference")
    head_sha = _validated_head_sha(snapshot)
    payload: dict[str, Any] = {
        "schemaVersion": "gated-continuation/v1",
        "gateType": "merge_automation",
        "action": "reenter_gate",
        "reason": normalize_text(reason) or "resolver_wait",
        "executionRef": execution_ref,
        "headSha": head_sha,
    }
    signature = normalize_text(snapshot.get("progressSignature"))
    if signature:
        payload["progressSignature"] = signature
    if reason == "codex_review_grace_wait":
        expires_at = normalize_text(grace.get("expiresAt"))
        try:
            deadline = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("codex review grace requires a valid expiresAt") from exc
        if deadline.tzinfo is None:
            raise ValueError("codex review grace expiresAt must be timezone-aware")
        payload["notBefore"] = deadline.astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        )
    else:
        poll_seconds = grace.get("pollSeconds", 60)
        try:
            if isinstance(poll_seconds, bool):
                raise ValueError
            poll_value = int(poll_seconds)
            if poll_value < 1:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "gated continuation retry delay must be positive"
            ) from exc
        payload["retryAfterSeconds"] = poll_value
    return payload

def parse_reason(result_payload: dict[str, Any]) -> str:
    return normalize_text(
        result_payload.get("final_reason") or result_payload.get("reason")
    )

def classify_retry_action(
    reason: str,
    *,
    merge_not_ready_grace_remaining: int,
) -> str:
    normalized = normalize_text(reason)
    if not normalized:
        return "stop"
    if normalized in FULL_REMEDIATION_REASONS:
        return "full_remediation"
    if normalized in FINALIZE_ONLY_RETRY_REASONS:
        return "finalize_only_retry"
    if normalized == "merge_not_ready" and merge_not_ready_grace_remaining > 0:
        return "finalize_only_retry"
    return "stop"

def compute_backoff_seconds(
    retry_index: int,
    *,
    base_sleep_seconds: int,
    max_sleep_seconds: int,
) -> int:
    base = max(0, int(base_sleep_seconds))
    max_sleep = max(0, int(max_sleep_seconds))
    if base == 0 or max_sleep == 0:
        return 0
    value = base * (2 ** max(0, int(retry_index)))
    return min(max_sleep, value)

def remediation_next_step(reason: str) -> str:
    normalized = normalize_text(reason)
    if normalized == "merge_conflicts":
        return "run_fix_merge_conflicts_skill"
    if normalized == "actionable_comments":
        return "run_fix_comments_skill"
    if normalized == "ci_failures":
        return "run_fix_ci_skill"
    if normalized in {"ci_signal_degraded", "comments_unavailable"}:
        return "inspect_ci_and_comment_signal"
    if normalized == "snapshot_refresh_failed":
        return "retry_finalize_after_backoff"
    if normalized == "external_state_transient":
        return "retry_finalize_after_backoff"
    if normalized == "ci_running":
        return "wait_for_ci_and_retry_finalize"
    if normalized == "codex_review_grace_wait":
        return "wait_for_codex_review_and_retry_finalize"
    if normalized == "fresh_review_required_after_remediation":
        return "request_automated_review"
    if normalized == "automated_review_wait":
        return "wait_for_automated_review_and_retry_finalize"
    if normalized == "deferred_comments":
        return "manual_review"
    if normalized == "merge_pending":
        return "retry_finalize_after_backoff"
    if normalized == "comment_policy_not_enforced":
        return "inspect_comment_policy"
    if normalized == "merge_not_ready":
        return "inspect_mergeability_state"
    if normalized == "publish_unavailable":
        return "manual_review"
    return "manual_review"

def merge_automation_disposition_for_result(
    *,
    status: str,
    merge_outcome: str,
    final_reason: str | None,
    next_step: str | None = None,
) -> str:
    normalized_status = normalize_text(status).lower()
    normalized_outcome = normalize_text(merge_outcome).lower()
    normalized_reason = normalize_text(final_reason).lower()
    normalized_next_step = normalize_text(next_step).lower()
    if normalized_next_step == "request_automated_review":
        return MERGE_AUTOMATION_DISPOSITION_REQUEST_REVIEW
    if normalized_next_step.startswith("run_fix_"):
        return MERGE_AUTOMATION_DISPOSITION_REENTER_GATE
    if normalized_next_step in {
        "retry_finalize_after_backoff",
        "wait_for_ci_and_retry_finalize",
        "wait_for_codex_review_and_retry_finalize",
        "wait_for_automated_review_and_retry_finalize",
    }:
        return MERGE_AUTOMATION_DISPOSITION_REENTER_GATE
    if normalized_status == MERGE_AUTOMATION_DISPOSITION_REVIEW_CLEAN:
        return MERGE_AUTOMATION_DISPOSITION_REVIEW_CLEAN
    if normalized_status == "merged" and normalized_outcome == "merged":
        if normalized_reason == "already_merged":
            return MERGE_AUTOMATION_DISPOSITION_ALREADY_MERGED
        return MERGE_AUTOMATION_DISPOSITION_MERGED
    if normalized_status == "failed" or normalized_outcome == "failed":
        return MERGE_AUTOMATION_DISPOSITION_FAILED
    return MERGE_AUTOMATION_DISPOSITION_MANUAL_REVIEW
