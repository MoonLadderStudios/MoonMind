"""GitHub claim deadlines through the public durable execution/maintenance owners.

Provider work is an isolated cancellable fixture. Claim admission, HTTP I/O,
PostgreSQL receipts, Activity dispatch, Temporal timers and replay are production.
"""

# ruff: noqa: F811 -- imported pytest fixture

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal import github_issue_claim_lease as leases
from moonmind.workflows.temporal import github_issue_lease_workflow as lease_workflow
from moonmind.workflows.temporal import story_output_tools as tools
from moonmind.workflows.temporal.activity_runtime import TemporalIntegrationActivities
from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore
from moonmind.workflows.temporal.workflows import agent_run
from tests.integration.reliability.test_resolver_verification_capability_journey import (
    resolver_test_client,
)
from tests.unit.workflows.temporal.test_issue_claim_journey import journey  # noqa: F401

# The import registers the shared fixture; the alias keeps import linters that
# do not model pytest fixture injection from flagging the registration.
_JOURNEY_FIXTURE = journey

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn
class ClaimedParent:
    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        return await workflow.execute_child_workflow(
            agent_run.MoonMindAgentRun.run,
            request,
            id=workflow.info().workflow_id + ":agent",
        )


@pytest.mark.parametrize("journey", ["postgres"], indirect=True)
@pytest.mark.parametrize("agent_kind", ["managed", "external"])
@pytest.mark.parametrize(
    "fault",
    [
        "outage",
        "worker_replacement",
        "expired_before_launch",
        "completion_during_renewal",
    ],
)
async def test_public_agent_run_renews_and_stops_at_confirmed_deadline(
    journey, monkeypatch, agent_kind, fault
):
    state, service, sessions = journey
    client = await resolver_test_client()
    queue = "lease-owner-" + uuid4().hex
    owner = client.namespace + "/" + queue
    store = IssueClaimStore(sessions)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.get_temporal_client",
        AsyncMock(return_value=client),
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.issue_claim_store.IssueClaimStore", lambda: store
    )
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.GitHubService", lambda: service
    )
    assert leases.LEASE_DURATION == timedelta(minutes=30)
    assert leases.RENEW_INTERVAL == timedelta(minutes=5)
    assert lease_workflow.RENEW_SECONDS == 300
    assert lease_workflow.STOP_MARGIN_SECONDS == 60
    # Shorten wall-clock deadlines only; retain real SQL, HTTP, and durable timers.
    monkeypatch.setattr(
        leases,
        "LEASE_DURATION",
        timedelta(seconds=10 if fault in {"outage", "expired_before_launch"} else 60),
    )
    monkeypatch.setattr(leases, "RENEW_INTERVAL", timedelta(seconds=0.4))
    monkeypatch.setattr(lease_workflow, "RENEW_SECONDS", 0.5)
    monkeypatch.setattr(lease_workflow, "STOP_MARGIN_SECONDS", 2)
    brief = await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueNumber": 3970},
        {"execution_owner": owner},
        github_service_factory=lambda: service,
    )
    assert brief.status == "COMPLETED", brief.outputs
    lease = brief.outputs["issueClaimLease"]
    assert lease == (await store.get(owner)).handoff()["issueClaimLease"]
    before = state["comments"][0]["body"]
    started, preserved = asyncio.Event(), asyncio.Event()
    renewal_pending = asyncio.Event()
    start_count = 0

    @activity.defn(name="fixture.start")
    async def start():
        nonlocal start_count
        start_count += 1
        started.set()

    @activity.defn(name="fixture.preserve")
    async def preserve():
        preserved.set()

    @activity.defn(name="fixture.await_renewal")
    async def await_renewal():
        await renewal_pending.wait()

    async def cancellable_execution(self, request):
        await workflow.execute_activity(
            "fixture.start", start_to_close_timeout=timedelta(seconds=10)
        )
        try:
            if fault == "completion_during_renewal":
                await workflow.execute_activity(
                    "fixture.await_renewal",
                    start_to_close_timeout=timedelta(seconds=10),
                )
                return AgentRunResult(summary="fixture completed")
            await workflow.sleep(4 if fault == "worker_replacement" else 60)
            return AgentRunResult(summary="fixture completed")
        except asyncio.CancelledError:
            await workflow.execute_activity(
                "fixture.preserve", start_to_close_timeout=timedelta(seconds=10)
            )
            raise

    monkeypatch.setattr(
        agent_run.MoonMindAgentRun, "_run_under_claim", cancellable_execution
    )
    route = agent_run.DEFAULT_ACTIVITY_CATALOG.resolve_activity(
        "github_issue.renew_claim"
    )
    monkeypatch.setattr(
        agent_run,
        "DEFAULT_ACTIVITY_CATALOG",
        SimpleNamespace(resolve_activity=lambda _: replace(route, task_queue=queue)),
    )

    renewal_calls = 0

    @activity.defn(name="github_issue.renew_claim")
    async def renew(payload: dict):
        nonlocal renewal_calls
        renewal_calls += 1
        result = await TemporalIntegrationActivities().github_issue_renew_claim(payload)
        if fault == "completion_during_renewal" and renewal_calls > 1:
            renewal_pending.set()
            await asyncio.sleep(60)  # acknowledgement is still in flight at completion
        return result

    request = AgentExecutionRequest(
        agentKind=agent_kind,
        agentId="codex" if agent_kind == "managed" else "jules",
        correlationId=queue,
        idempotencyKey=queue,
        parameters={"issueClaimLease": lease},
    )
    if fault == "expired_before_launch":
        now = leases.utc_now()
        monkeypatch.setattr(leases, "utc_now", lambda: now + timedelta(seconds=11))
    options = {
        "task_queue": queue,
        "workflow_runner": UnsandboxedWorkflowRunner(),
        "max_cached_workflows": 0,
        "workflows": [agent_run.MoonMindAgentRun, ClaimedParent],
        "activities": [start, preserve, renew, await_renewal],
    }
    async with Worker(client, **options):
        handle = await client.start_workflow(
            ClaimedParent.run, request, id=queue, task_queue=queue
        )
        if fault == "expired_before_launch":
            with pytest.raises(WorkflowFailureError, match="Workflow execution failed"):
                await asyncio.wait_for(handle.result(), 15)
            assert not started.is_set()
            return
        await asyncio.wait_for(started.wait(), 15)
        if fault == "completion_during_renewal":
            result = await asyncio.wait_for(handle.result(), 15)
            assert result.summary == "fixture completed" and not preserved.is_set()
            return
        if fault == "outage":
            state["failed_read_path"] = "/repos/example/repo/issues/3970/comments"
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), 20)
            assert preserved.is_set()
            # The pre-launch confirmation may legitimately renew the initial
            # claim before the outage. No terminal release is fabricated.
            assert len(state["comments"]) == 1
            assert not (
                await store.get(owner)
            ).released  # expiry never invents a terminal handoff
            return
        # Let at least one lease renewal occur, then replace the worker while
        # its durable execution timer is still outstanding.
        for _ in range(40):
            if state["comments"][0]["body"] != before:
                break
            await asyncio.sleep(0.05)
        assert state["comments"][0]["body"] != before
    async with Worker(client, **options):
        result = await asyncio.wait_for(handle.result(), 45)
    assert result.summary == "fixture completed"
    assert start_count == 1 and not preserved.is_set()
    assert state["posts"] == 1
