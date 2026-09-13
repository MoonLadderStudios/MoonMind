"""Worker replacement preserves cleanup authority before host readmission."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
from collections.abc import Sequence

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.common import RawValue
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import (
    Base,
    OmnigentExecutionPlanRecord,
    OmnigentRuntimeBindingRecord,
)
from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
from moonmind.omnigent.control_plane.cleanup_authority import CanonicalCleanupAuthority
from moonmind.omnigent.control_plane.turn_commands import CanonicalTurnCommandService
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
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from tests.integration.services.temporal.workflows.test_agent_run import (
    MockProviderProfileManager,
)
from tests.unit.workflows.temporal.workflows.test_agent_run_omnigent_capacity_admission import (
    _admission,
)
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
            *(
                table
                for table in Base.metadata.sorted_tables
                if table.name
                in {
                    "omnigent_chat_binding_aliases",
                    "omnigent_cleanup_authority",
                    "omnigent_reconciliation_decisions",
                    "omnigent_commands",
                    "omnigent_observations",
                    "omnigent_turn_attempts",
                    "omnigent_sessions",
                }
            ),
        ]
    ) as sessions:
        harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
        plan = _plan("opencode-go/model")
        await DbExecutionPlanStore(sessions).persist(plan)
        harness.realizer._runtime_bindings = DbRuntimeBindingStore(sessions)
        canonical_store = OmnigentControlPlaneStore(sessions)
        harness.realizer._turn_commands = CanonicalTurnCommandService(canonical_store)
        harness.realizer._cleanup_authority = CanonicalCleanupAuthority(canonical_store)
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
                "executionProfileRef": "opencode-go-primary",
                "timeoutPolicy": {"startToCloseSeconds": 90},
            }
        )
        request = AgentExecutionRequest.model_validate(request_payload)

        original_realize = harness.realizer._host_runtime.realize
        launches = []

        async def allocate_and_interrupt(**kwargs):
            launches.append(kwargs["host_lease_ref"])
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
            if len(launches) == 1:
                entered.set()
                await asyncio.Event().wait()
            return {
                **await original_realize(**kwargs),
                "containerName": container,
                "stateVolumeRef": volume,
            }

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
        # Only transport/service ports are supplied by the harness. The real
        # AgentRun entrypoint owns admission, release, retry and terminal state.
        original_options = MoonMindAgentRun._execute_kwargs_for_route

        def isolated_options(route):
            return {
                **original_options(route),
                "task_queue": queue
                + (
                    "-activity"
                    if route.activity_type
                    == "integration.omnigent.profile_bound_execute"
                    else "-control"
                ),
            }

        monkeypatch.setattr(
            MoonMindAgentRun,
            "_execute_kwargs_for_route",
            staticmethod(isolated_options),
        )
        monkeypatch.setattr(
            MoonMindAgentRun,
            "_manager_workflow_id",
            staticmethod(lambda _runtime: queue + "-manager"),
        )
        monkeypatch.setattr(
            agent_run_module,
            "STREAMING_EXTERNAL_HEARTBEAT_TIMEOUT",
            timedelta(seconds=2),
        )
        controls = []
        execution_requests = []
        execute = harness.realizer.execute

        async def record_execution(admitted, selected_plan):
            execution_requests.append(admitted)
            return await execute(admitted, selected_plan)

        harness.realizer.execute = record_execution

        @activity.defn(dynamic=True)
        async def control(args: Sequence[RawValue]):
            name = activity.info().activity_type
            payload = (
                activity.payload_converter().from_payload(args[0].payload)
                if args
                else None
            )
            controls.append((name, payload))
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
                    .model_dump(mode="json", by_alias=True)
                )
            if name == "omnigent.admit_generic_host_capacity":
                return {"admitted": True}
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
            raise AssertionError("Unexpected control Activity: " + name)

        # Namespace creation can precede propagation to the history service.
        # Wait on an actual start before launching pollers, not only describe.
        for attempt in range(30):
            try:
                manager = await client.start_workflow(
                    MockProviderProfileManager.run,
                    {},
                    id=queue + "-manager",
                    task_queue=queue,
                )
                break
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND or attempt == 29:
                    raise
                await asyncio.sleep(1)

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
                workflows=[MoonMindAgentRun, MockProviderProfileManager],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ), Worker(client, task_queue=queue + "-control", activities=[control]):
                handle = await client.start_workflow(
                    MoonMindAgentRun.run,
                    request,
                    id=queue,
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=90),
                )
                await asyncio.wait_for(entered.wait(), 25)
                await first.shutdown()
                await asyncio.gather(first_task)
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
                assert result.failure_class is None
                assert len(launches) == 2
                assert launches[0] != launches[1]
                assert [
                    r.admitted_provider_capacity.admission_epoch
                    for r in execution_requests
                ] == [1, 1, 2]
                assert execution_requests[0] == execution_requests[1]
                assert (
                    execution_requests[0].parameters == execution_requests[2].parameters
                )
                assert (
                    execution_requests[0].workspace_spec
                    == execution_requests[2].workspace_spec
                )
                first_binding = await store.get(key)
                recovery = first_binding.terminalResult["metadata"]["admissionRecovery"]
                assert recovery["runtimeBindingRef"] == key
                manager_history = await manager.fetch_history()
                signals = [
                    e.workflow_execution_signaled_event_attributes.signal_name
                    for e in manager_history.events
                    if e.HasField("workflow_execution_signaled_event_attributes")
                ]
                assert signals.count("request_slot") == 2
                assert signals.count("release_slot") == 2
                assert (
                    len(
                        [
                            name
                            for name, _ in controls
                            if name == "omnigent.admit_generic_host_capacity"
                        ]
                    )
                    == 2
                )
                evidence = json.loads(
                    await gateway.read_bytes(recovery["cleanupAttestationRef"])
                )
                assert evidence["bindingId"] == key
                assert evidence["results"]["host"]["containerRemoved"] is True
                assert (await store.get(key)).state is RuntimeBindingState.cleaned
                assert harness.events.count("session-drained") == 1
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
                    workflows=[MoonMindAgentRun, MockProviderProfileManager],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
                ).replay_workflow(await handle.fetch_history())
        finally:
            if "manager" in locals():
                await manager.terminate(reason="isolated admission journey finished")
            if not first_task.done():
                await first.shutdown()
            await asyncio.gather(first_task, return_exceptions=True)
            await backend.run(["docker", "rm", "-f", container], check=False)
            await backend.run(["docker", "volume", "rm", volume], check=False)
