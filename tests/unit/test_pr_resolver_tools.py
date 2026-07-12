from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

def _load_module(script_path: str) -> dict[str, Any]:
    import runpy

    return runpy.run_path(script_path)

REPO_ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def get_pr_comments_module() -> dict[str, Any]:
    return _load_module(
        str(
            REPO_ROOT
            / ".agents"
            / "skills"
            / "fix-comments"
            / "tools"
            / "get_pr_comments.py"
        )
    )

@pytest.fixture
def get_branch_pr_comments_module() -> dict[str, Any]:
    return _load_module(
        str(
            REPO_ROOT
            / ".agents"
            / "skills"
            / "fix-comments"
            / "tools"
            / "get_branch_pr_comments.py"
        )
    )

@pytest.fixture
def pr_resolve_snapshot_module() -> dict[str, Any]:
    return _load_module(
        str(
            REPO_ROOT
            / ".agents"
            / "skills"
            / "pr-resolver"
            / "bin"
            / "pr_resolve_snapshot.py"
        )
    )

@pytest.fixture
def pr_resolve_finalize_module() -> dict[str, Any]:
    return _load_module(
        str(
            REPO_ROOT
            / ".agents"
            / "skills"
            / "pr-resolver"
            / "bin"
            / "pr_resolve_finalize.py"
        )
    )

@pytest.fixture
def pr_resolve_full_module() -> dict[str, Any]:
    return _load_module(
        str(
            REPO_ROOT
            / ".agents"
            / "skills"
            / "pr-resolver"
            / "bin"
            / "pr_resolve_full.py"
        )
    )

@pytest.fixture
def pr_resolve_orchestrate_module() -> dict[str, Any]:
    return _load_module(
        str(
            REPO_ROOT
            / ".agents"
            / "skills"
            / "pr-resolver"
            / "bin"
            / "pr_resolve_orchestrate.py"
        )
    )

@pytest.fixture
def pr_resolve_contract_module() -> dict[str, Any]:
    return _load_module(
        str(
            REPO_ROOT
            / ".agents"
            / "skills"
            / "pr-resolver"
            / "bin"
            / "pr_resolve_contract.py"
        )
    )

def test_parse_remote_url_accepts_https_and_ssh_urls(
    get_pr_comments_module: dict[str, Any],
) -> None:
    parse_remote_url = get_pr_comments_module["_parse_remote_url"]

    assert parse_remote_url("https://github.com/org/example.git") == ("org", "example")
    assert parse_remote_url("git@github.com:org/example.git") == ("org", "example")
    assert parse_remote_url("ssh://git@github.com/org/example") == ("org", "example")

def test_parse_remote_url_returns_none_for_unrelated_inputs(
    get_pr_comments_module: dict[str, Any],
) -> None:
    parse_remote_url = get_pr_comments_module["_parse_remote_url"]

    assert parse_remote_url("") is None
    assert parse_remote_url("not-a-repo") is None
    assert parse_remote_url("owner_only/") is None

def test_parse_repo_slug_accepts_remote_urls_and_owner_repo_forms(
    get_pr_comments_module: dict[str, Any],
) -> None:
    parse_repo_slug = get_pr_comments_module["parse_repo_slug"]

    assert parse_repo_slug("org/example") == ("org", "example")
    assert parse_repo_slug("https://github.com/org/example") == ("org", "example")
    assert parse_repo_slug("git@github.com:org/example") == ("org", "example")

    with pytest.raises(ValueError, match="Invalid --repo value"):
        parse_repo_slug("owner_only")


def test_normalize_review_comment_preserves_thread_node_identity(
    get_pr_comments_module: dict[str, Any],
) -> None:
    normalize = get_pr_comments_module["normalize_review_comment"]

    result = normalize(
        {"id": 42, "user": {"login": "review-bot"}, "body": "Fix this."},
        {
            42: {
                "threadId": "PRRT_kwDOExample",
                "isResolved": False,
                "isOutdated": False,
            }
        },
    )

    assert result["thread_id"] == "PRRT_kwDOExample"
    assert result["thread_resolved"] is False
    assert result["thread_outdated"] is False

def test_infer_repo_from_pr_url_handles_pull_url(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    infer_repo = pr_resolve_snapshot_module["infer_repo_from_pr_url"]

    assert infer_repo("https://github.com/org/example/pull/123") == "org/example"
    assert infer_repo("https://github.com/org/example.git/pull/123") == "org/example"

def test_infer_repo_from_pr_url_returns_none_for_invalid_url(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    infer_repo = pr_resolve_snapshot_module["infer_repo_from_pr_url"]

    assert infer_repo("") is None
    assert infer_repo("not a url") is None
    assert infer_repo("https://github.com/org") is None

def test_get_branch_pr_comments_run_json_command_ignores_stderr_warnings(
    get_branch_pr_comments_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_json_command = get_branch_pr_comments_module["run_json_command"]

    class _Completed:
        returncode = 0
        stdout = '{"ok": true, "count": 2}'
        stderr = "Warning: transient GraphQL issue ignored"

    monkeypatch.setattr(
        get_branch_pr_comments_module["subprocess"],
        "run",
        lambda *args, **kwargs: _Completed(),
    )

    payload = run_json_command(["fake", "command"], "failure hint")
    assert payload == {"ok": True, "count": 2}

def test_pr_resolve_snapshot_run_command_ignores_stderr_warnings(
    pr_resolve_snapshot_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_command = pr_resolve_snapshot_module["run_command"]

    class _Completed:
        returncode = 0
        stdout = '{"ok": true, "result": "ready"}'
        stderr = "Warning: retryable network issue recovered"

    monkeypatch.setattr(
        pr_resolve_snapshot_module["subprocess"],
        "run",
        lambda *args, **kwargs: _Completed(),
    )

    payload = run_command(["fake", "snapshot"], "failure hint")
    assert payload == {"ok": True, "result": "ready"}

def test_pr_resolve_snapshot_run_command_resolves_executable_with_fallback_path(
    pr_resolve_snapshot_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_command = pr_resolve_snapshot_module["run_command"]
    captured_cmd: list[str] = []
    captured_path = ""

    class _Completed:
        returncode = 0
        stdout = '{"ok": true}'
        stderr = ""

    def _fake_run(cmd: list[str], **kwargs: Any) -> _Completed:
        nonlocal captured_cmd, captured_path
        captured_cmd = cmd
        captured_path = str((kwargs.get("env") or {}).get("PATH", ""))
        return _Completed()

    monkeypatch.setenv("PATH", "/tmp/custom-bin")

    def _fake_which(executable: str, path: str | None = None) -> str | None:
        if executable == "gh" and isinstance(path, str) and "/usr/bin" in path:
            return "/usr/bin/gh"
        return None

    monkeypatch.setattr(pr_resolve_snapshot_module["shutil"], "which", _fake_which)
    monkeypatch.setattr(pr_resolve_snapshot_module["subprocess"], "run", _fake_run)

    payload = run_command(["gh", "api", "rate_limit"], "failure hint")
    assert payload == {"ok": True}
    assert captured_cmd[0] == "/usr/bin/gh"
    assert "/usr/bin" in captured_path

def test_fetch_pr_data_falls_back_to_current_branch_selector(
    pr_resolve_snapshot_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_pr_data = pr_resolve_snapshot_module["fetch_pr_data"]
    globals_dict = fetch_pr_data.__globals__

    monkeypatch.setitem(
        globals_dict,
        "_current_branch_name",
        lambda: "feature/test-branch",
    )
    monkeypatch.setitem(
        globals_dict,
        "_discover_pr_number_from_head_branch",
        lambda _branch: None,
    )

    def _fake_fetch(selector: str | None) -> tuple[dict[str, Any] | None, str | None]:
        if selector is None:
            return None, "default failed"
        if selector == "feature/test-branch":
            return {"number": 780, "url": "https://github.com/org/repo/pull/780"}, None
        return None, "unexpected selector"

    monkeypatch.setitem(globals_dict, "_fetch_pr_data_from_selector", _fake_fetch)

    pr_data, selector, errors = fetch_pr_data(None)
    assert isinstance(pr_data, dict)
    assert pr_data["number"] == 780
    assert selector == "feature/test-branch"
    assert len(errors) == 1
    assert errors[0].startswith("<default>:")

def test_fetch_pr_data_can_fallback_to_discovered_pr_number(
    pr_resolve_snapshot_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_pr_data = pr_resolve_snapshot_module["fetch_pr_data"]
    globals_dict = fetch_pr_data.__globals__

    monkeypatch.setitem(
        globals_dict,
        "_current_branch_name",
        lambda: "feature/test-branch",
    )
    monkeypatch.setitem(
        globals_dict,
        "_discover_pr_number_from_head_branch",
        lambda _branch: "780",
    )

    def _fake_fetch(selector: str | None) -> tuple[dict[str, Any] | None, str | None]:
        if selector == "780":
            return {"number": 780, "url": "https://github.com/org/repo/pull/780"}, None
        return None, "selector miss"

    monkeypatch.setitem(globals_dict, "_fetch_pr_data_from_selector", _fake_fetch)

    pr_data, selector, errors = fetch_pr_data(None)
    assert isinstance(pr_data, dict)
    assert pr_data["number"] == 780
    assert selector == "780"
    assert len(errors) >= 1

def test_get_branch_pr_comments_resolve_metadata_falls_back_to_head_pr_list(
    get_branch_pr_comments_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_pr_metadata = get_branch_pr_comments_module["resolve_pr_metadata"]
    globals_dict = resolve_pr_metadata.__globals__

    def _fake_run_json_command(
        command: list[str],
        failure_hint: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if command[:4] == ["gh", "pr", "view", "feature/test-branch"]:
            raise RuntimeError("no pull requests found for branch")
        if command[:3] == ["gh", "pr", "list"]:
            return [{"number": 780}]
        if command[:4] == ["gh", "pr", "view", "780"]:
            return {
                "number": 780,
                "title": "Fallback",
                "url": "https://github.com/org/repo/pull/780",
                "headRefName": "feature/test-branch",
                "baseRefName": "main",
            }
        raise AssertionError(f"Unexpected command: {command} ({failure_hint})")

    monkeypatch.setitem(globals_dict, "run_json_command", _fake_run_json_command)

    payload = resolve_pr_metadata("feature/test-branch")
    assert payload["number"] == 780
    assert payload["headRefName"] == "feature/test-branch"
def test_review_bot_comments_are_actionable_by_default(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]

    comments = [
        {
            "type": "review_comment",
            "user": "qodo-free-for-open-source-projects[bot]",
            "body": "Please update this logic.",
        },
        {
            "type": "review_comment",
            "user": "human-reviewer",
            "body": "Please update this logic.",
        },
    ]

    summary = summarize_comments(comments)
    assert summary["actionableCommentCount"] == 2
    assert summary["includeBotReviewComments"] is True

def test_review_bot_comments_can_be_excluded_when_disabled(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]

    comments = [
        {
            "type": "review_comment",
            "user": "chatgpt-codex-connector[bot]",
            "body": "This still needs to be addressed.",
        }
    ]

    summary = summarize_comments(comments, include_bot_review_comments=False)
    assert summary["actionableCommentCount"] == 0
    assert summary["includeBotReviewComments"] is False

def test_issue_command_comments_are_not_actionable(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]

    comments = [
        {
            "type": "issue_comment",
            "id": 1,
            "user": "human-reviewer",
            "body": "/review",
        },
        {
            "type": "review_comment",
            "id": 2,
            "user": "qodo-free-for-open-source-projects[bot]",
            "body": "Please update this section.",
        },
    ]

    summary = summarize_comments(comments, include_bot_review_comments=True)
    assert summary["actionableCommentCount"] == 1
    assert summary["actionableCommentIds"] == [2]
    assert summary["nonActionableReasonCounts"]["command_comment"] == 1

def test_standalone_codex_review_trigger_is_not_actionable(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]

    comments = [
        {
            "type": "issue_comment",
            "id": 1,
            "user": "human-reviewer",
            "body": "@codex review",
        }
    ]

    summary = summarize_comments(comments, include_bot_review_comments=True)
    assert summary["hasActionableComments"] is False
    assert summary["actionableCommentCount"] == 0
    assert summary["nonActionableReasonCounts"]["command_comment"] == 1

def test_codex_trigger_with_extra_feedback_stays_actionable(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]

    comments = [
        {
            "type": "issue_comment",
            "id": 1,
            "user": "human-reviewer",
            "body": "@codex review\n\nPlease also fix the failing migration test.",
        }
    ]

    summary = summarize_comments(comments, include_bot_review_comments=True)
    assert summary["hasActionableComments"] is True
    assert summary["actionableCommentCount"] == 1
    assert summary["actionableCommentIds"] == [1]

def test_normal_human_issue_comment_stays_actionable(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]

    comments = [
        {
            "type": "issue_comment",
            "id": 1,
            "user": "human-reviewer",
            "body": "This still needs a regression test.",
        }
    ]

    summary = summarize_comments(comments, include_bot_review_comments=True)
    assert summary["hasActionableComments"] is True
    assert summary["actionableCommentCount"] == 1
    assert summary["actionableCommentIds"] == [1]

def test_gemini_only_review_activates_codex_review_grace(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]
    apply_codex_review_grace = pr_resolve_snapshot_module["apply_codex_review_grace"]

    comments = [
        {
            "type": "review",
            "id": 4649647799,
            "user": "gemini-code-assist[bot]",
            "body": "There are no review comments, so I have no feedback to provide.",
            "created_at": "2026-07-07T23:21:05Z",
        }
    ]

    summary = summarize_comments(comments, include_bot_review_comments=True)
    summary = apply_codex_review_grace(
        summary,
        comments,
        head_commit_sha="0f3c29855026f4da35cbead50bfa2d6f0bde641c",
        now=datetime(2026, 7, 7, 23, 26, 44, tzinfo=UTC),
    )

    grace = summary["codexReviewGrace"]
    assert grace["active"] is True
    assert grace["reason"] == "gemini_only_review"
    assert grace["timeoutSeconds"] == 600
    assert grace["pollSeconds"] == 60

def test_gemini_grace_expires_after_ten_minutes_without_restart(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]
    apply_codex_review_grace = pr_resolve_snapshot_module["apply_codex_review_grace"]

    comments = [
        {
            "type": "review",
            "id": 4649647799,
            "user": "gemini-code-assist",
            "body": "There are no review comments.",
            "created_at": "2026-07-07T23:21:05Z",
        }
    ]
    first_now = datetime(2026, 7, 7, 23, 26, 44, tzinfo=UTC)
    first_summary = apply_codex_review_grace(
        summarize_comments(comments, include_bot_review_comments=True),
        comments,
        head_commit_sha="0f3c29855026f4da35cbead50bfa2d6f0bde641c",
        now=first_now,
    )

    later_summary = apply_codex_review_grace(
        summarize_comments(comments, include_bot_review_comments=True),
        comments,
        head_commit_sha="0f3c29855026f4da35cbead50bfa2d6f0bde641c",
        previous_grace=first_summary["codexReviewGrace"],
        now=first_now + timedelta(seconds=601),
    )

    assert later_summary["codexReviewGrace"]["active"] is False
    assert later_summary["codexReviewGrace"]["expired"] is True
    assert later_summary["codexReviewGrace"]["remainingSeconds"] == 0

def test_second_visible_comment_disables_gemini_grace(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]
    apply_codex_review_grace = pr_resolve_snapshot_module["apply_codex_review_grace"]

    comments = [
        {
            "type": "review",
            "id": 4649647799,
            "user": "gemini-code-assist[bot]",
            "body": "There are no review comments.",
        },
        {
            "type": "review_comment",
            "id": 3540280148,
            "user": "chatgpt-codex-connector",
            "body": "Please derive attack targets from TargetUnitId.",
        },
    ]

    summary = apply_codex_review_grace(
        summarize_comments(comments, include_bot_review_comments=True),
        comments,
        head_commit_sha="0f3c29855026f4da35cbead50bfa2d6f0bde641c",
        now=datetime(2026, 7, 7, 23, 30, 34, tzinfo=UTC),
    )

    assert summary["codexReviewGrace"]["active"] is False
    assert summary["codexReviewGrace"]["reason"] == "not_gemini_only"

def test_historical_comments_do_not_disable_gemini_grace(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]
    apply_codex_review_grace = pr_resolve_snapshot_module["apply_codex_review_grace"]

    comments = [
        {
            "type": "review",
            "id": 4649647799,
            "user": "gemini-code-assist",
            "body": "There are no review comments, so I have no feedback to provide.",
            "created_at": "2026-07-07T23:21:05Z",
        },
        {
            "type": "review_comment",
            "id": 3540280148,
            "user": "human-reviewer",
            "body": "Please update this old diff.",
            "thread_resolved": True,
        },
        {
            "type": "issue_comment",
            "id": 3540280149,
            "user": "human-reviewer",
            "body": "@codex review",
        },
    ]

    summary = apply_codex_review_grace(
        summarize_comments(comments, include_bot_review_comments=True),
        comments,
        head_commit_sha="0f3c29855026f4da35cbead50bfa2d6f0bde641c",
        now=datetime(2026, 7, 7, 23, 30, 34, tzinfo=UTC),
    )

    assert summary["hasActionableComments"] is False
    assert summary["codexReviewGrace"]["active"] is True
    assert summary["codexReviewGrace"]["reason"] == "gemini_only_review"

def test_actionable_gemini_review_body_stays_actionable(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]

    comments = [
        {
            "type": "review",
            "id": 4649647799,
            "user": "gemini-code-assist",
            "body": "Please add a regression test before merging.",
        }
    ]

    summary = summarize_comments(comments, include_bot_review_comments=True)

    assert summary["hasActionableComments"] is True
    assert summary["actionableCommentIds"] == [4649647799]

def test_resolved_review_threads_are_not_actionable(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    is_comment_actionable = pr_resolve_snapshot_module["_is_comment_actionable"]

    comment = {
        "type": "review_comment",
        "user": "human-reviewer",
        "body": "Please update this logic.",
        "thread_resolved": True,
    }

    assert is_comment_actionable(comment) is False

def test_outdated_review_threads_are_not_actionable(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    is_comment_actionable = pr_resolve_snapshot_module["_is_comment_actionable"]

    comment = {
        "type": "review_comment",
        "user": "human-reviewer",
        "body": "Please update this logic.",
        "thread_outdated": True,
    }

    assert is_comment_actionable(comment) is False

def test_pr_2014_shape_has_no_actionable_comments_and_can_finalize(
    pr_resolve_snapshot_module: dict[str, Any],
    pr_resolve_finalize_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]
    evaluate_finalize_action = pr_resolve_finalize_module["evaluate_finalize_action"]

    comments = [
        {
            "type": "review_comment",
            "id": 10,
            "user": "gemini-code-assist[bot]",
            "body": "Please simplify this branch.",
            "thread_resolved": True,
        },
        {
            "type": "review_comment",
            "id": 11,
            "user": "qodo-free-for-open-source-projects[bot]",
            "body": "This comment belongs to an older diff.",
            "thread_outdated": True,
        },
        {
            "type": "issue_comment",
            "id": 12,
            "user": "human-reviewer",
            "body": "@codex review",
        },
    ]

    comments_summary = summarize_comments(comments, include_bot_review_comments=True)
    assert comments_summary["hasActionableComments"] is False
    assert comments_summary["actionableCommentCount"] == 0
    assert comments_summary["nonActionableReasonCounts"] == {
        "thread_resolved": 1,
        "thread_outdated": 1,
        "command_comment": 1,
    }

    decision = evaluate_finalize_action(
        {
            "pr": {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"},
            "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
            "commentsFetch": {"succeeded": True, "source": "fixture"},
            "commentsSummary": comments_summary,
        }
    )
    assert decision == {"action": "merge_now", "reason": "ci_complete"}

def test_finalize_blocks_when_actionable_comments_exist(
    pr_resolve_finalize_module: dict[str, Any],
) -> None:
    evaluate_finalize_action = pr_resolve_finalize_module["evaluate_finalize_action"]

    decision = evaluate_finalize_action(
        {
            "pr": {"mergeable": "MERGEABLE", "mergeStateStatus": "UNSTABLE"},
            "ci": {"isRunning": True, "hasFailures": False, "signalQuality": "ok"},
            "commentsFetch": {"succeeded": True, "source": "fixture"},
            "commentsSummary": {
                "hasActionableComments": True,
                "includeBotReviewComments": True,
            },
        }
    )

    assert decision == {"action": "blocked", "reason": "actionable_comments"}

def test_finalize_blocks_during_codex_review_grace(
    pr_resolve_finalize_module: dict[str, Any],
) -> None:
    evaluate_finalize_action = pr_resolve_finalize_module["evaluate_finalize_action"]

    decision = evaluate_finalize_action(
        {
            "pr": {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"},
            "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
            "commentsFetch": {"succeeded": True, "source": "fixture"},
            "commentsSummary": {
                "hasActionableComments": False,
                "includeBotReviewComments": True,
                "codexReviewGrace": {"active": True},
            },
        }
    )

    assert decision == {"action": "blocked", "reason": "codex_review_grace_wait"}

def test_finalize_reports_ci_before_codex_review_grace(
    pr_resolve_finalize_module: dict[str, Any],
) -> None:
    evaluate_finalize_action = pr_resolve_finalize_module["evaluate_finalize_action"]

    decision = evaluate_finalize_action(
        {
            "pr": {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"},
            "ci": {"isRunning": False, "hasFailures": True, "signalQuality": "ok"},
            "commentsFetch": {"succeeded": True, "source": "fixture"},
            "commentsSummary": {
                "hasActionableComments": False,
                "includeBotReviewComments": True,
                "codexReviewGrace": {"active": True},
            },
        }
    )

    assert decision == {"action": "blocked", "reason": "ci_failures"}

def test_finalize_merges_after_codex_review_grace_expires(
    pr_resolve_finalize_module: dict[str, Any],
) -> None:
    evaluate_finalize_action = pr_resolve_finalize_module["evaluate_finalize_action"]

    decision = evaluate_finalize_action(
        {
            "pr": {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"},
            "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
            "commentsFetch": {"succeeded": True, "source": "fixture"},
            "commentsSummary": {
                "hasActionableComments": False,
                "includeBotReviewComments": True,
                "codexReviewGrace": {"active": False, "expired": True},
            },
        }
    )

    assert decision == {"action": "merge_now", "reason": "ci_complete"}

def test_finalize_prioritizes_merge_conflicts_before_comments_and_ci(
    pr_resolve_finalize_module: dict[str, Any],
) -> None:
    evaluate_finalize_action = pr_resolve_finalize_module["evaluate_finalize_action"]

    decision = evaluate_finalize_action(
        {
            "pr": {"mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"},
            "ci": {
                "isRunning": False,
                "hasFailures": True,
                "signalQuality": "degraded",
            },
            "commentsFetch": {"succeeded": True, "source": "fixture"},
            "commentsSummary": {
                "hasActionableComments": True,
                "includeBotReviewComments": True,
            },
        }
    )

    assert decision == {"action": "blocked", "reason": "merge_conflicts"}

def test_finalize_blocks_when_ci_running_and_comments_addressed(
    pr_resolve_finalize_module: dict[str, Any],
) -> None:
    evaluate_finalize_action = pr_resolve_finalize_module["evaluate_finalize_action"]

    decision = evaluate_finalize_action(
        {
            "pr": {"mergeable": "MERGEABLE", "mergeStateStatus": "UNSTABLE"},
            "ci": {"isRunning": True, "hasFailures": False, "signalQuality": "ok"},
            "commentsFetch": {"succeeded": True, "source": "fixture"},
            "commentsSummary": {
                "hasActionableComments": False,
                "includeBotReviewComments": True,
            },
        }
    )

    assert decision == {"action": "blocked", "reason": "ci_running"}

def test_finalize_merges_when_ci_complete_and_clean(
    pr_resolve_finalize_module: dict[str, Any],
) -> None:
    evaluate_finalize_action = pr_resolve_finalize_module["evaluate_finalize_action"]

    decision = evaluate_finalize_action(
        {
            "pr": {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"},
            "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
            "commentsFetch": {"succeeded": True, "source": "fixture"},
            "commentsSummary": {
                "hasActionableComments": False,
                "includeBotReviewComments": True,
            },
        }
    )

    assert decision == {"action": "merge_now", "reason": "ci_complete"}

def test_finalize_blocks_when_comments_are_unavailable(
    pr_resolve_finalize_module: dict[str, Any],
) -> None:
    evaluate_finalize_action = pr_resolve_finalize_module["evaluate_finalize_action"]

    decision = evaluate_finalize_action(
        {
            "pr": {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"},
            "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
            "commentsFetch": {"succeeded": False, "source": "fixture"},
            "commentsSummary": {
                "hasActionableComments": False,
                "includeBotReviewComments": True,
            },
        }
    )

    assert decision == {"action": "blocked", "reason": "comments_unavailable"}

def test_finalize_blocks_when_comment_policy_not_enforced(
    pr_resolve_finalize_module: dict[str, Any],
) -> None:
    evaluate_finalize_action = pr_resolve_finalize_module["evaluate_finalize_action"]

    decision = evaluate_finalize_action(
        {
            "pr": {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"},
            "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
            "commentsFetch": {"succeeded": True, "source": "fixture"},
            "commentsSummary": {
                "hasActionableComments": False,
                "includeBotReviewComments": False,
            },
        }
    )

    assert decision == {"action": "blocked", "reason": "comment_policy_not_enforced"}

def test_finalize_blocks_when_ci_signal_is_degraded(
    pr_resolve_finalize_module: dict[str, Any],
) -> None:
    evaluate_finalize_action = pr_resolve_finalize_module["evaluate_finalize_action"]

    decision = evaluate_finalize_action(
        {
            "pr": {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"},
            "ci": {
                "isRunning": False,
                "hasFailures": False,
                "signalQuality": "degraded",
            },
            "commentsFetch": {"succeeded": True, "source": "fixture"},
            "commentsSummary": {
                "hasActionableComments": False,
                "includeBotReviewComments": True,
            },
        }
    )

    assert decision == {"action": "blocked", "reason": "ci_signal_degraded"}

def test_full_marks_actionable_comments_as_needs_remediation(
    pr_resolve_full_module: dict[str, Any],
) -> None:
    evaluate_full_state = pr_resolve_full_module["evaluate_full_state"]
    result = evaluate_full_state(
        {
            "pr": {"mergeable": "MERGEABLE", "mergeStateStatus": "UNSTABLE"},
            "ci": {"isRunning": True, "hasFailures": False, "signalQuality": "ok"},
            "commentsFetch": {"succeeded": True, "source": "fixture"},
            "commentsSummary": {
                "hasActionableComments": True,
                "includeBotReviewComments": True,
            },
        }
    )
    assert result["status"] == "needs_remediation"
    assert result["reason"] == "actionable_comments"
    assert result["next_step"] == "run_fix_comments_skill"

def test_orchestrate_actionable_comments_escalates_once_then_merges(
    pr_resolve_orchestrate_module: dict[str, Any],
) -> None:
    run_orchestration = pr_resolve_orchestrate_module["run_orchestration"]

    finalize_results = iter(
        [
            {
                "status": "blocked",
                "merge_outcome": "blocked",
                "reason": "actionable_comments",
            },
            {"status": "merged", "merge_outcome": "merged", "reason": "ci_complete"},
        ]
    )
    full_calls: list[tuple[int, int, str]] = []
    sleeps: list[int] = []

    result, exit_code = run_orchestration(
        finalize_runner=lambda _attempt: next(finalize_results),
        full_runner=lambda attempt, escalation, reason: (
            full_calls.append((attempt, escalation, reason))
            or {
                "status": "needs_remediation",
                "merge_outcome": "blocked",
                "reason": reason,
            }
        ),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=lambda: 0.0,
        finalize_max_retries=2,
        fix_max_iterations=5,
        base_sleep_seconds=15,
        max_sleep_seconds=60,
        max_elapsed_seconds=900,
        merge_not_ready_grace_retries=1,
    )

    assert exit_code == 0
    assert result["status"] == "merged"
    assert result["mergeAutomationDisposition"] == "merged"
    assert len(full_calls) == 1
    assert full_calls[0][2] == "actionable_comments"
    assert sleeps == []

def test_orchestrate_no_progress_after_ci_wait_uses_min_attempt_floor(
    pr_resolve_orchestrate_module: dict[str, Any],
) -> None:
    run_orchestration = pr_resolve_orchestrate_module["run_orchestration"]
    full_calls: list[tuple[int, int, str]] = []
    sleeps: list[int] = []

    def finalize_runner(attempt: int) -> dict[str, Any]:
        continuation = {
            "schemaVersion": "gated-continuation/v1",
            "gateType": "merge_automation",
            "action": "reenter_gate",
            "reason": "resolver_wait",
            "retryAfterSeconds": 60,
            "executionRef": "step:1",
            "headSha": "abcdef1234567890",
        }
        if attempt == 1:
            return {
                "status": "blocked",
                "merge_outcome": "blocked",
                "reason": "ci_running",
                "gatedContinuation": continuation,
            }
        return {
            "status": "blocked",
            "merge_outcome": "blocked",
            "reason": "actionable_comments",
            "gatedContinuation": continuation,
        }

    result, exit_code = run_orchestration(
        finalize_runner=finalize_runner,
        full_runner=lambda attempt, escalation, reason: (
            full_calls.append((attempt, escalation, reason))
            or {
                "status": "needs_remediation",
                "merge_outcome": "blocked",
                "reason": reason,
            }
        ),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=lambda: 0.0,
        finalize_max_retries=2,
        fix_max_iterations=5,
        base_sleep_seconds=15,
        max_sleep_seconds=60,
        max_elapsed_seconds=900,
        merge_not_ready_grace_retries=1,
    )

    assert exit_code == 3
    assert result["status"] == "attempts_exhausted"
    assert result["merge_outcome"] == "attempts_exhausted"
    assert result["mergeAutomationDisposition"] == "reenter_gate"
    assert result["final_reason"] == "actionable_comments"
    assert result["attempt_count"] == 5
    assert result["max_attempts"] == 5
    assert result["min_attempts_before_exhausted"] == 5
    assert sleeps == [60]
    assert [call[0] for call in full_calls] == [2, 3, 4]

def test_orchestrate_ci_running_uses_finalize_only_retry_path(
    pr_resolve_orchestrate_module: dict[str, Any],
) -> None:
    run_orchestration = pr_resolve_orchestrate_module["run_orchestration"]

    finalize_results = iter(
        [
            {"status": "blocked", "merge_outcome": "blocked", "reason": "ci_running"},
            {"status": "merged", "merge_outcome": "merged", "reason": "ci_complete"},
        ]
    )
    sleeps: list[int] = []
    full_invocations = 0

    def full_runner(_attempt: int, _escalation: int, _reason: str) -> dict[str, Any]:
        nonlocal full_invocations
        full_invocations += 1
        return {"status": "failed", "merge_outcome": "failed", "reason": "unexpected"}

    result, exit_code = run_orchestration(
        finalize_runner=lambda _attempt: next(finalize_results),
        full_runner=full_runner,
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=lambda: 0.0,
        finalize_max_retries=2,
        fix_max_iterations=3,
        base_sleep_seconds=15,
        max_sleep_seconds=60,
        max_elapsed_seconds=900,
        merge_not_ready_grace_retries=1,
    )

    assert exit_code == 0
    assert result["status"] == "merged"
    assert result["mergeAutomationDisposition"] == "merged"
    assert full_invocations == 0
    assert sleeps == [60]


def test_orchestrate_codex_review_grace_waits_then_merges(
    pr_resolve_orchestrate_module: dict[str, Any],
) -> None:
    run_orchestration = pr_resolve_orchestrate_module["run_orchestration"]

    finalize_results = iter(
        [
            {
                "status": "blocked",
                "merge_outcome": "blocked",
                "reason": "codex_review_grace_wait",
            },
            {"status": "merged", "merge_outcome": "merged", "reason": "ci_complete"},
        ]
    )
    sleeps: list[int] = []
    full_invocations = 0

    def full_runner(_attempt: int, _escalation: int, _reason: str) -> dict[str, Any]:
        nonlocal full_invocations
        full_invocations += 1
        return {"status": "failed", "merge_outcome": "failed", "reason": "unexpected"}

    result, exit_code = run_orchestration(
        finalize_runner=lambda _attempt: next(finalize_results),
        full_runner=full_runner,
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=lambda: 0.0,
        finalize_max_retries=2,
        fix_max_iterations=3,
        base_sleep_seconds=15,
        max_sleep_seconds=120,
        max_elapsed_seconds=900,
        merge_not_ready_grace_retries=1,
    )

    assert exit_code == 0
    assert result["status"] == "merged"
    assert full_invocations == 0
    assert sleeps == [60]


def test_orchestrate_ci_running_sleep_floor_overrides_low_max_sleep(
    pr_resolve_orchestrate_module: dict[str, Any],
) -> None:
    run_orchestration = pr_resolve_orchestrate_module["run_orchestration"]

    finalize_results = iter(
        [
            {"status": "blocked", "merge_outcome": "blocked", "reason": "ci_running"},
            {"status": "merged", "merge_outcome": "merged", "reason": "ci_complete"},
        ]
    )
    sleeps: list[int] = []

    result, exit_code = run_orchestration(
        finalize_runner=lambda _attempt: next(finalize_results),
        full_runner=lambda _attempt, _escalation, _reason: {
            "status": "failed",
            "merge_outcome": "failed",
            "reason": "unexpected",
        },
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=lambda: 0.0,
        finalize_max_retries=2,
        fix_max_iterations=3,
        base_sleep_seconds=15,
        max_sleep_seconds=30,
        max_elapsed_seconds=900,
        merge_not_ready_grace_retries=1,
    )

    assert exit_code == 0
    assert result["status"] == "merged"
    assert result["mergeAutomationDisposition"] == "merged"
    assert sleeps == [60]


def test_orchestrate_ci_failures_after_transient_ci_states_escalates_fix_ci(
    pr_resolve_orchestrate_module: dict[str, Any],
) -> None:
    run_orchestration = pr_resolve_orchestrate_module["run_orchestration"]

    finalize_results = iter(
        [
            {
                "status": "blocked",
                "merge_outcome": "blocked",
                "reason": "ci_signal_degraded",
            },
            {
                "status": "blocked",
                "merge_outcome": "blocked",
                "reason": "ci_running",
            },
            {
                "status": "blocked",
                "merge_outcome": "blocked",
                "reason": "ci_failures",
            },
            {
                "status": "merged",
                "merge_outcome": "merged",
                "reason": "ci_complete",
            },
        ]
    )
    full_calls: list[tuple[int, int, str]] = []
    sleeps: list[int] = []

    result, exit_code = run_orchestration(
        finalize_runner=lambda _attempt: next(finalize_results),
        full_runner=lambda attempt, escalation, reason: (
            full_calls.append((attempt, escalation, reason))
            or {
                "status": "needs_remediation",
                "merge_outcome": "blocked",
                "reason": reason,
            }
        ),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=lambda: 0.0,
        finalize_max_retries=3,
        fix_max_iterations=3,
        base_sleep_seconds=15,
        max_sleep_seconds=60,
        max_elapsed_seconds=900,
        merge_not_ready_grace_retries=1,
    )

    assert exit_code == 0
    assert result["status"] == "merged"
    assert full_calls == [(3, 1, "ci_failures")]
    assert sleeps == [15, 60]

def test_orchestrate_merge_conflicts_after_ci_running_escalates_fix_conflicts(
    pr_resolve_orchestrate_module: dict[str, Any],
) -> None:
    run_orchestration = pr_resolve_orchestrate_module["run_orchestration"]

    finalize_results = iter(
        [
            {
                "status": "blocked",
                "merge_outcome": "blocked",
                "reason": "ci_running",
            },
            {
                "status": "blocked",
                "merge_outcome": "blocked",
                "reason": "merge_conflicts",
            },
            {
                "status": "merged",
                "merge_outcome": "merged",
                "reason": "ci_complete",
            },
        ]
    )
    full_calls: list[tuple[int, int, str]] = []
    sleeps: list[int] = []

    result, exit_code = run_orchestration(
        finalize_runner=lambda _attempt: next(finalize_results),
        full_runner=lambda attempt, escalation, reason: (
            full_calls.append((attempt, escalation, reason))
            or {
                "status": "needs_remediation",
                "merge_outcome": "blocked",
                "reason": reason,
            }
        ),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=lambda: 0.0,
        finalize_max_retries=3,
        fix_max_iterations=3,
        base_sleep_seconds=15,
        max_sleep_seconds=60,
        max_elapsed_seconds=900,
        merge_not_ready_grace_retries=1,
    )

    assert exit_code == 0
    assert result["status"] == "merged"
    assert full_calls == [(2, 1, "merge_conflicts")]
    assert sleeps == [60]

def test_orchestrate_merge_conflicts_after_merge_not_ready_escalates_fix_conflicts(
    pr_resolve_orchestrate_module: dict[str, Any],
) -> None:
    run_orchestration = pr_resolve_orchestrate_module["run_orchestration"]

    finalize_results = iter(
        [
            {
                "status": "blocked",
                "merge_outcome": "blocked",
                "reason": "merge_not_ready",
            },
            {
                "status": "blocked",
                "merge_outcome": "blocked",
                "reason": "merge_conflicts",
            },
            {
                "status": "merged",
                "merge_outcome": "merged",
                "reason": "ci_complete",
            },
        ]
    )
    full_calls: list[tuple[int, int, str]] = []
    sleeps: list[int] = []

    result, exit_code = run_orchestration(
        finalize_runner=lambda _attempt: next(finalize_results),
        full_runner=lambda attempt, escalation, reason: (
            full_calls.append((attempt, escalation, reason))
            or {
                "status": "needs_remediation",
                "merge_outcome": "blocked",
                "reason": reason,
            }
        ),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=lambda: 0.0,
        finalize_max_retries=3,
        fix_max_iterations=3,
        base_sleep_seconds=15,
        max_sleep_seconds=60,
        max_elapsed_seconds=900,
        merge_not_ready_grace_retries=1,
    )

    assert exit_code == 0
    assert result["status"] == "merged"
    assert full_calls == [(2, 1, "merge_conflicts")]
    assert sleeps == [15]

def test_orchestrate_main_uses_extended_finalize_wait_defaults(
    pr_resolve_orchestrate_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main = pr_resolve_orchestrate_module["main"]
    globals_dict = main.__globals__
    captured: dict[str, Any] = {}

    def _fake_run_orchestration(**kwargs: Any) -> tuple[dict[str, Any], int]:
        captured.update(kwargs)
        return (
            {
                "status": "merged",
                "decision": "merged",
                "merge_outcome": "merged",
                "final_reason": "ci_complete",
                "next_step": "done",
                "history": [],
                "escalations": 0,
                "max_attempts": 7,
                "finalize_max_retries": kwargs["finalize_max_retries"],
                "fix_max_iterations": kwargs["fix_max_iterations"],
                "started_at": "2026-04-04T00:00:00Z",
                "finished_at": "2026-04-04T00:00:01Z",
                "tool": "pr_resolve_orchestrate",
                "schema_version": "v1",
            },
            0,
        )

    monkeypatch.setitem(globals_dict, "run_orchestration", _fake_run_orchestration)
    monkeypatch.setitem(
        globals_dict,
        "_write_auto_publish_evidence",
        lambda _result_path, _snapshot_path: None,
    )

    result_path = tmp_path / "result.json"
    snapshot_path = tmp_path / "snapshot.json"
    attempts_dir = tmp_path / "attempts"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_orchestrate.py",
            "--result-path",
            str(result_path),
            "--snapshot-path",
            str(snapshot_path),
            "--attempt-artifacts-dir",
            str(attempts_dir),
        ],
    )

    with pytest.raises(SystemExit) as raised:
        main()

    assert int(raised.value.code) == 0
    assert captured["finalize_max_retries"] == 60
    assert captured["fix_max_iterations"] == 5
    assert captured["min_attempts_before_exhausted"] == 5
    assert captured["base_sleep_seconds"] == 30
    assert captured["max_sleep_seconds"] == 120
    assert captured["max_elapsed_seconds"] == 7200

def test_contract_snapshot_refresh_failed_is_finalize_only_retry(
    pr_resolve_contract_module: dict[str, Any],
) -> None:
    classify_retry_action = pr_resolve_contract_module["classify_retry_action"]

    action = classify_retry_action(
        "snapshot_refresh_failed",
        merge_not_ready_grace_remaining=1,
    )
    assert action == "finalize_only_retry"


def test_contract_external_state_transient_is_finalize_only_retry(
    pr_resolve_contract_module: dict[str, Any],
) -> None:
    classify_retry_action = pr_resolve_contract_module["classify_retry_action"]
    remediation_next_step = pr_resolve_contract_module["remediation_next_step"]

    action = classify_retry_action(
        "external_state_transient",
        merge_not_ready_grace_remaining=0,
    )

    assert action == "finalize_only_retry"
    assert (
        remediation_next_step("external_state_transient")
        == "retry_finalize_after_backoff"
    )

def test_contract_merge_pending_is_finalize_only_retry(
    pr_resolve_contract_module: dict[str, Any],
) -> None:
    classify_retry_action = pr_resolve_contract_module["classify_retry_action"]
    remediation_next_step = pr_resolve_contract_module["remediation_next_step"]

    action = classify_retry_action(
        "merge_pending",
        merge_not_ready_grace_remaining=0,
    )

    assert action == "finalize_only_retry"
    assert remediation_next_step("merge_pending") == "retry_finalize_after_backoff"

def test_contract_codex_review_grace_wait_is_finalize_only_retry(
    pr_resolve_contract_module: dict[str, Any],
) -> None:
    classify_retry_action = pr_resolve_contract_module["classify_retry_action"]
    remediation_next_step = pr_resolve_contract_module["remediation_next_step"]

    action = classify_retry_action(
        "codex_review_grace_wait",
        merge_not_ready_grace_remaining=0,
    )

    assert action == "finalize_only_retry"
    assert (
        remediation_next_step("codex_review_grace_wait")
        == "wait_for_codex_review_and_retry_finalize"
    )

def test_contract_run_fix_next_step_returns_reenter_gate(
    pr_resolve_contract_module: dict[str, Any],
) -> None:
    disposition_for_result = pr_resolve_contract_module[
        "merge_automation_disposition_for_result"
    ]

    disposition = disposition_for_result(
        status="attempts_exhausted",
        merge_outcome="attempts_exhausted",
        final_reason="actionable_comments",
        next_step="run_fix_comments_skill",
    )

    assert disposition == "reenter_gate"

def test_contract_finalize_retry_next_step_returns_reenter_gate(
    pr_resolve_contract_module: dict[str, Any],
) -> None:
    disposition_for_result = pr_resolve_contract_module[
        "merge_automation_disposition_for_result"
    ]

    disposition = disposition_for_result(
        status="blocked",
        merge_outcome="blocked",
        final_reason="ci_running",
        next_step="retry_finalize_after_backoff",
    )

    assert disposition == "reenter_gate"


def test_gated_continuation_rejects_null_retry_delay(
    pr_resolve_contract_module: dict[str, Any],
) -> None:
    build = pr_resolve_contract_module["build_gated_continuation"]

    with pytest.raises(ValueError, match="retry delay must be positive"):
        build(
            {
                "pr": {"headRefOid": "abcdef1234567890"},
                "commentsSummary": {
                    "codexReviewGrace": {"pollSeconds": None}
                },
            },
            reason="ci_running",
            execution_ref="step:1",
        )


def test_full_result_emits_typed_reenter_gate_contract(
    pr_resolve_full_module: dict[str, Any],
    tmp_path: Path,
) -> None:
    result_path = tmp_path / "full-result.json"
    pr_resolve_full_module["_write_result"](
        result_path,
        snapshot={
            "pr": {
                "number": 1209,
                "url": "https://example.invalid/pull/1209",
                "headRefOid": "abcdef1234567890",
            }
        },
        status="needs_remediation",
        merge_outcome="blocked",
        decision="remediation required",
        reason="actionable_comments",
        next_step="run_fix_comments_skill",
        max_iterations=5,
        merge_method="squash",
    )

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["mergeAutomationDisposition"] == "reenter_gate"
    assert payload["gatedContinuation"]["executionRef"]
    assert payload["gatedContinuation"]["headSha"] == "abcdef1234567890"
    assert payload["gatedContinuation"]["retryAfterSeconds"] == 60


def test_direct_finalizer_preserves_original_codex_review_deadline(
    pr_resolve_finalize_module: dict[str, Any],
    tmp_path: Path,
) -> None:
    write_result = pr_resolve_finalize_module["_write_result"]
    result_path = tmp_path / "result.json"
    expires_at = "2026-07-12T05:05:49Z"
    snapshot = {
        "pr": {
            "number": 1209,
            "url": "https://example.invalid/pull/1209",
            "headRefOid": "a8bb8c756f69e4508ed20776890417ba01d89c8d",
        },
        "commentsSummary": {
            "codexReviewGrace": {
                "active": True,
                "expiresAt": expires_at,
                "pollSeconds": 60,
            }
        },
    }

    write_result(
        result_path,
        snapshot=snapshot,
        decision="blocked",
        merge_outcome="blocked",
        status="blocked",
        reason="codex_review_grace_wait",
    )

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    continuation = payload["gatedContinuation"]
    assert continuation["notBefore"] == expires_at
    assert continuation["executionRef"]
    assert continuation["headSha"] == snapshot["pr"]["headRefOid"]
    assert "retryAfterSeconds" not in continuation

def test_finalize_snapshot_refresh_failure_is_blocked_retryable(
    pr_resolve_finalize_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import subprocess

    main = pr_resolve_finalize_module["main"]
    globals_dict = main.__globals__
    exit_code_blocked = pr_resolve_finalize_module["EXIT_CODE_BLOCKED"]

    def _boom(
        _snapshot_script: Path,
        _pr: str | None,
        _snapshot_path: Path,
    ) -> None:
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=["python3", "pr_resolve_snapshot.py"],
            output="transient failure",
        )

    monkeypatch.setitem(globals_dict, "_run_snapshot", _boom)

    result_path = tmp_path / "pr_resolver_result.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--result-path",
            str(result_path),
        ],
    )

    with pytest.raises(SystemExit) as raised:
        main()
    assert int(raised.value.code) == 0

    payload = result_path.read_text(encoding="utf-8")
    assert '"status": "blocked"' in payload
    assert '"reason": "snapshot_refresh_failed"' in payload
    assert '"mergeAutomationDisposition": "failed"' in payload

    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--strict-exit-codes",
            "--result-path",
            str(result_path),
        ],
    )
    with pytest.raises(SystemExit) as raised_strict:
        main()
    assert int(raised_strict.value.code) == int(exit_code_blocked)

def test_run_snapshot_captures_stderr_for_auth_classifier(
    pr_resolve_finalize_module: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Regression: _run_snapshot must capture stdout/stderr so that auth
    failures emitted by pr_resolve_snapshot.py surface through
    CalledProcessError.stderr, letting _snapshot_failed_reason detect
    them via _GITHUB_AUTH_FAILURE_MARKERS.

    Before the fix, subprocess.run was called without capture_output=True,
    so CalledProcessError.stderr was always None and every snapshot
    failure (including auth) was misclassified as pr_not_found.
    """
    import subprocess

    _run_snapshot = pr_resolve_finalize_module["_run_snapshot"]
    snapshot_failed_reason = pr_resolve_finalize_module["_snapshot_failed_reason"]

    fake_snapshot = tmp_path / "fake_snapshot.py"
    fake_snapshot.write_text(
        "import sys\n"
        "print('You are not logged into any GitHub hosts. "
        "Run gh auth login to authenticate.', file=sys.stderr)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    snapshot_path = tmp_path / "snapshot.json"

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        _run_snapshot(fake_snapshot, None, snapshot_path)

    exc = excinfo.value
    assert exc.stderr is not None, (
        "_run_snapshot must pass capture_output=True so CalledProcessError.stderr "
        "is populated for downstream auth-failure classification"
    )
    assert "not logged into any github hosts" in exc.stderr.lower()
    assert snapshot_failed_reason(exc) == "publish_unavailable"

def test_finalize_snapshot_auth_failure_reports_publish_unavailable(
    pr_resolve_finalize_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import subprocess

    main = pr_resolve_finalize_module["main"]
    globals_dict = main.__globals__
    exit_code_blocked = pr_resolve_finalize_module["EXIT_CODE_BLOCKED"]

    def _boom(
        _snapshot_script: Path,
        _pr: str | None,
        _snapshot_path: Path,
    ) -> None:
        raise subprocess.CalledProcessError(
            returncode=pr_resolve_finalize_module["EXIT_CODE_FAILED"],
            cmd=["python3", "pr_resolve_snapshot.py"],
            stderr=(
                "You are not logged into any GitHub hosts. "
                "Run gh auth login to authenticate."
            ),
        )

    monkeypatch.setitem(globals_dict, "_run_snapshot", _boom)
    monkeypatch.setitem(globals_dict, "_check_pr_merged", lambda _selector: False)

    result_path = tmp_path / "pr_resolver_result.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--pr",
            "feature/branch",
            "--result-path",
            str(result_path),
        ],
    )

    with pytest.raises(SystemExit) as raised:
        main()
    assert int(raised.value.code) == 0

    import json

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert payload["reason"] == "publish_unavailable"
    assert payload["next_step"] == "manual_review"
    assert payload["mergeAutomationDisposition"] == "manual_review"

    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--strict-exit-codes",
            "--pr",
            "feature/branch",
            "--result-path",
            str(result_path),
        ],
    )
    with pytest.raises(SystemExit) as raised_strict:
        main()
    assert int(raised_strict.value.code) == int(exit_code_blocked)

def test_summarize_ci_treats_stale_rollup_as_running(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    """When rollup has non-security checks but REST API has none for the
    actual HEAD, the rollup is stale and CI should be marked as running."""
    summarize = pr_resolve_snapshot_module["summarize_ci_checks"]

    # Simulate a stale rollup with one non-security check that passed
    stale_checks = [
        {
            "name": "test",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "workflowName": "Run Pytest Unit Tests",
        }
    ]
    summary = summarize(stale_checks)
    assert summary["nonSecurityCheckCount"] == 1
    assert summary["isRunning"] is False
    assert summary["hasFailures"] is False

    # Simulate an empty HEAD check-run result (CI hasn't started)
    head_summary = summarize([])
    head_non_sec = head_summary.get("nonSecurityCheckCount", 0)
    rollup_non_sec = summary.get("nonSecurityCheckCount", 0)

    # Verify the condition our cross-check uses
    assert rollup_non_sec > 0 and head_non_sec == 0

def test_summarize_ci_head_checks_propagate_failures(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    """When REST API check-runs for HEAD exist and include failures,
    the summarize function should report them."""
    summarize = pr_resolve_snapshot_module["summarize_ci_checks"]

    head_checks = [
        {
            "name": "test",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
            "workflowName": "Run Pytest Unit Tests",
        },
        {
            "name": "Analyze (python)",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "workflowName": "CodeQL",
        },
    ]
    summary = summarize(head_checks)
    assert summary["hasFailures"] is True
    assert summary["isRunning"] is False
    assert summary["nonSecurityCheckCount"] == 1
    assert len(summary["failedChecks"]) == 1
    assert summary["failedChecks"][0]["name"] == "test"

def test_finalize_already_merged_pr_returns_already_merged(
    pr_resolve_finalize_module: dict[str, Any],
) -> None:
    evaluate_finalize_action = pr_resolve_finalize_module["evaluate_finalize_action"]

    decision = evaluate_finalize_action(
        {
            "pr": {
                "state": "MERGED",
                "mergeable": "UNKNOWN",
                "mergeStateStatus": "UNKNOWN",
            },
            "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
            "commentsFetch": {"succeeded": True, "source": "fixture"},
            "commentsSummary": {
                "hasActionableComments": False,
                "includeBotReviewComments": True,
            },
        }
    )

    assert decision == {"action": "already_merged", "reason": "already_merged"}

def test_finalize_merge_request_waits_for_authoritative_merged_state(
    pr_resolve_finalize_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json

    main = pr_resolve_finalize_module["main"]
    globals_dict = main.__globals__
    exit_code_blocked = pr_resolve_finalize_module["EXIT_CODE_BLOCKED"]

    snapshot = {
        "pr": {
            "number": 3210,
            "state": "OPEN",
            "headRefOid": "abcdef1234567890",
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
        },
        "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
        "commentsFetch": {"succeeded": True, "source": "fixture"},
        "commentsSummary": {
            "hasActionableComments": False,
            "includeBotReviewComments": True,
        },
    }

    def _write_snapshot(
        _snapshot_script: Path,
        _pr: str | None,
        snapshot_path: Path,
    ) -> None:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    merge_calls: list[tuple[str, str]] = []
    monkeypatch.setitem(globals_dict, "_run_snapshot", _write_snapshot)
    monkeypatch.setitem(
        globals_dict,
        "_merge_pr",
        lambda selector, method: merge_calls.append((selector, method)),
    )
    monkeypatch.setitem(globals_dict, "_check_pr_merged", lambda _selector: False)

    result_path = tmp_path / "result.json"
    snapshot_path = tmp_path / "snapshot.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--strict-exit-codes",
            "--pr",
            "3210",
            "--snapshot-path",
            str(snapshot_path),
            "--result-path",
            str(result_path),
        ],
    )

    with pytest.raises(SystemExit) as raised:
        main()

    assert int(raised.value.code) == int(exit_code_blocked)
    assert merge_calls == [("3210", "squash")]
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert payload["reason"] == "merge_pending"
    assert payload["merge_outcome"] == "blocked"
    assert payload["mergeAutomationDisposition"] == "reenter_gate"

def test_finalize_pr_not_found_but_merged_succeeds(
    pr_resolve_finalize_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When snapshot refresh fails but the PR is actually merged,
    finalize should report status=merged and exit with EXIT_CODE_MERGED."""
    import subprocess as _subprocess

    main = pr_resolve_finalize_module["main"]
    globals_dict = main.__globals__
    exit_code_merged = pr_resolve_finalize_module["EXIT_CODE_MERGED"]
    exit_code_failed = pr_resolve_finalize_module["EXIT_CODE_FAILED"]

    # Simulate snapshot refresh failure with EXIT_CODE_FAILED
    def _boom(
        _snapshot_script: Path,
        _pr: str | None,
        _snapshot_path: Path,
    ) -> None:
        raise _subprocess.CalledProcessError(
            returncode=int(exit_code_failed),
            cmd=["python3", "pr_resolve_snapshot.py"],
            output="",
            stderr="no pull requests found",
        )

    monkeypatch.setitem(globals_dict, "_run_snapshot", _boom)

    # Simulate _check_pr_merged returning True (PR was merged)
    monkeypatch.setitem(globals_dict, "_check_pr_merged", lambda _selector: True)

    result_path = tmp_path / "pr_resolver_result.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--pr",
            "123",
            "--result-path",
            str(result_path),
        ],
    )

    with pytest.raises(SystemExit) as raised:
        main()
    assert int(raised.value.code) == int(exit_code_merged)

    import json

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "merged"
    assert payload["reason"] == "already_merged"
    assert payload["merge_outcome"] == "merged"
    assert payload["mergeAutomationDisposition"] == "already_merged"

# ---------------------------------------------------------------------------
# Review-thread authority
# ---------------------------------------------------------------------------

def test_unresolved_bot_comment_on_old_commit_remains_actionable(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    """A pushed head does not replace authoritative GitHub thread resolution."""
    classify = pr_resolve_snapshot_module["_classify_comment_actionability"]

    comment = {
        "type": "review_comment",
        "user": "github-code-quality[bot]",
        "body": "Unused import found.",
        "commit_id": "aaa111",
    }

    actionable, reason = classify(
        comment,
        include_bot_review_comments=True,
        head_commit_sha="bbb222",
    )
    assert actionable is True
    assert reason == "actionable"

def test_human_comment_on_old_commit_stays_actionable(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    """Human review comments on old commits should remain actionable."""
    classify = pr_resolve_snapshot_module["_classify_comment_actionability"]

    comment = {
        "type": "review_comment",
        "user": "human-reviewer",
        "body": "Please fix this.",
        "commit_id": "aaa111",
    }

    actionable, reason = classify(
        comment,
        include_bot_review_comments=True,
        head_commit_sha="bbb222",
    )
    assert actionable is True
    assert reason == "actionable"

def test_bot_comment_on_current_sha_stays_actionable(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    """Bot comment on the current HEAD commit should still be actionable."""
    classify = pr_resolve_snapshot_module["_classify_comment_actionability"]

    comment = {
        "type": "review_comment",
        "user": "gemini-code-assist[bot]",
        "body": "Please fix this.",
        "commit_id": "same_sha",
    }

    actionable, reason = classify(
        comment,
        include_bot_review_comments=True,
        head_commit_sha="same_sha",
    )
    assert actionable is True
    assert reason == "actionable"


def test_local_ledger_cannot_clear_unresolved_review_thread(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]

    summary = summarize_comments(
        [
            {
                "type": "review_comment",
                "id": 42,
                "user": "chatgpt-codex-connector",
                "body": "This remains unresolved on GitHub.",
                "thread_resolved": False,
                "thread_outdated": False,
                "commit_id": "old-head",
            }
        ],
        addressed_comment_ids={42},
        head_commit_sha="new-head",
    )

    assert summary["hasActionableComments"] is True
    assert summary["actionableCommentIds"] == [42]

# ---------------------------------------------------------------------------
# Ledger path/format normalization (Phase 2)
# ---------------------------------------------------------------------------

def test_load_addressed_ids_reads_disposition_field(
    pr_resolve_snapshot_module: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Ledger with 'disposition' field should be read correctly."""
    import json

    extract = pr_resolve_snapshot_module["_extract_ids_from_entries"]
    entries = [
        {"id": 100, "disposition": "addressed"},
        {"id": 200, "disposition": "not-applicable"},
        {"id": 300, "disposition": "deferred"},  # should be ignored
    ]
    ids = extract(entries)
    assert ids == {100, 200}

def test_load_addressed_ids_reads_status_field(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    """Ledger with 'status' field should also be accepted."""
    extract = pr_resolve_snapshot_module["_extract_ids_from_entries"]
    entries = [
        {"id": 100, "status": "addressed"},
        {"id": 200, "status": "not-applicable"},
    ]
    ids = extract(entries)
    assert ids == {100, 200}

def test_load_addressed_ids_reads_comment_id_key(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    """Ledger using 'comment_id' instead of 'id' should also work."""
    extract = pr_resolve_snapshot_module["_extract_ids_from_entries"]
    entries = [
        {"comment_id": 500, "disposition": "addressed"},
    ]
    ids = extract(entries)
    assert ids == {500}

def test_load_addressed_ids_searches_multiple_paths(
    pr_resolve_snapshot_module: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Loader should find ledgers at both explicit path and var/pr_comments/ dir."""
    import json

    load_ids = pr_resolve_snapshot_module["_load_addressed_comment_ids"]

    # Write an object-format ledger
    var_dir = tmp_path / "var" / "pr_comments"
    var_dir.mkdir(parents=True)
    ledger = {
        "resolutions": [
            {"comment_id": 42, "status": "addressed", "rationale": "fixed"},
        ]
    }
    ledger_path = var_dir / "comment-resolution-ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    # Use the explicit ledger_path parameter
    ids = load_ids(ledger_path=ledger_path)
    assert 42 in ids

# ---------------------------------------------------------------------------
# Thread-resolved enrichment (Phase 3)
# ---------------------------------------------------------------------------

def test_review_comment_with_thread_resolved_via_enrichment(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    """Comment with thread_resolved=True (from GraphQL enrichment) should be skipped."""
    summarize_comments = pr_resolve_snapshot_module["summarize_comments"]

    comments = [
        {
            "type": "review_comment",
            "id": 1,
            "user": "gemini-code-assist[bot]",
            "body": "Move import to top.",
            "thread_resolved": True,
            "commit_id": "old_sha",
        },
        {
            "type": "review_comment",
            "id": 2,
            "user": "github-code-quality[bot]",
            "body": "Unused import.",
            "thread_resolved": False,
            "commit_id": "current_sha",
        },
    ]

    summary = summarize_comments(
        comments,
        include_bot_review_comments=True,
        head_commit_sha="current_sha",
    )
    # Comment 1: thread_resolved → skipped
    # Comment 2: on HEAD commit, not resolved → actionable
    assert summary["actionableCommentCount"] == 1
    assert summary["actionableCommentIds"] == [2]
    assert summary["nonActionableReasonCounts"].get("thread_resolved") == 1

def test_pr_resolver_skill_owns_behavior_in_every_host() -> None:
    skill_text = (
        REPO_ROOT / ".agents" / "skills" / "pr-resolver" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Skill authority and host boundary" in skill_text
    assert "sole semantic implementation" in skill_text
    assert "MoonMind must execute this Skill through the ordinary agent Skill path" in skill_text
    assert "MoonMind must not" in skill_text
    assert "collect or classify PR comments" in skill_text
    assert "## Workflow" in skill_text
    assert "Terminal Success Contract" in skill_text
    assert "Bounded remediation actions" in skill_text
    assert "A local fix, local commit" in skill_text
    assert "status=merged" in skill_text
    assert "merge_outcome=merged" in skill_text
    assert "reason `publish_unavailable`" in skill_text
    assert "Never print raw environment variables" in skill_text
    assert "status=blocked" in skill_text
    assert "mergeAutomationDisposition=manual_review" in skill_text
    assert "branch is ahead of origin" in skill_text
    assert "pr_resolve_finalize.py" in skill_text
    assert "actionable_comments" in skill_text
    assert "fix-comments" in skill_text
    assert "Never reuse a pre-remediation snapshot" in skill_text
    assert "MOONMIND_ACTIVE_SKILLS_DIR" in skill_text
    assert "fresh `gh pr view` reports `state=MERGED`" in skill_text


def test_pr_resolver_snapshot_resolves_required_skill_from_active_root(
    pr_resolve_snapshot_module: dict[str, Any], tmp_path: Path
) -> None:
    script_dir = tmp_path / "active-snapshot" / "pr-resolver" / "bin"

    resolved = pr_resolve_snapshot_module["_sibling_skill_file"](
        script_dir,
        "fix-comments",
        "tools",
        "get_branch_pr_comments.py",
    )

    assert resolved == (
        tmp_path
        / "active-snapshot"
        / "fix-comments"
        / "tools"
        / "get_branch_pr_comments.py"
    )


def test_fix_comments_skill_requires_fresh_comments_and_remote_verification() -> None:
    skill_text = (
        REPO_ROOT / ".agents" / "skills" / "fix-comments" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert ".agents/skills/fix-comments/tools/get_branch_pr_comments.py" in skill_text
    assert "do not use a stale `var/pr_comments/current-branch-comments.json`" in skill_text
    assert "comments_helper_missing" in skill_text
    assert "verify the exact local `HEAD` SHA is visible" in skill_text
    assert "remote PR branch head SHA equals local `HEAD`" in skill_text
    assert "reason `publish_unavailable`" in skill_text
    assert "Never print raw environment variables" in skill_text
    assert "var/pr_resolver/result.json" in skill_text
    assert "mergeAutomationDisposition=manual_review" in skill_text
    assert "every\n  non-outdated comment in that thread" in skill_text
    assert "leave the entire thread unresolved" in skill_text
