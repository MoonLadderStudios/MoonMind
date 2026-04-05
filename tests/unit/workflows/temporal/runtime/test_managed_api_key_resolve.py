"""Tests for MANAGED_API_KEY_REF resolution."""

from __future__ import annotations

from types import SimpleNamespace

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
    def __init__(self, session: object) -> None:
        self._session = session

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _FakeScalarResult:
    def __init__(self, secret: object | None) -> None:
        self._secret = secret

    def scalar_one_or_none(self) -> object | None:
        return self._secret


class _FakeLookupSession:
    def __init__(
        self,
        *,
        values: dict[str, str | None] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._values = values or {}
        self._errors = errors or {}
        self.seen_slugs: list[str] = []

    async def execute(self, query) -> _FakeScalarResult:
        slug = str(query.compile().params["slug_1"])
        self.seen_slugs.append(slug)
        if slug in self._errors:
            raise self._errors[slug]
        value = self._values.get(slug)
        secret = None if value is None else SimpleNamespace(ciphertext=value)
        return _FakeScalarResult(secret)


class _FakeSessionMaker:
    def __init__(self, session: _FakeLookupSession) -> None:
        self._session = session
        self.calls = 0

    def __call__(self) -> _FakeAsyncSessionCtx:
        self.calls += 1
        return _FakeAsyncSessionCtx(self._session)


async def test_resolve_managed_github_token_from_store_first_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeLookupSession(values={"GITHUB_TOKEN": "ghp-from-managed-store"})
    session_maker = _FakeSessionMaker(session)
    monkeypatch.setattr("api_service.db.base.async_session_maker", session_maker)

    out = await resolve_managed_github_token_from_store()
    assert out == "ghp-from-managed-store"
    assert session_maker.calls == 1
    assert session.seen_slugs == ["GITHUB_TOKEN"]


async def test_resolve_managed_github_token_from_store_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeLookupSession()
    session_maker = _FakeSessionMaker(session)
    monkeypatch.setattr("api_service.db.base.async_session_maker", session_maker)

    assert await resolve_managed_github_token_from_store() is None
    assert session_maker.calls == 1
    assert session.seen_slugs == ["GITHUB_TOKEN", "GITHUB_PAT"]


async def test_resolve_managed_github_token_from_store_stops_after_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeLookupSession(errors={"GITHUB_TOKEN": RuntimeError("db down")})
    session_maker = _FakeSessionMaker(session)
    monkeypatch.setattr("api_service.db.base.async_session_maker", session_maker)

    with pytest.raises(RuntimeError, match="db down"):
        await resolve_managed_github_token_from_store()

    assert session_maker.calls == 1
    assert session.seen_slugs == ["GITHUB_TOKEN"]
