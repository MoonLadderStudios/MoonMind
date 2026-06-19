import asyncio
import inspect
import os
import signal
from functools import lru_cache
from pathlib import Path

import pytest

# Mirror the auth module's disabled-mode default user id to avoid importing
# api_service.auth during global pytest collection.
_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000000"

_REPO_ROOT = Path(__file__).resolve().parents[1]

_SLOW_TEST_MODULES = {
    Path("tests/unit/api/routers/test_agent_runs.py"),
}

_COMPONENT_PATTERNS = (
    "TestClient",
    "dependency_overrides",
)

_TEMPORAL_BOUNDARY_PATTERNS = (
    "WorkflowEnvironment",
    "Replayer",
    "temporalio.testing",
    "temporalio.worker",
)


@pytest.fixture(scope="session", autouse=True)
def global_test_settings():
    from moonmind.config.settings import settings

    settings.workflow.test_mode = True
    settings.workflow.enable_proposals = False


@lru_cache(maxsize=None)
def _test_source(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _relative_test_path(path: Path) -> Path:
    try:
        return path.resolve().relative_to(_REPO_ROOT)
    except ValueError:
        return path


def _item_has_marker(item: pytest.Item, name: str) -> bool:
    return item.get_closest_marker(name) is not None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Classify tests by runtime resource usage for impact-aware CI selection."""

    for item in items:
        path = Path(str(item.fspath))
        rel_path = _relative_test_path(path)
        source = _test_source(str(path))

        is_component = _item_has_marker(item, "component") or any(
            pattern in source for pattern in _COMPONENT_PATTERNS
        )
        is_temporal_boundary = _item_has_marker(item, "temporal_boundary") or any(
            pattern in source for pattern in _TEMPORAL_BOUNDARY_PATTERNS
        )
        is_slow = _item_has_marker(item, "slow") or rel_path in _SLOW_TEST_MODULES

        if is_component:
            item.add_marker(pytest.mark.component)
        if is_temporal_boundary:
            item.add_marker(pytest.mark.temporal_boundary)
        if is_slow:
            item.add_marker(pytest.mark.slow)

        if (
            len(rel_path.parts) >= 2
            and rel_path.parts[:2] == ("tests", "unit")
            and not is_component
            and not is_temporal_boundary
            and not is_slow
            and not _item_has_marker(item, "integration")
            and not _item_has_marker(item, "provider_verification")
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
def keycloak_mode(monkeypatch):
    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak", raising=False)
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
# child process.  Under pytest-xdist the worker process may exit before the
# async context manager's ``__aexit__`` runs, leaving the server alive.
# The parent pytest process then hangs waiting for the worker pipe to drain.
#
# An ``atexit`` handler fires on interpreter shutdown *inside each worker*,
# early enough to kill the orphaned child before the pipe blocks.
import atexit
import subprocess

def _kill_owned_temporal_servers() -> None:
    """Terminate ``temporal-test-server`` subprocesses owned by this process."""
    my_pid = os.getpid()
    try:
        out = subprocess.check_output(
            ["pgrep", "-P", str(my_pid), "-f", "temporal-test-server"],
            text=True,
        )
        for line in out.strip().splitlines():
            pid = int(line.strip())
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                # Best-effort: the child may already be gone or we may lack permission.
                pass
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        # Best-effort cleanup: if `pgrep` is unavailable, fails, or output is unexpected,
        # we silently ignore it to avoid disrupting test shutdown.
        pass

atexit.register(_kill_owned_temporal_servers)
