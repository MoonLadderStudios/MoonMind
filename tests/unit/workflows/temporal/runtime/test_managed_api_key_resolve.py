"""Tests for MANAGED_API_KEY_REF resolution."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
    resolve_managed_api_key_reference,
    resolve_managed_github_token_from_store,
)

pytestmark = pytest.mark.asyncio


async def test_resolve_from_worker_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TEST_SECRET_KEY", "abc123")
    out = await resolve_managed_api_key_reference("MY_TEST_SECRET_KEY")
    assert out == "abc123"


async def test_resolve_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        await resolve_managed_api_key_reference("  ")


async def test_resolve_unknown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOT_SET_XYZ", raising=False)
    with pytest.raises(ValueError, match="Unable to resolve"):
        await resolve_managed_api_key_reference("NOT_SET_XYZ")


class _FakeAsyncSessionCtx:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> bool:
        return False


async def test_resolve_managed_github_token_from_store_first_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker",
        lambda: _FakeAsyncSessionCtx(),
    )

    async def fake_get_secret(cls, db, slug: str) -> str | None:
        if slug == "GITHUB_TOKEN":
            return "ghp-from-managed-store"
        return None

    monkeypatch.setattr(
        "api_service.services.secrets.SecretsService.get_secret",
        classmethod(fake_get_secret),
    )

    out = await resolve_managed_github_token_from_store()
    assert out == "ghp-from-managed-store"


async def test_resolve_managed_github_token_from_store_falls_back_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker",
        lambda: _FakeAsyncSessionCtx(),
    )

    async def fake_get_secret(cls, db, slug: str) -> str | None:
        if slug == "GH_TOKEN":
            return "ghp-fallback-slug"
        return None

    monkeypatch.setattr(
        "api_service.services.secrets.SecretsService.get_secret",
        classmethod(fake_get_secret),
    )

    out = await resolve_managed_github_token_from_store()
    assert out == "ghp-fallback-slug"


async def test_resolve_managed_github_token_from_store_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker",
        lambda: _FakeAsyncSessionCtx(),
    )

    async def fake_get_secret(cls, db, slug: str) -> str | None:
        return None

    monkeypatch.setattr(
        "api_service.services.secrets.SecretsService.get_secret",
        classmethod(fake_get_secret),
    )

    assert await resolve_managed_github_token_from_store() is None
