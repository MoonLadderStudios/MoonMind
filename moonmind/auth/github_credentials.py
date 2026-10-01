"""Canonical GitHub credential resolution helpers."""

from __future__ import annotations

import asyncio
import os
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GitHubCredentialSource(StrEnum):
    EXPLICIT = "explicit"
    DIRECT_ENV = "direct_env"
    SECRET_REF_ENV = "secret_ref_env"
    SETTINGS_TOKEN_REF = "settings_token_ref"
    MISSING = "missing"
    UNRESOLVABLE = "unresolvable"


class ResolvedGitHubCredential(BaseModel):
    """Resolved token plus redaction-safe source metadata."""

    model_config = ConfigDict(populate_by_name=True)

    token: str = ""
    source: GitHubCredentialSource = GitHubCredentialSource.MISSING
    source_name: str | None = Field(None, alias="sourceName")
    repo: str | None = None
    diagnostic: str | None = None
    # A configured reference that could not be read (for example during a
    # brief secret-store outage) may resolve on retry; missing or empty
    # configuration will not.
    retryable: bool = False

    @property
    def resolved(self) -> bool:
        return bool(self.token)

    @property
    def safe_summary(self) -> str:
        if self.resolved:
            target = f" for {self.repo}" if self.repo else ""
            source = self.source_name or self.source.value
            return f"GitHub credential resolved from {source}{target}."
        return self.diagnostic or "GitHub credential is not configured."

    def safe_source_dict(self) -> dict[str, Any]:
        return {
            "sourceKind": self.source.value,
            "sourceName": self.source_name,
            "resolved": self.resolved,
        }


_DIRECT_TOKEN_ENVS: tuple[str, ...] = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "WORKFLOW_GITHUB_TOKEN",
)
_SECRET_REF_ENVS: tuple[str, ...] = (
    "GITHUB_TOKEN_SECRET_REF",
    "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
)
_SETTINGS_REF_ENVS: tuple[str, ...] = ("MOONMIND_GITHUB_TOKEN_REF",)


async def _resolve_secret_ref(ref: str) -> str:
    from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
        resolve_managed_api_key_reference,
    )

    return await resolve_managed_api_key_reference(ref)


def _secret_is_absent(exc: BaseException) -> bool:
    """Whether a reference failed because its secret is absent, not unreadable."""

    from moonmind.auth.secret_refs import SecretMissingError

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, SecretMissingError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


async def resolve_github_credential(
    explicit_token: str | None = None,
    *,
    repo: str | None = None,
) -> ResolvedGitHubCredential:
    """Resolve GitHub auth with one project-wide precedence model.

    ``explicit_token=None`` means omitted (ambient sources may apply).
    An explicitly passed-but-blank token (``""``/whitespace) is a distinct
    configured-empty state and fails closed instead of selecting ambient
    credentials (MoonLadderStudios/MoonMind#4007).
    """

    if explicit_token is not None and not str(explicit_token).strip():
        return ResolvedGitHubCredential(
            source=GitHubCredentialSource.UNRESOLVABLE,
            sourceName="explicit",
            repo=repo,
            diagnostic=(
                "Explicit GitHub credential is configured but empty"
                + (f" for {repo}." if repo else ".")
            ),
        )
    token = str(explicit_token or "").strip()
    if token:
        return ResolvedGitHubCredential(
            token=token,
            source=GitHubCredentialSource.EXPLICIT,
            sourceName="explicit",
            repo=repo,
        )

    for env_name in _DIRECT_TOKEN_ENVS:
        token = str(os.environ.get(env_name, "")).strip()
        if token:
            return ResolvedGitHubCredential(
                token=token,
                source=GitHubCredentialSource.DIRECT_ENV,
                sourceName=env_name,
                repo=repo,
            )

    for env_name in _SECRET_REF_ENVS:
        secret_ref = str(os.environ.get(env_name, "")).strip()
        if not secret_ref:
            continue
        try:
            token = await _resolve_secret_ref(secret_ref)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ResolvedGitHubCredential(
                source=GitHubCredentialSource.UNRESOLVABLE,
                sourceName=env_name,
                repo=repo,
                diagnostic=(
                    f"GitHub credential reference from {env_name} could not be resolved"
                    + (f" for {repo}." if repo else ".")
                ),
                retryable=True,
            )
        token = str(token or "").strip()
        if token:
            return ResolvedGitHubCredential(
                token=token,
                source=GitHubCredentialSource.SECRET_REF_ENV,
                sourceName=env_name,
                repo=repo,
            )
        # A configured reference resolving empty is fail-closed (#4007):
        # it must not fall through to ambient credentials.
        return ResolvedGitHubCredential(
            source=GitHubCredentialSource.UNRESOLVABLE,
            sourceName=env_name,
            repo=repo,
            diagnostic=(
                f"GitHub credential reference from {env_name} resolved empty"
                + (f" for {repo}." if repo else ".")
            ),
        )

    from moonmind.config.settings import settings

    settings_ref = str(getattr(settings.github, "github_token_secret_ref", "") or "").strip()
    if settings_ref:
        try:
            token = await _resolve_secret_ref(settings_ref)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ResolvedGitHubCredential(
                source=GitHubCredentialSource.UNRESOLVABLE,
                sourceName="settings.github.github_token_secret_ref",
                repo=repo,
                diagnostic=(
                    "GitHub credential reference from settings could not be resolved"
                    + (f" for {repo}." if repo else ".")
                ),
                retryable=True,
            )
        token = str(token or "").strip()
        if token:
            return ResolvedGitHubCredential(
                token=token,
                source=GitHubCredentialSource.SECRET_REF_ENV,
                sourceName="settings.github.github_token_secret_ref",
                repo=repo,
            )
        return ResolvedGitHubCredential(
            source=GitHubCredentialSource.UNRESOLVABLE,
            sourceName="settings.github.github_token_secret_ref",
            repo=repo,
            diagnostic=(
                "GitHub credential reference from settings resolved empty"
                + (f" for {repo}." if repo else ".")
            ),
        )

    for env_name in _SETTINGS_REF_ENVS:
        secret_ref = str(os.environ.get(env_name, "")).strip()
        if not secret_ref:
            continue
        try:
            token = await _resolve_secret_ref(secret_ref)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ResolvedGitHubCredential(
                source=GitHubCredentialSource.UNRESOLVABLE,
                sourceName=env_name,
                repo=repo,
                diagnostic=(
                    f"GitHub credential reference from {env_name} could not be resolved"
                    + (f" for {repo}." if repo else ".")
                ),
                retryable=True,
            )
        token = str(token or "").strip()
        if token:
            return ResolvedGitHubCredential(
                token=token,
                source=GitHubCredentialSource.SETTINGS_TOKEN_REF,
                sourceName=env_name,
                repo=repo,
            )
        return ResolvedGitHubCredential(
            source=GitHubCredentialSource.UNRESOLVABLE,
            sourceName=env_name,
            repo=repo,
            diagnostic=(
                f"GitHub credential reference from {env_name} resolved empty"
                + (f" for {repo}." if repo else ".")
            ),
        )

    target = f" for {repo}" if repo else ""
    return ResolvedGitHubCredential(
        source=GitHubCredentialSource.MISSING,
        repo=repo,
        diagnostic=(
            "GitHub auth is not configured"
            f"{target}; set GITHUB_TOKEN, GH_TOKEN, WORKFLOW_GITHUB_TOKEN, "
            "GITHUB_TOKEN_SECRET_REF, WORKFLOW_GITHUB_TOKEN_SECRET_REF, "
            "or MOONMIND_GITHUB_TOKEN_REF."
        ),
    )


async def resolve_deployment_github_credential(
    *,
    repo: str | None = None,
) -> ResolvedGitHubCredential:
    """Resolve the deployment's GitHub declaration (#4023).

    This derives ``repository-connection:git-default`` while it is not
    recorded, with the precedence migration ``391_legacy_github_cred_4023``
    records: the configured sources above, then an active ``GITHUB_TOKEN`` or
    ``GITHUB_PAT`` managed secret, so a secret saved in Settings after the
    migration still selects the default. The managed secret is consulted only
    when nothing else is configured; a configured source that fails is the
    result, and an unreadable secret store is not mistaken for no secret.
    """

    resolved = await resolve_github_credential(repo=repo)
    if resolved.source != GitHubCredentialSource.MISSING:
        return resolved
    from moonmind.workflows.temporal.runtime import managed_api_key_resolve

    try:
        slug = await managed_api_key_resolve.load_active_managed_github_secret_slug()
    except asyncio.CancelledError:
        raise
    except Exception:
        return ResolvedGitHubCredential(
            source=GitHubCredentialSource.UNRESOLVABLE,
            sourceName="managed secrets",
            repo=repo,
            diagnostic=(
                "The GITHUB_TOKEN and GITHUB_PAT managed secrets could not be read"
                + (f" for {repo}" if repo else "")
                + "; MoonMind does not try another GitHub credential."
            ),
            retryable=True,
        )
    if slug is None:
        return resolved
    return await _resolve_reference_credential(
        f"db://{slug}",
        subject="Deployment GitHub credential",
        source_name=f"managed secret {slug}",
        repo=repo,
    )


async def _resolve_reference_credential(
    reference: str,
    *,
    subject: str,
    source_name: str,
    repo: str | None,
) -> ResolvedGitHubCredential:
    """Read one selected SecretRef; a failure is the result, never a fallback."""

    target = f" for {repo}" if repo else ""
    correction = (
        "; MoonMind does not try another GitHub credential. Set or rotate "
        f"{reference}, or select a different repository connection."
    )
    try:
        token = str(await _resolve_secret_ref(reference) or "").strip()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        absent = _secret_is_absent(exc)
        return ResolvedGitHubCredential(
            source=GitHubCredentialSource.UNRESOLVABLE,
            sourceName=source_name,
            repo=repo,
            diagnostic=(
                f"{subject} {reference} "
                + ("is not set" if absent else "could not be read")
                + f"{target}{correction}"
            ),
            retryable=not absent,
        )
    if not token:
        return ResolvedGitHubCredential(
            source=GitHubCredentialSource.UNRESOLVABLE,
            sourceName=source_name,
            repo=repo,
            diagnostic=f"{subject} {reference} is empty{target}{correction}",
        )
    return ResolvedGitHubCredential(
        token=token,
        source=GitHubCredentialSource.SECRET_REF_ENV,
        sourceName=source_name,
        repo=repo,
    )


async def resolve_connection_github_credential(
    connection: Any,
    *,
    repo: str | None = None,
) -> ResolvedGitHubCredential:
    """Resolve only the credential a selected repository connection names.

    A typed SecretRef is read from its own backend and nowhere else: when it
    is unreadable or empty the result is unresolved, never another
    environment token, Settings reference, or managed secret
    (MoonLadderStudios/MoonMind#4023). Only historical ``github_resolver``
    connections keep the deployment declaration
    (:func:`resolve_deployment_github_credential`).
    """

    connection_id = str(getattr(connection, "id", "") or "").strip() or "connection"
    credential = getattr(connection, "credential", None)
    source = str(getattr(credential, "source", "") or "").strip()
    target = f" for {repo}" if repo else ""
    if source == "github_resolver":
        return await resolve_deployment_github_credential(repo=repo)
    if source == "secret_ref":
        ref = getattr(credential, "credential_ref", None)
        reference = (
            f"{str(getattr(ref, 'provider', '') or '').strip()}://"
            f"{str(getattr(ref, 'key', '') or '').strip()}"
        )
        return await _resolve_reference_credential(
            reference,
            subject=f"Repository connection {connection_id} credential",
            source_name=connection_id,
            repo=repo,
        )
    return ResolvedGitHubCredential(
        source=GitHubCredentialSource.UNRESOLVABLE,
        sourceName=connection_id,
        repo=repo,
        diagnostic=(
            f"Repository connection {connection_id} uses {source or 'an unknown'} "
            f"credentials, which this boundary cannot acquire{target}; MoonMind "
            "does not substitute another GitHub credential."
        ),
    )
