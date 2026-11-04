"""Prove and record ended first-party CLI OAuth logins."""

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

#: Runtime -> provider of the OAuth-home enrollments this may disable.
_OAUTH_LOGIN_ENROLLMENTS = {
    "codex_cli": "openai",
    "claude_code": "anthropic",
}

# Run as ``python3 -c <probe> <claude home>``. Print only a verdict, never
# token material. Claude Code clears both tokens once a refresh is rejected
# and records when its refresh token expires. A login with neither an unexpired
# access token nor an unexpired refresh token cannot be used without a new
# sign-in; an aged access token with a live refresh token may still refresh.
# Missing, unreadable, or unexpected layouts remain unknown.
CLAUDE_LOGIN_SESSION_PROBE = """\
import json, sys, time
try:
    with open(sys.argv[1] + "/.credentials.json", encoding="utf-8") as handle:
        oauth = json.load(handle)["claudeAiOauth"]
    now = time.time() * 1000
    def usable(token, expiry):
        expires_at = oauth.get(expiry)
        if isinstance(expires_at, (int, float)) and expires_at < 1e11:
            expires_at *= 1000
        return bool(oauth.get(token)) and not (
            isinstance(expires_at, (int, float)) and expires_at <= now
        )
    ended = not usable("accessToken", "expiresAt") and not usable(
        "refreshToken", "refreshTokenExpiresAt"
    )
    print("ended" if ended else "active")
except Exception:
    print("unknown")
"""


async def record_ended_oauth_login(
    profile_id: str,
    credential_generation: int,
    *,
    runtime_id: str,
    reason: str,
) -> None:
    """Disable only the enrollment whose mounted login was proven unusable.

    Reuse OAuth validation's Settings and routing state. Lock the profile while
    checking its generation so an old host cannot invalidate a reconnect.
    Recording failure must not replace the original authentication failure.
    """
    provider_id = _OAUTH_LOGIN_ENROLLMENTS[runtime_id]
    try:
        from sqlalchemy import select

        from api_service.db.base import async_session_maker
        from api_service.db.models import (
            ManagedAgentProviderProfile,
            ProviderCredentialSource,
            ProviderProfileAuthState,
            RuntimeMaterializationMode,
        )
        from api_service.services.provider_profile_service import (
            apply_oauth_validation_failure,
            get_first_party_oauth_profile,
            sync_provider_profile_manager,
        )

        async with async_session_maker() as session:
            profile = (
                await session.execute(
                    select(ManagedAgentProviderProfile)
                    .where(
                        ManagedAgentProviderProfile.profile_id == profile_id,
                        ManagedAgentProviderProfile.credential_generation
                        == credential_generation,
                        ManagedAgentProviderProfile.runtime_id == runtime_id,
                        ManagedAgentProviderProfile.provider_id == provider_id,
                        ManagedAgentProviderProfile.credential_source
                        == ProviderCredentialSource.OAUTH_VOLUME,
                        ManagedAgentProviderProfile.runtime_materialization_mode
                        == RuntimeMaterializationMode.OAUTH_HOME,
                        ManagedAgentProviderProfile.auth_state
                        == ProviderProfileAuthState.CONNECTED,
                        ManagedAgentProviderProfile.enabled.is_(True),
                        ManagedAgentProviderProfile.disabled_reason.is_(None),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if profile is None:
                return
            apply_oauth_validation_failure(
                profile,
                mapping=get_first_party_oauth_profile(
                    profile.runtime_id, profile.provider_id
                ),
                reason=reason,
                failed_at=datetime.now(UTC),
            )
            await session.commit()
            await sync_provider_profile_manager(
                session=session, runtime_id=profile.runtime_id
            )
    except Exception as exc:
        # Database/provider errors can contain credentials or SQL parameters.
        # Retain only generation and exception type, never credential-derived
        # identifiers or exception text.
        logger.warning(
            "Could not record ended OAuth login: "
            "credential_generation=%s error_type=%s",
            credential_generation,
            type(exc).__name__,
        )
