import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from moonmind.config.settings import settings
from moonmind.workflows.temporal.workers import (
    describe_configured_worker,
    build_worker_activity_bindings,
    WORKFLOW_FLEET,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

async def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    topology = describe_configured_worker()
    
    client = await Client.connect(
        settings.temporal.address,
        namespace=settings.temporal.namespace,
    )
    
    workflows = []
    if topology.fleet == WORKFLOW_FLEET:
        workflows.append(MoonMindRunWorkflow)
        # Import ManifestIngest workflow if it exists
        try:
            from moonmind.workflows.temporal.workflows.manifest_ingest import MoonMindManifestIngestWorkflow
            workflows.append(MoonMindManifestIngestWorkflow)
        except ImportError:
            pass

    bindings = build_worker_activity_bindings(fleet=topology.fleet)
    activities = [b.handler for b in bindings]

    worker = Worker(
        client,
        task_queue=topology.task_queues[0],
        workflows=workflows,
        activities=activities,
        max_concurrent_activities=topology.concurrency_limit or 100,
    )
    
    logger.info(f"Starting Temporal worker for fleet '{topology.fleet}' on queue '{topology.task_queues[0]}'")
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
