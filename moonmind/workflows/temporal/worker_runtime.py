"""Temporal worker runtime entrypoint."""

import asyncio
import logging

from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker

from moonmind.config.settings import settings
from moonmind.workflows.temporal.workers import (
    WORKFLOW_FLEET,
    build_worker_activity_bindings,
    describe_configured_worker,
)

logger = logging.getLogger(__name__)


@workflow.defn(name="MoonMind.Run")
class MoonMindRun:
    """Placeholder for MoonMind.Run workflow."""

    @workflow.run
    async def run(self, *args, **kwargs) -> None:
        pass


@workflow.defn(name="MoonMind.ManifestIngest")
class MoonMindManifestIngest:
    """Placeholder for MoonMind.ManifestIngest workflow."""

    @workflow.run
    async def run(self, *args, **kwargs) -> None:
        pass


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

    if topology.fleet == WORKFLOW_FLEET:
        workflows = [MoonMindRun, MoonMindManifestIngest]
    else:
        bindings = build_worker_activity_bindings(fleet=topology.fleet)
        activities = [bind.handler for bind in bindings]

    worker = Worker(
        client,
        task_queue=topology.task_queues[0],
        workflows=workflows,
        activities=activities,
        max_concurrent_activities=topology.concurrency_limit,
    )

    logger.info("Worker started, polling task queues...")
    await worker.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async())
