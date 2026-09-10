"""Component tests for portable per-attempt handoffs (issue #4177).

Covers design sections 4 and 5.3 acceptance criteria: distinguishable
same-account deployments, the real Activity/adapter path with all required
fields and provenance validation, explicit outcomes for lost responses,
duplicates, out-of-order updates, spoofing, unsupported versions, and
missing predecessors, GitHub-alone reconstruction on a second device,
bounded internal retries with surviving holds, and redaction coverage.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from moonmind.workflows.temporal import github_issue_attempts as attempts


def _handoff(**overrides: Any) -> attempts.AttemptHandoff:
    base = {
        "attempt_id": attempts.new_attempt_id(
            repository="o/r", issue_number=4177, installation_id="device-a",
            workflow_id="wf-1", run_id="run-1",
        ),
        "deployment_id": "device-a",
        "repository": "o/r",
        "issue_number": 4177,
        "workflow_id": "wf-1",
        "run_id": "run-1",
        "activity": attempts.ACTIVITY_ACTIVE,
        "last_report": "working",
        "next_action": attempts.NEXT_CONTINUE_IMPLEMENTATION,
    }
    base.update(overrides)
    return attempts.AttemptHandoff(**base)


def _comment_dict(comment_id: int, handoff: attempts.AttemptHandoff, **overrides: Any) -> dict[str, Any]:
    payload = {"id": comment_id, "body": attempts.render_comment_body(handoff)}
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Work item 1: stable installation identity + bound attempt IDs
# ---------------------------------------------------------------------------


def test_two_same_account_devices_remain_distinguishable_across_restarts() -> None:
    first_a = attempts.resolve_installation_id(explicit="device-a")
    second_a = attempts.resolve_installation_id(environ={"MOONMIND_INSTALLATION_ID": "device-a"})
    device_b = attempts.resolve_installation_id(environ={"MOONMIND_INSTALLATION_ID": "device-b"})
    # Same deployment persists through restart/retry; devices differ even
    # when they share one GitHub account.
    assert first_a.installation_id == second_a.installation_id == "device-a"
    assert device_b.installation_id == "device-b"
    assert first_a.installation_id != device_b.installation_id


def test_missing_installation_identity_fails_fast_without_alias() -> None:
    with pytest.raises(ValueError, match="MOONMIND_INSTALLATION_ID"):
        attempts.resolve_installation_id(environ={})


def test_attempt_ids_are_unique_and_bound() -> None:
    first = attempts.new_attempt_id(repository="o/r", issue_number=4177, installation_id="device-a", workflow_id="wf", run_id="run-1")
    second = attempts.new_attempt_id(repository="o/r", issue_number=4177, installation_id="device-a", workflow_id="wf", run_id="run-1")
    assert attempts.is_attempt_id(first) and attempts.is_attempt_id(second)
    assert first != second
    # Same binding shares the binding prefix; different issues differ.
    assert first.split("-")[1] == second.split("-")[1]
    other_issue = attempts.new_attempt_id(repository="o/r", issue_number=4178, installation_id="device-a", workflow_id="wf", run_id="run-1")
    assert other_issue.split("-")[1] != first.split("-")[1]
    with pytest.raises(ValueError):
        attempts.new_attempt_id(repository="bad", issue_number=4177, installation_id="d", workflow_id="w", run_id="r")


# ---------------------------------------------------------------------------
# Work item 2: versioned bounded representation with all required fields
# ---------------------------------------------------------------------------


def test_comment_round_trip_carries_all_required_fields() -> None:
    handoff = _handoff(
        predecessor_attempt_id=attempts.new_attempt_id(
            repository="o/r", issue_number=4177, installation_id="device-a", workflow_id="wf-0", run_id="run-0"),
        predecessor_comment_id=11,
        writers_stopped=True,
        publication_disposition="pr-created",
        pr_url="https://github.com/o/r/pull/42",
        pr_head_sha="a" * 40,
        pr_base="main",
        outcome="failed-tests",
        remaining_requirements=("req-a", "req-b"),
        verification_summary="3 of 5 checks passed",
        failed_attempts=1,
        remaining_allowance=2,
        cooldown_until="2030-01-01T00:00:00Z",
    )
    body = attempts.render_comment_body(handoff)
    assert len(body) <= attempts.MAX_COMMENT_CHARS
    parsed = attempts.parse_comment_body(body)
    assert parsed.attempt_id == handoff.attempt_id
    assert parsed.deployment_id == "device-a"
    assert parsed.pr_url == "https://github.com/o/r/pull/42"
    assert parsed.pr_head_sha == "a" * 40
    assert list(parsed.remaining_requirements) == ["req-a", "req-b"]
    assert parsed.remaining_allowance == 2
    for activity in sorted(attempts.KNOWN_ACTIVITIES):
        assert attempts.AttemptHandoff(activity=activity, attempt_id=handoff.attempt_id, repository="o/r", issue_number=1)


def test_comment_bounds_hold_for_large_inputs() -> None:
    handoff = _handoff(last_report="x" * 5000, remaining_requirements=tuple(f"req-{i}-" + "y" * 500 for i in range(50)))
    body = attempts.render_comment_body(handoff)
    assert len(body) <= attempts.MAX_COMMENT_CHARS
    # Metadata survives truncation: the machine block still parses.
    assert attempts.parse_comment_body(body).attempt_id == handoff.attempt_id


# ---------------------------------------------------------------------------
# Work item 3: provenance and schema validation
# ---------------------------------------------------------------------------


def test_spoofed_marker_is_rejected_without_trusted_poster() -> None:
    body = attempts.render_comment_body(_handoff())
    validation = attempts.validate_attempt_comment(
        body=body, repository="o/r", issue_number=4177,
        poster_login="attacker", poster_type="user", trusted_poster_logins=["moonmind-bot"],
    )
    assert validation.ok is False and validation.code == attempts.VALIDATION_SPOOFED


def test_trusted_poster_with_wrong_issue_or_bad_ref_is_rejected() -> None:
    body = attempts.render_comment_body(_handoff())
    mismatched = attempts.validate_attempt_comment(
        body=body, repository="o/r", issue_number=9999,
        poster_login="moonmind-bot", poster_type="Bot", trusted_poster_logins=["moonmind-bot"],
    )
    assert mismatched.code == attempts.VALIDATION_ISSUE_MISMATCH
    bad_ref = attempts.validate_attempt_comment(
        body=attempts.render_comment_body(_handoff(pr_url="https://example.com/evil")),
        repository="o/r", issue_number=4177,
        poster_login="moonmind-bot", poster_type="Bot", trusted_poster_logins=["moonmind-bot"],
    )
    assert bad_ref.code == attempts.VALIDATION_REF


def test_unsupported_version_and_missing_predecessors_fail_closed() -> None:
    handoff = _handoff()
    body = attempts.render_comment_body(handoff).replace(" v=1 ", " v=999 ")
    assert "Unsupported attempt schema version" in attempts.try_parse_comment_body(body)[1]
    child = _handoff(predecessor_attempt_id=handoff.attempt_id)
    missing = attempts.validate_attempt_comment(
        body=attempts.render_comment_body(child), repository="o/r", issue_number=4177,
        poster_login="moonmind-bot", poster_type="Bot", trusted_poster_logins=["moonmind-bot"],
    )
    assert missing.code == attempts.VALIDATION_PREDECESSOR_MISSING
    unknown = attempts.validate_attempt_comment(
        body=attempts.render_comment_body(child), repository="o/r", issue_number=4177,
        poster_login="moonmind-bot", poster_type="Bot", trusted_poster_logins=["moonmind-bot"],
        known_attempt_ids=["att-aaaaaaaaaaaa-bbbbbbbb"],
    )
    assert unknown.code == attempts.VALIDATION_PREDECESSOR_UNKNOWN
    linked = attempts.validate_attempt_comment(
        body=attempts.render_comment_body(child), repository="o/r", issue_number=4177,
        poster_login="moonmind-bot", poster_type="Bot", trusted_poster_logins=["moonmind-bot"],
        known_attempt_ids=[handoff.attempt_id],
    )
    assert linked.ok is True


# ---------------------------------------------------------------------------
# Work item 4: serialized per-attempt writes
# ---------------------------------------------------------------------------


def test_marker_reconciliation_outcomes() -> None:
    first = _handoff()
    assert attempts.reconcile_marker_before_create(attempt_id=first.attempt_id, comments=[]).outcome == attempts.RECONCILE_CREATE
    one = _comment_dict(1, first)
    assert attempts.reconcile_marker_before_create(attempt_id=first.attempt_id, comments=[one]).outcome == attempts.RECONCILE_REUSE
    same = [_comment_dict(1, first), _comment_dict(2, first)]
    assert attempts.reconcile_marker_before_create(attempt_id=first.attempt_id, comments=same).outcome == attempts.RECONCILE_DUPLICATE_SAME
    divergent = dict(one)
    divergent_second = _comment_dict(2, _handoff(attempt_id=first.attempt_id, last_report="different"))
    assert attempts.reconcile_marker_before_create(
        attempt_id=first.attempt_id, comments=[divergent, divergent_second]).outcome == attempts.RECONCILE_CONFLICT


def test_updates_never_take_over_another_attempt() -> None:
    mine = _handoff()
    other = _handoff()
    comments = [_comment_dict(1, mine), _comment_dict(2, other)]
    assert attempts.select_own_comment(attempt_id=mine.attempt_id, comments=comments)["id"] == 1  # type: ignore[index]
    assert attempts.select_own_comment(attempt_id="att-aaaaaaaaaaaa-bbbbbbbb", comments=comments) is None


def test_progress_reports_coalesce_within_rate_limits() -> None:
    assert attempts.progress_update_allowed(last_update_epoch=None, now_epoch=1000.0) is True
    assert attempts.progress_update_allowed(last_update_epoch=900.0, now_epoch=1000.0) is False
    assert attempts.progress_update_allowed(last_update_epoch=100.0, now_epoch=1000.0) is True


# ---------------------------------------------------------------------------
# Work item 5: portable retry history
# ---------------------------------------------------------------------------


def test_retry_survives_new_workflow_ids_and_devices() -> None:
    first = _handoff(workflow_id="wf-1", run_id="run-1", failed_attempts=1, remaining_allowance=2)
    second = _handoff(workflow_id="wf-2", run_id="run-9", failed_attempts=2, remaining_allowance=1,
                      predecessor_attempt_id=first.attempt_id)
    decision = attempts.collect_retry_history([first, second], policy=attempts.RetryPolicy(max_attempts=3))
    assert decision.allowed is True and decision.remaining_allowance == 1 and decision.failed_attempts == 2


def test_hold_cooldown_exhaustion_and_resets() -> None:
    held = _handoff(operator_hold=True, hold_reason="operator investigating")
    assert attempts.collect_retry_history([held]).code == attempts.RETRY_HOLD
    cooling = _handoff(cooldown_until="2999999999")
    assert attempts.collect_retry_history([cooling], now_epoch=1000.0).code == attempts.RETRY_COOLDOWN
    exhausted = [_handoff(), _handoff(), _handoff()]
    assert attempts.collect_retry_history(exhausted, policy=attempts.RetryPolicy(max_attempts=3)).code == attempts.RETRY_EXHAUSTED
    reset = _handoff(reset_generation=1, reset_author="operator", reset_reason="audited retry reset")
    decision = attempts.collect_retry_history([_handoff(), reset], policy=attempts.RetryPolicy(max_attempts=3))
    assert (decision.allowed, decision.code) == (True, attempts.RETRY_RESET_AUTHORIZED)
    unaudited = _handoff(reset_generation=1)
    assert attempts.collect_retry_history([_handoff(), unaudited]).code == attempts.RETRY_MISSING_LINEAGE


# ---------------------------------------------------------------------------
# Work item 6: proposed vs completed release
# ---------------------------------------------------------------------------


def test_proposed_release_is_not_released_until_gates_hold() -> None:
    proposed = _handoff(activity=attempts.ACTIVITY_RELEASING, proposed_disposition="recovery-needed",
                        writers_stopped=True)
    assert attempts.evaluate_release(proposed).released is False
    released = _handoff(activity=attempts.ACTIVITY_RELEASED, released=True, writers_stopped=True,
                        mutations_settled=True, preservation_verified=False,
                        saved_branch="moonmind/save", saved_sha="b" * 40,
                        label_outcome_observed="status: recovery-needed added")
    assert attempts.evaluate_release(released).code == attempts.RELEASE_BLOCKED_PRESERVATION
    assert attempts.terminal_attempt_may_write(released) is False
    complete = _handoff(activity=attempts.ACTIVITY_RELEASED, released=True, writers_stopped=True,
                        mutations_settled=True, preservation_verified=True,
                        label_outcome_observed="labels reconciled")
    assert attempts.evaluate_release(complete).released is True
    assert attempts.terminal_attempt_may_write(complete) is False
    active = _handoff(activity=attempts.ACTIVITY_ACTIVE)
    assert attempts.terminal_attempt_may_write(active) is True


# ---------------------------------------------------------------------------
# Work item 7: redaction
# ---------------------------------------------------------------------------


def test_comment_and_error_paths_redact_secrets() -> None:
    secret = "ghp_" + "s" * 30
    body = attempts.render_comment_body(_handoff(last_report=f"token {secret} leaked", outcome="api_key=supersecret"))
    assert secret not in body and "supersecret" not in body
    assert "[REDACTED]" in body or "=[REDACTED]" in body
    private = attempts.render_comment_body(_handoff(workflow_link="http://localhost:8000/artifacts/run"))
    assert "localhost" not in private
    assert secret not in attempts.redacted_error_summary(f"create failed {secret}")


# ---------------------------------------------------------------------------
# Acceptance: real Activity/adapter path through a fake trusted boundary
# ---------------------------------------------------------------------------


class _AttemptFakeService:
    """Fake trusted GitHub boundary speaking the real adapter contract."""

    def __init__(self, trusted: str = "moonmind-bot") -> None:
        self.comments: list[dict[str, Any]] = []
        self.next_id = 101
        self.trusted = trusted
        self.fail_create: str | None = None

    async def list_issue_comments(self, *, repo: str, issue_number: int):
        return {"ok": True, "reasonCode": "listed", "comments": list(self.comments)}

    async def create_issue_comment(self, *, repo: str, issue_number: int, body: str, github_token: str | None = None):
        if self.fail_create == "unknown":
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "lost response"}
        self.next_id += 1
        self.comments.append({"id": self.next_id, "body": body, "poster_login": self.trusted,
                              "poster_type": "Bot", "created_at": "", "updated_at": "1"})
        return {"ok": True, "reasonCode": "created", "commentId": self.next_id}

    async def update_issue_comment(self, *, repo: str, comment_id: int, body: str, github_token: str | None = None):
        for comment in self.comments:
            if comment.get("id") == comment_id:
                comment["body"] = body
                comment["updated_at"] = "2"
                return {"ok": True, "reasonCode": "updated", "commentId": comment_id}
        return {"ok": False, "reasonCode": "denied", "summary": "not found"}


def _inputs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "repository": "o/r", "issueNumber": 4177, "installationId": "device-a",
        "workflowId": "wf-1", "runId": "run-1", "trustedPosterLogins": ["moonmind-bot"],
        "lastReport": "starting", "activity": attempts.ACTIVITY_ACTIVE,
        "nextAction": attempts.NEXT_CONTINUE_IMPLEMENTATION,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_activity_announces_and_updates_bounded_comments() -> None:
    service = _AttemptFakeService()
    announced = await attempts.publish_attempt_handoff(_inputs(), {}, github_service_factory=lambda: service)
    assert announced.status == "COMPLETED" and announced.outputs["decision"] == "announced"
    assert len(service.comments) == 1 and len(service.comments[0]["body"]) <= attempts.MAX_COMMENT_CHARS
    parsed = attempts.parse_comment_body(service.comments[0]["body"])
    assert parsed.deployment_id == "device-a" and parsed.repository == "o/r" and parsed.issue_number == 4177
    attempt_id = announced.outputs["attemptId"]
    # Lost create response reconciles to the same marker instead of duplicating.
    again = await attempts.publish_attempt_handoff(_inputs(attemptId=attempt_id), {}, github_service_factory=lambda: service)
    assert again.outputs["decision"] == "reused" and len(service.comments) == 1
    # Progress update flows through the same adapter path with provenance intact.
    progressed = await attempts.publish_attempt_handoff(
        _inputs(attemptId=attempt_id, updateMode="progress", lastReport="half done", lastUpdateEpoch=0.0, nowEpoch=10000.0),
        {}, github_service_factory=lambda: service)
    assert progressed.outputs["decision"] == "published"
    assert "half done" in service.comments[0]["body"]


@pytest.mark.asyncio
async def test_activity_explicit_failure_outcomes() -> None:
    service = _AttemptFakeService()
    announced = await attempts.publish_attempt_handoff(_inputs(), {}, github_service_factory=lambda: service)
    attempt_id = announced.outputs["attemptId"]
    # Coalesced progress within the rate limit.
    coalesced = await attempts.publish_attempt_handoff(
        _inputs(attemptId=attempt_id, updateMode="progress", lastReport="tick", lastUpdateEpoch=1000.0, nowEpoch=1100.0),
        {}, github_service_factory=lambda: service)
    assert coalesced.outputs["decision"] == "coalesced"
    # Out-of-order local update against newer remote state is refused.
    stale = await attempts.publish_attempt_handoff(
        _inputs(attemptId=attempt_id, updateMode="progress", lastReport="older view",
                lastUpdateEpoch=0.0, nowEpoch=20000.0,
                baseCommentBody=attempts.render_comment_body(_handoff(attempt_id=attempt_id, last_report="ancient"))),
        {}, github_service_factory=lambda: service)
    assert stale.status == "FAILED" and stale.outputs["reasonCode"] == "stale_base"
    # Conflicting same-marker copies require attention, not timestamp wins.
    mine = attempts.parse_comment_body(service.comments[0]["body"])
    divergent_body = attempts.render_comment_body(
        attempts.AttemptHandoff(attempt_id=mine.attempt_id, deployment_id="device-a", repository="o/r",
                                issue_number=4177, workflow_id="wf-x", run_id="run-x", last_report="forked"))
    service.comments.append({"id": 999, "body": divergent_body, "poster_login": "moonmind-bot",
                             "poster_type": "Bot", "created_at": "", "updated_at": ""})
    conflict = await attempts.publish_attempt_handoff(_inputs(attemptId=attempt_id), {}, github_service_factory=lambda: service)
    assert conflict.outputs["decision"] == "attention"
    # Spoofed markers never authenticate: untrusted posters yield no lineage.
    reconstruction = await attempts.reconstruct_retry_from_comments(
        repository="o/r", issue_number=4177, comments=service.comments, trusted_poster_logins=["someone-else"])
    assert reconstruction["handoffs"] == []
    assert reconstruction["retry"]["code"] in {attempts.RETRY_OK, attempts.RETRY_EXHAUSTED}


@pytest.mark.asyncio
async def test_device_b_reconstructs_retry_restrictions_from_github_alone() -> None:
    service = _AttemptFakeService()
    first = await attempts.publish_attempt_handoff(
        _inputs(lastReport="no progress", outcome="failed"), {}, github_service_factory=lambda: service)
    first_id = first.outputs["attemptId"]
    second_inputs = _inputs(workflowId="wf-2", runId="run-2", installationId="device-b",
                            predecessorAttemptId=first_id, lastReport="continuing",
                            operatorHold=True, holdReason="operator investigating")
    second = await attempts.publish_attempt_handoff(second_inputs, {}, github_service_factory=lambda: service)
    assert second.status == "COMPLETED"
    # A new device with no private logs derives the hold from GitHub alone.
    listed = await service.list_issue_comments(repo="o/r", issue_number=4177)
    reconstruction = await attempts.reconstruct_retry_from_comments(
        repository="o/r", issue_number=4177, comments=listed["comments"],
        trusted_poster_logins=["moonmind-bot"], policy=attempts.RetryPolicy(max_attempts=5))
    assert reconstruction["retry"]["code"] == attempts.RETRY_HOLD
    assert reconstruction["retry"]["failedAttempts"] == 2
    # Internal retries stay within the controlling attempt: publishing more
    # progress updates never mints new attempts.
    before = len(service.comments)
    progress = await attempts.publish_attempt_handoff(
        _inputs(attemptId=second.outputs["attemptId"], installationId="device-b", updateMode="progress",
                lastReport="internal retry 7", internalRetryCount=7, lastUpdateEpoch=0.0, nowEpoch=50000.0),
        {}, github_service_factory=lambda: service)
    assert progress.outputs["decision"] == "published" and len(service.comments) == before


@pytest.mark.asyncio
async def test_terminal_release_gate_and_redaction_on_publish_path() -> None:
    service = _AttemptFakeService()
    announced = await attempts.publish_attempt_handoff(_inputs(), {}, github_service_factory=lambda: service)
    attempt_id = announced.outputs["attemptId"]
    secret = "ghp_" + "q" * 30
    terminal = await attempts.publish_attempt_handoff(
        _inputs(attemptId=attempt_id, updateMode="terminal", activity=attempts.ACTIVITY_RELEASING,
                proposedDisposition="recovery-needed", writersStopped=True, mutationsSettled=True,
                lastReport=f"saw {secret} in logs"),
        {}, github_service_factory=lambda: service)
    assert terminal.status == "COMPLETED" and secret not in service.comments[0]["body"]
    assert terminal.outputs["release"]["code"] == attempts.RELEASE_PROPOSED
    released_inputs = _inputs(attemptId=attempt_id, updateMode="terminal", activity=attempts.ACTIVITY_RELEASED,
                              released=True, writersStopped=True, mutationsSettled=True,
                              preservationVerified=True, labelOutcomeObserved="recovery-needed added")
    released = await attempts.publish_attempt_handoff(released_inputs, {}, github_service_factory=lambda: service)
    assert released.outputs["release"]["released"] is True
    # A reconnecting old process performs no further writes once released.
    after = await attempts.publish_attempt_handoff(
        _inputs(attemptId=attempt_id, updateMode="progress", lastReport="stale retry",
                lastUpdateEpoch=0.0, nowEpoch=99999.0),
        {}, github_service_factory=lambda: service)
    assert after.status == "FAILED" and after.outputs["reasonCode"] == "already_released"


def test_unsupported_comment_version_produces_explicit_outcome() -> None:
    body = "<!-- moonmind-attempt: id=att-aaaaaaaaaaaa-bbbbbbbb v=999 -->\n```moonmind-attempt-json\n{}\n```"
    _, error = attempts.try_parse_comment_body(body)
    assert "Unsupported attempt schema version" in error
    validation = attempts.validate_attempt_comment(
        body=body, repository="o/r", issue_number=4177,
        poster_login="moonmind-bot", poster_type="Bot", trusted_poster_logins=["moonmind-bot"])
    assert validation.code == attempts.VALIDATION_VERSION


def test_missing_referenced_history_never_infers_fresh_start() -> None:
    predecessor = attempts.new_attempt_id(repository="o/r", issue_number=4177, installation_id="device-a",
                                          workflow_id="wf-0", run_id="run-0")
    child = _handoff(predecessor_attempt_id=predecessor)
    validation = attempts.validate_attempt_comment(
        body=attempts.render_comment_body(child), repository="o/r", issue_number=4177,
        poster_login="moonmind-bot", poster_type="Bot", trusted_poster_logins=["moonmind-bot"],
        known_attempt_ids=[],
    )
    assert validation.code == attempts.VALIDATION_PREDECESSOR_MISSING
    assert "fresh start" in validation.detail
    decision = attempts.collect_retry_history([], policy=attempts.RetryPolicy(max_attempts=3))
    assert decision.code == attempts.RETRY_OK and decision.remaining_allowance == 2
