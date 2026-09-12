"""Minimized replay for MoonLadderStudios/MoonMind#4225.

The 2026-09-10T18:00Z run published PR 4210 at step 08, then step 09
(``github.update_issue_status`` mode ``finalize_after_pr_or_done``) recorded
FAILED/blocked/reconciliation_required for conflicting
``status: in-progress`` + ``status: code-review`` labels. This replay loads
that recorded history shape from
``replays/finalize-mixed-labels-4225`` and proves it still replays: the mixed
labels still interpret as ``blocked_mixed`` (no silent normalization), the
no-PR path keeps the recorded FAILED outcome, and the with-PR path steers
add-only to COMPLETED/attention (degraded) with the PR handoff comment.
"""

from __future__ import annotations

import pytest

from moonmind.workflows.skills.tool_dispatcher import (
    ToolActivityDispatcher,
    execute_tool_activity,
)
from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
from moonmind.workflows.skills.tool_registry import ToolRegistrySnapshot
from moonmind.workflows.temporal import story_output_tools as story_tools
from moonmind.workflows.temporal.activity_runtime import (
    _default_registry_skill_payload,
)
from moonmind.workflows.temporal.github_issue_lifecycle import interpret_issue
from moonmind.workflows.temporal.story_output_tools import (
    GITHUB_UPDATE_ISSUE_STATUS_TOOL_NAME,
    register_story_output_tool_handlers,
    update_github_issue_status,
)
from tests.integration.reliability.helpers import load_replay
from tests.unit.workflows.temporal.test_github_issue_lifecycle import (
    _install,
    _LifecycleFakeService,
    _LifecycleHttpClient,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

REPLAY_ID = "finalize-mixed-labels-4225"


def test_recorded_failed_shape_still_replays() -> None:
    manifest = load_replay(REPLAY_ID, "manifest.json")
    expected = load_replay(REPLAY_ID, "expected-outcome.json")
    recorded = manifest["recordedHistory"]
    observed = recorded["observedIssue"]

    interpretation = interpret_issue({"state": observed["state"], "labels": observed["labels"]})
    assert interpretation.settled == expected["recordedSettled"]
    assert recorded["step09"]["recordedResult"]["status"] == "FAILED"
    assert recorded["step09"]["recordedResult"]["outputs"]["decision"] == expected["recordedDecision"]
    assert recorded["step09"]["recordedResult"]["outputs"]["reasonCode"] == expected["recordedReasonCode"]
    assert recorded["step08"]["outputs"]["pullRequestUrl"].endswith("/pull/4210")
    assert expected["recordedFailedShapeReplays"] is True


@pytest.mark.asyncio
async def test_replay_branches_without_and_with_pr(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    manifest = load_replay(REPLAY_ID, "manifest.json")
    expected = load_replay(REPLAY_ID, "expected-outcome.json")
    recorded = manifest["recordedHistory"]
    observed = recorded["observedIssue"]

    service = _LifecycleFakeService(initial_labels=list(observed["labels"]))
    _install(monkeypatch, service)
    blocked = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": observed["number"],
            "mode": "finalize_after_pr_or_done",
            "requireVerification": False,
        },
        github_service_factory=lambda: service,
    )
    assert blocked.status == "FAILED"
    assert blocked.outputs["decision"] == "blocked"
    assert expected["withoutPrStaysBlocked"] is True

    pr_artifact = tmp_path / "pr-4210.json"
    pr_artifact.write_text(
        f'{{"pullRequestUrl": "{recorded["step08"]["outputs"]["pullRequestUrl"]}"}}',
        encoding="utf-8",
    )
    steered = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": observed["number"],
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
            "requireVerification": False,
        },
        github_service_factory=lambda: service,
    )
    assert steered.status == "COMPLETED"
    assert steered.outputs["decision"] == expected["attentionDecision"]
    assert steered.outputs["degraded"] is expected["attentionDegraded"]
    assert steered.outputs["transition"]["toTarget"] == expected["attentionTransition"]
    assert ("add", "status: needs-attention") in service.operations
    assert [op for op in service.operations if op[0] == "remove"] == []
    assert expected["withPrSteersToAttention"] is True
    assert expected["attentionAddsOnly"] is True


@pytest.mark.asyncio
async def test_replay_with_pr_through_tool_activity_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Exercise the recorded with-PR steering through the production tool-activity boundary.

    The direct-call replay above proves the tool logic; this boundary replay
    proves activity-result propagation for the same recorded history: the
    dispatcher-registered ``github.update_issue_status`` skill (the handler the
    Temporal worker invokes via ``mm.tool.execute``) returns the promised
    COMPLETED/degraded terminal outcome with the PR handoff comment applied.
    """

    manifest = load_replay(REPLAY_ID, "manifest.json")
    expected = load_replay(REPLAY_ID, "expected-outcome.json")
    recorded = manifest["recordedHistory"]
    observed = recorded["observedIssue"]

    service = _LifecycleFakeService(initial_labels=list(observed["labels"]))
    _install(monkeypatch, service)
    # The dispatcher-registered handler calls update_github_issue_status with
    # its default github_service_factory (bound at def time), so patch both the
    # module attribute and the kwdefault to route the boundary through the fake.
    monkeypatch.setattr(
        story_tools, "GitHubService", lambda *args, **kwargs: service
    )
    monkeypatch.setattr(
        story_tools.update_github_issue_status,
        "__kwdefaults__",
        {
            **story_tools.update_github_issue_status.__kwdefaults__,
            "github_service_factory": lambda: service,
        },
    )

    dispatcher = ToolActivityDispatcher()
    register_story_output_tool_handlers(dispatcher)
    assert GITHUB_UPDATE_ISSUE_STATUS_TOOL_NAME in dispatcher._skill_handlers

    snapshot = ToolRegistrySnapshot(
        digest="reg:sha256:finalize-mixed-labels-4225",
        artifact_ref="art:sha256:finalize-mixed-labels-4225",
        skills=(
            parse_tool_definition(
                _default_registry_skill_payload(
                    name=GITHUB_UPDATE_ISSUE_STATUS_TOOL_NAME
                )
            ),
        ),
    )

    pr_artifact = tmp_path / "pr-4210-boundary.json"
    pr_artifact.write_text(
        f'{{"pullRequestUrl": "{recorded["step08"]["outputs"]["pullRequestUrl"]}"}}',
        encoding="utf-8",
    )
    result = await execute_tool_activity(
        invocation_payload={
            "id": "finalize-after-pr",
            "tool": {
                "type": "skill",
                "name": GITHUB_UPDATE_ISSUE_STATUS_TOOL_NAME,
            },
            "inputs": {
                "repository": "MoonLadderStudios/MoonMind",
                "issueNumber": observed["number"],
                "mode": "finalize_after_pr_or_done",
                "pullRequestArtifactPath": str(pr_artifact),
                "requireVerification": False,
            },
        },
        registry_snapshot=snapshot,
        dispatcher=dispatcher,
        context={
            "namespace": "default",
            "workflow_id": "mm:replay-finalize-mixed-4225",
            "run_id": "run-4225-replay",
            "node_id": "finalize-after-pr",
        },
    )
    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == expected["attentionDecision"]
    assert result.outputs["degraded"] is expected["attentionDegraded"]
    assert result.outputs["transition"]["toTarget"] == expected["attentionTransition"]
    assert result.outputs["reasonCode"] == "reconciliation_required"
    assert result.outputs["pullRequestUrl"].endswith("/pull/4210")
    assert ("add", "status: needs-attention") in service.operations
    assert [op for op in service.operations if op[0] == "remove"] == []
    assert "comment" in result.outputs["appliedActions"]
    assert result.outputs["sideEffect"]["operation"] == "github.issue.update"
    posted_bodies = [
        str(kwargs.get("json") or "") for _url, kwargs in _LifecycleHttpClient.posts
    ]
    assert any("/pull/4210" in body for body in posted_bodies)
