#!/usr/bin/env python3
"""Dependency-free container-job CLI projected into isolated Omnigent hosts.

The API remains the authority for validation, workspace resolution, execution,
and evidence. This adapter only constructs the caller-scoped request and waits
for its durable terminal result; it never receives a Docker endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"succeeded", "failed", "canceled", "timed_out", "rejected"}


class CliError(RuntimeError):
    """Bounded actionable error safe to show inside the agent session."""


def _required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise CliError(
            f"{name} is required; run this command inside a MoonMind managed workflow"
        )
    return value


def _workspace() -> dict[str, str]:
    kind = str(
        os.environ.get("MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND")
        or "managed_runtime"
    ).strip()
    relative_path = str(
        os.environ.get("MOONMIND_CONTAINER_JOBS_WORKSPACE_RELATIVE_PATH") or "repo"
    ).strip()
    if kind == "managed_runtime":
        return {
            "kind": kind,
            "runtimeId": _required_env("MOONMIND_RUNTIME_ID"),
            "agentRunId": _required_env("MOONMIND_AGENT_RUN_ID"),
            "relativePath": relative_path,
        }
    if kind == "sandbox":
        return {
            "kind": kind,
            "workspaceId": _required_env("MOONMIND_CONTAINER_JOBS_WORKSPACE_ID"),
            "relativePath": relative_path,
        }
    raise CliError(f"unsupported container-job workspace kind: {kind}")


def _source() -> dict[str, str]:
    agent_run_id = _required_env("MOONMIND_AGENT_RUN_ID")
    session_id = _required_env("MOONMIND_CONTAINER_JOBS_SESSION_ID")
    kind = str(
        os.environ.get("MOONMIND_CONTAINER_JOBS_SOURCE_KIND") or "managed_session"
    ).strip()
    source = {
        "source": kind,
        "workflowId": str(
            os.environ.get("MOONMIND_TASK_WORKFLOW_ID") or agent_run_id
        ).strip(),
        "agentRunId": agent_run_id,
    }
    if kind == "managed_session":
        source["managedSessionId"] = session_id
    elif kind == "omnigent":
        source["omnigentConversationId"] = session_id
    else:
        raise CliError(f"unsupported container-job source kind: {kind}")
    step_id = str(os.environ.get("MOONMIND_STEP_ID") or "").strip()
    if step_id:
        source["stepId"] = step_id
    return source


def _call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    endpoint = _required_env("MOONMIND_CONTAINER_JOBS_MCP_URL").rstrip("/")
    request = urllib.request.Request(
        endpoint + "/tools/call",
        data=json.dumps({"tool": tool, "arguments": arguments}).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Authorization": (
                "Bearer " + _required_env("MOONMIND_CONTAINER_JOBS_BEARER_TOKEN")
            ),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    payload: Any = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            if exc.code >= 500 and attempt < 2:
                time.sleep(0.25 * (2**attempt))
                continue
            detail = ""
            try:
                body = json.loads(exc.read().decode("utf-8"))
                raw_detail = body.get("detail") if isinstance(body, dict) else None
                if isinstance(raw_detail, dict):
                    detail = str(
                        raw_detail.get("message") or raw_detail.get("code") or ""
                    )
            except (UnicodeDecodeError, json.JSONDecodeError):
                # The HTTP status remains authoritative when an intermediary
                # returns a non-JSON or undecodable diagnostic body.
                pass
            suffix = f": {detail}" if detail else ""
            raise CliError(
                f"MoonMind container tool '{tool}' returned HTTP {exc.code}{suffix}"
            ) from exc
        except (OSError, TimeoutError) as exc:
            if attempt < 2:
                time.sleep(0.25 * (2**attempt))
                continue
            raise CliError(
                f"MoonMind container tool '{tool}' is unavailable: {exc}"
            ) from exc
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise CliError(
            f"MoonMind container tool '{tool}' returned no structured result"
        )
    return result


def _idempotency_key(prefix: str, request_id: str | None = None) -> str:
    normalized_request_id = str(request_id or uuid.uuid4().hex).strip()
    if not normalized_request_id:
        raise CliError("container job request id must not be empty")
    raw = (
        f"{prefix}:{_required_env('MOONMIND_AGENT_RUN_ID')}:"
        f"{normalized_request_id}"
    )
    if len(raw) <= 255:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw[: 255 - len(digest) - 1] + ":" + digest


def _read_log_tail(
    job_id: str,
    *,
    max_pages: int = 100,
    max_lines: int = 250,
) -> tuple[str, ...]:
    """Read all bounded log pages while retaining the terminal tail."""

    cursor: str | None = None
    seen_cursors: set[str] = set()
    lines: deque[str] = deque(maxlen=max_lines)
    for _page in range(max_pages):
        arguments: dict[str, Any] = {"jobId": job_id, "limit": 500}
        if cursor:
            arguments["cursor"] = cursor
        payload = _call("container.logs", arguments)
        for entry in payload.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            entry_text = str(entry.get("text") or entry.get("message") or "")
            if entry_text:
                lines.append(entry_text)
        next_cursor = str(payload.get("nextCursor") or "").strip()
        if not next_cursor:
            return tuple(lines)
        if next_cursor in seen_cursors:
            raise CliError("container.logs returned a repeated cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise CliError(f"container.logs exceeded {max_pages} pages")


def _run(
    spec: dict[str, Any],
    *,
    timeout_seconds: int,
    prefix: str,
    request_id: str | None = None,
) -> int:
    spec = dict(spec)
    spec["workspaceRef"] = _workspace()
    submission = {
        "contractVersion": "v1",
        "idempotencyKey": _idempotency_key(prefix, request_id),
        "source": _source(),
        "spec": spec,
    }
    accepted = _call("container.submit", submission)
    job_id = str(accepted.get("jobId") or "").strip()
    if not job_id:
        raise CliError("container.submit returned no jobId")
    deadline = time.monotonic() + timeout_seconds + 120
    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = _call("container.status", {"jobId": job_id})
        state = str(snapshot.get("state") or "").strip().lower()
        if state in TERMINAL_STATES:
            break
        time.sleep(2)
    else:
        raise CliError(f"container job {job_id} did not reach a terminal state")

    try:
        for line in _read_log_tail(job_id):
            print(line)
    except CliError as exc:
        print(f"Warning: terminal logs could not be read: {exc}", file=sys.stderr)

    terminal = snapshot.get("terminal")
    terminal = terminal if isinstance(terminal, dict) else {}
    exit_code = terminal.get("exitCode")
    print(
        f"container job {job_id}: {state} "
        f"(exitCode={exit_code}, logsRef={snapshot.get('logsRef')}, "
        f"artifactsRef={snapshot.get('artifactsRef')})"
    )
    return 0 if state == "succeeded" and exit_code in {None, 0} else 1


def _python_tests(targets: list[str], timeout_seconds: int) -> int:
    spec = {
        "imageSourceRef": "moonmind-python-tests",
        "command": [
            "bash",
            "-lc",
            './tools/test_unit.sh --python-only -- "$@"',
            "moonmind-python-tests",
            *targets,
        ],
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
            {"name": "pytest-junit", "relativePath": "artifacts/pytest-unit.xml"}
        ],
    }
    return _run(spec, timeout_seconds=timeout_seconds, prefix="python-tests")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moonmind")
    command = parser.add_subparsers(dest="command", required=True)
    container = command.add_parser("container")
    operation = container.add_subparsers(dest="operation", required=True)
    python_tests = operation.add_parser("python-tests")
    python_tests.add_argument("targets", nargs="*")
    python_tests.add_argument("--timeout-seconds", type=int, default=3600)
    run = operation.add_parser("run")
    run.add_argument("--spec", required=True)
    run.add_argument("--request-id")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.operation == "python-tests":
            if not 1 <= args.timeout_seconds <= 86400:
                raise CliError("--timeout-seconds must be between 1 and 86400")
            return _python_tests(args.targets, args.timeout_seconds)
        spec_path = Path(args.spec)
        payload = json.loads(spec_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise CliError("container job spec must be a JSON object")
        timeout_seconds = int(payload.get("timeoutSeconds") or 3600)
        return _run(
            payload,
            timeout_seconds=timeout_seconds,
            prefix="container-run",
            request_id=args.request_id,
        )
    except (CliError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
