from __future__ import annotations

from typing import Mapping, Optional, Protocol


class SecretProvider(Protocol):
    """Protocol for synchronous secret providers."""

    def get_secret(self, key: str) -> Optional[str]: ...


class EnvSecretProvider:
    def __init__(self, env: Mapping[str, str]):
        self.env = env

    def get_secret(self, key: str) -> Optional[str]:
        return self.env.get(key)


class ProfileSecretProvider:
    def __init__(self, profile: Mapping[str, str]):
        self.profile = profile

    def get_secret(self, key: str) -> Optional[str]:
        return self.profile.get(key)


class SecretProviderManager:
    def __init__(
        self,
        *,
        profile_provider: SecretProvider | None = None,
        env_provider: SecretProvider | None = None,
        extra_providers: Mapping[str, SecretProvider] | None = None,
    ) -> None:
        self.profile_provider = profile_provider
        self.env_provider = env_provider
        self.providers = {k.lower(): v for k, v in (extra_providers or {}).items()}

    def get_secret(self, provider: str, key: str) -> str:
        provider_lc = provider.lower()
        if provider_lc == "profile":
            if self.profile_provider:
                value = self.profile_provider.get_secret(key)
                if value is not None:
                    return value
            if self.env_provider:
                value = self.env_provider.get_secret(key)
                if value is not None:
                    return value
            raise ValueError(f"secret not found: {key}")

        if provider_lc == "env":
            if not self.env_provider:
                raise ValueError("env provider not configured")
            value = self.env_provider.get_secret(key)
            if value is None:
                raise ValueError(f"env variable not found: {key}")
            return value

        provider_obj = self.providers.get(provider_lc)
        if provider_obj is not None:
            value = provider_obj.get_secret(key)
            if value is None:
                raise ValueError(f"secret not found: {key}")
            return value
        raise ValueError(f"unsupported auth provider '{provider}'")
