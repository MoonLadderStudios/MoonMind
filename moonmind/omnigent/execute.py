"""Run one Omnigent streaming-gateway execution for MM-991."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from temporalio import activity

from moonmind.omnigent.settings import (
    OMNIGENT_DISABLED_MESSAGE,
    build_omnigent_gate,
    resolved_request_timeout_seconds,
    resolved_server_url,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    build_omnigent_first_message,
    omnigent_parameters,
)
from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
)

_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "canceled",
    "cancelled",
    "timed_out",
    "timeout",
}
_NON_TERMINAL_STATUSES = {
    "created",
    "launching",
    "provisioning",
    "running",
    "waiting",
    "idle",
}


class OmnigentContractError(RuntimeError):
    """Raised when Omnigent emits an unsupported adapter contract value."""


def _compact_summary(value: object | None, *, fallback: str) -> str:
    text = str(value or fallback).strip() or fallback
    return text[:4096]


def _session_id(payload: dict[str, Any]) -> str:
    raw = payload.get("id") or payload.get("session_id") or payload.get("sessionId")
    session_id = str(raw or "").strip()
    if not session_id:
        raise OmnigentContractError("Omnigent session creation response missing session id")
    return session_id


def _agent_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items") or payload.get("agents") or payload.get("data") or []
    if isinstance(items, dict):
        items = list(items.values())
    return [item for item in items if isinstance(item, dict)]


def _resolve_agent_id(*, agents_payload: dict[str, Any], requested_name: str | None) -> str:
    items = _agent_items(agents_payload)
    if not items:
        raise OmnigentContractError("Omnigent agent target could not be resolved")
    if requested_name:
        for item in items:
            if str(item.get("name") or "").strip() == requested_name:
                raw = item.get("id") or item.get("agent_id") or item.get("agentId")
                if raw:
                    return str(raw)
    raw = items[0].get("id") or items[0].get("agent_id") or items[0].get("agentId")
    if not raw:
        raise OmnigentContractError("Omnigent agent target is missing an id")
    return str(raw)


def _has_active_elicitation(payload: dict[str, Any]) -> bool:
    pending = payload.get("pending_inputs") or payload.get("pendingInputs") or []
    if isinstance(pending, list) and pending:
        return True
    return bool(payload.get("elicitation") or payload.get("elicitation_request"))


def normalize_omnigent_observation(payload: dict[str, Any]) -> str | None:
    """Normalize Omnigent observations; unknown values are contract errors."""

    event_type = str(payload.get("type") or "").strip()
    if event_type in {"stream.done"}:
        return None
    if event_type in {"response.completed", "completed"}:
        return "completed"
    if event_type in {"response.failed", "failed"}:
        return "failed"
    if event_type in {"response.elicitation_request", "elicitation_request"}:
        return "awaiting_approval"
    if event_type.startswith("response.") or event_type.startswith("session."):
        known_prefixes = (
            "response.output",
            "response.delta",
            "session.input",
            "session.item",
        )
        if not event_type.startswith(known_prefixes):
            raise OmnigentContractError(f"Unsupported Omnigent event type: {event_type}")

    status = payload.get("status")
    session = payload.get("session")
    if isinstance(session, dict) and status is None:
        status = session.get("status")
    if status is None:
        return None

    raw = str(status).strip().lower()
    if raw in {"cancelled", "timeout"}:
        return {"cancelled": "canceled", "timeout": "timed_out"}[raw]
    if raw in _TERMINAL_STATUSES:
        return raw
    if raw == "waiting":
        return (
            "awaiting_approval"
            if _has_active_elicitation(payload)
            else "intervention_requested"
        )
    if raw in _NON_TERMINAL_STATUSES:
        return raw
    raise OmnigentContractError(f"Unsupported Omnigent status: {raw}")


def _failure_class_for(status: str) -> str | None:
    if status == "completed":
        return None
    if status == "failed":
        return "execution_error"
    if status in {"canceled", "timed_out"}:
        return "system_error"
    return "integration_error"


def _session_options(omni: dict[str, Any]) -> dict[str, Any]:
    session = omni.get("session")
    return session if isinstance(session, dict) else {}


def build_omnigent_result(
    *,
    request: AgentExecutionRequest,
    terminal_status: str,
    session_id: str,
    agent_id: str | None,
    final_snapshot: dict[str, Any],
    event_count: int,
    failure_summary: str | None = None,
    provider_error_code: str | None = None,
) -> AgentRunResult:
    """Build compact terminal canonical result for Omnigent."""

    refs = final_snapshot.get("outputRefs") or final_snapshot.get("output_refs") or []
    output_refs = [str(ref) for ref in refs if str(ref).strip()]
    if not output_refs:
        output_refs = [f"omnigent://sessions/{session_id}/snapshot/final"]
    diagnostics_ref = (
        final_snapshot.get("diagnosticsRef")
        or final_snapshot.get("diagnostics_ref")
        or f"omnigent://sessions/{session_id}/diagnostics"
    )
    summary = final_snapshot.get("summary") or failure_summary
    if not summary:
        summary = (
            "Omnigent session completed"
            if terminal_status == "completed"
            else "Omnigent session failed"
        )

    metadata = {
        "providerName": "omnigent",
        "normalizedStatus": terminal_status,
        "omnigentSessionId": session_id,
        "sseEventsCaptured": event_count,
        "correlationId": request.correlation_id,
    }
    if agent_id:
        metadata["omnigentAgentId"] = agent_id
    for key in ("omnigentAgentName", "hostType", "workspace"):
        value = final_snapshot.get(key) or final_snapshot.get(key[:1].lower() + key[1:])
        if value:
            metadata[key] = str(value)
    for key in ("captureManifestRef", "patchRef", "githubPrUrl"):
        value = final_snapshot.get(key) or final_snapshot.get(key[:1].lower() + key[1:])
        if value:
            metadata[key] = str(value)

    return AgentRunResult(
        outputRefs=output_refs,
        summary=_compact_summary(
            summary,
            fallback="Omnigent session reached a terminal status",
        ),
        diagnosticsRef=str(diagnostics_ref),
        failureClass=_failure_class_for(terminal_status),
        providerErrorCode=provider_error_code,
        metadata=metadata,
    )


async def run_omnigent_execution(request: AgentExecutionRequest) -> AgentRunResult:
    """Execute one Omnigent session and return only terminal AgentRunResult."""

    gate = build_omnigent_gate()
    if not gate.enabled:
        raise RuntimeError(
            f"{OMNIGENT_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    params = request.parameters or {}
    omni = omnigent_parameters(request)
    token = os.environ.get("OMNIGENT_API_TOKEN", "").strip() or None
    client = OmnigentHttpClient(
        base_url=resolved_server_url(),
        token=token,
        request_timeout_seconds=float(resolved_request_timeout_seconds()),
    )

    agent_cfg = omni.get("agent") if isinstance(omni.get("agent"), dict) else {}
    agent_id = str(agent_cfg.get("agentId") or agent_cfg.get("id") or "").strip()
    agent_name = str(
        agent_cfg.get("agentName")
        or agent_cfg.get("name")
        or os.environ.get("OMNIGENT_DEFAULT_AGENT_NAME", "")
    ).strip()
    if not agent_id:
        agent_id = _resolve_agent_id(
            agents_payload=await client.list_agents(),
            requested_name=agent_name or None,
        )

    session_cfg = _session_options(omni)
    host_type = str(
        session_cfg.get("hostType")
        or omni.get("hostType")
        or os.environ.get("OMNIGENT_DEFAULT_HOST_TYPE")
        or "managed"
    ).strip()
    session_payload: dict[str, Any] = {
        "agent_id": agent_id,
        "title": str(params.get("title") or "MoonMind Agent Task"),
        "labels": {
            "moonmind.correlation_id": request.correlation_id,
            "moonmind.idempotency_key": request.idempotency_key,
            "moonmind.issue": "MM-991",
        },
        "host_type": host_type,
    }
    workspace = (
        session_cfg.get("workspace")
        or omni.get("workspace")
        or request.workspace_spec.get("repository")
    )
    if workspace:
        session_payload["workspace"] = workspace
    host_id = session_cfg.get("hostId") or omni.get("hostId")
    if host_type == "external" and host_id:
        session_payload["host_id"] = str(host_id)
    model = session_cfg.get("modelOverride") or omni.get("model")
    if model:
        session_payload["model_override"] = str(model)
    reasoning_effort = session_cfg.get("reasoningEffort") or omni.get(
        "reasoningEffort"
    )
    if reasoning_effort:
        session_payload["reasoning_effort"] = str(reasoning_effort)

    try:
        create_response = await client.create_session(session_payload)
        session_id = _session_id(create_response)
        first_message = build_omnigent_first_message(request)
        digest = hashlib.sha256(
            json.dumps(first_message, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        first_message.setdefault("metadata", {})["moonmindFirstMessageDigest"] = digest
        await client.post_event(session_id, first_message)

        event_count = 0
        terminal_status: str | None = None
        async for event in client.stream_events(session_id):
            event_count += 1
            normalized = normalize_omnigent_observation(event)
            if normalized in {"awaiting_approval", "intervention_requested"}:
                activity.heartbeat(
                    {
                        "normalizedStatus": normalized,
                        "omnigentSessionId": session_id,
                        "events": event_count,
                    }
                )
                continue
            if normalized in {"completed", "failed", "canceled", "timed_out"}:
                terminal_status = normalized
                break
            if event_count % 8 == 0:
                activity.heartbeat(
                    {"omnigentSessionId": session_id, "events": event_count}
                )

        final_snapshot = await client.get_session(session_id)
        if terminal_status is None:
            normalized_snapshot = normalize_omnigent_observation(final_snapshot)
            if normalized_snapshot in {"completed", "failed", "canceled", "timed_out"}:
                terminal_status = normalized_snapshot
        if terminal_status is None:
            raise OmnigentContractError(
                "Omnigent stream ended before a terminal session outcome"
            )
        return build_omnigent_result(
            request=request,
            terminal_status=terminal_status,
            session_id=session_id,
            agent_id=agent_id,
            final_snapshot=final_snapshot,
            event_count=event_count,
        )
    except OmnigentClientError as exc:
        return AgentRunResult(
            outputRefs=[],
            summary=_compact_summary(exc, fallback="Omnigent integration error"),
            diagnosticsRef="omnigent://diagnostics/transport-error",
            failureClass="integration_error",
            providerErrorCode=str(exc.status_code or "omnigent_http_error"),
            metadata={"normalizedStatus": "failed", "providerName": "omnigent"},
        )


__all__ = [
    "OmnigentContractError",
    "build_omnigent_result",
    "normalize_omnigent_observation",
    "run_omnigent_execution",
]
