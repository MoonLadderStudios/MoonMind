import asyncio
import logging

from temporalio.common import VersioningBehavior
from temporalio.worker import (
    UnsandboxedWorkflowRunner,
    Worker,
    WorkerDeploymentConfig,
    WorkerDeploymentVersion,
)
from moonmind.workflows.temporal.client import get_temporal_client

from moonmind.config.settings import settings
from moonmind.workflows.temporal.workers import (
    WORKFLOW_FLEET,
    build_worker_activity_bindings,
    build_worker_spec,
    describe_configured_worker,
)
from moonmind.workflows.temporal.workflow_registry import (
    workflow_fleet_activity_handlers,
    workflow_fleet_workflow_classes,
)


async def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    topology = describe_configured_worker()

    client = await get_temporal_client(
        settings.temporal.address,
        settings.temporal.namespace,
    )

    workflows = []
    if topology.fleet == WORKFLOW_FLEET:
        workflows.extend(workflow_fleet_workflow_classes())

    if topology.fleet == WORKFLOW_FLEET:
        activities = list(workflow_fleet_activity_handlers())
    else:
        bindings = build_worker_activity_bindings(fleet=topology.fleet)
        activities = [b.handler for b in bindings]

    spec = build_worker_spec(
        topology=topology,
        workflows=workflows,
        activities=activities,
    )
    worker_kwargs = {
        "workflows": spec.workflows,
        "activities": spec.activities,
        "max_concurrent_activities": topology.concurrency_limit or 100,
        "workflow_runner": UnsandboxedWorkflowRunner(),
    }
    if spec.versioning_enabled:
        worker_kwargs["deployment_config"] = WorkerDeploymentConfig(
            version=WorkerDeploymentVersion(
                deployment_name=spec.deployment_id,
                build_id=spec.build_id,
            ),
            use_worker_versioning=True,
            default_versioning_behavior=VersioningBehavior.AUTO_UPGRADE,
        )

    workers = [
        Worker(
            client,
            task_queue=task_queue,
            **worker_kwargs,
        )
        for task_queue in topology.task_queues
    ]

    logger.info(
        "Starting Temporal worker for fleet '%s' on queues '%s'",
        topology.fleet,
        ", ".join(topology.task_queues),
    )
    logger.info("Executable worker specification: %s", spec.readiness_payload())
    async with asyncio.TaskGroup() as tg:
        for worker in workers:
            tg.create_task(worker.run())


if __name__ == "__main__":
    asyncio.run(main())
