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
from collections.abc import Mapping
from hashlib import sha256
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import aiohttp
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import Response, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.execution_principal import (
    execution_principal_dependency,
    resolve_execution_principal,
)
from api_service.api.routers.executions import _get_service as _get_execution_service
from api_service.api.routers.retrieval_gateway import get_capability_registry
from api_service.auth_providers import get_current_user
from api_service.db.base import async_session_maker, get_async_session
from api_service.db.models import User
from api_service.retrieval_capabilities import RetrievalCapabilityRegistry
from api_service.services.omnigent_agent_profile_service import (
    record_upstream_sync_failure,
    synchronize_upstream_inventory,
)
from api_service.services.omnigent_policies import (
    OmnigentPolicyService,
    PolicyConflict,
    PolicyNotFound,
)
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    HOST_PROTOCOL_MODE_PROXY,
    OmnigentBridgeConfig,
    resolve_bridge_config,
)
from moonmind.omnigent.bridge_artifacts import (
    LocalOmnigentArtifactGateway,
    OmnigentArtifactError,
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
    BRIDGE_EVENT_JOURNAL_KEY,
    SESSION_CREATED_EVENT_TYPE,
    BridgeProjectionAmbiguousError,
    OmnigentBridgeSessionStore,
    OmnigentIdempotencyError,
)
from moonmind.omnigent.control_plane.turn_sources import TurnSource
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
from moonmind.omnigent.native_ui import (
    NATIVE_UI_MOUNT_PREFIX,
    NATIVE_UI_ROUTE_FEATURE_VERSION,
    evaluate_native_ui_compatibility,
    scoped_api_base,
)
from moonmind.omnigent.native_ui_compat import (
    CODE_COMPAT_REVIEW_REQUIRED,
    CODE_TRANSPORT_UNSUPPORTED,
    CODE_WS_SUBPROTOCOL_REJECTED,
    NativeUiCompatibilityError,
    DISPOSITION_SERVED,
    classify_native_ui_http,
    classify_native_ui_websocket,
    negotiate_ws_subprotocol,
    upstream_http_path,
    upstream_websocket_path,
)
from moonmind.omnigent.settings import (
    OMNIGENT_DISABLED_MESSAGE,
    build_omnigent_gate,
    resolved_api_token,
    resolved_default_agent_name,
    resolved_host_runner_token,
    resolved_native_ui_serving_enabled,
    resolved_native_ui_version,
    resolved_proxy_forward_headers,
    resolved_server_url,
)
from moonmind.omnigent.native_outbound_scan import (
    NativeScanBlockedError,
    NativeScanEnforcementError,
    NativeScanEvidence,
    NativeScanSurface,
    canonical_payload_digest,
    scan_native_outbound,
)
from moonmind.omnigent.workflow_chat_facade import (
    CAP_RESOLVE_ELICITATION,
    CODE_BINDING_UNKNOWN,
    CODE_CONTENT_BLOCKED,
    CODE_ENFORCEMENT_UNAVAILABLE,
    CODE_IDEMPOTENCY_CONFLICT,
    CODE_MALFORMED_PAYLOAD,
    CODE_OPERATION_DENIED,
    CODE_PAYLOAD_TOO_LARGE,
    CODE_ROUTE_NOT_ALLOWLISTED,
    CODE_SESSION_NOT_READY,
    CODE_SESSION_READ_ONLY,
    CODE_SESSION_SUBSTITUTION,
    CODE_UNSUPPORTED_MEDIA_TYPE,
    WorkflowChatFacadeError,
    assert_no_identity_substitution,
    is_read_only,
    match_facade_operation,
    required_capability_for_event,
)
from moonmind.omnigent.effective_capabilities import (
    EffectiveCapabilitySet,
    caller_capabilities_for_bridge,
    resolve_bridge_row_capabilities,
)
from moonmind.utils.build_info import resolve_moonmind_build_id
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentAgentSelection,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
)

logger = logging.getLogger(__name__)
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

    auth: dict[str, Any] | None = None
    if config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED:
        # Proxy readiness does not depend on embedded image pins, so a missing
        # persisted default policy degrades the diagnostics projection rather
        # than failing readiness for the default proxy-first deployment.
        policy_authority = await _resolve_bridge_policy_authority_optional()
        readiness = config.readiness()
    else:
        # Embedded mode resolves evidence and images from the policy authority
        # and must fail closed when that authority is unavailable.
        policy_authority = await _resolve_bridge_policy_authority()
        readiness = config.readiness(
            evidence_validation=await _resolve_embedded_evidence(
                config, policy_authority=policy_authority
            )
        )
        try:
            profile = await _active_host_auth_profile()
            auth = await host_auth_readiness(profile=profile)
        except HostAuthProfileError as exc:
            auth = {"ready": False, "code": exc.code}
        readiness["hostAuthentication"] = auth
        if not auth["ready"]:
            readiness["conformanceState"] = "gated"
            readiness.setdefault("gateReason", auth.get("code"))
    readiness["compatibilityDiagnostics"] = _compatibility_diagnostics(
        config=config,
        readiness=readiness,
        auth=auth,
        policy_authority=policy_authority,
    )
    return readiness


_PROXY_ROLLBACK_RECOMMENDATION = (
    "Select upstream_omnigent_server_proxy for new sessions; "
    "existing sessions retain their recorded bridge mode."
)


def _compatibility_diagnostics(
    *,
    config: OmnigentBridgeConfig,
    readiness: dict[str, Any],
    auth: dict[str, Any] | None = None,
    policy_authority: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one bounded support projection shared by readiness surfaces."""

    validation = readiness.get("evidenceValidation") or {}
    selected_embedded = config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
    evidence_fresh = not selected_embedded or (
        bool(validation)
        and all(item.get("status") == "passed" for item in validation.values())
    )
    failure_reason = (
        None
        if readiness.get("conformanceState") == "ready"
        else readiness.get("gateReason") or "bridge_not_ready"
    )
    support_matrix = []
    for host_mode in ("static_compose", "on_demand_docker"):
        row = config.readiness(
            evidence_validation=validation or None,
            host_mode=host_mode,
        )
        supported = row["conformanceState"] == "ready"
        support_matrix.append(
            {
                "hostMode": host_mode,
                "supported": supported,
                "failureReason": None if supported else row.get("gateReason"),
            }
        )
    evidence_refs = sorted(
        {
            str(item["evidenceRef"])
            for item in validation.values()
            if item.get("status") == "passed" and item.get("evidenceRef")
        }
    )
    image_sets = [
        item.get("images")
        for item in validation.values()
        if item.get("status") == "passed" and isinstance(item.get("images"), dict)
    ]
    authority_host = (policy_authority or {}).get("boundaries", {}).get("host", {})
    images = (
        image_sets[0]
        if image_sets
        else {
            "server": authority_host.get("serverImageRef"),
            "host": authority_host.get("hostImageRef"),
        }
    )
    projection = {
        "bridgeMode": config.host_protocol_mode,
        "compatibilityProfile": readiness.get("protocolProfile"),
        "authProfile": (
            config.host_connection.embedded.auth_mode if selected_embedded else None
        ),
        "upstreamComponentVersion": readiness.get("upstreamComponentVersion"),
        "serverImage": images.get("server"),
        "hostImage": images.get("host"),
        "hostArchitecture": os.getenv("OMNIGENT_HOST_ARCHITECTURE") or None,
        "auth": auth,
        "evidence": {
            "fresh": evidence_fresh,
            "refs": evidence_refs,
            "validation": validation,
        },
        "supportMatrix": support_matrix,
        "failureReason": failure_reason,
        "policyAuthority": (
            {
                "policyRef": policy_authority["policyRef"],
                "policyDigest": policy_authority["policyDigest"],
                "policySnapshotRef": policy_authority["snapshotRef"],
                "validation": policy_authority["validation"],
            }
            if policy_authority is not None
            else None
        ),
        "rollbackRecommendation": (
            _PROXY_ROLLBACK_RECOMMENDATION
            if selected_embedded and failure_reason
            else None
        ),
    }
    projection["releaseMetadata"] = {
        key: projection[key]
        for key in (
            "bridgeMode",
            "compatibilityProfile",
            "authProfile",
            "serverImage",
            "hostImage",
            "hostArchitecture",
        )
    }
    projection["nativeUi"] = _native_ui_diagnostics(config=config)
    return projection


def _native_ui_diagnostics(*, config: OmnigentBridgeConfig) -> dict[str, Any]:
    """Report native Omnigent UI serving readiness (MoonLadderStudios/MoonMind#3638).

    Operators need a bounded, non-secret projection of whether the native
    Workflow Chat UI is served through MoonMind: UI-asset serving, the
    compatibility version gate, the scoped HTTP/SSE/WebSocket route surface,
    and credential separation. It reveals no upstream URL, credential, or
    provider session identity.
    """

    serving_enabled = resolved_native_ui_serving_enabled()
    compatibility = evaluate_native_ui_compatibility(
        resolved_native_ui_version(),
        enabled=bool(config.enabled) and serving_enabled,
    )
    scoped_api = scoped_api_base("{chatBindingId}")
    return {
        "servingEnabled": serving_enabled,
        "ready": compatibility.ready,
        "reason": compatibility.reason,
        "assetsServedThroughMoonMind": serving_enabled,
        "compatibilityVersion": NATIVE_UI_ROUTE_FEATURE_VERSION,
        "reportedVersion": compatibility.reported_version,
        "supportedVersions": list(compatibility.supported_versions),
        "scopedRoutes": {
            "uiMountPath": f"{NATIVE_UI_MOUNT_PREFIX}/{{chatBindingId}}",
            "apiBase": scoped_api,
            "http": scoped_api,
            "sse": f"{scoped_api}/v1/sessions/{{chatBindingId}}/stream",
            "websocket": f"{scoped_api}/v1/sessions/{{chatBindingId}}/stream",
        },
        # MoonMind strips its own credentials before forwarding upstream and
        # virtualizes the provider session id, so neither the upstream credential
        # nor the raw provider session id reaches the browser.
        "credentialSeparation": True,
        "directUpstreamBrowserExposure": False,
    }


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
    *,
    policy_authority: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve claims with the artifact service's trusted service principal."""

    results: dict[str, dict[str, Any]] = {}
    authority = policy_authority or await _resolve_bridge_policy_authority()
    host = authority["boundaries"]["host"]
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
                    expected_host_architecture=(
                        os.getenv("OMNIGENT_HOST_ARCHITECTURE") or ""
                    ),
                    expected_images={
                        "server": str(host["serverImageRef"]),
                        "host": str(host["hostImageRef"]),
                    },
                )
                results[key] = {
                    "status": "passed",
                    "evidenceRef": getattr(embedded, attribute),
                    "schemaVersion": claim.schema_version,
                    "generatedAt": claim.generated_at.isoformat(),
                    "expiresAt": claim.expires_at.isoformat(),
                    "supportedHostModes": list(claim.supported_host_modes),
                    "hostArchitecture": claim.host_architecture,
                    "images": dict(claim.images),
                }
            except Exception:  # noqa: BLE001 - every resolver failure gates mode
                # Read/auth/schema failures intentionally share one bounded,
                # non-enumerating public result.
                results[key] = {
                    "status": "failed",
                    "reason": "evidence_unavailable_or_invalid",
                }
    return results


async def _resolve_bridge_policy_authority() -> dict[str, Any]:
    """Fail closed unless the bridge's persisted default authority is usable."""

    try:
        async with async_session_maker() as session:
            return await OmnigentPolicyService(
                session
            ).resolve_default_runtime_snapshot("omnigent-codex")
    except (PolicyConflict, PolicyNotFound) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "omnigent_policy_authority_unavailable",
                "message": "The persisted Omnigent bridge policy is unavailable.",
            },
        ) from exc


async def _resolve_bridge_policy_authority_optional() -> dict[str, Any] | None:
    """Resolve the persisted default authority, tolerating its absence.

    Proxy mode does not embed image pins, so the default proxy-first Compose
    deployment can be fully ready before a usable default policy is seeded.
    Readiness surfaces must degrade the policy-authority projection instead of
    returning 503 in that mode; embedded mode still resolves the authority with
    the fail-closed variant because it drives evidence and image resolution.
    Delegates to the fail-closed resolver so both surfaces share one resolution
    path, only softening its 503 into an absent (None) projection.
    """

    try:
        return await _resolve_bridge_policy_authority()
    except HTTPException as exc:
        if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            return None
        raise


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
    detail: dict[str, Any] = {
        "code": exc.code,
        "failureClass": exc.failure_class,
        "message": str(exc),
    }
    # Facade errors may carry bounded, already-redacted public metadata (for
    # example a blocked scan's finding categories and safe locations). It never
    # contains a detected value or a raw message body.
    public_details = getattr(exc, "public_details", None)
    if isinstance(public_details, dict) and public_details:
        detail.update(public_details)
    return HTTPException(
        status_code=status_code,
        detail=detail,
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


async def _get_launch_default_agent_selection(
    session: AsyncSession = Depends(get_async_session),
) -> OmnigentAgentSelection | None:
    """Resolve the durable default Omnigent agent for a proxy-mode launch.

    MoonLadderStudios/MoonMind#3517 §8: an active default agent profile is the
    durable authority; ``OMNIGENT_DEFAULT_AGENT_NAME`` is only a recorded
    bootstrap/local-development fallback. A profile marked default but not
    launch-ready is a conflict and fails closed at this launch boundary.
    """

    from api_service.services.omnigent_agent_bootstrap_service import (
        BootstrapDefaultConflictError,
        resolve_default_agent_selection,
    )

    try:
        resolution = await resolve_default_agent_selection(session)
    except BootstrapDefaultConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if resolution.agent_id:
        return OmnigentAgentSelection(agent_id=resolution.agent_id)
    if resolution.agent_name:
        return OmnigentAgentSelection(agent_name=resolution.agent_name)
    return None


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
    launch_default_agent: OmnigentAgentSelection | None = Depends(
        _get_launch_default_agent_selection
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
        return await proxy.create_session(
            request=payload,
            binding=binding,
            default_agent_override=launch_default_agent,
        )
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
    registry: RetrievalCapabilityRegistry = Depends(get_capability_registry),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store),
) -> dict[str, Any]:
    facade = (
        embedded_facade
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
        else proxy
    )
    await _authorize_session_control(
        session_id=session_id, user=user, service=service, proxy=facade
    )
    await _revoke_session_retrieval_authority(
        session_id=session_id,
        registry=registry,
        store=store,
        reason="session_deleted",
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


def _retrieval_lifecycle_scope(row: Any) -> dict[str, str]:
    """Compile the exact retrieval scope owned by one bridge session row."""

    return {
        "run_id": str(getattr(row, "moonmind_run_id", "") or ""),
        "host_id": str(getattr(row, "omnigent_host_id", "") or ""),
        "session_id": str(getattr(row, "omnigent_session_id", "") or ""),
        "step_id": str(getattr(row, "step_execution_id", "") or ""),
    }


async def _revoke_session_retrieval_authority(
    *,
    session_id: str,
    registry: RetrievalCapabilityRegistry,
    store: OmnigentBridgeSessionStore,
    reason: str,
) -> list[str]:
    """Close scoped retrieval authority before a destructive host boundary.

    When the session cannot be scoped precisely — no bridge row, or a partially
    established one — the outcome depends on whether live authority still names
    it.  Live-but-unscopable authority fails closed so the host is not mutated
    behind an open capability; a session that provably owns no capability is a
    no-op so cleanup is never blocked.  Either way the scope is never widened to
    a run-wide wildcard, and a store failure propagates rather than silently
    skipping revocation.
    """

    row = await store.get_session_by_provider_session_id(session_id)
    if row is None:
        if registry.has_live_session_authority(session_id=session_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "omnigent_retrieval_authority_unresolved",
                    "message": (
                        "The bridge session retrieval authority could not be resolved."
                    ),
                },
            )
        logger.info(
            "No bridge row resolves Omnigent session %s; no retrieval "
            "authority to close for %s.",
            session_id,
            reason,
        )
        return []
    scope = _retrieval_lifecycle_scope(row)
    if not all(scope.values()):
        if registry.has_live_session_authority(
            session_id=scope["session_id"] or session_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "omnigent_retrieval_scope_incomplete",
                    "message": (
                        "Live retrieval authority cannot be bounded to this "
                        "session because its bridge scope is incomplete."
                    ),
                },
            )
        logger.info(
            "Bridge session %s has an incomplete retrieval scope and owns no "
            "live capability; nothing to close for %s.",
            session_id,
            reason,
        )
        return []
    revoked = registry.revoke_scope(**scope)
    if not revoked:
        return []
    await store.append_events(
        row.bridge_session_id,
        [
            {
                "eventType": "retrieval.capabilities.revoked",
                "direction": "moonmind_to_host",
                "deduplicationKey": (
                    f"retrieval-capabilities-revoked:{row.bridge_session_id}:{reason}"
                ),
                "metadata": {
                    "revokedCount": len(revoked),
                    "reason": reason,
                },
            }
        ],
    )
    return revoked


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
    policy_id: str | None = None
    policy_version: int | None = None
    policy_digest: str | None = None
    policy_validation: dict[str, Any] | None = None
    policy_snapshot_ref: str | None = None
    effective_launch_snapshot_ref: str | None = None
    provider_session_ref: str | None = None
    omnigent_host_ref: str | None = None
    omnigent_runner_ref: str | None = None
    first_message_state: str | None = None
    initial_retrieval: dict[str, Any] | None = None
    capabilities: dict[str, bool] = Field(default_factory=dict)
    compatibility_diagnostics: dict[str, Any] = Field(default_factory=dict)


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


def _effective_capabilities(row: Any, user: User) -> EffectiveCapabilitySet:
    """Return the canonical decision filtered by actual caller authority."""

    return resolve_bridge_row_capabilities(
        row,
        caller_capabilities=caller_capabilities_for_bridge(row, user),
    )


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
    capabilities = _projection_capabilities(row)
    compatibility_profile = row.compatibility_profile
    historical_embedded = (
        str((getattr(row, "metadata_", None) or {}).get("hostProtocolMode") or "")
        == HOST_PROTOCOL_MODE_EMBEDDED
    )
    compatibility_evidence_ref = (
        str(launch.get("compatibilityEvidenceRef") or "") or None
    )
    authority = (
        launch.get("policyAuthority")
        if isinstance(launch.get("policyAuthority"), dict)
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
        compatibility_profile=compatibility_profile,
        provider_profile_id=row.provider_profile_id,
        provider_lease_ref=getattr(row, "provider_lease_id", None),
        credential_generation=getattr(row, "credential_generation", None),
        host_binding_ref=row.host_binding_ref,
        host_lease_ref=getattr(row, "host_lease_ref", None),
        host_mode=str(launch.get("hostMode") or "") or None,
        execution_profile_ref=str(launch.get("executionProfileRef") or "") or None,
        launch_policy_ref=str(launch.get("launchPolicyRef") or "") or None,
        policy_id=str(authority.get("policyId") or "") or None,
        policy_version=(
            int(authority["policyVersion"])
            if authority.get("policyVersion") is not None
            else None
        ),
        policy_digest=str(authority.get("policyDigest") or "") or None,
        policy_validation=(
            dict(authority["validation"])
            if isinstance(authority.get("validation"), dict)
            else None
        ),
        policy_snapshot_ref=str(authority.get("snapshotRef") or "") or None,
        effective_launch_snapshot_ref=str(launch.get("snapshotRef") or "") or None,
        provider_session_ref=row.omnigent_session_id,
        omnigent_host_ref=getattr(row, "omnigent_host_id", None),
        omnigent_runner_ref=getattr(row, "omnigent_runner_id", None),
        first_message_state=getattr(row, "first_message_state", None),
        capabilities=capabilities,
        compatibility_diagnostics={
            "bridgeMode": (
                HOST_PROTOCOL_MODE_EMBEDDED
                if historical_embedded
                else HOST_PROTOCOL_MODE_PROXY
            ),
            "compatibilityProfile": compatibility_profile,
            "hostMode": str(launch.get("hostMode") or "") or None,
            "authProfile": (
                str(launch.get("authProfile") or "") or "upstream_runner_tunnel"
                if historical_embedded
                else None
            ),
            "serverImage": str(launch.get("serverImageRef") or "") or None,
            "hostImage": str(launch.get("hostImageRef") or "") or None,
            "hostArchitecture": str(launch.get("hostArchitecture") or "") or None,
            "authGeneration": getattr(row, "credential_generation", None),
            "evidenceRef": compatibility_evidence_ref,
            "evidenceFresh": launch.get("compatibilityEvidenceFresh"),
            "lifecycleState": row.status,
            "supportedCapabilities": sorted(
                key for key, supported in capabilities.items() if supported
            ),
            "unsupportedCapabilities": sorted(
                key for key, supported in capabilities.items() if not supported
            ),
            "failureReason": (
                str(launch.get("compatibilityFailureReason") or "")
                or (
                    "historical_compatibility_evidence_not_recorded"
                    if historical_embedded and not compatibility_evidence_ref
                    else None
                )
            ),
            "rollbackRecommendation": (
                str(launch.get("rollbackRecommendation") or "")
                or None
                or (
                    _PROXY_ROLLBACK_RECOMMENDATION
                    if historical_embedded and not compatibility_evidence_ref
                    else None
                )
            ),
        },
        initial_retrieval=dict(
            ((getattr(row, "metadata_", None) or {}).get("initialRetrieval") or {})
        )
        or None,
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
            {}
            if row.status not in _BRIDGE_TERMINAL_STATUSES
            else {"resourceProjection": "Terminal resource evidence was not published."}
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
    request: Request,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
    registry: RetrievalCapabilityRegistry = Depends(get_capability_registry),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store),
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
    row = await store.get_session_by_provider_session_id(session_id)
    chat_binding_id = str(getattr(row, "chat_binding_id", "") or "").strip()
    if row is None or not chat_binding_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": CODE_SESSION_NOT_READY,
                "message": (
                    "This compatibility control has no canonical Workflow Chat "
                    "binding authority."
                ),
            },
        )

    required = required_capability_for_event(payload.type)
    caller_grants = caller_capabilities_for_bridge(row, user)
    capability_set = resolve_bridge_row_capabilities(
        row, caller_capabilities=caller_grants
    )
    if not capability_set.capabilities.get(required, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": CODE_OPERATION_DENIED,
                "message": "The requested control is not permitted for this binding.",
            },
        )

    effective_key = _facade_idempotency_key(request) or f"mm-{uuid4().hex}"
    body = payload.model_dump(by_alias=True, exclude_none=True)
    try:
        scan_evidence = _enforce_native_outbound_scan(
            surface=NativeScanSurface.MESSAGE,
            body=body,
            idempotency_key=effective_key,
            chat_binding_id=chat_binding_id,
            user=user,
            operation="post_event",
        )
    except WorkflowChatFacadeError as exc:
        raise _http_error_from_bridge(exc) from exc
    preconditions = _mutation_preconditions(body)
    allow_terminal = required in {"harvestEvidence", "cleanupSession"}
    try:
        fresh_row, fresh_provider, fresh_capabilities = (
            await _revalidate_binding_before_mutation(
                store=store,
                chat_binding_id=chat_binding_id,
                expected_provider_session_id=session_id,
                expected_authority_digest=capability_set.authority_digest,
                user=user,
                allow_terminal_mutation=allow_terminal,
                **preconditions,
            )
        )
    except WorkflowChatFacadeError as exc:
        raise _http_error_from_bridge(exc) from exc
    if not fresh_capabilities.get(required, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": CODE_OPERATION_DENIED, "message": "Capability revoked."},
        )
    request_time = datetime.now(tz=UTC).isoformat()
    try:
        claimed = await _claim_facade_message(
            store=store,
            row=fresh_row,
            event_type=payload.type,
            actor=str(user.id),
            idempotency_key=effective_key,
            payload_digest=scan_evidence.payload_digest,
            scan_evidence=scan_evidence,
            request_time=request_time,
            request_preconditions=preconditions,
        )
    except WorkflowChatFacadeError as exc:
        raise _http_error_from_bridge(exc) from exc
    if not claimed:
        prior = await _reconcile_facade_mutation(
            store=store,
            row=fresh_row,
            facade=control_facade,
            control_type=payload.type,
            idempotency_key=effective_key,
            provider_session_id=fresh_provider,
            chat_binding_id=chat_binding_id,
            actor=str(user.id),
            scan_evidence=scan_evidence,
            request_preconditions=preconditions,
        )
        if prior is not None:
            return prior
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": CODE_SESSION_NOT_READY,
                "message": "Prior delivery is not yet reconcilable.",
            },
        )
    dispatch_time = datetime.now(tz=UTC).isoformat()
    try:
        result = await _apply_owned_session_control(
            control_facade=control_facade,
            session_id=fresh_provider,
            payload=payload,
            config=config,
            actor=str(user.id),
            proxy=proxy,
            embedded_facade=embedded_facade,
            registry=registry,
            store=store,
        )
        await _record_facade_mutation_audit(
            store=store,
            row=fresh_row,
            control_type=payload.type,
            outcome="posted",
            actor=str(user.id),
            idempotency_key=effective_key,
            scan_evidence=scan_evidence,
            upstream_result=result,
            request_time=request_time,
            dispatch_time=dispatch_time,
            request_preconditions=preconditions,
        )
        return result
    except OmnigentBridgeError as exc:
        await _record_facade_mutation_audit(
            store=store,
            row=fresh_row,
            control_type=payload.type,
            outcome="delivery_unknown",
            actor=str(user.id),
            idempotency_key=effective_key,
            scan_evidence=scan_evidence,
            request_time=request_time,
            dispatch_time=dispatch_time,
            request_preconditions=preconditions,
        )
        raise _http_error_from_bridge(exc) from exc


async def _apply_owned_session_control(
    *,
    control_facade: Any,
    session_id: str,
    payload: BridgeSessionEventRequest,
    config: OmnigentBridgeConfig,
    actor: str,
    proxy: OmnigentBridgeSessionProxy | None,
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None,
    registry: RetrievalCapabilityRegistry,
    store: OmnigentBridgeSessionStore,
) -> dict[str, Any]:
    """Apply an Omnigent control event to an already-authorized provider session.

    Shared canonical control path for both the provider-session control route
    (``post_omnigent_session_event``) and the binding-scoped Workflow Chat
    facade. Callers authorize the session first (by provider-session owner or by
    durable ``chatBindingId`` binding) and pass the resolved ``session_id``;
    this helper owns the harvest/clear/stop/cleanup policy, embedded/proxy
    branching, and retrieval-authority revocation so neither surface reimplements
    control semantics. Raises :class:`OmnigentBridgeError`; callers map it to a
    redacted HTTP error.
    """

    if payload.type in {"clear_session", "reset_session"}:
        # Embedded mode rejects clear/reset without replacing or stopping the
        # session, so revoking first would permanently disable retrieval for
        # a session that keeps running.  Reject before mutating authority.
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
            raise OmnigentBridgeError(
                "Embedded clear/reset requires a new session and idempotency key.",
                failure_class="user_error",
                status_code=status.HTTP_409_CONFLICT,
                code="omnigent_embedded_new_session_required",
            )
        await _revoke_session_retrieval_authority(
            session_id=session_id,
            registry=registry,
            store=store,
            reason="session_replaced",
        )
    if payload.type in {"stop", "session.stop", "stop_session"}:
        await _revoke_session_retrieval_authority(
            session_id=session_id,
            registry=registry,
            store=store,
            reason="session_stopped",
        )
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
            assert embedded_facade is not None
            return await embedded_facade.stop_session(
                session_id,
                payload=payload.model_dump(by_alias=True, exclude_none=True),
                actor=actor,
            )
        return await control_facade.stop_session(session_id)
    if payload.type in {"cleanup_session", "terminal_cleanup"}:
        if config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED:
            raise OmnigentBridgeError(
                "Typed owned-resource cleanup is unavailable in this mode.",
                failure_class="user_error",
                status_code=501,
                code="omnigent_bridge_capability_unavailable",
            )
        assert embedded_facade is not None
        cleanup_row = await store.get_session_by_provider_session_id(session_id)
        cleanup_refs = dict(getattr(cleanup_row, "terminal_refs", None) or {})
        cleanup_lease = str(getattr(cleanup_row, "host_lease_ref", None) or "").strip()
        if (
            cleanup_row is None
            or not cleanup_lease
            or cleanup_refs.get("cleanupState") not in {"runner_exited", "failed"}
        ):
            raise OmnigentBridgeError(
                "Cleanup requires durable terminal evidence and lease authority.",
                failure_class="user_error",
                status_code=status.HTTP_409_CONFLICT,
                code="omnigent_cleanup_not_ready",
            )
        await _revoke_session_retrieval_authority(
            session_id=session_id,
            registry=registry,
            store=store,
            reason="session_cleanup",
        )
        return await embedded_facade.cleanup_session(
            session_id,
            payload=payload.model_dump(by_alias=True, exclude_none=True),
            actor=actor,
        )
    if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
        assert embedded_facade is not None
        if payload.type == "harvest_session":
            return await embedded_facade.harvest_session(
                session_id,
                payload=payload.model_dump(by_alias=True, exclude_none=True),
                actor=actor,
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
            session_id=session_id, event=payload, actor=actor
        )
    assert proxy is not None
    return await proxy.post_event(session_id=session_id, event=payload)


async def _claim_owned_session_turn(
    *,
    store: OmnigentBridgeSessionStore,
    session_id: str,
    event_type: str,
    turn_source: TurnSource,
    actor: str,
    idempotency_key: str,
    payload_digest: str,
) -> dict[str, Any] | None:
    """Claim one canonical turn command for a provider-session-scoped mutation.

    Source: MoonLadderStudios/MoonMind#3707 §1. The provider-session surface is
    an ordinary instruction source, not an independent bridge authority: it
    resolves the same canonical session, records a distinct turn attempt, and
    fences incompatible cleanup before the provider is mutated. Sessions with no
    canonical projection yet (legacy proxy sessions without a bridge row) are not
    blocked -- there is no canonical authority to preserve.
    """

    row = await store.get_session_by_provider_session_id(session_id)
    if row is None:
        return None
    from moonmind.omnigent.control_plane.records import ControlPlaneOutcome
    from moonmind.omnigent.control_plane.turn_admission import (
        CanonicalTurnAdmissionRejected,
    )
    from moonmind.omnigent.control_plane.turn_commands import (
        CanonicalTurnAuthorityUnavailable,
    )

    try:
        claim = await store.claim_canonical_turn_command(
            row=row,
            command_type=event_type,
            turn_source=turn_source,
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            actor_principal=str(actor) or None,
        )
    except CanonicalTurnAdmissionRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": CODE_SESSION_NOT_READY,
                "message": (
                    "This session cannot accept the request without a new "
                    f"session ({exc.decision.value})."
                ),
            },
        ) from exc
    except CanonicalTurnAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": CODE_SESSION_NOT_READY,
                "message": "The canonical session authority is unavailable.",
            },
        ) from exc
    if claim.outcome is ControlPlaneOutcome.ALREADY_APPLIED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": CODE_SESSION_NOT_READY,
                "message": "This request was already delivered to the provider.",
            },
        )
    if claim.outcome is not ControlPlaneOutcome.APPLIED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": CODE_SESSION_NOT_READY,
                "message": "The canonical turn command is owned by another delivery.",
            },
        )
    return {
        "workflowId": str(getattr(row, "moonmind_workflow_id", "") or ""),
        "idempotencyKey": idempotency_key,
    }


async def _settle_owned_session_turn(
    *,
    store: OmnigentBridgeSessionStore,
    claim: Mapping[str, Any] | None,
    delivered: bool,
) -> None:
    """Settle the canonical command with the outcome that actually occurred."""

    if not claim:
        return
    from moonmind.omnigent.control_plane.records import ControlPlaneOutcome

    try:
        await store.settle_canonical_turn_command(
            workflow_id=str(claim["workflowId"]),
            idempotency_key=str(claim["idempotencyKey"]),
            outcome=(
                ControlPlaneOutcome.APPLIED
                if delivered
                else ControlPlaneOutcome.DELIVERY_UNKNOWN
            ),
        )
    except Exception:
        logger.exception("Canonical Omnigent turn settlement remains pending")


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
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store),
) -> dict[str, Any]:
    """Resolve a pending Omnigent elicitation through the bridge surface."""

    facade = (
        embedded if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED else proxy
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
    # An approval response is a canonical turn like any other instruction
    # source (#3707 §1): claim the fenced turn command before the provider
    # mutation so this surface cannot become an independent bridge authority.
    claimed = await _claim_owned_session_turn(
        store=store,
        session_id=session_id,
        event_type="resolve_elicitation",
        turn_source=TurnSource.APPROVAL_RESPONSE,
        actor=str(user.id),
        idempotency_key=f"elicitation:{session_id}:{elicitation_id}",
        payload_digest=canonical_payload_digest(payload or {}),
    )
    try:
        result = await facade.resolve_elicitation(
            session_id=session_id,
            elicitation_id=elicitation_id,
            payload=payload,
            **(
                {"actor": str(user.id)}
                if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
                else {}
            ),
        )
    except OmnigentBridgeError as exc:
        await _settle_owned_session_turn(
            store=store, claim=claimed, delivered=False
        )
        raise _http_error_from_bridge(exc) from exc
    except BaseException:
        await _settle_owned_session_turn(
            store=store, claim=claimed, delivered=False
        )
        raise
    await _settle_owned_session_turn(store=store, claim=claimed, delivered=True)
    return result


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
        agents = await facade.list_agents()
        try:
            async with async_session_maker() as session:
                await synchronize_upstream_inventory(
                    session,
                    endpoint_ref="default",
                    bridge_mode=str(config.host_protocol_mode),
                    inventory=agents,
                )
        except Exception:
            # Projection evidence is auxiliary to the authenticated inventory
            # response and must not overwrite primary bridge success.
            logger.exception("Failed to persist Omnigent agent inventory projection")
        return agents
    except OmnigentBridgeError as exc:
        try:
            async with async_session_maker() as session:
                await record_upstream_sync_failure(
                    session,
                    endpoint_ref="default",
                    bridge_mode=str(config.host_protocol_mode),
                    error=str(exc),
                )
        except Exception:
            logger.exception("Failed to record Omnigent inventory sync failure")
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
        hosts = await facade.list_hosts()
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
            policy_authority = await _resolve_bridge_policy_authority()
            evidence = await _resolve_embedded_evidence(
                config, policy_authority=policy_authority
            )
        else:
            policy_authority = await _resolve_bridge_policy_authority_optional()
            evidence = None
        readiness = config.readiness(evidence_validation=evidence)
        diagnostics = _compatibility_diagnostics(
            config=config,
            readiness=readiness,
            policy_authority=policy_authority,
        )
        return [
            {
                **host,
                "compatibilityDiagnostics": {
                    **diagnostics,
                    "lifecycleState": host.get("status"),
                    "supportedCapabilities": sorted(
                        str(item) for item in host.get("capabilities", [])
                    ),
                },
            }
            for host in hosts
        ]
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
        raise HTTPException(
            status_code=403, detail={"code": "host_auth_operator_required"}
        )


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
        raise HTTPException(
            status_code=409, detail={"code": "host_auth_unconfigured"}
        ) from exc
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
        if (
            not config.enabled
            or config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED
        ):
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
            4403 if exc.code in {"host_auth_revoked", "host_auth_disabled"} else 1013
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
        close_code = (
            4403 if exc.code in {"host_auth_revoked", "host_auth_disabled"} else 1013
        )
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
async def embedded_omnigent_runner_tunnel(websocket: WebSocket, runner_id: str) -> None:
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
            runner_id=runner_id,
            headers=websocket.headers,
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


# ---------------------------------------------------------------------------
# Binding-scoped Workflow Chat facade (MoonLadderStudios/MoonMind#3634)
#
# The native Omnigent web application drives one binding-scoped surface keyed by
# an opaque ``chatBindingId`` (docs/Omnigent/OmnigentBridge.md §4.2, §5.2):
#
#   /api/workflow-chat-bindings/{chatBindingId}/omnigent/{path}
#
# Every request (and every SSE reconnect) independently authenticates the
# MoonMind caller, resolves the durable binding, authorizes the caller against
# the bound Workflow Execution and requested operation, verifies session
# references map to the one bound provider session, recomputes capabilities from
# trusted state, strips MoonMind credentials, injects server-side upstream
# authority, and fails closed before any unauthorized upstream access.
#
# Binding resolution seam: allocation of the dedicated opaque ``chat_binding_id``
# column (§7.1) is MoonLadderStudios/MoonMind#3633. Until it lands, the facade
# resolves ``chatBindingId`` through the durable bridge session — the existing
# opaque, server-owned MoonMind key. The provider session id and upstream
# endpoint stay server-side, so the browser contract is unchanged when a
# dedicated lookup is introduced (swap ``_resolve_chat_binding_row`` only).
# ---------------------------------------------------------------------------

WORKFLOW_CHAT_BINDINGS_MOUNT_PATH = "/api/workflow-chat-bindings"

workflow_chat_router = APIRouter(tags=["Omnigent Workflow Chat"])

_FACADE_MAX_BODY_BYTES = 1 * 1024 * 1024
# Full caller reauthorization cadence while a stream is open. Each new SSE
# connection (including every EventSource reconnect) is fully authorized by the
# route dependencies; a long-lived open stream additionally re-verifies binding
# authority on this cadence so it cannot silently outlive revoked authority.
_FACADE_STREAM_REAUTH_EVERY_POLLS = 15


async def _resolve_chat_binding_row(
    *, chat_binding_id: str, store: OmnigentBridgeSessionStore
) -> Any | None:
    """Resolve the durable binding row for an opaque ``chatBindingId``.

    Single resolution seam (see module note). MoonLadderStudios/MoonMind#3633
    added the dedicated opaque ``chat_binding_id`` column, so the browser-facing
    id is resolved by that column. The resolved row's server-owned
    ``bridge_session_id`` is what journal and terminal lookups use.
    """

    return await store.get_session_by_chat_binding_id(chat_binding_id)


def _audit_facade(
    *,
    outcome: str,
    operation: str,
    chat_binding_id: str,
    user: Any,
    reason: str | None = None,
    scan_evidence: NativeScanEvidence | None = None,
) -> None:
    """Record bounded, non-disclosing audit evidence for the facade boundary.

    Security-relevant denials and mutations are logged server-side with the
    binding id and caller only. The record never states whether an alternate
    provider session exists, matching the non-enumerating browser contract.

    When an outbound scan produced a decision, its bounded evidence (scanner
    policy ref, payload digest, outcome, and redacted finding categories/safe
    locations) is recorded too — never the detected value or the message body.
    """

    scan_suffix = ""
    if scan_evidence is not None:
        parts = [
            f"scanOutcome={scan_evidence.outcome}",
            f"scannerPolicyRef={scan_evidence.scanner_policy_ref}",
            f"payloadDigest={scan_evidence.payload_digest}",
        ]
        if scan_evidence.findings:
            categories = sorted(
                {finding.category for finding in scan_evidence.findings}
            )
            parts.append(f"findingCategories={','.join(categories)}")
            parts.append(
                "findingLocations="
                + ",".join(finding.location for finding in scan_evidence.findings)
            )
        scan_suffix = " " + " ".join(parts)
    logger.info(
        "omnigent.workflow_chat_facade outcome=%s operation=%s binding=%s caller=%s%s%s",
        outcome,
        operation,
        chat_binding_id,
        getattr(user, "id", None),
        f" reason={reason}" if reason else "",
        scan_suffix,
    )


def _binding_unknown_error() -> WorkflowChatFacadeError:
    """One non-enumerating error shared by unknown-binding and unauthorized-caller.

    Returning the same typed 404 for a missing binding and for a caller that does
    not own an existing binding prevents enumerating which binding ids exist and
    never reveals whether an alternate provider session exists (OB-§4.2).
    """

    return WorkflowChatFacadeError(
        "No Workflow Chat binding resolves the requested id.",
        failure_class="user_error",
        status_code=status.HTTP_404_NOT_FOUND,
        code=CODE_BINDING_UNKNOWN,
    )


async def _resolve_and_authorize_chat_binding(
    *,
    chat_binding_id: str,
    operation: str,
    user: User,
    service: Any,
    store: OmnigentBridgeSessionStore,
) -> Any:
    """Resolve the durable binding and authorize the caller, failing closed.

    Implements OB-§4.2 steps 1-3 for one request: authenticate (already enforced
    by the route dependency), resolve the durable binding, and authorize the
    caller against the bound Workflow Execution. Unknown binding and unauthorized
    caller collapse to one non-enumerating response; the audit record carries the
    precise reason.
    """

    row = await _resolve_chat_binding_row(chat_binding_id=chat_binding_id, store=store)
    if row is None:
        _audit_facade(
            outcome="denied",
            operation=operation,
            chat_binding_id=chat_binding_id,
            user=user,
            reason="binding_unknown",
        )
        raise _binding_unknown_error()
    metadata = dict(getattr(row, "metadata_", None) or {})
    caller_authorities = metadata.get("callerAuthorities")
    caller_id = str(getattr(user, "id", "") or "")
    # A durable, principal-specific sharing grant authorizes binding discovery;
    # its capability map is still intersected independently for every request.
    # Missing/empty grants never turn a guessed viewer into an owner.
    if (
        isinstance(caller_authorities, Mapping)
        and isinstance(caller_authorities.get(caller_id), Mapping)
        and any(value is True for value in caller_authorities[caller_id].values())
    ):
        return row
    workflow_id = str(getattr(row, "moonmind_workflow_id", "") or "").strip()
    agent_run_id = str(getattr(row, "moonmind_agent_run_id", "") or "").strip() or None
    principal = await resolve_execution_principal(
        user=user,
        service=service,
        workflow_id_header=workflow_id,
        agent_run_id_header=agent_run_id,
    )
    if not principal.workflow_id:
        _audit_facade(
            outcome="denied",
            operation=operation,
            chat_binding_id=chat_binding_id,
            user=user,
            reason="caller_unauthorized",
        )
        raise _binding_unknown_error()
    return row


# Upstream/topology keys that must never reach the browser, at any nesting
# depth. Dropped from every mapping in a virtualized response — not only the
# root — so a list result (e.g. ``GET v1/agents``) or a nested snapshot/resource
# object cannot preserve ``endpoint``, ``host_id``, ``runner_id``, or MoonMind
# binding metadata (OB-§4.2: provider topology stays server-side).
_FACADE_TOPOLOGY_KEYS: frozenset[str] = frozenset(
    {
        "moonmind",
        "host_id",
        "hostId",
        "runner_id",
        "runnerId",
        "endpoint",
        "endpoint_ref",
        "endpointRef",
        "base_url",
        "baseUrl",
        "upstream_url",
        "upstreamUrl",
    }
)


def _virtualize_facade_payload(
    payload: Any, *, provider_session_id: str, chat_binding_id: str
) -> Any:
    """Rewrite a response so no server-side identity reaches the browser.

    Exact-match occurrences of the provider session id are replaced by the
    virtual ``chatBindingId`` at any nesting depth, and upstream topology fields
    (endpoint, host, runner, and MoonMind binding metadata) are dropped from
    every mapping regardless of response shape. Provider session id and upstream
    endpoint never leave the server (``exposeProviderSessionId: false``).
    """

    def _rewrite(value: Any) -> Any:
        if isinstance(value, str):
            if provider_session_id and value == provider_session_id:
                return chat_binding_id
            return value
        if isinstance(value, dict):
            return {
                key: _rewrite(item)
                for key, item in value.items()
                if key not in _FACADE_TOPOLOGY_KEYS
            }
        if isinstance(value, list):
            return [_rewrite(item) for item in value]
        return value

    rewritten = _rewrite(payload)
    if isinstance(rewritten, dict):
        if "id" in rewritten or "session_id" in rewritten or "sessionId" in rewritten:
            rewritten["id"] = chat_binding_id
    return rewritten


async def _read_facade_json_body(request: Request) -> dict[str, Any]:
    """Read a bounded JSON object body for a facade mutation, or fail closed."""

    content_type = str(request.headers.get("content-type") or "").split(";")[0].strip()
    if content_type.lower() != "application/json":
        raise WorkflowChatFacadeError(
            "This operation requires an application/json request body.",
            failure_class="user_error",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            code=CODE_UNSUPPORTED_MEDIA_TYPE,
        )

    def _too_large() -> WorkflowChatFacadeError:
        return WorkflowChatFacadeError(
            "The request body exceeds the Workflow Chat facade limit.",
            failure_class="user_error",
            status_code=413,
            code=CODE_PAYLOAD_TOO_LARGE,
        )

    # Reject an oversized *declared* length before reading a single byte so an
    # authenticated client cannot make the API buffer a very large payload just
    # to receive the 413 (OB-§4.2 resource boundary).
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > _FACADE_MAX_BODY_BYTES:
                raise _too_large()
        except ValueError as exc:
            raise WorkflowChatFacadeError(
                "The request declared an invalid content length.",
                failure_class="user_error",
                status_code=status.HTTP_400_BAD_REQUEST,
                code=CODE_MALFORMED_PAYLOAD,
            ) from exc

    # Consume the ASGI body incrementally with a hard byte cap so a chunked or
    # length-underdeclared upload is aborted as soon as it crosses the limit
    # rather than after the whole payload is resident in memory.
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > _FACADE_MAX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        parsed = json.loads(raw or b"{}")
    except (json.JSONDecodeError, ValueError) as exc:
        raise WorkflowChatFacadeError(
            "The request body is not valid JSON.",
            failure_class="user_error",
            status_code=status.HTTP_400_BAD_REQUEST,
            code=CODE_MALFORMED_PAYLOAD,
        ) from exc
    if not isinstance(parsed, dict):
        raise WorkflowChatFacadeError(
            "The request body must be a JSON object.",
            failure_class="user_error",
            status_code=status.HTTP_400_BAD_REQUEST,
            code=CODE_MALFORMED_PAYLOAD,
        )
    return parsed


def _facade_liveness_state(row: Any) -> str:
    status_value = str(getattr(row, "status", "") or "").strip().lower()
    if is_read_only(status_value):
        return "ended"
    if not str(getattr(row, "omnigent_session_id", "") or "").strip():
        return "starting"
    return "available"


def _durable_terminal_snapshot(
    row: Any, *, chat_binding_id: str, capabilities: dict[str, bool]
) -> dict[str, Any]:
    """Build a read-only bootstrap snapshot from the durable projection.

    Used when a terminal binding's provider session was deleted after harvest:
    the durable row still carries final snapshots, event journals, and terminal
    refs, so the native application can display its read-only transcript without
    a live upstream session id. No server-side identity is included.
    """

    envelope = _terminal_envelope(row)
    snapshot: dict[str, Any] = {
        "id": chat_binding_id,
        "sessionId": chat_binding_id,
        "status": str(getattr(row, "status", "") or ""),
        "readOnly": True,
        "capabilities": capabilities,
        "providerSessionAvailable": False,
    }
    if envelope is not None:
        snapshot["terminal"] = envelope.model_dump(by_alias=True)
    return snapshot


def _terminal_items_query(query_params: Any) -> tuple[int, str | None, str | None, str]:
    """Validate the stock transcript pagination query for durable replay."""

    try:
        limit = int(str(query_params.get("limit") or "100"))
    except (TypeError, ValueError) as exc:
        raise WorkflowChatFacadeError(
            "The transcript page limit is invalid.",
            failure_class="user_error",
            status_code=400,
            code=CODE_MALFORMED_PAYLOAD,
        ) from exc
    order = str(query_params.get("order") or "asc").strip().lower()
    after = str(query_params.get("after") or "").strip() or None
    before = str(query_params.get("before") or "").strip() or None
    if not 1 <= limit <= 1000 or order not in {"asc", "desc"} or (after and before):
        raise WorkflowChatFacadeError(
            "The transcript pagination query is invalid.",
            failure_class="user_error",
            status_code=400,
            code=CODE_MALFORMED_PAYLOAD,
        )
    return limit, after, before, order


def _durable_provider_session_id(row: Any) -> str:
    """Recover the internal provider id needed only for response redaction."""

    metadata = dict(getattr(row, "metadata_", None) or {})
    authority = dict(metadata.get("capabilityAuthority") or {})
    provider_session_id = str(authority.get("providerSessionId") or "").strip()
    if provider_session_id:
        return provider_session_id
    journal = metadata.get(BRIDGE_EVENT_JOURNAL_KEY)
    if isinstance(journal, list):
        for event in reversed(journal):
            if (
                not isinstance(event, dict)
                or event.get("type") != SESSION_CREATED_EVENT_TYPE
            ):
                continue
            provider_session_id = str(event.get("omnigentSessionId") or "").strip()
            if provider_session_id:
                return provider_session_id
    return ""


async def _durable_terminal_items_page(
    row: Any,
    *,
    chat_binding_id: str,
    query_params: Any,
) -> dict[str, Any]:
    """Page captured terminal transcript items after provider cleanup."""

    limit, after, before, order = _terminal_items_query(query_params)
    final_snapshot_ref = str(getattr(row, "final_snapshot_ref", None) or "").strip()
    if not final_snapshot_ref:
        return {
            "object": "list",
            "data": [],
            "first_id": None,
            "last_id": None,
            "has_more": False,
        }
    provider_session_id = _durable_provider_session_id(row)
    if not provider_session_id:
        raise WorkflowChatFacadeError(
            "The captured terminal transcript cannot be safely virtualized.",
            failure_class="integration_error",
            status_code=503,
            code="omnigent_bridge_terminal_evidence_unavailable",
        )
    try:
        captured = json.loads(
            await LocalOmnigentArtifactGateway().read_text(final_snapshot_ref)
        )
    except (OmnigentArtifactError, OSError, ValueError) as exc:
        raise WorkflowChatFacadeError(
            "The captured terminal transcript is unavailable.",
            failure_class="integration_error",
            status_code=503,
            code="omnigent_bridge_terminal_evidence_unavailable",
        ) from exc
    raw_items = captured.get("items") if isinstance(captured, dict) else None
    items = [item for item in (raw_items or []) if isinstance(item, dict)]
    ordered = list(items if order == "asc" else reversed(items))
    cursor = after or before
    if cursor:
        cursor_index = next(
            (
                index
                for index, item in enumerate(ordered)
                if str(item.get("id") or "") == cursor
            ),
            None,
        )
        if cursor_index is None:
            ordered = []
        elif after:
            ordered = ordered[cursor_index + 1 :]
        else:
            ordered = ordered[:cursor_index]
    has_more = len(ordered) > limit
    page = ordered[:limit]
    virtualized = _virtualize_facade_payload(
        page,
        provider_session_id=provider_session_id,
        chat_binding_id=chat_binding_id,
    )
    return {
        "object": "list",
        "data": virtualized,
        "first_id": (str(page[0].get("id") or "") or None) if page else None,
        "last_id": (str(page[-1].get("id") or "") or None) if page else None,
        "has_more": has_more,
    }


def _facade_idempotency_key(request: Request) -> str | None:
    """Return the caller-supplied ``Idempotency-Key`` for a facade mutation."""

    raw = str(request.headers.get("Idempotency-Key") or "").strip()
    return raw[:255] or None


def _enforce_native_outbound_scan(
    *,
    surface: NativeScanSurface,
    body: Any,
    idempotency_key: str | None,
    chat_binding_id: str,
    user: Any,
    operation: str,
) -> NativeScanEvidence:
    """Preserve the high-security outbound-scan guarantee at the native boundary.

    Runs the versioned native-scan contract (MoonLadderStudios/MoonMind#3637)
    before any provider forward and returns bounded, digest-bound evidence. A
    secret-like finding is rejected with a distinct content-blocked error that
    surfaces only the redacted finding category/location; an enforcement failure
    (unknown/uninspectable shape, undecodable field, binary part, or a scanner
    error) fails closed with a *separate* enforcement-unavailable error so the
    two conditions are never conflated. High-security mode disabled returns an
    allow result and the original payload is forwarded unchanged. The raised
    error and the audit record never echo the detected value or the message
    body.
    """

    try:
        evidence = scan_native_outbound(
            surface=surface,
            body=body or {},
            idempotency_key=idempotency_key,
        )
    except NativeScanBlockedError as exc:
        _audit_facade(
            outcome="content_blocked",
            operation=operation,
            chat_binding_id=chat_binding_id,
            user=user,
            reason="content_blocked",
            scan_evidence=exc.evidence,
        )
        raise WorkflowChatFacadeError(
            "The message contains content blocked by the security policy.",
            failure_class="user_error",
            status_code=status.HTTP_403_FORBIDDEN,
            code=CODE_CONTENT_BLOCKED,
            public_details={
                "findings": [
                    {"category": finding.category, "location": finding.location}
                    for finding in exc.evidence.findings
                ],
            },
        ) from exc
    except NativeScanEnforcementError as exc:
        _audit_facade(
            outcome="enforcement_unavailable",
            operation=operation,
            chat_binding_id=chat_binding_id,
            user=user,
            reason=exc.evidence.reason,
            scan_evidence=exc.evidence,
        )
        raise WorkflowChatFacadeError(
            "The security scan required for this request is unavailable.",
            failure_class="system_error",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=CODE_ENFORCEMENT_UNAVAILABLE,
        ) from exc
    return evidence


async def _revalidate_binding_before_mutation(
    *,
    store: OmnigentBridgeSessionStore,
    chat_binding_id: str,
    expected_provider_session_id: str,
    expected_authority_digest: str,
    user: User,
    expected_session_epoch: Any = None,
    expected_active_turn: Any = None,
    expected_elicitation: Any = None,
    expected_terminal_state: Any = None,
    expected_agent_profile_digest: Any = None,
    expected_provider_generation: Any = None,
    expected_launch_snapshot_ref: Any = None,
    expected_policy_digest: Any = None,
    allow_terminal_mutation: bool = False,
) -> tuple[Any, str, dict[str, bool]]:
    """Re-read and compare-and-set the binding state at the mutation handoff.

    The read-only decision and capabilities were computed from the row loaded at
    the start of the request, but the provider mutation is issued later — after
    the body is buffered and scanned. Re-resolve the durable binding immediately
    before the side effect so a session that terminalized, was replaced, or had
    its policy revoked mid-request cannot be mutated with stale authority. The
    fresh provider session id and fresh capabilities are returned for the caller
    to forward with and re-check.
    """

    fresh = await _resolve_chat_binding_row(
        chat_binding_id=chat_binding_id, store=store
    )
    if fresh is None:
        raise _binding_unknown_error()
    fresh_status = str(getattr(fresh, "status", "") or "")
    if (
        expected_terminal_state is not None
        and str(expected_terminal_state) != fresh_status
    ):
        raise WorkflowChatFacadeError(
            "The Workflow Chat session state does not match the request precondition.",
            failure_class="user_error",
            status_code=status.HTTP_409_CONFLICT,
            code=CODE_SESSION_NOT_READY,
        )
    if is_read_only(fresh_status) and not allow_terminal_mutation:
        raise WorkflowChatFacadeError(
            "This Workflow Chat session is terminal and read-only.",
            failure_class="user_error",
            status_code=status.HTTP_409_CONFLICT,
            code=CODE_SESSION_READ_ONLY,
        )
    fresh_provider = str(getattr(fresh, "omnigent_session_id", "") or "").strip()
    if fresh_provider != expected_provider_session_id:
        raise WorkflowChatFacadeError(
            "The Workflow Chat session changed before the request completed.",
            failure_class="user_error",
            status_code=status.HTTP_409_CONFLICT,
            code=CODE_SESSION_NOT_READY,
        )
    caller_grants = caller_capabilities_for_bridge(fresh, user)
    current_capabilities = resolve_bridge_row_capabilities(
        fresh, caller_capabilities=caller_grants
    )
    if current_capabilities.authority_digest != expected_authority_digest:
        raise WorkflowChatFacadeError(
            "The Workflow Chat authority changed before the request completed.",
            failure_class="user_error",
            status_code=status.HTTP_409_CONFLICT,
            code=CODE_SESSION_NOT_READY,
        )
    fresh_capabilities = resolve_bridge_row_capabilities(
        fresh,
        caller_capabilities=caller_grants,
        expected_session_epoch=expected_session_epoch,
        expected_active_turn=expected_active_turn,
        expected_elicitation=expected_elicitation,
        expected_agent_profile_digest=expected_agent_profile_digest,
        expected_provider_generation=expected_provider_generation,
        expected_launch_snapshot_ref=expected_launch_snapshot_ref,
        expected_policy_digest=expected_policy_digest,
    )
    has_request_precondition = any(
        value is not None
        for value in (
            expected_session_epoch,
            expected_active_turn,
            expected_elicitation,
            expected_agent_profile_digest,
            expected_provider_generation,
            expected_launch_snapshot_ref,
            expected_policy_digest,
        )
    )
    if has_request_precondition:
        stale_reasons = {
            "session_epoch_stale",
            "active_turn_stale",
            "elicitation_stale",
            "agent_profile_stale",
            "provider_generation_stale",
            "launch_snapshot_stale",
            "policy_snapshot_stale",
        }
        if any(
            item.reason in stale_reasons
            for item in fresh_capabilities.decisions.values()
        ):
            raise WorkflowChatFacadeError(
                "The Workflow Chat request precondition is stale.",
                failure_class="user_error",
                status_code=status.HTTP_409_CONFLICT,
                code=CODE_SESSION_NOT_READY,
            )
    return fresh, fresh_provider, fresh_capabilities.capabilities


def _mutation_preconditions(body: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return only caller-supplied canonical compare-and-set fields."""

    payload = body or {}
    return {
        "expected_session_epoch": payload.get("expectedSessionEpoch"),
        "expected_active_turn": payload.get("expectedActiveTurn"),
        "expected_elicitation": payload.get("expectedElicitation"),
        "expected_terminal_state": payload.get("expectedTerminalState"),
        "expected_agent_profile_digest": payload.get("expectedAgentProfileDigest"),
        "expected_provider_generation": payload.get(
            "expectedProviderProfileGeneration"
        ),
        "expected_launch_snapshot_ref": payload.get("expectedLaunchSnapshotRef"),
        "expected_policy_digest": payload.get("expectedPolicyDigest"),
    }


#: Explicit control-type -> closed canonical turn source (#3707). Instruction
#: bearing controls are Workflow Chat turns and elicitation resolution is an
#: approval response; every other native control is session-directed steering.
#: The mapping is exhaustive by construction (no substring matching): unmapped
#: control types are steering because they direct the session rather than submit
#: new instruction text.
_INSTRUCTION_CONTROL_TYPES: frozenset[str] = frozenset(
    {"message", "user.message", "workflow_chat_message"}
)
_APPROVAL_CONTROL_TYPES: frozenset[str] = frozenset({"resolve_elicitation"})


def _native_turn_source(event_type: str) -> "TurnSource":
    normalized = str(event_type or "").strip().lower()
    if normalized in _INSTRUCTION_CONTROL_TYPES:
        return TurnSource.WORKFLOW_CHAT
    if normalized in _APPROVAL_CONTROL_TYPES:
        return TurnSource.APPROVAL_RESPONSE
    return TurnSource.STEERING


async def _claim_facade_message(
    *,
    store: OmnigentBridgeSessionStore,
    row: Any,
    event_type: str,
    actor: str,
    idempotency_key: str,
    payload_digest: str,
    turn_source: "TurnSource | None" = None,
    scan_evidence: NativeScanEvidence | None = None,
    request_time: str | None = None,
    request_preconditions: Mapping[str, Any] | None = None,
) -> bool:
    """Atomically claim a native message submission for exactly-once forwarding.

    Persists a durable, secret-safe control record keyed by the caller's
    idempotency key. Returns ``True`` for the one claimant that may forward the
    provider POST and ``False`` on a genuine replay (browser retry or
    double-submit) of the *same* payload, so the facade never issues a duplicate
    provider turn or duplicate billing. The scanned payload digest is recorded so
    the claim durably names the exact payload that passed the outbound scan.

    A repeated idempotency key bound to a *different* scanned payload digest is
    not a benign replay: reporting it as a successful deduplication would
    silently drop the changed message. Such a conflict fails closed with a
    distinct, redacted error rather than returning ``False``.
    """

    event_identity = f"workflow-chat-message:{idempotency_key}"
    canonical_claim = getattr(store, "claim_canonical_turn_command", None)
    if callable(canonical_claim):
        try:
            claim = await canonical_claim(
                row=row,
                command_type=event_type,
                turn_source=turn_source or _native_turn_source(event_type),
                idempotency_key=idempotency_key,
                payload_digest=payload_digest,
                actor_principal=str(actor) or None,
            )
        except Exception as exc:
            from moonmind.omnigent.control_plane.turn_admission import (
                CanonicalTurnAdmissionRejected,
            )
            from moonmind.omnigent.control_plane.turn_commands import (
                CanonicalTurnAuthorityUnavailable,
            )

            if isinstance(exc, CanonicalTurnAdmissionRejected):
                # A refused admission -- notably a session whose cleanup has
                # completed -- is the same actionable session-not-ready conflict
                # the provider-session endpoint already returns, not a server
                # error for Workflow Chat, steering, and facade approvals.
                raise WorkflowChatFacadeError(
                    "This session cannot accept the request without a new "
                    f"session ({exc.decision.value}).",
                    failure_class="system_error",
                    status_code=status.HTTP_409_CONFLICT,
                    code=CODE_SESSION_NOT_READY,
                ) from exc
            if isinstance(exc, CanonicalTurnAuthorityUnavailable):
                raise WorkflowChatFacadeError(
                    str(exc),
                    failure_class="system_error",
                    status_code=status.HTTP_409_CONFLICT,
                    code=CODE_SESSION_NOT_READY,
                ) from exc
            raise
        from moonmind.omnigent.control_plane.records import ControlPlaneOutcome

        if claim.outcome is ControlPlaneOutcome.ALREADY_APPLIED:
            # Canonical settlement is authoritative even if the facade's
            # compatibility ledger was lost or has not yet converged. Never
            # redeliver a provider turn (or duplicate billing) from a second
            # transport-local claim.
            return False
        if claim.outcome is not ControlPlaneOutcome.APPLIED:
            raise WorkflowChatFacadeError(
                "The canonical turn command is owned by another delivery.",
                failure_class="system_error",
                status_code=status.HTTP_409_CONFLICT,
                code=CODE_SESSION_NOT_READY,
            )
    now = request_time or datetime.now(tz=UTC).isoformat()
    launch = dict(getattr(row, "effective_launch_snapshot_json", None) or {})
    policy = dict(launch.get("policyAuthority") or {})
    authority = dict(
        dict(getattr(row, "metadata_", None) or {}).get("capabilityAuthority") or {}
    )
    state = dict(authority.get("state") or {})
    pending_receipt: dict[str, Any] = {
        "receiptSchemaVersion": "moonmind.omnigent.mutation-receipt.v1",
        "actor": actor,
        "controlType": event_type,
        "controlOutcome": "pending",
        "moonmindRequestId": idempotency_key,
        "workflowId": str(getattr(row, "moonmind_workflow_id", "") or ""),
        "runId": str(getattr(row, "moonmind_run_id", "") or ""),
        "stepExecutionId": str(getattr(row, "step_execution_id", "") or ""),
        "agentRunId": str(getattr(row, "moonmind_agent_run_id", "") or ""),
        "bridgeSessionId": str(getattr(row, "bridge_session_id", "") or ""),
        "providerSessionId": str(getattr(row, "omnigent_session_id", "") or ""),
        "providerProfileId": str(getattr(row, "provider_profile_id", "") or ""),
        "providerProfileGeneration": getattr(row, "credential_generation", None),
        "agentProfileRef": str(launch.get("executionProfileRef") or ""),
        "agentProfileDigest": str(launch.get("executionProfileDigest") or ""),
        "launchPolicyRef": str(launch.get("launchPolicyRef") or ""),
        "effectiveLaunchSnapshotRef": str(launch.get("snapshotRef") or ""),
        "policySnapshotRef": str(policy.get("snapshotRef") or ""),
        "policyDigest": str(policy.get("policyDigest") or ""),
        "expectedSessionEpoch": state.get("sessionEpoch"),
        "expectedActiveTurn": state.get("activeTurnId"),
        "expectedElicitation": state.get("elicitationId"),
        "expectedTerminalState": str(getattr(row, "status", "") or ""),
        "requestTime": now,
        "dispatchTime": None,
        "completionTime": None,
        "durableAuditRef": (
            f"omnigent-bridge-event://{getattr(row, 'bridge_session_id', '')}/"
            f"workflow-chat-control/{idempotency_key}"
        ),
        "controlIdempotencyKey": idempotency_key,
        "scannedPayloadDigest": payload_digest,
        "sourceMode": "workflow_chat_facade",
    }
    for receipt_key, argument_key in (
        ("expectedSessionEpoch", "expected_session_epoch"),
        ("expectedActiveTurn", "expected_active_turn"),
        ("expectedElicitation", "expected_elicitation"),
        ("expectedTerminalState", "expected_terminal_state"),
        ("expectedAgentProfileDigest", "expected_agent_profile_digest"),
        ("expectedProviderProfileGeneration", "expected_provider_generation"),
        ("expectedLaunchSnapshotRef", "expected_launch_snapshot_ref"),
        ("expectedPolicyDigest", "expected_policy_digest"),
    ):
        supplied = dict(request_preconditions or {}).get(argument_key)
        if supplied is not None:
            pending_receipt[receipt_key] = supplied
    if scan_evidence is not None:
        pending_receipt.update(scan_evidence.audit_metadata())
    claimed = await store.claim_lifecycle_event(
        row.idempotency_key,
        event_type="workflow_chat_message",
        event_identity=event_identity,
        summary=f"native chat {event_type}",
        metadata=pending_receipt,
    )
    if claimed:
        return True

    # An existing claim for this key: reconcile against the digest it was first
    # bound to. A matching digest is a true replay; a different digest means the
    # same key was reused for changed content and must fail closed.
    existing = await store.get_lifecycle_event_metadata(
        row.idempotency_key, event_identity=event_identity
    )
    prior_digest = str((existing or {}).get("scannedPayloadDigest") or "")
    if prior_digest and prior_digest != payload_digest:
        raise WorkflowChatFacadeError(
            "This Idempotency-Key was already used for a different message.",
            failure_class="user_error",
            status_code=status.HTTP_409_CONFLICT,
            code=CODE_IDEMPOTENCY_CONFLICT,
        )
    # A process may have persisted the bridge receipt and then failed before
    # settling the canonical command. Reconcile that narrow handoff on replay;
    # never re-forward the provider mutation.
    canonical_settle = getattr(store, "settle_canonical_turn_command", None)
    if callable(canonical_settle):
        from moonmind.omnigent.control_plane.records import ControlPlaneOutcome

        for prior_outcome, canonical_outcome in (
            ("posted", ControlPlaneOutcome.APPLIED),
            ("delivery_unknown", ControlPlaneOutcome.DELIVERY_UNKNOWN),
        ):
            receipt = await store.get_lifecycle_event_metadata(
                row.idempotency_key,
                event_identity=(
                    f"workflow-chat-control:{event_type}:{prior_outcome}:"
                    f"{idempotency_key}"
                ),
            )
            if receipt is not None:
                await canonical_settle(
                    workflow_id=str(row.moonmind_workflow_id),
                    idempotency_key=idempotency_key,
                    outcome=canonical_outcome,
                    result_ref=str(receipt.get("durableAuditRef") or "") or None,
                )
                break
    return False


async def _record_facade_mutation_audit(
    *,
    store: OmnigentBridgeSessionStore,
    row: Any,
    control_type: str,
    outcome: str,
    actor: str,
    idempotency_key: str | None = None,
    scan_evidence: NativeScanEvidence | None = None,
    upstream_result: Any = None,
    request_time: str | None = None,
    dispatch_time: str | None = None,
    request_preconditions: Mapping[str, Any] | None = None,
) -> str:
    """Persist durable, secret-safe evidence of a facade mutation (OB-§19).

    A rotating process log is not durable evidence, so mutation outcomes are
    recorded in the binding's durable control/event ledger with the actor,
    control type, normalized outcome, and (when present) idempotency key. When
    the mutation ran the native outbound scan, the bounded, non-disclosing scan
    evidence (contract version, surface, effective mode, scanner policy ref,
    payload digest, and allow/block outcome) is persisted too so the durable
    record proves the exact payload was scanned even after process logs rotate —
    for message *and* elicitation/approval mutations alike.
    """

    now = datetime.now(tz=UTC).isoformat()
    launch = dict(getattr(row, "effective_launch_snapshot_json", None) or {})
    policy = dict(launch.get("policyAuthority") or {})
    request_id = idempotency_key or f"mm-{uuid4().hex}"
    metadata: dict[str, Any] = {
        "receiptSchemaVersion": "moonmind.omnigent.mutation-receipt.v1",
        "actor": actor,
        "controlType": control_type,
        "controlOutcome": outcome,
        "reasonCode": (
            None if outcome in {"posted", "completed", "accepted"} else outcome
        ),
        "moonmindRequestId": request_id,
        "workflowId": str(getattr(row, "moonmind_workflow_id", "") or ""),
        "runId": str(getattr(row, "moonmind_run_id", "") or ""),
        "stepExecutionId": str(getattr(row, "step_execution_id", "") or ""),
        "agentRunId": str(getattr(row, "moonmind_agent_run_id", "") or ""),
        "bridgeSessionId": str(getattr(row, "bridge_session_id", "") or ""),
        "providerSessionId": str(getattr(row, "omnigent_session_id", "") or ""),
        "providerProfileId": str(getattr(row, "provider_profile_id", "") or ""),
        "providerProfileGeneration": getattr(row, "credential_generation", None),
        "agentProfileRef": str(launch.get("executionProfileRef") or ""),
        "agentProfileDigest": str(launch.get("executionProfileDigest") or ""),
        "launchPolicyRef": str(launch.get("launchPolicyRef") or ""),
        "effectiveLaunchSnapshotRef": str(launch.get("snapshotRef") or ""),
        "policySnapshotRef": str(policy.get("snapshotRef") or ""),
        "policyDigest": str(policy.get("policyDigest") or ""),
        "expectedSessionEpoch": dict(
            dict(getattr(row, "metadata_", None) or {}).get("capabilityAuthority") or {}
        )
        .get("state", {})
        .get("sessionEpoch"),
        "expectedActiveTurn": dict(
            dict(getattr(row, "metadata_", None) or {}).get("capabilityAuthority") or {}
        )
        .get("state", {})
        .get("activeTurnId"),
        "expectedElicitation": dict(
            dict(getattr(row, "metadata_", None) or {}).get("capabilityAuthority") or {}
        )
        .get("state", {})
        .get("elicitationId"),
        "expectedTerminalState": str(getattr(row, "status", "") or ""),
        "requestTime": request_time or now,
        "dispatchTime": dispatch_time,
        "completionTime": None if outcome == "pending" else now,
        "durableAuditRef": (
            f"omnigent-bridge-event://{getattr(row, 'bridge_session_id', '')}/"
            f"workflow-chat-control/{request_id}"
        ),
        "sourceMode": "workflow_chat_facade",
    }
    supplied = dict(request_preconditions or {})
    for receipt_key, argument_key in (
        ("expectedSessionEpoch", "expected_session_epoch"),
        ("expectedActiveTurn", "expected_active_turn"),
        ("expectedElicitation", "expected_elicitation"),
        ("expectedTerminalState", "expected_terminal_state"),
        ("expectedAgentProfileDigest", "expected_agent_profile_digest"),
        ("expectedProviderProfileGeneration", "expected_provider_generation"),
        ("expectedLaunchSnapshotRef", "expected_launch_snapshot_ref"),
        ("expectedPolicyDigest", "expected_policy_digest"),
    ):
        if supplied.get(argument_key) is not None:
            metadata[receipt_key] = supplied[argument_key]
    if upstream_result is not None:
        normalized = _virtualize_facade_payload(
            upstream_result,
            provider_session_id=str(getattr(row, "omnigent_session_id", "") or ""),
            chat_binding_id=str(getattr(row, "chat_binding_id", "") or ""),
        )
        metadata["normalizedResult"] = normalized
        if isinstance(upstream_result, dict):
            metadata["upstreamCorrelation"] = (
                str(
                    upstream_result.get("request_id")
                    or upstream_result.get("requestId")
                    or upstream_result.get("id")
                    or ""
                )
                or None
            )
    if idempotency_key:
        metadata["controlIdempotencyKey"] = idempotency_key
    if scan_evidence is not None:
        metadata.update(scan_evidence.audit_metadata())
    identity_suffix = idempotency_key or actor
    # A mutation is not complete without its durable receipt.  Propagate a
    # terminal-row/idempotency conflict to the caller so the durable owner can
    # retry or reconcile it; silently dropping the audit would turn a provider
    # side effect into success without objective MoonMind evidence.
    await store.record_lifecycle_event(
        row.idempotency_key,
        event_type="workflow_chat_control",
        event_identity=(
            f"workflow-chat-control:{control_type}:{outcome}:{identity_suffix}"
        ),
        summary=f"workflow chat {control_type} {outcome}",
        metadata=metadata,
    )
    canonical_settle = getattr(store, "settle_canonical_turn_command", None)
    if callable(canonical_settle) and outcome in {
        "posted",
        "completed",
        "accepted",
        "delivery_unknown",
    }:
        from moonmind.omnigent.control_plane.records import ControlPlaneOutcome

        provider_receipt_id = str(metadata.get("upstreamCorrelation") or "") or None
        await canonical_settle(
            workflow_id=str(row.moonmind_workflow_id),
            idempotency_key=request_id,
            outcome=(
                ControlPlaneOutcome.DELIVERY_UNKNOWN
                if outcome == "delivery_unknown"
                else ControlPlaneOutcome.APPLIED
            ),
            provider_receipt_id=provider_receipt_id,
            result_ref=str(metadata["durableAuditRef"]),
        )
    return request_time or now


async def _reconcile_facade_mutation(
    *,
    store: OmnigentBridgeSessionStore,
    row: Any,
    facade: Any,
    control_type: str,
    idempotency_key: str,
    provider_session_id: str,
    chat_binding_id: str,
    actor: str,
    scan_evidence: NativeScanEvidence,
    request_preconditions: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Reconcile an occupied mutation claim without repeating its side effect.

    A completed receipt is authoritative.  For stop controls, a fresh upstream
    terminal snapshot is also objective terminal evidence and can safely close
    a request whose transport outcome was ambiguous.  Other controls remain
    occupied until their provider supplies equivalent correlation evidence;
    they are never retried speculatively.
    """

    posted_identity = f"workflow-chat-control:{control_type}:posted:{idempotency_key}"
    prior = await store.get_lifecycle_event_metadata(
        row.idempotency_key, event_identity=posted_identity
    )
    if isinstance(prior, dict) and "normalizedResult" in prior:
        result = prior["normalizedResult"]
        return result if isinstance(result, dict) else {"result": result}

    if control_type not in {"stop", "session.stop", "stop_session"}:
        return None
    try:
        snapshot = await facade.get_session(provider_session_id)
    except OmnigentBridgeError:
        return None
    terminal_statuses = {
        "completed",
        "failed",
        "canceled",
        "cancelled",
        "stopped",
        "terminated",
    }
    if (
        not isinstance(snapshot, dict)
        or str(snapshot.get("status") or "").lower() not in terminal_statuses
    ):
        return None

    reconciled = _virtualize_facade_payload(
        {**snapshot, "reconciled": True},
        provider_session_id=provider_session_id,
        chat_binding_id=chat_binding_id,
    )
    await _record_facade_mutation_audit(
        store=store,
        row=row,
        control_type=control_type,
        outcome="posted",
        actor=actor,
        idempotency_key=idempotency_key,
        scan_evidence=scan_evidence,
        upstream_result=reconciled,
        request_preconditions=request_preconditions,
    )
    return reconciled


def _event_is_chat_visible(row: Any) -> bool:
    """Return whether a journal row is meant for the browser transcript.

    Bridge lifecycle/control rows explicitly set
    ``metadata.moonmind.workflowChatVisible = False`` (they can carry internal
    control idempotency keys, cleanup details, and host-lease references). Those
    rows are excluded from the native SSE stream; anything without an explicit
    ``False`` remains visible.
    """

    metadata = getattr(row, "metadata_", None)
    if not isinstance(metadata, dict):
        return True
    moonmind = metadata.get("moonmind")
    if isinstance(moonmind, dict) and moonmind.get("workflowChatVisible") is False:
        return False
    return True


async def _stream_workflow_chat_events(
    *,
    request: Request,
    chat_binding_id: str,
    bridge_session_id: str,
    provider_session_id: str,
    initial_cursor: int,
    user: User,
    service: Any,
    store: OmnigentBridgeSessionStore,
):
    """Yield durable bridge-journal events with cursor + reauthorization semantics.

    Authorization already ran before this generator is returned. The generator
    additionally re-verifies binding authority on a bounded cadence so a stream
    cannot silently outlive revoked authority, translates ``Last-Event-ID`` /
    cursor resume, surfaces retention gaps, and only reports terminal on durable
    terminal evidence (stream close is never treated as terminal success).

    Journal reads use the resolved ``bridge_session_id`` (not the browser's
    ``chat_binding_id``); reauthorization uses the ``chat_binding_id``. Rows the
    durable projection marks non-visible are skipped, and every emitted event is
    virtualized so no server-side identity reaches the browser.
    """

    last_sequence = initial_cursor
    idle_polls = 0
    polls_since_reauth = 0
    while True:
        if await request.is_disconnected():
            return
        polls_since_reauth += 1
        if polls_since_reauth >= _FACADE_STREAM_REAUTH_EVERY_POLLS:
            polls_since_reauth = 0
            try:
                refreshed = await _resolve_and_authorize_chat_binding(
                    chat_binding_id=chat_binding_id,
                    operation="stream_events",
                    user=user,
                    service=service,
                    store=store,
                )
                if (
                    not _effective_capabilities(refreshed, user).capabilities.get(
                        "viewTranscript", False
                    )
                    or str(getattr(refreshed, "bridge_session_id", "") or "")
                    != bridge_session_id
                    or str(getattr(refreshed, "omnigent_session_id", "") or "")
                    != provider_session_id
                ):
                    raise WorkflowChatFacadeError(
                        "Workflow Chat transcript authority is no longer valid.",
                        failure_class="user_error",
                        status_code=status.HTTP_403_FORBIDDEN,
                        code=CODE_OPERATION_DENIED,
                    )
            except (WorkflowChatFacadeError, OmnigentBridgeError, HTTPException):
                payload = {
                    "code": CODE_BINDING_UNKNOWN,
                    "message": "Workflow Chat binding authority is no longer valid.",
                }
                yield (
                    "event: error\n"
                    f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                )
                return
        page = await store.list_event_page(
            bridge_session_id, after=last_sequence, limit=_BRIDGE_STREAM_PAGE_SIZE
        )
        if (
            page.earliest_sequence is not None
            and last_sequence + 1 < page.earliest_sequence
        ):
            gap = BridgeRetentionGap(
                requested_after=last_sequence,
                earliest_available=page.earliest_sequence,
            )
            yield (
                "event: retention_gap\n"
                f"data: {gap.model_dump_json(by_alias=True)}\n\n"
            )
            return
        for row in page.rows:
            last_sequence = row.sequence
            idle_polls = 0
            if not _event_is_chat_visible(row):
                continue
            payload = _bridge_event_payload(row)
            # The browser only ever sees the opaque chatBindingId; map the
            # server-owned bridge-session identifiers to it before virtualizing
            # away any provider session id.
            for identity_key in ("bridgeSessionId", "sessionId", "session_id"):
                if identity_key in payload:
                    payload[identity_key] = chat_binding_id
            event_payload = json.dumps(
                _virtualize_facade_payload(
                    payload,
                    provider_session_id=provider_session_id,
                    chat_binding_id=chat_binding_id,
                ),
                separators=(",", ":"),
            )
            yield f"id: {row.sequence}\nevent: bridge_event\ndata: {event_payload}\n\n"
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
            yield (
                "event: terminal\n"
                f"data: {envelope.model_dump_json(by_alias=True)}\n\n"
            )
            return
        if page.has_more:
            continue
        idle_polls += 1
        yield ": keepalive\n\n"
        if idle_polls >= _BRIDGE_STREAM_MAX_IDLE_POLLS:
            return
        await asyncio.sleep(_BRIDGE_STREAM_POLL_SECONDS)


def _initial_stream_cursor(
    *, cursor: int | None, since: int | None, last_event_id: str | None
) -> int:
    header_cursor: int | None = None
    if last_event_id:
        try:
            header_cursor = int(last_event_id)
        except ValueError as exc:
            raise WorkflowChatFacadeError(
                "The Last-Event-ID cursor is invalid.",
                failure_class="user_error",
                status_code=status.HTTP_400_BAD_REQUEST,
                code=CODE_MALFORMED_PAYLOAD,
            ) from exc
    return max(
        (value for value in (cursor, header_cursor, since) if value is not None),
        default=0,
    )


async def workflow_chat_binding_facade(
    chat_binding_id: str,
    omnigent_path: str,
    request: Request,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store),
    proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
    registry: RetrievalCapabilityRegistry = Depends(get_capability_registry),
):
    """Single binding-scoped entrypoint for the native Omnigent Workflow Chat UI.

    Not a generic reverse proxy: every request is matched against an explicit
    method + route allowlist, reauthorized against the durable binding, checked
    for identity substitution, capability-gated from recomputed trusted state,
    and forwarded only to the server-resolved provider session with MoonMind
    credentials stripped and upstream credentials injected server-side.
    """

    try:
        return await _dispatch_workflow_chat_facade(
            chat_binding_id=chat_binding_id,
            omnigent_path=omnigent_path,
            request=request,
            config=config,
            user=user,
            service=service,
            store=store,
            proxy=proxy,
            embedded_facade=embedded_facade,
            registry=registry,
        )
    except (WorkflowChatFacadeError, OmnigentBridgeError) as exc:
        raise _http_error_from_bridge(exc) from exc


async def _read_bounded_facade_body(request: Request) -> bytes:
    """Read any reviewed native payload without allowing unbounded buffering."""

    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
            if declared_size < 0 or declared_size > _FACADE_MAX_BODY_BYTES:
                raise ValueError("too large")
        except ValueError as exc:
            raise WorkflowChatFacadeError(
                "The request body exceeds the Workflow Chat facade limit.",
                failure_class="user_error",
                status_code=413,
                code=CODE_PAYLOAD_TOO_LARGE,
            ) from exc
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > _FACADE_MAX_BODY_BYTES:
            raise WorkflowChatFacadeError(
                "The request body exceeds the Workflow Chat facade limit.",
                failure_class="user_error",
                status_code=413,
                code=CODE_PAYLOAD_TOO_LARGE,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_native_resource_path(value: str | None) -> None:
    """Reject encoded traversal, absolute, drive/UNC, and NUL-bearing paths."""

    from urllib.parse import unquote

    candidate = str(value or "")
    for _ in range(3):
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    normalized = candidate.replace("\\", "/")
    parts = normalized.split("/")
    if (
        "\x00" in normalized
        or normalized.startswith("/")
        or normalized.startswith("//")
        or (len(normalized) >= 2 and normalized[1] == ":")
        or any(part in {".", ".."} for part in parts)
    ):
        raise WorkflowChatFacadeError(
            "The requested resource path is not permitted.",
            failure_class="user_error",
            status_code=403,
            code=CODE_OPERATION_DENIED,
        )


def _validate_native_match_paths(match: Any) -> None:
    """Validate every caller-controlled captured or wildcard path segment."""

    for name, value in match.params.items():
        if name in {"session_id", "runner_id"}:
            continue
        _validate_native_resource_path(value)


def _identity_query_values(query_params: Any) -> list[dict[str, str]]:
    """Preserve duplicate query values while scanning server-owned identities."""

    return [{str(key): str(value)} for key, value in query_params.multi_items()]


def _rewrite_native_facade_location(
    location: str, *, chat_binding_id: str, provider_session_id: str
) -> str:
    """Keep an upstream native API redirect inside the binding-scoped facade."""

    import posixpath
    from urllib.parse import urlsplit

    base = (f"{WORKFLOW_CHAT_BINDINGS_MOUNT_PATH}/{chat_binding_id}/omnigent").rstrip(
        "/"
    )
    target = str(location or "").strip()
    if not target:
        return base + "/"
    split = urlsplit(target)
    suffix = (f"?{split.query}" if split.query else "") + (
        f"#{split.fragment}" if split.fragment else ""
    )
    path_segments = [
        chat_binding_id if segment == provider_session_id else segment
        for segment in split.path.split("/")
    ]
    scoped_path = "/".join(path_segments)
    if split.scheme or split.netloc or scoped_path.startswith("/"):
        combined = base + "/" + scoped_path.lstrip("/")
    else:
        combined = base + "/" + scoped_path
    normalized = posixpath.normpath(combined)
    if normalized != base and not normalized.startswith(base + "/"):
        return base + "/"
    return normalized + suffix


def _native_response_from_receipt(value: Any) -> Response | None:
    """Rebuild a bounded native mutation response from its durable receipt."""

    if not isinstance(value, Mapping):
        return None
    try:
        status_code = int(value.get("statusCode"))
    except (TypeError, ValueError):
        return None
    media_type = str(value.get("mediaType") or "application/json")
    headers = {
        str(key): str(item)
        for key, item in dict(value.get("headers") or {}).items()
        if key in {"Content-Disposition", "Location"}
    }
    json_value = value.get("json")
    content = (
        json.dumps(json_value, separators=(",", ":")).encode()
        if "json" in value
        else b""
    )
    return Response(
        content=content,
        status_code=status_code,
        media_type=media_type,
        headers=headers,
    )


async def _dispatch_native_ui_http(
    *,
    match: Any,
    row: Any,
    request: Request,
    chat_binding_id: str,
    user: Any,
    store: OmnigentBridgeSessionStore,
    config: OmnigentBridgeConfig,
):
    """Relay one explicitly inventoried HTTP operation through its binding."""

    route = match.route
    provider_session_id = str(getattr(row, "omnigent_session_id", "") or "").strip()
    session_status = str(getattr(row, "status", "") or "")
    body = b""
    parsed_body: Any | None = None
    if request.method.upper() in {"POST", "PUT", "PATCH"}:
        body = await _read_bounded_facade_body(request)
        content_type = str(request.headers.get("content-type") or "").lower()
        if content_type.startswith("application/json") and body:
            try:
                parsed_body = json.loads(body)
            except (json.JSONDecodeError, ValueError) as exc:
                raise WorkflowChatFacadeError(
                    "The request body is not valid JSON.",
                    failure_class="user_error",
                    status_code=400,
                    code=CODE_MALFORMED_PAYLOAD,
                ) from exc
    assert_no_identity_substitution(
        chat_binding_id=chat_binding_id,
        path_session_id=match.params.get("session_id"),
        query=_identity_query_values(request.query_params),
        body=parsed_body,
        headers=request.headers,
    )
    runner_alias = str(match.params.get("runner_id") or "")
    if runner_alias and runner_alias != chat_binding_id:
        raise WorkflowChatFacadeError(
            "The request attempts to supply a server-owned runner identity.",
            failure_class="user_error",
            status_code=403,
            code=CODE_OPERATION_DENIED,
        )
    _validate_native_match_paths(match)

    if config.host_protocol_mode != HOST_PROTOCOL_MODE_PROXY:
        raise WorkflowChatFacadeError(
            "The selected Omnigent host mode does not support this native route.",
            failure_class="user_error",
            status_code=status.HTTP_409_CONFLICT,
            code="omnigent_bridge_mode_unsupported",
        )

    capability_set = _effective_capabilities(row, user)
    capabilities = capability_set.capabilities
    if route.capability and not capabilities.get(route.capability, False):
        raise WorkflowChatFacadeError(
            "The requested operation is not permitted for this binding.",
            failure_class="user_error",
            status_code=403,
            code=CODE_OPERATION_DENIED,
        )
    if route.mutation and is_read_only(session_status):
        raise WorkflowChatFacadeError(
            "This Workflow Chat session is terminal and read-only.",
            failure_class="user_error",
            status_code=409,
            code=CODE_SESSION_READ_ONLY,
        )

    # Topology/liveness and reconnect are projections of the durable binding;
    # browser-provided host, runner, profile, or endpoint identity is never used.
    if route.name == "host_liveness":
        return {
            "hosts": [{"id": chat_binding_id, "state": _facade_reconnect_state(row)}]
        }
    if route.name == "runner_liveness":
        return {
            "chatBindingId": chat_binding_id,
            "state": _facade_reconnect_state(row),
            "readOnly": is_read_only(session_status),
        }
    if (
        route.name == "session_items"
        and is_read_only(session_status)
        and not provider_session_id
    ):
        return await _durable_terminal_items_page(
            row,
            chat_binding_id=chat_binding_id,
            query_params=request.query_params,
        )
    if not provider_session_id and route.name != "session_reconnect":
        raise WorkflowChatFacadeError(
            "The Workflow Chat binding has no active provider session yet.",
            failure_class="user_error",
            status_code=409,
            code=CODE_SESSION_NOT_READY,
        )

    fresh_row = row
    scan_evidence: NativeScanEvidence | None = None
    effective_key: str | None = None
    request_time: str | None = None
    request_preconditions: dict[str, Any] = {}
    if route.mutation:
        effective_key = _facade_idempotency_key(request) or f"mm-{uuid4().hex}"
        scan_payload: Any = (
            parsed_body if parsed_body is not None else body if body else {}
        )
        scan_evidence = _enforce_native_outbound_scan(
            surface=NativeScanSurface.NATIVE_MUTATION,
            body=scan_payload,
            idempotency_key=effective_key,
            chat_binding_id=chat_binding_id,
            user=user,
            operation=route.name,
        )
        request_preconditions = _mutation_preconditions(
            parsed_body if isinstance(parsed_body, Mapping) else None
        )
        fresh_row, fresh_provider, fresh_capabilities = (
            await _revalidate_binding_before_mutation(
                store=store,
                chat_binding_id=chat_binding_id,
                expected_provider_session_id=provider_session_id,
                expected_authority_digest=capability_set.authority_digest,
                user=user,
                **request_preconditions,
            )
        )
        if route.capability and not fresh_capabilities.get(route.capability, False):
            raise WorkflowChatFacadeError(
                "The requested operation is not permitted for this binding.",
                failure_class="user_error",
                status_code=403,
                code=CODE_OPERATION_DENIED,
            )
        if canonical_payload_digest(scan_payload) != scan_evidence.payload_digest:
            raise WorkflowChatFacadeError(
                "The security scan required for this request is unavailable.",
                failure_class="system_error",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code=CODE_ENFORCEMENT_UNAVAILABLE,
            )
        provider_session_id = fresh_provider
        request_time = datetime.now(tz=UTC).isoformat()
        claimed = await _claim_facade_message(
            store=store,
            row=fresh_row,
            event_type=route.name,
            actor=str(user.id),
            idempotency_key=effective_key,
            payload_digest=scan_evidence.payload_digest,
            scan_evidence=scan_evidence,
            request_time=request_time,
            request_preconditions=request_preconditions,
        )
        if not claimed:
            prior = await store.get_lifecycle_event_metadata(
                fresh_row.idempotency_key,
                event_identity=(
                    f"workflow-chat-control:{route.name}:posted:{effective_key}"
                ),
            )
            replay = _native_response_from_receipt(
                prior.get("normalizedResult") if isinstance(prior, dict) else None
            )
            if replay is not None:
                return replay
            raise WorkflowChatFacadeError(
                "The prior request is still pending or its delivery is unknown.",
                failure_class="system_error",
                status_code=status.HTTP_409_CONFLICT,
                code=CODE_SESSION_NOT_READY,
            )

    from urllib.parse import urlencode, urlsplit

    # Reconnect is a MoonMind-owned durable-state projection in the pinned
    # compatibility profile.  It still is a mutation: claim the idempotency key,
    # revalidate authority, and persist the same terminal receipt as an upstream
    # mutation before reporting success.  An early local return here previously
    # left this one stock native control without mutation evidence (#3632).
    if route.name == "session_reconnect":
        assert effective_key and scan_evidence and request_time
        dispatch_time = datetime.now(tz=UTC).isoformat()
        normalized = {
            "chatBindingId": chat_binding_id,
            "state": _facade_reconnect_state(fresh_row),
            "readOnly": is_read_only(str(getattr(fresh_row, "status", "") or "")),
        }
        encoded = json.dumps(normalized, separators=(",", ":")).encode()
        receipt_result = {
            "statusCode": status.HTTP_200_OK,
            "mediaType": "application/json",
            "headers": {},
            "bodyDigest": sha256(encoded).hexdigest(),
            "json": normalized,
        }
        await _record_facade_mutation_audit(
            store=store,
            row=fresh_row,
            control_type=route.name,
            outcome="posted",
            actor=str(user.id),
            idempotency_key=effective_key,
            scan_evidence=scan_evidence,
            upstream_result=receipt_result,
            request_time=request_time,
            dispatch_time=dispatch_time,
            request_preconditions=request_preconditions,
        )
        _audit_facade(
            outcome="mutation",
            operation=route.name,
            chat_binding_id=chat_binding_id,
            user=user,
            reason="binding_scoped_native_local",
        )
        return Response(
            content=encoded,
            status_code=status.HTTP_200_OK,
            media_type="application/json",
        )

    upstream_path = upstream_http_path(match, provider_session_id)
    query = urlencode(list(request.query_params.multi_items()))
    base = urlsplit(resolved_server_url())
    upstream_url = f"{base.scheme}://{base.netloc}{upstream_path}"
    if query:
        upstream_url += "?" + query
    headers: dict[str, str] = {"Accept": str(request.headers.get("accept") or "*/*")}
    content_type = request.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = str(content_type)
    token = resolved_api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    idempotency_key = request.headers.get("idempotency-key")
    if idempotency_key:
        headers["Idempotency-Key"] = str(idempotency_key)[:256]

    dispatch_time = datetime.now(tz=UTC).isoformat() if route.mutation else None
    receipt_result: dict[str, Any] | None = None
    try:
        async with aiohttp.ClientSession(headers=headers) as client:
            async with client.request(
                request.method.upper(),
                upstream_url,
                data=body or None,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as upstream:
                payload = await upstream.content.read(_FACADE_MAX_BODY_BYTES + 1)
                if len(payload) > _FACADE_MAX_BODY_BYTES:
                    raise WorkflowChatFacadeError(
                        "The upstream response exceeds the facade limit.",
                        failure_class="integration_error",
                        status_code=502,
                        code=CODE_PAYLOAD_TOO_LARGE,
                    )
                response_type = str(
                    upstream.headers.get("content-type") or "application/octet-stream"
                )
                normalized_json: Any = None
                if "json" in response_type.lower() and payload:
                    try:
                        value = json.loads(payload)
                    except (json.JSONDecodeError, ValueError):
                        value = None
                    if value is not None:
                        value = _virtualize_facade_payload(
                            value,
                            provider_session_id=provider_session_id,
                            chat_binding_id=chat_binding_id,
                        )
                        normalized_json = value
                        payload = json.dumps(value, separators=(",", ":")).encode()
                response_headers: dict[str, str] = {}
                disposition = str(upstream.headers.get("content-disposition") or "")
                if disposition and "\r" not in disposition and "\n" not in disposition:
                    response_headers["Content-Disposition"] = disposition[:512]
                location = str(upstream.headers.get("location") or "")
                if location and "\r" not in location and "\n" not in location:
                    response_headers["Location"] = _rewrite_native_facade_location(
                        location,
                        chat_binding_id=chat_binding_id,
                        provider_session_id=provider_session_id,
                    )
                result = Response(
                    content=payload,
                    status_code=upstream.status,
                    media_type=response_type.split(";", 1)[0],
                    headers=response_headers,
                )
                receipt_result = {
                    "statusCode": upstream.status,
                    "mediaType": response_type.split(";", 1)[0],
                    "headers": response_headers,
                    "bodyDigest": sha256(payload).hexdigest(),
                }
                if normalized_json is not None:
                    receipt_result["json"] = normalized_json
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        if route.mutation:
            assert effective_key and scan_evidence and request_time
            await _record_facade_mutation_audit(
                store=store,
                row=fresh_row,
                control_type=route.name,
                outcome="delivery_unknown",
                actor=str(user.id),
                idempotency_key=effective_key,
                scan_evidence=scan_evidence,
                request_time=request_time,
                dispatch_time=dispatch_time,
                request_preconditions=request_preconditions,
            )
        raise WorkflowChatFacadeError(
            "The upstream Omnigent route is unavailable.",
            failure_class="integration_error",
            status_code=502,
            code="omnigent_chat_upstream_unavailable",
        ) from exc
    except WorkflowChatFacadeError:
        if route.mutation:
            assert effective_key and scan_evidence and request_time
            await _record_facade_mutation_audit(
                store=store,
                row=fresh_row,
                control_type=route.name,
                outcome="delivery_unknown",
                actor=str(user.id),
                idempotency_key=effective_key,
                scan_evidence=scan_evidence,
                request_time=request_time,
                dispatch_time=dispatch_time,
                request_preconditions=request_preconditions,
            )
        raise
    except Exception:
        if route.mutation:
            assert effective_key and scan_evidence and request_time
            await _record_facade_mutation_audit(
                store=store,
                row=fresh_row,
                control_type=route.name,
                outcome="delivery_unknown",
                actor=str(user.id),
                idempotency_key=effective_key,
                scan_evidence=scan_evidence,
                request_time=request_time,
                dispatch_time=dispatch_time,
                request_preconditions=request_preconditions,
            )
        raise

    if route.mutation:
        assert effective_key and scan_evidence and request_time and receipt_result
        await _record_facade_mutation_audit(
            store=store,
            row=fresh_row,
            control_type=route.name,
            outcome="posted",
            actor=str(user.id),
            idempotency_key=effective_key,
            scan_evidence=scan_evidence,
            upstream_result=receipt_result,
            request_time=request_time,
            dispatch_time=dispatch_time,
            request_preconditions=request_preconditions,
        )
        _audit_facade(
            outcome="mutation",
            operation=route.name,
            chat_binding_id=chat_binding_id,
            user=user,
            reason="binding_scoped_native_http",
        )
    return result


# Register the single binding-scoped facade handler once per HTTP method with an
# explicit, method-suffixed operation id. A single ``api_route(methods=[...])``
# derives its OpenAPI operation id from ``route.methods`` (a set), so the
# exported operation id — and the frontend types generated from it — would flip
# between ``_get`` and ``_post`` across processes with different hash seeds,
# making the generated-contract check non-deterministic. One route per method
# keeps the exported contract stable (matching the proxy router idiom).
for _facade_method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
    workflow_chat_router.add_api_route(
        "/{chat_binding_id}/omnigent/{omnigent_path:path}",
        workflow_chat_binding_facade,
        methods=[_facade_method],
        operation_id=f"workflow_chat_binding_facade_{_facade_method.lower()}",
        responses=_PUBLIC_ERROR_RESPONSES,
    )


async def _dispatch_workflow_chat_facade(
    *,
    chat_binding_id: str,
    omnigent_path: str,
    request: Request,
    config: OmnigentBridgeConfig,
    user: User,
    service: Any,
    store: OmnigentBridgeSessionStore,
    proxy: OmnigentBridgeSessionProxy | None,
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None,
    registry: RetrievalCapabilityRegistry,
):
    # 1. Resolve + authorize the durable binding first, so an unauthorized
    #    caller cannot even probe which routes exist (fully non-enumerating).
    match = match_facade_operation(request.method, omnigent_path)
    native_match = (
        None if match else classify_native_ui_http(request.method, omnigent_path)
    )
    operation_name = (
        match.operation.name
        if match
        else native_match.route.name if native_match else "unknown"
    )
    row = await _resolve_and_authorize_chat_binding(
        chat_binding_id=chat_binding_id,
        operation=operation_name,
        user=user,
        service=service,
        store=store,
    )

    # 2. Enforce the explicit method + route allowlist.
    if match is None and native_match is None:
        _audit_facade(
            outcome="denied",
            operation="unknown",
            chat_binding_id=chat_binding_id,
            user=user,
            reason="route_not_allowlisted",
        )
        raise WorkflowChatFacadeError(
            "The requested route is not available on this binding.",
            failure_class="user_error",
            status_code=status.HTTP_404_NOT_FOUND,
            code=CODE_ROUTE_NOT_ALLOWLISTED,
        )
    if native_match is not None:
        return await _dispatch_native_ui_http(
            match=native_match,
            row=row,
            request=request,
            chat_binding_id=chat_binding_id,
            user=user,
            store=store,
            config=config,
        )
    assert match is not None
    operation = match.operation
    params = match.params
    # Journal reads use the resolved durable bridge-session key; the browser only
    # ever names the opaque ``chat_binding_id`` (they are the same value today but
    # diverge once #3633 adds a dedicated binding column — see module note).
    bridge_session_id = str(getattr(row, "bridge_session_id", "") or "").strip()
    provider_session_id = str(getattr(row, "omnigent_session_id", "") or "").strip()
    session_status = str(getattr(row, "status", "") or "")
    # Capabilities are recomputed from trusted status *and* intersected with the
    # binding's stored policy so a disabled operation is never re-advertised or
    # re-authorized from status alone.
    capability_set = _effective_capabilities(row, user)
    capabilities = capability_set.capabilities

    # 3. Read a bounded JSON body for mutations before the substitution guard.
    body: dict[str, Any] | None = None
    if request.method.upper() == "POST":
        body = await _read_facade_json_body(request)

    # 4. Reject any attempt to substitute a server-owned identity in the path,
    #    query, body, or headers (session id may only echo the bound id).
    try:
        assert_no_identity_substitution(
            chat_binding_id=chat_binding_id,
            path_session_id=params.get("session_id"),
            query=_identity_query_values(request.query_params),
            body=body,
            headers=request.headers,
        )
    except WorkflowChatFacadeError:
        _audit_facade(
            outcome="denied",
            operation=operation.name,
            chat_binding_id=chat_binding_id,
            user=user,
            reason="identity_substitution",
        )
        raise

    # 5. Liveness probe is served locally and never forwarded upstream.
    if operation.name == "liveness":
        return {
            "chatBindingId": chat_binding_id,
            "state": _facade_liveness_state(row),
            "readOnly": is_read_only(session_status),
            "capabilities": capabilities,
            "disabledReasons": capability_set.disabled_reasons,
            "capabilitySchemaVersion": capability_set.schema_version,
            "capabilityAuthorityDigest": capability_set.authority_digest,
        }

    # Stock Omnigent boot probes are projected locally. The native presentation
    # client needs these shapes to start, but it must not inherit the upstream
    # server's global account/session authority through a workflow binding.
    if operation.name == "native_server_info":
        return {
            "accounts_enabled": False,
            "single_user": False,
            "login_url": None,
            "needs_setup": False,
            "databricks_features": False,
            "managed_sandboxes_enabled": False,
            "sandbox_provider": None,
            "sharing_mode": "off",
            "public_sharing_enabled": False,
            "server_version": None,
            "smart_routing_enabled": False,
            "smart_routing_sources": [],
            "harness_install_enabled": False,
            "installable_harnesses": [],
            "dictation_available": False,
        }
    if operation.name == "native_current_user":
        return {"user_id": "workflow-owner", "is_admin": False}
    if operation.name == "list_projects":
        return []

    # 6. Recompute-driven capability + session-state gates.
    #    A terminal binding deliberately survives provider cleanup with durable
    #    final snapshots and event journals, so its read-only transcript bootstrap
    #    (``get_session``) is served from the durable projection rather than
    #    requiring a live upstream session id. Every other provider-session-backed
    #    operation still fails closed until a session is attached.
    serve_durable_terminal_snapshot = (
        operation.name in {"get_session", "list_sessions"}
        and is_read_only(session_status)
        and not provider_session_id
    )
    if (
        operation.requires_provider_session
        and not provider_session_id
        and not serve_durable_terminal_snapshot
    ):
        raise WorkflowChatFacadeError(
            "The Workflow Chat binding has no active provider session yet.",
            failure_class="user_error",
            status_code=status.HTTP_409_CONFLICT,
            code=CODE_SESSION_NOT_READY,
        )
    if operation.capability and not capabilities.get(operation.capability, False):
        _audit_facade(
            outcome="denied",
            operation=operation.name,
            chat_binding_id=chat_binding_id,
            user=user,
            reason="capability_denied",
        )
        raise WorkflowChatFacadeError(
            "The requested operation is not permitted for this binding.",
            failure_class="user_error",
            status_code=status.HTTP_403_FORBIDDEN,
            code=CODE_OPERATION_DENIED,
        )

    facade = (
        embedded_facade
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
        else proxy
    )
    if operation.name != "stream_events" and facade is None:
        raise WorkflowChatFacadeError(
            "The Omnigent bridge host protocol mode does not support this route.",
            failure_class="system_error",
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            code="omnigent_bridge_mode_unsupported",
        )

    # 7. Live/replay stream from the durable journal (cursor + reauth semantics).
    if operation.name == "stream_events":
        initial_cursor = _initial_stream_cursor(
            cursor=_strict_query_cursor(request.query_params.get("cursor"), "cursor"),
            since=_strict_query_cursor(request.query_params.get("since"), "since"),
            last_event_id=request.headers.get("Last-Event-ID"),
        )
        return StreamingResponse(
            _stream_workflow_chat_events(
                request=request,
                chat_binding_id=chat_binding_id,
                bridge_session_id=bridge_session_id,
                provider_session_id=provider_session_id,
                initial_cursor=initial_cursor,
                user=user,
                service=service,
                store=store,
            ),
            media_type="text/event-stream",
        )

    # 8. Session metadata / catalog.
    if operation.name == "list_agents":
        agents = await facade.list_agents()
        return _virtualize_facade_payload(
            agents,
            provider_session_id=provider_session_id,
            chat_binding_id=chat_binding_id,
        )

    if operation.name == "list_sessions":
        snapshot = (
            _durable_terminal_snapshot(
                row,
                chat_binding_id=chat_binding_id,
                capabilities=capabilities,
            )
            if serve_durable_terminal_snapshot
            else await facade.get_session(provider_session_id)
        )
        virtualized = _virtualize_facade_payload(
            snapshot,
            provider_session_id=provider_session_id,
            chat_binding_id=chat_binding_id,
        )
        if not isinstance(virtualized, dict):
            raise WorkflowChatFacadeError(
                "The bound session catalog has an unsupported response shape.",
                failure_class="integration_error",
                status_code=status.HTTP_502_BAD_GATEWAY,
                code="omnigent_bridge_upstream_payload",
            )
        virtualized["capabilities"] = capabilities
        virtualized["readOnly"] = is_read_only(session_status)
        return {
            "object": "list",
            "data": [virtualized],
            "first_id": chat_binding_id,
            "last_id": chat_binding_id,
            "has_more": False,
        }

    # 9. Session snapshot / bootstrap / history.
    if operation.name == "get_session":
        if serve_durable_terminal_snapshot:
            # Provider session was deleted after harvest; bootstrap the read-only
            # transcript from the durable projection instead of a live upstream id.
            return _durable_terminal_snapshot(
                row,
                chat_binding_id=chat_binding_id,
                capabilities=capabilities,
            )
        snapshot = await facade.get_session(provider_session_id)
        virtualized = _virtualize_facade_payload(
            snapshot,
            provider_session_id=provider_session_id,
            chat_binding_id=chat_binding_id,
        )
        if isinstance(virtualized, dict):
            virtualized["capabilities"] = capabilities
            virtualized["readOnly"] = is_read_only(session_status)
        return virtualized

    # 10. Message / control events — routed through the shared control path.
    if operation.name == "post_event":
        request_preconditions = _mutation_preconditions(body)
        try:
            event = BridgeSessionEventRequest.model_validate(body or {})
        except ValidationError as exc:
            raise WorkflowChatFacadeError(
                "The control event body is not a supported shape.",
                failure_class="user_error",
                status_code=status.HTTP_400_BAD_REQUEST,
                code=CODE_MALFORMED_PAYLOAD,
            ) from exc
        required = required_capability_for_event(event.type)
        allow_terminal_mutation = required in {"harvestEvidence", "cleanupSession"}
        if is_read_only(session_status) and not allow_terminal_mutation:
            raise WorkflowChatFacadeError(
                "This Workflow Chat session is terminal and read-only.",
                failure_class="user_error",
                status_code=status.HTTP_409_CONFLICT,
                code=CODE_SESSION_READ_ONLY,
            )
        if not capabilities.get(required, False):
            _audit_facade(
                outcome="denied",
                operation="post_event",
                chat_binding_id=chat_binding_id,
                user=user,
                reason="capability_denied",
            )
            raise WorkflowChatFacadeError(
                "The requested control is not permitted for this binding.",
                failure_class="user_error",
                status_code=status.HTTP_403_FORBIDDEN,
                code=CODE_OPERATION_DENIED,
            )

        # Idempotent submission: a caller-supplied ``Idempotency-Key`` makes a
        # browser retry or double-submit resolve to exactly one provider turn.
        # Resolved before the scan so the scan evidence binds the decision to
        # both the canonical payload digest and the idempotency key.
        client_key = _facade_idempotency_key(request)
        effective_key = client_key or f"mm-{uuid4().hex}"

        # Fail-closed outbound secret scan before any provider forward. The
        # digest-bound evidence proves which exact payload was scanned, so a
        # retry, queued flush, steer, reconnect replay, or reconciliation cannot
        # reuse an allow result after the content changes.
        scan_evidence = _enforce_native_outbound_scan(
            surface=NativeScanSurface.MESSAGE,
            body=body,
            idempotency_key=effective_key,
            chat_binding_id=chat_binding_id,
            user=user,
            operation="post_event",
        )

        # Revalidate the binding/session state at the mutation handoff (CAS):
        # the row was loaded before the body was buffered and scanned.
        fresh_row, fresh_provider, fresh_capabilities = (
            await _revalidate_binding_before_mutation(
                store=store,
                chat_binding_id=chat_binding_id,
                expected_provider_session_id=provider_session_id,
                expected_authority_digest=capability_set.authority_digest,
                user=user,
                allow_terminal_mutation=allow_terminal_mutation,
                **request_preconditions,
            )
        )
        if not fresh_capabilities.get(required, False):
            _audit_facade(
                outcome="denied",
                operation="post_event",
                chat_binding_id=chat_binding_id,
                user=user,
                reason="capability_revoked",
            )
            raise WorkflowChatFacadeError(
                "The requested control is not permitted for this binding.",
                failure_class="user_error",
                status_code=status.HTTP_403_FORBIDDEN,
                code=CODE_OPERATION_DENIED,
            )

        # The provider forward must carry the exact payload that was scanned. A
        # digest mismatch here means the payload changed after the scan (an
        # unscanned-forward attempt) and fails closed rather than forwarding.
        if canonical_payload_digest(body or {}) != scan_evidence.payload_digest:
            raise WorkflowChatFacadeError(
                "The security scan required for this request is unavailable.",
                failure_class="system_error",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code=CODE_ENFORCEMENT_UNAVAILABLE,
            )

        request_time = datetime.now(tz=UTC).isoformat()
        claimed = await _claim_facade_message(
            store=store,
            row=fresh_row,
            event_type=event.type,
            actor=str(user.id),
            idempotency_key=effective_key,
            payload_digest=scan_evidence.payload_digest,
            scan_evidence=scan_evidence,
            request_time=request_time,
            request_preconditions=request_preconditions,
        )
        if not claimed:
            # Replay of a previously accepted key: reconcile without re-posting.
            _audit_facade(
                outcome="deduplicated",
                operation=f"post_event:{event.type}",
                chat_binding_id=chat_binding_id,
                user=user,
            )
            prior = await store.get_lifecycle_event_metadata(
                fresh_row.idempotency_key,
                event_identity=(
                    f"workflow-chat-control:{event.type}:posted:{effective_key}"
                ),
            )
            if isinstance(prior, dict) and "normalizedResult" in prior:
                return prior["normalizedResult"]
            reconciled = await _reconcile_facade_mutation(
                store=store,
                row=fresh_row,
                facade=facade,
                control_type=event.type,
                idempotency_key=effective_key,
                provider_session_id=fresh_provider,
                chat_binding_id=chat_binding_id,
                actor=str(user.id),
                scan_evidence=scan_evidence,
                request_preconditions=request_preconditions,
            )
            if reconciled is not None:
                return reconciled
            raise WorkflowChatFacadeError(
                "The prior request is still pending or its delivery is unknown.",
                failure_class="system_error",
                status_code=status.HTTP_409_CONFLICT,
                code=CODE_SESSION_NOT_READY,
            )
        dispatch_time = datetime.now(tz=UTC).isoformat()

        try:
            result = await _apply_owned_session_control(
                control_facade=facade,
                session_id=fresh_provider,
                payload=event,
                config=config,
                actor=str(user.id),
                proxy=proxy,
                embedded_facade=embedded_facade,
                registry=registry,
                store=store,
            )
        except Exception:
            # The provider may have accepted the request before the transport
            # failed. Persist the ambiguity and leave the claim occupied so a
            # retry reconciles instead of repeating the side effect.
            await _record_facade_mutation_audit(
                store=store,
                row=fresh_row,
                control_type=event.type,
                outcome="delivery_unknown",
                actor=str(user.id),
                idempotency_key=effective_key,
                scan_evidence=scan_evidence,
                request_time=request_time,
                dispatch_time=dispatch_time,
                request_preconditions=request_preconditions,
            )
            raise
        await _record_facade_mutation_audit(
            store=store,
            row=fresh_row,
            control_type=event.type,
            outcome="posted",
            actor=str(user.id),
            idempotency_key=effective_key,
            scan_evidence=scan_evidence,
            upstream_result=result,
            request_time=request_time,
            dispatch_time=dispatch_time,
            request_preconditions=request_preconditions,
        )
        _audit_facade(
            outcome="mutation",
            operation=f"post_event:{event.type}",
            chat_binding_id=chat_binding_id,
            user=user,
        )
        return _virtualize_facade_payload(
            result,
            provider_session_id=fresh_provider,
            chat_binding_id=chat_binding_id,
        )

    # 11. Elicitation / approval resolution.
    if operation.name == "resolve_elicitation":
        request_preconditions = _mutation_preconditions(body)
        if is_read_only(session_status):
            raise WorkflowChatFacadeError(
                "This Workflow Chat session is terminal and read-only.",
                failure_class="user_error",
                status_code=status.HTTP_409_CONFLICT,
                code=CODE_SESSION_READ_ONLY,
            )
        # Approval resolution requires its own capability (intersected with the
        # binding policy), not merely a non-terminal session. A workflow owner
        # whose effective policy denies approval must not resolve elicitations.
        if not capabilities.get(CAP_RESOLVE_ELICITATION, False):
            _audit_facade(
                outcome="denied",
                operation="resolve_elicitation",
                chat_binding_id=chat_binding_id,
                user=user,
                reason="capability_denied",
            )
            raise WorkflowChatFacadeError(
                "Approval resolution is not permitted for this binding.",
                failure_class="user_error",
                status_code=status.HTTP_403_FORBIDDEN,
                code=CODE_OPERATION_DENIED,
            )

        # Fail-closed outbound secret scan of the operator-authored approval
        # response text before any provider forward, on the same contract as the
        # message path (brief §1: elicitation/approval response text is a
        # supported text-bearing native surface).
        elicitation_scan_evidence = _enforce_native_outbound_scan(
            surface=NativeScanSurface.ELICITATION_RESPONSE,
            body=body,
            idempotency_key=_facade_idempotency_key(request),
            chat_binding_id=chat_binding_id,
            user=user,
            operation="resolve_elicitation",
        )

        # Revalidate the binding/session state at the mutation handoff so a
        # revoked policy or replaced session cannot be resolved with stale
        # authority (stale/replayed resolutions are also rejected upstream by
        # the provider's pending-elicitation check).
        fresh_row, fresh_provider, fresh_capabilities = (
            await _revalidate_binding_before_mutation(
                store=store,
                chat_binding_id=chat_binding_id,
                expected_provider_session_id=provider_session_id,
                expected_authority_digest=capability_set.authority_digest,
                user=user,
                **request_preconditions,
            )
        )
        if not fresh_capabilities.get(CAP_RESOLVE_ELICITATION, False):
            _audit_facade(
                outcome="denied",
                operation="resolve_elicitation",
                chat_binding_id=chat_binding_id,
                user=user,
                reason="capability_revoked",
            )
            raise WorkflowChatFacadeError(
                "Approval resolution is not permitted for this binding.",
                failure_class="user_error",
                status_code=status.HTTP_403_FORBIDDEN,
                code=CODE_OPERATION_DENIED,
            )
        actor_kwargs = (
            {"actor": str(user.id)}
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
            else {}
        )
        elicitation_key = _facade_idempotency_key(request) or f"mm-{uuid4().hex}"
        request_time = datetime.now(tz=UTC).isoformat()
        claimed = await _claim_facade_message(
            store=store,
            row=fresh_row,
            event_type="resolve_elicitation",
            turn_source=TurnSource.APPROVAL_RESPONSE,
            actor=str(user.id),
            idempotency_key=elicitation_key,
            payload_digest=elicitation_scan_evidence.payload_digest,
            scan_evidence=elicitation_scan_evidence,
            request_time=request_time,
            request_preconditions=request_preconditions,
        )
        if not claimed:
            prior = await store.get_lifecycle_event_metadata(
                fresh_row.idempotency_key,
                event_identity=(
                    "workflow-chat-control:resolve_elicitation:posted:"
                    f"{elicitation_key}"
                ),
            )
            if isinstance(prior, dict) and "normalizedResult" in prior:
                return prior["normalizedResult"]
            raise WorkflowChatFacadeError(
                "The prior request is still pending or its delivery is unknown.",
                failure_class="system_error",
                status_code=status.HTTP_409_CONFLICT,
                code=CODE_SESSION_NOT_READY,
            )
        dispatch_time = datetime.now(tz=UTC).isoformat()
        try:
            result = await facade.resolve_elicitation(
                session_id=fresh_provider,
                elicitation_id=params["elicitation_id"],
                payload=body or {},
                **actor_kwargs,
            )
        except Exception:
            await _record_facade_mutation_audit(
                store=store,
                row=fresh_row,
                control_type="resolve_elicitation",
                outcome="delivery_unknown",
                actor=str(user.id),
                idempotency_key=elicitation_key,
                scan_evidence=elicitation_scan_evidence,
                request_time=request_time,
                dispatch_time=dispatch_time,
                request_preconditions=request_preconditions,
            )
            raise
        await _record_facade_mutation_audit(
            store=store,
            row=fresh_row,
            control_type="resolve_elicitation",
            outcome="posted",
            actor=str(user.id),
            idempotency_key=elicitation_key,
            scan_evidence=elicitation_scan_evidence,
            upstream_result=result,
            request_time=request_time,
            dispatch_time=dispatch_time,
            request_preconditions=request_preconditions,
        )
        _audit_facade(
            outcome="mutation",
            operation="resolve_elicitation",
            chat_binding_id=chat_binding_id,
            user=user,
        )
        return _virtualize_facade_payload(
            result,
            provider_session_id=fresh_provider,
            chat_binding_id=chat_binding_id,
        )

    # 12. Read-only resource indexes and content.
    resource_value = params.get("res_path") or params.get("file_id")
    result = await facade.get_resource(
        operation.name, provider_session_id, resource_value
    )
    if operation.binary and isinstance(result, bytes):
        media_type = (
            "text/x-diff"
            if operation.name == "workspace_diff"
            else "application/octet-stream"
        )
        return Response(content=result, media_type=media_type)
    return _virtualize_facade_payload(
        result,
        provider_session_id=provider_session_id,
        chat_binding_id=chat_binding_id,
    )


def _strict_query_cursor(value: Any, field: str) -> int | None:
    """Parse a stream ``cursor``/``since`` query value, or fail closed.

    A missing value returns ``None``. Unlike a silently-coerced cursor, a
    nonnumeric or negative value is rejected with the stable malformed-cursor
    error rather than resuming the stream from zero and replaying the entire
    retained journal, which would duplicate already-rendered transcript events.
    """

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError as exc:
        raise WorkflowChatFacadeError(
            f"The {field} cursor is invalid.",
            failure_class="user_error",
            status_code=status.HTTP_400_BAD_REQUEST,
            code=CODE_MALFORMED_PAYLOAD,
        ) from exc
    if parsed < 0:
        raise WorkflowChatFacadeError(
            f"The {field} cursor is invalid.",
            failure_class="user_error",
            status_code=status.HTTP_400_BAD_REQUEST,
            code=CODE_MALFORMED_PAYLOAD,
        )
    return parsed


# ---------------------------------------------------------------------------
# Binding-scoped Workflow Chat WebSocket facade (MoonLadderStudios/MoonMind#3635)
#
# The native Omnigent UI also drives live surfaces over WebSocket: session
# control/event channels, terminal/PTY streams, execution logs, browser panes,
# sub-agent/task trees, and reconnect/wake flows. Leaving any of them pointed at
# the upstream server would bypass the scoped HTTP facade, so every WebSocket
# connection *and every reconnect* independently authenticates the caller,
# resolves the durable binding, authorizes against the bound Workflow Execution,
# rejects caller-supplied identity, recomputes capabilities from trusted state,
# validates the requested transport against the versioned native-UI
# compatibility map, and negotiates only an allowlisted subprotocol — all
# *before* the socket is accepted. A successful earlier HTTP bootstrap never
# authorizes a later WebSocket (this handler reauthorizes from scratch).
#
# The pinned compatibility profile supplies the exact reviewed upstream paths.
# Unknown or changed transports remain absent from that map and fail closed;
# reviewed transports are identity-virtualized and relayed with bounded,
# renewable authority.
# ---------------------------------------------------------------------------

# Application-defined WebSocket close codes (4000-4999 private range), mirroring
# the host/runner tunnel convention above. The close ``reason`` carries the
# stable, non-enumerating diagnostic code.
WS_CLOSE_BINDING_UNKNOWN = 4404
WS_CLOSE_CAPABILITY_DENIED = 4403
WS_CLOSE_IDENTITY_SUBSTITUTION = 4403
WS_CLOSE_READ_ONLY = 4410
WS_CLOSE_SESSION_NOT_READY = 4409
WS_CLOSE_SUBPROTOCOL_REJECTED = 4406
WS_CLOSE_TRANSPORT_UNSUPPORTED = 4400
WS_CLOSE_COMPAT_REVIEW = 4451
WS_CLOSE_UPSTREAM_UNAVAILABLE = 1013
WS_CLOSE_LIMIT_EXCEEDED = 1009

_NATIVE_WS_MAX_MESSAGE_BYTES = 1 * 1024 * 1024
_NATIVE_WS_IDLE_SECONDS = 60.0
_NATIVE_WS_LIFETIME_SECONDS = 60.0 * 60.0
_NATIVE_WS_REAUTH_SECONDS = 2.0
_NATIVE_WS_MESSAGES_PER_MINUTE = 240


def _websocket_origin_allowed(websocket: WebSocket) -> bool:
    """Apply a same-origin check without trusting forwarded topology headers."""

    origin = str(websocket.headers.get("origin") or "").strip()
    if not origin:  # non-browser clients authenticate through the normal dependency
        return True
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(origin)
    except ValueError:
        return False
    host = str(websocket.headers.get("host") or "").strip().lower()
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host


def _upstream_ws_url(path: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    configured = urlsplit(resolved_server_url())
    scheme = "wss" if configured.scheme == "https" else "ws"
    return urlunsplit((scheme, configured.netloc, path, "", ""))


async def _relay_native_websocket(
    *,
    browser: WebSocket,
    upstream_url: str,
    subprotocol: str | None,
    still_authorized: Any,
    browser_frame_filter: Any | None = None,
    upstream_frame_filter: Any | None = None,
    browser_frame_guard: Any | None = None,
    browser_frame_audit: Any | None = None,
) -> None:
    """Relay one reviewed native transport with bounded, renewable authority."""

    headers: dict[str, str] = {}
    token = resolved_api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    started = time.monotonic()
    recent_messages: list[float] = []
    try:
        async with aiohttp.ClientSession(headers=headers) as client:
            async with client.ws_connect(
                upstream_url,
                protocols=([subprotocol] if subprotocol else ()),
                max_msg_size=_NATIVE_WS_MAX_MESSAGE_BYTES,
                heartbeat=_NATIVE_WS_IDLE_SECONDS / 2,
                receive_timeout=None,
            ) as upstream:
                await browser.accept(subprotocol=subprotocol)

                async def browser_to_upstream() -> None:
                    while True:
                        # Server-to-browser sockets can be healthy indefinitely
                        # without browser input. Lifetime and authority monitors,
                        # plus the upstream heartbeat, own bounded termination.
                        message = await browser.receive()
                        kind = message.get("type")
                        if kind == "websocket.disconnect":
                            await upstream.close()
                            return
                        now = time.monotonic()
                        recent_messages[:] = [
                            t for t in recent_messages if now - t < 60
                        ]
                        if len(recent_messages) >= _NATIVE_WS_MESSAGES_PER_MINUTE:
                            await browser.close(
                                code=WS_CLOSE_LIMIT_EXCEEDED,
                                reason="omnigent_chat_ws_rate_limited",
                            )
                            return
                        recent_messages.append(now)
                        if message.get("text") is not None:
                            payload = str(message["text"])
                            if (
                                len(payload.encode("utf-8"))
                                > _NATIVE_WS_MAX_MESSAGE_BYTES
                            ):
                                raise ValueError("message too large")
                            if browser_frame_filter is not None:
                                payload = browser_frame_filter(payload)
                                if payload is None:
                                    continue
                            receipt = None
                            if browser_frame_guard is not None:
                                payload, receipt = await browser_frame_guard(
                                    payload, False
                                )
                            try:
                                await upstream.send_str(payload)
                            except Exception:
                                if (
                                    browser_frame_audit is not None
                                    and receipt is not None
                                ):
                                    await browser_frame_audit(
                                        receipt, "delivery_unknown"
                                    )
                                raise
                            if browser_frame_audit is not None and receipt is not None:
                                await browser_frame_audit(receipt, "posted")
                        elif message.get("bytes") is not None:
                            payload = bytes(message["bytes"])
                            if len(payload) > _NATIVE_WS_MAX_MESSAGE_BYTES:
                                raise ValueError("message too large")
                            receipt = None
                            if browser_frame_guard is not None:
                                payload, receipt = await browser_frame_guard(
                                    payload, True
                                )
                            try:
                                await upstream.send_bytes(payload)
                            except Exception:
                                if (
                                    browser_frame_audit is not None
                                    and receipt is not None
                                ):
                                    await browser_frame_audit(
                                        receipt, "delivery_unknown"
                                    )
                                raise
                            if browser_frame_audit is not None and receipt is not None:
                                await browser_frame_audit(receipt, "posted")

                async def upstream_to_browser() -> None:
                    async for message in upstream:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            payload = message.data
                            if upstream_frame_filter is not None:
                                payload = upstream_frame_filter(payload)
                                if payload is None:
                                    continue
                            await browser.send_text(payload)
                        elif message.type == aiohttp.WSMsgType.BINARY:
                            await browser.send_bytes(message.data)
                        elif message.type in {
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        }:
                            return

                async def authority_monitor() -> None:
                    while True:
                        await asyncio.sleep(_NATIVE_WS_REAUTH_SECONDS)
                        if time.monotonic() - started >= _NATIVE_WS_LIFETIME_SECONDS:
                            await browser.close(
                                code=1000, reason="omnigent_chat_ws_lifetime"
                            )
                            return
                        if not await still_authorized():
                            await browser.close(
                                code=WS_CLOSE_CAPABILITY_DENIED,
                                reason=CODE_OPERATION_DENIED,
                            )
                            await upstream.close()
                            return

                tasks = [
                    asyncio.create_task(browser_to_upstream()),
                    asyncio.create_task(upstream_to_browser()),
                    asyncio.create_task(authority_monitor()),
                ]
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    task.result()
                if not upstream.closed:
                    await upstream.close()
                try:
                    await browser.close(code=1000)
                except RuntimeError:
                    # Another relay task may already have closed the browser;
                    # closing a WebSocket is intentionally idempotent here.
                    return
    except (aiohttp.ClientError, asyncio.TimeoutError):
        await browser.close(
            code=WS_CLOSE_UPSTREAM_UNAVAILABLE,
            reason="omnigent_chat_upstream_unavailable",
        )
    except PermissionError:
        await browser.close(
            code=WS_CLOSE_IDENTITY_SUBSTITUTION,
            reason=CODE_SESSION_SUBSTITUTION,
        )
    except WorkflowChatFacadeError as exc:
        await browser.close(
            code=WS_CLOSE_CAPABILITY_DENIED,
            reason=exc.code,
        )
    except ValueError:
        await browser.close(
            code=WS_CLOSE_LIMIT_EXCEEDED,
            reason="omnigent_chat_ws_message_too_large",
        )
    except WebSocketDisconnect:
        # A browser disconnect is normal terminal evidence for this bounded
        # relay; no payload or terminal input is logged.
        return


def _facade_reconnect_state(row: Any) -> str:
    """Return a truthful native reconnect/liveness state (OB §17, brief §5).

    The browser never selects a host, runner, endpoint, profile, or credential
    generation; the state is derived only from the durable binding so the native
    UI can distinguish a still-starting session from a terminal one and from one
    that has no live provider session to reconnect to.
    """

    status_value = str(getattr(row, "status", "") or "").strip().lower()
    metadata = getattr(row, "metadata_", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    if is_read_only(status_value):
        return "terminal"
    if metadata.get("compatibilityUnsupported") is True:
        return "unsupported"
    if metadata.get("localStranded") is True:
        return "local-stranded"
    if metadata.get("hostOnline") is False:
        return "wakeable" if metadata.get("hostWakeable") is True else "host-offline"
    if not str(getattr(row, "omnigent_session_id", "") or "").strip():
        return "starting"
    return "available"


def _filter_session_updates_watch_frame(
    payload: str, *, chat_binding_id: str, provider_session_id: str
) -> str:
    """Validate and virtualize the client-authored updates watch set."""

    try:
        frame = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PermissionError("invalid session updates frame") from exc
    if not isinstance(frame, dict) or frame.get("type") != "watch":
        raise PermissionError("unrecognized session updates frame")
    session_ids = frame.get("session_ids")
    if not isinstance(session_ids, list) or any(
        item != chat_binding_id for item in session_ids
    ):
        raise PermissionError("session updates identity substitution")
    frame["session_ids"] = [provider_session_id] if session_ids else []
    return json.dumps(frame, separators=(",", ":"))


def _virtualize_session_updates_frame(
    payload: str, *, chat_binding_id: str, provider_session_id: str
) -> str | None:
    """Virtualize provider identity in an updates frame; drop malformed data."""

    try:
        frame = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    virtualized = _virtualize_facade_payload(
        frame,
        provider_session_id=provider_session_id,
        chat_binding_id=chat_binding_id,
    )
    return json.dumps(virtualized, separators=(",", ":"))


async def workflow_chat_binding_facade_ws(
    websocket: WebSocket,
    chat_binding_id: str,
    omnigent_path: str,
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store),
) -> None:
    """Authenticate, authorize, and validate a native-UI WebSocket before upgrade.

    Fails closed with a specific, non-enumerating close code for every rejection
    path; recognized-but-unreviewed transports are closed with the compatibility
    diagnostic instead of being proxied. The socket is only ever accepted after
    the full per-connection authorization succeeds.
    """

    journal_match = match_facade_operation("WEBSOCKET", omnigent_path)
    if journal_match is not None:
        await _stream_workflow_chat_events_websocket(
            websocket=websocket,
            chat_binding_id=chat_binding_id,
            match=journal_match,
            user=user,
            service=service,
            store=store,
        )
        return

    match = classify_native_ui_websocket(omnigent_path)
    operation = match.route.name if match else "unknown"

    # 1. Authenticate (dependency) + resolve/authorize the durable binding. An
    #    unknown binding and an unauthorized caller collapse to one close code so
    #    the browser cannot enumerate which bindings exist.
    try:
        row = await _resolve_and_authorize_chat_binding(
            chat_binding_id=chat_binding_id,
            operation=operation,
            user=user,
            service=service,
            store=store,
        )
    except (WorkflowChatFacadeError, OmnigentBridgeError, HTTPException):
        await websocket.close(
            code=WS_CLOSE_BINDING_UNKNOWN, reason=CODE_BINDING_UNKNOWN
        )
        return

    # 2. Reject any caller-supplied server-owned identity in the path or query
    #    (the only session id the browser may name is the bound chatBindingId).
    try:
        assert_no_identity_substitution(
            chat_binding_id=chat_binding_id,
            path_session_id=(match.params.get("session_id") if match else None),
            query=_identity_query_values(websocket.query_params),
            headers=websocket.headers,
        )
    except WorkflowChatFacadeError as exc:
        _audit_facade(
            outcome="denied",
            operation=operation,
            chat_binding_id=chat_binding_id,
            user=user,
            reason="identity_substitution",
        )
        await websocket.close(code=WS_CLOSE_IDENTITY_SUBSTITUTION, reason=exc.code)
        return

    # 3. Unknown transport class fails closed with a compatibility diagnostic;
    #    it is never generically proxied (issue §1, acceptance criterion 8).
    if match is None:
        _audit_facade(
            outcome="denied",
            operation="unknown",
            chat_binding_id=chat_binding_id,
            user=user,
            reason="transport_unsupported",
        )
        await websocket.close(
            code=WS_CLOSE_TRANSPORT_UNSUPPORTED, reason=CODE_TRANSPORT_UNSUPPORTED
        )
        return
    route = match.route
    try:
        _validate_native_match_paths(match)
    except WorkflowChatFacadeError:
        await websocket.close(
            code=WS_CLOSE_CAPABILITY_DENIED, reason=CODE_OPERATION_DENIED
        )
        return

    if config.host_protocol_mode != HOST_PROTOCOL_MODE_PROXY:
        await websocket.close(
            code=WS_CLOSE_TRANSPORT_UNSUPPORTED,
            reason="omnigent_bridge_mode_unsupported",
        )
        return

    if route.disposition != DISPOSITION_SERVED:
        await websocket.close(
            code=WS_CLOSE_COMPAT_REVIEW, reason=CODE_COMPAT_REVIEW_REQUIRED
        )
        return

    if not _websocket_origin_allowed(websocket):
        _audit_facade(
            outcome="denied",
            operation=operation,
            chat_binding_id=chat_binding_id,
            user=user,
            reason="origin_rejected",
        )
        await websocket.close(
            code=WS_CLOSE_CAPABILITY_DENIED,
            reason="omnigent_chat_ws_origin_rejected",
        )
        return

    # 4. A terminal/revoked binding cannot open a new live transport; close it
    #    rather than letting a WebSocket outlive terminal authority (criterion 10).
    session_status = str(getattr(row, "status", "") or "")
    if is_read_only(session_status):
        await websocket.close(code=WS_CLOSE_READ_ONLY, reason=CODE_SESSION_READ_ONLY)
        return

    # 5. Capability gate from recomputed trusted state intersected with policy.
    #    Terminal create/attach/input/resize/close and browser-pane control
    #    require capabilities the facade never grants, so a read-only viewer or a
    #    nonowner is denied here before the transport is opened (criteria 4, 5).
    capabilities = _effective_capabilities(row, user).capabilities
    terminal_read_only = (
        operation == "terminal_attach"
        and str(websocket.query_params.get("read_only") or "").lower() == "true"
    )
    required_capabilities = (route.capability,) if route.capability else ()
    if operation == "terminal_attach":
        required_capabilities = (
            ("viewTerminal",)
            if terminal_read_only
            else ("attachTerminal", "writeTerminal")
        )
    if any(
        not capabilities.get(capability, False) for capability in required_capabilities
    ):
        _audit_facade(
            outcome="denied",
            operation=operation,
            chat_binding_id=chat_binding_id,
            user=user,
            reason="capability_denied",
        )
        await websocket.close(
            code=WS_CLOSE_CAPABILITY_DENIED, reason=CODE_OPERATION_DENIED
        )
        return

    # 6. Negotiate only an allowlisted subprotocol before upgrade.
    offered = [
        part.strip()
        for part in str(websocket.headers.get("sec-websocket-protocol") or "").split(
            ","
        )
        if part.strip()
    ]
    try:
        selected_subprotocol = negotiate_ws_subprotocol(offered, route=route)
    except NativeUiCompatibilityError:
        _audit_facade(
            outcome="denied",
            operation=operation,
            chat_binding_id=chat_binding_id,
            user=user,
            reason="subprotocol_rejected",
        )
        await websocket.close(
            code=WS_CLOSE_SUBPROTOCOL_REJECTED, reason=CODE_WS_SUBPROTOCOL_REJECTED
        )
        return

    # 7. A live provider session must exist before any live transport (the
    #    reconnect probe is exempt: it truthfully reports a still-starting state).
    provider_session_id = str(getattr(row, "omnigent_session_id", "") or "").strip()
    if not provider_session_id and route.operation_class != "reconnect":
        await websocket.close(
            code=WS_CLOSE_SESSION_NOT_READY, reason=CODE_SESSION_NOT_READY
        )
        return

    if not provider_session_id:
        await websocket.accept(subprotocol=selected_subprotocol)
        await websocket.send_json({"state": _facade_reconnect_state(row)})
        await websocket.close(code=1000)
        return

    # 8. Replace the browser-visible binding alias with the server-owned provider
    #    identity and relay through MoonMind. Browser headers, credentials, and
    #    query parameters are never forwarded upstream.
    upstream_path = upstream_websocket_path(match, provider_session_id)
    browser_frame_filter = None
    upstream_frame_filter = None
    if operation == "ws_session_updates":

        def browser_frame_filter(payload: str) -> str:
            return _filter_session_updates_watch_frame(
                payload,
                chat_binding_id=chat_binding_id,
                provider_session_id=provider_session_id,
            )

        def upstream_frame_filter(payload: str) -> str | None:
            return _virtualize_session_updates_frame(
                payload,
                chat_binding_id=chat_binding_id,
                provider_session_id=provider_session_id,
            )

    if operation == "terminal_attach":
        from urllib.parse import urlencode

        query = {"read_only": "true" if terminal_read_only else "false"}
        transport = str(websocket.query_params.get("transport") or "").strip()
        if transport:
            if transport not in {"control", "pty"}:
                await websocket.close(
                    code=WS_CLOSE_CAPABILITY_DENIED,
                    reason=CODE_OPERATION_DENIED,
                )
                return
            query["transport"] = transport
        upstream_path += "?" + urlencode(query)

    async def still_authorized() -> bool:
        try:
            refreshed = await _resolve_and_authorize_chat_binding(
                chat_binding_id=chat_binding_id,
                operation=operation,
                user=user,
                service=service,
                store=store,
            )
        except (WorkflowChatFacadeError, OmnigentBridgeError, HTTPException):
            return False
        if is_read_only(str(getattr(refreshed, "status", "") or "")):
            return False
        if (
            str(getattr(refreshed, "omnigent_session_id", "") or "").strip()
            != provider_session_id
        ):
            return False
        refreshed_capabilities = _effective_capabilities(refreshed, user).capabilities
        return all(
            refreshed_capabilities.get(capability, False)
            for capability in required_capabilities
        )

    browser_frame_guard = None
    browser_frame_audit = None
    if route.mutation:

        async def browser_frame_guard(
            payload: str | bytes, is_binary: bool
        ) -> tuple[str | bytes, dict[str, Any]]:
            refreshed = await _resolve_and_authorize_chat_binding(
                chat_binding_id=chat_binding_id,
                operation=operation,
                user=user,
                service=service,
                store=store,
            )
            refreshed_capabilities = _effective_capabilities(
                refreshed, user
            ).capabilities
            if (
                is_read_only(str(getattr(refreshed, "status", "") or ""))
                or str(getattr(refreshed, "omnigent_session_id", "") or "").strip()
                != provider_session_id
                or any(
                    not refreshed_capabilities.get(capability, False)
                    for capability in required_capabilities
                )
            ):
                raise WorkflowChatFacadeError(
                    "The WebSocket frame is no longer authorized.",
                    failure_class="user_error",
                    status_code=status.HTTP_403_FORBIDDEN,
                    code=CODE_OPERATION_DENIED,
                )
            scan_payload: Any = payload if is_binary else {"frame": payload}
            frame_key = f"ws-{uuid4().hex}"
            evidence = _enforce_native_outbound_scan(
                surface=NativeScanSurface.WEBSOCKET_FRAME,
                body=scan_payload,
                idempotency_key=frame_key,
                chat_binding_id=chat_binding_id,
                user=user,
                operation=f"{operation}:frame",
            )
            if canonical_payload_digest(scan_payload) != evidence.payload_digest:
                raise WorkflowChatFacadeError(
                    "The security scan required for this frame is unavailable.",
                    failure_class="system_error",
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code=CODE_ENFORCEMENT_UNAVAILABLE,
                )
            request_time = datetime.now(tz=UTC).isoformat()
            claimed = await _claim_facade_message(
                store=store,
                row=refreshed,
                event_type=f"{operation}_frame",
                # The pinned WebSocket classes are terminal attach/input/resize,
                # dictation, and the session update stream. None of them submit
                # chat instruction text, so each frame is session-directed
                # steering in the closed #3707 vocabulary.
                turn_source=TurnSource.STEERING,
                actor=str(user.id),
                idempotency_key=frame_key,
                payload_digest=evidence.payload_digest,
                scan_evidence=evidence,
                request_time=request_time,
            )
            if not claimed:
                raise WorkflowChatFacadeError(
                    "The WebSocket frame could not be durably claimed.",
                    failure_class="system_error",
                    status_code=status.HTTP_409_CONFLICT,
                    code=CODE_SESSION_NOT_READY,
                )
            return payload, {
                "row": refreshed,
                "key": frame_key,
                "evidence": evidence,
                "requestTime": request_time,
                "dispatchTime": datetime.now(tz=UTC).isoformat(),
            }

        async def browser_frame_audit(receipt: Mapping[str, Any], outcome: str) -> None:
            await _record_facade_mutation_audit(
                store=store,
                row=receipt["row"],
                control_type=f"{operation}_frame",
                outcome=outcome,
                actor=str(user.id),
                idempotency_key=str(receipt["key"]),
                scan_evidence=receipt["evidence"],
                upstream_result=({"accepted": True} if outcome == "posted" else None),
                request_time=str(receipt["requestTime"]),
                dispatch_time=str(receipt["dispatchTime"]),
            )

    _audit_facade(
        outcome="connected",
        operation=f"{operation}:{_facade_reconnect_state(row)}",
        chat_binding_id=chat_binding_id,
        user=user,
        reason="binding_scoped_websocket",
    )
    await _relay_native_websocket(
        browser=websocket,
        upstream_url=_upstream_ws_url(upstream_path),
        subprotocol=selected_subprotocol,
        still_authorized=still_authorized,
        browser_frame_filter=browser_frame_filter,
        upstream_frame_filter=upstream_frame_filter,
        browser_frame_guard=browser_frame_guard,
        browser_frame_audit=browser_frame_audit,
    )


async def _stream_workflow_chat_events_websocket(
    *,
    websocket: WebSocket,
    chat_binding_id: str,
    match: Any,
    user: User,
    service: Any,
    store: OmnigentBridgeSessionStore,
) -> None:
    """Serve the durable binding journal over the scoped native WebSocket."""

    origin = str(websocket.headers.get("origin") or "").rstrip("/")
    host = str(websocket.headers.get("host") or "")
    if not host or origin not in {f"http://{host}", f"https://{host}"}:
        await websocket.close(code=4403, reason="omnigent_chat_origin_denied")
        return
    try:
        row = await _resolve_and_authorize_chat_binding(
            chat_binding_id=chat_binding_id,
            operation=match.operation.name,
            user=user,
            service=service,
            store=store,
        )
        assert_no_identity_substitution(
            chat_binding_id=chat_binding_id,
            path_session_id=match.params.get("session_id"),
            query=_identity_query_values(websocket.query_params),
            headers=websocket.headers,
        )
    except (WorkflowChatFacadeError, OmnigentBridgeError, HTTPException) as exc:
        await websocket.close(
            code=WS_CLOSE_BINDING_UNKNOWN,
            reason=getattr(exc, "code", CODE_BINDING_UNKNOWN),
        )
        return

    bridge_session_id = str(getattr(row, "bridge_session_id", "") or "")
    provider_session_id = str(getattr(row, "omnigent_session_id", "") or "")
    if not _effective_capabilities(row, user).capabilities.get("viewTranscript", False):
        await websocket.close(
            code=WS_CLOSE_CAPABILITY_DENIED, reason=CODE_OPERATION_DENIED
        )
        return
    try:
        cursor = (
            _strict_query_cursor(websocket.query_params.get("cursor"), "cursor") or 0
        )
    except WorkflowChatFacadeError as exc:
        await websocket.close(code=WS_CLOSE_TRANSPORT_UNSUPPORTED, reason=exc.code)
        return

    await websocket.accept()
    polls_since_reauth = 0
    try:
        while True:
            polls_since_reauth += 1
            if polls_since_reauth >= _FACADE_STREAM_REAUTH_EVERY_POLLS:
                polls_since_reauth = 0
                refreshed = await _resolve_and_authorize_chat_binding(
                    chat_binding_id=chat_binding_id,
                    operation=match.operation.name,
                    user=user,
                    service=service,
                    store=store,
                )
                if (
                    not _effective_capabilities(refreshed, user).capabilities.get(
                        "viewTranscript", False
                    )
                    or str(getattr(refreshed, "bridge_session_id", "") or "")
                    != bridge_session_id
                    or str(getattr(refreshed, "omnigent_session_id", "") or "")
                    != provider_session_id
                ):
                    raise WorkflowChatFacadeError(
                        "Workflow Chat transcript authority is no longer valid.",
                        failure_class="user_error",
                        status_code=status.HTTP_403_FORBIDDEN,
                        code=CODE_OPERATION_DENIED,
                    )
            page = await store.list_event_page(
                bridge_session_id,
                after=cursor,
                limit=_BRIDGE_STREAM_PAGE_SIZE,
            )
            if (
                page.earliest_sequence is not None
                and cursor + 1 < page.earliest_sequence
            ):
                gap = BridgeRetentionGap(
                    requested_after=cursor,
                    earliest_available=page.earliest_sequence,
                )
                await websocket.send_json(
                    {
                        "type": "retention_gap",
                        "data": gap.model_dump(by_alias=True),
                    }
                )
                await websocket.close(code=1000)
                return
            for event in page.rows:
                cursor = event.sequence
                if not _event_is_chat_visible(event):
                    continue
                payload = _bridge_event_payload(event)
                for key in ("bridgeSessionId", "sessionId", "session_id"):
                    if key in payload:
                        payload[key] = chat_binding_id
                await websocket.send_json(
                    {
                        "type": "bridge_event",
                        "sequence": event.sequence,
                        "data": _virtualize_facade_payload(
                            payload,
                            provider_session_id=provider_session_id,
                            chat_binding_id=chat_binding_id,
                        ),
                    }
                )
            session_row = await store.get_bridge_session(bridge_session_id)
            envelope = _terminal_envelope(session_row)
            if (
                envelope is not None
                and cursor >= page.latest_sequence
                and not page.has_more
            ):
                confirmation = await store.list_event_page(
                    bridge_session_id, after=cursor, limit=1
                )
                if confirmation.rows or confirmation.latest_sequence > cursor:
                    continue
                await websocket.send_json(
                    {"type": "terminal", "data": envelope.model_dump(by_alias=True)}
                )
                await websocket.close(code=1000)
                return
            try:
                await asyncio.wait_for(
                    websocket.receive_text(), timeout=_BRIDGE_STREAM_POLL_SECONDS
                )
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        return
    except WorkflowChatFacadeError as exc:
        await websocket.close(
            code=WS_CLOSE_CAPABILITY_DENIED,
            reason=exc.code,
        )
    except (OmnigentBridgeError, HTTPException):
        await websocket.close(
            code=WS_CLOSE_CAPABILITY_DENIED,
            reason=CODE_BINDING_UNKNOWN,
        )


workflow_chat_router.add_api_websocket_route(
    "/{chat_binding_id}/omnigent/{omnigent_path:path}",
    workflow_chat_binding_facade_ws,
)


__all__ = [
    "OMNIGENT_BRIDGE_MOUNT_PATH",
    "WORKFLOW_CHAT_BINDINGS_MOUNT_PATH",
    "router",
    "workflow_chat_router",
]
