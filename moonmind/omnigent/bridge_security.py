"""§16 auth-topology enforcement for the MoonMind Omnigent Bridge.

MM-1154 implements the bounded §16 ("Security and authentication") slice from
``docs/Omnigent/OmnigentBridge.md``. The bridge boundary must:

- authorize the MoonMind principal + workflow + AgentRun + bridge session
  before any provider call (§16 rule 1);
- keep session labels id-only, never secrets (§16 rule 4);
- redact secret-like raw provider events before artifact persistence
  (§16 rule 5);
- never leak MoonMind internal auth headers upstream in proxy mode unless
  explicitly configured (§16 rule 7).

These are runtime-neutral primitives: they take plain requests/labels/headers
and fail closed, so both the current Omnigent execution path and the future
Session API Facade (§18.2) can call them without duplicating the rules.

Source issue traceability: MM-1140 -> MM-1154.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.utils.logging import redact_sensitive_payload


class OmnigentAuthorizationError(RuntimeError):
    """Raised when the §16 bridge auth topology cannot be satisfied.

    Carries a canonical MoonMind failure class so a fail-closed authorization
    outcome classifies consistently with the rest of the Omnigent path.
    """

    def __init__(self, message: str, *, failure_class: str = "user_error") -> None:
        super().__init__(message)
        self.failure_class = failure_class


# MoonMind-internal auth headers that must never reach an upstream Omnigent
# Server in proxy mode unless an operator explicitly allowlists them
# (OmnigentBridge.md §16 rule 7). The upstream Omnigent credentials are set
# service-side by the bridge itself, not forwarded from a MoonMind caller.
_MOONMIND_INTERNAL_AUTH_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-access-token",
        "x-session-token",
        "x-csrf-token",
        "x-moonmind-token",
        "x-moonmind-auth",
        "x-moonmind-authorization",
        "x-moonmind-api-key",
        "x-moonmind-user",
        "x-moonmind-session",
        "x-forwarded-authorization",
    }
)
# Header-name fragments that mark a credential-bearing header regardless of the
# exact spelling. Anything matching is dropped unless explicitly allowlisted.
_SENSITIVE_HEADER_FRAGMENTS: tuple[str, ...] = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "credential",
    "password",
    "api-key",
    "api_key",
    "apikey",
)
# Label-key fragments that flag an attempt to smuggle a credential into the
# provider session labels. Bare ``key`` is intentionally excluded so legitimate
# id labels such as ``moonmind.idempotency_key`` are preserved.
_SENSITIVE_LABEL_FRAGMENTS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "credential",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
    "bearer",
    "cookie",
    "auth",
    "authorization",
)
_RAW_PROVIDER_TOKEN_PATTERN = re.compile(
    r"\b(?:sk|sk-proj|sk-ant|sk-live|sk-test)-[A-Za-z0-9_-]{8,}\b"
)


def _credential_fragment_key(value: str) -> str:
    return value.lower().replace("-", "_").replace(".", "_")


def _redact_raw_provider_tokens(value: Any) -> Any:
    if isinstance(value, str):
        return _RAW_PROVIDER_TOKEN_PATTERN.sub("[REDACTED]", value)
    if isinstance(value, Mapping):
        return {
            str(nested_key): _redact_raw_provider_tokens(nested_value)
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_raw_provider_tokens(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_raw_provider_tokens(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class BridgeAuthorizationContext:
    """Authorized MoonMind identity for one bridge session.

    ``principal`` is the MoonMind workflow that owns the bridge session; the
    remaining ids are the durable authorization scope enforced before any
    provider call.
    """

    principal: str
    workflow_id: str
    agent_run_id: str
    bridge_session_key: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class BridgeSessionBinding:
    """Durable MoonMind identity already bound to a bridge session row."""

    workflow_id: str
    agent_run_id: str


def _require_identity(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OmnigentAuthorizationError(
            f"Omnigent bridge authorization requires {field_name}",
            failure_class="user_error",
        )
    return text


def authorize_bridge_access(
    request: AgentExecutionRequest,
) -> BridgeAuthorizationContext:
    """Authorize MoonMind principal + workflow + AgentRun + bridge session.

    Runs before any provider call and fails closed when required MoonMind
    identity is missing (OmnigentBridge.md §16 rule 1). The workflow/AgentRun
    ids mirror the durable idempotency mapping so a bridge session is scoped to
    a single MoonMind owner.
    """

    correlation_id = _require_identity(
        request.correlation_id, field_name="correlationId"
    )
    bridge_session_key = _require_identity(
        request.idempotency_key, field_name="idempotencyKey"
    )
    if request.step_execution is not None:
        workflow_id = _require_identity(
            request.step_execution.workflow_id, field_name="workflowId"
        )
        agent_run_id = _require_identity(
            request.step_execution.run_id, field_name="runId"
        )
    else:
        workflow_id = correlation_id
        agent_run_id = correlation_id
    return BridgeAuthorizationContext(
        principal=workflow_id,
        workflow_id=workflow_id,
        agent_run_id=agent_run_id,
        bridge_session_key=bridge_session_key,
        correlation_id=correlation_id,
    )


def assert_bridge_session_binding(
    context: BridgeAuthorizationContext,
    existing: BridgeSessionBinding | None,
) -> None:
    """Fail closed when an existing bridge session belongs to another owner.

    A bridge session (idempotency key) may only be reused by the MoonMind
    workflow + AgentRun that created it. This authorizes the bridge session
    before any provider call and rejects cross-owner idempotency-key reuse.
    """

    if existing is None:
        return
    if (
        existing.workflow_id != context.workflow_id
        or existing.agent_run_id != context.agent_run_id
    ):
        raise OmnigentAuthorizationError(
            "Omnigent bridge session is bound to a different MoonMind "
            "workflow/AgentRun; refusing cross-owner reuse",
            failure_class="user_error",
        )


def enforce_id_only_labels(labels: Mapping[str, Any]) -> dict[str, Any]:
    """Return id-only session labels or fail closed on secret-like content.

    Session labels carry MoonMind ids and idempotency keys but never secrets
    (OmnigentBridge.md §16 rule 4). Both the key (credential-shaped names) and
    the value (credential-shaped tokens, at any nesting depth) are rejected.
    """

    sanitized: dict[str, Any] = {}
    for raw_key, raw_value in labels.items():
        key = str(raw_key).strip()
        normalized = _credential_fragment_key(key)
        if any(fragment in normalized for fragment in _SENSITIVE_LABEL_FRAGMENTS):
            raise OmnigentAuthorizationError(
                f"Omnigent session labels must not carry secret-like keys: {key!r}",
                failure_class="user_error",
            )
        # Compare the value against its redacted form (keyless, so a legitimate
        # id such as an idempotency key is not flagged by its label name). Any
        # difference means the redactor found credential-shaped content.
        if (
            redact_sensitive_payload(raw_value) != raw_value
            or _redact_raw_provider_tokens(raw_value) != raw_value
        ):
            raise OmnigentAuthorizationError(
                f"Omnigent session label {key!r} must not carry a secret-like value",
                failure_class="user_error",
            )
        sanitized[key] = raw_value
    return sanitized


def sanitize_proxy_headers(
    headers: Mapping[str, Any],
    *,
    allowed_upstream_headers: Iterable[str] = (),
) -> dict[str, str]:
    """Strip MoonMind-internal auth headers before forwarding upstream.

    Proxy mode must not leak MoonMind internal auth headers to the upstream
    Omnigent Server unless an operator explicitly allowlists them by name
    (OmnigentBridge.md §16 rule 7). Non-auth headers pass through unchanged.
    """

    allowlist = {
        str(name).strip().lower()
        for name in allowed_upstream_headers
        if str(name).strip()
    }
    forwarded: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).strip()
        if not name:
            continue
        normalized = name.lower()
        fragment_key = _credential_fragment_key(name)
        if normalized in allowlist:
            forwarded[name] = str(raw_value)
            continue
        if normalized in _MOONMIND_INTERNAL_AUTH_HEADERS:
            continue
        if any(fragment in fragment_key for fragment in _SENSITIVE_HEADER_FRAGMENTS):
            continue
        forwarded[name] = str(raw_value)
    return forwarded


def redact_raw_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Redact secret-like fields from raw provider events before persistence.

    Applied on the bridge raw-event persistence path so the artifact system
    stays a safe evidence boundary (OmnigentBridge.md §16 rule 5).
    """

    redacted: list[dict[str, Any]] = []
    for event in events:
        scrubbed = _redact_raw_provider_tokens(redact_sensitive_payload(dict(event)))
        redacted.append(
            scrubbed if isinstance(scrubbed, dict) else {"value": scrubbed}
        )
    return redacted


__all__ = [
    "BridgeAuthorizationContext",
    "BridgeSessionBinding",
    "OmnigentAuthorizationError",
    "assert_bridge_session_binding",
    "authorize_bridge_access",
    "enforce_id_only_labels",
    "redact_raw_events",
    "sanitize_proxy_headers",
]
