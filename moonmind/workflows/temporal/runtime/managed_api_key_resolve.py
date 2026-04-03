"""Resolve SecretRefs for managed agent subprocess launches."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from moonmind.auth.secret_refs import parse_secret_ref, SecretBackend, VaultSecretResolver, load_vault_token
from moonmind.auth.resolvers import (
    EnvSecretResolver,
    DbEncryptedSecretResolver,
    ExecSecretResolver,
    AdapterVaultSecretResolver,
    RootSecretResolver,
)

logger = logging.getLogger(__name__)

# Slugs tried when no profile secret_refs / env token / WORKFLOW_GITHUB_TOKEN_SECRET_REF
# produced a token (matches api_service startup seeding and dashboard hints).
_MANAGED_GITHUB_TOKEN_SLUGS: tuple[str, ...] = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_PAT",
)


async def resolve_managed_github_token_from_store() -> str | None:
    """Return an active GitHub PAT from managed secrets (Settings), if any.

    This is separate from provider profile ``secret_refs``: operators store one
    org-wide token under a well-known slug without binding it to each profile.
    """
    from api_service.db.base import async_session_maker
    from api_service.services.secrets import SecretsService

    for slug in _MANAGED_GITHUB_TOKEN_SLUGS:
        try:
            async with async_session_maker() as session:
                plaintext = await SecretsService.get_secret(session, slug)
        except Exception:
            logger.debug(
                "Managed GitHub token lookup failed for slug %s",
                slug,
                exc_info=True,
            )
            continue
        candidate = str(plaintext or "").strip()
        if candidate:
            return candidate
    return None


async def resolve_managed_api_key_reference(ref: str) -> str:
    """Resolve a profile api_key_ref or secret_ref into the credential string using RootSecretResolver."""

    stripped = ref.strip()
    if not stripped:
        raise ValueError("MANAGED_API_KEY_REF is empty")
        
    if "://" not in stripped:
        stripped = f"env://{stripped}"

    try:
        parsed = parse_secret_ref(stripped)
    except Exception as e:
        raise ValueError(f"Unable to resolve MANAGED_API_KEY_REF={ref!r}: {e}")

    resolvers = {}
    vault_resolver_instance = None

    if parsed.backend == SecretBackend.ENV:
        resolvers[SecretBackend.ENV] = EnvSecretResolver()

    elif parsed.backend == SecretBackend.DB_ENCRYPTED:
        resolvers[SecretBackend.DB_ENCRYPTED] = DbEncryptedSecretResolver()

    elif parsed.backend == SecretBackend.EXEC:
        resolvers[SecretBackend.EXEC] = ExecSecretResolver()

    elif parsed.backend == SecretBackend.VAULT:
        addr = str(
            os.environ.get("MOONMIND_VAULT_ADDR")
            or os.environ.get("VAULT_ADDR")
            or ""
        ).strip()
        token_file_raw = str(os.environ.get("MOONMIND_VAULT_TOKEN_FILE", "")).strip()
        token_file = Path(token_file_raw) if token_file_raw else None
        direct_token = str(
            os.environ.get("MOONMIND_VAULT_TOKEN")
            or os.environ.get("VAULT_TOKEN")
            or ""
        ).strip()
        token = load_vault_token(
            token=direct_token or None,
            token_file=token_file,
        )
        if not addr or not token:
            raise ValueError(
                "vault:// api_key_ref requires MOONMIND_VAULT_ADDR (or VAULT_ADDR) "
                "and MOONMIND_VAULT_TOKEN / VAULT_TOKEN or MOONMIND_VAULT_TOKEN_FILE"
            )
        namespace = str(
            os.environ.get("MOONMIND_VAULT_NAMESPACE")
            or os.environ.get("VAULT_NAMESPACE")
            or ""
        ).strip() or None
        mounts_csv = str(os.environ.get("MOONMIND_VAULT_ALLOWED_MOUNTS", "kv")).strip()
        allowed = tuple(m.strip() for m in mounts_csv.split(",") if m.strip()) or (
            "kv",
        )
        vault_resolver_instance = VaultSecretResolver(
            address=addr,
            token=token,
            namespace=namespace,
            allowed_mounts=allowed,
        )
        resolvers[SecretBackend.VAULT] = AdapterVaultSecretResolver(vault_resolver_instance)

    root_resolver = RootSecretResolver(resolvers)

    try:
        return await root_resolver.resolve(parsed)
    except Exception as e:
        raise ValueError(f"Unable to resolve MANAGED_API_KEY_REF={ref!r}: {e}")
    finally:
        if vault_resolver_instance is not None:
            await vault_resolver_instance.aclose()


__all__ = [
    "resolve_managed_api_key_reference",
    "resolve_managed_github_token_from_store",
]
