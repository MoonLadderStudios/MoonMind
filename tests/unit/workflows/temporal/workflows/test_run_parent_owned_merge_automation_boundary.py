from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def _patch_workflow_context(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-parent", "run_id": "run-parent"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )


@pytest.mark.asyncio
async def test_parent_owned_merge_automation_awaits_child_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workflow_context(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"
    workflow._publish_context["branch"] = "feature"
    workflow._publish_context["baseRef"] = "main"
    workflow._publish_context["headSha"] = "abc123"
    calls: list[dict[str, Any]] = []

    async def fake_execute_child_workflow(
        workflow_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        calls.append({"workflow_type": workflow_type, "payload": payload, "kwargs": kwargs})
        assert workflow._awaiting_external is True
        return {"status": "merged", "prNumber": 350, "prUrl": payload["pullRequest"]["url"]}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )

    await workflow._maybe_start_merge_gate(
        parameters={
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True, "jiraIssueKey": "MM-350"},
        },
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/350",
    )

    assert calls[0]["workflow_type"] == "MoonMind.MergeAutomation"
    assert calls[0]["payload"]["workflowType"] == "MoonMind.MergeAutomation"
    assert workflow._awaiting_external is False
    assert workflow._publish_context["mergeAutomationStatus"] == "merged"
    assert workflow._publish_context["mergeAutomationWorkflowId"].startswith(
        "merge-automation:"
    )


@pytest.mark.asyncio
async def test_parent_owned_merge_automation_blocks_parent_success_on_child_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workflow_context(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"
    workflow._publish_context["branch"] = "feature"
    workflow._publish_context["baseRef"] = "main"
    workflow._publish_context["headSha"] = "abc123"

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "status": "blocked",
            "blockers": [{"summary": "Required checks are failing."}],
        }

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )

    with pytest.raises(ValueError, match="Required checks are failing"):
        await workflow._maybe_start_merge_gate(
            parameters={
                "publishMode": "pr",
                "mergeAutomation": {"enabled": True, "jiraIssueKey": "MM-350"},
            },
            pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/350",
        )

    assert workflow._awaiting_external is False
    assert workflow._publish_context["mergeAutomationStatus"] == "blocked"


@pytest.mark.asyncio
async def test_parent_owned_merge_automation_duplicate_retry_preserves_one_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workflow_context(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"
    workflow._publish_context["branch"] = "feature"
    workflow._publish_context["baseRef"] = "main"
    workflow._publish_context["headSha"] = "abc123"
    calls: list[str] = []

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        calls.append(str(kwargs["id"]))
        return {"status": "merged", "prNumber": 350}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    parameters = {
        "publishMode": "pr",
        "mergeAutomation": {"enabled": True, "jiraIssueKey": "MM-350"},
    }

    await workflow._maybe_start_merge_gate(
        parameters=parameters,
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/350",
    )
    await workflow._maybe_start_merge_gate(
        parameters=parameters,
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/350",
    )

    assert len(calls) == 1
    assert workflow._publish_context["mergeAutomationWorkflowId"] == calls[0]
