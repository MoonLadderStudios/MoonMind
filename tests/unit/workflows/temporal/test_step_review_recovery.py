"""Exercise bounded producer repair through the real step-review Activity."""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from moonmind.workflows.skills.approval_policy import parse_step_gate_result
from moonmind.workflows.temporal.activities import step_review as review
from moonmind.workflows.temporal.activities.reviewer import ConfiguredStepReviewer, ReviewerUnavailable


class SequenceReviewer:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []
        self.route = {"provider": "fixture", "model": "fixture-model"}

    def describe_route(self, model):
        return dict(self.route)

    async def review(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return await response()
        return response


def payload():
    return {
        "node_id": "review-current-candidate",
        "review_attempt": 2,
        "tool_name": "repo.run_tests",
        "execution_result": {"status": "COMPLETED", "outputs": {"candidate": "unchanged"}},
        "inputs": {"goal": "verify the selected issue"},
    }


def response(verdict="FULLY_IMPLEMENTED", **extra):
    return json.dumps({"verdict": verdict, "confidence": "high", **extra})


def run(reviewer=None, data=None, store=None):
    return asyncio.run(review.step_review_activity(
        payload() if data is None else data,
        reviewer=reviewer, committed_store={} if store is None else store,
    ))


@pytest.mark.parametrize("broken", [
    "not json", "[]", "{}", '{"confidence": "high"}',
    '{"verdict":"UNKNOWN"}',
    '{"verdict":"FULLY_IMPLEMENTED","verdict":"FULLY_IMPLEMENTED"}',
    '{"verdict":"FULLY_IMPLEMENTED","confidence":NaN}',
    response(invalid="false"), response(recoverableInCurrentRuntime="true"),
])
def test_one_report_repair_preserves_candidate_and_commits_only_valid_result(broken):
    reviewer = SequenceReviewer(broken, response())
    data = payload()
    before = copy.deepcopy(data)
    store = {}
    result = run(reviewer, data, store)
    assert result["verdict"] == "FULLY_IMPLEMENTED"
    assert result["recommendedNextAction"] == "advance"
    assert data == before
    assert len(reviewer.calls) == 2
    assert reviewer.calls[0]["model"] == reviewer.calls[1]["model"]
    assert 0 < reviewer.calls[1]["timeout"] <= reviewer.calls[0]["timeout"]
    identity = result["reviewProvenance"]["reviewAttemptIdentity"]
    assert result["reviewProvenance"]["reviewAttempt"] == 2
    assert result["reviewProvenance"]["reportRepair"]["outcome"] == "repaired"
    assert "previousResponse" not in json.dumps(result)
    assert store[identity]["verdict"] == "FULLY_IMPLEMENTED"
    repeated = run(reviewer, data, store)
    assert parse_step_gate_result(repeated) == parse_step_gate_result(result)
    assert len(reviewer.calls) == 2  # duplicate delivery reuses the same decision


@pytest.mark.parametrize("verdict,action", [
    ("ADDITIONAL_WORK_NEEDED", "reattempt_current_step"),
    ("ADDITIONAL_WORK_NEEDED", "needs_human"),
    ("NO_DETERMINATION", "blocked"),
    ("NO_DETERMINATION", "needs_human"),
    ("BLOCKED", "blocked"),
    ("FAILED_UNRECOVERABLE", "blocked"),
])
def test_valid_nonpass_or_human_decision_is_never_retried_for_approval(verdict, action):
    reviewer = SequenceReviewer(response(verdict, recommendedNextAction=action), response())
    result = run(reviewer)
    assert result["verdict"] == verdict
    assert result["recommendedNextAction"] == action
    assert len(reviewer.calls) == 1


@pytest.mark.parametrize("first,second", [
    (response("ADDITIONAL_WORK_NEEDED", recommendedNextAction="advance"), response()),
    (response(invalid=True), response()),
    (response(degraded=True), response()),
    (response("NO_DETERMINATION", recommendedNextAction="blocked", confidence=True), response()),
    (response("NO_DETERMINATION", recommendedNextAction="needs_human", confidence=True),
     response("NO_DETERMINATION", recommendedNextAction="blocked")),
])
def test_format_repair_cannot_reverse_declared_nonpass_or_explicit_stop(first, second):
    store = {}
    reviewer = SequenceReviewer(first, second)
    result = run(reviewer, store=store)
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["recommendedNextAction"] == "blocked"
    assert result["reviewProvenance"]["reportRepair"]["outcome"] == "invalid"
    assert store == {}
    assert len(reviewer.calls) == 2


def test_exhausted_report_repair_preserves_nonpass_without_human_fallback():
    reviewer = SequenceReviewer("{}", "{}", response())
    data = payload()
    before = copy.deepcopy(data)
    store = {}
    result = run(reviewer, data, store)
    assert len(reviewer.calls) == 2
    assert data == before and store == {}
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["recommendedNextAction"] == "blocked"
    assert result["recoverableInCurrentRuntime"] is False
    assert result["issues"][0]["code"] == "reviewer_malformed"
    assert parse_step_gate_result(result).recommended_next_action == "blocked"


@pytest.mark.parametrize("failure", [
    ReviewerUnavailable("credential detail must not escape", code="reviewer_disabled"),
    ReviewerUnavailable("credential detail must not escape", code="reviewer_unavailable"),
    TimeoutError("credential detail must not escape"),
    httpx.ConnectError("credential detail must not escape"),
])
def test_provider_failures_do_not_retry_business_work_or_request_manual_approval(failure):
    reviewer = SequenceReviewer(failure, response())
    result = run(reviewer)
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["recommendedNextAction"] == "blocked"
    assert len(reviewer.calls) == 1
    assert "credential detail" not in json.dumps(result)
    assert not result.get("validatedRefs")


def test_missing_reviewer_is_not_an_inferred_human_decision():
    result = run()
    assert result["recommendedNextAction"] == "blocked"
    assert result["issues"][0]["code"] == "reviewer_unavailable"


@pytest.mark.parametrize("recoverable,expected", [(False, "blocked"), (True, "reattempt_current_step")])
def test_fresh_inconclusive_result_has_explicit_evidence_continuation(recoverable, expected):
    reviewer = SequenceReviewer(response("NO_DETERMINATION", recoverableInCurrentRuntime=recoverable))
    result = run(reviewer)
    assert result["recommendedNextAction"] == expected
    assert len(reviewer.calls) == 1


def test_route_change_prevents_report_repair():
    reviewer = SequenceReviewer(response())
    async def first():
        reviewer.route["provider"] = "different-provider"
        return "{}"
    reviewer.responses = iter([first, response()])
    result = run(reviewer)
    assert len(reviewer.calls) == 1
    assert result["issues"][0]["code"] == "reviewer_route_changed"
    assert result["verdict"] == "NO_DETERMINATION"


def test_response_and_repair_prompt_budgets_are_not_relaxed(monkeypatch):
    oversized = SequenceReviewer("x" * 64_001, response())
    result = run(oversized)
    assert len(oversized.calls) == 1
    assert result["issues"][0]["code"] == "reviewer_truncated"
    monkeypatch.setattr(review, "PROMPT_BUDGET_BYTES", 20)
    monkeypatch.setattr(review, "build_review_prompt", lambda request: "small")
    repaired = SequenceReviewer("{}", response())
    result = run(repaired)
    assert len(repaired.calls) == 1
    assert result["issues"][0]["code"] == "review_evidence_too_large"


def test_report_repair_shares_one_deadline_instead_of_resetting_timeout():
    async def slow_initial():
        await asyncio.sleep(0.6)
        return "{}"
    async def slow_repair():
        await asyncio.sleep(0.6)
        return response()
    reviewer = SequenceReviewer(slow_initial, slow_repair)
    result = run(reviewer, {**payload(), "review_timeout_seconds": 1})
    assert len(reviewer.calls) == 2
    assert result["issues"][0]["code"] == "reviewer_timeout"
    assert result["verdict"] == "NO_DETERMINATION"


def test_cancellation_is_not_swallowed_or_retried():
    reviewer = SequenceReviewer(asyncio.CancelledError(), response())
    with pytest.raises(asyncio.CancelledError):
        run(reviewer)
    assert len(reviewer.calls) == 1


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
def test_repair_crosses_configured_provider_adapter_without_live_network(provider):
    requests = []
    async def wire(request):
        body = json.loads(request.content)
        requests.append(body)
        text = "{}" if len(requests) == 1 else response("ADDITIONAL_WORK_NEEDED")
        if provider == "google":
            result = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        elif provider == "openai":
            result = {"choices": [{"message": {"content": text}}]}
        else:
            result = {"content": [{"type": "text", "text": text}]}
        return httpx.Response(200, json=result)
    provider_config = SimpleNamespace(**{
        f"{provider}_enabled": True,
        f"{provider}_api_key": "test-only-credential",
        f"{provider}_chat_model": "fixture-model",
    })
    config = SimpleNamespace(default_chat_provider=provider, **{provider: provider_config})
    result = run(ConfiguredStepReviewer(config, transport=httpx.MockTransport(wire)))
    assert len(requests) == 2
    assert result["verdict"] == "ADDITIONAL_WORK_NEEDED"
    assert result["recommendedNextAction"] == "reattempt_current_step"
    assert result["reviewProvenance"]["provider"] == provider
    assert result["reviewProvenance"]["reportRepair"]["outcome"] == "repaired"
    assert "test-only-credential" not in json.dumps(result)
