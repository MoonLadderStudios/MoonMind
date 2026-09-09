"""Component tests for the shared GitHub issue lifecycle boundary.

Covers issue MoonLadderStudios/MoonMind#4176 acceptance criteria:
table-driven states/transitions/guards, targeted add-before-remove mutations
preserving unrelated labels, failure injection, active-attempt evidence over
missing labels, no todo/lock reliance, and replay compatibility.
"""

from __future__ import annotations

import re
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
    plan_label_mutation,
    plan_transition,
    should_abandon_retry,
)
from moonmind.workflows.temporal.story_output_tools import update_github_issue_status


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
         {"authorized_resolution": True, "preserved_work_disposition": "kept"}, "resolve", True, "allowed"),
        # Missing guards block admission rather than normalizing optimistically.
        ("available", "to_in_progress", {}, "admit", False, "missing_guard"),
        ("available", "to_in_progress",
         {"admission_passed": True, "prior_work_inspected": True}, "", False, "missing_guard"),
        ("in_progress", "to_code_review", {"gates_satisfied": True}, "publish", False, "missing_guard"),
        ("in_progress", "to_available", {"writers_stopped": True}, "release", False, "missing_guard"),
        ("in_progress", "to_available",
         {"writers_stopped": True, "terminal_proof": ""}, "release", False, "missing_guard"),
        # Unsupported transitions require an explicit authority decision.
        ("available", "to_recovery_needed", {"admission_passed": True}, "x", False, "unsupported_transition"),
        ("code_review", "to_code_review", {"gates_satisfied": True}, "x", False, "unsupported_transition"),
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


# ---------------------------------------------------------------------------
# Tool-level tests with targeted-mutation fakes
# ---------------------------------------------------------------------------


class _LifecycleFakeService:
    """Fake trusted GitHub boundary with targeted label operations."""

    def __init__(self, initial_labels: list[str] | None = None) -> None:
        self.token_requests: list[str] = []
        self.labels = list(initial_labels) if initial_labels is not None else ["moonspec"]
        self.operations: list[tuple[str, str]] = []
        self.readiness_result: dict[str, Any] | None = None
        self.fail_add: str | None = None
        self.fail_remove: str | None = None
        self.create_issue_requests: list[dict[str, Any]] = []

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
            "state": "open",
            "labels": [{"name": label} for label in labels],
        }

    async def get(self, url: str, **kwargs: Any):
        type(self).reads += 1
        if type(self).fail_read_back and type(self).reads > 1:
            raise story_tools.httpx.ReadTimeout("lost", request=story_tools.httpx.Request("GET", url))
        return _LifecycleFakeHttpResponse(self._payload())

    async def patch(self, url: str, **kwargs: Any):
        return _LifecycleFakeHttpResponse(self._payload())

    async def post(self, url: str, **kwargs: Any):
        return _LifecycleFakeHttpResponse({"id": 1})


def _install(monkeypatch: pytest.MonkeyPatch, service: _LifecycleFakeService) -> None:
    _LifecycleHttpClient.service = service
    _LifecycleHttpClient.fail_read_back = False
    _LifecycleHttpClient.extra_unrelated_labels = None
    _LifecycleHttpClient.reads = 0
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
async def test_unknown_remove_reports_incomplete_not_success(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
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
    assert result.status == "COMPLETED"
    assert result.outputs["mutationOutcome"] == "incomplete"
    assert result.outputs["warnings"]


@pytest.mark.asyncio
async def test_response_loss_on_read_back_reports_outcome_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _LifecycleFakeService()
    _install(monkeypatch, service)
    _LifecycleHttpClient.fail_read_back = True
    result = await update_github_issue_status(
        {"repository": "MoonLadderStudios/MoonMind", "issueNumber": 4176, "mode": "start"},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["mutationOutcome"] == "outcome_unknown"


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


def test_interpretation_and_decision_serialize_for_history() -> None:
    interpretation = interpret_issue({"state": "open", "labels": ["status: in-progress"]})
    payload = interpretation.to_dict()
    assert payload["settled"] == "in_progress"
    assert set(payload) >= {"githubState", "canonicalPresent", "settled", "blockedReason"}
    decision = plan_transition(from_settled="available", to_target="to_in_progress",
                               evidence={"admission_passed": True, "prior_work_inspected": True}, reason="r")
    assert decision.to_dict()["allowed"] is True
