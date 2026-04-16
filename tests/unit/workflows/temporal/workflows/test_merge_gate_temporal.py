from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from moonmind.workflows.temporal.workflows import merge_gate as merge_gate_module
from moonmind.workflows.temporal.workflows.merge_gate import MoonMindMergeAutomationWorkflow


def _start_input() -> dict[str, Any]:
    return {
        "workflowType": "MoonMind.MergeAutomation",
        "parentWorkflowId": "mm:parent",
        "parentRunId": "run-1",
        "publishContextRef": "artifact://publish-context",
        "pullRequest": {
            "repo": "MoonLadderStudios/MoonMind",
            "number": 341,
            "url": "https://github.com/MoonLadderStudios/MoonMind/pull/341",
            "headSha": "abc123",
            "headBranch": "feature",
            "baseBranch": "main",
        },
        "jiraIssueKey": "MM-341",
        "mergeAutomationConfig": {
            "resolver": {"mergeMethod": "squash"},
            "timeouts": {"fallbackPollSeconds": 12},
        },
        "resolverTemplate": {"repository": "MoonLadderStudios/MoonMind"},
        "idempotencyKey": "merge-automation:mm-parent:MoonLadderStudios/MoonMind:341:abc123",
    }


@pytest.mark.asyncio
async def test_merge_gate_rechecks_after_wait_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    evaluations = 0
    wait_timeouts: list[object] = []

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal evaluations
        assert activity_type == "merge_automation.evaluate_readiness"
        evaluations += 1
        if evaluations == 2:
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

    async def fake_wait_condition(*_args: Any, **kwargs: Any) -> None:
        wait_timeouts.append(kwargs["timeout"])
        raise TimeoutError("stop test loop")

    monkeypatch.setattr(merge_gate_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(merge_gate_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(merge_gate_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_gate_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(merge_gate_module.workflow, "upsert_search_attributes", lambda _attrs: None)

    result = await workflow.run(_start_input())

    assert result["status"] == "blocked"
    summary = workflow.summary()
    assert summary["outputStatus"] == "blocked"
    assert str(wait_timeouts[0]) == "0:00:12"
    assert evaluations == 2


@pytest.mark.asyncio
async def test_merge_gate_launches_one_resolver_when_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    calls: list[tuple[str, Any]] = []

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append((activity_type, payload))
        if activity_type == "merge_automation.evaluate_readiness":
            return {"headSha": "abc123", "ready": True, "blockers": []}
        if activity_type == "merge_automation.create_resolver_run":
            return {"workflowId": "mm:resolver", "runId": "run-resolver", "created": True}
        raise AssertionError(activity_type)

    monkeypatch.setattr(merge_gate_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(merge_gate_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_gate_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(merge_gate_module.workflow, "upsert_search_attributes", lambda _attrs: None)

    result = await workflow.run(_start_input())
    result_again = await workflow.run(_start_input())

    assert result["status"] == "resolver_launched"
    assert result_again["status"] == "resolver_launched"
    assert [name for name, _ in calls].count("merge_automation.create_resolver_run") == 1
    resolver_payload = calls[1][1]
    assert resolver_payload["idempotencyKey"] == "resolver:mm:parent:MoonLadderStudios/MoonMind:341:abc123"
    assert resolver_payload["runInput"]["initialParameters"]["task"]["publish"]["mode"] == "none"


@pytest.mark.asyncio
async def test_merge_gate_blocks_closed_pull_request(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
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


@pytest.mark.asyncio
async def test_merge_automation_expires_without_resolver_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    payload = {**_start_input(), "expireAt": "2026-04-16T00:00:00+00:00"}
    calls: list[str] = []

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append(activity_type)
        if activity_type == "merge_automation.evaluate_readiness":
            return {
                "headSha": "abc123",
                "ready": False,
                "blockers": [
                    {
                        "kind": "checks_running",
                        "summary": "Checks still running.",
                        "retryable": True,
                        "source": "github",
                    }
                ],
            }
        raise AssertionError(activity_type)

    monkeypatch.setattr(merge_gate_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        merge_gate_module.workflow,
        "now",
        lambda: datetime(2026, 4, 16, 0, 0, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(merge_gate_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(merge_gate_module.workflow, "upsert_search_attributes", lambda _attrs: None)

    result = await workflow.run(payload)

    assert result["status"] == "expired"
    assert "merge_automation.create_resolver_run" not in calls
