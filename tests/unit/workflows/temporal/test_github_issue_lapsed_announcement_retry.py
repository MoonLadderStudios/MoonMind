"""A pre-dispatch announcement must not spend the issue's retries.

The 2026-09-15 launcher outage left the backlog in a shape the
``runtime_unavailable`` exemption cannot see. Every scheduled run announced an
attempt -- a version-2 handoff comment with ``outcome: in_progress`` and a
lease -- and then died before recording any outcome about itself.

Selection announces with the short ``preparing`` lease and holds that deadline
until dispatch; the first execution renewal promotes the handoff to ``active``.
An expired ``preparing`` handoff therefore never reached dispatch and could not
have produced work. An expired ``active`` one did reach dispatch, so it still
costs an attempt: GitHub alone cannot prove no agent ran, and exempting it
would let a crash loop bypass the allowance while discarding unrecovered work.
Proving that no runtime started needs the controlling history, which is what
the local claim sweep uses to record ``runtime_unavailable``.

Nothing else finalizes those records either. ``reconcile_expired_issue``
retires the lapsed reservation by removing ``status: in-progress``, and
deliberately claims no terminal authority over another deployment's attempt;
once the label is gone the issue reads Available, so the bounded reconciliation
scan classifies it ``owned_by_normal_path`` and never looks at its comments
again. The announcement stays ``in_progress`` forever.

Admission, meanwhile, reads the comments and charged every one of those
announcements against the issue's three-attempt allowance. Three scheduled
ticks per issue was enough: by 2026-09-19 the search-and-implement preset
rejected 78 of 110 candidates with ``budget_exhausted`` and selected nothing,
for attempts that never touched the repository. That is exactly the failure the
allowance rule exists to prevent -- "only attempts that could have produced work
consume it" (design section 5.3) -- reached from before the attempt could write
its own outcome down.

A lapsed pre-dispatch announcement that recorded no work is therefore treated
like the ``runtime_unavailable`` outcome it never managed to publish: retained
in lineage, not charged, and carrying the same portable back-off from the
moment its lease lapsed, so a still-broken deployment rotates past the
candidate instead of hammering it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from moonmind.workflows.temporal.github_issue_attempts import (
    RUNTIME_UNAVAILABLE_COOLDOWN_SECONDS,
    AttemptHandoff,
    compute_effective_retry,
    reconstruct_from_comments,
    render_attempt_comment,
)

REPO = "MoonLadderStudios/MoonMind"
ISSUE = 4350
LEASE = timedelta(minutes=30)


def _now() -> datetime:
    return datetime.now(UTC)


def _announcement(
    attempt_id: str,
    *,
    predecessor: str = "",
    predecessor_comment_id: str = "",
    lapsed_minutes: float | None = None,
    leased: bool = True,
    activity: str = "preparing",
    **overrides,
) -> AttemptHandoff:
    """One pre-dispatch announcement: ``in_progress``, no recorded work.

    ``lapsed_minutes`` is how long ago the lease expired; ``None`` leaves a
    live lease. ``leased=False`` produces the version-1 shape, whose writers
    never agreed to an expiry.
    """
    lease_fields: dict[str, str] = {}
    if leased:
        expires = _now() - timedelta(minutes=lapsed_minutes or -30)
        lease_fields = {
            "lease_renewed_at": (expires - LEASE).isoformat(),
            "lease_expires_at": expires.isoformat(),
        }
    return AttemptHandoff(
        attempt_id=attempt_id,
        deployment_id="inst-outage",
        repository=REPO,
        issue_number=ISSUE,
        predecessor_attempt_id=predecessor,
        predecessor_comment_id=predecessor_comment_id,
        activity=activity,
        outcome="in_progress",
        next_action="continue_implementation",
        retry_allowance=3,
        **lease_fields,
        **overrides,
    )


def _terminal(
    attempt_id: str, outcome: str, *, predecessor: str = ""
) -> AttemptHandoff:
    return AttemptHandoff(
        attempt_id=attempt_id,
        deployment_id="inst-outage",
        repository=REPO,
        issue_number=ISSUE,
        predecessor_attempt_id=predecessor,
        activity="released",
        outcome=outcome,
        next_action="fresh_retry",
        retry_allowance=3,
        writers_stopped=True,
    )


# ---------------------------------------------------------------------------
# The allowance counts outcomes, not announcements
# ---------------------------------------------------------------------------


def test_lapsed_announcements_do_not_consume_the_allowance() -> None:
    """Three ticks announced and vanished; the issue attempted nothing."""
    decision = compute_effective_retry(
        [
            _announcement("att-000000000001-aaaa", lapsed_minutes=4000),
            _announcement(
                "att-000000000002-aaaa",
                predecessor="att-000000000001-aaaa",
                lapsed_minutes=3900,
            ),
            _announcement(
                "att-000000000003-aaaa",
                predecessor="att-000000000002-aaaa",
                lapsed_minutes=3800,
            ),
        ],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert decision.allowed is True
    assert decision.reason_code == "allowed"
    assert decision.remaining == 3


def test_the_production_lineage_shape_is_admissible_again() -> None:
    """One real release, then two announcements that never reached dispatch."""
    decision = compute_effective_retry(
        [
            _terminal("att-7b5bc80f4e65-c88b", "no_work"),
            _announcement(
                "att-aac637a5617d-4e4a",
                predecessor="att-7b5bc80f4e65-c88b",
                lapsed_minutes=5700,
            ),
            _announcement(
                "att-c798abee1c1c-8ecc",
                predecessor="att-aac637a5617d-4e4a",
                lapsed_minutes=5620,
            ),
        ],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert decision.allowed is True
    # Only the one attempt that reported an outcome is charged.
    assert decision.remaining == 2


def test_lapsed_announcements_stay_in_the_reported_lineage() -> None:
    """Not charged is not erased: the history stays observable."""
    comments = [
        {
            "id": "100",
            "user": {"login": "moonmind-bot"},
            "body": render_attempt_comment(
                _announcement("att-000000000001-aaaa", lapsed_minutes=4000)
            ),
        },
        {
            "id": "101",
            "user": {"login": "moonmind-bot"},
            "body": render_attempt_comment(
                _announcement(
                    "att-000000000002-aaaa",
                    predecessor="att-000000000001-aaaa",
                    predecessor_comment_id="100",
                    lapsed_minutes=3900,
                )
            ),
        },
    ]

    result = reconstruct_from_comments(
        comments,
        expected_repository=REPO,
        expected_issue_number=ISSUE,
        trusted_posters=["moonmind-bot"],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert result.outcome == "reconstructed"
    assert len(result.lineage) == 2
    assert result.retry_remaining == 3


# ---------------------------------------------------------------------------
# What still costs an attempt
# ---------------------------------------------------------------------------


def test_a_live_announcement_still_occupies_a_slot() -> None:
    """An attempt that currently holds the reservation is a real attempt."""
    decision = compute_effective_retry(
        [_announcement("att-000000000001-aaaa")],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert decision.remaining == 2


def test_an_expired_active_attempt_still_costs_one() -> None:
    """Dispatch happened: the execution lease was granted and then renewed.

    GitHub alone cannot prove no agent ran, so a worker that crashed after
    dispatch must stay charged -- otherwise a crash loop bypasses the bounded
    allowance and keeps discarding work it never published.
    """
    decision = compute_effective_retry(
        [
            _announcement(
                "att-000000000001-aaaa", activity="active", lapsed_minutes=4000
            ),
            _announcement(
                "att-000000000002-aaaa",
                activity="active",
                predecessor="att-000000000001-aaaa",
                lapsed_minutes=3900,
            ),
            _announcement(
                "att-000000000003-aaaa",
                activity="active",
                predecessor="att-000000000002-aaaa",
                lapsed_minutes=3800,
            ),
        ],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert decision.allowed is False
    assert decision.reason_code == "budget_exhausted"
    assert decision.remaining == 0


def test_a_crash_loop_after_dispatch_cannot_outlast_the_allowance() -> None:
    """Each post-dispatch crash spends a slot even with nothing published."""
    chain = [
        _announcement(
            f"att-00000000000{index}-aaaa",
            activity="active",
            predecessor=f"att-00000000000{index - 1}-aaaa" if index > 1 else "",
            lapsed_minutes=4000 - index,
        )
        for index in range(1, 5)
    ]

    decision = compute_effective_retry(
        chain, max_attempts=3, now_epoch=_now().timestamp()
    )

    assert decision.allowed is False
    assert decision.reason_code == "budget_exhausted"


def test_a_version_one_announcement_keeps_its_non_expiring_contract() -> None:
    """Version 1 predates the lease agreement; nothing proves it lapsed."""
    decision = compute_effective_retry(
        [_announcement("att-000000000001-aaaa", leased=False)],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert decision.remaining == 2


def test_a_caller_without_a_clock_keeps_the_existing_accounting() -> None:
    """A lapse cannot be proven without a clock, so nothing is exempted."""
    decision = compute_effective_retry(
        [
            _announcement("att-000000000001-aaaa", lapsed_minutes=4000),
            _announcement(
                "att-000000000002-aaaa",
                predecessor="att-000000000001-aaaa",
                lapsed_minutes=3900,
            ),
            _announcement(
                "att-000000000003-aaaa",
                predecessor="att-000000000002-aaaa",
                lapsed_minutes=3800,
            ),
        ],
        max_attempts=3,
    )

    assert decision.allowed is False
    assert decision.reason_code == "budget_exhausted"


def test_a_lapsed_announcement_that_recorded_work_still_counts() -> None:
    """Preserved work is evidence about the issue; it is recovery, not nothing."""
    with_pr = _announcement(
        "att-000000000001-aaaa",
        lapsed_minutes=4000,
        pr_url="https://github.com/MoonLadderStudios/MoonMind/pull/4350",
    )
    with_branch = _announcement(
        "att-000000000002-aaaa",
        predecessor="att-000000000001-aaaa",
        lapsed_minutes=3900,
        saved_branch="moonmind/issue-4350",
    )
    with_sha = _announcement(
        "att-000000000003-aaaa",
        predecessor="att-000000000002-aaaa",
        lapsed_minutes=3800,
        saved_sha="0270b583b74bd0ca8273c927a2e9c0123bc29e8821b22064f8d808d4a11a020c",
    )

    decision = compute_effective_retry(
        [with_pr, with_branch, with_sha],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert decision.allowed is False
    assert decision.reason_code == "budget_exhausted"


def test_genuine_outcomes_still_exhaust_the_allowance() -> None:
    """Regression guard: the budget must still bound real work attempts."""
    decision = compute_effective_retry(
        [
            _terminal("att-000000000001-aaaa", "no_work"),
            _terminal(
                "att-000000000002-aaaa", "no_work", predecessor="att-000000000001-aaaa"
            ),
            _terminal(
                "att-000000000003-aaaa", "failed", predecessor="att-000000000002-aaaa"
            ),
        ],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert decision.allowed is False
    assert decision.reason_code == "budget_exhausted"
    assert decision.remaining == 0


def test_an_operator_hold_still_outranks_a_lapsed_announcement() -> None:
    held = replace(
        _announcement("att-000000000001-aaaa", lapsed_minutes=4000),
        operator_hold=True,
        operator_hold_reason="paused",
    )

    decision = compute_effective_retry(
        [held], max_attempts=3, now_epoch=_now().timestamp()
    )

    assert decision.allowed is False
    assert decision.reason_code == "operator_hold"


# ---------------------------------------------------------------------------
# Back-off: a still-broken deployment rotates instead of hammering one issue
# ---------------------------------------------------------------------------


def test_a_fresh_lapse_defers_the_candidate_without_spending_the_budget() -> None:
    lapsed_minutes = max(1.0, RUNTIME_UNAVAILABLE_COOLDOWN_SECONDS / 60 - 10)
    decision = compute_effective_retry(
        [_announcement("att-000000000001-aaaa", lapsed_minutes=lapsed_minutes)],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert decision.allowed is False
    assert decision.reason_code == "cooling_down"
    # Deferred, never exhausted: the allowance is intact for the next window.
    assert decision.remaining == 3
    assert decision.cooldown_until


def test_an_elapsed_backoff_restores_the_candidate_with_no_operator_act() -> None:
    lapsed_minutes = RUNTIME_UNAVAILABLE_COOLDOWN_SECONDS / 60 + 10
    decision = compute_effective_retry(
        [_announcement("att-000000000001-aaaa", lapsed_minutes=lapsed_minutes)],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert decision.allowed is True
    assert decision.reason_code == "allowed"


def test_a_recorded_cooldown_still_wins_when_it_is_the_later_one() -> None:
    """The synthesized back-off never shortens a longer recorded one."""
    recorded = (_now() + timedelta(days=1)).isoformat()
    decision = compute_effective_retry(
        [
            _announcement(
                "att-000000000001-aaaa", lapsed_minutes=4000, cooldown_until=recorded
            )
        ],
        max_attempts=3,
        now_epoch=_now().timestamp(),
    )

    assert decision.allowed is False
    assert decision.reason_code == "cooling_down"
    assert decision.cooldown_until == recorded
