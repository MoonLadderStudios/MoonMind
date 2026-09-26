"""The retry allowance must explain what it charged, and a reset must be audited.

Issue #4503's history is the motivating shape. Three deployments each
announced an attempt, promoted it to ``active``, and stopped renewing. Their
comments read 3/3, 2/3 and 1/3 remaining, all still ``active`` with outcome
``in_progress``, all with long-expired leases. Reconstruction charges each of
them, so the issue is ``budget_exhausted`` -- and the rejection said "observed
portable failures exhaust the retry allowance", which describes three
unfinished accounting records as three proven implementation failures.

An expired ``active`` attempt keeps costing one (a crash loop must not outlast
the allowance), so the fix is not to stop charging it. The decision instead
names the allowance, every charged attempt and outcome, and how many of them
never recorded a terminal outcome. Where the owning deployment can no longer
recover the evidence, an operator records an audited reset: a GitHub-visible
record that keeps every earlier attempt in lineage but stops charging it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from moonmind.workflows.temporal.github_issue_attempts import (
    OUTCOME_RETRY_RESET,
    AttemptHandoff,
    build_attempt_handoff,
    build_retry_reset_handoff,
    compute_effective_retry,
    parse_attempt_comment,
    reconstruct_from_comments,
    render_attempt_comment,
)

REPO = "MoonLadderStudios/MoonMind"
ISSUE = 4503
LEASE = timedelta(minutes=30)
BOT = "moonmind-bot"


def _now() -> datetime:
    return datetime.now(UTC)


def _stale_active(
    attempt_id: str,
    *,
    deployment: str,
    remaining: int,
    expired_days_ago: float,
    predecessor: str = "",
    predecessor_comment_id: str = "",
) -> AttemptHandoff:
    """One #4503-shaped record: promoted to active, then never finalized."""
    expires = _now() - timedelta(days=expired_days_ago)
    return AttemptHandoff(
        attempt_id=attempt_id,
        deployment_id=deployment,
        repository=REPO,
        issue_number=ISSUE,
        predecessor_attempt_id=predecessor,
        predecessor_comment_id=predecessor_comment_id,
        activity="active",
        outcome="in_progress",
        next_action="continue_implementation",
        retry_allowance=3,
        retry_remaining=remaining,
        lease_renewed_at=(expires - LEASE).isoformat(),
        lease_expires_at=expires.isoformat(),
    )


def _terminal(
    attempt_id: str, outcome: str, *, predecessor: str = ""
) -> AttemptHandoff:
    return AttemptHandoff(
        attempt_id=attempt_id,
        deployment_id="inst-recorded",
        repository=REPO,
        issue_number=ISSUE,
        predecessor_attempt_id=predecessor,
        activity="released",
        outcome=outcome,
        next_action="fresh_retry",
        retry_allowance=3,
        writers_stopped=True,
    )


def _issue_4503_history() -> list[AttemptHandoff]:
    return [
        _stale_active(
            "att-4503-first-aaaa",
            deployment="inst-device-a",
            remaining=3,
            expired_days_ago=5,
        ),
        _stale_active(
            "att-4503-second-bbbb",
            deployment="inst-device-b",
            remaining=2,
            expired_days_ago=4.5,
            predecessor="att-4503-first-aaaa",
            predecessor_comment_id="100",
        ),
        _stale_active(
            "att-4503-third-cccc",
            deployment="inst-device-c",
            remaining=1,
            expired_days_ago=4,
            predecessor="att-4503-second-bbbb",
            predecessor_comment_id="101",
        ),
    ]


def _comments(handoffs, *, poster: str = BOT, first_id: int = 100):
    return [
        {
            "id": str(first_id + index),
            "user": {"login": poster},
            "body": render_attempt_comment(item),
        }
        for index, item in enumerate(handoffs)
    ]


def _reset(
    predecessor: AttemptHandoff, predecessor_comment_id: str, superseded
) -> AttemptHandoff:
    return build_retry_reset_handoff(
        attempt_id="att-4503-reset-dddd",
        deployment_id="inst-operator",
        repository=REPO,
        issue_number=ISSUE,
        authorized_by="nsticco",
        authorized_at="2026-09-26T12:00:00+00:00",
        reason="Attempt records were stranded by an interrupted finalizer.",
        predecessor_attempt_id=predecessor.attempt_id,
        predecessor_comment_id=predecessor_comment_id,
        superseded_attempt_ids=superseded,
        allowance=3,
    )


# ---------------------------------------------------------------------------
# The decision explains what was charged
# ---------------------------------------------------------------------------


def test_stale_active_attempts_exhaust_the_allowance_as_unfinished_accounting() -> None:
    decision = compute_effective_retry(
        _issue_4503_history(), max_attempts=3, now_epoch=_now().timestamp()
    )

    # Still charged: an expired active attempt reached dispatch.
    assert decision.allowed is False
    assert decision.reason_code == "budget_exhausted"
    assert decision.allowance == 3
    assert decision.unresolved_attempts == 3
    assert [item["attemptId"] for item in decision.charged_attempts] == [
        "att-4503-first-aaaa",
        "att-4503-second-bbbb",
        "att-4503-third-cccc",
    ]
    assert {item["outcome"] for item in decision.charged_attempts} == {"in_progress"}
    assert all(item["unresolved"] for item in decision.charged_attempts)
    # The summary must not describe unfinished records as proven failures.
    assert "never recorded a terminal outcome" in decision.summary
    assert "failures" not in decision.summary
    assert decision.to_dict()["unresolvedAttempts"] == 3
    assert decision.to_dict()["allowance"] == 3


def test_recorded_outcomes_are_not_reported_as_unfinished_accounting() -> None:
    decision = compute_effective_retry(
        [
            _terminal("att-000000000001-aaaa", "no_work"),
            _terminal(
                "att-000000000002-aaaa", "failed", predecessor="att-000000000001-aaaa"
            ),
            _terminal(
                "att-000000000003-aaaa",
                "implemented",
                predecessor="att-000000000002-aaaa",
            ),
        ],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert decision.reason_code == "budget_exhausted"
    assert decision.unresolved_attempts == 0
    assert [item["outcome"] for item in decision.charged_attempts] == [
        "no_work",
        "failed",
        "implemented",
    ]
    assert "never recorded a terminal outcome" not in decision.summary


def test_a_live_attempt_is_charged_but_is_not_unfinished_accounting() -> None:
    renewed = _now()
    live = replace(
        _issue_4503_history()[0],
        lease_renewed_at=renewed.isoformat(),
        lease_expires_at=(renewed + LEASE).isoformat(),
    )

    decision = compute_effective_retry(
        [live], max_attempts=3, now_epoch=renewed.timestamp()
    )

    assert decision.remaining == 2
    assert decision.unresolved_attempts == 0
    assert decision.charged_attempts[0]["unresolved"] is False


def test_identical_duplicate_copies_are_one_logical_attempt() -> None:
    failed = _terminal("att-000000000001-aaaa", "failed")
    comments = _comments([failed, failed])

    result = reconstruct_from_comments(
        comments,
        expected_repository=REPO,
        expected_issue_number=ISSUE,
        trusted_posters=[BOT],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert result.outcome == "reconstructed"
    assert result.retry_remaining == 2


def test_reconstruction_carries_the_retry_explanation() -> None:
    result = reconstruct_from_comments(
        _comments(_issue_4503_history()),
        expected_repository=REPO,
        expected_issue_number=ISSUE,
        trusted_posters=[BOT],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert result.reason_code == "budget_exhausted"
    retry = result.to_dict()["retry"]
    assert retry["allowance"] == 3
    assert retry["unresolvedAttempts"] == 3
    assert [item["attemptId"] for item in retry["chargedAttempts"]][
        -1
    ] == "att-4503-third-cccc"


# ---------------------------------------------------------------------------
# An audited reset restores the allowance without erasing history
# ---------------------------------------------------------------------------


def test_an_audited_reset_record_restores_the_allowance_and_keeps_history() -> None:
    history = _issue_4503_history()
    reset = _reset(history[-1], "102", [item.attempt_id for item in history])
    comments = _comments([*history, reset])

    result = reconstruct_from_comments(
        comments,
        expected_repository=REPO,
        expected_issue_number=ISSUE,
        trusted_posters=[BOT],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert result.outcome == "reconstructed"
    assert result.reason_code == "allowed"
    assert result.retry_remaining == 3
    # Nothing is erased: every stranded attempt and the reset stay in lineage.
    assert len(result.lineage) == 4
    retry = result.to_dict()["retry"]
    assert retry["chargedAttempts"] == []
    assert retry["resetAuthorization"] == reset.reset_authorization
    assert "nsticco" in reset.reset_authorization


def test_a_reset_record_is_released_history_that_explains_itself() -> None:
    history = _issue_4503_history()
    reset = _reset(history[-1], "102", [item.attempt_id for item in history])
    body = render_attempt_comment(reset)

    parsed = parse_attempt_comment(body)
    assert parsed.status == "ok"
    assert parsed.handoff.activity == "released"
    assert parsed.handoff.outcome == OUTCOME_RETRY_RESET
    assert parsed.handoff.retry_history == tuple(item.attempt_id for item in history)
    assert "retry reset" in body.lower()
    assert "Attempt records were stranded" in body


def test_attempts_after_a_reset_still_count() -> None:
    history = _issue_4503_history()
    reset = _reset(history[-1], "102", [item.attempt_id for item in history])
    after = _terminal("att-4503-after-eeee", "failed", predecessor=reset.attempt_id)

    decision = compute_effective_retry(
        [*history, reset, after], max_attempts=3, now_epoch=_now().timestamp()
    )

    assert decision.allowed is True
    assert decision.remaining == 2
    assert [item["attemptId"] for item in decision.charged_attempts] == [
        "att-4503-after-eeee"
    ]


def test_a_reset_never_releases_an_operator_hold() -> None:
    held = replace(
        _terminal("att-000000000001-aaaa", "held"),
        operator_hold=True,
        operator_hold_reason="paused by operator",
    )
    reset = _reset(held, "100", [held.attempt_id])

    decision = compute_effective_retry(
        [held, reset], max_attempts=3, now_epoch=_now().timestamp()
    )

    assert decision.allowed is False
    assert decision.reason_code == "operator_hold"


def test_lifecycle_tool_inputs_cannot_forge_a_reset() -> None:
    """Workflow inputs flow through ``build_attempt_handoff``; it cannot mint one."""
    history = _issue_4503_history()
    forged = build_attempt_handoff(
        attempt_id="att-4503-forged-ffff",
        deployment_id="inst-device-c",
        repository=REPO,
        issue_number=ISSUE,
        activity="released",
        outcome=OUTCOME_RETRY_RESET,
        reset_authorization="agent-authored",
        predecessor_attempt_id=history[-1].attempt_id,
        retry_allowance=3,
    )

    assert forged.outcome != OUTCOME_RETRY_RESET
    decision = compute_effective_retry(
        [*history, forged], max_attempts=3, now_epoch=_now().timestamp()
    )
    assert decision.reason_code == "budget_exhausted"


def test_an_untrusted_reset_comment_does_not_reset_anything() -> None:
    history = _issue_4503_history()
    reset = _reset(history[-1], "102", [item.attempt_id for item in history])
    comments = _comments(history) + _comments(
        [reset], poster="drive-by-user", first_id=103
    )

    result = reconstruct_from_comments(
        comments,
        expected_repository=REPO,
        expected_issue_number=ISSUE,
        trusted_posters=[BOT],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert result.outcome == "needs_attention"
    assert result.reason_code == "untrusted_poster"
