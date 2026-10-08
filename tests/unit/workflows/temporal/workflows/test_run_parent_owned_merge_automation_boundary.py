from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from temporalio.workflow import ChildWorkflowCancellationType

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

def _patch_workflow_context(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"task_queue": "mm.workflow.user.v2", "namespace": "default", "workflow_id": "wf-parent", "run_id": "run-parent"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
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
        assert workflow._state == "awaiting_external"
        assert workflow._waiting_reason == "Waiting for PR merge automation."
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
    assert calls[0]["kwargs"]["cancellation_type"] == ChildWorkflowCancellationType.TRY_CANCEL
    assert workflow._awaiting_external is False
    assert workflow._publish_context["mergeAutomationStatus"] == "merged"
    assert workflow._publish_context["mergeAutomationWorkflowId"].startswith(
        "merge-automation:"
    )

@pytest.mark.asyncio
async def test_parent_owned_merge_automation_preserves_post_merge_jira_summary(
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
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert payload["mergeAutomationConfig"]["postMergeJira"]["enabled"] is True
        return {
            "status": "merged",
            "prNumber": 350,
            "prUrl": payload["pullRequest"]["url"],
            "postMergeJira": {
                "status": "noop_already_done",
                "issueKey": "MM-350",
                "alreadyDone": True,
                "transitioned": False,
            },
            "artifactRefs": {"postMergeJiraResolution": "art-resolution"},
        }

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

    summary = workflow._merge_automation_summary_from_context()

    assert summary is not None
    assert summary["postMergeJira"]["status"] == "noop_already_done"
    assert summary["artifactRefs"]["postMergeJiraResolution"] == "art-resolution"

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
async def test_parent_owned_merge_automation_canceled_child_cancels_parent(
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
        return {"status": "canceled", "summary": "operator canceled merge automation"}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )

    await workflow._maybe_start_merge_gate(
        parameters={
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True, "jiraIssueKey": "MM-353"},
        },
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/353",
    )

    assert workflow._cancel_requested is True
    assert workflow._awaiting_external is False
    assert workflow._publish_context["mergeAutomationStatus"] == "canceled"

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("child_result", "expected_reason"),
    [
        ({}, "merge automation failed: missing terminal status"),
        (
            {"status": "completed"},
            "merge automation failed: unsupported terminal status completed",
        ),
    ],
)
async def test_parent_owned_merge_automation_invalid_child_status_fails_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    child_result: dict[str, Any],
    expected_reason: str,
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
        return child_result

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    memo_statuses: list[str | None] = []
    original_update_memo = workflow._update_memo

    def record_memo_update() -> None:
        memo_statuses.append(workflow._publish_context.get("mergeAutomationStatus"))
        original_update_memo()

    monkeypatch.setattr(workflow, "_update_memo", record_memo_update)

    with pytest.raises(ValueError, match=expected_reason):
        await workflow._maybe_start_merge_gate(
            parameters={
                "publishMode": "pr",
                "mergeAutomation": {"enabled": True, "jiraIssueKey": "MM-353"},
            },
            pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/353",
        )

    assert workflow._awaiting_external is False
    assert workflow._publish_context["mergeAutomationStatus"] == "failed"
    assert workflow._publish_context["mergeAutomationSummary"] == expected_reason
    assert memo_statuses == ["awaiting_child", "failed"]

@pytest.mark.asyncio
async def test_parent_owned_merge_automation_never_publishes_unsupported_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workflow_context(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"
    workflow._publish_context["branch"] = "feature"
    workflow._publish_context["baseRef"] = "main"
    workflow._publish_context["headSha"] = "abc123"
    published_statuses: list[str | None] = []

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {"status": "completed"}

    def record_visibility() -> None:
        published_statuses.append(workflow._publish_context.get("mergeAutomationStatus"))

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(workflow, "_update_memo", record_visibility)
    monkeypatch.setattr(workflow, "_update_search_attributes", record_visibility)

    with pytest.raises(
        ValueError,
        match="merge automation failed: unsupported terminal status completed",
    ):
        await workflow._maybe_start_merge_gate(
            parameters={
                "publishMode": "pr",
                "mergeAutomation": {"enabled": True, "jiraIssueKey": "MM-353"},
            },
            pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/353",
        )

    assert "completed" not in published_statuses
    assert published_statuses[-1] == "failed"

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


@pytest.mark.asyncio
async def test_parent_owned_review_only_accepts_review_complete_without_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workflow_context(monkeypatch)
    parent = MoonMindRunWorkflow()
    parent._repo = "MoonLadderStudios/MoonMind"
    parent._publish_context["headSha"] = "abc123"
    parameters = {
        "publishMode": "none",
        "omnigentExecutionPlan": {
            "planRef": "omnigent-execution-plan:sha256:" + "a" * 64,
            "planDigest": "sha256:" + "a" * 64,
            "planArtifactRef": "artifact:review-parent-plan",
            "taskInputSnapshotRef": "artifact:review-parent-input",
            "taskInputSnapshotDigest": "sha256:" + "b" * 64,
        },
        "mergeAutomation": {
            "enabled": True,
            "finishMode": "review_only",
            "reviewLoop": {"enabled": True, "provider": "codex"},
        },
    }

    async def fake_execute_child_workflow(
        _workflow_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert payload["mergeAutomationConfig"]["finishMode"] == "review_only"
        summary = parent._merge_automation_summary_from_context()
        assert summary is not None
        assert summary["finishMode"] == "review_only"
        assert summary["status"] == "awaiting_child"
        assert payload["parentExecutionPlan"] == parameters["omnigentExecutionPlan"]
        return {"status": "review_complete", "latestHeadSha": "abc123"}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )

    await parent._maybe_start_merge_gate(
        parameters=parameters,
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/350",
    )

    assert parent._publish_context["mergeAutomationStatus"] == "review_complete"
    assert (
        parent._merge_automation_summary_from_context()["finishMode"] == "review_only"
    )
    assert parent._awaiting_external is False
    assert parent._merge_happened() is False
    assert parent._publish_status != "published"


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_mode", [None, "fix_only"])
async def test_parent_owned_default_mode_rejects_review_complete(
    monkeypatch: pytest.MonkeyPatch,
    finish_mode: str | None,
) -> None:
    _patch_workflow_context(monkeypatch)
    parent = MoonMindRunWorkflow()
    parent._repo = "MoonLadderStudios/MoonMind"
    parent._publish_context["headSha"] = "abc123"
    config: dict[str, Any] = {"enabled": True}
    if finish_mode is not None:
        config["finishMode"] = finish_mode

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {"status": "review_complete", "latestHeadSha": "abc123"}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )

    with pytest.raises(ValueError, match="unsupported terminal status review_complete"):
        await parent._maybe_start_merge_gate(
            parameters={"publishMode": "none", "mergeAutomation": config},
            pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/350",
        )

    assert parent._publish_context["mergeAutomationStatus"] == "failed"
    assert parent._awaiting_external is False


@pytest.mark.parametrize("finish_mode", ["merge", "fix_only", "review_only"])
def test_parent_merge_automation_summary_preserves_result_finish_mode(
    finish_mode: str,
) -> None:
    parent = MoonMindRunWorkflow()
    parent._publish_context["mergeAutomationResult"] = {
        "status": "review_complete" if finish_mode == "review_only" else "review_clean",
        "finishMode": finish_mode,
    }

    assert parent._merge_automation_summary_from_context().get("finishMode") == (
        finish_mode if finish_mode == "review_only" else None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_mode", [None, "merge", "fix_only", "review_only"])
@pytest.mark.parametrize("resolver_plan_patch", [False, True])
async def test_merge_gate_child_command_preserves_retained_authority_shape(
    monkeypatch: pytest.MonkeyPatch,
    finish_mode: str | None,
    resolver_plan_patch: bool,
) -> None:
    """Retained Omnigent modes carry authority only in the resolver template."""
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch: (
            resolver_plan_patch
            if patch
            == run_workflow_module.RUN_MERGE_AUTOMATION_OMNIGENT_RESOLVER_PLAN_PATCH
            else True
        ),
    )
    parent = MoonMindRunWorkflow()
    parent._repo = "MoonLadderStudios/MoonMind"
    parent._publish_context["headSha"] = "abc123"
    binding = {
        "planRef": "omnigent-execution-plan:sha256:" + "a" * 64,
        "planDigest": "sha256:" + "a" * 64,
        "planArtifactRef": "artifact:parent-plan",
        "taskInputSnapshotRef": "artifact:parent-input",
        "taskInputSnapshotDigest": "sha256:" + "b" * 64,
    }
    config: dict[str, Any] = {"enabled": True}
    if finish_mode is not None:
        config["finishMode"] = finish_mode
    if finish_mode == "review_only":
        config["reviewLoop"] = {"enabled": True, "provider": "codex"}
    calls = []

    async def execute_child(workflow_type, payload, **_kwargs):
        calls.append((workflow_type, payload))
        return {
            "status": (
                "review_complete" if finish_mode == "review_only" else "review_clean"
            ),
            "latestHeadSha": "abc123",
        }

    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_child_workflow", execute_child
    )
    await parent._maybe_start_merge_gate(
        parameters={
            "publishMode": "none" if finish_mode == "review_only" else "pr",
            "targetRuntime": "omnigent",
            "omnigentExecutionPlan": binding,
            "mergeAutomation": config,
        },
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/350",
    )

    assert len(calls) == 1
    workflow_type, payload = calls[0]
    assert workflow_type == "MoonMind.MergeAutomation"
    assert set(payload) == {
        "workflowType",
        "parentWorkflowId",
        "parentRunId",
        "principal",
        "publishContextRef",
        "pullRequest",
        "jiraIssueKey",
        "mergeAutomationConfig",
        "resolverTemplate",
        "idempotencyKey",
    } | ({"parentExecutionPlan"} if finish_mode == "review_only" else set())
    assert payload["resolverTemplate"] == {
        "repository": parent._repo,
        "targetRuntime": "omnigent",
        "requiredCapabilities": ["git", "gh"],
        "inputs": {"returnToGate": True},
        **({"parentOmnigentExecutionPlan": binding} if resolver_plan_patch else {}),
    }
    if finish_mode == "review_only":
        assert payload["parentExecutionPlan"] == binding
