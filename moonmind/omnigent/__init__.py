"""Omnigent runtime integration settings."""

from moonmind.omnigent.settings import (
    OMNIGENT_DISABLED_MESSAGE,
    OmnigentRuntimeGate,
    build_omnigent_gate,
    is_omnigent_enabled,
    resolved_api_token,
    resolved_default_agent_name,
    resolved_proxy_forward_headers,
    resolved_server_url,
)

__all__ = [
    "OMNIGENT_DISABLED_MESSAGE",
    "OmnigentRuntimeGate",
    "build_omnigent_gate",
    "is_omnigent_enabled",
    "resolved_api_token",
    "resolved_default_agent_name",
    "resolved_proxy_forward_headers",
    "resolved_server_url",
]
