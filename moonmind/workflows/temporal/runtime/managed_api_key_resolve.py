"""Resolve SecretRefs for managed agent subprocess launches."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import select

from moonmind.auth.secret_refs import parse_secret_ref, SecretBackend, VaultSecretResolver, load_vault_token
from moonmind.auth.resolvers import (
    EnvSecretResolver,
    DbEncryptedSecretResolver,
    ExecSecretResolver,
    AdapterVaultSecretResolver,
    RootSecretResolver,
)

# Slugs tried when no profile secret_refs / env token / WORKFLOW_GITHUB_TOKEN_SECRET_REF
# produced a token (matches api_service startup seeding and dashboard hints).
_MANAGED_GITHUB_TOKEN_SLUGS: tuple[str, ...] = (
    "GITHUB_TOKEN",
    "GITHUB_PAT",
)

logger = logging.getLogger(__name__)


def _normalize_secret_ref_input(
    ref: str | Mapping[str, Any],
    *,
    field_name: str = "MANAGED_API_KEY_REF",
) -> str:
    if not isinstance(ref, str):
        raise ValueError(
            f"{field_name} must be a string secret reference, got {type(ref).__name__}"
        )

    stripped = ref.strip()
    if not stripped:
        raise ValueError(f"{field_name} is empty")
    return stripped


async def resolve_managed_github_token_from_store() -> str | None:
    """Return an active GitHub PAT from managed secrets (Settings), if any.

    This is separate from provider profile ``secret_refs``: operators store one
    org-wide token under a well-known slug without binding it to each profile.
    """
    from api_service.db.base import async_session_maker
    from api_service.db.models import ManagedSecret, SecretStatus

    async with async_session_maker() as session:
        for slug in _MANAGED_GITHUB_TOKEN_SLUGS:
            # Probe well-known slugs quietly so an expected "not configured"
            # path does not emit one warning per candidate.
            result = await session.execute(
                select(ManagedSecret).where(
                    ManagedSecret.slug == slug,
                    ManagedSecret.status == SecretStatus.ACTIVE,
                )
            )
            secret = result.scalar_one_or_none()
            candidate = str(secret.ciphertext if secret else "").strip()
            if candidate:
                return candidate
    return None


async def resolve_github_token_for_launch(
    environment: Mapping[str, str] | None = None,
    *,
    github_credential: Any | None = None,
) -> str | None:
    """Resolve the GitHub token used for launch-time auth seeding.

    When a non-sensitive descriptor is provided, the descriptor controls
    resolution. Legacy environment ``GITHUB_TOKEN`` remains a launch-boundary
    input only so older callers can still be scrubbed before container launch.
    """

    launch_environment = environment or {}
    token = str(launch_environment.get("GITHUB_TOKEN", "")).strip()
    if token:
        return token

    if github_credential is not None:
        source = str(getattr(github_credential, "source", "") or "").strip()
        required = bool(getattr(github_credential, "required", False))
        if source == "environment":
            env_var = str(
                getattr(github_credential, "env_var", None) or "GITHUB_TOKEN"
            ).strip()
            token = str(os.environ.get(env_var, "")).strip()
            if token:
                return token
            if required:
                raise ValueError(
                    f"GitHub credential environment reference {env_var} is not set"
                )
            return None
        if source == "secret_ref":
            secret_ref = str(getattr(github_credential, "secret_ref", "") or "").strip()
            if not secret_ref:
                if required:
                    raise ValueError("GitHub credential secretRef is not configured")
                return None
            try:
                return await resolve_managed_api_key_reference(
                    secret_ref,
                    field_name="githubCredential.secretRef",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if required:
                    raise ValueError(
                        "GitHub credential secretRef could not be resolved"
                    ) from exc
                logger.warning(
                    "Failed to resolve GitHub credential secret ref for managed "
                    "runtime launch",
                    exc_info=True,
                )
                return None
        if source == "managed_secret":
            try:
                resolved = await resolve_managed_github_token_from_store()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if required:
                    raise ValueError(
                        "GitHub credential managed secret could not be resolved"
                    ) from exc
                logger.warning(
                    "Failed to resolve GitHub token from managed secrets store",
                    exc_info=True,
                )
                return None
            if resolved:
                return resolved
            if required:
                raise ValueError("GitHub credential managed secret is not configured")
            return None
        raise ValueError(f"Unsupported GitHub credential source: {source or '<blank>'}")

    from moonmind.config.settings import settings as _mm_settings

    secret_ref = str(
        getattr(_mm_settings.github, "github_token_secret_ref", "") or ""
    ).strip()
    if secret_ref:
        try:
            return await resolve_managed_api_key_reference(secret_ref)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Failed to resolve GitHub token secret ref for managed runtime launch",
                exc_info=True,
            )

    try:
        return await resolve_managed_github_token_from_store()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning(
            "Failed to resolve GitHub token from managed secrets store",
            exc_info=True,
        )
        return None


def build_github_credential_descriptor_for_launch(
    environment: Mapping[str, str] | None = None,
    *,
    ambient_github_token: str | None = None,
    enable_managed_secret_fallback: bool = False,
) -> Any:
    """Return a non-sensitive GitHub launch credential descriptor."""

    from moonmind.config.settings import settings as _mm_settings
    from moonmind.schemas.managed_session_models import (
        ManagedGitHubCredentialDescriptor,
    )

    launch_environment = environment or {}
    if str(launch_environment.get("GITHUB_TOKEN", "")).strip():
        return ManagedGitHubCredentialDescriptor(
            source="environment",
            envVar="GITHUB_TOKEN",
            required=False,
        )

    ambient_token = str(ambient_github_token or "").strip()
    if enable_managed_secret_fallback and ambient_token:
        return ManagedGitHubCredentialDescriptor(
            source="environment",
            envVar="GITHUB_TOKEN",
            required=False,
        )

    secret_ref = str(
        getattr(_mm_settings.github, "github_token_secret_ref", "") or ""
    ).strip()
    if enable_managed_secret_fallback and secret_ref:
        return ManagedGitHubCredentialDescriptor(
            source="secret_ref",
            secretRef=secret_ref,
            required=False,
        )

    if enable_managed_secret_fallback:
        return ManagedGitHubCredentialDescriptor(source="managed_secret", required=False)
    return None


async def shape_launch_github_auth_environment(
    environment: Mapping[str, str] | None = None,
    *,
    ambient_github_token: str | None = None,
) -> dict[str, str]:
    """Return launch env with GitHub auth seeded using explicit precedence."""

    shaped_environment = {
        str(key): str(value) for key, value in (environment or {}).items()
    }
    ambient_token = str(ambient_github_token or "").strip()

    if ambient_token and not str(shaped_environment.get("GITHUB_TOKEN", "")).strip():
        shaped_environment["GITHUB_TOKEN"] = ambient_token

    github_token = await resolve_github_token_for_launch(shaped_environment)
    if github_token:
        shaped_environment["GITHUB_TOKEN"] = github_token
        shaped_environment.setdefault("GIT_TERMINAL_PROMPT", "0")

    return shaped_environment


async def resolve_managed_api_key_reference(
    ref: str | Mapping[str, Any],
    *,
    field_name: str = "MANAGED_API_KEY_REF",
) -> str:
    """Resolve a profile api_key_ref or secret_ref into the credential string using RootSecretResolver."""

    stripped = _normalize_secret_ref_input(ref, field_name=field_name)
        
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
    "build_github_credential_descriptor_for_launch",
    "resolve_github_token_for_launch",
    "resolve_managed_api_key_reference",
    "resolve_managed_github_token_from_store",
    "shape_launch_github_auth_environment",
]
