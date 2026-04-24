import asyncio
import os
import json
import pytest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.supervisor import ManagedRunSupervisor
from moonmind.workflows.temporal.runtime.strategies.base import ManagedRuntimeExitResult

class _StubArtifactStorage:
    """Minimal file-based artifact storage for tests (replaces AgentQueueArtifactStorage)."""

    def __init__(self, root) -> None:
        self._root = root

    def write_artifact(self, *, job_id, artifact_name, data):
        target_dir = self._root / job_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / artifact_name
        target.write_bytes(data)
        return target, f"{job_id}/{artifact_name}"

    def resolve_storage_path(self, ref):
        return self._root / ref

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

def _resolve_diagnostics_path(
    artifact_storage: _StubArtifactStorage,
    record: ManagedRunRecord,
) -> str | None:
    if not record.diagnostics_ref:
        return None
    return str(artifact_storage.resolve_storage_path(record.diagnostics_ref))

@pytest.fixture
def supervisor_env(tmp_path):
    store_root = tmp_path / "store"
    artifact_root = tmp_path / "artifacts"
    store_root.mkdir()
    artifact_root.mkdir()

    store = ManagedRunStore(store_root)
    artifact_storage = _StubArtifactStorage(artifact_root)
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
async def test_supervise_terminates_stalled_runtime_progress(supervisor_env):
    store, artifact_storage, _, supervisor = supervisor_env
    record = ManagedRunRecord(
        run_id="run-stalled-progress",
        agent_id="codex_cli",
        runtime_id="codex_cli",
        status="launching",
        started_at=datetime.now(tz=UTC),
        workspace_path="/tmp/workspace",
    )
    store.save(record)

    process = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stub_strategy = SimpleNamespace(
        create_output_parser=lambda: None,
        terminate_on_live_rate_limit=lambda: False,
        progress_stall_timeout_seconds=lambda *, timeout_seconds: 2,
        probe_progress_at=lambda **_: record.started_at,
    )

    with patch("moonmind.workflows.temporal.runtime.supervisor.get_strategy", return_value=stub_strategy):
        with patch("moonmind.workflows.temporal.runtime.supervisor.HEARTBEAT_INTERVAL", 1):
            with patch(
                "moonmind.workflows.temporal.runtime.supervisor.NO_OUTPUT_ANNOTATION_INTERVAL_SECONDS",
                1,
            ):
                result = await supervisor.supervise(
                    run_id="run-stalled-progress",
                    process=process,
                    timeout_seconds=30,
                )

    assert result.status == "failed"
    assert result.failure_class == "system_error"
    assert "no observable progress" in str(result.error_message)

    diagnostics_path = _resolve_diagnostics_path(artifact_storage, result)
    assert diagnostics_path is not None
    diagnostics = json.loads(
        artifact_storage.resolve_storage_path(diagnostics_path).read_text(encoding="utf-8")
    )
    annotations = diagnostics.get("annotations", [])
    assert any(
        isinstance(annotation, dict)
        and annotation.get("annotation_type") == "termination_requested_stalled_progress"
        for annotation in annotations
    )

@pytest.mark.asyncio
async def test_supervise_uses_record_started_at_for_progress_probe(supervisor_env):
    store, _, _, supervisor = supervisor_env
    expected_started_at = datetime(2026, 4, 4, 6, 0, tzinfo=UTC)
    record = ManagedRunRecord(
        run_id="run-started-at",
        agent_id="codex_cli",
        runtime_id="codex_cli",
        status="launching",
        started_at=expected_started_at,
        workspace_path="/tmp/workspace",
    )
    store.save(record)

    process = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    captured_started_at: list[datetime] = []
    to_thread_calls: list[str] = []

    def _probe_progress_at(**kwargs):
        captured_started_at.append(kwargs["started_at"])
        return record.started_at

    async def _fake_to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(getattr(func, "__name__", "probe_progress_at"))
        return func(*args, **kwargs)

    stub_strategy = SimpleNamespace(
        create_output_parser=lambda: None,
        terminate_on_live_rate_limit=lambda: False,
        progress_stall_timeout_seconds=lambda *, timeout_seconds: 2,
        probe_progress_at=_probe_progress_at,
    )

    with patch("moonmind.workflows.temporal.runtime.supervisor.get_strategy", return_value=stub_strategy):
        with patch("moonmind.workflows.temporal.runtime.supervisor.asyncio.to_thread", new=_fake_to_thread):
            with patch("moonmind.workflows.temporal.runtime.supervisor.HEARTBEAT_INTERVAL", 1):
                with patch(
                    "moonmind.workflows.temporal.runtime.supervisor.NO_OUTPUT_ANNOTATION_INTERVAL_SECONDS",
                    1,
                ):
                    result = await supervisor.supervise(
                        run_id="run-started-at",
                        process=process,
                        timeout_seconds=30,
                    )

    assert result.status == "failed"
    assert result.failure_class == "system_error"
    assert to_thread_calls
    assert captured_started_at == [expected_started_at, expected_started_at]

@pytest.mark.asyncio
async def test_stalled_progress_does_not_override_clean_exit_without_termination(supervisor_env):
    store, artifact_storage, _, supervisor = supervisor_env
    record = ManagedRunRecord(
        run_id="run-stall-race",
        agent_id="codex_cli",
        runtime_id="codex_cli",
        status="launching",
        started_at=datetime.now(tz=UTC),
        workspace_path="/tmp/workspace",
    )
    store.save(record)

    process = await asyncio.create_subprocess_exec(
        "sh", "-c", "sleep 2; exit 0",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stub_strategy = SimpleNamespace(
        create_output_parser=lambda: None,
        terminate_on_live_rate_limit=lambda: False,
        progress_stall_timeout_seconds=lambda *, timeout_seconds: 1,
        probe_progress_at=lambda **_: record.started_at,
        classify_result=lambda **_: ManagedRuntimeExitResult(
            status="completed",
            failure_class=None,
        ),
    )

    async def _no_op_terminate_on_signal(*, process, trigger):
        await trigger.wait()
        return False

    with patch("moonmind.workflows.temporal.runtime.supervisor.get_strategy", return_value=stub_strategy):
        with patch(
            "moonmind.workflows.temporal.runtime.supervisor.ManagedRunSupervisor._terminate_on_signal",
            new=staticmethod(_no_op_terminate_on_signal),
        ):
            with patch("moonmind.workflows.temporal.runtime.supervisor.HEARTBEAT_INTERVAL", 1):
                with patch(
                    "moonmind.workflows.temporal.runtime.supervisor.NO_OUTPUT_ANNOTATION_INTERVAL_SECONDS",
                    1,
                ):
                    result = await supervisor.supervise(
                        run_id="run-stall-race",
                        process=process,
                        timeout_seconds=30,
                    )

    assert result.status == "completed"
    assert result.failure_class is None

    diagnostics_path = _resolve_diagnostics_path(artifact_storage, result)
    assert diagnostics_path is not None
    diagnostics = json.loads(
        artifact_storage.resolve_storage_path(diagnostics_path).read_text(encoding="utf-8")
    )
    annotations = diagnostics.get("annotations", [])
    assert any(
        isinstance(annotation, dict)
        and annotation.get("annotation_type")
        == "run_completed_before_stalled_progress_termination"
        for annotation in annotations
    )

@pytest.mark.asyncio
async def test_exit_code_file_is_authoritative(supervisor_env, tmp_path):
    store, artifact_storage, _, supervisor = supervisor_env
    _make_record(store, "run-exit-file", "launching")
    exit_code_path = tmp_path / "run-exit-file.exit"
    exit_code_path.write_text("42\n", encoding="utf-8")

    process = await asyncio.create_subprocess_exec(
        "sh", "-c", "exit 0",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    record = await supervisor.supervise(
        run_id="run-exit-file",
        process=process,
        timeout_seconds=30,
        exit_code_path=str(exit_code_path),
        cleanup_paths=[str(exit_code_path)],
    )

    assert record.status == "failed"
    assert record.exit_code == 42
    assert record.failure_class == "execution_error"
    assert not exit_code_path.exists()
    diagnostics_path = _resolve_diagnostics_path(artifact_storage, record)
    assert diagnostics_path is not None
    diagnostics = json.loads(
        artifact_storage.resolve_storage_path(diagnostics_path).read_text(encoding="utf-8")
    )
    annotations = diagnostics.get("annotations", [])
    assert any(
        isinstance(annotation, dict)
        and annotation.get("annotation_type") == "exit_code_resolved"
        and "42" in str(annotation.get("text", ""))
        for annotation in annotations
    )

@pytest.mark.asyncio
async def test_missing_exit_code_file_fails_closed(supervisor_env, tmp_path):
    store, _, _, supervisor = supervisor_env
    _make_record(store, "run-missing-exit", "launching")
    exit_code_path = tmp_path / "missing.exit"

    process = await asyncio.create_subprocess_exec(
        "sh", "-c", "exit 0",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    record = await supervisor.supervise(
        run_id="run-missing-exit",
        process=process,
        timeout_seconds=30,
        exit_code_path=str(exit_code_path),
        cleanup_paths=[str(exit_code_path)],
    )

    assert record.status == "failed"
    assert record.exit_code == 1
    assert record.failure_class == "execution_error"

@pytest.mark.asyncio
async def test_timeout_ignores_exit_code_file_and_cleans_it(
    supervisor_env, tmp_path
):
    store, _, _, supervisor = supervisor_env
    _make_record(store, "run-timeout-exit", "launching")
    exit_code_path = tmp_path / "run-timeout.exit"
    exit_code_path.write_text("0\n", encoding="utf-8")

    process = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    record = await supervisor.supervise(
        run_id="run-timeout-exit",
        process=process,
        timeout_seconds=1,
        exit_code_path=str(exit_code_path),
        cleanup_paths=[str(exit_code_path)],
    )

    assert record.status == "timed_out"
    assert record.exit_code is None
    assert not exit_code_path.exists()

@pytest.mark.asyncio
async def test_supervise_limits_repeated_warning_annotations(supervisor_env):
    store, artifact_storage, _, supervisor = supervisor_env
    _make_record(store, "run-warning", "launching")

    process = await asyncio.create_subprocess_exec(
        "sh",
        "-c",
        "printf 'warning: repeated config issue\\nwarning: repeated config issue\\nwarning: repeated config issue\\n' >&2",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    record = await supervisor.supervise(
        run_id="run-warning", process=process, timeout_seconds=30
    )

    diagnostics_path = _resolve_diagnostics_path(artifact_storage, record)
    assert diagnostics_path is not None
    diagnostics = json.loads(
        artifact_storage.resolve_storage_path(diagnostics_path).read_text(encoding="utf-8")
    )
    warning_annotations = [
        annotation
        for annotation in diagnostics.get("annotations", [])
        if (
            isinstance(annotation, dict)
            and annotation.get("annotation_type") == "warning_deduplicated"
        )
    ]
    assert len(warning_annotations) == 1
    assert (
        "repeated config warning observed"
        in str(warning_annotations[0].get("text", ""))
    )
    assert warning_annotations[0].get("metadata", {}).get("duplicate_count") == 3

@pytest.mark.asyncio
async def test_supervise_debounces_no_output_interval(supervisor_env):
    store, artifact_storage, _, supervisor = supervisor_env
    _make_record(store, "run-no-output", "launching")

    async def _fake_heartbeat_and_wait_with_timeout(
        self,
        run_id: str,
        process: asyncio.subprocess.Process,
        timeout_seconds: int,
        no_output_callback=None,
    ) -> tuple[int | None, bool]:
        base_time = datetime.now(tz=UTC)
        if no_output_callback is None:
            return 0, False
        await no_output_callback(base_time + timedelta(seconds=2.5))
        await no_output_callback(base_time + timedelta(seconds=4.9))
        await no_output_callback(base_time + timedelta(seconds=5.0))
        return 0, False

    process = await asyncio.create_subprocess_exec(
        "true",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    with patch.object(
        ManagedRunSupervisor,
        "_heartbeat_and_wait_with_timeout",
        _fake_heartbeat_and_wait_with_timeout,
    ):
        with patch(
            "moonmind.workflows.temporal.runtime.supervisor.NO_OUTPUT_ANNOTATION_INTERVAL_SECONDS",
            2,
        ):
            record = await supervisor.supervise(
                run_id="run-no-output",
                process=process,
                timeout_seconds=30,
            )

    diagnostics_path = _resolve_diagnostics_path(artifact_storage, record)
    assert diagnostics_path is not None
    diagnostics = json.loads(
        artifact_storage.resolve_storage_path(diagnostics_path).read_text(encoding="utf-8")
    )
    no_output_annotations = [
        annotation
        for annotation in diagnostics.get("annotations", [])
        if (
            isinstance(annotation, dict)
            and annotation.get("annotation_type") == "no_output_interval"
        )
    ]
    assert len(no_output_annotations) == 2

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
    assert loaded.status == "canceled"
    assert "run-1" not in supervisor._active_processes

@pytest.mark.asyncio
async def test_cancel_cleans_registered_runtime_files(supervisor_env, tmp_path):
    store, _, _, supervisor = supervisor_env
    _make_record(store, "run-cancel-cleanup", "running")
    cleanup_path = tmp_path / "cleanup.target"
    cleanup_path.write_text("keep", encoding="utf-8")
    deferred_cleanup_path = tmp_path / "cleanup.deferred"
    deferred_cleanup_path.write_text("keep", encoding="utf-8")

    process = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    supervisor._active_processes["run-cancel-cleanup"] = process
    supervisor._cleanup_paths["run-cancel-cleanup"] = (str(cleanup_path),)
    supervisor._deferred_cleanup_paths["run-cancel-cleanup"] = (
        str(deferred_cleanup_path),
    )

    await supervisor.cancel("run-cancel-cleanup")

    loaded = store.load("run-cancel-cleanup")
    assert loaded.status == "canceled"
    assert not cleanup_path.exists()
    assert not deferred_cleanup_path.exists()

@pytest.mark.asyncio
async def test_supervise_preserves_deferred_cleanup_until_explicit_release(
    supervisor_env, tmp_path
):
    store, _, _, supervisor = supervisor_env
    _make_record(store, "run-deferred-cleanup", "launching")
    deferred_cleanup_path = tmp_path / "cleanup.deferred"
    deferred_cleanup_path.write_text("keep", encoding="utf-8")

    process = await asyncio.create_subprocess_exec(
        "echo", "hello",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    record = await supervisor.supervise(
        run_id="run-deferred-cleanup",
        process=process,
        timeout_seconds=30,
        deferred_cleanup_paths=[str(deferred_cleanup_path)],
    )

    assert record.status == "completed"
    assert deferred_cleanup_path.exists()

    supervisor.cleanup_deferred_run_files("run-deferred-cleanup")

    assert not deferred_cleanup_path.exists()

def test_cleanup_runtime_files_removes_directories(tmp_path):
    nested_dir = tmp_path / "cleanup.dir" / "nested"
    nested_dir.mkdir(parents=True)
    nested_file = nested_dir / "config.toml"
    nested_file.write_text("keep", encoding="utf-8")

    ManagedRunSupervisor._cleanup_runtime_files((str(tmp_path / "cleanup.dir"),))

    assert not (tmp_path / "cleanup.dir").exists()

@pytest.mark.asyncio
async def test_cancel_without_process(supervisor_env):
    store, _, _, supervisor = supervisor_env
    _make_record(store, "run-1", "running")

    await supervisor.cancel("run-1")

    loaded = store.load("run-1")
    assert loaded.status == "canceled"

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

@pytest.mark.asyncio
async def test_supervise_uses_output_parser_from_strategy(supervisor_env, tmp_path):
    """Supervisor wires strategy.create_output_parser() and parsed output
    appears in the diagnostics artifact."""
    import json as _json
    from datetime import datetime, UTC
    store, artifact_storage, _, supervisor = supervisor_env

    record = ManagedRunRecord(
        run_id="run-gemini-parser",
        agent_id="agent-1",
        runtime_id="gemini_cli",
        status="launching",
        started_at=datetime.now(tz=UTC),
    )
    store.save(record)

    gemini_err = "message: No capacity available for model\nreason: MODEL_CAPACITY_EXHAUSTED"
    process = await asyncio.create_subprocess_exec(
        "sh", "-c", f"echo '{gemini_err}' >&2",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    result = await supervisor.supervise(
        run_id="run-gemini-parser", process=process, timeout_seconds=30
    )

    # Read the diagnostics artifact and verify parsed_output is present
    diag_path = artifact_storage.resolve_storage_path(result.diagnostics_ref)
    diag = _json.loads(diag_path.read_text())
    assert "parsed_output" in diag
    po = diag["parsed_output"]
    assert po["rate_limited"] is True
