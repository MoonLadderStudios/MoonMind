from __future__ import annotations

import importlib.util
import json
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
        raise_on_terminate: bool = False,
        raise_on_kill: bool = False,
        raise_on_wait: bool = False,
    ) -> None:
        self.returncode = returncode
        self.exit_on_terminate = exit_on_terminate
        self.raise_on_terminate = raise_on_terminate
        self.raise_on_kill = raise_on_kill
        self.raise_on_wait = raise_on_wait
        self.stdout: list[str] = []
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        if self.raise_on_terminate:
            raise OSError("already exited")
        self.terminated = True
        if self.exit_on_terminate:
            self.returncode = 0

    def kill(self) -> None:
        if self.raise_on_kill:
            raise OSError("already exited")
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.raise_on_wait:
            raise OSError("already reaped")
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
    assert normal_env["WORKER_HEALTHCHECK_ENABLED"] == "true"
    assert normal_env["WORKER_HEALTHCHECK_PORT"] == "8081"
    assert normal_env["MOONMIND_PROMETHEUS_BIND_ADDRESS"] == "127.0.0.1:9090"

    assert merge_env["TEMPORAL_WORKER_FLEET"] == "workflow"
    assert (
        merge_env["TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE"]
        == "mm.workflow.merge_automation"
    )
    assert merge_env["TEMPORAL_WORKFLOW_WORKER_CONCURRENCY"] == "2"
    assert merge_env["WORKER_HEALTHCHECK_ENABLED"] == "true"
    assert merge_env["WORKER_HEALTHCHECK_PORT"] == "8082"
    assert merge_env["MOONMIND_PROMETHEUS_BIND_ADDRESS"] == "127.0.0.1:9091"


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


def test_merge_worker_prometheus_bind_address_is_distinct_when_inherited() -> None:
    env = launcher.build_child_environment(
        launcher.WorkerRole("merge-automation-workflow-worker"),
        {
            "MOONMIND_PROMETHEUS_BIND_ADDRESS": "0.0.0.0:9200",
        },
    )

    assert env["MOONMIND_PROMETHEUS_BIND_ADDRESS"] == "0.0.0.0:9201"


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


def test_parent_health_endpoint_probes_child_health_urls() -> None:
    child_server = launcher.start_health_server(
        launcher.GroupHealthState(children=[]),
        port=0,
    )
    parent_state = launcher.GroupHealthState(
        children=[FakeProcess()],
        child_health_urls=[f"http://127.0.0.1:{child_server.server_port}/readyz"],
    )
    parent_server = launcher.start_health_server(parent_state, port=0)
    try:
        response = urllib.request.urlopen(
            f"http://127.0.0.1:{parent_server.server_port}/readyz",
            timeout=5,
        )
        assert response.status == 200

        child_server.shutdown()
        child_server.server_close()

        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{parent_server.server_port}/readyz",
                timeout=5,
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
        else:
            raise AssertionError("expected unhealthy response")
    finally:
        parent_server.shutdown()
        parent_server.server_close()


def test_group_readiness_rejects_mixed_worker_registry_identity(monkeypatch) -> None:
    payloads = iter(
        [
            {"ready": True, "registryFingerprint": "sha256:new", "buildId": "new"},
            {"ready": True, "registryFingerprint": "sha256:old", "buildId": "old"},
        ]
    )
    monkeypatch.setattr(launcher, "_read_child_readiness", lambda _url: next(payloads))
    state = launcher.GroupHealthState(
        children=[FakeProcess(), FakeProcess()],
        child_health_urls=["http://child-one/readyz", "http://child-two/readyz"],
    )

    assert state.is_ready() is False


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


def test_terminate_children_ignores_os_errors_from_exited_processes() -> None:
    already_exited = FakeProcess(
        exit_on_terminate=False,
        raise_on_terminate=True,
        raise_on_kill=True,
        raise_on_wait=True,
    )

    launcher._terminate_children(
        [(launcher.WorkerRole("normal-workflow-worker"), already_exited)],
        timeout_seconds=0,
    )

    assert already_exited.terminated is False


def test_structured_child_log_lines_preserve_json_and_add_role() -> None:
    formatted = launcher._format_child_log_line(
        "normal-workflow-worker",
        '{"event":"started","level":"info"}\n',
    )

    payload = json.loads(formatted)
    assert payload == {
        "event": "started",
        "level": "info",
        "worker_role": "normal-workflow-worker",
    }


def test_unstructured_child_log_lines_keep_human_role_prefix() -> None:
    formatted = launcher._format_child_log_line(
        "merge-automation-workflow-worker",
        "plain log line\n",
    )

    assert formatted == "[merge-automation-workflow-worker] plain log line\n"
