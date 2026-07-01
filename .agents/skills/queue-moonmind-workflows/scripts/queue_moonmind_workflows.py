#!/usr/bin/env python3
"""Submit and verify child MoonMind workflow executions from a manifest."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import traceback
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

API_EXECUTIONS_ENDPOINT = "/api/executions"
IDEMPOTENCY_KEY_MAX_LENGTH = 128
TERMINAL_FAILURE_STATES = {"failed", "canceled"}


@dataclass(frozen=True)
class ChildExecution:
    ref: str
    request: dict[str, Any]
    idempotency_key: str | None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _request_is_task_shape(request: dict[str, Any]) -> bool:
    return "type" in request


def _request_idempotency_key(request: dict[str, Any]) -> str | None:
    if _request_is_task_shape(request):
        payload = request.get("payload")
        if isinstance(payload, dict):
            return _text(payload.get("idempotencyKey"))
        return None
    return _text(request.get("idempotencyKey"))


def _set_request_idempotency_key(request: dict[str, Any], key: str) -> None:
    if _request_is_task_shape(request):
        payload = request.setdefault("payload", {})
        if not isinstance(payload, dict):
            raise RuntimeError("task-shaped request.payload must be an object")
        payload["idempotencyKey"] = key
        return
    request["idempotencyKey"] = key


def _safe_key_fragment(value: str) -> str:
    fragment = re.sub(r"[^A-Za-z0-9_.#:-]+", "_", value).strip("_")
    return fragment[:32] or "child"


def _derive_idempotency_key(*, batch_scope: str, ref: str, request: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"scope": batch_scope, "ref": ref, "request": request},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    key = f"queue-moonmind-workflows:{_safe_key_fragment(ref)}:sha256:{digest}"
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        key = f"queue-moonmind-workflows:sha256:{digest}"
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise RuntimeError("generated idempotency key exceeds storage limit")
    return key


def _looks_like_parent_run_scope(value: str) -> bool:
    if re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        value,
    ):
        return True
    return bool(re.fullmatch(r"(?:mm:|task-)[A-Za-z0-9_.:-]+", value))


def _stable_scope_from_path(path: Path) -> str:
    resolved = path.resolve(strict=False)
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:24]
    return f"path:{digest}"


def _parent_run_scope_from_artifacts_dir(path: Path) -> str | None:
    resolved = path.resolve(strict=False)
    if resolved.name != "artifacts":
        return _stable_scope_from_path(resolved)
    parent_name = resolved.parent.name
    if parent_name and _looks_like_parent_run_scope(parent_name):
        return parent_name
    return _stable_scope_from_path(resolved.parent)


def _parent_run_scope() -> str | None:
    for env_key in (
        "MOONMIND_TASK_RUN_ID",
        "MOONMIND_TASK_WORKFLOW_ID",
        "MOONMIND_WORKFLOW_ID",
        "TEMPORAL_WORKFLOW_ID",
        "MOONMIND_RUN_ID",
        "TASK_RUN_ID",
    ):
        value = _text(os.getenv(env_key))
        if value:
            return value
    spool_path = _text(os.getenv("MOONMIND_SESSION_ARTIFACT_SPOOL_PATH"))
    if spool_path:
        return _parent_run_scope_from_artifacts_dir(Path(spool_path))
    return None


def _normalize_child_request(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError("child request must be an object")
    request = deepcopy(raw)
    if _request_is_task_shape(request):
        if not isinstance(request.get("payload"), dict):
            raise RuntimeError("task-shaped request.payload must be an object")
        return request
    workflow_type = _text(request.get("workflowType"))
    if not workflow_type:
        raise RuntimeError(
            "child request must be task-shaped or include workflowType"
        )
    return request


def _load_manifest(
    manifest_path: Path,
    *,
    batch_scope_override: str | None = None,
    allow_missing_idempotency: bool = False,
    max_workflows: int | None = None,
) -> tuple[list[ChildExecution], list[dict[str, Any]]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        workflows = payload
        manifest_scope = None
    elif isinstance(payload, dict):
        workflows = (
            payload["workflows"]
            if "workflows" in payload
            else payload.get("executions")
        )
        manifest_scope = _text(payload.get("batchScope"))
    else:
        raise RuntimeError("manifest must be a JSON object or list")

    if not isinstance(workflows, list):
        raise RuntimeError("manifest must contain workflows as a list")

    skipped: list[dict[str, Any]] = []
    if (
        max_workflows is not None
        and max_workflows >= 0
        and len(workflows) > max_workflows
    ):
        for item in workflows[max_workflows:]:
            ref = _text(item.get("ref")) if isinstance(item, dict) else None
            skipped.append(
                {"ref": ref or "(unknown)", "reason": "max_workflows_exceeded"}
            )
        workflows = workflows[:max_workflows]

    batch_scope = batch_scope_override or manifest_scope or _parent_run_scope()
    children: list[ChildExecution] = []
    for index, item in enumerate(workflows, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"workflow item {index} must be an object")
        ref = _text(item.get("ref")) or f"workflow-{index}"
        raw_request = item.get("request")
        if raw_request is None:
            raise RuntimeError(f"workflow item {ref} is missing request")
        request = _normalize_child_request(raw_request)
        key = _request_idempotency_key(request) or _text(item.get("idempotencyKey"))
        if key:
            _set_request_idempotency_key(request, key)
        elif batch_scope:
            key = _derive_idempotency_key(
                batch_scope=batch_scope,
                ref=ref,
                request=request,
            )
            _set_request_idempotency_key(request, key)
        elif not allow_missing_idempotency:
            raise RuntimeError(
                f"workflow item {ref} has no idempotencyKey and no batchScope"
            )
        children.append(ChildExecution(ref=ref, request=request, idempotency_key=key))
    return children, skipped


def _read_worker_token() -> str | None:
    token = _text(os.getenv("MOONMIND_WORKER_TOKEN"))
    if token:
        return token
    token_file = _text(os.getenv("MOONMIND_WORKER_TOKEN_FILE"))
    if not token_file:
        return None
    path = Path(token_file)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def _read_api_auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    auth_header = _text(os.getenv("MOONMIND_AUTH_HEADER"))
    if auth_header and auth_header.lower().startswith("authorization:"):
        auth_header = _text(auth_header.split(":", 1)[1])
    bearer_token = _text(
        os.getenv("MOONMIND_API_TOKEN")
        or os.getenv("MOONMIND_AUTH_TOKEN")
        or os.getenv("MOONMIND_BEARER_TOKEN")
    )
    api_key = _text(os.getenv("MOONMIND_API_KEY"))

    if auth_header:
        headers["Authorization"] = auth_header
    elif bearer_token:
        if bearer_token.lower().startswith("bearer "):
            headers["Authorization"] = bearer_token
        else:
            headers["Authorization"] = f"Bearer {bearer_token}"
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _request_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    headers.update(_read_api_auth_headers())
    token = _read_worker_token()
    if token:
        headers["X-MoonMind-Worker-Token"] = token
    for env_key in (
        "MOONMIND_TASK_WORKFLOW_ID",
        "MOONMIND_WORKFLOW_ID",
        "TEMPORAL_WORKFLOW_ID",
    ):
        value = _text(os.getenv(env_key))
        if value:
            headers["X-MoonMind-Task-Workflow-Id"] = value
            break
    for env_key in ("MOONMIND_AGENT_RUN_ID", "MOONMIND_RUN_ID", "AGENT_RUN_ID"):
        value = _text(os.getenv(env_key))
        if value:
            headers["X-MoonMind-Agent-Run-Identifier"] = value
            break
    return headers


def _workflow_id_from_response(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    return _text(data.get("workflowId") or data.get("id") or data.get("taskId"))


def _http_error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text.strip()
        return f"{exc.response.status_code} {exc.response.reason_phrase}: {body[:500]}"
    return str(exc)


async def _verify_execution_visible(
    client: httpx.AsyncClient,
    workflow_id: str,
    *,
    attempts: int,
    delay_seconds: float,
) -> dict[str, Any]:
    encoded = quote(workflow_id, safe="")
    last_error = "not checked"
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = await client.get(f"{API_EXECUTIONS_ENDPOINT}/{encoded}")
            response.raise_for_status()
            data = response.json()
            actual = _workflow_id_from_response(data)
            if actual != workflow_id:
                raise RuntimeError(
                    f"describe returned workflowId {actual!r}, expected {workflow_id!r}"
                )
            state = _text(data.get("state"))
            if state in TERMINAL_FAILURE_STATES:
                raise RuntimeError(
                    f"execution {workflow_id} is already terminal state {state}"
                )
            return data
        except Exception as exc:  # noqa: BLE001 - preserve last verification failure
            last_error = _http_error_text(exc)
            if attempt < max(1, attempts) and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
    raise RuntimeError(f"workflow {workflow_id} was not verified: {last_error}")


async def _submit_and_verify(
    children: list[ChildExecution],
    *,
    moonmind_url: str,
    verify_attempts: int,
    verify_delay_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    queued: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        base_url=moonmind_url.rstrip("/"),
        timeout=30.0,
        headers=_request_headers(),
    ) as client:
        for child in children:
            try:
                response = await client.post(API_EXECUTIONS_ENDPOINT, json=child.request)
                response.raise_for_status()
                data = response.json()
                workflow_id = _workflow_id_from_response(data)
                if not workflow_id:
                    raise RuntimeError("create response did not include workflowId")
                described = await _verify_execution_visible(
                    client,
                    workflow_id,
                    attempts=verify_attempts,
                    delay_seconds=verify_delay_seconds,
                )
                queued.append(
                    {
                        "ref": child.ref,
                        "workflowId": workflow_id,
                        "runId": described.get("runId") or data.get("runId"),
                        "state": described.get("state"),
                        "temporalStatus": described.get("temporalStatus"),
                        "idempotencyKey": child.idempotency_key,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - report per child
                errors.append(
                    {
                        "ref": child.ref,
                        "idempotencyKey": child.idempotency_key,
                        "error": _http_error_text(exc),
                    }
                )
    return queued, errors


def _session_artifact_spool_path() -> Path | None:
    raw = _text(os.getenv("MOONMIND_SESSION_ARTIFACT_SPOOL_PATH"))
    return Path(raw) if raw else None


def _resolve_artifacts_dir(raw_artifacts_dir: str) -> Path:
    raw = str(raw_artifacts_dir or "").strip()
    if not raw or Path(raw).parts in {("artifacts",), (".", "artifacts")}:
        spool = _session_artifact_spool_path()
        if spool is not None:
            return spool
    return Path(raw or "artifacts")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Submit and verify child MoonMind workflow executions."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--artifacts-dir", default="artifacts")
    parser.add_argument("--batch-scope", default=None)
    parser.add_argument("--max-workflows", type=int, default=25)
    parser.add_argument("--verify-attempts", type=int, default=5)
    parser.add_argument("--verify-delay-seconds", type=float, default=1.0)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--allow-missing-idempotency", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        raise RuntimeError(f"manifest file not found: {manifest_path}")

    children, skipped = _load_manifest(
        manifest_path,
        batch_scope_override=_text(args.batch_scope),
        allow_missing_idempotency=bool(args.allow_missing_idempotency),
        max_workflows=int(args.max_workflows) if args.max_workflows is not None else None,
    )
    if not children and not args.allow_empty:
        raise RuntimeError("manifest contains no workflows to queue")

    dry_run = bool(args.dry_run)
    queued: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if dry_run:
        queued = [
            {
                "ref": child.ref,
                "workflowId": None,
                "idempotencyKey": child.idempotency_key,
                "request": child.request,
            }
            for child in children
        ]
    else:
        moonmind_url = _text(os.getenv("MOONMIND_URL"))
        if not moonmind_url:
            raise RuntimeError("MOONMIND_URL is required to queue workflows")
        queued, errors = await _submit_and_verify(
            children,
            moonmind_url=moonmind_url,
            verify_attempts=max(1, int(args.verify_attempts)),
            verify_delay_seconds=max(0.0, float(args.verify_delay_seconds)),
        )

    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "manifest": str(manifest_path),
        "dryRun": dry_run,
        "requested": len(children),
        "submitted": 0 if dry_run else len(queued),
        "verified": 0 if dry_run else len(queued),
        "wouldSubmit": len(queued) if dry_run else 0,
        "queued": queued,
        "skipped": skipped,
        "errors": errors,
    }
    status = "success"
    if dry_run:
        status = "dry_run"
    elif skipped:
        status = "partial" if queued else "failed"
    elif errors:
        status = "partial" if queued else "failed"
    elif not queued and args.allow_empty:
        status = "no_op"
    payload["status"] = status

    artifacts_dir = _resolve_artifacts_dir(args.artifacts_dir)
    result_path = artifacts_dir / "queue-moonmind-workflows-result.json"
    _write_json(result_path, payload)
    if status in {"failed", "partial", "no_op", "dry_run"}:
        _write_json(
            artifacts_dir / "skill_outcome.json",
            {
                "schema_version": 1,
                "status": status,
                "resultPath": str(result_path),
                "requested": payload["requested"],
                "submitted": payload["submitted"],
                "verified": payload["verified"],
                "skipped": skipped,
                "errors": errors,
            },
        )

    print(json.dumps(payload, indent=2, sort_keys=True))
    print(
        "queued={submitted} verified={verified} skipped={skipped} errors={errors} "
        "status={status}".format(
            submitted=payload["submitted"],
            verified=payload["verified"],
            skipped=len(skipped),
            errors=len(errors),
            status=status,
        )
    )
    return 1 if errors or skipped else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001 - surface the actionable root cause
        print(f"error: queue-moonmind-workflows failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)
