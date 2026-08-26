from __future__ import annotations

import pytest

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

def _enabled_parameters() -> dict[str, object]:
    return {
        "publishMode": "pr",
        "task": {
            "publish": {
                "mergeAutomation": {
                    "enabled": True,
                    "mergeMethod": "squash",
                    "jiraIssueKey": "MM-350",
                }
            }
        },
    }

def test_parent_owned_merge_automation_payload_uses_required_workflow_type() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"
    workflow._publish_context["branch"] = "feature"
    workflow._publish_context["baseRef"] = "main"

    payload = workflow._build_merge_gate_start_payload(
        parameters=_enabled_parameters(),
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/350",
        head_sha="abc123",
        parent_workflow_id="mm:parent",
        parent_run_id="run-1",
    )

    assert payload is not None
    assert payload["workflowType"] == "MoonMind.MergeAutomation"
    assert payload["idempotencyKey"].startswith("merge-automation:")
    assert payload["pullRequest"]["number"] == 350
    assert payload["pullRequest"]["headSha"] == "abc123"
    assert payload["jiraIssueKey"] == "MM-350"

def test_parent_owned_merge_automation_requires_real_head_sha() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"

    payload = workflow._build_merge_gate_start_payload(
        parameters=_enabled_parameters(),
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/350",
        head_sha=None,
        parent_workflow_id="mm:parent",
        parent_run_id="run-1",
    )

    assert payload is None

@pytest.mark.parametrize("status", ["merged", "already_merged"])
def test_parent_owned_merge_automation_success_outcomes(status: str) -> None:
    workflow = MoonMindRunWorkflow()

    assert workflow._merge_automation_child_succeeded({"status": status}) is True

@pytest.mark.parametrize("status", ["blocked", "failed", "expired", "completed"])
def test_parent_owned_merge_automation_non_success_outcomes(status: str) -> None:
    workflow = MoonMindRunWorkflow()

    assert workflow._merge_automation_child_succeeded({"status": status}) is False

def test_parent_owned_merge_automation_canceled_outcome_is_cancellation() -> None:
    workflow = MoonMindRunWorkflow()

    assert workflow._merge_automation_child_canceled({"status": "canceled"}) is True
    assert workflow._merge_automation_child_succeeded({"status": "canceled"}) is False

@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({}, "merge automation failed: missing terminal status"),
        (
            {"status": "completed"},
            "merge automation failed: unsupported terminal status completed",
        ),
    ],
)
def test_parent_owned_merge_automation_unknown_status_reason_is_deterministic(
    result: dict[str, object],
    expected: str,
) -> None:
    workflow = MoonMindRunWorkflow()

    assert workflow._merge_automation_failure_reason(result) == expected

def test_parent_owned_merge_automation_failure_reason_includes_blocker() -> None:
    workflow = MoonMindRunWorkflow()

    reason = workflow._merge_automation_failure_reason(
        {
            "status": "blocked",
            "blockers": [
                {
                    "kind": "checks_failed",
                    "summary": "Required checks are failing.",
                }
            ],
        }
    )

    assert "blocked" in reason
    assert "Required checks are failing." in reason

def test_run_records_merge_automation_disposition_from_step_outputs() -> None:
    workflow = MoonMindRunWorkflow()

    workflow._record_execution_context(
        node_id="resolver",
        execution_result={
            "outputs": {
                "mergeAutomationDisposition": "already_merged",
                "headSha": "abc123",
            }
        },
    )

    assert workflow._merge_automation_disposition == "already_merged"
    assert workflow._merge_automation_head_sha == "abc123"

def test_run_records_pushed_head_sha_for_parent_owned_merge_automation() -> None:
    workflow = MoonMindRunWorkflow()

    workflow._record_execution_context(
        node_id="publish",
        execution_result={
            "outputs": {
                "push_status": "pushed",
                "push_head_sha": "pushed-head-sha",
            }
        },
    )

    assert workflow._publish_context["headSha"] == "pushed-head-sha"

def test_parent_run_summary_projects_merge_automation_visibility() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._publish_context = {
        "pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/354",
        "headSha": "abc123",
        "mergeAutomationWorkflowId": "merge-automation:wf-parent",
        "mergeAutomationStatus": "blocked",
        "mergeAutomationResult": {
            "status": "blocked",
            "prNumber": 354,
            "prUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/354",
            "latestHeadSha": "abc123",
            "cycles": 2,
            "resolverChildWorkflowIds": ["resolver-1", "resolver-2"],
            "blockers": [{"kind": "checks_failed", "summary": "Checks failed."}],
            "artifactRefs": {
                "summary": "summary-artifact",
                "gateSnapshots": ["gate-artifact"],
                "resolverAttempts": ["resolver-artifact"],
            },
        },
    }

    summary = workflow._merge_automation_summary_from_context()

    assert summary == {
        "enabled": True,
        "status": "blocked",
        "prNumber": 354,
        "prUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/354",
        "latestHeadSha": "abc123",
        "childWorkflowId": "merge-automation:wf-parent",
        "resolverChildWorkflowIds": ["resolver-1", "resolver-2"],
        "cycles": 2,
        "blockers": [{"kind": "checks_failed", "summary": "Checks failed."}],
        "artifactRefs": {
            "summary": "summary-artifact",
            "gateSnapshots": ["gate-artifact"],
            "resolverAttempts": ["resolver-artifact"],
        },
    }

def _fix_only_parameters() -> dict[str, object]:
    return {
        "publishMode": "none",
        "task": {
            "publish": {
                "mergeAutomation": {
                    "enabled": True,
                    "mergeMethod": "squash",
                    "finishMode": "fix_only",
                }
            }
        },
    }

def test_merge_automation_request_defaults_to_the_merge_finish_mode() -> None:
    workflow = MoonMindRunWorkflow()

    request = workflow._merge_automation_request(_enabled_parameters())

    assert request is not None
    assert request["finishMode"] == "merge"

def test_merge_automation_request_carries_the_fix_only_finish_mode() -> None:
    workflow = MoonMindRunWorkflow()

    request = workflow._merge_automation_request(_fix_only_parameters())

    assert request is not None
    assert request["finishMode"] == "fix_only"

def test_merge_gate_start_payload_carries_the_finish_mode() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"
    workflow._publish_context["branch"] = "feature"
    workflow._publish_context["baseRef"] = "main"

    payload = workflow._build_merge_gate_start_payload(
        parameters=_fix_only_parameters(),
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/350",
        head_sha="abc123",
        parent_workflow_id="mm:parent",
        parent_run_id="run-1",
    )

    assert payload is not None
    config = payload["mergeAutomationConfig"]
    assert config["finishMode"] == "fix_only"
    # The merge method still travels so an enabled finish pass is unambiguous.
    assert config["resolver"]["mergeMethod"] == "squash"

def test_merge_gate_start_payload_defaults_the_finish_mode_to_merge() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"

    payload = workflow._build_merge_gate_start_payload(
        parameters=_enabled_parameters(),
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/350",
        head_sha="abc123",
        parent_workflow_id="mm:parent",
        parent_run_id="run-1",
    )

    assert payload is not None
    assert payload["mergeAutomationConfig"]["finishMode"] == "merge"

def test_review_clean_is_a_merge_automation_child_success() -> None:
    workflow = MoonMindRunWorkflow()

    assert workflow._merge_automation_child_succeeded({"status": "review_clean"}) is True
    assert workflow._merge_automation_child_status_valid({"status": "review_clean"}) is True

def test_review_clean_is_not_reported_as_a_merge() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._publish_context["mergeAutomationStatus"] = "review_clean"

    assert workflow._merge_happened() is False

def test_fix_only_run_is_not_failed_for_an_unmerged_pull_request() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._publish_context["mergeAutomationStatus"] = "review_clean"
    parameters = _fix_only_parameters()

    assert workflow._merge_required(parameters) is False
    assert (
        workflow._missing_required_outcome_reason(
            parameters=parameters,
            publish_mode="none",
        )
        is None
    )

def test_merge_finish_mode_still_requires_a_merged_pull_request() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._publish_context["mergeAutomationStatus"] = "review_clean"
    parameters = _enabled_parameters()

    assert workflow._merge_required(parameters) is True
    assert (
        workflow._missing_required_outcome_reason(
            parameters=parameters,
            publish_mode="none",
        )
        == "merge automation requested but PR was not merged"
    )
