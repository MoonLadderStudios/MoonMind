"""Run one Omnigent streaming-gateway execution inside a Temporal activity."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from temporalio import activity

from moonmind.omnigent.settings import (
    DEFAULT_OMNIGENT_ENDPOINT_REF,
    OMNIGENT_DISABLED_MESSAGE,
    build_omnigent_gate,
    resolved_api_token,
    resolved_default_agent_name,
    resolved_request_timeout_seconds,
    resolved_server_url,
)
from moonmind.omnigent.store import (
    FIRST_MESSAGE_POSTED,
    FIRST_MESSAGE_POSTING,
    FIRST_MESSAGE_TERMINAL,
    OmnigentDigestMismatchError,
    OmnigentRunStore,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
)


class OmnigentFirstMessageAmbiguousError(RuntimeError):
    """Raised when a retry cannot prove first-message acceptance or absence."""


async def run_omnigent_execution(
    request: AgentExecutionRequest,
    *,
    client: Any | None = None,
    store: OmnigentRunStore | None = None,
) -> AgentRunResult:
    """Create or reattach to an Omnigent session and execute one first message."""

    gate = build_omnigent_gate()
    if not gate.enabled:
        raise RuntimeError(
            f"{OMNIGENT_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    owns_client = client is None
    if client is None:
        client = OmnigentHttpClient(
            base_url=resolved_server_url(),
            api_token=resolved_api_token(),
            request_timeout_seconds=resolved_request_timeout_seconds(),
        )
    if store is None:
        from api_service.db.base import async_session_maker

        store = OmnigentRunStore(async_session_maker)

    try:
        return await _execute_with_dependencies(request, client=client, store=store)
    except OmnigentDigestMismatchError as exc:
        return _failure_result(
            request=request,
            summary=str(exc),
            failure_class="user_error",
            provider_error_code="omnigent_first_message_digest_mismatch",
        )
    except OmnigentFirstMessageAmbiguousError as exc:
        return _failure_result(
            request=request,
            summary=str(exc),
            failure_class="integration_error",
            provider_error_code="omnigent_first_message_acceptance_ambiguous",
        )
    except OmnigentClientError as exc:
        return _failure_result(
            request=request,
            summary=str(exc),
            failure_class=_classify_client_error(exc),
            provider_error_code=str(exc.status_code or "omnigent_transport_error"),
        )
    finally:
        if owns_client and hasattr(client, "aclose"):
            await client.aclose()


async def _execute_with_dependencies(
    request: AgentExecutionRequest,
    *,
    client: Any,
    store: OmnigentRunStore,
) -> AgentRunResult:
    params = request.parameters or {}
    omnigent = _mapping(params.get("omnigent"))
    endpoint_ref = _clean(omnigent.get("endpointRef")) or DEFAULT_OMNIGENT_ENDPOINT_REF
    agent_id, agent_name = await _resolve_agent(client, omnigent)
    session_payload = _build_session_payload(
        request,
        omnigent=omnigent,
        endpoint_ref=endpoint_ref,
        agent_id=agent_id,
        agent_name=agent_name,
    )
    row = await store.get_or_create(
        request=request,
        endpoint_ref=endpoint_ref,
        agent_id=agent_id,
        agent_name=agent_name,
        target_metadata=session_payload,
    )

    session_id = row.omnigent_session_id
    if not session_id:
        created = await client.create_session(session_payload)
        session_id = _extract_session_id(created)
        row = await store.attach_session(request.idempotency_key, session_id)

    base_message = _build_first_message_text(request, omnigent=omnigent)
    digest = _first_message_digest(base_message)
    marker = _first_message_marker(request, digest=digest)
    message = _message_with_marker(
        base_message,
        marker=marker,
        include_marker=_include_marker(omnigent),
    )
    row = await store.mark_prepared(
        request.idempotency_key,
        digest=digest,
        marker=marker,
    )

    initial_snapshot = await client.get_session(session_id)
    should_post = await _should_post_first_message(
        row=row,
        snapshot=initial_snapshot,
        store=store,
        idempotency_key=request.idempotency_key,
    )
    if should_post:
        await store.mark_posting(request.idempotency_key)
        response = await client.post_event(
            session_id,
            {
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": message}],
                },
            },
        )
        await store.mark_posted(request.idempotency_key, response=response)

    events_captured = 0
    terminal_event: dict[str, Any] | None = None
    async for event in client.stream_events(session_id):
        events_captured += 1
        _heartbeat(
            {
                "provider": "omnigent",
                "omnigentSessionId": session_id,
                "eventsCaptured": events_captured,
                "idempotencyKeyHash": _short_hash(request.idempotency_key),
            }
        )
        if _is_terminal_event(event):
            terminal_event = event
            break

    final_snapshot = await client.get_session(session_id)
    status = _normalized_terminal_status(final_snapshot, terminal_event)
    if status == "completed":
        await store.mark_terminal(
            request.idempotency_key,
            status="completed",
            terminal_refs={"finalSnapshot": "inline:final_snapshot"},
        )
        return AgentRunResult(
            outputRefs=[],
            summary=_final_summary(final_snapshot, terminal_event),
            metadata={
                "normalizedStatus": "completed",
                "providerName": "omnigent",
                "omnigentSessionId": session_id,
                "omnigentAgentId": agent_id,
                "omnigentAgentName": agent_name,
                "endpointRef": endpoint_ref,
                "firstMessageState": FIRST_MESSAGE_POSTED,
                "firstMessageDigest": digest,
                "eventsCaptured": events_captured,
                "sourceIssue": "MM-981",
                "implementationIssue": "MM-992",
            },
        )

    await store.mark_terminal(
        request.idempotency_key,
        status="failed",
        terminal_refs={"finalSnapshot": "inline:final_snapshot"},
    )
    return _failure_result(
        request=request,
        summary=_final_summary(final_snapshot, terminal_event),
        failure_class="execution_error",
        provider_error_code=f"omnigent_{status}",
        metadata={
            "omnigentSessionId": session_id,
            "firstMessageState": FIRST_MESSAGE_POSTED,
            "eventsCaptured": events_captured,
        },
    )


async def _should_post_first_message(
    *,
    row: Any,
    snapshot: dict[str, Any],
    store: OmnigentRunStore,
    idempotency_key: str,
) -> bool:
    state = str(row.first_message_state or "")
    if state in {FIRST_MESSAGE_POSTED, FIRST_MESSAGE_TERMINAL}:
        return False
    if state == FIRST_MESSAGE_POSTING:
        if _snapshot_contains_first_message(snapshot, row.first_message_marker, row.first_message_digest):
            await store.mark_posted(idempotency_key)
            return False
        if _snapshot_proves_absence(snapshot):
            return True
        raise OmnigentFirstMessageAmbiguousError(
            "Retry found first-message state=posting but Omnigent snapshot did not "
            "prove acceptance or absence; failing closed."
        )
    return True


def _build_session_payload(
    request: AgentExecutionRequest,
    *,
    omnigent: dict[str, Any],
    endpoint_ref: str,
    agent_id: str | None,
    agent_name: str | None,
) -> dict[str, Any]:
    session = _mapping(omnigent.get("session"))
    host_type = _clean(session.get("hostType")) or "managed"
    workspace = session.get("workspace")
    host_id = session.get("hostId")
    if host_type == "managed" and host_id:
        raise OmnigentClientError("managed Omnigent sessions must not include hostId")
    payload: dict[str, Any] = {
        "agent_id": agent_id,
        "title": _clean(session.get("title")) or _clean((request.parameters or {}).get("title")) or "MoonMind Omnigent task",
        "labels": {
            "moonmind.correlation_id": request.correlation_id,
            "moonmind.idempotency_key": request.idempotency_key,
        },
        "host_type": host_type,
        "workspace": workspace,
        "model_override": session.get("modelOverride"),
        "reasoning_effort": session.get("reasoningEffort"),
        "terminal_launch_args": session.get("terminalLaunchArgs") or [],
        "endpoint_ref": endpoint_ref,
    }
    if host_type == "external":
        payload["host_id"] = host_id
    if agent_name and not agent_id:
        payload["agent_name"] = agent_name
    return {key: value for key, value in payload.items() if value is not None}


async def _resolve_agent(client: Any, omnigent: dict[str, Any]) -> tuple[str | None, str | None]:
    agent = _mapping(omnigent.get("agent"))
    agent_id = _clean(agent.get("agentId"))
    agent_name = _clean(agent.get("agentName")) or resolved_default_agent_name()
    if agent_id:
        return agent_id, agent_name or None
    if not agent_name:
        return None, None
    if not hasattr(client, "list_agents"):
        return None, agent_name
    agents = await client.list_agents()
    for item in agents:
        name = _clean(item.get("name") or item.get("agentName"))
        if name == agent_name:
            return _clean(item.get("id") or item.get("agent_id")) or None, agent_name
    raise OmnigentClientError(f"Unknown Omnigent agent name: {agent_name}", status_code=400)


def _build_first_message_text(
    request: AgentExecutionRequest, *, omnigent: dict[str, Any]
) -> str:
    prompt = _mapping(omnigent.get("prompt"))
    text = _clean(prompt.get("text"))
    if text:
        return text
    params = request.parameters or {}
    description = _clean(params.get("description"))
    if description:
        return description
    title = _clean(params.get("title")) or "MoonMind Omnigent task"
    return "\n\n".join(
        [
            f"Task title: {title}",
            f"Workspace spec (JSON):\n{json.dumps(request.workspace_spec or {}, sort_keys=True)}",
            "Input refs: " + ", ".join(request.input_refs) if request.input_refs else "",
        ]
    ).strip()


def _first_message_digest(message_text: str) -> str:
    payload = {
        "role": "user",
        "content": [{"type": "input_text", "text": message_text}],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _first_message_marker(request: AgentExecutionRequest, *, digest: str) -> str:
    return "\n".join(
        [
            "MoonMind-Omnigent-Run:",
            f"  correlation_id: {request.correlation_id}",
            f"  idempotency_key: {request.idempotency_key}",
            f"  first_message_digest: {digest}",
        ]
    )


def _message_with_marker(message: str, *, marker: str, include_marker: bool) -> str:
    if not include_marker:
        return message
    return f"{message.rstrip()}\n\n{marker}"


def _include_marker(omnigent: dict[str, Any]) -> bool:
    prompt = _mapping(omnigent.get("prompt"))
    return prompt.get("includeIdempotencyMarker") is not False


def _snapshot_contains_first_message(
    snapshot: dict[str, Any], marker: str | None, digest: str | None
) -> bool:
    blob = json.dumps(snapshot, sort_keys=True, default=str)
    return bool((marker and marker in blob) or (digest and digest in blob))


def _snapshot_proves_absence(snapshot: dict[str, Any]) -> bool:
    return isinstance(snapshot.get("items"), list) or isinstance(
        snapshot.get("pending_inputs"), list
    )


def _is_terminal_event(event: dict[str, Any]) -> bool:
    event_type = _clean(event.get("type") or event.get("eventType"))
    return event_type in {
        "response.completed",
        "response.failed",
        "session.completed",
        "session.failed",
    }


def _normalized_terminal_status(
    snapshot: dict[str, Any], terminal_event: dict[str, Any] | None
) -> str:
    event_type = _clean((terminal_event or {}).get("type") or (terminal_event or {}).get("eventType"))
    if event_type.endswith(".completed"):
        return "completed"
    if event_type.endswith(".failed"):
        return "failed"
    status = _clean(snapshot.get("status")).lower()
    if status in {"completed", "complete", "idle"}:
        return "completed"
    if status in {"failed", "error"}:
        return "failed"
    return "failed"


def _final_summary(
    snapshot: dict[str, Any], terminal_event: dict[str, Any] | None
) -> str:
    for source in (terminal_event or {}, snapshot):
        for key in ("summary", "text", "final_response", "message"):
            value = _clean(source.get(key))
            if value:
                return value[:4096]
    return "Omnigent execution reached a terminal state."


def _failure_result(
    *,
    request: AgentExecutionRequest,
    summary: str,
    failure_class: str,
    provider_error_code: str,
    metadata: dict[str, Any] | None = None,
) -> AgentRunResult:
    return AgentRunResult(
        outputRefs=[],
        summary=summary[:4096],
        failureClass=failure_class,
        providerErrorCode=provider_error_code,
        metadata={
            "normalizedStatus": "failed",
            "providerName": "omnigent",
            "correlationId": request.correlation_id,
            "sourceIssue": "MM-981",
            "implementationIssue": "MM-992",
            **(metadata or {}),
        },
    )


def _classify_client_error(exc: OmnigentClientError) -> str:
    if exc.status_code in {400, 404}:
        return "user_error"
    return "integration_error"


def _extract_session_id(payload: dict[str, Any]) -> str:
    for key in ("id", "session_id", "sessionId"):
        value = _clean(payload.get(key))
        if value:
            return value
    raise OmnigentClientError("Omnigent session create response did not include a session id")


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _clean(value: object | None) -> str:
    return str(value or "").strip()


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _heartbeat(details: dict[str, Any]) -> None:
    try:
        activity.heartbeat(details)
    except RuntimeError as exc:
        if "Not in activity context" not in str(exc):
            raise


__all__ = [
    "OmnigentFirstMessageAmbiguousError",
    "run_omnigent_execution",
]
