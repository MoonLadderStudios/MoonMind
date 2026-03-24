"""Resolve ``MANAGED_API_KEY_REF`` for managed agent subprocess launches."""

from __future__ import annotations

import os
import re
from pathlib import Path

from moonmind.auth.secret_refs import VaultSecretResolver, load_vault_token

_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


async def resolve_managed_api_key_reference(ref: str) -> str:
    """Resolve a profile ``api_key_ref`` into the credential string.

    Supported forms:

    - **Environment indirection**: value is an env var name (e.g. ``MINIMAX_API_KEY``)
      that is set on the agent worker process.
    - **Vault**: ``vault://<mount>/<path>#<field>`` when Vault env is configured
      on the worker (same variables as the codex worker: ``MOONMIND_VAULT_ADDR``,
      ``MOONMIND_VAULT_TOKEN`` or token file, etc.).
    """

    stripped = str(ref or "").strip()
    if not stripped:
        raise ValueError("MANAGED_API_KEY_REF is empty")

    if stripped.lower().startswith("vault://"):
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
        resolver = VaultSecretResolver(
            address=addr,
            token=token,
            namespace=namespace,
            allowed_mounts=allowed,
        )
        try:
            return await resolver.resolve_plain_kv_value(stripped)
        finally:
            await resolver.aclose()

    if _ENV_KEY_PATTERN.fullmatch(stripped):
        val = os.environ.get(stripped)
        if val and str(val).strip():
            return str(val).strip()

    raise ValueError(
        f"Unable to resolve MANAGED_API_KEY_REF={stripped!r}: "
        "set that environment variable on the agent worker, or use vault://..."
    )


__all__ = ["resolve_managed_api_key_reference"]
