"""Runtime gate for the Omnigent external agent integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

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
    """Return the embedded host/runner auth token configured service-side."""

    source = env if env is not None else os.environ
    return _clean(source.get("OMNIGENT_HOST_RUNNER_TOKEN"))


def resolved_host_runner_credential_refs(
    *, env: Mapping[str, Any] | None = None
) -> tuple[tuple[str, int], ...]:
    """Return current and explicitly-overlapped credential refs, never values."""

    source = env if env is not None else os.environ
    current_ref = _clean(source.get("OMNIGENT_HOST_RUNNER_SECRET_REF")) or (
        "env://OMNIGENT_HOST_RUNNER_TOKEN"
    )
    try:
        current_generation = int(_clean(source.get("OMNIGENT_HOST_RUNNER_GENERATION")) or "1")
    except ValueError as exc:
        raise ValueError("OMNIGENT_HOST_RUNNER_GENERATION must be a positive integer") from exc
    if current_generation < 1:
        raise ValueError("OMNIGENT_HOST_RUNNER_GENERATION must be a positive integer")
    refs = [(current_ref, current_generation)]
    previous_ref = _clean(source.get("OMNIGENT_HOST_RUNNER_PREVIOUS_SECRET_REF"))
    overlap = _clean(source.get("OMNIGENT_HOST_RUNNER_ALLOW_PREVIOUS")) in _TRUE_VALUES
    if previous_ref and overlap:
        previous_generation = int(
            _clean(source.get("OMNIGENT_HOST_RUNNER_PREVIOUS_GENERATION"))
            or str(current_generation - 1)
        )
        if previous_generation < 1 or previous_generation >= current_generation:
            raise ValueError("previous host credential generation must precede current")
        refs.append((previous_ref, previous_generation))
    return tuple(refs)


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
    "build_omnigent_gate",
    "is_omnigent_enabled",
    "resolved_api_token",
    "resolved_default_agent_name",
    "resolved_host_runner_token",
    "resolved_host_runner_credential_refs",
    "resolved_proxy_forward_headers",
    "resolved_server_url",
]
