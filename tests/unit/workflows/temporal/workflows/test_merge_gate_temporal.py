from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from moonmind.workflows.temporal.workflows import merge_gate as merge_gate_module
from moonmind.workflows.temporal.workflows.merge_gate import MoonMindMergeGateWorkflow


def _start_input() -> dict[str, Any]:
    return {
        "workflowType": "MoonMind.MergeGate",
        "parent": {"workflowId": "mm:parent", "runId": "run-1"},
        "pullRequest": {
            "repo": "MoonLadderStudios/MoonMind",
            "number": 341,
            "url": "https://github.com/MoonLadderStudios/MoonMind/pull/341",
            "headSha": "abc123",
            "headBranch": "feature",
            "baseBranch": "main",
        },
        "jiraIssueKey": "MM-341",
        "policy": {"mergeMethod": "squash"},
        "idempotencyKey": "merge-gate:mm-parent:MoonLadderStudios/MoonMind:341:abc123",
    }


@pytest.mark.asyncio
async def test_merge_gate_waits_with_sanitized_blockers(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = MoonMindMergeGateWorkflow()

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any) -> dict[str, Any]:
        assert activity_type == "merge_gate.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": False,
            "blockers": [
                {
                    "kind": "checks_running",
                    "summary": "Checks still running token=fixture-secret-value",
                    "retryable": True,
                    "source": "github",
                }
            ],
        }

    async def fake_wait_condition(*_args: Any, **_kwargs: Any) -> None:
        raise TimeoutError("stop test loop")

    monkeypatch.setattr(merge_gate_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(merge_gate_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(merge_gate_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_gate_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(merge_gate_module.workflow, "upsert_search_attributes", lambda _attrs: None)

    result = await workflow.run(_start_input())

    assert result["status"] == "waiting"
    summary = workflow.summary()
    assert summary["status"] == "waiting"
    assert summary["blockers"][0]["kind"] == "checks_running"
    assert "token=" not in summary["blockers"][0]["summary"]


@pytest.mark.asyncio
async def test_merge_gate_launches_one_resolver_when_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = MoonMindMergeGateWorkflow()
    calls: list[tuple[str, Any]] = []

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append((activity_type, payload))
        if activity_type == "merge_gate.evaluate_readiness":
            return {"headSha": "abc123", "ready": True, "blockers": []}
        if activity_type == "merge_gate.create_resolver_run":
            return {"workflowId": "mm:resolver", "runId": "run-resolver", "created": True}
        raise AssertionError(activity_type)

    monkeypatch.setattr(merge_gate_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(merge_gate_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_gate_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(merge_gate_module.workflow, "upsert_search_attributes", lambda _attrs: None)

    result = await workflow.run(_start_input())
    result_again = await workflow.run(_start_input())

    assert result["status"] == "completed"
    assert result_again["status"] == "completed"
    assert [name for name, _ in calls].count("merge_gate.create_resolver_run") == 1
    resolver_payload = calls[1][1]
    assert resolver_payload["idempotencyKey"] == "resolver:mm:parent:MoonLadderStudios/MoonMind:341:abc123"


@pytest.mark.asyncio
async def test_merge_gate_blocks_closed_pull_request(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = MoonMindMergeGateWorkflow()

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any) -> dict[str, Any]:
        assert activity_type == "merge_gate.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": False,
            "blockers": [
                {
                    "kind": "pull_request_closed",
                    "summary": "Pull request is closed.",
                    "retryable": False,
                    "source": "github",
                }
            ],
        }

    monkeypatch.setattr(merge_gate_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(merge_gate_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_gate_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(merge_gate_module.workflow, "upsert_search_attributes", lambda _attrs: None)

    result = await workflow.run(_start_input())

    assert result["status"] == "blocked"
    assert workflow.summary()["blockers"][0]["kind"] == "pull_request_closed"
