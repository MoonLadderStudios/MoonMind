"""Integration tests for managed-runtime log streaming.

Verifies RuntimeLogStreamer and ManagedRunSupervisor capture piped stdout/stderr
to artifact storage. Uses short-lived real subprocesses where noted.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import pytest

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
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
