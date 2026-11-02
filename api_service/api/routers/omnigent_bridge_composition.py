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

from typing import Any, Mapping, Sequence

from api_service.db.base import async_session_maker
from api_service.services.omnigent_agent_profile_service import (
    record_upstream_sync_failure,
    synchronize_upstream_inventory,
)
from api_service.services.omnigent_policies import OmnigentPolicyService
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    HOST_PROTOCOL_MODE_PROXY,
    OmnigentBridgeConfig,
)
from moonmind.omnigent.bridge_proxy import OmnigentBridgeSessionProxy
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.embedded_drain import probe_embedded_drain
from moonmind.omnigent.settings import (
    resolved_api_token,
    resolved_default_agent_name,
    resolved_proxy_forward_headers,
    resolved_server_url,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient


# The bridge resolves its persisted default runtime authority under one runtime
# id. Which runtime backs the bridge is composition, not a route decision.
BRIDGE_POLICY_RUNTIME_ID = "omnigent-codex"


class OmnigentBridgeModeUnsupportedError(RuntimeError):
    """The configured host protocol mode cannot own this operation."""

    code = "omnigent_bridge_mode_unsupported"


def build_bridge_session_store() -> OmnigentBridgeSessionStore:
    """Return the durable bridge session store bound to the API database."""

    return OmnigentBridgeSessionStore(async_session_maker)


async def probe_embedded_transport_drain(
    *, store: OmnigentBridgeSessionStore | None = None
) -> dict[str, Any]:
    """Report the live drain disposition for the retired embedded transport.

    MoonLadderStudios/MoonMind#3955 (plan step 2): the production caller that
    wires durable counts into
    :func:`moonmind.omnigent.embedded_drain.summarize_embedded_drain` — active
    embedded sessions from ``active_host_protocol_modes`` and active embedded
    host leases from ``list_embedded_host_readiness``. Transport removal is
    gated on this probe reporting drained; a store failure propagates rather
    than implying drain.
    """

    owned = store if store is not None else build_bridge_session_store()
    return await probe_embedded_drain(owned)


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
        # MoonLadderStudios/MoonMind#3884: deliberate separate lifecycle. This
        # proxy composition is API-process-owned and long-lived, so it must
        # never touch the worker-owned shared pool (no cross-process or
        # cross-event-loop sharing). With no explicit httpx client, every
        # request resolves a per-request bounded owned client that is closed
        # after the call: no pooling across requests, but also no leakage and
        # no contention with worker stream/control budgets.
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


async def resolve_default_bridge_policy_snapshot() -> dict[str, Any]:
    """Return the persisted default runtime policy snapshot for the bridge.

    ``PolicyConflict`` and ``PolicyNotFound`` stay unmapped: the caller owns
    whether an absent authority fails closed or degrades its projection.
    """

    async with async_session_maker() as session:
        return await OmnigentPolicyService(session).resolve_default_runtime_snapshot(
            BRIDGE_POLICY_RUNTIME_ID
        )


async def project_upstream_inventory(
    *, endpoint_ref: str, bridge_mode: str, inventory: Sequence[Mapping[str, Any]]
) -> None:
    """Persist the auxiliary Agent Profile projection for one inventory read."""

    async with async_session_maker() as session:
        await synchronize_upstream_inventory(
            session,
            endpoint_ref=endpoint_ref,
            bridge_mode=bridge_mode,
            inventory=inventory,
        )


async def project_upstream_inventory_failure(
    *, endpoint_ref: str, bridge_mode: str, error: str
) -> None:
    """Record that an inventory read failed, without replacing the primary error."""

    async with async_session_maker() as session:
        await record_upstream_sync_failure(
            session,
            endpoint_ref=endpoint_ref,
            bridge_mode=bridge_mode,
            error=error,
        )


__all__ = [
    "BRIDGE_POLICY_RUNTIME_ID",
    "OmnigentBridgeModeUnsupportedError",
    "build_bridge_session_proxy",
    "build_bridge_session_store",
    "project_upstream_inventory",
    "project_upstream_inventory_failure",
    "probe_embedded_transport_drain",
    "resolve_default_bridge_policy_snapshot",
]
