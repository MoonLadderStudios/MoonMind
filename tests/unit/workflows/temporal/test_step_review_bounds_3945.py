"""Bounds, provenance, and retry-safety for step.review (MoonMind#3945)."""

from __future__ import annotations

import json

import pytest

from moonmind.workflows.skills.approval_policy import parse_step_gate_result
from moonmind.workflows.temporal.activities.reviewer import (
    REVIEW_MAX_OUTPUT_TOKENS,
    REVIEW_RESPONSE_MAX_BYTES,
    ReviewerUnavailable,
)
from moonmind.workflows.temporal.activities.step_review import (
    review_attempt_identity,
    review_evidence_digest,
    step_review_activity,
)


class _StubConfig:
    default_chat_provider = "openai"

    class openai:  # noqa: D106
        openai_api_key = "hermetic"
        openai_enabled = True
        openai_chat_model = "stub-model"


class _StubReviewer:
    def __init__(self, text: str, *, capture: dict | None = None) -> None:
        self._text = text
        self._capture = capture if capture is not None else {}
        self._config = _StubConfig()

    def describe_route(self, model: str) -> dict[str, str]:
        return {"provider": "openai", "model": model if model != "default" else "stub-model"}

    async def review(self, *, prompt: str, model: str, timeout: int) -> str:
        self._capture["prompt"] = prompt
        self._capture["model"] = model
        self._capture["timeout"] = timeout
        return self._text


def _payload(**overrides) -> dict:
    base: dict = {
        "node_id": "n1",
        "step_index": 1,
        "total_steps": 1,
        "review_attempt": 1,
        "tool_name": "repo.run_tests",
        "tool_type": "skill",
        "inputs": {"goal": "fix tests"},
        "execution_result": {"status": "COMPLETED"},
        "workflow_context": {"plan_title": "Fix tests"},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_positive_result_carries_provenance_binding():
    text = json.dumps({"verdict": "FULLY_IMPLEMENTED", "confidence": 0.9})
    capture: dict = {}
    result = await step_review_activity(_payload(), reviewer=_StubReviewer(text, capture=capture))
    assert result["verdict"] == "FULLY_IMPLEMENTED"
    provenance = result["reviewProvenance"]
    assert provenance["provider"] == "openai"
    assert provenance["model"] == "stub-model"
    assert provenance["evidenceDigest"].startswith("sha256:")
    assert provenance["reviewAttemptIdentity"].startswith("review:")
    assert provenance["policy"] == {"timeoutSeconds": 120}
    # Credentials never land in durable payloads.
    assert "hermetic" not in json.dumps(result)


@pytest.mark.asyncio
async def test_unavailable_result_carries_provenance():
    class _Failing(_StubReviewer):
        async def review(self, *, prompt: str, model: str, timeout: int) -> str:
            raise ReviewerUnavailable("no authority", code="reviewer_disabled")

    result = await step_review_activity(_payload(), reviewer=_Failing("unused"))
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["issues"][0]["code"] == "reviewer_disabled"
    assert result["reviewProvenance"]["evidenceDigest"].startswith("sha256:")


@pytest.mark.asyncio
async def test_reviewer_unavailable_truncated_and_timeout_carry_provenance():
    class _Truncated(_StubReviewer):
        async def review(self, *, prompt: str, model: str, timeout: int) -> str:
            raise ReviewerUnavailable("too large", code="reviewer_truncated")

    truncated = await step_review_activity(_payload(), reviewer=_Truncated("unused"))
    assert truncated["verdict"] == "NO_DETERMINATION"
    assert truncated["issues"][0]["code"] == "reviewer_truncated"
    assert truncated["reviewProvenance"]["evidenceDigest"].startswith("sha256:")
    assert truncated["reviewProvenance"]["reviewAttemptIdentity"].startswith("review:")

    class _Hanging(_StubReviewer):
        async def review(self, *, prompt: str, model: str, timeout: int) -> str:
            raise TimeoutError("slow provider")

    timed_out = await step_review_activity(_payload(), reviewer=_Hanging("unused"))
    assert timed_out["verdict"] == "NO_DETERMINATION"
    assert timed_out["issues"][0]["code"] == "reviewer_timeout"
    assert timed_out["reviewProvenance"]["evidenceDigest"].startswith("sha256:")


@pytest.mark.asyncio
async def test_secret_shaped_inputs_redacted_before_provider_send():
    text = json.dumps({"verdict": "FULLY_IMPLEMENTED", "confidence": 0.9})
    capture: dict = {}
    payload = _payload(inputs={"goal": "fix", "openai_api_key": "sk-secret-value"})
    await step_review_activity(payload, reviewer=_StubReviewer(text, capture=capture))
    assert "sk-secret-value" not in capture["prompt"]
    assert "[redacted]" in capture["prompt"]


@pytest.mark.asyncio
async def test_oversized_section_rejected_before_expansion():
    payload = _payload(inputs={"blob": "x" * 70_000})
    result = await step_review_activity(payload, reviewer=_StubReviewer("{}"))
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["issues"][0]["code"] == "review_evidence_too_large"


@pytest.mark.asyncio
async def test_timeout_over_deployment_ceiling_rejected():
    result = await step_review_activity(
        _payload(review_timeout_seconds=3600), reviewer=_StubReviewer("{}")
    )
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["issues"][0]["code"] == "review_timeout_over_budget"


@pytest.mark.asyncio
async def test_non_finite_confidence_is_malformed_not_positive():
    text = json.dumps({"verdict": "FULLY_IMPLEMENTED", "confidence": float("nan")})
    result = await step_review_activity(_payload(), reviewer=_StubReviewer(text))
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["issues"][0]["code"] == "reviewer_malformed"
    # Contract boundary also fails closed on non-finite confidence.
    gate = parse_step_gate_result({"verdict": "FULLY_IMPLEMENTED", "confidence": float("inf")})
    assert gate.verdict == "NO_DETERMINATION"


@pytest.mark.asyncio
async def test_oversized_findings_are_truncated_not_positive():
    text = json.dumps(
        {
            "verdict": "FULLY_IMPLEMENTED",
            "confidence": 0.9,
            "issues": [{"description": "x" * 5000}],
        }
    )
    result = await step_review_activity(_payload(), reviewer=_StubReviewer(text))
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["issues"][0]["code"] == "reviewer_truncated"


@pytest.mark.asyncio
async def test_malformed_wire_result_uses_distinct_code():
    result = await step_review_activity(
        _payload(), reviewer=_StubReviewer("not json{{{")
    )
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["issues"][0]["code"] == "reviewer_malformed"


def test_review_attempt_identity_stable_and_evidence_bound():
    kwargs = {
        "node_id": "n1",
        "step_index": 1,
        "review_attempt": 1,
        "tool_name": "repo.run_tests",
        "tool_type": "skill",
        "inputs": {"goal": "fix"},
        "execution_result": {"status": "COMPLETED"},
        "workflow_context": {},
        "previous_feedback": None,
    }
    digest = review_evidence_digest(**kwargs)
    first = review_attempt_identity(
        provider="openai", model="stub-model", evidence_digest=digest, review_attempt=1
    )
    second = review_attempt_identity(
        provider="openai", model="stub-model", evidence_digest=digest, review_attempt=1
    )
    assert first == second
    changed = review_attempt_identity(
        provider="openai",
        model="other-model",
        evidence_digest=digest,
        review_attempt=1,
    )
    assert changed != first
    other_digest = review_evidence_digest(**{**kwargs, "inputs": {"goal": "other"}})
    assert other_digest != digest


def test_reviewer_output_budget_constants():
    assert REVIEW_MAX_OUTPUT_TOKENS == 4096
    assert REVIEW_RESPONSE_MAX_BYTES == 64_000
