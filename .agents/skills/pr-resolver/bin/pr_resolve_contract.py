#!/usr/bin/env python3
"""Shared contracts and retry policy helpers for pr-resolver scripts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys
from typing import Any

for _parent in Path(__file__).resolve().parents:
    if (_parent / "moonmind" / "pr_resolver_core").is_dir():
        sys.path.insert(0, str(_parent))
        break

from moonmind.pr_resolver_core import (  # noqa: E402
    FINALIZE_ONLY_RETRY_REASONS,
    FULL_REMEDIATION_REASONS,
    classify_retry_action,
    compute_backoff_seconds,
    normalize_terminal_status,
    normalize_text,
)

RESULT_SCHEMA_VERSION = 2

NON_RETRYABLE_REASONS = {
    "comment_policy_not_enforced",
    "merge_not_ready",
    "pr_not_found",
    "publish_unavailable",
    "already_merged",
}

EXIT_CODE_MERGED = 0
EXIT_CODE_BLOCKED = 2
EXIT_CODE_ATTEMPTS_EXHAUSTED = 3
EXIT_CODE_FAILED = 4

MERGE_AUTOMATION_DISPOSITION_MERGED = "merged"
MERGE_AUTOMATION_DISPOSITION_ALREADY_MERGED = "already_merged"
MERGE_AUTOMATION_DISPOSITION_REENTER_GATE = "reenter_gate"
MERGE_AUTOMATION_DISPOSITION_MANUAL_REVIEW = "manual_review"
MERGE_AUTOMATION_DISPOSITION_FAILED = "failed"

def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()

def parse_reason(result_payload: dict[str, Any]) -> str:
    return normalize_text(
        result_payload.get("final_reason") or result_payload.get("reason")
    )

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
    if normalized == "ci_running":
        return "wait_for_ci_and_retry_finalize"
    if normalized == "codex_review_grace_wait":
        return "wait_for_codex_review_and_retry_finalize"
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
    if normalized_next_step.startswith("run_fix_"):
        return MERGE_AUTOMATION_DISPOSITION_REENTER_GATE
    if normalized_next_step in {
        "retry_finalize_after_backoff",
        "wait_for_ci_and_retry_finalize",
        "wait_for_codex_review_and_retry_finalize",
    }:
        return MERGE_AUTOMATION_DISPOSITION_REENTER_GATE
    if normalized_status == "merged" and normalized_outcome == "merged":
        if normalized_reason == "already_merged":
            return MERGE_AUTOMATION_DISPOSITION_ALREADY_MERGED
        return MERGE_AUTOMATION_DISPOSITION_MERGED
    if normalized_status == "failed" or normalized_outcome == "failed":
        return MERGE_AUTOMATION_DISPOSITION_FAILED
    return MERGE_AUTOMATION_DISPOSITION_MANUAL_REVIEW
