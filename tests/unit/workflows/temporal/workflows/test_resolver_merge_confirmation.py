"""A resolver's terminal claim cannot replace remote merge evidence."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.workflows import merge_automation as module
from tests.unit.workflows.temporal.workflows.test_merge_automation_temporal import (
    _payload_with_post_merge_github,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("disposition", ["merged", "already_merged"])
@pytest.mark.parametrize("remote", ["merged", "open", "closed_unmerged"])
async def test_resolver_merge_refreshes_remote_head_before_finalization(
    monkeypatch, disposition, remote
):
    calls = []
    reads = 0

    async def execute(activity, payload, **kwargs):
        nonlocal reads
        calls.append(activity)
        if activity == "merge_automation.evaluate_readiness":
            reads += 1
            if reads == 1:
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
            return {
                "headSha": "def456",
                "pullRequestMerged": remote == "merged",
                "pullRequestOpen": remote == "open",
            }
        if activity == "merge_automation.complete_post_merge_github":
            assert payload["pullRequest"]["headSha"] == "def456"
            assert payload["pullRequest"]["number"] == 350
            assert payload["pullRequest"]["repo"] == "MoonLadderStudios/MoonMind"
            return {"status": "succeeded", "required": True, "confirmedState": "closed"}
        raise AssertionError(activity)

    async def child(*args, **kwargs):
        # The escaped resolver returned no head; the parent must read GitHub.
        return {
            "status": "success",
            "mergeAutomationDisposition": disposition,
            "headSha": None,
        }

    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "execute_child_workflow", child)
    monkeypatch.setattr(module.workflow, "patched", lambda name: True)
    monkeypatch.setattr(module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(
        module.workflow, "info", lambda: SimpleNamespace(workflow_id="merge-test")
    )
    monkeypatch.setattr(module.workflow, "upsert_memo", lambda value: None)
    monkeypatch.setattr(module.workflow, "upsert_search_attributes", lambda value: None)
    result = await module.MoonMindMergeAutomationWorkflow().run(
        _payload_with_post_merge_github()
    )
    assert reads == 2
    if remote == "merged":
        assert result["status"] == disposition
        assert result["latestHeadSha"] == "def456"
        assert calls.count("merge_automation.complete_post_merge_github") == 1
    else:
        assert result["status"] == "failed"
        assert result["blockers"][0]["kind"] == "resolver_disposition_invalid"
        assert "merge_automation.complete_post_merge_github" not in calls
