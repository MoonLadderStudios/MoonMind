import logging

from api_service.db.models import User

from .env_provider import EnvAuthProvider
from .profile_provider import ProfileAuthProvider


class AuthProviderManager:
    def __init__(
        self, profile_provider: ProfileAuthProvider, env_provider: EnvAuthProvider
    ) -> None:
        self.profile_provider = profile_provider
        self.env_provider = env_provider

    async def get_secret(
        self, provider: str, *, key: str, user: User | None = None, **kwargs
    ) -> str | None:
        provider = provider.lower()
        if provider == "profile":
            try:
                secret = await self.profile_provider.get_secret(key=key, user=user)
            except Exception as exc:  # pragma: no cover - provider failure
                logging.warning("Profile provider error: %s", exc)
                secret = None
            if secret:
                return secret
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
