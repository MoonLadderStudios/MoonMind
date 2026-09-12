"""A real Temporal delivery replacement retains PostgreSQL session authority."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db.models import (
    OmnigentExecutionPlanRecord,
    OmnigentRuntimeBindingRecord,
)
from moonmind.omnigent.activity_ownership import delivery_was_revoked
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.runtime_bindings import (
    DbRuntimeBindingStore,
    RuntimeBindingState,
)
from moonmind.schemas.agent_runtime_models import AgentRunResult
from tests.integration.reliability.test_release_routing_journey import connect
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.omnigent.test_generic_platform_production_services import (
    _PUSHED_PUBLICATION,
    _exact_plan,
    _generic_publication_harness,
    _prime_attested_host_binding,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize("deliveries", [2, 20])
async def test_concurrent_finalization_deliveries_share_one_publication_receipt(
    deliveries,
):
    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
    from moonmind.omnigent.runtime_bindings import RuntimeBindingSessionAuthoritySink
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    async with isolated_postgres(
        [OmnigentExecutionPlanRecord.__table__, OmnigentRuntimeBindingRecord.__table__]
    ) as sessions:
        plan = _exact_plan("opencode-go/model")
        await DbExecutionPlanStore(sessions).persist(plan)
        stores = [DbRuntimeBindingStore(sessions) for _ in range(deliveries)]
        first_store, second_store = stores[:2]
        binding = await first_store.create_initial(
            execution_plan_ref=plan.planRef,
            idempotency_key="concurrent-finalization",
            provider_leases={},
        )
        sink = RuntimeBindingSessionAuthoritySink(first_store, binding)
        await sink.record_phase(
            "workspace",
            {
                "workspaceSpec": {
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": "recorded",
                        "relativePath": "repo",
                    }
                }
            },
        )
        await sink.record_phase(
            "compute",
            AgentRunResult(summary="verified candidate").model_dump(
                mode="json", by_alias=True
            ),
        )
        await sink.record_phase("saved", {"archiveRef": "artifact://saved-candidate"})
        binding = sink.binding
        for state in (RuntimeBindingState.cleanup_pending, RuntimeBindingState.cleaned):
            binding = await first_store.update(
                binding.bindingId,
                expected_revision=binding.revision,
                expected_fencing_generation=binding.fencingGeneration,
                state=state,
            )
        request = AgentExecutionRequest.model_validate(
            {
                "agentKind": "external",
                "agentId": "omnigent",
                "correlationId": "workflow",
                "idempotencyKey": "concurrent-finalization",
            }
        )
        entered, release = asyncio.Event(), asyncio.Event()
        publications = []

        async def publish(bound, result):
            publications.append(bound.workspace_spec["workspaceLocator"])
            entered.set()
            await release.wait()
            return result.model_copy(update={"summary": "publication verified"})

        realizers = []
        for store in stores:
            realizer = object.__new__(GenericOmnigentHostRealizer)
            realizer._runtime_bindings = store
            realizer._publish_repository = publish
            realizers.append(realizer)
        first = asyncio.create_task(
            realizers[0]._reconcile_finalization(request, binding)
        )
        try:
            await asyncio.wait_for(entered.wait(), 10)
            retries = [
                asyncio.create_task(realizer._reconcile_finalization(request, binding))
                for realizer in realizers[1:]
            ]
            await asyncio.sleep(0.1)
            assert len(publications) == 1
        finally:
            release.set()
        outcomes = await asyncio.wait_for(asyncio.gather(first, *retries), 10)
        assert all(result == outcomes[0] for result in outcomes)
        assert len(publications) == 1
        assert outcomes[0].summary == "publication verified"
        assert "publication" in (await second_store.get(binding.bindingId)).phaseResults


@workflow.defn
class ReplaceDelivery:
    @workflow.run
    async def run(self, activity_queue: str):
        return await workflow.execute_activity(
            "reliability.replace_delivery",
            task_queue=activity_queue,
            start_to_close_timeout=timedelta(seconds=30),
            heartbeat_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=100), maximum_attempts=3
            ),
        )


async def test_shutdown_resumes_one_fenced_session_on_replacement_worker(monkeypatch):
    client = await connect()
    queue = "replace-delivery-" + uuid4().hex
    entered = asyncio.Event()
    observations = []
    async with isolated_postgres(
        [OmnigentExecutionPlanRecord.__table__, OmnigentRuntimeBindingRecord.__table__]
    ) as sessions:
        harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
        harness.runtime_store = DbRuntimeBindingStore(sessions)
        harness.realizer._runtime_bindings = harness.runtime_store
        from moonmind.omnigent.activity_ownership import current_delivery_owner

        harness.realizer._execution_owner = current_delivery_owner
        harness.realizer._host_runtime.cleanup_authorities = AsyncMock()
        plan = _exact_plan("opencode-go/model")
        await DbExecutionPlanStore(sessions).persist(plan)
        await _prime_attested_host_binding(harness, plan)

        async def driver(request, *, session_authority_sink):
            await session_authority_sink.session_created("session-1")
            if not entered.is_set():
                entered.set()
                try:
                    while True:
                        activity.heartbeat("provider is still running")
                        await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    observations.append(delivery_was_revoked())
                    raise
            assert session_authority_sink.binding.omnigentSessionId == "session-1"
            return AgentRunResult(
                summary="same provider completed",
                metadata={"omnigentSessionId": "session-1"},
            )

        harness.realizer._session_driver = driver

        @activity.defn(name="reliability.replace_delivery")
        async def execute():
            # Reconstruct storage exactly as another process does. The same
            # immutable plan and request, including admission identity, resume.
            harness.realizer._runtime_bindings = DbRuntimeBindingStore(sessions)
            result = await harness.realizer._execute_lifecycle(
                harness.publish_request, plan
            )
            return result.summary

        async with Worker(
            client,
            task_queue=queue,
            workflows=[ReplaceDelivery],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            first = Worker(
                client,
                task_queue=queue + "-activity",
                activities=[execute],
                graceful_shutdown_timeout=timedelta(0),
            )
            first_task = asyncio.create_task(first.run())
            try:
                handle = await client.start_workflow(
                    ReplaceDelivery.run,
                    queue + "-activity",
                    id=queue,
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=60),
                )
                await asyncio.wait_for(entered.wait(), 15)
                await first.shutdown()
                await asyncio.gather(first_task)
                assert observations == [True]
                assert "session-drained" not in harness.events
                assert "provider-released" not in harness.events
                assert "host-cleaned" not in harness.events
                async with sessions() as session:
                    rows = (
                        (await session.execute(select(OmnigentRuntimeBindingRecord)))
                        .scalars()
                        .all()
                    )
                assert len(rows) == 1
                binding = await harness.runtime_store.get(rows[0].binding_id)
                assert binding.state is RuntimeBindingState.session_active
                assert binding.omnigentSessionId == "session-1"
                from moonmind.config.settings import settings
                from moonmind.omnigent.generic_host_janitor import (
                    temporal_owner_has_closed,
                )

                monkeypatch.setattr(
                    settings.temporal,
                    "address",
                    client.service_client.config.target_host,
                )
                assert binding.phaseResults["owner"]["workflowId"] == queue
                assert await temporal_owner_has_closed(binding) is False
                async with Worker(
                    client, task_queue=queue + "-activity", activities=[execute]
                ):
                    assert await handle.result() == "same provider completed"
                assert harness.events.count("workspace-published") == 1
                assert harness.events.count("session-drained") == 1
                assert harness.events.count("provider-released") == 1
                assert "host-ready" not in harness.events
            finally:
                if not first_task.done():
                    await first.shutdown()
                    await asyncio.gather(first_task)
