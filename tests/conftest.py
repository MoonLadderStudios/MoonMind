import asyncio
import atexit
import inspect
import os
import signal
import subprocess
import threading
from pathlib import Path

import pytest

# Mirror the auth module's disabled-mode default user id to avoid importing
# api_service.auth during global pytest collection.
_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000000"

_REPO_ROOT = Path(__file__).resolve().parents[1]

_SLOW_TEST_MODULES = {
    Path("tests/unit/api/routers/test_agent_runs.py"),
}

_COMPONENT_TEST_PATH_PREFIXES = (
    Path("tests/unit/api"),
    Path("tests/unit/api_service"),
    Path("tests/component/api"),
)

_TEMPORAL_BOUNDARY_TEST_PATHS = {
    Path("tests/unit/workflows/temporal/test_agent_runtime_activities.py"),
    Path("tests/unit/workflows/temporal/test_agent_session_replayer.py"),
    Path("tests/unit/workflows/temporal/test_openclaw_activities.py"),
    Path("tests/unit/workflows/temporal/test_run_replayer.py"),
    Path("tests/unit/workflows/temporal/test_run_ungated_continuation_disposition.py"),
    Path("tests/unit/workflows/temporal/test_typed_activity_boundaries.py"),
    Path("tests/unit/workflows/temporal/workflows/test_run_dependency_signals.py"),
    Path(
        "tests/unit/workflows/temporal/workflows/"
        "test_run_dependency_wait_through_rerun.py"
    ),
    Path("tests/unit/workflows/temporal/workflows/test_run_scheduling.py"),
    Path("tests/unit/workflows/temporal/workflows/test_run_signals_updates.py"),
}

_TEMPORAL_BOUNDARY_TEST_PATH_PREFIXES = (Path("tests/unit/workflows/temporal"),)


@pytest.fixture(scope="session", autouse=True)
def global_test_settings():
    from moonmind.config.settings import settings

    settings.workflow.test_mode = True


def _relative_test_path(path: Path) -> Path:
    try:
        return path.resolve().relative_to(_REPO_ROOT)
    except ValueError:
        return path


def _path_is_relative_to(path: Path, prefix: Path) -> bool:
    try:
        return path.is_relative_to(prefix)
    except ValueError:
        return False


def _is_component_test_path(path: Path) -> bool:
    return any(
        _path_is_relative_to(path, prefix) for prefix in _COMPONENT_TEST_PATH_PREFIXES
    )


def _is_temporal_boundary_test_path(path: Path) -> bool:
    return path in _TEMPORAL_BOUNDARY_TEST_PATHS or any(
        _path_is_relative_to(path, prefix)
        for prefix in _TEMPORAL_BOUNDARY_TEST_PATH_PREFIXES
    )


def _is_reliability_journey_test_path(path: Path) -> bool:
    return path.parts[:3] == ("tests", "integration", "reliability")


def _item_has_marker(item: pytest.Item, name: str) -> bool:
    return item.get_closest_marker(name) is not None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Classify tests by runtime resource usage for impact-aware CI selection."""

    for item in items:
        path = Path(str(item.fspath))
        rel_path = _relative_test_path(path)

        is_component = _item_has_marker(item, "component") or _is_component_test_path(
            rel_path
        )
        is_temporal_boundary = _item_has_marker(
            item, "temporal_boundary"
        ) or _is_temporal_boundary_test_path(rel_path)
        is_reliability_journey = _item_has_marker(
            item, "reliability_journey"
        ) or _is_reliability_journey_test_path(rel_path)
        is_slow = _item_has_marker(item, "slow") or rel_path in _SLOW_TEST_MODULES

        if is_component:
            item.add_marker(pytest.mark.component)
        if is_temporal_boundary:
            item.add_marker(pytest.mark.temporal_boundary)
        if is_reliability_journey:
            item.add_marker(pytest.mark.reliability_journey)
        if is_slow:
            item.add_marker(pytest.mark.slow)

        if (
            len(rel_path.parts) >= 2
            and rel_path.parts[:2] == ("tests", "unit")
            and not is_component
            and not is_temporal_boundary
            and not is_reliability_journey
            and not is_slow
            and not _item_has_marker(item, "integration")
            and not _item_has_marker(item, "provider_verification")
            and not _item_has_marker(item, "requires_credentials")
        ):
            item.add_marker(pytest.mark.unit_fast)


@pytest.fixture
def disabled_env_keys(monkeypatch):
    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled", raising=False)
    monkeypatch.setattr(
        settings.oidc, "DEFAULT_USER_ID", _DEFAULT_USER_ID, raising=False
    )
    monkeypatch.setattr(
        settings.oidc, "DEFAULT_USER_EMAIL", "seed@example.com", raising=False
    )
    monkeypatch.setattr(settings.openai, "openai_api_key", "sk-test", raising=False)
    monkeypatch.setattr(settings.google, "google_api_key", "g-test", raising=False)
    yield


@pytest.fixture
def authenticated_mode(monkeypatch):
    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc", raising=False)
    yield


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Execute `@pytest.mark.asyncio` tests without requiring pytest-asyncio."""

    if "asyncio" not in pyfuncitem.keywords:
        return None

    test_function = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_function):
        return None

    signature = inspect.signature(test_function)
    bound_args = {
        name: pyfuncitem.funcargs[name]
        for name in signature.parameters
        if name in pyfuncitem.funcargs
    }

    async def _run_test_with_keepalive():
        stop_event = asyncio.Event()

        async def _keepalive() -> None:
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                # Keepalive cancellation is an expected shutdown path for this helper task.
                pass

        keepalive_task = asyncio.create_task(_keepalive())
        try:
            await test_function(**bound_args)
        finally:
            stop_event.set()
            await keepalive_task

    asyncio.run(_run_test_with_keepalive())
    return True


# ── atexit cleanup for orphaned Temporal test-server processes ──────────────
#
# ``WorkflowEnvironment.start_time_skipping()`` spawns a ``temporal-test-server``
# child. Normal shutdown uses atexit as a fallback; pytest-timeout's fatal
# thread callback must clean up before os._exit bypasses context managers.


def _kill_owned_temporal_servers() -> None:
    """Terminate ``temporal-test-server`` subprocesses owned by this process."""
    my_pid = os.getpid()
    try:
        out = subprocess.check_output(
            ["pgrep", "-P", str(my_pid), "-f", "temporal-test-server"],
            text=True,
            timeout=2,
        )
        for line in out.strip().splitlines():
            pid = int(line.strip())
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                # Best-effort: the child may already be gone or we may lack permission.
                pass
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        ValueError,
    ):
        # Best-effort cleanup: if `pgrep` is unavailable, fails, or output is unexpected,
        # we silently ignore it to avoid disrupting test shutdown.
        pass


atexit.register(_kill_owned_temporal_servers)


@pytest.hookimpl(tryfirst=True, optionalhook=True)
def pytest_timeout_set_timer(item, settings):
    """Keep pytest-timeout's fatal policy while cleaning this worker's servers."""
    if settings.method != "thread" and (
        settings.method != "signal"
        or threading.current_thread() is threading.main_thread()
    ):
        return None

    import pytest_timeout

    def timeout_owned_worker():
        if not settings.disable_debugger_detection and pytest_timeout.is_debugging():
            return
        try:
            _kill_owned_temporal_servers()
        finally:
            pytest_timeout.timeout_timer(item, settings)

    # Preserve pytest-timeout 2.4.0's thread timer and cancellation lifecycle.
    timer = threading.Timer(settings.timeout, timeout_owned_worker)
    timer.name = "%s %s" % (pytest_timeout.__name__, item.nodeid)

    def cancel():
        timer.cancel()
        timer.join()

    item.cancel_timeout = cancel
    timer.start()
    return True


@pytest.hookimpl(tryfirst=True, optionalhook=True)
def pytest_xdist_make_scheduler(config, log):
    """Finalize a crashed item once while retaining native file grouping."""
    if config.option.dist != "loadfile":
        return None

    from xdist.scheduler.loadfile import LoadFileScheduling

    class _CrashFinalizingLoadFileScheduling(LoadFileScheduling):
        """Compatibility repair for the pinned xdist 3.8.0 private state.

        Remove when a released dependency passes the crash-recovery subprocess
        cases with its native scheduler; see BackendTestSelection.md.
        """

        def schedule(self):
            restarting = self.collection is not None
            super().schedule()
            if restarting:
                # Match upstream #1328: a replacement needs a next item (or
                # shutdown) before it can execute its first queued test.
                for node in self.nodes:
                    self._reschedule(node)

        def remove_node(self, node):
            # pytest-xdist 3.8.0 LoadScopeScheduling.remove_node, with the
            # selected crash item completed before native requeue/reschedule.
            # DSession still receives that item and reports its original failure.
            workload = self.assigned_work.pop(node)
            if not self._pending_of(workload):
                return None

            for work_unit in workload.values():
                for nodeid, completed in work_unit.items():
                    if not completed:
                        crashitem = nodeid
                        work_unit[nodeid] = True
                        break
                else:
                    continue
                break
            else:
                raise RuntimeError(
                    "Unable to identify crashitem on a workload with pending items"
                )

            # Completed files would send an empty test list, leaving a
            # replacement waiting before any test timeout can start.
            self.workqueue.update(
                (scope, work_unit)
                for scope, work_unit in workload.items()
                if self._pending_of({scope: work_unit})
            )
            for remaining_node in self.assigned_work:
                self._reschedule(remaining_node)
            return crashitem

    return _CrashFinalizingLoadFileScheduling(config, log)
