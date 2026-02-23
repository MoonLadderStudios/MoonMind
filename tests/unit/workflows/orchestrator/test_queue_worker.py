"""Unit tests for orchestrator DB queue worker behavior."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

from moonmind.workflows.agent_queue.job_types import ORCHESTRATOR_RUN_JOB_TYPE
from moonmind.workflows.orchestrator.queue_worker import (
    ClaimedJob,
    OrchestratorQueueWorker,
    QueueWorkerConfig,
)


class _FakeQueueClient:
    """Minimal in-memory queue client for orchestrator worker tests."""

    def __init__(
        self,
        *,
        claim_jobs: list[ClaimedJob] | None = None,
        heartbeat_payloads: list[dict[str, Any]] | None = None,
        should_fail_job: str | None = None,
    ) -> None:
        self._jobs = list(claim_jobs or [])
        self._heartbeat_payloads = heartbeat_payloads or []
        self.should_fail_job = should_fail_job
        self.claim_calls: list[dict[str, object]] = []
        self.fail_calls: list[tuple[UUID, str]] = []
        self.complete_calls: list[tuple[UUID, str]] = []
        self.cancel_acks: list[tuple[UUID, str | None]] = []
        self.append_events: list[dict[str, object]] = []
        self.heartbeats: list[UUID] = []

    async def claim_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        allowed_types: tuple[str, ...],
        worker_capabilities: tuple[str, ...],
    ) -> ClaimedJob | None:
        self.claim_calls.append(
            {
                "worker_id": worker_id,
                "lease_seconds": lease_seconds,
                "allowed_types": allowed_types,
                "worker_capabilities": worker_capabilities,
            }
        )
        return self._jobs.pop(0) if self._jobs else None

    async def heartbeat(
        self, *, job_id: UUID, worker_id: str, lease_seconds: int
    ) -> dict[str, Any]:
        del worker_id, lease_seconds
        self.heartbeats.append(job_id)
        if self._heartbeat_payloads:
            return self._heartbeat_payloads.pop(0)
        return {}

    async def ack_cancel(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        message: str | None = None,
    ) -> dict[str, Any]:
        del worker_id
        self.cancel_acks.append((job_id, message))
        return {"id": str(job_id)}

    async def complete_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        result_summary: str,
    ) -> None:
        del worker_id
        self.complete_calls.append((job_id, result_summary))

    async def fail_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        error_message: str,
    ) -> None:
        del worker_id
        self.fail_calls.append((job_id, error_message))
        if self.should_fail_job:
            raise RuntimeError(self.should_fail_job)

    async def append_event(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        del worker_id
        self.append_events.append(
            {
                "job_id": job_id,
                "level": level,
                "message": message,
                "payload": payload,
            }
        )


def _worker_config() -> QueueWorkerConfig:
    return QueueWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token="worker-token",
        poll_interval_ms=1,
        lease_seconds=3,
        allowed_types=(ORCHESTRATOR_RUN_JOB_TYPE,),
        worker_capabilities=("orchestrator",),
    )


def test_run_forever_fails_jobs_without_stopping() -> None:
    """Unexpected errors in _process_job should be marked as failed and swallowed."""

    run_id = uuid4()
    job = ClaimedJob(id=run_id, type=ORCHESTRATOR_RUN_JOB_TYPE, payload={})
    queue = _FakeQueueClient(claim_jobs=[job])
    worker = OrchestratorQueueWorker(config=_worker_config(), queue_client=queue)

    stop_event = asyncio.Event()

    async def _crashing_job(_: ClaimedJob) -> None:
        stop_event.set()
        raise RuntimeError("boom")

    worker._process_job = _crashing_job  # type: ignore[method-assign]

    asyncio.run(worker.run_forever(stop_event=stop_event))

    assert queue.fail_calls == [
        (run_id, "orchestrator worker failed while processing queue job"),
    ]


def test_process_job_rejects_invalid_payload_generically() -> None:
    """Payload parsing errors should fail with a non-detailed user-facing message."""

    bad_job = ClaimedJob(
        id=uuid4(),
        type=ORCHESTRATOR_RUN_JOB_TYPE,
        payload={"steps": []},
    )
    queue = _FakeQueueClient()
    worker = OrchestratorQueueWorker(config=_worker_config(), queue_client=queue)

    asyncio.run(worker._process_job(bad_job))

    assert queue.fail_calls == [
        (bad_job.id, "invalid orchestrator payload"),
    ]
    assert queue.append_events == []


def test_heartbeat_loop_marks_job_cancelled_when_api_reports_cancel_request(
    monkeypatch,
) -> None:
    """Heartbeat responses with cancelRequestedAt should mark a cancellation request."""

    async def _immediate_timeout(coro: object, *_args: object, **_kwargs: object) -> None:
        if hasattr(coro, "close"):
            coro.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", _immediate_timeout)

    queue = _FakeQueueClient(
        heartbeat_payloads=[
            {"job": {"cancelRequestedAt": "2026-01-01T00:00:00Z"}},
        ]
    )
    worker = OrchestratorQueueWorker(config=_worker_config(), queue_client=queue)
    stop_event = asyncio.Event()
    cancel_event = asyncio.Event()

    async def _run() -> None:
        await worker._heartbeat_loop(uuid4(), stop_event, cancel_event)

    asyncio.run(_run())

    assert stop_event.is_set()
    assert cancel_event.is_set()
    assert queue.heartbeats


def test_process_job_acks_cancellation_after_step_execution(monkeypatch) -> None:
    """When cancellation is detected, the run should be acknowledged and not completed."""

    class _FakePlanStep:
        def __init__(self, ready: asyncio.Event) -> None:
            self.ready = ready

        async def __call__(self, _run_id: UUID, _step_name: str) -> None:
            await self.ready.wait()

    ready = asyncio.Event()
    fake_execute_step = _FakePlanStep(ready)

    queue = _FakeQueueClient()
    worker = OrchestratorQueueWorker(config=_worker_config(), queue_client=queue)

    from moonmind.workflows.orchestrator import queue_worker


    async def _fake_heartbeat_loop(
        job_id: UUID,
        stop_event: asyncio.Event,
        cancel_event: asyncio.Event,
    ) -> None:
        cancel_event.set()
        stop_event.set()
        ready.set()

    monkeypatch.setattr(
        queue_worker,
        "_execute_plan_step_async",
        fake_execute_step,
    )
    worker._heartbeat_loop = _fake_heartbeat_loop  # type: ignore[method-assign]
    run_id = uuid4()
    job = ClaimedJob(
        id=run_id,
        type=ORCHESTRATOR_RUN_JOB_TYPE,
        payload={
            "runId": str(run_id),
            "steps": ["analyze"],
            "includeRollback": False,
        },
    )

    asyncio.run(worker._process_job(job))

    assert queue.cancel_acks == [
        (run_id, f"orchestrator run {run_id} cancelled"),
    ]
    assert queue.complete_calls == []
    assert queue.fail_calls == []
