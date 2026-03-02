#!/usr/bin/env python3
"""Shared contracts and retry policy helpers for pr-resolver scripts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

RESULT_SCHEMA_VERSION = 2

FULL_REMEDIATION_REASONS = {
    "actionable_comments",
    "ci_failures",
    "merge_conflicts",
}

FINALIZE_ONLY_RETRY_REASONS = {
    "ci_running",
    "comments_unavailable",
    "ci_signal_degraded",
}

NON_RETRYABLE_REASONS = {
    "comment_policy_not_enforced",
    "merge_not_ready",
}

EXIT_CODE_MERGED = 0
EXIT_CODE_BLOCKED = 2
EXIT_CODE_ATTEMPTS_EXHAUSTED = 3
EXIT_CODE_FAILED = 4


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


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
    value = base * (2**max(0, int(retry_index)))
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
    if normalized == "ci_running":
        return "wait_for_ci_and_retry_finalize"
    if normalized == "comment_policy_not_enforced":
        return "inspect_comment_policy"
    if normalized == "merge_not_ready":
        return "inspect_mergeability_state"
    return "manual_review"
