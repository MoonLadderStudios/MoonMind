"""Shared Jules status normalization helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

JulesNormalizedStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "canceled",
    "unknown",
    "awaiting_feedback",
]

JULES_DEFAULT_PROVIDER_STATUS = "pending"
JULES_SUCCESS_PROVIDER_STATUSES = frozenset(
    {"completed", "succeeded", "success", "done", "resolved", "finished"}
)
JULES_CANCELED_PROVIDER_STATUSES = frozenset({"cancelled", "canceled"})
JULES_FAILED_PROVIDER_STATUSES = frozenset(
    {"error", "failed", "rejected", "timed_out", "timeout"}
)
JULES_TERMINAL_SUCCESS_PROVIDER_STATUSES = JULES_SUCCESS_PROVIDER_STATUSES
JULES_TERMINAL_FAILURE_PROVIDER_STATUSES = frozenset(
    {
        *JULES_CANCELED_PROVIDER_STATUSES,
        *JULES_FAILED_PROVIDER_STATUSES,
    }
)
_JULES_QUEUED_PROVIDER_STATUSES = frozenset({"pending", "queued"})
_JULES_RUNNING_PROVIDER_STATUSES = frozenset(
    {"running", "in_progress", "in-progress", "processing"}
)
_JULES_AWAITING_FEEDBACK_PROVIDER_STATUSES = frozenset(
    {"awaiting_user_feedback"}
)


@dataclass(frozen=True, slots=True)
class JulesStatusSnapshot:
    """Normalized Jules provider status plus portable MoonMind mapping."""

    provider_status: str
    provider_status_token: str
    normalized_status: JulesNormalizedStatus
    terminal: bool
    succeeded: bool
    failed: bool
    canceled: bool


def normalize_jules_status(raw_status: str | None) -> JulesStatusSnapshot:
    """Normalize one Jules status into provider and MoonMind status fields."""

    provider_status = str(raw_status or "").strip() or JULES_DEFAULT_PROVIDER_STATUS
    provider_status_token = provider_status.lower()

    if provider_status_token in JULES_SUCCESS_PROVIDER_STATUSES:
        normalized_status: JulesNormalizedStatus = "succeeded"
    elif provider_status_token in JULES_CANCELED_PROVIDER_STATUSES:
        normalized_status = "canceled"
    elif provider_status_token in JULES_FAILED_PROVIDER_STATUSES:
        normalized_status = "failed"
    elif provider_status_token in _JULES_QUEUED_PROVIDER_STATUSES:
        normalized_status = "queued"
    elif provider_status_token in _JULES_RUNNING_PROVIDER_STATUSES:
        normalized_status = "running"
    elif provider_status_token in _JULES_AWAITING_FEEDBACK_PROVIDER_STATUSES:
        normalized_status = "awaiting_feedback"
    else:
        normalized_status = "unknown"

    return JulesStatusSnapshot(
        provider_status=provider_status,
        provider_status_token=provider_status_token,
        normalized_status=normalized_status,
        terminal=normalized_status in {"succeeded", "failed", "canceled"},
        succeeded=normalized_status == "succeeded",
        failed=normalized_status == "failed",
        canceled=normalized_status == "canceled",
    )


__all__ = [
    "JULES_CANCELED_PROVIDER_STATUSES",
    "JULES_DEFAULT_PROVIDER_STATUS",
    "JULES_FAILED_PROVIDER_STATUSES",
    "JULES_SUCCESS_PROVIDER_STATUSES",
    "JULES_TERMINAL_FAILURE_PROVIDER_STATUSES",
    "JULES_TERMINAL_SUCCESS_PROVIDER_STATUSES",
    "JulesNormalizedStatus",
    "JulesStatusSnapshot",
    "normalize_jules_status",
]
