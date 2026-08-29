"""Adapters that satisfy the profile-bound execution ports.

Source issue: MoonLadderStudios/MoonMind#3711.

Persistence and Temporal detail is owned here so the legacy Codex coordinator
keeps only its side-effect ordering. Provider-native and storage-native
vocabulary (SQLAlchemy rows, ``PolicyConflict``, Temporal ``activity.info()``)
is normalized at this boundary into the pure contracts in
:mod:`moonmind.omnigent.execution_ports`.
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select
from temporalio import activity

from api_service.db.models import ManagedAgentProviderProfile
from api_service.services.omnigent_policies import OmnigentPolicyService, PolicyConflict
from api_service.services.provider_profile_readiness import (
    provider_profile_launch_ready,
)
from moonmind.omnigent.execution_ports import (
    ExecutionPolicyAuthorityUnavailableError,
    ProviderProfileAuthority,
)
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.provider_profiles.oauth_policy import is_omnigent_oauth_profile


class DbProviderProfileAuthority:
    """Resolve and validate the durable Provider Profile row for a launch."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    async def resolve(self, profile_id: str) -> ProviderProfileAuthority:
        async with self._session_factory() as session:
            profile = (
                await session.execute(
                    select(ManagedAgentProviderProfile).where(
                        ManagedAgentProviderProfile.profile_id == profile_id
                    )
                )
            ).scalar_one_or_none()
            if profile is None:
                raise OmnigentOAuthHostError(
                    "Provider Profile was not found", code="profile_resolution_failed"
                )
            if not is_omnigent_oauth_profile(
                runtime_id=profile.runtime_id,
                credential_source=profile.credential_source,
                materialization_mode=profile.runtime_materialization_mode,
            ):
                raise OmnigentOAuthHostError(
                    "Provider Profile is not a supported Omnigent OAuth profile",
                    code="profile_resolution_failed",
                )
            return ProviderProfileAuthority.model_validate(
                {
                    "profileId": str(profile.profile_id),
                    "runtimeId": str(
                        getattr(profile.runtime_id, "value", profile.runtime_id)
                    ),
                    "credentialGeneration": int(
                        getattr(profile, "credential_generation", 0) or 0
                    ),
                    "cooldownAfter429Seconds": int(
                        getattr(profile, "cooldown_after_429_seconds", 0) or 0
                    ),
                    "launchReady": bool(provider_profile_launch_ready(profile)),
                }
            )


class DbExecutionPolicyAuthority:
    """Resolve the persisted runtime policy snapshot for a launch policy ref."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    async def resolve_runtime_snapshot(self, policy_ref: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            try:
                return await OmnigentPolicyService(session).resolve_runtime_snapshot(
                    policy_ref
                )
            except PolicyConflict as exc:
                raise ExecutionPolicyAuthorityUnavailableError(str(exc)) from exc


class TemporalExecutionAttempt:
    """Report the durable Temporal attempt, or one outside an Activity."""

    def current_attempt(self) -> int:
        try:
            return max(1, int(activity.info().attempt))
        except RuntimeError:
            return 1


__all__ = [
    "DbExecutionPolicyAuthority",
    "DbProviderProfileAuthority",
    "TemporalExecutionAttempt",
]
