"""Unit tests for portable per-attempt handoffs (MoonLadderStudios/MoonMind#4177).

Covers the six acceptance checkboxes through the real Activity/adapter path
(``publish_attempt_handoff`` + ``GitHubService`` issue-comment methods with
httpx fakes): installation/attempt identity, versioned bounded comments with
all required fields and provenance validation, lost-response/duplicate/
out-of-order/spoofed/unsupported/missing-predecessor outcomes, cross-device
reconstruction from GitHub alone, bounded internal retries with surviving
hold/cancellation evidence, and redaction of comment + structured-error paths.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from moonmind.workflows.temporal import github_issue_attempt as attempt
from moonmind.workflows.temporal.github_issue_attempt import AttemptHandoff


def _handoff(**overrides: Any) -> AttemptHandoff:
    identity, error = attempt.build_attempt_identity(
        repository="MoonLadderStudios/MoonMind",
        issue_number=4177,
        workflow_id="wf-1",
        run_id="run-1",
        installation_id="device-a",
    )
    assert error == ""
    params: dict[str, Any] = {
        "attempt_id": identity["attemptId"],
        "deployment_id": identity["deploymentId"],
        "repository": identity["repository"],
        "issue_number": identity["issueNumber"],
        "workflow_id": identity["workflowId"],
        "run_id": identity["runId"],
        "activity": attempt.ACTIVITY_ACTIVE,
        "last_report": "Working on the handoff.",
        "writers_stopped": False,
        "outcome": "pending",
        "remaining_requirements": ("req-1", "req-2"),
        "verification_summary": "Not yet verified.",
        "next_action": "continue-implementation",
        "policy_lineage": "policy-v1",
    }
    params.update(overrides)
    return AttemptHandoff(**params)


def _full_handoff(**overrides: Any) -> AttemptHandoff:
    predecessor, _ = attempt.build_attempt_identity(
        repository="MoonLadderStudios/MoonMind",
        issue_number=4177,
        workflow_id="wf-0",
        run_id="run-0",
        installation_id="device-a",
    )
    head = "a" * 40
    params: dict[str, Any] = {
        "predecessor_attempt_id": predecessor["attemptId"],
        "predecessor_comment_id": 101,
        "last_activity_at": "2026-09-10T16:00:00Z",
        "stop_evidence": "All writers stopped; no pending publications.",
        "pending_disposition": "to_recovery_needed",
        "pr_url": "https://github.com/MoonLadderStudios/MoonMind/pull/9999",
        "pr_head_sha": head,
        "pr_base": "main",
        "met_requirements": ("req-0",),
        "cooldown_until": "2026-09-10T17:00:00Z",
    }
    params.update(overrides)
    return _handoff(**params)


# ---------------------------------------------------------------------------
# Req 1: two devices sharing one GitHub account stay distinguishable
# ---------------------------------------------------------------------------


def test_installation_identity_requires_explicit_configuration() -> None:
    installation_id, error = attempt.resolve_installation_id(explicit="", env={})
    assert installation_id == ""
    assert "MOONMIND_INSTALLATION_ID" in error


def test_installation_identity_rejects_competing_alias_shapes() -> None:
    for bad in ["", "ab", "shared github user", "device/a!"]:
        installation_id, error = attempt.resolve_installation_id(explicit=bad, env={})
        assert installation_id == ""
        assert error


def test_two_devices_with_same_account_stay_distinguishable() -> None:
    device_a, error_a = attempt.resolve_installation_id(explicit="device-a", env={})
    device_b, error_b = attempt.resolve_installation_id(explicit="device-b", env={"MOONMIND_INSTALLATION_ID": "device-b"})
    assert (device_a, error_a) == ("device-a", "")
    assert (device_b, error_b) == ("device-b", "")
    assert device_a != device_b
    identity_a, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf", run_id="run-a", installation_id=device_a,
    )
    identity_b, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf", run_id="run-b", installation_id=device_b,
    )
    assert identity_a["attemptId"] != identity_b["attemptId"]
    assert identity_a["deploymentId"] != identity_b["deploymentId"]
    # Same explicit value persists unchanged across restarts/retries.
    again, _ = attempt.resolve_installation_id(explicit="device-a", env={})
    assert again == device_a


def test_attempt_identity_binds_repo_issue_workflow_run() -> None:
    identity, error = attempt.build_attempt_identity(
        repository="o/r", issue_number=7, workflow_id="wf", run_id="run", installation_id="device-a",
    )
    assert error == ""
    assert re.fullmatch(r"att_[0-9a-f]{24}", identity["attemptId"])
    assert (identity["repository"], identity["issueNumber"]) == ("o/r", 7)
    bad, bad_error = attempt.build_attempt_identity(
        repository="o/r", issue_number=0, workflow_id="", run_id="", installation_id="device-a",
    )
    assert bad == {}
    assert bad_error


# ---------------------------------------------------------------------------
# Req 2: one versioned, bounded comment with all required fields
# ---------------------------------------------------------------------------


def test_rendered_comment_carries_all_required_fields_and_stays_bounded() -> None:
    handoff = _full_handoff()
    body, error = attempt.render_attempt_comment(handoff)
    assert error == ""
    assert f"{attempt.ATTEMPT_MARKER_PREFIX} {handoff.attempt_id} v1" in body
    metadata, extract_error = attempt.extract_attempt_metadata(body)
    assert extract_error == ""
    assert metadata is not None
    assert metadata["formatVersion"] == attempt.ATTEMPT_COMMENT_FORMAT_VERSION
    assert metadata["attemptId"] == handoff.attempt_id
    assert metadata["deploymentId"] == "device-a"
    assert (metadata["repository"], metadata["issueNumber"]) == ("MoonLadderStudios/MoonMind", 4177)
    assert metadata["predecessorAttemptId"] == handoff.predecessor_attempt_id
    assert metadata["predecessorCommentId"] == 101
    assert metadata["activity"] == attempt.ACTIVITY_ACTIVE
    assert metadata["lastReport"]
    assert "writersStopped" in metadata
    assert metadata["pendingDisposition"] == "to_recovery_needed"
    preserved = metadata["preservedWork"]
    assert preserved["prUrl"] == "https://github.com/MoonLadderStudios/MoonMind/pull/9999"
    assert preserved["prHeadSha"] == "a" * 40
    assert preserved["prBase"] == "main"
    assert metadata["outcome"] == "pending"
    assert metadata["remainingRequirements"] == ["req-1", "req-2"]
    assert metadata["verificationSummary"]
    assert metadata["nextAction"] == "continue-implementation"
    retry = metadata["retryHistory"]
    assert retry["policyLineage"] == "policy-v1"
    assert len(body) <= attempt.MAX_COMMENT_CHARS


def test_activity_vocabulary_has_defined_meanings_without_label_families() -> None:
    assert set(attempt.ACTIVITY_MEANINGS) == set(attempt.ACTIVITIES)
    assert attempt.ACTIVITIES == {"preparing", "active", "awaiting-review", "releasing", "released", "attention"}
    bad = _handoff(activity="status: claiming")
    _, error = attempt.render_attempt_comment(bad)
    assert "Unsupported activity" in error


def test_oversized_content_is_explicitly_rejected_not_silently_dropped() -> None:
    handoff = _handoff(
        last_report="r" * 5000,
        verification_summary="v" * 5000,
        remaining_requirements=tuple(f"requirement-{index}-" + "x" * 300 for index in range(20)),
    )
    body, error = attempt.render_attempt_comment(handoff)
    # Field caps truncate first (explicit "[truncated]" markers); content that
    # still exceeds the total bound is rejected explicitly, never posted whole
    # and never silently dropped.
    assert body == "" or "[truncated]" in body
    if body == "":
        assert "bound" in error


def test_single_oversized_field_truncates_and_still_renders() -> None:
    handoff = _handoff(last_report="r" * 5000)
    body, error = attempt.render_attempt_comment(handoff)
    assert error == ""
    assert len(body) <= attempt.MAX_COMMENT_CHARS
    assert "[truncated]" in body


def test_tombstone_retains_lineage_for_redaction() -> None:
    body, error = attempt.render_tombstone(
        attempt_id=_handoff().attempt_id,
        deployment_id="device-a",
        repository="o/r",
        issue_number=1,
        failed_attempt_refs=["att_" + "1" * 24],
        policy_lineage="policy-v1",
        successor_attempt_id="att_" + "2" * 24,
    )
    assert error == ""
    metadata, _ = attempt.extract_attempt_metadata(body)
    assert metadata is not None
    assert metadata["tombstone"] is True
    assert metadata["retryHistory"]["failedAttempts"] == ["att_" + "1" * 24]


# ---------------------------------------------------------------------------
# Req 3: provenance / schema / lineage validation
# ---------------------------------------------------------------------------


def test_trusted_poster_provenance_with_supported_schema() -> None:
    handoff = _handoff()
    body, _ = attempt.render_attempt_comment(handoff)
    metadata, _ = attempt.extract_attempt_metadata(body)
    assert metadata is not None
    decision = attempt.validate_attempt_handoff(
        metadata,
        repository="MoonLadderStudios/MoonMind",
        issue_number=4177,
        trusted_posters=["moonmind-bot"],
        author_login="moonmind-bot",
    )
    assert decision["allowed"] is True


@pytest.mark.parametrize("author", ["someone-else", "", "Moonmind-Bot-Impostor"])
def test_copied_marker_without_trusted_poster_is_rejected(author: str) -> None:
    handoff = _handoff()
    body, _ = attempt.render_attempt_comment(handoff)
    metadata, _ = attempt.extract_attempt_metadata(body)
    assert metadata is not None
    decision = attempt.validate_attempt_handoff(
        metadata,
        repository="MoonLadderStudios/MoonMind",
        issue_number=4177,
        trusted_posters=["moonmind-bot"],
        author_login=author,
    )
    assert decision["allowed"] is False
    assert decision["reasonCode"] == "untrusted_poster"


def test_unsupported_version_and_issue_mismatch_are_rejected() -> None:
    handoff = _handoff()
    body, _ = attempt.render_attempt_comment(handoff)
    metadata, _ = attempt.extract_attempt_metadata(body)
    assert metadata is not None
    evolved = dict(metadata, formatVersion="moonmind.github_issue_attempt.v99")
    decision = attempt.validate_attempt_handoff(
        evolved, repository="MoonLadderStudios/MoonMind", issue_number=4177,
        trusted_posters=["bot"], author_login="bot",
    )
    assert decision["reasonCode"] == "unsupported_version"
    wrong_issue = attempt.validate_attempt_handoff(
        metadata, repository="MoonLadderStudios/MoonMind", issue_number=9999,
        trusted_posters=["bot"], author_login="bot",
    )
    assert wrong_issue["reasonCode"] == "issue_identity_mismatch"


def test_invalid_preserved_references_are_rejected() -> None:
    handoff = _handoff()
    body, _ = attempt.render_attempt_comment(handoff)
    metadata, _ = attempt.extract_attempt_metadata(body)
    assert metadata is not None
    tampered = json.loads(json.dumps(metadata))
    tampered["preservedWork"] = {"prUrl": "https://github.com/other/repo/pull/1"}
    decision = attempt.validate_attempt_handoff(
        tampered, repository="MoonLadderStudios/MoonMind", issue_number=4177,
        trusted_posters=["bot"], author_login="bot",
    )
    assert decision["reasonCode"] == "invalid_reference"


def test_missing_predecessor_blocks_rather_than_starting_fresh() -> None:
    metadata = _full_handoff().to_metadata()
    outcome = attempt.check_predecessor_available(metadata, known_attempt_ids=["att_" + "9" * 24])
    assert outcome["ok"] is False
    assert outcome["reasonCode"] == "missing_predecessor"
    present = attempt.check_predecessor_available(metadata, known_attempt_ids=[metadata["predecessorAttemptId"]])
    assert present["ok"] is True


def test_malformed_marker_yields_explicit_format_error() -> None:
    metadata, error = attempt.extract_attempt_metadata("<!-- moonmind-github-attempt: broken --> no json")
    assert metadata is None
    assert error.startswith("unsupported_format")
    ordinary, ordinary_error = attempt.extract_attempt_metadata("Just a human discussion comment.")
    assert ordinary is None
    assert ordinary_error == ""


def test_stale_local_update_must_not_overwrite_newer_remote() -> None:
    assert attempt.is_stale_local_update(local_seq=3, remote_seq=5) is True
    assert attempt.is_stale_local_update(local_seq=6, remote_seq=5) is False


# ---------------------------------------------------------------------------
# Req 4: serialized per-attempt writes, duplicates, conflicts, coalescing
# ---------------------------------------------------------------------------


def _comment(comment_id: int, body: str) -> dict[str, Any]:
    return {"id": comment_id, "body": body, "user": {"login": "moonmind-bot"}}


def test_reconcile_guides_create_update_and_conflict() -> None:
    handoff = _handoff()
    body, _ = attempt.render_attempt_comment(handoff)
    assert attempt.reconcile_attempt_comments([], attempt_id=handoff.attempt_id)["action"] == "create"
    single = attempt.reconcile_attempt_comments([_comment(11, body)], attempt_id=handoff.attempt_id)
    assert (single["action"], single["commentId"]) == ("update", 11)
    duplicates = attempt.reconcile_attempt_comments(
        [_comment(11, body), _comment(12, body)], attempt_id=handoff.attempt_id
    )
    assert duplicates["action"] == "update"
    assert duplicates["reasonCode"] == "duplicate_copies_one_attempt"
    assert duplicates["commentId"] == 11
    other = _handoff(activity=attempt.ACTIVITY_ATTENTION, last_report="Different content here.")
    other_body, _ = attempt.render_attempt_comment(other)
    # Same logical attempt ID with divergent content: rebind every occurrence
    # (marker and embedded metadata) so both comments claim one attempt.
    conflicting_body = other_body.replace(other.attempt_id, handoff.attempt_id)
    conflict = attempt.reconcile_attempt_comments(
        [_comment(11, body), _comment(12, conflicting_body)], attempt_id=handoff.attempt_id
    )
    assert conflict["action"] == "attention"
    assert conflict["reasonCode"] == "conflicting_copies"


def test_progress_reports_coalesce_within_rate_limits() -> None:
    assert attempt.should_publish_progress(last_publish_ts=None, now_ts=1000.0) is True
    assert attempt.should_publish_progress(last_publish_ts=1000.0, now_ts=1100.0) is False
    assert attempt.should_publish_progress(last_publish_ts=1000.0, now_ts=1400.0) is True
    assert attempt.should_publish_progress(last_publish_ts=1000.0, now_ts=1100.0, activity_changed=True) is True
    assert attempt.should_publish_progress(last_publish_ts=1000.0, now_ts=1100.0, force=True) is True


class _AttemptFakeService:
    """Fake trusted GitHub boundary for the attempt-comment path."""

    def __init__(self, comments: list[dict[str, Any]] | None = None) -> None:
        self.comments = list(comments or [])
        self.next_id = 1000
        self.fail_list: str | None = None
        self.fail_create: str | None = None
        self.fail_update: str | None = None
        self.creates = 0
        self.updates: list[int] = []

    async def list_issue_comments(self, *, repo: str, issue_number: int, github_token: str | None = None):
        if self.fail_list == "unknown":
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "lost response"}
        if self.fail_list == "denied":
            return {"ok": False, "reasonCode": "denied", "summary": "forbidden"}
        return {"ok": True, "reasonCode": "listed", "summary": "listed", "comments": list(self.comments)}

    async def create_issue_comment(self, *, repo: str, issue_number: int, body: str, github_token: str | None = None):
        self.creates += 1
        if self.fail_create == "unknown":
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "create response lost"}
        if self.fail_create == "denied":
            return {"ok": False, "reasonCode": "denied", "summary": "forbidden"}
        self.next_id += 1
        self.comments.append({"id": self.next_id, "body": body})
        return {"ok": True, "reasonCode": "created", "summary": "created", "commentId": self.next_id}

    async def update_issue_comment(self, *, repo: str, comment_id: int, body: str, github_token: str | None = None):
        self.updates.append(comment_id)
        if self.fail_update == "unknown":
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": "update response lost"}
        if self.fail_update == "denied":
            return {"ok": False, "reasonCode": "denied", "summary": "forbidden"}
        for comment in self.comments:
            if comment["id"] == comment_id:
                comment["body"] = body
        return {"ok": True, "reasonCode": "updated", "summary": "updated", "commentId": comment_id}


@pytest.mark.asyncio
async def test_publish_path_creates_then_updates_own_comment_only() -> None:
    service = _AttemptFakeService()
    handoff = _handoff()
    created = await attempt.publish_attempt_handoff(
        service=service, repository="MoonLadderStudios/MoonMind", issue_number=4177, handoff=handoff, force=True,
    )
    assert created["ok"] is True
    assert created["reasonCode"] == "created"
    # A retry after a lost create response reconciles by marker: no duplicate.
    updated = await attempt.publish_attempt_handoff(
        service=service, repository="MoonLadderStudios/MoonMind", issue_number=4177, handoff=handoff, force=True,
    )
    assert updated["ok"] is True
    assert updated["reasonCode"] == "updated"
    assert updated["commentId"] == created["commentId"]
    assert service.creates == 1


@pytest.mark.asyncio
async def test_publish_path_never_overwrites_another_attempt() -> None:
    other = _handoff()
    other_body, _ = attempt.render_attempt_comment(other)
    service = _AttemptFakeService(comments=[_comment(55, other_body)])
    mine = _handoff()
    result = await attempt.publish_attempt_handoff(
        service=service, repository="MoonLadderStudios/MoonMind", issue_number=4177, handoff=mine, force=True,
    )
    assert result["ok"] is True
    assert result["reasonCode"] == "created"
    assert service.updates == []
    assert any(comment["id"] == 55 for comment in service.comments)


@pytest.mark.asyncio
async def test_publish_path_reports_lost_responses_as_unknown() -> None:
    handoff = _handoff()
    body, _ = attempt.render_attempt_comment(handoff)
    service = _AttemptFakeService(comments=[_comment(77, body)])
    service.fail_update = "unknown"
    result = await attempt.publish_attempt_handoff(
        service=service, repository="MoonLadderStudios/MoonMind", issue_number=4177, handoff=handoff, force=True,
    )
    assert result["ok"] is False
    assert result["reasonCode"] == "outcome_unknown"
    assert "marker" in result["summary"]
    fresh = _AttemptFakeService()
    fresh.fail_create = "unknown"
    created = await attempt.publish_attempt_handoff(
        service=fresh, repository="MoonLadderStudios/MoonMind", issue_number=4177, handoff=handoff, force=True,
    )
    assert created["reasonCode"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_publish_path_conflicting_copies_require_attention() -> None:
    handoff = _handoff()
    body, _ = attempt.render_attempt_comment(handoff)
    other = _handoff(activity=attempt.ACTIVITY_ATTENTION, last_report="Divergent copy.")
    other_body, _ = attempt.render_attempt_comment(other)
    conflicting = other_body.replace(other.attempt_id, handoff.attempt_id)
    service = _AttemptFakeService(comments=[_comment(11, body), _comment(12, conflicting)])
    result = await attempt.publish_attempt_handoff(
        service=service, repository="MoonLadderStudios/MoonMind", issue_number=4177, handoff=handoff, force=True,
    )
    assert result["ok"] is False
    assert result["reasonCode"] == "conflicting_copies"
    assert service.creates == 0
    assert service.updates == []


@pytest.mark.asyncio
async def test_publish_path_coalesces_routine_progress() -> None:
    service = _AttemptFakeService()
    handoff = _handoff()
    result = await attempt.publish_attempt_handoff(
        service=service,
        repository="MoonLadderStudios/MoonMind",
        issue_number=4177,
        handoff=handoff,
        last_publish_ts=1000.0,
        now_ts=1100.0,
    )
    assert result["ok"] is True
    assert result["reasonCode"] == "coalesced"
    assert service.creates == 0


@pytest.mark.asyncio
async def test_publish_path_refuses_released_attempt_resume() -> None:
    service = _AttemptFakeService()
    handoff = _handoff(activity=attempt.ACTIVITY_RELEASED)
    result = await attempt.publish_attempt_handoff(
        service=service, repository="MoonLadderStudios/MoonMind", issue_number=4177, handoff=handoff, force=True,
    )
    assert result["ok"] is False
    assert result["reasonCode"] == "terminal_released_no_resume"
    assert service.creates == 0


# ---------------------------------------------------------------------------
# Req 5: portable retry history (this issue owns the calculation)
# ---------------------------------------------------------------------------


def _chain_entry(attempt_id: str, **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "attemptId": attempt_id,
        "outcome": "failed",
        "policyLineage": "policy-v1",
        "workflowId": "wf-old",
        "deploymentId": "device-a",
    }
    entry.update(overrides)
    return entry


def test_retry_history_survives_workflow_device_and_label_changes() -> None:
    first, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf-1", run_id="r1", installation_id="device-a",
    )
    second, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf-2", run_id="r2", installation_id="device-b",
    )
    state = attempt.derive_retry_state(
        [
            _chain_entry(first["attemptId"], workflowId="wf-1", deploymentId="device-a"),
            _chain_entry(second["attemptId"], predecessorAttemptId=first["attemptId"],
                         workflowId="wf-2", deploymentId="device-b", noProgress=True),
        ],
        policy={"maxAttempts": 3, "lineageRef": "policy-v1"},
    )
    assert state["blocked"] is False
    assert state["attemptsObserved"] == 2
    assert state["failuresRetained"] == 2
    assert state["noProgressRetained"] == 1
    assert state["remainingAllowance"] == 1


def test_internal_retries_do_not_create_issue_attempts() -> None:
    only, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf", run_id="r", installation_id="device-a",
    )
    state = attempt.derive_retry_state(
        [_chain_entry(only["attemptId"], outcome="pending", internalRetries=9)],
        policy={"maxAttempts": 3, "lineageRef": "policy-v1"},
    )
    assert state["attemptsObserved"] == 1
    assert state["remainingAllowance"] == 3


def test_missing_or_incompatible_lineage_blocks_recovery() -> None:
    only, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf", run_id="r", installation_id="device-a",
    )
    missing = attempt.derive_retry_state(
        [_chain_entry(only["attemptId"], policyLineage="")],
        policy={"maxAttempts": 3, "lineageRef": "policy-v1"},
    )
    assert missing["reasonCode"] == "missing_policy_lineage"
    assert missing["blocked"] is True
    incompatible = attempt.derive_retry_state(
        [_chain_entry(only["attemptId"], policyLineage="policy-v2")],
        policy={"maxAttempts": 3, "lineageRef": "policy-v1"},
    )
    assert incompatible["reasonCode"] == "incompatible_policy_lineage"


def test_authorized_reset_requires_audited_decision() -> None:
    first, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf", run_id="r1", installation_id="device-a",
    )
    second, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf", run_id="r2", installation_id="device-a",
    )
    exhausted = attempt.derive_retry_state(
        [_chain_entry(first["attemptId"]), _chain_entry(second["attemptId"], predecessorAttemptId=first["attemptId"])],
        policy={"maxAttempts": 2, "lineageRef": "policy-v1"},
    )
    assert exhausted["reasonCode"] == "retry_budget_exhausted"
    reset_entry = _chain_entry(
        second["attemptId"], predecessorAttemptId=first["attemptId"],
        retryHistory={"authorizedReset": {"resetBy": "operator", "resetReason": "root cause fixed", "resetAt": "2026-09-10T18:00:00Z"}},
    )
    reset_state = attempt.derive_retry_state(
        [_chain_entry(first["attemptId"]), reset_entry],
        policy={"maxAttempts": 2, "lineageRef": "policy-v1"},
    )
    assert reset_state["blocked"] is False
    # An unaudited reset (missing reason) does not restart the allowance.
    unaudited = _chain_entry(
        second["attemptId"], predecessorAttemptId=first["attemptId"],
        retryHistory={"authorizedReset": {"resetBy": "operator"}},
    )
    still_blocked = attempt.derive_retry_state(
        [_chain_entry(first["attemptId"]), unaudited],
        policy={"maxAttempts": 2, "lineageRef": "policy-v1"},
    )
    assert still_blocked["blocked"] is True


def test_operator_hold_and_cancellation_survive_new_ids_and_devices() -> None:
    first, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf-1", run_id="r1", installation_id="device-a",
    )
    second, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf-2", run_id="r2", installation_id="device-b",
    )
    state = attempt.derive_retry_state(
        [
            _chain_entry(first["attemptId"], outcome="cancelled", operatorHold=True,
                         retryHistory={"operatorHold": True, "operatorHoldReason": "operator paused automation",
                                       "policyLineage": "policy-v1"}),
            _chain_entry(second["attemptId"], predecessorAttemptId=first["attemptId"], outcome="pending"),
        ],
        policy={"maxAttempts": 5, "lineageRef": "policy-v1"},
    )
    assert state["blocked"] is True
    assert state["reasonCode"] == "operator_hold"


def test_simultaneous_races_do_not_claim_an_exact_counter() -> None:
    first, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf", run_id="r1", installation_id="device-a",
    )
    fork_a, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf", run_id="r2", installation_id="device-b",
    )
    fork_b, _ = attempt.build_attempt_identity(
        repository="o/r", issue_number=1, workflow_id="wf", run_id="r3", installation_id="device-c",
    )
    state = attempt.derive_retry_state(
        [
            _chain_entry(first["attemptId"]),
            _chain_entry(fork_a["attemptId"], predecessorAttemptId=first["attemptId"]),
            _chain_entry(fork_b["attemptId"], predecessorAttemptId=first["attemptId"]),
        ],
        policy={"maxAttempts": 5, "lineageRef": "policy-v1"},
    )
    assert state["approximate"] is True
    assert state["remainingAllowance"] is None


def test_device_b_reconstructs_remaining_work_from_github_alone() -> None:
    first_handoff = _handoff(activity=attempt.ACTIVITY_ACTIVE, remaining_requirements=("req-1", "req-2"))
    body_a, _ = attempt.render_attempt_comment(first_handoff)
    metadata_a, _ = attempt.extract_attempt_metadata(body_a)
    assert metadata_a is not None
    validated_a = attempt.validate_attempt_handoff(
        metadata_a, repository="MoonLadderStudios/MoonMind", issue_number=4177,
        trusted_posters=["shared-bot"], author_login="shared-bot",
    )
    assert validated_a["allowed"] is True
    second = _handoff(
        activity=attempt.ACTIVITY_ACTIVE,
        predecessor_attempt_id=first_handoff.attempt_id,
        remaining_requirements=("req-2",),
        met_requirements=("req-1",),
    )
    body_b, _ = attempt.render_attempt_comment(second)
    metadata_b, _ = attempt.extract_attempt_metadata(body_b)
    assert metadata_b is not None
    # Device B uses only GitHub-visible evidence: no private logs involved.
    chain = attempt.collect_linked_chain(
        [validated_a["metadata"], metadata_b], head_attempt_id=second.attempt_id,
    )
    assert [entry["attemptId"] for entry in chain] == [first_handoff.attempt_id, second.attempt_id]
    remaining = chain[-1]["remainingRequirements"]
    assert remaining == ["req-2"]
    retry = attempt.derive_retry_state(
        [{"attemptId": first_handoff.attempt_id, "outcome": "failed", "policyLineage": "policy-v1"}],
        policy={"maxAttempts": 3, "lineageRef": "policy-v1"},
    )
    assert retry["remainingAllowance"] == 2


def test_lineage_gap_is_explicit_not_a_clean_slate() -> None:
    chain = attempt.collect_linked_chain(
        [{"attemptId": "att_" + "3" * 24, "predecessorAttemptId": "att_" + "4" * 24}],
        head_attempt_id="att_" + "3" * 24,
    )
    assert chain[-1].get("lineageGap") is True


# ---------------------------------------------------------------------------
# Req 6: proposed release vs completed release
# ---------------------------------------------------------------------------


def test_release_requires_all_four_confirmations() -> None:
    handoff = _handoff(activity=attempt.ACTIVITY_RELEASING, pending_disposition="to_available")
    proposed = attempt.evaluate_release(
        handoff, writers_stopped=True, mutations_settled=True,
        preservation_verified_or_no_work=False, label_outcome_observed=True,
    )
    assert proposed["released"] is False
    assert proposed["reasonCode"] == "release_proposed"
    assert "preservation_verified_or_no_work" in proposed["missing"]
    released = attempt.evaluate_release(
        handoff, writers_stopped=True, mutations_settled=True,
        preservation_verified_or_no_work=True, label_outcome_observed=True,
    )
    assert released["released"] is True
    assert attempt.is_terminal_released(_handoff(activity=attempt.ACTIVITY_RELEASED)) is True
    assert attempt.is_terminal_released(handoff) is False


# ---------------------------------------------------------------------------
# Req 7: hygiene + redaction on comment and structured-error paths
# ---------------------------------------------------------------------------


def test_secret_shaped_content_never_reaches_postable_comments() -> None:
    handoff = _handoff(last_report="token leaked: ghp_" + "A" * 30)
    body, error = attempt.render_attempt_comment(handoff)
    assert body == ""
    assert "outbound scan" in error
    assert "ghp_" not in error


def test_private_key_material_is_blocked_from_comments() -> None:
    key_block = "-----BEGIN RSA PRIVATE KEY-----\nMIIB fake\n-----END RSA PRIVATE KEY-----"
    handoff = _handoff(verification_summary=key_block)
    body, error = attempt.render_attempt_comment(handoff)
    assert body == ""
    assert "outbound scan" in error
    assert "PRIVATE KEY" not in error


def test_structured_error_path_redacts_without_echoing_secrets() -> None:
    raw = "push failed with password hunter-super-secret-value and token=abc123"
    safe, error = attempt.redacted_error_detail(raw)
    assert error == ""
    assert "hunter-super-secret-value" not in safe
    empty, _ = attempt.redacted_error_detail("")
    assert empty == "No detail available."
    key_error, _ = attempt.redacted_error_detail("ghp_" + "B" * 30)
    assert "ghp_" not in key_error


def test_local_workflow_links_are_optional_diagnostics() -> None:
    handoff = _handoff(local_workflow_ref="temporal://namespace/wf/run")
    body, error = attempt.render_attempt_comment(handoff)
    assert error == ""
    metadata, _ = attempt.extract_attempt_metadata(body)
    assert metadata is not None
    assert metadata.get("localWorkflowRef") == "temporal://namespace/wf/run"
    plain = _handoff()
    plain_body, _ = attempt.render_attempt_comment(plain)
    plain_metadata, _ = attempt.extract_attempt_metadata(plain_body)
    assert plain_metadata is not None
    assert "localWorkflowRef" not in plain_metadata


# ---------------------------------------------------------------------------
# GitHubService adapter: issue-comment list/create/update
# ---------------------------------------------------------------------------


def _service_responses(monkeypatch: pytest.MonkeyPatch, handler: Any) -> Any:
    from moonmind.workflows.adapters import github_service as service_module

    calls: list[tuple[str, str, Any]] = []

    class _FakeResponse:
        def __init__(self, payload: Any, status_code: int = 200) -> None:
            self._payload = payload
            self.status_code = status_code
            self.headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = service_module.httpx.Request("GET", "https://api.github.com")
                response = service_module.httpx.Response(self.status_code, request=request)
                raise service_module.httpx.HTTPStatusError("error", request=request, response=response)

        def json(self) -> Any:
            return self._payload

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            calls.append(("GET", url, kwargs))
            return handler("GET", url, kwargs)

        async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            calls.append(("POST", url, kwargs))
            return handler("POST", url, kwargs)

        async def patch(self, url: str, **kwargs: Any) -> _FakeResponse:
            calls.append(("PATCH", url, kwargs))
            return handler("PATCH", url, kwargs)

    monkeypatch.setattr(service_module.httpx, "AsyncClient", _FakeClient)
    return calls


@pytest.mark.asyncio
async def test_service_comment_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    from moonmind.workflows.adapters.github_service import GitHubService

    stored: list[dict[str, Any]] = [{"id": 1, "body": "hello"}]

    def handler(method: str, url: str, kwargs: Any) -> Any:
        from moonmind.workflows.adapters import github_service as service_module

        class _R:
            status_code = 200
            headers: dict[str, str] = {}

            def raise_for_status(self) -> None:
                return None

            def json(self) -> Any:
                if method == "GET":
                    return list(stored)
                if method == "POST":
                    comment = {"id": 2, "body": kwargs["json"]["body"]}
                    stored.append(comment)
                    return comment
                return {"id": 1, "body": kwargs["json"]["body"]}

        return _R()

    _service_responses(monkeypatch, handler)
    service = GitHubService()

    async def fake_token(explicit_token: str | None = None, *, repo: str | None = None):
        return "ghs-test", None

    monkeypatch.setattr(GitHubService, "resolve_github_token", staticmethod(fake_token))
    listed = await service.list_issue_comments(repo="o/r", issue_number=1)
    assert listed["ok"] is True
    assert len(listed["comments"]) == 1
    created = await service.create_issue_comment(repo="o/r", issue_number=1, body="attempt body")
    assert created["ok"] is True
    assert created["commentId"] == 2
    updated = await service.update_issue_comment(repo="o/r", comment_id=1, body="new body")
    assert updated["ok"] is True


@pytest.mark.asyncio
async def test_service_comment_transport_loss_is_outcome_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    from moonmind.workflows.adapters import github_service as service_module
    from moonmind.workflows.adapters.github_service import GitHubService

    def handler(method: str, url: str, kwargs: Any) -> Any:
        raise service_module.httpx.ConnectError("down")

    _service_responses(monkeypatch, handler)
    service = GitHubService()

    async def fake_token(explicit_token: str | None = None, *, repo: str | None = None):
        return "ghs-test", None

    monkeypatch.setattr(GitHubService, "resolve_github_token", staticmethod(fake_token))
    assert (await service.list_issue_comments(repo="o/r", issue_number=1))["reasonCode"] == "outcome_unknown"
    assert (await service.create_issue_comment(repo="o/r", issue_number=1, body="b"))["reasonCode"] == "outcome_unknown"
    assert (await service.update_issue_comment(repo="o/r", comment_id=1, body="b"))["reasonCode"] == "outcome_unknown"
