"""Run one Omnigent streaming-gateway execution for MM-1059."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import httpx
from temporalio import activity

from moonmind.omnigent.bridge_artifacts import (
    LocalOmnigentArtifactGateway,
    OmnigentArtifactError,
    OmnigentArtifactGateway,
    OmnigentContractError,
    _build_capture_bundle,
    _compact_summary,
    build_omnigent_result,
    build_omnigent_terminal_refs,
)
from moonmind.omnigent.bridge_events import (
    build_omnigent_bridge_event,
    normalize_omnigent_observation,
)
from moonmind.omnigent.bridge_security import (
    BridgeSessionBinding,
    OmnigentAuthorizationError,
    assert_bridge_session_binding,
    authorize_bridge_access,
    redact_raw_events,
)
from moonmind.omnigent.bridge_store import (
    FIRST_MESSAGE_POSTED,
    FIRST_MESSAGE_POSTING,
    FIRST_MESSAGE_TERMINAL,
    OmnigentBridgeSessionStore,
    OmnigentDigestMismatchError,
)
from moonmind.omnigent.failure_classification import (
    OmnigentFailureReason,
    classify_omnigent_failure,
    failure_class_for_terminal_status,
)
from moonmind.omnigent.settings import (
    OMNIGENT_DISABLED_MESSAGE,
    build_omnigent_gate,
    resolved_api_token,
    resolved_default_agent_name,
    resolved_proxy_forward_headers,
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

_NON_TERMINAL_STATUSES = {
    "created",
    "launching",
    "provisioning",
    "running",
    "waiting",
    "idle",
}
_logger = logging.getLogger(__name__)


class OmnigentSessionStillRunningError(OmnigentClientError):
    """Raised when the stream ends while the provider session is still active."""


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


def _session_options(omni: dict[str, Any]) -> dict[str, Any]:
    session = omni.get("session")
    return session if isinstance(session, dict) else {}


async def _build_omnigent_first_message(
    *,
    request: AgentExecutionRequest,
    prompt: dict[str, Any],
    artifact_gateway: OmnigentArtifactGateway,
) -> dict[str, Any]:
    text = str(prompt.get("text") or "").strip()
    explicit_instruction_ref = str(prompt.get("instructionRef") or "").strip()
    inline_instruction = str(request.instruction_ref or "").strip()
    instruction_ref = explicit_instruction_ref or inline_instruction
    if not text and instruction_ref:
        if explicit_instruction_ref:
            text = (await artifact_gateway.read_text(instruction_ref)).strip()
        else:
            text = inline_instruction
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


def _first_message_text(first_message: dict[str, Any]) -> str:
    data = first_message.get("data")
    if not isinstance(data, dict):
        return ""
    content = data.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _first_message_marker(*, request: AgentExecutionRequest, digest: str) -> str:
    return "\n".join(
        [
            "MoonMind-Omnigent-Run:",
            f"  correlationId: {request.correlation_id}",
            f"  idempotencyKey: {request.idempotency_key}",
            f"  firstMessageDigest: {digest}",
        ]
    )


def _new_external_state_evidence(
    *,
    endpoint_ref: object,
    idempotency_key: str,
) -> dict[str, Any]:
    return {
        "endpointRef": str(endpoint_ref or "default"),
        "retry": {
            "idempotencyKey": idempotency_key,
            "sessionResolution": "pending",
            "attached": False,
            "attachSource": None,
            "firstMessageOutcome": "pending",
        },
        "firstMessage": {},
    }


def _profile_authorization_evidence(
    request: AgentExecutionRequest,
) -> dict[str, Any]:
    parameters = request.parameters if isinstance(request.parameters, dict) else {}
    omnigent = parameters.get("omnigent")
    if not isinstance(omnigent, dict):
        return {}
    payload = omnigent.get("_moonmindProfileAuthorization")
    if not isinstance(payload, dict):
        return {}
    allowed = {
        "providerProfileId",
        "credentialGeneration",
        "providerLeaseRef",
        "hostBindingRef",
        "hostLeaseRef",
        "endpointRef",
        "omnigentHostId",
        "bridgeSessionId",
    }
    return {key: payload[key] for key in allowed if payload.get(key) is not None}


def _snapshot_contains_first_message_marker(
    snapshot: dict[str, Any],
    *,
    digest: str,
    marker: str,
) -> bool:
    needle_values = {digest, marker}
    stack: list[Any] = [snapshot]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str) and any(needle in value for needle in needle_values):
            return True
    return False


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
    except RuntimeError as exc:
        _logger.debug("Skipping Omnigent heartbeat outside activity context: %s", exc)


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
    status: dict[str, str],
    interval_seconds: float = 30.0,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        _safe_heartbeat(
            {
                "omnigentSessionId": session_id,
                "normalizedStatus": status.get("value", "running"),
                "eventsCaptured": event_count.get("value", 0),
                "alive": True,
            }
        )


async def _enqueue_stream_events(
    *,
    client: OmnigentHttpClient,
    session_id: str,
    queue: asyncio.Queue[dict[str, Any] | BaseException | None],
) -> None:
    try:
        async for event in client.stream_events(session_id):
            await queue.put(event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await queue.put(exc)
    finally:
        await queue.put(None)


async def _queued_stream_events(
    *,
    queue: asyncio.Queue[dict[str, Any] | BaseException | None],
    stream_task: asyncio.Task[None],
) -> AsyncIterator[dict[str, Any]]:
    while True:
        event = await queue.get()
        if event is None:
            break
        if isinstance(event, BaseException):
            raise event
        yield event
    if stream_task.done() and not stream_task.cancelled():
        stream_task.result()


async def _cancel_omnigent_session(
    client: OmnigentHttpClient,
    session_id: str,
) -> None:
    with suppress(Exception):
        await client.interrupt(session_id)
    with suppress(Exception):
        snapshot = await client.get_session(session_id)
        normalized = normalize_omnigent_observation(snapshot)
        if normalized in {
            "created",
            "launching",
            "provisioning",
            "running",
            "waiting",
            "idle",
            "awaiting_approval",
            "intervention_requested",
        }:
            await client.stop_session(session_id)


async def _capture_cancelled_omnigent_session(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    agent_id: str | None,
    initial_snapshot: dict[str, Any] | None,
    first_message_request: dict[str, Any] | None,
    first_message_response: dict[str, Any] | None,
    first_message_posted: bool,
    first_message_response_identifiers: dict[str, str],
    raw_events: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
    capture_policy: dict[str, Any] | None,
    external_state: dict[str, Any] | None = None,
) -> None:
    with suppress(Exception):
        final_snapshot = await client.get_session(session_id)
        await _build_capture_bundle(
            client=client,
            artifact_gateway=artifact_gateway,
            request=request,
            session_id=session_id,
            agent_id=agent_id,
            initial_snapshot=initial_snapshot,
            final_snapshot=final_snapshot or {"status": "canceled"},
            first_message_request=first_message_request,
            first_message_response=first_message_response,
            first_message_posted=first_message_posted,
            first_message_response_identifiers=first_message_response_identifiers,
            raw_events=raw_events,
            normalized_events=normalized_events,
            terminal_status="canceled",
            diagnostics={
                "cancelled": True,
                "failureClass": "system_error",
            },
            harvest_resources=True,
            capture_policy=capture_policy,
            external_state=external_state,
        )


def _first_message_response_identifiers(
    response: dict[str, Any] | None = None,
    *,
    pending_id: object | None = None,
    item_id: object | None = None,
) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    if isinstance(response, dict):
        pending_id = response.get("pending_id", pending_id)
        item_id = response.get("item_id", item_id)
    for key, value in (("pendingId", pending_id), ("itemId", item_id)):
        text = str(value).strip() if value is not None else ""
        if text:
            identifiers[key] = text
    return identifiers


async def _cancel_task(task: asyncio.Task[Any] | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # Expected after requesting cancellation of a helper task.
        pass


async def _publish_active_journals(
    *,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    raw_events: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
) -> tuple[str, str]:
    """Finalize the current crash-safe journal prefix before its DB commit."""

    def jsonl(items: list[dict[str, Any]]) -> str:
        return "".join(
            f"{json.dumps(item, sort_keys=True, default=str)}\n" for item in items
        )

    raw_ref = await artifact_gateway.write_text(
        request=request,
        name="runtime.omnigent.sse.raw.jsonl",
        payload=jsonl(redact_raw_events(raw_events)),
        link_type="runtime.omnigent.sse.raw",
        content_type="application/x-ndjson",
    )
    normalized_ref = await artifact_gateway.write_text(
        request=request,
        name="runtime.omnigent.sse.normalized.jsonl",
        payload=jsonl(normalized_events),
        link_type="runtime.omnigent.sse.normalized",
        content_type="application/x-ndjson",
    )
    return raw_ref, normalized_ref


async def run_omnigent_execution(
    request: AgentExecutionRequest,
    *,
    artifact_gateway: OmnigentArtifactGateway | None = None,
    run_store: OmnigentBridgeSessionStore | None = None,
) -> AgentRunResult:
    """Execute one Omnigent session and return only terminal AgentRunResult."""

    gate = build_omnigent_gate()
    if not gate.enabled:
        raise RuntimeError(
            f"{OMNIGENT_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    client: OmnigentHttpClient | None = None
    session_id = ""
    stream_task: asyncio.Task[None] | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    artifact_gateway = artifact_gateway or LocalOmnigentArtifactGateway()
    first_message: dict[str, Any] | None = None
    first_message_response: dict[str, Any] | None = None
    first_message_posted = False
    first_message_response_identifiers: dict[str, str] = {}
    initial_snapshot: dict[str, Any] | None = None
    raw_events: list[dict[str, Any]] = []
    normalized_events: list[dict[str, Any]] = []
    event_diagnostics: list[dict[str, Any]] = []
    target_agent_id: str | None = None
    delete_after_harvest = False
    capture_policy: dict[str, Any] | None = None
    external_state: dict[str, Any] | None = None
    try:
        # §16 rule 1: authorize the MoonMind principal + workflow + AgentRun +
        # bridge session before any provider call. Fails closed on missing
        # identity through the non-retryable user-error result path below.
        authorization = authorize_bridge_access(request)
        # §16 rule 1: authorize the durable bridge session before any provider
        # call; refuse cross-owner reuse of an idempotency key.
        if run_store is not None:
            assert_bridge_session_binding(
                authorization,
                await run_store.get_binding(request.idempotency_key),
            )
        selection = build_omnigent_selection(request)
        capture_policy = selection.capture
        external_state = _new_external_state_evidence(
            endpoint_ref=selection.endpoint_ref or "default",
            idempotency_key=request.idempotency_key,
        )
        external_state.update(_profile_authorization_evidence(request))
        delete_after_harvest = bool(
            selection.capture.get("deleteOmnigentSessionAfterHarvest", False)
        )
        async with httpx.AsyncClient() as httpx_client:
            client = OmnigentHttpClient(
                base_url=resolved_server_url(),
                api_token=resolved_api_token(),
                client=httpx_client,
                upstream_header_allowlist=resolved_proxy_forward_headers(),
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
            target_agent_id = target.agent_id
            session_payload = build_omnigent_session_create_payload(
                request=request,
                selection=selection,
                target=target,
            )
            session_payload["idempotency_key"] = request.idempotency_key
            labels = session_payload.setdefault("labels", {})
            if isinstance(labels, dict):
                labels.setdefault("moonmind.issue", "MM-1059")

            durable_row = None
            bridge_session_id: str | None = None
            if run_store is not None:
                durable_row = await run_store.get_or_create(
                    request=request,
                    endpoint_ref=str(selection.endpoint_ref or "default"),
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    target_metadata={
                        "hostType": selection.session.host_type,
                        "workspace": selection.session.workspace,
                    },
                )
                bridge_session_id = str(
                    getattr(durable_row, "bridge_session_id", "") or ""
                )
                external_state["bridgeSessionId"] = bridge_session_id
                assert_bridge_session_binding(
                    authorization,
                    BridgeSessionBinding(
                        workflow_id=str(
                            getattr(
                                durable_row,
                                "moonmind_workflow_id",
                                authorization.workflow_id,
                            )
                        ),
                        agent_run_id=str(
                            getattr(
                                durable_row,
                                "moonmind_agent_run_id",
                                authorization.agent_run_id,
                            )
                        ),
                    ),
                )

            retry_state = _heartbeat_state()
            durable_session_id = str(
                getattr(durable_row, "omnigent_session_id", None) or ""
            ).strip()
            heartbeat_session_id = _heartbeat_session_id(retry_state)
            session_id = durable_session_id or heartbeat_session_id
            first_message_posted = bool(retry_state.get("firstMessagePosted"))
            first_message_reconcile_required = False
            if durable_row is not None:
                first_message_reconcile_required = (
                    durable_row.first_message_state == FIRST_MESSAGE_POSTING
                )
                first_message_posted = (
                    durable_row.first_message_state
                    in {FIRST_MESSAGE_POSTED, FIRST_MESSAGE_TERMINAL}
                )
                first_message_response_identifiers = _first_message_response_identifiers(
                    pending_id=getattr(durable_row, "first_message_pending_id", None),
                    item_id=getattr(durable_row, "first_message_item_id", None),
                )
                external_state["firstMessage"]["durableState"] = (
                    durable_row.first_message_state
                )
                if first_message_response_identifiers:
                    external_state["firstMessage"]["responseIdentifiers"] = dict(
                        first_message_response_identifiers
                    )
            if session_id:
                external_state["retry"].update(
                    {
                        "sessionResolution": "attached",
                        "attached": True,
                        "attachSource": (
                            "bridge_session_store"
                            if durable_session_id
                            else "activity_heartbeat"
                        ),
                    }
                )
            if not session_id:
                create_response = await client.create_session(session_payload)
                session_id = _session_id(create_response)
                external_state["retry"].update(
                    {
                        "sessionResolution": "created",
                        "attached": False,
                        "attachSource": None,
                    }
                )
                if run_store is not None:
                    await run_store.attach_session(
                        request.idempotency_key,
                        session_id,
                    )
                _safe_heartbeat(
                    {
                        "omnigentSessionId": session_id,
                        "normalizedStatus": "running",
                        "eventsCaptured": 0,
                        "firstMessagePosted": False,
                    }
                )
            with suppress(Exception):
                initial_snapshot = await client.get_session(session_id)

            first_message = await _build_omnigent_first_message(
                request=request,
                prompt=selection.prompt,
                artifact_gateway=artifact_gateway,
            )
            digest = hashlib.sha256(
                json.dumps(first_message, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            marker = _first_message_marker(request=request, digest=digest)
            first_message.setdefault("metadata", {})[
                "moonmindFirstMessageDigest"
            ] = digest
            first_message["metadata"]["moonmindIdempotencyKey"] = request.idempotency_key
            if selection.prompt.get("includeIdempotencyMarker", True):
                first_message_text = _first_message_text(first_message)
                first_message["data"]["content"][0]["text"] = (
                    f"{first_message_text}\n\n{marker}".strip()
                )
            external_state["firstMessage"].update(
                {
                    "digest": digest,
                    "idempotencyMarkerPresent": selection.prompt.get(
                        "includeIdempotencyMarker", True
                    ),
                    "postedBeforeRetry": first_message_posted,
                    "reconcileRequired": first_message_reconcile_required,
                    "state": "posted" if first_message_posted else "prepared",
                }
            )
            if run_store is not None:
                try:
                    durable_row = await run_store.mark_prepared(
                        request.idempotency_key,
                        digest=digest,
                        marker=marker,
                    )
                    first_message_reconcile_required = (
                        durable_row.first_message_state == FIRST_MESSAGE_POSTING
                    )
                    external_state["firstMessage"]["durableState"] = (
                        durable_row.first_message_state
                    )
                    external_state["firstMessage"][
                        "reconcileRequired"
                    ] = first_message_reconcile_required
                except OmnigentDigestMismatchError as exc:
                    external_state["retry"].update(
                        {
                            "firstMessageOutcome": "unrecoverable_mismatch",
                            "mismatchReason": "digest_mismatch",
                        }
                    )
                    bundle = await _build_capture_bundle(
                        client=client,
                        artifact_gateway=artifact_gateway,
                        request=request,
                        session_id=session_id,
                        agent_id=target.agent_id,
                        initial_snapshot=initial_snapshot,
                        final_snapshot=initial_snapshot or {"status": "failed"},
                        first_message_request=first_message,
                        first_message_response=None,
                        first_message_posted=first_message_posted,
                        first_message_response_identifiers=first_message_response_identifiers,
                        raw_events=raw_events,
                        normalized_events=normalized_events,
                        terminal_status="failed",
                        diagnostics={
                            "error": str(exc),
                            "nonRetryable": True,
                            "failureClass": classify_omnigent_failure(
                                OmnigentFailureReason.FIRST_MESSAGE_DIGEST_MISMATCH
                            ),
                        },
                        harvest_resources=False,
                        external_state=external_state,
                    )
                    return build_omnigent_result(
                        request=request,
                        terminal_status="failed",
                        session_id=session_id,
                        agent_id=target.agent_id,
                        final_snapshot={
                            "status": "failed",
                            "summary": "First-message digest mismatch",
                        },
                        event_count=0,
                        capture_bundle=bundle,
                        failure_summary="First-message digest mismatch",
                        provider_error_code="omnigent_first_message_digest_mismatch",
                        failure_reason=(
                            OmnigentFailureReason.FIRST_MESSAGE_DIGEST_MISMATCH
                        ),
                    )
            stream_queue: asyncio.Queue[dict[str, Any] | BaseException | None] | None = None
            if first_message_reconcile_required:
                reconciliation_snapshot = await client.get_session(session_id)
                if not _snapshot_contains_first_message_marker(
                    reconciliation_snapshot,
                    digest=digest,
                    marker=marker,
                ):
                    external_state["retry"].update(
                        {
                            "firstMessageOutcome": "unrecoverable_mismatch",
                            "mismatchReason": "reconcile_failed",
                            "reconciliationChecked": True,
                            "markerFound": False,
                        }
                    )
                    bundle = await _build_capture_bundle(
                        client=client,
                        artifact_gateway=artifact_gateway,
                        request=request,
                        session_id=session_id,
                        agent_id=target.agent_id,
                        initial_snapshot=initial_snapshot,
                        final_snapshot=reconciliation_snapshot,
                        first_message_request=first_message,
                        first_message_response=None,
                        first_message_posted=first_message_posted,
                        first_message_response_identifiers=first_message_response_identifiers,
                        raw_events=raw_events,
                        normalized_events=normalized_events,
                        terminal_status="failed",
                        diagnostics={
                            "error": "Unable to reconcile first-message posting state",
                            "failureClass": classify_omnigent_failure(
                                OmnigentFailureReason.AMBIGUOUS_POSTING_RECONCILIATION
                            ),
                        },
                        harvest_resources=False,
                        external_state=external_state,
                    )
                    return build_omnigent_result(
                        request=request,
                        terminal_status="failed",
                        session_id=session_id,
                        agent_id=target.agent_id,
                        final_snapshot={
                            "status": "failed",
                            "summary": "Unable to reconcile first-message posting state",
                        },
                        event_count=0,
                        capture_bundle=bundle,
                        failure_summary="Unable to reconcile first-message posting state",
                        provider_error_code="omnigent_first_message_reconcile_failed",
                        failure_reason=(
                            OmnigentFailureReason.AMBIGUOUS_POSTING_RECONCILIATION
                        ),
                    )
                if run_store is not None:
                    await run_store.mark_posted(request.idempotency_key)
                first_message_posted = True
                external_state["retry"].update(
                    {
                        "firstMessageOutcome": "reconciled",
                        "reconciliationChecked": True,
                        "markerFound": True,
                    }
                )
                external_state["firstMessage"]["state"] = "posted"
            if not first_message_posted:
                stream_queue = asyncio.Queue()
                stream_task = asyncio.create_task(
                    _enqueue_stream_events(
                        client=client,
                        session_id=session_id,
                        queue=stream_queue,
                    )
                )
                await asyncio.sleep(0)
                if run_store is not None:
                    await run_store.mark_posting(request.idempotency_key)
                first_message_response = await client.post_event(session_id, first_message)
                first_message_posted = True
                first_message_response_identifiers = _first_message_response_identifiers(
                    first_message_response
                )
                if run_store is not None:
                    await run_store.mark_posted(
                        request.idempotency_key,
                        response=first_message_response,
                    )
                _safe_heartbeat(
                    {
                        "omnigentSessionId": session_id,
                        "normalizedStatus": "running",
                        "eventsCaptured": 0,
                        "firstMessagePosted": True,
                        "firstMessageDigest": digest,
                    }
                )
                external_state["retry"]["firstMessageOutcome"] = "posted"
                external_state["firstMessage"]["state"] = "posted"
            elif external_state["retry"]["firstMessageOutcome"] == "pending":
                external_state["retry"]["firstMessageOutcome"] = "already_posted"
                external_state["firstMessage"]["state"] = "posted"

            event_count = {"value": 0}
            heartbeat_status = {"value": "running"}
            heartbeat_task = asyncio.create_task(
                _periodic_stream_heartbeat(
                    session_id=session_id,
                    event_count=event_count,
                    status=heartbeat_status,
                )
            )
            terminal_status: str | None = None
            try:
                stream_events = (
                    _queued_stream_events(
                        queue=stream_queue,
                        stream_task=stream_task,
                    )
                    if stream_queue is not None and stream_task is not None
                    else client.stream_events(session_id)
                )
                async for event in stream_events:
                    event_count["value"] += 1
                    raw_events.append(dict(event))
                    normalized_bridge_event = build_omnigent_bridge_event(
                        payload=event,
                        sequence=event_count["value"],
                        request=request,
                        omnigent_session_id=session_id,
                        bridge_session_id=bridge_session_id,
                    )
                    if normalized_bridge_event.diagnostic is not None:
                        event_diagnostics.append(normalized_bridge_event.diagnostic)
                    normalized_events.append(normalized_bridge_event.event)
                    if run_store is not None and bridge_session_id:
                        # Durability policy: publish the redacted journals first,
                        # then commit each normalized index row. A crash can leave
                        # an unreferenced artifact, never a DB row whose evidence
                        # does not exist. Per-event commits favor loss bounds over
                        # throughput for this interactive stream.
                        raw_ref, normalized_ref = await _publish_active_journals(
                            artifact_gateway=artifact_gateway,
                            request=request,
                            raw_events=raw_events,
                            normalized_events=normalized_events,
                        )
                        normalized_bridge_event.event["artifactRef"] = normalized_ref
                        await run_store.attach_active_journal_refs(
                            bridge_session_id,
                            raw_ref=raw_ref,
                            normalized_ref=normalized_ref,
                        )
                        await run_store.append_events(
                            bridge_session_id, [normalized_bridge_event.event]
                        )
                    normalized = normalized_bridge_event.event["normalizedStatus"]
                    _safe_heartbeat(
                        {
                            "omnigentSessionId": session_id,
                            "normalizedStatus": normalized,
                            "eventsCaptured": event_count["value"],
                            "firstMessagePosted": True,
                            "eventType": normalized_bridge_event.event["type"],
                        }
                    )
                    if normalized in {"awaiting_approval", "intervention_requested"}:
                        heartbeat_status["value"] = normalized
                        _safe_heartbeat(
                            {
                                "normalizedStatus": normalized,
                                "omnigentSessionId": session_id,
                                "eventsCaptured": event_count["value"],
                                "firstMessagePosted": True,
                            }
                        )
                        continue
                    if normalized in {"completed", "failed", "canceled", "timed_out"}:
                        terminal_status = normalized
                        heartbeat_status["value"] = normalized
                        break
                    if event_count["value"] % 8 == 0:
                        _safe_heartbeat(
                            {
                                "omnigentSessionId": session_id,
                                "normalizedStatus": normalized,
                                "eventsCaptured": event_count["value"],
                                "firstMessagePosted": True,
                            }
                        )
            finally:
                await _cancel_task(heartbeat_task)
                await _cancel_task(stream_task)

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
                    # The stream ended without emitting a terminal event but the
                    # final snapshot is terminal. Append an indexed terminal event
                    # derived from the snapshot so the durable event index records
                    # how the run ended; otherwise diagnostics/Workflow Chat would
                    # see a terminal session row with no terminal event (§7.2).
                    normalized_bridge_event = build_omnigent_bridge_event(
                        payload={
                            "type": "session.final_snapshot",
                            "session": final_snapshot,
                        },
                        sequence=len(normalized_events) + 1,
                        request=request,
                        omnigent_session_id=session_id,
                        bridge_session_id=bridge_session_id,
                    )
                    normalized_events.append(normalized_bridge_event.event)
                    if run_store is not None and bridge_session_id:
                        raw_ref, normalized_ref = await _publish_active_journals(
                            artifact_gateway=artifact_gateway,
                            request=request,
                            raw_events=raw_events,
                            normalized_events=normalized_events,
                        )
                        normalized_bridge_event.event["artifactRef"] = normalized_ref
                        await run_store.attach_active_journal_refs(
                            bridge_session_id,
                            raw_ref=raw_ref,
                            normalized_ref=normalized_ref,
                        )
                        await run_store.append_events(
                            bridge_session_id, [normalized_bridge_event.event]
                        )
                elif normalized_snapshot in _NON_TERMINAL_STATUSES:
                    raise OmnigentSessionStillRunningError(
                        "Omnigent stream ended while the provider session is still running"
                    )
            if terminal_status is None:
                raise OmnigentContractError(
                    "Omnigent stream ended before a terminal session outcome"
                )
            bundle = await _build_capture_bundle(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                agent_id=target.agent_id,
                initial_snapshot=initial_snapshot,
                final_snapshot=final_snapshot,
                first_message_request=first_message,
                first_message_response=first_message_response,
                first_message_posted=first_message_posted,
                first_message_response_identifiers=first_message_response_identifiers,
                raw_events=raw_events,
                normalized_events=normalized_events,
                terminal_status=terminal_status,
                diagnostics={
                    "failureClass": failure_class_for_terminal_status(terminal_status),
                    "eventDiagnostics": event_diagnostics,
                },
                harvest_resources=True,
                external_state=external_state,
                capture_policy=capture_policy,
            )
            if run_store is not None:
                await run_store.mark_terminal(
                    request.idempotency_key,
                    status=terminal_status,
                    terminal_refs=build_omnigent_terminal_refs(bundle),
                    # Persist the full, non-lossy normalized status stream into
                    # the durable event index (OmnigentBridge §7.2).
                    events=normalized_events,
                )
            # §17: an optional resource-harvest failure resolves to
            # completed-with-diagnostics unless policy requires full evidence,
            # in which case the missing required evidence escalates.
            harvest_failure_reason: OmnigentFailureReason | None = None
            if (
                terminal_status == "completed"
                and bundle.resource_harvest_failure_class
            ):
                harvest_failure_reason = (
                    OmnigentFailureReason.OPTIONAL_RESOURCE_HARVEST_FAILED
                )
            return build_omnigent_result(
                request=request,
                terminal_status=terminal_status,
                session_id=session_id,
                agent_id=target.agent_id,
                final_snapshot=final_snapshot,
                event_count=event_count["value"],
                capture_bundle=bundle,
                failure_reason=harvest_failure_reason,
                require_full_evidence=harvest_failure_reason is not None,
                failure_summary=(
                    "Required Omnigent resource evidence was missing after "
                    "session completion"
                    if harvest_failure_reason is not None
                    else None
                ),
                provider_error_code=(
                    "omnigent_required_resource_evidence_missing"
                    if harvest_failure_reason is not None
                    else None
                ),
            )
    except asyncio.CancelledError:
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        if client is not None and session_id:
            async with httpx.AsyncClient() as cleanup_httpx_client:
                cleanup_client = OmnigentHttpClient(
                    base_url=resolved_server_url(),
                    api_token=resolved_api_token(),
                    client=cleanup_httpx_client,
                    upstream_header_allowlist=resolved_proxy_forward_headers(),
                )
                await _cancel_omnigent_session(cleanup_client, session_id)
                await _capture_cancelled_omnigent_session(
                    client=cleanup_client,
                    artifact_gateway=artifact_gateway,
                    request=request,
                    session_id=session_id,
                    agent_id=target_agent_id,
                    initial_snapshot=initial_snapshot,
                    first_message_request=first_message,
                    first_message_response=first_message_response,
                    first_message_posted=first_message_posted,
                    first_message_response_identifiers=first_message_response_identifiers,
                    raw_events=raw_events,
                    normalized_events=normalized_events,
                    capture_policy=capture_policy,
                    external_state=external_state,
                )
                if delete_after_harvest:
                    with suppress(Exception):
                        await cleanup_client.delete_session(session_id)
        raise
    except OmnigentArtifactError as exc:
        # §17: required artifact-persistence failure -> system_error
        # (MoonMind artifact authority failed).
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        failure_class = classify_omnigent_failure(
            OmnigentFailureReason.REQUIRED_ARTIFACT_PERSISTENCE_FAILED
        )
        final_snapshot = {"status": "failed", "summary": str(exc)}
        try:
            bundle = await _build_capture_bundle(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                agent_id=target_agent_id,
                initial_snapshot=initial_snapshot,
                final_snapshot=final_snapshot,
                first_message_request=first_message,
                first_message_response=first_message_response,
                first_message_posted=first_message_posted,
                first_message_response_identifiers=first_message_response_identifiers,
                raw_events=raw_events,
                normalized_events=normalized_events,
                terminal_status="failed",
                diagnostics={
                    "error": str(exc),
                    "failureClass": failure_class,
                    "artifactAuthorityFailed": True,
                },
                harvest_resources=False,
                external_state=external_state,
                capture_policy=capture_policy,
            )
        except OmnigentArtifactError:
            # Artifact authority is unavailable even for evidence capture;
            # still surface the system_error terminal outcome.
            return AgentRunResult(
                summary=_compact_summary(
                    exc, fallback="Omnigent artifact persistence failed"
                ),
                failureClass=failure_class,
                providerErrorCode="omnigent_artifact_persistence_failed",
                metadata={
                    "normalizedStatus": "failed",
                    "providerName": "omnigent",
                    "artifactAuthorityFailed": True,
                },
            )
        return AgentRunResult(
            outputRefs=bundle.output_refs,
            summary=_compact_summary(
                exc, fallback="Omnigent artifact persistence failed"
            ),
            diagnosticsRef=bundle.diagnostics_ref,
            failureClass=failure_class,
            providerErrorCode="omnigent_artifact_persistence_failed",
            metadata={
                "normalizedStatus": "failed",
                "providerName": "omnigent",
                **bundle.metadata_refs,
            },
        )
    except OmnigentAuthorizationError as exc:
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        failure_class = exc.failure_class
        final_snapshot = {"status": "failed", "summary": str(exc)}
        diagnostics = {
            "error": str(exc),
            "failureClass": failure_class,
            "authorizationDenied": True,
        }
        try:
            bundle = await _build_capture_bundle(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                agent_id=target_agent_id,
                initial_snapshot=initial_snapshot,
                final_snapshot=final_snapshot,
                first_message_request=first_message,
                first_message_response=first_message_response,
                first_message_posted=first_message_posted,
                first_message_response_identifiers=first_message_response_identifiers,
                raw_events=raw_events,
                normalized_events=normalized_events,
                terminal_status="failed",
                diagnostics=diagnostics,
                harvest_resources=False,
                external_state=external_state,
                capture_policy=capture_policy,
            )
        except OmnigentArtifactError:
            return AgentRunResult(
                summary=_compact_summary(
                    exc, fallback="Omnigent bridge authorization denied"
                ),
                failureClass=failure_class,
                providerErrorCode="omnigent_authorization_denied",
                metadata={
                    "normalizedStatus": "failed",
                    "providerName": "omnigent",
                    "authorizationDenied": True,
                },
            )
        return AgentRunResult(
            outputRefs=bundle.output_refs,
            summary=_compact_summary(
                exc, fallback="Omnigent bridge authorization denied"
            ),
            diagnosticsRef=bundle.diagnostics_ref,
            failureClass=failure_class,
            providerErrorCode="omnigent_authorization_denied",
            metadata={
                "normalizedStatus": "failed",
                "providerName": "omnigent",
                "authorizationDenied": True,
                **bundle.metadata_refs,
            },
        )
    except (
        OmnigentContractError,
        OmnigentAdapterError,
        ValueError,
    ) as exc:
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        if isinstance(exc, OmnigentAdapterError) and exc.failure_class == classify_omnigent_failure(
            OmnigentFailureReason.INVALID_SESSION_PAYLOAD
        ):
            # §17: invalid session-create payload -> user_error.
            failure_reason = OmnigentFailureReason.INVALID_SESSION_PAYLOAD
            provider_error_code = "omnigent_invalid_session_payload"
        else:
            # Contract/adapter integration faults surface as the §17
            # integration rows (upstream/host register/connect).
            failure_reason = OmnigentFailureReason.HOST_REGISTER_CONNECT
            provider_error_code = "omnigent_contract_error"
        failure_class = classify_omnigent_failure(failure_reason)
        final_snapshot = {"status": "failed", "summary": str(exc)}
        bundle = await _build_capture_bundle(
            client=client,
            artifact_gateway=artifact_gateway,
            request=request,
            session_id=session_id,
            agent_id=target_agent_id,
            initial_snapshot=initial_snapshot,
            final_snapshot=final_snapshot,
            first_message_request=first_message,
            first_message_response=first_message_response,
            first_message_posted=first_message_posted,
            first_message_response_identifiers=first_message_response_identifiers,
            raw_events=raw_events,
            normalized_events=normalized_events,
            terminal_status="failed",
            diagnostics={
                "error": str(exc),
                "failureClass": failure_class,
            },
            harvest_resources=bool(client and session_id),
            external_state=external_state,
            capture_policy=capture_policy,
        )
        return AgentRunResult(
            outputRefs=bundle.output_refs,
            summary=_compact_summary(exc, fallback="Omnigent contract error"),
            diagnosticsRef=bundle.diagnostics_ref,
            failureClass=failure_class,
            providerErrorCode=provider_error_code,
            metadata={
                "normalizedStatus": "failed",
                "providerName": "omnigent",
                **bundle.metadata_refs,
            },
        )
    except OmnigentSessionStillRunningError:
        raise
    except (OmnigentClientError, httpx.HTTPError) as exc:
        # §17 transport rows: upstream unreachable / host register-connect /
        # auth failure map to integration_error, while 4xx client-input
        # failures (invalid session payload) map to user_error. The client
        # already classified this via the shared §17 classifier and preserves
        # redacted host/server diagnostics through OmnigentClientError.
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        status_code = exc.status_code if isinstance(exc, OmnigentClientError) else None
        transport_failure_class = classify_omnigent_failure(
            OmnigentFailureReason.HOST_REGISTER_CONNECT
        )
        failure_class = (
            exc.failure_class
            if isinstance(exc, OmnigentClientError)
            else transport_failure_class
        )
        final_snapshot = {"status": "failed", "summary": str(exc)}
        diagnostics = (
            exc.diagnostics()
            if isinstance(exc, OmnigentClientError)
            else {"error": str(exc), "failureClass": transport_failure_class}
        )
        bundle = await _build_capture_bundle(
            client=client,
            artifact_gateway=artifact_gateway,
            request=request,
            session_id=session_id,
            agent_id=target_agent_id,
            initial_snapshot=initial_snapshot,
            final_snapshot=final_snapshot,
            first_message_request=first_message,
            first_message_response=first_message_response,
            first_message_posted=first_message_posted,
            first_message_response_identifiers=first_message_response_identifiers,
            raw_events=raw_events,
            normalized_events=normalized_events,
            terminal_status="failed",
            diagnostics=diagnostics,
            harvest_resources=bool(client and session_id),
            external_state=external_state,
            capture_policy=capture_policy,
        )
        return AgentRunResult(
            outputRefs=bundle.output_refs,
            summary=_compact_summary(exc, fallback="Omnigent integration error"),
            diagnosticsRef=bundle.diagnostics_ref,
            failureClass=failure_class,
            providerErrorCode=str(status_code or "omnigent_http_error"),
            metadata={
                "normalizedStatus": "failed",
                "providerName": "omnigent",
                **bundle.metadata_refs,
            },
        )


__all__ = [
    "OmnigentContractError",
    "OmnigentSessionStillRunningError",
    "normalize_omnigent_observation",
    "run_omnigent_execution",
]
