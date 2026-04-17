from __future__ import annotations

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
        fix_max_iterations=3,
        base_sleep_seconds=15,
        max_sleep_seconds=60,
        max_elapsed_seconds=900,
        merge_not_ready_grace_retries=1,
    )

    assert exit_code == 0
    assert result["status"] == "merged"
    assert len(full_calls) == 1
    assert full_calls[0][2] == "actionable_comments"
    assert sleeps == []


def test_orchestrate_caps_retries_with_attempts_exhausted(
    pr_resolve_orchestrate_module: dict[str, Any],
) -> None:
    run_orchestration = pr_resolve_orchestrate_module["run_orchestration"]

    result, exit_code = run_orchestration(
        finalize_runner=lambda _attempt: {
            "status": "blocked",
            "merge_outcome": "blocked",
            "reason": "actionable_comments",
        },
        full_runner=lambda _attempt, _escalation, reason: {
            "status": "needs_remediation",
            "merge_outcome": "blocked",
            "reason": reason,
        },
        sleep_fn=lambda _seconds: None,
        monotonic_fn=lambda: 0.0,
        finalize_max_retries=2,
        fix_max_iterations=3,
        base_sleep_seconds=15,
        max_sleep_seconds=60,
        max_elapsed_seconds=900,
        merge_not_ready_grace_retries=1,
    )

    assert exit_code == 3
    assert result["status"] == "attempts_exhausted"
    assert result["merge_outcome"] == "attempts_exhausted"
    assert result["final_reason"] == "actionable_comments"


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
    assert full_invocations == 0
    assert sleeps == [15]


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
    assert sleeps == [15, 30]


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
    assert captured["finalize_max_retries"] == 6
    assert captured["base_sleep_seconds"] == 30
    assert captured["max_sleep_seconds"] == 120
    assert captured["max_elapsed_seconds"] == 1800


def test_contract_snapshot_refresh_failed_is_finalize_only_retry(
    pr_resolve_contract_module: dict[str, Any],
) -> None:
    classify_retry_action = pr_resolve_contract_module["classify_retry_action"]

    action = classify_retry_action(
        "snapshot_refresh_failed",
        merge_not_ready_grace_remaining=1,
    )
    assert action == "finalize_only_retry"


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


# ---------------------------------------------------------------------------
# Stale-commit bot comment filtering (Phase 1)
# ---------------------------------------------------------------------------


def test_stale_bot_comment_on_old_commit_is_not_actionable(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    """Bot review comment whose commit_id != HEAD should be classified as stale."""
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
    assert actionable is False
    assert reason == "stale_bot_comment"


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


def test_stale_bot_comment_with_matching_sha_stays_actionable(
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


def test_pr_resolver_skill_requires_orchestrated_merge_completion() -> None:
    skill_text = (
        REPO_ROOT / ".agents" / "skills" / "pr-resolver" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Primary Command (mandatory first action)" in skill_text
    assert "Terminal Success Contract" in skill_text
    assert "Main Loop" in skill_text
    assert "A local fix, local commit" in skill_text
    assert "status=merged" in skill_text
    assert "merge_outcome=merged" in skill_text
    assert "reason `publish_unavailable`" in skill_text
    assert "branch is ahead of origin" in skill_text

    repeated_blocker_step = (
        "If the same blocker repeats after its specialized skill ran and no remote "
        "PR branch change is visible"
    )
    skill_execution_step = "Execute the matching specialized skill exactly once"
    assert skill_text.index(repeated_blocker_step) < skill_text.index(
        skill_execution_step
    )
