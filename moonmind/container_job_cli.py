"""Managed-agent CLI for durable MoonMind container jobs."""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx


TERMINAL_STATES = frozenset(
    {"succeeded", "failed", "canceled", "timed_out", "rejected"}
)


class ContainerJobCliError(RuntimeError):
    """Actionable failure returned to a managed agent."""


@dataclass(frozen=True, slots=True)
class PythonTestResult:
    job_id: str
    state: str
    exit_code: int | None
    logs_ref: str | None
    artifacts_ref: str | None
    log_tail: tuple[str, ...] = ()
    log_error: str | None = None


class ContainerJobMcpClient:
    """Small synchronous client for MoonMind's bounded MCP helper endpoint."""

    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/") + "/tools/call"
        headers = {"accept": "application/json"}
        normalized_token = str(bearer_token or "").strip()
        if normalized_token:
            headers["authorization"] = f"Bearer {normalized_token}"
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers=headers,
        )

    def close(self) -> None:
        self._client.close()

    def call(self, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        payload: Any = None
        for attempt in range(3):
            try:
                response = self._client.post(
                    self._endpoint,
                    json={"tool": tool, "arguments": dict(arguments)},
                )
                response.raise_for_status()
                payload = response.json()
                break
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise ContainerJobCliError(
                        f"MoonMind container tool '{tool}' is unavailable: {exc}"
                    ) from exc
                time.sleep(0.25 * (2**attempt))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500 and attempt < 2:
                    time.sleep(0.25 * (2**attempt))
                    continue
                detail = ""
                try:
                    error_payload = exc.response.json()
                    if isinstance(error_payload, Mapping):
                        raw_detail = error_payload.get("detail")
                        detail = (
                            str(
                                raw_detail.get("message")
                                or raw_detail.get("code")
                                or ""
                            )
                            if isinstance(raw_detail, Mapping)
                            else str(raw_detail or "")
                        )
                except ValueError:
                    # Error detail is optional; the HTTP status remains the
                    # authoritative failure when the response body is not JSON.
                    pass
                suffix = f": {detail}" if detail else ""
                raise ContainerJobCliError(
                    f"MoonMind container tool '{tool}' returned "
                    f"HTTP {exc.response.status_code}{suffix}"
                ) from exc
            except ValueError as exc:
                raise ContainerJobCliError(
                    f"MoonMind container tool '{tool}' returned invalid JSON"
                ) from exc
        result = payload.get("result") if isinstance(payload, Mapping) else None
        if not isinstance(result, Mapping):
            raise ContainerJobCliError(
                f"MoonMind container tool '{tool}' returned no structured result"
            )
        return dict(result)


def _required_env(source: Mapping[str, str], key: str) -> str:
    value = str(source.get(key) or "").strip()
    if not value:
        raise ContainerJobCliError(
            f"{key} is required; run this command inside a MoonMind managed workflow"
        )
    return value


def _mcp_endpoint(source: Mapping[str, str]) -> str:
    explicit = str(source.get("MOONMIND_CONTAINER_JOBS_MCP_URL") or "").strip()
    if explicit:
        return explicit
    return _required_env(source, "MOONMIND_URL").rstrip("/") + "/mcp"


def _mcp_bearer_token(source: Mapping[str, str]) -> str | None:
    return (
        str(source.get("MOONMIND_CONTAINER_JOBS_BEARER_TOKEN") or "").strip()
        or None
    )


def python_test_submission(
    targets: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int = 3600,
) -> dict[str, Any]:
    """Build the canonical submission for Python tests in the active workspace."""

    source = os.environ if env is None else env
    agent_run_id = _required_env(source, "MOONMIND_AGENT_RUN_ID")
    runtime_id = _required_env(source, "MOONMIND_RUNTIME_ID")
    workflow_id = str(source.get("MOONMIND_TASK_WORKFLOW_ID") or agent_run_id).strip()
    test_targets = [str(target).strip() for target in targets if str(target).strip()]
    command = [
        "bash",
        "-lc",
        "./tools/test_unit.sh --python-only -- \"$@\"",
        "moonmind-python-tests",
        *test_targets,
    ]
    return {
        "contractVersion": "v1",
        "idempotencyKey": f"python-tests:{agent_run_id}:{uuid4().hex}",
        "source": {
            "source": "managed_session",
            "workflowId": workflow_id,
            "managedSessionId": str(
                source.get("MOONMIND_CONTAINER_JOBS_SESSION_ID") or ""
            ).strip()
            or None,
            "agentRunId": agent_run_id,
        },
        "spec": {
            "imageSourceRef": "moonmind-python-tests",
            "workspaceRef": {
                "kind": "managed_runtime",
                "runtimeId": runtime_id,
                "agentRunId": agent_run_id,
                "relativePath": "repo",
            },
            "command": command,
            "workdir": "/workspace",
            "networkMode": "bridge",
            "environment": [
                {"name": "MOONMIND_FORCE_LOCAL_TESTS", "value": "1"},
                {
                    "name": "MOONMIND_PYTEST_JUNITXML",
                    "value": "artifacts/pytest-unit.xml",
                },
                {"name": "PYTHONPATH", "value": "/workspace"},
            ],
            "resources": {"cpuMillis": 4000, "memoryMiB": 4096, "pids": 512},
            "timeoutSeconds": timeout_seconds,
            "outputs": [
                {
                    "name": "pytest-junit",
                    "relativePath": "artifacts/pytest-unit.xml",
                }
            ],
        },
    }


def run_python_tests(
    targets: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int = 3600,
    poll_seconds: float = 2.0,
    client: ContainerJobMcpClient | None = None,
) -> PythonTestResult:
    """Submit Python tests, wait durably, and return authoritative evidence refs."""

    source = os.environ if env is None else env
    owned_client = client is None
    active_client = client or ContainerJobMcpClient(
        endpoint=_mcp_endpoint(source),
        bearer_token=_mcp_bearer_token(source),
    )
    try:
        accepted = active_client.call(
            "container.submit",
            python_test_submission(
                targets, env=source, timeout_seconds=timeout_seconds
            ),
        )
        job_id = str(accepted.get("jobId") or "").strip()
        if not job_id:
            raise ContainerJobCliError("container.submit returned no jobId")
        deadline = time.monotonic() + timeout_seconds + 120
        snapshot: dict[str, Any] = {}
        while time.monotonic() < deadline:
            snapshot = active_client.call("container.status", {"jobId": job_id})
            state = str(snapshot.get("state") or "").strip().lower()
            if state in TERMINAL_STATES:
                break
            time.sleep(max(0.1, poll_seconds))
        else:
            raise ContainerJobCliError(
                f"container job {job_id} did not reach a terminal state"
            )
        terminal = snapshot.get("terminal")
        terminal_payload = terminal if isinstance(terminal, Mapping) else {}
        log_tail: tuple[str, ...] = ()
        log_error: str | None = None
        try:
            log_tail = _read_log_tail(active_client, job_id)
        except ContainerJobCliError as exc:
            # Log retrieval is auxiliary evidence and must not overwrite the
            # authoritative job result.
            log_error = str(exc)
        return PythonTestResult(
            job_id=job_id,
            state=state,
            exit_code=(
                int(terminal_payload["exitCode"])
                if terminal_payload.get("exitCode") is not None
                else None
            ),
            logs_ref=str(snapshot.get("logsRef") or "").strip() or None,
            artifacts_ref=str(snapshot.get("artifactsRef") or "").strip() or None,
            log_tail=log_tail,
            log_error=log_error,
        )
    finally:
        if owned_client:
            active_client.close()


def _read_log_tail(
    client: ContainerJobMcpClient,
    job_id: str,
    *,
    max_pages: int = 100,
    max_lines: int = 250,
) -> tuple[str, ...]:
    """Read a bounded tail while following the service's bounded cursors."""

    cursor: str | None = None
    seen_cursors: set[str] = set()
    tail: deque[str] = deque(maxlen=max_lines)
    for _page_number in range(max_pages):
        arguments: dict[str, Any] = {"jobId": job_id, "limit": 500}
        if cursor is not None:
            arguments["cursor"] = cursor
        page = client.call("container.logs", arguments)
        entries = page.get("entries")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                stream = str(entry.get("stream") or "log").strip()
                message = str(entry.get("text") or "").rstrip("\n")
                if message:
                    tail.append(f"[{stream}] {message}")
        next_cursor = str(page.get("nextCursor") or "").strip() or None
        if next_cursor is None:
            break
        if next_cursor in seen_cursors:
            raise ContainerJobCliError(
                f"container.logs returned a repeated cursor for job {job_id}"
            )
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return tuple(tail)
