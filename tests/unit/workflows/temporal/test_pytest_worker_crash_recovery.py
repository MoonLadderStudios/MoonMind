"""Exercise pytest worker failure against real, case-owned Temporal servers."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from temporalio.api.workflowservice.v1 import GetSystemInfoRequest
from temporalio.testing import WorkflowEnvironment

pytestmark = pytest.mark.slow

CONFTEST = Path(__file__).resolve().parents[4] / "tests/conftest.py"

COMMON = """
import json, os, socket
from pathlib import Path
import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

@workflow.defn
class ProbeWorkflow:
    @workflow.run
    async def run(self, value: str) -> str:
        return value

@pytest.fixture(scope="module")
def module_socket():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        yield listener.getsockname()[1]

@pytest.fixture(scope="class")
def class_directory(tmp_path_factory):
    return str(tmp_path_factory.mktemp("class-owned"))

async def operation(name, module_socket, *, wait=False, class_directory=None):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue="owned-" + name, workflows=[ProbeWorkflow],
                          workflow_runner=UnsandboxedWorkflowRunner()):
            value = await env.client.execute_workflow(ProbeWorkflow.run, name,
                          id="owned-" + name, task_queue="owned-" + name)
            assert value == name
            children = []
            for path in Path("/proc").iterdir():
                if not path.name.isdigit():
                    continue
                try:
                    parent = next(int(line.split()[1]) for line in (path / "status").read_text().splitlines()
                                  if line.startswith("PPid:"))
                    command = (path / "cmdline").read_bytes().split(bytes([0]))
                    is_server = (b"temporal-test-server" in command[0]
                                 or command[1:3] == [b"server", b"start-dev"])
                    if parent == os.getpid() and is_server:
                        children.append(int(path.name))
                except (OSError, StopIteration):
                    pass
            record = {"case": name, "worker": os.environ["PYTEST_XDIST_WORKER"],
                      "pid": os.getpid(), "servers": children, "module_socket": module_socket,
                      "class_directory": class_directory, "result": value}
            directory = Path(os.environ["WORKER_CRASH_REGRESSION_RECEIPTS"])
            (directory / f"{name}-{os.getpid()}.json").write_text(json.dumps(record))
            if wait:
                import asyncio
                await asyncio.Event().wait()
"""

CONTROL = """
import pytest
from probe import module_socket, operation

@pytest.mark.asyncio
async def test_real_temporal_control(module_socket):
    await operation("control", module_socket)
"""

CRASH = """
import pytest
from probe import module_socket, operation

@pytest.mark.asyncio
@pytest.mark.timeout(3, func_only=True)
async def test_thread_timeout_after_real_operation(module_socket):
    await operation("crash", module_socket, wait=True)
"""

PENDING = """
import pytest
from probe import class_directory, module_socket, operation

@pytest.mark.asyncio
async def test_completed_before_crash(module_socket):
    await operation("before", module_socket)

@pytest.mark.asyncio
@pytest.mark.timeout(3, func_only=True)
async def test_thread_timeout_after_real_operation(module_socket):
    await operation("crash", module_socket, wait=True)

class TestPending:
    @pytest.mark.asyncio
    async def test_same_file_sibling_after_crash(self, module_socket, class_directory):
        await operation("pending", module_socket, class_directory=class_directory)
"""

DEBUGGER = """
import asyncio
import pytest
import pytest_timeout
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner
from probe import ProbeWorkflow, module_socket

@pytest.mark.asyncio
@pytest.mark.timeout(1, func_only=True)
async def test_debugger_suppression_keeps_real_temporal_alive(module_socket, monkeypatch):
    # Only the external debugger-presence decision is supplied by the fixture.
    monkeypatch.setattr(pytest_timeout, "is_debugging", lambda: True)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue="owned-debugger", workflows=[ProbeWorkflow],
                          workflow_runner=UnsandboxedWorkflowRunner()):
            await asyncio.sleep(1.3)
            value = await env.client.execute_workflow(ProbeWorkflow.run, "debugger-live",
                          id="owned-debugger", task_queue="owned-debugger")
            assert value == "debugger-live"
"""


def _run_fixture(
    tmp_path: Path,
    crash_source: str,
    *,
    completed_files: int = 0,
    quiet: bool = False,
    local_server: bool = False,
    dev_server_existing_path: Path | None = None,
) -> tuple[subprocess.CompletedProcess, list[dict]]:
    assert Path(
        "/proc"
    ).is_dir(), "Use the supported Linux test container for process ownership"
    tests = tmp_path / "tests"
    tests.mkdir()
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    shutil.copyfile(CONFTEST, tests / "conftest.py")
    common = COMMON
    if local_server:
        start = (
            f"start_local(dev_server_existing_path={str(dev_server_existing_path)!r})"
            if dev_server_existing_path is not None
            else "start_local()"
        )
        common = common.replace("start_time_skipping()", start)
        crash_source = crash_source.replace("start_time_skipping()", start)
    (tests / "probe.py").write_text(common)
    (tests / "test_a_control.py").write_text(CONTROL)
    for index in range(completed_files):
        (tests / f"test_b_prelude_{index}.py").write_text(
            CONTROL.replace("control", f"prelude-{index}").replace(
                f"test_real_temporal_prelude-{index}",
                f"test_real_temporal_prelude_{index}",
            )
        )
    (tests / "test_z_crash.py").write_text(crash_source)
    env = {
        **os.environ,
        "MOONMIND_ALLOW_LIVE_TEMPORAL_IN_TESTS": "1",
        "WORKER_CRASH_REGRESSION_RECEIPTS": str(receipts),
    }
    arguments = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q" if quiet else "-vv",
        "--tb=short",
        "--timeout",
        "10",
        "--timeout-method=thread",
        "-n",
        "1" if completed_files else "2",
        "--dist",
        "loadfile",
    ]
    process = subprocess.Popen(
        arguments,
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        try:
            output, _ = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                output, _ = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate(timeout=3)
            pytest.fail(
                "Crashed test was not finalized once; remaining file-scoped coverage stalled:\n"
                + output
            )
        records = [
            json.loads(path.read_text()) for path in sorted(receipts.glob("*.json"))
        ]
        active_owned = []
        for record in records:
            for pid in record["servers"]:
                try:
                    command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                    if b"temporal-test-server" in command[0] or (
                        command[1:3] == [b"server", b"start-dev"]
                    ):
                        active_owned.append(pid)
                except FileNotFoundError:
                    # An exited owned server needs no further cleanup.
                    pass
        assert (
            not active_owned
        ), f"Fatal timeout left owned Temporal servers alive: {active_owned}\n{output}"
        return (
            subprocess.CompletedProcess(arguments, process.returncode, output, ""),
            records,
        )
    finally:
        # This session was created solely for the fixture; no other test shares it.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


@pytest.mark.parametrize(
    "with_pending_sibling, local_server", [(False, False), (True, False), (True, True)]
)
def test_thread_timeout_preserves_failure_and_file_scoped_remaining_coverage(
    tmp_path: Path, with_pending_sibling: bool, local_server: bool
) -> None:
    result, records = _run_fixture(
        tmp_path,
        PENDING if with_pending_sibling else CRASH,
        quiet=not with_pending_sibling,
        local_server=local_server,
    )
    assert result.returncode == 1, result.stdout
    if with_pending_sibling:
        assert "scheduling tests via" in result.stdout and "LoadFile" in result.stdout
    assert (
        "crashed while running 'tests/test_z_crash.py::test_thread_timeout_after_real_operation'"
        in result.stdout
    )
    expected = (
        {"control", "crash", "before", "pending"}
        if with_pending_sibling
        else {"control", "crash"}
    )
    assert len(records) == len(expected), result.stdout
    by_case = {record["case"]: record for record in records}
    assert set(by_case) == expected
    assert all(
        record["servers"] and record["result"] == record["case"] for record in records
    )
    assert (
        "1 failed, 3 passed" if with_pending_sibling else "1 failed, 1 passed"
    ) in result.stdout
    if with_pending_sibling:
        assert by_case["before"]["worker"] == by_case["crash"]["worker"]
        assert by_case["before"]["module_socket"] == by_case["crash"]["module_socket"]
        assert by_case["pending"]["worker"] != by_case["crash"]["worker"]
        assert by_case["pending"]["class_directory"]


@pytest.mark.parametrize("local_server", [False, True])
def test_thread_timeout_honors_debugger_suppression(
    tmp_path: Path, local_server: bool
) -> None:
    result, records = _run_fixture(tmp_path, DEBUGGER, local_server=local_server)
    assert result.returncode == 0, result.stdout
    assert "2 passed" in result.stdout
    assert [record["case"] for record in records] == ["control"]


@pytest.mark.asyncio
@pytest.mark.parametrize("debugger", [False, True])
async def test_custom_cli_cleanup_stays_inside_worker_parent(
    tmp_path: Path, debugger: bool
) -> None:
    # Keep a real CLI server outside the disposable worker's ownership fence.
    async with await WorkflowEnvironment.start_local() as control:
        pids = subprocess.check_output(
            ["pgrep", "-P", str(os.getpid()), "-f", " server start-dev"], text=True
        ).split()
        assert len(pids) == 1
        executable = Path(f"/proc/{pids[0]}/exe").resolve(strict=True)
        custom_cli = tmp_path / "custom temporal executable"
        custom_cli.symlink_to(executable)
        fixture_root = tmp_path / "worker"
        fixture_root.mkdir()
        result, records = await asyncio.to_thread(
            _run_fixture,
            fixture_root,
            DEBUGGER if debugger else PENDING,
            local_server=True,
            dev_server_existing_path=custom_cli,
        )
        assert result.returncode == (0 if debugger else 1), result.stdout
        assert ("2 passed" if debugger else "1 failed, 3 passed") in result.stdout
        assert len(records) == (1 if debugger else 4), result.stdout
        await control.client.workflow_service.get_system_info(GetSystemInfoRequest())


def test_completed_files_do_not_leave_replacement_waiting_for_empty_work(
    tmp_path: Path,
) -> None:
    result, records = _run_fixture(tmp_path, CRASH, completed_files=3)
    assert result.returncode == 1, result.stdout
    assert "1 failed, 4 passed" in result.stdout
    assert len(records) == 5, result.stdout
    assert {record["case"] for record in records} == {
        "control",
        "prelude-0",
        "prelude-1",
        "prelude-2",
        "crash",
    }
    assert all(
        record["servers"] and record["result"] == record["case"] for record in records
    )
    assert len({record["worker"] for record in records}) == 1


def test_replacement_starts_single_test_files_while_more_work_is_queued(
    tmp_path: Path,
) -> None:
    """The larger crash file runs before the single-test files under loadfile."""
    result, records = _run_fixture(tmp_path, PENDING, completed_files=3)
    assert result.returncode == 1, result.stdout
    assert "1 failed, 6 passed" in result.stdout
    assert len(records) == 7, result.stdout
    assert {record["case"] for record in records} == {
        "control",
        "prelude-0",
        "prelude-1",
        "prelude-2",
        "before",
        "crash",
        "pending",
    }
