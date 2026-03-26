"""Integration tests for tmate-wrapped managed agent lifecycle.

Tests the launcher→supervisor→teardown paths that exercise tmate
session management — specifically the fallback behavior when tmate
fails and the teardown guarantees in the supervisor's ``finally``.

The tmate subprocess is always mocked; no real tmate binary is needed,
though some tests still spawn short-lived real commands (for example,
``echo``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
# Fixtures
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
        agent_id="agent-1",
        execution_profile_ref="default-managed",
        correlation_id="tmate-integ",
        idempotency_key="run-tmate-integ",
    )
    defaults.update(overrides)
    return AgentExecutionRequest(**defaults)


class _FakeProcess:
    """Minimal fake asyncio subprocess."""

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
        # Pre-feed data so readers don't hang.
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


class _StubArtifactStorage:
    """Minimal file-based artifact storage for supervisor tests."""

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
# Test 1 — tmate start() raises → fallback to plain subprocess
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tmate_start_failure_falls_back_to_plain_subprocess(
    tmp_path: Path, monkeypatch
):
    """When TmateSessionManager.start() raises, the launcher must fall back
    to a plain subprocess and return endpoints=None."""
    store = ManagedRunStore(tmp_path / "store")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile()
    request = _make_request()

    # Make tmate appear available.
    monkeypatch.setattr(TmateSessionManager, "is_available", staticmethod(lambda: True))

    # Track teardown.
    teardown_called = False

    async def _tracking_teardown(self):
        nonlocal teardown_called
        teardown_called = True

    monkeypatch.setattr(TmateSessionManager, "teardown", _tracking_teardown)

    # Make start() raise to simulate tmate failure.
    async def _failing_start(self, command=None, **kwargs):
        raise RuntimeError("tmate crashed on startup")

    monkeypatch.setattr(TmateSessionManager, "start", _failing_start)

    # The fallback path creates a plain subprocess — mock it.
    plain_process = _FakeProcess(pid=5555, returncode=0)

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return plain_process

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    record, process, endpoints, tmate_manager = await launcher.launch(
        run_id="tmate-fail-1", request=request, profile=profile
    )

    # Should have fallen back: no endpoints, no tmate_manager.
    assert endpoints is None
    assert tmate_manager is None
    assert process.pid == 5555
    assert record.run_id == "tmate-fail-1"
    # teardown() should have been called on the failed manager.
    assert teardown_called


# ---------------------------------------------------------------------------
# Test 2 — tmate process exits immediately → launcher detects and falls back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tmate_immediate_exit_falls_back(
    tmp_path: Path, monkeypatch
):
    """When tmate starts but the process exits immediately with a non-zero
    returncode, the launcher should detect this and fall back."""
    store = ManagedRunStore(tmp_path / "store")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile()
    request = _make_request()

    monkeypatch.setattr(TmateSessionManager, "is_available", staticmethod(lambda: True))

    # start() succeeds but returns a dead process.
    dead_process = _FakeProcess(pid=6666, returncode=1)

    async def _start_with_dead_process(self, command=None, **kwargs):
        self._process = dead_process
        self._endpoints = TmateEndpoints(
            session_name="mm-dead", socket_path="/tmp/dead.sock"
        )
        return self._endpoints

    monkeypatch.setattr(TmateSessionManager, "start", _start_with_dead_process)

    # Track teardown.
    teardown_called = False

    async def _tracking_teardown(self):
        nonlocal teardown_called
        teardown_called = True

    monkeypatch.setattr(TmateSessionManager, "teardown", _tracking_teardown)

    # Fallback plain subprocess.
    plain_process = _FakeProcess(pid=7777, returncode=0)

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return plain_process

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    record, process, endpoints, tmate_manager = await launcher.launch(
        run_id="tmate-dead-1", request=request, profile=profile
    )

    assert endpoints is None
    assert tmate_manager is None
    assert process.pid == 7777
    assert teardown_called


# ---------------------------------------------------------------------------
# Test 3 — tmate success → supervisor teardown in finally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_tears_down_tmate_on_success(tmp_path: Path):
    """After successful supervision, tmate_manager.teardown() must be called
    in the supervisor's finally block."""
    store_root = tmp_path / "store"
    artifact_root = tmp_path / "artifacts"
    store_root.mkdir()
    artifact_root.mkdir()

    store = ManagedRunStore(store_root)
    artifact_storage = _StubArtifactStorage(artifact_root)
    log_streamer = RuntimeLogStreamer(artifact_storage)
    supervisor = ManagedRunSupervisor(store, log_streamer)

    # Create the run record that supervisor expects.
    record = ManagedRunRecord(
        run_id="tmate-sup-1",
        agent_id="agent-1",
        runtime_id="codex-cli",
        status="launching",
        pid=8888,
        started_at=datetime.now(tz=UTC),
    )
    store.save(record)

    # A real short-lived process for the supervisor to wait on.
    process = await asyncio.create_subprocess_exec(
        "echo", "hello",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Mock tmate_manager with trackable teardown.
    tmate_manager = MagicMock(spec=TmateSessionManager)
    tmate_manager.teardown = AsyncMock()

    result = await supervisor.supervise(
        run_id="tmate-sup-1",
        process=process,
        timeout_seconds=30,
        tmate_manager=tmate_manager,
    )

    assert result.status == "completed"
    tmate_manager.teardown.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 4 — full launcher→supervisor lifecycle with tmate_manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_tmate_launcher_supervisor_lifecycle(
    tmp_path: Path, monkeypatch
):
    """End-to-end: launcher with tmate success → supervisor completes →
    tmate_manager teardown is called."""
    store_root = tmp_path / "store"
    artifact_root = tmp_path / "artifacts"
    store_root.mkdir()
    artifact_root.mkdir()

    store = ManagedRunStore(store_root)
    launcher = ManagedRuntimeLauncher(store)
    artifact_storage = _StubArtifactStorage(artifact_root)
    log_streamer = RuntimeLogStreamer(artifact_storage)
    supervisor = ManagedRunSupervisor(store, log_streamer)

    profile = _make_profile()
    request = _make_request()

    monkeypatch.setattr(TmateSessionManager, "is_available", staticmethod(lambda: True))

    # Use a real short-lived process that the supervisor can wait on.
    real_process = await asyncio.create_subprocess_exec(
        "echo", "lifecycle-test",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    real_pid = real_process.pid

    run_id = "tmate-lifecycle-1"
    expected_session_name = "mm-" + run_id.replace("-", "")[:16]

    fake_endpoints = TmateEndpoints(
        session_name=expected_session_name,
        socket_path=f"/tmp/moonmind/tmate/{expected_session_name}.sock",
        attach_ro="ssh ro@host",
        web_ro="https://tmate.io/t/ro",
    )

    async def _fake_start(self, command=None, **kwargs):
        self._process = real_process
        self._endpoints = fake_endpoints
        self._exit_code_path_value = None
        return fake_endpoints

    monkeypatch.setattr(TmateSessionManager, "start", _fake_start)

    # Track teardown.
    teardown_called = False

    async def _tracking_teardown(self):
        nonlocal teardown_called
        teardown_called = True

    monkeypatch.setattr(TmateSessionManager, "teardown", _tracking_teardown)

    # Launch
    record, process, endpoints, tmate_manager = await launcher.launch(
        run_id=run_id, request=request, profile=profile
    )

    assert endpoints is not None
    assert endpoints["tmate_session_name"] == expected_session_name
    assert tmate_manager is not None
    assert process.pid == real_pid

    # Supervise
    result = await supervisor.supervise(
        run_id="tmate-lifecycle-1",
        process=process,
        timeout_seconds=30,
        tmate_manager=tmate_manager,
    )

    assert result.status == "completed"
    assert teardown_called


# ---------------------------------------------------------------------------
# Test 5 — supervisor teardown called even on supervision error (timeout)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_tears_down_tmate_on_timeout(tmp_path: Path):
    """When the supervised process times out, tmate_manager.teardown() must
    still be called (via the finally block)."""
    store_root = tmp_path / "store"
    artifact_root = tmp_path / "artifacts"
    store_root.mkdir()
    artifact_root.mkdir()

    store = ManagedRunStore(store_root)
    artifact_storage = _StubArtifactStorage(artifact_root)
    log_streamer = RuntimeLogStreamer(artifact_storage)
    supervisor = ManagedRunSupervisor(store, log_streamer)

    record = ManagedRunRecord(
        run_id="tmate-timeout-1",
        agent_id="agent-1",
        runtime_id="codex-cli",
        status="launching",
        pid=11111,
        started_at=datetime.now(tz=UTC),
    )
    store.save(record)

    # Long-running process that will be timed out.
    process = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    tmate_manager = MagicMock(spec=TmateSessionManager)
    tmate_manager.teardown = AsyncMock()

    result = await supervisor.supervise(
        run_id="tmate-timeout-1",
        process=process,
        timeout_seconds=1,
        tmate_manager=tmate_manager,
    )

    assert result.status == "timed_out"
    tmate_manager.teardown.assert_awaited_once()
