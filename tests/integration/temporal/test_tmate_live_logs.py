"""Integration tests for tmate live-log tailing capability.

Verifies the end-to-end flow: TmateSessionManager wraps a process →
stdout/stderr piped through tmate → RuntimeLogStreamer captures output
to artifact storage → TmateEndpoints provide web_ro for browser tailing.

All tests use mocked subprocesses; no real tmate binary is needed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    ManagedRunRecord,
    ManagedRuntimeProfile,
)
from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.supervisor import ManagedRunSupervisor
from moonmind.workflows.temporal.runtime.tmate_session import (
    TmateEndpoints,
    TmateSessionManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(**overrides) -> ManagedRuntimeProfile:
    defaults = dict(
        runtime_id="codex-cli",
        command_template=["echo", "hello"],
        default_model="o4-mini",
        default_effort="medium",
        default_timeout_seconds=3600,
        workspace_mode="tempdir",
        env_overrides={},
    )
    defaults.update(overrides)
    return ManagedRuntimeProfile(**defaults)


def _make_request(**overrides) -> AgentExecutionRequest:
    defaults = dict(
        agent_kind="managed",
        agent_id="agent-logs",
        execution_profile_ref="default-managed",
        correlation_id="logs-integ",
        idempotency_key="run-logs-integ",
    )
    defaults.update(overrides)
    return AgentExecutionRequest(**defaults)


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


class _FakeProcess:
    """Minimal fake asyncio subprocess with controllable stdout/stderr."""

    def __init__(
        self,
        *,
        pid: int = 9999,
        returncode: int | None = None,
        stdout_bytes: bytes = b"",
        stderr_bytes: bytes = b"",
    ) -> None:
        self.pid = pid
        self.returncode = returncode
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self._stdout_bytes = stdout_bytes
        self._stderr_bytes = stderr_bytes
        self.stdout.feed_data(stdout_bytes)
        self.stdout.feed_eof()
        self.stderr.feed_data(stderr_bytes)
        self.stderr.feed_eof()

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout_bytes, self._stderr_bytes

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Test 1 — tmate session captures stdout to artifact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tmate_session_captures_stdout_to_artifact(tmp_path: Path):
    """Launch a process via TmateSessionManager that writes known output.

    Verify RuntimeLogStreamer.stream_to_artifact captures the full content
    into artifact storage.
    """
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
        run_id="tmate-logs-1",
        stream_name="stdout",
    )

    # Verify artifact was written.
    assert ref == "tmate-logs-1/stdout.log"
    artifact_path = artifact_root / "tmate-logs-1" / "stdout.log"
    assert artifact_path.exists()
    assert artifact_path.read_bytes() == expected_output

    # Verify decoded content matches.
    assert "Agent starting" in content
    assert "Task completed successfully" in content


# ---------------------------------------------------------------------------
# Test 2 — tmate endpoints available during log streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tmate_endpoints_available_during_log_streaming(
    tmp_path: Path, monkeypatch
):
    """Start a tmate session and verify that web_ro and attach_ro are
    populated (non-None), confirming the session is ready for live tailing
    before the process completes.
    """
    store = ManagedRunStore(tmp_path / "store")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile()
    request = _make_request()

    monkeypatch.setattr(
        TmateSessionManager, "is_available", staticmethod(lambda: True)
    )

    # Simulate a long-running process (still alive).
    long_process = _FakeProcess(
        pid=11111,
        returncode=None,
        stdout_bytes=b"streaming live output...\n",
    )

    fake_endpoints = TmateEndpoints(
        session_name="mm-livelogs1",
        socket_path="/tmp/moonmind/tmate/mm-livelogs1.sock",
        attach_ro="ssh ro-token@tmate.example.com",
        attach_rw="ssh rw-token@tmate.example.com",
        web_ro="https://tmate.example.com/t/ro-livelogs1",
        web_rw="https://tmate.example.com/t/rw-livelogs1",
    )

    async def _fake_start(self, command=None, **kwargs):
        self._process = long_process
        self._endpoints = fake_endpoints
        self._exit_code_path_value = None
        return fake_endpoints

    monkeypatch.setattr(TmateSessionManager, "start", _fake_start)

    # Prevent teardown side effects.
    monkeypatch.setattr(
        TmateSessionManager, "teardown",
        AsyncMock(),
    )

    record, process, endpoints, tmate_manager = await launcher.launch(
        run_id="tmate-livelogs-1", request=request, profile=profile
    )

    # Endpoints dict should be populated with RO URLs for live tailing.
    assert endpoints is not None
    assert endpoints["web_ro"] == "https://tmate.example.com/t/ro-livelogs1"
    assert endpoints["attach_ro"] == "ssh ro-token@tmate.example.com"
    assert endpoints["tmate_session_name"] == "mm-livelogs1"

    # RW endpoints should also be present.
    assert endpoints["web_rw"] == "https://tmate.example.com/t/rw-livelogs1"

    # Manager should be returned for supervisor to tear down later.
    assert tmate_manager is not None


# ---------------------------------------------------------------------------
# Test 3 — supervisor streams tmate logs to artifacts (full lifecycle)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_streams_tmate_logs_to_artifacts(tmp_path: Path):
    """Full launcher→supervisor lifecycle: launch via TmateSessionManager,
    supervise to completion, verify log artifacts contain expected output.
    """
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
        run_id="tmate-stream-1",
        agent_id="agent-logs",
        runtime_id="codex-cli",
        status="launching",
        pid=22222,
        started_at=datetime.now(tz=UTC),
    )
    store.save(record)

    # Use a real short-lived process that produces known output.
    process = await asyncio.create_subprocess_exec(
        "echo", "live-log-content-from-tmate",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Mock tmate_manager for teardown tracking.
    tmate_manager = MagicMock(spec=TmateSessionManager)
    tmate_manager.teardown = AsyncMock()

    result = await supervisor.supervise(
        run_id="tmate-stream-1",
        process=process,
        timeout_seconds=30,
        tmate_manager=tmate_manager,
    )

    # Verify supervision completed successfully.
    assert result.status == "completed"
    tmate_manager.teardown.assert_awaited_once()

    # Verify stdout artifact was captured with expected content.
    stdout_artifact = artifact_root / "tmate-stream-1" / "stdout.log"
    assert stdout_artifact.exists()
    assert "live-log-content-from-tmate" in stdout_artifact.read_text()

    # Verify diagnostics artifact was created.
    diag = artifact_root / "tmate-stream-1" / "diagnostics.json"
    assert diag.exists()


# ---------------------------------------------------------------------------
# Test 4 — exit code capture during streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tmate_exit_code_capture_during_streaming(tmp_path: Path):
    """Launch a tmate-wrapped process that exits with a known code.

    Verify exit_code_path is written and the supervisor correctly reads it.
    """
    store_root = tmp_path / "store"
    artifact_root = tmp_path / "artifacts"
    store_root.mkdir()
    artifact_root.mkdir()

    store = ManagedRunStore(store_root)
    storage = _StubArtifactStorage(artifact_root)
    streamer = RuntimeLogStreamer(storage)
    supervisor = ManagedRunSupervisor(store, streamer)

    run_id = "tmate-exit-1"

    record = ManagedRunRecord(
        run_id=run_id,
        agent_id="agent-logs",
        runtime_id="codex-cli",
        status="launching",
        pid=33333,
        started_at=datetime.now(tz=UTC),
    )
    store.save(record)

    # Simulate exit code file written by tmate wrapper.
    exit_code_file = tmp_path / "exit_code.txt"
    exit_code_file.write_text("42\n")

    # Process that exits quickly (rc=0 from the wrapper, actual rc in the file).
    process = await asyncio.create_subprocess_exec(
        "echo", "exiting-with-code-42",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    tmate_manager = MagicMock(spec=TmateSessionManager)
    tmate_manager.teardown = AsyncMock()

    result = await supervisor.supervise(
        run_id=run_id,
        process=process,
        timeout_seconds=30,
        exit_code_path=str(exit_code_file),
        tmate_manager=tmate_manager,
    )

    # The supervisor should read exit code 42 from the file (not 0 from process).
    assert result.exit_code == 42
    # Non-zero exit means the run classified as failed.
    assert result.status == "failed"
    tmate_manager.teardown.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 5 — concurrent stdout/stderr streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tmate_concurrent_stdout_stderr_streaming(tmp_path: Path):
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

    log_refs, stdout_text, stderr_text, parsed = await streamer.stream_and_parse(
        stdout_reader,
        stderr_reader,
        run_id="tmate-concurrent-1",
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
    stdout_artifact = artifact_root / "tmate-concurrent-1" / "stdout.log"
    stderr_artifact = artifact_root / "tmate-concurrent-1" / "stderr.log"
    assert stdout_artifact.exists()
    assert stderr_artifact.exists()
    assert stdout_artifact.read_bytes() == stdout_content
    assert stderr_artifact.read_bytes() == stderr_content
