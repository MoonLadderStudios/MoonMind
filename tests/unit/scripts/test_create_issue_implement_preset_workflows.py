import hashlib
import json
import sys

from scripts import create_issue_implement_preset_workflows as module
from scripts.create_issue_implement_preset_workflows import (
    build_expand_payload,
    build_payload,
    build_runtime_block,
    extract_column_issue_keys,
    post_json,
)


def _board_issues_fixture() -> dict:
    """Mimic the GET /api/jira/boards/{id}/issues response shape."""
    return {
        "boardId": "15",
        "columns": [
            {"id": "backlog", "name": "Backlog", "order": 0, "count": 0, "statusIds": []},
            {"id": "to-do", "name": "To Do", "order": 1, "count": 2, "statusIds": ["10013"]},
            {"id": "in-progress", "name": "In Progress", "order": 2, "count": 1, "statusIds": ["3"]},
            {"id": "done", "name": "Done", "order": 3, "count": 1, "statusIds": ["10014"]},
        ],
        "itemsByColumn": {
            "to-do": [
                {"issueKey": "MM-874", "summary": "First", "columnId": "to-do"},
                {"issueKey": "MM-880", "summary": "Second", "columnId": "to-do"},
                {"issueKey": "MM-874", "summary": "Duplicate", "columnId": "to-do"},
            ],
            "in-progress": [{"issueKey": "MM-900", "summary": "Active", "columnId": "in-progress"}],
            "done": [{"issueKey": "MM-100", "summary": "Closed", "columnId": "done"}],
        },
        "unmappedItems": [],
    }


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


def test_build_runtime_block_omits_unset_overrides() -> None:
    assert build_runtime_block(runtime="codex_cli") == {"mode": "codex_cli"}
    assert build_runtime_block(runtime="claude_code", model="claude-opus-4-8", effort="xhigh") == {
        "mode": "claude_code",
        "model": "claude-opus-4-8",
        "effort": "xhigh",
    }


def test_build_payload_passes_claude_code_model_and_effort() -> None:
    payload = build_payload(
        provider="jira",
        issue_ref="MM-874",
        repository="MoonLadderStudios/MoonMind",
        runtime="claude_code",
        model="claude-opus-4-8",
        effort="xhigh",
        preset_version="1.1.0",
        expanded_steps=[{"title": "Load Jira preset brief"}],
    )

    request_payload = payload["payload"]
    assert request_payload["targetRuntime"] == "claude_code"
    assert request_payload["publishMode"] == "pr"
    assert request_payload["mergeAutomation"] == {"enabled": True}
    # task.runtime carries the model/effort the execution router passes through.
    assert request_payload["task"]["runtime"] == {
        "mode": "claude_code",
        "model": "claude-opus-4-8",
        "effort": "xhigh",
    }
    # Model/effort are reflected in the idempotency key (via a bounded shaping token)
    # so distinct runtime shaping does not collide with prior submissions for the same
    # issue, while staying within the varchar(128) persisted idempotency column.
    expected_token = "rt-" + hashlib.sha256(b"claude-opus-4-8|xhigh").hexdigest()[:10]
    key = request_payload["idempotencyKey"]
    assert key == (
        f"jira-implement:MM-874:MoonLadderStudios/MoonMind:claude_code:1.1.0:"
        f"{expected_token}:pr-merge-automation-expanded-steps"
    )
    assert len(key) <= 128


def test_build_payload_without_overrides_keeps_runtime_only_and_stable_key() -> None:
    payload = build_payload(
        provider="jira",
        issue_ref="MM-770",
        repository="MoonLadderStudios/MoonMind",
        runtime="codex_cli",
        preset_version="1.1.0",
        expanded_steps=[],
    )["payload"]
    assert payload["task"]["runtime"] == {"mode": "codex_cli"}
    assert payload["idempotencyKey"] == (
        "jira-implement:MM-770:MoonLadderStudios/MoonMind:codex_cli:1.1.0:"
        "pr-merge-automation-expanded-steps"
    )


def test_extract_column_issue_keys_returns_to_do_issues_in_order() -> None:
    keys = extract_column_issue_keys(_board_issues_fixture(), "To Do")
    assert keys == ["MM-874", "MM-880"]


def test_extract_column_issue_keys_is_case_insensitive() -> None:
    assert extract_column_issue_keys(_board_issues_fixture(), "to do") == ["MM-874", "MM-880"]


def test_extract_column_issue_keys_unknown_column_is_empty() -> None:
    assert extract_column_issue_keys(_board_issues_fixture(), "Nonexistent") == []


def test_main_discovers_board_to_do_issues(monkeypatch, capsys) -> None:
    expanded = {
        "steps": [{"title": f"Step {index}"} for index in range(8)],
        "appliedTemplate": {"slug": "jira-implement", "version": "1.1.0"},
    }
    submitted: list[dict] = []

    def fake_fetch_board_issues(**kwargs):
        assert kwargs["board_id"] == "15"
        return _board_issues_fixture()

    def fake_expand_issue_implement(**kwargs):
        return expanded

    def fake_post_json(**kwargs):
        submitted.append(kwargs["payload"])
        return {
            "status": 201,
            "body": {
                "workflowId": f"workflow-{len(submitted)}",
                "runId": f"run-{len(submitted)}",
                "title": "Run Jira Implement",
                "state": "queued",
            },
        }

    monkeypatch.setattr(module, "fetch_board_issues", fake_fetch_board_issues)
    monkeypatch.setattr(module, "expand_issue_implement", fake_expand_issue_implement)
    monkeypatch.setattr(module, "post_json", fake_post_json)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "create_issue_implement_preset_workflows.py",
            "--provider",
            "jira",
            "--base-url",
            "http://moonmind.test",
            "--board-id",
            "15",
            "--project-key",
            "MM",
            "--runtime",
            "claude_code",
            "--model",
            "claude-opus-4-8",
            "--effort",
            "xhigh",
        ],
    )

    assert module.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["discovery"] == {
        "boardId": "15",
        "column": "To Do",
        "discoveredIssues": ["MM-874", "MM-880"],
    }
    assert report["model"] == "claude-opus-4-8"
    assert report["effort"] == "xhigh"
    assert [record["issue"] for record in report["results"]] == ["MM-874", "MM-880"]
    assert report["failures"] == []
    # Every submitted task uses the Claude Code runtime with the requested overrides
    # and PR + merge automation.
    for payload in submitted:
        task = payload["payload"]["task"]
        assert task["runtime"] == {
            "mode": "claude_code",
            "model": "claude-opus-4-8",
            "effort": "xhigh",
        }
        assert task["publish"] == {"mode": "pr", "mergeAutomation": {"enabled": True}}


def test_main_errors_when_board_column_has_no_issues(monkeypatch, capsys) -> None:
    def fake_fetch_board_issues(**kwargs):
        return {"columns": [{"id": "to-do", "name": "To Do"}], "itemsByColumn": {"to-do": []}}

    monkeypatch.setattr(module, "fetch_board_issues", fake_fetch_board_issues)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "create_issue_implement_preset_workflows.py",
            "--base-url",
            "http://moonmind.test",
            "--board-id",
            "15",
        ],
    )

    assert module.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert "No issues to queue" in report["error"]
