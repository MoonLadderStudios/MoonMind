"""Provider refusal settles its exact durable review cycle before termination."""

from __future__ import annotations

import pytest

from moonmind.workflows.merge_automation_review import build_review_request_key
from moonmind.workflows.temporal.workflows import merge_automation as module
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
)
from tests.unit.workflows.temporal.workflows.test_merge_automation_review_loop import (
    HEAD_1,
    MERGE_AUTOMATION_WORKFLOW_ID,
    _Harness,
    _awaiting_review,
    _payload,
    _posted,
    _ready,
    _request_review_result,
    _review_only_harness,
    _review_only_payload,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_mode", ["merge", "fix_only", "review_only"])
@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize("settlement_enabled", [False, True])
async def test_refusal_settles_only_its_matching_cycle(
    monkeypatch, finish_mode, restored, settlement_enabled
):
    payload = _review_only_payload() if finish_mode == "review_only" else _payload()
    payload["mergeAutomationConfig"]["finishMode"] = finish_mode
    active = {
        **_posted(HEAD_1),
        "requestKey": build_review_request_key(
            parent_workflow_id=MERGE_AUTOMATION_WORKFLOW_ID,
            repository=payload["pullRequest"]["repo"],
            pr_number=350,
            head_sha=HEAD_1,
            provider="codex",
        ),
    }
    previous = {
        "cycle": 1,
        **active,
        "requestKey": "earlier-request",
        "requestCommentId": 10,
        "status": "completed",
        "completionKind": "review",
        "completionId": 11,
        "completedAt": "2026-08-24T22:16:00Z",
    }
    if restored:
        payload["activeReviewRequest"] = active
        payload["reviewCycles"] = [
            previous,
            {"cycle": 2, **active, "status": "requested"},
        ]
    refusal = _awaiting_review(
        HEAD_1,
        automatedReviewComplete=None,
        automatedReviewRequestCommentId=active["requestCommentId"],
        automatedReviewRequestedAt=active["requestedAt"],
        readinessObservationId="refused-observation",
        blockers=[
            {
                "kind": "automated_review_request_failed",
                "summary": "Provider quota refused review.",
                "retryable": False,
                "source": "codex",
            }
        ],
    )
    observations = (
        [refusal]
        if restored
        else [
            (
                _awaiting_review(HEAD_1)
                if finish_mode == "review_only"
                else _ready(HEAD_1)
            ),
            refusal,
        ]
    )
    harness = (
        _review_only_harness(
            monkeypatch, readiness=observations, request_results=[active]
        )
        if finish_mode == "review_only"
        else _Harness(
            monkeypatch,
            readiness=observations,
            child_results=(
                []
                if restored
                else lambda child, attempt: _request_review_result(
                    child_workflow_id=child, head_sha=HEAD_1
                )
            ),
            request_results=[active],
        )
    )
    original_patched = module.workflow.patched
    monkeypatch.setattr(
        module.workflow,
        "patched",
        lambda name: (
            settlement_enabled
            if name.startswith("merge-automation-review-refusal-settlement-v1:")
            else original_patched(name)
        ),
    )
    result = await MoonMindMergeAutomationWorkflow().run(payload)
    assert result["status"] == "blocked"
    assert result["blockers"][0]["kind"] == "automated_review_request_failed"
    cycles = result["reviewLoop"]["cycleRecords"]
    if restored:
        for key, value in previous.items():
            if key in cycles[0]:
                assert cycles[0][key] == value
    assert cycles[-1]["status"] == ("failed" if settlement_enabled else "requested")
    assert cycles[-1]["requestCommentId"] == active["requestCommentId"]
    assert cycles[-1].get("completionId") is None
    assert (result["reviewLoop"]["activeRequest"] is None) is settlement_enabled
    assert len(harness.child_payloads) == int(
        not restored and finish_mode != "review_only"
    )
    assert len(harness.request_payloads) == int(not restored)


@pytest.mark.parametrize(
    "change",
    [
        "missing_observation",
        "wrong_head",
        "missing_selected",
        "missing_time",
        "policy_failure",
    ],
)
def test_unmatched_refusal_does_not_clear_retained_admission(monkeypatch, change):
    from copy import deepcopy
    from moonmind.schemas.temporal_models import MergeAutomationStartInput

    _Harness(monkeypatch, readiness=[], child_results=[])
    gate = MoonMindMergeAutomationWorkflow()
    gate._input = MergeAutomationStartInput.model_validate(_payload())
    active = {
        "provider": "codex",
        "headSha": HEAD_1,
        "requestKey": "key",
        "requestCommentId": 100,
        "requestedAt": "2026-08-24T22:15:00Z",
    }
    gate._active_review_request = deepcopy(active)
    gate._review_cycles = [{"cycle": 1, **active, "status": "requested"}]
    original_cycles = deepcopy(gate._review_cycles)
    evaluation = {
        "headSha": HEAD_1,
        "automatedReviewComplete": None,
        "automatedReviewRequestCommentId": 100,
        "automatedReviewRequestedAt": active["requestedAt"],
        "readinessObservationId": "qualified-refusal",
        "blockers": [
            {
                "kind": "automated_review_request_failed",
                "source": "codex",
                "summary": "Provider refused.",
                "retryable": False,
            }
        ],
    }
    if change == "missing_observation":
        evaluation.pop("readinessObservationId")
    elif change == "wrong_head":
        evaluation["headSha"] = "other-head"
    elif change == "missing_selected":
        evaluation.pop("automatedReviewRequestCommentId")
    elif change == "missing_time":
        active["requestedAt"] = None
        gate._active_review_request["requestedAt"] = None
        gate._review_cycles[0]["requestedAt"] = None
        original_cycles[0]["requestedAt"] = None
        evaluation["automatedReviewRequestedAt"] = None
    else:
        evaluation["blockers"][0]["source"] = "policy"
    gate._settle_active_review_request(evaluation)
    assert gate._active_review_request == active
    assert gate._review_cycles == original_cycles
