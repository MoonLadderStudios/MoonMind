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

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, AsyncIterator, Mapping, Sequence

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
from moonmind.omnigent.bridge_embedded import (
    OmnigentEmbeddedHostProtocolFacade,
    verify_embedded_host_auth,
)
from moonmind.omnigent.bridge_proxy import OmnigentBridgeSessionProxy
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.host_auth_contracts import (
    HostAuthCredentialProfile,
    HostAuthProfileError,
    rotate_host_auth_profile,
)
from moonmind.omnigent.host_auth_profile import (
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
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
)

# The bridge resolves its persisted default runtime authority under one runtime
# id. Which runtime backs the bridge is composition, not a route decision.
BRIDGE_POLICY_RUNTIME_ID = "omnigent-codex"


class OmnigentBridgeModeUnsupportedError(RuntimeError):
    """The configured host protocol mode cannot own this operation."""

    code = "omnigent_bridge_mode_unsupported"


class HostAuthProfileConflictError(RuntimeError):
    """A durable host-auth profile transition lost its expected-state check.

    Credential *validation* failures keep raising ``HostAuthProfileError`` so
    the router maps one vocabulary to HTTP. This error is the separate,
    non-credential outcome of an administration operation that raced or was
    applied to the wrong lifecycle state.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_bridge_session_store() -> OmnigentBridgeSessionStore:
    """Return the durable bridge session store bound to the API database."""

    return OmnigentBridgeSessionStore(async_session_maker)


def build_host_auth_store() -> HostAuthProfileStore:
    return HostAuthProfileStore(async_session_maker)


async def resolve_active_host_auth_profile() -> HostAuthCredentialProfile:
    """Return the managed host-auth profile, or the deployment default."""

    managed = await build_host_auth_store().get_active()
    return managed or load_host_auth_profile()


async def evaluate_active_host_auth_readiness() -> dict[str, Any]:
    """Project readiness for the active managed host-auth profile.

    Profile *resolution* failures (revoked, disabled, unconfigured, incompatible)
    and credential *resolution* failures share one redacted projection shape, so
    a caller never has to know which boundary failed to render readiness.
    """

    try:
        profile = await resolve_active_host_auth_profile()
    except HostAuthProfileError as exc:
        return {"ready": False, "code": exc.code}
    return await host_auth_readiness(profile=profile)


async def evaluate_embedded_host_auth_readiness(
    config: OmnigentBridgeConfig,
) -> dict[str, Any]:
    """Evaluate the selected embedded contract at the enablement boundary."""

    if not config.enabled or config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED:
        return {"ready": True, "code": "host_auth_not_selected"}
    return await evaluate_active_host_auth_readiness()


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


async def connected_host_frame_is_authorized(auth: Any) -> bool:
    """Re-resolve credential authority for one frame of a connected tunnel.

    Immediate revocation and overlap expiry must drain tunnels that were already
    accepted, so the durable profile and its live generation set are re-read per
    frame rather than trusted from the handshake.
    """

    active = await resolve_host_auth_credentials(
        profile=await resolve_active_host_auth_profile()
    )
    return (
        active.profile.profile_id == auth.credential_profile_id
        and auth.credential_generation in active.tokens_by_generation
    )


async def configure_active_host_auth_profile(
    candidate: HostAuthCredentialProfile,
) -> HostAuthCredentialProfile:
    """Validate a candidate profile's SecretRef, then durably select it.

    Selection is initial-only: an already-configured deployment rotates instead,
    so the durable row is never replaced by an unversioned write.
    """

    await resolve_host_auth_credentials(profile=candidate)
    store = build_host_auth_store()
    if await store.get_active() is not None:
        raise HostAuthProfileConflictError("host_auth_already_configured")
    try:
        return await store.put(candidate, expected_generation=0)
    except RuntimeError as exc:
        raise HostAuthProfileConflictError("host_auth_already_configured") from exc


async def rotate_active_host_auth_profile(
    *, new_secret_ref: str, overlap: timedelta
) -> HostAuthCredentialProfile:
    """Validate the next generation before atomically replacing durable metadata."""

    store = build_host_auth_store()
    current = await store.get_active()
    if current is None:
        raise HostAuthProfileConflictError("host_auth_unconfigured")
    candidate = rotate_host_auth_profile(
        current, new_secret_ref=new_secret_ref, overlap=overlap
    )
    await resolve_host_auth_credentials(profile=candidate)
    try:
        return await store.put(
            candidate, expected_generation=current.current_generation
        )
    except RuntimeError as exc:
        raise HostAuthProfileConflictError("host_auth_generation_conflict") from exc


async def revoke_active_host_auth_profile() -> HostAuthCredentialProfile:
    """Immediately revoke the durable profile without resolving either secret."""

    try:
        return await build_host_auth_store().revoke()
    except LookupError as exc:
        raise HostAuthProfileConflictError("host_auth_unconfigured") from exc


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


@asynccontextmanager
async def bridge_artifact_service() -> AsyncIterator[TemporalArtifactService]:
    """Open the artifact service the bridge reads embedded evidence through."""

    async with async_session_maker() as session:
        yield TemporalArtifactService(TemporalArtifactRepository(session))


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


def build_embedded_host_facade(
    config: OmnigentBridgeConfig,
) -> OmnigentEmbeddedHostProtocolFacade:
    return OmnigentEmbeddedHostProtocolFacade(
        run_store=build_bridge_session_store(),
        config=config,
    )


__all__ = [
    "BRIDGE_POLICY_RUNTIME_ID",
    "HostAuthProfileConflictError",
    "OmnigentBridgeModeUnsupportedError",
    "bridge_artifact_service",
    "build_bridge_session_proxy",
    "build_bridge_session_store",
    "build_embedded_host_facade",
    "build_host_auth_store",
    "configure_active_host_auth_profile",
    "connected_host_frame_is_authorized",
    "evaluate_active_host_auth_readiness",
    "evaluate_embedded_host_auth_readiness",
    "project_upstream_inventory",
    "project_upstream_inventory_failure",
    "resolve_active_host_auth_profile",
    "resolve_default_bridge_policy_snapshot",
    "revoke_active_host_auth_profile",
    "rotate_active_host_auth_profile",
    "verify_embedded_host_request",
]
