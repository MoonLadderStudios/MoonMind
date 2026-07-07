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
from typing import Callable, Iterable, Mapping, Protocol, Sequence

START_WORKER_SCRIPT = "/app/services/temporal/scripts/start-worker.sh"
DEFAULT_HEALTHCHECK_PORT = 8080
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 20.0


class WorkerProcess(Protocol):
    stdout: object

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


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
    shutting_down: bool = False

    def is_healthy(self) -> bool:
        return not self.shutting_down and all(
            child.poll() is None for child in self.children
        )


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
    env["WORKER_HEALTHCHECK_ENABLED"] = "false"

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
            print(f"[{role_name}] {line}", end="", flush=True)

    thread = threading.Thread(
        target=_stream,
        name=f"{role_name}-log-stream",
        daemon=True,
    )
    thread.start()
    return thread


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        state: GroupHealthState = self.server.state  # type: ignore[attr-defined]
        healthy = state.is_healthy()
        status = 200 if healthy else 503
        payload = json.dumps(
            {
                "status": "ok" if healthy else "unhealthy",
                "workers": len(state.children),
                "shutting_down": state.shutting_down,
            }
        ).encode("utf-8")
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
        process.terminate()

    deadline = time.monotonic() + timeout_seconds
    for role, process in live_children:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            print(f"[workflow-worker-group] killing {role.name}", flush=True)
            process.kill()
            process.wait(timeout=5.0)


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
