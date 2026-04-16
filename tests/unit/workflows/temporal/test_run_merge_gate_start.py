from __future__ import annotations

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def test_merge_automation_disabled_by_default() -> None:
    workflow = MoonMindRunWorkflow()

    assert workflow._merge_automation_request({"publishMode": "pr"}) is None


def test_merge_automation_request_can_be_enabled_from_task_publish() -> None:
    workflow = MoonMindRunWorkflow()

    request = workflow._merge_automation_request(
        {
            "publishMode": "pr",
            "task": {
                "publish": {
                    "mode": "pr",
                    "mergeAutomation": {
                        "enabled": True,
                        "mergeMethod": "squash",
                        "jiraIssueKey": "MM-341",
                    },
                }
            },
        }
    )

    assert request is not None
    assert request["mergeMethod"] == "squash"
    assert request["jiraIssueKey"] == "MM-341"


def test_build_merge_gate_start_payload_from_published_pr() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"
    workflow._publish_context["branch"] = "feature"
    workflow._publish_context["baseRef"] = "main"

    payload = workflow._build_merge_gate_start_payload(
        parameters={
            "publishMode": "pr",
            "task": {
                "publish": {
                    "mergeAutomation": {
                        "enabled": True,
                        "mergeMethod": "squash",
                        "jiraIssueKey": "MM-341",
                    }
                }
            },
        },
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/341",
        head_sha="abc123",
        parent_workflow_id="mm:parent",
        parent_run_id="run-1",
    )

    assert payload is not None
    assert payload["workflowType"] == "MoonMind.MergeAutomation"
    assert payload["parentWorkflowId"] == "mm:parent"
    assert payload["parentRunId"] == "run-1"
    assert payload["publishContextRef"].startswith("artifact://")
    assert payload["pullRequest"]["number"] == 341
    assert payload["pullRequest"]["headSha"] == "abc123"
    assert payload["jiraIssueKey"] == "MM-341"
    assert payload["mergeAutomationConfig"]["resolver"]["mergeMethod"] == "squash"
    assert payload["resolverTemplate"]["repository"] == "MoonLadderStudios/MoonMind"


def test_build_merge_gate_start_payload_requires_real_head_sha() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"

    payload = workflow._build_merge_gate_start_payload(
        parameters={
            "publishMode": "pr",
            "task": {
                "publish": {
                    "mergeAutomation": {
                        "enabled": True,
                        "mergeMethod": "squash",
                    }
                }
            },
        },
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/341",
        head_sha=None,
        parent_workflow_id="mm:parent",
        parent_run_id="run-1",
    )

    assert payload is None
