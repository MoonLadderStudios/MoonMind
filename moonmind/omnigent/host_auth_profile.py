"""Versioned, SecretRef-backed embedded Omnigent host authentication."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
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

MAX_ROTATION_OVERLAP = timedelta(minutes=15)
logger = structlog.get_logger(__name__)


class HostAuthProfileError(RuntimeError):
    """Stable, secret-free host-auth configuration or resolution failure."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class HostAuthCredentialProfile:
    profile_id: str
    current_secret_ref: str
    current_generation: int
    protocol_profile: str = PINNED_PROTOCOL_PROFILE
    enabled: bool = True
    revoked: bool = False
    previous_secret_ref: str | None = None
    previous_generation: int | None = None
    previous_expires_at: datetime | None = None
    rotated_at: datetime | None = None
    bootstrap_fallback: bool = False

    def validate(self, *, now: datetime | None = None) -> None:
        now = now or datetime.now(tz=UTC)
        if not self.enabled:
            raise HostAuthProfileError(
                "embedded host authentication is disabled", code="host_auth_disabled"
            )
        if self.revoked:
            raise HostAuthProfileError(
                "embedded host authentication is revoked", code="host_auth_revoked"
            )
        if not self.profile_id or not self.current_secret_ref or self.current_generation < 1:
            raise HostAuthProfileError(
                "embedded host authentication profile is incomplete",
                code="host_auth_unconfigured",
            )
        if self.protocol_profile != PINNED_PROTOCOL_PROFILE:
            raise HostAuthProfileError(
                "embedded host authentication protocol profile is incompatible",
                code="host_auth_profile_incompatible",
            )
        previous = (
            self.previous_secret_ref,
            self.previous_generation,
            self.previous_expires_at,
        )
        if any(value is not None for value in previous):
            if any(value is None for value in previous):
                raise HostAuthProfileError(
                    "previous host credential metadata is incomplete",
                    code="host_auth_rotation_invalid",
                )
            assert self.previous_generation is not None
            assert self.previous_expires_at is not None
            if self.previous_generation >= self.current_generation:
                raise HostAuthProfileError(
                    "previous host credential generation is not older",
                    code="host_auth_rotation_invalid",
                )
            if (
                self.rotated_at is None
                or self.previous_expires_at
                > self.rotated_at + MAX_ROTATION_OVERLAP
            ):
                raise HostAuthProfileError(
                    "host credential overlap exceeds the maximum",
                    code="host_auth_rotation_invalid",
                )
            if self.previous_expires_at <= now and self.previous_secret_ref:
                # Expired metadata is valid configuration; it is simply excluded at resolution.
                return

    def metadata(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(tz=UTC)
        return {
            "profileId": self.profile_id,
            "currentGeneration": self.current_generation,
            "previousGeneration": self.previous_generation,
            "previousGenerationActive": bool(
                self.previous_expires_at and self.previous_expires_at > now
            ),
            "protocolProfile": self.protocol_profile,
            "upstreamCommit": PINNED_OMNIGENT_COMMIT,
            "enabled": self.enabled,
            "revoked": self.revoked,
            "bootstrapFallback": self.bootstrap_fallback,
            "rotatedAt": self.rotated_at.isoformat() if self.rotated_at else None,
        }


@dataclass(frozen=True, slots=True)
class ResolvedHostAuthCredentials:
    profile: HostAuthCredentialProfile
    tokens_by_generation: Mapping[int, str]


def profile_from_metadata(value: Mapping[str, Any]) -> HostAuthCredentialProfile:
    """Deserialize only the safe metadata persisted by the API service."""

    profile = HostAuthCredentialProfile(
        profile_id=_clean(value.get("profileId")),
        current_secret_ref=_clean(value.get("currentSecretRef")),
        current_generation=_integer(value.get("currentGeneration"), 0) or 0,
        protocol_profile=_clean(value.get("protocolProfile")) or PINNED_PROTOCOL_PROFILE,
        enabled=bool(value.get("enabled", True)),
        revoked=bool(value.get("revoked", False)),
        previous_secret_ref=_clean(value.get("previousSecretRef")) or None,
        previous_generation=_integer(value.get("previousGeneration")),
        previous_expires_at=_timestamp(value.get("previousExpiresAt")),
        rotated_at=_timestamp(value.get("rotatedAt")),
        bootstrap_fallback=False,
    )
    return profile


def profile_persistence_metadata(profile: HostAuthCredentialProfile) -> dict[str, Any]:
    """Serialize references and lifecycle metadata, never resolved secret bodies."""

    return {
        "profileId": profile.profile_id,
        "currentSecretRef": profile.current_secret_ref,
        "currentGeneration": profile.current_generation,
        "protocolProfile": profile.protocol_profile,
        "enabled": profile.enabled,
        "revoked": profile.revoked,
        "previousSecretRef": profile.previous_secret_ref,
        "previousGeneration": profile.previous_generation,
        "previousExpiresAt": (
            profile.previous_expires_at.isoformat() if profile.previous_expires_at else None
        ),
        "rotatedAt": profile.rotated_at.isoformat() if profile.rotated_at else None,
    }


def rotate_host_auth_profile(
    profile: HostAuthCredentialProfile,
    *,
    new_secret_ref: str,
    now: datetime | None = None,
    overlap: timedelta = MAX_ROTATION_OVERLAP,
) -> HostAuthCredentialProfile:
    """Build a validated next generation without mutating the current profile.

    Persistence owners can commit the returned value atomically; validation
    failure leaves the durable/current value untouched.
    """
    now = now or datetime.now(tz=UTC)
    if not new_secret_ref or overlap < timedelta(0) or overlap > MAX_ROTATION_OVERLAP:
        raise HostAuthProfileError(
            "host credential rotation is invalid", code="host_auth_rotation_invalid"
        )
    rotated = replace(
        profile,
        current_secret_ref=new_secret_ref,
        current_generation=profile.current_generation + 1,
        previous_secret_ref=profile.current_secret_ref if overlap else None,
        previous_generation=profile.current_generation if overlap else None,
        previous_expires_at=now + overlap if overlap else None,
        rotated_at=now,
        bootstrap_fallback=False,
    )
    rotated.validate(now=now)
    logger.info(
        "embedded_host_auth_rotated",
        profile_id=rotated.profile_id,
        current_generation=rotated.current_generation,
        previous_generation=rotated.previous_generation,
        overlap_seconds=int(overlap.total_seconds()),
    )
    return rotated


def revoke_host_auth_profile(profile: HostAuthCredentialProfile) -> HostAuthCredentialProfile:
    """Return immediately revoked safe metadata without resolving either secret."""
    revoked = replace(profile, revoked=True, previous_secret_ref=None,
                      previous_generation=None, previous_expires_at=None)
    logger.info(
        "embedded_host_auth_revoked",
        profile_id=profile.profile_id,
        current_generation=profile.current_generation,
    )
    return revoked


def _clean(value: object | None) -> str:
    return str(value or "").strip()


def _bool(value: object | None, default: bool) -> bool:
    raw = _clean(value).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _integer(value: object | None, default: int | None = None) -> int | None:
    raw = _clean(value)
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise HostAuthProfileError(
            "host credential generation is invalid",
            code="host_auth_generation_invalid",
        ) from exc


def _timestamp(value: object | None) -> datetime | None:
    raw = _clean(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.astimezone(UTC)
    except ValueError as exc:
        raise HostAuthProfileError(
            "host credential rotation timestamp is invalid",
            code="host_auth_rotation_invalid",
        ) from exc


def load_host_auth_profile(
    *, env: Mapping[str, Any] | None = None
) -> HostAuthCredentialProfile:
    """Load safe profile metadata; raw bootstrap tokens are represented by an env SecretRef."""

    source = env if env is not None else os.environ
    current_ref = _clean(source.get("OMNIGENT_HOST_AUTH_CURRENT_SECRET_REF"))
    bootstrap = False
    if not current_ref and _clean(source.get("OMNIGENT_HOST_RUNNER_TOKEN")):
        current_ref = "env://OMNIGENT_HOST_RUNNER_TOKEN"
        bootstrap = True
    profile = HostAuthCredentialProfile(
        profile_id=_clean(source.get("OMNIGENT_HOST_AUTH_PROFILE_ID"))
        or ("bootstrap-local" if bootstrap else ""),
        current_secret_ref=current_ref,
        current_generation=_integer(source.get("OMNIGENT_HOST_AUTH_CURRENT_GENERATION"), 1) or 0,
        protocol_profile=_clean(source.get("OMNIGENT_HOST_AUTH_PROTOCOL_PROFILE"))
        or PINNED_PROTOCOL_PROFILE,
        enabled=_bool(source.get("OMNIGENT_HOST_AUTH_ENABLED"), True),
        revoked=_bool(source.get("OMNIGENT_HOST_AUTH_REVOKED"), False),
        previous_secret_ref=_clean(source.get("OMNIGENT_HOST_AUTH_PREVIOUS_SECRET_REF")) or None,
        previous_generation=_integer(source.get("OMNIGENT_HOST_AUTH_PREVIOUS_GENERATION")),
        previous_expires_at=_timestamp(source.get("OMNIGENT_HOST_AUTH_PREVIOUS_EXPIRES_AT")),
        rotated_at=_timestamp(source.get("OMNIGENT_HOST_AUTH_ROTATED_AT")),
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
    "HostAuthCredentialProfile",
    "HostAuthProfileError",
    "MAX_ROTATION_OVERLAP",
    "ResolvedHostAuthCredentials",
    "host_auth_readiness",
    "load_host_auth_profile",
    "profile_from_metadata",
    "profile_persistence_metadata",
    "resolve_host_auth_credentials",
    "revoke_host_auth_profile",
    "rotate_host_auth_profile",
]
