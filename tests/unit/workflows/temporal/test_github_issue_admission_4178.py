"""Shared exact-issue admission/guard boundary (issue #4178).

Covers MoonLadderStudios/MoonMind#4178 acceptance criteria through the real
workflow/Activity call shapes: the same admit_exact_issue boundary backs
search, explicit, orchestration, continuation, and retry entrypoints with
pinned identities; blocking evidence classes; interleaved announcements;
resume/pre-mutation revalidation with the documented unfenced race; child
attempt propagation; and retained-history behavior.
"""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.workflows.temporal import github_issue_attempt as attempt_mod
from moonmind.workflows.temporal import story_output_tools as story_tools
from moonmind.workflows.temporal.github_issue_admission import (
    ENTRYPOINT_CONTINUATION,
    ENTRYPOINT_EXPLICIT,
    ENTRYPOINT_ORCHESTRATION,
    ENTRYPOINT_RETRY,
    ENTRYPOINT_SEARCH,
    UNFENCED_CHECK_TO_WRITE_RACE_NOTE,
    AdmissionRequest,
    admit_exact_issue,
    admit_for_entrypoint,
    announce_before_assessment,
    check_delayed_mutation_fenced,
    child_attempt_context,
    contender_quiesce_decision,
    persist_admission_identity,
    recandidate_after_abandon,
    revalidate_for_mutation,
    should_stop_on_resume,
)
from moonmind.workflows.temporal.github_issue_lifecycle import (
    SETTLED_AVAILABLE,
    interpret_issue,
    plan_transition,
    should_abandon_retry,
)
from moonmind.workflows.temporal.github_issue_search import (
    has_in_progress_status,
    is_lifecycle_selectable_candidate,
)


def _req(entrypoint: str = ENTRYPOINT_EXPLICIT) -> AdmissionRequest:
    return AdmissionRequest(
        repository="o/r",
        issue_number=4178,
        workflow_id="wf-1",
        run_id="run-1",
        installation_id="inst-abc12345",
        attempt_id="att_" + "a" * 24,
        entrypoint=entrypoint,
    )


def _linked_failure(attempt_id: str) -> dict:
    return {"attemptId": attempt_id, "outcome": "failed"}


# ---------------------------------------------------------------------------
# Acceptance A: one boundary, pinned identities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entrypoint",
    [ENTRYPOINT_SEARCH, ENTRYPOINT_EXPLICIT, ENTRYPOINT_ORCHESTRATION, ENTRYPOINT_CONTINUATION, ENTRYPOINT_RETRY],
)
def test_all_entrypoints_share_one_boundary(entrypoint: str) -> None:
    decision = admit_for_entrypoint(
        entrypoint,
        repository="o/r",
        issue_number=4178,
        issue={"state": "open", "labels": []},
    )
    assert decision.allowed is True
    assert decision.entrypoint == entrypoint
    assert decision.issue_ref == "o/r#4178"
    assert decision.settled == SETTLED_AVAILABLE


def test_unknown_entrypoint_and_unpinned_identity_block() -> None:
    denied = admit_exact_issue(
        AdmissionRequest(repository="o/r", issue_number=1, entrypoint="preset-exempt"),
        issue={"state": "open", "labels": []},
    )
    assert denied.allowed is False
    assert denied.reason_code == "unknown_entrypoint"

    unpinned = admit_exact_issue(
        AdmissionRequest(repository="", issue_number=0, entrypoint=ENTRYPOINT_SEARCH),
        issue={"state": "open", "labels": []},
    )
    assert unpinned.allowed is False
    assert unpinned.reason_code == "unpinned_identity"


def test_search_and_explicit_use_same_interpretation() -> None:
    # Real workflow call shape: search selector + explicit admission agree.
    issue = {"state": "open", "labels": ["bug"], "number": 1}
    assert is_lifecycle_selectable_candidate({"state": "open", "labels": ["bug"]}) is True
    for entrypoint in (ENTRYPOINT_SEARCH, ENTRYPOINT_EXPLICIT):
        decision = admit_for_entrypoint(
            entrypoint, repository="o/r", issue_number=1,
            issue={"state": "open", "labels": ["bug"]},
        )
        assert decision.allowed is True


# ---------------------------------------------------------------------------
# Acceptance B: blocking evidence classes
# ---------------------------------------------------------------------------


def test_missing_label_plus_active_comment_blocks() -> None:
    decision = admit_exact_issue(
        _req(),
        issue={"state": "open", "labels": []},
        attempt_context={"hasUnresolvedActiveAttempt": True},
    )
    assert decision.allowed is False
    assert decision.reason_code == "missing_label_with_active_attempt"


def test_manual_in_progress_without_trusted_owner_blocks() -> None:
    decision = admit_exact_issue(
        _req(), issue={"state": "open", "labels": ["status: in-progress"]}
    )
    assert decision.allowed is False
    assert decision.reason_code == "manual_in_progress_without_trusted_owner"


def test_unknown_format_mixed_read_failure_exhausted_block() -> None:
    unknown = admit_exact_issue(_req(), issue={"state": "open", "labels": ["status: frobnicate"]})
    assert unknown.reason_code == "unknown_format"

    mixed = admit_exact_issue(
        _req(), issue={"state": "open", "labels": ["status: in-progress", "status: code-review"]}
    )
    assert mixed.reason_code == "mixed_state"

    read_failure = admit_exact_issue(
        _req(),
        issue={"state": "open", "labels": []},
        reads_complete={"labels": True, "comments": False},
    )
    assert read_failure.reason_code == "read_failure"

    exhausted = admit_exact_issue(
        _req(),
        issue={"state": "open", "labels": []},
        attempt_context={"linkedAttempts": [
            {"attemptId": "att_" + c * 24, "outcome": "failed"} for c in ("b", "c", "d")
        ]},
        retry_policy={"maxAttempts": 3},
    )
    # derive_retry_state counts 3 linked failures against allowance 3.
    assert exhausted.allowed is False
    assert exhausted.reason_code in {"retry_exhausted", "missing_policy_lineage"}


def test_operator_hold_and_blockers_and_ambiguous_pr_block() -> None:
    hold = admit_exact_issue(
        _req(),
        issue={"state": "open", "labels": []},
        attempt_context={
            "linkedAttempts": [{"attemptId": "att_" + "c" * 24, "operatorHold": True}],
        },
        retry_policy={"maxAttempts": 3},
    )
    assert hold.allowed is False
    assert hold.reason_code in {"operator_hold", "missing_policy_lineage"}

    blocked = admit_exact_issue(
        _req(),
        issue={"state": "open", "labels": []},
        blockers=[{"source": "prerequisite", "done": False}],
    )
    assert blocked.reason_code == "blocked_prerequisite"

    ambiguous = admit_exact_issue(
        _req(),
        issue={"state": "open", "labels": []},
        pr_identities=[{"prUrl": "https://github.com/o/r/pull/1"}, {"ambiguous": True}],
    )
    assert ambiguous.reason_code == "ambiguous_pr_identity"


# ---------------------------------------------------------------------------
# Req 2: announce before assessment
# ---------------------------------------------------------------------------


def test_announce_before_assessment_plans_in_progress() -> None:
    planned = announce_before_assessment(
        settled=SETTLED_AVAILABLE, repository="o/r", issue_number=1,
        attempt_id="att_" + "d" * 24, current_labels=[],
    )
    assert planned["planned"] is True
    assert planned["transition"]["allowed"] is True
    assert planned["mutation"]["labelsToAdd"] == ["status: in-progress"]

    recovery = announce_before_assessment(
        settled="recovery_needed", repository="o/r", issue_number=1,
        attempt_id="att_" + "d" * 24, current_labels=["status: recovery-needed"],
    )
    assert recovery["planned"] is True

    ineligible = announce_before_assessment(
        settled="in_progress", repository="o/r", issue_number=1,
        attempt_id="att_" + "d" * 24,
    )
    assert ineligible["planned"] is False


# ---------------------------------------------------------------------------
# Req 3: persist exactly once
# ---------------------------------------------------------------------------


def test_persist_once_and_substitution_denied() -> None:
    first = persist_admission_identity({"repository": "o/r", "issueNumber": 1}, None)
    assert first["persisted"] is True
    assert first["reasonCode"] == "persisted_once"

    replay = persist_admission_identity(
        {"repository": "o/r", "issueNumber": 1}, first["identity"]
    )
    assert replay["persisted"] is True

    substituted = persist_admission_identity(
        {"repository": "o/r", "issueNumber": 2}, first["identity"]
    )
    assert substituted["persisted"] is False
    assert substituted["reasonCode"] == "substitution_denied"


# ---------------------------------------------------------------------------
# Acceptance C: two- and three-interleaved announcements
# ---------------------------------------------------------------------------


def test_two_interleaved_announcements_quiesce_without_clearing() -> None:
    own = "att_" + "e" * 24
    other = {"attemptId": "att_" + "f" * 24, "activity": "preparing"}
    decision = contender_quiesce_decision(own_attempt_id=own, observed_contenders=[other])
    assert decision["quiesce"] is True
    assert decision["stopSharedPublication"] is True
    assert decision["preserveOutput"] is True
    assert decision["clearOtherInProgress"] is False
    assert decision["contenders"] == ["att_" + "f" * 24]


def test_three_simultaneous_announcements_all_observe_contenders() -> None:
    attempts = ["att_" + c * 24 for c in ("a", "b", "c")]
    for own in attempts:
        others = [
            {"attemptId": other, "activity": "active"}
            for other in attempts if other != own
        ]
        decision = contender_quiesce_decision(own_attempt_id=own, observed_contenders=others)
        assert decision["quiesce"] is True
        assert decision["clearOtherInProgress"] is False
        assert len(decision["contenders"]) == 2


def test_recandidate_only_after_abandon_and_settled_writers() -> None:
    assert recandidate_after_abandon(own_announcement_abandoned=True, writers_settled=True)["allowed"] is True
    assert recandidate_after_abandon(own_announcement_abandoned=True, writers_settled=False)["allowed"] is False
    assert recandidate_after_abandon(own_announcement_abandoned=False, writers_settled=True)["allowed"] is False


# ---------------------------------------------------------------------------
# Acceptance D: successor resume + delayed mutation honesty
# ---------------------------------------------------------------------------


def test_reconnection_after_known_successor_stops_effects() -> None:
    stopped = revalidate_for_mutation(
        own_attempt_id="att_" + "a" * 24, observed={"settled": "available"},
        known_successor_attempt_id="att_" + "b" * 24,
    )
    assert stopped["allowed"] is False
    assert stopped["reasonCode"] == "known_successor"
    assert "Unfenced" in stopped["raceNote"] or "unfenced" in stopped["raceNote"].lower()

    resume = should_stop_on_resume(
        own_attempt_id="att_" + "a" * 24, known_successor_attempt_id="att_" + "b" * 24
    )
    assert resume["stop"] is True


def test_delayed_already_issued_mutation_not_falsely_fenced() -> None:
    unfenced = check_delayed_mutation_fenced(mutation_issued_before_check=True)
    assert unfenced["fenced"] is False
    assert unfenced["reasonCode"] == "unfenced_race"
    assert UNFENCED_CHECK_TO_WRITE_RACE_NOTE.split(":")[0][:8] in unfenced["summary"]

    # should_abandon_retry remains the honest successor detector for real shapes.
    observed = interpret_issue({"state": "open", "labels": ["status: code-review"]})
    abandon, _ = should_abandon_retry(intended_from_settled="available", observed=observed)
    assert abandon is True


# ---------------------------------------------------------------------------
# Acceptance E: child attempt propagation
# ---------------------------------------------------------------------------


def test_internal_retry_and_review_children_retain_controlling_attempt() -> None:
    controlling = "att_" + "a" * 24
    retry = child_attempt_context(controlling, child_kind="internal_retry")
    assert retry["allowed"] is True
    assert retry["attemptId"] == controlling
    assert retry["stayInProgress"] is True

    review = child_attempt_context(controlling, child_kind="review_wait")
    assert review["allowed"] is True
    assert review["attemptId"] == controlling

    repair_denied = child_attempt_context(controlling, child_kind="existing_pr_repair")
    assert repair_denied["allowed"] is False
    assert repair_denied["reasonCode"] == "missing_pr_target"

    repair = child_attempt_context(
        controlling, child_kind="existing_pr_repair", pr_url="https://github.com/o/r/pull/7"
    )
    assert repair["allowed"] is True
    assert repair["reasonCode"] == "admitted_repair"


# ---------------------------------------------------------------------------
# Acceptance F: real workflow/Activity shapes + retained history
# ---------------------------------------------------------------------------


class _AdmissionFakeService:
    """Minimal trusted GitHub boundary: fetch + token only (no writes)."""

    def __init__(self, labels: list[str] | None = None) -> None:
        self.labels = list(labels) if labels is not None else []
        self.operations: list[tuple[str, str]] = []

    async def resolve_github_token(self, *, repo: str):
        return "ghs-test", None

    def _github_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _github_permission_summary(self, response) -> str:
        return f"github status {response.status_code}"

    async def check_issue_label_readiness(self, *, repo: str, issue_number: int,
                                          required_labels: list[str], github_token: str | None = None):
        return {"ready": True, "reasonCode": "ready", "summary": "ready"}

    async def add_issue_labels(self, *, repo: str, issue_number: int,
                               labels: list[str], github_token: str | None = None):
        for label in labels:
            self.operations.append(("add", label))
            self.labels.append(label)
        return {"ok": True, "reasonCode": "added", "summary": "added"}

    async def remove_issue_label(self, *, repo: str, issue_number: int,
                                 label: str, github_token: str | None = None):
        self.operations.append(("remove", label))
        self.labels = [existing for existing in self.labels if existing != label]
        return {"ok": True, "reasonCode": "removed", "summary": "removed"}


class _AdmissionFakeHttpResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _AdmissionHttpClient:
    """HTTP fake serving one issue payload (or search items) for admission tests."""

    issue_payload: dict[str, Any] = {}
    search_payload: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, **kwargs: Any):
        if "search/issues" in url:
            return _AdmissionFakeHttpResponse(dict(type(self).search_payload))
        return _AdmissionFakeHttpResponse(dict(type(self).issue_payload))


def _install_admission_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _AdmissionHttpClient)


def _admission_issue_payload(number: int, labels: list[str]) -> dict[str, Any]:
    return {
        "number": number,
        "title": "admission",
        "body": "body",
        "html_url": f"https://github.com/o/r/issues/{number}",
        "state": "open",
        "labels": [{"name": label} for label in labels],
    }


@pytest.mark.asyncio
async def test_start_guard_blocks_threaded_blockers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Req-1 blocker bundle from the trusted channel blocks the start write."""
    service = _AdmissionFakeService(labels=[])
    _install_admission_http(monkeypatch)
    _AdmissionHttpClient.issue_payload = _admission_issue_payload(4178, [])
    result = await story_tools.update_github_issue_status(
        {
            "repository": "o/r",
            "issueNumber": 4178,
            "mode": "start",
            "blockingIssues": [{"repository": "o/r", "number": 1}],
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["reasonCode"] == "blocked_prerequisite"
    assert service.operations == []


@pytest.mark.asyncio
async def test_start_guard_blocks_incomplete_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit failed read is unknown evidence, never an empty owner set."""
    service = _AdmissionFakeService(labels=[])
    _install_admission_http(monkeypatch)
    _AdmissionHttpClient.issue_payload = _admission_issue_payload(4178, [])
    result = await story_tools.update_github_issue_status(
        {
            "repository": "o/r",
            "issueNumber": 4178,
            "mode": "start",
            "readsComplete": {"labels": True, "comments": False},
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "read_failure"
    assert service.operations == []


@pytest.mark.asyncio
async def test_start_guard_blocks_exhausted_retry_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid retry policy blocks automatic admission rather than assuming fresh."""
    service = _AdmissionFakeService(labels=[])
    _install_admission_http(monkeypatch)
    _AdmissionHttpClient.issue_payload = _admission_issue_payload(4178, [])
    result = await story_tools.update_github_issue_status(
        {
            "repository": "o/r",
            "issueNumber": 4178,
            "mode": "start",
            "retryPolicy": {"maxAttempts": "many"},
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "retry_exhausted"
    assert service.operations == []


@pytest.mark.asyncio
async def test_resume_after_known_successor_stops_shared_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconnection after a known successor performs no new shared mutation."""
    service = _AdmissionFakeService(labels=[])
    _install_admission_http(monkeypatch)
    _AdmissionHttpClient.issue_payload = _admission_issue_payload(4178, [])
    result = await story_tools.update_github_issue_status(
        {
            "repository": "o/r",
            "issueNumber": 4178,
            "mode": "start",
            "attemptId": "att_" + "a" * 24,
            "knownSuccessorAttemptId": "att_" + "b" * 24,
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "resume_blocked"
    assert service.operations == []


@pytest.mark.asyncio
async def test_observed_contender_quiesces_without_clearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An observed competing attempt stops this write; its label is never cleared."""
    service = _AdmissionFakeService(labels=["status: in-progress"])
    _install_admission_http(monkeypatch)
    _AdmissionHttpClient.issue_payload = _admission_issue_payload(4178, ["status: in-progress"])
    result = await story_tools.update_github_issue_status(
        {
            "repository": "o/r",
            "issueNumber": 4178,
            "mode": "start",
            "attemptId": "att_" + "a" * 24,
            "observedContenders": [{"attemptId": "att_" + "b" * 24, "activity": "active"}],
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "contender_observed"
    assert service.operations == []
    assert service.labels == ["status: in-progress"]


@pytest.mark.asyncio
async def test_search_admission_runs_without_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_issue enforces the shared boundary on the production path (no resolver)."""
    from moonmind.workflows.temporal import github_issue_search as search_tools
    from moonmind.workflows.temporal.github_issue_search import resolve_issue

    def _candidate(number: int, labels: list[str]) -> dict[str, Any]:
        return {
            "number": number,
            "title": "candidate",
            "body": "body",
            "html_url": f"https://github.com/o/r/issues/{number}",
            "state": "open",
            "labels": [{"name": label} for label in labels],
        }

    _AdmissionHttpClient.search_payload = {
        "incomplete_results": False,
        "items": [_candidate(21, []), _candidate(22, [])],
    }
    monkeypatch.setattr(search_tools.httpx, "AsyncClient", _AdmissionHttpClient)
    service = _AdmissionFakeService()

    async def no_blockers(issue: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    number, _ = await resolve_issue(
        repository="o/r",
        query="task",
        github_service=service,  # type: ignore[arg-type]
        blockers_from_issue=no_blockers,
    )
    assert number == 21

    # Supplied unresolved attempt evidence blocks selection even when the
    # in-progress label is missing — through the async search shape.
    blocked, _ = await resolve_issue(
        repository="o/r",
        query="task",
        github_service=service,  # type: ignore[arg-type]
        blockers_from_issue=no_blockers,
        attempt_context={"has_unresolved_active_attempt": True},
    )
    assert blocked is None


@pytest.mark.asyncio
async def test_explicit_load_honors_entrypoint_and_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct loads route orchestration/continuation identities via the same boundary."""
    service = _AdmissionFakeService(labels=[])
    _install_admission_http(monkeypatch)
    _AdmissionHttpClient.issue_payload = _admission_issue_payload(4178, [])
    result = await story_tools.load_github_issue_preset_brief(
        {
            "repository": "o/r",
            "issueNumber": 4178,
            "entrypoint": "orchestration",
            "blockingIssues": [{"repository": "o/r", "number": 1}],
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    assert "lifecycle admission" in result.outputs["error"]

    admitted = await story_tools.load_github_issue_preset_brief(
        {"repository": "o/r", "issueNumber": 4178, "entrypoint": "continuation"},
        github_service_factory=lambda: service,
    )
    assert admitted.status == "COMPLETED"


def test_real_transition_and_retry_shapes_preserved() -> None:
    decision = plan_transition(
        from_settled="available", to_target="to_in_progress",
        evidence={"admission_passed": True, "prior_work_inspected": True}, reason="admit",
    )
    assert decision.allowed is True

    # Portable retry lineage still owns the budget (no duplicate implementation).
    state = attempt_mod.derive_retry_state([], policy={"maxAttempts": 3})
    assert state["blocked"] is False

    # Retained history: legacy in-progress spellings still count.
    assert has_in_progress_status({"labels": ["status: in-progress"]}) is True
