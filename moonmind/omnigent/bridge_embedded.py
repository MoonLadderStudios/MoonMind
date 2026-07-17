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
from moonmind.omnigent.bridge_artifacts import OmnigentContractError
from moonmind.omnigent.bridge_events import build_omnigent_bridge_event
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    OmnigentBridgeError,
    validate_bridge_host_fields,
)
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

    async def create_session(
        self,
        *,
        request: BridgeSessionCreateRequest,
        binding: BridgePrincipalBinding,
    ) -> dict[str, Any]:
        """Create or reuse a local embedded bridge session."""

        self._require_embedded_mode()
        validate_bridge_host_fields(
            host_type=request.host_type,
            host_id=request.host_id,
            workspace=request.workspace,
        )
        exec_request = _request_for_create(request=request, binding=binding)
        row = await self._run_store.get_or_create(
            request=exec_request,
            endpoint_ref=(request.endpoint_ref or "").strip() or "embedded",
            agent_id=(request.agent_id or "").strip() or None,
            agent_name=None,
            target_metadata={
                "hostType": request.host_type,
                "hostId": (request.host_id or "").strip() or None,
                "workspace": (request.workspace or "").strip() or None,
            },
            workflow_id=binding.workflow_id,
            agent_run_id=binding.agent_run_id,
        )
        _assert_row_owner(row, binding)
        session_id = str(getattr(row, "omnigent_session_id", None) or "").strip()
        reused = bool(session_id)
        if not session_id:
            session_id = f"emb_{row.bridge_session_id}"
            row = await self._run_store.attach_session(
                binding.idempotency_key, session_id
            )
        await self._run_store.record_session_created(
            binding.idempotency_key,
            session_id=session_id,
            agent_id=(request.agent_id or "").strip() or None,
            endpoint_ref=(request.endpoint_ref or "").strip() or "embedded",
        )
        return {
            "id": session_id,
            "status": row.status,
            "moonmind": {
                "workflowId": binding.workflow_id,
                "agentRunId": binding.agent_run_id,
                "idempotencyKey": binding.idempotency_key,
                "reused": reused,
                "bridgeSessionId": row.bridge_session_id,
                "bridgeLocal": True,
                "hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED,
                "protocolProfile": (
                    self._config.host_connection.embedded.protocol_profile
                ),
            },
        }

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
        _assert_embedded_host_owns_session(row=row, host_id=host_id)
        payload = request.model_dump(by_alias=True)
        payload.setdefault("direction", "host_to_moonmind")
        payload.setdefault("data", {})
        if isinstance(payload["data"], dict):
            payload["data"].setdefault("hostId", _clean(host_id))
        try:
            normalized = build_omnigent_bridge_event(
                payload=payload,
                sequence=1,
                request=_request_for_row(row),
                omnigent_session_id=session_id,
                bridge_session_id=row.bridge_session_id,
            ).event
        except (OmnigentContractError, ValueError) as exc:
            raise OmnigentBridgeError(
                str(exc), failure_class="integration_error", status_code=502
            ) from exc
        normalized_body = dict(normalized)
        normalized_body["metadata"] = dict(normalized.get("metadata") or {})
        metadata = dict(normalized.get("metadata") or {})
        metadata["embeddedRawEvent"] = payload
        metadata["embeddedNormalizedEvent"] = normalized_body
        normalized["metadata"] = metadata
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


def _request_for_create(
    *,
    request: BridgeSessionCreateRequest,
    binding: BridgePrincipalBinding,
) -> AgentExecutionRequest:
    session_params: dict[str, Any] = {
        "hostType": request.host_type,
        "workspace": (request.workspace or "").strip() or None,
        "hostId": (request.host_id or "").strip() or None,
        "title": request.title,
        "labels": dict(request.labels or {}),
        "modelOverride": request.model_override,
        "reasoningEffort": request.reasoning_effort,
        "terminalLaunchArgs": list(request.terminal_launch_args or []),
        "allowEmptyWorkspace": True,
    }
    omnigent_params: dict[str, Any] = {
        "endpointRef": (request.endpoint_ref or "").strip() or "embedded",
        "session": {
            key: value for key, value in session_params.items() if value is not None
        },
    }
    agent_id = (request.agent_id or "").strip()
    if agent_id:
        omnigent_params["agent"] = {"agentId": agent_id}
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId=binding.correlation_id,
        idempotencyKey=binding.idempotency_key,
        parameters={"omnigent": omnigent_params},
    )


def _assert_row_owner(row: Any, binding: BridgePrincipalBinding) -> None:
    stored_workflow_id = str(getattr(row, "moonmind_workflow_id", None) or "").strip()
    if stored_workflow_id and stored_workflow_id != binding.workflow_id:
        raise OmnigentBridgeError(
            "Omnigent bridge idempotency key is bound to a different MoonMind "
            "workflow; refusing cross-owner reuse",
            failure_class="user_error",
            status_code=409,
        )


def _assert_embedded_host_owns_session(*, row: Any, host_id: str) -> None:
    """Enforce the durable exact-host assignment on host-originated traffic.

    Profile-bound execution persists ``omnigent_host_id`` before launching a
    session.  The socket/HTTP transport is not an authority boundary: every
    host-originated operation must still agree with that durable assignment.
    Sessions created without a profile-bound host retain the legacy unbound
    behavior while embedded mode remains experimental.
    """

    provided_host_id = _clean(host_id)
    if not provided_host_id:
        raise OmnigentBridgeError(
            "Embedded Omnigent session traffic requires a host id.",
            failure_class="user_error",
            status_code=400,
        )
    assigned_host_id = _clean(getattr(row, "omnigent_host_id", None))
    if assigned_host_id and provided_host_id != assigned_host_id:
        raise OmnigentBridgeError(
            "Embedded Omnigent host is not assigned to the requested session.",
            failure_class="user_error",
            status_code=403,
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
