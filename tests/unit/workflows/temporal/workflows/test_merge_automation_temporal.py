from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from moonmind.workflows.temporal.workflows import merge_automation as merge_automation_module
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
)


def _payload() -> dict[str, Any]:
    return {
        "workflowType": "MoonMind.MergeAutomation",
        "parentWorkflowId": "wf-parent",
        "parentRunId": "run-parent",
        "publishContextRef": "artifact://publish-context",
        "pullRequest": {
            "repo": "MoonLadderStudios/MoonMind",
            "number": 350,
            "url": "https://github.com/MoonLadderStudios/MoonMind/pull/350",
            "headSha": "abc123",
            "headBranch": "feature",
            "baseBranch": "main",
        },
        "jiraIssueKey": "MM-350",
        "mergeAutomationConfig": {
            "gate": {
                "github": {
                    "checks": "required",
                    "automatedReview": "required",
                },
                "jira": {"status": "optional"},
            },
            "resolver": {"mergeMethod": "squash"},
            "timeouts": {"fallbackPollSeconds": 300},
        },
        "idempotencyKey": "merge-automation:wf-parent:MoonLadderStudios/MoonMind:350:abc123",
    }


@pytest.mark.asyncio
async def test_merge_automation_reenters_gate_after_resolver_remediation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    readiness_calls = 0
    child_results = [
        {
            "status": "success",
            "mergeAutomationDisposition": "reenter_gate",
            "headSha": "def456",
        },
        {
            "status": "success",
            "mergeAutomationDisposition": "merged",
        },
    ]
    child_workflow_ids: list[str] = []

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal readiness_calls
        assert activity_type == "merge_automation.evaluate_readiness"
        readiness_calls += 1
        head_sha = "abc123" if readiness_calls == 1 else "def456"
        return {
            "headSha": head_sha,
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    child_payloads: list[dict[str, Any]] = []

    async def fake_execute_child_workflow(
        workflow_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        child_payloads.append(payload)
        child_workflow_ids.append(str(kwargs["id"]))
        return child_results.pop(0)

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert readiness_calls == 2
    assert result["status"] == "merged"
    assert result["cycles"] == 2
    assert child_workflow_ids == [
        "resolver:wf-parent:MoonLadderStudios/MoonMind:350:abc123:1",
        "resolver:wf-parent:MoonLadderStudios/MoonMind:350:def456:2",
    ]
    assert child_payloads[0]["initialParameters"]["publishMode"] == "none"
    assert child_payloads[0]["initialParameters"]["task"]["tool"] == {
        "type": "skill",
        "name": "pr-resolver",
        "version": "1.0",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("disposition", "expected_status"),
    [
        ("merged", "merged"),
        ("already_merged", "already_merged"),
    ],
)
async def test_merge_automation_success_dispositions_complete_successfully(
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
    expected_status: str,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        return {"status": "success", "mergeAutomationDisposition": disposition}

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert result["status"] == expected_status
    assert result["blockers"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("disposition", "expected_summary"),
    [
        ("manual_review", "pr-resolver requested manual review."),
        ("failed", "pr-resolver reported failure."),
    ],
)
async def test_merge_automation_non_success_dispositions_fail(
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
    expected_summary: str,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        return {
            "status": "success",
            "mergeAutomationDisposition": disposition,
            "summary": "resolver details",
        }

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert result["status"] == "failed"
    assert result["summary"] == expected_summary
    assert result["blockers"]
    assert result["blockers"][0]["kind"] == disposition


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolver_result", "expected_summary"),
    [
        ({"status": "success"}, "pr-resolver child result missing mergeAutomationDisposition."),
        (
            {"status": "success", "mergeAutomationDisposition": "surprising"},
            "pr-resolver child result has unsupported mergeAutomationDisposition: surprising",
        ),
    ],
)
async def test_merge_automation_invalid_dispositions_fail_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    resolver_result: dict[str, Any],
    expected_summary: str,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        return resolver_result

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert result["status"] == "failed"
    assert result["summary"] == expected_summary
    assert result["blockers"]
    assert result["blockers"][0]["kind"] == "resolver_disposition_invalid"


@pytest.mark.asyncio
async def test_merge_automation_ignores_wait_condition_timeout_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    readiness_calls = 0

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal readiness_calls
        assert activity_type == "merge_automation.evaluate_readiness"
        readiness_calls += 1
        if readiness_calls == 1:
            return {
                "headSha": "abc123",
                "ready": False,
                "pullRequestOpen": True,
                "policyAllowed": True,
                "checksComplete": False,
            }
        return {
            "headSha": "abc123",
            "ready": False,
            "pullRequestOpen": False,
            "policyAllowed": True,
        }

    async def fake_wait_condition(*_args: Any, **_kwargs: Any) -> None:
        raise TimeoutError

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "wait_condition",
        fake_wait_condition,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert readiness_calls == 2
    assert result["status"] == "blocked"


@pytest.mark.asyncio
async def test_merge_automation_propagates_unexpected_wait_condition_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": False,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": False,
        }

    async def fake_wait_condition(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("unexpected workflow wait failure")

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "wait_condition",
        fake_wait_condition,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    with pytest.raises(RuntimeError, match="unexpected workflow wait failure"):
        await workflow.run(_payload())
