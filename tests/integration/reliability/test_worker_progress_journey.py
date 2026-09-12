"""A blocked source scan must not starve Temporal work on the same event loop."""

import asyncio
import threading
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from temporalio import activity, workflow
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal import worker_healthcheck as health
from moonmind.workflows.temporal.worker_code_identity import WorkerCodeIdentity
from tests.integration.reliability.test_release_routing_journey import connect

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn
class WorkerProgressProbe:
    @workflow.run
    async def run(self) -> str:
        return await workflow.execute_activity(
            "reliability.worker_progress",
            start_to_close_timeout=timedelta(seconds=10),
            heartbeat_timeout=timedelta(seconds=5),
        )


async def test_blocked_hash_preserves_real_temporal_progress_and_bounded_readiness(
    monkeypatch,
):
    client = await connect()
    scanning, release = threading.Event(), threading.Event()
    scans = []

    def scan():
        scans.append(1)
        scanning.set()
        assert release.wait(
            20
        ), "the event loop did not progress while hashing was blocked"
        return WorkerCodeIdentity(revision="candidate")

    @activity.defn(name="reliability.worker_progress")
    async def progress():
        activity.heartbeat("event loop is responsive")
        await asyncio.sleep(0)
        return "completed while source scan remained blocked"

    monkeypatch.setenv("WORKER_HEALTHCHECK_PORT", "0")
    monkeypatch.setattr(health, "_IDENTITY_REFRESH_SECONDS", 0.01)
    monkeypatch.setattr(health, "resolve_checkout_code_identity", scan)
    state = health.WorkerHealthState(True, True, True, code_revision="candidate")
    server = await health.start_healthcheck_server(state)
    queue = "worker-progress-" + uuid4().hex
    try:
        async with Worker(
            client,
            task_queue=queue,
            workflows=[WorkerProgressProbe],
            activities=[progress],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            assert await asyncio.to_thread(scanning.wait, 2)
            result = await client.execute_workflow(
                WorkerProgressProbe.run,
                id=queue,
                task_queue=queue,
                execution_timeout=timedelta(seconds=15),
            )
            assert result == "completed while source scan remained blocked"
            assert not release.is_set()
            async with httpx.AsyncClient(timeout=2) as http:
                url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/readyz"
                assert (await http.get(url)).status_code == 200
                state.identity_checked_at -= health._IDENTITY_MAX_AGE_SECONDS + 1
                expired = await http.get(url)
                assert expired.status_code == 503
                assert expired.json()["reasonCode"] == "code_identity_expired"
            assert len(scans) == 1
    finally:
        release.set()
        server.close()
        await server.wait_closed()
