"""Failed-attempt finalization with safe release (#4179).

Covers MoonLadderStudios/MoonMind#4179 acceptance through the real
workflow/Activity call shapes: controlling vs internal events, writer-stop
and mutation/preservation gates, evidence-driven dispositions, objective vs
execution separation, unfinalized/pending-sync and successor respect,
completion routing, interrupt-after-every-mutation pending states, and the
five failure stages (before edits, after partial work, after PR creation,
after accepted verification, after merge) through the production Activity
with a fake GitHub service.
"""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.workflows.temporal import github_issue_finalization as fin
from moonmind.workflows.temporal.activities import github_issue_finalization_activities as acts


def _writer(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "writers_stopped": True,
        "stop_method": "runtime_quiescence",
        "stop_evidence": "runtime reports 0 writers; poll confirms stopped",
    }
    base.update(overrides)
    return base


def _mutations(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"push_outcome": "confirmed", "pr_outcome": "confirmed", "merge_outcome": "absent_na",
                            "merge_outcome_absent": True}
    base.update(overrides)
    return base


def _preserved(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "save_method": "pr_head_verified",
        "pr_url": "https://github.com/o/r/pull/7",
        "pr_head_sha": "a" * 40,
        "pr_base": "main",
        "revision": "a" * 40,
        "preservation_verified": True,
    }
    base.update(overrides)
    return base


# -- Req 1: controlling boundary ---------------------------------------------


@pytest.mark.parametrize("event", ["success", "failed", "exhausted_retries", "blocked", "cancelled", "terminal_failure"])
def test_controlling_terminal_events_finalize(event: str) -> None:
    decision = fin.should_finalize_controlling_attempt(event)
    assert decision["finalize"] is True
    assert decision["reasonCode"] == "controlling_terminal"


@pytest.mark.parametrize(
    "event", ["internal_retry", "remediation_iteration", "review_wait", "step_failure", "child_failure", "awaiting_review"]
)
def test_internal_events_retain_attempt(event: str) -> None:
    decision = fin.should_finalize_controlling_attempt(event)
    assert decision["finalize"] is False
    assert decision["reasonCode"] == "internal_event_retains_attempt"


def test_unknown_event_never_releases() -> None:
    decision = fin.should_finalize_controlling_attempt("mystery-outcome")
    assert decision["finalize"] is False
    assert decision["reasonCode"] == "unknown_event_no_release"


def test_internal_retries_retain_ownership_and_cancellation_holds() -> None:
    assert fin.should_finalize_controlling_attempt("internal_retry")["finalize"] is False
    hold = fin.choose_disposition({"intentional_cancellation": True})
    assert hold["disposition"] == fin.DISPOSITION_NEEDS_ATTENTION
    assert hold["schedulesReplacement"] is False


# -- Req 2: writer / mutation / preservation gates ----------------------------


@pytest.mark.parametrize(
    "proof", ["agent_message_only", "workflow_timestamp_only", "cleanup_request_only", "unmerged_pr_only"]
)
def test_insufficient_stop_proofs_rejected(proof: str) -> None:
    result = fin.confirm_writer_stop(_writer(stop_proof=proof, stop_method="runtime_quiescence"))
    assert result["stopped"] is False
    assert result["reasonCode"] == "insufficient_stop_proof"


def test_writer_stop_requires_method_and_evidence() -> None:
    assert fin.confirm_writer_stop({"writers_stopped": False})["stopped"] is False
    assert fin.confirm_writer_stop(_writer(stop_evidence=""))["reasonCode"] == "stop_evidence_missing"
    assert fin.confirm_writer_stop(_writer(stop_method=""))["reasonCode"] == "stop_method_missing"
    assert fin.confirm_writer_stop(_writer())["stopped"] is True


@pytest.mark.parametrize(
    "evidence",
    [
        _mutations(push_outcome="unknown"),
        _mutations(pr_outcome="pending"),
        _mutations(merge_outcome="lost_response", merge_outcome_absent=False),
        _mutations(pr_outcome=""),
    ],
)
def test_unknown_mutation_outcomes_block_release(evidence: dict[str, Any]) -> None:
    result = fin.settle_shared_mutations(evidence)
    assert result["settled"] is False
    assert result["reasonCode"] == "mutations_unsettled"


def test_preservation_failure_blocks_and_retains_workspace() -> None:
    result = fin.preserve_authoritative_output({"save_method": "pr_head_verified"})
    assert result["preserved"] is False
    assert result["workspaceRetained"] is True
    failed_plan = fin.plan_failed_attempt_finalization(
        execution_event="failed",
        writer_evidence=_writer(),
        mutation_evidence=_mutations(),
        preservation_evidence={"save_method": "pr_head_verified"},
        disposition_evidence={"portable_work_safe": True, "preservation_verified": True},
    )
    assert failed_plan.releasable is False
    assert failed_plan.workspace_retained is True


def test_github_outage_never_false_release() -> None:
    plan = fin.plan_failed_attempt_finalization(
        execution_event="failed",
        writer_evidence=_writer(),
        mutation_evidence=_mutations(),
        preservation_evidence=_preserved(),
        disposition_evidence={"portable_work_safe": True, "preservation_verified": True, "github_unavailable": True},
    )
    assert plan.releasable is False
    assert plan.reason_code == "github_unavailable"
    assert plan.pending_sync is not None
    assert plan.workspace_retained is True


# -- Req 4: disposition matrix -------------------------------------------------


def test_disposition_recovery_needed_for_portable_work() -> None:
    choice = fin.choose_disposition({"portable_work_safe": True, "preservation_verified": True})
    assert choice["disposition"] == fin.DISPOSITION_RECOVERY_NEEDED


def test_disposition_available_for_safe_no_work_retry() -> None:
    choice = fin.choose_disposition({"trustworthy_no_work": True, "fresh_retry_allowed": True})
    assert choice["disposition"] == fin.DISPOSITION_AVAILABLE


def test_disposition_code_review_only_with_gates() -> None:
    choice = fin.choose_disposition({"gates_satisfied": True, "pr_url_verified": True})
    assert choice["disposition"] == fin.DISPOSITION_CODE_REVIEW


def test_partial_pr_alone_never_code_review() -> None:
    assert fin.partial_pr_justifies_code_review(gates_satisfied=False, pr_url_verified=True)["allowed"] is False
    assert fin.partial_pr_justifies_code_review(gates_satisfied=True, pr_url_verified=False)["allowed"] is False


def test_disposition_closed_and_needs_attention() -> None:
    assert fin.choose_disposition({"completion_verified": True})["disposition"] == fin.DISPOSITION_CLOSED
    assert fin.choose_disposition({"budget_exhausted": True})["disposition"] == fin.DISPOSITION_NEEDS_ATTENTION
    assert fin.choose_disposition({"unsafe_recovery": True})["disposition"] == fin.DISPOSITION_NEEDS_ATTENTION
    assert fin.choose_disposition({"unresolved_decision": True})["disposition"] == fin.DISPOSITION_NEEDS_ATTENTION


# -- Req 3: terminal comment ---------------------------------------------------


def test_terminal_comment_carries_required_sections() -> None:
    body = fin.render_terminal_comment(
        repository="o/r",
        issue_number=4179,
        attempt_id="att_test",
        primary_outcome="failed",
        pr_url="https://github.com/o/r/pull/7",
        pr_head_sha="a" * 40,
        pr_base="main",
        met_requirements=["req-a"],
        remaining_requirements=["req-b"],
        retry_history="1 failed / 0 no-progress",
        next_action="continue-implementation",
        disposition="to_recovery_needed",
    )
    for section in ("PR https://github.com/o/r/pull/7", "req-a", "req-b", "failed", "continue-implementation", "1 failed"):
        assert section in body


def test_full_plan_recovery_needed() -> None:
    plan = fin.plan_failed_attempt_finalization(
        repository="o/r",
        issue_number=4179,
        execution_event="failed",
        writer_evidence=_writer(),
        mutation_evidence=_mutations(),
        preservation_evidence=_preserved(),
        disposition_evidence={"portable_work_safe": True, "preservation_verified": True, "handoff_published": True},
        current_labels=["status: in-progress"],
        attempt_id="att_test",
        met_requirements=["req-a"],
        remaining_requirements=["req-b"],
        retry_history="1 failed",
        next_action="continue-implementation",
        reason="terminal failure with portable work",
    )
    assert plan.releasable is True
    assert plan.disposition == "to_recovery_needed"
    assert plan.transition is not None and plan.transition["allowed"] is True
    assert plan.mutation is not None
    assert "req-b" in plan.terminal_comment


def test_full_plan_available_no_work() -> None:
    plan = fin.plan_failed_attempt_finalization(
        execution_event="failed",
        writer_evidence=_writer(),
        mutation_evidence=_mutations(),
        preservation_evidence={"save_method": "explicit_no_work", "trustworthy_no_work": True},
        disposition_evidence={"trustworthy_no_work": True, "fresh_retry_allowed": True, "terminal_proof": True},
        reason="safe no-work retry",
    )
    assert plan.releasable is True
    assert plan.disposition == "to_available"


# -- Req 5: objective vs execution ---------------------------------------------


def test_verified_objective_never_rebought_on_reporting_failure() -> None:
    result = fin.separate_objective_from_execution(
        execution_outcome="failed", objective_verified=True, auxiliary_failures=["publication"]
    )
    assert result["rebuyImplementation"] is False
    assert result["auxiliaryFailures"] == ["publication"]


def test_unverified_objective_keeps_requirements_open() -> None:
    result = fin.separate_objective_from_execution(execution_outcome="failed", objective_verified=False)
    assert result["rebuyImplementation"] is True


# -- Req 6: unfinalized / pending-sync / successor ------------------------------


@pytest.mark.parametrize("trigger", ["abrupt_termination", "disconnected_device", "unknown_mutation"])
def test_abrupt_triggers_are_potentially_unfinalized(trigger: str) -> None:
    assert fin.classify_potentially_unfinalized({"trigger": trigger})["potentiallyUnfinalized"] is True
    plan = fin.plan_failed_attempt_finalization(
        execution_event=trigger,
        writer_evidence=_writer(),
        mutation_evidence=_mutations(),
        preservation_evidence=_preserved(),
        disposition_evidence={"portable_work_safe": True, "preservation_verified": True},
    )
    assert plan.releasable is False
    assert plan.reason_code == "potentially_unfinalized"


def test_pending_sync_recorded_and_successor_respected() -> None:
    pending = fin.record_pending_sync(reason="GitHub outage")
    assert pending["pendingSync"] is True and pending["workspaceRetained"] is True
    abandon = fin.should_abandon_for_successor(intended_from_settled="in_progress", observed_settled="recovery_needed")
    assert abandon["abandon"] is True
    hold = fin.should_abandon_for_successor(intended_from_settled="in_progress", observed_settled="needs_attention")
    assert hold["abandon"] is True
    same = fin.should_abandon_for_successor(intended_from_settled="in_progress", observed_settled="in_progress")
    assert same["abandon"] is False


def test_replayed_finalizer_respects_hold_and_history() -> None:
    # A late finalizer observing a hold or successor abandons new mutations.
    assert fin.should_abandon_for_successor(intended_from_settled="in_progress", observed_settled="needs_attention")["abandon"] is True
    assert fin.should_abandon_for_successor(intended_from_settled="in_progress", observed_settled="closed")["abandon"] is True


# -- Req 7: completion routing ---------------------------------------------------


def test_pr_only_and_parent_routing() -> None:
    pr_only = fin.route_completion_handoff(completion_mode="pr_only_handoff")
    assert pr_only["route"] == fin.COMPLETION_PR_ONLY
    assert pr_only["mergeAuthorized"] is False
    parent = fin.route_completion_handoff(completion_mode="parent_pr_and_merge")
    assert parent["mergeAuthorized"] is True
    review_failure = fin.route_completion_handoff(completion_mode="pr_only_handoff", review_owner_ended=True)
    assert review_failure["route"] == "missing_finalization_phase"
    assert review_failure["mergeAuthorized"] is False
    hold = fin.route_completion_handoff(completion_mode="parent_pr_and_merge", cancellation_hold=True)
    assert hold["route"] == "explicit_hold"
    assert hold["mergeAuthorized"] is False
    unknown = fin.route_completion_handoff(completion_mode="bogus")
    assert unknown["mergeAuthorized"] is False


# -- Activity boundary with fake service ---------------------------------------


class _FakeService:
    """Minimal fake of the GitHubService surface used by the finalizer."""

    def __init__(
        self,
        *,
        issue_labels: list[str] | None = None,
        issue_state: str = "open",
        fail_add: str = "",
        fail_remove: str = "",
        fail_create: str = "",
        fail_close: str = "",
        fail_update: str = "",
        unknown_update: bool = False,
        seed_comments: list[dict[str, Any]] | None = None,
    ) -> None:
        self.labels = list(issue_labels or ["status: in-progress"])
        self.state = issue_state
        self.fail_add = fail_add
        self.fail_remove = fail_remove
        self.fail_create = fail_create
        self.fail_close = fail_close
        self.fail_update = fail_update
        self.unknown_update = unknown_update
        self.comments: list[dict[str, Any]] = [dict(item) for item in (seed_comments or [])]
        self.calls: list[str] = []
        self.list_calls: list[str] = []

    async def resolve_github_token(self, repo: str = "") -> tuple[str | None, str]:
        _ = repo
        return "token", ""

    def _github_headers(self, token: str) -> dict[str, str]:
        _ = token
        return {}

    async def create_issue_comment(self, *, repo: str, issue_number: int, body: str) -> dict[str, Any]:
        _ = (repo, issue_number)
        self.calls.append("create_comment")
        if self.fail_create == "unknown":
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "lost response"}
        if self.fail_create:
            return {"ok": False, "reasonCode": "create_failed", "summary": "create failed"}
        comment_id = 100 + len(self.comments)
        self.comments.append({"id": comment_id, "body": body})
        return {"ok": True, "reasonCode": "created", "commentId": comment_id}

    async def add_issue_labels(self, *, repo: str, issue_number: int, labels: list[str]) -> dict[str, Any]:
        _ = (repo, issue_number)
        self.calls.append(f"add:{','.join(labels)}")
        if self.fail_add == "unknown":
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "lost response"}
        if self.fail_add == "denied":
            return {"ok": False, "reasonCode": "denied", "summary": "denied"}
        for label in labels:
            if label not in self.labels:
                self.labels.append(label)
        return {"ok": True, "reasonCode": "added"}

    async def remove_issue_label(self, *, repo: str, issue_number: int, label: str) -> dict[str, Any]:
        _ = (repo, issue_number)
        self.calls.append(f"remove:{label}")
        if self.fail_remove == "unknown":
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "lost response"}
        if self.fail_remove == "failed":
            return {"ok": False, "reasonCode": "remove_failed", "summary": "remove failed"}
        if label in self.labels:
            self.labels.remove(label)
        return {"ok": True, "reasonCode": "removed"}

    async def list_issue_comments(self, *, repo: str, issue_number: int) -> dict[str, Any]:
        _ = (repo, issue_number)
        self.list_calls.append("list_comments")
        return {"ok": True, "reasonCode": "listed", "comments": [dict(item) for item in self.comments]}

    async def update_issue_comment(self, *, repo: str, comment_id: int, body: str) -> dict[str, Any]:
        _ = repo
        self.calls.append(f"update:{comment_id}")
        if self.unknown_update or self.fail_update == "unknown":
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "lost response"}
        if self.fail_update == "denied":
            return {"ok": False, "reasonCode": "denied", "summary": "update denied"}
        if self.fail_update:
            return {"ok": False, "reasonCode": "update_failed", "summary": "update failed"}
        for comment in self.comments:
            if comment["id"] == comment_id:
                comment["body"] = body
        return {"ok": True, "reasonCode": "updated", "commentId": comment_id}

    async def close_issue(self, *, repo: str, issue_number: int) -> dict[str, Any]:
        _ = (repo, issue_number)
        self.calls.append("close_issue")
        if self.fail_close == "unknown":
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "lost response"}
        if self.fail_close == "denied":
            return {"ok": False, "reasonCode": "denied", "summary": "close denied"}
        if self.fail_close:
            return {"ok": False, "reasonCode": "close_failed", "summary": "close failed"}
        self.state = "closed"
        return {"ok": True, "reasonCode": "closed", "summary": "Closed."}


def _issue_reader(service: _FakeService):  # noqa: ANN202 - test helper
    async def _read(*, service: Any, repository: str, issue_number: int) -> dict[str, Any]:  # noqa: ANN202
        _ = (repository, issue_number)
        return {"ok": True, "reasonCode": "read", "issue": {"state": service.state, "labels": service.labels}}

    return _read


def _base_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "repository": "o/r",
        "issue_number": 4179,
        "execution_event": "failed",
        "from_settled": "in_progress",
        "current_labels": ["status: in-progress"],
        "writer_evidence": _writer(),
        "mutation_evidence": _mutations(),
        "preservation_evidence": _preserved(),
        "disposition_evidence": {
            "portable_work_safe": True,
            "preservation_verified": True,
            "handoff_published": True,
        },
        "attempt_id": "att_test",
        "primary_outcome": "failed",
        "met_requirements": ["req-a"],
        "remaining_requirements": ["req-b"],
        "retry_history": "1 failed",
        "next_action": "continue-implementation",
        "reason": "terminal failure with portable work",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_activity_releases_recovery_needed(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(**_base_kwargs(), service=service)
    assert result["released"] is True
    assert result["disposition"] == "to_recovery_needed"
    assert "status: recovery-needed" in service.labels
    assert "status: in-progress" not in service.labels
    assert result["workspaceRetained"] is False
    assert any("create_comment" in call for call in service.calls)


@pytest.mark.asyncio
async def test_activity_available_no_work_releases_without_status_label(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(
            preservation_evidence={"save_method": "explicit_no_work", "trustworthy_no_work": True},
            disposition_evidence={"trustworthy_no_work": True, "fresh_retry_allowed": True, "terminal_proof": True},
        ),
        service=service,
    )
    assert result["released"] is True
    assert result["disposition"] == "to_available"
    assert "status: in-progress" not in service.labels


@pytest.mark.asyncio
async def test_activity_writer_stop_failure_never_false_release() -> None:
    service = _FakeService()
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(writer_evidence={"writers_stopped": False}), service=service
    )
    assert result["released"] is False
    assert result["reasonCode"] == "writers_running"
    assert service.calls == []


@pytest.mark.asyncio
async def test_activity_unknown_mutation_and_preservation_block() -> None:
    service = _FakeService()
    unknown_mut = await acts.finalize_failed_attempt(
        **_base_kwargs(mutation_evidence=_mutations(push_outcome="unknown")), service=service
    )
    assert unknown_mut["released"] is False
    assert unknown_mut["reasonCode"] == "mutations_unsettled"
    bad_preserve = await acts.finalize_failed_attempt(
        **_base_kwargs(preservation_evidence={"save_method": "pr_head_verified"}), service=service
    )
    assert bad_preserve["released"] is False
    assert bad_preserve["workspaceRetained"] is True


@pytest.mark.asyncio
async def test_activity_interrupt_after_every_mutation_stays_pending(monkeypatch) -> None:
    # Interrupt after comment create: unknown label add.
    service = _FakeService(fail_add="unknown")
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    pending_add = await acts.finalize_failed_attempt(**_base_kwargs(), service=service)
    assert pending_add["released"] is False
    assert pending_add["reasonCode"] == "label_unknown"
    assert pending_add["pendingSync"] is not None
    assert pending_add["commentId"] is not None  # proposed comment durable for repair

    # Interrupt after label add: failed comment create never discards workspace.
    failing = _FakeService(fail_create="create_failed")
    failed_create = await acts.finalize_failed_attempt(**_base_kwargs(), service=failing)
    assert failed_create["released"] is False
    assert failed_create["workspaceRetained"] is True

    # Interrupt after labels: unknown read-back stays pending, not released.
    async def _unknown_read(*, service: Any, repository: str, issue_number: int) -> dict[str, Any]:
        _ = (service, repository, issue_number)
        return {"ok": False, "reasonCode": "outcome_unknown", "summary": "lost"}

    service_ok = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _unknown_read)
    pending_read = await acts.finalize_failed_attempt(**_base_kwargs(), service=service_ok)
    assert pending_read["released"] is False
    assert pending_read["reasonCode"] == "read_back_unknown"


@pytest.mark.asyncio
async def test_activity_denied_label_preserves_proposed_comment(monkeypatch) -> None:
    service = _FakeService(fail_add="denied")
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(**_base_kwargs(), service=service)
    assert result["released"] is False
    assert result["reasonCode"] == "mutation_denied"
    assert result["commentId"] is not None


# -- Five failure stages ---------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_stage_before_edits_safe_no_work(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(
            execution_event="failed",
            preservation_evidence={"save_method": "explicit_no_work", "trustworthy_no_work": True},
            disposition_evidence={"trustworthy_no_work": True, "fresh_retry_allowed": True, "terminal_proof": True},
            met_requirements=[],
            remaining_requirements=["req-a", "req-b"],
            retry_history="0 failed",
            next_action="fresh-retry",
        ),
        service=service,
    )
    assert result["released"] is True
    assert result["disposition"] == "to_available"


@pytest.mark.asyncio
async def test_failure_stage_after_partial_work_recovery_needed(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(**_base_kwargs(), service=service)
    assert result["released"] is True
    assert result["disposition"] == "to_recovery_needed"


@pytest.mark.asyncio
async def test_failure_stage_after_pr_creation_recovery_needed(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(
            preservation_evidence=_preserved(),
            disposition_evidence={"portable_work_safe": True, "preservation_verified": True, "handoff_published": True},
            met_requirements=["req-a"],
            remaining_requirements=["req-b"],
        ),
        service=service,
    )
    assert result["released"] is True
    assert "pull/7" in service.comments[0]["body"]


@pytest.mark.asyncio
async def test_failure_stage_after_accepted_verification_code_review(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(
            execution_event="failed",
            disposition_evidence={"gates_satisfied": True, "pr_url_verified": True},
        ),
        service=service,
    )
    assert result["released"] is True
    assert result["disposition"] == "to_code_review"


@pytest.mark.asyncio
async def test_failure_stage_after_merge_closed_without_rebuy(monkeypatch) -> None:
    service = _FakeService(issue_labels=["status: code-review"], issue_state="open")
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(
            execution_event="failed",
            from_settled="code_review",
            current_labels=["status: code-review"],
            disposition_evidence={"completion_verified": True},
        ),
        service=service,
    )
    # Failure after merge with verified objective completion releases to
    # closed: the Activity executes the close step, observes closed on
    # read-back, and publishes the released terminal comment.
    assert result["released"] is True
    assert result["reasonCode"] == "released"
    assert result["disposition"] == "to_closed"
    assert result["mutationOutcome"] is not None
    assert result["mutationOutcome"]["outcome"] == "applied"
    assert "close_issue" in service.calls
    assert service.state == "closed"
    assert "status: done" in service.labels
    assert "status: code-review" not in service.labels
    assert service.comments and "pull/7" in service.comments[0]["body"]
    assert "Released:" in service.comments[0]["body"]
    assert result["workspaceRetained"] is False
    # Objective satisfaction is never re-bought because reporting ran late:
    # the execution outcome stays failed while the objective stays verified.
    separation = fin.separate_objective_from_execution(
        execution_outcome="failed", objective_verified=True, auxiliary_failures=["publication"]
    )
    assert separation["rebuyImplementation"] is False


@pytest.mark.asyncio
async def test_activity_close_unknown_stays_pending(monkeypatch) -> None:
    service = _FakeService(issue_labels=["status: code-review"], fail_close="unknown")
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(
            execution_event="failed",
            from_settled="code_review",
            current_labels=["status: code-review"],
            disposition_evidence={"completion_verified": True},
        ),
        service=service,
    )
    assert result["released"] is False
    assert result["reasonCode"] == "close_unknown"
    assert result["workspaceRetained"] is True
    assert result["pendingSync"] is not None
    assert result["commentId"] is not None  # proposed comment durable for repair


@pytest.mark.asyncio
async def test_activity_close_denied_never_false_release(monkeypatch) -> None:
    service = _FakeService(issue_labels=["status: code-review"], fail_close="denied")
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(
            execution_event="failed",
            from_settled="code_review",
            current_labels=["status: code-review"],
            disposition_evidence={"completion_verified": True},
        ),
        service=service,
    )
    assert result["released"] is False
    assert result["reasonCode"] == "denied"
    assert result["workspaceRetained"] is True


# -- Req 1: controlling-outcome mapping ------------------------------------------


@pytest.mark.parametrize(
    "outcome", ["success", "failed", "exhausted_retries", "blocked", "cancelled", "terminal_failure"]
)
def test_controlling_outcomes_map_to_finalize(outcome: str) -> None:
    mapped = fin.execution_event_for_controlling_outcome(outcome)
    assert mapped["action"] == "finalize"
    assert fin.should_finalize_controlling_attempt(mapped["event"])["finalize"] is True


@pytest.mark.parametrize(
    "outcome", ["internal_retry", "remediation_iteration", "review_wait", "step_failure", "awaiting_review"]
)
def test_controlling_outcomes_retain_internal_attempts(outcome: str) -> None:
    mapped = fin.execution_event_for_controlling_outcome(outcome)
    assert mapped["action"] == "retain"
    assert fin.should_finalize_controlling_attempt(mapped["event"])["finalize"] is False


@pytest.mark.parametrize("outcome", ["terminated", "timed_out", "disconnected", "abrupt_termination"])
def test_abrupt_outcomes_are_potentially_unfinalized(outcome: str) -> None:
    mapped = fin.execution_event_for_controlling_outcome(outcome)
    assert mapped["action"] == "potentially_unfinalized"
    plan = fin.plan_failed_attempt_finalization(
        execution_event=mapped["event"],
        writer_evidence=_writer(),
        mutation_evidence=_mutations(),
        preservation_evidence=_preserved(),
        disposition_evidence={"portable_work_safe": True, "preservation_verified": True},
    )
    assert plan.releasable is False
    assert plan.reason_code == "potentially_unfinalized"
    assert plan.workspace_retained is True


@pytest.mark.parametrize("outcome", ["mystery-outcome", "", None, 123])
def test_unknown_outcomes_never_release(outcome: Any) -> None:
    mapped = fin.execution_event_for_controlling_outcome(outcome)
    assert mapped["action"] == "no_release"
    plan = fin.plan_failed_attempt_finalization(
        execution_event=mapped["event"] or outcome,
        writer_evidence=_writer(),
        mutation_evidence=_mutations(),
        preservation_evidence=_preserved(),
        disposition_evidence={"portable_work_safe": True, "preservation_verified": True},
    )
    assert plan.releasable is False
    assert plan.workspace_retained is True


# -- Req 1: durable failed-path tool boundary ------------------------------------


@pytest.mark.asyncio
async def test_failed_path_tool_releases_recovery_needed(monkeypatch) -> None:
    from moonmind.workflows.temporal import story_output_tools as tools

    service = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await tools.finalize_github_issue_failed_attempt(
        {
            "repository": "o/r",
            "issueNumber": 4179,
            "executionEvent": "failed",
            "fromSettled": "in_progress",
            "currentLabels": ["status: in-progress"],
            "writerEvidence": _writer(),
            "mutationEvidence": _mutations(),
            "preservationEvidence": _preserved(),
            "dispositionEvidence": {
                "portable_work_safe": True,
                "preservation_verified": True,
                "handoff_published": True,
            },
            "attemptId": "att_test",
            "primaryOutcome": "failed",
            "metRequirements": ["req-a"],
            "remainingRequirements": ["req-b"],
            "retryHistory": "1 failed",
            "nextAction": "continue-implementation",
            "reason": "terminal failure with portable work",
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["released"] is True
    assert result.outputs["disposition"] == "to_recovery_needed"
    assert result.outputs["mappingAction"] == "finalize"
    assert result.outputs["mergeAuthorized"] is False
    assert result.outputs["workspaceRetained"] is False
    assert "status: recovery-needed" in service.labels


@pytest.mark.asyncio
async def test_failed_path_tool_cancellation_holds_without_rerun(monkeypatch) -> None:
    from moonmind.workflows.temporal import story_output_tools as tools

    service = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await tools.finalize_github_issue_failed_attempt(
        {
            "repository": "o/r",
            "issueNumber": 4179,
            "executionEvent": "cancelled",
            "writerEvidence": _writer(),
            "mutationEvidence": _mutations(),
            "preservationEvidence": _preserved(),
            "dispositionEvidence": {
                "intentional_cancellation": True,
                "blocking_reason": "operator hold",
            },
            "reason": "intentional cancellation",
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["released"] is True
    assert result.outputs["disposition"] == "to_needs_attention"
    assert result.outputs["mergeAuthorized"] is False


@pytest.mark.asyncio
async def test_failed_path_tool_internal_event_retained_without_effects() -> None:
    from moonmind.workflows.temporal import story_output_tools as tools

    service = _FakeService()
    result = await tools.finalize_github_issue_failed_attempt(
        {
            "repository": "o/r",
            "issueNumber": 4179,
            "executionEvent": "remediation_iteration",
            "writerEvidence": _writer(),
            "mutationEvidence": _mutations(),
            "preservationEvidence": _preserved(),
            "dispositionEvidence": {"portable_work_safe": True, "preservation_verified": True},
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["released"] is False
    assert result.outputs["mappingAction"] == "retain"
    assert service.calls == []
    assert service.comments == []


@pytest.mark.asyncio
async def test_failed_path_tool_rejects_bad_issue_inputs() -> None:
    from moonmind.workflows.temporal import story_output_tools as tools

    service = _FakeService()
    result = await tools.finalize_github_issue_failed_attempt(
        {"repository": "", "issueNumber": 0, "executionEvent": "failed"},
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert service.calls == []


def test_failed_path_tool_registered_in_dispatcher() -> None:
    from moonmind.workflows.temporal import story_output_tools as tools

    class _Dispatcher:
        def __init__(self) -> None:
            self.skills: dict[str, Any] = {}

        def register_skill(self, *, skill_name: str, handler: Any) -> None:
            self.skills[skill_name] = handler

    dispatcher = _Dispatcher()
    tools.register_story_output_tool_handlers(dispatcher)
    assert tools.GITHUB_FINALIZE_FAILED_ATTEMPT_TOOL_NAME in dispatcher.skills
    assert "github.update_issue_status" in dispatcher.skills


# -- Replay / in-flight payload compatibility -------------------------------------


def test_legacy_blank_and_partial_payloads_degrade_safely() -> None:
    # A previous attempt/finalization payload shape without the new mapper
    # fields, with blank or unknown values, degrades to a safe
    # non-releasing outcome: no release, workspace retained, no crash.
    blank = fin.plan_failed_attempt_finalization(
        execution_event="",
        writer_evidence=_writer(),
        mutation_evidence=_mutations(),
        preservation_evidence=_preserved(),
        disposition_evidence={},
    )
    assert blank.releasable is False
    assert blank.workspace_retained is True
    # Unknown future disposition keys are ignored: the default is attention
    # with no automatic replacement work, never a silent rerun.
    future = fin.choose_disposition({"some_future_field": True, "another_new_flag": "yes"})
    assert future["disposition"] == fin.DISPOSITION_NEEDS_ATTENTION
    assert future["schedulesReplacement"] is False
    # Unknown completion modes grant no merge authority in failure cleanup.
    unknown_route = fin.route_completion_handoff(completion_mode="some_future_mode")
    assert unknown_route["mergeAuthorized"] is False
    assert unknown_route["route"] == "needs_decision"


def test_replayed_finalizer_respects_closed_successor_and_hold() -> None:
    # A late finalizer that rereads shared state and observes a closed issue
    # or a needs-attention hold abandons new mutations instead of
    # overwriting the successor's status.
    assert fin.should_abandon_for_successor(intended_from_settled="code_review", observed_settled="closed")["abandon"] is True
    assert fin.should_abandon_for_successor(intended_from_settled="in_progress", observed_settled="needs_attention")["abandon"] is True
    assert fin.should_abandon_for_successor(intended_from_settled="in_progress", observed_settled="in_progress")["abandon"] is False
    # Unknown auxiliary failure kinds are reported, not merged into the
    # primary outcome or treated as release evidence.
    separated = fin.separate_objective_from_execution(
        execution_outcome="failed", objective_verified=False, auxiliary_failures=["some_future_kind"]
    )
    assert separated["unknownAuxiliaryKinds"] == ["some_future_kind"]
    assert separated["rebuyImplementation"] is True


# -- Codex review remediation (PR #4244 follow-up) -------------------------------
# Covers the P1 findings on the first finalizer revision: strict boolean
# evidence parsing, cancellation-hold gating, pre-mutation reads with
# successor abandonment, outbound scanning, canonical attempt-comment reuse,
# pending (never released) comment-update failures, pending sync after every
# partial mutation failure, and origin-aware transition evidence.


@pytest.mark.parametrize("value", ["false", "False", "FALSE", "  false  ", "0", "no", "off", "", "  ", "maybe"])
def test_truthy_rejects_false_like_strings(value: Any) -> None:
    assert fin._truthy(value) is False


@pytest.mark.parametrize("value", [True, "true", "True", "TRUE", "  yes  ", "Yes", "YES", "1", 1, 1.0])
def test_truthy_accepts_only_approved_true_tokens(value: Any) -> None:
    assert fin._truthy(value) is True


@pytest.mark.parametrize("value", [False, None, 0, 0.0])
def test_truthy_rejects_falsy_non_strings(value: Any) -> None:
    assert fin._truthy(value) is False


def test_cancellation_hold_blocks_release_despite_releasable_evidence() -> None:
    plan = fin.plan_failed_attempt_finalization(
        execution_event="failed",
        writer_evidence=_writer(),
        mutation_evidence=_mutations(),
        preservation_evidence=_preserved(),
        disposition_evidence={"trustworthy_no_work": True, "fresh_retry_allowed": True, "terminal_proof": True},
        cancellation_hold=True,
        reason="operator hold with releasable evidence",
    )
    assert plan.releasable is False
    assert plan.reason_code == "cancellation_hold"
    assert plan.workspace_retained is True


@pytest.mark.asyncio
async def test_activity_cancellation_hold_makes_no_github_effects(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(**_base_kwargs(cancellation_hold=True), service=service)
    assert result["released"] is False
    assert result["reasonCode"] == "cancellation_hold"
    assert result["workspaceRetained"] is True
    assert service.calls == []
    assert service.comments == []


@pytest.mark.asyncio
async def test_tool_cancellation_hold_string_values(monkeypatch) -> None:
    from moonmind.workflows.temporal import story_output_tools as tools

    def _inputs(hold: Any) -> dict[str, Any]:
        return {
            "repository": "o/r",
            "issueNumber": 4179,
            "executionEvent": "failed",
            "fromSettled": "in_progress",
            "currentLabels": ["status: in-progress"],
            "writerEvidence": _writer(),
            "mutationEvidence": _mutations(),
            "preservationEvidence": _preserved(),
            "dispositionEvidence": {
                "portable_work_safe": True,
                "preservation_verified": True,
                "handoff_published": True,
            },
            "attemptId": "att_test",
            "primaryOutcome": "failed",
            "metRequirements": ["req-a"],
            "remainingRequirements": ["req-b"],
            "retryHistory": "1 failed",
            "nextAction": "continue-implementation",
            "reason": "terminal failure with portable work",
            "cancellationHold": hold,
        }

    service = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    held = await tools.finalize_github_issue_failed_attempt(_inputs("true"), github_service_factory=lambda: service)
    assert held.outputs["released"] is False
    assert held.outputs["reasonCode"] == "cancellation_hold"

    releasing = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(releasing))
    loose = await tools.finalize_github_issue_failed_attempt(_inputs("false"), github_service_factory=lambda: releasing)
    assert loose.status == "COMPLETED"
    assert loose.outputs["released"] is True


@pytest.mark.asyncio
async def test_preread_successor_abandons_before_effects(monkeypatch) -> None:
    service = _FakeService(issue_labels=["status: recovery-needed"])
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(**_base_kwargs(), service=service)
    assert result["released"] is False
    assert result["reasonCode"] == "successor_observed"
    assert result["workspaceRetained"] is True
    assert service.calls == []
    assert service.comments == []
    assert service.list_calls == []


@pytest.mark.asyncio
async def test_preread_closed_issue_abandons(monkeypatch) -> None:
    service = _FakeService(issue_labels=["status: in-progress"], issue_state="closed")
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(**_base_kwargs(), service=service)
    assert result["released"] is False
    assert result["reasonCode"] == "successor_observed"
    assert service.calls == []


@pytest.mark.asyncio
async def test_omitted_current_labels_derive_from_preread(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    kwargs = _base_kwargs()
    del kwargs["current_labels"]
    result = await acts.finalize_failed_attempt(**kwargs, service=service)
    assert result["released"] is True
    assert result["disposition"] == "to_recovery_needed"
    assert "status: recovery-needed" in service.labels


@pytest.mark.asyncio
async def test_stale_caller_labels_lose_to_observed_hold(monkeypatch) -> None:
    service = _FakeService(issue_labels=["status: needs-attention"])
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(**_base_kwargs(), service=service)
    assert result["released"] is False
    assert result["reasonCode"] == "successor_observed"
    assert service.calls == []
    assert service.comments == []


@pytest.mark.asyncio
async def test_scan_blocks_secret_like_handoff(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(met_requirements=["rotate deploy token ghp_" + "A" * 36]),
        service=service,
    )
    assert result["released"] is False
    assert result["reasonCode"] == "comment_blocked_by_scan"
    assert result["workspaceRetained"] is True
    assert service.calls == []
    assert service.comments == []


def _seeded_attempt_comment(attempt_id: str) -> dict[str, Any]:
    marker = f"<!-- moonmind-github-attempt: {attempt_id} v1 -->"
    body = (
        f"{marker}\n## MoonMind attempt `{attempt_id}` — progress\n\n"
        "<!-- moonmind.github_issue_attempt.v1 metadata (machine-readable, do not edit) -->\n"
        f'```json\n{{"attemptId": "{attempt_id}"}}\n```\n'
    )
    return {"id": 7, "body": body}


@pytest.mark.asyncio
async def test_reuses_canonical_attempt_comment(monkeypatch) -> None:
    from moonmind.workflows.temporal.github_issue_attempt import extract_attempt_metadata

    attempt_id = "att_" + "b" * 24
    service = _FakeService(seed_comments=[_seeded_attempt_comment(attempt_id)])
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(attempt_id=attempt_id), service=service
    )
    assert result["released"] is True
    assert result["commentId"] == 7
    assert len(service.comments) == 1
    assert not any("create_comment" in call for call in service.calls)
    assert "update:7" in service.calls
    merged = service.comments[0]["body"]
    assert "<!-- moonmind-github-attempt:" in merged
    assert "terminal handoff" in merged
    metadata, error = extract_attempt_metadata(merged)
    assert error == ""
    assert metadata is not None and metadata.get("attemptId") == attempt_id


@pytest.mark.asyncio
async def test_conflicting_attempt_copies_stay_pending(monkeypatch) -> None:
    attempt_id = "att_" + "c" * 24
    service = _FakeService(
        seed_comments=[_seeded_attempt_comment(attempt_id), _seeded_attempt_comment(attempt_id)]
    )
    service.comments[1]["id"] = 8
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(attempt_id=attempt_id), service=service
    )
    assert result["released"] is False
    assert result["reasonCode"] == "conflicting_attempt_copies"
    assert result["pendingSync"] is not None
    assert result["workspaceRetained"] is True
    assert not any("create_comment" in call for call in service.calls)
    assert len(service.comments) == 2


@pytest.mark.asyncio
async def test_released_comment_failure_stays_pending(monkeypatch) -> None:
    service = _FakeService(fail_update="denied")
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(**_base_kwargs(), service=service)
    assert result["released"] is False
    assert result["reasonCode"] == "released_comment_failed"
    assert result["pendingSync"] is not None
    assert result["workspaceRetained"] is True


@pytest.mark.asyncio
async def test_remove_failure_records_pending_sync(monkeypatch) -> None:
    service = _FakeService(fail_remove="failed")
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(**_base_kwargs(), service=service)
    assert result["released"] is False
    assert result["reasonCode"] == "remove_failed"
    assert result["pendingSync"] is not None
    assert result["workspaceRetained"] is True


@pytest.mark.asyncio
async def test_close_failure_records_pending_sync(monkeypatch) -> None:
    service = _FakeService(issue_labels=["status: code-review"], issue_state="open", fail_close="boom")
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(
            execution_event="failed",
            from_settled="code_review",
            current_labels=["status: code-review"],
            disposition_evidence={"completion_verified": True},
        ),
        service=service,
    )
    assert result["released"] is False
    assert result["reasonCode"] == "close_failed"
    assert result["pendingSync"] is not None
    assert result["workspaceRetained"] is True


def test_code_review_recovery_maps_owner_guard_evidence() -> None:
    allowed = fin.plan_failed_attempt_finalization(
        execution_event="failed",
        from_settled="code_review",
        writer_evidence=_writer(),
        mutation_evidence=_mutations(),
        preservation_evidence=_preserved(),
        disposition_evidence={"portable_work_safe": True, "preservation_verified": True},
        current_labels=["status: code-review"],
        review_owner_ended=True,
        next_action="continue-recovery",
        reason="review owner ended with safe portable work",
    )
    assert allowed.releasable is True
    assert allowed.disposition == fin.DISPOSITION_RECOVERY_NEEDED
    # Without owner/next-action evidence the same origin still denies.
    denied = fin.plan_failed_attempt_finalization(
        execution_event="failed",
        from_settled="code_review",
        writer_evidence=_writer(),
        mutation_evidence=_mutations(),
        preservation_evidence=_preserved(),
        disposition_evidence={"portable_work_safe": True, "preservation_verified": True},
        current_labels=["status: code-review"],
        reason="review owner ended with safe portable work",
    )
    assert denied.releasable is False
    assert denied.reason_code == "missing_guard"
    # The needs-attention target from a code-review origin uses the same guards.
    held = fin.plan_failed_attempt_finalization(
        execution_event="cancelled",
        from_settled="code_review",
        writer_evidence=_writer(),
        mutation_evidence=_mutations(),
        preservation_evidence=_preserved(),
        disposition_evidence={"intentional_cancellation": True, "blocking_reason": "operator hold"},
        current_labels=["status: code-review"],
        review_owner_ended=True,
        next_action="await-operator",
        reason="intentional cancellation",
    )
    assert held.releasable is True
    assert held.disposition == fin.DISPOSITION_NEEDS_ATTENTION


@pytest.mark.asyncio
async def test_activity_code_review_recovery_releases(monkeypatch) -> None:
    service = _FakeService(issue_labels=["status: code-review"], issue_state="open")
    monkeypatch.setattr(acts, "_fetch_issue", _issue_reader(service))
    result = await acts.finalize_failed_attempt(
        **_base_kwargs(
            execution_event="failed",
            from_settled="code_review",
            current_labels=["status: code-review"],
            review_owner_ended=True,
            next_action="continue-recovery",
        ),
        service=service,
    )
    assert result["released"] is True
    assert result["disposition"] == "to_recovery_needed"
    assert "status: recovery-needed" in service.labels
