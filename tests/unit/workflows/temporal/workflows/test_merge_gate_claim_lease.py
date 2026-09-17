"""Review waits retain the issue claim through the existing lease owner."""

import asyncio
from datetime import timedelta

import pytest

from moonmind.workflows.temporal.workflows import run as module
from tests.unit.workflows.temporal.workflows.test_run_parent_owned_merge_automation_boundary import (
    _patch_workflow_context,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claimed,patched", [(True, True), (False, True), (True, False)]
)
async def test_merge_wait_renews_only_claimed_new_histories(
    monkeypatch, claimed, patched
):
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(module.workflow, "patched", lambda _: patched)
    parent = module.MoonMindRunWorkflow()
    parent._repo = "example/repo"
    parent._publish_context.update(branch="feature", baseRef="main", headSha="abc123")
    lease = {"owner": "default/wf-parent", "attemptId": "attempt"}
    if claimed:
        parent._trusted_issue_context = {"issueClaimLease": lease}
    renewed = asyncio.Event()
    renewals = []

    async def renew(name, payload, **kwargs):
        assert name == "github_issue.renew_claim" and payload == lease
        assert kwargs["schedule_to_close_timeout"] <= timedelta(seconds=30)
        renewals.append(payload)
        renewed.set()
        return {
            "status": "renewed",
            **lease,
            "leaseExpiresAt": (
                module.workflow.now() + timedelta(minutes=30)
            ).isoformat(),
        }

    async def sleep(_seconds):
        await asyncio.sleep(0)

    async def merge(*_args, **_kwargs):
        if claimed and patched:
            renewed.clear()
            await asyncio.wait_for(renewed.wait(), timeout=1)
        return {"status": "merged"}

    monkeypatch.setattr(module.workflow, "execute_activity", renew)
    monkeypatch.setattr(module.workflow, "sleep", sleep)
    monkeypatch.setattr(module.workflow, "execute_child_workflow", merge)
    await parent._maybe_start_merge_gate(
        parameters={"publishMode": "pr", "mergeAutomation": {"enabled": True}},
        pull_request_url="https://github.com/example/repo/pull/1",
    )
    assert len(renewals) >= 2 if claimed and patched else not renewals
    assert parent._publish_context["mergeAutomationStatus"] == "merged"
