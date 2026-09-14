"""Escaped failed recurring claim through API schedule install and its real owner."""

# ruff: noqa: F811 -- imported pytest fixture

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.client import (
    ScheduleIntervalSpec,
    ScheduleSpec,
    ScheduleUpdate,
    WorkflowFailureError,
)
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal import client as clients
from moonmind.workflows.temporal import github_issue_claim_recovery as recovery
from moonmind.workflows.temporal import story_output_tools as tools
from moonmind.workflows.temporal.activity_runtime import TemporalIntegrationActivities
from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore
from moonmind.workflows.temporal.workflows import github_issue_reconcile as scheduled
from tests.integration.reliability.test_resolver_verification_capability_journey import (
    resolver_test_client,
)
from tests.unit.omnigent.test_claim_recovery_workspace import (
    capture_saved_workspace,
    comparison_from_git,
)
from tests.unit.workflows.temporal.test_github_issue_claim_recovery import (
    persist_saved_binding,
)
from tests.unit.workflows.temporal.test_issue_claim_journey import journey  # noqa: F401

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn(name="MoonMind.AgentRun")
class CapturedAssessment:
    @workflow.run
    async def run(self):
        await workflow.execute_activity(
            "fixture.capture", start_to_close_timeout=timedelta(seconds=60)
        )


@workflow.defn
class FailedRecurringClaim:
    @workflow.run
    async def run(self, with_agent: bool):
        await workflow.execute_activity(
            "fixture.claim", start_to_close_timeout=timedelta(seconds=30)
        )
        if with_agent:
            await workflow.execute_child_workflow(
                CapturedAssessment.run, id=workflow.info().workflow_id + ":agent"
            )
        raise ApplicationError(
            "Replay: failed after accepting an issue", non_retryable=True
        )


@pytest.mark.parametrize("journey", ["postgres"], indirect=True)
@pytest.mark.parametrize(
    "with_agent,startup_outage,foreign", [(False, False, False), (True, False, False), (True, True, False), (False, False, True)]
)
async def test_startup_installs_automatic_recovery_and_restart_releases_claim(
    journey, monkeypatch, tmp_path, with_agent, startup_outage, foreign
):
    from api_service import main as api_main
    from fastapi import FastAPI

    # Isolate lifespan-owned task handles from other tests' application/event
    # loops; startup and shutdown still use the production lifespan owner.
    monkeypatch.setattr(api_main, "app", FastAPI())

    state, service, sessions = journey
    client = await resolver_test_client(
        additional_search_attributes={
            "SessionStatus": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
        }
    )
    from temporalio.api.operatorservice.v1 import (
        AddSearchAttributesRequest,
        ListSearchAttributesRequest,
    )

    attributes = await client.operator_service.list_search_attributes(
        ListSearchAttributesRequest(namespace=client.namespace)
    )
    if "IsDegraded" not in attributes.custom_attributes:
        await client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                namespace=client.namespace,
                search_attributes={
                    "IsDegraded": IndexedValueType.INDEXED_VALUE_TYPE_BOOL
                },
            )
        )
    queue = "claim-recovery-" + uuid4().hex
    store = IssueClaimStore(sessions)
    monkeypatch.setattr(recovery, "IssueClaimStore", lambda: store)
    monkeypatch.setattr(clients, "get_temporal_client", AsyncMock(return_value=client))
    monkeypatch.setattr(
        clients.TemporalClientAdapter, "get_client", AsyncMock(return_value=client)
    )
    monkeypatch.setattr(
        clients.TemporalClientAdapter, "_get_task_queue", lambda self: queue
    )
    monkeypatch.setattr(clients, "GITHUB_ISSUE_RECONCILE_SCHEDULE_ID", queue)
    monkeypatch.setattr(
        clients, "GITHUB_ISSUE_RECONCILE_WORKFLOW_ID_BASE", queue + ":maintenance"
    )
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.GitHubService", lambda: service
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.activities.github_issue_reconciliation_activities._state_dir",
        lambda _: tmp_path,
    )
    assert recovery.CLEANUP_GRACE == timedelta(minutes=5)
    monkeypatch.setattr(recovery, "CLEANUP_GRACE", timedelta(0))
    route = scheduled.DEFAULT_ACTIVITY_CATALOG.resolve_activity(
        "github_issue.reconcile_handoffs"
    )
    monkeypatch.setattr(
        scheduled,
        "DEFAULT_ACTIVITY_CATALOG",
        SimpleNamespace(resolve_activity=lambda _: replace(route, task_queue=queue)),
    )

    @activity.defn(name="fixture.claim")
    async def claim():
        result = await tools.load_github_issue_preset_brief(
            {"repository": "example/repo", "issueSearch": ""},
            github_service_factory=lambda: service,
        )
        assert result.status == "COMPLETED", result.outputs

    @activity.defn(name="fixture.capture")
    async def capture():
        info = activity.info()
        saved, _, workspace, _ = await capture_saved_workspace(
            tmp_path,
            monkeypatch,
            workflow_id=info.workflow_id,
            run_id=info.workflow_run_id,
        )
        state["comparison_provider"] = comparison_from_git(workspace)
        state["compare_failures_remaining"] = 1
        await persist_saved_binding(
            sessions,
            agent_id=info.workflow_id,
            run_id=info.workflow_run_id,
            saved=saved,
        )

    recovered = asyncio.Event()
    observed = asyncio.Event()

    @activity.defn(name="github_issue.reconcile_handoffs")
    async def reconcile(payload: dict):
        result = await TemporalIntegrationActivities().github_issue_reconcile_handoffs(
            payload
        )
        observed.set()
        if result["localClaims"]["released"] or any(
            item.get("reasonCode") == "lease_expired"
            for repository in result.get("githubRepositories", [])
            for item in repository.get("results", [])
        ):
            recovered.set()
        return result

    worker_options = {
        "task_queue": queue,
        "workflow_runner": UnsandboxedWorkflowRunner(),
    }
    async with Worker(
        client,
        workflows=[FailedRecurringClaim, CapturedAssessment],
        activities=[claim, capture],
        **worker_options,
    ):
        with pytest.raises(WorkflowFailureError):
            await client.execute_workflow(
                FailedRecurringClaim.run, with_agent, id=queue, task_queue=queue
            )
    receipt = await store.get("default/" + queue)
    assert receipt and not receipt.released
    state["lose_release_ack"] = True
    # Worker replacement: no original workflow process participates in release.
    async with (
        AsyncExitStack() as stack,
        Worker(
            client,
            workflows=[scheduled.MoonMindGitHubIssueReconcileWorkflow],
            activities=[reconcile],
            **worker_options,
        ),
    ):
        if foreign:
            from api_service.db.models import GitHubIssueClaim
            from moonmind.config.settings import settings
            from moonmind.workflows.temporal import github_issue_claim_lease as leases
            from tests.support.isolated_postgres import isolated_postgres

            foreign_sessions = await stack.enter_async_context(isolated_postgres([GitHubIssueClaim.__table__]))
            foreign_store = IssueClaimStore(foreign_sessions)
            monkeypatch.setattr(recovery, "IssueClaimStore", lambda: foreign_store)
            monkeypatch.setattr(tools, "IssueClaimStore", lambda: foreign_store)
            monkeypatch.setattr(recovery, "_closed_execution_tree", AsyncMock(side_effect=AssertionError("Foreign runtime visibility is forbidden")))
            monkeypatch.setattr(settings.workflow, "github_repository", "example/repo")
            monkeypatch.setattr(service, "probe_token", AsyncMock(return_value={"repositoryAccessible": True}))
            now = leases.utc_now()
            monkeypatch.setattr(leases, "utc_now", lambda: now + timedelta(minutes=10))
            state["actor"] = {"id": 456, "login": "independent-consumer"}
            foreign_original = state["comments"][0]["body"]

        @asynccontextmanager
        async def database():
            async with sessions() as session:
                yield session

        monkeypatch.setattr("api_service.db.base.get_async_session_context", database)
        monkeypatch.setattr(
            "api_service.services.recurring_workflows_service.RecurringWorkflowsService.reconcile_schedules",
            AsyncMock(return_value=0),
        )
        if startup_outage:
            ensure = (
                clients.TemporalClientAdapter.ensure_github_issue_reconcile_schedule
            )
            installed = asyncio.Event()
            calls = 0

            async def transient_startup_failure(adapter, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError(
                        "Temporal temporarily unavailable during API startup"
                    )
                result = await ensure(adapter, **kwargs)
                installed.set()
                return result

            monkeypatch.setattr(
                clients.TemporalClientAdapter,
                "ensure_github_issue_reconcile_schedule",
                transient_startup_failure,
            )
            monkeypatch.setattr(
                api_main,
                "startup_event",
                api_main.ensure_recurring_workflow_schedules_reconciled,
            )
            monkeypatch.setattr(api_main, "get_async_session_context", database)
            monkeypatch.setattr(
                "api_service.services.recurring_workflows_service.RecurringWorkflowsService.reconcile_manual_runs",
                AsyncMock(return_value=0),
            )
            monkeypatch.setenv("OMNIGENT_ENABLED", "false")
            await stack.enter_async_context(api_main.lifespan(api_main.app))
            await asyncio.wait_for(installed.wait(), timeout=10)
            assert (
                calls == 2 and api_main.app.state.github_claim_recovery_schedule_ready
            )
        else:
            await api_main.ensure_recurring_workflow_schedules_reconciled()
        handle = client.get_schedule_handle(queue)
        try:
            description = await handle.describe()
            assert not description.schedule.state.paused
            assert description.schedule.spec.calendars[0].minute[0].step == 5

            # Temporal itself launches the production recovery owner; only
            # the test schedule cadence is shortened to avoid a five-minute wait.
            async def faster(update):
                update.description.schedule.spec = ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(every=timedelta(seconds=2))]
                )
                return ScheduleUpdate(schedule=update.description.schedule)

            await handle.update(faster)
            if foreign:
                await asyncio.wait_for(observed.wait(), timeout=45)
                assert state["labels"] == ["status: in-progress"]
                assert state["comments"][0]["body"] == foreign_original
                assert not recovered.is_set()
                monkeypatch.setattr(leases, "utc_now", lambda: now + timedelta(minutes=31))
            await asyncio.wait_for(recovered.wait(), timeout=45)
            assert (await store.get(receipt.owner)).released is not foreign
            if foreign:
                assert state["comments"][0]["body"] == foreign_original
                assert await foreign_store.get(receipt.owner) is None
                recovery._closed_execution_tree.assert_not_awaited()
            assert state["labels"] == [] and state["posts"] == 1
            result = await tools.load_github_issue_preset_brief(
                {"repository": "example/repo", "issueSearch": ""},
                {"execution_owner": "default/after-recovery"},
                github_service_factory=lambda: service,
            )
            assert (
                result.status == "COMPLETED" and result.completion_disposition != "idle"
            ), result.outputs
        finally:
            await handle.delete()
