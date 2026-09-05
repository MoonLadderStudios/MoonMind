"""Tests for the step.review Temporal activity."""

from __future__ import annotations

import pytest
from fastapi import Request

from moonmind.workflows.temporal.activities.step_review import (
    step_review_activity,
)

@pytest.mark.asyncio
async def test_step_review_activity_does_not_infer_review_from_completed_execution():
    """MoonLadderStudios/MoonMind#3927: execution success is not review evidence."""
    result = await step_review_activity(
        {
            "node_id": "n1",
            "step_index": 1,
            "total_steps": 3,
            "review_attempt": 1,
            "tool_name": "repo.run_tests",
            "tool_type": "skill",
            "inputs": {"repo_ref": "git:org/repo#branch"},
            "execution_result": {"status": "COMPLETED", "outputs": {}},
            "workflow_context": {"plan_title": "Fix tests"},
        }
    )
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["confidence"] == 0.0
    assert result["recommendedNextAction"] == "needs_human"
    assert result["recoverableInCurrentRuntime"] is False
    assert result["issues"][0]["code"] == "reviewer_unavailable"
    assert "no reviewer implementation is configured" in result["feedback"]
    assert not result.get("validatedRefs")

@pytest.mark.asyncio
async def test_step_review_activity_with_minimal_payload():
    """Activity handles sparse payloads gracefully."""
    result = await step_review_activity(
        {
            "node_id": "n1",
            "step_index": 1,
            "total_steps": 1,
            "review_attempt": 1,
            "tool_name": "test",
        }
    )
    assert result["verdict"] == "NO_DETERMINATION"

@pytest.mark.asyncio
async def test_step_review_activity_with_previous_feedback():
    """Activity accepts previous_feedback without error."""
    result = await step_review_activity(
        {
            "node_id": "n1",
            "step_index": 2,
            "total_steps": 5,
            "review_attempt": 2,
            "tool_name": "repo.apply_patch",
            "tool_type": "skill",
            "inputs": {},
            "execution_result": {},
            "workflow_context": {},
            "previous_feedback": "Missing import in utils.py",
        }
    )
    assert result["verdict"] == "NO_DETERMINATION"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_verdict", ["PASS", "FAIL"])
@pytest.mark.parametrize("provider", ["google", "openai", "anthropic"])
@pytest.mark.parametrize("model", [None, "default", "explicit-review-model"])
async def test_configured_reviewer_crosses_worker_wrapper_and_provider_wire(provider, model, provider_verdict):
    import json
    from fastapi import FastAPI, Request
    from httpx import ASGITransport
    from temporalio.testing import ActivityEnvironment
    from moonmind.config.settings import AppSettings
    from moonmind.workflows.temporal.activity_runtime import TemporalReviewActivities, _bind_activity_handler
    from moonmind.workflows.temporal.activities.reviewer import ConfiguredStepReviewer

    config = AppSettings(default_chat_provider=provider)
    selected = getattr(config, provider)
    setattr(selected, f"{provider}_api_key", "hermetic-provider-credential")
    setattr(selected, f"{provider}_enabled", True)
    setattr(selected, f"{provider}_chat_model", "configured-review-model")
    app = FastAPI()
    requests = []

    @app.post("/{path:path}")
    async def provider_endpoint(path: str, request: Request):
        body = await request.json()
        requests.append((path, body))
        expected = "configured-review-model" if model in (None, "default") else model
        if provider == "google":
            assert expected in path
            prompt = body["contents"][0]["parts"][0]["text"]
        else:
            assert body["model"] == expected
            prompt = body["messages"][0]["content"]
        assert "actual execution evidence" in prompt
        response = json.dumps({"verdict": provider_verdict, "confidence": 0.8, "feedback": "Reviewed supplied execution evidence."})
        if provider == "google":
            return {"candidates": [{"content": {"parts": [{"text": response}]}}]}
        if provider == "openai":
            return {"choices": [{"message": {"content": response}}]}
        return {"content": [{"type": "text", "text": response}]}

    implementation = TemporalReviewActivities(reviewer=ConfiguredStepReviewer(config, transport=ASGITransport(app)))
    handler = _bind_activity_handler(implementation, func=TemporalReviewActivities.step_review, activity_type="step.review")
    payload = {"node_id": "review", "execution_result": {"summary": "actual execution evidence"}}
    if model is not None:
        payload["reviewer_model"] = model
    result = await ActivityEnvironment().run(handler, payload)
    assert requests
    assert result["verdict"] == ("FULLY_IMPLEMENTED" if provider_verdict == "PASS" else "ADDITIONAL_WORK_NEEDED")
    assert result["confidence"] == 0.8


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["not json", "[]", '{"verdict":"NEW_PROVIDER_STATE"}'])
async def test_configured_reviewer_unknown_or_malformed_wire_result(response):
    from fastapi import FastAPI
    from httpx import ASGITransport
    from moonmind.config.settings import AppSettings, OpenAISettings
    from moonmind.workflows.temporal.activities.reviewer import ConfiguredStepReviewer
    app = FastAPI()
    @app.post("/v1/chat/completions")
    async def provider_endpoint():
        return {"choices": [{"message": {"content": response}}]}
    config = AppSettings(default_chat_provider="openai", openai=OpenAISettings(openai_api_key="hermetic", openai_enabled=True))
    result = await step_review_activity({"node_id": "review"}, reviewer=ConfiguredStepReviewer(config, transport=ASGITransport(app)))
    assert result["verdict"] == "NO_DETERMINATION"
    assert not result.get("validatedRefs")


@pytest.mark.asyncio
async def test_configured_reviewer_missing_credential_cannot_fallback():
    from moonmind.config.settings import AppSettings, OpenAISettings
    from moonmind.workflows.temporal.activities.reviewer import ConfiguredStepReviewer
    config = AppSettings(default_chat_provider="openai", openai=OpenAISettings(openai_api_key=None, openai_enabled=True))
    result = await step_review_activity({"node_id": "review"}, reviewer=ConfiguredStepReviewer(config))
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["issues"][0]["code"] == "reviewer_unavailable"


@pytest.mark.asyncio
async def test_configured_reviewer_timeout_preserves_completed_outputs():
    import asyncio
    from fastapi import FastAPI
    from httpx import ASGITransport
    from moonmind.config.settings import AppSettings, OpenAISettings
    from moonmind.workflows.temporal.activities.reviewer import ConfiguredStepReviewer
    app = FastAPI()
    @app.post("/v1/chat/completions")
    async def provider_endpoint():
        await asyncio.sleep(2)
        return {}
    config = AppSettings(default_chat_provider="openai", openai=OpenAISettings(openai_api_key="hermetic", openai_enabled=True))
    execution = {"status": "COMPLETED", "outputs": {"summary": "completed work"}}
    result = await step_review_activity({"node_id": "review", "review_timeout_seconds": 1, "execution_result": execution}, reviewer=ConfiguredStepReviewer(config, transport=ASGITransport(app)))
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["issues"][0]["code"] == "reviewer_timeout"
    assert execution["status"] == "COMPLETED"


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, "invalid", -1, 0])
async def test_invalid_review_timeout_returns_unavailable_without_losing_execution(timeout):
    from moonmind.config.settings import AppSettings
    from moonmind.workflows.temporal.activities.reviewer import ConfiguredStepReviewer
    execution = {"status": "COMPLETED", "outputs": {"summary": "accepted work"}}
    result = await step_review_activity(
        {"review_timeout_seconds": timeout, "execution_result": execution},
        reviewer=ConfiguredStepReviewer(AppSettings()),
    )
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["recoverableInCurrentRuntime"] is False
    assert execution["status"] == "COMPLETED"
