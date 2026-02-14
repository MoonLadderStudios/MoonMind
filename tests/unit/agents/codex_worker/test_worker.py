"""Unit tests for codex worker daemon loop and queue client behavior."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import pytest

from moonmind.agents.codex_worker.handlers import ArtifactUpload, WorkerExecutionResult
from moonmind.agents.codex_worker.worker import ClaimedJob, CodexWorker, CodexWorkerConfig

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


class FakeQueueClient:
    """In-memory queue client stub for worker tests."""

    def __init__(self, jobs: list[ClaimedJob | None] | None = None) -> None:
        self.jobs = list(jobs or [])
        self.heartbeats: list[str] = []
        self.completed: list[tuple[str, str | None]] = []
        self.failed: list[str] = []
        self.uploaded: list[str] = []
        self.events: list[str] = []

    async def claim_job(
        self,
        *,
        worker_id,
        lease_seconds,
        allowed_types=None,
        worker_capabilities=None,
    ):
        if not self.jobs:
            return None
        return self.jobs.pop(0)

    async def heartbeat(self, *, job_id, worker_id, lease_seconds):
        self.heartbeats.append(str(job_id))

    async def complete_job(self, *, job_id, worker_id, result_summary):
        self.completed.append((str(job_id), result_summary))

    async def fail_job(self, *, job_id, worker_id, error_message, retryable=False):
        self.failed.append(f"{job_id}:{error_message}")

    async def upload_artifact(self, *, job_id, worker_id, artifact):
        self.uploaded.append(artifact.name)

    async def append_event(self, *, job_id, worker_id, level, message, payload=None):
        self.events.append(f"{job_id}:{level}:{message}")


class FakeHandler:
    """Handler stub returning configured results."""

    def __init__(self, result: WorkerExecutionResult | Exception) -> None:
        self.result = result

    async def handle(self, *, job_id, payload):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_run_once_returns_false_when_no_job() -> None:
    """No claim should produce a no-work cycle."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=Path("/tmp/worker"),
    )
    queue = FakeQueueClient(jobs=[None])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="ok", error_message=None)
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is False
    assert queue.completed == []


async def test_run_once_success_uploads_and_completes(tmp_path: Path) -> None:
    """Successful handler execution should upload artifacts and complete job."""

    artifact_path = tmp_path / "result.log"
    artifact_path.write_text("ok", encoding="utf-8")

    job = ClaimedJob(id=uuid4(), type="codex_exec", payload={"repository": "a/b", "instruction": "run"})
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="done",
            error_message=None,
            artifacts=(ArtifactUpload(path=artifact_path, name="logs/result.log"),),
        )
    )
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert queue.uploaded == ["logs/result.log"]
    assert len(queue.completed) == 1
    assert queue.failed == []
    assert any("Worker claimed job" in value for value in queue.events)
    assert any("Job completed" in value for value in queue.events)


async def test_run_once_unsupported_type_fails_job(tmp_path: Path) -> None:
    """Unsupported claimed job types should be failed explicitly."""

    job = ClaimedJob(id=uuid4(), type="codex_skill", payload={})
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert len(queue.failed) == 1
    assert "unsupported job type" in queue.failed[0]
    assert any("Unsupported job type" in value for value in queue.events)


async def test_heartbeat_loop_runs_on_lease_interval(tmp_path: Path) -> None:
    """Heartbeat loop should emit renewals at roughly lease/3 cadence."""

    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=3,
        workdir=tmp_path,
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    stop_event = asyncio.Event()
    task = asyncio.create_task(worker._heartbeat_loop(job_id=uuid4(), stop_event=stop_event))
    await asyncio.sleep(2.3)
    stop_event.set()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert len(queue.heartbeats) >= 2


async def test_config_from_env_defaults_and_overrides(monkeypatch) -> None:
    """Worker config should respect documented defaults and env overrides."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000/")
    monkeypatch.setenv("MOONMIND_WORKER_ID", "executor-01")
    monkeypatch.setenv("MOONMIND_WORKER_TOKEN", "token-123")
    monkeypatch.setenv("MOONMIND_POLL_INTERVAL_MS", "2500")
    monkeypatch.setenv("MOONMIND_LEASE_SECONDS", "90")
    monkeypatch.setenv("MOONMIND_WORKDIR", "/tmp/moonmind-worker")
    monkeypatch.setenv("MOONMIND_WORKER_CAPABILITIES", "codex,git,codex")

    config = CodexWorkerConfig.from_env()

    assert config.moonmind_url == "http://localhost:5000"
    assert config.worker_id == "executor-01"
    assert config.worker_token == "token-123"
    assert config.poll_interval_ms == 2500
    assert config.lease_seconds == 90
    assert str(config.workdir) == "/tmp/moonmind-worker"
    assert config.worker_capabilities == ("codex", "git")


async def test_config_from_env_uses_defaults(monkeypatch) -> None:
    """Unset optional values should fall back to defaults."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.delenv("MOONMIND_WORKER_ID", raising=False)
    monkeypatch.delenv("MOONMIND_WORKER_TOKEN", raising=False)
    monkeypatch.delenv("MOONMIND_POLL_INTERVAL_MS", raising=False)
    monkeypatch.delenv("MOONMIND_LEASE_SECONDS", raising=False)
    monkeypatch.delenv("MOONMIND_WORKDIR", raising=False)
    monkeypatch.delenv("MOONMIND_WORKER_CAPABILITIES", raising=False)

    config = CodexWorkerConfig.from_env()

    assert config.worker_token is None
    assert config.poll_interval_ms == 1500
    assert config.lease_seconds == 120
    assert str(config.workdir) == "var/worker"
    assert config.worker_capabilities == ()
