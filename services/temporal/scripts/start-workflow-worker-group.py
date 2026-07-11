#!/usr/bin/env python
"""Supervise the default workflow worker lanes in one Compose service."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Iterable, Mapping, Protocol, Sequence

START_WORKER_SCRIPT = "/app/services/temporal/scripts/start-worker.sh"
DEFAULT_HEALTHCHECK_PORT = 8080
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 20.0
CHILD_HEALTHCHECK_PORTS = {
    "normal-workflow-worker": 8081,
    "merge-automation-workflow-worker": 8082,
}
CHILD_PROMETHEUS_BIND_ADDRESSES = {
    "normal-workflow-worker": "127.0.0.1:9090",
    "merge-automation-workflow-worker": "127.0.0.1:9091",
}


class WorkerProcess(Protocol):
    stdout: object

    def poll(self) -> int | None:
        pass

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        pass


@dataclass(frozen=True, slots=True)
class WorkerRole:
    name: str


WORKER_ROLES = (
    WorkerRole("normal-workflow-worker"),
    WorkerRole("merge-automation-workflow-worker"),
)


@dataclass(slots=True)
class GroupHealthState:
    children: Sequence[WorkerProcess]
    child_health_urls: Sequence[str] = ()
    shutting_down: bool = False

    def is_live(self) -> bool:
        return not self.shutting_down and all(
            child.poll() is None for child in self.children
        )

    def is_ready(self) -> bool:
        if not self.is_live():
            return False
        children = [
            payload
            for url in self.child_health_urls
            if (payload := _read_child_readiness(url)) is not None
        ]
        if len(children) != len(self.child_health_urls):
            return False
        fingerprints = {
            str(child.get("registryFingerprint"))
            for child in children
            if child.get("registryFingerprint")
        }
        build_ids = {
            str(child.get("buildId"))
            for child in children
            if child.get("buildId")
        }
        return len(fingerprints) <= 1 and len(build_ids) <= 1

    def is_healthy(self) -> bool:
        """Backward-compatible internal name; Compose now probes readiness."""

        return self.is_ready()


def _env_default(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name)
    if value is None or value == "":
        return default
    return value


def build_child_environment(
    role: WorkerRole,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env["TEMPORAL_WORKER_FLEET"] = "workflow"
    env["WORKER_HEALTHCHECK_ENABLED"] = "true"
    env["WORKER_HEALTHCHECK_PORT"] = str(_child_healthcheck_port(role))
    env["MOONMIND_PROMETHEUS_BIND_ADDRESS"] = _child_prometheus_bind_address(
        role,
        env.get("MOONMIND_PROMETHEUS_BIND_ADDRESS"),
    )

    if role.name == "normal-workflow-worker":
        env["TEMPORAL_WORKFLOW_WORKER_CONCURRENCY"] = _env_default(
            env,
            "TEMPORAL_WORKFLOW_WORKER_CONCURRENCY",
            "8",
        )
        return env

    if role.name == "merge-automation-workflow-worker":
        env["TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE"] = _env_default(
            env,
            "TEMPORAL_MERGE_AUTOMATION_WORKFLOW_TASK_QUEUE",
            "mm.workflow.merge_automation",
        )
        env["TEMPORAL_WORKFLOW_WORKER_CONCURRENCY"] = _env_default(
            env,
            "TEMPORAL_MERGE_AUTOMATION_WORKFLOW_WORKER_CONCURRENCY",
            "2",
        )
        return env

    raise ValueError(f"Unsupported worker role: {role.name}")


def _worker_command() -> list[str]:
    return ["/bin/sh", START_WORKER_SCRIPT]


def _child_healthcheck_port(role: WorkerRole) -> int:
    try:
        return CHILD_HEALTHCHECK_PORTS[role.name]
    except KeyError as exc:
        raise ValueError(f"Unsupported worker role: {role.name}") from exc


def _child_prometheus_bind_address(
    role: WorkerRole,
    inherited_bind_address: str | None,
) -> str:
    if role.name == "normal-workflow-worker":
        return inherited_bind_address or CHILD_PROMETHEUS_BIND_ADDRESSES[role.name]
    if role.name == "merge-automation-workflow-worker":
        if inherited_bind_address:
            host, separator, port = inherited_bind_address.rpartition(":")
            if separator and port.isdigit():
                return f"{host}:{int(port) + 1}"
        return CHILD_PROMETHEUS_BIND_ADDRESSES[role.name]
    raise ValueError(f"Unsupported worker role: {role.name}")


def _child_health_url(role: WorkerRole) -> str:
    return f"http://127.0.0.1:{_child_healthcheck_port(role)}/readyz"


def start_child_process(role: WorkerRole) -> WorkerProcess:
    return subprocess.Popen(
        _worker_command(),
        env=build_child_environment(role),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def _stream_child_output(role_name: str, process: WorkerProcess) -> threading.Thread:
    def _stream() -> None:
        stream = process.stdout
        if stream is None:
            return
        for line in stream:  # type: ignore[union-attr]
            print(_format_child_log_line(role_name, line), end="", flush=True)

    thread = threading.Thread(
        target=_stream,
        name=f"{role_name}-log-stream",
        daemon=True,
    )
    thread.start()
    return thread


def _format_child_log_line(role_name: str, line: str) -> str:
    trailing_newline = "\n" if line.endswith("\n") else ""
    raw_line = line[:-1] if trailing_newline else line
    try:
        payload = json.loads(raw_line)
    except json.JSONDecodeError:
        return f"[{role_name}] {line}"
    if not isinstance(payload, dict):
        return f"[{role_name}] {line}"
    payload.setdefault("worker_role", role_name)
    return json.dumps(payload, separators=(",", ":")) + trailing_newline


def _read_child_readiness(
    url: str, *, timeout_seconds: float = 0.5
) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            payload = json.loads(response.read())
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, urllib.error.URLError, TimeoutError):
        return None


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        state: GroupHealthState = self.server.state  # type: ignore[attr-defined]
        readiness = self.path == "/readyz"
        healthy = state.is_ready() if readiness else state.is_live()
        status = 200 if healthy else 503
        body: dict[str, object] = {
            "status": (
                "ready" if readiness and healthy else "ok" if healthy else "unhealthy"
            ),
            "live": state.is_live(),
            "ready": state.is_ready(),
            "workers": len(state.children),
            "shutting_down": state.shutting_down,
        }
        if readiness:
            children = [
                payload
                for url in state.child_health_urls
                if (payload := _read_child_readiness(url)) is not None
            ]
            body["children"] = children
            workflow_types = sorted(
                {
                    str(item)
                    for child in children
                    for item in child.get("workflowTypes", [])
                }
            )
            task_queues = sorted(
                {
                    str(item)
                    for child in children
                    for item in child.get("taskQueues", [])
                }
            )
            body["workflowTypes"] = workflow_types
            body["taskQueues"] = task_queues
            body["registryFingerprints"] = sorted(
                {
                    str(child.get("registryFingerprint"))
                    for child in children
                    if child.get("registryFingerprint")
                }
            )
            body["buildIds"] = sorted(
                {
                    str(child.get("buildId"))
                    for child in children
                    if child.get("buildId")
                }
            )
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _healthcheck_port(env: Mapping[str, str] | None = None) -> int:
    raw = (os.environ if env is None else env).get("WORKER_HEALTHCHECK_PORT", "")
    return int(raw) if raw.strip().isdigit() else DEFAULT_HEALTHCHECK_PORT


def start_health_server(
    state: GroupHealthState,
    *,
    port: int | None = None,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(
        ("0.0.0.0", DEFAULT_HEALTHCHECK_PORT if port is None else port),
        _HealthHandler,
    )
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(
        target=server.serve_forever,
        name="workflow-worker-group-healthcheck",
        daemon=True,
    )
    thread.start()
    print(
        f"[workflow-worker-group] healthcheck listening on port {server.server_port}",
        flush=True,
    )
    return server


def _terminate_children(
    children: Iterable[tuple[WorkerRole, WorkerProcess]],
    *,
    timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
) -> None:
    live_children = [
        (role, process) for role, process in children if process.poll() is None
    ]
    for role, process in live_children:
        print(f"[workflow-worker-group] terminating {role.name}", flush=True)
        try:
            process.terminate()
        except OSError as exc:
            print(
                f"[workflow-worker-group] ignored terminate failure for "
                f"{role.name}: {exc}",
                flush=True,
            )

    deadline = time.monotonic() + timeout_seconds
    for role, process in live_children:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            print(f"[workflow-worker-group] killing {role.name}", flush=True)
            try:
                process.kill()
            except OSError as exc:
                print(
                    f"[workflow-worker-group] ignored kill failure for "
                    f"{role.name}: {exc}",
                    flush=True,
                )
            try:
                process.wait(timeout=5.0)
            except OSError as exc:
                print(
                    f"[workflow-worker-group] ignored post-kill wait failure for "
                    f"{role.name}: {exc}",
                    flush=True,
                )
        except OSError as exc:
            print(
                f"[workflow-worker-group] ignored wait failure for {role.name}: {exc}",
                flush=True,
            )


def supervise_workers(
    *,
    start_process: Callable[[WorkerRole], WorkerProcess] = start_child_process,
    sleep: Callable[[float], None] = time.sleep,
    install_signal_handlers: bool = True,
    poll_interval_seconds: float = 0.5,
    health_port: int | None = None,
) -> int:
    children: list[tuple[WorkerRole, WorkerProcess]] = []
    state = GroupHealthState(children=[])
    health_server: ThreadingHTTPServer | None = None

    def _request_shutdown(_signum: int, _frame: object) -> None:
        state.shutting_down = True

    if install_signal_handlers:
        signal.signal(signal.SIGTERM, _request_shutdown)
        signal.signal(signal.SIGINT, _request_shutdown)

    exit_code = 0
    try:
        for role in WORKER_ROLES:
            process = start_process(role)
            children.append((role, process))
            print(f"[workflow-worker-group] started {role.name}", flush=True)
            _stream_child_output(role.name, process)

        state.children = [process for _role, process in children]
        state.child_health_urls = [
            _child_health_url(role) for role, _process in children
        ]
        health_server = start_health_server(
            state,
            port=_healthcheck_port() if health_port is None else health_port,
        )

        while not state.shutting_down:
            exited = [
                (role, process.poll())
                for role, process in children
                if process.poll() is not None
            ]
            if exited:
                role, code = exited[0]
                print(
                    f"[workflow-worker-group] {role.name} exited unexpectedly "
                    f"with code {code}; stopping group",
                    flush=True,
                )
                state.shutting_down = True
                exit_code = 1
                break
            sleep(poll_interval_seconds)
    finally:
        state.shutting_down = True
        if health_server is not None:
            health_server.shutdown()
            health_server.server_close()
        _terminate_children(children)

    return exit_code


def main() -> int:
    return supervise_workers()


if __name__ == "__main__":
    raise SystemExit(main())
