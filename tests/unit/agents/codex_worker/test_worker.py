"""Unit tests for codex worker daemon loop and queue client behavior."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from moonmind.agents.codex_worker.handlers import (
    ArtifactUpload,
    CommandCancelledError,
    CommandResult,
    WorkerExecutionResult,
)
from moonmind.agents.codex_worker.worker import (
    ClaimedJob,
    CodexWorker,
    CodexWorkerConfig,
    PreparedTaskWorkspace,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


class FakeQueueClient:
    """In-memory queue client stub for worker tests."""

    def __init__(self, jobs: list[ClaimedJob | None] | None = None) -> None:
        self.jobs = list(jobs or [])
        self.claim_calls: list[dict[str, object]] = []
        self.heartbeats: list[str] = []
        self.heartbeat_payloads: list[dict[str, object]] = []
        self.live_session_reports: list[dict[str, object]] = []
        self.live_session_heartbeats: list[str] = []
        self.live_session_state: dict[str, object] | None = None
        self.completed: list[tuple[str, str | None]] = []
        self.failed: list[str] = []
        self.cancel_acks: list[tuple[str, str | None]] = []
        self.uploaded: list[str] = []
        self.events: list[dict[str, object]] = []
        self.cancel_requested_at: str | None = None
        self.submitted_proposals: list[dict[str, object]] = []

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
        payload = {"id": str(job_id)}
        if self.cancel_requested_at:
            payload["cancelRequestedAt"] = self.cancel_requested_at
        self.heartbeat_payloads.append(payload)
        return payload

    async def ack_cancel(self, *, job_id, worker_id, message=None):
        self.cancel_acks.append((str(job_id), message))
        return {"id": str(job_id), "status": "cancelled"}

    async def complete_job(self, *, job_id, worker_id, result_summary):
        self.completed.append((str(job_id), result_summary))

    async def fail_job(self, *, job_id, worker_id, error_message, retryable=False):
        self.failed.append(f"{job_id}:{error_message}")

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


class FailingUploadQueueClient(FakeQueueClient):
    """Queue client stub that simulates artifact upload failures."""

    async def upload_artifact(self, *, job_id, worker_id, artifact):
        raise RuntimeError("artifact upload failed")


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

    job = ClaimedJob(id=uuid4(), type="task", payload={})
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
    assert started[0]["payload"]["stepId"] == "inspect"
    assert started[1]["payload"]["stepId"] == "patch"


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
    ):
        recorded_commands.append(tuple(str(item) for item in command))
        if len(command) >= 2 and command[0] == "docker" and command[1] == "run":
            await asyncio.sleep(1.2)
            return CommandResult(tuple(command), 0, "", "")
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
    ):
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
    cancel_event = asyncio.Event()
    task = asyncio.create_task(
        worker._heartbeat_loop(
            job_id=uuid4(),
            stop_event=stop_event,
            cancel_event=cancel_event,
        )
    )
    await asyncio.sleep(2.3)
    stop_event.set()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert len(queue.heartbeats) >= 2


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
    ):
        _ = (cwd, log_path, check, env, redaction_values, cancel_event)
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
    ):
        _ = (cwd, log_path, check, env, redaction_values, cancel_event)
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
    assert any(
        event["message"] == "Job cancellation requested; stopping"
        for event in queue.events
    )


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
    assert config.git_user_name == "Nate Sticco"
    assert config.git_user_email == "nsticco@gmail.com"


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

    with pytest.raises(ValueError, match="MOONMIND_SKILL_POLICY_MODE must be one of"):
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
