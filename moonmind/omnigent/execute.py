"""Run one Omnigent streaming-gateway execution for MM-991."""

from __future__ import annotations

import asyncio
import hashlib
import json
import inspect
from typing import Any

import httpx
from temporalio import activity

from moonmind.omnigent.settings import (
    OMNIGENT_DISABLED_MESSAGE,
    build_omnigent_gate,
    resolved_api_token,
    resolved_default_agent_name,
    resolved_server_url,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
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
    elif not isinstance(items, list):
        items = []
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
        raise OmnigentContractError(
            f"Requested Omnigent agent name '{requested_name}' could not be resolved"
        )
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


def _build_omnigent_first_message(
    *,
    request: AgentExecutionRequest,
    prompt: dict[str, Any],
) -> dict[str, Any]:
    text = str(prompt.get("text") or "").strip()
    instruction_ref = str(prompt.get("instructionRef") or "").strip()
    if not text and (instruction_ref or request.instruction_ref):
        raise OmnigentContractError(
            "Omnigent prompt requires inline text; instructionRef cannot be sent "
            "without artifact resolution"
        )
    if not text:
        text = str((request.parameters or {}).get("description") or "").strip()
    if not text:
        title = str((request.parameters or {}).get("title") or "MoonMind Agent Task").strip()
        workspace_blob = json.dumps(request.workspace_spec or {}, indent=2, default=str)
        parts = [
            f"Task title: {title}",
            f"Correlation ID: {request.correlation_id}",
            f"Workspace spec (JSON):\n{workspace_blob}",
        ]
        if request.input_refs:
            parts.append("Input refs: " + ", ".join(request.input_refs))
        text = "\n\n".join(parts)

    return {
        "type": "message",
        "data": {
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


async def _unsupported_bundle_upload(bundle_ref: str) -> dict[str, Any]:
    raise OmnigentContractError(
        f"Omnigent bundleRef cannot be resolved by this activity: {bundle_ref}"
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _safe_heartbeat(details: dict[str, Any]) -> None:
    try:
        activity.heartbeat(details)
    except RuntimeError:
        pass


def _heartbeat_details() -> tuple[Any, ...]:
    try:
        raw = getattr(activity.info(), "heartbeat_details", ())
    except RuntimeError:
        return ()
    if raw is None:
        return ()
    if isinstance(raw, tuple):
        return raw
    if isinstance(raw, list):
        return tuple(raw)
    return (raw,)


def _heartbeat_state() -> dict[str, Any]:
    for detail in reversed(_heartbeat_details()):
        if isinstance(detail, dict):
            return detail
    return {}


def _heartbeat_session_id(state: dict[str, Any]) -> str:
    return str(
        state.get("omnigentSessionId")
        or state.get("session_id")
        or state.get("sessionId")
        or ""
    ).strip()


async def _periodic_stream_heartbeat(
    *,
    session_id: str,
    event_count: dict[str, int],
    interval_seconds: float = 30.0,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        _safe_heartbeat(
            {
                "omnigentSessionId": session_id,
                "events": event_count.get("value", 0),
                "alive": True,
            }
        )


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
    snapshot_metadata_keys = {
        "omnigentAgentName": "omnigent_agent_name",
        "hostType": "host_type",
        "workspace": "workspace",
        "captureManifestRef": "capture_manifest_ref",
        "patchRef": "patch_ref",
        "githubPrUrl": "github_pr_url",
    }
    for metadata_key, snake_key in snapshot_metadata_keys.items():
        value = final_snapshot.get(metadata_key) or final_snapshot.get(snake_key)
        if value:
            metadata[metadata_key] = str(value)

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

    try:
        selection = build_omnigent_selection(request)
        async with httpx.AsyncClient() as httpx_client:
            client = OmnigentHttpClient(
                base_url=resolved_server_url(),
                api_token=resolved_api_token(),
                client=httpx_client,
            )

            async def list_agents() -> list[dict[str, Any]]:
                raw = await _maybe_await(client.list_agents())
                if isinstance(raw, list):
                    return [item for item in raw if isinstance(item, dict)]
                if isinstance(raw, dict):
                    return _agent_items(raw)
                return []

            target = await resolve_omnigent_target(
                selection,
                list_agents=list_agents,
                upload_agent_bundle=_unsupported_bundle_upload,
                default_agent_name=resolved_default_agent_name(),
            )
            session_payload = build_omnigent_session_create_payload(
                request=request,
                selection=selection,
                target=target,
            )
            session_payload["idempotency_key"] = request.idempotency_key
            labels = session_payload.setdefault("labels", {})
            if isinstance(labels, dict):
                labels.setdefault("moonmind.issue", "MM-991")

            retry_state = _heartbeat_state()
            session_id = _heartbeat_session_id(retry_state)
            first_message_posted = bool(retry_state.get("firstMessagePosted"))
            if not session_id:
                create_response = await client.create_session(session_payload)
                session_id = _session_id(create_response)
                _safe_heartbeat(
                    {
                        "omnigentSessionId": session_id,
                        "firstMessagePosted": False,
                    }
                )

            first_message = _build_omnigent_first_message(
                request=request,
                prompt=selection.prompt,
            )
            digest = hashlib.sha256(
                json.dumps(first_message, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            first_message.setdefault("metadata", {})[
                "moonmindFirstMessageDigest"
            ] = digest
            first_message["metadata"]["moonmindIdempotencyKey"] = request.idempotency_key
            if not first_message_posted:
                await client.post_event(session_id, first_message)
                _safe_heartbeat(
                    {
                        "omnigentSessionId": session_id,
                        "firstMessagePosted": True,
                        "firstMessageDigest": digest,
                    }
                )

            event_count = {"value": 0}
            heartbeat_task = asyncio.create_task(
                _periodic_stream_heartbeat(
                    session_id=session_id,
                    event_count=event_count,
                )
            )
            terminal_status: str | None = None
            try:
                async for event in client.stream_events(session_id):
                    event_count["value"] += 1
                    normalized = normalize_omnigent_observation(event)
                    if normalized in {"awaiting_approval", "intervention_requested"}:
                        _safe_heartbeat(
                            {
                                "normalizedStatus": normalized,
                                "omnigentSessionId": session_id,
                                "events": event_count["value"],
                                "firstMessagePosted": True,
                            }
                        )
                        continue
                    if normalized in {"completed", "failed", "canceled", "timed_out"}:
                        terminal_status = normalized
                        break
                    if event_count["value"] % 8 == 0:
                        _safe_heartbeat(
                            {
                                "omnigentSessionId": session_id,
                                "events": event_count["value"],
                                "firstMessagePosted": True,
                            }
                        )
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

            final_snapshot = await client.get_session(session_id)
            if terminal_status is None:
                normalized_snapshot = normalize_omnigent_observation(final_snapshot)
                if normalized_snapshot in {
                    "completed",
                    "failed",
                    "canceled",
                    "timed_out",
                }:
                    terminal_status = normalized_snapshot
            if terminal_status is None:
                raise OmnigentContractError(
                    "Omnigent stream ended before a terminal session outcome"
                )
            return build_omnigent_result(
                request=request,
                terminal_status=terminal_status,
                session_id=session_id,
                agent_id=target.agent_id,
                final_snapshot=final_snapshot,
                event_count=event_count["value"],
            )
    except (OmnigentContractError, OmnigentAdapterError, ValueError) as exc:
        return AgentRunResult(
            outputRefs=[],
            summary=_compact_summary(exc, fallback="Omnigent contract error"),
            diagnosticsRef="omnigent://diagnostics/contract-error",
            failureClass="integration_error",
            providerErrorCode="omnigent_contract_error",
            metadata={"normalizedStatus": "failed", "providerName": "omnigent"},
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
