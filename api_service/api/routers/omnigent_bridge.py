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
from datetime import timedelta
from typing import Any, Literal

from fastapi import (
    APIRouter, Depends, Header, HTTPException, Query, Request, WebSocket,
    WebSocketDisconnect, status,
)
from fastapi.responses import Response, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

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
from moonmind.omnigent.bridge_store import (
    BridgeProjectionAmbiguousError,
    OmnigentBridgeSessionStore,
    OmnigentIdempotencyError,
)
from moonmind.omnigent.embedded_evidence import (
    EmbeddedEvidenceError,
    validate_embedded_evidence,
)
from moonmind.omnigent.embedded_host_channel import (
    EmbeddedHostChannelError,
    embedded_host_channels,
)
from moonmind.omnigent.host_protocol_adapter import UpstreamHostProtocolError
from moonmind.omnigent.host_auth_profile import (
    HostAuthProfileError,
    HostAuthCredentialProfile,
    host_auth_readiness,
    load_host_auth_profile,
    resolve_host_auth_credentials,
)
from moonmind.omnigent.host_auth_store import HostAuthProfileStore
from moonmind.omnigent.settings import (
    OMNIGENT_DISABLED_MESSAGE,
    build_omnigent_gate,
    resolved_api_token,
    resolved_default_agent_name,
    resolved_proxy_forward_headers,
    resolved_server_url,
)
from moonmind.utils.build_info import resolve_moonmind_build_id
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
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


def _host_auth_store() -> HostAuthProfileStore:
    return HostAuthProfileStore(async_session_maker)


async def _active_host_auth_profile() -> HostAuthCredentialProfile:
    managed = await _host_auth_store().get_active()
    return managed or load_host_auth_profile()


async def embedded_host_auth_preflight() -> dict[str, Any]:
    """Evaluate the selected embedded contract at the enablement boundary."""

    if (
        not _BRIDGE_CONFIG.enabled
        or _BRIDGE_CONFIG.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED
    ):
        return {"ready": True, "code": "host_auth_not_selected"}
    try:
        profile = await _active_host_auth_profile()
    except HostAuthProfileError as exc:
        return {"ready": False, "code": exc.code}
    return await host_auth_readiness(profile=profile)


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


class OmnigentPublicErrorDetail(BaseModel):
    """Stable error detail shared by proxy and embedded public routes."""

    model_config = ConfigDict(extra="allow")
    code: str
    message: str | None = None


class OmnigentPublicErrorResponse(BaseModel):
    detail: OmnigentPublicErrorDetail


class HostAuthProfilePutRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    profile_id: str = Field(alias="profileId", min_length=1, max_length=128)
    current_secret_ref: str = Field(alias="currentSecretRef", min_length=1)
    current_generation: int = Field(1, alias="currentGeneration", ge=1)


class HostAuthRotateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    new_secret_ref: str = Field(alias="newSecretRef", min_length=1)
    overlap_seconds: int = Field(900, alias="overlapSeconds", ge=0, le=900)


class OmnigentMoonMindBinding(BaseModel):
    """Mode-neutral durable ownership metadata added to session responses."""

    model_config = ConfigDict(extra="allow")
    workflow_id: str | None = Field(default=None, alias="workflowId")
    agent_run_id: str | None = Field(default=None, alias="agentRunId")
    bridge_session_id: str | None = Field(default=None, alias="bridgeSessionId")
    host_protocol_mode: str | None = Field(default=None, alias="hostProtocolMode")


class OmnigentSessionResponse(BaseModel):
    """Supported common session snapshot/create/attach response profile."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    id: str
    status: str | None = None
    terminal: bool | None = None
    moonmind: OmnigentMoonMindBinding | None = None


class OmnigentAgentResponse(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    id: str = Field(validation_alias=AliasChoices("id", "agentId", "agent_id"))
    name: str | None = None
    ready: bool | None = None


class OmnigentHostResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    status: str | None = None
    ready: bool | None = None


class OmnigentOperationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    ok: bool | None = None


class OmnigentStreamEvent(BaseModel):
    """Typed canonical event envelope used when MoonMind supplies a schema."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    schema_version: Literal["moonmind.omnigent_bridge.event.v1"] | None = Field(
        default=None, alias="schemaVersion"
    )
    type: str
    sequence: int | None = None
    status: str | None = None
    terminal: bool | None = None


_PUBLIC_ERROR_RESPONSES = {
    code: {"model": OmnigentPublicErrorResponse}
    for code in (400, 403, 404, 409, 500, 501, 502, 503)
}


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


@router.get("/readiness", response_model=dict)
async def get_omnigent_bridge_readiness(
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    _user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    """Expose selected protocol and conformance gates without secret material."""

    if config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED:
        return config.readiness()
    readiness = config.readiness(
        evidence_validation=await _resolve_embedded_evidence(config)
    )
    try:
        profile = await _active_host_auth_profile()
        auth = await host_auth_readiness(profile=profile)
    except HostAuthProfileError as exc:
        auth = {"ready": False, "code": exc.code}
    readiness["hostAuthentication"] = auth
    if not auth["ready"]:
        readiness["conformanceState"] = "gated"
    return readiness


_EMBEDDED_EVIDENCE_SLOTS = {
    "proxyConformance": ("proxy_conformance", "proxy_conformance_evidence_ref"),
    "liveSmoke": ("live_smoke", "live_smoke_evidence_ref"),
    "hostAuthConformance": (
        "host_auth_conformance",
        "host_auth_conformance_evidence_ref",
    ),
}


def _artifact_id_from_evidence_ref(value: str | None) -> str:
    candidate = str(value or "").strip()
    if candidate.startswith("artifact://"):
        candidate = candidate[len("artifact://") :]
    if not candidate or "/" in candidate or "?" in candidate or "#" in candidate:
        raise EmbeddedEvidenceError("evidence ref must identify one MoonMind artifact")
    return candidate


async def _resolve_embedded_evidence(
    config: OmnigentBridgeConfig,
) -> dict[str, dict[str, Any]]:
    """Resolve claims with the artifact service's trusted service principal."""

    results: dict[str, dict[str, Any]] = {}
    embedded = config.host_connection.embedded
    build_identity = resolve_moonmind_build_id()
    if not build_identity:
        return {
            key: {"status": "failed", "reason": "moonmind_build_identity_missing"}
            for key in _EMBEDDED_EVIDENCE_SLOTS
        }
    async with async_session_maker() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        for key, (claim_type, attribute) in _EMBEDDED_EVIDENCE_SLOTS.items():
            try:
                artifact_id = _artifact_id_from_evidence_ref(
                    getattr(embedded, attribute)
                )
                _artifact, body = await service.read(
                    artifact_id=artifact_id,
                    principal="service:omnigent-embedded-evidence-gate",
                )
                claim = validate_embedded_evidence(
                    body,
                    expected_claim_type=claim_type,
                    moonmind_build_identity=build_identity,
                    bridge_config_sha256=config.evidence_policy_sha256(),
                )
                results[key] = {
                    "status": "passed",
                    "schemaVersion": claim.schema_version,
                    "generatedAt": claim.generated_at.isoformat(),
                    "expiresAt": claim.expires_at.isoformat(),
                }
            except Exception:  # noqa: BLE001 - every resolver failure gates mode
                # Read/auth/schema failures intentionally share one bounded,
                # non-enumerating public result.
                results[key] = {
                    "status": "failed",
                    "reason": "evidence_unavailable_or_invalid",
                }
    return results


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


async def _require_embedded_mode(
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
) -> OmnigentBridgeConfig:
    """Fail fast when an embedded-host route is called outside embedded mode."""

    if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
        validation = await _resolve_embedded_evidence(config)
        readiness = config.readiness(evidence_validation=validation)
        if readiness["conformanceState"] == "ready":
            return config
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "omnigent_embedded_evidence_gated",
                "message": "Embedded mode requires authorized, current passing evidence.",
                "evidenceValidation": validation,
            },
        )
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
    request: Request,
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
                    f"{OMNIGENT_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
                ),
            },
        )
    client = OmnigentHttpClient(
        base_url=resolved_server_url(),
        api_token=resolved_api_token(),
        forward_headers=request.headers,
        upstream_header_allowlist=resolved_proxy_forward_headers(),
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


async def _require_mode_transition_safe(
    payload: BridgeSessionCreateRequest,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store),
) -> OmnigentBridgeConfig:
    """Prevent a configured mode change from orphaning active session owners."""

    idempotency_key = _clean(payload.labels.get(_IDEMPOTENCY_KEY_LABEL))
    active_modes = await store.active_host_protocol_modes(
        exclude_idempotency_key=idempotency_key
    )
    conflicts = {
        mode: count
        for mode, count in active_modes.items()
        if mode != config.host_protocol_mode
    }
    if conflicts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "omnigent_bridge_mode_transition_blocked",
                "message": (
                    "The configured Omnigent host protocol mode cannot take "
                    "ownership while active sessions belong to another or an "
                    "unknown mode. Drain or terminalize those sessions first."
                ),
                "selectedMode": config.host_protocol_mode,
                "activeSessionModes": conflicts,
            },
        )
    return config


def _get_embedded_host_facade(
    _config: OmnigentBridgeConfig = Depends(_require_embedded_mode),
) -> OmnigentEmbeddedHostProtocolFacade:
    return OmnigentEmbeddedHostProtocolFacade(
        run_store=OmnigentBridgeSessionStore(async_session_maker),
        config=_config,
    )


async def _get_create_embedded_facade(
    _config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
) -> OmnigentEmbeddedHostProtocolFacade | None:
    if _config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED:
        return None
    await _require_embedded_mode(_config)
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
        detail={
            "code": exc.code,
            "failureClass": exc.failure_class,
            "message": str(exc),
        },
    )


async def _embedded_auth_context(
    *,
    request: Request,
    config: OmnigentBridgeConfig,
):
    try:
        resolved = await resolve_host_auth_credentials(
            profile=await _active_host_auth_profile()
        )
        return verify_embedded_host_auth(
            headers=request.headers,
            config=config,
            configured_credentials=resolved.tokens_by_generation,
            credential_profile_id=resolved.profile.profile_id,
        )
    except HostAuthProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "failureClass": "system_error"},
        ) from exc
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
                    "The authenticated principal does not own the referenced workflow."
                ),
            },
        )

    return BridgePrincipalBinding(
        workflow_id=principal.workflow_id,
        correlation_id=_clean(labels.get(_CORRELATION_ID_LABEL)) or idempotency_key,
        idempotency_key=idempotency_key,
        agent_run_id=principal.agent_run_id or _clean(labels.get(_AGENT_RUN_ID_LABEL)),
    )


@router.post(
    _ROUTES.create_session,
    response_model=OmnigentSessionResponse,
    response_model_exclude_none=True,
    responses=_PUBLIC_ERROR_RESPONSES,
)
async def create_omnigent_session(
    payload: BridgeSessionCreateRequest,
    config: OmnigentBridgeConfig = Depends(_require_mode_transition_safe),
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
            response = await embedded_facade.create_session(
                request=payload, binding=binding
            )
            dispatch = await embedded_facade.dispatch_runner(
                idempotency_key=binding.idempotency_key
            )
            response.setdefault("moonmind", {})["runner"] = dispatch
            return response
        if proxy is None:
            raise OmnigentBridgeError(
                "Omnigent proxy is unavailable for the configured bridge mode",
                failure_class="system_error",
                status_code=501,
            )
        return await proxy.create_session(request=payload, binding=binding)
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.get(
    _ROUTES.get_session,
    response_model=OmnigentSessionResponse,
    response_model_exclude_none=True,
    responses=_PUBLIC_ERROR_RESPONSES,
)
async def get_omnigent_session(
    session_id: str,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
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

    facade = (
        embedded_facade
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
        else proxy
    )
    if facade is None:
        raise HTTPException(
            status_code=501,
            detail={"code": "omnigent_bridge_mode_unsupported"},
        )
    owner = await facade.get_session_owner(session_id)
    if owner is None:
        # The bridge only exposes sessions it created/attached; an id it does
        # not own is not proxied upstream (avoids leaking arbitrary sessions).
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "omnigent_bridge_session_unknown",
                "message": (
                    "No Omnigent bridge session is bound to the requested session id."
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
        return await facade.get_session(session_id)
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.post(
    _ROUTES.attach_session,
    response_model=OmnigentSessionResponse,
    response_model_exclude_none=True,
    responses=_PUBLIC_ERROR_RESPONSES,
)
async def attach_omnigent_session(
    session_id: str,
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
    """Reconcile an already-created provider session after a create retry."""

    binding = await _resolve_bridge_binding(
        user=user, service=service, principal_context=principal_context, payload=payload
    )
    try:
        facade = (
            embedded_facade
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
            else proxy
        )
        if facade is None:
            raise OmnigentBridgeError("Unsupported bridge mode", status_code=501)
        return await facade.attach_session(session_id=session_id, binding=binding)
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.delete(
    _ROUTES.delete_session,
    response_model=OmnigentOperationResponse,
    responses=_PUBLIC_ERROR_RESPONSES,
)
async def delete_omnigent_session(
    session_id: str,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> dict[str, Any]:
    facade = (
        embedded_facade
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
        else proxy
    )
    await _authorize_session_control(
        session_id=session_id, user=user, service=service, proxy=facade
    )
    try:
        return await facade.delete_session(session_id)
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


async def _authorize_session_control(
    *,
    session_id: str,
    user: User,
    service: Any,
    proxy: Any,
) -> None:
    if proxy is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "code": "omnigent_bridge_mode_unsupported",
                "message": "Unsupported bridge mode",
            },
        )
    owner = await proxy.get_session_owner(session_id)
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "omnigent_bridge_session_unknown",
                "message": (
                    "No Omnigent bridge session is bound to the requested session id."
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
            detail={"code": "omnigent_bridge_session_unknown"},
        )
    principal = await resolve_execution_principal(
        user=user,
        service=service,
        workflow_id_header=owner.workflow_id,
        agent_run_id_header=owner.agent_run_id,
    )
    if not principal.workflow_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "omnigent_bridge_session_unknown"},
        )


_BRIDGE_TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled", "timed_out"})
_BRIDGE_EVENTS_SCHEMA = "moonmind.bridge-session-events-page.v1"
_BRIDGE_RESOLUTION_SCHEMA = "moonmind.bridge-session-resolution.v1"
_BRIDGE_TERMINAL_SCHEMA = "moonmind.bridge-session-terminal.v1"
_BRIDGE_PAGE_MAX = 500
_BRIDGE_STREAM_PAGE_SIZE = 100
_BRIDGE_STREAM_POLL_SECONDS = 1.0
_BRIDGE_STREAM_MAX_IDLE_POLLS = 300


class BridgeSessionResolution(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.title() for part in value.split("_")[1:]),
        populate_by_name=True,
    )
    schema_version: str = _BRIDGE_RESOLUTION_SCHEMA
    bridge_session_id: str
    workflow_id: str
    run_id: str | None = None
    step_execution_id: str | None = None
    agent_run_id: str
    idempotency_key: str
    status: str
    latest_sequence: int
    live_tailing_available: bool
    terminal_evidence_available: bool
    compatibility_profile: str
    provider_profile_id: str | None = None
    provider_lease_ref: str | None = None
    credential_generation: int | None = None
    host_binding_ref: str | None = None
    host_lease_ref: str | None = None
    host_mode: str | None = None
    execution_profile_ref: str | None = None
    launch_policy_ref: str | None = None
    effective_launch_snapshot_ref: str | None = None
    provider_session_ref: str | None = None
    omnigent_host_ref: str | None = None
    omnigent_runner_ref: str | None = None
    first_message_state: str | None = None
    capabilities: dict[str, bool] = Field(default_factory=dict)


class BridgeRetentionGap(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.title() for part in value.split("_")[1:]),
        populate_by_name=True,
    )
    requested_after: int
    earliest_available: int


class BridgeTerminalEnvelope(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.title() for part in value.split("_")[1:]),
        populate_by_name=True,
    )
    schema_version: str = _BRIDGE_TERMINAL_SCHEMA
    status: str
    failure_class: str | None = None
    failure_code: str | None = None
    summary: str | None = None
    diagnostics_ref: str | None = None
    capture_manifest_ref: str | None = None
    initial_snapshot_ref: str | None = None
    final_snapshot_ref: str | None = None
    raw_events_ref: str | None = None
    normalized_events_ref: str | None = None
    external_state_ref: str | None = None
    cleanup_state: str | None = None
    lease_release_state: str | None = None
    evidence_incomplete_reason: str | None = None


class BridgeEventPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    sequence: int
    timestamp: str
    stream: str
    text: str
    kind: str
    bridgeSessionId: str
    sessionId: str
    normalizedStatus: str | None = None
    artifactRef: str | None = None
    metadata: dict[str, Any]


class BridgeSseFrame(BaseModel):
    """Versioned data shapes emitted by the bridge SSE optimization."""

    event: str
    id: str | None = None
    data: BridgeEventPayload | BridgeRetentionGap | BridgeTerminalEnvelope


class BridgeEventPageResponse(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.title() for part in value.split("_")[1:]),
        populate_by_name=True,
    )
    schema_version: str = _BRIDGE_EVENTS_SCHEMA
    bridge_session_id: str
    items: list[BridgeEventPayload]
    after: int
    next_cursor: str | None
    has_more: bool
    terminal: bool
    latest_sequence: int
    retention_gap: BridgeRetentionGap | None = None
    terminal_envelope: BridgeTerminalEnvelope | None = None


def _terminal_envelope(row: Any) -> BridgeTerminalEnvelope | None:
    if row is None or str(row.status or "") not in _BRIDGE_TERMINAL_STATUSES:
        return None
    refs = dict(row.terminal_refs or {})
    metadata = dict(row.metadata_ or {})
    summary = str(refs.get("summary") or metadata.get("summary") or "")[:2000] or None
    has_evidence = any(
        (row.diagnostics_ref, row.capture_manifest_ref, row.final_snapshot_ref, refs)
    )
    return BridgeTerminalEnvelope(
        status=row.status,
        failure_class=refs.get("failureClass"),
        failure_code=refs.get("failureCode"),
        summary=summary,
        diagnostics_ref=row.diagnostics_ref,
        capture_manifest_ref=row.capture_manifest_ref,
        initial_snapshot_ref=row.initial_snapshot_ref,
        final_snapshot_ref=row.final_snapshot_ref,
        raw_events_ref=row.raw_events_ref,
        normalized_events_ref=row.normalized_events_ref,
        external_state_ref=row.external_state_ref,
        cleanup_state=refs.get("cleanupState"),
        lease_release_state=refs.get("leaseReleaseState"),
        evidence_incomplete_reason=(
            None if has_evidence else "No terminal artifacts were captured."
        ),
    )


def _projection_capabilities(row: Any) -> dict[str, bool]:
    metadata = getattr(row, "metadata_", None)
    if not isinstance(metadata, dict):
        return {}
    raw = metadata.get("interventionCapabilities")
    if not isinstance(raw, dict):
        raw = metadata.get("capabilities")
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): value
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, bool)
    }


def _bridge_event_kind(event_type: str | None) -> str:
    raw = str(event_type or "").strip()
    if raw in {"session.created", "session.started"}:
        return "session_started"
    if raw.startswith("session.input") or raw in {"message.sent", "input.message"}:
        return "user_message_submitted"
    if raw in {"response.delta", "response.output.delta"} or raw.endswith(".delta"):
        return "assistant_message_delta"
    if raw.startswith("response.output") or raw in {
        "response.message",
        "message.received",
    }:
        return "assistant_message"
    if raw in {
        "response.completed",
        "completed",
        "stream.done",
        "session.item.turn.completed",
    }:
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


@router.get("/bridge-sessions/resolve", response_model=BridgeSessionResolution)
async def resolve_omnigent_bridge_session_projection(
    workflow_id: str | None = Query(default=None, alias="workflowId"),
    run_id: str | None = Query(default=None, alias="runId"),
    step_execution_id: str | None = Query(default=None, alias="stepExecutionId"),
    agent_run_id: str | None = Query(default=None, alias="agentRunId"),
    idempotency_key: str | None = Query(default=None, alias="idempotencyKey"),
    _enabled: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store),
) -> BridgeSessionResolution:
    """Resolve the bridge session Workflow Chat should read before legacy logs."""

    try:
        row = await store.resolve_projection_session(
            workflow_id=workflow_id,
            run_id=run_id,
            step_execution_id=step_execution_id,
            agent_run_id=agent_run_id,
            idempotency_key=idempotency_key,
        )
    except BridgeProjectionAmbiguousError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "omnigent_bridge_session_unknown"},
        ) from exc
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
    page = await store.list_event_page(row.bridge_session_id, after=0, limit=1)
    launch = (
        getattr(row, "effective_launch_snapshot_json", None)
        if isinstance(getattr(row, "effective_launch_snapshot_json", None), dict)
        else {}
    )
    return BridgeSessionResolution(
        bridge_session_id=row.bridge_session_id,
        workflow_id=row.moonmind_workflow_id,
        run_id=row.moonmind_run_id,
        step_execution_id=row.step_execution_id,
        agent_run_id=row.moonmind_agent_run_id,
        idempotency_key=row.idempotency_key,
        status=row.status,
        latest_sequence=page.latest_sequence,
        live_tailing_available=row.status not in _BRIDGE_TERMINAL_STATUSES,
        terminal_evidence_available=_terminal_envelope(row) is not None,
        compatibility_profile=row.compatibility_profile,
        provider_profile_id=row.provider_profile_id,
        provider_lease_ref=getattr(row, "provider_lease_id", None),
        credential_generation=getattr(row, "credential_generation", None),
        host_binding_ref=row.host_binding_ref,
        host_lease_ref=getattr(row, "host_lease_ref", None),
        host_mode=str(launch.get("hostMode") or "") or None,
        execution_profile_ref=str(launch.get("executionProfileRef") or "") or None,
        launch_policy_ref=str(launch.get("launchPolicyRef") or "") or None,
        effective_launch_snapshot_ref=str(launch.get("snapshotRef") or "") or None,
        provider_session_ref=row.omnigent_session_id,
        omnigent_host_ref=getattr(row, "omnigent_host_id", None),
        omnigent_runner_ref=getattr(row, "omnigent_runner_id", None),
        first_message_state=getattr(row, "first_message_state", None),
        capabilities=_projection_capabilities(row),
    )


@router.get(
    "/bridge-sessions/{bridge_session_id}/events",
    response_model=BridgeEventPageResponse,
)
async def list_omnigent_bridge_session_events(
    bridge_session_id: str,
    after: int = Query(default=0, ge=0),
    cursor: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=_BRIDGE_PAGE_MAX),
    _enabled: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store),
) -> BridgeEventPageResponse:
    """List Workflow Chat projection events for one bridge session (§15)."""

    await _authorize_bridge_session_projection(
        bridge_session_id=bridge_session_id,
        user=user,
        service=service,
        store=store,
    )
    effective_after = cursor if cursor is not None else after
    page = await store.list_event_page(
        bridge_session_id, after=effective_after, limit=limit
    )
    session_row = await store.get_bridge_session(bridge_session_id)
    gap = None
    if (
        page.earliest_sequence is not None
        and effective_after + 1 < page.earliest_sequence
    ):
        gap = BridgeRetentionGap(
            requested_after=effective_after, earliest_available=page.earliest_sequence
        )
    delivered = page.rows[-1].sequence if page.rows else effective_after
    envelope = _terminal_envelope(session_row)
    terminal = (
        envelope is not None and delivered >= page.latest_sequence and not page.has_more
    )
    return BridgeEventPageResponse(
        bridge_session_id=bridge_session_id,
        items=[_bridge_event_payload(row) for row in page.rows],
        after=effective_after,
        next_cursor=str(delivered) if page.rows else None,
        has_more=page.has_more,
        terminal=terminal,
        latest_sequence=page.latest_sequence,
        retention_gap=gap,
        terminal_envelope=envelope if terminal else None,
    )


@router.get("/bridge-sessions/{bridge_session_id}/resources", response_model=dict)
async def get_omnigent_bridge_session_resources(
    bridge_session_id: str,
    _enabled: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store),
) -> dict[str, Any]:
    """Return owner-authorized artifact evidence for one bridge session."""

    await _authorize_bridge_session_projection(
        bridge_session_id=bridge_session_id,
        user=user,
        service=service,
        store=store,
    )
    row = await store.get_bridge_session(bridge_session_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    projection = dict((row.terminal_refs or {}).get("resourceProjection") or {})
    if projection:
        return projection
    return {
        "schemaVersion": "moonmind.omnigent.resource_projection.v1",
        "completeness": (
            "pending" if row.status not in _BRIDGE_TERMINAL_STATUSES else "degraded"
        ),
        "unavailableReasons": (
            {} if row.status not in _BRIDGE_TERMINAL_STATUSES else
            {"resourceProjection": "Terminal resource evidence was not published."}
        ),
        "groups": [],
    }


@router.get("/bridge-sessions/{bridge_session_id}/stream")
async def stream_omnigent_bridge_session_events(
    bridge_session_id: str,
    request: Request,
    since: int | None = Query(default=None),
    cursor: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
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
    try:
        header_cursor = int(last_event_id) if last_event_id else None
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "invalid_bridge_event_cursor"}
        ) from exc
    # EventSource reconnects retain the original query string and add the most
    # recently delivered sequence as Last-Event-ID. Resume after the greatest
    # acknowledged sequence instead of rejecting that standard request shape.
    initial_cursor = max(
        (value for value in (cursor, header_cursor, since) if value is not None),
        default=0,
    )

    async def _event_stream():
        last_sequence = initial_cursor
        idle_polls = 0
        while True:
            if await request.is_disconnected():
                return
            page = await store.list_event_page(
                bridge_session_id,
                after=last_sequence,
                limit=_BRIDGE_STREAM_PAGE_SIZE,
            )
            if (
                page.earliest_sequence is not None
                and last_sequence + 1 < page.earliest_sequence
            ):
                gap = BridgeRetentionGap(
                    requested_after=last_sequence,
                    earliest_available=page.earliest_sequence,
                )
                yield f"event: retention_gap\ndata: {gap.model_dump_json(by_alias=True)}\n\n"
                return
            for row in page.rows:
                last_sequence = row.sequence
                idle_polls = 0
                payload = json.dumps(_bridge_event_payload(row), separators=(",", ":"))
                yield f"id: {row.sequence}\nevent: bridge_event\ndata: {payload}\n\n"
            session_row = await store.get_bridge_session(bridge_session_id)
            envelope = _terminal_envelope(session_row)
            if (
                envelope is not None
                and last_sequence >= page.latest_sequence
                and not page.has_more
            ):
                confirmation = await store.list_event_page(
                    bridge_session_id, after=last_sequence, limit=1
                )
                if confirmation.rows or confirmation.latest_sequence > last_sequence:
                    continue
                yield f"event: terminal\ndata: {envelope.model_dump_json(by_alias=True)}\n\n"
                return
            if page.has_more:
                continue
            idle_polls += 1
            yield ": keepalive\n\n"
            if idle_polls >= _BRIDGE_STREAM_MAX_IDLE_POLLS:
                return
            await asyncio.sleep(_BRIDGE_STREAM_POLL_SECONDS)

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@router.post(
    _ROUTES.post_event,
    response_model=OmnigentOperationResponse,
    responses=_PUBLIC_ERROR_RESPONSES,
)
async def post_omnigent_session_event(
    session_id: str,
    payload: BridgeSessionEventRequest,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> dict[str, Any]:
    """Apply Omnigent controls, including bridge-local harvest/clear policy."""

    control_facade = (
        embedded_facade
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
        else proxy
    )
    if control_facade is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "code": "omnigent_bridge_mode_unsupported",
                "message": "Unsupported Omnigent bridge host protocol mode.",
            },
        )
    await _authorize_session_control(
        session_id=session_id,
        user=user,
        service=service,
        proxy=control_facade,
    )
    try:
        if payload.type in {"stop", "session.stop", "stop_session"}:
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
                assert embedded_facade is not None
                return await embedded_facade.stop_session(
                    session_id,
                    payload=payload.model_dump(by_alias=True, exclude_none=True),
                    actor=str(user.id),
                )
            return await control_facade.stop_session(session_id)
        if payload.type in {"cleanup_session", "terminal_cleanup"}:
            if config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED:
                raise OmnigentBridgeError(
                    "Typed owned-resource cleanup is unavailable in this mode.",
                    failure_class="user_error", status_code=501,
                    code="omnigent_bridge_capability_unavailable",
                )
            assert embedded_facade is not None
            return await embedded_facade.cleanup_session(
                session_id,
                payload=payload.model_dump(by_alias=True, exclude_none=True),
                actor=str(user.id),
            )
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
            assert embedded_facade is not None
            if payload.type == "harvest_session":
                return await embedded_facade.harvest_session(
                    session_id,
                    payload=payload.model_dump(by_alias=True, exclude_none=True),
                    actor=str(user.id),
                )
            if payload.type in {"clear_session", "reset_session"}:
                raise OmnigentBridgeError(
                    "Embedded clear/reset requires a new session and idempotency key.",
                    failure_class="user_error",
                    status_code=status.HTTP_409_CONFLICT,
                    code="omnigent_embedded_new_session_required",
                )
            if payload.type == "interrupt":
                raise OmnigentBridgeError(
                    "Embedded interrupt is not supported by the pinned host "
                    "protocol; use stop when terminating the runner is intended.",
                    failure_class="user_error",
                    status_code=status.HTTP_501_NOT_IMPLEMENTED,
                    code="omnigent_embedded_control_unsupported",
                )
            if payload.type not in {"message", "user.message"}:
                raise OmnigentBridgeError(
                    f"Embedded control {payload.type!r} is not supported.",
                    failure_class="user_error",
                    status_code=status.HTTP_501_NOT_IMPLEMENTED,
                    code="omnigent_embedded_control_unsupported",
                )
            return await embedded_facade.post_event(
                session_id=session_id, event=payload, actor=str(user.id)
            )
        assert proxy is not None
        return await proxy.post_event(session_id=session_id, event=payload)
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.post(
    _ROUTES.resolve_elicitation,
    response_model=OmnigentOperationResponse,
    responses=_PUBLIC_ERROR_RESPONSES,
)
async def resolve_omnigent_elicitation(
    session_id: str,
    elicitation_id: str,
    payload: dict[str, Any],
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> dict[str, Any]:
    """Resolve a pending Omnigent elicitation through the bridge surface."""

    facade = (
        embedded
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
        else proxy
    )
    if facade is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "code": "omnigent_bridge_mode_unsupported",
                "message": "Unsupported bridge mode",
            },
        )
    await _authorize_session_control(
        session_id=session_id,
        user=user,
        service=service,
        proxy=facade,
    )
    try:
        return await facade.resolve_elicitation(
            session_id=session_id,
            elicitation_id=elicitation_id,
            payload=payload,
            **({"actor": str(user.id)} if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED else {}),
        )
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.get(
    _ROUTES.agents,
    response_model=list[OmnigentAgentResponse],
    response_model_exclude_none=True,
    responses=_PUBLIC_ERROR_RESPONSES,
)
async def list_omnigent_agents(
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    _user: User = Depends(get_current_user()),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> list[dict[str, Any]]:
    """Proxy the Omnigent agent catalog (OB-§4.1)."""

    try:
        facade = (
            embedded_facade
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
            else proxy
        )
        if facade is None:
            raise OmnigentBridgeError("Unsupported bridge mode", status_code=501)
        return await facade.list_agents()
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.get(
    _ROUTES.hosts,
    response_model=list[OmnigentHostResponse],
    response_model_exclude_none=True,
    responses=_PUBLIC_ERROR_RESPONSES,
)
async def list_omnigent_hosts(
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    _user: User = Depends(get_current_user()),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> list[dict[str, Any]]:
    """Expose bounded host readiness metadata; callers cannot select a host."""

    try:
        facade = (
            embedded_facade
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
            else proxy
        )
        if facade is None:
            raise OmnigentBridgeError("Unsupported bridge mode", status_code=501)
        return await facade.list_hosts()
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.get(
    _ROUTES.stream_events,
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "text/event-stream": {"schema": OmnigentStreamEvent.model_json_schema()}
            }
        },
        **_PUBLIC_ERROR_RESPONSES,
    },
)
async def stream_upstream_omnigent_events(
    session_id: str,
    _request: Request,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> StreamingResponse:
    facade = (
        embedded_facade
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
        else proxy
    )
    await _authorize_session_control(
        session_id=session_id, user=user, service=service, proxy=facade
    )
    event_after = 0
    if last_event_id:
        try:
            event_after = max(int(last_event_id), 0)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "omnigent_bridge_invalid_event_cursor"},
            ) from exc

    async def _stream():
        try:
            stream = facade.stream_events(session_id, after=event_after)
            async for event in stream:
                # Reject unknown future canonical envelopes visibly instead of
                # silently coercing them into the pinned public contract.
                if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
                    OmnigentStreamEvent.model_validate(event)
                event_id = ""
                sequence = event.get("sequence")
                if isinstance(sequence, int) and sequence >= 0:
                    event_id = f"id: {sequence}\n"
                yield (
                    f"{event_id}data: "
                    f"{json.dumps(event, separators=(',', ':'))}\n\n"
                )
        except (OmnigentBridgeError, ValidationError) as exc:
            code = (
                exc.code
                if isinstance(exc, OmnigentBridgeError)
                else "omnigent_bridge_schema_version_unsupported"
            )
            payload = {
                "code": code,
                "message": "The upstream Omnigent event stream became unavailable.",
            }
            yield f"event: error\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


async def _owned_resource(
    *,
    operation: str,
    session_id: str,
    value: str | None,
    user: User,
    service: Any,
    proxy: Any,
):
    await _authorize_session_control(
        session_id=session_id, user=user, service=service, proxy=proxy
    )
    try:
        return await proxy.get_resource(operation, session_id, value)
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.get(_ROUTES.changed_files, response_model=dict)
async def list_omnigent_changed_files(
    session_id: str,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
):
    return await _owned_resource(
        operation="changed_files",
        session_id=session_id,
        value=None,
        user=user,
        service=service,
        proxy=(
            embedded
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
            else proxy
        ),
    )


@router.get(_ROUTES.workspace_files, response_model=dict)
async def list_omnigent_workspace_files(
    session_id: str,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
):
    return await _owned_resource(
        operation="workspace_files",
        session_id=session_id,
        value=None,
        user=user,
        service=service,
        proxy=(
            embedded
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
            else proxy
        ),
    )


@router.get(
    _ROUTES.workspace_file,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def get_omnigent_workspace_file(
    session_id: str,
    path: str,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> Response:
    content = await _owned_resource(
        operation="workspace_file",
        session_id=session_id,
        value=path,
        user=user,
        service=service,
        proxy=(
            embedded
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
            else proxy
        ),
    )
    return Response(content=content, media_type="application/octet-stream")


@router.get(
    _ROUTES.workspace_diffs,
    responses={200: {"content": {"text/x-diff": {}}}},
)
async def get_omnigent_workspace_diff(
    session_id: str,
    path: str,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> Response:
    content = await _owned_resource(
        operation="workspace_diff",
        session_id=session_id,
        value=path,
        user=user,
        service=service,
        proxy=(
            embedded
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
            else proxy
        ),
    )
    return Response(content=content, media_type="text/x-diff")


@router.get(_ROUTES.session_files, response_model=dict)
async def list_omnigent_session_files(
    session_id: str,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
):
    return await _owned_resource(
        operation="session_files",
        session_id=session_id,
        value=None,
        user=user,
        service=service,
        proxy=(
            embedded
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
            else proxy
        ),
    )


@router.get(
    _ROUTES.session_file,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def get_omnigent_session_file(
    session_id: str,
    file_id: str,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> Response:
    content = await _owned_resource(
        operation="session_file",
        session_id=session_id,
        value=file_id,
        user=user,
        service=service,
        proxy=(
            embedded
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
            else proxy
        ),
    )
    return Response(content=content, media_type="application/octet-stream")


def _require_host_auth_operator(user: User) -> None:
    if not bool(getattr(user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail={"code": "host_auth_operator_required"})


def _host_auth_http_error(exc: HostAuthProfileError) -> HTTPException:
    return HTTPException(
        status_code=409 if exc.code.startswith("host_auth_rotation") else 503,
        detail={"code": exc.code},
    )


@router.put("/host-auth/profile", response_model=dict)
async def put_embedded_host_auth_profile(
    payload: HostAuthProfilePutRequest,
    user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    """Atomically select safe managed metadata after resolving its SecretRef."""

    _require_host_auth_operator(user)
    candidate = HostAuthCredentialProfile(
        profile_id=payload.profile_id,
        current_secret_ref=payload.current_secret_ref,
        current_generation=payload.current_generation,
    )
    try:
        await resolve_host_auth_credentials(profile=candidate)
    except HostAuthProfileError as exc:
        raise _host_auth_http_error(exc) from exc
    store = _host_auth_store()
    if await store.get_active() is not None:
        raise HTTPException(
            status_code=409, detail={"code": "host_auth_already_configured"}
        )
    try:
        stored = await store.put(candidate, expected_generation=0)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "host_auth_already_configured"}
        ) from exc
    return stored.metadata()


@router.post("/host-auth/rotate", response_model=dict)
async def rotate_embedded_host_auth_profile(
    payload: HostAuthRotateRequest,
    user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    """Validate a new generation before atomically replacing durable metadata."""

    _require_host_auth_operator(user)
    store = _host_auth_store()
    current = await store.get_active()
    if current is None:
        raise HTTPException(status_code=409, detail={"code": "host_auth_unconfigured"})
    from moonmind.omnigent.host_auth_profile import rotate_host_auth_profile
    try:
        candidate = rotate_host_auth_profile(
            current,
            new_secret_ref=payload.new_secret_ref,
            overlap=timedelta(seconds=payload.overlap_seconds),
        )
        await resolve_host_auth_credentials(profile=candidate)
    except HostAuthProfileError as exc:
        raise _host_auth_http_error(exc) from exc
    try:
        stored = await store.put(
            candidate, expected_generation=current.current_generation
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "host_auth_generation_conflict"}
        ) from exc
    return stored.metadata()


@router.post("/host-auth/revoke", response_model=dict)
async def revoke_embedded_host_auth_profile(
    user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    _require_host_auth_operator(user)
    try:
        stored = await _host_auth_store().revoke()
    except LookupError as exc:
        raise HTTPException(status_code=409, detail={"code": "host_auth_unconfigured"}) from exc
    return stored.metadata()


@router.post("/v1/hosts/register", response_model=dict)
async def register_embedded_omnigent_host(
    payload: EmbeddedHostRegisterRequest,
    request: Request,
    config: OmnigentBridgeConfig = Depends(_require_embedded_mode),
    facade: OmnigentEmbeddedHostProtocolFacade = Depends(_get_embedded_host_facade),
) -> dict[str, Any]:
    """Register an unchanged host against MoonMind's embedded host facade."""

    auth = await _embedded_auth_context(request=request, config=config)
    try:
        return await facade.register_host(request=payload, auth=auth)
    except OmnigentBridgeError as exc:
        raise _http_error_from_bridge(exc) from exc


@router.websocket("/v1/hosts/{host_id}/tunnel")
async def embedded_omnigent_host_tunnel(websocket: WebSocket, host_id: str) -> None:
    """Serve the pinned stock-host frame protocol over one authenticated tunnel."""

    try:
        config = get_bridge_config()
        if not config.enabled or config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED:
            await websocket.close(code=4404)
            return
        try:
            await _require_embedded_mode(config)
        except HTTPException:
            await websocket.close(code=4403)
            return
        resolved = await resolve_host_auth_credentials(
            profile=await _active_host_auth_profile()
        )
        auth = verify_embedded_host_auth(
            headers=websocket.headers,
            config=config,
            configured_credentials=resolved.tokens_by_generation,
            credential_profile_id=resolved.profile.profile_id,
        )
        if host_id != auth.runner_id:
            await websocket.close(code=4403)
            return
    except HostAuthProfileError as exc:
        close_code = (
            4403
            if exc.code in {"host_auth_revoked", "host_auth_disabled"}
            else 1013
        )
        await websocket.close(code=close_code, reason=exc.code)
        return
    except OmnigentBridgeError as exc:
        await websocket.close(code=4401, reason=exc.code)
        return
    await websocket.accept()
    channel = embedded_host_channels.connect(
        host_id=host_id, send_text=websocket.send_text
    )
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=OmnigentBridgeSessionStore(async_session_maker), config=config
    )
    try:
        while True:
            frame_text = await websocket.receive_text()
            # Re-resolve safe profile state for every frame so immediate
            # revocation and overlap expiry drain already-connected tunnels.
            active = await resolve_host_auth_credentials(
                profile=await _active_host_auth_profile()
            )
            if (
                active.profile.profile_id != auth.credential_profile_id
                or auth.credential_generation not in active.tokens_by_generation
            ):
                await websocket.close(code=4403)
                break
            frame = channel.accept_host_frame(frame_text)
            if isinstance(frame, channel.adapter.frames.HostRunnerExitedFrame):
                await facade.record_runner_exit(
                    runner_id=frame.runner_id, error=frame.error
                )
                embedded_host_channels.revoke_runner_binding(frame.runner_id)
    except HostAuthProfileError as exc:
        close_code = 4403 if exc.code in {"host_auth_revoked", "host_auth_disabled"} else 1013
        await websocket.close(code=close_code, reason=exc.code)
    except WebSocketDisconnect:
        pass
    except (EmbeddedHostChannelError, UpstreamHostProtocolError):
        await websocket.close(code=4400)
    finally:
        embedded_host_channels.disconnect(channel)
        try:
            await facade.disconnect_host(host_id=host_id, auth=auth)
        except OmnigentBridgeError:
            # The durable lease may already have terminalized while the socket
            # was closing; terminal state remains authoritative.
            pass


@router.websocket("/v1/runners/{runner_id}/tunnel")
async def embedded_omnigent_runner_tunnel(
    websocket: WebSocket, runner_id: str
) -> None:
    """Accept the stock runner tunnel created by an embedded host launch."""

    config = get_bridge_config()
    if not config.enabled or config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED:
        await websocket.close(code=4404)
        return
    try:
        await _require_embedded_mode(config)
    except HTTPException:
        await websocket.close(code=4403)
        return
    try:
        store = OmnigentBridgeSessionStore(async_session_maker)
        binding = await store.get_active_session_by_runner_identity(runner_id)
        if (
            binding is None
            or not binding.omnigent_host_id
            or not binding.omnigent_session_id
            or binding.credential_generation is None
        ):
            raise EmbeddedHostChannelError("runner has no active durable binding")
        from moonmind.omnigent.embedded_host_channel import derive_runner_binding_token

        binding_token = derive_runner_binding_token(
            resolved_host_runner_token(),
            host_id=binding.omnigent_host_id,
            session_id=binding.omnigent_session_id,
            generation=int(
                ((binding.metadata_ or {}).get("embedded_runner_launch") or {}).get(
                    "generation"
                )
                or binding.credential_generation
            ),
        )
        embedded_host_channels.authenticate_runner(
            runner_id=runner_id, headers=websocket.headers,
            binding_token=binding_token,
        )
    except (EmbeddedHostChannelError, UpstreamHostProtocolError):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    channel = None
    try:
        channel = embedded_host_channels.connect_runner(
            runner_id=runner_id,
            send_text=websocket.send_text,
            hello_text=await websocket.receive_text(),
        )
        facade = OmnigentEmbeddedHostProtocolFacade(
            run_store=OmnigentBridgeSessionStore(async_session_maker), config=config
        )
        await facade.record_runner_tunnel_ready(runner_id=runner_id)
        while True:
            channel.accept_frame(await websocket.receive_text())
    except WebSocketDisconnect:
        pass
    except EmbeddedHostChannelError:
        await websocket.close(code=4400)
    finally:
        if channel is not None:
            embedded_host_channels.disconnect_runner(channel)
            facade = OmnigentEmbeddedHostProtocolFacade(
                run_store=OmnigentBridgeSessionStore(async_session_maker), config=config
            )
            try:
                await facade.record_runner_tunnel_disconnected(runner_id=runner_id)
            except (EmbeddedHostChannelError, OmnigentIdempotencyError):
                # Terminal exit processing may have won the race. Its durable
                # terminal evidence remains authoritative over disconnect.
                pass


@router.post("/v1/hosts/{host_id}/heartbeat", response_model=dict)
async def heartbeat_embedded_omnigent_host(
    host_id: str,
    payload: EmbeddedHostHeartbeatRequest,
    request: Request,
    config: OmnigentBridgeConfig = Depends(_require_embedded_mode),
    facade: OmnigentEmbeddedHostProtocolFacade = Depends(_get_embedded_host_facade),
) -> dict[str, Any]:
    """Accept a host heartbeat through the embedded host facade."""

    auth = await _embedded_auth_context(request=request, config=config)
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

    auth = await _embedded_auth_context(request=request, config=config)
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
