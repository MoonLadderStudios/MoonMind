"""Unit tests for codex worker Vault secret reference helpers."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from moonmind.auth.secret_refs import (
    ParsedSecretRef,
    SecretBackend,
    SecretReferenceError,
    VaultSecretResolver,
    load_vault_token,
    parse_secret_ref,
    parse_vault_reference,
)

pytestmark = [pytest.mark.asyncio]


def test_parse_secret_ref_env() -> None:
    """Parser should validate env:// references."""
    parsed = parse_secret_ref("env://MINIMAX_API_KEY")
    assert parsed.backend == SecretBackend.ENV
    assert parsed.locator == "MINIMAX_API_KEY"
    assert parsed.normalized_ref == "env://MINIMAX_API_KEY"


def test_parse_secret_ref_db() -> None:
    """Parser should validate db:// references."""
    parsed = parse_secret_ref("db://provider-minimax-api-key")
    assert parsed.backend == SecretBackend.DB_ENCRYPTED
    assert parsed.locator == "provider-minimax-api-key"
    assert parsed.normalized_ref == "db://provider-minimax-api-key"


def test_parse_secret_ref_exec() -> None:
    """Parser should validate exec:// references."""
    parsed = parse_secret_ref("exec://op?vault=MoonMind&item=minimax&field=api_key")
    assert parsed.backend == SecretBackend.EXEC
    assert parsed.locator == "op?vault=MoonMind&item=minimax&field=api_key"
    assert parsed.normalized_ref == "exec://op?vault=MoonMind&item=minimax&field=api_key"


def test_parse_secret_ref_vault() -> None:
    """Parser should validate vault:// references."""
    parsed = parse_secret_ref("vault://kv/providers/minimax#api_key")
    assert parsed.backend == SecretBackend.VAULT
    assert parsed.locator == "kv/providers/minimax#api_key"
    assert parsed.normalized_ref == "vault://kv/providers/minimax#api_key"


def test_parse_secret_ref_invalid_scheme() -> None:
    """Parser should reject unsupported schemes."""
    with pytest.raises(SecretReferenceError, match="unsupported secret backend"):
        parse_secret_ref("https://example.com/secret")


def test_parse_secret_ref_invalid_env() -> None:
    """Parser should reject invalid env locators."""
    with pytest.raises(SecretReferenceError, match="invalid env locator format"):
        parse_secret_ref("env://my-key-with-dashes")


def test_parse_secret_ref_invalid_db() -> None:
    """Parser should reject invalid db locators."""
    with pytest.raises(SecretReferenceError, match="invalid db locator format"):
        parse_secret_ref("db://My_Key_With_Uppercase")


async def test_parse_vault_reference_accepts_valid_ref() -> None:
    """Parser should normalize valid vault:// mount/path#field references."""

    parsed = parse_vault_reference(
        "vault://kv/moonmind/repos/Moon/Mind#github_token",
        allowed_mounts=("kv",),
    )

    assert parsed.mount == "kv"
    assert parsed.path == "moonmind/repos/Moon/Mind"
    assert parsed.field == "github_token"
    assert parsed.normalized_ref == "vault://kv/moonmind/repos/Moon/Mind#github_token"


async def test_parse_vault_reference_rejects_invalid_scheme() -> None:
    """Only vault:// references are allowed in hardened auth contract."""

    with pytest.raises(SecretReferenceError, match="vault:// scheme"):
        parse_vault_reference("https://example.com/secret")


async def test_load_vault_token_prefers_direct_token(tmp_path: Path) -> None:
    """Explicit token should take precedence over token-file fallback."""

    token_file = tmp_path / "token.txt"
    token_file.write_text("from-file\n", encoding="utf-8")

    token = load_vault_token(token="from-env", token_file=token_file)

    assert token == "from-env"


async def test_vault_secret_resolver_reads_kv_v2_field() -> None:
    """Resolver should read token/metadata from Vault KV-v2 JSON structure."""

    async def _handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Vault-Token"] == "vault-token"
        assert request.url.path == "/v1/kv/data/moonmind/repos/Moon/Mind"
        return httpx.Response(
            200,
            json={
                "data": {
                    "data": {
                        "github_token": "ghp_test_secret",
                        "username": "x-access-token",
                        "host": "github.com",
                    }
                }
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler),
        base_url="https://vault.local",
    )
    resolver = VaultSecretResolver(
        address="https://vault.local",
        token="vault-token",
        allowed_mounts=("kv",),
        client=client,
    )

    resolved = await resolver.resolve_github_auth(
        "vault://kv/moonmind/repos/Moon/Mind#github_token"
    )

    await client.aclose()
    assert resolved.token == "ghp_test_secret"
    assert resolved.username == "x-access-token"
    assert resolved.host == "github.com"
    assert resolved.source_ref == "vault://kv/moonmind/repos/Moon/Mind#github_token"


async def test_vault_secret_resolver_rejects_missing_field() -> None:
    """Missing referenced field should fail with non-secret error details."""

    async def _handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"data": {}}})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler),
        base_url="https://vault.local",
    )
    resolver = VaultSecretResolver(
        address="https://vault.local",
        token="vault-token",
        client=client,
    )

    with pytest.raises(SecretReferenceError, match="missing or empty"):
        await resolver.resolve_github_auth("vault://kv/path/to/secret#token")
    await client.aclose()
