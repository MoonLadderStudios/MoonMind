"""Portable Skill tests for the pr-resolver automated review loop."""

from __future__ import annotations

import json
import os
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

