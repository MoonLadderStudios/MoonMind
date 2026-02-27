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


def test_finalize_enables_auto_merge_when_ci_running_and_comments_addressed(
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

    assert decision == {"action": "enable_auto_merge", "reason": "ci_running"}


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
