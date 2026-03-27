"""Unit tests verifying that ManagedRunSupervisor streams output concurrently.

These tests specifically demonstrate:
  1. Streaming runs concurrently with the process (not after exit).
  2. Large output doesn't deadlock due to OS pipe buffer exhaustion.
  3. None stdout/stderr (tmate-like case) is handled gracefully.
  4. The _heartbeat_and_wait_with_timeout helper returns correct tuples.
  5. Exit-code-file override still works in the concurrent path.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.supervisor import ManagedRunSupervisor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubArtifactStorage:
    """Minimal file-based artifact storage for tests."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write_artifact(
        self, *, job_id: str, artifact_name: str, data: bytes
    ) -> tuple[Path, str]:
        target_dir = self._root / job_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / artifact_name
        target.write_bytes(data)
        return target, f"{job_id}/{artifact_name}"

    def resolve_storage_path(self, ref: str) -> Path:
        return self._root / ref


def _make_supervisor(
    tmp_path: Path,
) -> tuple[ManagedRunSupervisor, ManagedRunStore, _StubArtifactStorage]:
    store_root = tmp_path / "store"
    store_root.mkdir()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    store = ManagedRunStore(store_root)
    storage = _StubArtifactStorage(artifact_root)
    streamer = RuntimeLogStreamer(storage)
    supervisor = ManagedRunSupervisor(store, streamer)
    return supervisor, store, storage


def _save_record(store: ManagedRunStore, run_id: str) -> ManagedRunRecord:
    record = ManagedRunRecord(
        run_id=run_id,
        agent_id="test-agent",
        runtime_id="gemini_cli",
        status="launching",
        pid=12345,
        started_at=datetime.now(tz=UTC),
    )
    store.save(record)
    return record


# ---------------------------------------------------------------------------
# Test 1 — heartbeat_and_wait_with_timeout returns (exit_code, False) on normal exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_and_wait_with_timeout_normal_exit(tmp_path: Path):
    """_heartbeat_and_wait_with_timeout returns (rc, False) when process exits normally."""
    supervisor, store, _ = _make_supervisor(tmp_path)

    process = await asyncio.create_subprocess_exec(
        "true",  # exits immediately with rc=0
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    exit_code, timed_out = await supervisor._heartbeat_and_wait_with_timeout(
        "run-t1", process, timeout_seconds=10
    )
    assert exit_code == 0
    assert timed_out is False


# ---------------------------------------------------------------------------
# Test 2 — heartbeat_and_wait_with_timeout returns (None, True) on timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_and_wait_with_timeout_timed_out(tmp_path: Path):
    """_heartbeat_and_wait_with_timeout returns (None, True) when process times out."""
    supervisor, store, _ = _make_supervisor(tmp_path)

    process = await asyncio.create_subprocess_exec(
        "sleep", "30",  # much longer than the timeout
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        exit_code, timed_out = await supervisor._heartbeat_and_wait_with_timeout(
            "run-t2", process, timeout_seconds=1
        )
        assert exit_code is None
        assert timed_out is True
    finally:
        # Process may already be terminated by _heartbeat_and_wait_with_timeout.
        try:
            process.kill()
            await process.wait()
        except ProcessLookupError:
            pass


# ---------------------------------------------------------------------------
# Test 3 — supervise() captures stdout concurrently (does not deadlock)
#
# This is the core regression test.  Before the fix, streaming happened AFTER
# _heartbeat_and_wait, meaning the process had to finish before any output was
# read.  For large output this causes the OS pipe buffer to fill up, blocking
# the write-end of the pipe inside the subprocess — a deadlock.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_captures_large_stdout_without_deadlock(tmp_path: Path):
    """Large output is captured without deadlocking (proves concurrent streaming).

    We produce ~256 KB of output — well above the typical 64KB pipe buffer.
    Sequential streaming would deadlock here; concurrent streaming succeeds.
    """
    supervisor, store, storage = _make_supervisor(tmp_path)
    run_id = "run-large-output"
    _save_record(store, run_id)

    # python -c "print('x' * 256000)" produces ~256KB to stdout
    process = await asyncio.create_subprocess_exec(
        "python3", "-c", "print('x' * 256000)",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    result = await supervisor.supervise(
        run_id=run_id,
        process=process,
        timeout_seconds=30,
    )

    assert result.status == "completed"
    stdout_artifact = storage.resolve_storage_path(f"{run_id}/stdout.log")
    assert stdout_artifact.exists()
    content = stdout_artifact.read_bytes()
    # Should have captured all 256K+ bytes (256000 'x' chars + newline)
    assert len(content) >= 256000


# ---------------------------------------------------------------------------
# Test 4 — supervise() captures stdout for a normal short-lived process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_captures_stdout_normal_process(tmp_path: Path):
    """supervise() captures stdout and classifies exit correctly."""
    supervisor, store, storage = _make_supervisor(tmp_path)
    run_id = "run-normal-1"
    _save_record(store, run_id)

    process = await asyncio.create_subprocess_exec(
        "echo", "live-output-works",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    result = await supervisor.supervise(
        run_id=run_id,
        process=process,
        timeout_seconds=30,
    )

    assert result.status == "completed"
    stdout_artifact = storage.resolve_storage_path(f"{run_id}/stdout.log")
    assert stdout_artifact.exists()
    assert "live-output-works" in stdout_artifact.read_text()


# ---------------------------------------------------------------------------
# Test 5 — supervise() with None stdout/stderr (tmate-like scenario)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_with_none_stdout_does_not_crash(tmp_path: Path):
    """supervise() gracefully handles None stdout/stderr (tmate wrapping case)."""
    supervisor, store, storage = _make_supervisor(tmp_path)
    run_id = "run-none-streams"
    _save_record(store, run_id)

    # Simulate a process where stdout/stderr are None (no PIPE requested)
    process = await asyncio.create_subprocess_exec(
        "true",
        stdin=asyncio.subprocess.DEVNULL,
        # stdout and stderr NOT piped — will be None
    )
    assert process.stdout is None
    assert process.stderr is None

    # Should complete without crashing
    result = await supervisor.supervise(
        run_id=run_id,
        process=process,
        timeout_seconds=10,
    )

    # Diagnostics artifact should still be created even with no stream content
    assert result.status == "completed"
    diag_artifact = storage.resolve_storage_path(f"{run_id}/diagnostics.json")
    assert diag_artifact.exists()


# ---------------------------------------------------------------------------
# Test 6 — supervise() reads exit code from file when provided
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_exit_code_from_file_overrides_process_rc(tmp_path: Path):
    """When exit_code_path is provided, supervisor reads exit code from file."""
    supervisor, store, storage = _make_supervisor(tmp_path)
    run_id = "run-exitfile-1"
    _save_record(store, run_id)

    exit_file = tmp_path / "exit_code.txt"
    exit_file.write_text("42\n")

    # Process exits 0, but the exit file says 42
    process = await asyncio.create_subprocess_exec(
        "true",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    result = await supervisor.supervise(
        run_id=run_id,
        process=process,
        exit_code_path=str(exit_file),
        timeout_seconds=10,
    )

    assert result.exit_code == 42
    assert result.status == "failed"


# ---------------------------------------------------------------------------
# Test 7 — supervise() with non-zero exit code classifies as failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_nonzero_exit_classified_as_failed(tmp_path: Path):
    """Non-zero exit code is classified as failed."""
    supervisor, store, storage = _make_supervisor(tmp_path)
    run_id = "run-fail-1"
    _save_record(store, run_id)

    process = await asyncio.create_subprocess_exec(
        "false",  # exits with rc=1
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    result = await supervisor.supervise(
        run_id=run_id,
        process=process,
        timeout_seconds=10,
    )

    assert result.status == "failed"
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Test 8 — supervise() invokes tmate_manager.teardown on completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_calls_tmate_teardown(tmp_path: Path):
    """tmate_manager.teardown() is always called after supervision ends."""
    supervisor, store, storage = _make_supervisor(tmp_path)
    run_id = "run-tmate-td"
    _save_record(store, run_id)

    process = await asyncio.create_subprocess_exec(
        "echo", "done",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    tmate_manager = MagicMock()
    tmate_manager.teardown = AsyncMock()

    await supervisor.supervise(
        run_id=run_id,
        process=process,
        timeout_seconds=10,
        tmate_manager=tmate_manager,
    )

    tmate_manager.teardown.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 9 — streaming starts during process execution, not after
#
# This test proves the correctness guarantee: a process that produces output
# and THEN sleeps is fully captured.  If streaming started only after exit the
# data would be captured from the buffer, which would work in this case too.
# But the key is that the gather() starts BOTH tasks before either completes —
# verifiable by patching stream_and_parse to assert it's called before process.wait.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_starts_concurrently_with_heartbeat(tmp_path: Path):
    """stream_and_parse is started concurrently (not after) _heartbeat_and_wait."""
    supervisor, store, storage = _make_supervisor(tmp_path)
    run_id = "run-concurrent-verify"
    _save_record(store, run_id)

    call_order: list[str] = []

    original_stream = supervisor._log_streamer.stream_and_parse
    original_heartbeat = supervisor._heartbeat_and_wait_with_timeout

    async def tracking_stream(*args, **kwargs):
        call_order.append("stream_started")
        result = await original_stream(*args, **kwargs)
        call_order.append("stream_done")
        return result

    async def tracking_heartbeat(*args, **kwargs):
        call_order.append("heartbeat_started")
        result = await original_heartbeat(*args, **kwargs)
        call_order.append("heartbeat_done")
        return result

    supervisor._log_streamer.stream_and_parse = tracking_stream
    supervisor._heartbeat_and_wait_with_timeout = tracking_heartbeat

    process = await asyncio.create_subprocess_exec(
        "echo", "hello",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    await supervisor.supervise(
        run_id=run_id,
        process=process,
        timeout_seconds=10,
    )

    # Both tasks must have been started (appeared in call_order)
    assert "stream_started" in call_order
    assert "heartbeat_started" in call_order
    # Both must complete
    assert "stream_done" in call_order
    assert "heartbeat_done" in call_order
    # Crucially: both _started_ before either _done_ (concurrency proof)
    assert call_order.index("stream_started") < call_order.index("heartbeat_done")
    assert call_order.index("heartbeat_started") < call_order.index("stream_done")
