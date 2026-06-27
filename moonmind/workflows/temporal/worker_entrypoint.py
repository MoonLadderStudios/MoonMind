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
from moonmind.workflows.temporal.hard_switch_cutover import registered_user_workflow_type
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.agent_session import (
    MoonMindAgentSessionWorkflow,
)
from moonmind.workflows.temporal.workflows.managed_session_reconcile import (
    MoonMindManagedSessionReconcileWorkflow,
)
from moonmind.workflows.temporal.workflows.managed_runtime_workspace_cleanup import (
    MoonMindManagedRuntimeWorkspaceCleanupWorkflow,
)
from moonmind.workflows.temporal.workflows.oauth_session import (
    MoonMindOAuthSessionWorkflow,
)
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    MoonMindProviderProfileManagerWorkflow,
)
from moonmind.workflows.temporal.workflows.run import (
    MoonMindUserWorkflow,
)
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
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
        registered_user_workflow_type(settings.temporal)
        workflows.extend(
            [
                MoonMindUserWorkflow,
                MoonMindProviderProfileManagerWorkflow,
                MoonMindAgentSessionWorkflow,
                MoonMindManagedSessionReconcileWorkflow,
                MoonMindManagedRuntimeWorkspaceCleanupWorkflow,
                MoonMindAgentRun,
                MoonMindOAuthSessionWorkflow,
                MoonMindMergeAutomationWorkflow,
            ]
        )
        # Import ManifestIngest workflow if it exists
        try:
            from moonmind.workflows.temporal.workflows.manifest_ingest import (
                MoonMindManifestIngestWorkflow,
            )

            workflows.append(MoonMindManifestIngestWorkflow)
        except ImportError:
            logger.info(
                "Optional MoonMindManifestIngestWorkflow not available; skipping registration"
            )

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
