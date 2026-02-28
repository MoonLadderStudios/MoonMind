"""Unit tests for codex worker daemon loop and queue client behavior."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence
from uuid import uuid4

import pytest

from moonmind.agents.codex_worker.handlers import (
    ArtifactUpload,
    CodexExecHandler,
    CommandCancelledError,
    CommandResult,
    WorkerExecutionResult,
)
from moonmind.agents.codex_worker.worker import (
    ClaimedJob,
    CodexWorker,
    CodexWorkerConfig,
    PreparedTaskWorkspace,
    QueueApiClient,
    QueueClaimResult,
    QueueClientError,
    QueueHeartbeatResult,
    QueueSystemStatus,
    ResolvedTaskStep,
)
from moonmind.config.settings import settings

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


class FakeQueueClient:
    """In-memory queue client stub for worker tests."""

    def __init__(
        self,
        jobs: list[ClaimedJob | None] | None = None,
        system_status: QueueSystemStatus | None = None,
    ) -> None:
        self.jobs = list(jobs or [])
        self.claim_calls: list[dict[str, object]] = []
        self.heartbeats: list[str] = []
        self.heartbeat_payloads: list[dict[str, object]] = []
        self.live_session_reports: list[dict[str, object]] = []
        self.live_session_heartbeats: list[str] = []
        self.live_session_state: dict[str, object] | None = None
        self.completed: list[tuple[str, str | None]] = []
        self.completed_finish_payloads: list[dict[str, object]] = []
        self.failed: list[str] = []
        self.failed_retryable: list[bool] = []
        self.failed_finish_payloads: list[dict[str, object]] = []
        self.cancel_acks: list[tuple[str, str | None]] = []
        self.cancel_ack_finish_payloads: list[dict[str, object]] = []
        self.uploaded: list[str] = []
        self.events: list[dict[str, object]] = []
        self.cancel_requested_at: str | None = None
        self.submitted_proposals: list[dict[str, object]] = []
        now = datetime.now(UTC)
        self.system_status = system_status or QueueSystemStatus(
            workers_paused=False,
            mode=None,
            reason=None,
            version=1,
            requested_at=None,
            updated_at=now,
        )

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
        job = self.jobs.pop(0) if self.jobs else None
        return QueueClaimResult(job=job, system=self.system_status)

    async def heartbeat(self, *, job_id, worker_id, lease_seconds):
        self.heartbeats.append(str(job_id))
        payload = {"id": str(job_id)}
        if self.cancel_requested_at:
            payload["cancelRequestedAt"] = self.cancel_requested_at
        self.heartbeat_payloads.append(payload)
        return QueueHeartbeatResult(job=payload, system=self.system_status)

    async def ack_cancel(
        self,
        *,
        job_id,
        worker_id,
        message=None,
        finish_outcome_code=None,
        finish_outcome_stage=None,
        finish_outcome_reason=None,
        finish_summary=None,
    ):
        self.cancel_acks.append((str(job_id), message))
        self.cancel_ack_finish_payloads.append(
            {
                "finishOutcomeCode": finish_outcome_code,
                "finishOutcomeStage": finish_outcome_stage,
                "finishOutcomeReason": finish_outcome_reason,
                "finishSummary": finish_summary,
            }
        )
        return {"id": str(job_id), "status": "cancelled"}

    async def complete_job(
        self,
        *,
        job_id,
        worker_id,
        result_summary,
        finish_outcome_code=None,
        finish_outcome_stage=None,
        finish_outcome_reason=None,
        finish_summary=None,
    ):
        self.completed.append((str(job_id), result_summary))
        self.completed_finish_payloads.append(
            {
                "finishOutcomeCode": finish_outcome_code,
                "finishOutcomeStage": finish_outcome_stage,
                "finishOutcomeReason": finish_outcome_reason,
                "finishSummary": finish_summary,
            }
        )

    async def fail_job(
        self,
        *,
        job_id,
        worker_id,
        error_message,
        retryable=False,
        finish_outcome_code=None,
        finish_outcome_stage=None,
        finish_outcome_reason=None,
        finish_summary=None,
    ):
        self.failed.append(f"{job_id}:{error_message}")
        self.failed_retryable.append(bool(retryable))
        self.failed_finish_payloads.append(
            {
                "finishOutcomeCode": finish_outcome_code,
                "finishOutcomeStage": finish_outcome_stage,
                "finishOutcomeReason": finish_outcome_reason,
                "finishSummary": finish_summary,
            }
        )

    async def report_live_session(self, **payload):
        self.live_session_reports.append(dict(payload))
        status = str(payload.get("status") or "").strip().lower()
        worker_id = str(payload.get("worker_id") or "").strip() or None
        if status:
            self.live_session_state = {
                "session": {
                    "status": status,
                    "workerId": worker_id,
                }
            }
        return self.live_session_state or {}

    async def heartbeat_live_session(self, *, job_id, worker_id):
        self.live_session_heartbeats.append(str(job_id))
        return self.live_session_state or {}

    async def get_live_session(self, *, job_id):
        _ = job_id
        return self.live_session_state

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

    async def create_task_proposal(self, *, proposal):
        self.submitted_proposals.append(dict(proposal))
        return {"id": str(uuid4())}


async def test_parse_positive_int_field_rejects_invalid_values() -> None:
    """Queue payload integer parsing should fail fast on invalid values."""

    with pytest.raises(QueueClientError):
        QueueApiClient._parse_positive_int_field(
            node={"attempt": "nope"},
            field_name="attempt",
            default=1,
        )
    with pytest.raises(QueueClientError):
        QueueApiClient._parse_positive_int_field(
            node={"maxAttempts": 0},
            field_name="maxAttempts",
            default=3,
        )


class FailingUploadQueueClient(FakeQueueClient):
    """Queue client stub that simulates artifact upload failures."""

    async def upload_artifact(self, *, job_id, worker_id, artifact):
        raise RuntimeError("artifact upload failed")


class SelectiveFailUploadQueueClient(FakeQueueClient):
    """Queue client stub that fails uploads for a selected artifact name set."""

    def __init__(
        self,
        jobs: list[ClaimedJob | None] | None = None,
        *,
        fail_names: set[str] | None = None,
    ) -> None:
        super().__init__(jobs=jobs)
        self.fail_names = set(fail_names or set())

    async def upload_artifact(self, *, job_id, worker_id, artifact):
        if artifact.name in self.fail_names:
            raise RuntimeError("artifact upload failed")
        await super().upload_artifact(
            job_id=job_id, worker_id=worker_id, artifact=artifact
        )


class FakeHandler:
    """Handler stub returning configured results."""

    def __init__(
        self,
        result: (
            WorkerExecutionResult | Exception | list[WorkerExecutionResult | Exception]
        ),
    ) -> None:
        self.result = result
        self.calls: list[str] = []
        self.exec_payloads: list[dict[str, object]] = []
        self.skill_payloads: list[dict[str, object]] = []
        self._results = list(result) if isinstance(result, list) else None

    def _next_result(self) -> WorkerExecutionResult:
        candidate: WorkerExecutionResult | Exception
        if self._results is None:
            candidate = self.result  # type: ignore[assignment]
        else:
            if not self._results:
                raise RuntimeError("no fake handler result configured for call")
            candidate = self._results.pop(0)
        if isinstance(candidate, Exception):
            raise candidate
        return candidate

    async def handle(
        self, *, job_id, payload, cancel_event=None, output_chunk_callback=None
    ):
        self.calls.append("codex_exec")
        self.exec_payloads.append(dict(payload))
        return self._next_result()

    async def handle_skill(
        self,
        *,
        job_id,
        payload,
        selected_skill,
        fallback=False,
        cancel_event=None,
        output_chunk_callback=None,
    ):
        self.calls.append(f"codex_skill:{selected_skill}:{fallback}")
        self.skill_payloads.append(dict(payload))
        return self._next_result()

    async def _run_command(
        self,
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        output_chunk_callback=None,
    ):
        del cwd, check, env, redaction_values, cancel_event, output_chunk_callback
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[command] $ {' '.join(command)}\n")
        return CommandResult(tuple(command), 0, "", "")


class CumulativeStepLogHandler(FakeHandler):
    """Handler stub that appends each step output into one shared runtime log."""

    def __init__(self, *, workdir_root: Path, segments: Sequence[str]) -> None:
        super().__init__(
            WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
        )
        self._workdir_root = workdir_root
        self._segments = list(segments)
        self._segment_index = 0

    async def handle(
        self, *, job_id, payload, cancel_event=None, output_chunk_callback=None
    ):
        del cancel_event, output_chunk_callback
        self.calls.append("codex_exec")
        self.exec_payloads.append(dict(payload))
        if self._segment_index >= len(self._segments):
            raise RuntimeError("no cumulative step log segment configured")

        segment = self._segments[self._segment_index]
        self._segment_index += 1
        log_path = self._workdir_root / str(job_id) / "artifacts" / "codex_exec.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(segment)
        return WorkerExecutionResult(
            succeeded=True,
            summary=f"step {self._segment_index} ok",
            error_message=None,
            artifacts=(ArtifactUpload(path=log_path, name="logs/codex_exec.log"),),
        )


def _build_execute_stage_payloads() -> tuple[dict[str, object], dict[str, object]]:
    """Return minimal payloads for direct execute-stage tests."""

    canonical_payload: dict[str, object] = {
        "repository": "MoonLadderStudios/MoonMind",
        "targetRuntime": "codex",
        "task": {
            "instructions": "run",
            "skill": {"id": "auto", "args": {}},
            "runtime": {"mode": "codex"},
            "publish": {"mode": "none"},
        },
    }
    source_payload: dict[str, object] = {"workdirMode": "reuse"}
    return canonical_payload, source_payload


def _build_execute_stage_workspace(*, tmp_path: Path, job_id) -> PreparedTaskWorkspace:
    """Create a minimal prepared workspace for direct execute-stage invocation."""

    job_root = tmp_path / str(job_id)
    repo_dir = job_root / "repo"
    artifacts_dir = job_root / "artifacts"
    logs_dir = artifacts_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    repo_dir.mkdir(parents=True, exist_ok=True)
    return PreparedTaskWorkspace(
        job_root=job_root,
        repo_dir=repo_dir,
        artifacts_dir=artifacts_dir,
        prepare_log_path=logs_dir / "prepare.log",
        execute_log_path=logs_dir / "execute.log",
        publish_log_path=logs_dir / "publish.log",
        task_context_path=artifacts_dir / "task_context.json",
        publish_result_path=artifacts_dir / "publish_result.json",
        default_branch="main",
        starting_branch="main",
        new_branch=None,
        working_branch="main",
        workdir_mode="reuse",
        repo_command_env=None,
        publish_command_env=None,
    )


def _build_resolved_step(
    *, step_index: int, step_id: str, instructions: str
) -> ResolvedTaskStep:
    """Construct one execute-stage step definition."""

    return ResolvedTaskStep(
        step_index=step_index,
        step_id=step_id,
        title=None,
        instructions=instructions,
        effective_skill_id="auto",
        effective_skill_args={},
        has_step_instructions=True,
    )


class StreamingReplayStepHandler(FakeHandler):
    """Handler stub that streams Codex-like replay chunks through real dedupe logic."""

    def __init__(self, *, workdir_root: Path) -> None:
        super().__init__(
            WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
        )
        self._workdir_root = workdir_root
        self._streaming_handler = CodexExecHandler(workdir_root=workdir_root)

    async def handle(
        self, *, job_id, payload, cancel_event=None, output_chunk_callback=None
    ):
        del cancel_event
        self.calls.append("codex_exec")
        self.exec_payloads.append(dict(payload))
        log_path = self._workdir_root / str(job_id) / "artifacts" / "codex_exec.log"
        await self._streaming_handler._run_command(
            ["codex", "exec", "simulate"],
            cwd=self._workdir_root,
            log_path=log_path,
            check=True,
            output_chunk_callback=output_chunk_callback,
            enable_replay_dedupe=True,
        )
        return WorkerExecutionResult(
            succeeded=True,
            summary="streamed step",
            error_message=None,
            artifacts=(ArtifactUpload(path=log_path, name="logs/codex_exec.log"),),
        )


class _FakeStreamingReader:
    def __init__(self, chunks: Sequence[str]) -> None:
        self._chunks = [chunk.encode("utf-8") for chunk in chunks]

    async def read(self, _size: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _FakeStreamingProcess:
    def __init__(self, chunks: Sequence[str]) -> None:
        self.returncode = 0
        self.stdout = _FakeStreamingReader(chunks)
        self.stderr = _FakeStreamingReader(())

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        del input
        return (b"", b"")


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


async def test_run_once_returns_false_when_system_paused(tmp_path: Path) -> None:
    """Paused system responses should short-circuit claims without work."""

    now = datetime.now(UTC)
    paused_metadata = QueueSystemStatus(
        workers_paused=True,
        mode="drain",
        reason="Upgrading images",
        version=2,
        requested_at=now,
        updated_at=now,
    )
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    queue = FakeQueueClient(jobs=[], system_status=paused_metadata)
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="ok", error_message=None)
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is False
    assert worker._last_run_outcome == "paused"
    assert queue.completed == []


async def test_system_metadata_logging_occurs_once_per_version(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Pause/resume logs should only emit once per system version."""

    caplog.set_level(logging.INFO)
    now = datetime.now(UTC)
    metadata = QueueSystemStatus(
        workers_paused=True,
        mode="quiesce",
        reason="short maintenance",
        version=5,
        requested_at=now,
        updated_at=now,
    )
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    worker._handle_system_metadata(metadata)
    worker._handle_system_metadata(metadata)
    resumed = QueueSystemStatus(
        workers_paused=False,
        mode=None,
        reason=None,
        version=6,
        requested_at=None,
        updated_at=datetime.now(UTC),
    )
    worker._handle_system_metadata(resumed)

    pause_logs = [
        record.message
        for record in caplog.records
        if "Worker pause active" in record.message
    ]
    resume_logs = [
        record.message
        for record in caplog.records
        if "Worker pause cleared" in record.message
    ]
    assert len(pause_logs) == 1
    assert len(resume_logs) == 1


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
    assert "logs/result.log" in queue.uploaded
    assert "task_context.json" in queue.uploaded
    assert len(queue.completed) == 1
    assert queue.failed == []
    assert any(event["message"] == "Worker claimed job" for event in queue.events)
    assert any(event["message"] == "Job completed" for event in queue.events)
    claimed = next(
        event for event in queue.events if event["message"] == "Worker claimed job"
    )
    payload = claimed["payload"]
    assert isinstance(payload, dict)
    assert payload["selectedSkill"] == "auto"
    assert payload["executionPath"] == "direct_only"


@pytest.mark.parametrize(
    "provider",
    ("[REDACTED]", "unsupported-provider", "google"),
)
async def test_run_once_reports_rag_unavailable_when_embedding_provider_unexecutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    """task_context and claim metadata should agree when retrieval cannot execute."""

    monkeypatch.setenv("RAG_ENABLED", "1")
    monkeypatch.setenv("QDRANT_ENABLED", "1")
    monkeypatch.setenv("DEFAULT_EMBEDDING_PROVIDER", provider)
    monkeypatch.delenv("MOONMIND_RETRIEVAL_URL", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "a/b",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="done", error_message=None)
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
    claimed = next(
        event for event in queue.events if event["message"] == "Worker claimed job"
    )
    claimed_payload = claimed["payload"]
    assert isinstance(claimed_payload, dict)
    assert claimed_payload["ragAvailable"] is False
    assert claimed_payload["ragMode"] == "unavailable"
    assert claimed_payload["ragUnavailableReason"] in {
        "embedding_provider_unsupported",
        "embedding_provider_not_configured",
    }
    assert "ragCommand" not in claimed_payload

    task_context_path = tmp_path / str(job.id) / "artifacts" / "task_context.json"
    task_context = json.loads(task_context_path.read_text(encoding="utf-8"))
    rag_payload = task_context["rag"]
    assert rag_payload["ragAvailable"] is False
    assert rag_payload["ragMode"] == "unavailable"
    assert rag_payload["ragUnavailableReason"] in {
        "embedding_provider_unsupported",
        "embedding_provider_not_configured",
    }
    assert "ragCommand" not in rag_payload


async def test_run_once_writes_runtime_config_into_task_context(tmp_path: Path) -> None:
    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "runtime": {
                    "mode": "codex",
                    "model": "gpt-5-codex",
                    "effort": "high",
                },
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="done", error_message=None)
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
    task_context_path = tmp_path / str(job.id) / "artifacts" / "task_context.json"
    task_context = json.loads(task_context_path.read_text(encoding="utf-8"))
    runtime_config = task_context["runtimeConfig"]
    assert runtime_config["mode"] == "codex"
    assert runtime_config["model"] == "gpt-5-codex"
    assert runtime_config["effort"] == "high"


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
    assert "logs/run.log" in queue.uploaded
    assert "patches/changes.patch" not in queue.uploaded
    assert len(queue.completed) == 1
    assert queue.failed == []


async def test_run_once_optional_artifact_upload_failures_are_non_fatal(
    tmp_path: Path,
) -> None:
    """Optional artifact upload failures should not fail otherwise successful jobs."""

    step_log = tmp_path / "step.log"
    step_patch = tmp_path / "step.patch"
    step_log.write_text("step", encoding="utf-8")
    step_patch.write_text("diff", encoding="utf-8")

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = SelectiveFailUploadQueueClient(
        jobs=[job], fail_names={"logs/steps/step-0000.log"}
    )
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="step ok",
            error_message=None,
            artifacts=(
                ArtifactUpload(path=step_log, name="logs/codex_exec.log"),
                ArtifactUpload(path=step_patch, name="patches/changes.patch"),
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
    assert len(queue.completed) == 1
    assert queue.failed == []
    assert any(
        event["message"] == "Optional artifact upload failed" for event in queue.events
    )
    assert any(
        event["message"] == "Optional artifact uploads failed" for event in queue.events
    )
    assert queue.completed[0][1] is not None
    assert "optional artifact upload(s) failed" in queue.completed[0][1]


async def test_worker_submits_task_proposals(tmp_path: Path) -> None:
    """Workers should submit proposals when the feature flag is enabled."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
        enable_task_proposals=True,
    )
    queue = FakeQueueClient()
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="ok", error_message=None)
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    job = ClaimedJob(id=uuid4(), type="task", payload={"repository": "moon/org"})
    context_dir = tmp_path / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    proposals_path = context_dir / "task_proposals.json"
    proposals_path.write_text(
        json.dumps(
            [
                {
                    "title": "Add regression tests",
                    "summary": "Cover auth flow edge cases",
                    "taskCreateRequest": {
                        "type": "task",
                        "priority": 0,
                        "maxAttempts": 3,
                        "payload": {
                            "repository": "MoonLadderStudios/MoonMind",
                            "task": {"instructions": "Add tests"},
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    prepared = PreparedTaskWorkspace(
        job_root=tmp_path / "job",
        repo_dir=tmp_path / "repo",
        artifacts_dir=tmp_path / "artifacts",
        prepare_log_path=tmp_path / "prepare.log",
        execute_log_path=tmp_path / "execute.log",
        publish_log_path=tmp_path / "publish.log",
        task_context_path=context_dir,
        publish_result_path=tmp_path / "publish-result.log",
        default_branch="main",
        starting_branch="main",
        new_branch=None,
        working_branch="feature/proposal-tests",
        workdir_mode="checkout",
        repo_command_env=None,
        publish_command_env=None,
    )

    await worker._maybe_submit_task_proposals(job=job, prepared=prepared)

    assert queue.submitted_proposals
    first = queue.submitted_proposals[0]
    assert first["origin"]["source"] == "queue"
    assert first["origin"]["id"] == str(job.id)
    assert first["origin"]["metadata"]["workingBranch"] == "feature/proposal-tests"
    assert not proposals_path.exists()


async def test_task_proposal_request_uses_task_flag_with_config_gate(
    tmp_path: Path,
) -> None:
    """Task-level proposeTasks should be interpreted, but still gated by config."""

    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
            enable_task_proposals=False,
        ),
        queue_client=FakeQueueClient(),
        codex_exec_handler=FakeHandler(
            WorkerExecutionResult(succeeded=True, summary="ok", error_message=None)
        ),
    )

    assert worker._task_proposals_requested(
        canonical_payload={"repository": "moon/org", "task": {"proposeTasks": True}}
    )
    assert (
        worker._task_proposals_requested(
            canonical_payload={
                "repository": "moon/org",
                "task": {"proposeTasks": False},
            }
        )
        is False
    )
    assert (
        worker._task_proposals_requested(
            canonical_payload={"repository": "moon/org", "task": {}}
        )
        is False
    )


async def test_run_once_exception_still_records_terminal_failure_when_upload_fails(
    tmp_path: Path,
) -> None:
    """Exception path should fail the job even when artifact upload retry errors."""

    job = ClaimedJob(
        id=uuid4(),
        type="codex_exec",
        payload={"repository": "a/b", "instruction": "run"},
    )
    queue = FailingUploadQueueClient(jobs=[job])
    handler = FakeHandler(RuntimeError("execute exploded"))
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
    assert "execute exploded" in queue.failed[0]
    assert queue.completed == []


async def test_run_once_redacts_task_context_payload(
    tmp_path: Path, monkeypatch
) -> None:
    """task_context.json should redact secret values from job-derived payloads."""

    secret_value = "super-secret-token-value"
    monkeypatch.setenv("MOONMIND_TEST_TOKEN", secret_value)

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "a/b",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "speckit", "args": {"api_token": secret_value}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="done", error_message=None)
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

    task_context_path = tmp_path / str(job.id) / "artifacts" / "task_context.json"
    task_context_content = task_context_path.read_text(encoding="utf-8")

    assert processed is True
    assert secret_value not in task_context_content
    assert "[REDACTED]" in task_context_content


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


async def test_run_once_task_routes_through_direct_exec_path(tmp_path: Path) -> None:
    """Canonical `task` jobs should execute through direct codex path by default."""

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "branch"},
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="task ok", error_message=None)
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
    assert handler.calls == ["codex_exec"]
    claimed = next(
        event for event in queue.events if event["message"] == "Worker claimed job"
    )
    payload = claimed["payload"]
    assert isinstance(payload, dict)
    assert payload["selectedSkill"] == "auto"
    assert payload["executionPath"] == "direct_only"
    assert "ref" not in handler.exec_payloads[0]
    assert "logs/publish.log" in queue.uploaded
    assert "publish_result.json" in queue.uploaded
    assert "reports/run_summary.json" in queue.uploaded
    assert queue.completed_finish_payloads[0]["finishOutcomeCode"] == "NO_CHANGES"
    assert queue.completed_finish_payloads[0]["finishOutcomeStage"] == "publish"
    finish_summary = queue.completed_finish_payloads[0]["finishSummary"]
    assert isinstance(finish_summary, dict)
    assert finish_summary["finishOutcome"]["code"] == "NO_CHANGES"
    assert finish_summary["publish"]["status"] == "skipped"
    assert any(event["message"] == "moonmind.task.prepare" for event in queue.events)
    assert any(event["message"] == "moonmind.task.execute" for event in queue.events)
    assert any(event["message"] == "moonmind.task.publish" for event in queue.events)


async def test_run_once_task_skill_routes_through_skill_path(tmp_path: Path) -> None:
    """Canonical `task` jobs with concrete skill id should use skill handler path."""

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {
                    "id": "speckit",
                    "args": {"repo": "MoonLadderStudios/MoonMind"},
                },
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "develop", "newBranch": None},
                "publish": {"mode": "none"},
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True, summary="task skill ok", error_message=None
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
    assert len(queue.completed) == 1
    assert handler.calls == ["codex_skill:speckit:False"]
    assert "ref" not in handler.skill_payloads[0]
    inputs = handler.skill_payloads[0].get("inputs")
    assert isinstance(inputs, dict)
    assert "ref" not in inputs
    publish_event = next(
        event for event in queue.events if event["message"] == "moonmind.task.publish"
    )
    payload = publish_event["payload"]
    assert isinstance(payload, dict)
    assert payload["status"] == "skipped"
    assert "reports/run_summary.json" in queue.uploaded
    assert queue.completed_finish_payloads[0]["finishOutcomeCode"] == "PUBLISH_DISABLED"
    finish_summary = queue.completed_finish_payloads[0]["finishSummary"]
    assert isinstance(finish_summary, dict)
    assert finish_summary["publish"]["status"] == "not_run"


async def test_run_once_task_steps_execute_in_order_with_step_events(
    tmp_path: Path,
) -> None:
    """Multi-step tasks should execute each step in order with step lifecycle events."""

    step1_log = tmp_path / "step1.log"
    step1_patch = tmp_path / "step1.patch"
    step2_log = tmp_path / "step2.log"
    step2_patch = tmp_path / "step2.patch"
    step1_log.write_text("step1", encoding="utf-8")
    step1_patch.write_text("diff1", encoding="utf-8")
    step2_log.write_text("step2", encoding="utf-8")
    step2_patch.write_text("diff2", encoding="utf-8")

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [
                    {"id": "inspect", "instructions": "Inspect code"},
                    {
                        "id": "patch",
                        "instructions": "Patch code",
                        "skill": {"id": "speckit", "args": {"phase": "patch"}},
                    },
                ],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        [
            WorkerExecutionResult(
                succeeded=True,
                summary="step1 ok",
                error_message=None,
                artifacts=(
                    ArtifactUpload(path=step1_log, name="logs/codex_exec.log"),
                    ArtifactUpload(path=step1_patch, name="patches/changes.patch"),
                ),
            ),
            WorkerExecutionResult(
                succeeded=True,
                summary="step2 ok",
                error_message=None,
                artifacts=(
                    ArtifactUpload(path=step2_log, name="logs/codex_exec.log"),
                    ArtifactUpload(path=step2_patch, name="patches/changes.patch"),
                ),
            ),
        ]
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
    assert queue.failed == []
    assert handler.calls == ["codex_exec", "codex_skill:speckit:False"]
    assert "logs/steps/step-0000.log" in queue.uploaded
    assert "logs/steps/step-0001.log" in queue.uploaded
    assert "patches/steps/step-0000.patch" in queue.uploaded
    assert "patches/steps/step-0001.patch" in queue.uploaded
    assert any(event["message"] == "task.steps.plan" for event in queue.events)
    started = [
        event for event in queue.events if event["message"] == "task.step.started"
    ]
    finished = [
        event for event in queue.events if event["message"] == "task.step.finished"
    ]
    assert len(started) == 2
    assert len(finished) == 2
    assert started[0]["payload"]["stepId"] == "step-1"
    assert started[1]["payload"]["stepId"] == "step-2"


async def test_run_once_task_steps_step_log_excludes_previous_session_headers(
    tmp_path: Path,
) -> None:
    """Step logs should contain only new runtime output for that step."""

    step_one_segment = "== SESSION HEADER step-1 ==\nstep-one output\n"
    step_two_segment = "== SESSION HEADER step-2 ==\nstep-two output\n"
    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [
                    {"id": "step-1", "instructions": "First"},
                    {"id": "step-2", "instructions": "Second"},
                ],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = CumulativeStepLogHandler(
        workdir_root=tmp_path,
        segments=[step_one_segment, step_two_segment],
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
    step_one_log = (
        tmp_path / str(job.id) / "artifacts" / "logs" / "steps" / "step-0000.log"
    )
    step_two_log = (
        tmp_path / str(job.id) / "artifacts" / "logs" / "steps" / "step-0001.log"
    )
    step_one_text = step_one_log.read_text(encoding="utf-8")
    step_two_text = step_two_log.read_text(encoding="utf-8")
    assert step_one_text == step_one_segment
    assert step_two_text == step_two_segment
    assert "SESSION HEADER step-1" in step_one_text
    assert "SESSION HEADER step-1" not in step_two_text
    assert "SESSION HEADER step-2" in step_two_text


async def test_run_once_task_step_log_without_truncation_skips_companion_artifact(
    tmp_path: Path,
) -> None:
    """Untruncated per-step log snapshots should not emit companion artifacts."""

    step_log = tmp_path / "small-step.log"
    step_log.write_text("step output\nall good\n", encoding="utf-8")

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="step ok",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
        )
    )
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
        step_log_max_bytes=4096,
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    step_log_path = (
        tmp_path / str(job.id) / "artifacts" / "logs" / "steps" / "step-0000.log"
    )
    full_step_log_path = (
        tmp_path
        / str(job.id)
        / "artifacts"
        / "logs"
        / "steps"
        / "step-0000.full.log.gz"
    )
    linkage_metadata_path = (
        tmp_path
        / str(job.id)
        / "artifacts"
        / "logs"
        / "steps"
        / "step-0000.full.log.metadata.json"
    )

    assert step_log_path.read_text(encoding="utf-8") == "step output\nall good\n"
    assert not full_step_log_path.exists()
    assert not linkage_metadata_path.exists()
    assert "logs/steps/step-0000.full.log.gz" not in queue.uploaded
    assert "logs/steps/step-0000.full.log.metadata.json" not in queue.uploaded


async def test_run_once_task_step_transcript_truncated_mid_command_fails_with_retryable_run_quality(
    tmp_path: Path,
) -> None:
    """Incomplete command markers should fail the step with structured run_quality retry."""

    step_log = tmp_path / "incomplete-step.log"
    step_log.write_text(
        "[command] $ codex exec run integrity check\n"
        "partial output that never reached command completion\n",
        encoding="utf-8",
    )

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="step ok",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
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
    assert queue.completed == []
    assert len(queue.failed) == 1
    assert queue.failed_retryable == [True]
    step_failed_event = next(
        event for event in queue.events if event["message"] == "task.step.failed"
    )
    run_quality = step_failed_event["payload"]["runQuality"]
    assert run_quality["category"] == "run_quality"
    assert run_quality["code"] == "step_transcript_truncated_mid_command"
    assert "retry" in run_quality["tags"]
    assert "artifact_gap" in run_quality["tags"]
    finish_summary = queue.failed_finish_payloads[0]["finishSummary"]
    assert isinstance(finish_summary, dict)
    assert (
        finish_summary["runQuality"]["code"] == "step_transcript_truncated_mid_command"
    )


async def test_run_once_task_step_transcript_with_completion_marker_succeeds(
    tmp_path: Path,
) -> None:
    """A balanced transcript ending with completion marker should pass integrity checks."""

    step_log = tmp_path / "complete-step.log"
    step_log.write_text(
        "[command] $ codex exec run integrity check; control=worker\n"
        "done\n"
        "[command] complete: rc=0; cmd=codex exec; stdoutChars=5; stderrChars=0; control=worker\n",
        encoding="utf-8",
    )

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="step ok",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
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
    assert len(queue.completed) == 1
    assert queue.failed == []
    assert queue.failed_retryable == []
    assert any(event["message"] == "task.step.finished" for event in queue.events)


async def test_run_once_task_step_transcript_with_correlated_marker_ids_succeeds(
    tmp_path: Path,
) -> None:
    """Balanced start/complete markers should pass when IDs are present."""

    marker_id = str(uuid4())
    step_log = tmp_path / "complete-step-with-id.log"
    step_log.write_text(
        (
            "[command] $ codex exec run integrity check; "
            f"id={marker_id}; control=worker\n"
            "done\n"
            "[command] complete: rc=0; cmd=codex exec; stdoutChars=5; stderrChars=0; "
            f"id={marker_id}; control=worker\n"
        ),
        encoding="utf-8",
    )

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="step ok",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
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
    assert len(queue.completed) == 1
    assert queue.failed == []


async def test_run_once_task_step_transcript_reconciles_legacy_multiline_start_markers(
    tmp_path: Path,
) -> None:
    """Legacy multiline starts should be reconciled when completion markers are present."""

    step_log = tmp_path / "legacy-start-reconciled.log"
    step_log.write_text(
        (
            "[command] $ codex exec run integrity check\n"
            "MOONMIND TASK OBJECTIVE:\n"
            "line one\n"
            "line two; control=worker\n"
            "[command] complete: rc=0; cmd=codex exec; stdoutChars=5; stderrChars=0; "
            "control=worker\n"
        ),
        encoding="utf-8",
    )

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="step ok",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
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
    assert len(queue.completed) == 1
    assert queue.failed == []


async def test_run_once_task_step_transcript_with_mismatched_marker_ids_fails(
    tmp_path: Path,
) -> None:
    """Mismatched marker IDs should fail integrity even when counts appear balanced."""

    start_id = str(uuid4())
    complete_id = str(uuid4())
    step_log = tmp_path / "mismatched-marker-id.log"
    step_log.write_text(
        (
            "[command] $ codex exec run integrity check; "
            f"id={start_id}; control=worker\n"
            "[command] complete: rc=0; cmd=codex exec; stdoutChars=5; stderrChars=0; "
            f"id={complete_id}; control=worker\n"
        ),
        encoding="utf-8",
    )

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="step ok",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
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
    assert queue.completed == []
    assert len(queue.failed) == 1
    assert queue.failed_retryable == [True]
    step_failed_event = next(
        event for event in queue.events if event["message"] == "task.step.failed"
    )
    run_quality = step_failed_event["payload"]["runQuality"]
    assert run_quality["code"] == "step_transcript_invalid_marker_balance"


async def test_run_once_retryable_run_quality_retry_skips_proposal_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry attempts for retryable run_quality failures should skip proposals."""

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        attempt=2,
        max_attempts=3,
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "proposeTasks": True,
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=False,
            summary=None,
            error_message="[run_quality] simulated",
            artifacts=(),
            run_quality_reason={
                "category": "run_quality",
                "code": "step_transcript_invalid_marker_balance",
                "tags": ["retry", "artifact_gap"],
            },
        )
    )
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
        enable_task_proposals=True,
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    async def _unexpected_post_skill(*args, **kwargs):
        raise AssertionError("proposal skill execution should be skipped")

    async def _unexpected_submit(*args, **kwargs):
        raise AssertionError("proposal submission should be skipped")

    monkeypatch.setattr(
        worker, "_run_post_task_proposal_skills", _unexpected_post_skill
    )
    monkeypatch.setattr(worker, "_maybe_submit_task_proposals", _unexpected_submit)

    processed = await worker.run_once()

    assert processed is True
    assert queue.completed == []
    assert len(queue.failed) == 1
    assert queue.failed_retryable == [False]
    assert any(
        event["message"] == "task.proposalSubmission.skipped" for event in queue.events
    )


async def test_run_once_task_step_transcript_uses_full_companion_when_preview_truncated(
    tmp_path: Path,
) -> None:
    """Integrity checks should prefer full companion logs when preview is truncated."""

    step_log_max_bytes = 4096
    prefix = "prefix-no-markers\n" * (
        (step_log_max_bytes // len("prefix-no-markers\n")) + 32
    )
    middle = "middle-content\n" * ((step_log_max_bytes // len("middle-content\n")) + 32)
    step_log = tmp_path / "companion-step.log"
    step_log.write_text(
        (
            prefix
            + "[command] $ codex exec run integrity check; control=worker\n"
            + middle
            + "[command] complete: rc=0; cmd=codex exec; stdoutChars=5; "
            "stderrChars=0; control=worker\n"
        ),
        encoding="utf-8",
    )

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="step ok",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
        )
    )
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
        step_log_max_bytes=step_log_max_bytes,
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert len(queue.completed) == 1
    assert queue.failed == []
    assert queue.failed_retryable == []


async def test_run_once_task_step_transcript_ignores_unscoped_marker_like_output(
    tmp_path: Path,
) -> None:
    """Integrity checks should ignore marker-like text not emitted as worker controls."""

    step_log = tmp_path / "quoted-marker-step.log"
    step_log.write_text(
        (
            "[command] $ codex exec run integrity check; control=worker\n"
            "model output: [command] $ not a worker control marker\n"
            "[command] complete: rc=0; cmd=codex exec; stdoutChars=5; stderrChars=0; control=worker\n"
        ),
        encoding="utf-8",
    )

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="step ok",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
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
    assert len(queue.completed) == 1
    assert queue.failed == []
    assert queue.failed_retryable == []
    assert not any(event["message"] == "task.step.failed" for event in queue.events)


async def test_run_once_task_step_log_truncation_writes_full_companion_artifact(
    tmp_path: Path,
) -> None:
    """Truncated previews should emit a gzip full-fidelity companion and linkage."""

    source_content = (
        "prefix-line\n" * 200
    ) + "ERROR: critical failure context that must be preserved\n"
    source_step_log = tmp_path / "truncated-step.log"
    source_step_log.write_text(source_content, encoding="utf-8")

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="step ok",
            error_message=None,
            artifacts=(
                ArtifactUpload(path=source_step_log, name="logs/codex_exec.log"),
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
        step_log_max_bytes=320,
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    step_log_path = (
        tmp_path / str(job.id) / "artifacts" / "logs" / "steps" / "step-0000.log"
    )
    full_step_log_path = (
        tmp_path
        / str(job.id)
        / "artifacts"
        / "logs"
        / "steps"
        / "step-0000.full.log.gz"
    )
    linkage_metadata_path = (
        tmp_path
        / str(job.id)
        / "artifacts"
        / "logs"
        / "steps"
        / "step-0000.full.log.metadata.json"
    )

    preview = step_log_path.read_text(encoding="utf-8")
    assert step_log_path.stat().st_size <= 320
    assert "[moonmind] step log truncated" in preview
    assert "[moonmind] step log linkage:" in preview
    assert "logs/steps/step-0000.full.log.metadata.json" in preview

    with gzip.open(full_step_log_path, "rb") as handle:
        companion_content = handle.read().decode("utf-8")
    assert companion_content == source_content

    metadata = json.loads(linkage_metadata_path.read_text(encoding="utf-8"))
    assert metadata["kind"] == "moonmind.stepLogLinkage"
    assert metadata["truncated"] is True
    assert metadata["previewArtifact"] == "logs/steps/step-0000.log"
    assert metadata["fullArtifact"] == "logs/steps/step-0000.full.log.gz"
    assert metadata["metadataArtifact"] == "logs/steps/step-0000.full.log.metadata.json"
    assert metadata["sourceDeltaBytes"] == len(source_content.encode("utf-8"))
    assert metadata["omittedBytes"] > 0

    assert "logs/steps/step-0000.log" in queue.uploaded
    assert "logs/steps/step-0000.full.log.gz" in queue.uploaded
    assert "logs/steps/step-0000.full.log.metadata.json" in queue.uploaded


async def test_run_once_task_steps_bounds_log_size_and_keeps_failure_tail(
    tmp_path: Path,
) -> None:
    """Bounded step logs should keep failure context from the tail."""

    large_step_log = tmp_path / "large-step.log"
    large_step_log.write_text(
        ("prefix-line\n" * 200)
        + "ERROR: critical failure context that must be preserved\n",
        encoding="utf-8",
    )

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="step ok",
            error_message=None,
            artifacts=(
                ArtifactUpload(path=large_step_log, name="logs/codex_exec.log"),
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
        step_log_max_bytes=320,
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    step_log_path = (
        tmp_path / str(job.id) / "artifacts" / "logs" / "steps" / "step-0000.log"
    )
    step_log_content = step_log_path.read_text(encoding="utf-8")
    assert step_log_path.stat().st_size <= 320
    assert "[moonmind] step log truncated" in step_log_content
    assert "ERROR: critical failure context that must be preserved" in step_log_content


async def test_run_once_task_steps_step_log_growth_is_bounded_per_step(
    tmp_path: Path,
) -> None:
    """Later step logs should not inherit earlier large output chunks."""

    step_one_segment = "== SESSION HEADER step-1 ==\n" + ("A" * 4096) + "\n"
    step_two_segment = "== SESSION HEADER step-2 ==\n" + ("B" * 96) + "\n"
    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [
                    {"id": "step-1", "instructions": "First"},
                    {"id": "step-2", "instructions": "Second"},
                ],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = CumulativeStepLogHandler(
        workdir_root=tmp_path,
        segments=[step_one_segment, step_two_segment],
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
    step_two_log = (
        tmp_path / str(job.id) / "artifacts" / "logs" / "steps" / "step-0001.log"
    )
    step_two_text = step_two_log.read_text(encoding="utf-8")
    assert step_two_text == step_two_segment
    assert len(step_two_text) == len(step_two_segment)
    assert len(step_two_text) < len(step_one_segment)
    assert len(step_two_text) < len(step_one_segment) + len(step_two_segment)


async def test_run_once_task_step_log_truncation_preserves_utf8(
    tmp_path: Path,
) -> None:
    """Bounded step logs should keep valid UTF-8 boundaries around truncation."""

    unicode_block = "🚀" * 200
    large_step_log = tmp_path / "unicode-step.log"
    large_step_log.write_text(
        f"prefix-{unicode_block}\n" + f"tail-{unicode_block}\n",
        encoding="utf-8",
    )

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="step ok",
            error_message=None,
            artifacts=(
                ArtifactUpload(path=large_step_log, name="logs/codex_exec.log"),
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
        step_log_max_bytes=360,
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    step_log_path = (
        tmp_path / str(job.id) / "artifacts" / "logs" / "steps" / "step-0000.log"
    )
    step_log_bytes = step_log_path.read_bytes()
    step_log_text = step_log_bytes.decode("utf-8")
    assert step_log_path.stat().st_size <= 360
    assert "[moonmind] step log truncated" in step_log_text
    assert "🚀" in step_log_text


async def test_copy_incremental_step_log_rejects_symlink_source(tmp_path: Path) -> None:
    """Incremental log copy should reject symlink inputs to avoid disclosure."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="step", error_message=None)
    )
    worker = CodexWorker(
        config=config,
        queue_client=FakeQueueClient(),
        codex_exec_handler=handler,
    )
    real_log = tmp_path / "real.log"
    real_log.write_text("safe", encoding="utf-8")
    symlink_log = tmp_path / "linked.log"
    symlink_log.symlink_to(real_log)

    with pytest.raises(ValueError, match="Refusing to read step log symlink"):
        worker._copy_incremental_step_log(
            source_path=symlink_log,
            destination_path=tmp_path / "copied.log",
            step_log_offsets={},
        )


async def test_run_once_task_steps_write_incremental_step_logs_without_duplication(
    tmp_path: Path,
) -> None:
    """Later step logs should only include new output, not repeated history."""

    step1_patch = tmp_path / "step1.patch"
    step2_patch = tmp_path / "step2.patch"
    step1_patch.write_text("diff1", encoding="utf-8")
    step2_patch.write_text("diff2", encoding="utf-8")

    class LocalCumulativeStepLogHandler(CumulativeStepLogHandler):
        async def handle(
            self, *, job_id, payload, cancel_event=None, output_chunk_callback=None
        ):
            del payload, cancel_event, output_chunk_callback
            self.calls.append("codex_exec")
            self._segment_index += 1
            patch_path = step1_patch if self._segment_index == 1 else step2_patch
            if self._segment_index > len(self._segments):
                raise RuntimeError("no cumulative step log segment configured")
            segment = self._segments[self._segment_index - 1]
            log_path = self._workdir_root / str(job_id) / "artifacts" / "codex_exec.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(segment)
            return WorkerExecutionResult(
                succeeded=True,
                summary=f"step{self._segment_index} ok",
                error_message=None,
                artifacts=(
                    ArtifactUpload(
                        path=log_path,
                        name="logs/codex_exec.log",
                    ),
                    ArtifactUpload(path=patch_path, name="patches/changes.patch"),
                ),
            )

    handler = LocalCumulativeStepLogHandler(
        workdir_root=tmp_path,
        segments=["step-1 output\n", "step-2 output\n"],
    )
    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [
                    {"id": "step-1", "instructions": "Do step 1"},
                    {"id": "step-2", "instructions": "Do step 2"},
                ],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
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
    step1_log_path = (
        tmp_path / str(job.id) / "artifacts" / "logs" / "steps" / "step-0000.log"
    )
    step2_log_path = (
        tmp_path / str(job.id) / "artifacts" / "logs" / "steps" / "step-0001.log"
    )
    assert step1_log_path.read_text(encoding="utf-8") == "step-1 output\n"
    step2_text = step2_log_path.read_text(encoding="utf-8")
    assert step2_text == "step-2 output\n"
    assert "step-1 output" not in step2_text


async def test_run_execute_stage_step_log_deltas_survive_worker_restart(
    tmp_path: Path,
) -> None:
    """Restarted workers should keep per-step log boundaries from persisted offsets."""

    job_id = uuid4()
    step_one_segment = "== SESSION HEADER step-1 ==\nstep-one output\n"
    step_two_segment = "== SESSION HEADER step-2 ==\nstep-two output\n"
    queue = FakeQueueClient()
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    prepared = _build_execute_stage_workspace(tmp_path=tmp_path, job_id=job_id)
    canonical_payload, source_payload = _build_execute_stage_payloads()

    first_worker = CodexWorker(
        config=config,
        queue_client=queue,
        codex_exec_handler=CumulativeStepLogHandler(
            workdir_root=tmp_path,
            segments=[step_one_segment],
        ),
    )
    first_result = await first_worker._run_execute_stage(
        job_id=job_id,
        canonical_payload=canonical_payload,
        source_payload=source_payload,
        runtime_mode="codex",
        resolved_steps=(
            _build_resolved_step(
                step_index=0,
                step_id="step-1",
                instructions="First",
            ),
        ),
        prepared=prepared,
    )
    assert first_result.succeeded is True

    second_worker = CodexWorker(
        config=config,
        queue_client=queue,
        codex_exec_handler=CumulativeStepLogHandler(
            workdir_root=tmp_path,
            segments=[step_two_segment],
        ),
    )
    second_result = await second_worker._run_execute_stage(
        job_id=job_id,
        canonical_payload=canonical_payload,
        source_payload=source_payload,
        runtime_mode="codex",
        resolved_steps=(
            _build_resolved_step(
                step_index=1,
                step_id="step-2",
                instructions="Second",
            ),
        ),
        prepared=prepared,
    )
    assert second_result.succeeded is True

    step_one_log = (
        tmp_path / str(job_id) / "artifacts" / "logs" / "steps" / "step-0000.log"
    )
    step_two_log = (
        tmp_path / str(job_id) / "artifacts" / "logs" / "steps" / "step-0001.log"
    )
    checkpoint_path = (
        tmp_path / str(job_id) / "artifacts" / "state" / "step_log_offsets.json"
    )
    assert step_one_log.read_text(encoding="utf-8") == step_one_segment
    step_two_text = step_two_log.read_text(encoding="utf-8")
    assert step_two_text == step_two_segment
    assert "SESSION HEADER step-1" not in step_two_text
    assert checkpoint_path.exists()


async def test_run_execute_stage_step_log_deltas_handle_source_truncation_on_resume(
    tmp_path: Path,
) -> None:
    """Truncation before the next step should not duplicate prior step bytes."""

    job_id = uuid4()
    step_one_segment = "== SESSION HEADER step-1 ==\nalpha\n"
    step_two_segment = "== SESSION HEADER step-2 ==\nbeta\n"
    queue = FakeQueueClient()
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    prepared = _build_execute_stage_workspace(tmp_path=tmp_path, job_id=job_id)
    canonical_payload, source_payload = _build_execute_stage_payloads()

    first_worker = CodexWorker(
        config=config,
        queue_client=queue,
        codex_exec_handler=CumulativeStepLogHandler(
            workdir_root=tmp_path,
            segments=[step_one_segment],
        ),
    )
    first_result = await first_worker._run_execute_stage(
        job_id=job_id,
        canonical_payload=canonical_payload,
        source_payload=source_payload,
        runtime_mode="codex",
        resolved_steps=(
            _build_resolved_step(
                step_index=0,
                step_id="step-1",
                instructions="First",
            ),
        ),
        prepared=prepared,
    )
    assert first_result.succeeded is True

    source_log = tmp_path / str(job_id) / "artifacts" / "codex_exec.log"
    source_log.write_text("", encoding="utf-8")

    second_worker = CodexWorker(
        config=config,
        queue_client=queue,
        codex_exec_handler=CumulativeStepLogHandler(
            workdir_root=tmp_path,
            segments=[step_two_segment],
        ),
    )
    second_result = await second_worker._run_execute_stage(
        job_id=job_id,
        canonical_payload=canonical_payload,
        source_payload=source_payload,
        runtime_mode="codex",
        resolved_steps=(
            _build_resolved_step(
                step_index=1,
                step_id="step-2",
                instructions="Second",
            ),
        ),
        prepared=prepared,
    )
    assert second_result.succeeded is True

    step_two_log = (
        tmp_path / str(job_id) / "artifacts" / "logs" / "steps" / "step-0001.log"
    )
    step_two_text = step_two_log.read_text(encoding="utf-8")
    assert step_two_text == step_two_segment
    assert "SESSION HEADER step-1" not in step_two_text
    assert source_log.read_text(encoding="utf-8") == step_two_segment


async def test_run_once_task_step_logs_dedupe_replay_blocks_and_keep_distinct_turn_repeats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute-stage artifacts should dedupe replay chunks without collapsing distinct turns."""

    completion_block = (
        "Implemented execute-stage duplicate-output regression coverage.\n"
        "**Completion Summary**\n"
        "- Added worker-level replay regression assertions.\n"
    )
    test_summary_block = (
        "**Test Summary**\n"
        "- Ran required unit test script: `./tools/test_unit.sh`\n"
        "- Result: `802 passed, 8 subtests passed`.\n"
    )
    repeated_turn_line = "- Final answer status: tests already green.\n"

    async def fake_exec(*args, **kwargs):
        del args, kwargs
        return _FakeStreamingProcess(
            (
                "thinking\n",
                completion_block,
                test_summary_block,
                completion_block,
                test_summary_block,
                "assistant\n",
                repeated_turn_line,
                "user\nPlease repeat the same status line exactly.\n",
                repeated_turn_line,
                "done\n",
            )
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = StreamingReplayStepHandler(workdir_root=tmp_path)
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
    codex_log_path = tmp_path / str(job.id) / "artifacts" / "codex_exec.log"
    step_log_path = (
        tmp_path / str(job.id) / "artifacts" / "logs" / "steps" / "step-0000.log"
    )
    codex_text = codex_log_path.read_text(encoding="utf-8")
    step_text = step_log_path.read_text(encoding="utf-8")
    for text in (codex_text, step_text):
        assert (
            text.count(
                "Implemented execute-stage duplicate-output regression coverage."
            )
            == 1
        )
        assert text.count("Result: `802 passed, 8 subtests passed`.") == 1
        assert text.count("Final answer status: tests already green.") == 2

    stdout_event_text = "".join(
        str(event["message"])
        for event in queue.events
        if event["payload"].get("kind") == "log"
        and event["payload"].get("stream") == "stdout"
    )
    assert (
        stdout_event_text.count(
            "Implemented execute-stage duplicate-output regression coverage."
        )
        == 1
    )
    assert stdout_event_text.count("Result: `802 passed, 8 subtests passed`.") == 1
    assert stdout_event_text.count("Final answer status: tests already green.") == 2


async def test_run_once_task_step_and_exec_logs_dedupe_mixed_prefix_final_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed-prefix snapshot replays should emit the final summary block once."""

    final_summary_block = (
        "Implemented and verified.\n"
        "- Reproduced from run logs: duplicate final block at lines 5274 and 5281.\n"
        "- Added semantic replay dedupe coverage for mixed prefixes.\n"
    )
    replay_a = f"thinking\n**Planning concise final response**\n{final_summary_block}"
    replay_b = f"codex\n{final_summary_block}"
    replay_c = final_summary_block

    async def fake_exec(*args, **kwargs):
        del args, kwargs
        return _FakeStreamingProcess((replay_a, replay_b, replay_c, "done\n"))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [{"id": "step-1", "instructions": "Do step 1"}],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = StreamingReplayStepHandler(workdir_root=tmp_path)
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
    codex_log_path = tmp_path / str(job.id) / "artifacts" / "codex_exec.log"
    step_log_path = (
        tmp_path / str(job.id) / "artifacts" / "logs" / "steps" / "step-0000.log"
    )
    marker = "Reproduced from run logs: duplicate final block at lines 5274 and 5281."
    codex_text = codex_log_path.read_text(encoding="utf-8")
    step_text = step_log_path.read_text(encoding="utf-8")
    assert codex_text.count(marker) == 1
    assert step_text.count(marker) == 1


def test_load_step_log_offsets_checkpoint_ignores_large_payload(
    tmp_path: Path,
) -> None:
    """Oversized checkpoint payloads should be ignored instead of loaded into memory."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    queue = FakeQueueClient()
    worker = CodexWorker(
        config=config,
        queue_client=queue,
        codex_exec_handler=FakeHandler(
            WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
        ),  # type: ignore[arg-type]
    )
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = worker._step_log_offsets_checkpoint_path(
        artifacts_dir=artifacts_dir
    )
    checkpoint_path.write_text("x" * (70_000), encoding="utf-8")

    assert worker._load_step_log_offsets_checkpoint(artifacts_dir=artifacts_dir) == {}


def test_persist_step_log_offsets_checkpoint_safely_skips_symlinked_parent(
    tmp_path: Path,
) -> None:
    """Checkpoint writes must avoid symlinked checkpoint state directories."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    queue = FakeQueueClient()
    worker = CodexWorker(
        config=config,
        queue_client=queue,
        codex_exec_handler=FakeHandler(
            WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
        ),  # type: ignore[arg-type]
    )
    artifacts_dir = tmp_path / "job" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    state_target = tmp_path / "state-target"
    state_target.mkdir(parents=True, exist_ok=True)
    state_link = artifacts_dir / "state"
    state_link.symlink_to(state_target)

    worker._persist_step_log_offsets_checkpoint(
        artifacts_dir=artifacts_dir,
        step_log_offsets={"artifacts/source.log": {"offset": 1}},
    )

    assert not (state_target / "step_log_offsets.json").exists()


def test_persist_step_log_offsets_checkpoint_handles_temp_path_directory(
    tmp_path: Path,
) -> None:
    """Cleanup handling should tolerate a pre-existing temporary path directory."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    queue = FakeQueueClient()
    worker = CodexWorker(
        config=config,
        queue_client=queue,
        codex_exec_handler=FakeHandler(
            WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
        ),  # type: ignore[arg-type]
    )

    artifacts_dir = tmp_path / "job-2" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = worker._step_log_offsets_checkpoint_path(
        artifacts_dir=artifacts_dir
    )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = checkpoint_path.with_name(f"{checkpoint_path.name}.tmp")
    temp_path.mkdir()
    try:
        worker._persist_step_log_offsets_checkpoint(
            artifacts_dir=artifacts_dir,
            step_log_offsets={"artifacts/source.log": {"offset": 1}},
        )
    finally:
        assert temp_path.is_dir()


async def test_run_once_skill_gate_step_fails_when_gate_reports_failure(
    tmp_path: Path,
) -> None:
    """Gated skills must hard-fail the task when machine-readable gate is FAIL."""

    step_log = tmp_path / "gate-step.log"
    step_log.write_text("step", encoding="utf-8")

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "Run skill gate",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "branch"},
                "steps": [
                    {
                        "id": "quality-gate",
                        "instructions": "Execute gated skill",
                        "skill": {
                            "id": "speckit",
                            "args": {
                                "gateType": "quality-gate",
                                "resultsSubdir": ".artifacts/skill-gates/quality-gate",
                            },
                        },
                    }
                ],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])

    class GateFailingHandler(FakeHandler):
        async def handle_skill(
            self,
            *,
            job_id,
            payload,
            selected_skill,
            fallback=False,
            cancel_event=None,
            output_chunk_callback=None,
        ):
            gate_path = (
                tmp_path
                / str(job_id)
                / "repo"
                / ".artifacts"
                / "skill-gates"
                / "quality-gate"
                / "latest"
                / "gate.json"
            )
            gate_path.parent.mkdir(parents=True, exist_ok=True)
            gate_path.write_text(
                json.dumps({"status": "FAIL", "reason": "docker daemon unreachable"}),
                encoding="utf-8",
            )
            return await super().handle_skill(
                job_id=job_id,
                payload=payload,
                selected_skill=selected_skill,
                fallback=fallback,
                cancel_event=cancel_event,
                output_chunk_callback=output_chunk_callback,
            )

    handler = GateFailingHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="skill returned",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
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
    assert queue.completed == []
    assert len(queue.failed) == 1
    assert "quality-gate gate failed" in queue.failed[0]
    assert handler.calls == ["codex_skill:speckit:False"]
    assert "gates/steps/step-0000.json" in queue.uploaded
    assert not any(
        event["message"] == "moonmind.task.publish" for event in queue.events
    )
    failed_events = [
        event for event in queue.events if event["message"] == "task.step.failed"
    ]
    assert len(failed_events) == 1
    assert "quality-gate gate failed" in str(failed_events[0]["payload"]["summary"])


async def test_run_once_skill_gate_step_succeeds_when_gate_reports_pass(
    tmp_path: Path,
) -> None:
    """PASS gate status should permit normal task completion."""

    step_log = tmp_path / "gate-step.log"
    step_log.write_text("step", encoding="utf-8")

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "Run skill gate",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "id": "quality-gate",
                        "instructions": "Execute gated skill",
                        "skill": {
                            "id": "speckit",
                            "args": {
                                "gateType": "quality-gate",
                                "resultsSubdir": ".artifacts/skill-gates/quality-gate",
                            },
                        },
                    }
                ],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])

    class GatePassingHandler(FakeHandler):
        async def handle_skill(
            self,
            *,
            job_id,
            payload,
            selected_skill,
            fallback=False,
            cancel_event=None,
            output_chunk_callback=None,
        ):
            gate_path = (
                tmp_path
                / str(job_id)
                / "repo"
                / ".artifacts"
                / "skill-gates"
                / "quality-gate"
                / "latest"
                / "gate.json"
            )
            gate_path.parent.mkdir(parents=True, exist_ok=True)
            gate_path.write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "reason": "Requested phases completed successfully",
                    }
                ),
                encoding="utf-8",
            )
            return await super().handle_skill(
                job_id=job_id,
                payload=payload,
                selected_skill=selected_skill,
                fallback=fallback,
                cancel_event=cancel_event,
                output_chunk_callback=output_chunk_callback,
            )

    handler = GatePassingHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="skill returned",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
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
    assert queue.failed == []
    assert len(queue.completed) == 1
    assert handler.calls == ["codex_skill:speckit:False"]
    assert "gates/steps/step-0000.json" in queue.uploaded
    finished_events = [
        event for event in queue.events if event["message"] == "task.step.finished"
    ]
    assert len(finished_events) == 1
    assert "quality-gate gate passed" in str(finished_events[0]["payload"]["summary"])


async def test_run_once_skill_gate_step_fails_when_gate_path_is_outside_allowed_roots(
    tmp_path: Path,
) -> None:
    """Gate artifact paths must stay inside workspace artifact directories."""

    step_log = tmp_path / "gate-step.log"
    step_log.write_text("step", encoding="utf-8")

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "Run skill gate",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "id": "quality-gate",
                        "instructions": "Execute gated skill",
                        "skill": {
                            "id": "speckit",
                            "args": {
                                "gateType": "quality-gate",
                                "gateFile": "/tmp/quality-gate.json",
                            },
                        },
                    }
                ],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="skill returned",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
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
    assert queue.completed == []
    assert len(queue.failed) == 1
    assert (
        "quality-gate gate failed: quality-gate gate artifact path is outside allowed "
        "artifacts directories" in queue.failed[0]
    )
    assert handler.calls == ["codex_skill:speckit:False"]
    assert "gates/steps/step-0000.json" in queue.uploaded


async def test_run_once_skill_gate_step_treats_missing_status_as_invalid(
    tmp_path: Path,
) -> None:
    """Gate payloads missing status must produce INVALID gate failure."""

    step_log = tmp_path / "gate-step.log"
    step_log.write_text("step", encoding="utf-8")

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "Run skill gate",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "id": "quality-gate",
                        "instructions": "Execute gated skill",
                        "skill": {
                            "id": "speckit",
                            "args": {
                                "gateType": "quality-gate",
                                "resultsSubdir": ".artifacts/skill-gates/quality-gate",
                            },
                        },
                    }
                ],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])

    class MissingStatusGateHandler(FakeHandler):
        async def handle_skill(
            self,
            *,
            job_id,
            payload,
            selected_skill,
            fallback=False,
            cancel_event=None,
            output_chunk_callback=None,
        ):
            gate_path = (
                tmp_path
                / str(job_id)
                / "repo"
                / ".artifacts"
                / "skill-gates"
                / "quality-gate"
                / "latest"
                / "gate.json"
            )
            gate_path.parent.mkdir(parents=True, exist_ok=True)
            gate_path.write_text(
                json.dumps({"reason": "missing status field"}), encoding="utf-8"
            )
            return await super().handle_skill(
                job_id=job_id,
                payload=payload,
                selected_skill=selected_skill,
                fallback=fallback,
                cancel_event=cancel_event,
                output_chunk_callback=output_chunk_callback,
            )

    handler = MissingStatusGateHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="skill returned",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
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
    assert queue.completed == []
    assert len(queue.failed) == 1
    assert (
        "quality-gate gate failed: quality-gate gate artifact is missing required status"
        in queue.failed[0]
    )


async def test_compose_step_instruction_dedupes_objective_text(
    tmp_path: Path,
) -> None:
    """Duplicate step/objective text should collapse to a single objective copy."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    queue = FakeQueueClient()
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    instruction = worker._compose_step_instruction_for_runtime(
        canonical_payload={
            "task": {
                "instructions": "Implement direct worker to Qdrant retrieval path."
            }
        },
        runtime_mode="codex",
        step=ResolvedTaskStep(
            step_index=0,
            step_id="step-1",
            title="Intake",
            instructions="  Implement   direct worker to Qdrant retrieval path.\n",
            effective_skill_id="auto",
            effective_skill_args={},
            has_step_instructions=True,
        ),
        total_steps=1,
    )

    assert (
        "MOONMIND TASK OBJECTIVE:\nImplement direct worker to Qdrant retrieval path."
        in instruction
    )
    assert (
        "STEP 1/1 step-1 Intake:\n(same as task objective; no additional step-specific instructions)"
        in instruction
    )
    assert instruction.count("Implement direct worker to Qdrant retrieval path.") == 1


async def test_compose_step_instruction_keeps_distinct_step_text(
    tmp_path: Path,
) -> None:
    """Distinct step instructions should remain visible after objective rendering."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    queue = FakeQueueClient()
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    step_text = "Run speckit-specify and preserve user constraints."
    instruction = worker._compose_step_instruction_for_runtime(
        canonical_payload={
            "task": {
                "instructions": "Implement direct worker to Qdrant retrieval path."
            }
        },
        runtime_mode="codex",
        step=ResolvedTaskStep(
            step_index=0,
            step_id="step-1",
            title=None,
            instructions=step_text,
            effective_skill_id="auto",
            effective_skill_args={},
            has_step_instructions=True,
        ),
        total_steps=1,
    )

    assert (
        "MOONMIND TASK OBJECTIVE:\nImplement direct worker to Qdrant retrieval path."
        in instruction
    )
    assert f"STEP 1/1 step-1:\n{step_text}" in instruction
    assert (
        "(same as task objective; no additional step-specific instructions)"
        not in instruction
    )


async def test_compose_step_instruction_allows_pr_resolver_self_publish_when_publish_none(
    tmp_path: Path,
) -> None:
    """`pr-resolver` should be told to commit/push directly when publish is disabled."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    queue = FakeQueueClient()
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    instruction = worker._compose_step_instruction_for_runtime(
        canonical_payload={
            "task": {
                "instructions": "Resolve PR #999",
                "publish": {"mode": "none"},
            }
        },
        runtime_mode="codex",
        step=ResolvedTaskStep(
            step_index=0,
            step_id="step-1",
            title=None,
            instructions="Follow pr-resolver workflow",
            effective_skill_id="pr-resolver",
            effective_skill_args={},
            has_step_instructions=True,
        ),
        total_steps=1,
    )

    assert (
        "Commit/push/merge directly when required by this skill. Publish stage is disabled for this task."
        in instruction
    )
    assert (
        "Do NOT commit or push. Publish is handled by MoonMind publish stage."
        not in instruction
    )


async def test_run_once_task_steps_fail_fast_on_first_failed_step(
    tmp_path: Path,
) -> None:
    """Execute stage should stop on first step failure and skip publish."""

    step1_log = tmp_path / "step1.log"
    step2_log = tmp_path / "step2.log"
    step1_log.write_text("step1", encoding="utf-8")
    step2_log.write_text("step2", encoding="utf-8")

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "branch"},
                "steps": [
                    {"id": "step-1", "instructions": "Do step 1"},
                    {"id": "step-2", "instructions": "Do step 2"},
                    {"id": "step-3", "instructions": "Do step 3"},
                ],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        [
            WorkerExecutionResult(
                succeeded=True,
                summary="step1 ok",
                error_message=None,
                artifacts=(ArtifactUpload(path=step1_log, name="logs/codex_exec.log"),),
            ),
            WorkerExecutionResult(
                succeeded=False,
                summary=None,
                error_message="step2 failed",
                artifacts=(ArtifactUpload(path=step2_log, name="logs/codex_exec.log"),),
            ),
            WorkerExecutionResult(
                succeeded=True,
                summary="unexpected step3",
                error_message=None,
            ),
        ]
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
    assert queue.completed == []
    assert len(queue.failed) == 1
    assert "step2 failed" in queue.failed[0]
    assert queue.failed_finish_payloads[0]["finishOutcomeCode"] == "FAILED"
    assert queue.failed_finish_payloads[0]["finishOutcomeStage"] == "execute"
    failure_summary = queue.failed_finish_payloads[0]["finishSummary"]
    assert isinstance(failure_summary, dict)
    assert failure_summary["finishOutcome"]["stage"] == "execute"
    assert "reports/run_summary.json" in queue.uploaded
    assert "reports/errors.json" in queue.uploaded
    assert handler.calls == ["codex_exec", "codex_exec"]
    assert not any(
        event["message"] == "moonmind.task.publish" for event in queue.events
    )
    failed_events = [
        event for event in queue.events if event["message"] == "task.step.failed"
    ]
    assert len(failed_events) == 1
    assert failed_events[0]["payload"]["stepId"] == "step-2"


async def test_run_once_task_steps_materialize_union_of_selected_skills(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Prepare stage should materialize the union of non-auto task/step skills."""

    class _MaterializedWorkspace:
        def __init__(self, selected):
            self.selected = selected

        def to_payload(self):
            return {"selectedSkills": self.selected}

    captured: dict[str, object] = {}

    def _fake_resolve_run_skill_selection(*, run_id, context):
        captured["run_id"] = run_id
        captured["skills"] = list(context.get("skill_selection") or [])
        return object()

    def _fake_materialize_run_skill_workspace(
        *,
        selection,
        run_root,
        cache_root,
        verify_signatures,
    ):
        del selection, run_root, cache_root, verify_signatures
        return _MaterializedWorkspace(captured.get("skills") or [])

    monkeypatch.setattr(
        "moonmind.agents.codex_worker.worker.resolve_run_skill_selection",
        _fake_resolve_run_skill_selection,
    )
    monkeypatch.setattr(
        "moonmind.agents.codex_worker.worker.materialize_run_skill_workspace",
        _fake_materialize_run_skill_workspace,
    )

    step_log = tmp_path / "step.log"
    step_log.write_text("step", encoding="utf-8")
    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "speckit", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "main", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [
                    {"id": "one", "instructions": "First", "skill": {"id": "custom"}},
                    {"id": "two", "instructions": "Second", "skill": {"id": "speckit"}},
                ],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        [
            WorkerExecutionResult(
                succeeded=True,
                summary="step1 ok",
                error_message=None,
                artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
            ),
            WorkerExecutionResult(
                succeeded=True,
                summary="step2 ok",
                error_message=None,
                artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
            ),
        ]
    )
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
        allowed_skills=("speckit", "custom"),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert len(queue.completed) == 1
    assert set(captured["skills"]) == {"custom", "speckit"}
    assert handler.calls == ["codex_skill:custom:True", "codex_skill:speckit:False"]


async def test_run_once_rejects_runtime_not_supported_by_worker_mode(
    tmp_path: Path,
) -> None:
    """Runtime-specific worker mode should reject tasks for other runtimes."""

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
            },
        },
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
        worker_runtime="gemini",
        worker_capabilities=("gemini", "git"),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert queue.completed == []
    assert len(queue.failed) == 1
    assert "unsupported task runtime" in queue.failed[0]


async def test_run_once_rejects_when_required_capabilities_missing(
    tmp_path: Path,
) -> None:
    """Deny-by-default policy should fail jobs requiring unavailable capabilities."""

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "requiredCapabilities": ["codex", "git", "qdrant"],
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
            },
        },
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
        worker_capabilities=("codex", "git"),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert len(queue.failed) == 1
    assert "missing required capabilities" in queue.failed[0]
    assert handler.calls == []


async def test_run_once_rejects_resolve_pr_publish_none_without_pr_resolver(
    tmp_path: Path,
) -> None:
    """Contract validation should reject resolve-PR tasks that cannot self-publish."""

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "Resolve PR #777",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "feature/resolve-pr", "newBranch": None},
                "publish": {"mode": "none"},
            },
        },
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
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert queue.completed == []
    assert len(queue.failed) == 1
    assert (
        "resolve-PR objectives with task.publish.mode='none' require skill 'pr-resolver'"
        in queue.failed[0]
    )
    assert handler.calls == []


async def test_run_once_fails_resolve_pr_when_final_state_unresolved(
    tmp_path: Path,
) -> None:
    """resolve-PR runs must fail when final snapshot remains unresolved."""

    step_log = tmp_path / "resolve-pr.log"
    step_log.write_text("step", encoding="utf-8")

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "Resolve PR #321",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "feature/resolve-pr", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "id": "resolve",
                        "instructions": "Follow pr-resolver workflow",
                        "skill": {"id": "pr-resolver", "args": {"pr": "321"}},
                    }
                ],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])

    class _UnresolvedPrStateHandler(FakeHandler):
        async def handle_skill(
            self,
            *,
            job_id,
            payload,
            selected_skill,
            fallback=False,
            cancel_event=None,
            output_chunk_callback=None,
        ):
            snapshot_path = (
                tmp_path
                / str(job_id)
                / "repo"
                / "artifacts"
                / "pr_resolver_snapshot.json"
            )
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(
                json.dumps(
                    {
                        "pr": {
                            "number": 321,
                            "mergeable": "CONFLICTING",
                            "mergeStateStatus": "DIRTY",
                        },
                        "commentsSummary": {
                            "hasActionableComments": True,
                            "actionableCommentCount": 2,
                        },
                    }
                ),
                encoding="utf-8",
            )
            return await super().handle_skill(
                job_id=job_id,
                payload=payload,
                selected_skill=selected_skill,
                fallback=fallback,
                cancel_event=cancel_event,
                output_chunk_callback=output_chunk_callback,
            )

    handler = _UnresolvedPrStateHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="resolver completed",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
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
    assert queue.completed == []
    assert len(queue.failed) == 1
    assert "pr-resolution final state unresolved" in queue.failed[0]
    assert "reports/pr_resolution_validation.json" in queue.uploaded
    assert queue.failed_finish_payloads[0]["finishOutcomeStage"] == "execute"
    assert queue.failed_finish_payloads[0]["finishOutcomeCode"] == "FAILED"
    assert not any(
        event["message"] == "moonmind.task.publish" for event in queue.events
    )
    assert handler.calls == ["codex_skill:pr-resolver:True"]


async def test_run_once_allows_resolve_pr_when_final_state_is_resolved(
    tmp_path: Path,
) -> None:
    """resolve-PR runs should complete when final snapshot has no unresolved signals."""

    step_log = tmp_path / "resolve-pr-ok.log"
    step_log.write_text("step", encoding="utf-8")

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "Resolve PR #654",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": "feature/resolve-pr", "newBranch": None},
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "id": "resolve",
                        "instructions": "Follow pr-resolver workflow",
                        "skill": {"id": "pr-resolver", "args": {"pr": "654"}},
                    }
                ],
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])

    class _ResolvedPrStateHandler(FakeHandler):
        async def handle_skill(
            self,
            *,
            job_id,
            payload,
            selected_skill,
            fallback=False,
            cancel_event=None,
            output_chunk_callback=None,
        ):
            snapshot_path = (
                tmp_path
                / str(job_id)
                / "repo"
                / "artifacts"
                / "pr_resolver_snapshot.json"
            )
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(
                json.dumps(
                    {
                        "pr": {
                            "number": 654,
                            "mergeable": "MERGEABLE",
                            "mergeStateStatus": "CLEAN",
                        },
                        "commentsSummary": {
                            "hasActionableComments": False,
                            "actionableCommentCount": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            return await super().handle_skill(
                job_id=job_id,
                payload=payload,
                selected_skill=selected_skill,
                fallback=fallback,
                cancel_event=cancel_event,
                output_chunk_callback=output_chunk_callback,
            )

    handler = _ResolvedPrStateHandler(
        WorkerExecutionResult(
            succeeded=True,
            summary="resolver completed",
            error_message=None,
            artifacts=(ArtifactUpload(path=step_log, name="logs/codex_exec.log"),),
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
    assert queue.failed == []
    assert len(queue.completed) == 1
    assert "reports/pr_resolution_validation.json" in queue.uploaded
    assert handler.calls == ["codex_skill:pr-resolver:True"]


async def test_run_once_universal_worker_executes_gemini_task(tmp_path: Path) -> None:
    """Universal worker mode should execute non-codex runtime tasks."""

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "gemini",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "gemini"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
            },
        },
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
        worker_runtime="universal",
        worker_capabilities=("codex", "gemini", "claude", "git"),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert len(queue.completed) == 1
    assert queue.failed == []
    assert handler.calls == []


async def test_run_once_task_container_executes_generic_docker_path(
    tmp_path: Path,
) -> None:
    """Container-enabled task should execute through docker path, not codex handler."""

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
                "container": {
                    "enabled": True,
                    "image": "mcr.microsoft.com/dotnet/sdk:8.0",
                    "command": ["bash", "-lc", "dotnet --info"],
                    "env": {"NUGET_AUTH_TOKEN": "secret-token-value"},
                },
            },
        },
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
        worker_capabilities=("codex", "git", "docker"),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert len(queue.completed) == 1
    assert queue.failed == []
    assert handler.calls == []
    assert "container/metadata/run.json" in queue.uploaded
    assert any(
        event["message"] == "moonmind.task.container.started" for event in queue.events
    )
    assert any(
        event["message"] == "moonmind.task.container.finished" for event in queue.events
    )

    execute_log = tmp_path / str(job.id) / "artifacts" / "logs" / "execute.log"
    content = execute_log.read_text(encoding="utf-8")
    assert "docker run" in content
    assert "mcr.microsoft.com/dotnet/sdk:8.0" in content
    assert "--env NUGET_AUTH_TOKEN" in content
    assert "secret-token-value" not in content


async def test_run_once_task_container_supports_distinct_images(
    tmp_path: Path,
) -> None:
    """One worker should execute container tasks with different task-provided images."""

    dotnet_job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
                "container": {
                    "enabled": True,
                    "image": "mcr.microsoft.com/dotnet/sdk:8.0",
                    "command": ["bash", "-lc", "dotnet --info"],
                },
            },
        },
    )
    alpine_job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
                "container": {
                    "enabled": True,
                    "image": "alpine:3.20",
                    "command": ["sh", "-lc", "echo ok"],
                },
            },
        },
    )
    queue = FakeQueueClient(jobs=[dotnet_job, alpine_job])
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
        worker_capabilities=("codex", "git", "docker"),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    first_processed = await worker.run_once()
    second_processed = await worker.run_once()

    assert first_processed is True
    assert second_processed is True
    assert len(queue.completed) == 2
    assert queue.failed == []
    assert handler.calls == []

    first_log = (
        tmp_path / str(dotnet_job.id) / "artifacts" / "logs" / "execute.log"
    ).read_text(encoding="utf-8")
    second_log = (
        tmp_path / str(alpine_job.id) / "artifacts" / "logs" / "execute.log"
    ).read_text(encoding="utf-8")
    assert "mcr.microsoft.com/dotnet/sdk:8.0" in first_log
    assert "alpine:3.20" in second_log


async def test_run_once_task_container_with_steps_fails_contract_validation(
    tmp_path: Path,
) -> None:
    """Container tasks with explicit steps should fail canonical contract validation."""

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
                "container": {
                    "enabled": True,
                    "image": "python:3.11",
                    "command": ["python", "--version"],
                },
                "steps": [{"id": "step-1", "instructions": "Do work"}],
            },
        },
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
        worker_capabilities=("codex", "git", "docker"),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert queue.completed == []
    assert len(queue.failed) == 1
    assert "invalid job payload" in queue.failed[0]
    assert "task.steps is not supported" in queue.failed[0]


async def test_run_once_task_container_timeout_attempts_stop_and_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """Timed out container execution should attempt docker stop and fail the job."""

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
                "container": {
                    "enabled": True,
                    "image": "mcr.microsoft.com/dotnet/sdk:8.0",
                    "command": ["bash", "-lc", "sleep 5"],
                    "timeoutSeconds": 1,
                },
            },
        },
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
        worker_capabilities=("codex", "git", "docker"),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    recorded_commands: list[tuple[str, ...]] = []

    async def fake_run_stage_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        timeout_seconds=None,
    ):
        _ = (cwd, log_path, check, env, redaction_values)
        recorded_commands.append(tuple(str(item) for item in command))
        if len(command) >= 2 and command[0] == "docker" and command[1] == "run":
            raise asyncio.TimeoutError(
                f"command timed out after {float(timeout_seconds or 0):g}s"
            )
        return CommandResult(tuple(command), 0, "", "")

    monkeypatch.setattr(worker, "_run_stage_command", fake_run_stage_command)

    processed = await worker.run_once()

    assert processed is True
    assert queue.completed == []
    assert len(queue.failed) == 1
    assert "timed out" in queue.failed[0]
    assert handler.calls == []
    assert any(cmd[:2] == ("docker", "stop") for cmd in recorded_commands)


async def test_run_once_task_container_precreates_artifact_subdir(
    tmp_path: Path, monkeypatch
) -> None:
    """Container artifact subdir should exist before docker run starts."""

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
                "container": {
                    "enabled": True,
                    "image": "alpine:3.20",
                    "command": ["sh", "-lc", "echo ok"],
                    "artifactsSubdir": "container/custom",
                },
            },
        },
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
        worker_capabilities=("codex", "git", "docker"),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    async def fake_run_stage_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        timeout_seconds=None,
    ):
        _ = timeout_seconds
        if len(command) >= 2 and command[0] == "docker" and command[1] == "run":
            artifact_root = (
                tmp_path / str(job.id) / "artifacts" / "container" / "custom"
            )
            assert artifact_root.exists()
            assert artifact_root.is_dir()
        return CommandResult(tuple(command), 0, "", "")

    monkeypatch.setattr(worker, "_run_stage_command", fake_run_stage_command)

    processed = await worker.run_once()

    assert processed is True
    assert len(queue.completed) == 1
    assert queue.failed == []


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
        skill_policy_mode="allowlist",
        allowed_skills=("speckit",),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert len(queue.failed) == 1
    assert "skill not allowlisted" in queue.failed[0]
    assert handler.calls == []


async def test_run_once_codex_skill_permissive_mode_allows_non_allowlisted_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Permissive mode should allow selected skills outside configured allowlist."""

    class _MaterializedWorkspace:
        def to_payload(self):
            return {"skills": [{"name": "custom-skill"}]}

    def _resolve_run_skill_selection_stub(*, run_id, context):
        return object()

    monkeypatch.setattr(
        "moonmind.agents.codex_worker.worker.resolve_run_skill_selection",
        _resolve_run_skill_selection_stub,
    )
    monkeypatch.setattr(
        "moonmind.agents.codex_worker.worker.materialize_run_skill_workspace",
        lambda *, selection, run_root, cache_root, verify_signatures: _MaterializedWorkspace(),
    )

    job = ClaimedJob(
        id=uuid4(),
        type="codex_skill",
        payload={"skillId": "custom-skill", "inputs": {"repo": "Moon/Mind"}},
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="done", error_message=None)
    )
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
        skill_policy_mode="permissive",
        allowed_skills=("speckit",),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert queue.failed == []
    assert len(queue.completed) == 1
    assert handler.calls == ["codex_skill:custom-skill:True"]


async def test_heartbeat_loop_runs_on_lease_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    cancel_event = asyncio.Event()
    original_sleep = asyncio.sleep

    async def _fast_sleep(_seconds: float) -> None:
        await original_sleep(0)

    heartbeats_target = 2
    original_heartbeat = queue.heartbeat

    async def _heartbeat_and_stop(*, job_id, worker_id, lease_seconds):
        result = await original_heartbeat(
            job_id=job_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        if len(queue.heartbeats) >= heartbeats_target:
            stop_event.set()
        return result

    monkeypatch.setattr(
        "moonmind.agents.codex_worker.worker.asyncio.sleep",
        _fast_sleep,
    )
    monkeypatch.setattr(queue, "heartbeat", _heartbeat_and_stop)
    task = asyncio.create_task(
        worker._heartbeat_loop(
            job_id=uuid4(),
            stop_event=stop_event,
            cancel_event=cancel_event,
        )
    )
    await asyncio.wait_for(stop_event.wait(), timeout=1)
    await asyncio.wait_for(task, timeout=1)

    assert len(queue.heartbeats) >= heartbeats_target


async def test_heartbeat_loop_sets_pause_event_for_quiesce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quiesce mode should trigger the worker pause event during heartbeats."""

    now = datetime.now(UTC)
    quiesce_status = QueueSystemStatus(
        workers_paused=True,
        mode="quiesce",
        reason="short maintenance",
        version=9,
        requested_at=now,
        updated_at=now,
    )
    queue = FakeQueueClient(jobs=[], system_status=quiesce_status)
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
    cancel_event = asyncio.Event()
    pause_event = asyncio.Event()
    original_sleep = asyncio.sleep

    async def _fast_sleep(_seconds: float) -> None:
        await original_sleep(0)

    original_heartbeat = queue.heartbeat

    async def _heartbeat_and_stop(*, job_id, worker_id, lease_seconds):
        result = await original_heartbeat(
            job_id=job_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        if pause_event.is_set():
            stop_event.set()
        return result

    monkeypatch.setattr(
        "moonmind.agents.codex_worker.worker.asyncio.sleep",
        _fast_sleep,
    )
    monkeypatch.setattr(queue, "heartbeat", _heartbeat_and_stop)
    task = asyncio.create_task(
        worker._heartbeat_loop(
            job_id=uuid4(),
            stop_event=stop_event,
            cancel_event=cancel_event,
            pause_event=pause_event,
        )
    )
    await asyncio.wait_for(stop_event.wait(), timeout=1)
    await asyncio.wait_for(task, timeout=1)

    assert pause_event.is_set()


async def test_should_bootstrap_live_session_respects_default_enable(
    tmp_path: Path,
) -> None:
    """Default-enabled live sessions should always bootstrap."""

    queue = FakeQueueClient(jobs=[])
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
        live_session_enabled_default=True,
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    assert await worker._should_bootstrap_live_session(job_id=uuid4()) is True


async def test_ensure_live_session_started_skips_opt_in_without_request(
    tmp_path: Path,
) -> None:
    """Opt-in mode should not start live sessions when no explicit request exists."""

    queue = FakeQueueClient(jobs=[])
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
        live_session_enabled_default=False,
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]
    stage_commands: list[tuple[str, ...]] = []

    async def _capture_stage_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        timeout_seconds=None,
    ):
        _ = (cwd, log_path, check, env, redaction_values, cancel_event, timeout_seconds)
        stage_commands.append(tuple(command))
        return CommandResult(tuple(command), 0, "", "")

    worker._run_stage_command = _capture_stage_command  # type: ignore[method-assign]

    await worker._ensure_live_session_started(
        job_id=uuid4(),
        log_path=tmp_path / "prepare.log",
        cwd=tmp_path / "job",
    )

    assert queue.live_session_reports == []
    assert stage_commands == []
    assert worker._active_live_session is None


async def test_ensure_live_session_started_honors_explicit_request_and_uses_tmate_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt-in mode should bootstrap when explicitly requested and keep RW endpoints out of stage commands."""

    queue = FakeQueueClient(jobs=[])
    queue.live_session_state = {"session": {"status": "starting"}}
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
        live_session_enabled_default=False,
        tmate_server_host="tmate.internal",
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]
    stage_commands: list[tuple[str, ...]] = []
    quiet_commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(
        "moonmind.agents.codex_worker.worker.shutil.which", lambda _: "tmate"
    )

    async def _capture_stage_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        timeout_seconds=None,
    ):
        _ = (cwd, log_path, check, env, redaction_values, cancel_event, timeout_seconds)
        stage_commands.append(tuple(command))
        return CommandResult(tuple(command), 0, "", "")

    async def _capture_quiet_command(command, *, cwd):
        _ = cwd
        normalized = tuple(command)
        quiet_commands.append(normalized)
        if "#{tmate_ssh_ro}" in normalized:
            return CommandResult(normalized, 0, "ssh ro\n", "")
        if "#{tmate_ssh}" in normalized:
            return CommandResult(normalized, 0, "ssh rw\n", "")
        if "#{tmate_web_ro}" in normalized:
            return CommandResult(normalized, 0, "https://ro.example\n", "")
        if "#{tmate_web}" in normalized:
            return CommandResult(normalized, 0, "https://rw.example\n", "")
        return CommandResult(normalized, 0, "", "")

    worker._run_stage_command = _capture_stage_command  # type: ignore[method-assign]
    worker._run_command_without_logging = _capture_quiet_command  # type: ignore[method-assign]

    job_id = uuid4()
    await worker._ensure_live_session_started(
        job_id=job_id,
        log_path=tmp_path / "prepare.log",
        cwd=tmp_path / "job",
    )

    assert worker._active_live_session is not None
    config_path = worker._active_live_session.config_path
    assert config_path is not None
    assert config_path.exists()
    assert all("display" not in command for command in stage_commands)
    assert any("#{tmate_ssh}" in command for command in quiet_commands)
    assert any("-f" in command for command in stage_commands)
    assert any(report.get("status") == "ready" for report in queue.live_session_reports)

    await worker._teardown_live_session(job_id=job_id)
    assert not config_path.exists()


async def test_run_once_acks_cancellation_requested_via_heartbeat(
    tmp_path: Path,
) -> None:
    """Worker should acknowledge cancellation and avoid completion/failure transitions."""

    class CancelAwareHandler(FakeHandler):
        async def handle(
            self, *, job_id, payload, cancel_event=None, output_chunk_callback=None
        ):
            assert cancel_event is not None
            await asyncio.wait_for(cancel_event.wait(), timeout=3.0)
            raise CommandCancelledError("cancelled by request")

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
            },
        },
    )
    queue = FakeQueueClient(jobs=[job])
    queue.cancel_requested_at = datetime.now(UTC).isoformat()
    handler = CancelAwareHandler(
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

    processed = await worker.run_once()

    assert processed is True
    assert queue.completed == []
    assert queue.failed == []
    assert len(queue.cancel_acks) == 1
    assert queue.cancel_acks[0][0] == str(job.id)
    assert queue.cancel_ack_finish_payloads[0]["finishOutcomeCode"] == "CANCELLED"
    cancel_summary = queue.cancel_ack_finish_payloads[0]["finishSummary"]
    assert isinstance(cancel_summary, dict)
    assert cancel_summary["finishOutcome"]["code"] == "CANCELLED"
    assert "reports/run_summary.json" in queue.uploaded
    assert any(
        event["message"] == "Job cancellation requested; stopping"
        for event in queue.events
    )


async def test_determine_finish_outcome_pr_publish_without_pr_url_maps_to_published_pr(
    tmp_path: Path,
) -> None:
    """PR publish mode should classify successful publish as PUBLISHED_PR even without URL."""

    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=FakeQueueClient(jobs=[]),
        codex_exec_handler=FakeHandler(
            WorkerExecutionResult(succeeded=True, summary="ok", error_message=None)
        ),
    )

    outcome = worker._determine_finish_outcome(
        succeeded=True,
        cancelled=False,
        cancel_reason=None,
        failure_stage=None,
        failure_reason=None,
        publish_mode="pr",
        publish_status="published",
        publish_reason=None,
        publish_pr_url=None,
        publish_branch="task/branch",
    )

    assert outcome.code == "PUBLISHED_PR"
    assert outcome.stage == "publish"


async def test_live_log_chunk_callback_emits_redacted_step_metadata(
    tmp_path: Path,
) -> None:
    """Live log callback should emit redacted log events with step metadata."""

    queue = FakeQueueClient(jobs=[])
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
        live_log_events_batch_bytes=32,
        live_log_events_flush_interval_ms=20,
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]
    worker._register_redaction_value("secret-token")

    callback = worker._build_live_log_chunk_callback(
        job_id=uuid4(),
        stage="moonmind.task.execute",
        step_id="step-2",
        step_index=1,
    )
    assert callback is not None

    await callback("stdout", "hello secret-token\n")
    await callback("stderr", "warn output\n")
    await callback("stdout", None)
    await callback("stderr", None)

    emitted = [event for event in queue.events if event["payload"].get("kind") == "log"]
    assert len(emitted) >= 2
    assert any(event["payload"].get("stream") == "stdout" for event in emitted)
    assert any(event["payload"].get("stream") == "stderr" for event in emitted)
    assert any(event["level"] == "warn" for event in emitted)
    first_stdout = next(
        event for event in emitted if event["payload"].get("stream") == "stdout"
    )
    assert first_stdout["payload"]["stage"] == "moonmind.task.execute"
    assert first_stdout["payload"]["stepId"] == "step-2"
    assert first_stdout["payload"]["stepIndex"] == 1
    assert "secret-token" not in first_stdout["message"]
    assert "[REDACTED]" in first_stdout["message"]


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
    monkeypatch.setenv("MOONMIND_DOCKER_BINARY", "docker")
    monkeypatch.setenv("MOONMIND_CONTAINER_WORKSPACE_VOLUME", "agent_workspaces")
    monkeypatch.setenv("MOONMIND_CONTAINER_TIMEOUT_SECONDS", "1800")
    monkeypatch.setenv("MOONMIND_STAGE_COMMAND_TIMEOUT_SECONDS", "2400")
    monkeypatch.setenv("MOONMIND_ARTIFACT_UPLOAD_INCREMENTAL", "false")
    monkeypatch.setenv("MOONMIND_STEP_LOG_MAX_BYTES", "2097152")
    monkeypatch.setenv("MOONMIND_SKILL_POLICY_MODE", "allowlist")
    monkeypatch.setenv("MOONMIND_GIT_USER_NAME", "Nate Sticco")
    monkeypatch.setenv("MOONMIND_GIT_USER_EMAIL", "nsticco@gmail.com")

    config = CodexWorkerConfig.from_env()

    assert config.moonmind_url == "http://localhost:5000"
    assert config.worker_id == "executor-01"
    assert config.worker_token == "token-123"
    assert config.poll_interval_ms == 2500
    assert config.lease_seconds == 90
    assert str(config.workdir) == "/tmp/moonmind-worker"
    assert config.default_codex_model == "gpt-5-codex"
    assert config.default_codex_effort == "high"
    assert config.skill_policy_mode == "allowlist"
    assert config.worker_capabilities == ("codex", "git")
    assert config.docker_binary == "docker"
    assert config.container_workspace_volume == "agent_workspaces"
    assert config.container_default_timeout_seconds == 1800
    assert config.stage_command_timeout_seconds == 2400
    assert config.artifact_upload_incremental is False
    assert config.step_log_max_bytes == 2097152
    assert config.git_user_name == "Nate Sticco"
    assert config.git_user_email == "nsticco@gmail.com"


async def test_config_from_env_rejects_non_integer_step_log_max_bytes(
    monkeypatch,
) -> None:
    """Non-integer step log cap values should include actionable context."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.setenv("MOONMIND_STEP_LOG_MAX_BYTES", "abc")

    with pytest.raises(ValueError, match="must be an integer"):
        CodexWorkerConfig.from_env()


async def test_config_from_env_rejects_excessive_step_log_max_bytes(
    monkeypatch,
) -> None:
    """Step log cap should enforce a safe upper bound."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.setenv("MOONMIND_STEP_LOG_MAX_BYTES", str(70 * 1024 * 1024))

    with pytest.raises(ValueError, match="must be <="):
        CodexWorkerConfig.from_env()


async def test_config_from_env_supports_legacy_spec_git_user_env(monkeypatch) -> None:
    """Legacy SPEC_WORKFLOW git user env vars should remain supported by worker config."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.delenv("WORKFLOW_GIT_USER_NAME", raising=False)
    monkeypatch.delenv("WORKFLOW_GIT_USER_EMAIL", raising=False)
    monkeypatch.delenv("MOONMIND_GIT_USER_NAME", raising=False)
    monkeypatch.delenv("MOONMIND_GIT_USER_EMAIL", raising=False)
    monkeypatch.setenv("SPEC_WORKFLOW_GIT_USER_NAME", "Legacy Name")
    monkeypatch.setenv("SPEC_WORKFLOW_GIT_USER_EMAIL", "legacy@example.com")

    config = CodexWorkerConfig.from_env()

    assert config.git_user_name == "Legacy Name"
    assert config.git_user_email == "legacy@example.com"


async def test_config_from_env_git_user_precedence(monkeypatch) -> None:
    """Worker config should resolve git user vars as WORKFLOW > SPEC_WORKFLOW > MOONMIND."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.setenv("MOONMIND_GIT_USER_NAME", "MoonMind Name")
    monkeypatch.setenv("MOONMIND_GIT_USER_EMAIL", "moonmind@example.com")
    monkeypatch.setenv("SPEC_WORKFLOW_GIT_USER_NAME", "Spec Name")
    monkeypatch.setenv("SPEC_WORKFLOW_GIT_USER_EMAIL", "spec@example.com")
    monkeypatch.setenv("WORKFLOW_GIT_USER_NAME", "Workflow Name")
    monkeypatch.setenv("WORKFLOW_GIT_USER_EMAIL", "workflow@example.com")

    config = CodexWorkerConfig.from_env()

    assert config.git_user_name == "Workflow Name"
    assert config.git_user_email == "workflow@example.com"


async def test_config_from_env_supports_legacy_skill_policy_mode_env(
    monkeypatch,
) -> None:
    """Legacy SKILL_POLICY_MODE should remain supported for compatibility."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.delenv("WORKFLOW_SKILL_POLICY_MODE", raising=False)
    monkeypatch.delenv("SPEC_WORKFLOW_SKILL_POLICY_MODE", raising=False)
    monkeypatch.setenv("SKILL_POLICY_MODE", "allowlist")

    config = CodexWorkerConfig.from_env()

    assert config.skill_policy_mode == "allowlist"


async def test_config_from_env_supports_legacy_moonmind_allowed_skills(
    monkeypatch,
) -> None:
    """Legacy MOONMIND_ALLOWED_SKILLS should still participate in resolution."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.delenv("WORKFLOW_ALLOWED_SKILLS", raising=False)
    monkeypatch.delenv("SPEC_WORKFLOW_ALLOWED_SKILLS", raising=False)
    monkeypatch.setenv("MOONMIND_ALLOWED_SKILLS", "custom,speckit")

    config = CodexWorkerConfig.from_env()

    assert config.allowed_skills == ("custom", "speckit")


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
    monkeypatch.delenv("MOONMIND_DOCKER_BINARY", raising=False)
    monkeypatch.delenv("MOONMIND_CONTAINER_WORKSPACE_VOLUME", raising=False)
    monkeypatch.delenv("MOONMIND_CONTAINER_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("MOONMIND_STAGE_COMMAND_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("MOONMIND_ARTIFACT_UPLOAD_INCREMENTAL", raising=False)
    monkeypatch.delenv("MOONMIND_STEP_LOG_MAX_BYTES", raising=False)
    monkeypatch.delenv("MOONMIND_SKILL_POLICY_MODE", raising=False)
    monkeypatch.delenv("SPEC_WORKFLOW_SKILL_POLICY_MODE", raising=False)
    monkeypatch.delenv("SKILL_POLICY_MODE", raising=False)

    config = CodexWorkerConfig.from_env()

    assert config.worker_token is None
    assert config.poll_interval_ms == 1500
    assert config.lease_seconds == 120
    assert str(config.workdir) == "var/worker"
    assert config.default_skill == "speckit"
    assert config.skill_policy_mode == "permissive"
    assert config.allowed_skills == ("speckit",)
    assert config.default_codex_model is None
    assert config.default_codex_effort is None
    assert config.legacy_job_types_enabled is True
    assert config.worker_runtime == "codex"
    assert config.allowed_types == ("task", "codex_exec", "codex_skill")
    assert config.worker_capabilities == ("codex", "git", "gh")
    assert config.docker_binary == "docker"
    assert config.container_workspace_volume is None
    assert config.container_default_timeout_seconds == 3600
    assert config.stage_command_timeout_seconds == 3600
    assert config.artifact_upload_incremental is True
    assert config.step_log_max_bytes == 1024 * 1024


async def test_config_from_env_enables_task_proposals(monkeypatch) -> None:
    """Flag should toggle worker proposal submission behavior."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.setenv("MOONMIND_ENABLE_TASK_PROPOSALS", "true")
    config = CodexWorkerConfig.from_env()
    assert config.enable_task_proposals is True

    monkeypatch.setenv("MOONMIND_ENABLE_TASK_PROPOSALS", "false")
    config = CodexWorkerConfig.from_env()
    assert config.enable_task_proposals is False


async def test_config_from_env_parses_live_session_settings(monkeypatch) -> None:
    """Live session config should parse from MOONMIND_LIVE_SESSION_* variables."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.setenv("MOONMIND_LIVE_SESSION_ENABLED_DEFAULT", "false")
    monkeypatch.setenv("MOONMIND_LIVE_SESSION_PROVIDER", "tmate")
    monkeypatch.setenv("MOONMIND_LIVE_SESSION_TTL_MINUTES", "120")
    monkeypatch.setenv("MOONMIND_LIVE_SESSION_RW_GRANT_TTL_MINUTES", "30")
    monkeypatch.setenv("MOONMIND_LIVE_SESSION_ALLOW_WEB", "true")
    monkeypatch.setenv("MOONMIND_TMATE_SERVER_HOST", "tmate.internal")
    monkeypatch.setenv("MOONMIND_LIVE_SESSION_MAX_CONCURRENT_PER_WORKER", "5")

    config = CodexWorkerConfig.from_env()

    assert config.live_session_enabled_default is False
    assert config.live_session_provider == "tmate"
    assert config.live_session_ttl_minutes == 120
    assert config.live_session_rw_grant_ttl_minutes == 30
    assert config.live_session_allow_web is True
    assert config.tmate_server_host == "tmate.internal"
    assert config.live_session_max_concurrent_per_worker == 5


async def test_config_from_env_rejects_invalid_skill_policy_mode(monkeypatch) -> None:
    """Invalid policy mode should fail fast during worker startup."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.setenv("MOONMIND_SKILL_POLICY_MODE", "invalid")

    with pytest.raises(ValueError, match="WORKFLOW_SKILL_POLICY_MODE must be one of"):
        CodexWorkerConfig.from_env()


async def test_config_from_env_rejects_non_integer_stage_command_timeout(
    monkeypatch,
) -> None:
    """Stage command timeout env must be an integer for actionable startup errors."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.setenv("MOONMIND_STAGE_COMMAND_TIMEOUT_SECONDS", "abc")

    with pytest.raises(
        ValueError,
        match="WORKFLOW_STAGE_COMMAND_TIMEOUT_SECONDS must be an integer",
    ):
        CodexWorkerConfig.from_env()


async def test_config_from_env_runtime_mode_controls_default_capabilities(
    monkeypatch,
) -> None:
    """Runtime mode should derive safe default capabilities when unset."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.setenv("MOONMIND_WORKER_RUNTIME", "universal")
    monkeypatch.delenv("MOONMIND_WORKER_CAPABILITIES", raising=False)

    config = CodexWorkerConfig.from_env()

    assert config.worker_runtime == "universal"
    assert config.worker_capabilities == ("codex", "gemini", "claude", "git", "gh")


async def test_config_from_env_disables_legacy_job_types_when_flag_is_off(
    monkeypatch,
) -> None:
    """Workers should be task-only when legacy compatibility flag is disabled."""

    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.setenv("MOONMIND_ENABLE_LEGACY_JOB_TYPES", "false")

    config = CodexWorkerConfig.from_env()

    assert config.legacy_job_types_enabled is False
    assert config.allowed_types == ("task",)


async def test_config_from_env_loads_vault_settings(
    monkeypatch, tmp_path: Path
) -> None:
    """Vault settings should parse from environment for secret-ref resolution."""

    token_file = tmp_path / "vault.token"
    token_file.write_text("vault-file-token\n", encoding="utf-8")
    monkeypatch.setenv("MOONMIND_URL", "http://localhost:5000")
    monkeypatch.setenv("MOONMIND_VAULT_ADDR", "https://vault.local")
    monkeypatch.setenv("MOONMIND_VAULT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("MOONMIND_VAULT_NAMESPACE", "moonmind")
    monkeypatch.setenv("MOONMIND_VAULT_ALLOWED_MOUNTS", "kv,kv-runtime")
    monkeypatch.setenv("MOONMIND_VAULT_TIMEOUT_SECONDS", "5")
    monkeypatch.delenv("MOONMIND_VAULT_TOKEN", raising=False)

    config = CodexWorkerConfig.from_env()

    assert config.vault_address == "https://vault.local"
    assert config.vault_token == "vault-file-token"
    assert config.vault_token_file == token_file
    assert config.vault_namespace == "moonmind"
    assert config.vault_allowed_mounts == ("kv", "kv-runtime")
    assert config.vault_timeout_seconds == 5.0


async def test_runtime_override_precedence_prefers_task_then_worker_defaults(
    tmp_path: Path,
) -> None:
    """Runtime model/effort should resolve as task override then worker default."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
        worker_runtime="universal",
        default_gemini_model="gemini-default",
        default_gemini_effort="medium",
    )
    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    model, effort = worker._resolve_runtime_overrides(
        canonical_payload={
            "task": {
                "runtime": {
                    "mode": "gemini",
                    "model": None,
                    "effort": None,
                }
            }
        },
        runtime_mode="gemini",
    )
    assert model == "gemini-default"
    assert effort == "medium"

    model_override, effort_override = worker._resolve_runtime_overrides(
        canonical_payload={
            "task": {
                "runtime": {
                    "mode": "gemini",
                    "model": "gemini-2.5-pro",
                    "effort": "high",
                }
            }
        },
        runtime_mode="gemini",
    )
    assert model_override == "gemini-2.5-pro"
    assert effort_override == "high"


@pytest.fixture
def codex_worker_components(
    tmp_path: Path,
) -> tuple[CodexWorker, FakeQueueClient, FakeHandler]:
    """Provides a configured worker with fake queue/handler dependencies."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
    )
    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]
    return worker, queue, handler


def test_resolve_skills_cache_root_uses_worker_workdir_for_relative_paths(
    codex_worker_components: tuple[CodexWorker, FakeQueueClient, FakeHandler],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _, _ = codex_worker_components
    monkeypatch.setattr(
        settings.spec_workflow,
        "skills_cache_root",
        "var/skill_cache",
        raising=False,
    )

    resolved = worker._resolve_skills_cache_root()

    assert resolved == (worker._config.workdir / "var/skill_cache").resolve()


async def test_derive_default_pr_title_prefers_first_non_empty_step_title(
    codex_worker_components: tuple[CodexWorker, FakeQueueClient, FakeHandler],
) -> None:
    """Default title should come from the first non-empty step title."""

    worker, _, _ = codex_worker_components
    payload = {
        "task": {
            "instructions": "Fallback instructions",
            "steps": [
                {"id": "one", "title": " "},
                {"id": "two", "title": "Ship publish title defaults for queue tasks"},
            ],
        }
    }

    resolved_steps = worker._resolve_task_steps(payload)
    title = worker._derive_default_pr_title(
        job_id=uuid4(),
        canonical_payload=payload,
        resolved_steps=resolved_steps,
    )

    assert title == "Ship publish title defaults for queue tasks"


async def test_derive_default_pr_title_uses_short_correlation_fallback_without_step_titles(
    codex_worker_components: tuple[CodexWorker, FakeQueueClient, FakeHandler],
) -> None:
    """Missing step titles should fall back to task instructions when available."""

    worker, _, _ = codex_worker_components
    job_id = uuid4()
    payload = {
        "task": {
            "instructions": "Fix publish behavior for 123e4567-e89b-12d3-a456-426614174000.",
            "steps": [{"id": "step-1", "title": " "}],
        }
    }

    title = worker._derive_default_pr_title(
        job_id=job_id,
        canonical_payload=payload,
        resolved_steps=worker._resolve_task_steps(payload),
    )

    assert title == "Fix publish behavior for 123e4567-e89b-12d3-a456-426614174000."
    assert str(job_id) not in title
    assert len(title) <= 90


async def test_derive_default_pr_title_uses_task_instructions_when_step_title_is_numeric(
    codex_worker_components: tuple[CodexWorker, FakeQueueClient, FakeHandler],
) -> None:
    """Numeric-only step titles should not hide task instruction intent."""

    worker, _, _ = codex_worker_components
    job_id = uuid4()
    payload = {
        "task": {
            "instructions": "Fix publish behavior without adding step titles.",
            "steps": [{"id": "step-1", "title": "1"}],
        }
    }

    job_id = uuid4()
    title = worker._derive_default_pr_title(
        job_id=job_id,
        canonical_payload=payload,
        resolved_steps=worker._resolve_task_steps(payload),
    )

    assert title == "Fix publish behavior without adding step titles."
    assert str(job_id) not in title
    assert len(title) <= 90


async def test_derive_default_pr_title_sanitizes_embedded_uuid_tokens(
    codex_worker_components: tuple[CodexWorker, FakeQueueClient, FakeHandler],
) -> None:
    """UUIDs adjacent to word characters should still be redacted from titles."""

    worker, _, _ = codex_worker_components
    full_uuid = "123e4567-e89b-12d3-a456-426614174000"
    payload = {
        "task": {
            "instructions": " ",
            "steps": [
                {
                    "id": "step-1",
                    "title": f"Publish task_{full_uuid} for queue telemetry",
                }
            ],
        }
    }

    title = worker._derive_default_pr_title(
        job_id=uuid4(),
        canonical_payload=payload,
        resolved_steps=worker._resolve_task_steps(payload),
    )

    assert title == "Publish task_job for queue telemetry"
    assert full_uuid not in title


async def test_derive_default_pr_title_uses_short_correlation_fallback(
    codex_worker_components: tuple[CodexWorker, FakeQueueClient, FakeHandler],
) -> None:
    """Missing step titles and instructions should fall back to short token title."""

    worker, _, _ = codex_worker_components
    job_id = uuid4()
    payload = {
        "task": {
            "instructions": " ",
            "steps": [{"id": "step-1", "title": " "}],
        }
    }

    title = worker._derive_default_pr_title(
        job_id=job_id,
        canonical_payload=payload,
        resolved_steps=worker._resolve_task_steps(payload),
    )

    assert title == f"MoonMind task result [mm:{str(job_id)[:8]}]"
    assert str(job_id) not in title
    assert len(title) <= 90


async def test_derive_default_pr_body_contains_required_correlation_footer() -> None:
    """Generated PR body should include stable MoonMind metadata footer fields."""

    job_id = uuid4()
    body = CodexWorker._derive_default_pr_body(
        job_id=job_id,
        runtime_mode="gemini",
        base_branch="main",
        head_branch="task/20260219/abcd1234",
    )

    assert "<!-- moonmind:begin -->" in body
    assert f"MoonMind Job: {job_id}" in body
    assert "Runtime: gemini" in body
    assert "Base: main" in body
    assert "Head: task/20260219/abcd1234" in body
    assert "<!-- moonmind:end -->" in body


async def test_derive_default_pr_body_sanitizes_metadata_values() -> None:
    """Generated footer should normalize metadata and redact secret-like branch values."""

    job_id = uuid4()
    body = CodexWorker._derive_default_pr_body(
        job_id=job_id,
        runtime_mode="gemini\nMoonMind Job: forged",
        base_branch="main\nHead: forged",
        head_branch="feature/token=supersecret",
    )

    assert f"MoonMind Job: {job_id}" in body
    assert "Runtime: gemini MoonMind Job: forged" in body
    assert "Base: main Head: forged" in body
    assert "Head: [REDACTED]" in body


async def test_resolve_publish_text_override_preserves_non_empty_verbatim() -> None:
    """Non-empty overrides should remain verbatim while whitespace-only values are omitted."""

    assert CodexWorker._resolve_publish_text_override("  keep exact text  ") == (
        "  keep exact text  "
    )
    assert CodexWorker._resolve_publish_text_override(" \n\t ") is None
    assert CodexWorker._resolve_publish_text_override(None) is None


def test_parse_git_status_paths_collects_renamed_source_paths() -> None:
    """Git status parser should include both sides of a rename or copy for safety."""

    status_output = """
R  moonmind/agents/codex_worker/worker.py -> docs/README.md
R  \"src/legacy path.py\" -> \"legacy/src/archived.py\"
 M \"docs -> handbook.md\"\n"""
    paths = CodexWorker._parse_git_status_paths(status_output)
    assert paths == (
        "moonmind/agents/codex_worker/worker.py",
        "docs/README.md",
        "src/legacy path.py",
        "legacy/src/archived.py",
        "docs -> handbook.md",
    )


def test_is_source_code_change_path_preserves_dotfile_classes() -> None:
    """Source-path classifier should keep dot-prefixed allowlist entries working."""

    assert (
        CodexWorker._is_source_code_change_path(".github/workflows/test.yml") is False
    )
    assert (
        CodexWorker._is_source_code_change_path("./.github/workflows/test.yml") is False
    )
    assert (
        CodexWorker._is_source_code_change_path(".specify/specs/overview.md") is False
    )
    assert CodexWorker._is_source_code_change_path(".gitignore") is False
    assert (
        CodexWorker._is_source_code_change_path(
            "moonmind/agents/codex_worker/worker.py"
        )
        is True
    )


def test_resolve_publish_verification_skip_reason_rejects_legacy_fields() -> None:
    """Only `verificationSkipReason.category`/`reason` should be accepted."""

    assert CodexWorker._resolve_publish_verification_skip_reason(
        {
            "verificationSkipReason": {
                "category": "ops",
                "reason": "scheduled maintenance",
            }
        }
    ) == {"category": "ops", "reason": "scheduled maintenance"}

    with pytest.raises(ValueError, match="task.publish.verificationSkipReason.reason"):
        CodexWorker._resolve_publish_verification_skip_reason(
            {
                "verificationSkipReason": {
                    "category": "ops",
                    "detail": "temporary issue",
                }
            }
        )

    with pytest.raises(
        ValueError, match="task.publish.verificationSkipReason.category"
    ):
        CodexWorker._resolve_publish_verification_skip_reason(
            {
                "verificationSkipReason": {
                    "type": "ops",
                    "details": "temporary issue",
                }
            }
        )

    with pytest.raises(ValueError, match="task.publish.verification.skipReason"):
        CodexWorker._resolve_publish_verification_skip_reason(
            {"verification": {"skipReason": {"category": "ops", "reason": "legacy"}}}
        )


def test_collect_verification_evidence_ignores_non_prefixed_stdout_lines(
    tmp_path: Path,
) -> None:
    """Evidence collection should ignore plain `$`-prefixed output text."""

    prepared = PreparedTaskWorkspace(
        job_root=tmp_path,
        repo_dir=tmp_path / "repo",
        artifacts_dir=tmp_path / "artifacts",
        prepare_log_path=tmp_path / "prepare.log",
        execute_log_path=tmp_path / "execute.log",
        publish_log_path=tmp_path / "publish.log",
        task_context_path=tmp_path / "context",
        publish_result_path=tmp_path / "publish-result.json",
        default_branch="main",
        starting_branch="main",
        new_branch="feature/branch",
        working_branch="feature/branch",
        workdir_mode="checkout",
        repo_command_env=None,
        publish_command_env=None,
    )
    prepared.execute_log_path.write_text(
        "$ pytest\n" "[command] $ ./tools/test_unit.sh\n" "[command] $ npm run build\n",
        encoding="utf-8",
    )
    evidence, read_errors = CodexWorker._collect_verification_evidence(
        prepared=prepared
    )
    assert len(evidence) == 2
    assert len(read_errors) == 0
    assert evidence[0]["command"] == "./tools/test_unit.sh"
    assert evidence[1]["command"] == "npm run build"


def test_collect_verification_evidence_records_log_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreadable verification logs should surface errors while preserving other evidence."""

    prepared = PreparedTaskWorkspace(
        job_root=tmp_path,
        repo_dir=tmp_path / "repo",
        artifacts_dir=tmp_path / "artifacts",
        prepare_log_path=tmp_path / "prepare.log",
        execute_log_path=tmp_path / "execute.log",
        publish_log_path=tmp_path / "publish.log",
        task_context_path=tmp_path / "context",
        publish_result_path=tmp_path / "publish-result.json",
        default_branch="main",
        starting_branch="main",
        new_branch="feature/branch",
        working_branch="feature/branch",
        workdir_mode="checkout",
        repo_command_env=None,
        publish_command_env=None,
    )
    prepared.artifacts_dir.mkdir(parents=True, exist_ok=True)
    (prepared.artifacts_dir / "logs").mkdir(exist_ok=True)
    prepared.execute_log_path.write_text(
        "[command] $ ./tools/test_unit.sh\n", encoding="utf-8"
    )
    readable_log_path = prepared.artifacts_dir / "logs" / "codex_exec.log"
    readable_log_path.write_text("[command] $ npm run build\n", encoding="utf-8")

    original_read_text = Path.read_text

    def _raise_read_text(self, encoding=None, errors=None):
        if self == prepared.execute_log_path:
            raise OSError("permission denied")
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", _raise_read_text)

    evidence, read_errors = CodexWorker._collect_verification_evidence(
        prepared=prepared
    )
    assert len(evidence) == 1
    assert evidence[0]["command"] == "npm run build"
    assert len(read_errors) == 1
    assert (
        read_errors[0]
        == "could not read one or more verification logs; check worker logs for details"
    )


def test_collect_verification_evidence_prefers_structured_report_records(
    tmp_path: Path,
) -> None:
    """Structured verification records should count as evidence when commands passed."""

    prepared = PreparedTaskWorkspace(
        job_root=tmp_path,
        repo_dir=tmp_path / "repo",
        artifacts_dir=tmp_path / "artifacts",
        prepare_log_path=tmp_path / "prepare.log",
        execute_log_path=tmp_path / "execute.log",
        publish_log_path=tmp_path / "publish.log",
        task_context_path=tmp_path / "context",
        publish_result_path=tmp_path / "publish-result.json",
        default_branch="main",
        starting_branch="main",
        new_branch="feature/branch",
        working_branch="feature/branch",
        workdir_mode="checkout",
        repo_command_env=None,
        publish_command_env=None,
    )
    report_path = prepared.artifacts_dir / "reports" / "verification_commands.jsonl"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schemaVersion": "v1",
                        "command": "./tools/test_unit.sh",
                        "status": "passed",
                        "returncode": 0,
                        "logArtifact": "logs/publish.log",
                    }
                ),
                json.dumps(
                    {
                        "schemaVersion": "v1",
                        "command": "pytest -q",
                        "status": "failed",
                        "returncode": 1,
                        "logArtifact": "logs/publish.log",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    evidence, read_errors = CodexWorker._collect_verification_evidence(
        prepared=prepared
    )

    assert len(read_errors) == 0
    assert len(evidence) == 1
    assert evidence[0]["command"] == "./tools/test_unit.sh"
    assert evidence[0]["artifact"] == "logs/publish.log"


async def test_run_publish_stage_uses_verbatim_overrides_and_redacts_command_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish stage should pass override text unchanged while marking command args as sensitive."""

    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=queue,
        codex_exec_handler=handler,
    )  # type: ignore[arg-type]

    run_calls: list[dict[str, object]] = []

    async def _capture_stage_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
    ):
        _ = (cwd, log_path, check, env, cancel_event)
        run_calls.append(
            {
                "command": tuple(str(part) for part in command),
                "redaction_values": tuple(str(value) for value in redaction_values),
            }
        )
        if tuple(command[:2]) == ("git", "status"):
            return CommandResult(tuple(command), 0, " M worker.py\n", "")
        if tuple(command[:3]) == ("gh", "pr", "create"):
            return CommandResult(
                tuple(command),
                0,
                "https://github.com/MoonLadderStudios/MoonMind/pull/999\n",
                "",
            )
        return CommandResult(tuple(command), 0, "", "")

    monkeypatch.setattr(worker, "_run_stage_command", _capture_stage_command)

    job_id = uuid4()
    prepared = PreparedTaskWorkspace(
        job_root=tmp_path / str(job_id),
        repo_dir=tmp_path / "repo",
        artifacts_dir=tmp_path / "artifacts",
        prepare_log_path=tmp_path / "prepare.log",
        execute_log_path=tmp_path / "execute.log",
        publish_log_path=tmp_path / "publish.log",
        task_context_path=tmp_path / "context",
        publish_result_path=tmp_path / "publish-result.json",
        default_branch="main",
        starting_branch="main",
        new_branch="feature/branch",
        working_branch="feature/branch",
        workdir_mode="checkout",
        repo_command_env=None,
        publish_command_env=None,
    )
    prepared.repo_dir.mkdir(parents=True, exist_ok=True)
    prepared.task_context_path.mkdir(parents=True, exist_ok=True)
    prepared.execute_log_path.write_text(
        "[command] $ ./tools/test_unit.sh\nok\n",
        encoding="utf-8",
    )

    commit_override = "  commit title with preserved spaces  "
    pr_title_override = "  PR title with preserved spaces  "
    pr_body_override = "  PR body first line\nsecond line preserved  "
    canonical_payload = {
        "task": {
            "instructions": "unused",
            "runtime": {"mode": "gemini"},
            "publish": {
                "mode": "pr",
                "commitMessage": commit_override,
                "prTitle": pr_title_override,
                "prBody": pr_body_override,
            },
        }
    }

    staged_artifacts: list[ArtifactUpload] = []
    publish_note = await worker._run_publish_stage(
        job_id=job_id,
        canonical_payload=canonical_payload,
        prepared=prepared,
        skill_meta={},
        job_type="task",
        staged_artifacts=staged_artifacts,
    )

    assert publish_note is not None
    commit_call = next(
        call for call in run_calls if call["command"][:3] == ("git", "commit", "-m")
    )
    assert commit_call["command"][3] == commit_override
    assert commit_call["redaction_values"] == (commit_override,)

    pr_call = next(
        call for call in run_calls if call["command"][:3] == ("gh", "pr", "create")
    )
    pr_command = pr_call["command"]
    assert pr_command[pr_command.index("--title") + 1] == pr_title_override
    assert pr_command[pr_command.index("--body") + 1] == pr_body_override
    assert pr_call["redaction_values"] == (pr_title_override, pr_body_override)
    assert any(
        artifact.name == "reports/publish_preflight.json"
        for artifact in staged_artifacts
    )


async def test_run_publish_stage_fails_without_verification_evidence_for_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source-code changes should block publish without verification evidence/skip reason."""

    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=queue,
        codex_exec_handler=handler,
    )  # type: ignore[arg-type]

    async def _capture_stage_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
    ):
        _ = (cwd, log_path, check, env, redaction_values, cancel_event)
        if tuple(command[:2]) == ("git", "status"):
            return CommandResult(
                tuple(command),
                0,
                " M moonmind/agents/codex_worker/worker.py\n",
                "",
            )
        return CommandResult(tuple(command), 0, "", "")

    monkeypatch.setattr(worker, "_run_stage_command", _capture_stage_command)

    job_id = uuid4()
    prepared = PreparedTaskWorkspace(
        job_root=tmp_path / str(job_id),
        repo_dir=tmp_path / "repo",
        artifacts_dir=tmp_path / "artifacts",
        prepare_log_path=tmp_path / "prepare.log",
        execute_log_path=tmp_path / "execute.log",
        publish_log_path=tmp_path / "publish.log",
        task_context_path=tmp_path / "context",
        publish_result_path=tmp_path / "publish-result.json",
        default_branch="main",
        starting_branch="main",
        new_branch="feature/branch",
        working_branch="feature/branch",
        workdir_mode="checkout",
        repo_command_env=None,
        publish_command_env=None,
    )
    prepared.repo_dir.mkdir(parents=True, exist_ok=True)
    prepared.task_context_path.mkdir(parents=True, exist_ok=True)

    staged_artifacts: list[ArtifactUpload] = []
    with pytest.raises(RuntimeError, match="publish preflight failed"):
        await worker._run_publish_stage(
            job_id=job_id,
            canonical_payload={"task": {"publish": {"mode": "branch"}}},
            prepared=prepared,
            skill_meta={},
            job_type="task",
            staged_artifacts=staged_artifacts,
        )

    preflight_path = prepared.artifacts_dir / "reports" / "publish_preflight.json"
    assert preflight_path.exists()
    preflight_payload = json.loads(preflight_path.read_text(encoding="utf-8"))
    assert preflight_payload["status"] == "FAIL"
    assert preflight_payload["verification"]["required"] is True
    assert preflight_payload["verification"]["evidenceCount"] == 0
    assert preflight_payload["verification"]["skipReason"] is None
    assert any(
        artifact.name == "reports/publish_preflight.json"
        for artifact in staged_artifacts
    )
    assert any(artifact.name == "logs/publish.log" for artifact in staged_artifacts)
    assert any(artifact.name == "publish_result.json" for artifact in staged_artifacts)
    publish_payload = json.loads(
        prepared.publish_result_path.read_text(encoding="utf-8")
    )
    assert publish_payload["verification"]["status"] == "failed"
    assert (
        "publish preflight failed: source-code changes detected"
        in publish_payload["reason"]
    )


async def test_run_publish_stage_auto_runs_default_test_script_when_evidence_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish should run `./tools/test_unit.sh` when source changes have no evidence."""

    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=queue,
        codex_exec_handler=handler,
    )  # type: ignore[arg-type]

    run_calls: list[tuple[str, ...]] = []
    real_run_stage_command = worker._run_stage_command

    async def _capture_stage_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
    ):
        run_calls.append(tuple(command))
        if tuple(command[:2]) == ("git", "status"):
            return CommandResult(
                tuple(command),
                0,
                " M moonmind/agents/codex_worker/worker.py\n",
                "",
            )
        return await real_run_stage_command(
            command,
            cwd=cwd,
            log_path=log_path,
            check=check,
            env=env,
            redaction_values=redaction_values,
            cancel_event=cancel_event,
        )

    monkeypatch.setattr(worker, "_run_stage_command", _capture_stage_command)

    job_id = uuid4()
    prepared = PreparedTaskWorkspace(
        job_root=tmp_path / str(job_id),
        repo_dir=tmp_path / "repo",
        artifacts_dir=tmp_path / "artifacts",
        prepare_log_path=tmp_path / "prepare.log",
        execute_log_path=tmp_path / "execute.log",
        publish_log_path=tmp_path / "publish.log",
        task_context_path=tmp_path / "context",
        publish_result_path=tmp_path / "publish-result.json",
        default_branch="main",
        starting_branch="main",
        new_branch="feature/branch",
        working_branch="feature/branch",
        workdir_mode="checkout",
        repo_command_env=None,
        publish_command_env=None,
    )
    prepared.repo_dir.mkdir(parents=True, exist_ok=True)
    prepared.task_context_path.mkdir(parents=True, exist_ok=True)
    script_path = prepared.repo_dir / "tools" / "test_unit.sh"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    script_path.chmod(0o755)

    staged_artifacts: list[ArtifactUpload] = []
    publish_note = await worker._run_publish_stage(
        job_id=job_id,
        canonical_payload={"task": {"publish": {"mode": "branch"}}},
        prepared=prepared,
        skill_meta={},
        job_type="task",
        staged_artifacts=staged_artifacts,
    )

    assert publish_note == "published branch feature/branch"
    assert ("./tools/test_unit.sh",) in run_calls
    publish_payload = json.loads(
        prepared.publish_result_path.read_text(encoding="utf-8")
    )
    assert publish_payload["verification"]["status"] == "passed"
    assert publish_payload["verification"]["evidenceCount"] >= 1
    assert any(
        entry["command"] == "./tools/test_unit.sh"
        for entry in publish_payload["verification"]["evidence"]
    )


async def test_run_publish_stage_no_local_changes_does_not_reference_preflight_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-change publish path should report verification as not run without a preflight artifact."""

    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=queue,
        codex_exec_handler=handler,
    )  # type: ignore[arg-type]

    async def _capture_stage_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
    ):
        _ = (cwd, log_path, check, env, redaction_values, cancel_event)
        if tuple(command[:2]) == ("git", "status"):
            return CommandResult(tuple(command), 0, "", "")
        return CommandResult(tuple(command), 0, "", "")

    monkeypatch.setattr(worker, "_run_stage_command", _capture_stage_command)

    job_id = uuid4()
    prepared = PreparedTaskWorkspace(
        job_root=tmp_path / str(job_id),
        repo_dir=tmp_path / "repo",
        artifacts_dir=tmp_path / "artifacts",
        prepare_log_path=tmp_path / "prepare.log",
        execute_log_path=tmp_path / "execute.log",
        publish_log_path=tmp_path / "publish.log",
        task_context_path=tmp_path / "context",
        publish_result_path=tmp_path / "publish-result.json",
        default_branch="main",
        starting_branch="main",
        new_branch="feature/branch",
        working_branch="feature/branch",
        workdir_mode="checkout",
        repo_command_env=None,
        publish_command_env=None,
    )
    prepared.repo_dir.mkdir(parents=True, exist_ok=True)
    prepared.task_context_path.mkdir(parents=True, exist_ok=True)

    staged_artifacts: list[ArtifactUpload] = []
    publish_note = await worker._run_publish_stage(
        job_id=job_id,
        canonical_payload={"task": {"publish": {"mode": "branch"}}},
        prepared=prepared,
        skill_meta={},
        job_type="task",
        staged_artifacts=staged_artifacts,
    )

    assert publish_note == "publish skipped: no local changes"
    publish_payload = json.loads(
        prepared.publish_result_path.read_text(encoding="utf-8")
    )
    assert publish_payload["verification"]["status"] == "not_required"
    assert "evidenceArtifact" not in publish_payload["verification"]
    assert any(artifact.name == "logs/publish.log" for artifact in staged_artifacts)


async def test_run_publish_stage_fails_for_renamed_source_change_without_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renamed source paths should still be checked for verification evidence."""

    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=queue,
        codex_exec_handler=handler,
    )  # type: ignore[arg-type]

    async def _capture_stage_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
    ):
        _ = (cwd, log_path, check, env, redaction_values, cancel_event)
        if tuple(command[:2]) == ("git", "status"):
            return CommandResult(
                tuple(command),
                0,
                "R  moonmind/agents/codex_worker/worker.py -> docs/README.md\n",
                "",
            )
        return CommandResult(tuple(command), 0, "", "")

    monkeypatch.setattr(worker, "_run_stage_command", _capture_stage_command)

    job_id = uuid4()
    prepared = PreparedTaskWorkspace(
        job_root=tmp_path / str(job_id),
        repo_dir=tmp_path / "repo",
        artifacts_dir=tmp_path / "artifacts",
        prepare_log_path=tmp_path / "prepare.log",
        execute_log_path=tmp_path / "execute.log",
        publish_log_path=tmp_path / "publish.log",
        task_context_path=tmp_path / "context",
        publish_result_path=tmp_path / "publish-result.json",
        default_branch="main",
        starting_branch="main",
        new_branch="feature/branch",
        working_branch="feature/branch",
        workdir_mode="checkout",
        repo_command_env=None,
        publish_command_env=None,
    )
    prepared.repo_dir.mkdir(parents=True, exist_ok=True)
    prepared.task_context_path.mkdir(parents=True, exist_ok=True)

    staged_artifacts: list[ArtifactUpload] = []
    with pytest.raises(RuntimeError, match="publish preflight failed"):
        await worker._run_publish_stage(
            job_id=job_id,
            canonical_payload={"task": {"publish": {"mode": "branch"}}},
            prepared=prepared,
            skill_meta={},
            job_type="task",
            staged_artifacts=staged_artifacts,
        )


async def test_run_publish_stage_allows_structured_verification_skip_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structured skip reason should allow publish and propagate into PR body/artifacts."""

    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=queue,
        codex_exec_handler=handler,
    )  # type: ignore[arg-type]

    run_calls: list[tuple[str, ...]] = []

    async def _capture_stage_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
    ):
        _ = (cwd, log_path, check, env, redaction_values, cancel_event)
        command_tuple = tuple(str(part) for part in command)
        run_calls.append(command_tuple)
        if command_tuple[:2] == ("git", "status"):
            return CommandResult(
                command_tuple,
                0,
                " M moonmind/agents/codex_worker/worker.py\n",
                "",
            )
        if command_tuple[:3] == ("gh", "pr", "create"):
            return CommandResult(
                command_tuple,
                0,
                "https://github.com/MoonLadderStudios/MoonMind/pull/777\n",
                "",
            )
        return CommandResult(command_tuple, 0, "", "")

    monkeypatch.setattr(worker, "_run_stage_command", _capture_stage_command)

    job_id = uuid4()
    prepared = PreparedTaskWorkspace(
        job_root=tmp_path / str(job_id),
        repo_dir=tmp_path / "repo",
        artifacts_dir=tmp_path / "artifacts",
        prepare_log_path=tmp_path / "prepare.log",
        execute_log_path=tmp_path / "execute.log",
        publish_log_path=tmp_path / "publish.log",
        task_context_path=tmp_path / "context",
        publish_result_path=tmp_path / "publish-result.json",
        default_branch="main",
        starting_branch="main",
        new_branch="feature/branch",
        working_branch="feature/branch",
        workdir_mode="checkout",
        repo_command_env=None,
        publish_command_env=None,
    )
    prepared.repo_dir.mkdir(parents=True, exist_ok=True)
    prepared.task_context_path.mkdir(parents=True, exist_ok=True)

    staged_artifacts: list[ArtifactUpload] = []
    publish_note = await worker._run_publish_stage(
        job_id=job_id,
        canonical_payload={
            "task": {
                "runtime": {"mode": "codex"},
                "publish": {
                    "mode": "pr",
                    "verificationSkipReason": {
                        "category": "infra_unavailable",
                        "reason": "CI runner was unavailable during this queue window.",
                        "ticket": "OPS-1234",
                    },
                },
            }
        },
        prepared=prepared,
        skill_meta={},
        job_type="task",
        staged_artifacts=staged_artifacts,
    )

    assert publish_note is not None
    assert "verification skipped (infra_unavailable)" in publish_note
    pr_call = next(call for call in run_calls if call[:3] == ("gh", "pr", "create"))
    pr_body = pr_call[pr_call.index("--body") + 1]
    assert "## Verification" in pr_body
    assert "Category: infra_unavailable" in pr_body
    assert "Reason: CI runner was unavailable during this queue window." in pr_body
    assert "Ticket: OPS-1234" in pr_body

    publish_payload = json.loads(
        prepared.publish_result_path.read_text(encoding="utf-8")
    )
    assert publish_payload["verification"]["status"] == "skipped"
    assert (
        publish_payload["verification"]["skipReason"]["category"] == "infra_unavailable"
    )
    assert any(
        artifact.name == "reports/publish_preflight.json"
        for artifact in staged_artifacts
    )


async def test_run_stage_command_fallback_masks_sensitive_command_arguments(
    tmp_path: Path,
) -> None:
    """Fallback command logging should mask publish text arguments."""

    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=queue,
        codex_exec_handler=handler,
    )  # type: ignore[arg-type]
    worker._codex_exec_handler = object()  # type: ignore[assignment]

    log_path = tmp_path / "publish.log"
    with pytest.raises(RuntimeError, match="missing command runner"):
        await worker._run_stage_command(
            [
                "gh",
                "pr",
                "create",
                "--title",
                "very sensitive title",
                "--body",
                "token=top-secret",
            ],
            cwd=tmp_path,
            log_path=log_path,
        )
    with pytest.raises(RuntimeError, match="missing command runner"):
        await worker._run_stage_command(
            ["git", "commit", "-m", "secret commit body"],
            cwd=tmp_path,
            log_path=log_path,
        )

    log_content = log_path.read_text(encoding="utf-8")
    assert "very sensitive title" not in log_content
    assert "token=top-secret" not in log_content
    assert "secret commit body" not in log_content
    assert "[REDACTED]" in log_content


async def test_run_stage_command_records_structured_verification_report(
    tmp_path: Path,
) -> None:
    """Verification-like commands should be recorded in structured report artifacts."""

    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=queue,
        codex_exec_handler=handler,
    )  # type: ignore[arg-type]

    artifacts_dir = tmp_path / "artifacts"
    log_path = artifacts_dir / "logs" / "publish.log"
    await worker._run_stage_command(
        ["./tools/test_unit.sh"],
        cwd=tmp_path,
        log_path=log_path,
    )

    report_path = artifacts_dir / "reports" / "verification_commands.jsonl"
    assert report_path.exists()
    records = [
        json.loads(line)
        for line in report_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1
    assert records[0]["command"] == "./tools/test_unit.sh"
    assert records[0]["status"] == "passed"
    assert records[0]["returncode"] == 0
    assert records[0]["logArtifact"] == "logs/publish.log"


async def test_run_stage_command_enforces_timeout(tmp_path: Path, monkeypatch) -> None:
    """Stage command wrapper should fail fast when a command runner hangs."""

    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
            stage_command_timeout_seconds=2,
        ),
        queue_client=queue,
        codex_exec_handler=handler,
    )  # type: ignore[arg-type]

    async def _slow_run_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        output_chunk_callback=None,
    ):
        _ = (
            command,
            cwd,
            log_path,
            check,
            env,
            redaction_values,
            cancel_event,
            output_chunk_callback,
        )
        await asyncio.Event().wait()
        return CommandResult(("git", "status"), 0, "", "")

    monkeypatch.setattr(handler, "_run_command", _slow_run_command)

    log_path = tmp_path / "execute.log"
    with pytest.raises(asyncio.TimeoutError, match=r"command timed out after 0.01s"):
        await worker._run_stage_command(
            ["git", "status"],
            cwd=tmp_path,
            log_path=log_path,
            timeout_seconds=0.01,
        )

    log_content = log_path.read_text(encoding="utf-8")
    assert "command timed out after 0.01s: git status" in log_content


async def test_resolve_pr_base_branch_prefers_publish_override() -> None:
    """PR base should use override when present, otherwise starting branch."""

    assert (
        CodexWorker._resolve_pr_base_branch(
            publish={"prBaseBranch": "release/1.2"},
            starting_branch="main",
        )
        == "release/1.2"
    )
    assert (
        CodexWorker._resolve_pr_base_branch(
            publish={"prBaseBranch": " "},
            starting_branch="develop",
        )
        == "develop"
    )


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


async def test_resolve_task_auth_context_includes_git_identity_without_token(
    tmp_path: Path, monkeypatch
) -> None:
    """Git identity settings should be applied even when GitHub token is absent."""

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-1",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=120,
        workdir=tmp_path,
        git_user_name="Nate Sticco",
        git_user_email="nsticco@gmail.com",
    )
    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    auth_context = await worker._resolve_task_auth_context(
        canonical_payload={},
        publish_mode="pr",
    )

    assert auth_context.repo_auth_source == "none"
    assert auth_context.publish_auth_source == "none"
    assert auth_context.repo_command_env is not None
    assert auth_context.publish_command_env is not None
    assert auth_context.repo_command_env["GIT_AUTHOR_NAME"] == "Nate Sticco"
    assert auth_context.repo_command_env["GIT_COMMITTER_NAME"] == "Nate Sticco"
    assert auth_context.repo_command_env["GIT_AUTHOR_EMAIL"] == "nsticco@gmail.com"
    assert auth_context.repo_command_env["GIT_COMMITTER_EMAIL"] == "nsticco@gmail.com"


async def test_build_command_env_uses_minimal_inherited_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/tmp/home")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("SECRET_TOKEN", "should-not-leak")

    command_env = CodexWorker._build_command_env(
        "ghp-example",
        git_user_name="Nate Sticco",
        git_user_email="nsticco@gmail.com",
    )

    assert command_env is not None
    assert command_env["PATH"] == "/usr/bin"
    assert command_env["HOME"] == "/tmp/home"
    assert command_env["LANG"] == "C.UTF-8"
    assert command_env["GITHUB_TOKEN"] == "ghp-example"
    assert command_env["GH_TOKEN"] == "ghp-example"
    assert command_env["GIT_AUTHOR_NAME"] == "Nate Sticco"
    assert command_env["GIT_AUTHOR_EMAIL"] == "nsticco@gmail.com"
    assert "SECRET_TOKEN" not in command_env


async def test_run_once_claims_with_configured_policy_fields(tmp_path: Path) -> None:
    """Claim request should forward local policy hints without adding repo overrides."""

    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-9",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=75,
        workdir=tmp_path,
        allowed_types=("task", "codex_exec", "codex_skill"),
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
    assert claim["allowed_types"] == ("task", "codex_exec", "codex_skill")
    assert claim["worker_capabilities"] == ("codex", "git")


async def test_run_once_fails_auth_ref_when_vault_not_configured(
    tmp_path: Path,
) -> None:
    """Auth refs should fail closed when Vault resolver config is absent."""

    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "auth": {
                "repoAuthRef": "vault://kv/moonmind/repos/MoonLadderStudios/MoonMind#github_token",
                "publishAuthRef": None,
            },
            "task": {
                "instructions": "run",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
            },
        },
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
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert len(queue.failed) == 1
    assert "Vault resolver is not configured" in queue.failed[0]
    assert handler.calls == []


async def test_run_once_fails_legacy_job_when_feature_flag_disabled(
    tmp_path: Path,
) -> None:
    """Workers with legacy compatibility disabled should reject legacy jobs."""

    job = ClaimedJob(
        id=uuid4(),
        type="codex_exec",
        payload={"repository": "a/b", "instruction": "run"},
    )
    queue = FakeQueueClient(jobs=[job])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    config = CodexWorkerConfig(
        moonmind_url="http://localhost:5000",
        worker_id="worker-9",
        worker_token=None,
        poll_interval_ms=1500,
        lease_seconds=75,
        workdir=tmp_path,
        allowed_types=("task",),
        legacy_job_types_enabled=False,
        worker_capabilities=("codex", "git"),
    )
    worker = CodexWorker(config=config, queue_client=queue, codex_exec_handler=handler)  # type: ignore[arg-type]

    processed = await worker.run_once()

    assert processed is True
    assert len(queue.failed) == 1
    assert "legacy job type disabled" in queue.failed[0]
    assert handler.calls == []
