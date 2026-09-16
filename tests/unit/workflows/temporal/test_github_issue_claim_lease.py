"""Independent consumers communicate through the real GitHub HTTP boundary only."""

# ruff: noqa: F811 -- imported pytest fixture

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import GitHubIssueClaim
from moonmind.workflows.temporal import github_issue_claim_lease as leases
from moonmind.workflows.temporal import story_output_tools as tools
from moonmind.workflows.temporal.github_issue_attempts import (
    parse_attempt_comment,
    render_attempt_comment,
)
from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore, verify_claim
from tests.unit.workflows.temporal.test_issue_claim_journey import journey  # noqa: F401

# The import registers the shared fixture; the alias keeps import linters that
# do not model pytest fixture injection from flagging the registration.
_JOURNEY_FIXTURE = journey  # noqa: F841 -- referenced below to keep fixture registration explicit
assert _JOURNEY_FIXTURE is journey


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["expired", "live", "legacy", "hold", "read_failure"])
async def test_foreign_claim_requires_only_github_and_expired_owner_cannot_resume(
    journey, monkeypatch, tmp_path, fault
):
    state, service, sessions_a = journey
    store_a = IssueClaimStore(sessions_a)
    await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueSearch": ""},
        {"execution_owner": "default/deployment-a"},
        github_service_factory=lambda: service,
    )
    original = await store_a.get("default/deployment-a")
    handoff = parse_attempt_comment(original.comment_body).handoff
    assert handoff.lease_expires_at
    if fault in {"legacy", "hold"}:
        handoff = (
            replace(handoff, lease_renewed_at="", lease_expires_at="")
            if fault == "legacy"
            else replace(handoff, operator_hold=True)
        )
        body = render_attempt_comment(handoff)
        async with store_a.locked(original.owner) as row:
            row.comment_body = body
        state["comments"][0]["body"] = body
    original_body = state["comments"][0]["body"]
    base = leases.utc_now()
    # An announced-but-not-started reservation carries the five-minute
    # preparing deadline, so "live" is minutes old and "expired" is past it.
    monkeypatch.setattr(
        leases,
        "utc_now",
        lambda: base + timedelta(minutes=31 if fault != "live" else 2),
    )
    if fault == "read_failure":
        state["failed_read_path"] = "/repos/example/repo/issues/3970/comments"
    engine_b = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'independent-b.db'}"
    )
    async with engine_b.begin() as conn:
        await conn.run_sync(GitHubIssueClaim.__table__.create)
    sessions_b = async_sessionmaker(engine_b, expire_on_commit=False)
    store_b = IssueClaimStore(sessions_b)
    monkeypatch.setattr(tools, "IssueClaimStore", lambda: store_b)
    state["actor"] = {"id": 456, "login": "independent-consumer"}

    async def inaccessible(*args, **kwargs):
        raise AssertionError("Deployment B must not query A's Temporal or runtime")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.get_temporal_client", inaccessible
    )
    try:
        result = await tools.load_github_issue_preset_brief(
            {"repository": "example/repo", "issueSearch": ""},
            {"execution_owner": "other-namespace/deployment-b"},
            github_service_factory=lambda: service,
        )
        assert state["comments"][0]["body"] == original_body
        if fault != "expired":
            assert (
                result.completion_disposition == "idle" or result.status == "FAILED"
            ), result.outputs
            assert await store_b.get("other-namespace/deployment-b") is None
            return
        assert (
            result.status == "COMPLETED" and result.completion_disposition != "idle"
        ), result.outputs
        successor = parse_attempt_comment(
            (await store_b.get("other-namespace/deployment-b")).comment_body
        ).handoff
        assert successor.predecessor_attempt_id == original.attempt_id
        assert successor.retry_remaining == 2
        assert "Read the retained attempt history" in result.outputs["presetBrief"]
        assert state["labels"] == ["status: in-progress"]
        with pytest.raises(ValueError, match="claim_lease_expired"):
            await verify_claim(await store_a.get(original.owner), service)
        with pytest.raises(
            ValueError, match="claim_lease_lost|active_attempt_conflict"
        ):
            await leases.renew_owned_claim(
                store=store_a, service=service, owner=original.owner
            )
        assert len(state["comments"]) == 2
        engine_c = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'independent-c.db'}"
        )
        try:
            async with engine_c.begin() as conn:
                await conn.run_sync(GitHubIssueClaim.__table__.create)
            store_c = IssueClaimStore(
                async_sessionmaker(engine_c, expire_on_commit=False)
            )
            monkeypatch.setattr(tools, "IssueClaimStore", lambda: store_c)
            monkeypatch.setattr(leases, "utc_now", lambda: base + timedelta(minutes=62))
            # The successor announced at +31 and never renewed; its own
            # preparing deadline has passed by now.
            state["actor"] = {"id": 789, "login": "third-consumer"}
            third = await tools.load_github_issue_preset_brief(
                {"repository": "example/repo", "issueSearch": ""},
                {"execution_owner": "third/consumer-c"},
                github_service_factory=lambda: service,
            )
            assert (
                third.status == "COMPLETED" and third.completion_disposition != "idle"
            ), third.outputs
            final = parse_attempt_comment(
                (await store_c.get("third/consumer-c")).comment_body
            ).handoff
            assert final.retry_remaining == 1
            assert final.predecessor_attempt_id == successor.attempt_id
            assert (
                len(state["comments"]) == 3
                and state["comments"][0]["body"] == original_body
            )
        finally:
            await engine_c.dispose()
    finally:
        await engine_b.dispose()


@pytest.mark.asyncio
async def test_renewal_is_coalesced_and_lost_ack_is_confirmed(journey, monkeypatch):
    state, service, sessions = journey
    store = IssueClaimStore(sessions)
    owner = "default/renewing"
    await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueSearch": ""},
        {"execution_owner": owner},
        github_service_factory=lambda: service,
    )
    before = await store.get(owner)
    before_handoff = parse_attempt_comment(before.comment_body).handoff
    assert before_handoff.activity == "preparing"
    # Execution renewal promotes the announcement to the running lease so a
    # multi-minute run is not canceled before its first renewal.
    promoted = await leases.renew_owned_claim(
        store=store, service=service, owner=owner
    )
    promoted_handoff = parse_attempt_comment(promoted.comment_body).handoff
    assert promoted_handoff.activity == "active"
    assert leases.parse_time(
        promoted_handoff.lease_expires_at
    ) - leases.parse_time(promoted_handoff.lease_renewed_at) == timedelta(minutes=30)
    base = leases.utc_now()
    # Past the running renew interval but inside its thirty-minute deadline.
    monkeypatch.setattr(leases, "utc_now", lambda: base + timedelta(minutes=6))
    state["lose_update_ack"] = True
    renewed = await leases.renew_owned_claim(store=store, service=service, owner=owner)
    assert not state["lose_update_ack"]
    assert leases.parse_time(
        parse_attempt_comment(renewed.comment_body).handoff.lease_expires_at
    ) > leases.parse_time(promoted_handoff.lease_expires_at)
    assert len(state["comments"]) == 1 and renewed.pending_comment_body is None


@pytest.mark.asyncio
async def test_resume_confirms_durable_pending_renewal_after_old_deadline(
    journey, monkeypatch
):
    state, service, sessions = journey
    owner = "default/interrupted-renewal"
    store = IssueClaimStore(sessions)
    inputs = {"repository": "example/repo", "issueNumber": 3970}
    await tools.load_github_issue_preset_brief(
        inputs, {"execution_owner": owner}, github_service_factory=lambda: service
    )
    original = await store.get(owner)
    base = leases.utc_now()
    monkeypatch.setattr(leases, "utc_now", lambda: base + timedelta(minutes=2))
    update = service.update_issue_comment

    async def lose_readback(**kwargs):
        result = await update(**kwargs)
        state["failed_read_path"] = "/repos/example/repo/issues/3970/comments"
        return result

    monkeypatch.setattr(service, "update_issue_comment", lose_readback)
    with pytest.raises(ValueError, match="claim_read_failure"):
        await leases.renew_owned_claim(store=store, service=service, owner=owner)
    assert (await store.get(owner)).pending_comment_body
    state.pop("failed_read_path")
    monkeypatch.setattr(leases, "utc_now", lambda: base + timedelta(minutes=6))
    assert leases.expired(parse_attempt_comment(original.comment_body).handoff)
    result = await tools.load_github_issue_preset_brief(
        inputs, {"execution_owner": owner}, github_service_factory=lambda: service
    )
    assert result.status == "COMPLETED", result.outputs
    current = await store.get(owner)
    assert current.attempt_id == original.attempt_id
    assert not current.pending_comment_body and not leases.expired(
        parse_attempt_comment(current.comment_body).handoff
    )
    assert state["posts"] == 1


@pytest.mark.asyncio
async def test_bounded_scan_reaches_expired_claim_beyond_first_page(
    journey, monkeypatch, tmp_path
):
    from unittest.mock import AsyncMock

    from moonmind.workflows.temporal import github_issue_reconciliation as policy
    from moonmind.workflows.temporal.activities.github_issue_reconciliation_activities import (
        reconcile_github_issue_handoffs,
    )

    state, service, _ = journey
    await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueNumber": 3970},
        {"execution_owner": "default/tail-claim"},
        github_service_factory=lambda: service,
    )
    original_body = state["comments"][0]["body"]
    now = leases.utc_now()
    monkeypatch.setattr(leases, "utc_now", lambda: now + timedelta(minutes=31))
    monkeypatch.setattr(policy, "MAX_SCAN_PER_PAGE", 2)
    monkeypatch.setattr(
        service, "probe_token", AsyncMock(return_value={"repositoryAccessible": True})
    )
    state["scan_entries"] = [
        {"number": number, "state": "open", "labels": []}
        for number in range(3966, 3971)
    ]
    examined = []
    for _ in range(8):
        result = await reconcile_github_issue_handoffs(
            repository="example/repo",
            max_pages=1,
            max_issues=1,
            service=service,
            state_dir=tmp_path,
        )
        assert len(result["results"]) <= 1
        examined.extend(item["issueNumber"] for item in result["results"])
        if not state["labels"]:
            break
    assert examined == list(range(3966, 3971))
    assert state["labels"] == [] and state["comments"][0]["body"] == original_body
