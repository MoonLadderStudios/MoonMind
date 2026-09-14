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
REVIEW_TIMEOUT_MAX_SECONDS = 600

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
        pass
    elif isinstance(confidence, (int, float)):
        if not math.isfinite(float(confidence)):
            return "reviewer_malformed"
    elif isinstance(confidence, str):
        normalized = confidence.strip().lower()
        if normalized in {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity"}:
            return "reviewer_malformed"
    return None


async def step_review_activity(payload: Mapping[str, Any], *, reviewer: Any = None) -> dict[str, Any]:
    """Execute the configured reviewer and fail closed on unavailable evidence."""
    if reviewer is None:
        return _unavailable("reviewer_unavailable", "no reviewer implementation is configured")
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
        return result_payload
    except (TimeoutError, asyncio.TimeoutError):
        return _unavailable("reviewer_timeout", "configured reviewer timed out")
    except ValueError as exc:
        message = str(exc) or "invalid review request"
        if "review timeout" in message.lower():
            # Distinguish authoring errors from provider failures while
            # staying fail-closed: required evidence/timeout problems can
            # never authorize advancement.
            code = "review_timeout_invalid"
        else:
            code = "review_evidence_missing"
        return _unavailable(code, message)
    except Exception as exc:
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
                return _unavailable(code, "configured reviewer has no usable authority")
            if code in {"reviewer_malformed", "reviewer_transport"}:
                return _unavailable(code, "configured reviewer returned malformed evidence")
        exc_name = type(exc).__name__
        if "Timeout" in exc_name or "Cancelled" in exc_name:
            return _unavailable("reviewer_timeout", "configured reviewer timed out")
        if "HTTP" in exc_name or "Transport" in exc_name or "Network" in exc_name:
            return _unavailable(
                "reviewer_transport", "configured reviewer transport failed"
            )
        return _unavailable("reviewer_unavailable", "configured reviewer failed or returned malformed evidence")


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
]
