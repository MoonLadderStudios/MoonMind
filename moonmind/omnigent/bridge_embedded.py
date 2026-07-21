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
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, field_validator

from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    OmnigentBridgeConfig,
)
from moonmind.omnigent.bridge_artifacts import (
    LocalOmnigentArtifactGateway,
    OmnigentArtifactGateway,
    OmnigentContractError,
    _build_capture_bundle,
    build_omnigent_terminal_refs,
)
from moonmind.omnigent.bridge_security import redact_raw_events
from moonmind.omnigent.bridge_events import build_omnigent_bridge_event
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


def _embedded_intervention_capabilities(
    host_capabilities: Mapping[str, Any], *, session_status: str = "running",
    runner_bound: bool = True,
) -> dict[str, bool]:
    """Project pinned host capabilities onto the runtime-neutral UI contract."""

    live = session_status not in {"completed", "failed", "canceled", "timed_out"}
    controllable = live and runner_bound
    return {
        # These operations are part of the pinned embedded runner tunnel.
        "sendFollowUp": controllable,
        "clearSession": False,
        "interruptTurn": False,
        "cancelSession": False,
        "resolveElicitation": controllable,
        "harvestResources": controllable,
        "newSession": False,
        # Cleanup is a durable handoff to the lease janitor after terminal
        # evidence exists; it does not require the runner to remain reachable.
        "terminalCleanup": not live,
    }


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


class _EmbeddedCaptureClient:
    """Adapt pinned embedded resource routes to the shared capture pipeline."""

    def __init__(self, facade: "OmnigentEmbeddedHostProtocolFacade", session_id: str):
        self._facade = facade
        self._session_id = session_id

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return {"id": session_id, "status": "captured"}

    async def list_changed_files(self, session_id: str) -> Any:
        return await self._facade.get_resource("changed_files", session_id)

    async def list_workspace_files(self, session_id: str) -> Any:
        return await self._facade.get_resource("workspace_files", session_id)

    async def get_workspace_file(self, session_id: str, path: str) -> bytes:
        return await self._facade.get_resource("workspace_file", session_id, path)

    async def get_workspace_diff(self, session_id: str, path: str) -> bytes:
        return await self._facade.get_resource("workspace_diff", session_id, path)

    async def list_session_files(self, session_id: str) -> Any:
        return await self._facade.get_resource("session_files", session_id)

    async def get_session_file_content(self, session_id: str, file_id: str) -> bytes:
        return await self._facade.get_resource("session_file", session_id, file_id)


def verify_embedded_host_auth(
    *,
    headers: Mapping[str, Any],
    config: OmnigentBridgeConfig,
    configured_token: str,
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
    try:
        identity = OmnigentHostAuthAdapter(
            allowed_tokens=frozenset({str(configured_token or "").strip()})
            if str(configured_token or "").strip()
            else frozenset()
        ).verify(headers)
    except UpstreamHostAuthError as exc:
        unavailable = "configured" in str(exc) or "entrypoint" in str(exc)
        raise OmnigentBridgeError(
            str(exc),
            failure_class="system_error" if unavailable else "user_error",
            status_code=503 if unavailable else 401,
        ) from exc
    return EmbeddedHostAuthContext(
        auth_mode=auth_mode,
        protocol_profile=identity.protocol_profile,
        runner_id=identity.runner_id,
        credential_generation=1,
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
        self._runner_binding_root_secret = runner_binding_root_secret
        self._journal_locks: dict[str, asyncio.Lock] = {}

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

    async def stop_runner(self, *, session_id: str, actor: str = "authenticated_session_owner",
                          expected_state: Mapping[str, Any] | None = None,
                          idempotency_key: str | None = None) -> dict[str, Any]:
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
        control_key = idempotency_key or self._control_key("stop", session_id, {})
        await self._validate_expected_state(row, "stop", control_key, expected_state, actor)
        prior_outcome = await self._control_outcome(row, control_key)
        if prior_outcome == "completed":
            return {"ok": True, "status": "stopped", "runnerId": runner_id, "reconciled": True}
        self._reject_ambiguous_retry(prior_outcome)
        await self._append_control(row, "stop", control_key, "requested", actor=actor)
        await self._append_control(row, "stop", control_key, "accepted", actor=actor)
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
            await self._append_control(
                row, "stop", control_key,
                "delivery_unknown" if isinstance(exc, TimeoutError) else "failed",
                code="embedded_control_delivery_unknown" if isinstance(exc, TimeoutError) else "embedded_control_failed",
                actor=actor,
            )
            raise OmnigentBridgeError(
                str(exc), failure_class="integration_error", status_code=503
            ) from exc
        await self._append_control(row, "stop", control_key, "completed", actor=actor)
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
        return {"ok": True, "status": "stopped", "runnerId": runner_id}

    async def post_event(
        self, *, session_id: str, event: Any, actor: str = "authenticated_session_owner",
        expected_state: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
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
        control_key = idempotency_key or self._control_key("message", session_id, payload)
        await self._validate_expected_state(row, "message", control_key, expected_state, actor)
        prior_outcome = await self._control_outcome(row, control_key)
        if prior_outcome == "completed":
            return {"ok": True, "reconciled": True, "idempotencyKey": control_key}
        self._reject_ambiguous_retry(prior_outcome)
        await self._append_control(row, "message", control_key, "requested", summary=payload, actor=actor)
        await self._append_control(row, "message", control_key, "accepted", actor=actor)
        try:
            result = await self._host_channels.post_runner_event(
                runner_id=runner_id, session_id=session_id, payload=payload
            )
        except (EmbeddedHostChannelError, TimeoutError) as exc:
            await self._append_control(
                row, "message", control_key,
                "delivery_unknown" if isinstance(exc, TimeoutError) else "failed",
                code="embedded_control_delivery_unknown" if isinstance(exc, TimeoutError) else "embedded_control_failed",
                actor=actor,
            )
            raise OmnigentBridgeError(
                str(exc), failure_class="integration_error", status_code=503
            ) from exc
        await self._append_control(row, "message", control_key, "completed", actor=actor)
        return result

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

    async def harvest_resources(
        self, *, session_id: str, terminal_status: str = "running",
        actor: str = "authenticated_session_owner",
        idempotency_key: str | None = None,
        expected_state: Mapping[str, Any] | None = None,
        capture_policy: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Publish embedded resources through the canonical capture contract."""

        row, _ = await self._bound_runner(session_id)
        control_key = idempotency_key or self._control_key(
            "harvest", session_id, {"terminalStatus": terminal_status}
        )
        await self._validate_expected_state(
            row, "harvest", control_key, expected_state, actor
        )
        prior = await self._control_outcome(row, control_key)
        if prior == "completed" and row.capture_manifest_ref:
            return {
                "ok": True, "reconciled": True,
                "captureManifestRef": row.capture_manifest_ref,
                "resourceProjection": dict((row.terminal_refs or {}).get("resourceProjection") or {}),
            }
        self._reject_ambiguous_retry(prior)
        await self._append_control(row, "harvest", control_key, "requested", actor=actor)
        await self._append_control(row, "harvest", control_key, "accepted", actor=actor)
        association_key = f"embedded-resource-association:{control_key}"
        await self._run_store.append_events(row.bridge_session_id, [{
            "schemaVersion": "moonmind.omnigent_bridge.event.v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridgeSessionId": row.bridge_session_id,
            "direction": "moonmind_system",
            "kind": "resource_evidence_pending",
            "eventType": "resource.evidence.pending",
            "status": "running",
            "normalizedStatus": "running",
            "text": "Embedded resource evidence is being harvested",
            "textPreview": "Embedded resource evidence is being harvested",
            "deduplicationKey": f"{association_key}:pending",
            "metadata": {
                "associationKey": association_key,
                "idempotencyKey": control_key,
                "associationState": "pending",
            },
        }])
        try:
            raw_events = await self._read_journal(row.raw_events_ref)
            normalized_events = await self._read_journal(row.normalized_events_ref)
            snapshot = {
                "id": session_id,
                "status": terminal_status,
                "hostId": row.omnigent_host_id,
                "runnerId": row.omnigent_runner_id,
            }
            bundle = await _build_capture_bundle(
                client=_EmbeddedCaptureClient(self, session_id),
                artifact_gateway=self._artifact_gateway,
                request=_request_for_row(row), session_id=session_id,
                agent_id=row.omnigent_agent_id, initial_snapshot=None,
                final_snapshot=snapshot, first_message_request=None,
                first_message_response=None, first_message_posted=False,
                first_message_response_identifiers=None, raw_events=raw_events,
                normalized_events=normalized_events,
                terminal_status=terminal_status, diagnostics={"sourceMode": HOST_PROTOCOL_MODE_EMBEDDED},
                harvest_resources=True, capture_policy=dict(capture_policy or {}),
            )
            terminal_refs = build_omnigent_terminal_refs(
                bundle, terminal_status=terminal_status, final_snapshot=snapshot
            )
            terminal_refs["leaseState"] = await self._run_store.embedded_lease_state(
                row.idempotency_key
            )
            terminal_refs["cleanupState"] = (
                "cleanup_required"
                if terminal_status in {"completed", "failed", "canceled", "timed_out"}
                else "not_terminal"
            )
            if terminal_status in {"completed", "failed", "canceled", "timed_out"}:
                await self._run_store.mark_terminal(
                    row.idempotency_key, status=terminal_status,
                    terminal_refs=terminal_refs,
                )
            else:
                terminal_refs.pop("failureClass", None)
                terminal_refs.pop("failureCode", None)
                terminal_refs.pop("summary", None)
                await self._run_store.attach_capture_evidence(
                    row.idempotency_key, terminal_refs=terminal_refs
                )
        except Exception as exc:
            await self._append_control(
                row, "harvest", control_key, "failed", actor=actor,
                code="embedded_resource_harvest_failed",
                summary={"error": str(exc)[:512]},
            )
            raise
        await self._append_control(row, "harvest", control_key, "completed", actor=actor)
        await self._run_store.append_events(row.bridge_session_id, [{
            "schemaVersion": "moonmind.omnigent_bridge.event.v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridgeSessionId": row.bridge_session_id,
            "direction": "moonmind_system",
            "kind": "resource_evidence_published",
            "eventType": "resource.evidence.published",
            "status": terminal_status,
            "normalizedStatus": terminal_status,
            "text": "Embedded resource evidence published",
            "textPreview": "Embedded resource evidence published",
            "deduplicationKey": f"{association_key}:published",
            "metadata": {
                "associationKey": association_key,
                "idempotencyKey": control_key,
                "captureManifestRef": bundle.capture_manifest_ref,
                "evidenceCompleteness": bundle.resource_projection.get("completeness"),
                "associationState": "published",
            },
        }])
        return {
            "ok": True, "captureManifestRef": bundle.capture_manifest_ref,
            "resourceProjection": bundle.resource_projection,
            "evidenceCompleteness": bundle.resource_projection.get("completeness"),
        }

    async def _read_journal(self, artifact_ref: str | None) -> list[dict[str, Any]]:
        if not artifact_ref:
            return []
        payload = await self._artifact_gateway.read_text(artifact_ref)
        values: list[dict[str, Any]] = []
        for line in payload.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                values.append(value)
        return values

    async def resolve_elicitation(
        self, *, session_id: str, elicitation_id: str, payload: dict[str, Any],
        actor: str = "authenticated_session_owner",
        expected_state: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve an elicitation through the exact durably bound runner."""

        row, runner_id = await self._bound_runner(session_id)
        control_key = idempotency_key or self._control_key(
            "elicitation", session_id, {"elicitationId": elicitation_id, "payload": payload}
        )
        await self._validate_expected_state(
            row, "elicitation", control_key, expected_state, actor
        )
        prior_outcome = await self._control_outcome(row, control_key)
        if prior_outcome == "completed":
            return {"ok": True, "reconciled": True, "idempotencyKey": control_key}
        self._reject_ambiguous_retry(prior_outcome)
        await self._append_control(
            row, "elicitation", control_key, "requested",
            summary={"elicitationId": elicitation_id}, actor=actor,
        )
        await self._append_control(
            row, "elicitation", control_key, "accepted", actor=actor
        )
        path = self._config.public_api.routes.resolve_elicitation.format(
            session_id=quote(session_id, safe=""),
            elicitation_id=quote(_safe_resource_identifier(elicitation_id), safe=""),
        )
        try:
            result = await self._host_channels.request_runner(
                runner_id=runner_id,
                method="POST",
                path=path,
                payload=payload,
                expect_json=True,
            )
        except (EmbeddedHostChannelError, TimeoutError) as exc:
            await self._append_control(
                row, "elicitation", control_key,
                "delivery_unknown" if isinstance(exc, TimeoutError) else "failed",
                code="embedded_control_delivery_unknown" if isinstance(exc, TimeoutError) else "embedded_control_failed",
                actor=actor,
            )
            raise OmnigentBridgeError(
                str(exc), failure_class="integration_error", status_code=503
            ) from exc
        await self._append_control(
            row, "elicitation", control_key, "completed", actor=actor
        )
        return result

    @staticmethod
    def _control_key(control: str, session_id: str, payload: Mapping[str, Any]) -> str:
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()[:24]
        return f"embedded:{control}:{session_id}:{digest}"[:100]

    async def _control_outcome(self, row: Any, control_key: str) -> str | None:
        events = await self._run_store.list_events(row.bridge_session_id)
        prefix = f"{control_key}:"
        outcomes = [
            event.deduplication_key.removeprefix(prefix)
            for event in events
            if event.deduplication_key.startswith(prefix)
        ]
        for outcome in ("completed", "delivery_unknown", "failed", "accepted", "requested"):
            if outcome in outcomes:
                return outcome
        return None

    async def reconcile_control(
        self, *, session_id: str, control: str, idempotency_key: str,
        outcome: str, actor: str = "embedded_host",
    ) -> dict[str, Any]:
        """Finalize an accepted/ambiguous control without repeating its side effect."""

        if control not in {"stop", "message", "elicitation", "harvest"}:
            raise OmnigentBridgeError(
                "Embedded control reconciliation requires a supported control",
                failure_class="user_error", status_code=422,
                code="embedded_control_reconciliation_invalid",
            )
        if not idempotency_key:
            raise OmnigentBridgeError(
                "Embedded control reconciliation requires an idempotency key",
                failure_class="user_error", status_code=422,
                code="embedded_control_reconciliation_invalid",
            )
        if outcome not in {"completed", "failed"}:
            raise OmnigentBridgeError(
                "Embedded control reconciliation outcome must be completed or failed",
                failure_class="user_error", status_code=422,
                code="embedded_control_reconciliation_invalid",
            )
        row = await self._run_store.get_session_by_provider_session_id(session_id)
        if row is None:
            raise OmnigentBridgeError(
                "No Omnigent bridge session is bound to the requested session id.",
                failure_class="user_error", status_code=404,
            )
        prior = await self._control_outcome(row, idempotency_key)
        if prior == outcome:
            return {"ok": outcome == "completed", "reconciled": True,
                    "outcome": outcome, "idempotencyKey": idempotency_key}
        if prior not in {"requested", "accepted", "delivery_unknown"}:
            raise OmnigentBridgeError(
                "Embedded control has no reconcilable durable delivery state",
                failure_class="user_error", status_code=409,
                code="embedded_control_reconciliation_not_allowed",
            )
        await self._append_control(
            row, control, idempotency_key, outcome, actor=actor,
            code=None if outcome == "completed" else "embedded_control_failed",
        )
        return {"ok": outcome == "completed", "reconciled": True,
                "outcome": outcome, "idempotencyKey": idempotency_key}

    async def _validate_expected_state(
        self, row: Any, control: str, control_key: str,
        expected: Mapping[str, Any] | None, actor: str,
    ) -> None:
        if not expected:
            return
        actual = {
            "sessionId": row.omnigent_session_id, "hostId": row.omnigent_host_id,
            "runnerId": row.omnigent_runner_id, "sessionStatus": row.status,
            "turnState": str((row.metadata_ or {}).get("turnState") or "unknown"),
        }
        mismatches = {
            key: {"expected": value, "actual": actual.get(key)}
            for key, value in expected.items()
            if key not in actual or value != actual.get(key)
        }
        if not mismatches:
            return
        await self._append_control(
            row, control, control_key, "rejected", actor=actor,
            summary={"stateMismatch": mismatches}, code="embedded_control_state_mismatch",
        )
        raise OmnigentBridgeError(
            "Embedded control expected state does not match durable session state",
            failure_class="user_error", status_code=409,
            code="embedded_control_state_mismatch",
        )

    @staticmethod
    def _reject_ambiguous_retry(prior_outcome: str | None) -> None:
        if prior_outcome is None:
            return
        raise OmnigentBridgeError(
            "Embedded control retry requires reconciliation before another live delivery",
            failure_class="integration_error",
            status_code=409,
            code="embedded_control_reconciliation_required",
        )

    async def _append_control(
        self, row: Any, control: str, control_key: str, outcome: str, *,
        summary: Mapping[str, Any] | None = None, code: str | None = None,
        actor: str = "authenticated_session_owner",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        redacted_summary = redact_raw_events([{"data": dict(summary or {})}])[0].get(
            "data", {}
        )
        summary_json = json.dumps(redacted_summary, sort_keys=True, default=str)
        evidence = {
            "schemaVersion": "moonmind.omnigent.control_evidence.v1",
            "control": control,
            "outcome": outcome,
            "actor": str(actor)[:256],
            "idempotencyKey": control_key,
            "sessionId": row.omnigent_session_id,
            "hostId": row.omnigent_host_id,
            "runnerId": row.omnigent_runner_id,
            "sessionStatus": row.status,
            "turnState": str((row.metadata_ or {}).get("turnState") or "unknown"),
            "requestSummary": summary_json[:2048],
            "code": code,
            "timestamp": now,
            "sourceMode": HOST_PROTOCOL_MODE_EMBEDDED,
        }
        evidence_ref = await self._artifact_gateway.write_json(
            request=_request_for_row(row),
            name=f"runtime.omnigent.control.{hashlib.sha256(control_key.encode()).hexdigest()[:24]}.{outcome}.json",
            payload=evidence,
            link_type="runtime.omnigent.control.evidence",
        )
        data = {
            "control": control,
            "outcome": outcome,
            "actor": str(actor)[:256],
            "idempotencyKey": control_key,
            "expectedState": {
                "sessionId": row.omnigent_session_id,
                "hostId": row.omnigent_host_id,
                "runnerId": row.omnigent_runner_id,
                "sessionStatus": row.status,
                "turnState": evidence["turnState"],
            },
            "requestSummary": summary_json[:2048],
            "evidenceRef": evidence_ref,
            "timestamp": now,
            "sourceMode": HOST_PROTOCOL_MODE_EMBEDDED,
        }
        if code:
            data["code"] = code
        await self._run_store.append_events(row.bridge_session_id, [{
            "schemaVersion": "moonmind.omnigent_bridge.event.v1",
            "timestamp": now,
            "bridgeSessionId": row.bridge_session_id,
            "omnigentSessionId": row.omnigent_session_id,
            "moonmindWorkflowId": row.moonmind_workflow_id,
            "moonmindAgentRunId": row.moonmind_agent_run_id,
            "direction": "moonmind_to_host",
            "type": f"control.{control}.{outcome}",
            "eventType": f"control.{control}.{outcome}",
            "normalizedStatus": "running",
            "data": data,
            "artifactRef": evidence_ref,
            "artifactRefs": [evidence_ref],
            "metadata": {
                "moonmind": {"workflowChatVisible": False, "source": "embedded_control"},
                "embeddedControl": data,
            },
            "deduplicationKey": f"{control_key}:{outcome}"[:128],
        }])

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
        metadata = dict(row.metadata_ or {})
        terminal = row.status in {"completed", "failed", "canceled", "timed_out"}
        hosts = await self._run_store.list_embedded_host_readiness()
        live_host = next(
            (host for host in hosts if host.get("id") == row.omnigent_host_id), None
        )
        disconnected = bool(row.omnigent_host_id) and (
            live_host is None or bool(live_host.get("disconnected"))
        )
        return {
            "id": session_id,
            "status": row.status,
            "agentId": row.omnigent_agent_id or "codex-native",
            "hostId": row.omnigent_host_id,
            "runnerId": row.omnigent_runner_id,
            "firstMessageState": row.first_message_state,
            "capabilities": (
                metadata.get("interventionCapabilities")
                or metadata.get("capabilities")
                or {}
            ),
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

    async def stop_session(self, session_id: str) -> dict[str, Any]:
        """Stop the exact bound runner through the common facade contract."""

        return await self.stop_runner(session_id=session_id)

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
        host_capabilities = await self._run_store.get_embedded_host_capabilities(
            host_id=str(authorized.omnigent_host_id)
        )
        await self._run_store.record_session_created(
            binding.idempotency_key,
            session_id=session_id,
            agent_id=(request.agent_id or "").strip() or None,
            endpoint_ref=(request.endpoint_ref or "").strip() or "embedded",
            capabilities=_embedded_intervention_capabilities(
                host_capabilities,
                session_status=row.status,
                # Dispatch follows creation; the durable host assignment makes
                # these controls available and dispatch failures terminalize it.
                runner_bound=True,
            ),
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
        metadata = dict(normalized.get("metadata") or {})
        # Event rows are a compact query/projection index. The execution capture
        # pipeline persists the complete redacted raw and normalized streams in
        # artifact-backed journals; copying either payload here amplifies storage
        # and turns the database into an unintended evidence authority.
        metadata["embeddedEventIndexed"] = True
        metadata["sourceMode"] = HOST_PROTOCOL_MODE_EMBEDDED
        normalized["metadata"] = metadata
        raw_ref, normalized_ref = await self._publish_embedded_journals(
            row=row, raw_event=payload, normalized_event=normalized,
            sequence=next_sequence,
        )
        normalized["artifactRef"] = normalized_ref
        normalized["artifactRefs"] = [normalized_ref, raw_ref]
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

    async def _publish_embedded_journals(
        self, *, row: Any, raw_event: dict[str, Any],
        normalized_event: dict[str, Any], sequence: int,
    ) -> tuple[str, str]:
        """Publish the redacted journal prefix before indexing its new event."""

        async def restored(ref: str | None) -> list[dict[str, Any]]:
            if not ref:
                return []
            payload = await self._artifact_gateway.read_text(ref)
            values: list[dict[str, Any]] = []
            for line in payload.splitlines():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    values.append(value)
            return values

        lock = self._journal_locks.setdefault(row.bridge_session_id, asyncio.Lock())
        async with lock:
            refreshed = await self._run_store.get_session_by_provider_session_id(
                row.omnigent_session_id
            )
            raw_events = await restored(refreshed.raw_events_ref)
            normalized_events = await restored(refreshed.normalized_events_ref)
            raw_events.extend(redact_raw_events([raw_event]))
            normalized_events.append(normalized_event)
            request = _request_for_row(refreshed)
            prefix = f"{sequence:08d}"
            encode = lambda values: "".join(
                json.dumps(value, sort_keys=True, default=str, separators=(",", ":")) + "\n"
                for value in values
            )
            raw_ref = await self._artifact_gateway.write_text(
                request=request, name=f"runtime.omnigent.sse.raw.{prefix}.jsonl",
                payload=encode(raw_events), link_type="runtime.omnigent.sse.raw",
                content_type="application/x-ndjson",
            )
            normalized_ref = await self._artifact_gateway.write_text(
                request=request, name=f"runtime.omnigent.sse.normalized.{prefix}.jsonl",
                payload=encode(normalized_events), link_type="runtime.omnigent.sse.normalized",
                content_type="application/x-ndjson",
            )
            await self._run_store.attach_active_journal_refs(
                row.bridge_session_id, raw_ref=raw_ref, normalized_ref=normalized_ref,
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
