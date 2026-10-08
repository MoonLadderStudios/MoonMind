"""Every current finding needs a disposition, regardless of its priority."""

from __future__ import annotations

import json
import runpy
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pr_resolver_core import (
    ResolverAction,
    classify_snapshot,
    normalize_portable_snapshot,
)
from pr_resolver_core.review_providers import (
    automated_review_provider_or_raise,
    is_clean_review_comment,
)

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / ".agents" / "skills"
HEAD = "a" * 40
PROVIDER_LOGIN = "chatgpt-codex-connector[bot]"
CLEAN_BODY = "Codex Review: Didn't find any major issues. 🚀"


@pytest.fixture
def snapshot_module():
    return runpy.run_path(str(SKILLS / "pr-resolver/bin/pr_resolve_snapshot.py"))


def _finding(kind="review_comment", body="[P2] Preserve the pending retry.", **extra):
    return {
        "id": 51,
        "type": kind,
        "user": PROVIDER_LOGIN,
        "body": body,
        "commit_id": HEAD,
        "created_at": "2026-08-24T22:19:00Z",
        **extra,
    }


@pytest.mark.parametrize("kind", ["review_comment", "issue_comment", "review"])
@pytest.mark.parametrize(
    "body",
    [
        "[P2] Preserve the pending retry.",
        "[P3] Explain the fallback.",
        "severity: low\nReport the missing value.",
        "priority: medium\nKeep the result visible.",
        "[P1] Preserve the saved candidate.",
    ],
)
def test_current_provider_findings_remain_actionable(snapshot_module, kind, body):
    summary = snapshot_module["summarize_comments"]([_finding(kind, body)])

    assert summary["actionableCommentIds"] == [51]
    assert summary["classifiedComments"][0]["reason"] == "actionable"


@pytest.mark.parametrize("state", ["thread_resolved", "thread_outdated"])
def test_explicit_thread_state_still_handles_low_priority_findings(
    snapshot_module, state
):
    summary = snapshot_module["summarize_comments"]([_finding(**{state: True})])

    assert summary["actionableCommentIds"] == []
    assert summary["nonActionableReasonCounts"] == {state: 1}


def test_old_commit_alone_does_not_dispose_of_a_current_thread(snapshot_module):
    summary = snapshot_module["summarize_comments"](
        [_finding(commit_id="b" * 40)], head_commit_sha=HEAD
    )

    assert summary["actionableCommentIds"] == [51]


def test_explicit_inline_bot_exclusion_remains_supported(snapshot_module):
    summary = snapshot_module["summarize_comments"](
        [_finding()], include_bot_review_comments=False
    )

    assert summary["nonActionableReasonCounts"] == {"bot_review_comment_excluded": 1}


def test_unrelated_bot_discussion_remains_excluded(snapshot_module):
    summary = snapshot_module["summarize_comments"](
        [_finding("issue_comment", "Build uploaded.", user="github-actions[bot]")]
    )

    assert summary["actionableCommentIds"] == []
    assert summary["nonActionableReasonCounts"] == {"bot_comment_excluded": 1}


def test_unmarked_provider_status_does_not_become_a_finding(snapshot_module):
    summary = snapshot_module["summarize_comments"](
        [_finding("issue_comment", "Review completed without nits.")]
    )

    assert summary["actionableCommentIds"] == []
    assert summary["nonActionableReasonCounts"] == {"bot_comment_excluded": 1}


@pytest.mark.parametrize("disposition", ["addressed", "not-applicable", "deferred"])
@pytest.mark.parametrize("kind", ["review_comment", "issue_comment", "review"])
def test_existing_ledger_dispositions_apply_without_hiding_open_threads(
    snapshot_module, monkeypatch, tmp_path, disposition, kind
):
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / "artifacts/pr_resolver_addressed_comments.json"
    ledger.parent.mkdir()
    ledger.write_text(
        json.dumps(
            [
                {
                    "id": 51,
                    "disposition": disposition,
                    "rationale": "Checked current code.",
                }
            ]
        )
    )
    summary = snapshot_module["summarize_comments"](
        [_finding(kind)],
        addressed_comment_ids=snapshot_module["_load_addressed_comment_ids"](),
        deferred_comment_ids=snapshot_module["_load_deferred_comment_ids"](),
    )

    still_actionable = kind == "review_comment" or disposition == "deferred"
    assert summary["actionableCommentIds"] == ([51] if still_actionable else [])
    assert summary["deferredCommentIds"] == ([51] if disposition == "deferred" else [])
    if not still_actionable:
        assert summary["nonActionableReasonCounts"] == {"addressed_in_ledger": 1}


@pytest.mark.parametrize(
    "suffix",
    [
        "\n\n[P2] Preserve the pending retry.",
        "\n\nseverity: low\nDocument the retry condition.",
        "\n\n<details><summary>ℹ️ About Codex in GitHub</summary>Help</details>\n[P3] Keep the result.",
        "\n\n<details><summary>ℹ️ About Codex in GitHub</summary>[P2] Keep the result.</details>",
    ],
)
def test_clean_phrase_cannot_hide_trailing_findings(snapshot_module, suffix):
    comment = _finding("issue_comment", CLEAN_BODY + suffix)

    assert not is_clean_review_comment(
        automated_review_provider_or_raise("codex"),
        comment,
        requested_at=datetime(2026, 8, 24, 22, 15, tzinfo=UTC),
        head_sha=HEAD,
    )
    assert snapshot_module["summarize_comments"]([comment])["actionableCommentIds"] == [
        51
    ]


@pytest.mark.parametrize(
    "body",
    [
        CLEAN_BODY,
        (
            "### **Codex Review:** Didn't find any major issues. 🚀\n\n"
            "<details><summary>ℹ️ About Codex in GitHub</summary>Help</details>"
        ),
    ],
)
def test_exact_supported_clean_body_remains_completion_evidence(body):
    assert is_clean_review_comment(
        automated_review_provider_or_raise("codex"),
        _finding("issue_comment", body),
        requested_at=datetime(2026, 8, 24, 22, 15, tzinfo=UTC),
        head_sha=HEAD,
    )


@pytest.mark.parametrize("transient_failure", [False, True])
def test_branch_helper_stops_retrying_after_success(monkeypatch, transient_failure):
    module = runpy.run_path(
        str(SKILLS / "fix-comments/tools/get_branch_pr_comments.py")
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if transient_failure and len(calls) == 1:
            return subprocess.CompletedProcess(command, 1, "", "connection reset")
        return subprocess.CompletedProcess(command, 0, '{"number":350}', "")

    monkeypatch.setattr(module["subprocess"], "run", run)
    monkeypatch.setattr(module["time"], "sleep", lambda _: None)

    assert module["run_json_command"](["gh", "pr", "view"], "Failed") == {"number": 350}
    assert len(calls) == (2 if transient_failure else 1)


@pytest.mark.parametrize("kind", ["review_comment", "issue_comment", "review"])
@pytest.mark.parametrize("completion_kind", ["issue_comment", "review", "reaction"])
def test_independent_clean_completion_cannot_erase_a_finding(
    snapshot_module, kind, completion_kind
):
    comments = [
        _finding(
            "issue_comment",
            "@codex review",
            id=1,
            user="owner",
            created_at="2026-08-24T22:15:00Z",
        ),
        _finding(kind),
    ]
    reviews = []
    reactions = []
    if completion_kind == "issue_comment":
        comments.append(_finding("issue_comment", CLEAN_BODY, id=2))
    elif completion_kind == "review":
        reviews.append(
            {
                "id": 2,
                "user": {"login": PROVIDER_LOGIN},
                "body": "",
                "state": "COMMENTED",
                "commit_id": HEAD,
                "submitted_at": "2026-08-24T22:20:00Z",
            }
        )
    else:
        reactions.append(
            {
                "id": 2,
                "user": {"login": PROVIDER_LOGIN},
                "content": "+1",
                "created_at": "2026-08-24T22:20:00Z",
            }
        )
    evidence = snapshot_module["build_automated_review_evidence"](
        provider="codex",
        require_fresh_review=True,
        pr_repo="owner/repo",
        pr_number=350,
        head_sha=HEAD,
        comments=comments,
        reviews=reviews,
        head_committed_at=datetime(2026, 8, 24, 22, 10, tzinfo=UTC),
        reactions_for_request=reactions,
        reactions_for_pr=[],
    )
    summary = snapshot_module["summarize_comments"](comments)
    decision = classify_snapshot(
        normalize_portable_snapshot(
            {
                "pr": {
                    "state": "OPEN",
                    "headRefOid": HEAD,
                    "mergeable": True,
                    "mergeStateStatus": "CLEAN",
                },
                "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
                "commentsFetch": {"succeeded": True},
                "commentsSummary": summary,
                "automatedReview": evidence,
            }
        )
    )

    assert evidence["freshReviewForHead"] is True
    assert evidence["completionKind"] == completion_kind
    assert summary["actionableCommentIds"] == [51]
    assert decision.action is ResolverAction.RUN_REMEDIATION
    assert decision.remediation_skill == "fix-comments"


@pytest.mark.parametrize("flag", [None, "--include-empty-reviews", "--exclude-reviews"])
def test_comment_helper_reuses_complete_raw_reviews_before_inventory(
    monkeypatch, capsys, flag
):
    module = runpy.run_path(str(SKILLS / "fix-comments/tools/get_pr_comments.py"))
    main = module["main"]
    scope = main.__globals__
    reviews = [
        {
            "id": 11,
            "body": "",
            "state": "COMMENTED",
            "commit_id": HEAD,
            "submitted_at": "2026-08-24T22:19:00Z",
            "user": {"login": PROVIDER_LOGIN},
        },
        {
            "id": 12,
            "body": "[P2] Keep the result.",
            "state": "COMMENTED",
            "commit_id": HEAD,
            "submitted_at": "2026-08-24T22:20:00Z",
            "user": {"login": PROVIDER_LOGIN},
        },
    ]
    reads = []

    def fetch(endpoint, _token):
        kind = (
            "reviews"
            if endpoint.endswith("/reviews")
            else ("issue_comments" if "/issues/" in endpoint else "inline_comments")
        )
        reads.append(kind)
        return reviews if kind == "reviews" else []

    def threads(*_args):
        reads.append("threads")
        return {}, False

    monkeypatch.setitem(scope, "resolve_token", lambda _: "test-token")
    monkeypatch.setitem(scope, "api_get_json", lambda *_: {"title": "Test"})
    monkeypatch.setitem(scope, "fetch_paginated", fetch)
    monkeypatch.setitem(scope, "fetch_review_thread_status", threads)
    monkeypatch.setattr(
        "sys.argv",
        [
            "get_pr_comments.py",
            "350",
            "--repo",
            "owner/repo",
            *([flag] if flag else []),
        ],
    )
    main()
    result = json.loads(capsys.readouterr().out)

    included = flag != "--exclude-reviews"
    assert result["reviews"] == (reviews if included else [])
    assert reads == (["reviews"] if included else []) + [
        "issue_comments",
        "inline_comments",
        "threads",
    ]
    assert result["review_evidence_precedes_inventory"] is included
    assert result["thread_inventory_complete"] is False
    expected_ids = [] if not included else ([11, 12] if flag else [12])
    assert [comment["id"] for comment in result["comments"]] == expected_ids
    assert result["comment_count"] == len(expected_ids)


@pytest.mark.parametrize("included", [True, False])
def test_branch_helper_preserves_raw_reviews_and_collection_order_flag(
    monkeypatch, capsys, included
):
    module = runpy.run_path(
        str(SKILLS / "fix-comments/tools/get_branch_pr_comments.py")
    )
    main = module["main"]
    scope = main.__globals__
    reviews = (
        [{"id": 11, "body": "", "user": {"login": PROVIDER_LOGIN}}] if included else []
    )
    payload = {
        "reviews": reviews,
        "review_evidence_precedes_inventory": included,
        "comments": [],
        "comment_count": 0,
        "thread_inventory_complete": False,
    }
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        return payload

    monkeypatch.setitem(scope, "detect_current_branch", lambda: "feature/test")
    monkeypatch.setitem(scope, "resolve_pr_metadata", lambda _: {"number": 350})
    monkeypatch.setitem(scope, "fetch_comments", fetch)
    monkeypatch.setattr(
        "sys.argv",
        ["get_branch_pr_comments.py", *([] if included else ["--exclude-reviews"])],
    )
    assert main() == 0
    result = json.loads(capsys.readouterr().out)

    assert result["reviews"] == reviews
    assert result["review_evidence_precedes_inventory"] is included
    assert result["thread_inventory_complete"] is False
    assert len(calls) == 1
    assert calls[0]["exclude_reviews"] is (not included)
