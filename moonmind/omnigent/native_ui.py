"""Native Omnigent Workflow Chat UI serving primitives.

MoonLadderStudios/MoonMind#3638. MoonMind serves the provider-maintained native
Omnigent web application as the Workflow Chat UI through MoonMind-controlled,
binding-scoped routes (``docs/UI/WorkflowChatPanel.md`` §4,
``docs/Omnigent/OmnigentBridge.md`` §4.2, §6 ``workflowChat``):

```text
/omnigent-ui/workflow-chat/{chatBindingId}[?embedded=1]
```

The browser loads the native UI assets and every application request exclusively
through MoonMind-scoped routes. It never connects directly to the upstream
Omnigent server and never receives a raw provider session id, upstream URL,
credential, host/runner id, profile ref, or workspace authority.

This module holds the runtime-neutral primitives the FastAPI serving router
composes so they can be unit-tested in isolation and the browser-boundary
semantics stay in one place:

* the browser-safe bootstrap contract (:func:`build_chat_bootstrap`);
* the native UI/server version compatibility gate
  (:func:`evaluate_native_ui_compatibility`);
* the embedded vs full-page security-header policy
  (:func:`native_ui_security_headers`);
* SPA-document vs hashed-asset request classification
  (:func:`is_document_request`, :func:`upstream_path_for`); and
* bootstrap injection and scoped asset-URL rewriting for the served document
  (:func:`render_native_ui_document`).

Keeping the mechanism here (not inline in the router) mirrors
``moonmind/omnigent/workflow_chat_facade.py``, the sibling binding-scoped HTTP/SSE
API facade (MoonLadderStudios/MoonMind#3634).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from moonmind.omnigent.host_auth_adapter import PINNED_OMNIGENT_COMMIT

# --- Route / contract constants ----------------------------------------------
# The native UI is served under this fixed, server-owned mount prefix. The
# browser never authors an upstream route; it only ever navigates within this
# binding-scoped prefix (docs/Omnigent/OmnigentBridge.md §6 ``uiMountPath``).
NATIVE_UI_MOUNT_PREFIX = "/omnigent-ui/workflow-chat"

# Stable browser bootstrap schema version. The native application reads the
# injected ``window.__MOONMIND_OMNIGENT_CHAT__`` object; a compatibility change
# to the contract shape bumps this version.
NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION = "moonmind.omnigent_native_ui.bootstrap.v1"

# Versioned scoped route/feature manifest. Bumped when the scoped serving routes
# or the bootstrap features the native UI depends on change in a way that
# requires a matching native UI build.
NATIVE_UI_ROUTE_FEATURE_VERSION = "1"

# The single upstream source pin MoonMind's native UI serving is verified
# against. Reuses the one upstream commit pin (host_auth_adapter) so there is no
# parallel native-UI version authority (Simplicity Gate / Compatibility Policy).
SUPPORTED_NATIVE_UI_VERSIONS: frozenset[str] = frozenset({PINNED_OMNIGENT_COMMIT})

# Stable, actionable failure code for an unknown/incompatible native UI version.
# The serving router returns this instead of partially bypassing the scoped
# facade (issue #3638 requirement 6).
CODE_NATIVE_CHAT_UNAVAILABLE = "omnigent_native_chat_unavailable"

PresentationMode = Literal["embedded", "full_page"]

_TRUE_QUERY_VALUES = {"1", "true", "yes", "on"}


def scoped_ui_base(chat_binding_id: str) -> str:
    """Return the binding-scoped native UI base path (no trailing slash)."""

    return f"{NATIVE_UI_MOUNT_PREFIX}/{chat_binding_id}"


def scoped_api_base(chat_binding_id: str) -> str:
    """Return the binding-scoped API/SSE facade base path (no trailing slash).

    Mirrors the value constructed by ``resolve_workflow_chat_binding`` so the
    served document and the ``chat-binding`` resolution API agree on one
    server-owned scoped base (MoonLadderStudios/MoonMind#3634).
    """

    return f"/api/workflow-chat-bindings/{chat_binding_id}/omnigent"


def presentation_mode_from_query(value: Any) -> PresentationMode:
    """Map the ``embedded`` query parameter to a presentation mode.

    ``embedded=1`` (or any truthy token) selects embedded (iframe/microfrontend)
    presentation; anything else — including a missing parameter — selects the
    full-page **Open in Omnigent** presentation.
    """

    token = str(value if value is not None else "").strip().lower()
    return "embedded" if token in _TRUE_QUERY_VALUES else "full_page"


# --- Version compatibility gate ----------------------------------------------


@dataclass(frozen=True, slots=True)
class NativeUiCompatibility:
    """Result of the native UI/server version compatibility gate."""

    ready: bool
    reported_version: str | None
    supported_versions: tuple[str, ...]
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "reportedVersion": self.reported_version,
            "supportedVersions": list(self.supported_versions),
            "reason": self.reason,
        }


def evaluate_native_ui_compatibility(
    reported_version: str | None,
    *,
    enabled: bool = True,
    supported_versions: frozenset[str] = SUPPORTED_NATIVE_UI_VERSIONS,
) -> NativeUiCompatibility:
    """Gate serving on a known-compatible native UI/server version.

    An unknown, blank, or unsupported version fails closed with an actionable
    ``native-chat-unavailable`` reason rather than partially serving a native UI
    that has not run conformance (issue #3638 requirement 6). Upgrading the
    upstream image is therefore a deliberate step: the operator pins the new
    version only after conformance adds it to ``supported_versions``.
    """

    ordered = tuple(sorted(supported_versions))
    if not enabled:
        return NativeUiCompatibility(
            ready=False,
            reported_version=reported_version,
            supported_versions=ordered,
            reason="omnigent_disabled",
        )
    version = str(reported_version or "").strip()
    if not version:
        return NativeUiCompatibility(
            ready=False,
            reported_version=None,
            supported_versions=ordered,
            reason="native_ui_version_unknown",
        )
    if version not in supported_versions:
        return NativeUiCompatibility(
            ready=False,
            reported_version=version,
            supported_versions=ordered,
            reason="native_ui_version_unsupported",
        )
    return NativeUiCompatibility(
        ready=True,
        reported_version=version,
        supported_versions=ordered,
        reason=None,
    )


# --- Browser-safe bootstrap contract -----------------------------------------


def build_chat_bootstrap(
    *,
    chat_binding_id: str,
    mode: PresentationMode,
    read_only: bool,
    capabilities: Mapping[str, bool],
    state: str,
    compatibility_version: str = NATIVE_UI_ROUTE_FEATURE_VERSION,
    unavailable_reason: str | None = None,
    labels: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the browser-safe native application bootstrap object (issue §2).

    The native application receives only browser-safe information: the opaque
    ``chatBindingId``, the scoped API base path, the presentation mode,
    read-only state, the filtered effective capability manifest with disabled
    reasons, safe display labels, and a stable compatibility version.

    ``wsBase`` is deliberately not advertised: the binding-scoped facade
    currently exposes only HTTP/SSE routes, so the bootstrap does not promise a
    WebSocket base until a binding-authorized WebSocket handler exists
    (MoonLadderStudios/MoonMind#3638).

    It deliberately never carries a raw provider session id, upstream endpoint,
    host, runner, credential, profile, launch policy, or workspace authority —
    those stay server-side and are only reachable through the separately
    authorized scoped facade.
    """

    filtered_caps = {
        str(key): bool(value) for key, value in dict(capabilities or {}).items()
    }
    disabled_reasons = {
        capability: (
            "session_read_only" if read_only else "policy_or_capability_denied"
        )
        for capability, allowed in filtered_caps.items()
        if not allowed
    }
    bootstrap: dict[str, Any] = {
        "schemaVersion": NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION,
        "chatBindingId": chat_binding_id,
        "apiBase": scoped_api_base(chat_binding_id),
        "mode": mode,
        "embedded": mode == "embedded",
        "readOnly": bool(read_only),
        "state": state,
        "capabilities": filtered_caps,
        "disabledReasons": disabled_reasons,
        "compatibilityVersion": compatibility_version,
        "labels": {str(k): v for k, v in dict(labels or {}).items()},
    }
    if unavailable_reason:
        bootstrap["unavailableReason"] = unavailable_reason
    return bootstrap


# --- Security header policy ---------------------------------------------------


def native_ui_security_headers(
    *, mode: PresentationMode, is_document: bool
) -> dict[str, str]:
    """Return the explicit CSP/frame/cache/referrer policy for a served response.

    Embedded mode is framed same-origin inside the MoonMind dashboard, so it
    permits ``frame-ancestors 'self'``; the full-page **Open in Omnigent** mode
    is a top-level navigation and refuses framing entirely
    (``frame-ancestors 'none'`` / ``X-Frame-Options: DENY``).

    A served *document* carries the binding-scoped bootstrap (which embeds the
    caller's effective capabilities and state), so it is never cached
    (``Cache-Control: no-store`` + ``Vary: Cookie``): one binding's bootstrap or
    private session data can never leak to another caller through a shared cache.
    Hashed static assets carry no binding data and are content-addressed, so they
    are privately cacheable behind the per-request authorization boundary.
    """

    if mode == "embedded":
        frame_ancestors = "'self'"
        x_frame_options = "SAMEORIGIN"
    else:
        frame_ancestors = "'none'"
        x_frame_options = "DENY"

    headers: dict[str, str] = {
        # Restrict framing and neutralize base-tag/redirect escapes to unscoped
        # upstream routes; ``connect-src 'self'`` keeps API/SSE traffic on the
        # MoonMind origin (the scoped facade), never the upstream server, so
        # provider JavaScript cannot open a fetch/XHR/EventSource/WebSocket
        # connection to an absolute upstream or external URL.
        "Content-Security-Policy": (
            f"frame-ancestors {frame_ancestors}; base-uri 'self'; "
            "form-action 'self'; connect-src 'self'"
        ),
        "X-Frame-Options": x_frame_options,
        "X-Content-Type-Options": "nosniff",
        # Never leak the scoped route (which contains the opaque binding id) to a
        # cross-origin destination through the Referer header.
        "Referrer-Policy": "same-origin",
    }
    if is_document:
        headers["Cache-Control"] = "no-store, private"
        headers["Vary"] = "Cookie"
    else:
        headers["Cache-Control"] = "private, max-age=300"
    return headers


# --- SPA document vs hashed asset classification -----------------------------

_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({".html", ".htm"})


def is_document_request(ui_path: str | None) -> bool:
    """Return whether a scoped request should be served the SPA document.

    The root route, any extension-less deep link, and explicit ``*.html`` all
    resolve to the SPA document so deep links and refreshes work
    (issue #3638 requirement 5). A path whose final segment has any other file
    extension is a hashed static asset to reverse-proxy from upstream.
    """

    path = str(ui_path or "").strip().strip("/")
    if not path:
        return True
    last_segment = path.rsplit("/", 1)[-1]
    if "." not in last_segment:
        return True
    extension = "." + last_segment.rsplit(".", 1)[-1].lower()
    return extension in _DOCUMENT_EXTENSIONS


def upstream_path_for(ui_path: str | None) -> str:
    """Map a scoped UI sub-path to the upstream Omnigent server path to fetch.

    SPA documents (root, deep links, refreshes) always fetch the upstream index
    document at ``/`` so the SPA shell is returned for client-side routing;
    hashed assets fetch their exact upstream path. Traversal segments are
    rejected by returning the safe index path so a caller can never escape the
    upstream asset root through the scoped route.
    """

    if is_document_request(ui_path):
        return "/"
    path = str(ui_path or "").strip().strip("/")
    if not path or ".." in path.split("/"):
        return "/"
    return "/" + path


# --- Bootstrap injection + scoped asset URL rewriting ------------------------

_ROOT_ABSOLUTE_ATTR = re.compile(r"""\b(src|href)=(["'])/(?!/)""")
_HEAD_OPEN = re.compile(r"<head[^>]*>", re.IGNORECASE)


def rewrite_asset_urls(html: str, *, scoped_base: str) -> str:
    """Rewrite root-absolute asset URLs onto the binding-scoped route.

    A stock SPA build references hashed assets with root-absolute URLs such as
    ``src="/assets/index-abc.js"``. Those ignore ``<base href>``, so they are
    rewritten to ``src="/omnigent-ui/workflow-chat/{id}/assets/index-abc.js"``
    and resolve back through the scoped serving route. Protocol-relative
    (``//host``) and absolute (``https://``) URLs are left untouched so a
    deliberate external resource is not silently rerouted.
    """

    base = scoped_base.rstrip("/")
    return _ROOT_ABSOLUTE_ATTR.sub(rf"\1=\g<2>{base}/", html)


def render_native_ui_document(
    upstream_html: str,
    *,
    bootstrap: Mapping[str, Any],
    scoped_base: str,
) -> str:
    """Return the served SPA document: scoped assets + injected bootstrap.

    Injects ``<base href>`` and the binding-scoped bootstrap object as the first
    children of ``<head>`` (so the native application reads its scoped API/WS
    base, presentation mode, and capabilities before any of its own scripts run)
    and rewrites root-absolute asset URLs onto the scoped route. The bootstrap
    JSON is HTML-escaped for ``</script>`` safety.
    """

    base = scoped_base.rstrip("/")
    payload = json.dumps(dict(bootstrap), separators=(",", ":")).replace(
        "</", "<\\/"
    )
    injected = (
        f'<base href="{base}/">'
        f"<script>window.__MOONMIND_OMNIGENT_CHAT__={payload};</script>"
    )
    rewritten = rewrite_asset_urls(upstream_html, scoped_base=base)
    match = _HEAD_OPEN.search(rewritten)
    if match is None:
        # No <head>: prepend the injection so the bootstrap still runs first.
        return injected + rewritten
    insert_at = match.end()
    return rewritten[:insert_at] + injected + rewritten[insert_at:]


__all__ = [
    "CODE_NATIVE_CHAT_UNAVAILABLE",
    "NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION",
    "NATIVE_UI_MOUNT_PREFIX",
    "NATIVE_UI_ROUTE_FEATURE_VERSION",
    "NativeUiCompatibility",
    "PresentationMode",
    "SUPPORTED_NATIVE_UI_VERSIONS",
    "build_chat_bootstrap",
    "evaluate_native_ui_compatibility",
    "is_document_request",
    "native_ui_security_headers",
    "presentation_mode_from_query",
    "render_native_ui_document",
    "rewrite_asset_urls",
    "scoped_api_base",
    "scoped_ui_base",
    "upstream_path_for",
]
