"""Portable Skill tests for the pr-resolver automated review loop."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pr_resolver_core import (
    ResolverAction,
    classify_snapshot,
    normalize_portable_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HEAD = "a" * 40
OLD_HEAD = "b" * 40
HEAD_COMMITTED_AT = datetime(2026, 8, 24, 22, 10, tzinfo=UTC)
CODEX_LOGIN = "chatgpt-codex-connector[bot]"
# Codex refusing a requested review, verbatim: MoonLadderStudios/Tactics#2788
# (comment 6025386732, nine seconds after `@codex review` comment 6025384173)
# and MoonLadderStudios/MoonMind#4648 (comment 5928614765).
CODEX_USAGE_LIMIT_REPLY = (
    "You have reached your Codex usage limits for code reviews. You can see your "
    "limits in the [Codex usage dashboard]"
    "(https://chatgpt.com/codex/cloud/settings/usage).\n"
    "To continue using code reviews, add credits to your account and enable them "
    "for code reviews in your "
    "[settings](https://chatgpt.com/codex/cloud/settings/code-review)."
)
CODEX_REPO_USAGE_LIMIT_REPLY = (
    "Codex usage limits have been reached for code reviews. Please check with the "
    "admins of this repo to increase the limits by adding credits.\n"
    "Repo admins can enable using credits for code reviews in their "
    "[settings](https://chatgpt.com/codex/cloud/settings/code-review)."
)


def _load_module(script_path: Path) -> dict[str, Any]:
    import runpy

    return runpy.run_path(str(script_path))


@pytest.fixture
def snapshot_module() -> dict[str, Any]:
    return _load_module(
        REPO_ROOT / ".agents" / "skills" / "pr-resolver" / "bin" / "pr_resolve_snapshot.py"
    )


@pytest.fixture
def contract_module() -> dict[str, Any]:
    return _load_module(
        REPO_ROOT / ".agents" / "skills" / "pr-resolver" / "bin" / "pr_resolve_contract.py"
    )


def _request_comment(created_at: str = "2026-08-24T22:15:00Z") -> dict[str, Any]:
    return {
        "id": 98765,
        "type": "issue_comment",
        "user": "moonmind-bot",
        "body": "@codex review",
        "created_at": created_at,
    }


def _evidence(snapshot_module, **kwargs: Any) -> dict[str, Any]:
    build = snapshot_module["build_automated_review_evidence"]
    params: dict[str, Any] = {
        "provider": "codex",
        "require_fresh_review": True,
        "pr_repo": "MoonLadderStudios/MoonMind",
        "pr_number": 350,
        "head_sha": HEAD,
        "comments": [],
        "reviews": [],
        "head_committed_at": HEAD_COMMITTED_AT,
        "reactions_for_request": [],
        "reactions_for_pr": [],
    }
    params.update(kwargs)
    return build(**params)


# ---------------------------------------------------------------------------
# snapshot evidence
# ---------------------------------------------------------------------------


def test_disabled_provider_disables_the_review_loop(snapshot_module) -> None:
    evidence = _evidence(snapshot_module, provider="none")
    assert evidence["enabled"] is False

    evidence = _evidence(snapshot_module, require_fresh_review=False)
    assert evidence["enabled"] is False


def test_no_request_and_no_review_requires_a_request(snapshot_module) -> None:
    evidence = _evidence(snapshot_module)

    assert evidence["enabled"] is True
    assert evidence["provider"] == "codex"
    assert evidence["command"] == "@codex review"
    assert evidence["freshReviewForHead"] is False
    assert evidence["requestPending"] is False


def test_request_after_head_commit_is_pending(snapshot_module) -> None:
    evidence = _evidence(snapshot_module, comments=[_request_comment()])

    assert evidence["requestPending"] is True
    assert evidence["requestCommentId"] == 98765
    assert evidence["freshReviewForHead"] is False


def test_request_before_head_commit_does_not_cover_the_head(snapshot_module) -> None:
    evidence = _evidence(
        snapshot_module,
        comments=[_request_comment(created_at="2026-08-24T20:00:00Z")],
    )

    assert evidence["requestPending"] is False
    assert evidence["requestCommentId"] is None


def test_review_for_head_commit_is_fresh(snapshot_module) -> None:
    evidence = _evidence(
        snapshot_module,
        comments=[_request_comment()],
        reviews=[
            {
                "id": 45678,
                "commit_id": HEAD,
                "submitted_at": "2026-08-24T22:19:00Z",
                "state": "COMMENTED",
                "user": {"login": "chatgpt-codex-connector"},
            }
        ],
    )

    assert evidence["freshReviewForHead"] is True
    assert evidence["requestPending"] is False
    assert evidence["completionKind"] == "review"
    assert evidence["completionId"] == 45678


def test_review_for_an_older_commit_is_not_fresh(snapshot_module) -> None:
    evidence = _evidence(
        snapshot_module,
        comments=[_request_comment()],
        reviews=[
            {
                "id": 1,
                "commit_id": OLD_HEAD,
                "submitted_at": "2026-08-24T22:19:00Z",
                "state": "COMMENTED",
                "user": {"login": "chatgpt-codex-connector"},
            }
        ],
    )

    assert evidence["freshReviewForHead"] is False
    assert evidence["requestPending"] is True


def test_review_from_another_identity_is_ignored(snapshot_module) -> None:
    evidence = _evidence(
        snapshot_module,
        comments=[_request_comment()],
        reviews=[
            {
                "id": 2,
                "commit_id": HEAD,
                "submitted_at": "2026-08-24T22:19:00Z",
                "state": "APPROVED",
                "user": {"login": "gemini-code-assist"},
            }
        ],
    )

    assert evidence["freshReviewForHead"] is False


def test_clean_review_reaction_on_request_comment_is_fresh(snapshot_module) -> None:
    evidence = _evidence(
        snapshot_module,
        comments=[_request_comment()],
        reactions_for_request=[
            {
                "id": 55,
                "content": "+1",
                "created_at": "2026-08-24T22:20:00Z",
                "user": {"login": "chatgpt-codex-connector[bot]"},
            }
        ],
    )

    assert evidence["freshReviewForHead"] is True
    assert evidence["completionKind"] == "reaction"
    assert evidence["completionId"] == 55


def test_progress_signature_is_stable_and_head_sensitive(snapshot_module) -> None:
    build = snapshot_module["build_progress_signature"]
    summary = {"actionableCommentIds": [2, 1], "deferredCommentIds": [9]}

    assert build(head_sha=HEAD, comments_summary=summary) == build(
        head_sha=HEAD, comments_summary={"actionableCommentIds": [1, 2], "deferredCommentIds": [9]}
    )
    assert build(head_sha=HEAD, comments_summary=summary) != build(
        head_sha=OLD_HEAD, comments_summary=summary
    )


def test_deferred_ledger_entries_surface_in_the_summary(snapshot_module, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / "artifacts" / "pr_resolver_addressed_comments.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            [
                {"id": 1, "disposition": "addressed"},
                {"id": 2, "disposition": "deferred"},
            ]
        ),
        encoding="utf-8",
    )

    addressed = snapshot_module["_load_addressed_comment_ids"]()
    deferred = snapshot_module["_load_deferred_comment_ids"]()
    assert addressed == {1}
    assert deferred == {2}

    summary = snapshot_module["summarize_comments"](
        [
            {"id": 1, "type": "issue_comment", "user": "human", "body": "fix this"},
            {"id": 2, "type": "issue_comment", "user": "human", "body": "and this"},
        ],
        addressed_comment_ids=addressed,
        deferred_comment_ids=deferred,
        head_commit_sha=HEAD,
    )
    assert summary["deferredCommentIds"] == [2]
    assert summary["hasDeferredComments"] is True


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


def _snapshot(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "repository": "MoonLadderStudios/MoonMind",
        "pr": {
            "number": 350,
            "state": "OPEN",
            "headRefOid": HEAD,
            "mergeStateStatus": "CLEAN",
            "mergeable": True,
        },
        "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
        "commentsFetch": {"succeeded": True},
        "commentsSummary": {
            "includeBotReviewComments": True,
            "hasActionableComments": False,
        },
        "automatedReview": {
            "enabled": True,
            "provider": "codex",
            "freshReviewForHead": False,
            "requestPending": False,
        },
        "progressSignature": f"{HEAD}||",
    }
    payload.update(overrides)
    return payload


def test_missing_fresh_review_requests_one() -> None:
    decision = classify_snapshot(normalize_portable_snapshot(_snapshot()))

    assert decision.action is ResolverAction.REQUEST_REVIEW
    assert decision.reason_code == "fresh_review_required_after_remediation"


def test_pending_request_waits_instead_of_requesting_again() -> None:
    snapshot = _snapshot()
    snapshot["automatedReview"]["requestPending"] = True

    decision = classify_snapshot(normalize_portable_snapshot(snapshot))

    assert decision.action is ResolverAction.WAIT
    assert decision.reason_code == "automated_review_wait"


@pytest.mark.parametrize("blocker", ["comments", "ci", "conflict"])
def test_pending_review_precedes_every_remediation(blocker) -> None:
    snapshot = _snapshot()
    snapshot["automatedReview"]["requestPending"] = True
    if blocker == "comments":
        snapshot["commentsSummary"]["hasActionableComments"] = True
    elif blocker == "ci":
        snapshot["ci"]["hasFailures"] = True
    else:
        snapshot["pr"]["mergeable"] = False
    decision = classify_snapshot(normalize_portable_snapshot(snapshot))
    assert decision.action is ResolverAction.WAIT
    assert decision.reason_code == "automated_review_wait"


@pytest.mark.parametrize("blocker", ["none", "comments", "ci", "conflict"])
def test_failed_request_stops_instead_of_waiting_or_remediating(blocker) -> None:
    snapshot = _snapshot()
    snapshot["automatedReview"]["requestFailed"] = True
    if blocker == "comments":
        snapshot["commentsSummary"]["hasActionableComments"] = True
    elif blocker == "ci":
        snapshot["ci"]["hasFailures"] = True
    elif blocker == "conflict":
        snapshot["pr"]["mergeable"] = False

    decision = classify_snapshot(normalize_portable_snapshot(snapshot))

    assert decision.action is ResolverAction.STOP_MANUAL_REVIEW
    assert decision.reason_code == "automated_review_request_failed"


@pytest.mark.parametrize("kind", ["issue_comment", "pr_reaction"])
def test_clean_response_finishes_without_requesting_another_review(
    snapshot_module, kind
) -> None:
    comment = {
        "id": 56,
        "type": "issue_comment",
        "user": "chatgpt-codex-connector[bot]",
        "body": "**Codex Review:** Didn't find any major issues. 🚀",
        "created_at": "2026-08-24T22:20:00Z",
    }
    params = (
        {"comments": [_request_comment(), comment]}
        if kind == "issue_comment"
        else {
            "comments": [_request_comment()],
            "reactions_for_pr": [
                {
                    "id": 57,
                    "content": "+1",
                    "created_at": "2026-08-24T22:20:00Z",
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                }
            ],
        }
    )
    evidence = _evidence(snapshot_module, **params)
    assert evidence["freshReviewForHead"] is True
    assert evidence["requestPending"] is False
    snapshot = _snapshot(automatedReview=evidence)
    assert (
        classify_snapshot(normalize_portable_snapshot(snapshot)).action
        is ResolverAction.ATTEMPT_MERGE
    )


@pytest.mark.parametrize(
    "change", ["old", "human", "quoted", "findings", "no_request", "old_head"]
)
def test_clean_comment_cannot_complete_an_unrelated_review(
    snapshot_module, change
) -> None:
    comment = {
        "id": 56,
        "type": "issue_comment",
        "user": "chatgpt-codex-connector[bot]",
        "body": "Codex Review: Didn't find any major issues. 🚀",
        "created_at": "2026-08-24T22:20:00Z",
    }
    request = _request_comment()
    if change == "old":
        comment["created_at"] = "2026-08-24T22:14:00Z"
    elif change == "human":
        comment["user"] = "human"
    elif change == "quoted":
        comment["body"] = "> " + comment["body"]
    elif change == "findings":
        comment["body"] += "\n\n[P1] Fix the authorization bug."
    elif change == "old_head":
        comment["commit_id"] = OLD_HEAD
    comments = [comment] if change == "no_request" else [request, comment]
    assert _evidence(snapshot_module, comments=comments)["freshReviewForHead"] is False


@pytest.mark.parametrize("state", ["PENDING", "DISMISSED", "", "FUTURE_STATE"])
def test_unsubmitted_or_unknown_review_is_not_completion(snapshot_module, state):
    evidence = _evidence(
        snapshot_module,
        comments=[_request_comment()],
        reviews=[
            {
                "id": 1,
                "state": state,
                "commit_id": HEAD,
                "submitted_at": "2026-08-24T22:19:00Z",
                "user": {"login": "chatgpt-codex-connector"},
            }
        ],
    )
    assert evidence["freshReviewForHead"] is False
    assert evidence["requestPending"] is True


@pytest.mark.parametrize(
    "reaction",
    [
        {
            "content": "eyes",
            "created_at": "2026-08-24T22:20:00Z",
            "user": {"login": "chatgpt-codex-connector"},
        },
        {
            "content": "+1",
            "created_at": "2026-08-24T22:14:00Z",
            "user": {"login": "chatgpt-codex-connector"},
        },
        {
            "content": "+1",
            "created_at": "2026-08-24T22:20:00Z",
            "user": {"login": "human"},
        },
    ],
)
def test_pr_reaction_requires_provider_clean_completion_after_request(
    snapshot_module, reaction
):
    evidence = _evidence(
        snapshot_module, comments=[_request_comment()], reactions_for_pr=[reaction]
    )
    assert evidence["freshReviewForHead"] is False


def _codex_reply(
    body: str, *, created_at: str = "2026-08-24T22:20:00Z", comment_id: int = 77
) -> dict[str, Any]:
    return {
        "id": comment_id,
        "type": "issue_comment",
        "user": CODEX_LOGIN,
        "body": body,
        "created_at": created_at,
    }


@pytest.mark.parametrize(
    "body", [CODEX_USAGE_LIMIT_REPLY, CODEX_REPO_USAGE_LIMIT_REPLY]
)
def test_provider_usage_limit_reply_fails_the_request(snapshot_module, body) -> None:
    request = _request_comment(created_at="2026-10-06T21:02:09Z")
    request["id"] = 6025384173
    reply = _codex_reply(
        body, created_at="2026-10-06T21:02:18Z", comment_id=6025386732
    )

    evidence = _evidence(snapshot_module, comments=[request, reply])

    assert evidence["freshReviewForHead"] is False
    assert evidence["requestPending"] is False
    assert evidence["requestFailed"] is True
    assert evidence["requestFailure"] == {
        "kind": "issue_comment",
        "id": 6025386732,
        "failedAt": "2026-10-06T21:02:18Z",
        "providerErrorClass": "rate_limit",
    }
    decision = classify_snapshot(
        normalize_portable_snapshot(_snapshot(automatedReview=evidence))
    )
    assert decision.action is ResolverAction.STOP_MANUAL_REVIEW
    assert decision.reason_code == "automated_review_request_failed"


@pytest.mark.parametrize("latest", ["clean", "failure"])
def test_latest_provider_reply_decides_the_request(snapshot_module, latest) -> None:
    clean = _codex_reply(
        "Codex Review: Didn't find any major issues. 🚀",
        created_at=(
            "2026-08-24T22:20:00Z" if latest == "clean" else "2026-08-24T22:19:00Z"
        ),
        comment_id=56,
    )
    failure = _codex_reply(
        CODEX_USAGE_LIMIT_REPLY,
        created_at=(
            "2026-08-24T22:20:00Z" if latest == "failure" else "2026-08-24T22:19:00Z"
        ),
        comment_id=55,
    )

    evidence = _evidence(snapshot_module, comments=[_request_comment(), clean, failure])

    assert evidence["freshReviewForHead"] is (latest == "clean")
    assert evidence["requestFailed"] is (latest == "failure")
    assert evidence["requestPending"] is False


@pytest.mark.parametrize(
    "change", ["before_request", "human", "old_head", "task_summary", "status_summary"]
)
def test_unrelated_reply_keeps_the_request_pending(snapshot_module, change) -> None:
    reply = _codex_reply(CODEX_USAGE_LIMIT_REPLY)
    if change == "before_request":
        reply["created_at"] = "2026-08-24T22:14:00Z"
    elif change == "human":
        reply["user"] = "nsticco"
    elif change == "old_head":
        reply["commit_id"] = OLD_HEAD
    elif change == "task_summary":
        reply["body"] = (
            "### Summary\n* Retried GitHub calls that hit the API rate limit and "
            "surfaced usage limits in the dashboard."
        )
    else:
        # The provider's own status comment carries short SHAs and timestamps;
        # digits such as `4290` are not an HTTP status.
        reply["body"] = (
            "<!-- codex-pull-request-review-summary -->\n\n## Codex Review Summary\n\n"
            "| Review | Status | Commit | Review trigger |\n| --- | --- | --- | --- |\n"
            "| 📝 **Code Review** | ⏳ **Running** | `4290abc` | Manual request |"
        )

    evidence = _evidence(snapshot_module, comments=[_request_comment(), reply])

    assert evidence["requestPending"] is True
    assert evidence["requestFailed"] is False
    assert evidence["requestFailure"] is None


def test_clean_reaction_completes_despite_a_failure_reply(snapshot_module) -> None:
    evidence = _evidence(
        snapshot_module,
        comments=[_request_comment(), _codex_reply(CODEX_USAGE_LIMIT_REPLY)],
        reactions_for_request=[
            {
                "id": 58,
                "content": "+1",
                "created_at": "2026-08-24T22:21:00Z",
                "user": {"login": CODEX_LOGIN},
            }
        ],
    )

    assert evidence["freshReviewForHead"] is True
    assert evidence["requestFailed"] is False


def test_new_request_after_a_failure_is_pending(snapshot_module) -> None:
    retried = _request_comment(created_at="2026-08-24T23:00:00Z")
    retried["id"] = 98766

    evidence = _evidence(
        snapshot_module,
        comments=[_request_comment(), _codex_reply(CODEX_USAGE_LIMIT_REPLY), retried],
    )

    assert evidence["requestCommentId"] == 98766
    assert evidence["requestPending"] is True
    assert evidence["requestFailed"] is False


@pytest.mark.parametrize(
    "fetcher,kwargs",
    [
        ("_fetch_pull_request_reviews", {"pr_number": 350}),
        ("_fetch_comment_reactions", {"comment_id": 98765}),
        ("_fetch_pr_reactions", {"pr_number": 350}),
    ],
)
def test_portable_review_fetch_reads_all_pages(
    snapshot_module, monkeypatch, fetcher, kwargs
):
    fn = snapshot_module[fetcher]
    calls = []

    def gh(cmd, *args, **kwargs):
        calls.append(cmd)
        assert kwargs["paginated"] is True
        return [{"id": n} for n in range(100)] + [{"id": 101}]

    monkeypatch.setitem(fn.__globals__, "run_command", gh)
    records = fn(pr_repo="owner/repo", **kwargs)
    assert records[-1] == {"id": 101}
    assert len(records) == 101
    assert "--paginate" in calls[0] and "--slurp" not in calls[0]


def test_historical_resolver_keeps_recorded_remediation_order():
    from moonmind.workflows.temporal.workflows.pr_resolver import (
        classify_pr_resolver_snapshot,
    )

    result = classify_pr_resolver_snapshot(
        {
            "headSha": HEAD,
            "checksComplete": True,
            "checksPassing": False,
            "blockers": [{"kind": "checks_failed"}, {"kind": "automated_review_wait"}],
        }
    )
    assert result["classification"] == "ci_failures"


@pytest.mark.parametrize("inventory_available", [True, False])
@pytest.mark.parametrize("head_changed", [False, True])
@pytest.mark.parametrize("statuses_available", [True, False])
def test_snapshot_collects_findings_after_review_completion(
    snapshot_module,
    monkeypatch,
    tmp_path,
    inventory_available,
    head_changed,
    statuses_available,
):
    main = snapshot_module["main"]
    scope = main.__globals__
    snapshot = _snapshot()
    checks = [{"name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS"}]
    pr = {
        **snapshot["pr"],
        "url": "https://github.com/owner/repo/pull/350",
        "statusCheckRollup": checks,
    }
    pr_reads = []

    def fetch_pr(_selector):
        pr_reads.append(True)
        observed = (
            {**pr, "headRefOid": OLD_HEAD} if head_changed and len(pr_reads) > 1 else pr
        )
        return observed, "350", []

    monkeypatch.setitem(scope, "fetch_pr_data", fetch_pr)
    monkeypatch.setitem(scope, "_fetch_required_status_checks", lambda **_kwargs: [])
    monkeypatch.setitem(scope, "_fetch_commit_check_runs", lambda **_kwargs: checks)
    monkeypatch.setitem(
        scope,
        "_fetch_commit_statuses",
        lambda **_kwargs: [] if statuses_available else None,
    )
    monkeypatch.setitem(scope, "_fetch_previous_commit_sha", lambda **_kwargs: None)
    monkeypatch.setitem(
        scope, "_fetch_head_commit_timestamp", lambda **_kwargs: HEAD_COMMITTED_AT
    )
    monkeypatch.setitem(
        scope,
        "_fetch_pull_request_reviews",
        lambda **_kwargs: [
            {
                "id": 50,
                "state": "COMMENTED",
                "commit_id": HEAD,
                "submitted_at": "2026-08-24T22:19:00Z",
                "user": {"login": "chatgpt-codex-connector"},
            }
        ],
    )
    reads = []

    def read_comments(*_args):
        reads.append(True)
        if len(reads) == 1:
            return {"comments": [_request_comment()]}
        if not inventory_available:
            return {}
        return {
            "comments": [
                _request_comment(),
                {
                    "id": 51,
                    "type": "review_comment",
                    "user": "chatgpt-codex-connector[bot]",
                    "body": "[P1] Fix the authorization check",
                    "created_at": "2026-08-24T22:19:01Z",
                },
            ]
        }

    monkeypatch.setitem(scope, "run_command", read_comments)
    path = tmp_path / "snapshot.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_snapshot.py",
            "--pr",
            "350",
            "--review-provider",
            "codex",
            "--require-fresh-review",
            "--snapshot-path",
            str(path),
        ],
    )
    if head_changed:
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert not path.exists()
        return
    main()
    captured = json.loads(path.read_text())
    assert len(reads) == 2
    assert captured["automatedReview"]["freshReviewForHead"] is inventory_available
    decision = classify_snapshot(normalize_portable_snapshot(captured))
    if inventory_available:
        assert captured["commentsSummary"]["actionableCommentIds"] == [51]
        if statuses_available:
            assert decision.remediation_skill == "fix-comments"
        else:
            assert decision.reason_code == "ci_signal_degraded"
            assert "head_statuses_unavailable" in captured["ci"]["degradedReasons"]
    else:
        assert decision.reason_code == "comments_unavailable"


def test_fresh_review_and_clean_state_merges() -> None:
    snapshot = _snapshot()
    snapshot["automatedReview"]["freshReviewForHead"] = True

    decision = classify_snapshot(normalize_portable_snapshot(snapshot))

    assert decision.action is ResolverAction.ATTEMPT_MERGE


def test_actionable_comments_are_fixed_before_a_review_is_requested() -> None:
    snapshot = _snapshot()
    snapshot["commentsSummary"]["hasActionableComments"] = True

    decision = classify_snapshot(normalize_portable_snapshot(snapshot))

    assert decision.action is ResolverAction.RUN_REMEDIATION
    assert decision.remediation_skill == "fix-comments"


def test_deferred_comments_stop_for_manual_review() -> None:
    snapshot = _snapshot()
    snapshot["commentsSummary"]["hasDeferredComments"] = True

    decision = classify_snapshot(normalize_portable_snapshot(snapshot))

    assert decision.action is ResolverAction.STOP_MANUAL_REVIEW
    assert decision.reason_code == "deferred_comments"


def test_review_loop_off_keeps_the_previous_merge_behavior() -> None:
    snapshot = _snapshot()
    snapshot["automatedReview"] = {"enabled": False}

    decision = classify_snapshot(normalize_portable_snapshot(snapshot))

    assert decision.action is ResolverAction.ATTEMPT_MERGE


# ---------------------------------------------------------------------------
# continuation contract
# ---------------------------------------------------------------------------


def test_request_review_continuation_names_only_the_provider(contract_module) -> None:
    payload = contract_module["build_gated_continuation"](
        _snapshot(),
        reason="fresh_review_required_after_remediation",
        execution_ref="step-execution-id",
    )

    assert payload == {
        "schemaVersion": "gated-continuation/v2",
        "gateType": "merge_automation",
        "action": "request_review",
        "provider": "codex",
        "reason": "fresh_review_required_after_remediation",
        "executionRef": "step-execution-id",
        "headSha": HEAD,
        "progressSignature": f"{HEAD}||",
    }


def test_request_review_continuation_requires_an_enabled_provider(
    contract_module,
) -> None:
    snapshot = _snapshot()
    snapshot["automatedReview"] = {"enabled": False, "provider": ""}

    with pytest.raises(ValueError):
        contract_module["build_gated_continuation"](
            snapshot,
            reason="fresh_review_required_after_remediation",
            execution_ref="step-execution-id",
        )


def test_request_review_next_step_maps_to_request_review_disposition(
    contract_module,
) -> None:
    assert (
        contract_module["remediation_next_step"](
            "fresh_review_required_after_remediation"
        )
        == "request_automated_review"
    )
    assert (
        contract_module["merge_automation_disposition_for_result"](
            status="blocked",
            merge_outcome="blocked",
            final_reason="fresh_review_required_after_remediation",
            next_step="request_automated_review",
        )
        == "request_review"
    )
    assert (
        contract_module["merge_automation_disposition_for_result"](
            status="blocked",
            merge_outcome="blocked",
            final_reason="automated_review_wait",
            next_step="wait_for_automated_review_and_retry_finalize",
        )
        == "reenter_gate"
    )


def test_finalize_writes_a_request_review_result(tmp_path, monkeypatch) -> None:
    finalize_module = _load_module(
        REPO_ROOT / ".agents" / "skills" / "pr-resolver" / "bin" / "pr_resolve_finalize.py"
    )
    main = finalize_module["main"]
    globals_dict = main.__globals__
    snapshot = _snapshot()

    def _write_snapshot(
        _snapshot_script: Path,
        _pr: str | None,
        snapshot_path: Path,
        **review_kwargs: Any,
    ) -> None:
        assert review_kwargs["review_provider"] == "codex"
        assert review_kwargs["require_fresh_review"] is True
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    monkeypatch.setitem(globals_dict, "_run_snapshot", _write_snapshot)
    monkeypatch.delenv("PR_RESOLVER_REVIEW_PROVIDER", raising=False)
    monkeypatch.delenv("PR_RESOLVER_REQUIRE_FRESH_REVIEW", raising=False)

    result_path = tmp_path / "result.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--pr",
            "350",
            "--snapshot-path",
            str(tmp_path / "snapshot.json"),
            "--result-path",
            str(result_path),
            "--review-provider",
            "codex",
            "--require-fresh-review",
            "--strict-exit-codes",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == finalize_module["EXIT_CODE_BLOCKED"]
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["final_reason"] == "fresh_review_required_after_remediation"
    assert payload["next_step"] == "request_automated_review"
    assert payload["mergeAutomationDisposition"] == "request_review"
    assert payload["gatedContinuation"]["action"] == "request_review"
    assert payload["gatedContinuation"]["provider"] == "codex"


def test_finalize_stops_on_a_refused_review_request(
    tmp_path, monkeypatch, contract_module
) -> None:
    finalize_module = _load_module(
        REPO_ROOT / ".agents" / "skills" / "pr-resolver" / "bin" / "pr_resolve_finalize.py"
    )
    main = finalize_module["main"]
    snapshot = _snapshot()
    snapshot["automatedReview"].update(
        {
            "requestFailed": True,
            "requestFailure": {
                "kind": "issue_comment",
                "id": 6025386732,
                "failedAt": "2026-10-06T21:02:18Z",
                "providerErrorClass": "rate_limit",
            },
        }
    )

    def _write_snapshot(
        _snapshot_script: Path, _pr: str | None, snapshot_path: Path, **_: Any
    ) -> None:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    monkeypatch.setitem(main.__globals__, "_run_snapshot", _write_snapshot)
    result_path = tmp_path / "result.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--pr",
            "350",
            "--snapshot-path",
            str(tmp_path / "snapshot.json"),
            "--result-path",
            str(result_path),
            "--review-provider",
            "codex",
            "--require-fresh-review",
            "--strict-exit-codes",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == finalize_module["EXIT_CODE_BLOCKED"]
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert payload["final_reason"] == "automated_review_request_failed"
    assert payload["next_step"] == "manual_review"
    assert payload["mergeAutomationDisposition"] == "manual_review"
    assert "gatedContinuation" not in payload
    assert "rate or usage limit" in payload["decision"]
    # A standalone orchestrator stops instead of retrying finalize.
    assert (
        contract_module["classify_retry_action"](
            payload["final_reason"], merge_not_ready_grace_remaining=5
        )
        == "stop"
    )


@pytest.mark.parametrize(
    "field,reason",
    [
        ("comments_available", "comments_unavailable"),
        ("comment_policy_enforced", "comment_policy_not_enforced"),
        ("checks_degraded", "ci_signal_degraded"),
        ("unknown_blocker", "unknown_blocker"),
        ("malformed", "snapshot_malformed"),
        ("deferred_comments", "deferred_comments"),
    ],
)
@pytest.mark.parametrize("conflicted", [False, True])
def test_terminal_evidence_precedes_pending_review(field, reason, conflicted):
    from dataclasses import replace

    snapshot = normalize_portable_snapshot(_snapshot())
    snapshot = replace(
        snapshot,
        review_loop_enabled=True,
        automated_review_requested=True,
        fresh_automated_review=False,
        merge_conflict=conflicted,
        **{field: field not in {"comments_available", "comment_policy_enforced"}},
    )
    decision = classify_snapshot(snapshot)
    assert decision.action == ResolverAction.STOP_MANUAL_REVIEW
    assert decision.reason_code == reason


@pytest.mark.parametrize("output", ['[]\n[{"id": 2}]', '[{"id": 1}]\n[]'])
def test_review_collection_decodes_gh_paginated_stream(
    snapshot_module, monkeypatch, output
):
    from types import SimpleNamespace

    run = snapshot_module["run_command"]
    monkeypatch.setattr(
        run.__globals__["subprocess"],
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=output, stderr=""),
    )
    assert snapshot_module["_fetch_pull_request_reviews"](
        pr_repo="owner/repo", pr_number=833
    )


@pytest.mark.parametrize(
    "output", ["", "{}", "[null]", '[{"id": 1}]\n[', '[{"id": 1}] garbage']
)
def test_review_collection_rejects_missing_or_partial_evidence(
    snapshot_module, monkeypatch, output
):
    from types import SimpleNamespace

    run = snapshot_module["run_command"]
    monkeypatch.setattr(
        run.__globals__["subprocess"],
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=output, stderr=""),
    )
    with pytest.raises(SystemExit):
        snapshot_module["_fetch_pull_request_reviews"](
            pr_repo="owner/repo", pr_number=833
        )


@pytest.mark.parametrize(
    "error",
    ["unknown flag: --slurp", "HTTP 401: Bad credentials", "HTTP 429: rate limit"],
)
def test_review_collection_preserves_command_failure(
    snapshot_module, monkeypatch, capsys, error
):
    from types import SimpleNamespace

    run = snapshot_module["run_command"]
    monkeypatch.setattr(
        run.__globals__["subprocess"],
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr=error),
    )
    monkeypatch.setattr(run.__globals__["time"], "sleep", lambda _: None)
    with pytest.raises(SystemExit):
        snapshot_module["_fetch_pull_request_reviews"](
            pr_repo="owner/repo", pr_number=833
        )
    assert error in capsys.readouterr().err


def test_pr833_completed_review_replay_through_cli_collection(
    snapshot_module, tmp_path, monkeypatch
):
    """Replay the escaped CLI boundary: old gh rejects --slurp; review is complete."""
    import sys

    fixture = json.loads(
        (REPO_ROOT / "tests/fixtures/pr_resolver/review_wait_833.json").read_text()
    )
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        + "import json, sys\n"
        + "if '--slurp' in sys.argv: sys.exit('unknown flag: --slurp')\n"
        + f"print(json.dumps([])); print(json.dumps({fixture['reviews']!r}))\n"
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    evidence = _evidence(
        snapshot_module,
        reviews=None,
        comments=fixture["comments"],
        head_sha=fixture["headSha"],
        head_committed_at=datetime.fromisoformat(fixture["headCommittedAt"]),
    )
    assert evidence["freshReviewForHead"] is True
    assert evidence["requestPending"] is False
    snapshot = normalize_portable_snapshot(
        _snapshot(
            automatedReview=evidence,
            commentsSummary={
                "includeBotReviewComments": True,
                "hasActionableComments": True,
                "actionableCommentIds": [3984847667],
            },
        )
    )
    assert classify_snapshot(snapshot).reason_code == "actionable_comments"


def test_review_collection_with_installed_gh():
    """Use real gh against an isolated HTTP fixture, including Link pagination."""
    import shutil
    import subprocess
    import sys

    if not shutil.which("gh"):
        pytest.skip("GitHub CLI is absent; the hermetic CLI replay remains required")
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tests/fixtures/pr_resolver/review_collection_probe.py"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert '"freshReviewForHead": true' in completed.stdout


@pytest.mark.parametrize(
    "body",
    [
        "[P1] Preserve rate limit metadata",
        "[P2] Handle usage limits in the dashboard",
        "> You have reached your Codex usage limits for code reviews.",
        '"Codex usage limits have been reached for code reviews."',
        "Example: You have reached your Codex usage limits for code reviews.",
        "```\nYou have reached your Codex usage limits for code reviews.\n```",
    ],
)
def test_limit_mentions_are_not_refusal_openings(snapshot_module, body):
    evidence = _evidence(
        snapshot_module, comments=[_request_comment(), _codex_reply(body)]
    )
    assert evidence["requestPending"] is True
    assert evidence["requestFailed"] is False


@pytest.mark.parametrize(
    "body", [CODEX_USAGE_LIMIT_REPLY, "Codex Review: Didn't find any major issues. 🚀"]
)
@pytest.mark.parametrize(
    "reply_id,request_id,answers",
    [
        (102, 101, True),
        ("102", "101", True),
        (100, 101, False),
        (101, 101, False),
        (None, 101, False),
        (102, None, False),
        ("invalid", 101, False),
        (True, 101, False),
    ],
)
def test_same_second_reply_requires_later_comment_id(
    snapshot_module, body, reply_id, request_id, answers
):
    request = _request_comment()
    request["id"] = request_id
    reply = _codex_reply(body, created_at=request["created_at"], comment_id=reply_id)
    evidence = _evidence(snapshot_module, comments=[reply, request])
    assert evidence["requestPending"] is (not answers)
    assert evidence["requestFailed"] is (answers and body == CODEX_USAGE_LIMIT_REPLY)
    assert evidence["freshReviewForHead"] is (
        answers and body != CODEX_USAGE_LIMIT_REPLY
    )


@pytest.mark.parametrize("latest_clean", [True, False])
def test_equal_timestamp_replies_use_ids_not_inventory_order(
    snapshot_module, latest_clean
):
    clean = _codex_reply(
        "Codex Review: Didn't find any major issues. 🚀",
        comment_id=103 if latest_clean else 102,
    )
    failure = _codex_reply(
        CODEX_USAGE_LIMIT_REPLY, comment_id=102 if latest_clean else 103
    )
    for replies in ([clean, failure], [failure, clean]):
        evidence = _evidence(snapshot_module, comments=[_request_comment(), *replies])
        assert evidence["freshReviewForHead"] is latest_clean
        assert evidence["requestFailed"] is (not latest_clean)


def test_equal_timestamp_requests_use_ids_not_inventory_order(snapshot_module):
    first = {**_request_comment(), "id": 101}
    failure = _codex_reply(
        CODEX_USAGE_LIMIT_REPLY, created_at=first["created_at"], comment_id=102
    )
    latest = {**first, "id": 103}
    for comments in ([latest, failure, first], [first, failure, latest]):
        evidence = _evidence(snapshot_module, comments=comments)
        assert evidence["requestCommentId"] == 103
        assert evidence["requestPending"] is True
        assert evidence["requestFailed"] is False


@pytest.mark.parametrize("prefix", ["    ", "\t", "\n    "])
def test_indented_refusal_example_is_not_a_provider_failure(snapshot_module, prefix):
    reply = _codex_reply(prefix + CODEX_USAGE_LIMIT_REPLY)
    evidence = _evidence(snapshot_module, comments=[_request_comment(), reply])
    assert evidence["requestPending"] is True
    assert evidence["requestFailed"] is False


@pytest.mark.parametrize(
    "change",
    ["quote", "embedded", "indented", "wrong_head", "review_comment", "invalid_time"],
)
def test_unrelated_request_does_not_supersede_a_refusal(snapshot_module, change):
    retry = {**_request_comment("2026-08-24T23:00:00Z"), "id": 98766}
    if change == "quote":
        retry["body"] = "> @codex review"
    elif change == "embedded":
        retry["body"] = "Please run @codex review later"
    elif change == "indented":
        retry["body"] = "    @codex review"
    elif change == "wrong_head":
        retry["commit_id"] = OLD_HEAD
    elif change == "review_comment":
        retry["type"] = "review_comment"
    else:
        retry["created_at"] = "invalid"
    evidence = _evidence(
        snapshot_module,
        comments=[retry, _request_comment(), _codex_reply(CODEX_USAGE_LIMIT_REPLY)],
    )
    assert evidence["requestCommentId"] == 98765
    assert evidence["requestFailed"] is True


@pytest.mark.parametrize("prefix", ["    ", "\t", "\n    "])
def test_indented_clean_reply_is_not_completion(snapshot_module, prefix):
    evidence = _evidence(
        snapshot_module,
        comments=[
            _request_comment(),
            _codex_reply(prefix + "Codex Review: Didn't find any major issues. 🚀"),
        ],
    )
    assert evidence["freshReviewForHead"] is False
    assert evidence["requestPending"] is True


@pytest.mark.parametrize("exact_review", [False, True])
def test_missing_head_timestamp_cannot_bind_historical_comments(
    snapshot_module, monkeypatch, exact_review
):
    build = snapshot_module["build_automated_review_evidence"]
    monkeypatch.setitem(
        build.__globals__, "_fetch_head_commit_timestamp", lambda **kwargs: None
    )
    kwargs = {
        "head_committed_at": None,
        "comments": [
            _request_comment("2020-01-01T00:00:00Z"),
            _codex_reply(
                "Codex Review: Didn't find any major issues. 🚀",
                created_at="2020-01-01T00:00:01Z",
            ),
        ],
        "reviews": (
            [
                {
                    "id": 50,
                    "state": "COMMENTED",
                    "commit_id": HEAD,
                    "submitted_at": "2026-08-24T22:19:00Z",
                    "user": {"login": CODEX_LOGIN},
                }
            ]
            if exact_review
            else []
        ),
    }
    if exact_review:
        with pytest.raises(RuntimeError, match="head.*timestamp"):
            _evidence(snapshot_module, **kwargs)
        kwargs["comments"] = []
        evidence = _evidence(snapshot_module, **kwargs)
        assert evidence["freshReviewForHead"] is True
        assert evidence["requestCommentId"] is None
    else:
        with pytest.raises(RuntimeError, match="head.*timestamp"):
            _evidence(snapshot_module, **kwargs)


@pytest.mark.parametrize("new_result", ["pending", "failure", "complete"])
@pytest.mark.parametrize("initial_result", ["complete", "failure"])
def test_refreshed_inventory_rebinds_review_evidence(
    snapshot_module, monkeypatch, tmp_path, new_result, initial_result
):
    main = snapshot_module["main"]
    scope = main.__globals__
    first = {**_request_comment(), "id": 100}
    second = {**_request_comment("2026-08-24T22:22:00Z"), "id": 102}
    first_clean = _codex_reply(
        CODEX_USAGE_LIMIT_REPLY
        if initial_result == "failure"
        else "Codex Review: Didn't find any major issues. \U0001f680",
        comment_id=101,
    )
    second_reply = _codex_reply(
        (
            CODEX_USAGE_LIMIT_REPLY
            if new_result == "failure"
            else "Codex Review: Didn't find any major issues. 🚀"
        ),
        created_at="2026-08-24T22:23:00Z",
        comment_id=103,
    )
    checks = [{"name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS"}]
    pr = {
        **_snapshot()["pr"],
        "url": "https://github.com/owner/repo/pull/350",
        "statusCheckRollup": checks,
    }
    replacements = {
        "fetch_pr_data": lambda selector: (pr, "350", []),
        "_fetch_required_status_checks": lambda **kw: [],
        "_fetch_commit_check_runs": lambda **kw: checks,
        "_fetch_commit_statuses": lambda **kw: [],
        "_fetch_previous_commit_sha": lambda **kw: None,
        "_fetch_head_commit_timestamp": lambda **kw: HEAD_COMMITTED_AT,
        "_fetch_pull_request_reviews": lambda **kw: [],
        "_fetch_comment_reactions": lambda **kw: [],
        "_fetch_pr_reactions": lambda **kw: [],
    }
    for key, value in replacements.items():
        monkeypatch.setitem(scope, key, value)
    reads = []

    def read_comments(*args):
        reads.append(True)
        comments = [first, first_clean]
        if len(reads) > 1:
            comments.append(second)
            if new_result != "pending":
                comments.append(second_reply)
        if len(reads) > 2:
            comments.append(
                {
                    "id": 104,
                    "type": "review_comment",
                    "user": CODEX_LOGIN,
                    "body": "[P1] Preserve authorization",
                    "created_at": "2026-08-24T22:23:01Z",
                }
            )
        return {"comments": comments, "thread_inventory_complete": True}

    monkeypatch.setitem(scope, "run_command", read_comments)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "snapshot.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "snapshot",
            "--pr",
            "350",
            "--review-provider",
            "codex",
            "--require-fresh-review",
            "--snapshot-path",
            str(path),
        ],
    )
    main()
    result = json.loads(path.read_text())
    assert result["automatedReview"]["requestCommentId"] == 102
    assert result["automatedReview"]["freshReviewForHead"] is (new_result == "complete")
    assert result["automatedReview"]["requestPending"] is (new_result == "pending")
    assert result["automatedReview"]["requestFailed"] is (new_result == "failure")
    if new_result == "complete":
        assert len(reads) == 3
        assert result["commentsSummary"]["actionableCommentIds"] == [104]


@pytest.mark.parametrize("comments", [[], [{"body": "Unrelated discussion"}]])
def test_first_request_does_not_require_optional_head_timestamp(
    snapshot_module, monkeypatch, comments
):
    build = snapshot_module["build_automated_review_evidence"]
    monkeypatch.setitem(build.__globals__, "_fetch_head_commit_timestamp", lambda **kw: None)
    evidence = _evidence(snapshot_module, head_committed_at=None, comments=comments)
    assert evidence["freshReviewForHead"] is False
    assert evidence["requestPending"] is False
    assert evidence["requestFailed"] is False
    assert evidence["requestCommentId"] is None
    assert evidence["completionId"] is None

    decision = classify_snapshot(normalize_portable_snapshot(_snapshot(automatedReview=evidence)))
    assert decision.action is ResolverAction.REQUEST_REVIEW
