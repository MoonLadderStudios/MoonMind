"""A scheduled janitor releases a stranded host while the same AgentRun waits."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleSpec,
)
from temporalio.common import RawValue
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import (
    OmnigentExecutionPlanRecord,
    OmnigentRuntimeBindingRecord,
    OmnigentHostBindingRecordV2,
    OmnigentHostLeaseRecordV2,
    MachineCapacityReservation,
)
from moonmind.capacity import (
    MachineCapacityLedger,
    OwnedContainerInventory,
    OWNED_CONTAINER_LABEL_FILTERS,
)
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.host_capacity import GenericHostCapacityAdmission
from moonmind.omnigent.host_leases import DbOmnigentHostLeaseRepository
from moonmind.omnigent.host_services.cleanup import DockerOmnigentHostCleanupService
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.runtime_bindings import (
    DbRuntimeBindingStore,
    RuntimeBindingState,
    stable_binding_id,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activities.omnigent_activities import (
    omnigent_oauth_host_janitor_activity,
)
from moonmind.workflows.temporal.activities.omnigent_session_activities import (
    omnigent_admit_generic_host_capacity_activity,
)
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.omnigent_oauth_host_janitor import (
    MoonMindOmnigentOAuthHostJanitorWorkflow,
)
from tests.integration.reliability.test_release_routing_journey import connect
from tests.integration.services.temporal.workflows.test_agent_run import (
    MockProviderProfileManager,
)
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.capacity.test_machine_capacity_boundaries import _budget
from tests.unit.omnigent.test_generic_platform_production_services import (
    _generic_publication_harness,
    _plan,
    _prime_attested_host_binding,
)
from tests.unit.workflows.temporal.workflows.test_agent_run_omnigent_capacity_admission import (
    _admission,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


async def test_scheduled_cleanup_isolates_oauth_failure_and_unblocks_same_agent_run(
    tmp_path, monkeypatch
):
    connected = await connect()
    client = Client(
        connected.service_client,
        namespace=connected.namespace,
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    key = "cleanup-capacity-" + uuid4().hex
    container, volume = "moonmind-test-" + key, "moonmind-test-" + key + "-state"
    backend = DockerCommandBackend()
    closed = await client.start_workflow(
        "test-unserved-owner", id=key + "-closed", task_queue=key + "-unserved"
    )
    await closed.terminate()
    live = await client.start_workflow(
        "test-unserved-owner", id=key + "-live", task_queue=key + "-unserved"
    )
    tables = [
        OmnigentExecutionPlanRecord.__table__,
        OmnigentRuntimeBindingRecord.__table__,
        OmnigentHostBindingRecordV2.__table__,
        OmnigentHostLeaseRecordV2.__table__,
        MachineCapacityReservation.__table__,
    ]
    async with isolated_postgres(tables) as sessions:
        monkeypatch.setattr("api_service.db.base.async_session_maker", sessions)
        monkeypatch.setattr(
            "moonmind.workflows.temporal.client.get_temporal_client",
            lambda *_args: asyncio.sleep(0, result=client),
        )
        harness = await _generic_publication_harness({})
        plan = _plan("opencode-go/model")
        await DbExecutionPlanStore(sessions).persist(plan)
        bindings = DbRuntimeBindingStore(sessions)
        ledger = MachineCapacityLedger(sessions)
        admission = GenericHostCapacityAdmission(
            session_factory=sessions,
            host_capacity=8,
            cold_launch_burst=8,
            cold_launch_window_seconds=30,
            machine_capacity=ledger,
            backend_ref=key,
        )
        budget = _budget()
        limits = {
            "memoryMiB": 4096,
            "cpuMillis": 1000,
            "processes": 256,
            "temporaryStorageMiB": 256,
        }
        leases = DbOmnigentHostLeaseRepository(
            sessions,
            capacity_admission=admission,
            machine_budget_provider=lambda: asyncio.sleep(0, result=budget),
        )
        acquire = leases.acquire

        async def allocate(**kwargs):
            return await acquire(**kwargs, resource_limits=limits)

        leases.acquire = allocate
        harness.runtime_store = harness.realizer._runtime_bindings = bindings
        harness.host_leases = harness.realizer._host_leases = leases
        from moonmind.omnigent import runtime_bindings as binding_module

        class HistoricalClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) - timedelta(hours=1)

        monkeypatch.setattr(binding_module, "datetime", HistoricalClock)
        await _prime_attested_host_binding(harness, plan)
        binding_id = stable_binding_id(
            execution_plan_ref=plan.planRef,
            idempotency_key=harness.publish_request.idempotency_key,
        )
        binding = await bindings.get(binding_id)
        binding = await bindings.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            updates={
                "phaseResults": {
                    "owner": {
                        "namespace": client.namespace,
                        "workflowId": closed.id,
                        "runId": closed.first_execution_run_id,
                    }
                }
            },
        )
        async with sessions() as session:
            lease_row = await session.get(
                OmnigentHostLeaseRecordV2, binding.hostLeaseRef
            )
            lease_row.cleanup_handle_json = {
                "kind": "host",
                "containerName": container,
                "stateVolumeRef": volume,
                "launchGeneration": lease_row.host_lease_generation,
            }
            await session.commit()
        live_binding = await bindings.create_initial(
            execution_plan_ref=plan.planRef,
            idempotency_key="live-owner",
            provider_leases={},
            initial_phase_results={
                "owner": {
                    "namespace": client.namespace,
                    "workflowId": live.id,
                    "runId": live.first_execution_run_id,
                }
            },
        )
        monkeypatch.setattr(binding_module, "datetime", datetime)
        host_lease = await leases.get(binding.hostLeaseRef)
        labels = [
            "--label",
            "moonmind.host_lease_ref=" + host_lease.leaseRef,
            "--label",
            "moonmind.host_lease_generation=" + str(host_lease.launchGeneration),
        ]
        await backend.run(["docker", "volume", "create", *labels, volume])
        await backend.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                container,
                "--network",
                "none",
                *labels,
                "--memory",
                "4096m",
                "--mount",
                f"type=volume,src={volume},dst=/owned-state",
                "--entrypoint",
                "/bin/sh",
                "postgres:17",
                "-c",
                "sleep 300",
            ]
        )
        harness.realizer._host_capacity_admission = admission
        await harness.realizer._confirm_machine_reservation(
            host_lease.leaseRef, container
        )
        cleanup = DockerOmnigentHostCleanupService(backend)

        async def clean(**kwargs):
            return await cleanup.cleanup(
                container_name=kwargs["host_context"]["containerName"],
                state_volume_ref=kwargs["host_context"]["stateVolumeRef"],
                host_lease_ref=kwargs["host_lease_ref"],
                host_lease_generation=kwargs["host_lease_generation"],
            )

        harness.realizer._host_runtime.cleanup = clean
        harness.realizer._provider_leases.release_from_binding = AsyncMock()

        async def inventory():
            # This disposable backend contains only the named test allocation.
            # Absence is read from Docker, never synthesized from a lease flag.
            _, stdout, _ = await backend.run(
                ["docker", "ps", "-aq", "--filter", f"name=^/{container}$"]
            )
            assert not stdout.strip(), "capacity may release only after Docker removal"
            return OwnedContainerInventory(
                containers={}, label_selectors=OWNED_CONTAINER_LABEL_FILTERS
            )

        services = SimpleNamespace(
            host_lease_repository=leases,
            runtime_binding_store=bindings,
            generic_realizer=harness.realizer,
            machine_capacity=ledger,
            machine_backend_ref=key,
            owned_container_inventory=inventory,
            cpu_pool=None,
            planned_host_resolver=AsyncMock(
                return_value=(None, SimpleNamespace(limits=limits))
            ),
            machine_budget_provider=lambda: asyncio.sleep(0, result=budget),
        )
        monkeypatch.setattr(
            "moonmind.omnigent.production.build_generic_omnigent_execution_services",
            lambda **_kwargs: services,
        )
        monkeypatch.setattr(
            GenericHostCapacityAdmission,
            "from_environment",
            lambda **_kwargs: admission,
        )
        monkeypatch.setattr(
            "moonmind.omnigent.settings.generic_host_enabled", lambda: True
        )
        # The escaped historical mismatch is raised by the OAuth owner's actual
        # run boundary; the scheduled Activity must still enter generic cleanup.
        monkeypatch.setattr(
            "moonmind.omnigent.oauth_host_janitor.OmnigentOAuthHostJanitor.run",
            AsyncMock(
                side_effect=ValueError(
                    "host lease credential_generation must match binding and profile"
                )
            ),
        )
        original = MoonMindAgentRun._execute_kwargs_for_route
        monkeypatch.setattr(
            MoonMindAgentRun,
            "_execute_kwargs_for_route",
            staticmethod(
                lambda route: {**original(route), "task_queue": key + "-control"}
            ),
        )
        monkeypatch.setattr(
            MoonMindAgentRun,
            "_manager_workflow_id",
            staticmethod(lambda _runtime: key + "-manager"),
        )
        request = harness.publish_request.model_dump(by_alias=True, mode="json")
        request.update(
            omnigentExecutionPlan={
                "planRef": plan.planRef,
                "planDigest": "sha256:" + plan.planRef.rsplit(":", 1)[-1],
                "planArtifactRef": "artifact:plan",
                "taskInputSnapshotRef": "artifact:input",
                "taskInputSnapshotDigest": "sha256:" + "2" * 64,
            },
            idempotencyKey=key,
            executionProfileRef="opencode-go-primary",
            timeoutPolicy={"startToCloseSeconds": 90},
        )
        waiting, launched = asyncio.Event(), asyncio.Event()
        admitted_requests = []

        @activity.defn(dynamic=True)
        async def control(args: Sequence[RawValue]):
            name = activity.info().activity_type
            payload = (
                activity.payload_converter().from_payload(args[0].payload)
                if args
                else None
            )
            if name == "integration.resolve_adapter_metadata":
                return {
                    "agent_id": "omnigent",
                    "execution_style": "streaming_gateway",
                    "supports_callbacks": False,
                }
            if name == "omnigent.evaluate_session_admission":
                return (
                    _admission(profile_ref="opencode-go-primary")
                    .model_copy(update={"admitted": False})
                    .model_dump(by_alias=True, mode="json")
                )
            if name == "omnigent.admit_generic_host_capacity":
                result = await omnigent_admit_generic_host_capacity_activity(payload)
                if not result["admitted"]:
                    assert result["limitingResource"] == "machine_memory"
                    waiting.set()
                return result
            if name == "integration.omnigent.profile_bound_execute":
                admitted_requests.append(payload)
                launched.set()
                # This test stops at provider admission. No fabricated agent
                # completion grants publication or a successful terminal result.
                await asyncio.Event().wait()
            if name == "provider_profile.list":
                return {"profiles": []}
            if name == "provider_profile.manager_state":
                return {
                    "running": True,
                    "inspection_succeeded": True,
                    "requester_pending": True,
                }
            if name == "agent_runtime.publish_artifacts":
                return payload
            raise AssertionError(name)

        from moonmind.workflows.temporal.activity_catalog import (
            build_default_activity_catalog,
        )

        janitor_queue = (
            build_default_activity_catalog()
            .resolve_activity("integration.omnigent.oauth_host_janitor")
            .task_queue
        )
        handle = schedule = None
        try:
            async with (
                Worker(
                    client,
                    task_queue=key,
                    workflows=[
                        MoonMindAgentRun,
                        MockProviderProfileManager,
                        MoonMindOmnigentOAuthHostJanitorWorkflow,
                    ],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                Worker(client, task_queue=key + "-control", activities=[control]),
                Worker(
                    client,
                    task_queue=janitor_queue,
                    activities=[omnigent_oauth_host_janitor_activity],
                ),
            ):
                manager = await client.start_workflow(
                    MockProviderProfileManager.run,
                    {},
                    id=key + "-manager",
                    task_queue=key,
                )
                handle = await client.start_workflow(
                    MoonMindAgentRun.run,
                    AgentExecutionRequest.model_validate(request),
                    id=key,
                    task_queue=key,
                )
                await asyncio.wait_for(waiting.wait(), 25)
                assert not launched.is_set()
                schedule = await client.create_schedule(
                    key + "-janitor",
                    Schedule(
                        action=ScheduleActionStartWorkflow(
                            MoonMindOmnigentOAuthHostJanitorWorkflow.run,
                            {},
                            id=key + "-cleanup",
                            task_queue=key,
                        ),
                        spec=ScheduleSpec(
                            intervals=[ScheduleIntervalSpec(every=timedelta(days=1))]
                        ),
                    ),
                    trigger_immediately=True,
                )
                for _ in range(100):
                    actions = (await schedule.describe()).info.recent_actions
                    if actions:
                        break
                    await asyncio.sleep(0.1)
                result = await client.get_workflow_handle(
                    actions[-1].action.workflow_id
                ).result()
                assert result["status"] == "degraded"
                assert result["genericHost"]["reconciled"] == 1
                assert (
                    result["genericHost"]["machineCapacity"]["backendObserved"] is True
                )
                assert (await ledger.usage(backend_ref=key)).reserved_memory_mib == 0
                assert (
                    await bindings.get(binding_id)
                ).state is RuntimeBindingState.cleaned
                assert (
                    await bindings.get(live_binding.bindingId)
                ).state is not RuntimeBindingState.cleaned
                await asyncio.wait_for(launched.wait(), 45)
                assert len(admitted_requests) == 1
                assert admitted_requests[0]["idempotencyKey"] == key
                assert (
                    admitted_requests[0]["omnigentExecutionPlan"]["planRef"]
                    == plan.planRef
                )
                await Replayer(
                    workflows=[MoonMindAgentRun],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
                ).replay_workflow(await handle.fetch_history())
                await handle.cancel()
                await manager.terminate()
        finally:
            if schedule is not None:
                await schedule.delete()
            await live.terminate()
            if handle is not None:
                await handle.terminate()
            await backend.run(["docker", "rm", "-f", container], check=False)
            await backend.run(["docker", "volume", "rm", "-f", volume], check=False)
