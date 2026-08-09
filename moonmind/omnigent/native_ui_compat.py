"""Versioned native Omnigent UI network-surface compatibility map (MoonLadderStudios/MoonMind#3635).

Issue #3634 gave the binding-scoped Workflow Chat facade its HTTP + SSE surface
(:mod:`moonmind.omnigent.workflow_chat_facade`). The native Omnigent web
application is more than an HTTP transcript, though: it also opens WebSockets,
creates and attaches terminals/PTYs, inspects execution logs, browses workspace
state, opens browser panes, and interacts with sub-agent/task surfaces
(``docs/Omnigent/OmnigentBridge.md`` §4.2 step list, §11, §21 Q1).

This module owns the **transport and route coverage** contract for that surface:
a single, *versioned* inventory of every network route and transport class the
supported native UI can use, pinned to the ``omnigent.server.v1`` compatibility
profile. It exists so the facade is never a generic open reverse proxy and so an
upstream upgrade that adds or changes a route produces an explicit compatibility
diagnostic until reviewed, rather than being proxied by default (issue §1,
acceptance criteria 1 and 8).

Two dispositions are expressed per route:

* ``served`` — the facade actively serves this route today (the HTTP/SSE surface
  owned by #3634). These entries are derived from
  :data:`moonmind.omnigent.workflow_chat_facade.FACADE_OPERATIONS` so there is a
  single source of truth for the served HTTP allowlist; this module never
  re-declares them.
* ``compatibility_review_required`` — a reserved disposition for a recognized
  route whose upstream contract has not been reviewed. The pinned WebSocket
  routes below are reviewed and served through the binding-scoped relay; future
  or changed routes remain absent and therefore fail closed.

The classifier is pure and side-effect-free so it can be unit-tested in
isolation and reused by the FastAPI router (both the HTTP dispatcher and the
WebSocket handler).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from moonmind.omnigent.workflow_chat_facade import (
    CAP_READ_RESOURCES,
    CAP_VIEW_TRANSCRIPT,
    FACADE_OPERATIONS,
    FacadeOperation,
    WorkflowChatFacadeError,
)

# The pinned supported native Omnigent UI/server compatibility profile. The map
# is versioned by this value; a different upstream profile must be reviewed
# before its routes are added, matching ``compatibility.profile`` in the bridge
# config (``omnigent.server.v1``).
NATIVE_UI_COMPAT_VERSION = "omnigent.server.v1"

# --- Transports ---------------------------------------------------------------
TRANSPORT_HTTP = "http"
TRANSPORT_SSE = "sse"
TRANSPORT_WEBSOCKET = "websocket"

# --- Dispositions -------------------------------------------------------------
# The facade serves this route today.
DISPOSITION_SERVED = "served"
# The route/transport is recognized but its upstream rewrite is unresolved; it
# fails closed with a compatibility diagnostic instead of being proxied.
DISPOSITION_COMPAT_REVIEW = "compatibility_review_required"

# --- Operation classes (issue §1 inventory) -----------------------------------
CLASS_LIVENESS = "liveness"
CLASS_RECONNECT = "reconnect"
CLASS_SESSION_READ = "session_read"
CLASS_STREAM = "event_stream"
CLASS_CONTROL = "control"
CLASS_RESOURCE_READ = "resource_read"
CLASS_RESOURCE_MUTATE = "resource_mutate"
CLASS_TERMINAL_VIEW = "terminal_view"
CLASS_TERMINAL_CREATE = "terminal_create"
CLASS_TERMINAL_ATTACH = "terminal_attach"
CLASS_TERMINAL_INPUT = "terminal_input"
CLASS_TERMINAL_RESIZE = "terminal_resize"
CLASS_TERMINAL_CLOSE = "terminal_close"
CLASS_EXEC_LOG = "execution_log"
CLASS_BROWSER_PANE = "browser_pane"
CLASS_SUBAGENT = "subagent_tree"
CLASS_TASK = "task_todo"

# --- Capability keys the facade never grants (mirror workflow_chat_facade) -----
# Terminal create/attach/input/resize/close and workspace mutation each require a
# distinct authority the browser facade does not carry; they always fail closed.
CAP_CREATE_TERMINAL = "createTerminal"
CAP_WRITE_TERMINAL = "writeTerminal"
CAP_MUTATE_WORKSPACE = "mutateWorkspace"

# --- Stable, non-enumerating compatibility diagnostic codes -------------------
CODE_COMPAT_REVIEW_REQUIRED = "omnigent_chat_compat_review_required"
CODE_TRANSPORT_UNSUPPORTED = "omnigent_chat_transport_unsupported"
CODE_WS_SUBPROTOCOL_REJECTED = "omnigent_chat_ws_subprotocol_rejected"

# The only WebSocket subprotocol the native chat surface may negotiate. An
# offered subprotocol outside this allowlist is rejected before upgrade so the
# browser cannot select an unbounded upstream protocol.
NATIVE_UI_WS_SUBPROTOCOLS: tuple[str, ...] = ("omnigent.workflow-chat.v1",)
NATIVE_UI_PAYLOAD_CLASSES: tuple[str, ...] = ("json", "text", "binary", "multipart")
NATIVE_UI_URL_BEHAVIORS: tuple[str, ...] = (
    "route_relative_redirect",
    "asset_base_discovery",
    "api_base_discovery",
)


class NativeUiCompatibilityError(WorkflowChatFacadeError):
    """A recognized native-UI transport whose upstream rewrite needs review.

    Subclasses :class:`WorkflowChatFacadeError` so the router's existing
    bridge-error-to-HTTP mapping renders one consistent, non-enumerating
    envelope. Raised (or projected to a WebSocket close) when a recognized route
    is not yet authorized for proxy in the pinned compatibility profile.
    """


# ``session_id`` capture used by every session-scoped native-UI route.
_SESSION = r"(?P<session_id>[^/]+)"
_TERMINAL = r"(?P<terminal_id>[^/]+)"


@dataclass(frozen=True, slots=True)
class NativeUiRoute:
    """One route/transport class in the versioned native-UI compatibility map."""

    name: str
    transport: str
    methods: tuple[str, ...]
    operation_class: str
    disposition: str
    # Capability required to invoke the operation. ``None`` means no capability
    # gate beyond binding ownership (e.g. liveness). For ``served`` HTTP routes
    # the capability is authoritative in ``workflow_chat_facade``; it is mirrored
    # here for the versioned inventory only.
    capability: str | None = CAP_VIEW_TRANSCRIPT
    mutation: bool = False
    # WebSocket subprotocol allowlist (empty for HTTP/SSE routes).
    subprotocols: tuple[str, ...] = ()
    # Compiled path matcher; ``None`` for the served entries whose matching is
    # owned by ``workflow_chat_facade.match_facade_operation``.
    pattern: re.Pattern[str] | None = None


@dataclass(frozen=True, slots=True)
class NativeUiRouteMatch:
    """A classified native-UI request."""

    route: NativeUiRoute
    params: dict[str, str] = field(default_factory=dict)


# Map a served HTTP/SSE FacadeOperation name to its operation class + transport
# for the versioned inventory. This keeps the served surface single-sourced from
# ``FACADE_OPERATIONS`` while still describing it in native-UI terms.
_SERVED_OPERATION_CLASS: dict[str, tuple[str, str]] = {
    "liveness": (CLASS_LIVENESS, TRANSPORT_HTTP),
    "list_agents": (CLASS_SESSION_READ, TRANSPORT_HTTP),
    "get_session": (CLASS_SESSION_READ, TRANSPORT_HTTP),
    "stream_events": (CLASS_STREAM, TRANSPORT_SSE),
    "post_event": (CLASS_CONTROL, TRANSPORT_HTTP),
    "resolve_elicitation": (CLASS_CONTROL, TRANSPORT_HTTP),
    "changed_files": (CLASS_RESOURCE_READ, TRANSPORT_HTTP),
    "workspace_files": (CLASS_RESOURCE_READ, TRANSPORT_HTTP),
    "workspace_file": (CLASS_RESOURCE_READ, TRANSPORT_HTTP),
    "workspace_diff": (CLASS_RESOURCE_READ, TRANSPORT_HTTP),
    "session_files": (CLASS_RESOURCE_READ, TRANSPORT_HTTP),
    "session_file": (CLASS_RESOURCE_READ, TRANSPORT_HTTP),
}


def _served_route(operation: FacadeOperation) -> NativeUiRoute:
    operation_class, transport = _SERVED_OPERATION_CLASS.get(
        operation.name, (CLASS_SESSION_READ, TRANSPORT_HTTP)
    )
    if operation.sse:
        transport = TRANSPORT_SSE
    return NativeUiRoute(
        name=operation.name,
        transport=transport,
        methods=(operation.method,),
        operation_class=operation_class,
        disposition=DISPOSITION_SERVED,
        capability=operation.capability,
        mutation=operation.mutation,
    )


# Served HTTP/SSE routes, derived (never re-declared) from the #3634 allowlist.
_SERVED_ROUTES: tuple[NativeUiRoute, ...] = tuple(
    _served_route(operation) for operation in FACADE_OPERATIONS
)


def _ws(path: str, **kwargs: Any) -> NativeUiRoute:
    disposition = str(kwargs.pop("disposition", DISPOSITION_SERVED))
    return NativeUiRoute(
        transport=TRANSPORT_WEBSOCKET,
        methods=("GET",),
        disposition=disposition,
        subprotocols=NATIVE_UI_WS_SUBPROTOCOLS,
        pattern=re.compile("^" + path + "$"),
        **kwargs,
    )


def _reviewed_http(
    path: str,
    *,
    name: str,
    methods: tuple[str, ...],
    operation_class: str,
    capability: str | None = CAP_VIEW_TRANSCRIPT,
    mutation: bool = False,
) -> NativeUiRoute:
    """Inventory a pinned stock route which is not yet exposed by the facade.

    Keeping these entries in the compatibility contract is important even while
    they fail closed: omission would make an upstream route indistinguishable
    from an undiscovered route and allowed the old completeness test to pass
    with a three-entry map.
    """

    return NativeUiRoute(
        name=name,
        transport=TRANSPORT_HTTP,
        methods=methods,
        operation_class=operation_class,
        disposition=DISPOSITION_COMPAT_REVIEW,
        capability=capability,
        mutation=mutation,
        pattern=re.compile("^" + path + "$"),
    )


# Network contract used by the pinned native workspace UI beyond the #3634
# transcript facade.  The paths are taken from the vendored server route set;
# each unsupported operation is deliberately visible as review-required rather
# than disappearing from the inventory or falling through to a generic proxy.
_PINNED_HTTP_ROUTES: tuple[NativeUiRoute, ...] = (
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/terminals", name="terminal_view", methods=("GET",), operation_class=CLASS_TERMINAL_VIEW, capability=CAP_READ_RESOURCES),
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/terminals", name="terminal_create", methods=("POST",), operation_class=CLASS_TERMINAL_CREATE, capability=CAP_CREATE_TERMINAL, mutation=True),
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/terminals/{_TERMINAL}", name="terminal_status", methods=("GET",), operation_class=CLASS_TERMINAL_VIEW, capability=CAP_READ_RESOURCES),
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/terminals/{_TERMINAL}/transfer", name="terminal_input", methods=("POST",), operation_class=CLASS_TERMINAL_INPUT, capability=CAP_WRITE_TERMINAL, mutation=True),
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/terminals/{_TERMINAL}", name="terminal_resize", methods=("PATCH",), operation_class=CLASS_TERMINAL_RESIZE, capability=CAP_WRITE_TERMINAL, mutation=True),
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/terminals/{_TERMINAL}", name="terminal_close", methods=("DELETE",), operation_class=CLASS_TERMINAL_CLOSE, capability=CAP_WRITE_TERMINAL, mutation=True),
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/environments/default/shell", name="terminal_shell", methods=("POST",), operation_class=CLASS_TERMINAL_CREATE, capability=CAP_CREATE_TERMINAL, mutation=True),
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/terminals/{_TERMINAL}/logs", name="execution_logs", methods=("GET",), operation_class=CLASS_EXEC_LOG, capability=CAP_READ_RESOURCES),
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/environments/default/filesystem/(?P<res_path>.+)", name="workspace_edit", methods=("PUT", "PATCH"), operation_class=CLASS_RESOURCE_MUTATE, capability=CAP_MUTATE_WORKSPACE, mutation=True),
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/environments/default/filesystem/(?P<res_path>.+)", name="workspace_delete", methods=("DELETE",), operation_class=CLASS_RESOURCE_MUTATE, capability=CAP_MUTATE_WORKSPACE, mutation=True),
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/files", name="resource_upload", methods=("POST",), operation_class=CLASS_RESOURCE_MUTATE, capability=CAP_MUTATE_WORKSPACE, mutation=True),
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/files/(?P<file_id>[^/]+)/content", name="resource_download", methods=("GET",), operation_class=CLASS_RESOURCE_READ, capability=CAP_READ_RESOURCES),
    _reviewed_http(rf"v1/sessions/{_SESSION}/resources/files/(?P<file_id>[^/]+)/attach", name="resource_attach", methods=("POST",), operation_class=CLASS_RESOURCE_MUTATE, capability=CAP_MUTATE_WORKSPACE, mutation=True),
    _reviewed_http(rf"v1/sessions/{_SESSION}/browser(?:/.*)?", name="browser_pane", methods=("GET", "POST", "DELETE"), operation_class=CLASS_BROWSER_PANE, capability=CAP_MUTATE_WORKSPACE, mutation=True),
    _reviewed_http(rf"v1/sessions/{_SESSION}/subagents(?:/.*)?", name="subagent_tree", methods=("GET", "POST"), operation_class=CLASS_SUBAGENT, capability=CAP_VIEW_TRANSCRIPT),
    _reviewed_http(rf"v1/sessions/{_SESSION}/tasks(?:/.*)?", name="task_todo", methods=("GET", "POST", "PATCH"), operation_class=CLASS_TASK, capability=CAP_VIEW_TRANSCRIPT),
    _reviewed_http(r"v1/hosts", name="host_liveness", methods=("GET",), operation_class=CLASS_LIVENESS, capability=None),
    _reviewed_http(r"v1/runners/[^/]+/status", name="runner_liveness", methods=("GET",), operation_class=CLASS_LIVENESS, capability=None),
    _reviewed_http(rf"v1/sessions/{_SESSION}/reconnect", name="session_reconnect", methods=("POST",), operation_class=CLASS_RECONNECT, capability=CAP_VIEW_TRANSCRIPT),
)


# Reviewed native-UI WebSocket transport classes. Each is authenticated,
# authorized, capability-checked, binding-validated, and identity-virtualized
# before the server-owned upstream relay is opened.
_WEBSOCKET_ROUTES: tuple[NativeUiRoute, ...] = (
    # The stock UI's global list/update channel carries caller-authored watch
    # sets and therefore remains closed until per-frame alias filtering lands.
    _ws(
        r"v1/sessions/updates",
        name="ws_session_updates",
        operation_class=CLASS_STREAM,
        capability=CAP_VIEW_TRANSCRIPT,
        disposition=DISPOSITION_COMPAT_REVIEW,
    ),
    # The only session-scoped WebSocket in the pinned server contract.
    _ws(
        rf"v1/sessions/{_SESSION}/resources/terminals/{_TERMINAL}/attach",
        name="terminal_attach",
        operation_class=CLASS_TERMINAL_ATTACH,
        capability=CAP_WRITE_TERMINAL,
        mutation=True,
    ),
    # Dictation is binary input and needs a separately reviewed audio policy.
    _ws(
        r"v1/dictation/stream",
        name="dictation_stream",
        operation_class=CLASS_CONTROL,
        capability=CAP_MUTATE_WORKSPACE,
        mutation=True,
        disposition=DISPOSITION_COMPAT_REVIEW,
    ),
)


# The full versioned inventory: served HTTP/SSE + recognized WebSocket classes.
NATIVE_UI_ROUTES: tuple[NativeUiRoute, ...] = (
    _SERVED_ROUTES + _PINNED_HTTP_ROUTES + _WEBSOCKET_ROUTES
)


def classify_native_ui_websocket(path: str) -> NativeUiRouteMatch | None:
    """Classify a native-UI WebSocket sub-path, or return ``None`` if unknown.

    ``path`` is the portion after ``/omnigent/`` (no leading slash). A ``None``
    result means the transport class is not in the pinned compatibility map: the
    router closes with a non-enumerating diagnostic rather than proxying an
    unknown upstream WebSocket (issue §1, acceptance criterion 8).
    """

    candidate = str(path or "").strip().strip("/")
    for route in _WEBSOCKET_ROUTES:
        assert route.pattern is not None  # WebSocket routes always compile a pattern
        matched = route.pattern.match(candidate)
        if matched is not None:
            params = dict(matched.groupdict())
            params["_browser_path"] = candidate
            return NativeUiRouteMatch(route=route, params=params)
    return None


def upstream_websocket_path(match: NativeUiRouteMatch, provider_session_id: str) -> str:
    """Return the reviewed upstream path with the opaque browser id virtualized.

    The compatibility map is the only route allowlist.  Replacing the captured
    session segment here (rather than forwarding the browser path) prevents a
    path alias from becoming provider authority.
    """

    candidate = match.route.pattern.pattern if match.route.pattern else ""
    del candidate  # make it explicit that regex source is never used as a URL
    browser_path = str(match.params.get("_browser_path", ""))
    if not browser_path:
        raise ValueError("classified websocket path is missing its source path")
    browser_session = str(match.params.get("session_id") or "")
    if browser_session:
        parts = browser_path.split("/")
        try:
            index = parts.index(browser_session)
        except ValueError as exc:
            raise ValueError("classified websocket session is not in path") from exc
        parts[index] = provider_session_id
        browser_path = "/".join(parts)
    return "/" + browser_path.lstrip("/")


def negotiate_ws_subprotocol(
    offered: list[str] | tuple[str, ...] | None,
    *,
    route: NativeUiRoute | None = None,
) -> str | None:
    """Return the single allowed subprotocol to accept, or raise if none match.

    A native chat WebSocket must negotiate one of :data:`NATIVE_UI_WS_SUBPROTOCOLS`.
    When the browser offers no subprotocol, ``None`` is returned (accept without
    one). When it offers subprotocols but none are allowlisted, the connection is
    rejected before upgrade so the browser cannot select an unbounded protocol.
    """

    allow = tuple(route.subprotocols) if route and route.subprotocols else NATIVE_UI_WS_SUBPROTOCOLS
    offered_list = [str(item).strip() for item in (offered or []) if str(item).strip()]
    if not offered_list:
        return None
    for candidate in offered_list:
        if candidate in allow:
            return candidate
    raise NativeUiCompatibilityError(
        "The WebSocket subprotocol is not permitted for this binding.",
        failure_class="user_error",
        status_code=403,
        code=CODE_WS_SUBPROTOCOL_REJECTED,
    )


def compatibility_map() -> dict[str, Any]:
    """Return the versioned native-UI compatibility map for diagnostics/tests.

    A bounded, non-secret projection of :data:`NATIVE_UI_ROUTES`. Consumers use
    it to prove the supported surface is fully inventoried and that every
    recognized transport declares an explicit disposition (issue acceptance
    criterion 1).
    """

    return {
        "version": NATIVE_UI_COMPAT_VERSION,
        "transports": sorted({route.transport for route in NATIVE_UI_ROUTES}),
        "wsSubprotocols": list(NATIVE_UI_WS_SUBPROTOCOLS),
        "payloadClasses": list(NATIVE_UI_PAYLOAD_CLASSES),
        "urlBehaviors": list(NATIVE_UI_URL_BEHAVIORS),
        "routes": [
            {
                "name": route.name,
                "transport": route.transport,
                "methods": list(route.methods),
                "operationClass": route.operation_class,
                "disposition": route.disposition,
                "capability": route.capability,
                "mutation": route.mutation,
            }
            for route in NATIVE_UI_ROUTES
        ],
    }


__all__ = [
    "CAP_CREATE_TERMINAL",
    "CAP_MUTATE_WORKSPACE",
    "CAP_WRITE_TERMINAL",
    "CLASS_BROWSER_PANE",
    "CLASS_CONTROL",
    "CLASS_EXEC_LOG",
    "CLASS_LIVENESS",
    "CLASS_RECONNECT",
    "CLASS_RESOURCE_MUTATE",
    "CLASS_RESOURCE_READ",
    "CLASS_SESSION_READ",
    "CLASS_STREAM",
    "CLASS_SUBAGENT",
    "CLASS_TASK",
    "CLASS_TERMINAL_ATTACH",
    "CLASS_TERMINAL_CLOSE",
    "CLASS_TERMINAL_CREATE",
    "CLASS_TERMINAL_INPUT",
    "CLASS_TERMINAL_RESIZE",
    "CLASS_TERMINAL_VIEW",
    "CODE_COMPAT_REVIEW_REQUIRED",
    "CODE_TRANSPORT_UNSUPPORTED",
    "CODE_WS_SUBPROTOCOL_REJECTED",
    "DISPOSITION_COMPAT_REVIEW",
    "DISPOSITION_SERVED",
    "NATIVE_UI_COMPAT_VERSION",
    "NATIVE_UI_ROUTES",
    "NATIVE_UI_WS_SUBPROTOCOLS",
    "NativeUiCompatibilityError",
    "NativeUiRoute",
    "NativeUiRouteMatch",
    "TRANSPORT_HTTP",
    "TRANSPORT_SSE",
    "TRANSPORT_WEBSOCKET",
    "classify_native_ui_websocket",
    "compatibility_map",
    "negotiate_ws_subprotocol",
    "upstream_websocket_path",
]
