import json
import sys

from scripts import create_issue_implement_preset_workflows as module
from scripts.create_issue_implement_preset_workflows import (
    build_expand_payload,
    build_payload,
    post_json,
)


def test_build_payload_uses_jira_implement_pr_with_merge_automation() -> None:
    payload = build_payload(
        provider="jira",
        issue_ref="MM-770",
        repository="MoonLadderStudios/MoonMind",
        runtime="codex_cli",
        expanded_steps=[
            {"title": "Load Jira preset brief"},
            {"title": "Finalize Jira status"},
        ],
        applied_template={"slug": "jira-implement", "version": "1.1.0"},
        preset_version="1.1.0",
    )

    assert payload["type"] == "task"
    request_payload = payload["payload"]
    assert request_payload["repository"] == "MoonLadderStudios/MoonMind"
    assert request_payload["targetRuntime"] == "codex_cli"
    assert request_payload["publishMode"] == "pr"
    assert request_payload["mergeAutomation"] == {"enabled": True}
    assert request_payload["idempotencyKey"] == (
        "jira-implement:MM-770:MoonLadderStudios/MoonMind:codex_cli:1.1.0:"
        "pr-merge-automation-expanded-steps"
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
        "version": "1.1.0",
        "scope": "global",
    }
    assert task["presetSchedule"] == {
        "source": "batch",
        "reason": "jira_issue_batch",
        "presetSlug": "jira-implement",
        "presetVersion": "1.1.0",
        "issueProvider": "jira",
        "issueRef": "MM-770",
        "jiraIssueKey": "MM-770",
    }
    assert task["appliedStepTemplates"] == [
        {"slug": "jira-implement", "version": "1.1.0"}
    ]


def test_build_payload_idempotency_key_includes_run_shaping_inputs() -> None:
    base = build_payload(
        provider="jira",
        issue_ref="MM-770",
        repository="MoonLadderStudios/MoonMind",
        runtime="codex_cli",
        preset_version="1.1.0",
        expanded_steps=[],
    )["payload"]["idempotencyKey"]
    changed_repository = build_payload(
        provider="jira",
        issue_ref="MM-770",
        repository="MoonLadderStudios/Other",
        runtime="codex_cli",
        preset_version="1.1.0",
        expanded_steps=[],
    )["payload"]["idempotencyKey"]
    changed_runtime = build_payload(
        provider="jira",
        issue_ref="MM-770",
        repository="MoonLadderStudios/MoonMind",
        runtime="claude_code",
        preset_version="1.1.0",
        expanded_steps=[],
    )["payload"]["idempotencyKey"]
    changed_version = build_payload(
        provider="jira",
        issue_ref="MM-770",
        repository="MoonLadderStudios/MoonMind",
        runtime="codex_cli",
        preset_version="2.0.0",
        expanded_steps=[],
    )["payload"]["idempotencyKey"]

    assert base != changed_repository
    assert base != changed_runtime
    assert base != changed_version


def test_build_expand_payload_targets_jira_issue_picker_input() -> None:
    payload = build_expand_payload(
        provider="jira",
        issue="mm-779",
        repository="MoonLadderStudios/MoonMind",
        runtime="codex_cli",
        preset_version="1.1.0",
    )

    assert payload == {
        "version": "1.1.0",
        "inputs": {"jira_issue": {"key": "MM-779"}, "constraints": ""},
        "context": {
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex_cli",
        },
        "options": {"enforceStepLimit": True},
    }


def test_post_json_returns_failure_for_urlopen_exceptions(monkeypatch) -> None:
    def raise_timeout(*args, **kwargs):
        raise TimeoutError("request timed out")

    monkeypatch.setattr(module.request, "urlopen", raise_timeout)

    assert post_json(
        base_url="http://moonmind.test",
        payload={"type": "task"},
        timeout=1.0,
    ) == {"status": 0, "error": "request timed out"}


def test_main_continues_after_expand_failure(monkeypatch, capsys) -> None:
    expanded = {
        "steps": [{"title": f"Step {index}"} for index in range(8)],
        "appliedTemplate": {"slug": "jira-implement", "version": "1.1.0"},
    }

    def fake_expand_issue_implement(**kwargs):
        if kwargs["issue"].upper() == "MM-770":
            raise TimeoutError("expand unavailable")
        return expanded

    def fake_post_json(**kwargs):
        return {
            "status": 201,
            "body": {
                "workflowId": "workflow-mm-771",
                "runId": "run-mm-771",
                "title": "Run Jira Implement for MM-771",
                "state": "queued",
            },
        }

    monkeypatch.setattr(module, "expand_issue_implement", fake_expand_issue_implement)
    monkeypatch.setattr(module, "post_json", fake_post_json)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "create_issue_implement_preset_workflows.py",
            "MM-770",
            "MM-771",
            "--provider",
            "jira",
            "--base-url",
            "http://moonmind.test",
        ],
    )

    assert module.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["results"][0] == {
        "issue": "MM-770",
        "status": 0,
        "error": "Expansion failed: expand unavailable",
    }
    assert report["results"][1]["issue"] == "MM-771"
    assert report["results"][1]["workflowId"] == "workflow-mm-771"
    assert report["failures"] == [report["results"][0]]
