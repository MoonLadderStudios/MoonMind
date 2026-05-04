from __future__ import annotations

import pytest

from moonmind.auth.github_credentials import (
    GitHubCredentialSource,
    resolve_github_credential,
    resolve_github_credential_sync,
)

pytestmark = pytest.mark.asyncio


async def test_resolver_precedence_and_source_diagnostics(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-env-token")
    monkeypatch.setenv("GH_TOKEN", "gh-env-token")
    monkeypatch.setenv("WORKFLOW_GITHUB_TOKEN", "workflow-env-token")
    monkeypatch.setenv("GITHUB_TOKEN_SECRET_REF", "env://SECRET_TOKEN")
    monkeypatch.setenv("SECRET_TOKEN", "secret-ref-token")
    monkeypatch.setenv("MOONMIND_GITHUB_TOKEN_REF", "env://SETTINGS_TOKEN")
    monkeypatch.setenv("SETTINGS_TOKEN", "settings-ref-token")

    resolved = await resolve_github_credential("explicit-token", repo="owner/repo")

    assert resolved.token == "explicit-token"
    assert resolved.source == GitHubCredentialSource.EXPLICIT
    assert resolved.source_name == "explicit"
    assert resolved.repo == "owner/repo"
    assert "explicit-token" not in resolved.safe_summary


@pytest.mark.parametrize(
    ("env_name", "expected_source"),
    [
        ("GITHUB_TOKEN", GitHubCredentialSource.DIRECT_ENV),
        ("GH_TOKEN", GitHubCredentialSource.DIRECT_ENV),
        ("WORKFLOW_GITHUB_TOKEN", GitHubCredentialSource.DIRECT_ENV),
    ],
)
async def test_direct_env_sources_are_supported(monkeypatch, env_name, expected_source):
    for key in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "WORKFLOW_GITHUB_TOKEN",
        "GITHUB_TOKEN_SECRET_REF",
        "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
        "MOONMIND_GITHUB_TOKEN_REF",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(env_name, f"{env_name.lower()}-token")

    resolved = await resolve_github_credential()

    assert resolved.token == f"{env_name.lower()}-token"
    assert resolved.source == expected_source
    assert resolved.source_name == env_name


async def test_settings_token_ref_is_resolved_after_secret_ref_sources(monkeypatch):
    for key in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "WORKFLOW_GITHUB_TOKEN",
        "GITHUB_TOKEN_SECRET_REF",
        "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MOONMIND_GITHUB_TOKEN_REF", "env://SETTINGS_TOKEN")
    monkeypatch.setenv("SETTINGS_TOKEN", "settings-ref-token")

    resolved = await resolve_github_credential()

    assert resolved.token == "settings-ref-token"
    assert resolved.source == GitHubCredentialSource.SETTINGS_TOKEN_REF
    assert resolved.source_name == "MOONMIND_GITHUB_TOKEN_REF"
    assert "settings-ref-token" not in resolved.safe_summary


async def test_missing_credential_returns_redaction_safe_diagnostic(monkeypatch):
    for key in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "WORKFLOW_GITHUB_TOKEN",
        "GITHUB_TOKEN_SECRET_REF",
        "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
        "MOONMIND_GITHUB_TOKEN_REF",
    ):
        monkeypatch.delenv(key, raising=False)

    resolved = await resolve_github_credential(repo="owner/repo")

    assert resolved.token == ""
    assert resolved.source == GitHubCredentialSource.MISSING
    assert "owner/repo" in resolved.safe_summary
    assert "token" in resolved.safe_summary.lower()


async def test_sync_resolver_uses_all_direct_env_sources_inside_running_loop(monkeypatch):
    for key in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "WORKFLOW_GITHUB_TOKEN",
        "GITHUB_TOKEN_SECRET_REF",
        "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
        "MOONMIND_GITHUB_TOKEN_REF",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GH_TOKEN", "gh-env-token")

    resolved = resolve_github_credential_sync(repo="owner/repo")

    assert resolved.token == "gh-env-token"
    assert resolved.source == GitHubCredentialSource.DIRECT_ENV
    assert resolved.source_name == "GH_TOKEN"


async def test_sync_resolver_resolves_secret_refs_inside_running_loop(monkeypatch):
    for key in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "WORKFLOW_GITHUB_TOKEN",
        "GITHUB_TOKEN_SECRET_REF",
        "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
        "MOONMIND_GITHUB_TOKEN_REF",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WORKFLOW_GITHUB_TOKEN_SECRET_REF", "db://github-token")

    async def _fake_secret_ref(ref: str) -> str:
        assert ref == "db://github-token"
        return "secret-ref-token"

    monkeypatch.setattr(
        "moonmind.auth.github_credentials._resolve_secret_ref",
        _fake_secret_ref,
    )

    resolved = resolve_github_credential_sync(repo="owner/repo")

    assert resolved.token == "secret-ref-token"
    assert resolved.source == GitHubCredentialSource.SECRET_REF_ENV
    assert resolved.source_name == "WORKFLOW_GITHUB_TOKEN_SECRET_REF"
