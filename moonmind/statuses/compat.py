"""Compatibility helpers for historical status aliases."""

from __future__ import annotations

import logging
from typing import Any, Mapping

LEGACY_WORKFLOW_STATE_ALIASES = {
    "no_changes": "no_commit",
}

LEGACY_FINISH_OUTCOME_ALIASES = {
    "NO_CHANGES": "NO_COMMIT",
}

LEGACY_PUBLISH_REASON_ALIASES = {
    "no_changes": "no_commit",
}

_NO_COMMIT_LEGACY_REASONS = {
    "publish skipped: no local changes",
    "no local changes",
    "workflow completed with no changes.",
}


def canonicalize_workflow_state_alias(
    value: Any,
    *,
    logger: logging.Logger | None = None,
) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    canonical = LEGACY_WORKFLOW_STATE_ALIASES.get(candidate, candidate)
    if canonical != candidate and logger is not None:
        logger.warning(
            "Observed legacy workflow state alias '%s'; canonicalized to '%s'",
            candidate,
            canonical,
        )
    return canonical


def canonicalize_finish_outcome_code_alias(
    value: Any,
    *,
    logger: logging.Logger | None = None,
) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    canonical = LEGACY_FINISH_OUTCOME_ALIASES.get(candidate.upper(), candidate)
    if canonical != candidate and logger is not None:
        logger.warning(
            "Observed legacy finish outcome alias '%s'; canonicalized to '%s'",
            candidate,
            canonical,
        )
    return canonical


def canonicalize_publish_reason_alias(value: Any) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    return LEGACY_PUBLISH_REASON_ALIASES.get(candidate, candidate)


def normalize_no_commit_finish_summary(
    finish_summary: Mapping[str, Any] | None,
    *,
    logger: logging.Logger | None = None,
) -> dict[str, Any] | None:
    if not isinstance(finish_summary, Mapping):
        return None
    normalized = dict(finish_summary)
    finish_outcome_key = (
        "finishOutcome" if "finishOutcome" in normalized else "finish_outcome"
    )
    finish_outcome = normalized.get(finish_outcome_key)
    if isinstance(finish_outcome, Mapping):
        outcome = dict(finish_outcome)
        code = canonicalize_finish_outcome_code_alias(
            outcome.get("code"),
            logger=logger,
        )
        if code is not None:
            outcome["code"] = code
        reason = str(outcome.get("reason") or "").strip().lower()
        if code == "NO_COMMIT" and reason in _NO_COMMIT_LEGACY_REASONS:
            outcome["reason"] = "No repository commit was needed."
        normalized[finish_outcome_key] = outcome
    publish = normalized.get("publish")
    if isinstance(publish, Mapping):
        publish_payload = dict(publish)
        reason_code = canonicalize_publish_reason_alias(publish_payload.get("reasonCode"))
        if reason_code is not None:
            publish_payload["reasonCode"] = reason_code
        normalized["publish"] = publish_payload
    return normalized
