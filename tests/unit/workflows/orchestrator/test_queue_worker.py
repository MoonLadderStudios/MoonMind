"""Unit tests for orchestrator DB queue worker behavior."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from moonmind.workflows.agent_queue.job_types import (
    ORCHESTRATOR_RUN_JOB_TYPE,
    ORCHESTRATOR_TASK_JOB_TYPE,
)
from moonmind.workflows.orchestrator.queue_worker import (
    ClaimedJob,
    OrchestratorQueueWorker,
    QueueWorkerConfig,
    TaskRuntimeStep,
)
from moonmind.workflows.orchestrator import queue_worker


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
        allowed_types=(ORCHESTRATOR_RUN_JOB_TYPE, ORCHESTRATOR_TASK_JOB_TYPE),
        worker_capabilities=("orchestrator",),
        queue_api_retry_attempts=3,
        queue_api_retry_delay_seconds=0.01,
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
        (run_id, "orchestrator run execution failed"),
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

    async def _immediate_timeout(
        coro: object, *_args: object, **_kwargs: object
    ) -> None:
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
        (run_id, "orchestrator run cancelled"),
    ]
    assert queue.complete_calls == []
    assert queue.fail_calls == []


def test_process_task_job_completes_when_steps_succeed(monkeypatch) -> None:
    """orchestrator_task jobs should complete when all task steps succeed."""

    run_id = uuid4()
    job = ClaimedJob(
        id=uuid4(),
        type=ORCHESTRATOR_TASK_JOB_TYPE,
        payload={
            "taskId": str(run_id),
            "steps": [
                {
                    "stepId": "step-1",
                    "title": "Step 1",
                    "instructions": "Run update",
                    "skillId": "update-moonmind",
                    "skillArgs": {},
                }
            ],
        },
    )

    class _FakeSink:
        async def record_task_status(self, **_kwargs):
            return None

        async def record_step_status(self, **_kwargs):
            return None

        async def record_artifact(self, **_kwargs):
            return None

        async def flush(self):
            return None

    queue = _FakeQueueClient()
    worker = OrchestratorQueueWorker(config=_worker_config(), queue_client=queue)
    monkeypatch.setattr(
        "moonmind.workflows.orchestrator.queue_worker._build_state_sink",
        lambda: _FakeSink(),
    )

    async def _noop_step(*_args, **_kwargs):
        return None

    async def _noop_heartbeat(
        _job_id: UUID,
        stop_event: asyncio.Event,
        _cancel_event: asyncio.Event,
    ) -> None:
        await stop_event.wait()

    worker._execute_task_runtime_step = _noop_step  # type: ignore[method-assign]
    worker._heartbeat_loop = _noop_heartbeat  # type: ignore[method-assign]

    asyncio.run(worker._process_job(job))

    assert queue.complete_calls == [
        (job.id, f"orchestrator task {run_id} completed"),
    ]
    assert queue.fail_calls == []


def test_parse_task_payload_requires_task_id() -> None:
    payload = {
        "steps": [
            {
                "stepId": "step-1",
                "title": "Only step",
                "instructions": "Run",
                "skillId": "update-moonmind",
            }
        ]
    }

    with pytest.raises(ValueError, match="taskId is required"):
        OrchestratorQueueWorker._parse_task_payload(payload)


def test_parse_task_payload_rejects_runid_and_stepid_fallbacks() -> None:
    run_id = str(uuid4())
    with pytest.raises(ValueError, match="taskId is required"):
        OrchestratorQueueWorker._parse_task_payload(
            {
                "runId": run_id,
                "steps": [
                    {
                        "id": "legacy-step",
                        "title": "legacy",
                        "instructions": "Run",
                        "skillId": "update-moonmind",
                    }
                ],
            }
        )

    with pytest.raises(ValueError, match="stepId is required"):
        OrchestratorQueueWorker._parse_task_payload(
            {
                "taskId": run_id,
                "steps": [
                    {
                        "id": "legacy-step",
                        "title": "legacy",
                        "instructions": "Run",
                        "skillId": "update-moonmind",
                    }
                ],
            }
        )


def test_parse_task_payload_reads_attempt_and_step_index() -> None:
    run_id = str(uuid4())
    payload_task_id, steps = OrchestratorQueueWorker._parse_task_payload(
        {
            "taskId": run_id,
            "steps": [
                {
                    "stepId": "step-1",
                    "title": "Index check",
                    "instructions": "Run",
                    "skillId": "update-moonmind",
                    "stepIndex": 4,
                    "attempt": 3,
                }
            ],
        }
    )

    assert str(payload_task_id) == run_id
    assert len(steps) == 1
    assert steps[0].step_index == 4
    assert steps[0].attempt == 3


def test_execute_task_runtime_step_uses_env_for_step_payload(monkeypatch) -> None:
    task_id = uuid4()
    step = TaskRuntimeStep(
        step_id="step-1",
        title="Step 1",
        instructions="Use safe command",
        skill_id="update-moonmind",
        skill_args={"repo": "."},
        step_index=2,
        attempt=4,
    )
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*command: object, **kwargs: object):
        captured["command"] = list(command)
        captured["env"] = dict(kwargs.get("env") or {})
        del command

        class FakeProcess:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"task complete", b"")

        return FakeProcess()

    def fake_write_text(self, _run_id: UUID, path: str, text: str, **_kwargs: object):
        captured["artifact_path"] = path
        captured["artifact_text"] = text
        return SimpleNamespace(path=path, size_bytes=len(text), checksum="checksum")

    async def _noop_heartbeat(
        _job_id: UUID,
        stop_event: asyncio.Event,
        _cancel_event: asyncio.Event,
    ) -> None:
        await stop_event.wait()

    class _FakeSink:
        async def record_task_status(self, **_kwargs) -> None:
            return None

        async def record_step_status(self, **_kwargs) -> None:
            return None

        async def record_artifact(self, **_kwargs) -> None:
            return None

        async def flush(self) -> None:
            return None

    queue = _FakeQueueClient()
    worker = OrchestratorQueueWorker(config=_worker_config(), queue_client=queue)
    worker._heartbeat_loop = _noop_heartbeat  # type: ignore[method-assign]
    monkeypatch.setattr(
        queue_worker.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr(queue_worker.ArtifactStorage, "write_text", fake_write_text)

    asyncio.run(
        worker._execute_task_runtime_step(
            task_id=task_id,
            step=step,
            state_sink=_FakeSink(),
        )
    )

    command = captured["command"]
    assert command is not None
    assert "--skill-args-json" not in command
    context = json.loads(
        str(captured["env"][queue_worker._TASK_STEP_EXECUTION_ENV])
    )
    assert context["skillArgs"] == {"repo": "."}
    assert context["instructions"] == "Use safe command"
    assert context["stepIndex"] == 2
    assert context["attempt"] == 4
    assert "idx2" in str(captured["artifact_path"])
    assert "att4" in str(captured["artifact_path"])
