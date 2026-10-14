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
from tests.support.issue_claims import issue_claim_store, ClaimCommentFixture  # noqa: F401

from moonmind.workflows.temporal import story_output_tools as story_tools
from moonmind.workflows.temporal.github_issue_attempts import (
    ATTEMPT_ACTIVITY_ACTIVE,
    activity_for_lifecycle_mode,
    build_attempt_handoff,
    build_retry_reset_handoff,
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


def test_default_installation_identity_survives_container_recreation(
    tmp_path, monkeypatch
) -> None:
    """Compose mounts only ``moonmind_secrets`` (``var/secrets``) durably.

    Every other path under ``var`` belongs to the container and is discarded
    when an update recreates it. An identity written there changed on every
    update, so each deployment's own earlier attempts looked foreign and
    their accounting was never finished.
    """
    import shutil

    monkeypatch.delenv("MOONMIND_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("MOONMIND_INSTALLATION_ID_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    durable = tmp_path / "var" / "secrets"

    first = get_or_create_installation_id()

    for entry in (tmp_path / "var").iterdir():
        if entry != durable:
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    assert get_or_create_installation_id() == first


def test_replicas_racing_on_an_empty_volume_agree_on_one_identity(
    tmp_path, monkeypatch
) -> None:
    """The losing writer adopts the winner's identity instead of its own."""
    import os as real_os

    from moonmind.workflows.temporal import github_issue_attempts as attempts

    monkeypatch.delenv("MOONMIND_INSTALLATION_ID", raising=False)
    target = tmp_path / "secrets" / "moonmind-installation-id"
    real_link = real_os.link

    def _winner_publishes_first(source, destination, *args, **kwargs):
        # Another replica completes its create between our read and ours.
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("inst-winnerreplica01\n", encoding="utf-8")
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(attempts.os, "link", _winner_publishes_first)

    assert get_or_create_installation_id(path=target) == "inst-winnerreplica01"
    assert target.read_text(encoding="utf-8").strip() == "inst-winnerreplica01"
    assert [entry.name for entry in target.parent.iterdir()] == [target.name]


def test_an_unpersistable_identity_stays_stable_within_the_process(
    tmp_path, monkeypatch
) -> None:
    """A failed write must not mint a different identity per call."""
    monkeypatch.delenv("MOONMIND_INSTALLATION_ID", raising=False)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    target = blocker / "moonmind-installation-id"

    first = get_or_create_installation_id(path=target)

    assert first.startswith("inst-")
    assert get_or_create_installation_id(path=target) == first


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
    # The only reset is an audited record in lineage naming who authorized it.
    exhausted = [_handoff(attempt_id=f"att-{index}", outcome="failed") for index in range(3)]
    reset = build_retry_reset_handoff(
        attempt_id="att-reset", deployment_id="inst-device-a",
        repository="MoonLadderStudios/MoonMind", issue_number=4177,
        authorized_by="op-1", authorized_at="2026-09-26T00:00:00+00:00",
        reason="audited reset", predecessor_attempt_id="att-2",
        superseded_attempt_ids=[item.attempt_id for item in exhausted],
    )
    assert compute_effective_retry(
        [*exhausted, reset], max_attempts=3, authorized_resets={"att-reset"}
    ).remaining == 3
    # A reset record the caller did not authenticate resets nothing.
    assert compute_effective_retry([*exhausted, reset], max_attempts=3).remaining == 0
    with pytest.raises(ValueError, match="retry_reset_unauthorized"):
        build_retry_reset_handoff(
            attempt_id="att-reset", deployment_id="inst-device-a",
            repository="MoonLadderStudios/MoonMind", issue_number=4177,
            authorized_by="", authorized_at="2026-09-26T00:00:00+00:00", reason="x",
        )


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


class _AttemptFakeService(ClaimCommentFixture):
    def __init__(self, initial_labels: list[str] | None = None) -> None:
        self.labels = list(initial_labels) if initial_labels is not None else []
        self.operations: list[tuple[str, str]] = []
        self.created_bodies: list[str] = []
        self.listed: list[dict[str, Any]] = []

    async def resolve_github_token(self, *, repo: str, operations=()):
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
        comment = {"id": 900 + len(self.created_bodies), "body": redacted, "user": {"id": 123}}
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
    assert result.outputs["attemptId"] != "att-activity-1"
    assert result.outputs["deploymentId"] == "inst-device-a"
    assert result.outputs["attemptActivity"] == "preparing"
    assert result.outputs["attemptHandoff"]["repository"] == "MoonLadderStudios/MoonMind"
    assert len(service.created_bodies) == 1
    body = service.created_bodies[0]
    assert stable_attempt_marker(result.outputs["attemptId"], 2) in body
    assert result.outputs["attemptHandoff"]["formatVersion"] == 2
    assert result.outputs["attemptHandoff"]["leaseExpiresAt"]
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
    assert result.status == "FAILED"
    # A public marker without the durable receipt is not authority to adopt it.
    assert len(service.created_bodies) == 0
    assert result.outputs["reasonCode"] == "active_attempt_conflict"


@pytest.mark.parametrize("remaining", [0, "0"])
def test_transition_handoff_retains_explicit_exhausted_budget(remaining):
    handoff = story_tools._github_attempt_handoff_for_transition(
        mode="needs_attention",
        repository="o/r",
        issue_number=42,
        inputs={"retryRemaining": remaining},
        context={},
        pull_request_url="",
    )
    assert handoff.retry_remaining == 0


def test_sparse_transition_preserves_canonical_claim_evidence():
    from moonmind.workflows.temporal.issue_claim_store import ClaimReceipt

    prior = _handoff(
        saved_branch="saved-work",
        saved_sha="abc123",
        last_report="Need automation proof",
        predecessor_attempt_id="att-parent",
        predecessor_comment_id="99",
        retry_remaining=0,
        next_action="verify",
    )
    receipt = ClaimReceipt(
        owner="default/wf-1",
        repository=prior.repository.casefold(),
        issue_number=4177,
        attempt_id=prior.attempt_id,
        actor_id="123",
        comment_body=render_attempt_comment(prior),
        comment_id="100",
        confirmed=True,
        pending_comment_body=None,
        released=False,
    )
    result = story_tools._github_attempt_handoff_for_transition(
        mode="code_review",
        repository=prior.repository,
        issue_number=4177,
        inputs={"attemptId": "stale-attempt", "workflowId": "stale-workflow"},
        context={"issue_claim_receipt": receipt},
        pull_request_url="",
    )
    for field in (
        "attempt_id",
        "deployment_id",
        "workflow_id",
        "run_id",
        "predecessor_attempt_id",
        "predecessor_comment_id",
        "last_report",
        "pr_url",
        "pr_head",
        "pr_base",
        "saved_branch",
        "saved_sha",
        "remaining_requirements",
        "verification_summary",
        "retry_history",
        "retry_allowance",
        "retry_remaining",
    ):
        assert getattr(result, field) == getattr(prior, field), field
    assert result.activity == "awaiting-review"
    assert result.outcome == "implemented"
    assert result.next_action == "continue_review"
    updated = story_tools._github_attempt_handoff_for_transition(
        mode="code_review",
        repository=prior.repository,
        issue_number=4177,
        inputs={
            "saved_sha": "newsha",
            "remainingRequirements": [],
            "lastReport": "Verified",
        },
        context={"issue_claim_receipt": receipt},
        pull_request_url=prior.pr_url,
    )
    assert updated.saved_sha == "newsha"
    assert updated.remaining_requirements == ()
    assert updated.last_report == "Verified"
    recovering = story_tools._github_attempt_handoff_for_transition(
        mode="recovery_needed",
        repository=prior.repository,
        issue_number=4177,
        inputs={},
        context={"issue_claim_receipt": receipt},
        pull_request_url="",
    )
    assert recovering.next_action == "verify"


@pytest.mark.parametrize(
    "source, candidate",
    [
        (source, candidate)
        for source in (
            "inputs",
            "tool_previous",
            "context_previous",
            "unbound_previous",
            "matching_previous",
            "context_after_unbound",
        )
        for candidate in ("pr", "branch")
    ]
    + [("argument", "pr")],
)
@pytest.mark.parametrize("include_revision", [False, True])
def test_sparse_transition_binds_revision_to_replacement_reference(
    source, candidate, include_revision
):
    from moonmind.workflows.temporal.github_issue_finalization import (
        preserve_authoritative_output,
    )
    from moonmind.workflows.temporal.issue_claim_store import ClaimReceipt

    prior = _handoff(
        pr_head="old-pr-sha", saved_branch="old-branch", saved_sha="old-saved-sha"
    )
    receipt = ClaimReceipt(
        owner="default/wf-1",
        repository=prior.repository.casefold(),
        issue_number=4177,
        attempt_id=prior.attempt_id,
        actor_id="123",
        comment_body=render_attempt_comment(prior),
        comment_id="100",
        confirmed=True,
        pending_comment_body=None,
        released=False,
    )
    reference_key, revision_key = (
        ("prUrl", "prHead") if candidate == "pr" else ("savedBranch", "savedSha")
    )
    new_reference = (
        "https://github.com/MoonLadderStudios/MoonMind/pull/99"
        if candidate == "pr"
        else "replacement-branch"
    )
    replacement = {reference_key: new_reference}
    if include_revision:
        replacement[revision_key] = "new-revision"
    inputs = {}
    context = {"issue_claim_receipt": receipt}
    pull_request_url = ""
    if source == "inputs":
        inputs.update(replacement)
        inputs["previousOutputs"] = prior.to_dict()
    elif source == "tool_previous":
        inputs["previousOutputs"] = replacement
        context["previousOutputs"] = prior.to_dict()
    elif source == "context_previous":
        context["previousOutputs"] = replacement
    elif source == "unbound_previous":
        inputs.update(replacement)
        inputs["previousOutputs"] = {revision_key: "old-unbound-revision"}
    elif source == "matching_previous":
        inputs.update(replacement)
        inputs["previousOutputs"] = {
            reference_key: new_reference,
            revision_key: "new-revision",
        }
    elif source == "context_after_unbound":
        inputs["previousOutputs"] = {revision_key: "old-unbound-revision"}
        context["previousOutputs"] = replacement
    else:
        pull_request_url = new_reference
        if include_revision:
            inputs[revision_key] = "new-revision"
        inputs["previousOutputs"] = prior.to_dict()
    result = story_tools._github_attempt_handoff_for_transition(
        mode="recovery_needed",
        repository=prior.repository,
        issue_number=4177,
        inputs=inputs,
        context=context,
        pull_request_url=pull_request_url,
    )
    result_reference, result_revision = (
        (result.pr_url, result.pr_head)
        if candidate == "pr"
        else (result.saved_branch, result.saved_sha)
    )
    assert result_reference == new_reference
    assert result_revision == (
        "new-revision" if include_revision or source == "matching_previous" else ""
    )
    if candidate == "pr":
        assert result.saved_branch == prior.saved_branch
        assert result.saved_sha == prior.saved_sha
    else:
        assert result.pr_url == prior.pr_url
        assert result.pr_head == prior.pr_head
    preservation = preserve_authoritative_output(
        {
            "saveMethod": (
                "pr_head_verified" if candidate == "pr" else "saved_branch_verified"
            ),
            "prUrl": result.pr_url,
            "prHeadSha": result.pr_head,
            "savedBranch": result.saved_branch,
            "savedSha": result.saved_sha,
            "preservationVerified": True,
        }
    )
    assert preservation["preserved"] is bool(result_revision)
    if result_revision:
        assert f"{result_reference}@{result_revision}" in preservation["summary"]
    else:
        assert preservation["reasonCode"] == "preservation_incomplete"
        assert preservation["workspaceRetained"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requirements, expected",
    [
        ({}, None),
        ({"remainingRequirements": None}, None),
        ({"remaining_requirements": None}, None),
        ({"remainingRequirements": []}, []),
        ({"remaining_requirements": []}, []),
        ({"remainingRequirements": ["unfinished"]}, ["unfinished"]),
    ],
)
async def test_failed_attempt_adapter_preserves_omitted_remaining_requirements(
    monkeypatch, requirements, expected
):
    from moonmind.workflows.temporal.activities import (
        github_issue_finalization_activities,
    )
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalIntegrationActivities,
    )

    observed = []

    async def finalize(**kwargs):
        observed.append(kwargs["remaining_requirements"])
        return {
            "released": False,
            "reasonCode": "test_boundary",
            "disposition": "",
            "summary": "",
            "transition": None,
            "mutation": None,
            "mutationOutcome": None,
            "completionRoute": "",
            "mergeAuthorized": False,
            "workspaceRetained": True,
            "pendingSync": None,
            "commentId": None,
        }

    monkeypatch.setattr(
        github_issue_finalization_activities, "finalize_failed_attempt", finalize
    )
    result = await TemporalIntegrationActivities.github_issue_finalize_failed_attempt(
        None,
        {
            "repository": "o/r",
            "issueNumber": 4177,
            "executionEvent": "failed",
            **requirements,
        },
    )
    assert result["reasonCode"] == "test_boundary"
    assert observed == [expected]


@pytest.mark.parametrize(
    "method, reference_key, revision_key",
    [
        ("pr_head_verified", "prUrl", "prHeadSha"),
        ("pr_head_verified", "pr_url", "pr_head_sha"),
        ("pr_head_verified", "pr_url", "revision"),
        ("saved_branch_verified", "savedBranch", "savedSha"),
        ("saved_branch_verified", "saved_branch", "saved_sha"),
        ("saved_branch_verified", "saved_branch", "revision"),
    ],
)
def test_preservation_pair_keeps_supported_aliases(
    method, reference_key, revision_key
):
    from moonmind.workflows.temporal.github_issue_finalization import (
        preserve_authoritative_output,
    )

    result = preserve_authoritative_output(
        {
            "save_method": method,
            reference_key: "candidate",
            revision_key: "head-sha",
            "preservation_verified": True,
        }
    )
    assert result["preserved"] is True
    assert "candidate@head-sha" in result["summary"]


@pytest.fixture
def transition_claim():
    from moonmind.workflows.temporal.issue_claim_store import ClaimReceipt

    prior = _handoff(
        pr_head="old-pr-sha",
        saved_branch="old-branch",
        saved_sha="old-saved-sha",
        predecessor_attempt_id="att-parent",
        predecessor_comment_id="99",
        retry_history=["att-older", "att-parent", "Older attempt failed"],
    )
    receipt = ClaimReceipt(
        owner="default/wf-1",
        repository=prior.repository.casefold(),
        issue_number=4177,
        attempt_id=prior.attempt_id,
        actor_id="123",
        comment_body=render_attempt_comment(prior),
        comment_id="100",
        confirmed=True,
        pending_comment_body=None,
        released=False,
    )
    return prior, {"issue_claim_receipt": receipt}


@pytest.mark.parametrize(
    "reference_key, candidate, source",
    [
        (key, candidate, source)
        for candidate, keys in (
            ("pr", ("prUrl", "pr_url", "pullRequestUrl", "pull_request_url")),
            ("branch", ("savedBranch", "saved_branch", "branch")),
        )
        for key in keys
        for source in ("inputs", "tool_previous", "context_previous")
    ],
)
@pytest.mark.parametrize("revision_key", ["headSha", "head_sha"])
def test_generic_transition_revision_only_updates_its_supplied_reference(
    transition_claim, reference_key, candidate, source, revision_key
):
    prior, context = transition_claim
    reference = (
        "https://github.com/MoonLadderStudios/MoonMind/pull/99"
        if candidate == "pr"
        else "replacement-branch"
    )
    evidence = {revision_key: "new-sha"}
    evidence[reference_key] = reference
    inputs = evidence if source == "inputs" else {}
    if source == "tool_previous":
        inputs["previousOutputs"] = evidence
    elif source == "context_previous":
        context["previousOutputs"] = evidence
    result = story_tools._github_attempt_handoff_for_transition(
        mode="recovery_needed",
        repository=prior.repository,
        issue_number=4177,
        inputs=inputs,
        context=context,
        pull_request_url="",
    )
    if candidate == "pr":
        assert (result.pr_url, result.pr_head) == (reference, "new-sha")
        assert (result.saved_branch, result.saved_sha) == (
            prior.saved_branch,
            prior.saved_sha,
        )
    else:
        assert (result.saved_branch, result.saved_sha) == (reference, "new-sha")
        assert (result.pr_url, result.pr_head) == (prior.pr_url, prior.pr_head)


@pytest.mark.parametrize("revision_key", ["headSha", "head_sha"])
def test_generic_transition_revision_without_reference_keeps_both_candidates(
    transition_claim, revision_key
):
    prior, context = transition_claim
    result = story_tools._github_attempt_handoff_for_transition(
        mode="recovery_needed",
        repository=prior.repository,
        issue_number=4177,
        inputs={revision_key: "unbound-sha"},
        context=context,
        pull_request_url="",
    )
    assert (result.pr_url, result.pr_head) == (prior.pr_url, prior.pr_head)
    assert (result.saved_branch, result.saved_sha) == (
        prior.saved_branch,
        prior.saved_sha,
    )


@pytest.mark.parametrize(
    "container", [None, "metadata", "publishContext", "publish_context"]
)
@pytest.mark.parametrize("source", ["tool_previous", "context_previous"])
@pytest.mark.parametrize("revision_key", ["headSha", "head_sha"])
@pytest.mark.parametrize("new_branch", [None, "replacement-branch"])
def test_resolved_historical_pr_does_not_bind_current_generic_revision(
    transition_claim, container, source, revision_key, new_branch
):
    prior, context = transition_claim
    history = {"pullRequestUrl": prior.pr_url}
    if container:
        history = {container: history}
    inputs = {revision_key: "current-sha"}
    if new_branch:
        inputs["savedBranch"] = new_branch
    if source == "tool_previous":
        inputs["previousOutputs"] = history
    else:
        context["previousOutputs"] = history
    resolved_url = story_tools._github_status_pull_request_url(inputs, context)
    assert resolved_url == prior.pr_url
    result = story_tools._github_attempt_handoff_for_transition(
        mode="recovery_needed",
        repository=prior.repository,
        issue_number=4177,
        inputs=inputs,
        context=context,
        pull_request_url=resolved_url,
    )
    assert (result.pr_url, result.pr_head) == (prior.pr_url, prior.pr_head)
    assert (result.saved_branch, result.saved_sha) == (
        (new_branch, "current-sha")
        if new_branch
        else (prior.saved_branch, prior.saved_sha)
    )


@pytest.mark.parametrize("candidate", ["pr", "branch"])
def test_specific_transition_revision_can_update_unnamed_other_candidate(
    transition_claim, candidate
):
    prior, context = transition_claim
    inputs = (
        {"pull_request_url": prior.pr_url, "saved_sha": "new-saved-sha"}
        if candidate == "pr"
        else {"branch": prior.saved_branch, "pr_head": "new-pr-sha"}
    )
    result = story_tools._github_attempt_handoff_for_transition(
        mode="recovery_needed",
        repository=prior.repository,
        issue_number=4177,
        inputs={**inputs, "head_sha": "generic-sha"},
        context=context,
        pull_request_url="",
    )
    assert result.pr_head == ("generic-sha" if candidate == "pr" else "new-pr-sha")
    assert result.saved_sha == ("new-saved-sha" if candidate == "pr" else "generic-sha")


@pytest.mark.parametrize("history_key", ["retryHistory", "retry_history"])
@pytest.mark.parametrize("source", ["inputs", "tool_previous", "context_previous"])
@pytest.mark.parametrize(
    "history", [["att-parent", "Current repair retried", "Current repair retried"], []]
)
def test_transition_accepts_updated_retry_history_without_changing_claim_identity(
    transition_claim, history_key, source, history
):
    prior, context = transition_claim
    evidence = {
        history_key: history,
        "internalRetryCount": 1,
        "attempt_id": "wrong-attempt",
        "workflow_id": "wrong-workflow",
        "run_id": "wrong-run",
        "deployment_id": "inst-wrong",
        "predecessor_attempt_id": "wrong-parent",
        "predecessor_comment_id": "999",
    }
    inputs = evidence if source == "inputs" else {}
    if source == "tool_previous":
        inputs["previousOutputs"] = evidence
    elif source == "context_previous":
        context["previousOutputs"] = evidence
    result = story_tools._github_attempt_handoff_for_transition(
        mode="in_progress",
        repository=prior.repository,
        issue_number=4177,
        inputs=inputs,
        context=context,
        pull_request_url="",
    )
    assert result.retry_history == tuple(
        dict.fromkeys((*prior.retry_history, *history))
    )
    assert result.internal_retry_count == 1
    for field in (
        "attempt_id",
        "workflow_id",
        "run_id",
        "deployment_id",
        "predecessor_attempt_id",
        "predecessor_comment_id",
    ):
        assert getattr(result, field) == getattr(prior, field), field
    predecessor = _handoff(attempt_id="att-parent", outcome="failed")
    before = compute_effective_retry([predecessor, prior], max_attempts=3)
    after = compute_effective_retry([predecessor, result], max_attempts=3)
    assert after.remaining == before.remaining
    assert after.charged_attempts == before.charged_attempts
