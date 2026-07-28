"""Embedded Omnigent-compatible host protocol facade.

MM-1164 implements the bounded embedded-mode surface from
``docs/Omnigent/OmnigentBridge.md`` §3.2, §4.2, §5.2, §16, and §19.10. The
facade is intentionally thin: it authenticates unchanged hosts with the
configured host/runner auth profile, accepts host registration/heartbeat/event
messages, normalizes those host events through the existing bridge event model,
and persists them into the canonical bridge session store.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Any, Mapping
from urllib.parse import quote

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    OmnigentBridgeConfig,
)
from moonmind.omnigent.bridge_artifacts import (
    BridgeResourceHarvester,
    LocalOmnigentArtifactGateway,
    OmnigentArtifactGateway,
    OmnigentContractError,
)
from moonmind.omnigent.bridge_events import build_omnigent_bridge_event
from moonmind.omnigent.bridge_security import redact_raw_events
from moonmind.omnigent.bridge_proxy import (
    _MAX_FACADE_RESOURCE_BYTES,
    _bound_resource_lists,
    _safe_resource_identifier,
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    OmnigentBridgeError,
    validate_bridge_host_fields,
)
from moonmind.omnigent.bridge_store import (
    OmnigentBridgeSessionStore,
    OmnigentIdempotencyError,
)
from moonmind.omnigent.host_auth_adapter import (
    OmnigentHostAuthAdapter,
    UpstreamHostAuthError,
)
from moonmind.omnigent.embedded_host_channel import (
    EmbeddedHostChannelError,
    EmbeddedHostChannelRegistry,
    embedded_host_channels,
    derive_runner_binding_token,
)
from moonmind.omnigent.host_auth_adapter import OmnigentHostAuthAdapter
from moonmind.omnigent.settings import resolved_host_runner_token
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

MAX_EMBEDDED_CAPABILITIES = 128
MAX_EMBEDDED_CAPABILITY_BYTES = 64 * 1024
MAX_EMBEDDED_EVENT_ENTRIES = 1024
MAX_EMBEDDED_EVENT_BYTES = 1024 * 1024
logger = structlog.get_logger(__name__)
MAX_EMBEDDED_CONTROL_KEY_LENGTH = 220


def _bounded_mapping(
    value: dict[str, Any], *, label: str, max_entries: int, max_bytes: int
) -> dict[str, Any]:
    if len(value) > max_entries:
        raise ValueError(f"{label} exceeds the {max_entries}-entry limit")
    try:
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON serializable") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte limit")
    return value


class EmbeddedHostRegisterRequest(BaseModel):
    """Host registration payload accepted from an unchanged Omnigent host."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    host_id: str | None = Field(None, alias="hostId")
    runner_id: str | None = Field(None, alias="runnerId")
    capabilities: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_mapping(
            value,
            label="Host capabilities",
            max_entries=MAX_EMBEDDED_CAPABILITIES,
            max_bytes=MAX_EMBEDDED_CAPABILITY_BYTES,
        )


class EmbeddedHostHeartbeatRequest(BaseModel):
    """Host heartbeat payload."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_mapping(
            value,
            label="Host capabilities",
            max_entries=MAX_EMBEDDED_CAPABILITIES,
            max_bytes=MAX_EMBEDDED_CAPABILITY_BYTES,
        )


class EmbeddedHostSessionEventRequest(BaseModel):
    """Host-to-MoonMind session event payload."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    type: str = Field(..., min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("data")
    @classmethod
    def validate_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_mapping(
            value,
            label="Host event data",
            max_entries=MAX_EMBEDDED_EVENT_ENTRIES,
            max_bytes=MAX_EMBEDDED_EVENT_BYTES,
        )


@dataclass(frozen=True, slots=True)
class EmbeddedHostAuthContext:
    """Verified embedded host auth context."""

    auth_mode: str
    protocol_profile: str
    runner_id: str
    credential_generation: int
    credential_profile_id: str | None = None


def verify_embedded_host_auth(
    *,
    headers: Mapping[str, Any],
    config: OmnigentBridgeConfig,
    configured_token: str = "",
    configured_credentials: Mapping[int, str] | None = None,
    credential_profile_id: str = "bootstrap-local",
) -> EmbeddedHostAuthContext:
    """Verify the embedded host/runner auth profile (§16 rule 8).

    Authentication is delegated to the pinned upstream runner-tunnel verifier.
    """

    embedded = config.host_connection.embedded
    auth_mode = str(embedded.auth_mode or "").strip()
    if auth_mode != "upstream_runner_tunnel":
        raise OmnigentBridgeError(
            f"Unsupported embedded host auth mode: {auth_mode}",
            failure_class="system_error",
            status_code=501,
        )
    credentials = {
        int(generation): str(token).strip()
        for generation, token in (configured_credentials or {}).items()
        if str(token or "").strip()
    }
    if not credentials and str(configured_token or "").strip():
        credentials = {1: str(configured_token).strip()}
    try:
        adapter = OmnigentHostAuthAdapter(allowed_tokens=frozenset(credentials.values()))
        identity = adapter.verify(headers)
    except UpstreamHostAuthError as exc:
        unavailable = "configured" in str(exc) or "entrypoint" in str(exc)
        logger.warning(
            "embedded_host_auth_verification_failed",
            credential_profile_id=credential_profile_id,
            failure_code=("host_auth_unavailable" if unavailable else "host_auth_rejected"),
        )
        raise OmnigentBridgeError(
            "Embedded host credential could not be verified",
            failure_class="system_error" if unavailable else "user_error",
            status_code=503 if unavailable else 401,
            code=("host_auth_unavailable" if unavailable else "host_auth_rejected"),
        ) from exc
    header_values = (
        list(headers.getlist(adapter.token_header))
        if callable(getattr(headers, "getlist", None))
        else [
            str(value)
            for key, value in headers.items()
            if str(key).lower() == adapter.token_header.lower()
        ]
    )
    matched = [generation for generation, token in credentials.items() if header_values == [token]]
    if len(matched) != 1:
        logger.warning(
            "embedded_host_auth_verification_failed",
            credential_profile_id=credential_profile_id,
            failure_code="host_auth_stale_or_invalid_generation",
        )
        raise OmnigentBridgeError(
            "Embedded host credential generation could not be selected",
            failure_class="user_error",
            status_code=401,
            code="host_auth_stale_or_invalid_generation",
        )
    logger.info(
        "embedded_host_auth_verified",
        credential_profile_id=credential_profile_id,
        credential_generation=matched[0],
        runner_id=identity.runner_id,
    )
    return EmbeddedHostAuthContext(
        auth_mode=auth_mode,
        protocol_profile=identity.protocol_profile,
        runner_id=identity.runner_id,
        credential_generation=matched[0],
        credential_profile_id=credential_profile_id,
    )


class OmnigentEmbeddedHostProtocolFacade:
    """Embedded host-facing protocol facade over the bridge session store."""

    def __init__(
        self,
        *,
        run_store: OmnigentBridgeSessionStore,
        config: OmnigentBridgeConfig,
        host_channels: EmbeddedHostChannelRegistry = embedded_host_channels,
        artifact_gateway: OmnigentArtifactGateway | None = None,
        runner_binding_root_secret: str | None = None,
    ) -> None:
        self._run_store = run_store
        self._config = config
        self._host_channels = host_channels
        self._artifact_gateway = artifact_gateway or LocalOmnigentArtifactGateway()
        self._event_journal_locks: dict[str, asyncio.Lock] = {}
        self._runner_binding_root_secret = runner_binding_root_secret

    async def dispatch_runner(self, *, idempotency_key: str) -> dict[str, Any]:
        """Dispatch and durably bind a runner to an authorized embedded session."""

        self._require_embedded_mode()
        row = await self._run_store.get_existing(idempotency_key)
        if row is None or not row.omnigent_session_id or not row.omnigent_host_id:
            raise OmnigentBridgeError(
                "Embedded runner dispatch requires an assigned session and host",
                failure_class="system_error", status_code=409,
            )
        if row.omnigent_runner_id:
            if self._host_channels.is_runner_ready(row.omnigent_runner_id):
                await self._run_store.mark_embedded_runner_state(
                    idempotency_key,
                    state="runner_tunnel_ready",
                    code="live_tunnel_verified",
                )
                return {"runnerId": row.omnigent_runner_id, "reused": True}
            wait_ready = getattr(self._host_channels, "wait_runner_ready", None)
            if wait_ready is not None and await wait_ready(row.omnigent_runner_id):
                await self._run_store.mark_embedded_runner_state(
                    idempotency_key, state="runner_tunnel_ready",
                    code="bounded_runner_reconnect_verified",
                )
                return {"runnerId": row.omnigent_runner_id, "reused": True}
            await self._run_store.mark_embedded_runner_state(
                idempotency_key,
                state="stale",
                code="durable_runner_tunnel_unavailable",
            )
            stop_runner = getattr(self._host_channels, "stop_runner", None)
            if stop_runner is not None:
                try:
                    await stop_runner(
                        host_id=row.omnigent_host_id, runner_id=row.omnigent_runner_id
                    )
                except (EmbeddedHostChannelError, TimeoutError):
                    pass
                else:
                    await self._run_store.prepare_embedded_runner_replacement(
                        idempotency_key, runner_id=row.omnigent_runner_id
                    )
                    return await self.dispatch_runner(idempotency_key=idempotency_key)
            raise OmnigentBridgeError(
                "Durable embedded runner assignment did not reconnect before the "
                "bounded deadline; it was marked stale for safe replacement",
                failure_class="integration_error",
                status_code=503,
                code="embedded_runner_stale",
            )
        workspace = _clean(row.workspace)
        if not workspace:
            raise OmnigentBridgeError(
                "Embedded runner dispatch requires a workspace",
                failure_class="user_error", status_code=422,
            )
        try:
            credential_generation = int(row.credential_generation or 0)
            prior_launch = dict(
                (row.metadata_ or {}).get("embedded_runner_launch") or {}
            )
            launch_generation = int(prior_launch.get("launchGeneration") or 0)
            if prior_launch.get("state") not in {"pending", "launched"}:
                launch_generation += 1
            launch_generation = max(launch_generation, 1)
            binding_generation = credential_generation * 1_000_000 + launch_generation
            binding_token = derive_runner_binding_token(
                self._runner_binding_root_secret or resolved_host_runner_token(),
                host_id=row.omnigent_host_id,
                session_id=row.omnigent_session_id, generation=binding_generation,
            )
            expected_runner_id = OmnigentHostAuthAdapter(
                allowed_tokens=frozenset({binding_token})
            ).runner_id_for_binding_token(binding_token)
            reserved = await self._run_store.begin_embedded_runner_launch(
                idempotency_key, host_id=row.omnigent_host_id,
                runner_id=expected_runner_id, generation=binding_generation,
                credential_generation=credential_generation,
                launch_generation=launch_generation,
            )
        except (OmnigentIdempotencyError, EmbeddedHostChannelError) as exc:
            raise OmnigentBridgeError(
                str(exc), failure_class="integration_error", status_code=503
            ) from exc
        try:
            lifecycle_state = (
                (reserved.metadata_ or {}).get("embedded_runner_lifecycle") or {}
            ).get("state")
            # A retry after the launch command crossed the host boundary must
            # first reconcile the deterministic runner identity.  A freshly
            # authenticated tunnel is authoritative evidence that the command
            # succeeded, so resending it would only create a duplicate launch.
            if lifecycle_state in {"launch_sent", "launch_acknowledged"}:
                wait_ready = getattr(self._host_channels, "wait_runner_ready", None)
                if wait_ready is not None and await wait_ready(expected_runner_id):
                    if lifecycle_state == "launch_sent":
                        await self._run_store.mark_embedded_runner_state(
                            idempotency_key,
                            state="launch_acknowledged",
                            code="runner_tunnel_reconciled_launch_acknowledgement",
                        )
                    await self._run_store.bind_embedded_runner(
                        idempotency_key,
                        host_id=row.omnigent_host_id,
                        runner_id=expected_runner_id,
                    )
                    return {"runnerId": expected_runner_id, "reused": True}
                raise OmnigentBridgeError(
                    "Embedded runner launch already crossed the host boundary; "
                    "the reserved runner did not reconnect before the bounded deadline",
                    failure_class="integration_error",
                    status_code=503,
                    code="embedded_runner_launch_unconfirmed",
                )
            if lifecycle_state == "launch_reserved":
                await self._run_store.mark_embedded_runner_state(
                    idempotency_key,
                    state="launch_sent",
                    code="host_launch_command_sending",
                )
            runner_id = await self._host_channels.launch_runner(
                host_id=row.omnigent_host_id,
                workspace=workspace,
                session_id=row.omnigent_session_id,
                harness="codex-native",
                binding_token=binding_token,
            )
            if runner_id != expected_runner_id:
                raise EmbeddedHostChannelError("host returned a stale launch generation")
            if lifecycle_state != "launch_acknowledged":
                await self._run_store.mark_embedded_runner_state(
                    idempotency_key,
                    state="launch_acknowledged",
                    code="host_launch_acknowledged",
                )
        except (EmbeddedHostChannelError, TimeoutError) as exc:
            await self._run_store.fail_embedded_runner_launch(
                idempotency_key, host_id=row.omnigent_host_id
            )
            raise OmnigentBridgeError(
                str(exc), failure_class="integration_error", status_code=503
            ) from exc
        try:
            await self._run_store.bind_embedded_runner(
                idempotency_key, host_id=row.omnigent_host_id, runner_id=runner_id
            )
        except OmnigentIdempotencyError as exc:
            raise OmnigentBridgeError(
                str(exc), failure_class="integration_error", status_code=503
            ) from exc
        return {"runnerId": runner_id, "reused": False}

    async def record_runner_tunnel_ready(self, *, runner_id: str) -> None:
        row = await self._run_store.get_session_by_runner_id(runner_id)
        if row is None:
            raise EmbeddedHostChannelError("runner has no durable session binding")
        await self._run_store.mark_embedded_runner_state(
            row.idempotency_key,
            state="runner_tunnel_ready",
            code="authenticated_runner_handshake",
        )

    async def record_runner_tunnel_disconnected(self, *, runner_id: str) -> None:
        row = await self._run_store.get_session_by_runner_id(runner_id)
        if row is not None and row.status not in {
            "completed",
            "failed",
            "canceled",
            "timed_out",
        }:
            await self._run_store.mark_embedded_runner_state(
                row.idempotency_key,
                state="runner_tunnel_waiting",
                code="runner_tunnel_disconnected",
            )

    async def record_runner_exit(self, *, runner_id: str, error: str) -> None:
        """Record a stock host's authoritative runner process failure."""

        await self._run_store.record_embedded_runner_exit(
            runner_id=runner_id, error=error
        )

    async def stop_runner(
        self, *, session_id: str, payload: dict[str, Any] | None = None,
        actor: str | None = None, control_type: str = "stop",
    ) -> dict[str, Any]:
        """Stop the exact runner durably bound to an embedded session."""

        self._require_embedded_mode()
        row = await self._run_store.get_session_by_provider_session_id(session_id)
        if row is None:
            raise OmnigentBridgeError(
                "No Omnigent bridge session is bound to the requested session id.",
                failure_class="user_error",
                status_code=404,
            )
        host_id = _clean(row.omnigent_host_id)
        runner_id = _clean(row.omnigent_runner_id)
        if not host_id or not runner_id:
            raise OmnigentBridgeError(
                "Embedded session has no durable host/runner assignment",
                failure_class="integration_error",
                status_code=409,
            )
        terminal = row.status in {"completed", "failed", "canceled", "timed_out"}
        payload = payload or {}
        control_key = _clean(payload.get("idempotencyKey"))
        if not control_key:
            raise OmnigentBridgeError(
                "Embedded stop and cleanup controls require an idempotency key",
                failure_class="user_error", status_code=422,
                code="omnigent_embedded_control_idempotency_required",
            )
        reconciled = await self._reconcile_control(row, control_key)
        if reconciled is not None:
            return {**reconciled, "runnerId": runner_id}
        await self._validate_control_expectations(
            row=row, payload=payload, control_key=control_key,
            control_type=control_type, actor=actor,
        )
        claimed = await self._claim_control(
            row, control_key, control_type, "requested",
            summary=f"Embedded {control_type} requested", actor=actor,
        )
        if not claimed:
            return {**(await self._reconcile_claimed_control(control_key)), "runnerId": runner_id}
        if terminal and control_type == "terminal_cleanup":
            await self._record_control(
                row, control_key, control_type, "completed",
                summary="Terminal resources queued for janitor cleanup", actor=actor,
            )
            return {"ok": True, "status": "cleanup_scheduled", "runnerId": runner_id}
        if not terminal:
            await self._run_store.mark_embedded_runner_state(
                row.idempotency_key,
                state="draining",
                code="runner_stop_requested",
            )
        try:
            await self._host_channels.stop_runner(
                host_id=host_id, runner_id=runner_id
            )
        except (EmbeddedHostChannelError, TimeoutError) as exc:
            await self._record_control(
                row, control_key, control_type, "delivery_unknown",
                code="omnigent_embedded_control_delivery_unknown",
                summary=str(exc), actor=actor,
            )
            raise OmnigentBridgeError(
                str(exc), failure_class="integration_error", status_code=503,
                code="omnigent_embedded_control_delivery_unknown",
            ) from exc
        await self._record_control(
            row, control_key, control_type, "accepted",
            summary=f"Embedded host accepted {control_type}", actor=actor,
        )
        if not terminal:
            await self._run_store.mark_embedded_runner_state(
                row.idempotency_key,
                state="stopped",
                code="runner_stop_confirmed",
            )
            await self._run_store.record_lifecycle_event(
                row.idempotency_key,
                event_type="terminal",
                status="canceled",
                event_identity=f"embedded-stop:{runner_id}",
                summary="stopped by MoonMind control",
            )
        await self._record_control(
            row, control_key, control_type, "completed",
            summary=f"Embedded {control_type} completed", actor=actor,
        )
        return {"ok": True, "status": "stopped", "runnerId": runner_id}

    async def post_event(
        self, *, session_id: str, event: Any, actor: str | None = None
    ) -> dict[str, Any]:
        """Post a message through the exact durably bound runner tunnel."""

        self._require_embedded_mode()
        row = await self._run_store.get_session_by_provider_session_id(session_id)
        if row is None:
            raise OmnigentBridgeError(
                "No Omnigent bridge session is bound to the requested session id.",
                failure_class="user_error", status_code=404,
            )
        runner_id = _clean(row.omnigent_runner_id)
        if not runner_id:
            raise OmnigentBridgeError(
                "Embedded session has no durable runner assignment",
                failure_class="integration_error", status_code=409,
            )
        payload = event.model_dump(by_alias=True, exclude_none=True)
        supplied_key = _clean(payload.get("idempotencyKey"))
        control_key = supplied_key or f"message:{session_id}:{_stable_payload_digest(payload)}"
        await self._validate_control_expectations(
            row=row, payload=payload, control_key=control_key,
            control_type="message", actor=actor,
        )
        reconciled = await self._reconcile_control(row, control_key)
        if reconciled is not None:
            return reconciled
        claimed = await self._claim_control(
            row, control_key, "message", "requested",
            summary=_bounded_request_summary(payload), actor=actor,
        )
        if not claimed:
            return await self._reconcile_claimed_control(control_key)
        try:
            response = await self._host_channels.post_runner_event(
                runner_id=runner_id, session_id=session_id, payload=payload
            )
        except (EmbeddedHostChannelError, TimeoutError) as exc:
            await self._record_control(
                row, control_key, "message", "delivery_unknown",
                code="omnigent_embedded_control_delivery_unknown", summary=str(exc),
                actor=actor,
            )
            raise OmnigentBridgeError(
                str(exc), failure_class="integration_error", status_code=503,
                code="omnigent_embedded_control_delivery_unknown",
            ) from exc
        await self._record_control(
            row, control_key, "message", "accepted",
            summary="Embedded host accepted message", actor=actor,
        )
        await self._record_control(
            row, control_key, "message", "completed",
            summary="Embedded message delivered", actor=actor,
        )
        return response

    async def reconcile_first_message(self, *, session_id: str) -> dict[str, Any]:
        """Resolve response-before-persist from the runner's session evidence."""

        from moonmind.omnigent.execute import _snapshot_contains_first_message_marker

        row = await self._run_store.get_session_by_provider_session_id(session_id)
        if row is None or not _clean(row.omnigent_runner_id):
            raise OmnigentBridgeError(
                "Embedded first-message reconciliation requires a durable runner",
                failure_class="integration_error", status_code=409,
            )
        if row.first_message_state != "posting":
            return {"reconciled": row.first_message_state == "posted"}
        snapshot = await self._host_channels.request_runner(
            runner_id=row.omnigent_runner_id,
            method="GET",
            path=f"/v1/sessions/{quote(session_id, safe='')}",
        )
        if not _snapshot_contains_first_message_marker(
            snapshot,
            digest=str(row.first_message_digest or ""),
            marker=str(row.first_message_marker or ""),
        ):
            raise OmnigentBridgeError(
                "Runner session does not contain durable first-message evidence",
                failure_class="integration_error", status_code=503,
                code="omnigent_first_message_reconcile_failed",
            )
        response = snapshot.get("firstMessageResponse") or {}
        await self._run_store.mark_posted(row.idempotency_key, response=response)
        return {"reconciled": True, **response}

    async def get_resource(
        self, operation: str, session_id: str, value: str | None = None
    ) -> Any:
        """Read a bounded resource through the exact durably bound runner."""

        _, runner_id = await self._bound_runner(session_id)
        if value is not None:
            value = _safe_resource_identifier(value)
        routes = self._config.public_api.routes
        route_templates: dict[str, tuple[str, bool]] = {
            "changed_files": (routes.changed_files, True),
            "workspace_files": (routes.workspace_files, True),
            "workspace_file": (routes.workspace_file, False),
            "workspace_diff": (routes.workspace_diffs, False),
            "session_files": (routes.session_files, True),
            "session_file": (routes.session_file, False),
        }
        if operation not in route_templates:
            raise OmnigentBridgeError(
                f"Unknown embedded resource operation: {operation}",
                failure_class="user_error", status_code=404,
            )
        template, expect_json = route_templates[operation]
        path = template.replace("{path:path}", "{path}").format(
            session_id=quote(session_id, safe=""),
            path=quote(value or "", safe="/"),
            file_id=quote(value or "", safe=""),
        )
        try:
            result = await self._host_channels.request_runner(
                runner_id=runner_id,
                method="GET",
                path=path,
                expect_json=expect_json,
            )
        except (EmbeddedHostChannelError, TimeoutError) as exc:
            raise OmnigentBridgeError(
                str(exc), failure_class="integration_error", status_code=503
            ) from exc
        if isinstance(result, bytes):
            if len(result) > _MAX_FACADE_RESOURCE_BYTES:
                raise OmnigentBridgeError(
                    "Embedded runner resource exceeds the bridge response limit",
                    failure_class="integration_error", status_code=502,
                    code="omnigent_bridge_response_too_large",
                )
            return result
        bounded = _bound_resource_lists(result)
        try:
            encoded = json.dumps(bounded, separators=(",", ":")).encode()
        except (TypeError, ValueError, RecursionError) as exc:
            raise OmnigentBridgeError(
                "Embedded runner resource index is malformed",
                failure_class="integration_error",
                status_code=502,
                code="omnigent_bridge_upstream_payload",
            ) from exc
        if len(encoded) > _MAX_FACADE_RESOURCE_BYTES:
            raise OmnigentBridgeError(
                "Embedded runner resource index exceeds the bridge response limit",
                failure_class="integration_error", status_code=502,
                code="omnigent_bridge_response_too_large",
            )
        return bounded

    async def list_changed_files(self, session_id: str) -> Any:
        return await self.get_resource("changed_files", session_id)

    async def list_workspace_files(self, session_id: str) -> Any:
        return await self.get_resource("workspace_files", session_id)

    async def get_workspace_file(self, session_id: str, path: str) -> bytes:
        return await self.get_resource("workspace_file", session_id, path)

    async def get_workspace_diff(self, session_id: str, path: str) -> bytes:
        return await self.get_resource("workspace_diff", session_id, path)

    async def list_session_files(self, session_id: str) -> Any:
        return await self.get_resource("session_files", session_id)

    async def get_session_file_content(self, session_id: str, file_id: str) -> bytes:
        return await self.get_resource("session_file", session_id, file_id)

    async def harvest_session(
        self, session_id: str, *, payload: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Publish embedded resources through the canonical artifact contract."""
        row, _runner_id = await self._bound_runner(session_id)
        payload = payload or {}
        control_key = _clean(payload.get("idempotencyKey")) or f"harvest:{session_id}"
        await self._validate_control_expectations(
            row=row, payload=payload, control_key=control_key,
            control_type="harvest", actor=actor,
        )
        reconciled = await self._reconcile_control(row, control_key)
        if reconciled is not None:
            refreshed = await self._run_store.get_bridge_session(row.bridge_session_id)
            return {
                **reconciled,
                "captureManifestRef": refreshed.capture_manifest_ref,
                "resourceProjectionRef": (refreshed.terminal_refs or {}).get(
                    "resourceProjectionRef"
                ),
            }
        claimed = await self._claim_control(
            row, control_key, "harvest", "requested",
            summary="Embedded resource harvest requested", actor=actor,
        )
        if not claimed:
            return await self._reconcile_claimed_control(control_key)
        await self._record_control(
            row, control_key, "harvest", "accepted",
            summary="Embedded resource harvest accepted", actor=actor,
        )
        request = _request_for_row(row)
        async def write_required_json(**kwargs: Any) -> str:
            try:
                return await self._artifact_gateway.write_json(**kwargs)
            except Exception as exc:
                await self._record_control(
                    row, control_key, "harvest", "failed",
                    summary="Embedded required evidence persistence failed",
                    code="omnigent_embedded_required_evidence_unavailable",
                    actor=actor,
                )
                raise OmnigentBridgeError(
                    "Unable to persist required embedded harvest evidence",
                    failure_class="system_error", status_code=500,
                    code="omnigent_embedded_required_evidence_unavailable",
                ) from exc

        refs: dict[str, str] = {
            key: value
            for key, value in {
                "rawSseStreamRef": row.raw_events_ref,
                "normalizedEventStreamRef": row.normalized_events_ref,
                "initialSnapshotRef": row.initial_snapshot_ref,
            }.items()
            if value
        }
        # Embedded harvest owns the same durable, MoonMind-readable envelope as
        # proxy capture.  These documents are derived from the compact durable
        # projection, never from provider-native paths or live access tokens.
        durable_snapshot = await self.get_session(session_id)
        if "initialSnapshotRef" not in refs:
            refs["initialSnapshotRef"] = await write_required_json(
                request=request,
                name="runtime.omnigent.initial_snapshot.json",
                payload={**durable_snapshot, "capturePhase": "initial"},
                link_type="runtime.omnigent.initial_snapshot",
            )
        refs["finalSnapshotRef"] = await write_required_json(
            request=request,
            name="output.omnigent.final_snapshot.json",
            payload={**durable_snapshot, "capturePhase": "final"},
            link_type="output.omnigent.final_snapshot",
        )
        durable_events = await self._run_store.list_events(row.bridge_session_id)
        bounded_log = [
            {
                "sequence": event.sequence,
                "type": event.event_type,
                "status": event.normalized_status,
                "preview": event.text_preview,
            }
            for event in durable_events[-100:]
        ]
        refs["runnerLogRef"] = await write_required_json(
            request=request,
            name="runtime.omnigent.embedded.runner_log.json",
            payload={"sourceMode": HOST_PROTOCOL_MODE_EMBEDDED, "events": bounded_log},
            link_type="runtime.omnigent.runner_log",
        )
        refs["diagnosticsRef"] = await write_required_json(
            request=request,
            name="diagnostics.omnigent.embedded.json",
            payload={
                "sourceMode": HOST_PROTOCOL_MODE_EMBEDDED,
                "sessionStatus": durable_snapshot.get("status"),
                "terminal": durable_snapshot.get("terminal"),
                "eventCount": len(durable_events),
                "evidenceClassification": {
                    "required": ["initialSnapshotRef", "finalSnapshotRef", "diagnosticsRef"],
                    "optionalNotApplicable": [
                        "childSessionEvidenceRef",
                        "externalStateRef",
                        "stateCheckpointRef",
                    ],
                },
            },
            link_type="diagnostics.omnigent",
        )
        manifest: dict[str, Any] = {
            "schemaVersion": "moonmind.omnigent.capture_manifest.v1",
            "sourceIssue": "MoonLadderStudios/MoonMind#3424",
            "provider": "omnigent",
            "sourceMode": HOST_PROTOCOL_MODE_EMBEDDED,
            "evidenceCompleteness": "complete",
            "artifactRefs": refs,
            "evidenceClassification": {
                "required": sorted(refs),
                "optionalNotApplicable": [
                    "childSessionEvidenceRef",
                    "externalStateRef",
                    "stateCheckpointRef",
                ],
            },
        }
        harvester = BridgeResourceHarvester(
            client=self,
            artifact_gateway=self._artifact_gateway,
            request=request,
            session_id=session_id,
            manifest=manifest,
            refs=refs,
        )
        try:
            await harvester.harvest_resources(capture_policy=None)
        except Exception as exc:
            await self._record_control(
                row, control_key, "harvest", "failed",
                summary="Embedded resource harvest failed",
                code="omnigent_embedded_required_evidence_unavailable",
                actor=actor,
            )
            raise OmnigentBridgeError(
                "Unable to persist required embedded harvest evidence",
                failure_class="system_error", status_code=500,
                code="omnigent_embedded_required_evidence_unavailable",
            ) from exc
        unavailable = sorted(key for key in manifest if key.endswith("Unavailable"))
        item_unavailable = any(
            isinstance(item, dict) and item.get("unavailable")
            for group in ("changedFiles", "workspaceFiles", "sessionFiles")
            for item in (manifest.get(group) or [])
        )
        if unavailable or item_unavailable:
            manifest["evidenceCompleteness"] = "optional_degradation"
            if unavailable:
                manifest["optionalEvidenceUnavailable"] = unavailable
        projection = {
            "schemaVersion": "moonmind.omnigent.resource_projection.v1",
            "sourceMode": HOST_PROTOCOL_MODE_EMBEDDED,
            "resources": {
                key: value for key, value in manifest.items()
                if key in {"changedFiles", "workspaceFiles", "workspaceDiffs", "sessionFiles"}
            },
            "artifactRefs": refs,
            "evidenceCompleteness": manifest["evidenceCompleteness"],
        }
        try:
            projection_ref = await self._artifact_gateway.write_json(
                request=request,
                name="output.omnigent.resource_projection.json",
                payload=projection,
                link_type="output.omnigent.resource_projection",
            )
            refs["resourceProjectionRef"] = projection_ref
            manifest_ref = await self._artifact_gateway.write_json(
                request=request,
                name="output.omnigent.capture_manifest.json",
                payload=manifest,
                link_type="output.omnigent.capture_manifest",
            )
        except Exception as exc:
            await self._record_control(
                row, control_key, "harvest", "failed",
                summary="Embedded harvest manifest persistence failed",
                code="omnigent_embedded_required_evidence_unavailable",
                actor=actor,
            )
            raise OmnigentBridgeError(
                "Unable to persist required embedded harvest evidence",
                failure_class="system_error", status_code=500,
                code="omnigent_embedded_required_evidence_unavailable",
            ) from exc
        await self._run_store.attach_capture_evidence(
            row.bridge_session_id,
            capture_manifest_ref=manifest_ref,
            resource_projection_ref=projection_ref,
            evidence_completeness=manifest["evidenceCompleteness"],
        )
        await self._run_store.record_resource_harvest_completed(session_id)
        await self._run_store.record_lifecycle_event(
            row.idempotency_key,
            event_type="resource_association",
            status="completed",
            event_identity=f"embedded-resource-association:{control_key}",
            summary="Embedded resource evidence associated",
            diagnostics_ref=manifest_ref,
            metadata={
                "sourceMode": HOST_PROTOCOL_MODE_EMBEDDED,
                "controlType": "harvest",
                "controlKey": control_key,
                "captureManifestRef": manifest_ref,
                "resourceProjectionRef": projection_ref,
                "evidenceCompleteness": manifest["evidenceCompleteness"],
            },
        )
        await self._record_control(
            row, control_key, "harvest", "completed",
            summary="Embedded resource evidence published",
            code=None,
            audit_ref=manifest_ref, actor=actor,
        )
        return {
            "ok": True,
            "status": (
                "completed_with_diagnostics"
                if unavailable or item_unavailable else "completed"
            ),
            "captureManifestRef": manifest_ref,
            "resourceProjectionRef": projection_ref,
        }

    async def resolve_elicitation(
        self, *, session_id: str, elicitation_id: str, payload: dict[str, Any],
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Resolve an elicitation through the exact durably bound runner."""

        row, runner_id = await self._bound_runner(session_id)
        control_key = _clean(payload.get("idempotencyKey")) or (
            f"elicitation:{session_id}:{elicitation_id}"
        )
        await self._validate_control_expectations(
            row=row, payload=payload, control_key=control_key,
            control_type="elicitation", actor=actor,
        )
        reconciled = await self._reconcile_control(row, control_key)
        if reconciled is not None:
            return reconciled
        claimed = await self._claim_control(
            row, control_key, "elicitation", "requested",
            summary=f"Resolve elicitation {_safe_resource_identifier(elicitation_id)}",
            control_id=elicitation_id, actor=actor,
        )
        if not claimed:
            return await self._reconcile_claimed_control(control_key)
        path = self._config.public_api.routes.resolve_elicitation.format(
            session_id=quote(session_id, safe=""),
            elicitation_id=quote(_safe_resource_identifier(elicitation_id), safe=""),
        )
        try:
            response = await self._host_channels.request_runner(
                runner_id=runner_id,
                method="POST",
                path=path,
                payload=payload,
                expect_json=True,
            )
        except (EmbeddedHostChannelError, TimeoutError) as exc:
            await self._record_control(
                row, control_key, "elicitation", "delivery_unknown",
                code="omnigent_embedded_control_delivery_unknown", summary=str(exc),
                control_id=elicitation_id, actor=actor,
            )
            raise OmnigentBridgeError(
                str(exc), failure_class="integration_error", status_code=503,
                code="omnigent_embedded_control_delivery_unknown",
            ) from exc
        await self._record_control(
            row, control_key, "elicitation", "accepted",
            summary="Embedded elicitation resolution accepted", control_id=elicitation_id,
            actor=actor,
        )
        await self._record_control(
            row, control_key, "elicitation", "completed",
            summary="Embedded elicitation resolved", control_id=elicitation_id,
            actor=actor,
        )
        return response

    async def _control_already_requested(self, row: Any, control_key: str) -> bool:
        events = await self._run_store.list_events(row.bridge_session_id)
        identity = f"embedded-control:{control_key}:requested"
        return any(
            (event.metadata_ or {}).get("eventIdentity") == identity
            for event in events
        )

    async def _reconcile_control(
        self, row: Any, control_key: str
    ) -> dict[str, Any] | None:
        """Return the durable outcome for a retried logical control."""
        events = await self._run_store.list_events(row.bridge_session_id)
        prefix = f"embedded-control:{control_key}:"
        outcomes: dict[str, Any] = {}
        for event in events:
            metadata = event.metadata_ or {}
            if not str(metadata.get("eventIdentity") or "").startswith(prefix):
                continue
            details = metadata.get("metadata")
            outcome = str(
                (details.get("controlOutcome") if isinstance(details, dict) else None)
                or metadata.get("controlOutcome")
                or ""
            )
            outcomes[outcome] = event
        for outcome, ok in (("completed", True), ("failed", False),
                            ("rejected", False), ("delivery_unknown", False)):
            if outcome in outcomes:
                return {
                    "ok": ok,
                    "status": outcome,
                    "idempotencyKey": control_key,
                    "reconciled": True,
                }
        if "requested" in outcomes or "accepted" in outcomes:
            return {
                "ok": False,
                "status": "delivery_unknown",
                "idempotencyKey": control_key,
                "reconciled": True,
            }
        return None

    async def _validate_control_expectations(
        self, *, row: Any, payload: dict[str, Any], control_key: str,
        control_type: str, actor: str | None = None,
    ) -> None:
        if len(control_key) > MAX_EMBEDDED_CONTROL_KEY_LENGTH:
            raise OmnigentBridgeError(
                "Embedded control idempotency key exceeds the supported length",
                failure_class="user_error", status_code=422,
                code="omnigent_embedded_control_key_too_long",
            )
        expected = {
            "expectedWorkflowId": row.moonmind_workflow_id,
            "expectedRunId": row.moonmind_run_id,
            "expectedStepExecutionId": row.step_execution_id,
            "expectedAgentRunId": row.moonmind_agent_run_id,
            "expectedBridgeSessionId": row.bridge_session_id,
            "expectedSessionId": row.omnigent_session_id,
            "expectedHostId": row.omnigent_host_id,
            "expectedRunnerId": row.omnigent_runner_id,
            "expectedTurnState": row.first_message_state,
            "expectedTerminalState": row.status,
        }
        missing = next(
            (field for field, actual in expected.items()
             if _clean(actual) and not _clean(payload.get(field))),
            None,
        )
        if control_type in {"stop", "terminal_cleanup"} and missing is not None:
            await self._record_control(
                row, control_key, control_type, "rejected",
                code="omnigent_embedded_control_expected_state_required",
                summary=f"Embedded control rejected: {missing} is required",
                actor=actor,
            )
            raise OmnigentBridgeError(
                "Embedded control requires the complete durable expected state",
                failure_class="user_error", status_code=422,
                code="omnigent_embedded_control_expected_state_required",
            )
        mismatch = next(
            (
                (field, _clean(payload.get(field)), _clean(actual))
                for field, actual in expected.items()
                if payload.get(field) is not None
                and _clean(payload.get(field)) != _clean(actual)
            ),
            None,
        )
        if mismatch is None:
            return
        field, _requested, _actual = mismatch
        await self._record_control(
            row, control_key, control_type, "rejected",
            code="omnigent_embedded_control_state_mismatch",
            summary=f"Embedded control rejected: {field} mismatch", actor=actor,
        )
        raise OmnigentBridgeError(
            "Embedded control expected state does not match the durable binding",
            failure_class="user_error", status_code=409,
            code="omnigent_embedded_control_state_mismatch",
        )

    async def _record_control(
        self, row: Any, control_key: str, control_type: str, outcome: str, *,
        summary: str, code: str | None = None, control_id: str | None = None,
        audit_ref: str | None = None, actor: str | None = None,
    ) -> None:
        await self._run_store.record_lifecycle_event(
            row.idempotency_key,
            event_type="control",
            status="running",
            event_identity=f"embedded-control:{control_key}:{outcome}",
            code=code,
            summary=summary,
            diagnostics_ref=audit_ref,
            metadata=self._control_metadata(
                row, control_key, control_type, outcome, control_id=control_id,
                actor=actor,
            ),
        )

    async def _claim_control(
        self, row: Any, control_key: str, control_type: str, outcome: str, *,
        summary: str, control_id: str | None = None, actor: str | None = None,
    ) -> bool:
        return await self._run_store.claim_lifecycle_event(
            row.idempotency_key,
            event_type="control",
            event_identity=f"embedded-control:{control_key}:{outcome}",
            summary=summary,
            metadata=self._control_metadata(
                row, control_key, control_type, outcome, control_id=control_id,
                actor=actor,
            ),
        )

    async def _reconcile_claimed_control(self, control_key: str) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "delivery_unknown",
            "idempotencyKey": control_key,
            "reconciled": True,
        }

    @staticmethod
    def _control_metadata(
        row: Any, control_key: str, control_type: str, outcome: str, *,
        control_id: str | None = None, actor: str | None = None,
    ) -> dict[str, Any]:
        return {
            "actor": actor or "moonmind_system",
            "controlType": control_type,
            "controlOutcome": outcome,
            "controlId": control_id,
            "controlIdempotencyKey": control_key,
            "expectedSessionId": row.omnigent_session_id,
            "expectedWorkflowId": row.moonmind_workflow_id,
            "expectedRunId": row.moonmind_run_id,
            "expectedStepExecutionId": row.step_execution_id,
            "expectedAgentRunId": row.moonmind_agent_run_id,
            "expectedBridgeSessionId": row.bridge_session_id,
            "expectedHostId": row.omnigent_host_id,
            "expectedRunnerId": row.omnigent_runner_id,
            "expectedTurnState": row.first_message_state,
            "expectedTerminalState": row.status,
            "sourceMode": HOST_PROTOCOL_MODE_EMBEDDED,
        }

    async def _bound_runner(self, session_id: str) -> tuple[Any, str]:
        self._require_embedded_mode()
        row = await self._run_store.get_session_by_provider_session_id(session_id)
        if row is None:
            raise OmnigentBridgeError(
                "No Omnigent bridge session is bound to the requested session id.",
                failure_class="user_error", status_code=404,
            )
        runner_id = _clean(row.omnigent_runner_id)
        if not runner_id or not _clean(row.omnigent_host_id):
            raise OmnigentBridgeError(
                "Embedded session has no durable host/runner assignment",
                failure_class="integration_error", status_code=409,
            )
        return row, runner_id

    async def get_session_owner(self, session_id: str):
        """Return the durable MoonMind owner for public control authorization."""

        return await self._run_store.get_session_owner(session_id)

    async def list_hosts(self) -> list[dict[str, Any]]:
        """Expose bounded readiness without lease, profile, or credential data."""
        self._require_embedded_mode()
        hosts = await self._run_store.list_embedded_host_readiness()
        if not hosts:
            raise OmnigentBridgeError(
                "No compatible embedded Omnigent host capability is available",
                failure_class="integration_error",
                status_code=503,
                code="omnigent_bridge_capability_unavailable",
            )
        return hosts

    async def list_agents(self) -> list[dict[str, Any]]:
        """Derive the pinned Codex-first catalog from registered host evidence."""
        hosts = await self.list_hosts()
        compatible = [
            host
            for host in hosts
            if host.get("ready") and _supports_codex(host.get("capabilities"))
        ]
        if not compatible:
            raise OmnigentBridgeError(
                "No ready embedded host advertises the supported Codex harness",
                failure_class="integration_error",
                status_code=503,
                code="omnigent_bridge_capability_unavailable",
            )
        return [
            {
                "id": "codex-native",
                "name": "Codex",
                "harness": "codex-native",
                "ready": True,
                "capabilities": {"embedded": True},
            }
        ]

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """Build an Omnigent-shaped snapshot exclusively from durable evidence."""
        self._require_embedded_mode()
        row = await self._run_store.get_session_by_provider_session_id(session_id)
        if row is None:
            raise OmnigentBridgeError(
                "No Omnigent bridge session is bound to the requested session id.",
                failure_class="user_error", status_code=404,
                code="omnigent_bridge_session_unknown",
            )
        terminal = row.status in {"completed", "failed", "canceled", "timed_out"}
        hosts = await self._run_store.list_embedded_host_readiness()
        live_host = next(
            (host for host in hosts if host.get("id") == row.omnigent_host_id), None
        )
        disconnected = bool(row.omnigent_host_id) and (
            live_host is None or bool(live_host.get("disconnected"))
        )
        capabilities = _embedded_control_capabilities(
            terminal=terminal,
            disconnected=disconnected,
            host_capabilities=(live_host or {}).get("capabilities"),
        )
        return {
            "id": session_id,
            "status": row.status,
            "agentId": row.omnigent_agent_id or "codex-native",
            "hostId": row.omnigent_host_id,
            "runnerId": row.omnigent_runner_id,
            "firstMessageState": row.first_message_state,
            "capabilities": capabilities,
            "terminal": terminal,
            "terminalEvidenceAvailable": bool(
                row.terminal_refs or row.diagnostics_ref or row.final_snapshot_ref
            ),
            "disconnected": disconnected,
            "degraded": disconnected and not terminal,
            "summary": (
                str((row.terminal_refs or {}).get("summary") or "")[:2000] or None
            ),
            "diagnosticsRef": row.diagnostics_ref,
            "moonmind": {
                "workflowId": row.moonmind_workflow_id,
                "agentRunId": row.moonmind_agent_run_id,
                "idempotencyKey": row.idempotency_key,
                "bridgeSessionId": row.bridge_session_id,
                "bridgeLocal": True,
                "hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED,
                "protocolProfile": (
                    self._config.host_connection.embedded.protocol_profile
                ),
            },
        }

    async def stop_session(
        self, session_id: str, *, payload: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Stop the exact bound runner through the common facade contract."""

        if payload is None:
            row = await self._run_store.get_session_by_provider_session_id(session_id)
            if row is not None:
                payload = {
                    "idempotencyKey": (
                        f"internal-stop:{row.bridge_session_id}:"
                        f"{row.omnigent_runner_id}"
                    ),
                    "expectedWorkflowId": row.moonmind_workflow_id,
                    "expectedRunId": row.moonmind_run_id,
                    "expectedStepExecutionId": row.step_execution_id,
                    "expectedAgentRunId": row.moonmind_agent_run_id,
                    "expectedBridgeSessionId": row.bridge_session_id,
                    "expectedSessionId": row.omnigent_session_id,
                    "expectedHostId": row.omnigent_host_id,
                    "expectedRunnerId": row.omnigent_runner_id,
                    "expectedTurnState": row.first_message_state,
                    "expectedTerminalState": row.status,
                }
        return await self.stop_runner(session_id=session_id, payload=payload, actor=actor)

    async def cleanup_session(
        self, session_id: str, *, payload: dict[str, Any],
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Terminate owned live resources through the durable control ledger."""

        return await self.stop_runner(
            session_id=session_id, payload=payload, actor=actor,
            control_type="terminal_cleanup",
        )

    async def attach_session(
        self, *, session_id: str, binding: BridgePrincipalBinding
    ) -> dict[str, Any]:
        """Reconcile only the exact already-bound embedded session."""
        row = await self._run_store.get_existing(binding.idempotency_key)
        if row is None:
            raise OmnigentBridgeError(
                "No durable MoonMind binding exists for this idempotency key",
                failure_class="user_error", status_code=404,
                code="omnigent_bridge_session_unknown",
            )
        _assert_row_owner(row, binding)
        if _clean(row.omnigent_session_id) != _clean(session_id):
            raise OmnigentBridgeError(
                "The durable binding is attached to another embedded session",
                failure_class="user_error", status_code=409,
                code="omnigent_bridge_session_conflict",
            )
        return await self.get_session(session_id)

    async def delete_session(self, session_id: str) -> dict[str, Any]:
        """Reject provider-style deletion while preserving durable evidence."""
        await self.get_session(session_id)
        raise OmnigentBridgeError(
            "Embedded session deletion is unavailable because MoonMind retains "
            "bridge evidence",
            failure_class="user_error", status_code=409,
            code="omnigent_bridge_capability_unavailable",
        )

    async def stream_events(self, session_id: str, *, after: int = 0):
        """Replay committed canonical events and terminal state by durable sequence."""
        row = await self._run_store.get_session_by_provider_session_id(session_id)
        if row is None:
            raise OmnigentBridgeError(
                "No Omnigent bridge session is bound to the requested session id.",
                failure_class="user_error", status_code=404,
                code="omnigent_bridge_session_unknown",
            )
        cursor = max(after, 0)
        while True:
            page = await self._run_store.list_event_page(
                row.bridge_session_id, after=cursor, limit=500
            )
            for event in page.rows:
                cursor = event.sequence
                yield {
                    "schemaVersion": "moonmind.omnigent_bridge.event.v1",
                    "id": event.event_id,
                    "sequence": event.sequence,
                    "type": event.event_type,
                    "timestamp": event.timestamp.isoformat(),
                    "status": event.normalized_status,
                    "text": event.text_preview,
                    "artifactRef": event.artifact_ref,
                    "metadata": dict(event.metadata_ or {}),
                }
            refreshed = await self._run_store.get_bridge_session(row.bridge_session_id)
            if refreshed and not page.has_more and refreshed.status in {
                "completed", "failed", "canceled", "timed_out"
            }:
                yield {
                    "schemaVersion": "moonmind.omnigent_bridge.event.v1",
                    "sequence": cursor,
                    "type": "terminal",
                    "status": refreshed.status,
                    "terminal": True,
                    "evidenceAvailable": bool(
                        refreshed.terminal_refs
                        or refreshed.diagnostics_ref
                        or refreshed.final_snapshot_ref
                    ),
                }
                return
            await asyncio.sleep(1.0)

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
        authorized = await self._run_store.get_existing(binding.idempotency_key)
        if authorized is None or not all(
            (
                authorized.provider_profile_id,
                authorized.provider_lease_id,
                authorized.host_binding_ref,
                authorized.host_lease_ref,
                authorized.omnigent_host_id,
            )
        ):
            raise OmnigentBridgeError(
                "Embedded session creation requires a durable profile lease-bound "
                "host assignment",
                failure_class="system_error",
                status_code=409,
            )
        requested_host_id = _clean(request.host_id)
        if requested_host_id and requested_host_id != authorized.omnigent_host_id:
            raise OmnigentBridgeError(
                "Caller-provided host does not match the profile lease-bound host",
                failure_class="user_error",
                status_code=403,
            )
        row = await self._run_store.get_or_create(
            request=exec_request,
            endpoint_ref=(request.endpoint_ref or "").strip() or "embedded",
            agent_id=(request.agent_id or "").strip() or None,
            agent_name=None,
            target_metadata={
                "hostType": request.host_type,
                "hostId": authorized.omnigent_host_id,
                "workspace": (request.workspace or "").strip() or None,
                "hostProtocolMode": self._config.host_protocol_mode,
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
        capabilities = _embedded_control_capabilities(
            terminal=False, disconnected=False,
            host_capabilities=(authorized.metadata_ or {}).get("capabilities"),
        )
        await self._run_store.record_session_created(
            binding.idempotency_key,
            session_id=session_id,
            agent_id=(request.agent_id or "").strip() or None,
            endpoint_ref=(request.endpoint_ref or "").strip() or "embedded",
            capabilities=capabilities,
        )
        return {
            "id": session_id,
            "status": row.status,
            "capabilities": capabilities,
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
        host_id = _clean(request.host_id) or auth.runner_id
        runner_id = _clean(request.runner_id) or auth.runner_id
        if runner_id != auth.runner_id or host_id != auth.runner_id:
            raise OmnigentBridgeError(
                "Authenticated runner or host identity does not match registration",
                failure_class="user_error",
                status_code=403,
            )
        try:
            await self._run_store.record_embedded_host_lifecycle(
                host_id=host_id,
                credential_generation=auth.credential_generation,
                credential_profile_id=auth.credential_profile_id,
                capabilities=request.capabilities,
                readiness="registered",
            )
        except OmnigentIdempotencyError as exc:
            raise OmnigentBridgeError(
                str(exc), failure_class="user_error", status_code=403
            ) from exc
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
        if _clean(host_id) != auth.runner_id:
            raise OmnigentBridgeError(
                "Authenticated runner identity does not match host binding",
                failure_class="user_error",
                status_code=403,
            )
        try:
            await self._run_store.record_embedded_host_lifecycle(
                host_id=_clean(host_id),
                credential_generation=auth.credential_generation,
                credential_profile_id=auth.credential_profile_id,
                capabilities=request.capabilities,
                readiness=request.status,
            )
        except OmnigentIdempotencyError as exc:
            raise OmnigentBridgeError(
                str(exc), failure_class="user_error", status_code=403
            ) from exc
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

    async def disconnect_host(
        self, *, host_id: str, auth: EmbeddedHostAuthContext
    ) -> None:
        """Durably mark the exact authenticated host connection disconnected."""
        if _clean(host_id) != auth.runner_id:
            raise OmnigentBridgeError(
                "Authenticated runner identity does not match host binding",
                failure_class="user_error",
                status_code=403,
            )
        try:
            await self._run_store.record_embedded_host_lifecycle(
                host_id=_clean(host_id),
                credential_generation=auth.credential_generation,
                credential_profile_id=auth.credential_profile_id,
                readiness="disconnected",
                disconnected=True,
            )
        except OmnigentIdempotencyError as exc:
            raise OmnigentBridgeError(
                str(exc), failure_class="user_error", status_code=403
            ) from exc

    async def ingest_session_event(
        self,
        *,
        host_id: str,
        session_id: str,
        request: EmbeddedHostSessionEventRequest,
        auth: EmbeddedHostAuthContext,
    ) -> dict[str, Any]:
        self._require_embedded_mode()
        if _clean(host_id) != auth.runner_id:
            raise OmnigentBridgeError(
                "Authenticated runner identity does not match session binding",
                failure_class="user_error",
                status_code=403,
            )
        row = await self._run_store.get_session_by_provider_session_id(session_id)
        if row is None:
            raise OmnigentBridgeError(
                "No Omnigent bridge session is bound to the requested session id.",
                failure_class="user_error",
                status_code=404,
            )
        if not row.omnigent_host_id or row.omnigent_host_id != auth.runner_id:
            raise OmnigentBridgeError(
                "Authenticated host is not the durable host assigned to this session",
                failure_class="user_error",
                status_code=403,
            )
        if auth.credential_profile_id is not None:
            try:
                await self._run_store.record_embedded_host_lifecycle(
                    host_id=auth.runner_id,
                    credential_generation=auth.credential_generation,
                    credential_profile_id=auth.credential_profile_id,
                )
            except OmnigentIdempotencyError as exc:
                raise OmnigentBridgeError(
                    str(exc), failure_class="user_error", status_code=403,
                    code="host_auth_lease_mismatch",
                ) from exc
        payload = request.model_dump(by_alias=True)
        payload.setdefault("direction", "host_to_moonmind")
        payload.setdefault("data", {})
        if isinstance(payload["data"], dict):
            payload["data"].setdefault("hostId", _clean(host_id))
        existing_events = await self._run_store.list_events(row.bridge_session_id)
        next_sequence = max((event.sequence for event in existing_events), default=0) + 1
        try:
            normalized = build_omnigent_bridge_event(
                payload=payload,
                sequence=next_sequence,
                request=_request_for_row(row),
                omnigent_session_id=session_id,
                bridge_session_id=row.bridge_session_id,
            ).event
        except (OmnigentContractError, ValueError) as exc:
            raise OmnigentBridgeError(
                str(exc), failure_class="integration_error", status_code=502
            ) from exc
        lock = self._event_journal_locks.setdefault(
            row.bridge_session_id, asyncio.Lock()
        )
        async with lock:
            row = await self._run_store.get_bridge_session(row.bridge_session_id)
            existing_events = await self._run_store.list_events(row.bridge_session_id)
            next_sequence = max(
                (event.sequence for event in existing_events), default=0
            ) + 1
            normalized["sequence"] = next_sequence
            _raw_ref, normalized_ref = await self._publish_embedded_journals(
                row=row, payload=payload, normalized=normalized,
                sequence=next_sequence,
            )
            normalized["artifactRef"] = normalized_ref
            rows = await self._run_store.append_events(
                row.bridge_session_id, [normalized]
            )
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

    async def _publish_embedded_journals(
        self, *, row: Any, payload: dict[str, Any], normalized: dict[str, Any],
        sequence: int,
    ) -> tuple[str, str]:
        request = _request_for_row(row)
        raw_history = await _read_jsonl(self._artifact_gateway, row.raw_events_ref)
        normalized_history = await _read_jsonl(
            self._artifact_gateway, row.normalized_events_ref
        )
        raw_history.extend(redact_raw_events([payload]))
        normalized_history.extend(redact_raw_events([normalized]))
        raw_ref = await self._artifact_gateway.write_text(
            request=request,
            name=f"runtime.omnigent.embedded.sse.raw/{sequence}.jsonl",
            payload=_jsonl(raw_history),
            link_type="runtime.omnigent.sse.raw",
            content_type="application/x-ndjson",
        )
        normalized_ref = await self._artifact_gateway.write_text(
            request=request,
            name=f"runtime.omnigent.embedded.sse.normalized/{sequence}.jsonl",
            payload=_jsonl(normalized_history),
            link_type="runtime.omnigent.sse.normalized",
            content_type="application/x-ndjson",
        )
        await self._run_store.attach_active_journal_refs(
            row.bridge_session_id, raw_ref=raw_ref, normalized_ref=normalized_ref
        )
        return raw_ref, normalized_ref

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

def _request_for_row(row: Any) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId=str(row.moonmind_workflow_id or row.idempotency_key),
        idempotencyKey=str(row.idempotency_key),
    )


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _embedded_control_capabilities(
    *, terminal: bool, disconnected: bool, host_capabilities: Any
) -> dict[str, bool]:
    live = not terminal and not disconnected
    return {
        "sendMessage": live,
        "resolveElicitation": live,
        "stop": live,
        # The pinned embedded router has no interrupt operation. Capability
        # projection and routing must share that contract even if a future host
        # sends an unrecognized interrupt capability bit.
        "interrupt": False,
        "harvest": live,
        "clearSession": False,
        "newSession": True,
        "terminalCleanup": terminal,
    }


def _stable_payload_digest(payload: dict[str, Any]) -> str:
    import hashlib
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _bounded_request_summary(payload: dict[str, Any]) -> str:
    event_type = _clean(payload.get("type"))[:96] or "message"
    data = payload.get("data")
    text = _clean(data.get("text")) if isinstance(data, dict) else ""
    return f"{event_type}: {text[:256]}" if text else event_type


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)


async def _read_jsonl(
    gateway: OmnigentArtifactGateway, artifact_ref: str | None
) -> list[dict[str, Any]]:
    if not artifact_ref:
        return []
    try:
        content = await gateway.read_text(artifact_ref)
    except Exception as exc:
        raise OmnigentBridgeError(
            "Unable to extend embedded event journal",
            failure_class="system_error", status_code=500,
            code="omnigent_embedded_required_evidence_unavailable",
        ) from exc
    rows: list[dict[str, Any]] = []
    try:
        for line in content.splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    except (TypeError, ValueError) as exc:
        raise OmnigentBridgeError(
            "Embedded event journal is malformed",
            failure_class="system_error", status_code=500,
            code="omnigent_embedded_required_evidence_unavailable",
        ) from exc
    return rows


def _supports_codex(capabilities: Any) -> bool:
    if not isinstance(capabilities, dict):
        return False
    values = capabilities.get("harnesses") or capabilities.get("agents") or []
    if isinstance(values, str):
        values = [values]
    return any(
        str(value).strip().lower() in {"codex", "codex-native"}
        for value in values
    )


__all__ = [
    "EmbeddedHostAuthContext",
    "EmbeddedHostHeartbeatRequest",
    "EmbeddedHostRegisterRequest",
    "EmbeddedHostSessionEventRequest",
    "MAX_EMBEDDED_CAPABILITIES",
    "MAX_EMBEDDED_CAPABILITY_BYTES",
    "MAX_EMBEDDED_EVENT_BYTES",
    "MAX_EMBEDDED_EVENT_ENTRIES",
    "OmnigentEmbeddedHostProtocolFacade",
    "verify_embedded_host_auth",
]
