"""Portable retry and terminal normalization policy shared by resolver hosts."""

from typing import Any

FULL_REMEDIATION_REASONS = frozenset(
    {"actionable_comments", "ci_failures", "merge_conflicts"}
)
FINALIZE_ONLY_RETRY_REASONS = frozenset(
    {
        "ci_running",
        "codex_review_grace_wait",
        "comments_unavailable",
        "ci_signal_degraded",
        "snapshot_refresh_failed",
    }
)


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def classify_retry_action(
    reason: str, *, merge_not_ready_grace_remaining: int
) -> str:
    normalized = normalize_text(reason)
    if normalized in FULL_REMEDIATION_REASONS:
        return "full_remediation"
    if normalized in FINALIZE_ONLY_RETRY_REASONS or (
        normalized == "merge_not_ready" and merge_not_ready_grace_remaining > 0
    ):
        return "finalize_only_retry"
    return "stop"


def compute_backoff_seconds(
    retry_index: int, *, base_sleep_seconds: int, max_sleep_seconds: int
) -> int:
    base = max(0, int(base_sleep_seconds))
    maximum = max(0, int(max_sleep_seconds))
    if not base or not maximum:
        return 0
    return min(maximum, base * (2 ** max(0, int(retry_index))))


def normalize_terminal_status(payload: dict[str, Any]) -> str:
    """Normalize both hosts to the canonical bounded terminal status set."""

    status = normalize_text(payload.get("status")).lower()
    if status in {"merged", "blocked", "failed", "attempts_exhausted"}:
        return status
    outcome = normalize_text(payload.get("merge_outcome")).lower()
    if outcome in {"merged", "failed", "attempts_exhausted"}:
        return outcome
    return "blocked"
