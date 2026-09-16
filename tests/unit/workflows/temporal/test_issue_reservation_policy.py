"""Issue reservations are expiring, advisory, and independent of cleanup.

These exercise the production tools and helpers rather than nearby doubles:
an announced reservation that never starts lapses in minutes, withdrawing one
never waits for contention to clear, ending ownership never waits for
bookkeeping, and a version-1 backlog is retired by one explicit operator
cutover instead of blocking forever.
"""

# ruff: noqa: F811 -- imported pytest fixture

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import GitHubIssueClaim
from moonmind.workflows.temporal import github_issue_claim_lease as leases
from moonmind.workflows.temporal import story_output_tools as tools
from moonmind.workflows.temporal.activities.github_issue_finalization_activities import (
    finalize_failed_attempt,
)
from moonmind.workflows.temporal.github_issue_attempts import (
    AttemptHandoff,
    parse_attempt_comment,
    render_attempt_comment,
)
from moonmind.workflows.temporal.github_issue_claim_migration import (
    ACTION_OPERATOR_HOLD,
    ACTION_PRESERVE,
    ACTION_RETAIN_LIVE,
    ACTION_RETIRE,
    apply_issue_recovery,
    plan_issue_recovery,
)
from moonmind.workflows.temporal.issue_claim_store import (
    ActiveIssueClaimConflict,
    IssueClaimStore,
    publish_claim_comment,
)
from tests.unit.workflows.temporal.test_issue_claim_journey import journey  # noqa: F401

_JOURNEY_FIXTURE = journey  # noqa: F841 -- referenced below to keep fixture registration explicit
assert _JOURNEY_FIXTURE is journey

REPOSITORY = "example/repo"
ISSUE = 3970
CUTOVER = datetime(2026, 9, 14, tzinfo=UTC)


def _legacy_comment(**overrides):
    """A version-1 announcement: no lease, because none existed then."""
    handoff = AttemptHandoff.from_dict(
        {
            "formatVersion": 1,
            "attemptId": "att-stranded-legacy",
            "deploymentId": "inst-old-deployment",
            "repository": REPOSITORY,
            "issueNumber": ISSUE,
            "workflowId": "default/mm:07161650-2026-09-13T00:00:00Z",
            "activity": "preparing",
            "writersStopped": False,
            "outcome": "in_progress",
            "nextAction": "continue_implementation",
            **overrides,
        }
    )
    return {
        "id": 91,
        "body": render_attempt_comment(handoff),
        "user": {"id": 123, "login": "fixture-owner"},
        "author_association": "COLLABORATOR",
        "created_at": "2026-09-13T00:00:00Z",
    }


async def _announce(service, owner, *, inputs=None):
    return await tools.load_github_issue_preset_brief(
        inputs or {"repository": REPOSITORY, "issueSearch": ""},
        {"execution_owner": owner},
        github_service_factory=lambda: service,
    )


async def _independent_store(tmp_path, name):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    async with engine.begin() as connection:
        await connection.run_sync(GitHubIssueClaim.__table__.create)
    return engine, IssueClaimStore(async_sessionmaker(engine, expire_on_commit=False))


# -- Announcement is short-lived -------------------------------------------


@pytest.mark.asyncio
async def test_announced_reservation_carries_the_short_preparing_deadline(journey):
    """Selection to dispatch is minutes, so a death in between costs minutes."""
    state, service, sessions = journey
    await _announce(service, "default/announcer")
    handoff = parse_attempt_comment(state["comments"][0]["body"]).handoff
    window = leases.parse_time(handoff.lease_expires_at) - leases.parse_time(
        handoff.lease_renewed_at
    )
    assert handoff.activity == "preparing"
    assert window == leases.lease_duration_for("preparing")
    assert window == timedelta(minutes=5) < leases.LEASE_DURATION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "minutes,available", [(3, False), (6, True)], ids=["live", "lapsed"]
)
async def test_unstarted_reservation_frees_the_issue_after_its_deadline(
    journey, monkeypatch, tmp_path, minutes, available
):
    """A worker killed right after announcing must not hold the issue."""
    state, service, sessions = journey
    await _announce(service, "default/killed-after-announcement")
    base = leases.utc_now()
    monkeypatch.setattr(leases, "utc_now", lambda: base + timedelta(minutes=minutes))
    engine, successor = await _independent_store(tmp_path, "successor.db")
    monkeypatch.setattr(tools, "IssueClaimStore", lambda: successor)
    state["actor"] = {"id": 456, "login": "successor"}
    try:
        if not available:
            with pytest.raises(ActiveIssueClaimConflict):
                await tools._prepare_github_issue_claim(
                    inputs={"repository": REPOSITORY, "issueNumber": ISSUE},
                    context={"execution_owner": "other/successor"},
                    repository=REPOSITORY,
                    issue_number=ISSUE,
                    service=service,
                )
        else:
            receipt = await tools._prepare_github_issue_claim(
                inputs={"repository": REPOSITORY, "issueNumber": ISSUE},
                context={"execution_owner": "other/successor"},
                repository=REPOSITORY,
                issue_number=ISSUE,
                service=service,
            )
            assert receipt.issue_number == ISSUE
            # The predecessor's comment survives as history.
            assert len(state["comments"]) == 1
    finally:
        await engine.dispose()


# -- Waiting for capacity must not keep a reservation -----------------------


@pytest.mark.asyncio
async def test_lease_is_not_renewed_while_queued_behind_unavailable_capacity():
    """Reservation withdrawn by lapse; the deployment backs off, not the backlog."""
    from moonmind.workflows.temporal import github_issue_lease_workflow as lease_workflow

    clock = {"now": datetime(2026, 9, 14, tzinfo=UTC)}
    renewals = []
    slot_assigned = {"value": False}

    class _Clock:
        @staticmethod
        def now():
            return clock["now"]

        @staticmethod
        async def sleep(seconds):
            clock["now"] += timedelta(seconds=seconds)

    async def renew(payload):
        renewals.append(clock["now"])
        return {
            "status": "renewed",
            "owner": payload["owner"],
            "attemptId": payload["attemptId"],
            "leaseExpiresAt": (clock["now"] + timedelta(minutes=30)).isoformat(),
        }

    queued = asyncio.Event()

    async def execute():
        # The agent never acquires a slot: it waits for capacity forever.
        await queued.wait()

    import moonmind.workflows.temporal.github_issue_lease_workflow as module

    original = module.workflow
    module.workflow = _Clock
    try:
        with pytest.raises(Exception) as caught:
            await lease_workflow.execute_with_issue_lease(
                lease={"owner": "default/queued", "attemptId": "att-queued"},
                execute=execute,
                renew=renew,
                should_renew=lambda: slot_assigned["value"],
            )
    finally:
        module.workflow = original
    assert "lease expired" in str(caught.value)
    # Exactly one renewal: the pre-launch confirmation. Queued time never
    # refreshes the reservation.
    assert len(renewals) == 1


# -- Relinquishment is independent of contention ----------------------------


@pytest.mark.asyncio
async def test_withdrawal_succeeds_under_contention_but_other_updates_do_not(journey):
    """The operation that resolves contention cannot be blocked by contention."""
    state, service, sessions = journey
    store = IssueClaimStore(sessions)
    owner = "default/withdrawing"
    await _announce(service, owner, inputs={"repository": REPOSITORY, "issueNumber": ISSUE})
    receipt = await store.get(owner)
    contender = _legacy_comment(attemptId="att-successor", activity="active")
    contender["id"] = 99
    contender["created_at"] = "2026-09-20T00:00:00Z"
    state["comments"].append(contender)
    own = parse_attempt_comment(receipt.comment_body).handoff

    # Continuing shared work still requires uncontested ownership.
    with pytest.raises(ActiveIssueClaimConflict):
        await publish_claim_comment(
            store,
            receipt,
            service,
            render_attempt_comment(replace(own, activity="awaiting-review")),
        )

    receipt = await store.get(owner)
    released = render_attempt_comment(replace(own, activity="released"))
    assert await publish_claim_comment(store, receipt, service, released)
    retired = await store.get(owner)
    assert retired.released and retired.ownership_ended
    # The successor's comment is never rewritten by the withdrawing attempt.
    assert state["comments"][1]["body"] == contender["body"]


# -- Ownership ends even when bookkeeping cannot ----------------------------


@pytest.mark.asyncio
async def test_failed_label_bookkeeping_still_ends_ownership(
    journey, monkeypatch, tmp_path
):
    """"Cleanup unfinished" and "still reserving this issue" are separate facts."""
    state, service, sessions = journey
    store = IssueClaimStore(sessions)
    owner = "default/finalizing"
    state["labels"] = ["status: in-progress"]
    await _announce(service, owner, inputs={"repository": REPOSITORY, "issueNumber": ISSUE})
    receipt = await store.get(owner)

    async def denied(**_kwargs):
        return {"ok": False, "reasonCode": "denied", "summary": "Label remove denied."}

    monkeypatch.setattr(service, "remove_issue_label", denied)
    result = await finalize_failed_attempt(
        repository=REPOSITORY,
        issue_number=ISSUE,
        execution_event="failed",
        from_settled="in_progress",
        writer_evidence={
            "writersStopped": True,
            "stopMethod": "runtime_quiescence",
            "stopEvidence": "controlling run closed",
        },
        mutation_evidence={
            "pushOutcome": "verified_absent",
            "prOutcome": "verified_absent",
            "mergeOutcome": "verified_absent",
        },
        preservation_evidence={
            "saveMethod": "explicit_no_work",
            "trustworthyNoWork": True,
        },
        disposition_evidence={
            "trustworthyNoWork": True,
            "freshRetryAllowed": True,
            "budgetExhausted": False,
            "retryRemaining": 2,
        },
        reason="Automatic recovery test",
        next_action="fresh_retry",
        service=service,
        claim_store=store,
        claim_receipt=receipt,
    )
    # Bookkeeping stayed unfinished and remains retryable...
    assert result["released"] is False
    assert result["pendingSync"] or result["reasonCode"]
    # ...but the reservation is gone, so the issue is not held hostage.
    assert result["ownershipEnded"] is True
    assert (await store.get(owner)).ownership_ended is True
    successor = await store.prepare(
        owner="other/successor",
        repository=REPOSITORY,
        issue_number=ISSUE,
        attempt_id="att-successor-local",
        actor_id="456",
        comment_body="successor",
    )
    assert successor.owner == "other/successor"


# -- Version-1 backlog migration -------------------------------------------


def test_legacy_reservation_blocks_until_the_operator_declares_a_cutover():
    parsed = parse_attempt_comment(_legacy_comment()["body"])
    comment = _legacy_comment()
    now = CUTOVER + timedelta(days=1)
    assert (
        leases.reservation_status(parsed, comment, now=now, legacy_cutover=None)
        == leases.RESERVATION_LEGACY_PENDING
    )
    assert leases.blocks_new_work(
        leases.reservation_status(parsed, comment, now=now, legacy_cutover=None)
    )
    assert (
        leases.reservation_status(parsed, comment, now=now, legacy_cutover=CUTOVER)
        == leases.RESERVATION_LEGACY_RETIRED
    )
    # A claim announced after the cutover is not covered by it.
    fresh = {**comment, "created_at": "2026-09-15T00:00:00Z"}
    assert (
        leases.reservation_status(parsed, fresh, now=now, legacy_cutover=CUTOVER)
        == leases.RESERVATION_LEGACY_PENDING
    )


@pytest.mark.asyncio
async def test_migrated_legacy_comment_stops_reserving_but_stays_history(journey, monkeypatch):
    state, service, sessions = journey
    state["comments"] = [_legacy_comment()]
    original_body = state["comments"][0]["body"]
    issue = {"number": ISSUE, "labels": [{"name": "status: in-progress"}], "title": "t"}
    plan = plan_issue_recovery(
        repository=REPOSITORY,
        issue=issue,
        comments=state["comments"],
        cutover=CUTOVER,
        actor_id="123",
        now=CUTOVER + timedelta(days=1),
    )
    assert [item["action"] for item in plan["attempts"]] == [ACTION_RETIRE]
    assert plan["plannedWrites"] == 1
    outcome = await apply_issue_recovery(service=service, plan=plan, cutover=CUTOVER)
    assert outcome["applied"] == 1

    migrated = state["comments"][0]
    parsed = parse_attempt_comment(migrated["body"])
    # History is preserved: same attempt, same workflow, same lineage.
    assert parsed.attempt_id == "att-stranded-legacy"
    assert parsed.handoff.workflow_id.startswith("default/mm:")
    # No writer, completion, or no-work claim is invented.
    assert parsed.handoff.writers_stopped is False
    assert parsed.handoff.outcome == "in_progress"
    assert "operator-declared" in migrated["body"]
    assert original_body != migrated["body"]
    # And it no longer reserves the issue for anyone.
    assert (
        leases.reservation_status(
            parsed, migrated, now=CUTOVER + timedelta(days=1), legacy_cutover=CUTOVER
        )
        == leases.RESERVATION_EXPIRED
    )


def test_migration_preserves_work_and_never_touches_live_or_held_reservations():
    now = CUTOVER + timedelta(days=1)
    live = _legacy_comment(attemptId="att-live")
    live["id"] = 92
    live["body"] = render_attempt_comment(
        leases.with_lease(
            parse_attempt_comment(live["body"]).handoff, now=now
        )
    )
    held = _legacy_comment(attemptId="att-held", operatorHold=True)
    held["id"] = 93
    with_work = _legacy_comment(
        attemptId="att-partial", prUrl="https://github.com/example/repo/pull/7"
    )
    with_work["id"] = 94
    issue = {"number": ISSUE, "labels": [], "title": "t"}

    held_plan = plan_issue_recovery(
        repository=REPOSITORY,
        issue=issue,
        comments=[held, with_work],
        cutover=CUTOVER,
        actor_id="123",
        now=now,
    )
    # An explicit hold suppresses every write on that issue.
    assert held_plan["plannedWrites"] == 0
    assert held_plan["attempts"][0]["action"] == ACTION_OPERATOR_HOLD

    live_plan = plan_issue_recovery(
        repository=REPOSITORY,
        issue=issue,
        comments=[live, with_work],
        cutover=CUTOVER,
        actor_id="123",
        now=now,
    )
    assert live_plan["plannedWrites"] == 0
    assert live_plan["attempts"][0]["action"] == ACTION_RETAIN_LIVE

    alone = plan_issue_recovery(
        repository=REPOSITORY,
        issue=issue,
        comments=[with_work],
        cutover=CUTOVER,
        actor_id="123",
        now=now,
    )
    assert alone["attempts"][0]["action"] == ACTION_PRESERVE
    assert alone["nextAction"] == "continuation"
    assert alone["attempts"][0]["prUrl"].endswith("/pull/7")
