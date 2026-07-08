"""Omnigent Bridge Host Protocol Facade / Proxy (proxy mode).

MM-1155 (source: MM-1140): expose/proxy the Omnigent session routes via the
bridge facade. In proxy mode (``docs/Omnigent/OmnigentBridge.md`` §3.1, §5.2,
§8), this module is the Host Protocol Facade/Proxy: it validates the
Omnigent-shaped session-create request (§8.3 managed/external host rules),
reuses the STORY-002 durable store to create/reuse a row by idempotency key,
resolves the endpoint + target agent, forwards to the stock Omnigent Server,
persists ``omnigent_session_id`` before any first-message prepare/post, emits
``session.created`` into the durable bridge event journal, and returns an
Omnigent-shaped session snapshot.

The proxy deliberately reuses the existing MoonMind Omnigent primitives
(``build_omnigent_selection`` / ``resolve_omnigent_target`` /
``build_omnigent_session_create_payload`` and ``OmnigentBridgeSessionStore``) so
the create/attach/validate behavior has one canonical path rather than a
parallel implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_PROXY,
    OmnigentBridgeConfig,
)
from moonmind.omnigent.bridge_security import BridgeSessionBinding
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentAdapterError,
    OmnigentExecutionSelection,
    _is_git_url_with_optional_branch,
    build_omnigent_selection,
    build_omnigent_session_create_payload,
    resolve_omnigent_target,
)
from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
)

SOURCE_ISSUE = "MM-1155"


class OmnigentBridgeError(RuntimeError):
    """Bridge facade error carrying a MoonMind failure class + optional status."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: str = "integration_error",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.status_code = status_code


class BridgeSessionCreateRequest(BaseModel):
    """Omnigent-shaped ``POST /v1/sessions`` request body (OB-§8.1)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    agent_id: str | None = None
    title: str | None = None
    labels: dict[str, Any] = Field(default_factory=dict)
    host_type: str = "managed"
    host_id: str | None = None
    workspace: str | None = None
    model_override: str | None = None
    reasoning_effort: str | None = None
    terminal_launch_args: list[str] = Field(default_factory=list)
    endpoint_ref: str | None = None


@dataclass(frozen=True, slots=True)
class BridgePrincipalBinding:
    """Verified MoonMind identity for one bridge session-create request."""

    workflow_id: str
    correlation_id: str
    idempotency_key: str
    agent_run_id: str | None = None


def validate_bridge_host_fields(
    *,
    host_type: str,
    host_id: str | None,
    workspace: str | None,
) -> None:
    """Enforce the OB-§8.3 managed/external host rules at the facade boundary.

    Violations raise :class:`OmnigentBridgeError` with ``user_error`` so the
    router returns a 400 rather than forwarding an invalid request upstream.
    """

    normalized_type = (host_type or "").strip().lower()
    host_id = (host_id or "").strip() or None
    workspace = (workspace or "").strip() or None

    if normalized_type not in {"managed", "external"}:
        raise OmnigentBridgeError(
            "host_type must be 'managed' or 'external'",
            failure_class="user_error",
        )

    if normalized_type == "managed":
        if host_id:
            raise OmnigentBridgeError(
                "Managed Omnigent sessions must not provide host_id",
                failure_class="user_error",
            )
        if workspace and not _is_git_url_with_optional_branch(workspace):
            raise OmnigentBridgeError(
                "Managed Omnigent session workspace must be a repository URL "
                "with optional #branch; local absolute paths are invalid",
                failure_class="user_error",
            )
        return

    # external
    if not host_id:
        raise OmnigentBridgeError(
            "External Omnigent sessions require host_id",
            failure_class="user_error",
        )
    if not workspace:
        raise OmnigentBridgeError(
            "External Omnigent sessions require an absolute host workspace path",
            failure_class="user_error",
        )
    if _is_git_url_with_optional_branch(workspace):
        raise OmnigentBridgeError(
            "External Omnigent session workspace must be an absolute host path, "
            "not a repository URL",
            failure_class="user_error",
        )
    if not workspace.startswith("/"):
        raise OmnigentBridgeError(
            "External Omnigent session workspace must be an absolute host path",
            failure_class="user_error",
        )


class OmnigentBridgeSessionProxy:
    """Proxy-mode Session/Host Protocol facade over the stock Omnigent Server."""

    def __init__(
        self,
        *,
        run_store: OmnigentBridgeSessionStore,
        client: OmnigentHttpClient,
        config: OmnigentBridgeConfig | None = None,
        default_agent_name: str = "",
    ) -> None:
        self._run_store = run_store
        self._client = client
        self._config = config or OmnigentBridgeConfig()
        self._default_agent_name = (default_agent_name or "").strip()

    @property
    def config(self) -> OmnigentBridgeConfig:
        return self._config

    async def create_session(
        self,
        *,
        request: BridgeSessionCreateRequest,
        binding: BridgePrincipalBinding,
    ) -> dict[str, Any]:
        """Create or reuse a proxy-mode Omnigent session (OB-§8.2)."""

        self._require_proxy_mode()
        # OB-§8.3 host validation exposed through the facade.
        validate_bridge_host_fields(
            host_type=request.host_type,
            host_id=request.host_id,
            workspace=request.workspace,
        )

        exec_request = _build_execution_request(request, binding)

        # OB-§8.2 step 2 / §16 rule 1: read the durable row up front so an
        # idempotency key that is already bound to another MoonMind workflow is
        # rejected before any provider call, and so a durable retry that already
        # has a provider session can be served without re-resolving the target.
        existing = await self._run_store.get_existing(exec_request.idempotency_key)
        if existing is not None:
            self._assert_row_owner(existing, binding)
            reused_session_id = str(
                getattr(existing, "omnigent_session_id", None) or ""
            ).strip()
            if reused_session_id:
                return await self._reuse_attached_session(
                    exec_request=exec_request,
                    binding=binding,
                    row=existing,
                    session_id=reused_session_id,
                )

        selection = _selection(exec_request)
        endpoint_ref = str(selection.endpoint_ref or "default")

        async def _list_agents() -> list[dict[str, Any]]:
            return await self._list_agents_raw()

        try:
            target = await resolve_omnigent_target(
                selection,
                list_agents=_list_agents,
                upload_agent_bundle=_unsupported_bundle_upload,
                default_agent_name=self._default_agent_name,
            )
        except OmnigentAdapterError as exc:
            raise OmnigentBridgeError(
                str(exc), failure_class=exc.failure_class
            ) from exc

        payload = build_omnigent_session_create_payload(
            request=exec_request, selection=selection, target=target
        )
        payload["idempotency_key"] = exec_request.idempotency_key
        labels = payload.setdefault("labels", {})
        if isinstance(labels, dict):
            labels.setdefault("moonmind.issue", SOURCE_ISSUE)

        # Create or reuse the durable row by idempotency key (OB-§8.2 step 2),
        # persisting the *verified* MoonMind owner so later workflow-scoped
        # lookup/authorization binds to the workflow, not the correlation id.
        row = await self._run_store.get_or_create(
            request=exec_request,
            endpoint_ref=endpoint_ref,
            agent_id=target.agent_id,
            agent_name=target.agent_name,
            target_metadata={
                "hostType": selection.session.host_type,
                "workspace": selection.session.workspace,
            },
            workflow_id=binding.workflow_id,
            agent_run_id=binding.agent_run_id,
        )
        # Re-assert ownership after get_or_create closes the concurrent-create
        # race: a row created by another workflow under the same key must not be
        # reused under this caller's binding.
        self._assert_row_owner(row, binding)

        session_id = str(getattr(row, "omnigent_session_id", None) or "").strip()
        reused = bool(session_id)
        if not session_id:
            create_response = await self._forward_create(payload)
            session_id = _session_id(create_response)
            # Persist provider session id BEFORE any first-message prepare/post
            # (OB-§8.2 step 5).
            await self._run_store.attach_session(
                exec_request.idempotency_key, session_id
            )
        # Emit session.created (OB-§8.2 step 6) idempotently on both the create
        # and reuse paths. If a prior attempt attached the session id but crashed
        # before the journal write landed, a retry finds the session already
        # attached; re-emitting here (the store append is idempotent) prevents the
        # session.created event from being permanently lost on partial failure.
        await self._run_store.record_session_created(
            exec_request.idempotency_key,
            session_id=session_id,
            agent_id=target.agent_id,
            endpoint_ref=endpoint_ref,
        )

        snapshot = await self._best_effort_snapshot(session_id)
        return self._session_response(
            snapshot=snapshot,
            session_id=session_id,
            binding=binding,
            reused=reused,
        )

    async def _reuse_attached_session(
        self,
        *,
        exec_request: AgentExecutionRequest,
        binding: BridgePrincipalBinding,
        row: Any,
        session_id: str,
    ) -> dict[str, Any]:
        """Serve a durable retry whose provider session is already attached.

        Skips target resolution and the upstream create entirely (OB-§8.2): a
        default-agent request must not fail an otherwise-safe idempotent retry
        just because ``/api/agents`` is momentarily down or the default agent
        renamed. ``session.created`` is still re-emitted idempotently so a prior
        partial failure (attach landed, journal write did not) recovers.
        """

        endpoint_ref = str(
            getattr(row, "omnigent_endpoint_ref", None) or "default"
        )
        agent_id = getattr(row, "omnigent_agent_id", None)
        await self._run_store.record_session_created(
            exec_request.idempotency_key,
            session_id=session_id,
            agent_id=agent_id,
            endpoint_ref=endpoint_ref,
        )
        snapshot = await self._best_effort_snapshot(session_id)
        return self._session_response(
            snapshot=snapshot,
            session_id=session_id,
            binding=binding,
            reused=True,
        )

    def _assert_row_owner(
        self, row: Any, binding: BridgePrincipalBinding
    ) -> None:
        """Refuse cross-owner reuse of a bridge idempotency key (§16 rule 1).

        A durable bridge row (idempotency key) may only be reused by the
        MoonMind workflow that created it. Idempotency keys are caller-supplied
        labels on this public API, so a key already bound to another workflow
        fails fast instead of leaking that workflow's session snapshot.
        """

        stored_workflow_id = str(
            getattr(row, "moonmind_workflow_id", None) or ""
        ).strip()
        if stored_workflow_id and stored_workflow_id != binding.workflow_id:
            raise OmnigentBridgeError(
                "Omnigent bridge idempotency key is bound to a different MoonMind "
                "workflow; refusing cross-owner reuse",
                failure_class="user_error",
                status_code=409,
            )

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """Return an Omnigent-shaped session snapshot (OB-§8.2 / §4.1)."""

        self._require_proxy_mode()
        try:
            snapshot = await self._client.get_session(session_id)
        except OmnigentClientError as exc:
            raise OmnigentBridgeError(
                str(exc),
                failure_class=exc.failure_class,
                status_code=exc.status_code,
            ) from exc
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot.setdefault("id", session_id)
        return snapshot

    async def get_session_owner(
        self, session_id: str
    ) -> BridgeSessionBinding | None:
        """Return the MoonMind identity bound to a provider ``session_id``.

        Read-only durable lookup so the Session API Facade can authorize a
        direct session read (OB-§16 rule 1) before proxying it upstream with
        the service credential. Returns ``None`` when the bridge has no row
        bound to the provider session id.
        """

        return await self._run_store.get_session_owner(session_id)

    async def list_agents(self) -> list[dict[str, Any]]:
        """Proxy ``GET /api/agents`` to the stock Omnigent Server (OB-§4.1)."""

        self._require_proxy_mode()
        return await self._list_agents_raw()

    async def _list_agents_raw(self) -> list[dict[str, Any]]:
        try:
            agents = await self._client.list_agents()
        except OmnigentClientError as exc:
            raise OmnigentBridgeError(
                str(exc),
                failure_class=exc.failure_class,
                status_code=exc.status_code,
            ) from exc
        if isinstance(agents, list):
            return [item for item in agents if isinstance(item, dict)]
        return []

    async def _forward_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._client.create_session(payload)
        except OmnigentClientError as exc:
            raise OmnigentBridgeError(
                str(exc),
                failure_class=exc.failure_class,
                status_code=exc.status_code,
            ) from exc

    async def _best_effort_snapshot(self, session_id: str) -> dict[str, Any]:
        try:
            snapshot = await self._client.get_session(session_id)
        except OmnigentClientError:
            snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        return snapshot

    def _session_response(
        self,
        *,
        snapshot: dict[str, Any],
        session_id: str,
        binding: BridgePrincipalBinding,
        reused: bool,
    ) -> dict[str, Any]:
        response = dict(snapshot)
        response["id"] = session_id
        response["moonmind"] = {
            "workflowId": binding.workflow_id,
            "agentRunId": binding.agent_run_id,
            "idempotencyKey": binding.idempotency_key,
            "reused": reused,
            "hostProtocolMode": self._config.host_protocol_mode,
            "sourceIssue": SOURCE_ISSUE,
        }
        return response

    def _require_proxy_mode(self) -> None:
        if self._config.host_protocol_mode != HOST_PROTOCOL_MODE_PROXY:
            raise OmnigentBridgeError(
                "Omnigent bridge only implements upstream_omnigent_server_proxy "
                f"mode; got '{self._config.host_protocol_mode}'",
                failure_class="system_error",
                status_code=501,
            )


def _build_execution_request(
    request: BridgeSessionCreateRequest,
    binding: BridgePrincipalBinding,
) -> AgentExecutionRequest:
    """Translate an Omnigent-shaped request into an ``AgentExecutionRequest``."""

    session_params: dict[str, Any] = {
        "hostType": request.host_type,
        "workspace": (request.workspace or "").strip() or None,
        "hostId": (request.host_id or "").strip() or None,
        "title": request.title,
        "labels": dict(request.labels or {}),
        "modelOverride": request.model_override,
        "reasoningEffort": request.reasoning_effort,
        "terminalLaunchArgs": list(request.terminal_launch_args or []),
        # The facade does not force a managed workspace; OB-§8.3 workspace
        # requirements are validated by validate_bridge_host_fields.
        "allowEmptyWorkspace": True,
    }
    omnigent_params: dict[str, Any] = {
        "endpointRef": (request.endpoint_ref or "").strip() or "default",
        "session": {
            key: value
            for key, value in session_params.items()
            if value is not None
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


def _selection(exec_request: AgentExecutionRequest) -> OmnigentExecutionSelection:
    try:
        return build_omnigent_selection(exec_request)
    except OmnigentAdapterError as exc:
        raise OmnigentBridgeError(str(exc), failure_class=exc.failure_class) from exc


def _session_id(payload: dict[str, Any]) -> str:
    raw = payload.get("id") or payload.get("session_id") or payload.get("sessionId")
    session_id = str(raw or "").strip()
    if not session_id:
        raise OmnigentBridgeError(
            "Omnigent session creation response missing session id",
            failure_class="integration_error",
        )
    return session_id


async def _unsupported_bundle_upload(bundle_ref: str) -> dict[str, Any]:
    raise OmnigentBridgeError(
        f"Omnigent bundleRef cannot be resolved by the bridge proxy: {bundle_ref}",
        failure_class="user_error",
    )


__all__ = [
    "SOURCE_ISSUE",
    "BridgePrincipalBinding",
    "BridgeSessionCreateRequest",
    "OmnigentBridgeError",
    "OmnigentBridgeSessionProxy",
    "validate_bridge_host_fields",
]
