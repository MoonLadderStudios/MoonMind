"""A late operator draft hold survives automation and uncertain responses."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from moonmind.workflows.adapters.github_service import GitHubService

HEAD = "a" * 40


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "original", "initial_timeline_retry", "lost_ack", "malformed_ack", "readback_retry", "readback_outage",
        "redrafted", "timeline_unavailable", "race", "race_lost_ack", "race_head_moved",
        "race_timeline_retry", "restore_lost_ack", "restore_outage", "user_ready_after_restore",
    ],
)
async def test_ready_preserves_remote_draft_holds_and_reconciles_ack(monkeypatch, scenario):
    service = GitHubService()
    service._repository_reader = AsyncMock(return_value=("https://api.github.com", {}))
    state = {
        "draft": True, "ready": 0, "restore": 0, "head": HEAD,
        "converted": scenario == "redrafted", "read_failures": 0, "timeline_failures": 0,
    }

    def respond(request):
        if request.method == "GET":
            if state["ready"] and (
                scenario == "readback_outage"
                or (scenario == "readback_retry" and not state["read_failures"])
            ):
                state["read_failures"] += 1
                raise httpx.ReadError("readback unavailable")
            return httpx.Response(200, json={
                "number": 7, "node_id": "PR_7", "state": "open", "draft": state["draft"],
                "head": {"sha": state["head"]}, "base": {"repo": {"full_name": "org/repo"}},
            })
        query = json.loads(request.content)["query"]
        if "timelineItems" in query:
            if scenario == "timeline_unavailable" or (
                scenario == "initial_timeline_retry" and not state["timeline_failures"]
            ) or (
                scenario == "race_timeline_retry" and state["ready"] and not state["timeline_failures"]
            ):
                state["timeline_failures"] += 1
                return httpx.Response(503)
            return httpx.Response(200, json={"data": {"node": {
                "id": "PR_7", "timelineItems": {"nodes":
                    [{"createdAt": "2026-10-15T01:00:00Z"}] if state["converted"] else []},
            }}})
        if "markPullRequestReadyForReview" in query:
            state["ready"] += 1
            state["draft"] = False
            state["converted"] = scenario in {
                "race", "race_lost_ack", "race_head_moved", "race_timeline_retry",
                "restore_lost_ack", "restore_outage", "user_ready_after_restore",
            }
            if scenario == "race_head_moved":
                state["head"] = "b" * 40
            if scenario in {"lost_ack", "race_lost_ack", "readback_retry", "readback_outage"}:
                raise httpx.ReadError("successful mutation lost its acknowledgment")
            if scenario == "malformed_ack":
                return httpx.Response(200, content=b"response interrupted")
        else:
            assert "convertPullRequestToDraft" in query
            state["restore"] += 1
            if scenario == "restore_outage":
                return httpx.Response(503)
            state["draft"] = scenario != "user_ready_after_restore"
            if scenario == "restore_lost_ack":
                raise httpx.ReadError("successful restore lost its acknowledgment")
        return httpx.Response(200, json={"data": {}})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs
    ))
    result = await service.mark_automation_draft_ready("org/repo", 7, HEAD, admitted_workflow_id="owner")
    assert state["ready"] <= 1 and state["restore"] <= 1
    if scenario == "initial_timeline_retry":
        assert state["timeline_failures"] == 1
    elif scenario == "timeline_unavailable":
        assert state["timeline_failures"] == 3
    if scenario in {"original", "initial_timeline_retry", "lost_ack", "malformed_ack", "readback_retry"}:
        assert result["ready"] is True
        assert state["ready"] == 1
    elif scenario in {"redrafted", "timeline_unavailable"}:
        assert state["draft"] is True
        assert state["ready"] == 0
        assert result["ready"] is False
    elif scenario in {"readback_outage", "restore_outage", "user_ready_after_restore"}:
        assert result["ready"] is False
        assert result["retryable"] is False
        assert result["reconciliationRequired"] is True
        assert state["read_failures"] <= 3
    else:
        assert state["draft"] is True
        assert state["ready"] == state["restore"] == 1
        assert result["ready"] is False
        assert result["status"] == "held"


@pytest.mark.asyncio
@pytest.mark.parametrize("draft,converted,expected", [(False, False, "ready"), (True, True, "held"), (False, True, "held"), (True, False, "unavailable")])
async def test_ready_observation_never_repeats_mutation(monkeypatch, draft, converted, expected):
    service = GitHubService()
    service._repository_reader = AsyncMock(return_value=("https://api.github.com", {}))
    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"number": 7, "node_id": "PR_7", "state": "open", "draft": draft, "head": {"sha": HEAD}, "base": {"repo": {"full_name": "org/repo"}}})
        query = json.loads(request.content)["query"]
        assert query.startswith("query(") and "timelineItems" in query
        return httpx.Response(200, json={"data": {"node": {"id": "PR_7", "timelineItems": {"nodes": [{"createdAt": "2026-10-15T01:00:00Z"}] if converted else []}}}})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    result = await service.mark_automation_draft_ready("org/repo", 7, HEAD, admitted_workflow_id="owner", observe_only=True)
    assert result["status"] == expected
    assert result["ready"] is (expected == "ready")
    assert result["draft"] is draft
