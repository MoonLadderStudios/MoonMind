"""Review-only gate artifacts agree with the validated terminal outcome."""

import json
from datetime import timedelta

import pytest

from moonmind.workflows.temporal.workflows import merge_automation as module
from tests.unit.workflows.temporal.workflows.test_merge_automation_review_loop import (
    HEAD_1,
    HEAD_2,
    _assert_review_only_has_no_resolver,
    _awaiting_review,
    _posted,
    _review_only_completed,
    _review_only_harness,
    _review_only_payload,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    ["completed", "closed", "stale", "late_readiness_snapshot", "late_terminal_report"],
)
async def test_review_only_latest_gate_snapshot_matches_terminal_outcome(
    monkeypatch, outcome
):
    completion = _review_only_completed(HEAD_1)
    if outcome == "closed":
        completion["pullRequestOpen"] = False
    elif outcome == "stale":
        completion["headSha"] = HEAD_2
    harness = _review_only_harness(
        monkeypatch,
        readiness=[_awaiting_review(HEAD_1), completion],
        request_results=[_posted(HEAD_1)],
    )
    payload = _review_only_payload()
    payload["mergeAutomationConfig"]["timeouts"]["expireAfterSeconds"] = 180
    snapshots = []
    original = module.execute_typed_activity

    async def observe_artifact(name, request, **kwargs):
        body = json.loads(request.payload)
        if "ready" in body:
            snapshots.append(body)
            if (
                outcome == "late_readiness_snapshot"
                and len(harness.readiness_payloads) == 2
                and not body["ready"]
            ) or (outcome == "late_terminal_report" and body["ready"]):
                harness._now += timedelta(seconds=180)
        return await original(name, request, **kwargs)

    monkeypatch.setattr(module, "execute_typed_activity", observe_artifact)
    result = await module.MoonMindMergeAutomationWorkflow().run(payload)

    expected_status = {
        "completed": "review_complete",
        "closed": "blocked",
        "stale": "blocked",
        "late_readiness_snapshot": "expired",
        "late_terminal_report": "review_complete",
    }[outcome]
    assert result["status"] == expected_status
    assert snapshots
    accepted_completion = outcome in {"completed", "late_terminal_report"}
    assert snapshots[-1]["ready"] is accepted_completion
    if accepted_completion:
        assert snapshots[-1]["status"] == "review_complete"
        assert snapshots[-1]["summary"]["status"] == result["status"]
    if outcome in {"closed", "stale"}:
        assert not any(snapshot["ready"] for snapshot in snapshots)
    assert len(harness.request_payloads) == 1
    _assert_review_only_has_no_resolver(harness, result)
