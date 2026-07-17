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
import importlib
import importlib.metadata
import inspect
from typing import Any, Awaitable, Callable, Mapping

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

_FORBIDDEN_HOST_AUTH_HEADERS = frozenset(
    {"cookie", "x-moonmind-execution-principal", "x-moonmind-workflow-id"}
)


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
    host_id: str
    runner_id: str | None
    credential_generation: int
    profile_id: str


UpstreamHostVerifier = Callable[..., Mapping[str, Any] | Awaitable[Mapping[str, Any]]]


async def verify_embedded_host_auth(
    *,
    headers: Mapping[str, Any],
    config: OmnigentBridgeConfig,
    secret_ref: str,
    verifier: UpstreamHostVerifier | None = None,
    secret_resolver: Callable[[str], Awaitable[str]] | None = None,
) -> EmbeddedHostAuthContext:
    """Delegate host authentication to the exactly pinned upstream verifier."""

    embedded = config.host_connection.embedded
    auth_mode = str(embedded.auth_mode or "").strip()
    if auth_mode != "upstream_verifier":
        raise OmnigentBridgeError(
            f"Unsupported embedded host auth mode: {auth_mode}",
            failure_class="system_error",
            status_code=501,
        )
    ref = str(secret_ref or "").strip()
    if not ref:
        raise OmnigentBridgeError(
            "Embedded Omnigent host auth requires OMNIGENT_HOST_RUNNER_SECRET_REF",
            failure_class="system_error",
            status_code=503,
        )
    normalized = {str(k).lower(): v for k, v in headers.items()}
    forbidden = sorted(_FORBIDDEN_HOST_AUTH_HEADERS.intersection(normalized))
    if forbidden:
        raise OmnigentBridgeError(
            "MoonMind user/session authentication material is not valid host auth",
            failure_class="user_error",
            status_code=400,
        )
    selected_verifier = verifier or _load_pinned_upstream_verifier(config)
    resolver = secret_resolver or _resolve_host_secret
    try:
        secret = await resolver(ref)
        result = selected_verifier(headers=headers, server_credential=secret)
        if inspect.isawaitable(result):
            result = await result
    except OmnigentBridgeError:
        raise
    except Exception as exc:
        raise OmnigentBridgeError(
            "Upstream Omnigent host authentication failed",
            failure_class="integration_error",
            status_code=401,
        ) from exc
    claims = dict(result or {})
    host_id = _clean(claims.get("host_id") or claims.get("hostId"))
    generation = claims.get(
        "credential_generation", claims.get("credentialGeneration")
    )
    profile_id = _clean(claims.get("profile_id") or claims.get("profileId"))
    if (
        not host_id
        or not profile_id
        or not isinstance(generation, int)
        or generation < 1
    ):
        raise OmnigentBridgeError(
            "Upstream Omnigent verifier returned an unsupported identity shape",
            failure_class="integration_error",
            status_code=502,
        )
    return EmbeddedHostAuthContext(
        auth_mode=auth_mode,
        protocol_profile=embedded.protocol_profile,
        host_id=host_id,
        runner_id=_clean(claims.get("runner_id") or claims.get("runnerId")),
        credential_generation=generation,
        profile_id=profile_id,
    )


async def _resolve_host_secret(secret_ref: str) -> str:
    from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
        resolve_managed_api_key_reference,
    )

    return await resolve_managed_api_key_reference(secret_ref)


def _load_pinned_upstream_verifier(config: OmnigentBridgeConfig) -> UpstreamHostVerifier:
    embedded = config.host_connection.embedded
    try:
        installed = importlib.metadata.version(embedded.upstream_auth_distribution)
    except importlib.metadata.PackageNotFoundError as exc:
        raise OmnigentBridgeError(
            "Pinned upstream Omnigent auth distribution is not installed",
            failure_class="system_error",
            status_code=503,
        ) from exc
    if installed != embedded.upstream_auth_version:
        raise OmnigentBridgeError(
            "Installed upstream Omnigent auth version does not match the pin",
            failure_class="system_error",
            status_code=503,
        )
    module_name, separator, attribute = (
        embedded.upstream_verifier_entrypoint.partition(":")
    )
    if not separator or not module_name or not attribute:
        raise OmnigentBridgeError(
            "Invalid upstream Omnigent verifier entrypoint",
            failure_class="system_error",
            status_code=503,
        )
    try:
        candidate = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as exc:
        raise OmnigentBridgeError(
            "Pinned upstream Omnigent verifier entrypoint is unavailable",
            failure_class="system_error",
            status_code=503,
        ) from exc
    if not callable(candidate):
        raise OmnigentBridgeError(
            "Pinned upstream Omnigent verifier entrypoint is not callable",
            failure_class="system_error",
            status_code=503,
        )
    return candidate


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
        host_id = _clean(request.host_id) or auth.host_id
        runner_id = _clean(request.runner_id) or auth.runner_id
        _assert_auth_identity(auth, host_id=host_id, runner_id=runner_id)
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
        _assert_auth_identity(auth, host_id=_clean(host_id), runner_id=None)
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
        _assert_auth_identity(auth, host_id=_clean(host_id), runner_id=None)
        row = await self._run_store.get_session_by_provider_session_id(session_id)
        if row is None:
            raise OmnigentBridgeError(
                "No Omnigent bridge session is bound to the requested session id.",
                failure_class="user_error",
                status_code=404,
            )
        if (
            row.omnigent_host_id != auth.host_id
            or row.provider_profile_id != auth.profile_id
            or row.credential_generation != auth.credential_generation
        ):
            raise OmnigentBridgeError(
                "Verified host identity is not bound to this session",
                failure_class="user_error",
                status_code=403,
            )
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

def _assert_auth_identity(
    auth: EmbeddedHostAuthContext, *, host_id: str, runner_id: str | None
) -> None:
    if not host_id or host_id != auth.host_id:
        raise OmnigentBridgeError(
            "Verified host identity does not match the requested host",
            failure_class="user_error",
            status_code=403,
        )
    if runner_id and runner_id != auth.runner_id:
        raise OmnigentBridgeError(
            "Verified runner identity does not match the requested runner",
            failure_class="user_error",
            status_code=403,
        )


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
