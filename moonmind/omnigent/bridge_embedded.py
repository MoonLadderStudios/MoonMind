"""Embedded Omnigent-compatible host protocol facade.

MM-1164 implements the bounded embedded-mode surface from
``docs/Omnigent/OmnigentBridge.md`` §3.2, §4.2, §5.2, §16, and §19.10. The
facade is intentionally thin: it authenticates unchanged hosts with the
configured host/runner auth profile, accepts host registration/heartbeat/event
messages, normalizes those host events through the existing bridge event model,
and persists them into the canonical bridge session store.
"""

from __future__ import annotations

from dataclasses import dataclass
from hmac import compare_digest
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    OmnigentBridgeConfig,
)
from moonmind.omnigent.bridge_events import build_omnigent_bridge_event
from moonmind.omnigent.bridge_proxy import OmnigentBridgeError
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

_HOST_TOKEN_HEADER = "x-omnigent-host-token"


class EmbeddedHostRegisterRequest(BaseModel):
    """Host registration payload accepted from an unchanged Omnigent host."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    host_id: str | None = Field(None, alias="hostId")
    runner_id: str | None = Field(None, alias="runnerId")
    capabilities: dict[str, Any] = Field(default_factory=dict)


class EmbeddedHostHeartbeatRequest(BaseModel):
    """Host heartbeat payload."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str = "running"
    capabilities: dict[str, Any] = Field(default_factory=dict)


class EmbeddedHostSessionEventRequest(BaseModel):
    """Host-to-MoonMind session event payload."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    type: str = Field(..., min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmbeddedHostAuthContext:
    """Verified embedded host auth context."""

    auth_mode: str
    protocol_profile: str


def verify_embedded_host_auth(
    *,
    headers: Mapping[str, Any],
    config: OmnigentBridgeConfig,
    configured_token: str,
) -> EmbeddedHostAuthContext:
    """Verify the embedded host/runner auth profile (§16 rule 8).

    ``header_or_token`` accepts either ``Authorization: Bearer <token>`` or the
    Omnigent-shaped ``X-Omnigent-Host-Token`` header. The token is service-side
    configuration and is never accepted from bridge config or workflow payloads.
    """

    embedded = config.host_connection.embedded
    auth_mode = str(embedded.auth_mode or "").strip()
    if auth_mode != "header_or_token":
        raise OmnigentBridgeError(
            f"Unsupported embedded host auth mode: {auth_mode}",
            failure_class="system_error",
            status_code=501,
        )
    expected = str(configured_token or "").strip()
    if not expected:
        raise OmnigentBridgeError(
            "Embedded Omnigent host auth requires OMNIGENT_HOST_RUNNER_TOKEN",
            failure_class="system_error",
            status_code=503,
        )
    provided = _extract_host_token(headers)
    if not provided or not compare_digest(provided, expected):
        raise OmnigentBridgeError(
            "Embedded Omnigent host authentication failed",
            failure_class="user_error",
            status_code=401,
        )
    return EmbeddedHostAuthContext(
        auth_mode=auth_mode,
        protocol_profile=embedded.protocol_profile,
    )


class OmnigentEmbeddedHostProtocolFacade:
    """Embedded host-facing protocol facade over the bridge session store."""

    def __init__(
        self,
        *,
        run_store: OmnigentBridgeSessionStore,
        config: OmnigentBridgeConfig,
    ) -> None:
        self._run_store = run_store
        self._config = config

    async def register_host(
        self,
        *,
        request: EmbeddedHostRegisterRequest,
        auth: EmbeddedHostAuthContext,
    ) -> dict[str, Any]:
        self._require_embedded_mode()
        host_id = _clean(request.host_id) or "embedded-host"
        runner_id = _clean(request.runner_id)
        return {
            "hostId": host_id,
            "runnerId": runner_id,
            "status": "registered",
            "capabilities": request.capabilities,
            "moonmind": {
                "bridgeLocal": True,
                "hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED,
                "protocolProfile": auth.protocol_profile,
            },
        }

    async def heartbeat(
        self,
        *,
        host_id: str,
        request: EmbeddedHostHeartbeatRequest,
        auth: EmbeddedHostAuthContext,
    ) -> dict[str, Any]:
        self._require_embedded_mode()
        return {
            "hostId": _clean(host_id),
            "status": request.status,
            "capabilities": request.capabilities,
            "moonmind": {
                "bridgeLocal": True,
                "hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED,
                "protocolProfile": auth.protocol_profile,
            },
        }

    async def ingest_session_event(
        self,
        *,
        host_id: str,
        session_id: str,
        request: EmbeddedHostSessionEventRequest,
        auth: EmbeddedHostAuthContext,
    ) -> dict[str, Any]:
        self._require_embedded_mode()
        row = await self._run_store.get_session_by_provider_session_id(session_id)
        if row is None:
            raise OmnigentBridgeError(
                "No Omnigent bridge session is bound to the requested session id.",
                failure_class="user_error",
                status_code=404,
            )
        payload = request.model_dump(by_alias=True)
        payload.setdefault("direction", "host_to_moonmind")
        payload.setdefault("data", {})
        if isinstance(payload["data"], dict):
            payload["data"].setdefault("hostId", _clean(host_id))
        normalized = build_omnigent_bridge_event(
            payload=payload,
            sequence=1,
            request=_request_for_row(row),
            omnigent_session_id=session_id,
            bridge_session_id=row.bridge_session_id,
        ).event
        rows = await self._run_store.append_events(row.bridge_session_id, [normalized])
        return {
            "ok": True,
            "accepted": 1,
            "bridgeSessionId": row.bridge_session_id,
            "eventId": rows[0].event_id if rows else None,
            "sequence": rows[0].sequence if rows else None,
            "moonmind": {
                "bridgeLocal": True,
                "hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED,
                "protocolProfile": auth.protocol_profile,
            },
        }

    def _require_embedded_mode(self) -> None:
        if self._config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED:
            raise OmnigentBridgeError(
                "Embedded Omnigent host protocol requires "
                "embedded_omnigent_compatible_server mode.",
                failure_class="system_error",
                status_code=501,
            )


def _extract_host_token(headers: Mapping[str, Any]) -> str:
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    explicit = normalized.get(_HOST_TOKEN_HEADER)
    if explicit:
        return explicit.strip()
    authorization = normalized.get("authorization", "").strip()
    prefix = "bearer "
    if authorization.lower().startswith(prefix):
        return authorization[len(prefix) :].strip()
    return ""


def _request_for_row(row: Any) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId=str(row.moonmind_workflow_id or row.idempotency_key),
        idempotencyKey=str(row.idempotency_key),
    )


def _clean(value: Any) -> str:
    return str(value or "").strip()


__all__ = [
    "EmbeddedHostAuthContext",
    "EmbeddedHostHeartbeatRequest",
    "EmbeddedHostRegisterRequest",
    "EmbeddedHostSessionEventRequest",
    "OmnigentEmbeddedHostProtocolFacade",
    "verify_embedded_host_auth",
]
