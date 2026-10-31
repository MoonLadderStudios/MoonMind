"""Observe stored Codex logins and record proven expiry in profile readiness."""

import logging
from datetime import UTC, datetime

from moonmind.workflows.provider_failures import provider_auth_failure_markers

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


# Run on the admitted host, under its credential lease and restricted egress.
# account/read is the TUI bootstrap check: login status and model/list can both
# succeed from local state while workspace routing rejects the same login.
# Use the public CLI protocol, send no turn, and print no account/error bodies.
# Serialize the shared auth markers: refresh rejection may carry no HTTP status.
CODEX_ACCOUNT_READ_PROBE = (
    f"AUTH_FAILURE_MARKERS = {provider_auth_failure_markers()!r}\n"
    + """\
import asyncio, json, os, re, signal, sys

async def main():
    process = None
    verdict = "unavailable"
    try:
        process = await asyncio.create_subprocess_exec(
            "codex", "app-server",
            env={**os.environ, "CODEX_HOME": sys.argv[1]},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )

        async def request(request_id, method, params):
            process.stdin.write((json.dumps({
                "id": request_id, "method": method, "params": params,
            }) + "\\n").encode())
            await process.stdin.drain()
            while True:
                line = await process.stdout.readline()
                if not line:
                    raise RuntimeError("account probe stream ended")
                message = json.loads(line)
                if isinstance(message, dict) and message.get("id") == request_id:
                    return message

        async def read_account():
            initialized = await request(1, "initialize", {
                "clientInfo": {"name": "moonmind-login-preflight", "version": "1"},
            })
            if "error" in initialized or not isinstance(initialized.get("result"), dict):
                return "unavailable"
            process.stdin.write(b'{"method":"initialized"}\\n')
            await process.stdin.drain()
            response = await request(2, "account/read", {"refreshToken": False})

            def unauthorized(error):
                return isinstance(error, dict) and (
                    error.get("code") == 401 or re.search(
                        r"(?<![0-9])401(?![0-9])", str(error.get("message", ""))
                    )
                    or any(marker in str(error.get("message", "")).lower()
                           for marker in AUTH_FAILURE_MARKERS)
                )

            # The access token can be rejected while its refresh token is
            # still usable. Let Codex repair it once in the authoritative,
            # exclusively leased OAuth home before requiring a new sign-in.
            if unauthorized(response.get("error")):
                response = await request(3, "account/read", {"refreshToken": True})
            error = response.get("error")
            if isinstance(error, dict):
                if unauthorized(error):
                    return "unauthorized"
                return "unavailable"
            result = response.get("result")
            if not isinstance(result, dict):
                return "unavailable"
            account = result.get("account")
            if isinstance(account, dict) and account.get("type") == "chatgpt":
                return "ready"
            if account is None and result.get("requiresOpenaiAuth") is True:
                return "missing_login"
            return "unavailable"

        verdict = await asyncio.wait_for(read_account(), timeout=20)
    except Exception:
        pass
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
    print(verdict)

asyncio.run(main())
"""
)


async def record_ended_codex_login(
    profile_id: str,
    credential_generation: int,
    *,
    reason: str = "codex_login_session_ended",
) -> None:
    """Disable only the enrollment whose mounted login was proven unusable.

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
            "Could not record ended Codex login: "
            "credential_generation=%s error_type=%s",
            credential_generation,
            type(exc).__name__,
        )
