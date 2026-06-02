from scripts.create_jira_implement_preset_workflows import (
    build_expand_payload,
    build_payload,
)


def test_build_payload_uses_jira_implement_pr_with_merge_automation() -> None:
    payload = build_payload(
        issue_key="mm-770",
        repository="MoonLadderStudios/MoonMind",
        runtime="codex_cli",
        expanded_steps=[
            {"title": "Load Jira preset brief"},
            {"title": "Finalize Jira status"},
        ],
        applied_template={"slug": "jira-implement", "version": "1.0.0"},
    )

    assert payload["type"] == "task"
    request_payload = payload["payload"]
    assert request_payload["repository"] == "MoonLadderStudios/MoonMind"
    assert request_payload["targetRuntime"] == "codex_cli"
    assert request_payload["publishMode"] == "pr"
    assert request_payload["mergeAutomation"] == {"enabled": True}
    assert request_payload["idempotencyKey"] == (
        "jira-implement:MM-770:pr-merge-automation-expanded-steps"
    )

    task = request_payload["task"]
    assert task["title"] == "Run Jira Implement for MM-770"
    assert "Run Jira Implement for MM-770" in task["instructions"]
    assert task["inputs"]["jira_issue_key"] == "MM-770"
    assert task["steps"] == [
        {"title": "Load Jira preset brief"},
        {"title": "Finalize Jira status"},
    ]
    assert task["publish"] == {
        "mode": "pr",
        "mergeAutomation": {"enabled": True},
    }
    assert task["taskTemplate"] == {
        "slug": "jira-implement",
        "version": "1.0.0",
        "scope": "global",
    }
    assert task["presetSchedule"] == {
        "source": "batch",
        "reason": "jira_issue_batch",
        "presetSlug": "jira-implement",
        "presetVersion": "1.0.0",
        "jiraIssueKey": "MM-770",
    }
    assert task["appliedStepTemplates"] == [
        {"slug": "jira-implement", "version": "1.0.0"}
    ]


def test_build_expand_payload_targets_jira_issue_picker_input() -> None:
    payload = build_expand_payload(
        issue_key="mm-779",
        repository="MoonLadderStudios/MoonMind",
        runtime="codex_cli",
    )

    assert payload == {
        "version": "1.0.0",
        "inputs": {"jira_issue": {"key": "MM-779"}, "constraints": ""},
        "context": {
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex_cli",
        },
        "options": {"enforceStepLimit": True},
    }
