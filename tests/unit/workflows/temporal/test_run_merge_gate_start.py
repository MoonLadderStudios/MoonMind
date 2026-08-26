from __future__ import annotations

from moonmind.workflows.temporal.workflows.run import (
    MoonMindRunWorkflow,
    _worker_capability_unavailable_error,
)


def test_worker_capability_error_preserves_structured_details() -> None:
    capability = {
        "available": False,
        "reasonCode": "worker_capability_unavailable",
    }

    error = _worker_capability_unavailable_error(capability)

    assert error.type == "WORKER_CAPABILITY_UNAVAILABLE"
    assert error.non_retryable is True
    assert error.details == (capability,)

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


def test_merge_automation_request_infers_github_issue_completion() -> None:
    workflow = MoonMindRunWorkflow()

    request = workflow._merge_automation_request(
        {
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "workflow": {
                "inputs": {
                    "github_issue": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "number": 3143,
                    }
                }
            },
        }
    )

    assert request is not None
    assert request["postMergeGithub"] == {
        "enabled": True,
        "required": True,
        "repository": "MoonLadderStudios/MoonMind",
        "issueNumber": 3143,
    }


def test_merge_gate_start_payload_carries_post_merge_github_completion() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"
    workflow._publish_context["branch"] = "implement-3143"
    workflow._publish_context["baseRef"] = "main"

    payload = workflow._build_merge_gate_start_payload(
        parameters={
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "workflow": {
                "inputs": {
                    "github_issue": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "number": 3143,
                    }
                }
            },
        },
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/3225",
        head_sha="abc123",
        parent_workflow_id="mm:github-issue",
        parent_run_id="run-1",
    )

    assert payload is not None
    assert payload["mergeAutomationConfig"]["postMergeGithub"] == {
        "enabled": True,
        "required": True,
        "repository": "MoonLadderStudios/MoonMind",
        "issueNumber": 3143,
    }

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
                "skill": {"name": "jira-orchestrate"},
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

def test_merge_automation_request_infers_issue_key_from_workflow_keyed_task() -> None:
    # Regression for MM-823: canonical runtime parameters carry the task payload
    # under "workflow" (not "task") with merge automation enabled at top level and
    # no explicit jiraIssueKey. The issue key must still be derived from the
    # jira-implement applied template / step text so post-merge Jira completion is
    # enabled; otherwise the issue is left stuck in Code Review after merge.
    workflow = MoonMindRunWorkflow()

    request = workflow._merge_automation_request(
        {
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "instructions": (
                "Fetch Jira issue MM-823 through the deterministic trusted Jira "
                "tool surface."
            ),
            "workflow": {
                "title": (
                    "Fetch Jira issue MM-823 through the deterministic trusted "
                    "Jira tool surface."
                ),
                "publish": {"mode": "pr"},
                "appliedStepTemplates": [
                    {
                        "slug": "jira-implement",
                        "version": "1.0.0",
                        "inputs": {"jira_issue_key": "MM-823"},
                    }
                ],
                "steps": [
                    {
                        "title": "Finalize Jira status",
                        "instructions": "Transition Jira issue MM-823 to Code Review.",
                    }
                ],
            },
        }
    )

    assert request is not None
    assert request["jiraIssueKey"] == "MM-823"
    assert request["postMergeJira"]["enabled"] is True
    assert request["postMergeJira"]["required"] is True


def test_merge_automation_request_falls_back_when_workflow_payload_empty() -> None:
    # Mixed in-flight/legacy payloads can carry an empty canonical workflow
    # object while the Jira-backed task body remains under "task".
    workflow = MoonMindRunWorkflow()

    request = workflow._merge_automation_request(
        {
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "workflow": {},
            "task": {
                "publish": {"mode": "pr"},
                "appliedStepTemplates": [
                    {
                        "slug": "jira-implement",
                        "version": "1.0.0",
                        "inputs": {"jira_issue_key": "MM-823"},
                    }
                ],
                "steps": [
                    {
                        "title": "Finalize Jira status",
                        "instructions": "Transition Jira issue MM-823 to Code Review.",
                    }
                ],
            },
        }
    )

    assert request is not None
    assert request["jiraIssueKey"] == "MM-823"
    assert request["postMergeJira"]["enabled"] is True
    assert request["postMergeJira"]["required"] is True


def test_build_merge_gate_start_payload_carries_workflow_keyed_jira_key() -> None:
    # Regression for MM-823 at the merge-gate child boundary: the issue key and an
    # enabled post-merge Jira config must propagate into the merge automation child
    # payload when the task is supplied under the canonical "workflow" key.
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"
    workflow._publish_context["branch"] = "fetch-jira-issue-mm-823"
    workflow._publish_context["baseRef"] = "main"

    payload = workflow._build_merge_gate_start_payload(
        parameters={
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "instructions": (
                "Fetch Jira issue MM-823 through the deterministic trusted Jira "
                "tool surface."
            ),
            "workflow": {
                "title": "Fetch Jira issue MM-823.",
                "publish": {"mode": "pr"},
                "appliedStepTemplates": [
                    {
                        "slug": "jira-implement",
                        "version": "1.0.0",
                        "inputs": {"jira_issue_key": "MM-823"},
                    }
                ],
                "steps": [
                    {
                        "title": "Finalize Jira status",
                        "instructions": "Transition Jira issue MM-823 to Code Review.",
                    }
                ],
            },
        },
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/2501",
        head_sha="f818b9df3e325cd0ab18814e183240b95a42ea3f",
        parent_workflow_id="mm:2593ddff",
        parent_run_id="run-1",
    )

    assert payload is not None
    assert payload["jiraIssueKey"] == "MM-823"
    assert payload["mergeAutomationConfig"]["gate"]["jira"]["issueKey"] == "MM-823"
    assert payload["mergeAutomationConfig"]["postMergeJira"]["enabled"] is True


def test_merge_automation_uses_structured_jira_issue_over_source_traceability() -> None:
    workflow = MoonMindRunWorkflow()

    request = workflow._merge_automation_request(
        {
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "workflow": {
                "title": (
                    "Run Jira Implement for MM-904: Define authoring conventions"
                ),
                "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}},
                "inputs": {
                    "jira_issue": {
                        "key": "MM-904",
                        "summary": "Define authoring conventions",
                        "url": "https://moonladder.atlassian.net/browse/MM-904",
                    },
                    "constraints": "Preserve source issue MM-900 traceability.",
                },
                "taskTemplate": {
                    "slug": "jira-implement",
                    "version": "1.1.0",
                    "scope": "global",
                },
                "appliedStepTemplates": [
                    {
                        "slug": "jira-implement",
                        "version": "1.1.0",
                        "inputs": {
                            "jira_issue_key": "MM-904",
                            "jira_issue": {
                                "key": "MM-904",
                                "summary": "Define authoring conventions",
                                "url": "https://moonladder.atlassian.net/browse/MM-904",
                            },
                            "constraints": "Preserve source issue MM-900 traceability.",
                        },
                    }
                ],
                "steps": [
                    {
                        "title": "Finalize Jira status",
                        "instructions": (
                            "Transition Jira issue MM-904 to Code Review. "
                            "Preserve source issue MM-900 traceability."
                        ),
                    }
                ],
            },
        }
    )

    assert request is not None
    assert request["jiraIssueKey"] == "MM-904"
    assert request["postMergeJira"]["enabled"] is True
    assert request["postMergeJira"]["required"] is True


def test_build_merge_gate_start_payload_carries_structured_jira_issue_key() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._repo = "MoonLadderStudios/MoonMind"
    workflow._publish_context["branch"] = (
        "run-jira-implement-for-mm-904-define-authoring"
    )
    workflow._publish_context["baseRef"] = "main"

    payload = workflow._build_merge_gate_start_payload(
        parameters={
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "workflow": {
                "title": "Run Jira Implement for MM-904.",
                "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}},
                "inputs": {
                    "jira_issue": {
                        "key": "MM-904",
                        "summary": "Define authoring conventions",
                    },
                    "constraints": "Preserve source issue MM-900 traceability.",
                },
                "appliedStepTemplates": [
                    {
                        "slug": "jira-implement",
                        "version": "1.1.0",
                        "inputs": {
                            "jira_issue_key": "MM-904",
                            "constraints": "Preserve source issue MM-900 traceability.",
                        },
                    }
                ],
                "steps": [
                    {
                        "title": "Finalize Jira status",
                        "instructions": (
                            "Transition Jira issue MM-904 to Code Review. "
                            "Preserve source issue MM-900 traceability."
                        ),
                    }
                ],
            },
        },
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/2653",
        head_sha="5d8f11673fac6c346ae0b0a378748ce631435201",
        parent_workflow_id="mm:f4edab93",
        parent_run_id="run-1",
    )

    assert payload is not None
    assert payload["jiraIssueKey"] == "MM-904"
    assert payload["mergeAutomationConfig"]["gate"]["jira"]["issueKey"] == "MM-904"
    assert payload["mergeAutomationConfig"]["postMergeJira"]["enabled"] is True


def test_merge_automation_uses_snake_case_applied_template_jira_key() -> None:
    workflow = MoonMindRunWorkflow()

    request = workflow._merge_automation_request(
        {
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
            "workflow": {
                "title": "Run Jira Implement for MM-904.",
                "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}},
                "instructions": (
                    "Run Jira Implement for MM-904. Preserve source issue "
                    "MM-900 traceability."
                ),
                "applied_step_templates": [
                    {
                        "slug": "jira-implement",
                        "version": "1.1.0",
                        "input_mapping": {
                            "jira_issue_key": "MM-904",
                            "constraints": "Preserve source issue MM-900 traceability.",
                        },
                    }
                ],
            },
        }
    )

    assert request is not None
    assert request["jiraIssueKey"] == "MM-904"
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
                "skill": {"name": "jira-orchestrate"},
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
