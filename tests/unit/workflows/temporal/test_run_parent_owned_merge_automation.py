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


@pytest.mark.parametrize("status", ["blocked", "failed", "expired", "canceled", "completed"])
def test_parent_owned_merge_automation_non_success_outcomes(status: str) -> None:
    workflow = MoonMindRunWorkflow()

    assert workflow._merge_automation_child_succeeded({"status": status}) is False


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
