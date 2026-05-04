"""Canonical GitHub credential resolution helpers."""

from __future__ import annotations

import asyncio
import os
import threading
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


async def resolve_github_credential(
    explicit_token: str | None = None,
    *,
    repo: str | None = None,
) -> ResolvedGitHubCredential:
    """Resolve GitHub auth with one project-wide precedence model."""

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
            )
        if token:
            return ResolvedGitHubCredential(
                token=token,
                source=GitHubCredentialSource.SECRET_REF_ENV,
                sourceName=env_name,
                repo=repo,
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
            )
        if token:
            return ResolvedGitHubCredential(
                token=token,
                source=GitHubCredentialSource.SECRET_REF_ENV,
                sourceName="settings.github.github_token_secret_ref",
                repo=repo,
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
            )
        if token:
            return ResolvedGitHubCredential(
                token=token,
                source=GitHubCredentialSource.SETTINGS_TOKEN_REF,
                sourceName=env_name,
                repo=repo,
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


def resolve_github_credential_sync(
    explicit_token: str | None = None,
    *,
    repo: str | None = None,
) -> ResolvedGitHubCredential:
    """Synchronous adapter for legacy sync GitHub callers."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(resolve_github_credential(explicit_token, repo=repo))

    result: list[ResolvedGitHubCredential] = []
    errors: list[Exception] = []

    def _resolve_in_thread() -> None:
        try:
            result.append(
                asyncio.run(resolve_github_credential(explicit_token, repo=repo))
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=_resolve_in_thread, daemon=True)
    thread.start()
    thread.join()

    if errors:
        raise errors[0]
    return result[0]
