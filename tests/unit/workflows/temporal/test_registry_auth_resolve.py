"""Generic registry-auth resolver coverage (MoonLadderStudios/MoonMind#3257)."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.runtime import registry_auth_resolve
from moonmind.workflows.temporal.runtime.registry_auth_resolve import (
    RegistryAuthResolutionError,
    resolve_registry_pull_credentials,
)


@pytest.fixture
def fake_secret(monkeypatch):
    values: dict[str, str] = {}

    async def _resolve(ref, *, field_name="registryCredentialRef"):
        if ref not in values:
            raise ValueError(f"unresolved: {ref}")
        return values[ref]

    monkeypatch.setattr(
        registry_auth_resolve, "resolve_managed_api_key_reference", _resolve
    )
    return values


@pytest.mark.asyncio
async def test_resolves_json_username_password(fake_secret) -> None:
    fake_secret["db://ghcr"] = '{"username": "octo", "password": "s3cret"}'
    credential = await resolve_registry_pull_credentials("db://ghcr")
    assert credential.username == "octo"
    assert credential.secret == "s3cret"
    assert credential.docker_auth_entry() == {"username": "octo", "password": "s3cret"}


@pytest.mark.asyncio
async def test_resolves_json_token_alias(fake_secret) -> None:
    fake_secret["db://ghcr"] = '{"username": "octo", "token": "ghp_abc"}'
    credential = await resolve_registry_pull_credentials("db://ghcr")
    assert credential.secret == "ghp_abc"


@pytest.mark.asyncio
async def test_resolves_colon_pair_preserving_secret_colons(fake_secret) -> None:
    fake_secret["db://ghcr"] = "octo:tok:with:colons"
    credential = await resolve_registry_pull_credentials("db://ghcr")
    assert credential.username == "octo"
    assert credential.secret == "tok:with:colons"


@pytest.mark.asyncio
async def test_empty_reference_is_rejected(fake_secret) -> None:
    with pytest.raises(RegistryAuthResolutionError):
        await resolve_registry_pull_credentials("   ")


@pytest.mark.asyncio
async def test_unresolvable_reference_is_normalized_error(fake_secret) -> None:
    with pytest.raises(RegistryAuthResolutionError):
        await resolve_registry_pull_credentials("db://missing")


@pytest.mark.asyncio
async def test_malformed_value_is_rejected(fake_secret) -> None:
    fake_secret["db://ghcr"] = "no-colon-and-not-json"
    with pytest.raises(RegistryAuthResolutionError):
        await resolve_registry_pull_credentials("db://ghcr")
