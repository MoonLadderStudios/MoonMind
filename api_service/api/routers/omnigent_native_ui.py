"""Serve the native Omnigent web app through MoonMind-scoped routes.

MoonLadderStudios/MoonMind#3638 (design source #3628; depends on #3633 opaque
``chat_binding_id`` persistence + resolution and #3634 the binding-scoped
HTTP/SSE API facade). This router serves the provider-maintained native Omnigent
web application as the Workflow Chat UI at the server-owned, binding-scoped
route (``docs/UI/WorkflowChatPanel.md`` §4, ``docs/Omnigent/OmnigentBridge.md``
§4.2, §6 ``workflowChat``):

```text
GET /omnigent-ui/workflow-chat/{chatBindingId}[?embedded=1]
GET /omnigent-ui/workflow-chat/{chatBindingId}/{ui_path:path}
```

It is not a copy of the native React app and not an open reverse proxy: the
browser loads the stock native UI assets and every application request
exclusively through this MoonMind-scoped route, which reverse-proxies the stock
UI assets from the upstream server, serves the SPA document with a browser-safe
binding-scoped bootstrap injected, and never lets the browser connect directly
to the upstream Omnigent server.

Every request (embedded or full-page) independently authenticates the MoonMind
caller, resolves and authorizes the durable ``chatBindingId`` binding, gates on a
known-compatible native UI/server version, strips MoonMind credentials before
forwarding upstream, injects only the server-side upstream credential, and
applies the embedded vs full-page security-header policy. The runtime-neutral
mechanics live in :mod:`moonmind.omnigent.native_ui`.
"""

from __future__ import annotations

import json
import logging
import posixpath
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from api_service.api.routers.omnigent_bridge import (
    _facade_liveness_state,
    _effective_capabilities,
    _get_bridge_store,
    _get_execution_service,
    _require_bridge_enabled,
    _resolve_and_authorize_chat_binding,
)
from api_service.auth_providers import get_current_user
from api_service.api.routers.temporal_artifacts import _get_temporal_artifact_service
from api_service.db.models import User
from moonmind.omnigent.bridge_config import OmnigentBridgeConfig
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.native_chat_acceptance import (
    validate_native_chat_acceptance_report,
)
from moonmind.omnigent.native_chat_rollout import resolve_native_chat_rollout
from moonmind.omnigent.native_chat_telemetry import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    STAGE_DIAGNOSTIC_FALLBACK,
    STAGE_NATIVE_UI_COMPATIBILITY,
    STAGE_NATIVE_UI_LOAD,
    record_request as record_native_chat_request,
    record_rollout as record_native_chat_rollout,
)
from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.native_ui import (
    CODE_NATIVE_CHAT_UNAVAILABLE,
    NATIVE_UI_MOUNT_PREFIX,
    build_chat_bootstrap,
    evaluate_native_ui_compatibility,
    is_document_request,
    native_ui_security_headers,
    presentation_mode_from_query,
    render_native_ui_document,
    scoped_ui_base,
    upstream_path_for,
)
from moonmind.omnigent.settings import (
    resolved_api_token,
    resolved_native_chat_acceptance_ref,
    resolved_native_chat_rollout_mode,
    resolved_native_ui_serving_enabled,
    resolved_native_ui_version,
    resolved_server_url,
)
from moonmind.omnigent.workflow_chat_facade import WorkflowChatFacadeError, is_read_only
from moonmind.utils.build_info import resolve_moonmind_build_id
from moonmind.workflows.temporal.artifacts import TemporalArtifactService

logger = logging.getLogger(__name__)

# The bridge store dependency is reused verbatim so a test overriding
# ``_get_bridge_store`` on the app also overrides it for this router.
_get_bridge_store_dep = _get_bridge_store

# The native UI is served under this fixed, server-owned mount prefix. It is the
# router prefix used when the app includes this router (mirrors
# ``WORKFLOW_CHAT_BINDINGS_MOUNT_PATH`` in ``omnigent_bridge``).
NATIVE_UI_MOUNT_PATH = NATIVE_UI_MOUNT_PREFIX

native_ui_router = APIRouter(tags=["Omnigent Native UI"])

# The maximum single upstream asset MoonMind proxies through the scoped route.
_NATIVE_UI_MAX_ASSET_BYTES = 25 * 1024 * 1024
_NATIVE_UI_UPSTREAM_TIMEOUT = httpx.Timeout(30.0)


class NativeUiUpstreamError(RuntimeError):
    """The upstream native UI/server could not be reached or served."""


async def _acceptance_report_is_current(
    ref: str,
    *,
    artifact_service: TemporalArtifactService,
) -> bool:
    """Resolve and fully validate the configured canary proof, fail closed."""

    normalized = str(ref or "").strip()
    if not normalized.startswith("artifact://"):
        return False
    build_identity = resolve_moonmind_build_id()
    if not build_identity:
        return False
    pending = [normalized]
    objects: dict[str, dict[str, Any]] = {}
    try:
        while pending:
            current = pending.pop()
            if current in objects:
                continue
            artifact_id = current.removeprefix("artifact://").strip()
            if not artifact_id:
                return False
            _artifact, payload = await artifact_service.read(
                artifact_id=artifact_id,
                principal="service:omnigent-native-chat-acceptance-gate",
            )
            value = json.loads(payload)
            if not isinstance(value, dict):
                return False
            objects[current] = value
            refs = value.get("evidenceRefs")
            if isinstance(refs, list):
                pending.extend(item for item in refs if isinstance(item, str))
            cases = value.get("cases")
            if isinstance(cases, dict):
                for case in cases.values():
                    if isinstance(case, dict) and isinstance(case.get("evidenceRefs"), list):
                        pending.extend(
                            item for item in case["evidenceRefs"] if isinstance(item, str)
                        )
        report = objects.pop(normalized)
        validate_native_chat_acceptance_report(
            report,
            evidence_resolver=objects.__getitem__,
            expected_build=build_identity,
        )
    except (KeyError, TypeError, ValueError, ConformanceContractError, UnicodeDecodeError):
        logger.warning("omnigent.native_chat acceptance evidence rejected")
        return False
    except Exception:
        logger.exception("omnigent.native_chat acceptance evidence unavailable")
        return False
    return True


class NativeUiUpstreamResponse:
    """A minimal, browser-safe view of one upstream asset/document response."""

    __slots__ = ("status_code", "content", "media_type", "location")

    def __init__(
        self,
        *,
        status_code: int,
        content: bytes,
        media_type: str,
        location: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.media_type = media_type
        self.location = location

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308) and bool(self.location)


class NativeUiUpstream:
    """Fetch stock native UI assets/documents from the upstream Omnigent server.

    The browser never talks to the upstream server: MoonMind fetches assets with
    the server-side credential and forwards no browser/MoonMind headers upstream
    (``stripMoonMindCredentialsUpstream``/``injectUpstreamCredentialsServerSide``,
    OmnigentBridge.md §6). Redirects are surfaced (not auto-followed) so the
    router can keep them inside the scoped route.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = str(base_url).rstrip("/")
        self._api_token = api_token
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        # Only the server-side upstream credential is injected; no browser or
        # MoonMind header is forwarded (credential separation, issue §5).
        headers = {"Accept": "*/*"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    async def fetch(self, path: str) -> NativeUiUpstreamResponse:
        if not self._base:
            raise NativeUiUpstreamError("Upstream Omnigent server URL is not configured.")
        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=_NATIVE_UI_UPSTREAM_TIMEOUT,
                follow_redirects=False,
            ) as client:
                # Stream the (decoded) body and abort as soon as the cumulative
                # decoded size exceeds the limit, so a very large or highly
                # compressed upstream response cannot buffer unbounded bytes into
                # API-service memory before the length check runs.
                async with client.stream(
                    "GET", url, headers=self._headers()
                ) as response:
                    location = response.headers.get("location")
                    media_type = (
                        response.headers.get(
                            "content-type", "application/octet-stream"
                        )
                        .split(";")[0]
                        .strip()
                        or "application/octet-stream"
                    )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > _NATIVE_UI_MAX_ASSET_BYTES:
                            raise NativeUiUpstreamError(
                                "Upstream native UI asset exceeds the limit."
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
        except httpx.HTTPError as exc:  # noqa: BLE001 - one bounded failure surface
            raise NativeUiUpstreamError(str(exc)) from exc
        return NativeUiUpstreamResponse(
            status_code=response.status_code,
            content=content,
            media_type=media_type,
            location=location,
        )


def get_native_ui_upstream(
    _config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
) -> NativeUiUpstream:
    """Build the upstream fetcher over the configured stock Omnigent server."""

    return NativeUiUpstream(
        base_url=resolved_server_url(),
        api_token=resolved_api_token(),
    )


def _native_chat_unavailable(
    *, mode: str, is_document: bool, reason: str, status_code: int = 503
) -> Response:
    """Return an actionable native-chat-unavailable response, safe to expose.

    Fails with a stable, non-topology-revealing state rather than partially
    bypassing the scoped facade (issue #3638 requirement 6). Documents get a
    minimal branded HTML page the embedding shell can present; asset requests get
    a stable JSON error.
    """

    headers = native_ui_security_headers(mode=mode, is_document=is_document)
    if is_document:
        body = (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>Workflow Chat unavailable</title></head><body>"
            "<main role=\"main\"><h1>Workflow chat is unavailable</h1>"
            "<p>The native Omnigent chat could not be served for this workflow.</p>"
            f"<p data-reason=\"{reason}\">Reason: {reason}</p></body></html>"
        )
        return HTMLResponse(content=body, status_code=status_code, headers=headers)
    return JSONResponse(
        content={
            "detail": {"code": CODE_NATIVE_CHAT_UNAVAILABLE, "reason": reason}
        },
        status_code=status_code,
        headers=headers,
    )


def _rewrite_upstream_location(location: str, *, scoped_base: str) -> str:
    """Keep an upstream redirect inside the binding-scoped route.

    A root-absolute upstream ``Location`` is re-based onto the scoped route and an
    absolute upstream URL collapses to the scoped root, so a redirect can never
    escape to an unscoped upstream session route or reveal upstream topology
    (issue #3638 requirement 5).

    Relative redirects (including ones with ``..`` parent segments such as
    ``../../../../api/executions``) are resolved against the scoped base and then
    normalized; if the normalized target would walk above the binding mount — a
    path the browser would otherwise collapse outside the scoped route — the
    rewrite fails closed to the scoped root instead of emitting the escaping
    segments verbatim.
    """

    base = scoped_base.rstrip("/")
    target = str(location or "").strip()
    if not target:
        return base + "/"
    split = urlsplit(target)
    suffix = ""
    if split.query:
        suffix += "?" + split.query
    if split.fragment:
        suffix += "#" + split.fragment
    if split.scheme or split.netloc:
        # Absolute (possibly upstream-host) URL: keep only the path, scoped.
        raw = split.path or "/"
        combined = base + (raw if raw.startswith("/") else "/" + raw)
    elif split.path.startswith("/"):
        # Root-absolute upstream path re-based onto the scoped route.
        combined = base + split.path
    else:
        # Relative redirect: resolve against the scoped base so parent segments
        # cannot walk above the binding mount.
        combined = f"{base}/{split.path}"
    normalized = posixpath.normpath(combined)
    if normalized != base and not normalized.startswith(base + "/"):
        # Traversal escaped the binding mount: never emit an out-of-scope
        # Location the browser would normalize to an arbitrary same-origin path.
        return base + "/"
    return normalized + suffix


async def _serve_native_ui(
    *,
    chat_binding_id: str,
    ui_path: str,
    embedded: Any,
    config: OmnigentBridgeConfig,
    user: User,
    service: Any,
    store: OmnigentBridgeSessionStore,
    upstream: NativeUiUpstream,
    artifact_service: TemporalArtifactService,
) -> Response:
    mode = presentation_mode_from_query(embedded)
    document = is_document_request(ui_path)

    # 1. Authorize the caller against the durable binding before anything else,
    #    so an unauthorized caller cannot even probe whether assets exist.
    try:
        row = await _resolve_and_authorize_chat_binding(
            chat_binding_id=chat_binding_id,
            operation="serve_native_ui",
            user=user,
            service=service,
            store=store,
        )
    except WorkflowChatFacadeError:
        # Non-enumerating: unknown binding and unauthorized caller collapse to
        # one unavailable state (never reveals whether a binding exists).
        return _native_chat_unavailable(
            mode=mode,
            is_document=document,
            reason="binding_unknown_or_unauthorized",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # 1b. Rollout gate (issue #3642 §10). A rolled-back (read_only), canary
    #     deployment without recorded acceptance evidence, or disabled posture
    #     never serves the interactive native UI: it fails closed to the same
    #     non-topology-revealing unavailable state the read-only diagnostics
    #     fallback presents, rather than silently routing through a different
    #     runtime or the legacy chat path.
    rollout_mode = resolved_native_chat_rollout_mode()
    acceptance_ref = resolved_native_chat_acceptance_ref()
    acceptance_recorded = False
    raw_rollout_mode = str(rollout_mode or "").strip().lower()
    if raw_rollout_mode in {"", "canary"}:
        acceptance_recorded = await _acceptance_report_is_current(
            acceptance_ref, artifact_service=artifact_service
        )
    rollout = resolve_native_chat_rollout(
        mode=rollout_mode,
        acceptance_recorded=acceptance_recorded,
    )
    record_native_chat_rollout(
        rollout_mode=rollout.mode.value,
        readiness=rollout.telemetry_readiness(),
    )
    if not rollout.serve_native_ui:
        record_native_chat_request(STAGE_NATIVE_UI_LOAD, OUTCOME_FAILURE)
        record_native_chat_request(STAGE_DIAGNOSTIC_FALLBACK, OUTCOME_SUCCESS)
        return _native_chat_unavailable(
            mode=mode, is_document=document, reason=rollout.reason
        )

    # 2. Gate on native-UI serving being enabled and a known-compatible version.
    serving_enabled = resolved_native_ui_serving_enabled()
    compatibility = evaluate_native_ui_compatibility(
        resolved_native_ui_version(),
        enabled=bool(config.enabled) and serving_enabled,
    )
    if not compatibility.ready:
        record_native_chat_request(STAGE_NATIVE_UI_COMPATIBILITY, OUTCOME_FAILURE)
        record_native_chat_request(STAGE_DIAGNOSTIC_FALLBACK, OUTCOME_SUCCESS)
        reason = (
            "native_ui_serving_disabled"
            if config.enabled and not serving_enabled
            else compatibility.reason or "native_ui_unavailable"
        )
        return _native_chat_unavailable(
            mode=mode, is_document=document, reason=reason
        )

    # 3. Reverse-proxy the upstream asset or SPA document.
    scoped_base = scoped_ui_base(chat_binding_id)
    try:
        response = await upstream.fetch(upstream_path_for(ui_path))
    except NativeUiUpstreamError:
        record_native_chat_request(STAGE_NATIVE_UI_LOAD, OUTCOME_FAILURE)
        record_native_chat_request(STAGE_DIAGNOSTIC_FALLBACK, OUTCOME_SUCCESS)
        logger.info(
            "omnigent.native_ui upstream unavailable binding=%s document=%s",
            chat_binding_id,
            document,
        )
        return _native_chat_unavailable(
            mode=mode, is_document=document, reason="native_ui_upstream_unavailable"
        )

    headers = native_ui_security_headers(mode=mode, is_document=document)

    if response.is_redirect and response.location is not None:
        return RedirectResponse(
            url=_rewrite_upstream_location(
                response.location, scoped_base=scoped_base
            ),
            status_code=response.status_code,
            headers=headers,
        )

    if not document:
        # Hashed static asset: proxy bytes unchanged under the scoped route.
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type=response.media_type,
            headers=headers,
        )

    if response.status_code >= 400:
        return _native_chat_unavailable(
            mode=mode, is_document=True, reason="native_ui_upstream_unavailable"
        )

    # 4. SPA document: inject the browser-safe binding-scoped bootstrap and scope
    #    the asset URLs so every subsequent request stays on the MoonMind origin.
    session_status = str(getattr(row, "status", "") or "")
    capability_set = _effective_capabilities(row, user)
    bootstrap = build_chat_bootstrap(
        chat_binding_id=chat_binding_id,
        mode=mode,
        read_only=is_read_only(session_status),
        capabilities=capability_set.capabilities,
        state=_facade_liveness_state(row),
        capability_schema_version=capability_set.schema_version,
        capability_authority_digest=capability_set.authority_digest,
        disabled_reasons=capability_set.disabled_reasons,
    )
    rendered = render_native_ui_document(
        response.content.decode("utf-8", "replace"),
        bootstrap=bootstrap,
        scoped_base=scoped_base,
    )
    record_native_chat_request(STAGE_NATIVE_UI_COMPATIBILITY, OUTCOME_SUCCESS)
    record_native_chat_request(STAGE_NATIVE_UI_LOAD, OUTCOME_SUCCESS)
    return HTMLResponse(content=rendered, status_code=200, headers=headers)


@native_ui_router.get(
    "/{chat_binding_id}",
    include_in_schema=False,
    operation_id="serve_native_workflow_chat_ui_root",
)
async def serve_native_workflow_chat_ui_root(
    chat_binding_id: str,
    embedded: str | None = Query(default=None),
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store_dep),
    upstream: NativeUiUpstream = Depends(get_native_ui_upstream),
    artifact_service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> Response:
    """Serve the native Omnigent SPA document for a binding (root route)."""

    return await _serve_native_ui(
        chat_binding_id=chat_binding_id,
        ui_path="",
        embedded=embedded,
        config=config,
        user=user,
        service=service,
        store=store,
        upstream=upstream,
        artifact_service=artifact_service,
    )


@native_ui_router.get(
    "/{chat_binding_id}/{ui_path:path}",
    include_in_schema=False,
    operation_id="serve_native_workflow_chat_ui_asset",
)
async def serve_native_workflow_chat_ui_asset(
    chat_binding_id: str,
    ui_path: str,
    embedded: str | None = Query(default=None),
    config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    user: User = Depends(get_current_user()),
    service: Any = Depends(_get_execution_service),
    store: OmnigentBridgeSessionStore = Depends(_get_bridge_store_dep),
    upstream: NativeUiUpstream = Depends(get_native_ui_upstream),
    artifact_service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> Response:
    """Serve a scoped native UI asset or an SPA deep-link/refresh document."""

    return await _serve_native_ui(
        chat_binding_id=chat_binding_id,
        ui_path=ui_path,
        embedded=embedded,
        config=config,
        user=user,
        service=service,
        store=store,
        upstream=upstream,
        artifact_service=artifact_service,
    )


__all__ = [
    "NATIVE_UI_MOUNT_PATH",
    "NativeUiUpstream",
    "NativeUiUpstreamError",
    "NativeUiUpstreamResponse",
    "get_native_ui_upstream",
    "native_ui_router",
]
