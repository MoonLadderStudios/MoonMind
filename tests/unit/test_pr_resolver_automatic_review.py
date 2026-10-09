"""Automatic Codex completion evidence at the portable Skill boundary."""

from __future__ import annotations

import copy
import json
import runpy
from datetime import datetime
from pathlib import Path

import pytest

from pr_resolver_core import (
    ResolverAction,
    classify_snapshot,
    normalize_portable_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests/fixtures/pr_resolver/codex_automatic_review_4773.json"


@pytest.fixture
def captured():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def snapshot_module():
    return runpy.run_path(
        str(REPO_ROOT / ".agents/skills/pr-resolver/bin/pr_resolve_snapshot.py")
    )


def _evidence(snapshot_module, monkeypatch, captured, **overrides):
    build = snapshot_module["build_automated_review_evidence"]

    # Isolate the evidence decision from GitHub IO; this is the full SHA
    # returned by the repository's commit endpoint for the captured abbreviation.
    def resolve_commit(**kwargs):
        matches = {
            commit["sha"]
            for commit in captured["commits"]
            if commit["sha"].startswith(kwargs["commit_ref"])
        }
        return captured["head_sha"] if matches == {captured["head_sha"]} else None

    monkeypatch.setitem(
        build.__globals__, "_resolve_automatic_review_commit", resolve_commit
    )
    params = {
        "provider": "codex",
        "require_fresh_review": True,
        "pr_repo": captured["repository"],
        "pr_number": captured["pr_number"],
        "head_sha": captured["head_sha"],
        "head_committed_at": datetime.fromisoformat(
            captured["head_committed_at"].replace("Z", "+00:00")
        ),
        "comments": copy.deepcopy(captured["comments"]),
        "reviews": [],
        "reactions_for_request": [],
        "reactions_for_pr": copy.deepcopy(captured["reactions"]),
    }
    params.update(overrides)
    return build(**params)


def test_captured_pr4773_automatic_review_opens_the_current_head_gate(
    snapshot_module, monkeypatch, captured
):
    evidence = _evidence(snapshot_module, monkeypatch, captured)

    assert evidence["freshReviewForHead"] is True
    assert evidence["requestPending"] is False
    assert evidence["requestCommentId"] is None
    assert evidence["completionKind"] == "automatic_summary"
    assert evidence["completionId"] == 6082098611
    assert evidence["completedAt"] == "2026-10-09T13:45:42.886890+00:00"
    payload = {
        "repository": captured["repository"],
        "pr": {
            "number": captured["pr_number"],
            "state": "OPEN",
            "headRefOid": captured["head_sha"],
            "mergeStateStatus": "CLEAN",
            "mergeable": True,
        },
        "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
        "commentsFetch": {"succeeded": True},
        "commentsSummary": {
            "includeBotReviewComments": True,
            "hasActionableComments": False,
        },
        "automatedReview": evidence,
    }
    assert (
        classify_snapshot(normalize_portable_snapshot(payload)).action
        is ResolverAction.ATTEMPT_MERGE
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user", "operator"),
        ("user", "gemini-code-assist[bot]"),
        ("user", {"login": "chatgpt-codex-connector", "type": "User"}),
        ("user", {"login": "chatgpt-codex-connector[bot]", "type": "User"}),
        ("url", "https://github.com/other/repo/pull/4773#issuecomment-6082098611"),
        (
            "url",
            "https://github.com/MoonLadderStudios/MoonMind/pull/4772#issuecomment-6082098611",
        ),
        (
            "url",
            "https://github.com.evil.test/MoonLadderStudios/MoonMind/pull/4773#issuecomment-6082098611",
        ),
        ("url", None),
        ("id", 6082098612),
        ("created_at", "2026-10-09T13:45:50Z"),
        ("updated_at", None),
        ("updated_at", "2026-10-09T13:45:41Z"),
        ("updated_at", "2026-10-09T13:45:47Z"),
    ],
)
def test_unqualified_summary_metadata_cannot_complete(
    snapshot_module, monkeypatch, captured, field, value
):
    captured["comments"][0][field] = value
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("**Completed**", "**In Progress**"),
        ("**Completed**", "**Failed**"),
        ("7f81b48", "aaaaaaaa"),
        ("7f81b48", "7f81"),
        ("7f81b48", "7f81b48notasha"),
        ("13:45:42.886890Z</relative-time>", "13:45:43.886890Z</relative-time>"),
        ("13:45:42.886890Z", "13:45:42.886890"),
        ("<!-- codex-pull-request-review-summary -->", "Quoted example:"),
        ("📝 **Code Review**", "📝 **Task Execution**"),
    ],
)
def test_noncompletion_or_wrong_commit_is_rejected(
    snapshot_module, monkeypatch, captured, old, new
):
    summary = captured["comments"][0]
    summary["body"] = summary["body"].replace(old, new)
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


@pytest.mark.parametrize("form", ["quote", "indent", "fence", "P1", "P2", "unmarked"])
def test_embedded_or_finding_bearing_summary_is_not_clean(
    snapshot_module, monkeypatch, captured, form
):
    summary = captured["comments"][0]
    if form == "quote":
        summary["body"] = "\n".join(
            "> " + line for line in summary["body"].splitlines()
        )
    elif form == "indent":
        summary["body"] = "\n".join(
            "    " + line for line in summary["body"].splitlines()
        )
    elif form == "fence":
        fence = chr(96) * 3
        summary["body"] = fence + "html\n" + summary["body"] + "\n" + fence
    else:
        summary["body"] += "\n" + (
            f"[{form}] Fix the guard" if form != "unmarked" else "Fix the guard"
        )
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user", {"login": "operator", "type": "User"}),
        ("user", {"login": "gemini-code-assist[bot]"}),
        ("user", {"login": "chatgpt-codex-connector", "type": "User"}),
        ("content", "eyes"),
        ("content", "heart"),
        ("created_at", "2026-10-09T13:45:41Z"),
        ("created_at", "2026-10-09T13:45:43Z"),
        ("created_at", "2026-10-09T13:45:44Z"),
        ("created_at", None),
        ("id", None),
    ],
)
def test_unqualified_or_stale_reaction_cannot_complete(
    snapshot_module, monkeypatch, captured, field, value
):
    captured["reactions"][0][field] = value
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


def test_summary_without_no_findings_reaction_cannot_complete(
    snapshot_module, monkeypatch, captured
):
    assert (
        _evidence(snapshot_module, monkeypatch, captured, reactions_for_pr=[])[
            "freshReviewForHead"
        ]
        is False
    )


@pytest.mark.parametrize("when", ["2026-10-09T13:45:46Z", "2026-10-09T13:45:47Z"])
def test_newer_review_start_supersedes_old_thumb(
    snapshot_module, monkeypatch, captured, when
):
    captured["reactions"].append(
        {
            "id": 556689013,
            "content": "eyes",
            "created_at": when,
            "user": {"login": "chatgpt-codex-connector[bot]"},
        }
    )
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


def _feedback(captured, *, body, at, user="chatgpt-codex-connector[bot]"):
    return {
        "id": 6082098612,
        "type": "issue_comment",
        "user": user,
        "body": body,
        "created_at": at,
        "updated_at": at,
        "url": f"https://github.com/{captured['repository']}/pull/{captured['pr_number']}#issuecomment-6082098612",
    }


@pytest.mark.parametrize("change", ["started", "wrong_head", "malformed"])
def test_latest_summary_is_selected_before_qualification(
    snapshot_module, monkeypatch, captured, change
):
    newer = copy.deepcopy(captured["comments"][0])
    newer.update(_feedback(captured, body=newer["body"], at="2026-10-09T13:46:00Z"))
    if change == "started":
        newer["body"] = newer["body"].replace("**Completed**", "**In Progress**")
    elif change == "wrong_head":
        newer["body"] = newer["body"].replace("7f81b48", "aaaaaaaa")
    else:
        newer["body"] = newer["body"].replace("| Review |", "| Unknown |")
    captured["comments"].append(newer)
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


@pytest.mark.parametrize("edited", [False, True])
def test_later_provider_refusal_supersedes_automatic_completion(
    snapshot_module, monkeypatch, captured, edited
):
    refusal = _feedback(
        captured,
        body="You have reached your Codex usage limits for code reviews.",
        at="2026-10-09T13:45:47Z",
    )
    if edited:
        refusal["created_at"] = "2026-10-09T13:45:41Z"
    captured["comments"].append(refusal)
    evidence = _evidence(snapshot_module, monkeypatch, captured)
    assert evidence["freshReviewForHead"] is False
    assert evidence["requestFailed"] is True
    assert evidence["requestFailure"]["providerErrorClass"] == "rate_limit"
    assert evidence["requestFailure"]["failedAt"] == "2026-10-09T13:45:47+00:00"


def test_newer_completed_summary_can_supersede_earlier_refusal(
    snapshot_module, monkeypatch, captured
):
    captured["comments"].append(
        _feedback(
            captured,
            body="Codex usage limits have been reached for code reviews.",
            at="2026-10-09T13:41:00Z",
        )
    )
    evidence = _evidence(snapshot_module, monkeypatch, captured)
    assert evidence["freshReviewForHead"] is True
    assert evidence["requestFailed"] is False


def test_new_explicit_request_supersedes_automatic_completion(
    snapshot_module, monkeypatch, captured
):
    captured["comments"].append(
        _feedback(
            captured,
            body="@codex review",
            at="2026-10-09T13:45:47Z",
            user="operator",
        )
    )
    evidence = _evidence(snapshot_module, monkeypatch, captured)
    assert evidence["freshReviewForHead"] is False
    assert evidence["requestPending"] is True
    assert evidence["requestCommentId"] == 6082098612


@pytest.mark.parametrize(
    "variant", ["full_sha", "reused_comment", "same_second_update", "precise_update"]
)
def test_qualified_summary_variants_preserve_exact_head_completion(
    snapshot_module, monkeypatch, captured, variant
):
    if variant == "full_sha":
        captured["comments"][0]["body"] = captured["comments"][0]["body"].replace(
            "7f81b48", captured["head_sha"]
        )
    elif variant == "reused_comment":
        captured["comments"][0]["created_at"] = "2026-10-08T13:40:47Z"
    elif variant == "same_second_update":
        captured["comments"][0]["updated_at"] = "2026-10-09T13:45:42Z"
    else:
        captured["comments"][0]["updated_at"] = "2026-10-09T13:45:42.886890Z"
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is True
    )


@pytest.mark.parametrize("edited", [False, True])
def test_newer_unclassified_provider_feedback_cannot_reuse_old_completion(
    snapshot_module, monkeypatch, captured, edited
):
    feedback = _feedback(
        captured, body="The review is still running.", at="2026-10-09T13:45:47Z"
    )
    if edited:
        feedback["created_at"] = "2026-10-09T13:45:41Z"
    captured["comments"].append(feedback)
    evidence = _evidence(snapshot_module, monkeypatch, captured)
    assert evidence["freshReviewForHead"] is False
    assert evidence["requestFailed"] is False


def test_automatic_summary_requires_head_chronology(
    snapshot_module, monkeypatch, captured
):
    build = snapshot_module["build_automated_review_evidence"]
    monkeypatch.setitem(
        build.__globals__, "_fetch_head_commit_timestamp", lambda **kwargs: None
    )
    assert (
        _evidence(snapshot_module, monkeypatch, captured, head_committed_at=None)[
            "freshReviewForHead"
        ]
        is False
    )
    future = datetime.fromisoformat("2026-10-09T13:46:00+00:00")
    assert (
        _evidence(snapshot_module, monkeypatch, captured, head_committed_at=future)[
            "freshReviewForHead"
        ]
        is False
    )


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "ambiguous",
        "wrong_resolution",
        "wrong_repo",
        "wrong_pr",
        "changed_head",
        "incomplete",
        "malformed",
        "unavailable",
    ],
)
def test_abbreviated_commit_requires_authoritative_disambiguation(
    snapshot_module, monkeypatch, captured, change
):
    resolve = snapshot_module["_resolve_automatic_review_commit"]
    scope = resolve.__globals__
    pull = {
        "number": captured["pr_number"],
        "head": {"sha": captured["head_sha"]},
        "base": {"repo": {"full_name": captured["repository"]}},
        "commits": len(captured["commits"]),
    }
    commits = copy.deepcopy(captured["commits"])
    resolved = {"sha": captured["head_sha"]}
    if change == "ambiguous":
        commits.append({"sha": captured["head_sha"][:7] + "c" * 33})
        pull["commits"] += 1
    elif change == "wrong_resolution":
        resolved["sha"] = "b" * 40
    elif change == "wrong_repo":
        pull["base"]["repo"]["full_name"] = "other/repo"
    elif change == "wrong_pr":
        pull["number"] += 1
    elif change == "changed_head":
        pull["head"]["sha"] = "b" * 40
    elif change == "incomplete":
        pull["commits"] += 1
    elif change == "malformed":
        commits[0]["sha"] = "not-a-sha"

    def read(command, *_args, **_kwargs):
        if change == "unavailable":
            raise RuntimeError("GitHub evidence unavailable")
        return resolved if "/commits/" in command[-1] else pull

    monkeypatch.setitem(scope, "run_command", read)
    monkeypatch.setitem(scope, "_fetch_review_collection", lambda endpoint: commits)
    params = {
        "pr_repo": captured["repository"],
        "pr_number": captured["pr_number"],
        "commit_ref": captured["head_sha"][:7],
        "head_sha": captured["head_sha"],
    }
    if change in {"incomplete", "malformed", "unavailable"}:
        with pytest.raises(RuntimeError):
            resolve(**params)
    else:
        assert resolve(**params) == (captured["head_sha"] if change == "none" else None)


@pytest.mark.parametrize(
    "change",
    ["findings", "issue_findings", "head", "restart", "missing_inventory", "refusal"],
)
def test_snapshot_refreshes_automatic_completion_before_opening_gate(
    snapshot_module, monkeypatch, tmp_path, captured, change
):
    main = snapshot_module["main"]
    scope = main.__globals__
    head = captured["head_sha"]
    checks = [{"name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS"}]
    pr = {
        "number": captured["pr_number"],
        "state": "OPEN",
        "headRefOid": head,
        "mergeStateStatus": "CLEAN",
        "mergeable": True,
        "url": f"https://github.com/{captured['repository']}/pull/{captured['pr_number']}",
        "statusCheckRollup": checks,
    }
    pr_reads = []

    def fetch_pr(_selector):
        pr_reads.append(True)
        return (
            (
                {**pr, "headRefOid": "b" * 40}
                if change == "head" and len(pr_reads) > 1
                else pr
            ),
            str(pr["number"]),
            [],
        )

    monkeypatch.setitem(scope, "fetch_pr_data", fetch_pr)
    monkeypatch.setitem(
        scope, "_fetch_base_branch", lambda **kwargs: {"commit": {"sha": "base-head"}}
    )
    monkeypatch.setitem(scope, "_fetch_required_status_checks", lambda **kwargs: [])
    monkeypatch.setitem(scope, "_fetch_commit_check_runs", lambda **kwargs: checks)
    monkeypatch.setitem(scope, "_fetch_commit_statuses", lambda **kwargs: [])
    monkeypatch.setitem(scope, "_fetch_previous_commit_sha", lambda **kwargs: None)
    monkeypatch.setitem(
        scope,
        "_fetch_head_commit_timestamp",
        lambda **kwargs: datetime.fromisoformat(
            captured["head_committed_at"].replace("Z", "+00:00")
        ),
    )
    monkeypatch.setitem(
        scope, "_resolve_automatic_review_commit", lambda **kwargs: head
    )
    monkeypatch.setitem(
        scope, "_fetch_pr_reactions", lambda **kwargs: captured["reactions"]
    )
    reads = []

    def read_comments(*_args, **_kwargs):
        reads.append(True)
        if len(reads) > 1 and change == "missing_inventory":
            return {}
        comments = copy.deepcopy(captured["comments"])
        if len(reads) > 1:
            if change in {"findings", "issue_findings"}:
                finding = {
                    "id": 51,
                    "type": "review_comment",
                    "user": "chatgpt-codex-connector[bot]",
                    "body": "[P1] Fix the authorization check",
                    "commit_id": head,
                    "created_at": "2026-10-09T13:45:47Z",
                }
                if change == "issue_findings":
                    finding.update(
                        _feedback(
                            captured, body=finding["body"], at=finding["created_at"]
                        )
                    )
                comments.append(finding)
            elif change == "restart":
                comments[0]["body"] = comments[0]["body"].replace(
                    "**Completed**", "**In Progress**"
                )
                comments[0]["updated_at"] = "2026-10-09T13:45:47Z"
            elif change == "refusal":
                comments.append(
                    _feedback(
                        captured,
                        body="You have reached your Codex usage limits for code reviews.",
                        at="2026-10-09T13:45:47Z",
                    )
                )
        return {"comments": comments, "reviews": [], "thread_inventory_complete": True}

    monkeypatch.setitem(scope, "run_command", read_comments)
    path = tmp_path / "snapshot.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_snapshot.py",
            "--pr",
            str(pr["number"]),
            "--review-provider",
            "codex",
            "--require-fresh-review",
            "--snapshot-path",
            str(path),
        ],
    )
    if change == "head":
        with pytest.raises(SystemExit):
            main()
        assert not path.exists()
        return
    main()
    snapshot = json.loads(path.read_text())
    assert len(reads) >= 2
    decision = classify_snapshot(normalize_portable_snapshot(snapshot))
    assert decision.action is not ResolverAction.ATTEMPT_MERGE
    if change in {"findings", "issue_findings"}:
        assert snapshot["automatedReview"]["freshReviewForHead"] is True
        assert decision.remediation_skill == "fix-comments"
    elif change == "refusal":
        assert decision.reason_code == "automated_review_request_failed"
    elif change == "missing_inventory":
        assert decision.reason_code == "comments_unavailable"
    else:
        assert snapshot["automatedReview"]["freshReviewForHead"] is False
