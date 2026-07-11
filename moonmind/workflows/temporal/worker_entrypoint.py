import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from moonmind.workflows.temporal.client import get_temporal_client

from moonmind.config.settings import settings
from moonmind.workflows.temporal.workers import (
    WORKFLOW_FLEET,
    build_worker_activity_bindings,
    describe_configured_worker,
)
from moonmind.workflows.temporal.workflow_registry import (
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

    bindings = build_worker_activity_bindings(fleet=topology.fleet)
    activities = [b.handler for b in bindings]

    workers = [
        Worker(
            client,
            task_queue=task_queue,
            workflows=workflows,
            activities=activities,
            max_concurrent_activities=topology.concurrency_limit or 100,
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        for task_queue in topology.task_queues
    ]

    logger.info(
        "Starting Temporal worker for fleet '%s' on queues '%s'",
        topology.fleet,
        ", ".join(topology.task_queues),
    )
    async with asyncio.TaskGroup() as tg:
        for worker in workers:
            tg.create_task(worker.run())

if __name__ == "__main__":
    asyncio.run(main())
