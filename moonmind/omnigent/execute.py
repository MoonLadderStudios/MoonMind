"""Run one Omnigent streaming execution inside a Temporal activity.

Source issue traceability: MM-981 -> MM-995.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from moonmind.omnigent.settings import (
    OMNIGENT_DISABLED_MESSAGE,
    build_omnigent_gate,
    resolved_api_token,
    resolved_default_agent_name,
    resolved_server_url,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    FailureClass,
    raise_unsupported_status,
)
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentAdapterError,
    build_omnigent_selection,
    build_omnigent_session_create_payload,
    resolve_omnigent_target,
)
from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
)

OmnigentRunState = Literal[
    "queued",
    "launching",
    "running",
    "awaiting_approval",
    "completed",
    "failed",
    "canceled",
]

_TERMINAL_STATES = {"completed", "failed", "canceled"}


class OmnigentExecutionError(RuntimeError):
    """Execution-boundary failure carrying the canonical failure class."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: FailureClass = "integration_error",
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class


@dataclass(slots=True)
class OmnigentRunRecord:
    idempotency_key: str
    session_id: str
    prompt_digest: str
    first_message_state: Literal["none", "posting", "posted"] = "none"
    pending_id: str | None = None


class InMemoryOmnigentRunStore:
    """Activity-local idempotency store used until durable persistence is wired."""

    def __init__(self) -> None:
        self._records: dict[str, OmnigentRunRecord] = {}

    def get(self, key: str) -> OmnigentRunRecord | None:
        return self._records.get(key)

    def put(self, record: OmnigentRunRecord) -> OmnigentRunRecord:
        self._records[record.idempotency_key] = record
        return record

    def clear(self) -> None:
        self._records.clear()


_DEFAULT_RUN_STORE = InMemoryOmnigentRunStore()


async def run_omnigent_execution(
    request: AgentExecutionRequest,
    *,
    env: Mapping[str, Any] | None = None,
    client: OmnigentHttpClient | None = None,
    run_store: InMemoryOmnigentRunStore | None = None,
) -> AgentRunResult:
    """Execute an Omnigent run via the streaming activity boundary."""

    source_env = env if env is not None else os.environ
    gate = build_omnigent_gate(env=source_env)
    if not gate.enabled:
        raise RuntimeError(
            f"{OMNIGENT_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    client = client or OmnigentHttpClient(
        base_url=resolved_server_url(env=source_env),
        api_token=resolved_api_token(env=source_env),
    )
    store = run_store or _DEFAULT_RUN_STORE

    selection = build_omnigent_selection(request)
    target = await resolve_omnigent_target(
        selection,
        list_agents=client.list_agents,
        upload_agent_bundle=_unsupported_bundle_upload,
        default_agent_name=resolved_default_agent_name(env=source_env),
    )
    prompt = _resolve_prompt(request)
    prompt_digest = _prompt_digest(prompt)

    record = store.get(request.idempotency_key)
    created_session = False
    if record is None:
        create_payload = build_omnigent_session_create_payload(
            request=request,
            selection=selection,
            target=target,
        )
        created = await client.create_session(create_payload)
        session_id = _extract_session_id(created)
        record = store.put(
            OmnigentRunRecord(
                idempotency_key=request.idempotency_key,
                session_id=session_id,
                prompt_digest=prompt_digest,
            )
        )
        created_session = True
    elif record.prompt_digest != prompt_digest:
        raise OmnigentExecutionError(
            "Conflicting Omnigent first-message digest for idempotency key",
            failure_class="user_error",
        )

    events: list[dict[str, Any]] = []
    child_sessions: list[str] = []
    try:
        if record.first_message_state == "posting":
            snapshot = await client.get_session(record.session_id)
            if _snapshot_contains_first_message(snapshot, record.prompt_digest):
                record.first_message_state = "posted"
            else:
                raise OmnigentExecutionError(
                    "Ambiguous Omnigent posting reconciliation failed closed",
                    failure_class="integration_error",
                )

        if record.first_message_state != "posted":
            record.first_message_state = "posting"
            posted = await client.post_event(
                record.session_id,
                {
                    "type": "message",
                    "text": prompt,
                    "metadata": {
                        "moonmindIdempotencyKey": request.idempotency_key,
                        "moonmindCorrelationId": request.correlation_id,
                        "moonmindPromptDigest": record.prompt_digest,
                    },
                },
            )
            record.pending_id = _clean(posted.get("pending_id")) or _clean(
                posted.get("pendingId")
            )
            record.first_message_state = "posted"

        stream_disconnected = False
        try:
            async for event in client.stream_events(record.session_id):
                events.append(event)
                event_type = _event_type(event)
                if event_type == "child_session_created":
                    child_id = _clean(event.get("session_id")) or _clean(
                        event.get("sessionId")
                    )
                    if child_id:
                        child_sessions.append(child_id)
                if event_type == "elicitation_request":
                    await _resolve_elicitation_if_allowed(
                        client=client,
                        request=request,
                        session_id=record.session_id,
                        event=event,
                    )
                state = _state_from_event(event)
                if state in _TERMINAL_STATES:
                    break
        except OmnigentClientError:
            stream_disconnected = True

        snapshot = await client.get_session(record.session_id)
        state = _normalize_omnigent_state(_snapshot_status(snapshot))
        output_refs = await _harvest_output_refs(client, record.session_id)
        metadata: dict[str, Any] = {
            "providerName": "omnigent",
            "omnigentSessionId": record.session_id,
            "omnigentAgentId": target.agent_id,
            "omnigentAgentName": target.agent_name,
            "createdSession": created_session,
            "streamDisconnected": stream_disconnected,
            "eventsCaptured": len(events),
            "childSessions": child_sessions,
            "patchAvailable": False,
            "sourceIssue": "MM-981",
            "jiraIssue": "MM-995",
        }
        summary = _summary_from_snapshot(snapshot)
        diagnostics_ref = None
        if state == "failed":
            diagnostics_ref = f"omnigent://sessions/{record.session_id}/diagnostics"
        elif not metadata["patchAvailable"]:
            diagnostics_ref = (
                f"omnigent://sessions/{record.session_id}/diagnostics/patch-unavailable"
            )
        return AgentRunResult(
            outputRefs=output_refs,
            summary=summary,
            diagnosticsRef=diagnostics_ref,
            failureClass="execution_error" if state == "failed" else None,
            metadata=metadata,
        )
    except asyncio.CancelledError:
        await _best_effort_cancel(client, record.session_id)
        raise


def normalize_omnigent_state(raw_status: str | None) -> OmnigentRunState:
    """Map Omnigent observations to supported MoonMind-side states."""

    return _normalize_omnigent_state(raw_status)


def reset_in_memory_omnigent_run_store() -> None:
    """Clear the process-local test store."""

    _DEFAULT_RUN_STORE.clear()


async def _unsupported_bundle_upload(bundle_ref: str) -> Mapping[str, Any]:
    raise OmnigentAdapterError(
        f"Omnigent bundle upload is not implemented for {bundle_ref}",
        failure_class="integration_error",
    )


def _resolve_prompt(request: AgentExecutionRequest) -> str:
    omnigent = (request.parameters or {}).get("omnigent")
    if isinstance(omnigent, Mapping):
        prompt = omnigent.get("prompt")
        if isinstance(prompt, Mapping):
            text = _clean(prompt.get("text"))
            if text:
                return text
    if request.instruction_ref:
        return f"MoonMind instructionRef: {request.instruction_ref}"
    title = _clean((request.parameters or {}).get("title"))
    return title or "Execute the MoonMind task."


def _prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _extract_session_id(payload: Mapping[str, Any]) -> str:
    session_id = (
        _clean(payload.get("id"))
        or _clean(payload.get("sessionId"))
        or _clean(payload.get("session_id"))
    )
    if not session_id:
        raise OmnigentExecutionError(
            "Omnigent session create did not return a session id",
            failure_class="integration_error",
        )
    return session_id


def _snapshot_contains_first_message(
    snapshot: Mapping[str, Any],
    prompt_digest: str,
) -> bool:
    haystack = str(snapshot)
    return prompt_digest in haystack


def _state_from_event(event: Mapping[str, Any]) -> OmnigentRunState | None:
    raw = (
        _clean(event.get("status"))
        or _clean(event.get("state"))
        or _clean(event.get("sessionStatus"))
    )
    if raw:
        return _normalize_omnigent_state(raw)
    event_type = _event_type(event)
    if event_type in {"completed", "session.completed", "run.completed"}:
        return "completed"
    if event_type in {"failed", "session.failed", "run.failed"}:
        return "failed"
    if event_type in {"canceled", "cancelled", "session.canceled"}:
        return "canceled"
    if event_type == "elicitation_request":
        return "awaiting_approval"
    return None


def _snapshot_status(snapshot: Mapping[str, Any]) -> str | None:
    return (
        _clean(snapshot.get("status"))
        or _clean(snapshot.get("state"))
        or _clean(snapshot.get("sessionStatus"))
    )


def _normalize_omnigent_state(raw_status: str | None) -> OmnigentRunState:
    normalized = str(raw_status or "").strip().lower()
    if normalized in {"queued", "pending"}:
        return "queued"
    if normalized in {"launching", "starting", "provisioning"}:
        return "launching"
    if normalized in {"running", "in_progress", "working"}:
        return "running"
    if normalized in {"awaiting_approval", "awaiting_input", "elicitation"}:
        return "awaiting_approval"
    if normalized in {"completed", "complete", "succeeded", "success", "done"}:
        return "completed"
    if normalized in {"failed", "error", "errored"}:
        return "failed"
    if normalized in {"canceled", "cancelled", "stopped"}:
        return "canceled"
    raise_unsupported_status(raw_status or "", context="omnigent")


async def _resolve_elicitation_if_allowed(
    *,
    client: OmnigentHttpClient,
    request: AgentExecutionRequest,
    session_id: str,
    event: Mapping[str, Any],
) -> None:
    omnigent = (request.parameters or {}).get("omnigent")
    if not isinstance(omnigent, Mapping) or not omnigent.get("autoApproveElicitations"):
        return
    elicitation_id = _clean(event.get("id")) or _clean(event.get("elicitationId"))
    if not elicitation_id:
        return
    await client.resolve_elicitation(
        session_id,
        elicitation_id,
        {"decision": "approved", "source": "moonmind"},
    )


async def _harvest_output_refs(
    client: OmnigentHttpClient,
    session_id: str,
) -> list[str]:
    refs = [
        f"omnigent://sessions/{session_id}/snapshot/final",
        f"omnigent://sessions/{session_id}/transcript",
    ]
    try:
        changes = await client.list_changed_files(session_id)
    except OmnigentClientError:
        changes = {}
    for path in _changed_file_paths(changes):
        refs.append(f"omnigent://sessions/{session_id}/workspace/{path}")
        try:
            await client.get_workspace_file(session_id, path)
        except OmnigentClientError:
            continue
    try:
        files = await client.list_session_files(session_id)
    except OmnigentClientError:
        files = {}
    for file_id in _session_file_ids(files):
        refs.append(f"omnigent://sessions/{session_id}/files/{file_id}")
        try:
            await client.get_session_file_content(session_id, file_id)
        except OmnigentClientError:
            continue
    return refs


def _changed_file_paths(payload: Mapping[str, Any]) -> list[str]:
    raw_items = payload.get("changes") or payload.get("files") or []
    if not isinstance(raw_items, list):
        return []
    paths: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            paths.append(item)
        elif isinstance(item, Mapping):
            path = _clean(item.get("path")) or _clean(item.get("filename"))
            if path:
                paths.append(path)
    return paths


def _session_file_ids(payload: Mapping[str, Any]) -> list[str]:
    raw_items = payload.get("files") or payload.get("items") or []
    if not isinstance(raw_items, list):
        return []
    ids: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, Mapping):
            file_id = _clean(item.get("id")) or _clean(item.get("fileId"))
            if file_id:
                ids.append(file_id)
    return ids


async def _best_effort_cancel(client: OmnigentHttpClient, session_id: str) -> None:
    try:
        await client.interrupt(session_id)
    finally:
        await client.stop_session(session_id)


def _summary_from_snapshot(snapshot: Mapping[str, Any]) -> str | None:
    for key in ("summary", "final_response", "finalResponse", "output", "text"):
        value = _clean(snapshot.get(key))
        if value:
            return value
    return None


def _event_type(event: Mapping[str, Any]) -> str:
    return str(event.get("type") or event.get("event") or "").strip().lower()


def _clean(value: object | None) -> str | None:
    text = str(value or "").strip()
    return text or None


__all__ = [
    "InMemoryOmnigentRunStore",
    "OmnigentExecutionError",
    "OmnigentRunRecord",
    "normalize_omnigent_state",
    "reset_in_memory_omnigent_run_store",
    "run_omnigent_execution",
]
