"""Step review Temporal Activity.

Reports whether review evidence is available for a workflow step.
Registered as ``step.review`` in the activity catalog.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from typing import Any, Mapping

from moonmind.workflows.skills.approval_policy import (
    ReviewRequest,
    ReviewVerdict,
    build_review_prompt,
    parse_step_gate_result,
)
from moonmind.workflows.temporal.activities.reviewer import ReviewerUnavailable

logger = logging.getLogger(__name__)

# Deployment-owned bounds for MoonLadderStudios/MoonMind#3945. The final
# 256 KB prompt check is retained, but upstream sections are bounded before
# expansion so a fitting final prompt cannot smuggle an unbounded evidence
# copy, and provider responses are validated before they can authorize work.
PROMPT_BUDGET_BYTES = 256_000
EVIDENCE_SECTION_MAX_BYTES = 64_000
FEEDBACK_MAX_CHARS = 4_000
ISSUES_MAX_COUNT = 20
ISSUE_DESCRIPTION_MAX_CHARS = 2_000
REVIEW_TIMEOUT_MIN_SECONDS = 1
# Aligned with the production `step.review` Temporal route
# (activity_catalog.py: `TemporalActivityTimeouts(120, 300)`): the activity
# execution must finish within the 120s start-to-close timeout, so a longer
# review timeout could never complete and would risk duplicate provider
# requests. Keep this ceiling at or below the route's start-to-close budget.
REVIEW_TIMEOUT_MAX_SECONDS = 120

_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|apikey|token|secret|password|passwd|authorization|cookie|session)",
    re.IGNORECASE,
)


def _redact_secrets(value: Any) -> Any:
    """Recursively redact secret-shaped values before provider send."""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY_PATTERN.search(str(key)):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def review_evidence_digest(
    *,
    node_id: str,
    step_index: int,
    review_attempt: int,
    tool_name: str,
    tool_type: str,
    inputs: Mapping[str, Any],
    execution_result: Mapping[str, Any],
    workflow_context: Mapping[str, Any],
    previous_feedback: str | None,
) -> str:
    """Digest the exact evidence identity a review verdict binds to."""
    canonical = {
        "node_id": node_id,
        "step_index": step_index,
        "review_attempt": review_attempt,
        "tool_name": tool_name,
        "tool_type": tool_type,
        "inputs": dict(inputs),
        "execution_result": dict(execution_result),
        "workflow_context": dict(workflow_context),
        "previous_feedback": previous_feedback,
    }
    return "sha256:" + hashlib.sha256(_canonical_bytes(canonical)).hexdigest()


def review_attempt_identity(
    *, provider: str, model: str, evidence_digest: str, review_attempt: int
) -> str:
    """Immutable review-attempt identity: route/policy/evidence binding.

    An explicitly different route or evidence set is a new attempt; retries
    of the same admitted review reuse this identity and may reuse only the
    same committed decision.
    """
    canonical = {
        "provider": str(provider or "unknown"),
        "model": str(model or "default"),
        "evidence_digest": evidence_digest,
        "review_attempt": int(review_attempt),
    }
    return "review:" + hashlib.sha256(_canonical_bytes(canonical)).hexdigest()[:32]


def _resolve_route(reviewer: Any, model: str) -> dict[str, str]:
    """Resolve the admitted reviewer route without touching credentials."""
    describe = getattr(reviewer, "describe_route", None)
    if callable(describe):
        try:
            route = describe(model)
            if isinstance(route, Mapping):
                return {
                    "provider": str(route.get("provider") or "unknown"),
                    "model": str(route.get("model") or model or "default"),
                }
        except Exception:
            # Best-effort route introspection only: fall through to the
            # config-based resolution below when the reviewer probe fails.
            pass
    config = getattr(reviewer, "_config", None)
    if config is not None:
        try:
            provider = str(
                getattr(config, "default_chat_provider", "unknown") or "unknown"
            ).lower()
            selected = str(model or "default")
            if selected == "default":
                provider_config = getattr(config, provider, None)
                if provider_config is not None:
                    selected = str(
                        getattr(provider_config, f"{provider}_chat_model", selected)
                        or selected
                    )
            return {"provider": provider, "model": selected or "default"}
        except Exception:
            pass
    return {"provider": "unknown", "model": str(model or "default")}


def _section_too_large(value: Mapping[str, Any]) -> bool:
    return len(_canonical_bytes(dict(value))) > EVIDENCE_SECTION_MAX_BYTES


def _coerce_section(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _early_provenance(
    payload: Mapping[str, Any],
    reviewer: Any,
    *,
    timeout_value: int | None = None,
) -> dict[str, Any] | None:
    """Best-effort provenance for paths that return before a ReviewRequest.

    Binds the admitted route/model, policy and reviewed evidence digest to an
    immutable review attempt even when the review cannot proceed. Never raises,
    never touches credentials, and never returns provider detail. Returns None
    only when the payload itself cannot supply an evidence identity.
    """
    try:
        raw_inputs = _coerce_section(payload, "inputs")
        raw_result = _coerce_section(payload, "execution_result")
        raw_context = _coerce_section(payload, "workflow_context")
        raw_feedback_value = payload.get("previous_feedback")
        raw_feedback = (
            str(raw_feedback_value) if raw_feedback_value is not None else None
        )
        try:
            review_attempt = int(payload.get("review_attempt") or 1)
        except (TypeError, ValueError):
            review_attempt = 1
        try:
            step_index = int(payload.get("step_index") or 1)
        except (TypeError, ValueError):
            step_index = 1
        model = str(payload.get("reviewer_model", "default"))
        route = _resolve_route(reviewer, model)
        evidence_digest = review_evidence_digest(
            node_id=str(payload.get("node_id") or ""),
            step_index=step_index,
            review_attempt=review_attempt,
            tool_name=str(payload.get("tool_name") or ""),
            tool_type=str(payload.get("tool_type") or "skill"),
            inputs=_redact_secrets(raw_inputs),
            execution_result=_redact_secrets(raw_result),
            workflow_context=_redact_secrets(raw_context),
            previous_feedback=raw_feedback,
        )
        attempt_identity = review_attempt_identity(
            provider=route["provider"],
            model=route["model"],
            evidence_digest=evidence_digest,
            review_attempt=review_attempt,
        )
        provenance: dict[str, Any] = {
            "provider": route["provider"],
            "model": route["model"],
            "evidenceDigest": evidence_digest,
            "reviewAttemptIdentity": attempt_identity,
            "reviewAttempt": review_attempt,
        }
        if timeout_value is not None:
            provenance["policy"] = {"timeoutSeconds": timeout_value}
        return provenance
    except Exception:
        return None


# Smallest committed-review record for MoonLadderStudios/MoonMind#3945 (R5).
#
# Durability model: the workflow's persisted gate-result artifact plus the
# step-ledger check (run.py `_committed_review_gates`) is the durable owner
# of a committed decision. This module's mapping is a per-worker
# duplicate-delivery hint only: `TemporalReviewActivities.step_review`
# (activity_runtime.py) invokes this activity without an injected
# `committed_store`, so commitments land in the in-process fallback and do
# not survive worker death or cross-process retries. A lost acknowledgement
# before the first result reaches the workflow therefore re-invokes the
# provider under the same immutable review-attempt identity; the workflow
# persists the first completed delivery and reuses it for later duplicate
# deliveries via `resolve_committed_gate_reuse`, discarding divergent or
# unavailable redeliveries. Deployments needing cross-worker reuse before
# first workflow receipt must inject a shared mapping via `committed_store`.
# Records carry the complete bounded canonical gate payload (verdict,
# confidence, feedback, issues, refs, routing, policy and digests only) so a
# reused record never changes the committed decision. No credentials stored.
_COMMITTED_REVIEW_VERDICTS = frozenset({"FULLY_IMPLEMENTED", "ADDITIONAL_WORK_NEEDED"})

_committed_reviews: dict[str, dict[str, Any]] = {}
# Bound the in-process fallback so a long-lived worker cannot grow it
# without limit. run.py scopes identities per run (workflow_context carries
# workflow_id/run_id), so steady-state occupancy is small; eviction only
# drops reuse hints, never correctness: a miss re-invokes the provider.
_COMMITTED_REVIEWS_MAX_ENTRIES = 512


def lookup_committed_review(attempt_identity: str) -> dict[str, Any] | None:
    """Return a copy of the committed decision for an attempt identity."""
    try:
        record = _committed_reviews.get(str(attempt_identity))
    except Exception:
        return None
    return dict(record) if isinstance(record, dict) else None


def record_committed_review(
    attempt_identity: str,
    result_payload: Mapping[str, Any],
    *,
    store: Any | None = None,
) -> dict[str, Any] | None:
    """Commit a completed verified verdict under its attempt identity.

    Returns the committed copy, or None when the payload is not a committable
    completed review (unavailable verdicts are never committed).
    """
    try:
        if str(result_payload.get("verdict") or "") not in _COMMITTED_REVIEW_VERDICTS:
            return None
        provenance = result_payload.get("reviewProvenance")
        if not isinstance(provenance, Mapping):
            return None
        issues = [
            dict(issue)
            for issue in (result_payload.get("issues") or [])
            if isinstance(issue, Mapping)
        ][:ISSUES_MAX_COUNT]
        record = {
            "verdict": str(result_payload.get("verdict")),
            "confidence": result_payload.get("confidence", 0.0),
            "feedback": result_payload.get("feedback"),
            "issues": issues,
            "reviewProvenance": dict(provenance),
            "recommendedNextAction": result_payload.get("recommendedNextAction"),
            "recoverableInCurrentRuntime": bool(
                result_payload.get("recoverableInCurrentRuntime", False)
            ),
            # Preserve the complete bounded canonical gate payload so a
            # same-worker retry returns the identical committed decision:
            # dropping these fields would turn an ADDITIONAL_WORK_NEEDED
            # result into one without routing/remaining-work evidence, or a
            # passing result into one without validated references.
            "validatedRefs": dict(result_payload.get("validatedRefs") or {})
            if isinstance(result_payload.get("validatedRefs"), Mapping)
            else {},
            "invalidatedRefs": list(result_payload.get("invalidatedRefs") or [])
            if isinstance(result_payload.get("invalidatedRefs"), list)
            else [],
            "remainingWorkRef": result_payload.get("remainingWorkRef"),
            "blockingEvidenceRefs": list(
                result_payload.get("blockingEvidenceRefs") or []
            )
            if isinstance(result_payload.get("blockingEvidenceRefs"), list)
            else [],
            "targetLogicalStepId": result_payload.get("targetLogicalStepId"),
            "workspacePolicyRecommendation": result_payload.get(
                "workspacePolicyRecommendation"
            ),
            "invalid": bool(result_payload.get("invalid", False)),
            "degraded": bool(result_payload.get("degraded", False)),
            "downgradeReason": result_payload.get("downgradeReason"),
        }
        target = store if isinstance(store, dict) else _committed_reviews
        target[str(attempt_identity)] = record
        if not isinstance(store, dict):
            while len(target) > _COMMITTED_REVIEWS_MAX_ENTRIES:
                target.pop(next(iter(target)))
        return dict(record)
    except Exception:
        return None


def clear_committed_reviews(*, store: Any | None = None) -> None:
    """Clear committed-review records. Test and recovery hook only."""
    try:
        target = store if isinstance(store, dict) else _committed_reviews
        target.clear()
    except Exception:
        # Clearing is best-effort test/recovery hygiene; a failing clear
        # must not mask the caller's outcome.
        pass


def _lookup_committed_review(
    attempt_identity: str, store: Any | None
) -> dict[str, Any] | None:
    try:
        target = store if isinstance(store, dict) else _committed_reviews
        record = target.get(str(attempt_identity))
    except Exception:
        return None
    return dict(record) if isinstance(record, dict) else None


def _validate_decoded_sizes(decoded: Mapping[str, Any]) -> str | None:
    """Return an unavailable code when structured output exceeds ceilings."""
    feedback = decoded.get("feedback")
    if isinstance(feedback, str) and len(feedback) > FEEDBACK_MAX_CHARS:
        return "reviewer_truncated"
    issues = decoded.get("issues")
    if isinstance(issues, list):
        if len(issues) > ISSUES_MAX_COUNT:
            return "reviewer_truncated"
        for issue in issues:
            if not isinstance(issue, Mapping):
                continue
            description = issue.get("description")
            if isinstance(description, str) and len(description) > ISSUE_DESCRIPTION_MAX_CHARS:
                return "reviewer_truncated"
            evidence = issue.get("evidence")
            if isinstance(evidence, str) and len(evidence) > ISSUE_DESCRIPTION_MAX_CHARS:
                return "reviewer_truncated"
    confidence = decoded.get("confidence")
    if isinstance(confidence, bool):
        # Booleans are not numeric confidence: `float(True) == 1.0` would
        # otherwise authorize advancement on malformed provider evidence.
        # Reject explicitly so the caller returns `reviewer_malformed`.
        return "reviewer_malformed"
    elif isinstance(confidence, (int, float)):
        if not math.isfinite(float(confidence)):
            return "reviewer_malformed"
    elif isinstance(confidence, str):
        normalized = confidence.strip().lower()
        if normalized in {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity"}:
            return "reviewer_malformed"
    return None


async def step_review_activity(
    payload: Mapping[str, Any],
    *,
    reviewer: Any = None,
    committed_store: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the configured reviewer and fail closed on unavailable evidence."""
    if reviewer is None:
        return _unavailable(
            "reviewer_unavailable",
            "no reviewer implementation is configured",
            provenance=_early_provenance(payload, None),
        )
    provenance: Mapping[str, Any] | None = None
    try:
        raw_timeout = payload.get("review_timeout_seconds", 120)
        try:
            timeout_value = int(raw_timeout)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError("review timeout must be a positive number") from None
        if timeout_value < REVIEW_TIMEOUT_MIN_SECONDS:
            raise ValueError("review timeout must be positive")
        if timeout_value > REVIEW_TIMEOUT_MAX_SECONDS:
            return _unavailable(
                "review_timeout_over_budget",
                "requested review timeout exceeds the deployment ceiling",
                provenance=_early_provenance(
                    payload, reviewer, timeout_value=timeout_value
                ),
            )
        raw_inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
        raw_result = (
            payload.get("execution_result")
            if isinstance(payload.get("execution_result"), dict)
            else {}
        )
        raw_context = (
            payload.get("workflow_context")
            if isinstance(payload.get("workflow_context"), dict)
            else {}
        )
        raw_feedback = (
            str(payload["previous_feedback"])
            if payload.get("previous_feedback") is not None
            else None
        )
        if raw_feedback is not None and len(raw_feedback) > FEEDBACK_MAX_CHARS:
            return _unavailable(
                "review_evidence_too_large",
                "review feedback exceeds the bounded evidence budget",
                provenance=_early_provenance(
                    payload, reviewer, timeout_value=timeout_value
                ),
            )
        for section_name, section in (
            ("inputs", raw_inputs),
            ("execution_result", raw_result),
            ("workflow_context", raw_context),
        ):
            if _section_too_large(section):
                return _unavailable(
                    "review_evidence_too_large",
                    f"review {section_name} exceeds the bounded evidence budget",
                    provenance=_early_provenance(
                        payload, reviewer, timeout_value=timeout_value
                    ),
                )
        # Secret/data-use controls apply before any provider send.
        inputs = _redact_secrets(raw_inputs)
        execution_result = _redact_secrets(raw_result)
        workflow_context = _redact_secrets(raw_context)
        request = ReviewRequest(
            node_id=str(payload.get("node_id") or ""),
            step_index=int(payload.get("step_index") or 1),
            total_steps=int(payload.get("total_steps") or 1),
            review_attempt=int(payload.get("review_attempt") or 1),
            tool_name=str(payload.get("tool_name") or ""),
            tool_type=str(payload.get("tool_type") or "skill"),
            inputs=inputs if isinstance(inputs, dict) else {},
            execution_result=execution_result if isinstance(execution_result, dict) else {},
            workflow_context=workflow_context if isinstance(workflow_context, dict) else {},
            reviewer_model=str(payload.get("reviewer_model", "default")),
            review_timeout_seconds=timeout_value,
            previous_feedback=raw_feedback,
        )
        route = _resolve_route(reviewer, request.reviewer_model)
        evidence_digest = review_evidence_digest(
            node_id=request.node_id,
            step_index=request.step_index,
            review_attempt=request.review_attempt,
            tool_name=request.tool_name,
            tool_type=request.tool_type,
            inputs=request.inputs,
            execution_result=request.execution_result,
            workflow_context=request.workflow_context,
            previous_feedback=request.previous_feedback,
        )
        attempt_identity = review_attempt_identity(
            provider=route["provider"],
            model=route["model"],
            evidence_digest=evidence_digest,
            review_attempt=request.review_attempt,
        )
        provenance = {
            "provider": route["provider"],
            "model": route["model"],
            "evidenceDigest": evidence_digest,
            "reviewAttemptIdentity": attempt_identity,
            "reviewAttempt": request.review_attempt,
            "policy": {"timeoutSeconds": timeout_value},
        }
        # Duplicate delivery / lost-acknowledgment retry: reuse only the same
        # committed decision for this immutable attempt identity. A different
        # route or evidence set is a different identity and never reuses.
        committed = _lookup_committed_review(attempt_identity, committed_store)
        if committed is not None:
            return dict(committed)
        prompt = build_review_prompt(request)
        if len(prompt.encode("utf-8")) > PROMPT_BUDGET_BYTES:
            return _unavailable(
                "review_evidence_too_large",
                "review input exceeds the bounded evidence budget",
                provenance=provenance,
            )
        async with asyncio.timeout(timeout_value):
            text = await reviewer.review(
                prompt=prompt, model=request.reviewer_model,
                timeout=timeout_value,
            )
        if len(text.encode("utf-8")) > 64_000:
            return _unavailable(
                "reviewer_truncated",
                "configured reviewer response exceeds the bounded response budget",
                provenance=provenance,
            )
        try:
            decoded = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return _unavailable(
                "reviewer_malformed",
                "configured reviewer returned malformed evidence",
                provenance=provenance,
            )
        if not isinstance(decoded, dict):
            return _unavailable(
                "reviewer_malformed",
                "configured reviewer returned malformed evidence",
                provenance=provenance,
            )
        size_code = _validate_decoded_sizes(decoded)
        if size_code is not None:
            reason = (
                "configured reviewer returned oversized findings"
                if size_code == "reviewer_truncated"
                else "configured reviewer returned malformed evidence"
            )
            return _unavailable(reason_code(size_code), reason, provenance=provenance)
        gate = parse_step_gate_result(decoded)
        result_payload = gate.to_payload()
        result_payload["reviewProvenance"] = provenance
        # Commit the completed verified decision before acknowledging
        # completion so retried deliveries reuse it instead of re-invoking
        # the provider. Unavailable outcomes are never committed.
        record_committed_review(
            attempt_identity, result_payload, store=committed_store
        )
        return result_payload
    except (TimeoutError, asyncio.TimeoutError):
        return _unavailable(
            "reviewer_timeout", "configured reviewer timed out", provenance=provenance
        )
    except ReviewerUnavailable as exc:
        # ReviewerUnavailable subclasses ValueError, so it must be caught
        # before the generic ValueError branch to preserve its distinct
        # deployment-authority code (reviewer_disabled /
        # reviewer_misconfigured / reviewer_unavailable / reviewer_truncated /
        # review_timeout_invalid / review_timeout_over_budget). Never
        # propagate provider detail; keep the bounded reason secret-safe.
        return _unavailable(exc.code, "configured reviewer has no usable authority", provenance=provenance)
    except ValueError as exc:
        message = str(exc) or "invalid review request"
        if "review timeout" in message.lower():
            # Distinguish authoring errors from provider failures while
            # staying fail-closed: required evidence/timeout problems can
            # never authorize advancement.
            code = "review_timeout_invalid"
        else:
            code = "review_evidence_missing"
        if provenance is None:
            provenance = _early_provenance(payload, reviewer)
        return _unavailable(code, message, provenance=provenance)
    except Exception as exc:
        if provenance is None:
            provenance = _early_provenance(payload, reviewer)
        # Provider exception strings can contain request bodies and credentials.
        # Do not persist or log them in a workflow outcome.
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code:
            if code in {
                "reviewer_disabled",
                "reviewer_misconfigured",
                "reviewer_unavailable",
                "reviewer_truncated",
                "review_timeout_invalid",
                "review_timeout_over_budget",
            }:
                return _unavailable(code, "configured reviewer has no usable authority", provenance=provenance)
            if code in {"reviewer_malformed", "reviewer_transport"}:
                return _unavailable(code, "configured reviewer returned malformed evidence", provenance=provenance)
        exc_name = type(exc).__name__
        if "Timeout" in exc_name or "Cancelled" in exc_name:
            return _unavailable("reviewer_timeout", "configured reviewer timed out", provenance=provenance)
        if "HTTP" in exc_name or "Transport" in exc_name or "Network" in exc_name:
            return _unavailable(
                "reviewer_transport", "configured reviewer transport failed", provenance=provenance
            )
        return _unavailable("reviewer_unavailable", "configured reviewer failed or returned malformed evidence", provenance=provenance)


def reason_code(code: str) -> str:
    return code


def _unavailable(
    code: str, reason: str, *, provenance: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    payload = ReviewVerdict(
        verdict="NO_DETERMINATION", confidence=0.0,
        feedback=f"Review unavailable: {reason}. Preserve completed outputs and obtain review evidence before advancement.",
        issues=({"severity": "warning", "description": reason, "code": code},),
        recommended_next_action="needs_human", recoverable_in_current_runtime=False,
    ).to_payload()
    if provenance is not None:
        payload["reviewProvenance"] = dict(provenance)
    return payload


__all__ = [
    "step_review_activity",
    "review_evidence_digest",
    "review_attempt_identity",
    "lookup_committed_review",
    "record_committed_review",
    "clear_committed_reviews",
]
