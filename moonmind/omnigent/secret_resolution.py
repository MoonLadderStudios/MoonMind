"""Canonical post-lease SecretRef resolution boundary for Omnigent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from moonmind.auth.secret_refs import parse_secret_ref
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.materializers import (
    ProviderConfigEndpoint,
    is_api_key_launch_profile,
    provider_config_endpoint,
)
from moonmind.omnigent.provider_leases import AcquiredProviderLease


class SecretResolver(Protocol):
    async def resolve(self, ref: Any) -> str:
        raise NotImplementedError


@dataclass
class ScopedSecretBundle:
    """Short-lived plaintext values with an explicit reference-clearing API."""

    provider_profile_ref: str
    credential_generation: int
    values: dict[str, str] = field(default_factory=dict, repr=False)

    def require(self, role: str) -> str:
        value = self.values.get(role, "")
        if not value:
            raise HarnessPlatformError(
                f"resolved secret role {role} is empty",
                code=HarnessPlatformFailure.OMNIGENT_SECRET_RESOLUTION_FAILED,
            )
        return value

    def clear(self) -> None:
        self.values.clear()

    def __enter__(self) -> "ScopedSecretBundle":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.clear()


class OmnigentSecretResolutionService:
    def __init__(self, *, session_factory: Any, resolver: SecretResolver) -> None:
        self._session_factory = session_factory
        self._resolver = resolver

    async def _load_profile(self, acquired: AcquiredProviderLease) -> Any:
        from api_service.db.models import ManagedAgentProviderProfile

        async with self._session_factory() as session:
            profile = await session.get(
                ManagedAgentProviderProfile, acquired.provider_profile_ref
            )
        if profile is None:
            raise HarnessPlatformError(
                "Provider Profile disappeared after lease acquisition",
                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
            )
        if int(profile.credential_generation) != acquired.credential_generation:
            raise HarnessPlatformError(
                "Provider Profile credential generation changed after lease acquisition",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
            )
        return profile

    async def resolve(
        self,
        *,
        acquired: AcquiredProviderLease,
        allowed_secret_roles: tuple[str, ...] | list[str],
    ) -> ScopedSecretBundle:
        profile = await self._load_profile(acquired)
        return await self._resolve_roles(
            acquired=acquired,
            refs=dict(profile.secret_refs or {}),
            allowed_secret_roles=allowed_secret_roles,
        )

    async def resolve_provider_config(
        self,
        *,
        acquired: AcquiredProviderLease,
        allowed_secret_roles: tuple[str, ...] | list[str],
    ) -> tuple[ScopedSecretBundle, ProviderConfigEndpoint | None]:
        """Resolve a provider-config credential and the endpoint it targets.

        An API-key Codex or Claude Profile declares its key, endpoint and model
        pins in its launch environment; that one key is resolved under the
        materializer's ``api_key`` role. Other Profiles keep the
        materializer's own roles and the default endpoint.
        """

        profile = await self._load_profile(acquired)
        refs = dict(profile.secret_refs or {})
        if not is_api_key_launch_profile(profile):
            bundle = await self._resolve_roles(
                acquired=acquired, refs=refs, allowed_secret_roles=allowed_secret_roles
            )
            return bundle, None
        endpoint, reason = provider_config_endpoint(profile)
        if endpoint is None:
            raise HarnessPlatformError(
                f"API-key Provider Profile cannot run on Omnigent: {reason}",
                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
            )
        bundle = await self._resolve_roles(
            acquired=acquired, refs=refs, allowed_secret_roles=(endpoint.secretRole,)
        )
        bundle.values = {"api_key": bundle.values.pop(endpoint.secretRole)}
        return bundle, endpoint

    async def _resolve_roles(
        self,
        *,
        acquired: AcquiredProviderLease,
        refs: dict[str, Any],
        allowed_secret_roles: tuple[str, ...] | list[str],
    ) -> ScopedSecretBundle:
        allowed = tuple(dict.fromkeys(str(role) for role in allowed_secret_roles))
        extra = set(refs) - set(allowed)
        # Extra roles may exist on a multi-purpose profile, but are never resolved.
        _ = extra
        missing = [role for role in allowed if not str(refs.get(role) or "").strip()]
        if missing:
            raise HarnessPlatformError(
                f"Provider Profile is missing required SecretRef roles: {missing}",
                code=HarnessPlatformFailure.OMNIGENT_SECRET_RESOLUTION_FAILED,
            )
        values: dict[str, str] = {}
        try:
            for role in allowed:
                values[role] = await self._resolver.resolve(
                    parse_secret_ref(str(refs[role]))
                )
        except Exception as exc:
            values.clear()
            raise HarnessPlatformError(
                "Provider Profile SecretRef resolution failed",
                code=HarnessPlatformFailure.OMNIGENT_SECRET_RESOLUTION_FAILED,
            ) from exc
        return ScopedSecretBundle(
            provider_profile_ref=acquired.provider_profile_ref,
            credential_generation=acquired.credential_generation,
            values=values,
        )


__all__ = [
    "OmnigentSecretResolutionService",
    "ScopedSecretBundle",
]
