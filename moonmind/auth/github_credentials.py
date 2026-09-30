"""Canonical GitHub credential resolution helpers."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Mapping, Sequence
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


# ---------------------------------------------------------------------------
# Legacy identity classification (MoonLadderStudios/MoonMind#4023).
#
# Migration input only: the one legacy-credential migration uses this to map
# the effective legacy reference onto ``repository-connection:git-default``.
# It mirrors the precedence of ``resolve_github_credential`` but never reads,
# compares, or probes token values, so distinct accounts are never merged and
# no token is tested against GitHub to choose a winner.
# ---------------------------------------------------------------------------

OPERATOR_GITHUB_TOKEN_SETTING = "settings:integrations.github.token_ref"
_SETTINGS_FILE_REF_SOURCE = "settings.github.github_token_secret_ref"


class LegacyGitHubIdentityOutcome(StrEnum):
    PROVEN = "proven"
    ABSENT = "absent"
    UNREADABLE = "unreadable"
    CONFLICTING = "conflicting"


class LegacyGitHubIdentity(BaseModel):
    """Effective legacy GitHub credential reference; never a token value."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    outcome: LegacyGitHubIdentityOutcome
    credential_ref: str | None = Field(None, alias="credentialRef")
    source_name: str | None = Field(None, alias="sourceName")
    considered: tuple[str, ...] = ()
    correction: str | None = None

    def secret_ref_parts(self) -> tuple[str, str]:
        """Return the proven ``(provider, key)`` SecretRef locator."""

        if self.outcome is not LegacyGitHubIdentityOutcome.PROVEN or not self.credential_ref:
            raise ValueError("only a proven legacy identity has a SecretRef")
        provider, key = self.credential_ref.split("://", 1)
        return provider, key

    def safe_diagnostic(self, *, affected_action: str) -> dict[str, Any]:
        """Reference names, outcome, correction and affected action only."""

        return {
            "outcome": self.outcome.value,
            "credentialRef": self.credential_ref,
            "sourceName": self.source_name,
            "considered": list(self.considered),
            "correction": self.correction,
            "affectedAction": affected_action,
        }


def _parse_reference(raw: str) -> str | None:
    from moonmind.auth.secret_refs import SecretReferenceError, parse_secret_ref

    try:
        return parse_secret_ref(raw).normalized_ref
    except SecretReferenceError:
        return None


def classify_legacy_github_credential(
    environ: Mapping[str, str],
    *,
    settings_ref: str | None = None,
    operator_setting_refs: Sequence[str] = (),
) -> LegacyGitHubIdentity:
    """Classify the effective legacy GitHub credential reference.

    ``environ`` is the deployment configuration the legacy chain read.
    ``settings_ref`` is ``settings.github.github_token_secret_ref`` (which may
    come from a settings file rather than the environment).
    ``operator_setting_refs`` are persisted ``integrations.github.token_ref``
    overrides: explicit operator choices the runtime chain never consumed.

    The first configured legacy source is the effective one, exactly as the
    runtime chain fails closed on a configured-but-unreadable reference
    instead of falling through. A different explicit operator choice is a
    conflict to correct, not a winner to guess.
    """

    considered: list[str] = []
    effective: tuple[str, str] | None = None
    for env_name in _DIRECT_TOKEN_ENVS:
        if str(environ.get(env_name, "") or "").strip():
            considered.append(env_name)
            effective = (env_name, f"env://{env_name}")
            break
    if effective is None:
        configured_refs = [
            *((name, environ.get(name)) for name in _SECRET_REF_ENVS),
            (_SETTINGS_FILE_REF_SOURCE, settings_ref),
            *((name, environ.get(name)) for name in _SETTINGS_REF_ENVS),
        ]
        for name, raw in configured_refs:
            candidate = str(raw or "").strip()
            if not candidate:
                continue
            considered.append(name)
            normalized = _parse_reference(candidate)
            if normalized is None:
                return LegacyGitHubIdentity(
                    outcome=LegacyGitHubIdentityOutcome.UNREADABLE,
                    sourceName=name,
                    considered=tuple(considered),
                    correction=(
                        f"{name} is configured but is not a readable secret "
                        "reference (<backend>://<locator>); fix or remove it "
                        "and restart MoonMind."
                    ),
                )
            effective = (name, normalized)
            break

    operator_refs: list[str] = []
    for raw in operator_setting_refs:
        candidate = str(raw or "").strip()
        if not candidate:
            continue
        considered.append(OPERATOR_GITHUB_TOKEN_SETTING)
        normalized = _parse_reference(candidate)
        if normalized is None:
            return LegacyGitHubIdentity(
                outcome=LegacyGitHubIdentityOutcome.UNREADABLE,
                sourceName=OPERATOR_GITHUB_TOKEN_SETTING,
                considered=tuple(dict.fromkeys(considered)),
                correction=(
                    "Settings integrations.github.token_ref is not a readable "
                    "secret reference; choose a managed secret or clear it and "
                    "restart MoonMind."
                ),
            )
        if normalized not in operator_refs:
            operator_refs.append(normalized)
    considered_names = tuple(dict.fromkeys(considered))

    declared = [effective[1]] if effective is not None else []
    declared.extend(ref for ref in operator_refs if ref not in declared)
    if len(declared) > 1:
        return LegacyGitHubIdentity(
            outcome=LegacyGitHubIdentityOutcome.CONFLICTING,
            considered=considered_names,
            correction=(
                "GitHub access is declared by more than one credential ("
                + ", ".join(declared)
                + "); keep only the one GitHub work should use and restart "
                "MoonMind."
            ),
        )
    if effective is not None:
        return LegacyGitHubIdentity(
            outcome=LegacyGitHubIdentityOutcome.PROVEN,
            credentialRef=effective[1],
            sourceName=effective[0],
            considered=considered_names,
        )
    if operator_refs:
        return LegacyGitHubIdentity(
            outcome=LegacyGitHubIdentityOutcome.PROVEN,
            credentialRef=operator_refs[0],
            sourceName=OPERATOR_GITHUB_TOKEN_SETTING,
            considered=considered_names,
        )
    return LegacyGitHubIdentity(
        outcome=LegacyGitHubIdentityOutcome.ABSENT,
        considered=considered_names,
        correction=(
            "No GitHub credential is configured. Set GITHUB_TOKEN (or "
            "GITHUB_TOKEN_SECRET_REF) in .env, or choose a managed secret for "
            "Settings integrations.github.token_ref, then restart MoonMind."
        ),
    )
