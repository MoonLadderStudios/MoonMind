"""Tests for MANAGED_API_KEY_REF resolution."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.runtime import (
    managed_api_key_resolve as managed_api_key_resolve_module,
)
from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
    BrokenSecretRef,
    SecretRefLaunchBlockedError,
    assert_managed_secret_refs_active_for_launch,
    inspect_managed_secret_refs_for_launch,
    resolve_ghcr_pull_credentials_for_launch,
    resolve_github_token_for_launch,
    resolve_managed_api_key_reference,
)

pytestmark = pytest.mark.asyncio

_real_loader = managed_api_key_resolve_module.load_repository_connection_for_launch
_real_slug_loader = getattr(
    managed_api_key_resolve_module, "load_active_managed_github_secret_slug", None
)


@pytest.fixture(autouse=True)
def _no_recorded_default_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests have no database: record no connection or GitHub secret."""

    async def _absent(_connection_ref: str, **_kwargs: object):
        return None

    async def _no_secret() -> None:
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "load_repository_connection_for_launch",
        _absent,
    )
    monkeypatch.setattr(
        managed_api_key_resolve_module,
        "load_active_managed_github_secret_slug",
        _no_secret,
        raising=False,
    )

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

def _clear_ghcr_deployment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "GHCR_PULL_USER",
        "GHCR_PULL_TOKEN",
        "MOONMIND_GHCR_PULL_USER_SECRET_REF",
        "MOONMIND_GHCR_PULL_TOKEN_SECRET_REF",
        "WORKFLOW_GHCR_PULL_USER_SECRET_REF",
        "WORKFLOW_GHCR_PULL_TOKEN_SECRET_REF",
    ):
        monkeypatch.delenv(var, raising=False)


def _stub_empty_ghcr_store(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeLookupSession()
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker", _FakeSessionMaker(session)
    )


def _login(value):
    async def _resolve(_token):
        return value

    return _resolve


def _forbid_source_token_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _unexpected_github_token(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("source GitHub token resolution must not run")

    async def _unexpected_connection(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("repository connection lookup must not run")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_github_token_for_launch",
        _unexpected_github_token,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "load_repository_connection_for_launch",
        _unexpected_connection,
    )


async def test_resolve_ghcr_pull_credentials_requires_complete_env_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_ghcr_deployment_env(monkeypatch)
    monkeypatch.setenv("GHCR_PULL_USER", "pull-user")

    with pytest.raises(ValueError, match="requires both user and token"):
        await resolve_ghcr_pull_credentials_for_launch()

    _clear_ghcr_deployment_env(monkeypatch)
    monkeypatch.setenv("GHCR_PULL_TOKEN", "pull-token")

    with pytest.raises(ValueError, match="requires both user and token"):
        await resolve_ghcr_pull_credentials_for_launch()

async def test_resolve_ghcr_pull_credentials_uses_complete_env_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_ghcr_deployment_env(monkeypatch)
    monkeypatch.setenv("GHCR_PULL_USER", " pull-user ")
    monkeypatch.setenv("GHCR_PULL_TOKEN", " pull-token ")

    assert await resolve_ghcr_pull_credentials_for_launch() == (
        "pull-user",
        "pull-token",
    )

async def test_resolve_ghcr_pull_credentials_derives_from_the_github_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reverses #4012's removal by operator choice: with nothing registry-
    # specific configured, the deployment's own GitHub credential authenticates
    # ghcr.io rather than downgrading to an anonymous pull a private package
    # will deny.
    _clear_ghcr_deployment_env(monkeypatch)
    _stub_empty_ghcr_store(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "_resolve_github_login_for_token",
        _login("octocat"),
    )

    assert await resolve_ghcr_pull_credentials_for_launch() == (
        "octocat",
        "github-token",
    )


async def test_github_derivation_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An operator who wants #4012's strict separation keeps it with one setting.
    _clear_ghcr_deployment_env(monkeypatch)
    _stub_empty_ghcr_store(monkeypatch)
    _forbid_source_token_resolution(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("MOONMIND_GHCR_PULL_FROM_GITHUB_TOKEN_ENABLED", "false")

    assert await resolve_ghcr_pull_credentials_for_launch() is None


async def test_github_derivation_survives_a_failed_username_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # GHCR authenticates by token and ignores the username, so a /user outage
    # must not sink the pull.
    _clear_ghcr_deployment_env(monkeypatch)
    _stub_empty_ghcr_store(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "_resolve_github_login_for_token",
        _login(None),
    )

    user, token = await resolve_ghcr_pull_credentials_for_launch()
    assert token == "github-token"
    assert user


async def test_resolve_ghcr_pull_credentials_ignores_launch_environment_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Agent-authored launch fields are not accepted as registry authentication:
    # the resolver takes no launch mapping, so smuggled GHCR_PULL_* values
    # cannot be supplied. Deployment env empty => anonymous None.
    import inspect

    _clear_ghcr_deployment_env(monkeypatch)
    _stub_empty_ghcr_store(monkeypatch)
    # Derivation is a separate concern; this asserts only that the resolver
    # accepts no agent-supplied mapping.
    monkeypatch.setenv("MOONMIND_GHCR_PULL_FROM_GITHUB_TOKEN_ENABLED", "false")
    _forbid_source_token_resolution(monkeypatch)

    assert list(
        inspect.signature(resolve_ghcr_pull_credentials_for_launch).parameters
    ) == []
    assert await resolve_ghcr_pull_credentials_for_launch() is None

async def test_resolve_ghcr_pull_credentials_requires_complete_pair_without_source_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_ghcr_deployment_env(monkeypatch)
    monkeypatch.setenv("GHCR_PULL_USER", "pull-user")
    _forbid_source_token_resolution(monkeypatch)

    with pytest.raises(ValueError, match="requires both user and token"):
        await resolve_ghcr_pull_credentials_for_launch()

async def test_resolve_ghcr_pull_credentials_requires_complete_managed_secret_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_ghcr_deployment_env(monkeypatch)
    session = _FakeLookupSession(values={"GHCR_PULL_USER": "pull-user"})
    session_maker = _FakeSessionMaker(session)
    monkeypatch.setattr("api_service.db.base.async_session_maker", session_maker)
    _forbid_source_token_resolution(monkeypatch)

    with pytest.raises(ValueError, match="requires both user and token managed secrets"):
        await resolve_ghcr_pull_credentials_for_launch()

async def test_resolve_ghcr_pull_credentials_store_outage_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A managed-store outage is a registry-boundary failure, never an invitation
    # to try source auth, another identity, or an anonymous downgrade.
    _clear_ghcr_deployment_env(monkeypatch)
    session = _FakeLookupSession(errors={"GHCR_PULL_USER": RuntimeError("db down")})
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker", _FakeSessionMaker(session)
    )
    _forbid_source_token_resolution(monkeypatch)

    with pytest.raises(ValueError, match="store is unavailable"):
        await resolve_ghcr_pull_credentials_for_launch()

async def test_resolve_ghcr_pull_credentials_uses_secret_ref_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_ghcr_deployment_env(monkeypatch)
    monkeypatch.setenv("MY_GHCR_PULL_USER", " ref-user ")
    monkeypatch.setenv("MY_GHCR_PULL_TOKEN", " ref-token ")
    monkeypatch.setenv("MOONMIND_GHCR_PULL_USER_SECRET_REF", "MY_GHCR_PULL_USER")
    monkeypatch.setenv("MOONMIND_GHCR_PULL_TOKEN_SECRET_REF", "MY_GHCR_PULL_TOKEN")

    assert await resolve_ghcr_pull_credentials_for_launch() == (
        "ref-user",
        "ref-token",
    )

async def test_resolve_ghcr_pull_credentials_requires_complete_secret_ref_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_ghcr_deployment_env(monkeypatch)
    monkeypatch.setenv("MOONMIND_GHCR_PULL_USER_SECRET_REF", "MY_GHCR_PULL_USER")

    with pytest.raises(ValueError, match="requires both user and token secret refs"):
        await resolve_ghcr_pull_credentials_for_launch()

async def test_resolve_ghcr_pull_credentials_public_anonymous_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With no registry configuration and no deployment GitHub token, there is
    # nothing to present: the public-anonymous path still exists.
    _clear_ghcr_deployment_env(monkeypatch)
    _stub_empty_ghcr_store(monkeypatch)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    assert await resolve_ghcr_pull_credentials_for_launch() is None

async def test_ghcr_pull_credentials_bound_to_ghcr_registry() -> None:
    from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
        GHCR_REGISTRY,
    )

    assert GHCR_REGISTRY == "ghcr.io"

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


# --- MoonLadderStudios/MoonMind#4023: the launch boundary uses the recorded
# default repository connection and never searches other credentials. ---


def _default_connection(credential: dict[str, object]):
    from moonmind.workflows.executions.repository_contract import (
        DEFAULT_GIT_CONNECTION_REF,
        RepositoryConnection,
    )

    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": DEFAULT_GIT_CONNECTION_REF,
            "provider": "git",
            "displayName": "Default GitHub connection",
            "endpointRef": "https://github.com",
            "allowedOperations": ["read", "write", "branch_write", "review_request"],
            "clientPolicy": {
                "pinnedVersion": "2.46.0",
                "toolBundleRef": "repository-client:git-system",
                "executableSha256": "sha256:git",
            },
            "credential": credential,
            "ownership": {"ownerRef": "system:deployment", "scopeType": "system"},
            "hostingService": "github",
        }
    )


def _record_default_connection(monkeypatch: pytest.MonkeyPatch, connection) -> list[str]:
    loaded: list[str] = []

    async def _load(connection_ref: str):
        loaded.append(connection_ref)
        return connection

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "load_repository_connection_for_launch",
        _load,
    )
    return loaded


def _clear_deployment_github_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from moonmind.config.settings import settings as app_settings

    monkeypatch.setattr(app_settings.github, "github_token_secret_ref", None)
    for name in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "WORKFLOW_GITHUB_TOKEN",
        "GITHUB_TOKEN_SECRET_REF",
        "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
        "MOONMIND_GITHUB_TOKEN_REF",
    ):
        monkeypatch.delenv(name, raising=False)


async def test_recorded_default_connection_wins_over_ambient_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_deployment_github_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-A")
    monkeypatch.setenv("SELECTED_GITHUB_PAT", "selected-token-B")
    loaded = _record_default_connection(
        monkeypatch,
        _default_connection(
            {
                "source": "secret_ref",
                "credentialRef": {"provider": "env", "key": "SELECTED_GITHUB_PAT"},
            }
        ),
    )

    assert await resolve_github_token_for_launch({}) == "selected-token-B"
    assert loaded == ["repository-connection:git-default"]


async def test_failed_recorded_connection_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_deployment_github_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-A")
    monkeypatch.delenv("SELECTED_GITHUB_PAT", raising=False)
    _record_default_connection(
        monkeypatch,
        _default_connection(
            {
                "source": "secret_ref",
                "credentialRef": {"provider": "env", "key": "SELECTED_GITHUB_PAT"},
            }
        ),
    )

    assert await resolve_github_token_for_launch({}) is None


async def test_unreadable_default_connection_is_not_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_deployment_github_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-A")

    async def _unreadable(_connection_ref: str):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "load_repository_connection_for_launch",
        _unreadable,
    )

    assert await resolve_github_token_for_launch({}) is None


async def test_failed_configured_reference_does_not_search_other_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings as app_settings

    _clear_deployment_github_env(monkeypatch)
    _record_default_connection(monkeypatch, None)
    monkeypatch.setenv("GITHUB_TOKEN_SECRET_REF", "db://github-pat-broken")
    monkeypatch.setattr(
        app_settings.github, "github_token_secret_ref", "db://github-pat-settings"
    )
    attempted: list[str] = []

    async def _resolve(ref: str, **_kwargs: object) -> str:
        attempted.append(ref)
        raise ValueError("secret store unavailable")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "resolve_managed_api_key_reference",
        _resolve,
    )

    assert await resolve_github_token_for_launch({}) is None
    assert attempted == ["db://github-pat-broken"]


async def test_without_recorded_connection_the_declared_deployment_token_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_deployment_github_env(monkeypatch)
    _record_default_connection(monkeypatch, None)
    monkeypatch.setenv("GITHUB_TOKEN", "declared-token")

    assert await resolve_github_token_for_launch({}) == "declared-token"


async def test_no_recorded_connection_and_no_declaration_yields_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_deployment_github_env(monkeypatch)
    _record_default_connection(monkeypatch, None)

    assert await resolve_github_token_for_launch({}) is None


async def test_default_connection_descriptor_uses_recorded_connection_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.schemas.managed_session_models import (
        ManagedGitHubCredentialDescriptor,
    )

    _clear_deployment_github_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-A")
    _record_default_connection(
        monkeypatch,
        _default_connection(
            {
                "source": "secret_ref",
                "credentialRef": {"provider": "env", "key": "MISSING_SELECTED_PAT"},
            }
        ),
    )
    monkeypatch.delenv("MISSING_SELECTED_PAT", raising=False)
    descriptor = ManagedGitHubCredentialDescriptor(
        source="managed_secret", required=True
    )

    with pytest.raises(ValueError, match="repository-connection:git-default"):
        await resolve_github_token_for_launch({}, github_credential=descriptor)


async def test_repository_session_descriptor_selects_the_default_connection() -> None:
    from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
        build_github_credential_descriptor_for_launch,
    )

    repository = build_github_credential_descriptor_for_launch(
        {}, repository_session=True
    )
    scratch = build_github_credential_descriptor_for_launch(
        {}, repository_session=False
    )
    explicit = build_github_credential_descriptor_for_launch(
        {"GITHUB_TOKEN": "explicit-launch-token"}, repository_session=True
    )

    assert repository is not None and repository.source == "managed_secret"
    assert scratch is None
    assert explicit is not None and explicit.source == "environment"


async def test_default_connection_loader_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_deployment_github_env(monkeypatch)

    async def _cancelled(_connection_ref: str):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "load_repository_connection_for_launch",
        _cancelled,
    )

    with pytest.raises(asyncio.CancelledError):
        await resolve_github_token_for_launch({})


async def test_launch_connection_loader_distinguishes_deleted_from_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A deleted recorded connection is not absence (#4023)."""

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api_service.db.models import (
        Base,
        RepositoryConnectionAssignment,
        RepositoryConnectionRecord,
    )
    from moonmind.workflows.executions.repository_contract import (
        RepositoryRouteError,
    )
    from moonmind.workflows.temporal.runtime import managed_api_key_resolve

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/connections.db")
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[
                RepositoryConnectionRecord.__table__,
                RepositoryConnectionAssignment.__table__,
            ],
        )
        for connection_id, tombstone in (
            ("repository-connection:active", False),
            ("repository-connection:deleted", True),
        ):
            await connection.execute(
                sa.insert(RepositoryConnectionRecord.__table__).values(
                    connection_id=connection_id,
                    display_name="Recorded",
                    provider="git",
                    hosting_service="github",
                    endpoint_normalized="https://github.com",
                    endpoint_ref="https://github.com",
                    allowed_operations=["read"],
                    client_policy={
                        "pinnedVersion": "2.46.0",
                        "toolBundleRef": "repository-client:git-system",
                        "executableSha256": "sha256:git",
                    },
                    credential_config={
                        "source": "secret_ref",
                        "credentialRef": {"provider": "env", "key": "PAT", "extra": {}},
                    },
                    lifecycle="deleted" if tombstone else "active",
                    owner_ref="system:deployment",
                    scope_type="system",
                    allowed_principal_refs=[],
                    tombstone=tombstone,
                )
            )
        await connection.execute(
            sa.insert(RepositoryConnectionAssignment.__table__).values(
                id=uuid.uuid4(),
                connection_id="repository-connection:active",
                endpoint_normalized="https://github.com",
                repo_key="id:1",
                provider_repo_id="1",
                display_name="MoonLadderStudios/MoonMind",
                operations=["read"],
                revision=1,
                verified=True,
            )
        )
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker", async_sessionmaker(engine)
    )
    monkeypatch.setattr(
        managed_api_key_resolve,
        "load_repository_connection_for_launch",
        _real_loader,
    )

    try:
        active = await _real_loader(
            "repository-connection:active", repository="MoonLadderStudios/MoonMind"
        )
        absent = await _real_loader("repository-connection:absent")
        with pytest.raises(RepositoryRouteError, match="deleted"):
            await _real_loader("repository-connection:deleted")
    finally:
        await engine.dispose()

    assert active is not None and active.credential.source == "secret_ref"
    assert absent is None


async def test_deleted_default_connection_does_not_fall_back_to_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.workflows.executions.repository_contract import (
        REPOSITORY_DENIED,
        RepositoryRouteError,
    )

    _clear_deployment_github_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "declared-token")

    async def _deleted(connection_ref: str):
        raise RepositoryRouteError(REPOSITORY_DENIED, f"{connection_ref} was deleted")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "load_repository_connection_for_launch",
        _deleted,
    )

    assert await resolve_github_token_for_launch({}) is None
    resolved = await managed_api_key_resolve_module.resolve_default_github_connection_credential()
    assert "was deleted" in resolved.safe_summary
    assert "select a recorded connection" in resolved.safe_summary
    assert "Source Control" not in resolved.safe_summary


# --- The unrecorded default follows the precedence migration 391 records, and
# a recorded connection authorizes only the repositories assigned to it. ---


def _read_recorded_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        managed_api_key_resolve_module,
        "load_repository_connection_for_launch",
        _real_loader,
    )
    monkeypatch.setattr(
        managed_api_key_resolve_module,
        "load_active_managed_github_secret_slug",
        _real_slug_loader,
    )


async def test_github_secret_saved_after_the_migration_selects_the_unrecorded_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A GITHUB_PAT saved in Settings after migration 391 still reaches launches."""

    from moonmind.workflows.executions.repository_contract import (
        DEFAULT_GIT_CONNECTION_REF,
    )
    from tests.helpers.repository_connections import record_repository_connections

    _clear_deployment_github_env(monkeypatch)
    _read_recorded_store(monkeypatch)
    engine = await record_repository_connections(
        monkeypatch, tmp_path, managed_secrets={"GITHUB_PAT": "settings-pat"}
    )
    try:
        token = await resolve_github_token_for_launch({})
        selected = await managed_api_key_resolve_module.resolve_selected_github_credential_for_launch(
            DEFAULT_GIT_CONNECTION_REF, repo="MoonLadderStudios/MoonMind"
        )
    finally:
        await engine.dispose()

    assert token == "settings-pat"
    assert selected.token == "settings-pat"


async def test_declared_deployment_token_outranks_a_saved_github_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from tests.helpers.repository_connections import record_repository_connections

    _clear_deployment_github_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "declared-token")
    _read_recorded_store(monkeypatch)
    engine = await record_repository_connections(
        monkeypatch, tmp_path, managed_secrets={"GITHUB_TOKEN": "settings-token"}
    )
    try:
        token = await resolve_github_token_for_launch({})
    finally:
        await engine.dispose()

    assert token == "declared-token"


async def test_failed_configured_source_does_not_reach_the_saved_github_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from tests.helpers.repository_connections import record_repository_connections

    _clear_deployment_github_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN_SECRET_REF", "env://UNSET_DEPLOYMENT_PAT")
    monkeypatch.delenv("UNSET_DEPLOYMENT_PAT", raising=False)
    _read_recorded_store(monkeypatch)
    engine = await record_repository_connections(
        monkeypatch, tmp_path, managed_secrets={"GITHUB_TOKEN": "settings-token"}
    )
    try:
        resolved = await managed_api_key_resolve_module.resolve_default_github_connection_credential()
    finally:
        await engine.dispose()

    assert not resolved.token
    assert resolved.source.value == "unresolvable"
    assert "GITHUB_TOKEN_SECRET_REF" in resolved.safe_summary


async def test_unreadable_saved_github_secrets_are_not_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.schemas.managed_session_models import (
        ManagedGitHubCredentialDescriptor,
    )

    _clear_deployment_github_env(monkeypatch)

    async def _unreadable() -> str | None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        managed_api_key_resolve_module,
        "load_active_managed_github_secret_slug",
        _unreadable,
    )

    resolved = await managed_api_key_resolve_module.resolve_default_github_connection_credential()

    assert resolved.source.value == "unresolvable"
    assert resolved.retryable is True
    with pytest.raises(ValueError, match="could not be read"):
        await resolve_github_token_for_launch(
            {},
            github_credential=ManagedGitHubCredentialDescriptor(
                source="managed_secret", required=True
            ),
        )


async def test_recorded_connection_is_selected_only_for_its_assigned_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from moonmind.workflows.executions.repository_contract import (
        RepositoryContractError,
    )
    from tests.helpers.repository_connections import (
        github_pat_connection,
        github_repository_assignment,
        record_repository_connections,
    )

    _read_recorded_store(monkeypatch)
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        github_pat_connection("repository-connection:team-b", "TEAM_B_PAT"),
        assignments=[
            github_repository_assignment(
                "repository-connection:team-b",
                "MoonLadderStudios/MoonMind",
                operations=("read",),
            )
        ],
    )
    select = managed_api_key_resolve_module.select_git_connection_for_launch
    try:
        assigned = await select(
            "repository-connection:team-b",
            repository="https://github.com/moonladderstudios/moonmind.git",
        )
        with pytest.raises(
            RepositoryContractError, match="REPOSITORY_CONNECTION_UNAVAILABLE"
        ) as excinfo:
            await select(
                "repository-connection:team-b",
                repository="MoonLadderStudios/unassigned",
            )
    finally:
        await engine.dispose()

    # The assignment's operations bound the launch, not the connection's.
    assert assigned.allowed_operations == ("read",)
    assert "is not assigned to MoonLadderStudios/unassigned" in str(excinfo.value)


async def test_recorded_connection_without_assignments_authorizes_no_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from moonmind.workflows.executions.repository_contract import (
        RepositoryContractError,
    )
    from tests.helpers.repository_connections import (
        github_pat_connection,
        record_repository_connections,
    )

    monkeypatch.setenv("TEAM_B_PAT", "token-for-team-b")
    _read_recorded_store(monkeypatch)
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        github_pat_connection("repository-connection:team-b", "TEAM_B_PAT"),
    )
    try:
        with pytest.raises(
            RepositoryContractError, match="REPOSITORY_CONNECTION_UNAVAILABLE"
        ) as excinfo:
            await managed_api_key_resolve_module.resolve_selected_github_credential_for_launch(
                "repository-connection:team-b", repo="MoonLadderStudios/MoonMind"
            )
    finally:
        await engine.dispose()

    assert "token-for-team-b" not in str(excinfo.value)


async def test_recorded_default_keeps_its_classified_legacy_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The migrated default had no assignments before #4023 and needs none."""

    from sqlalchemy import update

    from api_service.db.models import RepositoryConnectionAuditEvent
    from moonmind.workflows.executions.repository_contract import (
        DEFAULT_GIT_CONNECTION_REF,
    )
    from tests.helpers.repository_connections import (
        github_pat_connection,
        record_repository_connections,
    )

    _read_recorded_store(monkeypatch)
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        github_pat_connection(DEFAULT_GIT_CONNECTION_REF, "DEFAULT_ACCOUNT_PAT"),
    )
    async with engine.begin() as database:
        await database.execute(
            update(RepositoryConnectionAuditEvent)
            .where(RepositoryConnectionAuditEvent.request_id == "test-connection-0")
            .values(request_id="migration:391:legacy-github-credential")
        )
    try:
        selected = await managed_api_key_resolve_module.select_git_connection_for_launch(
            DEFAULT_GIT_CONNECTION_REF, repository="MoonLadderStudios/MoonMind"
        )
    finally:
        await engine.dispose()

    assert selected is not None
    assert selected.allowed_operations == (
        "read",
        "write",
        "branch_write",
        "review_request",
    )


# ---------------------------------------------------------------------------
# Admitted repository access of a recorded run (MoonLadderStudios/MoonMind#4010)
# ---------------------------------------------------------------------------

_PLAN_DIGEST = "a" * 64
_PLAN_BINDING = {
    "planRef": f"omnigent-execution-plan:sha256:{_PLAN_DIGEST}",
    "planDigest": f"sha256:{_PLAN_DIGEST}",
    "planArtifactRef": "artifact:plan",
    "taskInputSnapshotRef": "artifact:task-input",
    "taskInputSnapshotDigest": "sha256:" + "b" * 64,
}


def _repository_binding(connection_ref: str, role: str) -> dict[str, str]:
    return {
        "authorityKind": "repository_connection",
        "connectionRef": connection_ref,
        "repositoryAccessSnapshotRef": "repository-access-snapshot:sha256:" + "c" * 64,
        "materializerRef": "repository-broker@1",
        "repositoryRole": role,
    }


def _recorded_run(monkeypatch, parameters, *, plan_bindings=None, plan_error=None):
    """Answer the canonical-record and frozen-plan reads at their seams."""

    from api_service.db import base as db_base
    from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore

    loaded: list[str] = []

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get(self, _model, workflow_id):
            if workflow_id != "mm:run":
                return None
            return SimpleNamespace(parameters=parameters)

    async def load(self, plan_ref):
        loaded.append(plan_ref)
        if plan_error is not None:
            raise plan_error
        if plan_bindings is None:
            return None
        return SimpleNamespace(
            payload=SimpleNamespace(credentialBindings=dict(plan_bindings))
        )

    monkeypatch.setattr(db_base, "async_session_maker", lambda: _Session())
    monkeypatch.setattr(SessionExecutionPlanStore, "load", load)
    return loaded


async def test_routed_run_acts_with_the_connection_its_plan_admitted(monkeypatch):
    """Routed admission omits connectionRef; the frozen plan binding names it."""

    loaded = _recorded_run(
        monkeypatch,
        {"repository": "acme/repo", "omnigentExecutionPlan": _PLAN_BINDING},
        plan_bindings={
            "model": {"authorityKind": "model", "providerProfileRef": "p"},
            "source": _repository_binding("repository-connection:routed-b", "source_read"),
            "collaboration": _repository_binding(
                "repository-connection:routed-b", "collaboration"
            ),
        },
    )

    access = await managed_api_key_resolve_module.load_admitted_repository_access(
        "mm:run"
    )

    assert access == ("repository-connection:routed-b", False)
    assert loaded == [_PLAN_BINDING["planRef"]]


async def test_routed_run_prefers_its_collaboration_connection(monkeypatch):
    _recorded_run(
        monkeypatch,
        {"repository": "acme/repo", "omnigentExecutionPlan": _PLAN_BINDING},
        plan_bindings={
            "source": _repository_binding("repository-connection:reader", "source_read"),
            "collaboration": _repository_binding(
                "repository-connection:collaborator", "collaboration"
            ),
        },
    )

    access = await managed_api_key_resolve_module.load_admitted_repository_access(
        "mm:run"
    )

    assert access == ("repository-connection:collaborator", False)


async def test_routed_plan_without_repository_bindings_uses_default(monkeypatch):
    """A plan that bound no repository authority admitted only the default."""

    _recorded_run(
        monkeypatch,
        {"repository": "acme/repo", "omnigentExecutionPlan": _PLAN_BINDING},
        plan_bindings={},
    )

    access = await managed_api_key_resolve_module.load_admitted_repository_access(
        "mm:run"
    )

    assert access == ("", False)


@pytest.mark.parametrize("plan_error", [None, RuntimeError("db unavailable")])
async def test_unreadable_routed_plan_is_unavailable_authority(monkeypatch, plan_error):
    from moonmind.workflows.executions.repository_contract import (
        RepositoryContractError,
    )

    _recorded_run(
        monkeypatch,
        {"repository": "acme/repo", "omnigentExecutionPlan": _PLAN_BINDING},
        plan_error=plan_error,
    )

    with pytest.raises(RepositoryContractError, match="no other connection"):
        await managed_api_key_resolve_module.load_admitted_repository_access("mm:run")


async def test_authored_connection_is_used_without_reading_the_plan(monkeypatch):
    loaded = _recorded_run(
        monkeypatch,
        {
            "repository": {
                "provider": "git",
                "connectionRef": "repository-connection:explicit",
                "repository": {"name": "acme/repo"},
            },
            "omnigentExecutionPlan": _PLAN_BINDING,
        },
    )

    access = await managed_api_key_resolve_module.load_admitted_repository_access(
        "mm:run"
    )

    assert access == ("repository-connection:explicit", False)
    assert loaded == []
