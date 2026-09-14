"""Two workers race the default selector through real PostgreSQL and HTTP."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from temporalio import activity, workflow
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db.models import GitHubIssueClaim
from moonmind.workflows.temporal import story_output_tools as tools
from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore
from tests.integration.reliability.test_release_routing_journey import connect
from tests.unit.workflows.temporal.test_issue_claim_journey import journey  # noqa: F401
from tests.unit.workflows.temporal.test_issue_claim_journey import (
    test_failed_brief_reads_release_only_unannounced_reservations as run_read_failure_journey,
    test_failed_finalization_releases_durable_claim_after_remote_confirmation as run_finalization_journey,
    test_search_skips_remote_contender_before_authorizing_announcement as run_remote_contender_journey,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize("journey", ["postgres"], indirect=True)
@pytest.mark.parametrize("fault", ["lose_release_ack", "reject_release"])
async def test_release_receipts_survive_unknown_github_effects(journey, fault):
    await run_finalization_journey(journey, fault)


@pytest.mark.parametrize("journey", ["postgres"], indirect=True)
@pytest.mark.parametrize("when", ["before_selection", "after_reservation", "before_locked_check"])
@pytest.mark.parametrize("eligible_successor", [False, True])
async def test_recurring_search_preserves_remote_owner_without_stranding_claim(
    journey, monkeypatch, when, eligible_successor
):
    await run_remote_contender_journey(journey, monkeypatch, when, eligible_successor)


@pytest.mark.parametrize("journey", ["postgres"], indirect=True)
async def test_concurrent_default_search_claims_one_owner_and_leaves_other_idle(
    journey,
):
    state, service, sessions = journey
    state["lost_ack"] = True
    inputs = {"repository": "example/repo", "issueSearch": ""}
    owners = ["default/worker-a", "default/worker-b"]
    # Both candidates are read before either reservation, so this exercises
    # the database uniqueness boundary rather than only advisory labels.
    ready = asyncio.Event()
    arrivals = 0
    actor = service.issue_claim_actor

    async def simultaneous_actor(**kwargs):
        nonlocal arrivals
        result = await actor(**kwargs)
        arrivals += 1
        if arrivals == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 10)
        return result

    service.issue_claim_actor = simultaneous_actor
    results = await asyncio.gather(
        *(
            tools.load_github_issue_preset_brief(
                inputs,
                {"execution_owner": owner},
                github_service_factory=lambda: service,
            )
            for owner in owners
        )
    )
    assert all(result.status == "COMPLETED" for result in results), [
        r.outputs for r in results
    ]
    assert sum(result.completion_disposition == "idle" for result in results) == 1
    assert state["posts"] == 1
    async with sessions() as session:
        claims = (await session.execute(select(GitHubIssueClaim))).scalars().all()
    assert len(claims) == 1 and claims[0].confirmed
    owner = claims[0].owner
    resumed = await tools.load_github_issue_preset_brief(
        inputs, {"execution_owner": owner}, github_service_factory=lambda: service
    )
    assert resumed.status == "COMPLETED", resumed.outputs
    assert state["posts"] == 1
    assert (
        resumed.outputs["attemptId"]
        == (await IssueClaimStore(sessions).get(owner)).attempt_id
    )


@pytest.mark.parametrize("journey", ["postgres"], indirect=True)
@pytest.mark.parametrize("lose_ack", [False, True])
@pytest.mark.parametrize("cancel_retry", [False, True])
async def test_same_owner_retry_cannot_cross_committed_intent_lock(
    journey, monkeypatch, lose_ack, cancel_retry
):
    state, service, sessions = journey
    state["lost_ack"] = lose_ack
    inputs = {"repository": "example/repo", "issueNumber": 3970}
    owner = "default/retried-announcement"
    post_ready, allow_post, retry_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    create = service.create_issue_comment
    locked = IssueClaimStore.locked

    @asynccontextmanager
    async def observe_retry(store, locked_owner):
        if post_ready.is_set():
            retry_waiting.set()
        async with locked(store, locked_owner) as row:
            yield row

    async def paused_create(**kwargs):
        # This read uses an independent connection: intent must already be
        # durable while another invocation still cannot enter the write region.
        receipt = await IssueClaimStore(sessions).get(owner)
        assert receipt.announcement_started and receipt.comment_id is None
        assert not await IssueClaimStore(sessions).abandon_unannounced(
            owner, receipt.attempt_id
        )
        post_ready.set()
        await asyncio.wait_for(allow_post.wait(), 10)
        return await create(**kwargs)

    monkeypatch.setattr(IssueClaimStore, "locked", observe_retry)
    service.create_issue_comment = paused_create

    async def execute():
        return await tools.load_github_issue_preset_brief(
            inputs, {"execution_owner": owner}, github_service_factory=lambda: service
        )

    first = asyncio.create_task(execute())
    second = None
    try:
        await asyncio.wait_for(post_ready.wait(), 10)
        second = asyncio.create_task(execute())
        await asyncio.wait_for(retry_waiting.wait(), 10)
        # Observe actual database contention before releasing the first POST.
        # An event before lock acquisition alone would permit a false pass.
        async with asyncio.timeout(5):
            async with sessions() as session:
                while not await session.scalar(text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks, "
                    "(SELECT hashtextextended(:owner, 0) AS key) AS claim "
                    "WHERE locktype = 'advisory' AND NOT granted "
                    "AND classid = ((key >> 32) & 4294967295)::oid "
                    "AND objid = (key & 4294967295)::oid)"
                ), {"owner": f"moonmind:issue-claim:{owner}"}):
                    await asyncio.sleep(0.02)
        if cancel_retry:
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
        allow_post.set()
        results = await asyncio.wait_for(
            asyncio.gather(first) if cancel_retry else asyncio.gather(first, second), 15
        )
        assert all(result.status == "COMPLETED" for result in results), results
        assert state["posts"] == 1
        assert (await IssueClaimStore(sessions).get(owner)).confirmed
        # An exception must also return a connection without a leaked lock.
        with pytest.raises(RuntimeError, match="fixture interruption"):
            async with IssueClaimStore(sessions).locked(owner):
                raise RuntimeError("fixture interruption")
        async with asyncio.timeout(5):
            async with IssueClaimStore(sessions).locked(owner) as row:
                assert row.confirmed
    finally:
        allow_post.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task), return_exceptions=True)


@workflow.defn
class ClaimChild:
    @workflow.run
    async def run(self, inputs: dict) -> dict:
        return await workflow.execute_activity(
            "claim.start", inputs, start_to_close_timeout=timedelta(seconds=30)
        )


@workflow.defn
class ClaimParent:
    @workflow.run
    async def run(self, inputs: dict) -> dict:
        claimed = await workflow.execute_activity(
            "claim.acquire", inputs, start_to_close_timeout=timedelta(seconds=30)
        )
        assert claimed["status"] == "COMPLETED", claimed
        child = await workflow.execute_child_workflow(
            ClaimChild.run,
            {
                **inputs,
                "attemptId": claimed["outputs"]["attemptId"],
                "childKind": "internal_retry",
            },
            id=workflow.info().workflow_id + "/child",
        )
        return {"parent": claimed, "child": child}


@pytest.mark.parametrize("journey", ["postgres"], indirect=True)
async def test_child_claim_inherits_only_server_recorded_parent(journey, monkeypatch):
    state, service, sessions = journey
    client = await connect()
    from moonmind.workflows.temporal import client as client_module

    async def selected_client(address, namespace):
        assert namespace == client.namespace
        return client

    monkeypatch.setattr(client_module, "get_temporal_client", selected_client)

    @activity.defn(name="claim.acquire")
    async def acquire(inputs: dict):
        result = await tools.load_github_issue_preset_brief(
            inputs, github_service_factory=lambda: service
        )
        return {"status": result.status, "outputs": result.outputs}

    @activity.defn(name="claim.start")
    async def start(inputs: dict):
        result = await tools.update_github_issue_status(
            inputs, github_service_factory=lambda: service
        )
        return {"status": result.status, "outputs": result.outputs}

    queue = "claim-ancestry-" + uuid4().hex
    async with Worker(
        client,
        task_queue=queue,
        workflows=[ClaimParent, ClaimChild],
        activities=[acquire, start],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        result = await client.execute_workflow(
            ClaimParent.run,
            {"repository": "example/repo", "issueNumber": 3970},
            id=queue,
            task_queue=queue,
            execution_timeout=timedelta(seconds=90),
        )
        assert result["child"]["status"] == "COMPLETED", result
        assert (
            result["child"]["outputs"]["attemptId"]
            == result["parent"]["outputs"]["attemptId"]
        )
        outsider = await client.execute_workflow(
            ClaimChild.run,
            {
                "repository": "example/repo",
                "issueNumber": 3970,
                "attemptId": result["parent"]["outputs"]["attemptId"],
            },
            id=queue + "/outsider",
            task_queue=queue,
            execution_timeout=timedelta(seconds=60),
        )
        assert outsider["status"] == "FAILED", outsider
    assert state["posts"] == 1
    async with sessions() as session:
        rows = (await session.execute(select(GitHubIssueClaim))).scalars().all()
    assert len(rows) == 1 and rows[0].owner == f"{client.namespace}/{queue}"


@pytest.mark.parametrize("journey", ["postgres"], indirect=True)
@pytest.mark.parametrize(
    "stage,read,post_authorized",
    [
        ("selection", "comments", False),
        ("selection", "prerequisite", False),
        ("resume", "comments", False),
        ("resume", "issue", False),
        ("resume", "comments", True),
        ("resume", "issue", True),
    ],
)
async def test_pre_mutation_read_failure_does_not_strand_postgres_claim(
    journey, monkeypatch, stage, read, post_authorized
):
    await run_read_failure_journey(journey, monkeypatch, stage, read, post_authorized)
