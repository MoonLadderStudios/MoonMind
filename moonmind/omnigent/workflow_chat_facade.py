"""Binding-scoped Workflow Chat facade primitives (MoonLadderStudios/MoonMind#3634).

The native Omnigent web application never calls the provider/service session API
with a caller-selected provider session id. It receives an opaque MoonMind
``chatBindingId`` and drives one binding-scoped facade
(``docs/Omnigent/OmnigentBridge.md`` §4.2, §5.2):

```text
/api/workflow-chat-bindings/{chatBindingId}/omnigent/{path}
```

This module holds the runtime-neutral primitives the FastAPI facade router
composes: the explicit route/method allowlist for the selected compatibility
profile (the facade is never a generic open reverse proxy), the per-request
identity-substitution guard, the capability recompute, and the stable, redacted
error codes. Keeping these decisions here (rather than inline in the router)
lets them be unit-tested in isolation and keeps the browser-boundary semantics
in one place.

Binding resolution note: the opaque ``chat_binding_id`` column described in
§7.1 and its allocation are owned by MoonLadderStudios/MoonMind#3633. Until that
lands, the facade resolves ``chatBindingId`` through the durable bridge session
(``bridge_session_id`` — itself an opaque, server-owned MoonMind key that maps
to the workflow owner, provider session id, and upstream endpoint). The
provider session id and upstream endpoint stay server-side either way, so the
browser-facing contract in this module does not change when a dedicated
``chat_binding_id`` lookup is introduced.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from moonmind.omnigent.bridge_proxy import OmnigentBridgeError

# Coarse session statuses that make a binding read-only. A read-only binding
# still serves transcript/resource reads but rejects every mutation before any
# upstream forward (OB-§4.2, §16). Kept aligned with the terminal set the bridge
# store coalesces from normalized provider statuses.
TERMINAL_SESSION_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "canceled", "cancelled", "timed_out", "stopped"}
)


# --- Stable, redacted facade error codes (OB-§4.2 step list, brief §6) --------
CODE_BINDING_UNKNOWN = "omnigent_chat_binding_unknown"
CODE_CALLER_UNAUTHORIZED = "omnigent_chat_caller_unauthorized"
CODE_ROUTE_NOT_ALLOWLISTED = "omnigent_chat_route_not_allowlisted"
CODE_OPERATION_DENIED = "omnigent_chat_operation_denied"
CODE_SESSION_SUBSTITUTION = "omnigent_chat_session_substitution"
CODE_IDENTITY_SUBSTITUTION = "omnigent_chat_identity_substitution"
CODE_SESSION_NOT_READY = "omnigent_chat_session_not_ready"
CODE_SESSION_READ_ONLY = "omnigent_chat_session_read_only"
CODE_UNSUPPORTED_MEDIA_TYPE = "omnigent_chat_unsupported_media_type"
CODE_PAYLOAD_TOO_LARGE = "omnigent_chat_payload_too_large"
CODE_MALFORMED_PAYLOAD = "omnigent_chat_malformed_payload"


class WorkflowChatFacadeError(OmnigentBridgeError):
    """Typed, redacted binding-scoped facade error.

    Subclasses :class:`OmnigentBridgeError` so the router's existing
    bridge-error-to-HTTP mapping renders one consistent, non-enumerating error
    envelope for both facades.
    """


# --- Capability keys the browser projection understands (WorkflowChatPanel §5)
CAP_VIEW_TRANSCRIPT = "viewTranscript"
CAP_SEND_MESSAGE = "sendMessage"
CAP_INTERRUPT_TURN = "interruptTurn"
CAP_RESOLVE_ELICITATION = "resolveElicitation"
CAP_READ_RESOURCES = "readResources"

# Capabilities the facade intentionally never grants to the browser. These are
# authority/immutability boundaries (terminal creation, workspace mutation,
# per-session model/effort/goal changes) that require separate authorization the
# facade does not carry; they are always reported false so the native UI hides
# the affordance and the server rejects the operation if attempted.
_ALWAYS_DENIED_CAPABILITIES: tuple[str, ...] = (
    "createTerminal",
    "writeTerminal",
    "mutateWorkspace",
    "changeModel",
    "changeEffort",
    "changeGoal",
)


def recompute_capabilities(status: str | None) -> dict[str, bool]:
    """Recompute effective capabilities from trusted session state (OB-§4.2 step 6).

    Capabilities are never trusted from mutable browser state; they are derived
    from the durable binding's coarse status on every request. A terminal
    session is read-only: transcript and resource reads remain, all mutations
    are denied.
    """

    read_only = str(status or "").strip().lower() in TERMINAL_SESSION_STATUSES
    capabilities = {
        CAP_VIEW_TRANSCRIPT: True,
        CAP_READ_RESOURCES: True,
        CAP_SEND_MESSAGE: not read_only,
        CAP_INTERRUPT_TURN: not read_only,
        CAP_RESOLVE_ELICITATION: not read_only,
    }
    for denied in _ALWAYS_DENIED_CAPABILITIES:
        capabilities[denied] = False
    return capabilities


def is_read_only(status: str | None) -> bool:
    return str(status or "").strip().lower() in TERMINAL_SESSION_STATUSES


# --- Route / method allowlist (OB-§4.1 compatibility matrix, §4.2) ------------
# The facade exposes only the Omnigent-shaped operations the native application
# needs. Every entry is an explicit (method, path-pattern) pair; anything not
# matched here is rejected with a non-enumerating error. Session lifecycle
# (create/attach/delete) is deliberately excluded: the browser receives a
# pre-created binding and never authors session creation through the facade.

_SESSION = r"(?P<session_id>[^/]+)"


@dataclass(frozen=True, slots=True)
class FacadeOperation:
    """One allowlisted binding-scoped operation."""

    name: str
    method: str
    pattern: re.Pattern[str]
    # Capability required to invoke the operation. ``None`` means no capability
    # gate beyond ownership (e.g. liveness). For ``post_event`` the capability
    # depends on the event type and is resolved separately.
    capability: str | None = CAP_VIEW_TRANSCRIPT
    # Whether the sub-path names a provider session that must map to the bound
    # session (i.e. the ``{session_id}`` segment must equal the chatBindingId).
    references_session: bool = True
    # Whether the operation requires an attached provider session id upstream.
    requires_provider_session: bool = True
    mutation: bool = False
    sse: bool = False
    binary: bool = False


def _op(path: str, **kwargs: Any) -> FacadeOperation:
    return FacadeOperation(pattern=re.compile("^" + path + "$"), **kwargs)


FACADE_OPERATIONS: tuple[FacadeOperation, ...] = (
    # Liveness/reconnect probe — served locally, never forwarded upstream.
    _op(
        r"health",
        name="liveness",
        method="GET",
        capability=None,
        references_session=False,
        requires_provider_session=False,
    ),
    # Session catalog / metadata required to bootstrap the native UI.
    _op(
        r"v1/agents",
        name="list_agents",
        method="GET",
        references_session=False,
        requires_provider_session=False,
    ),
    # Session snapshot / bootstrap / history.
    _op(rf"v1/sessions/{_SESSION}", name="get_session", method="GET"),
    # Live/replay stream (SSE) — served from the durable bridge journal so
    # Last-Event-ID cursor semantics and mid-stream reauthorization apply.
    _op(
        rf"v1/sessions/{_SESSION}/stream",
        name="stream_events",
        method="GET",
        sse=True,
        requires_provider_session=False,
    ),
    # Message and supported control events. Capability is resolved per event
    # type (and read-only state) by the router, so no generic gate applies here.
    _op(
        rf"v1/sessions/{_SESSION}/events",
        name="post_event",
        method="POST",
        capability=None,
        mutation=True,
    ),
    # Elicitation / approval resolution — capability + read-only gate applied by
    # the router.
    _op(
        rf"v1/sessions/{_SESSION}/elicitations/(?P<elicitation_id>[^/]+)/resolve",
        name="resolve_elicitation",
        method="POST",
        capability=None,
        mutation=True,
    ),
    # Read-only resource indexes and content.
    _op(
        rf"v1/sessions/{_SESSION}/resources/environments/default/changes",
        name="changed_files",
        method="GET",
        capability=CAP_READ_RESOURCES,
    ),
    _op(
        rf"v1/sessions/{_SESSION}/resources/environments/default/filesystem",
        name="workspace_files",
        method="GET",
        capability=CAP_READ_RESOURCES,
    ),
    _op(
        rf"v1/sessions/{_SESSION}/resources/environments/default/filesystem/"
        r"(?P<res_path>.+)",
        name="workspace_file",
        method="GET",
        capability=CAP_READ_RESOURCES,
        binary=True,
    ),
    _op(
        rf"v1/sessions/{_SESSION}/resources/environments/default/diff/"
        r"(?P<res_path>.+)",
        name="workspace_diff",
        method="GET",
        capability=CAP_READ_RESOURCES,
        binary=True,
    ),
    _op(
        rf"v1/sessions/{_SESSION}/resources/files",
        name="session_files",
        method="GET",
        capability=CAP_READ_RESOURCES,
    ),
    _op(
        rf"v1/sessions/{_SESSION}/resources/files/(?P<file_id>[^/]+)/content",
        name="session_file",
        method="GET",
        capability=CAP_READ_RESOURCES,
        binary=True,
    ),
)


@dataclass(frozen=True, slots=True)
class FacadeMatch:
    operation: FacadeOperation
    params: dict[str, str] = field(default_factory=dict)


def match_facade_operation(method: str, path: str) -> FacadeMatch | None:
    """Return the allowlisted operation for a method + sub-path, or ``None``.

    ``path`` is the portion after ``/omnigent/`` (no leading slash). Any method
    or path not explicitly allowlisted returns ``None`` so the router can answer
    with one non-enumerating rejection (OB-§4.2: no generic open reverse proxy;
    unknown targets do not reveal upstream session existence).
    """

    normalized = str(method or "").strip().upper()
    candidate = str(path or "").strip().strip("/")
    for operation in FACADE_OPERATIONS:
        if operation.method != normalized:
            continue
        matched = operation.pattern.match(candidate)
        if matched is not None:
            return FacadeMatch(operation=operation, params=dict(matched.groupdict()))
    return None


def required_capability_for_event(event_type: str | None) -> str:
    """Map a composer/control event type to the capability it requires."""

    raw = str(event_type or "").strip().lower()
    if raw == "interrupt":
        return CAP_INTERRUPT_TURN
    # message, stop, clear/reset, cleanup, and other controls the composer
    # offers are gated by sendMessage authority.
    return CAP_SEND_MESSAGE


# --- Identity-substitution guard (OB-§4.2 steps 4-5, brief §3) ----------------
# Structural keys that name an upstream/topology/authority identity the browser
# must never be able to inject through path, query, body, or header. A message
# body legitimately never carries these; their presence is an injection attempt
# and fails closed before any upstream forward. Bare session ids are handled
# separately (they may echo the virtual/bound id but never a different one).
_FORBIDDEN_IDENTITY_KEYS: frozenset[str] = frozenset(
    {
        "provider_session_id",
        "providersessionid",
        "endpoint",
        "endpoint_ref",
        "endpointref",
        "base_url",
        "baseurl",
        "upstream",
        "upstream_url",
        "upstreamurl",
        "server_url",
        "serverurl",
        "host",
        "host_id",
        "hostid",
        "runner",
        "runner_id",
        "runnerid",
        "workspace",
        "workspace_root",
        "workspaceroot",
        "workspace_path",
        "workspacepath",
        "agent_profile",
        "agentprofile",
        "agent_profile_ref",
        "provider_profile",
        "providerprofile",
        "provider_profile_id",
        "launch_policy",
        "launchpolicy",
        "launch_policy_ref",
        "model",
        "model_override",
        "modeloverride",
        "effort",
        "reasoning_effort",
        "reasoningeffort",
        "goal",
        "credential",
        "credentials",
        "bridge_session_id",
        "bridgesessionid",
        "workflow_id",
        "workflowid",
        "agent_run_id",
        "agentrunid",
        "endpointref",
    }
)
# Keys that name a session id. Their value may only be the bound chatBindingId.
_SESSION_ID_KEYS: frozenset[str] = frozenset(
    {"session_id", "sessionid", "session"}
)
# Request headers that attempt to name an upstream identity. sanitize_proxy
# already prevents credential headers from crossing the boundary; these are
# rejected outright (and audited) rather than silently dropped, because their
# presence is an explicit substitution attempt.
_FORBIDDEN_IDENTITY_HEADERS: frozenset[str] = frozenset(
    {
        "x-omnigent-session",
        "x-omnigent-session-id",
        "x-provider-session-id",
        "x-omnigent-endpoint",
        "x-omnigent-endpoint-ref",
        "x-upstream-url",
        "x-omnigent-host",
        "x-omnigent-host-id",
        "x-omnigent-runner",
        "x-omnigent-runner-id",
        "x-omnigent-workspace",
    }
)


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(".", "_")


def _reject_identity(message: str, *, code: str = CODE_IDENTITY_SUBSTITUTION) -> None:
    raise WorkflowChatFacadeError(
        message,
        failure_class="user_error",
        status_code=403,
        code=code,
    )


def _scan_mapping_for_identity(
    mapping: Mapping[str, Any], *, chat_binding_id: str
) -> None:
    for raw_key, raw_value in mapping.items():
        normalized = _normalize_key(raw_key)
        if normalized in _SESSION_ID_KEYS:
            value = str(raw_value or "").strip()
            if value and value != chat_binding_id:
                _reject_identity(
                    "A session reference does not map to the bound session.",
                    code=CODE_SESSION_SUBSTITUTION,
                )
            continue
        if normalized in _FORBIDDEN_IDENTITY_KEYS:
            _reject_identity(
                "The request attempts to supply a server-owned identity."
            )


def assert_no_identity_substitution(
    *,
    chat_binding_id: str,
    path_session_id: str | None,
    query: Mapping[str, Any] | None = None,
    body: Any | None = None,
    headers: Mapping[str, Any] | None = None,
) -> None:
    """Fail closed on any attempt to substitute a server-owned identity.

    Enforces OB-§4.2 steps 4-5 / brief §3 at the facade boundary: the only
    session id the browser may name is the bound ``chatBindingId``; upstream
    endpoint, host, runner, workspace, profile, launch policy, model, effort,
    goal, and credential identities may not appear in the path, query, body, or
    headers. The provider session id is virtualized server-side and never
    supplied by the caller.
    """

    if path_session_id is not None and path_session_id != chat_binding_id:
        _reject_identity(
            "A session reference does not map to the bound session.",
            code=CODE_SESSION_SUBSTITUTION,
        )

    if query:
        _scan_mapping_for_identity(query, chat_binding_id=chat_binding_id)

    if headers:
        for raw_name in headers.keys():
            if str(raw_name or "").strip().lower() in _FORBIDDEN_IDENTITY_HEADERS:
                _reject_identity(
                    "The request attempts to supply a server-owned identity header."
                )

    if isinstance(body, Mapping):
        _scan_mapping_for_identity(body, chat_binding_id=chat_binding_id)
        metadata = body.get("metadata")
        if isinstance(metadata, Mapping):
            _scan_mapping_for_identity(metadata, chat_binding_id=chat_binding_id)


__all__ = [
    "CAP_INTERRUPT_TURN",
    "CAP_READ_RESOURCES",
    "CAP_RESOLVE_ELICITATION",
    "CAP_SEND_MESSAGE",
    "CAP_VIEW_TRANSCRIPT",
    "CODE_BINDING_UNKNOWN",
    "CODE_CALLER_UNAUTHORIZED",
    "CODE_IDENTITY_SUBSTITUTION",
    "CODE_MALFORMED_PAYLOAD",
    "CODE_OPERATION_DENIED",
    "CODE_PAYLOAD_TOO_LARGE",
    "CODE_ROUTE_NOT_ALLOWLISTED",
    "CODE_SESSION_NOT_READY",
    "CODE_SESSION_READ_ONLY",
    "CODE_SESSION_SUBSTITUTION",
    "CODE_UNSUPPORTED_MEDIA_TYPE",
    "FACADE_OPERATIONS",
    "FacadeMatch",
    "FacadeOperation",
    "TERMINAL_SESSION_STATUSES",
    "WorkflowChatFacadeError",
    "assert_no_identity_substitution",
    "is_read_only",
    "match_facade_operation",
    "recompute_capabilities",
    "required_capability_for_event",
]
