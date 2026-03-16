import asyncio
import os
import pytest
from datetime import UTC, datetime
from unittest.mock import patch

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.workflows.agent_queue.storage import AgentQueueArtifactStorage
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.supervisor import ManagedRunSupervisor


def _make_record(
    store: ManagedRunStore,
    run_id: str = "run-1",
    status: str = "launching",
    pid: int | None = None,
) -> ManagedRunRecord:
    record = ManagedRunRecord(
        run_id=run_id,
        agent_id="agent-1",
        runtime_id="codex-cli",
        status=status,
        pid=pid,
        started_at=datetime.now(tz=UTC),
    )
    store.save(record)
    return record


@pytest.fixture
def supervisor_env(tmp_path):
    store_root = tmp_path / "store"
    artifact_root = tmp_path / "artifacts"
    store_root.mkdir()
    artifact_root.mkdir()

    store = ManagedRunStore(store_root)
    artifact_storage = AgentQueueArtifactStorage(artifact_root)
    log_streamer = RuntimeLogStreamer(artifact_storage)
    supervisor = ManagedRunSupervisor(store, log_streamer)
    return store, artifact_storage, log_streamer, supervisor


@pytest.mark.asyncio
async def test_success_exit_classification(supervisor_env):
    store, _, _, supervisor = supervisor_env
    _make_record(store, "run-1", "launching")

    process = await asyncio.create_subprocess_exec(
        "echo", "hello",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    record = await supervisor.supervise(
        run_id="run-1", process=process, timeout_seconds=30
    )

    assert record.status == "completed"
    assert record.exit_code == 0
    assert record.failure_class is None
    assert record.diagnostics_ref is not None


@pytest.mark.asyncio
async def test_failure_exit_classification(supervisor_env):
    store, _, _, supervisor = supervisor_env
    _make_record(store, "run-1", "launching")

    process = await asyncio.create_subprocess_exec(
        "sh", "-c", "exit 1",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    record = await supervisor.supervise(
        run_id="run-1", process=process, timeout_seconds=30
    )

    assert record.status == "failed"
    assert record.exit_code == 1
    assert record.failure_class == "execution_error"
    assert "exited with code 1" in record.error_message


@pytest.mark.asyncio
async def test_timeout_exit_classification(supervisor_env):
    store, _, _, supervisor = supervisor_env
    _make_record(store, "run-1", "launching")

    process = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    record = await supervisor.supervise(
        run_id="run-1", process=process, timeout_seconds=1
    )

    assert record.status == "timed_out"
    assert record.failure_class == "execution_error"
    assert "timed out" in record.error_message


@pytest.mark.asyncio
async def test_cancel_terminates(supervisor_env):
    store, _, _, supervisor = supervisor_env
    _make_record(store, "run-1", "running")

    process = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    supervisor._active_processes["run-1"] = process

    await supervisor.cancel("run-1")

    loaded = store.load("run-1")
    assert loaded.status == "cancelled"
    assert "run-1" not in supervisor._active_processes


@pytest.mark.asyncio
async def test_cancel_without_process(supervisor_env):
    store, _, _, supervisor = supervisor_env
    _make_record(store, "run-1", "running")

    await supervisor.cancel("run-1")

    loaded = store.load("run-1")
    assert loaded.status == "cancelled"


@pytest.mark.asyncio
async def test_reconcile_dead_pids(supervisor_env):
    store, _, _, supervisor = supervisor_env
    # Use a PID that definitely doesn't exist
    _make_record(store, "run-1", "running", pid=999999999)

    with patch.object(ManagedRunSupervisor, "_pid_alive", return_value=False):
        reconciled = await supervisor.reconcile()

    assert len(reconciled) == 1
    assert reconciled[0].run_id == "run-1"
    assert reconciled[0].status == "failed"
    assert reconciled[0].failure_class == "system_error"


@pytest.mark.asyncio
async def test_reconcile_skips_alive_pids(supervisor_env):
    store, _, _, supervisor = supervisor_env
    _make_record(store, "run-1", "running", pid=os.getpid())

    with patch.object(ManagedRunSupervisor, "_pid_alive", return_value=True):
        reconciled = await supervisor.reconcile()

    assert len(reconciled) == 0
    loaded = store.load("run-1")
    assert loaded.status == "running"


@pytest.mark.asyncio
async def test_heartbeat_updates(supervisor_env):
    store, _, _, supervisor = supervisor_env
    _make_record(store, "run-1", "launching")

    # Use a process that runs for ~2 seconds with a 1s heartbeat interval
    process = await asyncio.create_subprocess_exec(
        "sleep", "2",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Temporarily lower heartbeat interval for test using patch to avoid
    # mixed import style (import + from-import of same module).
    with patch('moonmind.workflows.temporal.runtime.supervisor.HEARTBEAT_INTERVAL', 1):
        record = await supervisor.supervise(
            run_id="run-1", process=process, timeout_seconds=30
        )

    assert record.status == "completed"
    # Last heartbeat should have been set
    loaded = store.load("run-1")
    assert loaded.last_heartbeat_at is not None


# ---------------------------------------------------------------------------
# Completion callback tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_callback_called_on_success(supervisor_env):
    store, _, log_streamer, _ = supervisor_env
    _make_record(store, "run-cb-ok", "launching")

    callback_results: list[dict] = []

    async def _callback(payload: dict) -> None:
        callback_results.append(payload)

    supervisor = ManagedRunSupervisor(
        store, log_streamer, completion_callback=_callback
    )

    process = await asyncio.create_subprocess_exec(
        "echo", "hello",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    record = await supervisor.supervise(
        run_id="run-cb-ok", process=process, timeout_seconds=30
    )

    assert record.status == "completed"
    assert len(callback_results) == 1
    payload = callback_results[0]
    assert payload["failure_class"] is None
    assert "summary" in payload
    assert isinstance(payload["output_refs"], list)


@pytest.mark.asyncio
async def test_completion_callback_called_on_failure(supervisor_env):
    store, _, log_streamer, _ = supervisor_env
    _make_record(store, "run-cb-fail", "launching")

    callback_results: list[dict] = []

    async def _callback(payload: dict) -> None:
        callback_results.append(payload)

    supervisor = ManagedRunSupervisor(
        store, log_streamer, completion_callback=_callback
    )

    process = await asyncio.create_subprocess_exec(
        "sh", "-c", "exit 1",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    record = await supervisor.supervise(
        run_id="run-cb-fail", process=process, timeout_seconds=30
    )

    assert record.status == "failed"
    assert len(callback_results) == 1
    payload = callback_results[0]
    assert payload["failure_class"] == "execution_error"
    assert "exited with code 1" in payload["summary"]


@pytest.mark.asyncio
async def test_completion_callback_error_does_not_crash_supervisor(supervisor_env):
    store, _, log_streamer, _ = supervisor_env
    _make_record(store, "run-cb-err", "launching")

    async def _bad_callback(payload: dict) -> None:
        raise RuntimeError("callback exploded")

    supervisor = ManagedRunSupervisor(
        store, log_streamer, completion_callback=_bad_callback
    )

    process = await asyncio.create_subprocess_exec(
        "echo", "hello",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Should NOT raise despite the callback failure
    record = await supervisor.supervise(
        run_id="run-cb-err", process=process, timeout_seconds=30
    )
    assert record.status == "completed"

