"""Worker replacement preserves cleanup authority before host readmission."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import (
    OmnigentExecutionPlanRecord,
    OmnigentRuntimeBindingRecord,
)
from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.host_services.cleanup import DockerOmnigentHostCleanupService
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.runtime_bindings import (
    DbRuntimeBindingStore,
    RuntimeBindingState,
    stable_binding_id,
)
from moonmind.schemas.agent_runtime_models import (
    AdmittedProviderCapacity,
    AgentExecutionRequest,
    AgentRunResult,
)
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from tests.integration.reliability.test_release_routing_journey import connect
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.omnigent.test_generic_platform_production_services import (
    _PUSHED_PUBLICATION,
    _generic_publication_harness,
    _plan,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn
class InterruptedAdmissionJourney:
    @workflow.run
    async def run(self, payload: dict, activity_queue: str) -> dict:
        request = AgentExecutionRequest.model_validate(payload)
        returned = await workflow.execute_activity(
            "integration.omnigent.profile_bound_execute",
            request,
            task_queue=activity_queue,
            start_to_close_timeout=timedelta(seconds=45),
            heartbeat_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=100), maximum_attempts=3
            ),
        )
        result = AgentRunResult.model_validate(returned)
        return {
            "readmissionReason": MoonMindAgentRun._omnigent_capacity_requeue_reason(
                result, request=request
            ),
            "recovery": result.metadata.get("admissionRecovery"),
        }


async def test_replacement_cleans_owned_docker_resources_before_readmission(
    tmp_path, monkeypatch
):
    connected = await connect()
    client = Client(
        connected.service_client,
        namespace=connected.namespace,
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    queue = "interrupted-admission-" + uuid4().hex
    container = "moonmind-test-interrupted-host-" + uuid4().hex[:12]
    volume = container + "-state"
    backend = DockerCommandBackend()
    cleanup = DockerOmnigentHostCleanupService(backend)
    entered = asyncio.Event()
    async with isolated_postgres(
        [
            OmnigentExecutionPlanRecord.__table__,
            OmnigentRuntimeBindingRecord.__table__,
        ]
    ) as sessions:
        harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
        plan = _plan("opencode-go/model")
        await DbExecutionPlanStore(sessions).persist(plan)
        harness.realizer._runtime_bindings = DbRuntimeBindingStore(sessions)
        gateway = LocalOmnigentArtifactGateway(root=tmp_path / "artifacts")
        harness.realizer._artifacts = gateway
        monkeypatch.setattr("api_service.db.base.async_session_maker", sessions)
        monkeypatch.setattr(
            "moonmind.omnigent.realizers.registry.get_default_registry",
            lambda: SimpleNamespace(require=lambda _ref: harness.realizer),
        )
        request_payload = harness.publish_request.model_dump(mode="json", by_alias=True)
        request_payload.update(
            {
                "omnigentExecutionPlan": {
                    "planRef": plan.planRef,
                    "planDigest": "sha256:" + plan.planRef.rsplit(":", 1)[-1],
                    "planArtifactRef": "artifact:qualification-plan",
                    "taskInputSnapshotRef": "artifact:qualification-input",
                    "taskInputSnapshotDigest": "sha256:" + "2" * 64,
                },
                "admittedProviderCapacity": AdmittedProviderCapacity(
                    leaseOwnerId=queue,
                    profiles=[
                        {
                            "providerProfileRef": "opencode-go-primary",
                            "providerRuntimeId": "opencode",
                        }
                    ],
                    executionPlanRef=plan.planRef,
                    stepExecutionId="step-1",
                    idempotencyKey=harness.publish_request.idempotency_key,
                    admissionEpoch=1,
                ).model_dump(mode="json", by_alias=True),
            }
        )
        request = AgentExecutionRequest.model_validate(request_payload)

        async def allocate_and_interrupt(**kwargs):
            ref, generation = kwargs["host_lease_ref"], kwargs["host_lease_generation"]
            authority = {
                "kind": "host",
                "containerName": container,
                "stateVolumeRef": volume,
                "launchGeneration": generation,
            }
            await kwargs["authority_sink"](authority)
            labels = [
                "--label",
                "moonmind.host_lease_ref=" + ref,
                "--label",
                "moonmind.host_lease_generation=" + str(generation),
            ]
            await backend.run(["docker", "volume", "create", *labels, volume])
            # An inert pre-session resource, using the same local dependency
            # image as Compose. No provider, credentials, or task success stub.
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
                    "--mount",
                    f"type=volume,src={volume},dst=/owned-state",
                    "--entrypoint",
                    "/bin/sh",
                    "postgres:17",
                    "-c",
                    "sleep 300",
                ]
            )
            entered.set()
            await asyncio.Event().wait()

        async def clean_owned(**kwargs):
            context = kwargs["host_context"]
            return await cleanup.cleanup(
                container_name=context["containerName"],
                state_volume_ref=context["stateVolumeRef"],
                host_lease_ref=kwargs["host_lease_ref"],
                host_lease_generation=kwargs["host_lease_generation"],
            )

        harness.realizer._host_runtime.realize = allocate_and_interrupt
        harness.realizer._host_runtime.cleanup = clean_owned
        catalog = build_default_activity_catalog()
        selected = TemporalActivityCatalog(
            activities=tuple(
                a
                for a in catalog.activities
                if a.activity_type == "integration.omnigent.profile_bound_execute"
            ),
            fleets=catalog.fleets,
        )
        (binding,) = build_activity_bindings(
            selected, agent_runtime_activities=TemporalAgentRuntimeActivities()
        )
        first = Worker(
            client,
            task_queue=queue + "-activity",
            activities=[binding.handler],
            graceful_shutdown_timeout=timedelta(0),
        )
        first_task = asyncio.create_task(first.run())
        try:
            async with Worker(
                client,
                task_queue=queue,
                workflows=[InterruptedAdmissionJourney],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await client.start_workflow(
                    InterruptedAdmissionJourney.run,
                    args=[
                        request.model_dump(mode="json", by_alias=True),
                        queue + "-activity",
                    ],
                    id=queue,
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=90),
                )
                await asyncio.wait_for(entered.wait(), 25)
                await first.shutdown()
                await first_task
                key = stable_binding_id(
                    execution_plan_ref=plan.planRef,
                    idempotency_key=request.idempotency_key,
                    admission_epoch=1,
                )
                store = DbRuntimeBindingStore(sessions)
                pending = await store.get(key)
                assert pending.state is RuntimeBindingState.host_allocating
                assert not pending.omnigentSessionId
                assert "provider-released" not in harness.events
                assert (
                    await backend.run(
                        [
                            "docker",
                            "container",
                            "inspect",
                            "--format",
                            "{{.State.Running}}",
                            container,
                        ]
                    )
                )[1].strip() == "true"
                harness.realizer._runtime_bindings = store
                async with Worker(
                    client, task_queue=queue + "-activity", activities=[binding.handler]
                ):
                    result = await handle.result()
                assert result["readmissionReason"]
                assert result["recovery"]["runtimeBindingRef"] == key
                evidence = json.loads(
                    await gateway.read_bytes(
                        result["recovery"]["cleanupAttestationRef"]
                    )
                )
                assert evidence["bindingId"] == key
                assert evidence["results"]["host"]["containerRemoved"] is True
                assert (await store.get(key)).state is RuntimeBindingState.cleaned
                assert "session-drained" not in harness.events
                assert (
                    await backend.run(
                        ["docker", "container", "inspect", container], check=False
                    )
                )[0] != 0
                assert (
                    await backend.run(
                        ["docker", "volume", "inspect", volume], check=False
                    )
                )[0] != 0
                await Replayer(
                    workflows=[InterruptedAdmissionJourney],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
                ).replay_workflow(await handle.fetch_history())
        finally:
            await first.shutdown()
            await asyncio.gather(first_task, return_exceptions=True)
            await backend.run(["docker", "rm", "-f", container], check=False)
            await backend.run(["docker", "volume", "rm", volume], check=False)
