"""Temporal worker runtime entrypoint."""

import asyncio
import logging
from contextlib import AsyncExitStack

from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from api_service.db.base import get_async_session_context
from moonmind.config.settings import settings
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.temporal.activity_runtime import (
    TemporalJulesActivities,
    TemporalPlanActivities,
    TemporalSandboxActivities,
    TemporalSkillActivities,
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
)

logger = logging.getLogger(__name__)


from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow as MoonMindRun
from moonmind.workflows.temporal.workflows.manifest_ingest import (
    MoonMindManifestIngestWorkflow as MoonMindManifestIngest,
)


async def _build_runtime_activities(topology) -> tuple[AsyncExitStack, list[object]]:
    resources = AsyncExitStack()
    try:
        session = await resources.enter_async_context(get_async_session_context())
        artifact_service = TemporalArtifactService(TemporalArtifactRepository(session))
        def _dummy_planner(inputs, parameters, snapshot):
            # Stub planner to unblock workflow execution until the real LLM planner is implemented
            from datetime import UTC, datetime
            return {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Dummy Plan",
                    "created_at": datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "registry_snapshot": {
                        "digest": "reg:sha256:dummy",
                        "artifact_ref": "art:sha256:dummy"
                    }
                },
                "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                "nodes": [{"id": "dummy-node", "skill": {"name": "dummy.skill", "version": "1.0"}, "inputs": {}}],
                "edges": []
            }

        bindings = build_worker_activity_bindings(
            fleet=topology.fleet,
            artifact_activities=TemporalArtifactActivities(artifact_service),
            plan_activities=TemporalPlanActivities(
                artifact_service=artifact_service,
                planner=_dummy_planner
            ),
            skill_activities=TemporalSkillActivities(
                dispatcher=SkillActivityDispatcher()
            ),
            sandbox_activities=TemporalSandboxActivities(
                artifact_service=artifact_service
            ),
            integration_activities=TemporalJulesActivities(
                artifact_service=artifact_service
            ),
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

    client = await Client.connect(
        settings.temporal.address, namespace=settings.temporal.namespace
    )

    workflows = []
    activities = []
    runtime_resources: AsyncExitStack | None = None

    if topology.fleet == WORKFLOW_FLEET:
        workflows = [MoonMindRun, MoonMindManifestIngest]
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async())
