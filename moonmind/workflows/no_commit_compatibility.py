"""Bounded compatibility helpers for legacy no-changes status values."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

LEGACY_WORKFLOW_STATE_ALIASES: Mapping[str, str] = {
    "no_changes": "no_commit",
}

LEGACY_FINISH_OUTCOME_ALIASES: Mapping[str, str] = {
    "NO_CHANGES": "NO_COMMIT",
}

LEGACY_PUBLISH_REASON_ALIASES: Mapping[str, str] = {
    "no_changes": "no_commit",
}

_NO_COMMIT_LEGACY_REASONS = frozenset(
    {
        "publish skipped: no local changes",
        "no local changes",
        "workflow completed with no changes.",
    }
)


def observe_legacy_alias(
    *,
    logger: logging.Logger,
    domain: str,
    alias: str,
    canonical: str,
) -> None:
    """Log a bounded legacy-alias observation without payload content."""

    logger.info(
        "legacy_alias_observed domain=%s alias=%s canonical=%s",
        domain,
        alias,
        canonical,
    )


def canonicalize_legacy_workflow_state(
    value: str,
    *,
    domain: str,
    logger: logging.Logger,
) -> str:
    """Translate legacy workflow state aliases at inbound/durable boundaries."""

    candidate = str(value).strip().lower()
    canonical = LEGACY_WORKFLOW_STATE_ALIASES.get(candidate)
    if canonical is None:
        return candidate
    observe_legacy_alias(
        logger=logger,
        domain=domain,
        alias=candidate,
        canonical=canonical,
    )
    return canonical


def parse_canonical_workflow_state(value: str) -> str:
    """Normalize a canonical workflow state value and reject legacy aliases."""

    candidate = str(value).strip().lower()
    if candidate in LEGACY_WORKFLOW_STATE_ALIASES:
        raise ValueError(f"Legacy workflow state alias is not canonical: {candidate}")
    return candidate


def canonicalize_legacy_finish_outcome_code(
    value: str | None,
    *,
    domain: str,
    logger: logging.Logger,
) -> str | None:
    """Translate legacy finish outcome aliases at artifact/history boundaries."""

    if value is None:
        return None
    candidate = str(value).strip().upper()
    canonical = LEGACY_FINISH_OUTCOME_ALIASES.get(candidate)
    if canonical is None:
        return candidate
    observe_legacy_alias(
        logger=logger,
        domain=domain,
        alias=candidate,
        canonical=canonical,
    )
    return canonical


def canonicalize_legacy_publish_reason_code(
    value: str,
    *,
    domain: str,
    logger: logging.Logger,
) -> str:
    """Translate legacy publish reason aliases at artifact/history boundaries."""

    candidate = str(value).strip()
    canonical = LEGACY_PUBLISH_REASON_ALIASES.get(candidate)
    if canonical is None:
        return candidate
    observe_legacy_alias(
        logger=logger,
        domain=domain,
        alias=candidate,
        canonical=canonical,
    )
    return canonical


def normalize_no_commit_finish_summary_aliases(
    finish_summary: Mapping[str, Any] | None,
    *,
    domain: str,
    logger: logging.Logger,
) -> dict[str, Any] | None:
    """Return a copy of a finish summary with legacy no-changes aliases repaired."""

    if not isinstance(finish_summary, Mapping):
        return None
    normalized = dict(finish_summary)
    finish_outcome_key = (
        "finishOutcome"
        if "finishOutcome" in normalized
        else "finish_outcome"
        if "finish_outcome" in normalized
        else None
    )
    finish_outcome = normalized.get(finish_outcome_key) if finish_outcome_key else None
    if isinstance(finish_outcome, Mapping):
        outcome = dict(finish_outcome)
        original_outcome = dict(outcome)
        outcome_code = canonicalize_legacy_finish_outcome_code(
            outcome.get("code"),
            domain=f"{domain}.finishOutcome.code",
            logger=logger,
        )
        if outcome_code:
            outcome["code"] = outcome_code
        reason = str(outcome.get("reason") or "").strip().lower()
        if outcome_code == "NO_COMMIT" and reason in _NO_COMMIT_LEGACY_REASONS:
            outcome["reason"] = "No repository commit was needed."
        if outcome != original_outcome:
            normalized[finish_outcome_key] = outcome

    publish = normalized.get("publish")
    if isinstance(publish, Mapping):
        publish_payload = dict(publish)
        original_publish_payload = dict(publish_payload)
        reason_code_key = (
            "reasonCode"
            if "reasonCode" in publish_payload
            else "reason_code"
            if "reason_code" in publish_payload
            else "reasonCode"
        )
        reason_code = str(publish_payload.get(reason_code_key) or "").strip()
        canonical_reason_code = canonicalize_legacy_publish_reason_code(
            reason_code,
            domain=f"{domain}.publish.reasonCode",
            logger=logger,
        )
        reason = str(publish_payload.get("reason") or "").strip().lower()
        if canonical_reason_code == "no_commit" or reason in _NO_COMMIT_LEGACY_REASONS:
            publish_payload["reasonCode"] = "no_commit"
            if "reason_code" in publish_payload:
                publish_payload["reason_code"] = "no_commit"
            publish_payload["reason"] = (
                "No repository changes were available to commit or publish."
            )
        if publish_payload != original_publish_payload:
            normalized["publish"] = publish_payload
    return normalized
