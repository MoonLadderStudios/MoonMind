from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[4]
LAUNCHER_PATH = (
    REPO_ROOT / "services" / "temporal" / "scripts" / "start-workflow-worker-group.py"
)

spec = importlib.util.spec_from_file_location(
    "workflow_worker_group_launcher",
    LAUNCHER_PATH,
)
assert spec is not None
launcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = launcher
assert spec.loader is not None
spec.loader.exec_module(launcher)


class FakeProcess:
    def __init__(
        self,
        returncode: int | None = None,
        *,
        exit_on_terminate: bool = True,
    ) -> None:
        self.returncode = returncode
        self.exit_on_terminate = exit_on_terminate
        self.stdout: list[str] = []
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if self.exit_on_terminate:
            self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-worker", timeout)
        return self.returncode


def test_child_environments_preserve_independent_workflow_lane_defaults() -> None:
    base_env = {
        "TEMPORAL_WORKFLOW_TASK_QUEUE": "mm.workflow",
        "TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE": "mm.workflow.user.v2",
    }

    normal_env = launcher.build_child_environment(
        launcher.WorkerRole("normal-workflow-worker"),
        base_env,
    )
    merge_env = launcher.build_child_environment(
        launcher.WorkerRole("merge-automation-workflow-worker"),
        base_env,
    )

    assert normal_env["TEMPORAL_WORKER_FLEET"] == "workflow"
    assert normal_env["TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE"] == "mm.workflow.user.v2"
    assert normal_env["TEMPORAL_WORKFLOW_WORKER_CONCURRENCY"] == "8"
    assert normal_env["WORKER_HEALTHCHECK_ENABLED"] == "false"

    assert merge_env["TEMPORAL_WORKER_FLEET"] == "workflow"
    assert (
        merge_env["TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE"]
        == "mm.workflow.merge_automation"
    )
    assert merge_env["TEMPORAL_WORKFLOW_WORKER_CONCURRENCY"] == "2"
    assert merge_env["WORKER_HEALTHCHECK_ENABLED"] == "false"


def test_child_environments_honor_explicit_merge_queue_and_concurrency() -> None:
    env = launcher.build_child_environment(
        launcher.WorkerRole("merge-automation-workflow-worker"),
        {
            "TEMPORAL_MERGE_AUTOMATION_WORKFLOW_TASK_QUEUE": "custom.merge",
            "TEMPORAL_MERGE_AUTOMATION_WORKFLOW_WORKER_CONCURRENCY": "4",
        },
    )

    assert env["TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE"] == "custom.merge"
    assert env["TEMPORAL_WORKFLOW_WORKER_CONCURRENCY"] == "4"


def test_parent_health_endpoint_requires_both_children_alive() -> None:
    healthy_child = FakeProcess()
    state = launcher.GroupHealthState(children=[healthy_child, FakeProcess()])
    server = launcher.start_health_server(state, port=0)
    port = server.server_port
    try:
        response = urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5)
        assert response.status == 200

        healthy_child.returncode = 1
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
        else:
            raise AssertionError("expected unhealthy response")
    finally:
        server.shutdown()
        server.server_close()


def test_supervisor_exits_nonzero_and_stops_group_when_child_exits() -> None:
    processes = {
        "normal-workflow-worker": FakeProcess(),
        "merge-automation-workflow-worker": FakeProcess(returncode=7),
    }

    exit_code = launcher.supervise_workers(
        start_process=lambda role: processes[role.name],
        sleep=lambda _seconds: None,
        install_signal_handlers=False,
        health_port=0,
    )

    assert exit_code == 1
    assert processes["normal-workflow-worker"].terminated is True


def test_terminate_children_kills_process_after_grace_timeout() -> None:
    stuck = FakeProcess(exit_on_terminate=False)

    launcher._terminate_children(
        [(launcher.WorkerRole("normal-workflow-worker"), stuck)],
        timeout_seconds=0,
    )

    assert stuck.terminated is True
    assert stuck.killed is True
