"""Run one Omnigent streaming execution inside a Temporal activity.

MM-994: enforce Omnigent cancellation, cleanup, security, and v1 boundaries.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from temporalio import activity

from moonmind.omnigent.settings import (
    OMNIGENT_DISABLED_MESSAGE,
    build_omnigent_gate,
    resolved_api_token,
    resolved_default_agent_name,
    resolved_server_url,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.utils.logging import (
    SecretRedactor,
    redact_sensitive_payload,
    redact_sensitive_text,
)
from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
)

_ACTIVE_STATUSES = {"created", "launching", "starting", "running", "waiting", "active"}
_FAILED_STATUSES = {"failed", "error", "errored"}
_COMPLETED_STATUSES = {"completed", "complete", "idle", "done", "succeeded", "success"}
_CANCELED_STATUSES = {"canceled", "cancelled", "stopped", "interrupted"}
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(token|secret|password|cookie|credential|authorization|auth_header|api[_-]?key)"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+/=-]+|ghp_[A-Za-z0-9_]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{16,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


class OmnigentExecutionError(RuntimeError):
    """Non-transport Omnigent execution failure with canonical classification."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: str = "integration_error",
        provider_error_code: str = "omnigent_execution_error",
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.provider_error_code = provider_error_code


async def run_omnigent_execution(request: AgentExecutionRequest) -> AgentRunResult:
    """Execute an Omnigent run via the streaming activity boundary."""

    gate = build_omnigent_gate()
    if not gate.enabled:
        raise RuntimeError(
            f"{OMNIGENT_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    token = resolved_api_token()
    redactor = SecretRedactor.from_environ(
        extra_secrets=[token],
        placeholder="[REDACTED]",
    )
    try:
        spec = _build_execution_spec(request, redactor=redactor)
        client = OmnigentHttpClient(
            base_url=resolved_server_url(),
            api_token=token,
            timeout_seconds=spec.timeout_seconds,
            stream_timeout_seconds=spec.stream_timeout_seconds,
        )
        return await _execute_with_client(
            request,
            client=client,
            spec=spec,
            redactor=redactor,
        )
    except asyncio.CancelledError:
        raise
    except OmnigentClientError as exc:
        return _failure_result(
            request,
            summary=str(exc),
            failure_class=exc.failure_class,
            provider_error_code=str(exc.status_code or "omnigent_http_error"),
            diagnostics={"clientError": exc.diagnostics()},
            redactor=redactor,
        )
    except OmnigentExecutionError as exc:
        return _failure_result(
            request,
            summary=str(exc),
            failure_class=exc.failure_class,
            provider_error_code=exc.provider_error_code,
            diagnostics={"error": str(exc)},
            redactor=redactor,
        )
    except asyncio.TimeoutError as exc:
        return _failure_result(
            request,
            summary="Omnigent execution timed out.",
            failure_class="timed_out",
            provider_error_code="omnigent_timed_out",
            diagnostics={"error": redact_sensitive_text(redactor.scrub(str(exc)))},
            redactor=redactor,
        )


async def _execute_with_client(
    request: AgentExecutionRequest,
    *,
    client: OmnigentHttpClient,
    spec: "_OmnigentExecutionSpec",
    redactor: SecretRedactor,
) -> AgentRunResult:
    diagnostics: dict[str, Any] = {
        "provider": "omnigent",
        "sourceIssue": "MM-994",
        "endpointRef": spec.endpoint_ref,
        "errors": [],
        "transport": {"sseConnected": False, "eventsCaptured": 0},
        "capture": {},
    }
    session_id = ""
    stream_task: asyncio.Task[list[dict[str, Any]]] | None = None
    stream_events: list[dict[str, Any]] = []
    final_snapshot: dict[str, Any] = {}

    try:
        session_payload = await _build_session_payload(client, request, spec)
        session_response = await client.create_session(session_payload)
        session_id = _extract_session_id(session_response)
        diagnostics["omnigentSessionId"] = session_id
        diagnostics["omnigentAgentId"] = session_payload.get("agent_id")

        initial_snapshot = await _safe_get_session(client, session_id, diagnostics)
        final_snapshot = initial_snapshot
        stream_task = asyncio.create_task(
            _collect_stream(client, session_id, stream_events)
        )
        first_message = _build_first_message_event(request, spec)
        await client.post_event(session_id, first_message)

        status = _normalize_status(initial_snapshot)
        while status not in {"completed", "failed", "canceled"}:
            await asyncio.sleep(spec.poll_interval_seconds)
            snapshot = await client.get_session(session_id)
            status = _normalize_status(snapshot)
            final_snapshot = snapshot
            activity.heartbeat(
                {
                    "providerName": "omnigent",
                    "omnigentSessionId": session_id,
                    "normalizedStatus": status,
                    "eventsCaptured": len(stream_events),
                }
            )

        if stream_task is not None:
            stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await stream_task
        diagnostics["transport"]["eventsCaptured"] = len(stream_events)

        harvest = await _harvest_artifacts(
            client,
            session_id,
            spec=spec,
            diagnostics=diagnostics,
            redactor=redactor,
        )
        await _maybe_delete_session(client, session_id, spec=spec, diagnostics=diagnostics)

        summary = _summary_from_snapshot(final_snapshot) or _summary_from_events(stream_events)
        if status == "failed":
            return _failure_result(
                request,
                summary=summary or "Omnigent execution failed.",
                failure_class="execution_error",
                provider_error_code="omnigent_execution_failed",
                diagnostics=diagnostics,
                redactor=redactor,
                extra_metadata=_result_metadata(spec, session_id, status, harvest),
            )
        if status == "canceled":
            return _failure_result(
                request,
                summary=summary or "Omnigent execution was canceled.",
                failure_class="canceled",
                provider_error_code="omnigent_canceled",
                diagnostics=diagnostics,
                redactor=redactor,
                extra_metadata=_result_metadata(spec, session_id, status, harvest),
            )
        diagnostics_ref = _write_json_artifact(
            spec,
            "runtime.omnigent.diagnostics.json",
            diagnostics,
            redactor=redactor,
        )
        return AgentRunResult(
            outputRefs=harvest.output_refs,
            summary=_compact_summary(summary or "Omnigent execution completed.", redactor),
            diagnosticsRef=diagnostics_ref,
            metadata=_result_metadata(spec, session_id, status, harvest),
        )
    except asyncio.CancelledError:
        if session_id:
            await _cancel_session(client, session_id, spec=spec, diagnostics=diagnostics)
            await _harvest_artifacts(
                client,
                session_id,
                spec=spec,
                diagnostics=diagnostics,
                redactor=redactor,
            )
        raise
    except Exception:
        if session_id:
            with suppress(Exception):
                await _maybe_delete_session(
                    client,
                    session_id,
                    spec=spec,
                    diagnostics=diagnostics,
                )
        raise
    finally:
        if stream_task is not None and not stream_task.done():
            stream_task.cancel()


async def _cancel_session(
    client: OmnigentHttpClient,
    session_id: str,
    *,
    spec: "_OmnigentExecutionSpec",
    diagnostics: dict[str, Any],
) -> None:
    diagnostics["cancellation"] = {"interruptSent": False, "stopSessionSent": False}
    with suppress(Exception):
        await client.interrupt(session_id)
        diagnostics["cancellation"]["interruptSent"] = True
    await asyncio.sleep(spec.cancel_grace_seconds)
    with suppress(Exception):
        snapshot = await _safe_get_session(client, session_id, diagnostics)
        if _normalize_status(snapshot) in {"launching", "running", "awaiting_approval"}:
            await client.stop_session(session_id)
            diagnostics["cancellation"]["stopSessionSent"] = True


async def _build_session_payload(
    client: OmnigentHttpClient,
    request: AgentExecutionRequest,
    spec: "_OmnigentExecutionSpec",
) -> dict[str, Any]:
    agent_id = spec.agent_id or await _resolve_agent_id(client, spec.agent_name)
    if not agent_id:
        raise OmnigentExecutionError(
            "No Omnigent agent target could be resolved.",
            failure_class="integration_error",
            provider_error_code="omnigent_agent_unresolved",
        )

    payload: dict[str, Any] = {
        "agent_id": agent_id,
        "title": spec.title,
        "labels": {
            "moonmind.correlation_id": request.correlation_id,
            "moonmind.idempotency_key": request.idempotency_key,
            "moonmind.source_issue": "MM-994",
        },
        "host_type": spec.host_type,
        "workspace": spec.workspace,
        "model_override": spec.model_override,
        "reasoning_effort": spec.reasoning_effort,
        "terminal_launch_args": list(spec.terminal_launch_args),
    }
    if spec.host_type == "external":
        payload["host_id"] = spec.host_id
    return {key: value for key, value in payload.items() if value is not None}


async def _resolve_agent_id(client: OmnigentHttpClient, agent_name: str) -> str:
    if not agent_name:
        return ""
    agents = await client.list_agents()
    for agent in agents:
        if str(agent.get("name") or "").strip() == agent_name:
            return str(agent.get("id") or "").strip()
    raise OmnigentExecutionError(
        f"Unknown Omnigent agent name: {agent_name}",
        failure_class="user_error",
        provider_error_code="omnigent_unknown_agent",
    )


async def _collect_stream(
    client: OmnigentHttpClient,
    session_id: str,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    async for event in client.stream_events(session_id):
        events.append(
            {
                "schemaVersion": "v1",
                "capturedAt": datetime.now(tz=UTC).isoformat(),
                "provider": "omnigent",
                "omnigentSessionId": session_id,
                "eventType": str(event.get("type") or event.get("event") or "unknown"),
                "payload": redact_sensitive_payload(event),
                "redaction": {"applied": True},
            }
        )
    return events


async def _harvest_artifacts(
    client: OmnigentHttpClient,
    session_id: str,
    *,
    spec: "_OmnigentExecutionSpec",
    diagnostics: dict[str, Any],
    redactor: SecretRedactor,
) -> "_HarvestResult":
    output_refs: list[str] = []
    changed_files_count = 0
    session_files_count = 0

    if spec.capture_changed_files:
        try:
            changed = await client.list_changed_files(session_id)
            changed_ref = _write_json_artifact(
                spec,
                "output.workspace.changed_files.index.json",
                changed,
                redactor=redactor,
            )
            output_refs.append(changed_ref)
            paths = _changed_file_paths(changed)
            changed_files_count = len(paths)
            for path in paths[: spec.max_harvest_files]:
                with suppress(Exception):
                    content = await client.get_workspace_file(session_id, path)
                    output_refs.append(
                        _write_bytes_artifact(
                            spec,
                            f"output.workspace.files/{_safe_artifact_name(path)}.current",
                            content,
                            redactor=redactor,
                        )
                    )
        except Exception as exc:
            diagnostics.setdefault("errors", []).append(
                {
                    "phase": "changed_files_harvest",
                    "failureClass": "system_error",
                    "message": redact_sensitive_text(redactor.scrub(str(exc))),
                }
            )

    if spec.capture_session_files:
        try:
            session_files = await client.list_session_files(session_id)
            session_ref = _write_json_artifact(
                spec,
                "output.omnigent.session_files.index.json",
                session_files,
                redactor=redactor,
            )
            output_refs.append(session_ref)
            files = _session_file_entries(session_files)
            session_files_count = len(files)
            for item in files[: spec.max_harvest_files]:
                file_id = str(item.get("id") or item.get("file_id") or "").strip()
                if not file_id:
                    continue
                with suppress(Exception):
                    content = await client.get_session_file_content(session_id, file_id)
                    output_refs.append(
                        _write_bytes_artifact(
                            spec,
                            "output.omnigent.session_files/"
                            f"{_safe_artifact_name(file_id)}/content",
                            content,
                            redactor=redactor,
                        )
                    )
        except Exception as exc:
            diagnostics.setdefault("errors", []).append(
                {
                    "phase": "session_files_harvest",
                    "failureClass": "system_error",
                    "message": redact_sensitive_text(redactor.scrub(str(exc))),
                }
            )

    diagnostics["capture"].update(
        {
            "changedFiles": changed_files_count,
            "sessionFiles": session_files_count,
            "patchSource": spec.patch_source,
            "patchProduced": False,
        }
    )
    manifest_ref = _write_json_artifact(
        spec,
        "output.workspace.manifest.json",
        {
            "provider": "omnigent",
            "omnigentSessionId": session_id,
            "outputRefs": output_refs,
            "capture": diagnostics["capture"],
        },
        redactor=redactor,
    )
    output_refs.append(manifest_ref)
    return _HarvestResult(output_refs=output_refs, manifest_ref=manifest_ref)


async def _maybe_delete_session(
    client: OmnigentHttpClient,
    session_id: str,
    *,
    spec: "_OmnigentExecutionSpec",
    diagnostics: dict[str, Any],
) -> None:
    cleanup = {
        "deleteRequested": spec.delete_after_harvest,
        "deleteBranch": spec.delete_branch,
        "sessionPreserved": not spec.delete_after_harvest,
    }
    diagnostics["cleanup"] = cleanup
    if not spec.delete_after_harvest:
        return
    await client.delete_session(session_id, delete_branch=spec.delete_branch)
    cleanup["sessionPreserved"] = False


def _build_execution_spec(
    request: AgentExecutionRequest,
    *,
    redactor: SecretRedactor,
) -> "_OmnigentExecutionSpec":
    params = dict(request.parameters or {})
    omnigent = _mapping(params.get("omnigent"))
    _reject_secret_payload(params, path="parameters")
    _reject_secret_text(request.correlation_id, path="correlationId")
    _reject_secret_text(request.idempotency_key, path="idempotencyKey")
    _reject_v1_non_goals(omnigent)

    session = _mapping(omnigent.get("session"))
    agent = _mapping(omnigent.get("agent"))
    prompt = _mapping(omnigent.get("prompt"))
    capture = _mapping(omnigent.get("capture"))
    policy = _mapping(omnigent.get("policy")) | _mapping(params.get("policy"))

    host_type = str(session.get("hostType") or "managed").strip().lower()
    host_id = _clean(session.get("hostId"))
    workspace = _clean(session.get("workspace"))
    if host_type == "managed" and not workspace:
        workspace = _managed_workspace_from_request(request, params)
    if host_type not in {"managed", "external"}:
        raise OmnigentExecutionError(
            "Omnigent session.hostType must be managed or external.",
            failure_class="user_error",
            provider_error_code="omnigent_invalid_host_type",
        )
    if host_type == "managed" and host_id:
        raise OmnigentExecutionError(
            "Omnigent managed sessions must not include session.hostId.",
            failure_class="user_error",
            provider_error_code="omnigent_invalid_managed_host_id",
        )
    if host_type == "managed" and workspace and _looks_like_local_path(workspace):
        raise OmnigentExecutionError(
            "Omnigent managed session.workspace must be a repository URL, not a local path.",
            failure_class="user_error",
            provider_error_code="omnigent_invalid_managed_workspace",
        )
    if host_type == "external" and not host_id:
        raise OmnigentExecutionError(
            "Omnigent external sessions require session.hostId.",
            failure_class="user_error",
            provider_error_code="omnigent_missing_external_host_id",
        )

    delete_after = _truthy(capture.get("deleteOmnigentSessionAfterHarvest"))
    delete_branch = _truthy(capture.get("delete_branch") or capture.get("deleteBranch"))
    allow_delete_branch = _truthy(
        policy.get("allowDestructiveBranchCleanup")
        or policy.get("allow_delete_branch")
        or capture.get("allowDeleteBranch")
    )
    if delete_branch and not allow_delete_branch:
        raise OmnigentExecutionError(
            "Omnigent delete_branch=true requires explicit operator or workflow policy.",
            failure_class="user_error",
            provider_error_code="omnigent_delete_branch_policy_required",
        )

    timeout_seconds = _float_env("OMNIGENT_REQUEST_TIMEOUT_SECONDS", 60.0)
    stream_timeout_seconds = _float_env("OMNIGENT_STREAM_HEARTBEAT_TIMEOUT_SECONDS", 120.0)
    return _OmnigentExecutionSpec(
        endpoint_ref=_clean(omnigent.get("endpointRef")) or "default",
        agent_id=_clean(agent.get("agentId")),
        agent_name=_clean(agent.get("agentName")) or resolved_default_agent_name(),
        title=_clean(session.get("title")) or _clean(params.get("title")) or "MoonMind Agent Task",
        prompt_text=_clean(prompt.get("text")) or _clean(params.get("description")),
        include_idempotency_marker=not (prompt.get("includeIdempotencyMarker") is False),
        host_type=host_type,
        host_id=host_id,
        workspace=workspace,
        model_override=_clean(session.get("modelOverride")) or None,
        reasoning_effort=_clean(session.get("reasoningEffort")) or None,
        terminal_launch_args=tuple(
            str(item).strip()
            for item in session.get("terminalLaunchArgs", ())
            if str(item).strip()
        ),
        capture_changed_files=capture.get("changedFiles") is not False,
        capture_session_files=capture.get("sessionFiles") is not False,
        delete_after_harvest=delete_after,
        delete_branch=delete_branch,
        patch_source=_clean(capture.get("patchSource")) or "github_pr_or_host_helper",
        timeout_seconds=timeout_seconds,
        stream_timeout_seconds=stream_timeout_seconds,
        cancel_grace_seconds=max(0.0, _float_env("OMNIGENT_CANCEL_GRACE_SECONDS", 5.0)),
        poll_interval_seconds=max(0.1, _float_env("OMNIGENT_POLL_INTERVAL_SECONDS", 2.0)),
        max_harvest_files=max(0, int(_float_env("OMNIGENT_MAX_HARVEST_FILES", 50.0))),
        artifact_dir=_artifact_dir(request, redactor=redactor),
    )


def _build_first_message_event(
    request: AgentExecutionRequest,
    spec: "_OmnigentExecutionSpec",
) -> dict[str, Any]:
    text = spec.prompt_text or request.instruction_ref or ""
    if not text and request.input_refs:
        text = "Input artifact refs: " + ", ".join(request.input_refs)
    if not text:
        text = f"Delegated MoonMind run {request.correlation_id}"
    payload = {"role": "user", "content": [{"type": "input_text", "text": text}]}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if spec.include_idempotency_marker:
        payload["content"].append(
            {
                "type": "input_text",
                "text": (
                    "\nMoonMind-Omnigent-Run:\n"
                    f"  correlation_id: {request.correlation_id}\n"
                    f"  idempotency_key: {request.idempotency_key}\n"
                    f"  first_message_digest: sha256:{digest}\n"
                ),
            }
        )
    return {"type": "message", "data": payload}


def _failure_result(
    request: AgentExecutionRequest,
    *,
    summary: str,
    failure_class: str,
    provider_error_code: str,
    diagnostics: Mapping[str, Any],
    redactor: SecretRedactor,
    extra_metadata: Mapping[str, Any] | None = None,
) -> AgentRunResult:
    spec = _minimal_artifact_spec(request)
    diagnostics_ref = _write_json_artifact(
        spec,
        "runtime.omnigent.diagnostics.json",
        diagnostics,
        redactor=redactor,
    )
    metadata = {
        "providerName": "omnigent",
        "normalizedStatus": "failed" if failure_class != "canceled" else "canceled",
        "sourceIssue": "MM-994",
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    return AgentRunResult(
        outputRefs=[],
        summary=_compact_summary(summary, redactor),
        diagnosticsRef=diagnostics_ref,
        failureClass=failure_class,
        providerErrorCode=provider_error_code,
        metadata=redact_sensitive_payload(metadata),
    )


def _result_metadata(
    spec: "_OmnigentExecutionSpec",
    session_id: str,
    status: str,
    harvest: "_HarvestResult",
) -> dict[str, Any]:
    return {
        "providerName": "omnigent",
        "normalizedStatus": status,
        "omnigentSessionId": session_id,
        "hostType": spec.host_type,
        "workspace": spec.workspace,
        "captureManifestRef": harvest.manifest_ref,
        "sourceIssue": "MM-994",
    }


async def _safe_get_session(
    client: OmnigentHttpClient,
    session_id: str,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    try:
        return await client.get_session(session_id)
    except Exception as exc:
        diagnostics.setdefault("errors", []).append(
            {
                "phase": "get_session",
                "failureClass": "integration_error",
                "message": redact_sensitive_text(str(exc)),
            }
        )
        return {}


def _normalize_status(snapshot: Mapping[str, Any]) -> str:
    raw = str(snapshot.get("status") or snapshot.get("state") or "").strip().lower()
    if raw in _COMPLETED_STATUSES:
        return "completed"
    if raw in _FAILED_STATUSES:
        return "failed"
    if raw in _CANCELED_STATUSES:
        return "canceled"
    if raw in {"waiting", "awaiting_approval"}:
        return "awaiting_approval"
    if raw in _ACTIVE_STATUSES or not raw:
        return "running"
    raise OmnigentExecutionError(
        f"Unknown Omnigent session status: {raw}",
        failure_class="integration_error",
        provider_error_code="omnigent_unknown_status",
    )


def _extract_session_id(response: Mapping[str, Any]) -> str:
    for key in ("id", "session_id", "sessionId"):
        value = _clean(response.get(key))
        if value:
            return value
    nested = _mapping(response.get("session"))
    for key in ("id", "session_id", "sessionId"):
        value = _clean(nested.get(key))
        if value:
            return value
    raise OmnigentExecutionError(
        "Omnigent session create response did not include a session id.",
        failure_class="integration_error",
        provider_error_code="omnigent_missing_session_id",
    )


def _summary_from_snapshot(snapshot: Mapping[str, Any]) -> str:
    for key in ("summary", "final_response", "finalResponse", "output", "message"):
        value = _clean(snapshot.get(key))
        if value:
            return value
    return ""


def _summary_from_events(events: list[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for event in events:
        payload = _mapping(event.get("payload"))
        for key in ("text", "delta", "content"):
            value = _clean(payload.get(key))
            if value:
                parts.append(value)
    return "".join(parts).strip()


def _changed_file_paths(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("files") or payload.get("changes") or []
    paths: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping):
                path = _clean(item.get("path") or item.get("filename") or item.get("name"))
            else:
                path = _clean(item)
            if path:
                paths.append(path)
    return paths


def _session_file_entries(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payload.get("files") or payload.get("items") or []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, Mapping)]
    return []


def _reject_secret_payload(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}"
            if _SENSITIVE_KEY_RE.search(key_text):
                if not _is_secret_ref(nested):
                    raise OmnigentExecutionError(
                        f"Omnigent request contains raw credential field {nested_path}.",
                        failure_class="user_error",
                        provider_error_code="omnigent_raw_secret_rejected",
                    )
            _reject_secret_payload(nested, path=nested_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_payload(item, path=f"{path}[{index}]")
    elif isinstance(value, str) and _SECRET_VALUE_RE.search(value):
        _raise_secret_value_error(path)


def _reject_secret_text(value: Any, *, path: str) -> None:
    if isinstance(value, str) and _SECRET_VALUE_RE.search(value):
        _raise_secret_value_error(path)


def _raise_secret_value_error(path: str) -> None:
    raise OmnigentExecutionError(
        f"Omnigent request contains a secret-like value at {path}.",
        failure_class="user_error",
        provider_error_code="omnigent_raw_secret_rejected",
    )


def _reject_v1_non_goals(omnigent: Mapping[str, Any]) -> None:
    forbidden_paths = (
        ("session", "sessionId"),
        ("session", "reuseSessionId"),
        ("session", "multiStepReuse"),
        ("capture", "hostSideHelper"),
        ("capture", "hostSideCapture"),
        ("streaming", "statusResults"),
    )
    for section_name, key in forbidden_paths:
        section = _mapping(omnigent.get(section_name))
        if key in section and section.get(key) not in (None, False, ""):
            raise OmnigentExecutionError(
                f"Omnigent v1 does not expose {section_name}.{key}.",
                failure_class="user_error",
                provider_error_code="omnigent_v1_non_goal",
            )


def _is_secret_ref(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value.startswith(("env://", "secret://", "vault://", "ref://"))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _looks_like_local_path(value: str) -> bool:
    return value.startswith(("/", "./", "../", "~")) or (
        re.match(r"^[A-Za-z]:[/\\]", value) is not None
    )


def _managed_workspace_from_request(
    request: AgentExecutionRequest,
    params: Mapping[str, Any],
) -> str:
    repository = _find_repository_url(request.workspace_spec)
    workspace_context = params.get("workspaceContext")
    if not repository and isinstance(workspace_context, Mapping):
        repository = _find_repository_url(workspace_context)
    omnigent = params.get("omnigent")
    if not repository and isinstance(omnigent, Mapping):
        nested_context = omnigent.get("workspaceContext")
        if isinstance(nested_context, Mapping):
            repository = _find_repository_url(nested_context)
    if not repository:
        return ""
    branch = _clean(request.workspace_spec.get("branch")) or _clean(
        request.workspace_spec.get("startingBranch")
    )
    if branch and "#" not in repository:
        return f"{repository}#{branch}"
    return repository


def _find_repository_url(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    for key in ("repository", "repositoryUrl", "repoUrl", "gitUrl"):
        value = payload.get(key)
        if isinstance(value, str) and _is_git_url_with_optional_branch(value):
            return value
        if isinstance(value, Mapping):
            nested = _find_repository_url(value)
            if nested:
                return nested
    return ""


def _is_git_url_with_optional_branch(value: str) -> bool:
    candidate = value.split("#", 1)[0]
    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https", "ssh", "git"} and parsed.netloc:
        return True
    if candidate.startswith("git@") and ":" in candidate:
        return True
    return False


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _artifact_dir(
    request: AgentExecutionRequest,
    *,
    redactor: SecretRedactor,
) -> Path:
    safe_key = _safe_artifact_name(
        request.idempotency_key or request.correlation_id or "omnigent-run"
    )
    root = Path(os.environ.get("MOONMIND_OMNIGENT_ARTIFACT_ROOT", "var/artifacts/omnigent"))
    path = root / safe_key
    return Path(redactor.scrub(str(path)))


def _minimal_artifact_spec(request: AgentExecutionRequest) -> "_OmnigentExecutionSpec":
    return _OmnigentExecutionSpec(
        endpoint_ref="default",
        agent_id="",
        agent_name="",
        title="MoonMind Agent Task",
        prompt_text="",
        include_idempotency_marker=True,
        host_type="managed",
        host_id="",
        workspace="",
        model_override=None,
        reasoning_effort=None,
        terminal_launch_args=(),
        capture_changed_files=True,
        capture_session_files=True,
        delete_after_harvest=False,
        delete_branch=False,
        patch_source="github_pr_or_host_helper",
        timeout_seconds=60.0,
        stream_timeout_seconds=120.0,
        cancel_grace_seconds=5.0,
        poll_interval_seconds=2.0,
        max_harvest_files=50,
        artifact_dir=Path("var/artifacts/omnigent")
        / _safe_artifact_name(request.idempotency_key or request.correlation_id or "failure"),
    )


def _write_json_artifact(
    spec: "_OmnigentExecutionSpec",
    name: str,
    payload: Any,
    *,
    redactor: SecretRedactor,
) -> str:
    data = json.dumps(
        redact_sensitive_payload(payload),
        indent=2,
        sort_keys=True,
        default=str,
    ).encode()
    return _write_bytes_artifact(spec, name, data, redactor=redactor)


def _write_bytes_artifact(
    spec: "_OmnigentExecutionSpec",
    name: str,
    data: bytes,
    *,
    redactor: SecretRedactor,
) -> str:
    target = spec.artifact_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        safe_data = redactor.scrub(data.decode("utf-8", errors="strict")).encode()
    except UnicodeDecodeError:
        safe_data = data
    target.write_bytes(safe_data)
    return f"file:{target.as_posix()}"


def _safe_artifact_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return safe.strip("._")[:160] or "artifact"


def _compact_summary(summary: str, redactor: SecretRedactor) -> str:
    safe = redact_sensitive_text(redactor.scrub(summary)).strip()
    return safe[:4093] + "..." if len(safe) > 4096 else safe


class _OmnigentExecutionSpec:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _HarvestResult:
    def __init__(self, *, output_refs: list[str], manifest_ref: str) -> None:
        self.output_refs = output_refs
        self.manifest_ref = manifest_ref


__all__ = ["run_omnigent_execution"]
