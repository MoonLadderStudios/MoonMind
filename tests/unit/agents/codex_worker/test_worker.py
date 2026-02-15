"""Unit tests for codex worker daemon loop and queue client behavior."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import pytest

from moonmind.agents.codex_worker.handlers import ArtifactUpload, WorkerExecutionResult
from moonmind.agents.codex_worker.worker import (
    ClaimedJob,
    CodexWorker,
    CodexWorkerConfig,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


class FakeQueueClient:
    """In-memory queue client stub for worker tests."""

    def __init__(self, jobs: list[ClaimedJob | None] | None = None) -> None:
        self.jobs = list(jobs or [])
        self.claim_calls: list[dict[str, object]] = []
        self.heartbeats: list[str] = []
        self.completed: list[tuple[str, str | None]] = []
        self.failed: list[str] = []
        self.uploaded: list[str] = []
        self.events: list[dict[str, object]] = []

    async def claim_job(
        self,
        *,
        worker_id,
        lease_seconds,
        allowed_types=None,
        worker_capabilities=None,
    ):
        self.claim_calls.append(
            {
                "worker_id": worker_id,
                "lease_seconds": lease_seconds,
                "allowed_types": tuple(allowed_types or ()),
                "worker_capabilities": tuple(worker_capabilities or ()),
            }
        )
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
        self.events.append(
            {
                "job_id": str(job_id),
                "level": level,
                "message": message,
                "payload": payload or {},
            }
        )


class FakeHandler:
    """Handler stub returning configured results."""

    def __init__(self, result: WorkerExecutionResult | Exception) -> None:
        self.result = result
        self.calls: list[str] = []

    async def handle(self, *, job_id, payload):
        self.calls.append("codex_exec")
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def handle_skill(self, *, job_id, payload, selected_skill, fallback=False):
        self.calls.append(f"codex_skill:{selected_skill}:{fallback}")
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

    job = ClaimedJob(
        id=uuid4(),
        type="codex_exec",
        payload={"repository": "a/b", "instruction": "run"},
    )
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
    assert any(event["message"] == "Worker claimed job" for event in queue.events)
    assert any(event["message"] == "Job completed" for event in queue.events)
    claimed = next(
        event for event in queue.events if event["message"] == "Worker claimed job"
    )
    payload = claimed["payload"]
    assert isinstance(payload, dict)
    assert payload["selectedSkill"] == "speckit"
    assert payload["executionPath"] == "direct_only"


async def test_run_once_skips_empty_artifacts(tmp_path: Path) -> None:
    """Zero-byte artifacts should be skipped to avoid upload validation failures."""

    empty_patch = tmp_path / "changes.patch"
    empty_patch.write_text("", encoding="utf-8")
    log_file = tmp_path / "run.log"
    log_file.write_text("ok", encoding="utf-8")

    job = ClaimedJob(
        id=uuid4(),
        type="codex_exec",
        payload={"repository": "a/b", "instruction": "run"},
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="done",
            error_message=None,
            artifacts=(
                ArtifactUpload(path=empty_patch, name="patches/changes.patch"),
                ArtifactUpload(path=log_file, name="logs/run.log"),
            ),
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
    assert queue.uploaded == ["logs/run.log"]
    assert len(queue.completed) == 1
    assert queue.failed == []


async def test_run_once_unsupported_type_fails_job(tmp_path: Path) -> None:
    """Unsupported claimed job types should be failed explicitly."""

    job = ClaimedJob(id=uuid4(), type="report", payload={})
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
    assert any(event["message"] == "Unsupported job type" for event in queue.events)


async def test_run_once_codex_skill_routes_through_skill_path(tmp_path: Path) -> None:
    """`codex_skill` jobs should execute through the skills-first path."""

    job = ClaimedJob(
        id=uuid4(),
        type="codex_skill",
        payload={
            "skillId": "speckit",
            "inputs": {"repo": "MoonLadderStudios/MoonMind", "instruction": "run"},
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="skill ok", error_message=None)
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
    assert len(queue.completed) == 1
    assert handler.calls == ["codex_skill:speckit:False"]
    claimed = next(
        event for event in queue.events if event["message"] == "Worker claimed job"
    )
    payload = claimed["payload"]
    assert isinstance(payload, dict)
    assert payload["executionPath"] == "skill"
    assert payload["usedSkills"] is True
    assert payload["usedFallback"] is False


async def test_run_once_codex_skill_disallowed_skill_fails(tmp_path: Path) -> None:
    """Disallowed skills should fail before handler execution."""

    job = ClaimedJob(
        id=uuid4(),
        type="codex_skill",
        payload={"skillId": "custom-skill", "inputs": {"repo": "Moon/Mind"}},
    )
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
        allowed_skills=("speckit",),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert len(queue.failed) == 1
    assert "skill not allowlisted" in queue.failed[0]
    assert handler.calls == []


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
    task = asyncio.create_task(
        worker._heartbeat_loop(job_id=uuid4(), stop_event=stop_event)
    )
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
    monkeypatch.setenv("MOONMIND_CODEX_MODEL", "gpt-5-codex")
    monkeypatch.setenv("MOONMIND_CODEX_EFFORT", "high")
    monkeypatch.setenv("MOONMIND_WORKER_CAPABILITIES", "codex,git,codex")

    config = CodexWorkerConfig.from_env()

    assert config.moonmind_url == "http://localhost:5000"
    assert config.worker_id == "executor-01"
    assert config.worker_token == "token-123"
    assert config.poll_interval_ms == 2500
    assert config.lease_seconds == 90
    assert str(config.workdir) == "/tmp/moonmind-worker"
    assert config.default_codex_model == "gpt-5-codex"
    assert config.default_codex_effort == "high"
    assert config.worker_capabilities == ("codex", "git")


async def test_config_from_env_uses_defaults(monkeypatch) -> None:
    """Unset optional values should fall back to defaults."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.delenv("MOONMIND_WORKER_ID", raising=False)
    monkeypatch.delenv("MOONMIND_WORKER_TOKEN", raising=False)
    monkeypatch.delenv("MOONMIND_POLL_INTERVAL_MS", raising=False)
    monkeypatch.delenv("MOONMIND_LEASE_SECONDS", raising=False)
    monkeypatch.delenv("MOONMIND_WORKDIR", raising=False)
    monkeypatch.delenv("MOONMIND_CODEX_MODEL", raising=False)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.delenv("MOONMIND_CODEX_EFFORT", raising=False)
    monkeypatch.delenv("CODEX_MODEL_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("MODEL_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("MOONMIND_WORKER_CAPABILITIES", raising=False)

    config = CodexWorkerConfig.from_env()

    assert config.worker_token is None
    assert config.poll_interval_ms == 1500
    assert config.lease_seconds == 120
    assert str(config.workdir) == "var/worker"
    assert config.default_skill == "speckit"
    assert config.allowed_skills == ("speckit",)
    assert config.default_codex_model is None
    assert config.default_codex_effort is None
    assert config.allowed_types == ("codex_exec", "codex_skill")
    assert config.worker_capabilities == ()


async def test_config_from_env_uses_codex_fallback_env_vars(monkeypatch) -> None:
    """Legacy env defaults should hydrate model/effort when MoonMind overrides unset."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.delenv("MOONMIND_CODEX_MODEL", raising=False)
    monkeypatch.delenv("MOONMIND_CODEX_EFFORT", raising=False)
    monkeypatch.setenv("CODEX_MODEL", "gpt-5.3-codex")
    monkeypatch.setenv("CODEX_MODEL_REASONING_EFFORT", "xhigh")

    config = CodexWorkerConfig.from_env()

    assert config.default_codex_model == "gpt-5.3-codex"
    assert config.default_codex_effort == "xhigh"


async def test_run_once_claims_with_configured_policy_fields(tmp_path: Path) -> None:
    """Claim request should forward local policy hints without adding repo overrides."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-9",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=75,
        workdir=tmp_path,
        allowed_types=("codex_exec", "codex_skill"),
        worker_capabilities=("codex", "git"),
    )
    queue = FakeQueueClient(jobs=[None])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="ok", error_message=None)
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is False
    assert len(queue.claim_calls) == 1
    claim = queue.claim_calls[0]
    assert claim["worker_id"] == "worker-9"
    assert claim["lease_seconds"] == 75
    assert claim["allowed_types"] == ("codex_exec", "codex_skill")
    assert claim["worker_capabilities"] == ("codex", "git")
