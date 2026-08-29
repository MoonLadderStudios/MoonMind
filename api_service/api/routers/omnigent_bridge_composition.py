"""Composition for the Omnigent bridge API surface.

Source issue: MoonLadderStudios/MoonMind#3711 (required work 5).

Route handlers authenticate, validate, call one facade operation, and map a
typed result to HTTP. Deciding *which* concrete session store, provider
transport client, embedded host facade, or host-auth credential profile backs
that call is composition, and it lives here. The router keeps the public route
contract and its FastAPI dependency identities; it no longer constructs
persistence, transport, or credential objects in handler scope.
"""

from __future__ import annotations

from typing import Any, Mapping

from api_service.db.base import async_session_maker
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    HOST_PROTOCOL_MODE_PROXY,
    OmnigentBridgeConfig,
)
from moonmind.omnigent.bridge_embedded import (
    OmnigentEmbeddedHostProtocolFacade,
    verify_embedded_host_auth,
)
from moonmind.omnigent.bridge_proxy import OmnigentBridgeSessionProxy
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.host_auth_profile import (
    HostAuthCredentialProfile,
    HostAuthProfileError,
    host_auth_readiness,
    load_host_auth_profile,
    resolve_host_auth_credentials,
)
from moonmind.omnigent.host_auth_store import HostAuthProfileStore
from moonmind.omnigent.settings import (
    resolved_api_token,
    resolved_default_agent_name,
    resolved_proxy_forward_headers,
    resolved_server_url,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient


class OmnigentBridgeModeUnsupportedError(RuntimeError):
    """The configured host protocol mode cannot own this operation."""

    code = "omnigent_bridge_mode_unsupported"


def build_bridge_session_store() -> OmnigentBridgeSessionStore:
    """Return the durable bridge session store bound to the API database."""

    return OmnigentBridgeSessionStore(async_session_maker)


def build_host_auth_store() -> HostAuthProfileStore:
    return HostAuthProfileStore(async_session_maker)


async def resolve_active_host_auth_profile() -> HostAuthCredentialProfile:
    """Return the managed host-auth profile, or the deployment default."""

    managed = await build_host_auth_store().get_active()
    return managed or load_host_auth_profile()


async def evaluate_embedded_host_auth_readiness(
    config: OmnigentBridgeConfig,
) -> dict[str, Any]:
    """Evaluate the selected embedded contract at the enablement boundary."""

    if not config.enabled or config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED:
        return {"ready": True, "code": "host_auth_not_selected"}
    try:
        profile = await resolve_active_host_auth_profile()
    except HostAuthProfileError as exc:
        return {"ready": False, "code": exc.code}
    return await host_auth_readiness(profile=profile)


async def verify_embedded_host_request(
    *, headers: Mapping[str, str], config: OmnigentBridgeConfig
):
    """Resolve host-auth credentials and verify one embedded host request."""

    resolved = await resolve_host_auth_credentials(
        profile=await resolve_active_host_auth_profile()
    )
    return verify_embedded_host_auth(
        headers=headers,
        config=config,
        configured_credentials=resolved.tokens_by_generation,
        credential_profile_id=resolved.profile.profile_id,
    )


def build_bridge_session_proxy(
    *, config: OmnigentBridgeConfig, forward_headers: Mapping[str, str]
) -> OmnigentBridgeSessionProxy | None:
    """Return the proxy-mode bridge, or ``None`` when embedded mode is selected."""

    if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
        return None
    if config.host_protocol_mode != HOST_PROTOCOL_MODE_PROXY:
        raise OmnigentBridgeModeUnsupportedError(
            "Unsupported Omnigent bridge host protocol mode."
        )
    client = OmnigentHttpClient(
        base_url=resolved_server_url(),
        api_token=resolved_api_token(),
        forward_headers=forward_headers,
        upstream_header_allowlist=resolved_proxy_forward_headers(),
    )
    return OmnigentBridgeSessionProxy(
        run_store=build_bridge_session_store(),
        client=client,
        config=config,
        default_agent_name=resolved_default_agent_name(),
    )


def build_embedded_host_facade(
    config: OmnigentBridgeConfig,
) -> OmnigentEmbeddedHostProtocolFacade:
    return OmnigentEmbeddedHostProtocolFacade(
        run_store=build_bridge_session_store(),
        config=config,
    )


__all__ = [
    "OmnigentBridgeModeUnsupportedError",
    "build_bridge_session_proxy",
    "build_bridge_session_store",
    "build_embedded_host_facade",
    "build_host_auth_store",
    "evaluate_embedded_host_auth_readiness",
    "resolve_active_host_auth_profile",
    "verify_embedded_host_request",
]
