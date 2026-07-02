"""Compatibility helpers for historical status aliases."""

from __future__ import annotations

import logging
from typing import Any, Mapping


WORKFLOW_STATE_COMPATIBILITY_ALIASES: dict[str, str] = {
    "no_changes": "no_commit",
}
LEGACY_WORKFLOW_STATE_ALIASES = WORKFLOW_STATE_COMPATIBILITY_ALIASES

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


def _observe_legacy_alias(
    *,
    logger: logging.Logger | None,
    domain: str,
    alias: str,
    canonical: str,
) -> None:
    if logger is None:
        return
    logger.warning(
        "Observed legacy status alias",
        extra={"domain": domain, "alias": alias, "canonical": canonical},
    )


def normalize_workflow_state_alias(raw: str) -> str:
    """Normalize legacy workflow state aliases before canonical parsing."""

    candidate = str(raw).strip().lower()
    return WORKFLOW_STATE_COMPATIBILITY_ALIASES.get(candidate, candidate)


def canonicalize_workflow_state_alias(
    value: Any,
    *,
    logger: logging.Logger | None = None,
) -> str | None:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return None
    canonical = WORKFLOW_STATE_COMPATIBILITY_ALIASES.get(candidate, candidate)
    if canonical != candidate:
        _observe_legacy_alias(
            logger=logger,
            domain="workflow_state",
            alias=candidate,
            canonical=canonical,
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
    alias_key = candidate.upper()
    canonical = LEGACY_FINISH_OUTCOME_ALIASES.get(alias_key, alias_key)
    if alias_key in LEGACY_FINISH_OUTCOME_ALIASES:
        _observe_legacy_alias(
            logger=logger,
            domain="finish_outcome",
            alias=candidate,
            canonical=canonical,
        )
    return canonical


def canonicalize_publish_reason_alias(
    value: Any,
    *,
    logger: logging.Logger | None = None,
) -> str | None:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return None
    canonical = LEGACY_PUBLISH_REASON_ALIASES.get(candidate, candidate)
    if canonical != candidate:
        _observe_legacy_alias(
            logger=logger,
            domain="publish_reason",
            alias=candidate,
            canonical=canonical,
        )
    return canonical


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
        reason_code_key = (
            "reasonCode"
            if "reasonCode" in publish_payload
            else "reason_code"
            if "reason_code" in publish_payload
            else "reasonCode"
        )
        raw_reason_code = publish_payload.get(reason_code_key)
        reason_code = canonicalize_publish_reason_alias(
            raw_reason_code,
            logger=logger,
        )
        reason = str(publish_payload.get("reason") or "").strip().lower()
        if reason_code is not None:
            publish_payload["reasonCode"] = reason_code
        if reason_code == "no_commit" or reason in _NO_COMMIT_LEGACY_REASONS:
            publish_payload["reasonCode"] = "no_commit"
            if "reason_code" in publish_payload:
                publish_payload["reason_code"] = "no_commit"
            publish_payload["reason"] = (
                "No repository changes were available to commit or publish."
            )
        normalized["publish"] = publish_payload
    return normalized


__all__ = [
    "LEGACY_FINISH_OUTCOME_ALIASES",
    "LEGACY_PUBLISH_REASON_ALIASES",
    "LEGACY_WORKFLOW_STATE_ALIASES",
    "WORKFLOW_STATE_COMPATIBILITY_ALIASES",
    "canonicalize_finish_outcome_code_alias",
    "canonicalize_publish_reason_alias",
    "canonicalize_workflow_state_alias",
    "normalize_no_commit_finish_summary",
    "normalize_workflow_state_alias",
]
