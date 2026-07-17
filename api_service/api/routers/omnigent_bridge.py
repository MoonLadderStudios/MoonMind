"""Omnigent Bridge Session API Facade (proxy mode).

MM-1155 (source: MM-1140): expose/proxy the Omnigent-shaped session routes
described in ``docs/Omnigent/OmnigentBridge.md`` (§4.1, §5.1, §8) at the
configured mount path. This router is the Session API Facade; the durable
create/attach/validate/forward behavior lives in
``moonmind.omnigent.bridge_proxy`` (the Host Protocol Facade/Proxy).

In proxy mode the facade forwards to a stock Omnigent Server. It authenticates
the MoonMind principal, validates workflow ownership for session creation, and
maps bridge failure classes onto HTTP status codes.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from api_service.api.execution_principal import (
    execution_principal_dependency,
    resolve_execution_principal,
)
from api_service.api.routers.executions import _get_service as _get_execution_service
from api_service.auth_providers import get_current_user
from api_service.db.base import async_session_maker
from api_service.db.models import User
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    HOST_PROTOCOL_MODE_PROXY,
    OmnigentBridgeConfig,
    resolve_bridge_config,
)
from moonmind.omnigent.bridge_embedded import (
    EmbeddedHostHeartbeatRequest,
    EmbeddedHostRegisterRequest,
    EmbeddedHostSessionEventRequest,
    OmnigentEmbeddedHostProtocolFacade,
    verify_embedded_host_auth,
)
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    BridgeSessionEventRequest,
    OmnigentBridgeError,
    OmnigentBridgeSessionProxy,
)
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.settings import (
    OMNIGENT_DISABLED_MESSAGE,
    build_omnigent_gate,
    resolved_api_token,
    resolved_default_agent_name,
    resolved_host_runner_token,
    resolved_server_url,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

# The bridge is exposed at the operator-declared mount path (OB-§6, §21.1). The
# route table and enablement are read from the operator-declared declarative
# bridge configuration (OMNIGENT_BRIDGE_CONFIG_PATH) before routes are mounted,
# so a deployment that disables the bridge, selects a non-proxy mode, or mounts
# at a custom path is honored rather than always exposing the default surface.
_BRIDGE_CONFIG = resolve_bridge_config()
_ROUTES = _BRIDGE_CONFIG.public_api.routes

OMNIGENT_BRIDGE_MOUNT_PATH = _BRIDGE_CONFIG.public_api.mount_path


def get_bridge_config() -> OmnigentBridgeConfig:
    """Return the resolved, immutable bridge configuration."""

    return _BRIDGE_CONFIG

router = APIRouter(tags=["Omnigent Bridge"])

_FAILURE_CLASS_STATUS = {
    "user_error": status.HTTP_400_BAD_REQUEST,
    "integration_error": status.HTTP_502_BAD_GATEWAY,
    "system_error": status.HTTP_500_INTERNAL_SERVER_ERROR,
}

_WORKFLOW_ID_LABEL = "moonmind.workflow_id"
_AGENT_RUN_ID_LABEL = "moonmind.agent_run_id"
_CORRELATION_ID_LABEL = "moonmind.correlation_id"
_IDEMPOTENCY_KEY_LABEL = "moonmind.idempotency_key"


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _require_bridge_enabled() -> OmnigentBridgeConfig:
    """Fail fast when the bridge is disabled."""

    if not _BRIDGE_CONFIG.enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "omnigent_bridge_disabled",
                "message": "The Omnigent bridge is disabled.",
            },
        )
    return _BRIDGE_CONFIG


def _require_proxy_mode(
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
) -> OmnigentBridgeConfig:
    """Fail fast when a proxy-only route is called outside proxy mode."""

    if config.host_protocol_mode != HOST_PROTOCOL_MODE_PROXY:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "code": "omnigent_bridge_mode_unsupported",
                "message": (
                    "This Omnigent bridge route requires "
                    "upstream_omnigent_server_proxy mode."
                ),
            },
        )
    return config


def _require_embedded_mode(
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
) -> OmnigentBridgeConfig:
    """Fail fast when an embedded-host route is called outside embedded mode."""

    if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
        return config
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "omnigent_bridge_mode_unsupported",
            "message": (
                "This Omnigent bridge route requires "
                "embedded_omnigent_compatible_server mode."
            ),
        },
    )


def _get_bridge_proxy(
    _config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
) -> OmnigentBridgeSessionProxy | None:
    """Build the proxy-mode bridge over the configured stock Omnigent Server."""

    if _config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
        return None
    if _config.host_protocol_mode != HOST_PROTOCOL_MODE_PROXY:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "code": "omnigent_bridge_mode_unsupported",
                "message": "Unsupported Omnigent bridge host protocol mode.",
            },
        )
    gate = build_omnigent_gate()
    if not gate.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "omnigent_disabled",
                "message": (
                    f"{OMNIGENT_DISABLED_MESSAGE} (missing: "
                    f"{', '.join(gate.missing)})"
                ),
            },
        )
    client = OmnigentHttpClient(
        base_url=resolved_server_url(),
        api_token=resolved_api_token(),
    )
    return OmnigentBridgeSessionProxy(
        run_store=OmnigentBridgeSessionStore(async_session_maker),
        client=client,
        config=_config,
        default_agent_name=resolved_default_agent_name(),
    )


def _get_bridge_store(
    _config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
) -> OmnigentBridgeSessionStore:
    return OmnigentBridgeSessionStore(async_session_maker)


def _get_embedded_host_facade(
    _config: OmnigentBridgeConfig = Depends(_require_embedded_mode),
) -> OmnigentEmbeddedHostProtocolFacade:
    return OmnigentEmbeddedHostProtocolFacade(
        run_store=OmnigentBridgeSessionStore(async_session_maker),
        config=_config,
    )


def _get_create_embedded_facade(
    _config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
) -> OmnigentEmbeddedHostProtocolFacade | None:
    if _config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED:
        return None
    return OmnigentEmbeddedHostProtocolFacade(
        run_store=OmnigentBridgeSessionStore(async_session_maker),
        config=_config,
    )

def _http_error_from_bridge(exc: OmnigentBridgeError) -> HTTPException:
    status_code = exc.status_code or _FAILURE_CLASS_STATUS.get(
        exc.failure_class, status.HTTP_500_INTERNAL_SERVER_ERROR
    )
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.failure_class, "message": str(exc)},
    )


def _embedded_auth_context(
    *,
    request: Request,
    config: OmnigentBridgeConfig,
):
    try:
        return verify_embedded_host_auth(
            headers=request.headers,
            config=config,
            configured_token=resolved_host_runner_token(),
        )
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


async def _resolve_bridge_binding(
    *,
    user: User,
    service: Any,
    principal_context: dict[str, Any],
    payload: BridgeSessionCreateRequest,
) -> BridgePrincipalBinding:
    """Validate the MoonMind principal + workflow ownership (OB-§8.2 step 1)."""

    labels = payload.labels or {}
    idempotency_key = _clean(labels.get(_IDEMPOTENCY_KEY_LABEL))
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "user_error",
                "message": (
                    f"labels['{_IDEMPOTENCY_KEY_LABEL}'] is required to "
                    "create or reuse a bridge session"
                ),
            },
        )
    workflow_id = _clean(labels.get(_WORKFLOW_ID_LABEL)) or _clean(
        principal_context.get("workflow_id_header")
    )
    if not workflow_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "user_error",
                "message": (
                    f"labels['{_WORKFLOW_ID_LABEL}'] is required to validate "
                    "workflow ownership"
                ),
            },
        )

    principal = await resolve_execution_principal(
        user=user,
        service=service,
        request=principal_context.get("request"),
        workflow_id_header=workflow_id,
        run_id_header=principal_context.get("run_id_header"),
        agent_run_id_header=(
            _clean(labels.get(_AGENT_RUN_ID_LABEL))
            or principal_context.get("agent_run_id_header")
        ),
    )
    if not principal.workflow_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "workflow_ownership_denied",
                "message": (
                    "The authenticated principal does not own the referenced "
                    "workflow."
                ),
            },
        )

    return BridgePrincipalBinding(
        workflow_id=principal.workflow_id,
        correlation_id=_clean(labels.get(_CORRELATION_ID_LABEL)) or idempotency_key,
        idempotency_key=idempotency_key,
        agent_run_id=principal.agent_run_id or _clean(labels.get(_AGENT_RUN_ID_LABEL)),
    )


@router.post(_ROUTES.create_session, response_model=dict)
async def create_omnigent_session(
    payload: BridgeSessionCreateRequest,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    principal_context: dict[str, Any] = Depends(execution_principal_dependency),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> dict[str, Any]:
    """Create or reuse an Omnigent-shaped session in the configured bridge mode."""

    binding = await _resolve_bridge_binding(
        user=user,
        service=service,
        principal_context=principal_context,
        payload=payload,
    )
    try:
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
            if embedded_facade is None:
                raise OmnigentBridgeError(
                    "Embedded Omnigent bridge facade is unavailable",
                    failure_class="system_error",
                    status_code=501,
                )
            return await embedded_facade.create_session(request=payload, binding=binding)
        if proxy is None:
            raise OmnigentBridgeError(
                "Omnigent proxy is unavailable for the configured bridge mode",
                failure_class="system_error",
                status_code=501,
            )
        return await proxy.create_session(request=payload, binding=binding)
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.get(_ROUTES.get_session, response_model=dict)
async def get_omnigent_session(
    session_id: str,
    _enabled: OmnigentBridgeConfig = Depends(_require_proxy_mode),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy = Depends(_get_bridge_proxy),
) -> dict[str, Any]:
    """Return an Omnigent-shaped session snapshot (OB-§4.1, §8.2).

    Enforces the §16 rule-1 authorization boundary on direct reads: unlike the
    create path, the raw provider ``session_id`` is caller-supplied, so the
    facade must confirm the caller owns the workflow that owns the session
    before proxying the read with the service credential. This closes the IDOR
    where any authenticated user could read any session snapshot by id.

    Ownership is resolved against the durable bridge binding (not caller-
    supplied task-identity headers), so the read requires no header parameters:
    the authenticated user must own the workflow that owns the session.
    """

    owner = await proxy.get_session_owner(session_id)
    if owner is None:
        # The bridge only exposes sessions it created/attached; an id it does
        # not own is not proxied upstream (avoids leaking arbitrary sessions).
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "omnigent_bridge_session_unknown",
                "message": (
                    "No Omnigent bridge session is bound to the requested "
                    "session id."
                ),
            },
        )

    principal = await resolve_execution_principal(
        user=user,
        service=service,
        workflow_id_header=owner.workflow_id,
    )
    if not principal.workflow_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "workflow_ownership_denied",
                "message": (
                    "The authenticated principal does not own the workflow "
                    "that owns this Omnigent session."
                ),
            },
        )

    try:
        return await proxy.get_session(session_id)
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


async def _authorize_session_control(
    *,
    session_id: str,
    user: User,
    service: Any,
    proxy: OmnigentBridgeSessionProxy,
) -> None:
    owner = await proxy.get_session_owner(session_id)
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "omnigent_bridge_session_unknown",
                "message": (
                    "No Omnigent bridge session is bound to the requested "
                    "session id."
                ),
            },
        )
    principal = await resolve_execution_principal(
        user=user,
        service=service,
        workflow_id_header=owner.workflow_id,
        agent_run_id_header=owner.agent_run_id,
    )
    if not principal.workflow_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "workflow_ownership_denied",
                "message": (
                    "The authenticated principal does not own the workflow "
                    "that owns this Omnigent session."
                ),
            },
        )


async def _authorize_bridge_session_projection(
    *,
    bridge_session_id: str,
    user: User,
    service: Any,
    store: OmnigentBridgeSessionStore,
) -> None:
    owner = await store.get_bridge_session_owner(bridge_session_id)
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "omnigent_bridge_session_unknown",
                "message": (
                    "No Omnigent bridge session is bound to the requested "
                    "bridge session id."
                ),
            },
        )
    principal = await resolve_execution_principal(
        user=user,
        service=service,
        workflow_id_header=owner.workflow_id,
        agent_run_id_header=owner.agent_run_id,
    )
    if not principal.workflow_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "workflow_ownership_denied",
                "message": (
                    "The authenticated principal does not own the workflow "
                    "that owns this Omnigent bridge session."
                ),
            },
        )


_BRIDGE_TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled", "timed_out"})


def _bridge_event_kind(event_type: str | None) -> str:
    raw = str(event_type or "").strip()
    if raw in {"session.created", "session.started"}:
        return "session_started"
    if raw.startswith("session.input") or raw in {"message.sent", "input.message"}:
        return "user_message_submitted"
    if raw in {"response.delta", "response.output.delta"} or raw.endswith(".delta"):
        return "assistant_message_delta"
    if raw.startswith("response.output") or raw in {"response.message", "message.received"}:
        return "assistant_message"
    if raw in {"response.completed", "completed", "stream.done"}:
        return "response_completed"
    if raw in {"response.failed", "failed"}:
        return "response_failed"
    if raw in {"response.elicitation_request", "elicitation_request"}:
        return "approval_requested"
    if "approval" in raw or "elicitation" in raw:
        return "approval_requested"
    if "interrupt" in raw or "stop" in raw:
        return "turn_interrupted"
    if raw.startswith("session.item") or raw.startswith("tool."):
        if "failed" in raw:
            return "tool_call_failed"
        if "output" in raw or "result" in raw or "completed" in raw:
            return "tool_call_output"
        return "tool_call_started"
    if raw.startswith("resource."):
        return "summary_published"
    return raw.replace(".", "_") or "system_annotation"


def _bridge_event_stream(direction: str, event_type: str | None) -> str:
    if str(direction or "").strip() == "moonmind_to_host":
        return "stdout"
    event_type_str = str(event_type or "")
    if event_type_str.startswith("session.") or event_type_str.startswith("resource."):
        return "session"
    return "stdout"


def _bridge_event_text(row: Any) -> str:
    if row.text_preview:
        return row.text_preview
    if row.artifact_ref:
        return "Bridge artifact available."
    event_type = str(row.event_type or "")
    return event_type.replace(".", " ") or "Bridge session event."


def _bridge_event_payload(row: Any) -> dict[str, Any]:
    metadata = dict(row.metadata_ or {})
    if row.artifact_ref:
        metadata.setdefault("artifactRef", row.artifact_ref)
    metadata.setdefault("source", "omnigent_bridge")
    metadata.setdefault("sourceKind", row.event_type)
    return {
        "id": row.event_id,
        "sequence": row.sequence,
        "timestamp": row.timestamp.isoformat(),
        "stream": _bridge_event_stream(row.direction, row.event_type),
        "text": _bridge_event_text(row),
        "kind": _bridge_event_kind(row.event_type),
        "bridgeSessionId": row.bridge_session_id,
        "sessionId": row.bridge_session_id,
        "session_id": row.bridge_session_id,
        "normalizedStatus": row.normalized_status,
        "artifactRef": row.artifact_ref,
        "metadata": metadata,
    }


@router.get("/bridge-sessions/resolve", response_model=dict)
async def resolve_omnigent_bridge_session_projection(
    workflow_id: str | None = Query(default=None, alias="workflowId"),
    agent_run_id: str | None = Query(default=None, alias="agentRunId"),
    idempotency_key: str | None = Query(default=None, alias="idempotencyKey"),
    _enabled: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store),
) -> dict[str, Any]:
    """Resolve the bridge session Workflow Chat should read before legacy logs."""

    row = await store.resolve_projection_session(
        workflow_id=workflow_id,
        agent_run_id=agent_run_id,
        idempotency_key=idempotency_key,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "omnigent_bridge_session_unknown"},
        )
    await _authorize_bridge_session_projection(
        bridge_session_id=row.bridge_session_id,
        user=user,
        service=service,
        store=store,
    )
    return {
        "bridgeSessionId": row.bridge_session_id,
        "workflowId": row.moonmind_workflow_id,
        "agentRunId": row.moonmind_agent_run_id,
        "idempotencyKey": row.idempotency_key,
        "status": row.status,
    }


@router.get("/bridge-sessions/{bridge_session_id}/events", response_model=dict)
async def list_omnigent_bridge_session_events(
    bridge_session_id: str,
    after: int | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    _enabled: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store),
) -> dict[str, Any]:
    """List Workflow Chat projection events for one bridge session (§15)."""

    await _authorize_bridge_session_projection(
        bridge_session_id=bridge_session_id,
        user=user,
        service=service,
        store=store,
    )
    events = await store.list_events_page(
        bridge_session_id,
        after=after,
        limit=limit,
    )
    page = events[:limit]
    has_more = len(events) > limit
    expected_first_sequence = (after or 0) + 1
    retention_gap = bool(page and page[0].sequence > expected_first_sequence)
    return {
        "bridgeSessionId": bridge_session_id,
        "events": [_bridge_event_payload(row) for row in page],
        "nextCursor": page[-1].sequence if has_more and page else None,
        "truncated": has_more,
        "retentionGap": retention_gap,
    }


@router.get("/bridge-sessions/{bridge_session_id}/stream")
async def stream_omnigent_bridge_session_events(
    bridge_session_id: str,
    since: int | None = Query(default=None),
    _enabled: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store),
) -> StreamingResponse:
    """Stream bridge session projection events as server-sent events (§15)."""

    await _authorize_bridge_session_projection(
        bridge_session_id=bridge_session_id,
        user=user,
        service=service,
        store=store,
    )
    async def _event_stream():
        last_sequence = since
        idle_polls = 0
        while True:
            rows = await store.list_events(bridge_session_id)
            if last_sequence is not None:
                rows = [row for row in rows if row.sequence > last_sequence]
            rows.sort(key=lambda row: row.sequence)
            for row in rows:
                last_sequence = row.sequence
                idle_polls = 0
                payload = json.dumps(_bridge_event_payload(row), separators=(",", ":"))
                yield f"event: bridge_event\ndata: {payload}\n\n"
                if str(row.normalized_status or "").strip() in _BRIDGE_TERMINAL_STATUSES:
                    return
            idle_polls += 1
            yield ": keepalive\n\n"
            await asyncio.sleep(1.0 if idle_polls < 30 else 5.0)

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@router.post(_ROUTES.post_event, response_model=dict)
async def post_omnigent_session_event(
    session_id: str,
    payload: BridgeSessionEventRequest,
    _enabled: OmnigentBridgeConfig = Depends(_require_proxy_mode),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy = Depends(_get_bridge_proxy),
) -> dict[str, Any]:
    """Apply Omnigent controls, including bridge-local harvest/clear policy."""

    await _authorize_session_control(
        session_id=session_id,
        user=user,
        service=service,
        proxy=proxy,
    )
    try:
        return await proxy.post_event(session_id=session_id, event=payload)
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.post(
    _ROUTES.resolve_elicitation,
    response_model=dict,
)
async def resolve_omnigent_elicitation(
    session_id: str,
    elicitation_id: str,
    payload: dict[str, Any],
    _enabled: OmnigentBridgeConfig = Depends(_require_proxy_mode),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy = Depends(_get_bridge_proxy),
) -> dict[str, Any]:
    """Resolve a pending Omnigent elicitation through the bridge surface."""

    await _authorize_session_control(
        session_id=session_id,
        user=user,
        service=service,
        proxy=proxy,
    )
    try:
        return await proxy.resolve_elicitation(
            session_id=session_id,
            elicitation_id=elicitation_id,
            payload=payload,
        )
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.get(_ROUTES.agents, response_model=list)
async def list_omnigent_agents(
    _enabled: OmnigentBridgeConfig = Depends(_require_proxy_mode),
    _user: User = Depends(get_current_user()),
    proxy: OmnigentBridgeSessionProxy = Depends(_get_bridge_proxy),
) -> list[dict[str, Any]]:
    """Proxy the Omnigent agent catalog (OB-§4.1)."""

    try:
        return await proxy.list_agents()
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.post("/v1/hosts/register", response_model=dict)
async def register_embedded_omnigent_host(
    payload: EmbeddedHostRegisterRequest,
    request: Request,
    config: OmnigentBridgeConfig = Depends(_require_embedded_mode),
    facade: OmnigentEmbeddedHostProtocolFacade = Depends(_get_embedded_host_facade),
) -> dict[str, Any]:
    """Register an unchanged host against MoonMind's embedded host facade."""

    auth = _embedded_auth_context(request=request, config=config)
    try:
        return await facade.register_host(request=payload, auth=auth)
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.post("/v1/hosts/{host_id}/heartbeat", response_model=dict)
async def heartbeat_embedded_omnigent_host(
    host_id: str,
    payload: EmbeddedHostHeartbeatRequest,
    request: Request,
    config: OmnigentBridgeConfig = Depends(_require_embedded_mode),
    facade: OmnigentEmbeddedHostProtocolFacade = Depends(_get_embedded_host_facade),
) -> dict[str, Any]:
    """Accept a host heartbeat through the embedded host facade."""

    auth = _embedded_auth_context(request=request, config=config)
    try:
        return await facade.heartbeat(host_id=host_id, request=payload, auth=auth)
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.post("/v1/hosts/{host_id}/sessions/{session_id}/events", response_model=dict)
async def ingest_embedded_omnigent_host_event(
    host_id: str,
    session_id: str,
    payload: EmbeddedHostSessionEventRequest,
    request: Request,
    config: OmnigentBridgeConfig = Depends(_require_embedded_mode),
    facade: OmnigentEmbeddedHostProtocolFacade = Depends(_get_embedded_host_facade),
) -> dict[str, Any]:
    """Ingest host/session events into the canonical bridge projection."""

    auth = _embedded_auth_context(request=request, config=config)
    try:
        return await facade.ingest_session_event(
            host_id=host_id,
            session_id=session_id,
            request=payload,
            auth=auth,
        )
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


__all__ = [
    "OMNIGENT_BRIDGE_MOUNT_PATH",
    "router",
]
