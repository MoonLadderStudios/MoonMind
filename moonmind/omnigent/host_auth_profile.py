"""SecretRef-backed resolution of the embedded Omnigent host credential.

Source issue: MoonLadderStudios/MoonMind#3711.

This module owns only the side-effecting host-auth boundary: reading deployment
environment, resolving SecretRefs, and projecting readiness. The pure profile,
failure, rotation, and serialization vocabulary is owned by
``moonmind.omnigent.host_auth_contracts``.
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
from typing import Any, Mapping

import structlog

from moonmind.auth.resolvers import EnvSecretResolver
from moonmind.auth.resolvers.base import RootSecretResolver
from moonmind.auth.secret_refs import SecretBackend, SecretReferenceError, parse_secret_ref
from moonmind.omnigent.host_auth_adapter import (
    PINNED_OMNIGENT_COMMIT,
    PINNED_PROTOCOL_PROFILE,
    UpstreamHostAuthError,
    assert_pinned_omnigent_auth_contract,
)
from moonmind.omnigent.host_auth_contracts import (
    HostAuthCredentialProfile,
    HostAuthProfileError,
    ResolvedHostAuthCredentials,
    clean_text,
    parse_flag,
    parse_generation,
    parse_timestamp,
)

logger = structlog.get_logger(__name__)


def load_host_auth_profile(
    *, env: Mapping[str, Any] | None = None
) -> HostAuthCredentialProfile:
    """Load safe profile metadata; raw bootstrap tokens are represented by an env SecretRef."""

    source = env if env is not None else os.environ
    current_ref = clean_text(source.get("OMNIGENT_HOST_AUTH_CURRENT_SECRET_REF"))
    bootstrap = False
    if not current_ref and clean_text(source.get("OMNIGENT_HOST_RUNNER_TOKEN")):
        current_ref = "env://OMNIGENT_HOST_RUNNER_TOKEN"
        bootstrap = True
    profile = HostAuthCredentialProfile(
        profile_id=clean_text(source.get("OMNIGENT_HOST_AUTH_PROFILE_ID"))
        or ("bootstrap-local" if bootstrap else ""),
        current_secret_ref=current_ref,
        current_generation=parse_generation(source.get("OMNIGENT_HOST_AUTH_CURRENT_GENERATION"), 1) or 0,
        protocol_profile=clean_text(source.get("OMNIGENT_HOST_AUTH_PROTOCOL_PROFILE"))
        or PINNED_PROTOCOL_PROFILE,
        enabled=parse_flag(source.get("OMNIGENT_HOST_AUTH_ENABLED"), True),
        revoked=parse_flag(source.get("OMNIGENT_HOST_AUTH_REVOKED"), False),
        previous_secret_ref=clean_text(source.get("OMNIGENT_HOST_AUTH_PREVIOUS_SECRET_REF")) or None,
        previous_generation=parse_generation(source.get("OMNIGENT_HOST_AUTH_PREVIOUS_GENERATION")),
        previous_expires_at=parse_timestamp(source.get("OMNIGENT_HOST_AUTH_PREVIOUS_EXPIRES_AT")),
        rotated_at=parse_timestamp(source.get("OMNIGENT_HOST_AUTH_ROTATED_AT")),
        bootstrap_fallback=bootstrap,
    )
    profile.validate()
    return profile


async def resolve_host_auth_credentials(
    *, profile: HostAuthCredentialProfile | None = None, now: datetime | None = None
) -> ResolvedHostAuthCredentials:
    """Resolve credential bodies at the server handshake boundary only."""

    profile = profile or load_host_auth_profile()
    now = now or datetime.now(tz=UTC)
    profile.validate(now=now)
    logger.info(
        "embedded_host_auth_profile_selected",
        profile_id=profile.profile_id,
        current_generation=profile.current_generation,
        previous_generation_active=bool(
            profile.previous_expires_at and profile.previous_expires_at > now
        ),
        bootstrap_fallback=profile.bootstrap_fallback,
    )
    try:
        assert_pinned_omnigent_auth_contract()
    except UpstreamHostAuthError as exc:
        raise HostAuthProfileError(
            "pinned Omnigent host verifier is unavailable",
            code="host_auth_verifier_unavailable",
        ) from exc

    resolvers = {SecretBackend.ENV: EnvSecretResolver()}
    refs = [profile.current_secret_ref]
    if (
        profile.previous_secret_ref
        and profile.previous_expires_at
        and profile.previous_expires_at > now
    ):
        refs.append(profile.previous_secret_ref)
    if any(ref.startswith("db://") for ref in refs):
        from moonmind.auth.resolvers.db_resolver import DbEncryptedSecretResolver
        resolvers[SecretBackend.DB_ENCRYPTED] = DbEncryptedSecretResolver()
    root = RootSecretResolver(resolvers)
    generations = [profile.current_generation]
    if len(refs) == 2:
        assert profile.previous_generation is not None
        generations.append(profile.previous_generation)
    try:
        values = [await root.resolve(parse_secret_ref(ref)) for ref in refs]
    except SecretReferenceError as exc:
        raise HostAuthProfileError(
            "embedded host credential reference could not be resolved",
            code="host_auth_secret_unavailable",
        ) from exc
    if len(values) != len(set(values)):
        raise HostAuthProfileError(
            "embedded host credential generations must resolve to distinct values",
            code="host_auth_rotation_invalid",
        )
    return ResolvedHostAuthCredentials(
        profile=profile,
        tokens_by_generation=dict(zip(generations, values, strict=True)),
    )


async def host_auth_readiness(
    *, profile: HostAuthCredentialProfile | None = None
) -> dict[str, Any]:
    """Return redacted compatibility/readiness evidence."""

    try:
        resolved = await resolve_host_auth_credentials(profile=profile)
        result = {
            "ready": True,
            "code": "host_auth_ready",
            **resolved.profile.metadata(),
        }
        logger.info(
            "embedded_host_auth_readiness",
            code=result["code"],
            profile_id=resolved.profile.profile_id,
            current_generation=resolved.profile.current_generation,
        )
        return result
    except HostAuthProfileError as exc:
        logger.warning("embedded_host_auth_readiness", code=exc.code)
        return {
            "ready": False,
            "code": exc.code,
            "message": str(exc),
            "protocolProfile": PINNED_PROTOCOL_PROFILE,
            "upstreamCommit": PINNED_OMNIGENT_COMMIT,
        }


__all__ = [
    "host_auth_readiness",
    "load_host_auth_profile",
    "resolve_host_auth_credentials",
]
