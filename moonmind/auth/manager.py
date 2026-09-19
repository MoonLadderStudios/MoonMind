import logging
from typing import Any

from .env_provider import EnvAuthProvider
from .profile_provider import ProfileAuthProvider

class AuthProviderManager:
    def __init__(
        self, profile_provider: ProfileAuthProvider, env_provider: EnvAuthProvider
    ) -> None:
        self.profile_provider = profile_provider
        self.env_provider = env_provider

    async def get_secret(
        self,
        provider: str,
        *,
        key: str,
        user: Any | None = None,
        profile_id: str | None = None,
        allow_env_fallback: bool | None = None,
        **kwargs: Any,
    ) -> str | None:
        provider = provider.lower()
        if provider == "profile":
            # A caller bound to an explicit profile must fail closed on that
            # profile alone: never select another profile, account, model, or
            # billing route via ambient env fallback unless the caller opts
            # in explicitly. Legacy user-only callers keep the prior default.
            bound_profile = profile_id or kwargs.get("profile_id")
            if allow_env_fallback is None:
                allow_env_fallback = False if bound_profile else True
            try:
                secret = await self.profile_provider.get_secret(
                    key=key, user=user, profile_id=bound_profile, **kwargs
                )
            except Exception as exc:  # pragma: no cover - provider failure
                logging.warning("Profile provider error: %s", exc)
                secret = None
            if secret:
                return secret
            if not allow_env_fallback:
                return None
            try:
                return await self.env_provider.get_secret(key=key)
            except Exception as exc:  # pragma: no cover - provider failure
                logging.warning("Env provider error: %s", exc)
                return None
        if provider == "env":
            try:
                return await self.env_provider.get_secret(key=key)
            except Exception as exc:  # pragma: no cover - provider failure
                logging.warning("Env provider error: %s", exc)
                return None
        raise ValueError(f"Unknown provider {provider}")
