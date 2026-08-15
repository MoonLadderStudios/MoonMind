"""Managed-agent CLI for durable MoonMind container jobs."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from moonmind.schemas.container_job_models import (
    ContainerJobSpec,
    ContainerJobSubmitRequest,
)


TERMINAL_STATES = frozenset(
    {"succeeded", "failed", "canceled", "timed_out", "rejected"}
)


class ContainerJobCliError(RuntimeError):
    """Actionable failure returned to a managed agent."""


@dataclass(frozen=True, slots=True)
class ContainerJobResult:
    job_id: str
    state: str
    exit_code: int | None
    failure_class: str | None
    message: str | None
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


def _authorized_workspace(source: Mapping[str, str]) -> dict[str, str]:
    workspace_kind = str(
        source.get("MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND") or "managed_runtime"
    ).strip()
    relative_path = str(
        source.get("MOONMIND_CONTAINER_JOBS_WORKSPACE_RELATIVE_PATH") or "repo"
    ).strip()
    if workspace_kind == "managed_runtime":
        return {
            "kind": "managed_runtime",
            "runtimeId": _required_env(source, "MOONMIND_RUNTIME_ID"),
            "agentRunId": _required_env(source, "MOONMIND_AGENT_RUN_ID"),
            "relativePath": relative_path,
        }
    if workspace_kind == "sandbox":
        return {
            "kind": "sandbox",
            "workspaceId": _required_env(
                source, "MOONMIND_CONTAINER_JOBS_WORKSPACE_ID"
            ),
            "relativePath": relative_path,
        }
    raise ContainerJobCliError(
        f"unsupported container-job workspace kind: {workspace_kind}"
    )


def _source_correlation(source: Mapping[str, str]) -> dict[str, str]:
    agent_run_id = _required_env(source, "MOONMIND_AGENT_RUN_ID")
    workflow_id = str(
        source.get("MOONMIND_TASK_WORKFLOW_ID") or agent_run_id
    ).strip()
    session_id = _required_env(source, "MOONMIND_CONTAINER_JOBS_SESSION_ID")
    source_kind = str(
        source.get("MOONMIND_CONTAINER_JOBS_SOURCE_KIND") or "managed_session"
    ).strip()
    correlation = {
        "source": source_kind,
        "workflowId": workflow_id,
        "agentRunId": agent_run_id,
    }
    if source_kind == "managed_session":
        correlation["managedSessionId"] = session_id
    elif source_kind == "omnigent":
        correlation["omnigentConversationId"] = session_id
    else:
        raise ContainerJobCliError(
            f"unsupported container-job source kind: {source_kind}"
        )
    step_id = str(source.get("MOONMIND_STEP_ID") or "").strip()
    if step_id:
        correlation["stepId"] = step_id
    return correlation


def container_job_submission(
    spec: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build and validate one managed-session container-job submission.

    The spec file controls workload intent only. MoonMind overwrites the
    workspace and correlation identity from the admitted managed session so a
    file in the repository cannot select another run's workspace.
    """

    source = os.environ if env is None else env
    agent_run_id = _required_env(source, "MOONMIND_AGENT_RUN_ID")
    normalized_request_id = str(request_id or uuid4().hex).strip()
    if not normalized_request_id:
        raise ContainerJobCliError("container job request id must not be empty")
    normalized_spec = dict(spec)
    normalized_spec["workspaceRef"] = _authorized_workspace(source)
    try:
        validated_spec = ContainerJobSpec.model_validate(normalized_spec)
        idempotency_components = (
            f"container-run:{agent_run_id}:{normalized_request_id}"
        )
        idempotency_key = idempotency_components
        if len(idempotency_key) > 255:
            digest = hashlib.sha256(idempotency_components.encode("utf-8")).hexdigest()
            readable_prefix = f"container-run:{agent_run_id}:"
            idempotency_key = (
                readable_prefix[: 255 - len(digest) - 1] + ":" + digest
            )
        submission = ContainerJobSubmitRequest.model_validate(
            {
                "contractVersion": "v1",
                "idempotencyKey": idempotency_key,
                "source": _source_correlation(source),
                "spec": validated_spec.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                ),
            }
        )
    except ValueError as exc:
        raise ContainerJobCliError(f"invalid container job spec: {exc}") from exc
    return submission.model_dump(mode="json", by_alias=True, exclude_none=True)


def load_container_job_spec(path: str | Path) -> dict[str, Any]:
    """Load one JSON job spec without accepting a full submission envelope."""

    spec_path = Path(path)
    try:
        payload = json.loads(spec_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContainerJobCliError(
            f"container job spec could not be read: {spec_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ContainerJobCliError(
            f"container job spec is not valid JSON: {spec_path}: {exc.msg}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ContainerJobCliError("container job spec must be a JSON object")
    if "spec" in payload or "owner" in payload or "source" in payload:
        raise ContainerJobCliError(
            "container job spec must contain workload fields only, not a "
            "submission envelope"
        )
    return dict(payload)


def python_test_submission(
    targets: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int = 3600,
) -> dict[str, Any]:
    """Build the canonical submission for Python tests in the active workspace."""

    source = os.environ if env is None else env
    agent_run_id = _required_env(source, "MOONMIND_AGENT_RUN_ID")
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
        "source": _source_correlation(source),
        "spec": {
            "imageSourceRef": "moonmind-python-tests",
            "workspaceRef": _authorized_workspace(source),
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


def run_container_job(
    spec: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    request_id: str | None = None,
    poll_seconds: float = 2.0,
    client: ContainerJobMcpClient | None = None,
) -> ContainerJobResult:
    """Submit one job, wait durably, and return authoritative evidence refs."""

    source = os.environ if env is None else env
    submission = container_job_submission(
        spec,
        env=source,
        request_id=request_id,
    )
    timeout_seconds = int(submission["spec"]["timeoutSeconds"])
    owned_client = client is None
    active_client = client or ContainerJobMcpClient(
        endpoint=_mcp_endpoint(source),
        bearer_token=_mcp_bearer_token(source),
    )
    try:
        accepted = active_client.call(
            "container.submit",
            submission,
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
        return ContainerJobResult(
            job_id=job_id,
            state=state,
            exit_code=(
                int(terminal_payload["exitCode"])
                if terminal_payload.get("exitCode") is not None
                else None
            ),
            failure_class=(
                str(terminal_payload.get("failureClass") or "").strip() or None
            ),
            message=str(terminal_payload.get("message") or "").strip() or None,
            logs_ref=str(snapshot.get("logsRef") or "").strip() or None,
            artifacts_ref=str(snapshot.get("artifactsRef") or "").strip() or None,
            log_tail=log_tail,
            log_error=log_error,
        )
    finally:
        if owned_client:
            active_client.close()


def run_python_tests(
    targets: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int = 3600,
    poll_seconds: float = 2.0,
    client: ContainerJobMcpClient | None = None,
) -> ContainerJobResult:
    """Run the canonical Python-test job through the generic submission path."""

    source = os.environ if env is None else env
    submission = python_test_submission(
        targets,
        env=source,
        timeout_seconds=timeout_seconds,
    )
    return run_container_job(
        submission["spec"],
        env=source,
        request_id=submission["idempotencyKey"].rsplit(":", 1)[-1],
        poll_seconds=poll_seconds,
        client=client,
    )


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
