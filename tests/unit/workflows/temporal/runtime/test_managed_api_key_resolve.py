"""Tests for MANAGED_API_KEY_REF resolution."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
    BrokenSecretRef,
    SecretRefLaunchBlockedError,
    assert_managed_secret_refs_active_for_launch,
    inspect_managed_secret_refs_for_launch,
    resolve_github_token_for_launch,
    resolve_managed_api_key_reference,
    resolve_managed_github_token_from_store,
    shape_launch_github_auth_environment,
)

pytestmark = pytest.mark.asyncio

async def test_resolve_from_worker_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TEST_SECRET_KEY", "abc123")
    out = await resolve_managed_api_key_reference("MY_TEST_SECRET_KEY")
    assert out == "abc123"

async def test_resolve_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        await resolve_managed_api_key_reference("  ")

async def test_resolve_rejects_structured_secret_ref_values() -> None:
    with pytest.raises(
        ValueError,
        match="MANAGED_API_KEY_REF must be a string secret reference",
    ):
        await resolve_managed_api_key_reference({"ref": "env://MY_TEST_SECRET_KEY"})

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

async def test_resolve_github_token_for_launch_prefers_existing_environment_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings as app_settings

    monkeypatch.setattr(app_settings.github, "github_token_secret_ref", "db://unused")

    async def _unexpected_resolve(_secret_ref: str) -> str:
        raise AssertionError("secret ref lookup should not run")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_managed_api_key_reference",
        _unexpected_resolve,
    )

    out = await resolve_github_token_for_launch({"GITHUB_TOKEN": "ghp-inline-token"})

    assert out == "ghp-inline-token"

async def test_resolve_github_token_for_launch_uses_canonical_workflow_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings as app_settings

    monkeypatch.setattr(app_settings.github, "github_token_secret_ref", None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("WORKFLOW_GITHUB_TOKEN", "workflow-token")

    out = await resolve_github_token_for_launch({})

    assert out == "workflow-token"

async def test_shape_launch_github_auth_environment_uses_ambient_token_before_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings as app_settings

    monkeypatch.setattr(app_settings.github, "github_token_secret_ref", None)

    async def _unexpected_store() -> str:
        raise AssertionError("managed store lookup should not run")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_managed_github_token_from_store",
        _unexpected_store,
    )

    shaped = await shape_launch_github_auth_environment(
        {"PATH": "/usr/bin"},
        ambient_github_token="ghp-ambient-token",
    )

    assert shaped["PATH"] == "/usr/bin"
    assert shaped["GITHUB_TOKEN"] == "ghp-ambient-token"
    assert shaped["GIT_TERMINAL_PROMPT"] == "0"

async def test_resolve_github_token_for_launch_propagates_cancellation_from_secret_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings as app_settings

    async def _fake_resolve(_secret_name: str) -> str:
        raise asyncio.CancelledError()

    monkeypatch.setattr(app_settings.github, "github_token_secret_ref", "db://github-pat")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_managed_api_key_reference",
        _fake_resolve,
    )

    with pytest.raises(asyncio.CancelledError):
        await resolve_github_token_for_launch({})

async def test_resolve_github_token_for_launch_propagates_cancellation_from_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings as app_settings

    async def _fake_store_token() -> str:
        raise asyncio.CancelledError()

    monkeypatch.setattr(app_settings.github, "github_token_secret_ref", None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_managed_github_token_from_store",
        _fake_store_token,
    )

    with pytest.raises(asyncio.CancelledError):
        await resolve_github_token_for_launch({})


class _FakeStatusResult:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self._rows = list(rows)

    def all(self) -> list[tuple[str, str]]:
        return list(self._rows)


class _FakeStatusSession:
    def __init__(self, statuses_by_slug: dict[str, str | None]) -> None:
        self._statuses = dict(statuses_by_slug)
        self.queried_slugs: list[tuple[str, ...]] = []

    async def execute(self, query) -> _FakeStatusResult:
        compiled = query.compile()
        params = compiled.params
        slugs_collected: list[str] = []
        for key, value in params.items():
            if not key.startswith("slug_"):
                continue
            if isinstance(value, str):
                slugs_collected.append(value)
            elif isinstance(value, (list, tuple, set)):
                slugs_collected.extend(
                    item for item in value if isinstance(item, str)
                )
        slugs = tuple(sorted(slugs_collected))
        self.queried_slugs.append(slugs)
        rows = [
            (slug, status)
            for slug, status in self._statuses.items()
            if slug in slugs and status is not None
        ]
        return _FakeStatusResult(rows)


class _FakeStatusSessionMaker:
    def __init__(self, session: _FakeStatusSession) -> None:
        self._session = session
        self.calls = 0

    def __call__(self) -> _FakeAsyncSessionCtx:
        self.calls += 1
        return _FakeAsyncSessionCtx(self._session)


async def test_inspect_managed_secret_refs_returns_empty_for_only_env_refs() -> None:
    issues = await inspect_managed_secret_refs_for_launch(
        ["env://OPENAI_API_KEY", "vault://kv/data/foo#bar"]
    )
    assert issues == []


async def test_inspect_managed_secret_refs_flags_missing_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeStatusSession(statuses_by_slug={})
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker",
        _FakeStatusSessionMaker(session),
    )

    issues = await inspect_managed_secret_refs_for_launch(
        ["db://missing-slug"]
    )

    assert len(issues) == 1
    issue = issues[0]
    assert isinstance(issue, BrokenSecretRef)
    assert issue.secret_ref == "db://missing-slug"
    assert issue.slug == "missing-slug"
    assert issue.status == "missing"
    assert issue.diagnostic_code == "broken_reference_missing"


async def test_inspect_managed_secret_refs_flags_disabled_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeStatusSession(statuses_by_slug={"github-pat-main": "disabled"})
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker",
        _FakeStatusSessionMaker(session),
    )

    issues = await inspect_managed_secret_refs_for_launch(
        ["db://github-pat-main"]
    )

    assert len(issues) == 1
    assert issues[0].status == "disabled"
    assert issues[0].diagnostic_code == "broken_reference_disabled"


async def test_inspect_managed_secret_refs_ignores_active_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeStatusSession(statuses_by_slug={"ok-secret": "active"})
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker",
        _FakeStatusSessionMaker(session),
    )

    issues = await inspect_managed_secret_refs_for_launch(
        ["db://ok-secret", "env://OPENAI_API_KEY"]
    )

    assert issues == []


async def test_assert_managed_secret_refs_raises_launch_blocked_for_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeStatusSession(
        statuses_by_slug={
            "claude-key": "disabled",
            "openai-key": "active",
        }
    )
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker",
        _FakeStatusSessionMaker(session),
    )

    with pytest.raises(SecretRefLaunchBlockedError) as exc_info:
        await assert_managed_secret_refs_active_for_launch(
            ["db://claude-key", "db://openai-key"]
        )

    error = exc_info.value
    assert len(error.broken) == 1
    assert error.broken[0].secret_ref == "db://claude-key"
    assert error.broken[0].status == "disabled"
    assert "claude-key" in str(error)
    assert "disabled" in str(error)


async def test_assert_managed_secret_refs_noop_for_all_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeStatusSession(statuses_by_slug={"ok-key": "active"})
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker",
        _FakeStatusSessionMaker(session),
    )

    await assert_managed_secret_refs_active_for_launch(
        ["db://ok-key", "env://ALSO_OK"]
    )
