"""Bounds, provenance, and retry-safety for step.review (MoonMind#3945)."""

from __future__ import annotations

import json

import pytest

from moonmind.workflows.skills.approval_policy import (
    parse_step_gate_result,
    review_gate_retry_allowed,
)
from moonmind.workflows.temporal.activities.reviewer import (
    REVIEW_MAX_OUTPUT_TOKENS,
    REVIEW_RESPONSE_MAX_BYTES,
    ReviewerUnavailable,
)
from moonmind.workflows.temporal.activities.step_review import (
    clear_committed_reviews,
    lookup_committed_review,
    review_attempt_identity,
    review_evidence_digest,
    step_review_activity,
)


@pytest.fixture(autouse=True)
def _isolated_committed_reviews():
    """Keep committed-review state hermetic across tests."""
    clear_committed_reviews()
    yield
    clear_committed_reviews()


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


# --- R2: pre-provenance early returns bind the review attempt (MoonMind#3945) ---


@pytest.mark.asyncio
async def test_reviewer_none_unavailable_carries_evidence_binding():
    result = await step_review_activity(_payload())
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["issues"][0]["code"] == "reviewer_unavailable"
    provenance = result["reviewProvenance"]
    assert provenance["provider"] == "unknown"
    assert provenance["evidenceDigest"].startswith("sha256:")
    assert provenance["reviewAttemptIdentity"].startswith("review:")
    assert "hermetic" not in json.dumps(result)


@pytest.mark.asyncio
async def test_over_budget_timeout_carries_evidence_binding():
    result = await step_review_activity(
        _payload(review_timeout_seconds=3600), reviewer=_StubReviewer("{}")
    )
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["issues"][0]["code"] == "review_timeout_over_budget"
    provenance = result["reviewProvenance"]
    assert provenance["evidenceDigest"].startswith("sha256:")
    assert provenance["reviewAttemptIdentity"].startswith("review:")
    assert provenance["policy"] == {"timeoutSeconds": 3600}


@pytest.mark.asyncio
async def test_oversized_section_carries_evidence_binding():
    payload = _payload(inputs={"blob": "x" * 70_000})
    result = await step_review_activity(payload, reviewer=_StubReviewer("{}"))
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["issues"][0]["code"] == "review_evidence_too_large"
    assert result["reviewProvenance"]["evidenceDigest"].startswith("sha256:")


@pytest.mark.asyncio
async def test_authoring_error_carries_evidence_binding():
    result = await step_review_activity(
        _payload(review_timeout_seconds=-1), reviewer=_StubReviewer("{}")
    )
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["issues"][0]["code"] == "review_timeout_invalid"
    assert result["reviewProvenance"]["evidenceDigest"].startswith("sha256:")


def test_provenance_survives_workflow_parse_and_persist_boundary():
    """The run.py scheduler persists gate_result.to_payload(); provenance must survive."""
    activity_payload = {
        "verdict": "FULLY_IMPLEMENTED",
        "confidence": 0.9,
        "feedback": "Reviewed supplied execution evidence.",
        "reviewProvenance": {
            "provider": "openai",
            "model": "stub-model",
            "evidenceDigest": "sha256:abc",
            "reviewAttemptIdentity": "review:abc123",
            "reviewAttempt": 1,
            "policy": {"timeoutSeconds": 120},
        },
    }
    gate = parse_step_gate_result(activity_payload)
    assert gate.verdict == "FULLY_IMPLEMENTED"
    persisted = gate.to_payload()
    assert persisted["reviewProvenance"]["reviewAttemptIdentity"] == "review:abc123"
    assert persisted["reviewProvenance"]["evidenceDigest"] == "sha256:abc"


def test_recorded_history_without_provenance_still_parses():
    """Replay compatibility: payloads recorded before provenance parse unchanged."""
    gate = parse_step_gate_result({"verdict": "FULLY_IMPLEMENTED", "confidence": 0.8})
    assert gate.verdict == "FULLY_IMPLEMENTED"
    assert gate.review_provenance is None
    assert "reviewProvenance" not in gate.to_payload()


# --- R5: keyed committed-decision reuse without repeating work (MoonMind#3945) ---


class _CountingReviewer(_StubReviewer):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.calls = 0

    async def review(self, *, prompt: str, model: str, timeout: int) -> str:
        self.calls += 1
        return await super().review(prompt=prompt, model=model, timeout=timeout)


@pytest.mark.asyncio
async def test_duplicate_delivery_reuses_same_committed_decision():
    text = json.dumps({"verdict": "FULLY_IMPLEMENTED", "confidence": 0.9})
    store: dict = {}
    reviewer = _CountingReviewer(text)
    first = await step_review_activity(
        _payload(), reviewer=reviewer, committed_store=store
    )
    assert first["verdict"] == "FULLY_IMPLEMENTED"
    assert reviewer.calls == 1
    identity = first["reviewProvenance"]["reviewAttemptIdentity"]

    second = await step_review_activity(
        _payload(), reviewer=reviewer, committed_store=store
    )
    assert reviewer.calls == 1
    assert second["verdict"] == "FULLY_IMPLEMENTED"
    assert second["reviewProvenance"]["reviewAttemptIdentity"] == identity
    # The committed record carries no credentials.
    assert "hermetic" not in json.dumps(lookup_committed_review(identity))


@pytest.mark.asyncio
async def test_changed_evidence_is_a_new_attempt_not_a_reuse():
    text = json.dumps({"verdict": "FULLY_IMPLEMENTED", "confidence": 0.9})
    store: dict = {}
    reviewer = _CountingReviewer(text)
    first = await step_review_activity(
        _payload(), reviewer=reviewer, committed_store=store
    )
    changed = await step_review_activity(
        _payload(inputs={"goal": "different work"}),
        reviewer=reviewer,
        committed_store=store,
    )
    assert reviewer.calls == 2
    assert (
        changed["reviewProvenance"]["reviewAttemptIdentity"]
        != first["reviewProvenance"]["reviewAttemptIdentity"]
    )


@pytest.mark.asyncio
async def test_unavailable_reviews_are_never_committed():
    class _Failing(_StubReviewer):
        async def review(self, *, prompt: str, model: str, timeout: int) -> str:
            raise ReviewerUnavailable("no authority", code="reviewer_disabled")

    store: dict = {}
    result = await step_review_activity(
        _payload(), reviewer=_Failing("unused"), committed_store=store
    )
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["recommendedNextAction"] == "blocked"
    assert result["recoverableInCurrentRuntime"] is False
    assert store == {}
    assert (
        lookup_committed_review(
            result["reviewProvenance"]["reviewAttemptIdentity"]
        )
        is None
    )


@pytest.mark.parametrize("action", ["blocked", "needs_human"])
def test_workflow_boundary_never_reruns_business_step_for_unavailable_reviewer(action):
    """Scheduler harness: an unavailable review must not authorize another attempt.

    run.py branches on review_gate_retry_allowed(); NO_DETERMINATION with
    blocked or needs_human + recoverable False must refuse retry so the completed
    business step is preserved and the run stops instead of re-executing.
    """
    unavailable = parse_step_gate_result(
        {
            "verdict": "NO_DETERMINATION",
            "confidence": 0.0,
            "recommendedNextAction": action,
            "recoverableInCurrentRuntime": False,
        }
    ).to_review_verdict()
    assert (
        review_gate_retry_allowed(
            verdict=unavailable,
            review_retry_count=0,
            max_review_attempts=3,
            consecutive_no_progress_attempts=0,
            max_consecutive_no_progress_attempts=3,
        )
        is False
    )


def test_workflow_boundary_preserves_bounded_retry_for_actionable_verdicts():
    """The no-rerun rule for unavailable reviews must not remove real retries."""
    actionable = parse_step_gate_result(
        {
            "verdict": "ADDITIONAL_WORK_NEEDED",
            "confidence": 0.7,
            "feedback": "Missing test coverage.",
            "recommendedNextAction": "reattempt_current_step",
            "recoverableInCurrentRuntime": True,
        }
    ).to_review_verdict()
    assert (
        review_gate_retry_allowed(
            verdict=actionable,
            review_retry_count=0,
            max_review_attempts=3,
            consecutive_no_progress_attempts=0,
            max_consecutive_no_progress_attempts=3,
        )
        is True
    )
