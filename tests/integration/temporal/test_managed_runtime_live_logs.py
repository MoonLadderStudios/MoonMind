"""Integration tests for managed-runtime log streaming.

Verifies RuntimeLogStreamer and ManagedRunSupervisor capture piped stdout/stderr
to artifact storage. Uses short-lived real subprocesses where noted.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.task_runs import router as task_runs_router
from api_service.auth_providers import get_current_user
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    ManagedRunRecord,
    ManagedRuntimeProfile,
)
from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.supervisor import ManagedRunSupervisor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubArtifactStorage:
    """Minimal file-based artifact storage for integration tests."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write_artifact(self, *, job_id, artifact_name, data):
        target_dir = self._root / job_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / artifact_name
        target.write_bytes(data)
        return target, f"{job_id}/{artifact_name}"

    def resolve_storage_path(self, ref):
        return self._root / ref


# ---------------------------------------------------------------------------
# Test 1 — stream_to_artifact captures stdout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_to_artifact_captures_stdout(tmp_path: Path):
    """RuntimeLogStreamer.stream_to_artifact writes known output to storage."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    storage = _StubArtifactStorage(artifact_root)
    streamer = RuntimeLogStreamer(storage)

    expected_lines = [
        b"[2026-03-25T12:00:00Z] Agent starting...\n",
        b"[2026-03-25T12:00:01Z] Processing task abc-123\n",
        b"[2026-03-25T12:00:05Z] Task completed successfully\n",
    ]
    expected_output = b"".join(expected_lines)

    reader = asyncio.StreamReader()
    for line in expected_lines:
        reader.feed_data(line)
    reader.feed_eof()

    ref, content, events = await streamer.stream_to_artifact(
        reader,
        run_id="managed-logs-1",
        stream_name="stdout",
    )

    # Verify artifact was written.
    assert ref == "managed-logs-1/stdout.log"
    artifact_path = artifact_root / "managed-logs-1" / "stdout.log"
    assert artifact_path.exists()
    assert artifact_path.read_bytes() == expected_output

    # Verify decoded content matches.
    assert "Agent starting" in content
    assert "Task completed successfully" in content


# ---------------------------------------------------------------------------
# Test 2 — supervisor streams managed logs to artifacts (full lifecycle)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_streams_managed_logs_to_artifacts(tmp_path: Path):
    """Full launcher→supervisor lifecycle: supervise to completion, verify logs."""
    store_root = tmp_path / "store"
    artifact_root = tmp_path / "artifacts"
    store_root.mkdir()
    artifact_root.mkdir()

    store = ManagedRunStore(store_root)
    storage = _StubArtifactStorage(artifact_root)
    streamer = RuntimeLogStreamer(storage)
    supervisor = ManagedRunSupervisor(store, streamer)

    # Create the run record.
    record = ManagedRunRecord(
        run_id="managed-stream-1",
        agent_id="agent-logs",
        runtime_id="codex-cli",
        status="launching",
        pid=22222,
        started_at=datetime.now(tz=UTC),
    )
    store.save(record)

    # Use a real short-lived process that produces known output.
    process = await asyncio.create_subprocess_exec(
        "echo", "live-log-content-from-agent",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    result = await supervisor.supervise(
        run_id="managed-stream-1",
        process=process,
        timeout_seconds=30,
    )

    # Verify supervision completed successfully.
    assert result.status == "completed"

    # Verify stdout artifact was captured with expected content.
    stdout_artifact = artifact_root / "managed-stream-1" / "stdout.log"
    assert stdout_artifact.exists()
    assert "live-log-content-from-agent" in stdout_artifact.read_text()

    # Verify diagnostics artifact was created.
    diag = artifact_root / "managed-stream-1" / "diagnostics.json"
    assert diag.exists()


# ---------------------------------------------------------------------------
# Test 3 — exit code capture during streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_code_capture_during_streaming(tmp_path: Path):
    """Supervisor reads authoritative exit code from exit_code_path when set."""
    store_root = tmp_path / "store"
    artifact_root = tmp_path / "artifacts"
    store_root.mkdir()
    artifact_root.mkdir()

    store = ManagedRunStore(store_root)
    storage = _StubArtifactStorage(artifact_root)
    streamer = RuntimeLogStreamer(storage)
    supervisor = ManagedRunSupervisor(store, streamer)

    run_id = "managed-exit-1"

    record = ManagedRunRecord(
        run_id=run_id,
        agent_id="agent-logs",
        runtime_id="codex-cli",
        status="launching",
        pid=33333,
        started_at=datetime.now(tz=UTC),
    )
    store.save(record)

    exit_code_file = tmp_path / "exit_code.txt"
    exit_code_file.write_text("42\n")

    # Process that exits quickly (rc=0 from the wrapper, actual rc in the file).
    process = await asyncio.create_subprocess_exec(
        "echo", "exiting-with-code-42",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    result = await supervisor.supervise(
        run_id=run_id,
        process=process,
        timeout_seconds=30,
        exit_code_path=str(exit_code_file),
    )

    # The supervisor should read exit code 42 from the file (not 0 from process).
    assert result.exit_code == 42
    # Non-zero exit means the run classified as failed.
    assert result.status == "failed"


# ---------------------------------------------------------------------------
# Test 4 — concurrent stdout/stderr streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_stdout_stderr_streaming(tmp_path: Path):
    """Launch a process that writes to both stdout and stderr.

    Verify stream_and_parse captures both streams independently without
    interleaving or data loss.
    """
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    storage = _StubArtifactStorage(artifact_root)
    streamer = RuntimeLogStreamer(storage)

    stdout_content = b"stdout line 1\nstdout line 2\nstdout line 3\n"
    stderr_content = b"stderr warning 1\nstderr warning 2\n"

    stdout_reader = asyncio.StreamReader()
    stdout_reader.feed_data(stdout_content)
    stdout_reader.feed_eof()

    stderr_reader = asyncio.StreamReader()
    stderr_reader.feed_data(stderr_content)
    stderr_reader.feed_eof()

    log_refs, stdout_text, stderr_text, parsed, _events = await streamer.stream_and_parse(
        stdout_reader,
        stderr_reader,
        run_id="managed-concurrent-1",
    )

    # Both refs should be populated.
    assert "stdout" in log_refs
    assert "stderr" in log_refs

    # Verify content integrity — no interleaving.
    assert "stdout line 1" in stdout_text
    assert "stdout line 2" in stdout_text
    assert "stdout line 3" in stdout_text
    assert "stderr warning 1" in stderr_text
    assert "stderr warning 2" in stderr_text

    # Verify artifacts on disk.
    stdout_artifact = artifact_root / "managed-concurrent-1" / "stdout.log"
    stderr_artifact = artifact_root / "managed-concurrent-1" / "stderr.log"
    assert stdout_artifact.exists()
    assert stderr_artifact.exists()
    assert stdout_artifact.read_bytes() == stdout_content
    assert stderr_artifact.read_bytes() == stderr_content


# ---------------------------------------------------------------------------
# Test 5 — supervisor streams concurrently (regression: deadlock prevention)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_streams_concurrently_with_process(tmp_path: Path):
    """Supervisor does not deadlock when the process produces large output.

    This is the critical regression test for the sequential-streaming bug.
    Before the fix, stream_and_parse was called AFTER _heartbeat_and_wait,
    meaning the process had to fully exit before any data was consumed.
    For output larger than the OS pipe buffer (~64KB), the subprocess
    write-end would block indefinitely — a deadlock.

    The fix runs streaming concurrently with heartbeating via asyncio.gather(),
    ensuring pipe buffers are drained in real-time.  This test produces ~256KB
    of output and must complete successfully within a reasonable timeout.
    """
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    storage = _StubArtifactStorage(artifact_root)
    store_root = tmp_path / "store"
    store_root.mkdir()
    store = ManagedRunStore(store_root)
    streamer = RuntimeLogStreamer(storage)
    supervisor = ManagedRunSupervisor(store, streamer)

    run_id = "integ-large-concurrent"
    record = ManagedRunRecord(
        run_id=run_id,
        agent_id="test-agent",
        runtime_id="gemini_cli",
        status="launching",
        pid=0,
        started_at=datetime.now(tz=UTC),
    )
    store.save(record)

    # Produce ~256KB — well above the typical 64KB OS pipe buffer.
    # A sequential-streaming supervisor would deadlock here.
    big_output_cmd = "print('x' * 256_000)"
    process = await asyncio.create_subprocess_exec(
        "python3", "-c", big_output_cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # If this hangs for more than 15 seconds it means the deadlock regressed.
    result = await asyncio.wait_for(
        supervisor.supervise(
            run_id=run_id,
            process=process,
            timeout_seconds=60,
        ),
        timeout=15,
    )

    assert result.status == "completed", f"Expected completed, got {result.status}"
    stdout_artifact = artifact_root / run_id / "stdout.log"
    assert stdout_artifact.exists(), "stdout.log artifact was not written"
    assert len(stdout_artifact.read_bytes()) >= 256_000


# ---------------------------------------------------------------------------
# Test 6 — supervisor handles None stdout/stderr gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_with_none_stdout_stderr_completes(tmp_path: Path):
    """Supervisor completes when stdout/stderr are not piped (None streams)."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    storage = _StubArtifactStorage(artifact_root)
    store_root = tmp_path / "store"
    store_root.mkdir()
    store = ManagedRunStore(store_root)
    streamer = RuntimeLogStreamer(storage)
    supervisor = ManagedRunSupervisor(store, streamer)

    run_id = "integ-none-streams"
    record = ManagedRunRecord(
        run_id=run_id,
        agent_id="test-agent",
        runtime_id="gemini_cli",
        status="launching",
        pid=0,
        started_at=datetime.now(tz=UTC),
    )
    store.save(record)

    # Spawn 'true' with no PIPE — stdout/stderr will be None
    process = await asyncio.create_subprocess_exec(
        "true",
        stdin=asyncio.subprocess.DEVNULL,
        # stdout/stderr intentionally NOT piped
    )
    assert process.stdout is None
    assert process.stderr is None

    result = await supervisor.supervise(
        run_id=run_id,
        process=process,
        timeout_seconds=10,
    )

    assert result.status == "completed"
    diag_artifact = artifact_root / run_id / "diagnostics.json"
    assert diag_artifact.exists(), "diagnostics.json was not written"


@pytest.mark.asyncio
async def test_long_running_launch_is_visible_through_observability_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    agent_jobs_root = tmp_path / "agent_jobs"
    store_root = agent_jobs_root / "managed_runs"
    artifact_root = agent_jobs_root / "artifacts"
    workspace = tmp_path / "workspace"
    store_root.mkdir(parents=True)
    artifact_root.mkdir(parents=True)
    workspace.mkdir()

    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(agent_jobs_root))
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_ARTIFACTS", str(agent_jobs_root))

    store = ManagedRunStore(store_root)
    storage = _StubArtifactStorage(artifact_root)
    streamer = RuntimeLogStreamer(storage)
    supervisor = ManagedRunSupervisor(store, streamer)
    launcher = ManagedRuntimeLauncher(store)

    run_id = str(uuid4())
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="test-agent",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        workspaceSpec={},
        parameters={},
    )
    profile = ManagedRuntimeProfile(
        runtimeId="test_runtime",
        profileId="profile-1",
        commandTemplate=[
            "python3",
            "-c",
            (
                "import sys,time; "
                "print('alpha', flush=True); "
                "time.sleep(0.4); "
                "print('beta', flush=True); "
                "time.sleep(0.4); "
                "print('omega', flush=True)"
            ),
        ],
        workspaceMode="shared",
    )

    record, process, cleanup_paths, deferred_cleanup_paths = await launcher.launch(
        run_id=run_id,
        workflow_id="mm:wf-live-1",
        request=request,
        profile=profile,
        workspace_path=workspace,
    )
    assert process is not None
    assert record.workflow_id == "mm:wf-live-1"

    supervise_task = asyncio.create_task(
        supervisor.supervise(
            run_id=run_id,
            process=process,
            timeout_seconds=30,
            cleanup_paths=cleanup_paths,
            deferred_cleanup_paths=deferred_cleanup_paths,
        )
    )

    app = FastAPI()
    app.include_router(task_runs_router, prefix="/api")
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=uuid4(),
        email="admin@example.com",
        is_superuser=True,
    )

    try:
        spool_path = workspace / "live_streams.spool"
        timeout_seconds = 15.0
        poll_interval = 0.1
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        last_contents: str | None = None
        while True:
            if spool_path.exists():
                last_contents = spool_path.read_text(encoding="utf-8")
                if "alpha" in last_contents:
                    break
            if loop.time() >= deadline:
                if spool_path.exists():
                    size = spool_path.stat().st_size
                    snippet = (last_contents or "")[-500:]
                    raise AssertionError(
                        f"Timed out waiting for 'alpha' in spool file {spool_path} "
                        f"after {timeout_seconds:.1f}s. File exists with size {size} bytes. "
                        f"Last 500 bytes of contents:\n{snippet}"
                    )
                raise AssertionError(
                    f"Timed out waiting for spool file {spool_path} to appear "
                    f"after {timeout_seconds:.1f}s; file does not exist."
                )
            await asyncio.sleep(poll_interval)

        with TestClient(app) as client:
            summary = client.get(f"/api/task-runs/{run_id}/observability-summary")
            assert summary.status_code == 200
            summary_body = summary.json()["summary"]
            assert summary_body["supportsLiveStreaming"] is True
            assert summary_body["liveStreamStatus"] == "available"

            merged = client.get(f"/api/task-runs/{run_id}/logs/merged")
            assert merged.status_code == 200
            assert merged.headers["x-merged-order-source"] == "spool"
            assert "alpha" in merged.text

        final = await asyncio.wait_for(supervise_task, timeout=10)
        assert final.status == "completed"
        assert store.find_latest_for_workflow("mm:wf-live-1") is not None

        with TestClient(app) as client:
            stdout_response = client.get(f"/api/task-runs/{run_id}/logs/stdout")
            assert stdout_response.status_code == 200
            assert "alpha" in stdout_response.text
            assert "omega" in stdout_response.text

            diagnostics_response = client.get(f"/api/task-runs/{run_id}/diagnostics")
            assert diagnostics_response.status_code == 200
            assert '"stdout"' in diagnostics_response.text
    finally:
        app.dependency_overrides.clear()
