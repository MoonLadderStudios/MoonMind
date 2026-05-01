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

def test_merge_automation_request_infers_jira_orchestrate_issue_key() -> None:
    workflow = MoonMindRunWorkflow()

    request = workflow._merge_automation_request(
        {
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "instructions": (
                "Change Jira issue THOR-336 to status In Progress before "
                "implementation starts."
            ),
            "task": {
                "tool": {"type": "skill", "name": "jira-orchestrate"},
                "skill": {"name": "jira-orchestrate", "version": "1.0"},
                "instructions": (
                    "Use the trusted Jira issue updater workflow for THOR-336."
                ),
                "steps": [
                    {
                        "title": "Move Jira issue to Code Review",
                        "instructions": "Preserve Jira issue THOR-336 in the PR.",
                    }
                ],
            },
        }
    )

    assert request is not None
    assert request["jiraIssueKey"] == "THOR-336"
    assert request["postMergeJira"]["enabled"] is True
    assert request["postMergeJira"]["required"] is True

def test_merge_automation_request_does_not_guess_ambiguous_jira_issue_key() -> None:
    workflow = MoonMindRunWorkflow()

    request = workflow._merge_automation_request(
        {
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "task": {
                "skill": {"name": "jira-orchestrate"},
                "instructions": "Coordinate THOR-336 and THOR-337.",
            },
        }
    )

    assert request is not None
    assert request["jiraIssueKey"] is None
    assert "enabled" not in request["postMergeJira"]

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

def test_build_merge_gate_start_payload_carries_inferred_jira_orchestrate_key() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/Tactics"
    workflow._publish_context["branch"] = "159-grid-overlay-controller"
    workflow._publish_context["baseRef"] = "main"

    payload = workflow._build_merge_gate_start_payload(
        parameters={
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "instructions": (
                "Change Jira issue THOR-336 to status In Progress before "
                "implementation starts."
            ),
            "task": {
                "skill": {"name": "jira-orchestrate", "version": "1.0"},
                "tool": {"type": "skill", "name": "jira-orchestrate"},
                "instructions": (
                    "Use the trusted Jira issue updater workflow for THOR-336."
                ),
                "publish": {"mode": "pr"},
            },
        },
        pull_request_url="https://github.com/MoonLadderStudios/Tactics/pull/1685",
        head_sha="c1092740a1f12857d820534963ae99b40e0e307c",
        parent_workflow_id="mm:23c4e893-7765-469d-bcb7-7f66331e1ad4",
        parent_run_id="run-1",
    )

    assert payload is not None
    assert payload["jiraIssueKey"] == "THOR-336"
    assert payload["mergeAutomationConfig"]["gate"]["jira"]["issueKey"] == "THOR-336"
    assert payload["mergeAutomationConfig"]["postMergeJira"]["enabled"] is True

def test_build_merge_gate_start_payload_preserves_parent_runtime_profile() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"

    payload = workflow._build_merge_gate_start_payload(
        parameters={
            "publishMode": "pr",
            "targetRuntime": "codex_cli",
            "profileId": "codex_default",
            "model": "gpt-5.4",
            "effort": "high",
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
        head_sha="abc123",
        parent_workflow_id="mm:parent",
        parent_run_id="run-1",
    )

    assert payload is not None
    resolver_template = payload["resolverTemplate"]
    assert resolver_template["targetRuntime"] == "codex_cli"
    assert resolver_template["executionProfileRef"] == "codex_default"
    assert resolver_template["model"] == "gpt-5.4"
    assert resolver_template["effort"] == "high"
    assert "profileId" not in resolver_template
    assert "providerProfile" not in resolver_template

def test_build_merge_gate_start_payload_inherits_task_runtime_profile() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"

    payload = workflow._build_merge_gate_start_payload(
        parameters={
            "publishMode": "pr",
            "task": {
                "runtime": {
                    "mode": "codex",
                    "executionProfileRef": "codex_default",
                },
                "publish": {
                    "mergeAutomation": {
                        "enabled": True,
                        "mergeMethod": "squash",
                    }
                },
            },
        },
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/341",
        head_sha="abc123",
        parent_workflow_id="mm:parent",
        parent_run_id="run-1",
    )

    assert payload is not None
    resolver_template = payload["resolverTemplate"]
    assert resolver_template["targetRuntime"] == "codex"
    assert resolver_template["executionProfileRef"] == "codex_default"

def test_build_merge_gate_start_payload_normalizes_timeout_values() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"

    payload = workflow._build_merge_gate_start_payload(
        parameters={
            "publishMode": "pr",
            "task": {
                "publish": {
                    "mergeAutomation": {
                        "enabled": True,
                        "fallbackPollSeconds": "not-a-number",
                        "timeouts": {"expireAfterSeconds": "900"},
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
    assert payload["mergeAutomationConfig"]["timeouts"] == {
        "fallbackPollSeconds": 120,
        "expireAfterSeconds": 900,
    }

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
