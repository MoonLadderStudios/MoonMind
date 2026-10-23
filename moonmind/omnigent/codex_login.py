"""Observe stored Codex logins and record proven expiry in profile readiness."""

import hashlib
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Run as ``python3 -c <probe> <codex home>``. Print only a verdict, never
# token material. An already-expired token minted without a lifetime proves
# the login ended; an ordinarily aged token may still refresh when Codex runs.
# API-key auth and unreadable or unexpected layouts remain unknown.
CODEX_LOGIN_SESSION_PROBE = """\
import base64, json, sys, time
try:
    with open(sys.argv[1] + "/auth.json", encoding="utf-8") as handle:
        token = json.load(handle)["tokens"]["access_token"]
    claims = token.split(".")[1]
    claims += "=" * (-len(claims) % 4)
    claims = json.loads(base64.urlsafe_b64decode(claims))
    expires_at = float(claims["exp"])
    ended = expires_at <= float(claims["iat"]) and expires_at <= time.time()
    print("ended" if ended else "active")
except Exception:
    print("unknown")
"""


async def record_ended_codex_login(profile_id: str, credential_generation: int) -> None:
    """Disable only the enrollment whose mounted login was proven ended.

    Reuse OAuth validation's Settings and routing state. Lock the profile while
    checking its generation so an old host cannot invalidate a reconnect.
    Recording failure must not replace the original authentication failure.
    """
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
                        ManagedAgentProviderProfile.runtime_id == "codex_cli",
                        ManagedAgentProviderProfile.provider_id == "openai",
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
                reason="codex_login_session_ended",
                failed_at=datetime.now(UTC),
            )
            await session.commit()
            await sync_provider_profile_manager(
                session=session, runtime_id=profile.runtime_id
            )
    except Exception as exc:
        # Database/provider errors can contain credentials or SQL parameters.
        # Hash credential-derived identifiers; retain only correlation,
        # generation and exception type, never raw values or exception text.
        logger.warning(
            "Could not record ended Codex login: profile_id_sha256=%s "
            "credential_generation=%s error_type=%s",
            hashlib.sha256(profile_id.encode("utf-8", "replace")).hexdigest()[:12],
            credential_generation,
            type(exc).__name__,
        )
