"""Regression coverage for ungated merge-automation continuation dispositions.

Incident background: a top-level ``MoonMind.UserWorkflow`` running ``pr-resolver``
finished with ``mergeAutomationDisposition = "reenter_gate"`` and was reported as
``status: success`` even though the pull request was never merged. ``reenter_gate``
is a *continuation* disposition that only has meaning inside a
``MoonMind.MergeAutomation`` gate that re-enters and finalizes the merge. When the
resolver runs standalone (no owning gate), that continuation can never be re-entered,
so the run must not be treated as a successful PR resolution.
"""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.workflows.merge_gate import (
    build_resolver_run_request,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def _ungated_resolver_parameters() -> dict[str, object]:
    """Parameters matching the standalone pr-resolver run from the incident.

    These mirror the real ``initial_parameters`` shape: a ``workflow`` block selecting
    the ``pr-resolver`` skill with ``publishMode=none`` and *no* ``mergeGate`` block.
    """

    return {
        "requestType": "task",
        "repository": "MoonLadderStudios/Tactics",
        "publishMode": "none",
        "targetRuntime": "claude_code",
        "workflow": {
            "instructions": "Resolve PR #1863 on branch story-006.",
            "tool": {"type": "skill", "name": "pr-resolver", "version": "1.0"},
            "skill": {"name": "pr-resolver", "version": "1.0"},
            "inputs": {"repo": "MoonLadderStudios/Tactics", "pr": "1863"},
            "publish": {"mode": "none"},
        },
    }


def _gated_resolver_parameters() -> dict[str, object]:
    """Parameters produced by MoonMind.MergeAutomation when it launches the resolver."""

    request = build_resolver_run_request(
        parent_workflow_id="mm:parent-merge-automation",
        pull_request={
            "repo": "MoonLadderStudios/Tactics",
            "number": 1863,
            "url": "https://github.com/MoonLadderStudios/Tactics/pull/1863",
            "headBranch": "story-006",
            "baseBranch": "main",
            "headSha": "e7a62914",
        },
        jira_issue_key=None,
        merge_method="squash",
    )
    return request["initial_parameters"]


def test_gated_parameters_are_recognized_as_merge_automation_owned() -> None:
    workflow = MoonMindRunWorkflow()
    assert workflow._is_merge_automation_gated(_gated_resolver_parameters()) is True


def test_standalone_resolver_parameters_are_not_gated() -> None:
    workflow = MoonMindRunWorkflow()
    assert workflow._is_merge_automation_gated(_ungated_resolver_parameters()) is False


def test_empty_merge_gate_without_parent_is_not_gated() -> None:
    workflow = MoonMindRunWorkflow()
    params = _ungated_resolver_parameters()
    params["mergeGate"] = {"pullRequestUrl": "https://example/pr/1"}
    assert workflow._is_merge_automation_gated(params) is False


def test_ungated_reenter_gate_disposition_blocks_success() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._merge_automation_disposition = "reenter_gate"

    message = workflow._continuation_disposition_failure_message(
        _ungated_resolver_parameters()
    )

    assert message is not None
    assert "reenter_gate" in message
    assert "MergeAutomation" in message


def test_gated_reenter_gate_disposition_is_allowed() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._merge_automation_disposition = "reenter_gate"

    assert (
        workflow._continuation_disposition_failure_message(
            _gated_resolver_parameters()
        )
        is None
    )


@pytest.mark.parametrize("disposition", ["merged", "already_merged"])
def test_terminal_dispositions_are_not_continuation_failures(disposition: str) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._merge_automation_disposition = disposition

    assert (
        workflow._continuation_disposition_failure_message(
            _ungated_resolver_parameters()
        )
        is None
    )


@pytest.mark.parametrize("disposition", ["", None, "   ", "manual_review", "totally_new_state"])
def test_blank_or_unknown_dispositions_do_not_block(disposition) -> None:
    """Degraded/unknown provider dispositions must not trip the continuation guard."""

    workflow = MoonMindRunWorkflow()
    workflow._merge_automation_disposition = disposition

    assert (
        workflow._continuation_disposition_failure_message(
            _ungated_resolver_parameters()
        )
        is None
    )
