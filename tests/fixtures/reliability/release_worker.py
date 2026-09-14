"""Minimal immutable worker image for the real release recovery journey."""

import asyncio
import json
import os

from temporalio.client import Client
from temporalio.common import VersioningBehavior, WorkerDeploymentVersion
from temporalio.worker import UnsandboxedWorkflowRunner, Worker, WorkerDeploymentConfig

from moonmind.release_identity import installed_release
from moonmind.workflows.temporal.worker_lifecycle import serve_workers
from moonmind.workflows.temporal.workflows.release_canary import (
    ReleaseCanaryWorkflow,
    inspect_release_activity,
)


async def main():
    release = installed_release()
    client = await Client.connect(os.environ["TEMPORAL_ADDRESS"])
    ready = False

    async def health(reader, writer):
        await reader.read(4096)
        body = json.dumps({"ready": ready, "buildId": release["digest"]}).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()

    async def mark_ready():
        nonlocal ready
        ready = True

    server = await asyncio.start_server(health, "0.0.0.0", 8080)
    workers = [
        Worker(
            client,
            task_queue=queue,
            workflows=[ReleaseCanaryWorkflow],
            activities=[inspect_release_activity],
            workflow_runner=UnsandboxedWorkflowRunner(),
            deployment_config=WorkerDeploymentConfig(
                version=WorkerDeploymentVersion(
                    os.environ["DEPLOYMENT"], release["digest"]
                ),
                use_worker_versioning=True,
                default_versioning_behavior=VersioningBehavior.AUTO_UPGRADE,
            ),
        )
        for queue in json.loads(os.environ["QUEUES"])
    ]
    async with server:
        await serve_workers(workers, ready=mark_ready)


asyncio.run(main())
