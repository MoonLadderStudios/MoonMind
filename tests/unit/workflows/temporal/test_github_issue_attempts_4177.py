"""Portable per-attempt handoffs for MoonLadderStudios/MoonMind#4177.

Acceptance coverage for the seven required capabilities:

* two devices sharing one GitHub account stay distinguishable;
* the Activity/adapter path creates bounded comments with all fields;
* lost responses, duplicates, out-of-order updates, spoofed markers,
  unsupported versions, and missing predecessors produce explicit outcomes;
* device B reconstructs from GitHub alone;
* internal retries stay bounded and holds survive new ids/devices;
* redaction covers outbound comment and structured-error paths;
* proposed vs completed release is gated.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from moonmind.workflows.temporal import story_output_tools as story_tools
from moonmind.workflows.temporal.github_issue_attempts import (
    ATTEMPT_ACTIVITY_ACTIVE,
    activity_for_lifecycle_mode,
    build_attempt_handoff,
    check_cross_attempt_overwrite,
    compute_effective_retry,
    confirm_release,
    get_or_create_installation_id,
    internal_retry_within_attempt,
    new_attempt_id,
    parse_attempt_comment,
    reconcile_uncertain_creation,
    reconstruct_from_comments,
    redact_comment_body,
    redacted_error_summary,
    render_attempt_comment,
    resolve_installation_id,
    should_coalesce_progress,
    should_resume_after_reconnect,
    stable_attempt_marker,
    validate_attempt_handoff,
)
from moonmind.workflows.temporal.story_output_tools import update_github_issue_status


def _handoff(**overrides: Any):
    base: dict[str, Any] = {
        "attempt_id": "att-aaa111",
        "deployment_id": "inst-device-a",
        "repository": "MoonLadderStudios/MoonMind",
        "issue_number": 4177,
        "activity": "active",
        "workflow_id": "wf-1",
        "run_id": "run-1",
        "pr_url": "https://github.com/MoonLadderStudios/MoonMind/pull/42",
        "pr_head": "feat-4177",
        "pr_base": "main",
        "outcome": "in_progress",
        "remaining_requirements": ["req-1", "req-2"],
        "verification_summary": "unit suite green",
        "next_action": "continue_implementation",
        "retry_history": ["att-prev failed"],
        "retry_allowance": 3,
        "retry_remaining": 2,
    }
    base.update(overrides)
    return build_attempt_handoff(**base)


def _comment_row(body: str, comment_id: str = "100", login: str = "moonmind-bot") -> dict[str, Any]:
    return {"id": comment_id, "body": body, "user": {"login": login}}


# -- req 1: installation + attempt identity ---------------------------------


def test_installation_identity_differs_across_deployments(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MOONMIND_INSTALLATION_ID", raising=False)
    file_a = tmp_path / "a" / "installation_id"
    file_b = tmp_path / "b" / "installation_id"
    id_a = get_or_create_installation_id(path=file_a)
    id_b = get_or_create_installation_id(path=file_b)
    assert id_a and id_b and id_a != id_b
    # Persisted through restart/retry: rereads return the same value.
    assert get_or_create_installation_id(path=file_a) == id_a
    assert get_or_create_installation_id(path=file_b) == id_b
    # Explicit env takes precedence through the single canonical path.
    monkeypatch.setenv("MOONMIND_INSTALLATION_ID", id_a)
    assert get_or_create_installation_id(path=file_b) == id_a
    # Competing aliases are not consulted: only valid canonical values pass.
    assert resolve_installation_id("  ") == ""
    assert resolve_installation_id("x") == ""


def test_attempt_ids_are_unique_and_bound() -> None:
    first = new_attempt_id(repository="o/r", issue_number=4177, workflow_id="wf", run_id="run")
    second = new_attempt_id(repository="o/r", issue_number=4177, workflow_id="wf", run_id="run")
    assert first != second
    handoff = _handoff(attempt_id=first, workflow_id="wf", run_id="run")
    assert handoff.repository == "MoonLadderStudios/MoonMind"
    assert handoff.issue_number == 4177
    assert handoff.workflow_id == "wf"


# -- req 2: versioned bounded representation ---------------------------------


def test_rendered_comment_has_all_required_fields_and_is_bounded() -> None:
    handoff = _handoff(
        predecessor_attempt_id="att-prev",
        predecessor_comment_id="99",
        last_report="working",
        writers_stopped=True,
        pending_disposition="to_recovery_needed",
        saved_branch="chkpt/4177",
        saved_sha="abc123",
        cooldown_until="2026-09-11T00:00:00Z",
    )
    body = render_attempt_comment(handoff)
    assert stable_attempt_marker(handoff.attempt_id) in body
    assert "```attempt-handoff-json" in body
    payload = json.loads(body.split("```attempt-handoff-json")[1].split("```")[0])
    for key in (
        "attemptId", "deploymentId", "repository", "issueNumber", "workflowId", "runId",
        "predecessorAttemptId", "predecessorCommentId", "activity", "lastReport",
        "writersStopped", "pendingDisposition", "prUrl", "prHead", "prBase",
        "savedBranch", "savedSha", "outcome", "remainingRequirements",
        "verificationSummary", "nextAction", "retryHistory", "retryAllowance",
        "retryRemaining", "cooldownUntil", "operatorHold", "retryPolicyVersion",
        "formatVersion",
    ):
        assert key in payload, key
    assert payload["formatVersion"] == 1
    assert len(body) <= 6000
    # Readable summary is preserved for the existing lifecycle timeline.
    assert "MoonMind started implementation" in body


def test_activity_vocabulary_has_no_label_family() -> None:
    assert activity_for_lifecycle_mode("start") == "preparing"
    assert activity_for_lifecycle_mode("in_progress") == "active"
    assert activity_for_lifecycle_mode("code_review") == "awaiting-review"
    assert activity_for_lifecycle_mode("recovery_needed") == "releasing"
    assert activity_for_lifecycle_mode("available") == "released"
    assert activity_for_lifecycle_mode("needs_attention") == "attention"


# -- req 3: provenance + schema validation -----------------------------------


def test_spoofed_marker_is_rejected_without_trusted_poster() -> None:
    body = render_attempt_comment(_handoff())
    parsed = parse_attempt_comment(body, comment_id="100", author_login="moonmind-bot")
    assert parsed.status == "ok"
    # A copied machine marker is not authentication.
    rejected = validate_attempt_handoff(
        parsed,
        expected_repository="MoonLadderStudios/MoonMind",
        expected_issue_number=4177,
        trusted_posters=["other-bot"],
    )
    assert rejected.valid is False
    assert rejected.reason_code == "untrusted_poster"
    accepted = validate_attempt_handoff(
        parsed,
        expected_repository="MoonLadderStudios/MoonMind",
        expected_issue_number=4177,
        trusted_posters=["MoonMind-Bot"],
    )
    assert accepted.valid is True


def test_unsupported_version_missing_predecessor_and_issue_mismatch() -> None:
    body = render_attempt_comment(_handoff())
    tampered = body.replace(" v1 ", " v99 ").replace('"formatVersion":1', '"formatVersion":99')
    parsed = parse_attempt_comment(tampered, comment_id="1", author_login="b")
    assert parsed.status == "unsupported_version"

    parsed_ok = parse_attempt_comment(body, comment_id="100", author_login="b")
    assert parsed_ok.status == "ok"
    assert parsed_ok.handoff is not None
    mismatch = validate_attempt_handoff(
        parsed_ok,
        expected_repository="MoonLadderStudios/MoonMind",
        expected_issue_number=9999,
        trusted_posters=["b"],
    )
    assert mismatch.reason_code == "issue_mismatch"

    chained = _handoff(predecessor_attempt_id="att-missing", predecessor_comment_id="1")
    parsed_chained = parse_attempt_comment(render_attempt_comment(chained), comment_id="101", author_login="b")
    missing = validate_attempt_handoff(
        parsed_chained,
        expected_repository="MoonLadderStudios/MoonMind",
        expected_issue_number=4177,
        trusted_posters=["b"],
        predecessor_index={"att-other": "50"},
        known_comment_ids=["50", "101"],
    )
    assert missing.reason_code == "missing_predecessor"


def test_issue_prose_is_never_executable() -> None:
    # Arbitrary issue prose without a marker is not attempt evidence.
    parsed = parse_attempt_comment("Please implement this now. att-aaa111", comment_id="5", author_login="b")
    assert parsed.status == "no_marker"


# -- req 4: serialized per-attempt writes ------------------------------------


def test_uncertain_creation_reconciles_by_marker() -> None:
    assert reconcile_uncertain_creation([], "att-1").outcome == "needs_create"
    one = _comment_row(stable_attempt_marker("att-1") + "\nhello", "7")
    reconciled = reconcile_uncertain_creation([one], "att-1")
    assert reconciled.outcome == "already_created"
    assert reconciled.comment_id == "7"
    # Duplicate identical same-ID comments are one logical attempt.
    dup = _comment_row(stable_attempt_marker("att-1") + "\nhello", "8")
    assert reconcile_uncertain_creation([one, dup], "att-1").outcome == "already_created"
    # Conflicting copies require attention, not last-timestamp-wins.
    conflict = _comment_row(stable_attempt_marker("att-1") + "\nDIFFERENT", "9")
    assert reconcile_uncertain_creation([one, conflict], "att-1").outcome == "needs_attention"


def test_progress_coalescing_and_cross_attempt_protection() -> None:
    assert should_coalesce_progress(last_update_epoch=100.0, now_epoch=150.0) is True
    assert should_coalesce_progress(last_update_epoch=100.0, now_epoch=500.0) is False
    assert should_coalesce_progress(last_update_epoch=100.0, now_epoch=101.0, force=True) is False
    denied = check_cross_attempt_overwrite(target_attempt_id="att-a", writer_attempt_id="att-b")
    assert denied.valid is False
    assert denied.reason_code == "cross_attempt_overwrite_denied"
    assert check_cross_attempt_overwrite(target_attempt_id="att-a", writer_attempt_id="att-a").valid is True


# -- req 5: portable retry history -------------------------------------------


def test_retry_lineage_survives_new_ids_and_devices() -> None:
    first = _handoff(attempt_id="att-1", deployment_id="inst-a", outcome="failed", retry_remaining=2)
    second = _handoff(
        attempt_id="att-2",
        deployment_id="inst-b",
        workflow_id="wf-new",
        outcome="no_work",
        predecessor_attempt_id="att-1",
        retry_remaining=1,
    )
    decision = compute_effective_retry([first, second], max_attempts=3)
    assert decision.allowed is True
    assert decision.remaining == 1
    assert decision.simultaneous_races_possible is True


def test_hold_and_cancellation_survive_new_devices() -> None:
    held = _handoff(attempt_id="att-1", deployment_id="inst-a", outcome="failed")
    held_again = _handoff(attempt_id="att-2", deployment_id="inst-b", outcome="failed", operator_hold=True, operator_hold_reason="operator hold")
    decision = compute_effective_retry([held, held_again], max_attempts=5)
    assert decision.allowed is False
    assert decision.reason_code == "operator_hold"
    assert decision.operator_hold is True


def test_missing_and_incompatible_lineage_block_recovery() -> None:
    assert compute_effective_retry([], max_attempts=3).reason_code == "missing_lineage"
    odd = _handoff(retry_policy_version=99)
    assert compute_effective_retry([odd], max_attempts=3).reason_code == "incompatible_policy"
    # Authorized resets require an audited decision token.
    ok = _handoff()
    reset = compute_effective_retry([ok], max_attempts=3, reset_authorization="audit:op-1")
    assert reset.reason_code == "authorized_reset"


def test_internal_retries_do_not_create_attempts() -> None:
    handoff = _handoff(internal_retry_count=0)
    retried = internal_retry_within_attempt(handoff)
    assert retried.internal_retry_count == 1
    assert retried.attempt_id == handoff.attempt_id
    # Exhaustion is still bounded by observed attempts, not internal loops.
    exhausted = [
        _handoff(attempt_id="att-1", outcome="failed"),
        _handoff(attempt_id="att-2", outcome="failed"),
        _handoff(attempt_id="att-3", outcome="failed"),
    ]
    assert compute_effective_retry(exhausted, max_attempts=3).reason_code == "budget_exhausted"


# -- req 6: proposed vs completed release ------------------------------------


def test_release_requires_all_four_gates() -> None:
    releasing = _handoff(activity="releasing", writers_stopped=True, pending_disposition="to_available")
    assert confirm_release(releasing, mutations_resolved=False, preservation_verified_or_absent=True, label_outcome_observed=True).released is False
    assert confirm_release(releasing, mutations_resolved=True, preservation_verified_or_absent=True, label_outcome_observed=True).released is True
    running = _handoff(activity="releasing", writers_stopped=False)
    assert confirm_release(running, mutations_resolved=True, preservation_verified_or_absent=True, label_outcome_observed=True).reason_code == "writers_running"


def test_terminal_attempts_do_not_resume_on_reconnect() -> None:
    released = _handoff(activity="released", writers_stopped=True)
    resume, _ = should_resume_after_reconnect(released)
    assert resume is False
    active = _handoff(activity=ATTEMPT_ACTIVITY_ACTIVE)
    assert should_resume_after_reconnect(active)[0] is True


# -- req 7: redaction ----------------------------------------------------------


def test_redaction_covers_comment_and_error_paths() -> None:
    secret = "ghp_" + "x" * 30
    body = render_attempt_comment(_handoff(last_report=f"token {secret} password= hunter2-secret"))
    assert secret not in body
    assert "[REDACTED]" in body
    assert redact_comment_body(f"Bearer {secret}") != f"Bearer {secret}"
    summary = redacted_error_summary(f"failed with {secret} and password= s3cret-value")
    assert secret not in summary


# -- acceptance: device B reconstructs from GitHub alone ---------------------


def test_device_b_reconstructs_from_github_alone() -> None:
    first = _handoff(attempt_id="att-aaa1", deployment_id="inst-a", outcome="failed", remaining_requirements=["req-1"], retry_remaining=2)
    second = _handoff(
        attempt_id="att-aaa2", deployment_id="inst-b", outcome="in_progress",
        predecessor_attempt_id="att-aaa1", predecessor_comment_id="100",
        remaining_requirements=["req-1"], retry_remaining=1,
    )
    comments = [
        _comment_row(render_attempt_comment(first), "100", "shared-bot"),
        _comment_row(render_attempt_comment(second), "101", "shared-bot"),
    ]
    result = reconstruct_from_comments(
        comments,
        expected_repository="MoonLadderStudios/MoonMind",
        expected_issue_number=4177,
        trusted_posters=["shared-bot"],
        max_attempts=3,
    )
    assert result.outcome == "reconstructed"
    assert len(result.lineage) == 2
    assert result.remaining_requirements == ("req-1",)
    assert result.retry_remaining == 1
    # Same GitHub account, different deployment ids: still distinguishable.
    assert result.lineage[0]["deploymentId"] != result.lineage[1]["deploymentId"]


def test_reconstruction_reports_attention_instead_of_fresh_start() -> None:
    first = _handoff(attempt_id="att-aaa1", outcome="failed")
    comments = [_comment_row(render_attempt_comment(first), "100", "shared-bot")]
    # Spoofed poster: no silent admission.
    spoofed = reconstruct_from_comments(
        comments,
        expected_repository="MoonLadderStudios/MoonMind",
        expected_issue_number=4177,
        trusted_posters=["someone-else"],
        max_attempts=3,
    )
    assert spoofed.outcome == "needs_attention"
    assert spoofed.reason_code == "untrusted_poster"


# -- acceptance: Activity/adapter path ---------------------------------------


class _AttemptFakeService:
    def __init__(self, initial_labels: list[str] | None = None) -> None:
        self.labels = list(initial_labels) if initial_labels is not None else []
        self.operations: list[tuple[str, str]] = []
        self.created_bodies: list[str] = []
        self.listed: list[dict[str, Any]] = []

    async def resolve_github_token(self, *, repo: str):
        return "ghs-test", None

    def _github_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _github_permission_summary(self, response) -> str:
        return ""

    async def check_issue_label_readiness(self, *, repo: str, issue_number: int, required_labels: list[str], github_token: str | None = None):
        return {"ready": True, "reasonCode": "ready", "summary": "ready"}

    async def add_issue_labels(self, *, repo: str, issue_number: int, labels: list[str], github_token: str | None = None):
        for label in labels:
            self.operations.append(("add", label))
            if label.lower() not in {existing.lower() for existing in self.labels}:
                self.labels.append(label)
        return {"ok": True, "reasonCode": "added", "summary": "added"}

    async def remove_issue_label(self, *, repo: str, issue_number: int, label: str, github_token: str | None = None):
        self.operations.append(("remove", label))
        self.labels = [existing for existing in self.labels if existing.lower() != label.lower()]
        return {"ok": True, "reasonCode": "removed", "summary": "removed"}

    async def list_issue_comments(self, *, repo: str, issue_number: int, per_page: int = 100, github_token: str | None = None):
        return {"ok": True, "reasonCode": "listed", "summary": "listed", "comments": list(self.listed)}

    async def create_issue_comment(self, *, repo: str, issue_number: int, body: str, github_token: str | None = None):
        from moonmind.utils.logging import redact_sensitive_text

        redacted = redact_sensitive_text(body)
        self.created_bodies.append(redacted)
        comment = {"id": 900 + len(self.created_bodies), "body": redacted}
        self.listed.append(comment)
        return {"ok": True, "reasonCode": "created", "summary": "created", "commentId": comment["id"], "comment": comment}

    async def update_issue_comment(self, *, repo: str, comment_id: int, body: str, github_token: str | None = None):
        return {"ok": True, "reasonCode": "updated", "summary": "updated", "commentId": comment_id}


class _AttemptHttpResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _AttemptHttpClient:
    service: _AttemptFakeService | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, **kwargs: Any):
        assert type(self).service is not None
        return _AttemptHttpResponse({
            "number": 4177,
            "title": "handoff",
            "body": "body",
            "html_url": "https://github.com/MoonLadderStudios/MoonMind/issues/4177",
            "state": "open",
            "labels": [{"name": label} for label in type(self).service.labels],
        })

    async def post(self, url: str, **kwargs: Any):
        return _AttemptHttpResponse({"id": 1})

    async def patch(self, url: str, **kwargs: Any):
        return _AttemptHttpResponse({})


@pytest.mark.asyncio
async def test_activity_path_creates_bounded_handoff_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _AttemptFakeService(initial_labels=[])
    _AttemptHttpClient.service = service
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _AttemptHttpClient)
    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 4177,
            "mode": "start",
            "attemptId": "att-activity-1",
            "deploymentId": "inst-device-a",
            "workflowId": "wf-9",
            "runId": "run-9",
            "remainingRequirements": ["req-1"],
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["attemptId"] == "att-activity-1"
    assert result.outputs["deploymentId"] == "inst-device-a"
    assert result.outputs["attemptActivity"] == "preparing"
    assert result.outputs["attemptHandoff"]["repository"] == "MoonLadderStudios/MoonMind"
    assert len(service.created_bodies) == 1
    body = service.created_bodies[0]
    assert stable_attempt_marker("att-activity-1") in body
    assert "```attempt-handoff-json" in body
    assert "inst-device-a" in body


@pytest.mark.asyncio
async def test_activity_path_reconciles_uncertain_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _AttemptFakeService(initial_labels=[])
    existing = _comment_row(stable_attempt_marker("att-activity-2") + "\nhello", "700", "b")
    service.listed.append(existing)
    _AttemptHttpClient.service = service
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _AttemptHttpClient)
    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 4177,
            "mode": "start",
            "attemptId": "att-activity-2",
            "deploymentId": "inst-device-a",
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    # Adopted the existing marker instead of creating a duplicate.
    assert len(service.created_bodies) == 0
    assert result.outputs["attemptCommentId"] == "700"
