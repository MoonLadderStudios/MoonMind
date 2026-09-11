"""Interrupted-handoff reconciliation and unresponsive-owner surfacing (#4182).

Covers MoonLadderStudios/MoonMind#4182 acceptance through the real
policy/Activity call shapes: crash-between-comment-and-labels repair
without reimplementation, mixed-label blocking with successor
invalidation, unresponsive/manual-owner surfacing without age-based
release, two-reconciler retry safety with coalesced reporting and
targeted label ops, explicit uncertainty across rate limits/outages/
lost acks/pagination/restart/unknown merges, default-deployment
scheduling through the production boundary, and local-vs-remote evidence
distinction preserving the source outcome.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from moonmind.workflows.temporal import github_issue_reconciliation as recon
from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle
from moonmind.workflows.temporal.activities import github_issue_reconciliation_activities as acts
from moonmind.workflows.temporal.github_issue_attempt import (
    AttemptHandoff,
    render_attempt_comment,
)

REPO = "o/r"
ATTEMPT_ID = "att_" + "a" * 24


def _conclusive_writer() -> dict[str, Any]:
    return {
        "writers_stopped": True,
        "stop_method": "runtime_quiescence",
        "stop_evidence": "runtime reports 0 writers; poll confirms stopped",
    }


def _settled_mutations() -> dict[str, Any]:
    return {"push_outcome": "confirmed", "pr_outcome": "confirmed", "merge_outcome": "absent_na", "merge_outcome_absent": True}


def _verified_preservation() -> dict[str, Any]:
    return {
        "save_method": "pr_head_verified",
        "pr_url": "https://github.com/o/r/pull/7",
        "pr_head_sha": "b" * 40,
        "pr_base": "main",
        "revision": "b" * 40,
        "preservation_verified": True,
    }


def _handoff_body(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "attempt_id": ATTEMPT_ID,
        "deployment_id": "deploy-a",
        "repository": REPO,
        "issue_number": 11,
        "workflow_id": "wf-1",
        "run_id": "run-1",
        "activity": "releasing",
        "writers_stopped": True,
        "stop_evidence": "runtime reports 0 writers",
        "pending_disposition": "code_review",
        "pr_url": "https://github.com/o/r/pull/7",
        "pr_head_sha": "b" * 40,
        "pr_base": "main",
        "outcome": "completed",
        "next_action": "continue-review",
    }
    base.update(overrides)
    body, error = render_attempt_comment(AttemptHandoff(**base))
    assert not error, error
    return body


class FakeGitHubService:
    """Minimal trusted-boundary fake: targeted ops only, no whole-set writes."""

    def __init__(self, issues: dict[int, dict[str, Any]] | None = None, comments: dict[int, list[str]] | None = None):
        self.issues = dict(issues or {})
        self.comments = {number: list(bodies) for number, bodies in (comments or {}).items()}
        self.added: list[tuple[int, list[str]]] = []
        self.removed: list[tuple[int, str]] = []
        self.created: list[tuple[int, str]] = []
        self.closed: list[int] = []
        self.read_issue_result: dict[str, Any] | None = None

    def _github_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def resolve_github_token(self, explicit_token=None, *, repo=None):
        return "fake-token", None

    async def get_issue(self, *, repo: str, issue_number: int) -> dict[str, Any]:
        if self.read_issue_result is not None:
            return dict(self.read_issue_result)
        issue = self.issues.get(issue_number)
        if issue is None:
            return {"ok": False, "reasonCode": "denied", "summary": "Issue read denied with HTTP 404."}
        return {"ok": True, "reasonCode": "read", "issue": dict(issue)}

    async def list_issue_comments(self, *, repo: str, issue_number: int, github_token=None) -> dict[str, Any]:
        bodies = self.comments.get(issue_number, [])
        return {"ok": True, "reasonCode": "read", "comments": [{"id": index + 1, "body": body} for index, body in enumerate(bodies)], "incomplete": False}

    async def add_issue_labels(self, *, repo: str, issue_number: int, labels: list[str], github_token=None) -> dict[str, Any]:
        self.added.append((issue_number, list(labels)))
        issue = self.issues.setdefault(issue_number, {"state": "open", "labels": []})
        names = issue.setdefault("labels", [])
        for label in labels:
            if label.lower() not in {str(n).lower() for n in names}:
                names.append(label)
        return {"ok": True, "reasonCode": "added", "summary": f"Added {labels}."}

    async def remove_issue_label(self, *, repo: str, issue_number: int, label: str, github_token=None) -> dict[str, Any]:
        self.removed.append((issue_number, label))
        issue = self.issues.setdefault(issue_number, {"state": "open", "labels": []})
        issue["labels"] = [n for n in issue.get("labels", []) if str(n).lower() != label.lower()]
        return {"ok": True, "reasonCode": "removed", "summary": f"Removed {label}."}

    async def create_issue_comment(self, *, repo: str, issue_number: int, body: str, github_token=None) -> dict[str, Any]:
        self.created.append((issue_number, body))
        self.comments.setdefault(issue_number, []).append(body)
        return {"ok": True, "reasonCode": "created", "summary": "Comment created.", "commentId": len(self.comments[issue_number])}

    async def close_issue(self, *, repo: str, issue_number: int, github_token=None) -> dict[str, Any]:
        self.closed.append(issue_number)
        self.issues.setdefault(issue_number, {"state": "open", "labels": []})["state"] = "closed"
        return {"ok": True, "reasonCode": "closed", "summary": "Closed."}


def _issue(*labels: str, state: str = "open") -> dict[str, Any]:
    return {"state": state, "labels": list(labels)}


# -- Acceptance: crash between terminal comment and label writes -------------


def test_crash_reconciled_from_conclusive_evidence_without_reimplementation() -> None:
    decision = recon.decide_issue_reconciliation(
        issue=_issue("status: in-progress"),
        intended_from_settled="in_progress",
        intended_to_target="to_code_review",
        writer_evidence=_conclusive_writer(),
        mutation_evidence=_settled_mutations(),
        preservation_evidence=_verified_preservation(),
        proposed_disposition="code_review",
        trusted_handoff_present=True,
        remote_handoff={"activity": "releasing", "attemptId": ATTEMPT_ID},
        local_workflow_available=False,
    )
    assert decision.action == recon.ACTION_COMPLETE
    assert decision.to_target == "to_code_review"
    planned = recon.plan_repair_mutation(
        from_settled="in_progress",
        to_target="to_code_review",
        current_labels=["status: in-progress"],
        proposed_disposition="code_review",
    )
    assert planned["allowed"] is True
    mutation = planned["mutation"]
    # Targeted ops only: destination added first, old blocker removed, no
    # whole-label-set replacement, no implementation repeated.
    assert mutation["labelsToAdd"] == ["status: code-review"]
    assert mutation["labelsToRemove"] == ["status: in-progress"]


def test_inconclusive_crash_evidence_surfaced_not_repaired() -> None:
    decision = recon.decide_issue_reconciliation(
        issue=_issue("status: in-progress"),
        intended_from_settled="in_progress",
        intended_to_target="to_code_review",
        writer_evidence={"writers_stopped": False},
        mutation_evidence=_settled_mutations(),
        preservation_evidence=_verified_preservation(),
        proposed_disposition="code_review",
        trusted_handoff_present=True,
        remote_handoff={"activity": "active", "attemptId": ATTEMPT_ID},
        local_workflow_available=False,
    )
    assert decision.action == recon.ACTION_ATTENTION
    assert decision.to_target == ""


# -- Acceptance: mixed labels block until repaired; successors invalidate ----


def test_mixed_labels_block_until_repaired() -> None:
    decision = recon.decide_issue_reconciliation(
        issue=_issue("status: in-progress", "status: needs-attention"),
        trusted_handoff_present=True,
        remote_handoff={"activity": "active", "attemptId": ATTEMPT_ID},
    )
    assert decision.action == recon.ACTION_ATTENTION
    assert decision.reason_code == "mixed_labels_blocked"
    assert set(decision.retain_labels) == {"status: in-progress", "status: needs-attention"}


def test_mixed_labels_repaired_with_conclusive_evidence() -> None:
    decision = recon.decide_issue_reconciliation(
        issue=_issue("status: in-progress", "status: needs-attention"),
        intended_from_settled="blocked_mixed",
        intended_to_target="to_needs_attention",
        writer_evidence=_conclusive_writer(),
        mutation_evidence=_settled_mutations(),
        preservation_evidence=_verified_preservation(),
        proposed_disposition="needs_attention",
        trusted_handoff_present=True,
        remote_handoff={"activity": "releasing", "attemptId": ATTEMPT_ID},
    )
    assert decision.action == recon.ACTION_COMPLETE


@pytest.mark.parametrize("successor", ["needs_attention", "closed", "code_review", "available"])
def test_newer_attempts_and_holds_invalidate_old_cleanup(successor: str) -> None:
    decision = recon.decide_issue_reconciliation(
        issue=_issue("status: in-progress"),
        intended_from_settled="in_progress",
        intended_to_target="to_available",
        writer_evidence=_conclusive_writer(),
        mutation_evidence=_settled_mutations(),
        preservation_evidence=_verified_preservation(),
        proposed_disposition="available",
        trusted_handoff_present=True,
        successor_observed_settled=successor,
    )
    assert decision.action in {recon.ACTION_ABANDONED, recon.ACTION_ATTENTION}


def test_operator_hold_abandons_repair() -> None:
    decision = recon.decide_issue_reconciliation(
        issue=_issue("status: in-progress"),
        intended_from_settled="in_progress",
        intended_to_target="to_available",
        writer_evidence=_conclusive_writer(),
        mutation_evidence=_settled_mutations(),
        preservation_evidence=_verified_preservation(),
        proposed_disposition="available",
        trusted_handoff_present=True,
        operator_hold=True,
    )
    assert decision.action in {recon.ACTION_ABANDONED, recon.ACTION_ATTENTION}
    assert decision.to_target == ""


# -- Acceptance: unresponsive owners and manual labels, no age-based release -


def test_manual_in_progress_surfaced_without_age_clear() -> None:
    decision = recon.decide_issue_reconciliation(
        issue=_issue("status: in-progress"),
        trusted_handoff_present=False,
        manual_in_progress=True,
    )
    assert decision.action == recon.ACTION_ATTENTION
    assert decision.reason_code == "manual_in_progress_surfaced"
    assert "status: in-progress" in decision.retain_labels


def test_silent_owner_surfaced_with_old_status_retained() -> None:
    decision = recon.decide_issue_reconciliation(
        issue=_issue("status: in-progress"),
        trusted_handoff_present=True,
        writer_evidence={"writers_stopped": False},
        remote_handoff={"activity": "active", "attemptId": ATTEMPT_ID},
        local_workflow_available=False,
    )
    assert decision.action == recon.ACTION_ATTENTION
    assert decision.reason_code == "unresponsive_owner_uncertain"
    assert "status: in-progress" in decision.retain_labels
    ops = recon.targeted_attention_ops(current_labels=["status: in-progress"], attention_already_present=False)
    assert ops["labelsToAdd"] == ["status: needs-attention"]
    assert ops["labelsToRemove"] == []


def test_unrecognized_status_never_silently_admitted() -> None:
    decision = recon.decide_issue_reconciliation(
        issue=_issue("status: claiming"),
        trusted_handoff_present=False,
    )
    assert decision.action == recon.ACTION_ATTENTION
    assert decision.reason_code == "classification_required"


# -- Acceptance: unknown outcomes require attention, not timed unlock --------


def test_unknown_merge_outcome_blocks_takeover() -> None:
    decision = recon.decide_issue_reconciliation(
        issue=_issue("status: in-progress"),
        intended_from_settled="in_progress",
        intended_to_target="to_available",
        writer_evidence=_conclusive_writer(),
        mutation_evidence=_settled_mutations(),
        preservation_evidence=_verified_preservation(),
        proposed_disposition="available",
        trusted_handoff_present=True,
        pr_state={"mergeRequested": True, "mergeOutcomeKnown": False},
        remote_handoff={"activity": "releasing", "attemptId": ATTEMPT_ID},
    )
    assert decision.action == recon.ACTION_ATTENTION
    assert decision.reason_code == "unknown_external_outcome"


def test_unmerged_pr_never_proves_merge_cannot_complete() -> None:
    assert recon._unknown_external_block({"mergeRequested": True, "mergeOutcomeKnown": False}) is True
    assert recon._unknown_external_block({"mergeRequested": False}) is False
    assert recon._unknown_external_block(None) is False


# -- Acceptance: two reconcilers, no overwrite, bounded comments --------------


def test_reconciler_never_rewrites_attempt_comments_and_coalesces() -> None:
    existing = _handoff_body()
    new_body = recon.render_reconciler_comment(
        repository=REPO, issue_number=11, reason_code="unresponsive_owner_uncertain", summary="Owner uncertain."
    )
    assert "att_" + "a" * 24 not in new_body or "ownership unchanged" in new_body
    # The reconciler marker never collides with the attempt marker.
    assert "<!-- moonmind-github-attempt:" not in new_body
    gate = recon.should_post_reconciler_comment(existing_bodies=[existing], reason_code="unresponsive_owner_uncertain", new_body=new_body)
    assert gate["post"] is True  # unrelated attempt comment does not coalesce
    gate2 = recon.should_post_reconciler_comment(existing_bodies=[new_body], reason_code="unresponsive_owner_uncertain", new_body=new_body)
    assert gate2["post"] is False  # duplicate observation coalesced


@pytest.mark.asyncio
async def test_two_reconciler_runs_produce_one_observation(tmp_path) -> None:
    service = FakeGitHubService(
        issues={11: _issue("status: in-progress")},
        comments={11: []},
    )
    kwargs: dict[str, Any] = {"repository": REPO, "issue_numbers": [11], "state_dir": str(tmp_path), "service": service}
    first = await acts.reconcile_github_issue_handoffs(**kwargs)
    second = await acts.reconcile_github_issue_handoffs(**kwargs)
    assert first["results"][0]["action"] == recon.ACTION_ATTENTION
    # Exactly one observation comment across both independent runs.
    assert len(service.created) == 1
    assert second["results"][0].get("commentCoalesced") is True
    # Only targeted single-label ops; no whole-set replacement exists.
    for _, labels in service.added:
        assert len(labels) == 1


# -- Acceptance: uncertainty preserved across failure modes --------------------


def test_rate_limit_and_outage_return_unknown_and_stop_admission() -> None:
    for transport in ("rate_limited", "outcome_unknown"):
        scan = recon.merge_scan_results(examined=3, surfaced=1, transport_error=transport, rate_limited=(transport == "rate_limited"))
        assert scan["status"] == recon.SCAN_UNKNOWN
        assert scan["admissionAllowed"] is False


def test_exhausted_budgets_return_partial_never_clean() -> None:
    scan = recon.merge_scan_results(examined=100, repaired=2, pages_exhausted=True, requests_exhausted=True)
    assert scan["status"] == recon.SCAN_PARTIAL
    assert "not clean" in scan["summary"]


@pytest.mark.asyncio
async def test_worker_restart_preserves_pending_sync(tmp_path) -> None:
    store = recon.record_pending_effect(
        None, repository=REPO, issue_number=11,
        intended_from_settled="in_progress", intended_to_target="to_code_review",
        proposed_disposition="code_review", reason="crash before label writes",
    )
    assert recon.pending_effects_for_issue(store, repository=REPO, issue_number=11)
    service = FakeGitHubService(issues={11: _issue("status: in-progress")}, comments={11: [_handoff_body()]})
    (tmp_path / "pending_sync.json").write_text(json.dumps(store), encoding="utf-8")

    async def _known_pr(*, service: Any, pr_url: str) -> dict[str, Any]:
        return {"known": True, "state": "open", "merged": False, "headSha": "b" * 40, "reasonCode": "pr_read", "summary": "open"}

    import moonmind.workflows.temporal.activities.github_issue_reconciliation_activities as activity_module

    original = activity_module._fetch_pr_state
    activity_module._fetch_pr_state = _known_pr  # type: ignore[assignment]
    try:
        result = await acts.reconcile_github_issue_handoffs(repository=REPO, issue_numbers=[11], state_dir=str(tmp_path), service=service)
    finally:
        activity_module._fetch_pr_state = original
    item = result["results"][0]
    assert item["action"] == recon.ACTION_COMPLETE
    assert item["reasonCode"] == "repaired"
    assert "status: code-review" in service.issues[11]["labels"]
    assert "status: in-progress" not in service.issues[11]["labels"]


@pytest.mark.asyncio
async def test_lost_acknowledgment_retains_pending_evidence(tmp_path) -> None:
    class LossyService(FakeGitHubService):
        async def add_issue_labels(self, *, repo: str, issue_number: int, labels: list[str], github_token=None) -> dict[str, Any]:
            self.added.append((issue_number, list(labels)))
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "Response lost; result unknown."}

    service = LossyService(issues={11: _issue("status: in-progress")}, comments={11: [_handoff_body()]})

    async def _known_pr(*, service: Any, pr_url: str) -> dict[str, Any]:
        return {"known": True, "state": "open", "merged": False, "headSha": "b" * 40, "reasonCode": "pr_read", "summary": "open"}

    import moonmind.workflows.temporal.activities.github_issue_reconciliation_activities as activity_module

    original = activity_module._fetch_pr_state
    activity_module._fetch_pr_state = _known_pr  # type: ignore[assignment]
    try:
        store = recon.record_pending_effect(
            None, repository=REPO, issue_number=11,
            intended_from_settled="in_progress", intended_to_target="to_code_review",
            proposed_disposition="code_review", reason="known interrupted transition",
        )
        (tmp_path / "pending_sync.json").write_text(json.dumps(store), encoding="utf-8")
        result = await acts.reconcile_github_issue_handoffs(repository=REPO, issue_numbers=[11], state_dir=str(tmp_path), service=service)
    finally:
        activity_module._fetch_pr_state = original
    item = result["results"][0]
    assert item["action"] == recon.ACTION_DEFERRED_UNKNOWN
    persisted = json.loads((tmp_path / "pending_sync.json").read_text(encoding="utf-8"))
    assert recon.pending_effects_for_issue(persisted, repository=REPO, issue_number=11)


@pytest.mark.asyncio
async def test_rate_limited_issue_read_defers_with_uncertainty(tmp_path) -> None:
    service = FakeGitHubService(issues={11: _issue("status: in-progress")})
    service.read_issue_result = {"ok": False, "reasonCode": "rate_limited", "summary": "Issue read rate limited (HTTP 429)."}
    result = await acts.reconcile_github_issue_handoffs(repository=REPO, issue_numbers=[11], state_dir=str(tmp_path), service=service)
    assert result["ok"] is False
    assert result["scan"]["status"] == recon.SCAN_UNKNOWN
    assert result["admissionAllowed"] is False


# -- Acceptance: default deployment schedules bounded reconciliation ----------


def test_default_maintenance_path_is_supported_not_hidden_or_paused() -> None:
    schedule = recon.default_reconciliation_schedule()
    assert schedule["workflowType"] == "MoonMind.GitHubIssueReconcile"
    assert schedule["activityType"] == "github_issue.reconcile_handoffs"
    assert schedule["enabled"] is True
    assert schedule["paused"] is False
    assert schedule["overlapMode"] == "skip"


def test_production_boundary_wires_workflow_activity_and_registry() -> None:
    from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
    from moonmind.workflows.temporal.activity_runtime import _ACTIVITY_HANDLER_ATTRS
    from moonmind.workflows.temporal.workflow_registry import raw_workflow_registrations
    from moonmind.workflows.temporal.workflows.github_issue_reconcile import MoonMindGitHubIssueReconcileWorkflow

    catalog = build_default_activity_catalog()
    route = catalog.resolve_activity("github_issue.reconcile_handoffs")
    assert route.task_queue
    assert route.fleet == "integrations"
    assert _ACTIVITY_HANDLER_ATTRS["github_issue.reconcile_handoffs"] == ("integrations", "github_issue_reconcile_handoffs")
    assert MoonMindGitHubIssueReconcileWorkflow is not None
    by_type = {r.load_class().__name__: r for r in raw_workflow_registrations()}
    assert "MoonMindGitHubIssueReconcileWorkflow" in by_type


def test_missing_credentials_are_actionable_not_silent_fallback() -> None:
    readiness = recon.check_reconciliation_readiness(token_available=False, token_error="no token resolved")
    assert readiness["ready"] is False
    assert readiness["reasonCode"] == "github_credentials_missing"
    assert "no fallback" in readiness["summary"]


@pytest.mark.asyncio
async def test_activity_refuses_mutations_without_credentials(tmp_path) -> None:
    class NoAuthService(FakeGitHubService):
        async def resolve_github_token(self, explicit_token=None, *, repo=None):
            return "", "no token configured"

    result = await acts.reconcile_github_issue_handoffs(repository=REPO, issue_numbers=[11], state_dir=str(tmp_path), service=NoAuthService())
    assert result["ok"] is False
    assert result["admissionAllowed"] is False
    assert result["results"] == []


# -- Acceptance: local vs remote evidence distinction --------------------------


def test_local_terminal_evidence_distinguished_from_remote_unresponsive() -> None:
    local = recon.classify_evidence_source(
        local_terminal_record={"terminal_recorded": True, "primaryOutcome": "failed"},
        remote_handoff=None,
        local_workflow_available=True,
    )
    assert local["locallyConfirmed"] is True
    assert local["sourceOutcome"] == "failed"
    assert local["sourceOutcomePreserved"] is True
    remote = recon.classify_evidence_source(
        local_terminal_record=None, remote_handoff={"activity": "active"}, local_workflow_available=False
    )
    assert remote["locallyConfirmed"] is False
    assert remote["remoteUnresponsive"] is True


def test_missing_local_record_is_not_remote_failure() -> None:
    source = recon.classify_evidence_source(local_terminal_record=None, remote_handoff=None, local_workflow_available=False)
    assert source["remoteUnresponsive"] is True
    assert source["locallyConfirmed"] is False
    # Uncertainty surfaces attention; it never fabricates a local failure.
    decision = recon.decide_issue_reconciliation(
        issue=_issue("status: in-progress"),
        trusted_handoff_present=False,
        manual_in_progress=True,
    )
    assert decision.action == recon.ACTION_ATTENTION


def test_diagnostics_expose_last_run_pending_ambiguous_and_failures() -> None:
    diagnostics = recon.build_reconciliation_diagnostics(
        last_success_at="2026-09-11T00:00:00Z",
        pending_effects=[{"key": "o/r#11"}],
        ambiguous_owners=[{"issueNumber": 12, "reasonCode": "unresponsive_owner_uncertain"}],
        failures=[{"reasonCode": "rate_limited", "summary": "slow down"}],
        scan={"status": recon.SCAN_UNKNOWN},
    )
    assert diagnostics["lastSuccessfulReconciliation"] == "2026-09-11T00:00:00Z"
    assert len(diagnostics["pendingIssueEffects"]) == 1
    assert len(diagnostics["ambiguousOwners"]) == 1
    assert len(diagnostics["actionableFailures"]) == 1


def test_blocked_repair_plan_touches_only_lifecycle_labels() -> None:
    planned = recon.plan_repair_mutation(
        from_settled="blocked_mixed",
        to_target="to_needs_attention",
        current_labels=["status: in-progress", "status: needs-attention", "bug"],
        proposed_disposition="needs_attention",
    )
    assert planned["allowed"] is True
    assert planned["mutation"]["labelsToAdd"] == []
    assert planned["mutation"]["labelsToRemove"] == ["status: in-progress"]
    readback = recon.classify_repair_readback(
        mutation=planned["mutation"],
        read_back={"state": "open", "labels": ["status: needs-attention", "bug"]},
        to_target="to_needs_attention",
    )
    assert readback["outcome"] == lifecycle.OUTCOME_APPLIED
    stale = recon.classify_repair_readback(
        mutation=planned["mutation"],
        read_back={"state": "open", "labels": ["status: in-progress", "status: needs-attention"]},
        to_target="to_needs_attention",
    )
    assert stale["outcome"] == lifecycle.OUTCOME_INCOMPLETE
    unknown = recon.classify_repair_readback(mutation=planned["mutation"], read_back=None)
    assert unknown["outcome"] == lifecycle.OUTCOME_UNKNOWN


def test_scan_scope_independent_of_eligible_candidate_filter() -> None:
    # Stranded in-progress/attention/review states are in scope even though
    # Search and Implement would never select them as candidates.
    for labels, expected in [
        (["status: in-progress"], True),
        (["status: needs-attention"], True),
        (["status: code-review"], True),
        (["status: in-progress", "status: needs-attention"], True),
        ([], False),
        (["status: recovery-needed"], False),
    ]:
        result = recon.classify_issue_for_scan({"state": "open", "labels": labels})
        assert result["inScope"] is expected, labels
        if expected:
            assert lifecycle.interpret_issue({"state": "open", "labels": labels}).eligible_for_implement is False
