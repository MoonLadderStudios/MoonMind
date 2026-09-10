"""Portable per-attempt handoffs and cross-deployment retry history.

Covers issue MoonLadderStudios/MoonMind#4177 acceptance criteria against the
real Activity/adapter path (``update_github_issue_status``) and the shared
policy entrypoint (``github_issue_attempt``): distinguishable installation
identities, versioned bounded comments with all required fields, safe
provenance validation, per-attempt serialization/reconciliation/rate limits,
GitHub-only reconstruction of remaining work and retry restrictions, bounded
internal retries with surviving hold/cancellation evidence, proposed vs
released distinction, and redaction of all outbound comment/error paths.
"""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.workflows.temporal import github_issue_attempt as attempt
from moonmind.workflows.temporal import story_output_tools as story_tools
from moonmind.workflows.temporal.github_issue_attempt import (
    AttemptHandoff,
    ParsedAttemptComment,
    attempt_binding_key,
    build_attempt_id,
    build_safe_comment,
    collect_attempt_comments,
    compute_retry_state,
    decide_release,
    is_terminal_released,
    normalize_attempt_id,
    parse_attempt_comment,
    reconcile_uncertain_create,
    reconstruct_handoff,
    redact_comment_text,
    redact_structured_error,
    render_attempt_comment,
    resolve_installation_id,
    scan_comment_text,
    select_own_comment,
    should_coalesce_progress,
    validate_attempt_comment,
)
from moonmind.workflows.temporal.story_output_tools import update_github_issue_status

REPO = "MoonLadderStudios/MoonMind"
ISSUE = 4177
ATTEMPT_A = "a" * 32
ATTEMPT_B = "b" * 32
DEPLOY_A = "device-a"
DEPLOY_B = "device-b"


def _handoff(**overrides: Any) -> AttemptHandoff:
    payload: dict[str, Any] = {
        "repository": REPO,
        "issue_number": ISSUE,
        "attempt_id": ATTEMPT_A,
        "deployment_id": DEPLOY_A,
        "workflow_id": "wf-1",
        "run_id": "run-1",
        "activity": attempt.ATTEMPT_ACTIVITY_ACTIVE,
        "last_report": "Implementing the handoff.",
        "pull_request_url": f"https://github.com/{REPO}/pull/9",
        "head_sha": "abc123",
        "head_branch": "feat/x",
        "base_branch": "main",
        "outcome": "in_progress",
        "unmet_requirements": ("req-2",),
        "next_action": "continue_implementation",
        "retry_history": ("attempt 0000 failed",),
    }
    payload.update(overrides)
    return AttemptHandoff(**payload)


def _comment_dict(
    body: str,
    *,
    comment_id: str = "11",
    author: str = "moonmind-bot",
) -> dict[str, Any]:
    return {
        "id": comment_id,
        "body": body,
        "user": {"login": author},
        "created_at": "2026-09-10T00:00:00Z",
        "updated_at": "2026-09-10T00:01:00Z",
    }


def _parsed(**overrides: Any) -> ParsedAttemptComment:
    body = render_attempt_comment(_handoff())
    item = parse_attempt_comment(_comment_dict(body))
    assert item is not None
    if not overrides:
        return item
    merged = dict(item.metadata)
    merged.update(overrides.pop("metadata", {}))
    return ParsedAttemptComment(
        comment_id=overrides.get("comment_id", item.comment_id),
        author=overrides.get("author", item.author),
        created_at=overrides.get("created_at", item.created_at),
        updated_at=overrides.get("updated_at", item.updated_at),
        attempt_id=overrides.get("attempt_id", item.attempt_id),
        deployment_id=overrides.get("deployment_id", item.deployment_id),
        version=overrides.get("version", item.version),
        metadata=overrides.get("metadata_full", merged),
        raw_body=overrides.get("raw_body", item.raw_body),
        has_marker=overrides.get("has_marker", item.has_marker),
        has_fence=overrides.get("has_fence", item.has_fence),
    )


# ---------------------------------------------------------------------------
# Req 1: stable installation identity + bound attempt ID
# ---------------------------------------------------------------------------


def test_installation_id_differs_across_deployments(tmp_path) -> None:
    first = resolve_installation_id(
        environ={"MOONMIND_INSTALLATION_ID": "device-a"},
        path=tmp_path / "a",
        persist=False,
    )
    second = resolve_installation_id(
        environ={"MOONMIND_INSTALLATION_ID": "device-b"},
        path=tmp_path / "b",
        persist=False,
    )
    assert first == "device-a"
    assert second == "device-b"
    assert first != second


def test_installation_id_persists_through_restart(tmp_path) -> None:
    path = tmp_path / "installation"
    first = resolve_installation_id(environ={}, path=path, persist=True)
    second = resolve_installation_id(environ={}, path=path, persist=True)
    assert first and first == second


def test_attempt_id_bound_and_unique() -> None:
    first = build_attempt_id(
        repository=REPO, issue_number=ISSUE, workflow_id="wf",
        run_id="run", deployment_id=DEPLOY_A,
    )
    second = build_attempt_id(
        repository=REPO, issue_number=ISSUE, workflow_id="wf",
        run_id="run", deployment_id=DEPLOY_A,
    )
    # Stable scope reuses the same marker across Activity retries so the
    # retry reconciles instead of minting a duplicate logical attempt.
    assert normalize_attempt_id(first) == first
    assert first == second
    assert attempt_binding_key(
        repository=REPO, issue_number=ISSUE, attempt_id=first
    ).startswith(f"{REPO.lower()}#{ISSUE}:")
    assert normalize_attempt_id("not-an-id") == ""
    # Callers without any stable scope still get fresh unique IDs.
    ephemeral_first = build_attempt_id(repository=REPO, issue_number=ISSUE)
    ephemeral_second = build_attempt_id(repository=REPO, issue_number=ISSUE)
    assert ephemeral_first != ephemeral_second


# ---------------------------------------------------------------------------
# Req 2: one versioned, bounded comment representation
# ---------------------------------------------------------------------------


def test_comment_carries_all_required_fields_and_bound() -> None:
    body = render_attempt_comment(
        _handoff(
            predecessor_attempt_id=ATTEMPT_B,
            predecessor_comment_id="7",
            writers_stopped=True,
            publication_outcome="pr_updated",
            pending_disposition="to_code_review",
            verification_summary="unit suite green",
            retry_allowance="2 remaining",
            cooldown_until="2026-09-10T01:00:00+00:00",
            operator_hold=False,
            reset_record="",
            policy_ref="retry.v1",
            diagnostics_ref="art_diag",
        )
    )
    assert len(body) <= attempt.MAX_ATTEMPT_COMMENT_CHARS
    assert "started implementation" in body
    item = parse_attempt_comment(_comment_dict(body))
    assert item is not None
    assert item.version == attempt.ATTEMPT_COMMENT_VERSION
    metadata = item.metadata
    for key in (
        "attemptId", "deploymentId", "workflowId", "runId",
        "predecessorAttemptId", "predecessorCommentId", "activity",
        "lastReport", "writersStopped", "publicationOutcome",
        "pendingDisposition", "pullRequestUrl", "headSha", "headBranch",
        "baseBranch", "outcome", "unmetRequirements", "verificationSummary",
        "nextAction", "retryHistory", "retryAllowance", "cooldownUntil",
        "operatorHold",
    ):
        assert key in metadata, key
    assert set(attempt.ATTEMPT_ACTIVITIES) == {
        "preparing", "active", "awaiting-review",
        "releasing", "released", "attention",
    }


def test_comment_bounded_under_oversized_input() -> None:
    body = render_attempt_comment(
        _handoff(last_report="x" * 60000, verification_summary="y" * 60000)
    )
    assert len(body) <= attempt.MAX_ATTEMPT_COMMENT_CHARS


def test_non_attempt_prose_is_not_parsed() -> None:
    assert parse_attempt_comment(_comment_dict("just a human note")) is None
    assert collect_attempt_comments([_comment_dict("plain"), {"id": 1}]) == []


# ---------------------------------------------------------------------------
# Req 3: provenance, schema, and lineage validation
# ---------------------------------------------------------------------------


def test_valid_comment_is_usable() -> None:
    item = _parsed()
    decision = validate_attempt_comment(
        item, repository=REPO, issue_number=ISSUE,
        trusted_posters=["moonmind-bot"],
    )
    assert decision.usable and decision.reason_code == "usable"


@pytest.mark.parametrize(
    ("mutate", "reason_code"),
    [
        ("version", "unsupported_version"),
        ("issue", "issue_mismatch"),
        ("format", "unsupported_format"),
        ("predecessor", "missing_predecessor"),
        ("pr", "invalid_reference"),
        ("activity", "unsupported_activity"),
        ("metadata", "missing_metadata"),
        ("attempt", "missing_attempt_id"),
    ],
)
def test_invalid_comments_rejected_without_fresh_start(
    mutate: str, reason_code: str
) -> None:
    item = _parsed()
    metadata = dict(item.metadata)
    version = item.version
    attempt_id = item.attempt_id
    has_fence = item.has_fence
    if mutate == "version":
        version = 99
    elif mutate == "issue":
        metadata["issueNumber"] = 9999
    elif mutate == "format":
        metadata["format"] = "other"
    elif mutate == "predecessor":
        metadata["predecessorAttemptId"] = "c" * 32
    elif mutate == "pr":
        metadata["pullRequestUrl"] = f"https://github.com/{REPO}/issues/1"
    elif mutate == "activity":
        metadata["activity"] = "locked"
    elif mutate == "metadata":
        metadata = {}
        has_fence = False
    elif mutate == "attempt":
        attempt_id = ""
        metadata["attemptId"] = ""
    candidate = ParsedAttemptComment(
        comment_id=item.comment_id, author=item.author,
        created_at=item.created_at, updated_at=item.updated_at,
        attempt_id=attempt_id, deployment_id=item.deployment_id,
        version=version, metadata=metadata, raw_body=item.raw_body,
        has_marker=True, has_fence=has_fence,
    )
    decision = validate_attempt_comment(
        candidate, repository=REPO, issue_number=ISSUE,
        trusted_posters=["moonmind-bot"], known_attempt_ids=[ATTEMPT_A],
    )
    assert not decision.usable
    assert decision.reason_code == reason_code


def test_spoofed_marker_without_trusted_poster_rejected() -> None:
    item = _parsed()
    decision = validate_attempt_comment(
        item, repository=REPO, issue_number=ISSUE,
        trusted_posters=["moonmind-bot"],
    )
    assert decision.usable
    spoofed = ParsedAttemptComment(
        comment_id="99", author="adversary", created_at=item.created_at,
        updated_at=item.updated_at, attempt_id=item.attempt_id,
        deployment_id=item.deployment_id, version=item.version,
        metadata=item.metadata, raw_body=item.raw_body,
        has_marker=True, has_fence=True,
    )
    denied = validate_attempt_comment(
        spoofed, repository=REPO, issue_number=ISSUE,
        trusted_posters=["moonmind-bot"],
    )
    assert denied.reason_code == "untrusted_poster"
    assert not denied.usable


# ---------------------------------------------------------------------------
# Req 4: serialization, reconciliation, coalescing
# ---------------------------------------------------------------------------


def test_select_own_comment_never_takes_another_attempt() -> None:
    own = _parsed()
    other = _parsed(attempt_id=ATTEMPT_B, comment_id="12")
    plan = select_own_comment([own, other], attempt_id=ATTEMPT_A)
    assert plan.action == "update"
    assert plan.comment_id == own.comment_id
    assert select_own_comment([other], attempt_id=ATTEMPT_A).action == "create"


def test_conflicting_same_id_copies_require_attention() -> None:
    first = _parsed()
    conflict_metadata = dict(first.metadata)
    conflict_metadata["lastReport"] = "divergent payload"
    second = ParsedAttemptComment(
        comment_id="13", author=first.author, created_at="2026-09-10T00:02:00Z",
        updated_at="2026-09-10T00:03:00Z", attempt_id=first.attempt_id,
        deployment_id=first.deployment_id, version=first.version,
        metadata=conflict_metadata, raw_body="conflict",
        has_marker=True, has_fence=True,
    )
    plan = select_own_comment([first, second], attempt_id=ATTEMPT_A)
    assert plan.action == "attention"
    assert plan.reason_code == "conflicting_copies"


def test_lost_create_reconciles_by_marker_before_retry() -> None:
    item = _parsed()
    adopted = reconcile_uncertain_create(
        [item], attempt_id=ATTEMPT_A, create_outcome_unknown=True
    )
    assert adopted.action == "adopt"
    assert adopted.comment_id == item.comment_id
    blocked = reconcile_uncertain_create(
        [], attempt_id=ATTEMPT_A, create_outcome_unknown=True
    )
    assert blocked.action == "blocked"


def test_progress_coalesces_within_rate_limit() -> None:
    coalesce, _ = should_coalesce_progress(
        last_update_at="2026-09-10T00:00:00+00:00",
        now="2026-09-10T00:00:30+00:00",
    )
    assert coalesce is True
    publish, _ = should_coalesce_progress(
        last_update_at="2026-09-10T00:00:00+00:00",
        now="2026-09-10T00:05:00+00:00",
    )
    assert publish is False
    always, _ = should_coalesce_progress(
        last_update_at="2026-09-10T00:00:00+00:00",
        now="2026-09-10T00:00:01+00:00",
        activity_changed=True,
    )
    assert always is False


# ---------------------------------------------------------------------------
# Req 5: portable retry history
# ---------------------------------------------------------------------------


def test_retry_allowance_derives_from_linked_history() -> None:
    failed_metadata = dict(_parsed().metadata)
    failed_metadata["outcome"] = "failed"
    failed = ParsedAttemptComment(
        comment_id="11", author="moonmind-bot", created_at="a", updated_at="a",
        attempt_id=ATTEMPT_A, deployment_id=DEPLOY_A, version=1,
        metadata=failed_metadata, raw_body="x", has_marker=True, has_fence=True,
    )
    state = compute_retry_state([failed], max_attempts=3)
    assert state.failures == 1
    assert state.allowance_remaining == 2
    assert state.blocked is False


def test_hold_and_cancellation_survive_new_ids_and_devices() -> None:
    first = _parsed()
    held_metadata = dict(_parsed(attempt_id=ATTEMPT_B).metadata)
    held_metadata["operatorHold"] = True
    held = ParsedAttemptComment(
        comment_id="12", author="moonmind-bot", created_at="b", updated_at="b",
        attempt_id=ATTEMPT_B, deployment_id=DEPLOY_B, version=1,
        metadata=held_metadata, raw_body="x", has_marker=True, has_fence=True,
    )
    state = compute_retry_state([first, held], max_attempts=3)
    assert state.blocked is True
    assert state.reason_code == "operator_hold"


def test_missing_and_incompatible_lineage_block_recovery() -> None:
    missing = compute_retry_state([])
    assert missing.blocked is True
    assert missing.reason_code == "missing_lineage"
    first_metadata = dict(_parsed().metadata)
    first_metadata["policyRef"] = "retry.v1"
    second_metadata = dict(_parsed(attempt_id=ATTEMPT_B).metadata)
    second_metadata["policyRef"] = "retry.v2"
    first = ParsedAttemptComment(
        comment_id="11", author="b", created_at="a", updated_at="a",
        attempt_id=ATTEMPT_A, deployment_id=DEPLOY_A, version=1,
        metadata=first_metadata, raw_body="x", has_marker=True, has_fence=True,
    )
    second = ParsedAttemptComment(
        comment_id="12", author="b", created_at="b", updated_at="b",
        attempt_id=ATTEMPT_B, deployment_id=DEPLOY_B, version=1,
        metadata=second_metadata, raw_body="x", has_marker=True, has_fence=True,
    )
    conflicted = compute_retry_state([first, second])
    assert conflicted.blocked is True
    assert conflicted.reason_code == "incompatible_policy_lineage"


def test_device_b_reconstructs_from_github_alone() -> None:
    item = _parsed()
    reconstruction = reconstruct_handoff([item], repository=REPO, issue_number=ISSUE)
    assert reconstruction["reconstructable"] is True
    assert reconstruction["latestAttemptId"] == ATTEMPT_A
    assert reconstruction["unmetRequirements"] == ["req-2"]
    assert reconstruction["pullRequestUrl"].endswith("/pull/9")
    assert reconstruction["retry"]["reasonCode"] == "retry_allowed"
    assert reconstruct_handoff([], repository=REPO, issue_number=ISSUE)[
        "reconstructable"
    ] is False


# ---------------------------------------------------------------------------
# Req 6: proposed release vs completed release
# ---------------------------------------------------------------------------


def test_released_requires_full_conjunction() -> None:
    assert decide_release(
        {}, writers_stopped=True, mutations_settled=True,
        preservation_verified=True, label_outcome_observed=True,
    ).released is True
    for kwargs in (
        {"writers_stopped": False, "mutations_settled": True,
         "preservation_verified": True, "label_outcome_observed": True},
        {"writers_stopped": True, "mutations_settled": False,
         "preservation_verified": True, "label_outcome_observed": True},
        {"writers_stopped": True, "mutations_settled": True,
         "preservation_verified": False, "label_outcome_observed": True},
        {"writers_stopped": True, "mutations_settled": True,
         "preservation_verified": True, "label_outcome_observed": False},
    ):
        assert decide_release({}, **kwargs).released is False
    # Explicit no-work evidence substitutes for preservation verification.
    assert decide_release(
        {}, writers_stopped=True, mutations_settled=True,
        preservation_verified=False, no_work_evidence=True,
        label_outcome_observed=True,
    ).released is True


def test_terminal_released_attempts_do_not_resume() -> None:
    released = dict(_parsed().metadata)
    released.update({"activity": "released", "writersStopped": True})
    assert is_terminal_released(released) is True
    proposed = dict(released)
    proposed["writersStopped"] = False
    assert is_terminal_released(proposed) is False


# ---------------------------------------------------------------------------
# Req 7: redaction on all outbound comment and structured-error paths
# ---------------------------------------------------------------------------


def test_comment_redaction_strips_secrets() -> None:
    secret = "ghp_" + "x" * 36
    body, scan = build_safe_comment(_handoff(last_report=f"token={secret}"))
    assert secret not in body
    assert scan["allowed"] is True
    assert scan["policyRef"] == "moonmind.security.outbound_scan.v1"


def test_outbound_scan_blocks_secret_bearing_comments() -> None:
    key = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    scan = scan_comment_text(f"report {key}")
    assert scan["allowed"] is False
    assert scan["diagnostics"]


def test_structured_errors_are_redacted_and_scanned() -> None:
    redacted = redact_structured_error(
        {"message": "failed", "token": "ghp_" + "y" * 36}
    )
    assert "ghp_" not in str(redacted)
    assert redacted["outboundScan"]["policyRef"] == (
        "moonmind.security.outbound_scan.v1"
    )
    assert redact_comment_text("") == ""


# ---------------------------------------------------------------------------
# Acceptance: real Activity/adapter path creates/updates bounded comments
# ---------------------------------------------------------------------------


class _AttemptFakeService:
    def __init__(self, initial_labels: list[str] | None = None) -> None:
        self.labels = list(initial_labels) if initial_labels is not None else []
        self.issue_state = "open"

    async def resolve_github_token(self, *, repo: str):
        return "ghs-test", None

    def _github_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _github_permission_summary(self, response) -> str:
        return f"github status {response.status_code}"

    async def check_issue_label_readiness(
        self, *, repo: str, issue_number: int,
        required_labels: list[str], github_token: str | None = None,
    ):
        return {"ready": True, "reasonCode": "ready", "summary": "ready"}

    async def add_issue_labels(
        self, *, repo: str, issue_number: int,
        labels: list[str], github_token: str | None = None,
    ):
        for label in labels:
            if label.lower() not in {item.lower() for item in self.labels}:
                self.labels.append(label)
        return {"ok": True, "reasonCode": "added", "summary": "added"}

    async def remove_issue_label(
        self, *, repo: str, issue_number: int,
        label: str, github_token: str | None = None,
    ):
        self.labels = [
            item for item in self.labels if item.lower() != label.lower()
        ]
        return {"ok": True, "reasonCode": "removed", "summary": "removed"}


class _AttemptFakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _AttemptHttpClient:
    service: _AttemptFakeService | None = None
    comments: list[dict[str, Any]] = []
    posts: list[tuple[str, Any]] = []
    patches: list[tuple[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def _issue_payload(self) -> dict[str, Any]:
        assert type(self).service is not None
        return {
            "number": ISSUE,
            "title": "handoff",
            "body": "body",
            "html_url": f"https://github.com/{REPO}/issues/{ISSUE}",
            "state": type(self).service.issue_state,
            "labels": [{"name": label} for label in type(self).service.labels],
        }

    async def get(self, url: str, **kwargs: Any):
        if url.rstrip("/").endswith("/comments"):
            return _AttemptFakeResponse(list(type(self).comments))
        return _AttemptFakeResponse(self._issue_payload())

    async def patch(self, url: str, **kwargs: Any):
        type(self).patches.append((url, kwargs))
        return _AttemptFakeResponse(self._issue_payload())

    async def post(self, url: str, **kwargs: Any):
        type(self).posts.append((url, kwargs))
        return _AttemptFakeResponse({"id": 42})


def _install_attempt_client(
    monkeypatch: pytest.MonkeyPatch,
    service: _AttemptFakeService,
    comments: list[dict[str, Any]] | None = None,
) -> None:
    _AttemptHttpClient.service = service
    _AttemptHttpClient.comments = list(comments or [])
    _AttemptHttpClient.posts = []
    _AttemptHttpClient.patches = []
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _AttemptHttpClient)


@pytest.mark.asyncio
async def test_activity_path_creates_bounded_attempt_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _AttemptFakeService(initial_labels=[])
    _install_attempt_client(monkeypatch, service)
    result = await update_github_issue_status(
        {"repository": REPO, "issueNumber": ISSUE, "mode": "start",
         "installationId": DEPLOY_A},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["deploymentId"] == DEPLOY_A
    assert normalize_attempt_id(result.outputs["attemptId"])
    assert result.outputs["attemptCommentAction"] == "created"
    assert result.outputs["attemptActivity"] == "active"
    assert result.outputs["outboundScan"]["allowed"] is True
    assert len(_AttemptHttpClient.posts) == 1
    body = _AttemptHttpClient.posts[0][1]["json"]["body"]
    assert len(body) <= attempt.MAX_ATTEMPT_COMMENT_CHARS
    assert attempt.ATTEMPT_MARKER_PREFIX in body
    assert "started implementation" in body
    item = parse_attempt_comment(_comment_dict(body))
    assert item is not None
    assert validate_attempt_comment(
        item, repository=REPO, issue_number=ISSUE,
        trusted_posters=["moonmind-bot"],
    ).usable is True
    assert validate_attempt_comment(
        item, repository=REPO, issue_number=ISSUE,
        trusted_posters=["someone-else"],
    ).reason_code == "untrusted_poster"


@pytest.mark.asyncio
async def test_activity_path_updates_own_comment_and_reuses_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _AttemptFakeService(initial_labels=["status: in-progress"])
    existing_body = render_attempt_comment(_handoff(attempt_id=ATTEMPT_A))
    _install_attempt_client(
        monkeypatch, service, comments=[_comment_dict(existing_body)],
    )
    result = await update_github_issue_status(
        {"repository": REPO, "issueNumber": ISSUE, "mode": "in_progress",
         "attemptId": ATTEMPT_A, "installationId": DEPLOY_A},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["attemptId"] == ATTEMPT_A
    assert result.outputs["attemptReused"] is True
    assert result.outputs["attemptCommentAction"] == "updated"
    assert _AttemptHttpClient.posts == []
    assert len(_AttemptHttpClient.patches) == 1


@pytest.mark.asyncio
async def test_activity_path_blocks_on_conflicting_copies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _AttemptFakeService(initial_labels=["status: in-progress"])
    first = _comment_dict(render_attempt_comment(_handoff(attempt_id=ATTEMPT_A)))
    conflict = _comment_dict(
        render_attempt_comment(
            _handoff(attempt_id=ATTEMPT_A, last_report="divergent")
        ),
        comment_id="13",
    )
    _install_attempt_client(monkeypatch, service, comments=[first, conflict])
    result = await update_github_issue_status(
        {"repository": REPO, "issueNumber": ISSUE, "mode": "in_progress",
         "attemptId": ATTEMPT_A, "installationId": DEPLOY_A},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert _AttemptHttpClient.posts == []
    assert _AttemptHttpClient.patches == []
    assert any("conflicting" in warning for warning in result.outputs["warnings"])
