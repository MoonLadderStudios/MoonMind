"""Boundary coverage for the non-publishing review-loop handoff into the gate.

``pr-review-resolve`` runs against a pull request that already exists, so the
workflow itself publishes nothing (``publishMode: none``). The durable
``MoonMind.MergeAutomation`` gate still has to start, bound to the exact head
SHA the trusted resolution tool reported. That handoff crosses three seams:

1. the tool step's outputs become publish context,
2. publish context supplies the pull request URL a non-publishing run never
   produces itself, and
3. the gate start payload carries the review-loop policy through unchanged.

These tests exercise the real ``MoonMindRunWorkflow`` methods on that path.
"""

from __future__ import annotations

from typing import Any

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

_PR_URL = "https://github.com/MoonLadderStudios/MoonMind/pull/3771"
_HEAD_SHA = "439c124db3d9c5ad60170b0bf5cfcb183e753a7e"


def _review_loop_parameters() -> dict[str, Any]:
    """Return the parameter shape the pr-review-resolve preset submits."""

    return {
        "publishMode": "none",
        "workflow": {
            "instructions": "Resolve the target pull request.",
            "publish": {
                "mode": "none",
                "mergeAutomation": {
                    "enabled": True,
                    "checks": "required",
                    "automatedReview": "required",
                    "mergeMethod": "squash",
                    "finishMode": "fix_only",
                    "reviewLoop": {
                        "enabled": True,
                        "provider": "codex",
                        "requestMode": "pr_comment",
                        "requireFreshReviewForEveryHead": True,
                        "requestAfterRemediation": True,
                        "maxCycles": 5,
                        "maxConsecutiveNoProgressCycles": 2,
                    },
                    "timeouts": {
                        "fallbackPollSeconds": 60,
                        "expireAfterSeconds": 86400,
                    },
                },
            },
        },
    }


def _resolution_tool_result() -> dict[str, Any]:
    """Return what ``github.resolve_pull_request_target`` emits on success."""

    return {
        "status": "COMPLETED",
        "outputs": {
            "repository": "MoonLadderStudios/MoonMind",
            "prNumber": 3771,
            "pull_request_url": _PR_URL,
            "pullRequestUrl": _PR_URL,
            "head_sha": _HEAD_SHA,
            "headSha": _HEAD_SHA,
            "branch": "feature-branch",
            "push_base_ref": "main",
            "prState": "open",
            "isDraft": False,
        },
    }


def test_resolution_tool_outputs_become_publish_context() -> None:
    """A non-publishing run gets its PR identity from the trusted tool step."""

    workflow = MoonMindRunWorkflow()

    workflow._record_execution_context(
        node_id="node-1",
        execution_result=_resolution_tool_result(),
    )

    assert workflow._publish_context["pullRequestUrl"] == _PR_URL
    assert workflow._publish_context["headSha"] == _HEAD_SHA


def test_review_loop_gate_starts_for_a_run_that_publishes_nothing() -> None:
    """Publish mode ``none`` must still produce a head-bound gate payload."""

    workflow = MoonMindRunWorkflow()
    parameters = _review_loop_parameters()
    workflow._record_execution_context(
        node_id="node-1",
        execution_result=_resolution_tool_result(),
    )

    payload = workflow._build_merge_gate_start_payload(
        parameters=parameters,
        pull_request_url=workflow._publish_context["pullRequestUrl"],
        head_sha=workflow._publish_context.get("headSha"),
        parent_workflow_id="mm:review-loop",
        parent_run_id="run-1",
    )

    assert payload is not None
    pull_request = payload["pullRequest"]
    assert pull_request["repo"] == "MoonLadderStudios/MoonMind"
    assert pull_request["number"] == 3771
    assert pull_request["headSha"] == _HEAD_SHA


def test_review_loop_policy_survives_the_gate_start_payload() -> None:
    """The operator-authored review-loop policy reaches the gate unchanged."""

    workflow = MoonMindRunWorkflow()
    parameters = _review_loop_parameters()
    workflow._record_execution_context(
        node_id="node-1",
        execution_result=_resolution_tool_result(),
    )

    payload = workflow._build_merge_gate_start_payload(
        parameters=parameters,
        pull_request_url=_PR_URL,
        head_sha=_HEAD_SHA,
        parent_workflow_id="mm:review-loop",
        parent_run_id="run-1",
    )

    assert payload is not None
    review_loop = payload["mergeAutomationConfig"]["reviewLoop"]
    assert review_loop["enabled"] is True
    assert review_loop["provider"] == "codex"
    assert review_loop["requireFreshReviewForEveryHead"] is True
    assert review_loop["maxCycles"] == 5
    assert review_loop["maxConsecutiveNoProgressCycles"] == 2
    # The gate identity is head-bound, so a new head starts a new gate.
    assert payload["idempotencyKey"].endswith(f":3771:{_HEAD_SHA}")
    gate_github = payload["mergeAutomationConfig"]["gate"]["github"]
    assert gate_github["automatedReview"] == "required"
    # The preset's default withholds merge authority; the gate must carry that
    # decision instead of quietly re-granting it.
    assert payload["mergeAutomationConfig"]["finishMode"] == "fix_only"


def test_finish_with_pr_resolver_grants_merge_authority_to_the_gate() -> None:
    """Turning the preset control on must reach the gate as finishMode merge."""

    workflow = MoonMindRunWorkflow()
    parameters = _review_loop_parameters()
    parameters["workflow"]["publish"]["mergeAutomation"]["finishMode"] = "merge"

    payload = workflow._build_merge_gate_start_payload(
        parameters=parameters,
        pull_request_url=_PR_URL,
        head_sha=_HEAD_SHA,
        parent_workflow_id="mm:review-loop",
        parent_run_id="run-1",
    )

    assert payload is not None
    assert payload["mergeAutomationConfig"]["finishMode"] == "merge"


def test_gate_payload_is_withheld_without_an_exact_head_sha() -> None:
    """No head SHA means no gate: the loop is head-sensitive from cycle one."""

    workflow = MoonMindRunWorkflow()

    payload = workflow._build_merge_gate_start_payload(
        parameters=_review_loop_parameters(),
        pull_request_url=_PR_URL,
        head_sha=None,
        parent_workflow_id="mm:review-loop",
        parent_run_id="run-1",
    )

    assert payload is None


def test_gate_payload_is_withheld_when_merge_automation_is_off() -> None:
    """Publishing nothing must not by itself start a merge gate."""

    workflow = MoonMindRunWorkflow()
    parameters = _review_loop_parameters()
    parameters["workflow"]["publish"]["mergeAutomation"]["enabled"] = False

    payload = workflow._build_merge_gate_start_payload(
        parameters=parameters,
        pull_request_url=_PR_URL,
        head_sha=_HEAD_SHA,
        parent_workflow_id="mm:review-loop",
        parent_run_id="run-1",
    )

    assert payload is None
