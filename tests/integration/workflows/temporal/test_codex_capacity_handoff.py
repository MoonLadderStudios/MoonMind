"""A starved execution queue must return an unused Codex profile slot."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.exceptions import ActivityError, TimeoutType
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db import base as db_base
from api_service.db.models import ProviderProfileSlotLease
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.omnigent_session_models import OmnigentSessionAdmissionDecision
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_TASK_QUEUE,
    ARTIFACTS_TASK_QUEUE,
    get_workflow_task_queue,
)
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    MoonMindProviderProfileManagerWorkflow,
)
from tests.integration.workflows.temporal.test_omnigent_session_supervisor import (
    _CodexCapacityActivities,
    _register_search_attributes,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.temporal_boundary,
]


@workflow.defn(name="Test.CodexCapacityHandoff")
class _CodexCapacityHandoffRun(MoonMindAgentRun):
    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> dict:
        admission = OmnigentSessionAdmissionDecision(
            admitted=True,
            reasonCode="enabled",
            admissionMode="enabled",
            executionRealizerRef="codex-profile-bound@1",
            capacityAcquisitionOwner="workflow",
            capacityProfiles=[
                {
                    "providerProfileRef": "provider-codex-native",
                    "providerRuntimeId": "codex_cli",
                    "credentialGeneration": 3,
                    "capacityScopeRef": "provider-profile:provider-codex-native",
                }
            ],
        )
        execution = (
            self._execute_profile_bound_with_remaining_budget
            if self._omnigent_uses_remaining_budget_retry(
                act_name="integration.omnigent.execute",
                admission=admission,
                admit_capacity_before_activity=True,
            )
            else self._execute_omnigent_with_admitted_capacity
        )
        try:
            result, _ = await execution(
                act_name="integration.omnigent.execute",
                request=request,
                admission=admission,
                parent_info=None,
                stc_seconds=600,
                admit_capacity_before_activity=True,
                execution_plan_admission=True,
            )
        except ActivityError as exc:
            return {"timeoutType": exc.cause.type.value}
        return result


class _HandoffCapacityActivities(_CodexCapacityActivities):
    def __init__(self, client, *, hold_first_release: bool = False):
        super().__init__(client)
        self.hold_first_release = hold_first_release
        self.first_release_started = asyncio.Event()
        self.allow_first_release = asyncio.Event()

    @activity.defn(name="provider_profile.sync_slot_leases")
    async def sync_slot_leases(self, payload: dict) -> dict:
        if (
            self.hold_first_release
            and payload.get("action") == "release_one"
            and not self.first_release_started.is_set()
        ):
            self.first_release_started.set()
            await self.allow_first_release.wait()
        return await super().sync_slot_leases(payload)

    @activity.defn(name="integration.omnigent.execute")
    async def execute(self, request: AgentExecutionRequest) -> dict:
        ticket = request.admitted_provider_capacity
        assert ticket is not None
        inspection = await self.client.get_workflow_handle(
            "provider-profile-manager:codex_cli"
        ).execute_update(
            "InspectCredentialLease",
            {"lease_id": ticket.lease_owner_id, "owner_id": ticket.lease_owner_id},
        )
        assert inspection["active"] is True
        assert inspection["purpose"] == "execution_omnigent"
        assert inspection["executionPlanRef"] == ticket.execution_plan_ref
        self.started.append(request)
        return AgentRunResult(
            summary="The next run acquired the returned slot",
            metadata={"admittedProviderCapacityCleanupCompleted": True},
        ).model_dump(mode="json", by_alias=True)


@pytest.mark.parametrize("hold_first_release", [False, True])
async def test_starved_codex_worker_returns_capacity_for_the_next_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hold_first_release: bool,
) -> None:
    monkeypatch.setattr(agent_run_module, "_OMNIGENT_EXECUTION_HANDOFF_SECONDS", 2)
    monkeypatch.setattr(agent_run_module, "_SLOT_WAIT_TIMEOUT_SECONDS", 1)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/codex-handoff.db")
    async with engine.begin() as conn:
        await conn.run_sync(ProviderProfileSlotLease.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_base, "async_session_maker", maker)
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="provider-codex-native",
        correlationId="codex-capacity-handoff",
        idempotencyKey="first-codex-handoff",
        instructionRef="artifact:handoff-instructions",
        parameters={"executionPlanRef": "omnigent-execution-plan:sha256:" + "d" * 64},
    )

    try:
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            await _register_search_attributes(env)
            capacity = _HandoffCapacityActivities(
                env.client,
                hold_first_release=hold_first_release,
            )
            workflow_queue = get_workflow_task_queue()
            with env.auto_time_skipping_disabled():
                async with (
                    Worker(
                        env.client,
                        task_queue=workflow_queue,
                        workflows=[
                            _CodexCapacityHandoffRun,
                            MoonMindProviderProfileManagerWorkflow,
                        ],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    ),
                    Worker(
                        env.client,
                        task_queue=ARTIFACTS_TASK_QUEUE,
                        activities=[
                            capacity.list_profiles,
                            capacity.sync_slot_leases,
                            capacity.pending_request_order,
                            capacity.verify_holders,
                            capacity.manager_state,
                        ],
                    ),
                ):
                    manager = await env.client.start_workflow(
                        MoonMindProviderProfileManagerWorkflow.run,
                        {"runtime_id": "codex_cli"},
                        id="provider-profile-manager:codex_cli",
                        task_queue=workflow_queue,
                    )
                    first = await env.client.start_workflow(
                        _CodexCapacityHandoffRun.run,
                        request,
                        id=f"starved-codex-{uuid4()}",
                        task_queue=workflow_queue,
                    )
                    # There is deliberately no execution worker. Only Temporal
                    # can decide that each single-shot delivery never started.
                    if hold_first_release:
                        await asyncio.wait_for(
                            capacity.first_release_started.wait(), 15
                        )
                        try:
                            async with asyncio.timeout(15):
                                while True:
                                    state = await manager.query("get_state")
                                    blocked_history = await first.fetch_history()
                                    scheduled = [
                                        event
                                        for event in blocked_history.events
                                        if event.event_type
                                        == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                                        and event.activity_task_scheduled_event_attributes.activity_type.name
                                        == "integration.omnigent.execute"
                                    ]
                                    assert len(scheduled) == 1, (
                                        "The retry inherited a lease before its durable "
                                        "release completed"
                                    )
                                    if state["pending_requests_ordered"] and any(
                                        item["requester_workflow_id"] == first.id
                                        for item in state["pending_requests"]
                                    ):
                                        break
                                    await asyncio.sleep(0.01)
                        finally:
                            capacity.allow_first_release.set()
                    try:
                        first_result = await asyncio.wait_for(first.result(), 30)
                    except TimeoutError:
                        history = await first.fetch_history()
                        pytest.fail(
                            "Starved execution did not reach its handoff timeout: "
                            + repr(
                                [
                                    (
                                        event.event_id,
                                        EventType.Name(event.event_type),
                                        event.activity_task_scheduled_event_attributes.activity_type.name,
                                    )
                                    for event in history.events[-15:]
                                ]
                            )
                            + repr(await manager.query("get_state"))
                        )
                    assert first_result == {
                        "timeoutType": TimeoutType.SCHEDULE_TO_START.value
                    }
                    history = await first.fetch_history()
                    deliveries = [
                        event.activity_task_scheduled_event_attributes
                        for event in history.events
                        if event.event_type
                        == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                        and event.activity_task_scheduled_event_attributes.activity_type.name
                        == "integration.omnigent.execute"
                    ]
                    assert len(deliveries) == 2
                    assert all(
                        item.retry_policy.maximum_attempts == 1 for item in deliveries
                    )
                    scheduled_ids = {
                        event.event_id
                        for event in history.events
                        if event.event_type
                        == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                        and event.activity_task_scheduled_event_attributes.activity_type.name
                        == "integration.omnigent.execute"
                    }
                    assert not any(
                        event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED
                        and event.activity_task_started_event_attributes.scheduled_event_id
                        in scheduled_ids
                        for event in history.events
                    )
                    async with asyncio.timeout(30):
                        while (await manager.query("get_state"))["profiles"][
                            "provider-codex-native"
                        ]["current_leases"]:
                            await asyncio.sleep(0.05)
                    async with maker() as session:
                        lease = (
                            await session.execute(
                                select(ProviderProfileSlotLease).where(
                                    ProviderProfileSlotLease.workflow_id == first.id
                                )
                            )
                        ).scalar_one()
                        assert lease.purpose == "execution_omnigent"
                        assert lease.lease_state == "released"
                        assert lease.fencing_generation == 2

                    async with Worker(
                        env.client,
                        task_queue=AGENT_RUNTIME_TASK_QUEUE,
                        activities=[capacity.execute],
                    ):
                        second = await env.client.start_workflow(
                            _CodexCapacityHandoffRun.run,
                            request.model_copy(
                                update={"idempotency_key": "next-codex-run"}
                            ),
                            id=f"next-codex-{uuid4()}",
                            task_queue=workflow_queue,
                        )
                        result = await asyncio.wait_for(second.result(), 30)
                        assert (
                            result["summary"]
                            == "The next run acquired the returned slot"
                        )
                        assert len(capacity.started) == 1
                    async with asyncio.timeout(30):
                        while (await manager.query("get_state"))["profiles"][
                            "provider-codex-native"
                        ]["current_leases"]:
                            await asyncio.sleep(0.01)
                    await manager.signal("shutdown")
                    await manager.result()
                    manager_history = await manager.fetch_history()

        await Replayer(
            workflows=[MoonMindProviderProfileManagerWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        ).replay_workflow(manager_history)
        await Replayer(
            workflows=[_CodexCapacityHandoffRun],
            workflow_runner=UnsandboxedWorkflowRunner(),
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        ).replay_workflow(history)
    finally:
        await engine.dispose()
