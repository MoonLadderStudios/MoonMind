"""Runtime gate for the Omnigent external agent integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from moonmind.auth.secret_refs import (
    SecretBackend,
    SecretReferenceError,
    parse_secret_ref,
)

OMNIGENT_DISABLED_MESSAGE = (
    "agentId=omnigent requires OMNIGENT_ENABLED=true with "
    "OMNIGENT_SERVER_URL configured"
)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class OmnigentRuntimeGate:
    """Whether Omnigent is enabled and required env vars are present."""

    enabled: bool
    missing: tuple[str, ...]
    error_message: str


@dataclass(frozen=True, slots=True)
class EmbeddedHostCredential:
    """Ephemeral embedded-host credential resolved at the service boundary."""

    value: str
    secret_ref: str
    generation: int


def _clean(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _enabled_from_env(*, env: Mapping[str, Any]) -> bool:
    raw = _clean(env.get("OMNIGENT_ENABLED"))
    if not raw:
        return False
    lowered = raw.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return False


def build_omnigent_gate(
    *,
    env: Mapping[str, Any] | None = None,
    error_message: str = OMNIGENT_DISABLED_MESSAGE,
) -> OmnigentRuntimeGate:
    """Return gate state for Omnigent (env-driven)."""

    source = env if env is not None else os.environ
    enabled_flag = _enabled_from_env(env=source)
    raw_enabled = source.get("OMNIGENT_ENABLED")
    server_url = _clean(source.get("OMNIGENT_SERVER_URL"))

    missing: list[str] = []
    if raw_enabled is None or _clean(raw_enabled) == "":
        missing.append("OMNIGENT_ENABLED")
        if not server_url:
            missing.append("OMNIGENT_SERVER_URL")
    elif enabled_flag and not server_url:
        missing.append("OMNIGENT_SERVER_URL")

    return OmnigentRuntimeGate(
        enabled=enabled_flag and len(missing) == 0,
        missing=tuple(missing),
        error_message=error_message,
    )


def is_omnigent_enabled(*, env: Mapping[str, Any] | None = None) -> bool:
    return build_omnigent_gate(env=env).enabled


def resolved_server_url(*, env: Mapping[str, Any] | None = None) -> str:
    """Return configured Omnigent server URL."""

    source = env if env is not None else os.environ
    return _clean(source.get("OMNIGENT_SERVER_URL"))


def resolved_api_token(*, env: Mapping[str, Any] | None = None) -> str:
    """Return configured Omnigent API token."""

    source = env if env is not None else os.environ
    return _clean(source.get("OMNIGENT_API_TOKEN"))


def resolved_default_agent_name(*, env: Mapping[str, Any] | None = None) -> str:
    """Return configured default Omnigent agent name."""

    source = env if env is not None else os.environ
    return _clean(source.get("OMNIGENT_DEFAULT_AGENT_NAME"))


def resolved_host_runner_token(*, env: Mapping[str, Any] | None = None) -> str:
    """Resolve the embedded token from its required service-side secret ref."""

    return resolve_host_runner_credential(env=env).value


def resolved_host_runner_token_ref(*, env: Mapping[str, Any] | None = None) -> str:
    """Return the service-side secret ref for embedded host authentication."""

    source = env if env is not None else os.environ
    return _clean(source.get("OMNIGENT_HOST_RUNNER_TOKEN_REF"))


def resolved_host_runner_credential_generation(
    *, env: Mapping[str, Any] | None = None
) -> int:
    """Return the declared embedded credential generation (never secret data)."""

    source = env if env is not None else os.environ
    raw = _clean(source.get("OMNIGENT_HOST_RUNNER_CREDENTIAL_GENERATION")) or "1"
    try:
        generation = int(raw)
    except ValueError as exc:
        raise ValueError("embedded host credential generation must be an integer") from exc
    if generation < 1:
        raise ValueError("embedded host credential generation must be at least 1")
    return generation


def resolve_host_runner_credential(
    *, env: Mapping[str, Any] | None = None
) -> EmbeddedHostCredential:
    """Resolve an embedded host credential without exposing it durably.

    The API service currently supports the portable ``env://`` backend here.
    Other backends fail visibly instead of silently falling back to a raw token.
    """

    source = env if env is not None else os.environ
    ref = resolved_host_runner_token_ref(env=source)
    try:
        parsed = parse_secret_ref(ref)
    except SecretReferenceError as exc:
        raise ValueError("embedded host credential secret ref is invalid") from exc
    if parsed.backend != SecretBackend.ENV:
        raise ValueError("embedded host credential secret ref backend is unsupported")
    value = _clean(source.get(parsed.locator))
    if not value:
        raise ValueError("embedded host credential secret ref is unresolved")
    return EmbeddedHostCredential(
        value=value,
        secret_ref=parsed.normalized_ref,
        generation=resolved_host_runner_credential_generation(env=source),
    )


def resolved_proxy_forward_headers(
    *, env: Mapping[str, Any] | None = None
) -> frozenset[str]:
    """Return the explicitly-configured upstream proxy header allowlist.

    Proxy mode forwards no MoonMind headers upstream by default (OmnigentBridge
    §16 rule 7); operators opt in per header via a comma-separated
    ``OMNIGENT_PROXY_FORWARD_HEADERS``. Names are normalized to lowercase.
    """

    source = env if env is not None else os.environ
    raw = _clean(source.get("OMNIGENT_PROXY_FORWARD_HEADERS"))
    if not raw:
        return frozenset()
    return frozenset(
        part.strip().lower() for part in raw.split(",") if part.strip()
    )


__all__ = [
    "OMNIGENT_DISABLED_MESSAGE",
    "OmnigentRuntimeGate",
    "EmbeddedHostCredential",
    "build_omnigent_gate",
    "is_omnigent_enabled",
    "resolved_api_token",
    "resolved_default_agent_name",
    "resolved_host_runner_token",
    "resolved_host_runner_token_ref",
    "resolved_host_runner_credential_generation",
    "resolve_host_runner_credential",
    "resolved_proxy_forward_headers",
    "resolved_server_url",
]
