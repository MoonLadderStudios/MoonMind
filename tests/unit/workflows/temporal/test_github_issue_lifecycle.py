"""Component tests for the shared GitHub issue lifecycle boundary.

Covers issue MoonLadderStudios/MoonMind#4176 acceptance criteria:
table-driven states/transitions/guards, targeted add-before-remove mutations
preserving unrelated labels, failure injection, active-attempt evidence over
missing labels, no todo/lock reliance, and replay compatibility.
"""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.workflows.temporal import story_output_tools as story_tools
from moonmind.workflows.temporal.github_issue_lifecycle import (
    SETTLED_AVAILABLE,
    SETTLED_BLOCKED_MIXED,
    SETTLED_BLOCKED_OPEN_DONE,
    SETTLED_BLOCKED_UNKNOWN,
    SETTLED_CLOSED,
    SETTLED_CODE_REVIEW,
    SETTLED_IN_PROGRESS,
    SETTLED_NEEDS_ATTENTION,
    SETTLED_RECOVERY_NEEDED,
    attempt_evidence_blocks_admission,
    classify_mutation_outcome,
    interpret_issue,
    is_selectable_candidate,
    is_workflow_status_like,
    plan_label_mutation,
    plan_transition,
    should_abandon_retry,
)
from moonmind.workflows.temporal.story_output_tools import update_github_issue_status
from tests.unit.workflows.skills import test_acceptance_contract as acceptance_helpers

candidate = acceptance_helpers.candidate


def _bind_verified_target(service, candidate):
    repo, _, report = candidate
    acceptance_helpers.git(repo, "update-ref", "refs/heads/release", acceptance_helpers.git(repo, "rev-parse", "HEAD"))
    current = acceptance_helpers.portable.capture(repo, "example/repo", "release", target_mode=True)
    report["validatedRefs"]["acceptance"]["completionTarget"] = current["completionTarget"]

    async def read_target(repository, ref):
        assert repository == "example/repo"
        assert ref == ""
        return acceptance_helpers.portable.capture(repo, repository, "release", target_mode=True)["completionTarget"]

    service.read_repository_target = read_target
    return report


# ---------------------------------------------------------------------------
# A1: table-driven state interpretation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("labels", "state", "expected_settled", "selectable"),
    [
        ([], "open", SETTLED_AVAILABLE, True),
        (["bug", "priority: high"], "open", SETTLED_AVAILABLE, True),
        (["status: in-progress"], "open", SETTLED_IN_PROGRESS, False),
        (["status: recovery-needed"], "open", SETTLED_RECOVERY_NEEDED, True),
        (["status: code-review"], "open", SETTLED_CODE_REVIEW, False),
        (["status: needs-attention"], "open", SETTLED_NEEDS_ATTENTION, False),
        (["status: in-progress", "status: code-review"], "open", SETTLED_BLOCKED_MIXED, False),
        (["status: in-progress", "status: needs-attention"], "open", SETTLED_BLOCKED_MIXED, False),
        # Ordinary labels that merely start with "status" are not workflow states.
        (["statuspage"], "open", SETTLED_AVAILABLE, True),
        (["statusreport", "bug"], "open", SETTLED_AVAILABLE, True),
        (["status: ready"], "open", SETTLED_BLOCKED_UNKNOWN, False),
        (["status: claiming"], "open", SETTLED_BLOCKED_UNKNOWN, False),
        (["status: frobnicate"], "open", SETTLED_BLOCKED_UNKNOWN, False),
        (["status: done"], "open", SETTLED_BLOCKED_OPEN_DONE, False),
        (["status: done", "status: in-progress"], "open", SETTLED_BLOCKED_OPEN_DONE, False),
        (["status: todo"], "open", SETTLED_AVAILABLE, True),
        (["status: in-progress"], "closed", SETTLED_CLOSED, False),
        ([], "closed", SETTLED_CLOSED, False),
        (["status: done"], "closed", SETTLED_CLOSED, False),
    ],
)
def test_lifecycle_settled_states(labels: list[str], state: str, expected_settled: str, selectable: bool) -> None:
    interpretation = interpret_issue({"state": state, "labels": labels})
    assert interpretation.settled == expected_settled
    ok, _ = is_selectable_candidate({"state": state, "labels": labels})
    assert ok is selectable


def test_ordinary_labels_are_independent_of_state_machine() -> None:
    interpretation = interpret_issue({"state": "open", "labels": ["bug", "feature", "priority: high"]})
    assert interpretation.settled == SETTLED_AVAILABLE
    assert interpretation.unknown_status_labels == ()


def test_status_like_detection_requires_a_separator() -> None:
    assert is_workflow_status_like("statuspage") is False
    assert is_workflow_status_like("statusreport") is False
    assert is_workflow_status_like("status") is False
    assert is_workflow_status_like("status: frobnicate") is True
    assert is_workflow_status_like("status/frobnicate") is True
    assert is_workflow_status_like("status-frobnicate") is True
    assert is_workflow_status_like("status_frobnicate") is True
    assert is_workflow_status_like("status frobnicate") is True


def test_closed_disposition_never_reports_success() -> None:
    interpretation = interpret_issue({"state": "closed", "labels": [{"name": "status: done"}]})
    assert interpretation.settled == SETTLED_CLOSED
    assert interpretation.eligible_for_implement is False


# ---------------------------------------------------------------------------
# A1: table-driven transitions, guards, reason requirements
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("from_settled", "to_target", "evidence", "reason", "allowed", "reason_code"),
    [
        ("available", "to_in_progress",
         {"admission_passed": True, "prior_work_inspected": True}, "admit", True, "allowed"),
        ("recovery_needed", "to_in_progress",
         {"predecessor_stopped": True, "handoff_usable": True, "admission_passed": True}, "continue", True, "allowed"),
        ("in_progress", "to_in_progress", {"same_attempt_active": True}, "retry", True, "allowed"),
        ("in_progress", "to_code_review",
         {"gates_satisfied": True, "pr_url_verified": "https://github.com/o/r/pull/1"}, "publish", True, "allowed"),
        ("in_progress", "to_recovery_needed",
         {"writers_stopped": True, "handoff_published": True}, "handoff", True, "allowed"),
        ("in_progress", "to_available",
         {"writers_stopped": True, "terminal_proof": "no work, budget kept"}, "release", True, "allowed"),
        ("in_progress", "to_needs_attention", {"blocking_reason": "unsafe recovery"}, "hold", True, "allowed"),
        ("in_progress", "to_closed", {"completion_verified": True}, "done", True, "allowed"),
        ("code_review", "to_closed", {"completion_verified": True}, "done", True, "allowed"),
        ("code_review", "to_in_progress", {"admitted_repair": True}, "repair", True, "allowed"),
        ("needs_attention", "to_available",
         {"authorized_resolution": True, "preserved_work_disposition": "kept",
          "terminal_proof": "stopped, no preserved work, retry budget retained"}, "resolve", True, "allowed"),
        # Missing guards block admission rather than normalizing optimistically.
        ("available", "to_in_progress", {}, "admit", False, "missing_guard"),
        ("available", "to_in_progress",
         {"admission_passed": True, "prior_work_inspected": True}, "", False, "missing_guard"),
        ("in_progress", "to_code_review", {"gates_satisfied": True}, "publish", False, "missing_guard"),
        ("in_progress", "to_available", {"writers_stopped": True}, "release", False, "missing_guard"),
        ("in_progress", "to_available",
         {"writers_stopped": True, "terminal_proof": ""}, "release", False, "missing_guard"),
        ("needs_attention", "to_available",
          {"authorized_resolution": True, "preserved_work_disposition": "kept"}, "resolve", False, "missing_guard"),
        # Attention resolves to code review only with reviewable evidence.
        ("needs_attention", "to_code_review",
          {"authorized_resolution": True, "preserved_work_disposition": "kept"}, "review", False, "missing_guard"),
        ("needs_attention", "to_code_review",
          {"authorized_resolution": True, "preserved_work_disposition": "kept",
           "pr_url_verified": "https://github.com/o/r/pull/1", "gates_satisfied": True},
          "review", True, "allowed"),
        # Idempotent close retry reconciles an already-closed issue.
        ("closed", "to_closed", {"completion_verified": True}, "retry", True, "allowed"),
        ("closed", "to_closed", {}, "retry", False, "missing_guard"),
        # Idempotent code-review retry reconciles to already_applied instead
        # of failing the workflow: already in review with verified PR
        # evidence is success. All other unlisted pairs still need an
        # explicit decision; there is no reason-only release to Available.
        ("code_review", "to_code_review",
         {"gates_satisfied": True, "pr_url_verified": "https://github.com/o/r/pull/1"}, "publish", True, "allowed"),
        ("code_review", "to_code_review", {"gates_satisfied": True}, "publish", False, "missing_guard"),
        ("code_review", "to_code_review",
         {"gates_satisfied": True, "pr_url_verified": "https://github.com/o/r/pull/1"}, "", False, "missing_guard"),
        # Unsupported transitions require an explicit authority decision.
        ("available", "to_recovery_needed", {"admission_passed": True}, "x", False, "unsupported_transition"),
        # Terminal and blocked states deny new transitions.
        ("closed", "to_in_progress", {"admission_passed": True}, "x", False, "closed_terminal"),
        ("blocked_mixed", "to_in_progress", {"admission_passed": True}, "x", False, "reconciliation_required"),
        ("blocked_unknown", "to_in_progress", {"admission_passed": True}, "x", False, "reconciliation_required"),
        ("blocked_open_done", "to_in_progress", {"admission_passed": True}, "x", False, "reconciliation_required"),
    ],
)
def test_transition_table(from_settled: str, to_target: str, evidence: dict[str, Any],
                           reason: str, allowed: bool, reason_code: str) -> None:
    decision = plan_transition(from_settled=from_settled, to_target=to_target,
                               evidence=evidence, reason=reason)
    assert decision.allowed is allowed
    assert decision.reason_code == reason_code


def test_transition_decision_is_typed_with_evidence_requirements() -> None:
    decision = plan_transition(from_settled="available", to_target="to_in_progress",
                               evidence={}, reason="admit")
    assert decision.missing_evidence == ("admission_passed", "prior_work_inspected")
    assert decision.to_dict()["fromSettled"] == "available"


# ---------------------------------------------------------------------------
# Mutation planning: add-before-remove, unrelated labels untouched
# ---------------------------------------------------------------------------


def test_mutation_plan_adds_destination_before_removing_old_blocker() -> None:
    plan = plan_label_mutation(from_settled="in_progress", to_target="to_code_review",
                               current_labels=["bug", "status: in-progress"])
    assert plan.ordered_operations() == [
        ("add", "status: code-review"),
        ("remove", "status: in-progress"),
    ]


def test_mutation_to_available_has_no_destination_label() -> None:
    plan = plan_label_mutation(from_settled="in_progress", to_target="to_available",
                               current_labels=["status: in-progress"])
    assert plan.labels_to_add == ()
    assert plan.labels_to_remove == ("status: in-progress",)


def test_mutation_plan_never_touches_unrelated_or_todo_labels() -> None:
    plan = plan_label_mutation(from_settled="available", to_target="to_in_progress",
                               current_labels=["bug", "status: todo"])
    assert plan.labels_to_add == ("status: in-progress",)
    assert plan.labels_to_remove == ()


def test_mutation_to_closed_adds_done_destination_before_removing_blocker() -> None:
    plan = plan_label_mutation(from_settled="code_review", to_target="to_closed",
                               current_labels=["bug", "status: code-review"])
    assert plan.labels_to_add == ("status: done",)
    assert plan.labels_to_remove == ("status: code-review",)
    assert plan.close_issue is True
    assert plan.ordered_operations() == [
        ("add", "status: done"),
        ("remove", "status: code-review"),
    ]


def test_mutation_to_needs_attention_preserves_active_ownership() -> None:
    plan = plan_label_mutation(from_settled="in_progress", to_target="to_needs_attention",
                               current_labels=["status: in-progress"])
    assert plan.labels_to_add == ("status: needs-attention",)
    assert plan.labels_to_remove == ()
    assert plan.close_issue is False


# ---------------------------------------------------------------------------
# Outcome taxonomy + obsolete-retry abandonment
# ---------------------------------------------------------------------------


def test_outcome_taxonomy() -> None:
    plan = plan_label_mutation(from_settled="available", to_target="to_in_progress", current_labels=[])
    assert classify_mutation_outcome(plan=plan, read_back=None, transport_error="ReadTimeout").outcome == "outcome_unknown"
    assert classify_mutation_outcome(plan=plan, read_back={}, denied=True).outcome == "denied"
    assert classify_mutation_outcome(
        plan=plan, read_back={"state": "open", "labels": ["status: in-progress"]}).outcome == "applied"
    assert classify_mutation_outcome(
        plan=plan, read_back={"state": "open", "labels": ["moonspec"]}).outcome == "incomplete"


def test_obsolete_retry_abandoned_on_successor_hold_or_conflict() -> None:
    for labels, state in [(["status: code-review"], "open"), (["status: needs-attention"], "open"),
                          ((["status: in-progress", "status: code-review"]), "open"), ([], "closed")]:
        observed = interpret_issue({"state": state, "labels": labels})
        abandon, _ = should_abandon_retry(intended_from_settled="in_progress", observed=observed)
        if observed.settled != "in_progress":
            assert abandon is True
    observed = interpret_issue({"state": "open", "labels": ["status: in-progress"]})
    assert should_abandon_retry(intended_from_settled="in_progress", observed=observed) == (False, "")


# ---------------------------------------------------------------------------
# A4: attempt evidence over missing labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("context", [
    {"has_unresolved_active_attempt": True},
    {"hasUnresolvedActiveAttempt": True},
    {"unresolved_active_attempt": True},
])
def test_unresolved_attempt_evidence_blocks_despite_missing_label(context: dict[str, Any]) -> None:
    assert attempt_evidence_blocks_admission(context) is True
    ok, _ = is_selectable_candidate({"state": "open", "labels": []}, context)
    assert ok is False


def test_no_attempt_evidence_keeps_available_selectable() -> None:
    assert attempt_evidence_blocks_admission(None) is False
    assert attempt_evidence_blocks_admission({}) is False
    ok, _ = is_selectable_candidate({"state": "open", "labels": []}, None)
    assert ok is True


def test_linked_retry_history_blocks_admission_when_budget_is_spent() -> None:
    first = "att_" + "1" * 24
    second = "att_" + "2" * 24
    linked = [
        {"attemptId": first, "outcome": "failed", "policyLineage": "policy-v1"},
        {
            "attemptId": second,
            "outcome": "failed",
            "predecessorAttemptId": first,
            "policyLineage": "policy-v1",
        },
    ]
    blocked_context: dict[str, Any] = {
        "linkedAttempts": linked,
        "retryPolicy": {"maxAttempts": 2, "lineageRef": "policy-v1"},
    }
    assert attempt_evidence_blocks_admission(blocked_context) is True
    ok, _ = is_selectable_candidate({"state": "open", "labels": []}, blocked_context)
    assert ok is False
    allowed_context: dict[str, Any] = {
        "linked_attempts": linked,
        "retry_policy": {"maxAttempts": 5, "lineageRef": "policy-v1"},
    }
    assert attempt_evidence_blocks_admission(allowed_context) is False
    gap_context: dict[str, Any] = {
        "linkedAttempts": [
            {"attemptId": second, "outcome": "pending", "policyLineage": "policy-v1"},
            {"attemptId": first, "lineageGap": True, "missingPredecessor": first},
        ],
        "retryPolicy": {"maxAttempts": 5, "lineageRef": "policy-v1"},
    }
    assert attempt_evidence_blocks_admission(gap_context) is True


# ---------------------------------------------------------------------------
# Tool-level tests with targeted-mutation fakes
# ---------------------------------------------------------------------------


class _LifecycleFakeService:
    """Fake trusted GitHub boundary with targeted label operations."""

    def __init__(self, initial_labels: list[str] | None = None) -> None:
        self.token_requests: list[str] = []
        self.labels = list(initial_labels) if initial_labels is not None else ["moonspec"]
        self.issue_state = "open"
        self.operations: list[tuple[str, str]] = []
        self.readiness_result: dict[str, Any] | None = None
        self.fail_add: str | None = None
        self.fail_remove: str | None = None
        self.create_issue_requests: list[dict[str, Any]] = []
        # MoonLadderStudios/MoonMind#4225: simulate a GitHub label write that
        # reports success without applying, so read-back observes an
        # incomplete steering mutation.
        self.add_without_apply: bool = False

    async def resolve_github_token(self, *, repo: str):
        self.token_requests.append(repo)
        return "ghs-test", None

    def _github_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _github_permission_summary(self, response) -> str:
        return f"github status {response.status_code}"

    async def check_issue_label_readiness(self, *, repo: str, issue_number: int,
                                          required_labels: list[str], github_token: str | None = None):
        if self.readiness_result is not None:
            return dict(self.readiness_result)
        return {"ready": True, "reasonCode": "ready", "summary": "ready"}

    async def add_issue_labels(self, *, repo: str, issue_number: int,
                               labels: list[str], github_token: str | None = None):
        if self.fail_add == "denied":
            return {"ok": False, "reasonCode": "denied", "summary": "Label add failed with HTTP 403."}
        if self.fail_add == "unknown":
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "Label add result unknown."}
        if self.add_without_apply:
            # Report success without mutating: read-back will not observe the
            # destination label, exercising the mutation_incomplete branch.
            for label in labels:
                self.operations.append(("add", label))
            return {"ok": True, "reasonCode": "added", "summary": "added"}
        if self.fail_add == "unknown_applied":
            # Ambiguous transport that still applied on GitHub: the mutation
            # is visible to read-back even though the result is unknown.
            for label in labels:
                self.operations.append(("add", label))
                if label.lower() not in {existing.lower() for existing in self.labels}:
                    self.labels.append(label)
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "Label add result unknown."}
        for label in labels:
            self.operations.append(("add", label))
            if label.lower() not in {existing.lower() for existing in self.labels}:
                self.labels.append(label)
        return {"ok": True, "reasonCode": "added", "summary": "added"}

    async def remove_issue_label(self, *, repo: str, issue_number: int,
                                 label: str, github_token: str | None = None):
        if self.fail_remove == "denied":
            return {"ok": False, "reasonCode": "denied", "summary": "Label remove failed with HTTP 403."}
        if self.fail_remove == "unknown":
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "Label remove result unknown."}
        if self.fail_remove == "unknown_removed":
            self.operations.append(("remove", label))
            self.labels = [existing for existing in self.labels if existing.lower() != label.lower()]
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "Label remove result unknown."}
        self.operations.append(("remove", label))
        self.labels = [existing for existing in self.labels if existing.lower() != label.lower()]
        return {"ok": True, "reasonCode": "removed", "summary": "removed"}


class _LifecycleFakeHttpResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _LifecycleHttpClient:
    """HTTP fake whose read-back reflects the fake service label state."""

    service: _LifecycleFakeService | None = None
    fail_read_back: bool = False
    extra_unrelated_labels: list[str] | None = None
    reads: int = 0
    posts: list[tuple[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def _payload(self) -> dict[str, Any]:
        assert type(self).service is not None
        labels = list(type(self).service.labels)
        if type(self).extra_unrelated_labels:
            labels.extend(type(self).extra_unrelated_labels)
        return {
            "number": 4176,
            "title": "lifecycle",
            "body": "body",
            "html_url": "https://github.com/MoonLadderStudios/MoonMind/issues/4176",
            "state": type(self).service.issue_state,
            "labels": [{"name": label} for label in labels],
        }

    async def get(self, url: str, **kwargs: Any):
        type(self).reads += 1
        if type(self).fail_read_back and type(self).reads > 1:
            raise story_tools.httpx.ReadTimeout("lost", request=story_tools.httpx.Request("GET", url))
        return _LifecycleFakeHttpResponse(self._payload())

    async def patch(self, url: str, **kwargs: Any):
        assert type(self).service is not None
        payload = kwargs.get("json") or {}
        if payload.get("state") == "closed":
            type(self).service.issue_state = "closed"
        return _LifecycleFakeHttpResponse(self._payload())

    async def post(self, url: str, **kwargs: Any):
        type(self).posts.append((url, kwargs))
        return _LifecycleFakeHttpResponse({"id": 1})


def _install(monkeypatch: pytest.MonkeyPatch, service: _LifecycleFakeService) -> None:
    _LifecycleHttpClient.service = service
    _LifecycleHttpClient.fail_read_back = False
    _LifecycleHttpClient.extra_unrelated_labels = None
    _LifecycleHttpClient.reads = 0
    _LifecycleHttpClient.posts = []
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _LifecycleHttpClient)


@pytest.mark.asyncio
async def test_start_preserves_unrelated_labels_with_add_before_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService(initial_labels=["bug", "moonspec"])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "start"},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["appliedActions"] == ["add_label:status: in-progress", "comment"]
    assert service.operations == [("add", "status: in-progress")]
    assert result.outputs["confirmedLabels"] == ["bug", "moonspec", "status: in-progress"]
    assert result.outputs["mutationOutcome"] == "applied"
    assert "status: todo" not in [label.lower() for label in result.outputs["confirmedLabels"]]


@pytest.mark.asyncio
async def test_code_review_adds_before_removing_in_progress(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    service = _LifecycleFakeService(initial_labels=["bug", "status: in-progress"])
    _install(monkeypatch, service)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/9999"}', encoding="utf-8")
    verify_artifact = tmp_path / "verify.json"
    verify_artifact.write_text('{"verdict": "FULLY_IMPLEMENTED"}', encoding="utf-8")
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176,
         "mode": "finalize_after_pr_or_done",
         "pullRequestArtifactPath": str(pr_artifact),
         "verificationArtifactPath": str(verify_artifact)},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert service.operations[:2] == [("add", "status: code-review"), ("remove", "status: in-progress")]
    assert "bug" in result.outputs["confirmedLabels"]
    assert "status: in-progress" not in result.outputs["confirmedLabels"]


@pytest.mark.asyncio
async def test_finalize_when_already_in_code_review_is_already_applied(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Losing contender finalizes after winner already labeled code-review.

    Regression for mm:12352fc2-44cb-4f82-8dbd-9283d0fa8f63: second verified PR
    must reconcile to COMPLETED already_applied with its PR link commented,
    not FAILED unsupported_transition that FAIL_FASTs the workflow.
    """
    service = _LifecycleFakeService(initial_labels=["bug", "status: code-review"])
    _install(monkeypatch, service)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/4233"}', encoding="utf-8")
    verify_artifact = tmp_path / "verify.json"
    verify_artifact.write_text('{"verdict": "FULLY_IMPLEMENTED"}', encoding="utf-8")
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176,
         "mode": "finalize_after_pr_or_done",
         "pullRequestArtifactPath": str(pr_artifact),
         "verificationArtifactPath": str(verify_artifact)},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["mutationOutcome"] == "already_applied"
    assert result.outputs["lifecycleSettled"] == "code_review"
    # No label mutation: already in desired state.
    assert service.operations == []
    # Second PR is still linked for the review journey.
    assert "comment" in result.outputs["appliedActions"]
    assert "status: code-review" in result.outputs["confirmedLabels"]
    # Contention is explicit: both PRs stay visible, review owner decides.
    assert any("already in code-review" in w for w in result.outputs.get("warnings", []))


@pytest.mark.asyncio
async def test_mixed_labels_block_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService(initial_labels=["status: in-progress", "status: code-review"])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "start"},
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["reasonCode"] == "reconciliation_required"
    assert service.operations == []


@pytest.mark.asyncio
async def test_finalize_mixed_with_pr_steers_to_needs_attention(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """MoonLadderStudios/MoonMind#4225: finalize with published PR steers to attention."""
    service = _LifecycleFakeService(initial_labels=["status: in-progress", "status: code-review"])
    _install(monkeypatch, service)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/4210"}',
        encoding="utf-8",
    )
    verify_artifact = tmp_path / "verify.json"
    verify_artifact.write_text('{"verdict": "FULLY_IMPLEMENTED"}', encoding="utf-8")
    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 4176,
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
            "verificationArtifactPath": str(verify_artifact),
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "attention"
    assert result.outputs["degraded"] is True
    assert result.outputs["reasonCode"] == "reconciliation_required"
    assert result.outputs["previousLifecycleSettled"] == "blocked_mixed"
    assert result.outputs["pullRequestUrl"] == "https://github.com/MoonLadderStudios/MoonMind/pull/4210"
    assert result.outputs["observedLabels"] == ["status: in-progress", "status: code-review"]
    # Add-only steering: needs-attention added, no existing label removed.
    assert ("add", "status: needs-attention") in service.operations
    assert [op for op in service.operations if op[0] == "remove"] == []
    confirmed = [label.lower() for label in result.outputs["confirmedLabels"]]
    assert "status: needs-attention" in confirmed
    assert "status: in-progress" in confirmed
    assert "status: code-review" in confirmed
    # PR handoff comment posted with the PR URL visible.
    assert "comment" in result.outputs["appliedActions"]
    posted_bodies = [str(kwargs.get("json") or "") for _url, kwargs in _LifecycleHttpClient.posts]
    assert any(
        "https://github.com/MoonLadderStudios/MoonMind/pull/4210" in body
        for body in posted_bodies
    )
    # Degraded outcome is surfaced for run summary / Workflow Detail projection.
    assert "degraded" in result.outputs["summary"].lower()
    assert "needs-attention" in result.outputs["summary"].lower()
    assert result.outputs["transition"]["toTarget"] == "to_needs_attention"
    assert result.outputs["sideEffect"]["operation"] == "github.issue.update"


@pytest.mark.asyncio
async def test_finalize_mixed_without_pr_stays_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same mixed labels with no PR artifact keep FAILED/blocked (replay shape)."""
    service = _LifecycleFakeService(initial_labels=["status: in-progress", "status: code-review"])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 4176,
            "mode": "finalize_after_pr_or_done",
            "requireVerification": False,
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["remainingEvidence"]
    assert interpret_issue({"state": "open", "labels": service.labels}).settled == "blocked_mixed"
    assert service.operations == []
    assert _LifecycleHttpClient.posts == []


@pytest.mark.asyncio
async def test_finalize_unknown_with_pr_steers_to_needs_attention(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    service = _LifecycleFakeService(initial_labels=["status: frobnicate"])
    _install(monkeypatch, service)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/4210"}',
        encoding="utf-8",
    )
    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 4176,
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
            "requireVerification": False,
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "attention"
    assert result.outputs["previousLifecycleSettled"] == "blocked_unknown"
    assert [op for op in service.operations if op[0] == "remove"] == []
    assert "status: needs-attention" in [label.lower() for label in result.outputs["confirmedLabels"]]


@pytest.mark.asyncio
async def test_finalize_open_done_with_pr_stays_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """blocked_open_done is not steered: it stays FAILED even with a PR."""
    service = _LifecycleFakeService(initial_labels=["status: done"])
    _install(monkeypatch, service)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/4210"}',
        encoding="utf-8",
    )
    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 4176,
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
            "requireVerification": False,
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["reasonCode"] == "reconciliation_required"
    assert service.operations == []


def _finalize_mixed_pr_inputs(tmp_path, pr_url: str = "https://github.com/MoonLadderStudios/MoonMind/pull/4210") -> dict[str, Any]:
    """Shared finalize inputs for #4225 steering denial-branch tests."""
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(f'{{"pullRequestUrl": "{pr_url}"}}', encoding="utf-8")
    return {
        "repository": "MoonLadderStudios/MoonMind",
        "issueNumber": 4176,
        "mode": "finalize_after_pr_or_done",
        "pullRequestArtifactPath": str(pr_artifact),
        "requireVerification": False,
    }


@pytest.mark.asyncio
async def test_finalize_mixed_with_pr_readiness_denied_stays_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """MoonLadderStudios/MoonMind#4225: readiness denial keeps FAILED/blocked."""
    service = _LifecycleFakeService(initial_labels=["status: in-progress", "status: code-review"])
    service.readiness_result = {
        "ready": False,
        "reasonCode": "missing_labels",
        "summary": "Required lifecycle labels are missing.",
    }
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        _finalize_mixed_pr_inputs(tmp_path),
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["reasonCode"] == "missing_labels"
    assert result.outputs["pullRequestUrl"] == "https://github.com/MoonLadderStudios/MoonMind/pull/4210"
    assert service.operations == []
    assert _LifecycleHttpClient.posts == []


@pytest.mark.asyncio
async def test_finalize_mixed_with_pr_label_denied_stays_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """MoonLadderStudios/MoonMind#4225: label-write denial keeps FAILED/blocked."""
    service = _LifecycleFakeService(initial_labels=["status: in-progress", "status: code-review"])
    service.fail_add = "denied"
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        _finalize_mixed_pr_inputs(tmp_path),
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["reasonCode"] == "denied"
    assert result.outputs["mutationOutcome"] == "denied"
    assert [op for op in service.operations if op[0] == "remove"] == []
    assert "comment" not in result.outputs.get("appliedActions", [])
    assert _LifecycleHttpClient.posts == []


@pytest.mark.asyncio
async def test_finalize_mixed_with_pr_label_unknown_stays_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """MoonLadderStudios/MoonMind#4225: ambiguous label write keeps FAILED/blocked."""
    service = _LifecycleFakeService(initial_labels=["status: in-progress", "status: code-review"])
    service.fail_add = "unknown"
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        _finalize_mixed_pr_inputs(tmp_path),
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["reasonCode"] == "mutation_unknown"
    assert result.outputs["mutationOutcome"] == "outcome_unknown"
    assert "comment" not in result.outputs.get("appliedActions", [])


@pytest.mark.asyncio
async def test_finalize_mixed_with_pr_incomplete_readback_stays_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """MoonLadderStudios/MoonMind#4225: missing destination on read-back keeps FAILED."""
    service = _LifecycleFakeService(initial_labels=["status: in-progress", "status: code-review"])
    service.add_without_apply = True
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        _finalize_mixed_pr_inputs(tmp_path),
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["reasonCode"] == "mutation_incomplete"
    assert result.outputs["mutationOutcome"] == "incomplete"
    assert ("add", "status: needs-attention") in service.operations
    assert [op for op in service.operations if op[0] == "remove"] == []
    assert "comment" not in result.outputs.get("appliedActions", [])


class _CommentDenyingLifecycleService(_LifecycleFakeService):
    """Fake trusted boundary that denies the PR handoff comment creation."""

    async def list_issue_comments(self, *, repo: str, issue_number: int,
                                  github_token: str | None = None):
        return {"ok": True, "reasonCode": "listed", "summary": "Listed 0 issue comments.", "comments": []}

    async def create_issue_comment(self, *, repo: str, issue_number: int,
                                   body: str, github_token: str | None = None):
        self.create_issue_requests.append({"repo": repo, "issue_number": issue_number, "body": body})
        return {"ok": False, "reasonCode": "denied", "summary": "Issue comment create failed with HTTP 403."}


@pytest.mark.asyncio
async def test_finalize_mixed_with_pr_comment_denied_stays_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """MoonLadderStudios/MoonMind#4225: comment denial keeps FAILED/blocked after label steering."""
    service = _CommentDenyingLifecycleService(
        initial_labels=["status: in-progress", "status: code-review"]
    )
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        _finalize_mixed_pr_inputs(tmp_path),
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["reasonCode"] == "denied"
    assert result.outputs["commentStatus"] == "rejected"
    # Label steering was applied add-only before the comment denial.
    assert ("add", "status: needs-attention") in service.operations
    assert [op for op in service.operations if op[0] == "remove"] == []
    assert service.create_issue_requests, "expected one denied comment attempt"
    assert "https://github.com/MoonLadderStudios/MoonMind/pull/4210" in service.create_issue_requests[0]["body"]


@pytest.mark.asyncio
async def test_unknown_status_blocks_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService(initial_labels=["status: ready"])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "start"},
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "reconciliation_required"


@pytest.mark.asyncio
async def test_open_done_blocks_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService(initial_labels=["status: done"])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "start"},
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "reconciliation_required"


@pytest.mark.asyncio
async def test_available_without_terminal_proof_blocks_release(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService(initial_labels=["status: in-progress"])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "available"},
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "missing_guard"


@pytest.mark.asyncio
async def test_available_with_terminal_proof_releases_status(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService(initial_labels=["status: in-progress"])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "available",
         "terminalProof": "writers stopped, no preserved work, retry budget retained"},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["mutationOutcome"] == "applied"
    assert "status: in-progress" not in result.outputs["confirmedLabels"]


@pytest.mark.asyncio
async def test_denied_label_add_blocks_without_claiming_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService()
    service.fail_add = "denied"
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "start"},
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["mutationOutcome"] == "denied"


@pytest.mark.asyncio
async def test_unknown_remove_stops_without_claiming_success(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    service = _LifecycleFakeService(initial_labels=["status: in-progress"])
    service.fail_remove = "unknown"
    _install(monkeypatch, service)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/9999"}', encoding="utf-8")
    verify_artifact = tmp_path / "verify.json"
    verify_artifact.write_text('{"verdict": "FULLY_IMPLEMENTED"}', encoding="utf-8")
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176,
         "mode": "finalize_after_pr_or_done",
         "pullRequestArtifactPath": str(pr_artifact),
         "verificationArtifactPath": str(verify_artifact)},
        github_service_factory=lambda: service,
    )
    # The old status is still observed on read-back, so the transition stops
    # with an unknown outcome instead of completing with a warning.
    assert result.status == "FAILED"
    assert result.outputs["mutationOutcome"] == "outcome_unknown"
    assert result.outputs["reasonCode"] == "mutation_unknown"
    assert "comment" not in result.outputs.get("appliedActions", [])


@pytest.mark.asyncio
async def test_response_loss_on_read_back_fails_without_terminal_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService()
    _install(monkeypatch, service)
    _LifecycleHttpClient.fail_read_back = True
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "start"},
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["mutationOutcome"] == "outcome_unknown"
    assert result.outputs["reasonCode"] == "mutation_unknown"


@pytest.mark.asyncio
async def test_concurrent_unrelated_label_edit_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService()
    _install(monkeypatch, service)
    _LifecycleHttpClient.extra_unrelated_labels = ["triage: concurrent"]
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "start"},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert "triage: concurrent" in result.outputs["confirmedLabels"]
    assert "status: in-progress" in result.outputs["confirmedLabels"]


@pytest.mark.asyncio
async def test_missing_labels_return_actionable_readiness_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService()
    service.readiness_result = {"ready": False, "reasonCode": "missing_labels",
                                "summary": "Required lifecycle labels are missing."}
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "start"},
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["reasonCode"] == "missing_labels"
    assert service.operations == []


@pytest.mark.asyncio
async def test_stale_ownership_blocks_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService()
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "start",
         "attemptContext": {"has_unresolved_active_attempt": True}},
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "active_attempt_conflict"
    assert service.operations == []


# ---------------------------------------------------------------------------
# A5: no todo/ready/claiming label and no distributed-lock storage
# ---------------------------------------------------------------------------


def test_new_path_actions_require_no_todo_label() -> None:
    actions = story_tools._GITHUB_STATUS_ACTIONS
    for mode, action in actions.items():
        assert "status: todo" not in [str(label).lower() for label in action.get("labelsToAdd", [])]
        assert "status: todo" not in [str(label).lower() for label in action.get("labelsToRemove", [])]
        assert "claiming" not in " ".join(str(label).lower() for label in action.get("labelsToAdd", []))


@pytest.mark.asyncio
async def test_start_needs_no_todo_label_present(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService(initial_labels=[])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "start"},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["confirmedLabels"] == ["status: in-progress"]


# ---------------------------------------------------------------------------
# A6: replay/compatibility for persisted tool payloads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_previous_outputs_still_drive_finalize(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Pre-change persisted payloads (snake_case, no lifecycle keys) keep working."""
    service = _LifecycleFakeService()
    _install(monkeypatch, service)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/9999"}', encoding="utf-8")
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176,
         "mode": "finalize_after_pr_or_done",
         "pullRequestArtifactPath": str(pr_artifact),
         "requireVerification": False,
         "previousOutputs": {"push_status": "pushed", "push_commit_count": 2}},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    # Workflow-facing result keys from before the change are preserved.
    for key in ("issueUrl", "appliedActions", "confirmedState", "confirmedLabels", "summary", "sideEffect"):
        assert key in result.outputs
    assert result.outputs["sideEffect"]["operation"] == "github.issue.update"


@pytest.mark.asyncio
async def test_finalize_mixed_labels_4225_recorded_failure_still_replays(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """MoonLadderStudios/MoonMind#4225: the 2026-09-10T18:00Z FAILED shape still replays.

    Loads ``replays/finalize-mixed-labels-4225``: step 09 recorded
    FAILED/blocked/reconciliation_required for blocked_mixed after PR 4210.
    The recorded shape must still interpret identically (no normalization of
    mixed labels), the no-PR path must keep the recorded FAILED outcome, and
    the with-PR path must steer to COMPLETED/attention add-only.
    """
    from tests.integration.reliability.helpers import load_replay

    manifest = load_replay("finalize-mixed-labels-4225", "manifest.json")
    expected = load_replay("finalize-mixed-labels-4225", "expected-outcome.json")
    recorded = manifest["recordedHistory"]
    observed = recorded["observedIssue"]

    interpretation = interpret_issue({"state": observed["state"], "labels": observed["labels"]})
    assert interpretation.settled == expected["recordedSettled"]
    assert recorded["step09"]["recordedResult"]["status"] == "FAILED"
    assert recorded["step09"]["recordedResult"]["outputs"]["decision"] == expected["recordedDecision"]
    assert recorded["step09"]["recordedResult"]["outputs"]["reasonCode"] == expected["recordedReasonCode"]
    assert expected["recordedFailedShapeReplays"] is True

    # No-PR replay keeps the recorded FAILED/blocked outcome.
    service = _LifecycleFakeService(initial_labels=list(observed["labels"]))
    _install(monkeypatch, service)
    blocked = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": observed["number"],
            "mode": "finalize_after_pr_or_done",
            "requireVerification": False,
        },
        github_service_factory=lambda: service,
    )
    assert blocked.status == "FAILED"
    assert blocked.outputs["decision"] == "blocked"
    assert blocked.outputs["remainingEvidence"]
    assert expected["withoutPrStaysBlocked"] is True

    # With-PR replay steers add-only to COMPLETED/attention (degraded).
    pr_artifact = tmp_path / "pr-4210.json"
    pr_artifact.write_text(
        f'{{"pullRequestUrl": "{recorded["step08"]["outputs"]["pullRequestUrl"]}"}}',
        encoding="utf-8",
    )
    steered = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": observed["number"],
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
            "requireVerification": False,
        },
        github_service_factory=lambda: service,
    )
    assert steered.status == "COMPLETED"
    assert steered.outputs["decision"] == expected["attentionDecision"]
    assert steered.outputs["degraded"] is expected["attentionDegraded"]
    assert steered.outputs["transition"]["toTarget"] == expected["attentionTransition"]
    assert ("add", "status: needs-attention") in service.operations
    assert [op for op in service.operations if op[0] == "remove"] == []
    assert expected["withPrSteersToAttention"] is True
    assert expected["attentionAddsOnly"] is True


def test_interpretation_and_decision_serialize_for_history() -> None:
    interpretation = interpret_issue({"state": "open", "labels": ["status: in-progress"]})
    payload = interpretation.to_dict()
    assert payload["settled"] == "in_progress"
    assert set(payload) >= {"githubState", "canonicalPresent", "settled", "blockedReason"}
    decision = plan_transition(from_settled="available", to_target="to_in_progress",
                               evidence={"admission_passed": True, "prior_work_inspected": True}, reason="r")
    assert decision.to_dict()["allowed"] is True


# ---------------------------------------------------------------------------
# Review remediation: ambiguous mutations, caller expectations, terminal
# evidence, mode-specific comments, Done closure, attention ownership
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_add_confirmed_on_read_back_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService()
    service.fail_add = "unknown_applied"
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "start"},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["mutationOutcome"] == "applied"
    assert "add_label:status: in-progress" in result.outputs["appliedActions"]
    assert any("confirmed on read-back" in warning for warning in result.outputs.get("warnings", []))


@pytest.mark.asyncio
async def test_ambiguous_add_missing_on_read_back_stops_before_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    service = _LifecycleFakeService(initial_labels=["bug", "status: in-progress"])
    service.fail_add = "unknown"
    _install(monkeypatch, service)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/9999"}', encoding="utf-8")
    verify_artifact = tmp_path / "verify.json"
    verify_artifact.write_text('{"verdict": "FULLY_IMPLEMENTED"}', encoding="utf-8")
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176,
         "mode": "finalize_after_pr_or_done",
         "pullRequestArtifactPath": str(pr_artifact),
         "verificationArtifactPath": str(verify_artifact)},
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["mutationOutcome"] == "outcome_unknown"
    # The old blocking label is never removed after an ambiguous add.
    assert service.operations == []
    assert "status: in-progress" in service.labels


@pytest.mark.asyncio
async def test_ambiguous_remove_confirmed_absent_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    service = _LifecycleFakeService(initial_labels=["bug", "status: in-progress"])
    service.fail_remove = "unknown_removed"
    _install(monkeypatch, service)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/9999"}', encoding="utf-8")
    verify_artifact = tmp_path / "verify.json"
    verify_artifact.write_text('{"verdict": "FULLY_IMPLEMENTED"}', encoding="utf-8")
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176,
         "mode": "finalize_after_pr_or_done",
         "pullRequestArtifactPath": str(pr_artifact),
         "verificationArtifactPath": str(verify_artifact)},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["mutationOutcome"] == "applied"
    assert "status: in-progress" not in result.outputs["confirmedLabels"]


@pytest.mark.asyncio
async def test_stale_expected_state_abandons_obsolete_update(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService(initial_labels=["status: code-review"])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176,
         "mode": "start", "expectedFromSettled": "in_progress"},
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["decision"] == "abandoned"
    assert service.operations == []


@pytest.mark.asyncio
async def test_matching_expected_state_does_not_abandon(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService(initial_labels=["status: in-progress"])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176,
         "mode": "start", "expectedFromSettled": "in_progress",
         "assessmentArtifactPath": ""},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["mutationOutcome"] == "already_applied"


@pytest.mark.asyncio
async def test_terminal_modes_leave_mode_specific_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService(initial_labels=["status: in-progress"])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176,
         "mode": "recovery_needed",
         "writersStopped": True, "handoffPublished": "art_handoff"},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    bodies = [kwargs.get("json", {}).get("body", "") for _, kwargs in _LifecycleHttpClient.posts]
    assert any("continuation handoff" in body for body in bodies)
    assert not any("started implementation" in body for body in bodies)


@pytest.mark.asyncio
async def test_attention_escalation_preserves_active_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService(initial_labels=["status: in-progress"])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176,
         "mode": "needs_attention", "blockingReason": "writer stop uncertain"},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert "status: in-progress" in result.outputs["confirmedLabels"]
    assert "status: needs-attention" in result.outputs["confirmedLabels"]
    bodies = [kwargs.get("json", {}).get("body", "") for _, kwargs in _LifecycleHttpClient.posts]
    assert any("needing attention" in body for body in bodies)


@pytest.mark.asyncio
async def test_available_release_leaves_release_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService(initial_labels=["status: in-progress"])
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176,
         "mode": "available",
         "terminalProof": "writers stopped, no preserved work, retry budget retained"},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    bodies = [kwargs.get("json", {}).get("body", "") for _, kwargs in _LifecycleHttpClient.posts]
    assert any("released" in body and "available" in body for body in bodies)
    assert not any("started implementation" in body for body in bodies)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["finalize_after_pr_or_done", "done"])
async def test_close_applies_done_destination(monkeypatch: pytest.MonkeyPatch, candidate, mode) -> None:
    service = _LifecycleFakeService(initial_labels=["bug", "status: code-review"])
    report = _bind_verified_target(service, candidate)
    _install(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": "example/repo", "issueNumber": 1, "verificationPayload": report,
         "mode": mode,
         **({"pullRequestUrl": "https://github.com/example/repo/pull/2"} if mode == "done" else {})},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["appliedActions"] == ["add_label:status: done", "remove_label:status: code-review", "close_issue"]
    assert result.outputs["mutationOutcome"] == "applied"
    assert result.outputs["confirmedState"] == "closed"


@pytest.mark.asyncio
async def test_closed_close_retry_reconciles_as_applied(monkeypatch: pytest.MonkeyPatch, tmp_path, candidate) -> None:
    service = _LifecycleFakeService(initial_labels=["status: code-review"])
    service.issue_state = "closed"
    report = _bind_verified_target(service, candidate)
    _install(monkeypatch, service)
    assessment = tmp_path / "assessment.json"
    assessment.write_text('{"verdict": "FULLY_IMPLEMENTED"}', encoding="utf-8")
    result = await update_github_issue_status(
        {"repository": "example/repo", "issueNumber": 1, "verificationPayload": report,
         "mode": "finalize_after_pr_or_done",
         "assessmentArtifactPath": str(assessment)},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["mutationOutcome"] == "applied"


def _search_candidate(number: int, labels: list[str]) -> dict[str, Any]:
    return {
        "number": number,
        "state": "open",
        "title": f"candidate {number}",
        "body": "Build the thing.",
        "html_url": f"https://github.com/o/r/issues/{number}",
        "labels": [{"name": label} for label in labels],
    }


class _SearchFakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _SearchFakeHttpClient:
    payload: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, **kwargs: Any):
        return _SearchFakeResponse(dict(type(self).payload))


class _SearchFakeService:
    async def resolve_github_token(self, *, repo: str):
        return "ghs-test", None

    def _github_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_resolve_issue_scans_past_recovery_without_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    from moonmind.workflows.temporal import github_issue_search as search_tools
    from moonmind.workflows.temporal.github_issue_search import resolve_issue

    _SearchFakeHttpClient.payload = {
        "incomplete_results": False,
        "items": [
            _search_candidate(11, ["status: recovery-needed"]),
            _search_candidate(12, []),
        ],
    }
    monkeypatch.setattr(search_tools.httpx, "AsyncClient", _SearchFakeHttpClient)
    service = _SearchFakeService()

    async def no_blockers(issue: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    number, _ = await resolve_issue(
        repository="o/r",
        query="task",
        github_service=service,  # type: ignore[arg-type]
        include_all_authors=True,
        blockers_from_issue=no_blockers,
    )
    assert number == 12

    number, _ = await resolve_issue(
        repository="o/r",
        query="task",
        github_service=service,  # type: ignore[arg-type]
        include_all_authors=True,
        blockers_from_issue=no_blockers,
        recovery_handoff={"predecessor_stopped": True, "handoff_usable": True},
    )
    assert number == 11
