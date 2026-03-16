"""Temporal worker runtime entrypoint."""

import asyncio
import logging
from contextlib import AsyncExitStack


from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db.base import get_async_session_context
from moonmind.config.settings import settings
from moonmind.workflows.temporal.activity_runtime import (
    TemporalJulesActivities,
    TemporalSandboxActivities,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.workers import (
    WORKFLOW_FLEET,
    build_worker_activity_bindings,
    describe_configured_worker,
    list_registered_workflow_types,
)
from moonmind.workflows.temporal.workflows.auth_profile_manager import (
    MoonMindAuthProfileManagerWorkflow as MoonMindAuthProfileManager,
)
from moonmind.workflows.temporal.workflows.manifest_ingest import (
    MoonMindManifestIngestWorkflow as MoonMindManifestIngest,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow as MoonMindRun
from moonmind.workflows.temporal.worker_healthcheck import start_healthcheck_server
from moonmind.workflows.temporal.workflows.agent_run import (
    MoonMindAgentRun,
    publish_artifacts_activity,
    invoke_adapter_cancel,
)

logger = logging.getLogger(__name__)


async def _build_runtime_activities(topology) -> tuple[AsyncExitStack, list[object]]:
    """Build activity handlers for the configured non-workflow fleet.

    Agent execution is handled by MoonMind.AgentRun (a child workflow on the
    workflow fleet).  This function wires only the activity-level bindings
    needed by supporting fleets (artifacts, sandbox, integrations).
    """
    resources = AsyncExitStack()
    try:
        session = await resources.enter_async_context(get_async_session_context())
        artifact_service = TemporalArtifactService(TemporalArtifactRepository(session))
        sandbox_activities = TemporalSandboxActivities(artifact_service=artifact_service)

        bindings = build_worker_activity_bindings(
            fleet=topology.fleet,
            artifact_activities=TemporalArtifactActivities(artifact_service),
            sandbox_activities=sandbox_activities,
            integration_activities=TemporalJulesActivities(
                artifact_service=artifact_service
            ),
        )
        binding_descriptors = sorted(
            f"{binding.activity_type}->{binding.task_queue}" for binding in bindings
        )
        logger.info(
            "Temporal activity bindings for fleet %s: %s",
            topology.fleet,
            ", ".join(binding_descriptors) if binding_descriptors else "(none)",
        )
        return resources, [binding.handler for binding in bindings]
    except Exception:
        await resources.aclose()
        raise


def _worker_concurrency_kwargs(topology) -> dict[str, int]:
    if topology.concurrency_limit is None:
        return {}
    if topology.fleet == WORKFLOW_FLEET:
        return {"max_concurrent_workflow_tasks": topology.concurrency_limit}
    return {"max_concurrent_activities": topology.concurrency_limit}


async def main_async() -> None:
    """Run the Temporal worker."""
    topology = describe_configured_worker()

    logger.info(
        f"Starting {topology.service_name} [{topology.fleet}] "
        f"queues={','.join(topology.task_queues)} "
        f"concurrency={topology.concurrency_limit}"
    )

    # Start healthcheck server before connecting to Temporal so probes
    # can confirm the process is alive even during initial connection.
    healthcheck_server = await start_healthcheck_server()

    client = await Client.connect(
        settings.temporal.address, namespace=settings.temporal.namespace
    )

    workflows = []
    activities = []
    runtime_resources: AsyncExitStack | None = None

    if topology.fleet == WORKFLOW_FLEET:
        workflows = [MoonMindRun, MoonMindManifestIngest, MoonMindAuthProfileManager, MoonMindAgentRun]
        activities = [publish_artifacts_activity, invoke_adapter_cancel]
        logger.info(
            "Temporal workflow fleet registrations: %s",
            ", ".join(list_registered_workflow_types()),
        )
    else:
        runtime_resources, activities = await _build_runtime_activities(topology)

    try:
        worker = Worker(
            client,
            task_queue=topology.task_queues[0],
            workflows=workflows,
            activities=activities,
            workflow_runner=UnsandboxedWorkflowRunner(),
            **_worker_concurrency_kwargs(topology),
        )

        logger.info("Worker started, polling task queues...")
        await worker.run()
    finally:
        if runtime_resources is not None:
            await runtime_resources.aclose()
        if healthcheck_server is not None:
            healthcheck_server.close()
            await healthcheck_server.wait_closed()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async())

