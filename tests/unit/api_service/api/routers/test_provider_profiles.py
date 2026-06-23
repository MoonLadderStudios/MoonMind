"""Unit/Integration tests for ManagedAgentProviderProfile CRUD API."""

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers import provider_profiles as provider_profiles_router
from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ManagedAgentRateLimitPolicy,
    ManagedSecret,
    ProviderCredentialSource,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    ProviderProfileDisabledReason,
    RuntimeMaterializationMode,
    SecretStatus,
)
from api_service.main import app
from api_service.services.provider_profile_service import (
    _managed_secret_statuses_for_profiles,
    _manager_profile_payload,
    normalize_runtime_default_profile,
)
from api_service.services.provider_profile_readiness import provider_profile_launch_ready

@pytest.fixture(scope="module")
def _module_db(tmp_path_factory):
    """Create a single SQLite engine and schema for the entire module."""
    import asyncio

    tmp = tmp_path_factory.mktemp("integration_db_auth")
    db_url = f"sqlite+aiosqlite:///{tmp}/shared.db"

    async def _setup():
        engine = create_async_engine(db_url, future=True)
        session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, session_maker

    async def _teardown(engine):
        await engine.dispose()

    engine, session_maker = asyncio.run(_setup())

    _orig = (db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker)
    db_base.DATABASE_URL = db_url
    db_base.engine = engine
    db_base.async_session_maker = session_maker
    yield
    db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker = _orig
    asyncio.run(_teardown(engine))

@pytest.fixture
def client_app(_module_db) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")

def _override_current_user(
    *,
    user_id=None,
    is_superuser: bool = False,
    settings_permissions: set[str] | None = None,
):
    user = SimpleNamespace(
        id=user_id if user_id is not None else uuid4(),
        email="provider-profile-test@example.com",
        is_active=True,
        is_superuser=is_superuser,
        settings_permissions=(
            {"provider_profiles.read", "provider_profiles.write"}
            if settings_permissions is None
            else settings_permissions
        ),
    )
    dependencies = {
        dep.call
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/v1/provider-profiles")
        and getattr(route, "dependant", None) is not None
        for dep in route.dependant.dependencies
        if getattr(dep.call, "__name__", "") == "_current_user_fallback"
    } or {get_current_user()}
    for dependency in dependencies:
        app.dependency_overrides[dependency] = lambda user=user: user
    return user


@pytest.mark.asyncio
async def test_provider_profile_list_requires_read_permission(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user(settings_permissions=set())

    async with client_app as client:
        response = await client.get("/api/v1/provider-profiles")

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing required provider profile permission: provider_profiles.read."


@pytest.mark.asyncio
async def test_provider_profile_get_requires_read_permission_before_lookup(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user(settings_permissions=set())

    async with client_app as client:
        response = await client.get("/api/v1/provider-profiles/missing-profile")

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Missing required provider profile permission: provider_profiles.read."
    )


@pytest.mark.asyncio
async def test_provider_profile_write_actions_require_write_permission(
    client_app: AsyncClient, _module_db
) -> None:
    user = _override_current_user(settings_permissions={"provider_profiles.read"})
    profile_id = "read_only_profile"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="read_only_runtime",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.NONE,
                    runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
                    owner_user_id=user.id,
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        create_response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": "read_only_created",
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "credential_source": "none",
                "runtime_materialization_mode": "composite",
            },
        )
        update_response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"enabled": False},
        )
        delete_response = await client.delete(f"/api/v1/provider-profiles/{profile_id}")
        api_key_response = await client.post(
            "/api/v1/provider-profiles/missing-api-key-profile/credentials/api-key",
            json={"api_key": "sk-mm875-read-only-token"},
        )

    assert create_response.status_code == 403
    assert update_response.status_code == 403
    assert delete_response.status_code == 403
    assert api_key_response.status_code == 403
    assert create_response.json()["detail"] == (
        "Missing required provider profile permission: provider_profiles.write."
    )


class _TrackedProfile:
    def __init__(
        self,
        *,
        profile_id: str,
        runtime_id: str,
        enabled: bool,
        priority: int,
        is_default: bool,
        events: list[tuple[object, ...]],
        auth_state: ProviderProfileAuthState = ProviderProfileAuthState.CONNECTED,
        disabled_reason: ProviderProfileDisabledReason | None = None,
        credential_source: ProviderCredentialSource = ProviderCredentialSource.NONE,
        runtime_materialization_mode: RuntimeMaterializationMode = (
            RuntimeMaterializationMode.COMPOSITE
        ),
        max_parallel_runs: int = 1,
        cooldown_after_429_seconds: int = 900,
        secret_refs: Any = None,
        volume_ref: str | None = None,
        volume_mount_path: str | None = None,
        command_behavior: dict | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.runtime_id = runtime_id
        self.enabled = enabled
        self.auth_state = auth_state
        self.disabled_reason = disabled_reason
        self.credential_source = credential_source
        self.runtime_materialization_mode = runtime_materialization_mode
        self.max_parallel_runs = max_parallel_runs
        self.cooldown_after_429_seconds = cooldown_after_429_seconds
        self.secret_refs = secret_refs or {}
        self.volume_ref = volume_ref
        self.volume_mount_path = volume_mount_path
        self.command_behavior = command_behavior or {}
        self.priority = priority
        self._is_default = is_default
        self._events = events

    @property
    def is_default(self) -> bool:
        return self._is_default

    @is_default.setter
    def is_default(self, value: bool) -> None:
        self._is_default = value
        self._events.append(("set", self.profile_id, value))


class _TrackedExecuteResult:
    def __init__(self, rows: list[_TrackedProfile]) -> None:
        self._rows = rows

    def scalars(self) -> "_TrackedExecuteResult":
        return self

    def all(self) -> list[_TrackedProfile]:
        return self._rows


class _TrackedDefaultSession:
    def __init__(
        self,
        rows: list[_TrackedProfile],
        events: list[tuple[object, ...]],
    ) -> None:
        self._rows = rows
        self._events = events

    async def execute(self, _statement):
        return _TrackedExecuteResult(self._rows)

    async def flush(self) -> None:
        self._events.append(
            (
                "flush",
                {
                    row.profile_id: row.is_default
                    for row in self._rows
                },
            )
        )


@pytest.mark.asyncio
async def test_runtime_default_switch_flushes_old_default_first():
    events: list[tuple[object, ...]] = []
    minimax = _TrackedProfile(
        profile_id="claude_minimax",
        runtime_id="claude_code",
        enabled=True,
        priority=200,
        is_default=True,
        events=events,
    )
    anthropic = _TrackedProfile(
        profile_id="claude_anthropic",
        runtime_id="claude_code",
        enabled=True,
        priority=100,
        is_default=False,
        events=events,
    )
    session = _TrackedDefaultSession([minimax, anthropic], events)

    selected = await normalize_runtime_default_profile(
        session=session,
        runtime_id="claude_code",
        preferred_profile_id="claude_anthropic",
    )

    assert selected == "claude_anthropic"
    assert events == [
        ("set", "claude_minimax", False),
        (
            "flush",
            {"claude_minimax": False, "claude_anthropic": False},
        ),
        ("set", "claude_anthropic", True),
        (
            "flush",
            {"claude_minimax": False, "claude_anthropic": True},
        ),
    ]


@pytest.mark.asyncio
async def test_runtime_default_normalization_skips_not_launch_ready_profiles():
    events: list[tuple[object, ...]] = []
    blocked_default = _TrackedProfile(
        profile_id="claude_blocked",
        runtime_id="claude_code",
        enabled=True,
        priority=500,
        is_default=True,
        auth_state=ProviderProfileAuthState.CONNECTED,
        command_behavior={"auth_readiness": {"launch_ready": False}},
        events=events,
    )
    ready_fallback = _TrackedProfile(
        profile_id="claude_ready",
        runtime_id="claude_code",
        enabled=True,
        priority=100,
        is_default=False,
        auth_state=ProviderProfileAuthState.CONNECTED,
        command_behavior={"auth_readiness": {"launch_ready": True}},
        events=events,
    )
    session = _TrackedDefaultSession([blocked_default, ready_fallback], events)

    selected = await normalize_runtime_default_profile(
        session=session,
        runtime_id="claude_code",
    )

    assert selected == "claude_ready"
    assert blocked_default.is_default is False
    assert ready_fallback.is_default is True


def test_launch_ready_rejects_malformed_secret_refs() -> None:
    profile = _TrackedProfile(
        profile_id="malformed_secret_refs",
        runtime_id="codex_cli",
        enabled=True,
        priority=100,
        is_default=False,
        events=[],
        auth_state=ProviderProfileAuthState.CONNECTED,
        credential_source=ProviderCredentialSource.SECRET_REF,
        secret_refs=["db://not-a-dict"],
    )

    assert provider_profile_launch_ready(profile) is False

    profile.secret_refs = {"provider_api_key": 123}

    assert provider_profile_launch_ready(profile) is False


@pytest.mark.asyncio
async def test_managed_secret_statuses_ignores_malformed_secret_refs() -> None:
    class _EmptySecretSession:
        async def execute(self, _stmt):
            raise AssertionError("malformed secret_refs should not query secrets")

    rows = [
        _TrackedProfile(
            profile_id="malformed_secret_refs",
            runtime_id="codex_cli",
            enabled=True,
            priority=100,
            is_default=False,
            events=[],
            secret_refs=["db://not-a-dict"],
        ),
        _TrackedProfile(
            profile_id="non_string_secret_ref",
            runtime_id="codex_cli",
            enabled=True,
            priority=100,
            is_default=False,
            events=[],
            secret_refs={"provider_api_key": 123},
        ),
    ]

    statuses = await _managed_secret_statuses_for_profiles(
        session=_EmptySecretSession(),
        rows=rows,
    )

    assert statuses == {}

async def get_or_create_sample_profile() -> ManagedAgentProviderProfile:
    """Helper to create a baseline profile in the test DB."""
    profile_id = "test_gemini_profile"
    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing:
            return existing
            
        profile = ManagedAgentProviderProfile(
            profile_id=profile_id,
            runtime_id="gemini_pro_runtime",
            credential_source=ProviderCredentialSource.OAUTH_VOLUME,
            volume_ref="gemini_auth_volume",
            account_label="test_account",
            max_parallel_runs=2,
            cooldown_after_429_seconds=120,
            rate_limit_policy=ManagedAgentRateLimitPolicy.BACKOFF,
            enabled=True,
            is_default=True,
        )
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        return profile

@pytest.mark.asyncio
async def test_provider_profile_response_redacts_secret_like_runtime_fields(
    client_app: AsyncClient, _module_db
) -> None:
    """Browser-visible profile responses must not expose raw secret-like values."""
    profile_id = "profile_with_raw_runtime_secret"
    raw_secret = "sk-test-raw-secret-value"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="redaction_runtime",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode="api_key_env",
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    env_template={"OPENAI_API_KEY": raw_secret},
                    file_templates=[
                        {"path": "/tmp/auth.json", "content": raw_secret},
                        {
                            "path": "/tmp/config.json",
                            "contentTemplate": {"token": raw_secret},
                        },
                        {
                            "path": "/tmp/config.toml",
                            "content_template": {"api_key": raw_secret},
                        },
                    ],
                    command_behavior={"authorization": f"Bearer {raw_secret}"},
                    secret_refs={"provider_api_key": "env://OPENAI_API_KEY"},
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    response_text = response.text
    assert raw_secret not in response_text
    assert "Bearer" not in response_text
    assert response.json()["volume_ref"] == "codex_auth_volume"
    assert response.json()["volume_mount_path"] == "/home/app/.codex"
    assert response.json()["env_template"]["OPENAI_API_KEY"] == "[REDACTED]"
    assert response.json()["file_templates"][0]["content"] == "[REDACTED]"
    assert response.json()["file_templates"][1]["contentTemplate"]["token"] == (
        "[REDACTED]"
    )
    assert (
        response.json()["file_templates"][2]["content_template"]["api_key"]
        == "[REDACTED]"
    )
    assert response.json()["secret_refs"] == {"provider_api_key": "env://OPENAI_API_KEY"}

def test_provider_profile_manager_payload_redacts_secret_like_runtime_fields() -> None:
    raw_secret = "sk-test-manager-payload-secret"
    row = ManagedAgentProviderProfile(
        profile_id="manager_payload_redaction",
        runtime_id="codex_cli",
        provider_id="openai",
        credential_source=ProviderCredentialSource.OAUTH_VOLUME,
        runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
        volume_ref="codex_auth_volume",
        volume_mount_path="/home/app/.codex",
        env_template={"OPENAI_API_KEY": raw_secret},
        file_templates=[
            {"path": "/tmp/auth.json", "content": raw_secret},
            {"path": "/tmp/config.json", "contentTemplate": raw_secret},
            {
                "path": "/tmp/config.toml",
                "content_template": {"api_key": raw_secret},
            },
        ],
        command_behavior={"authorization": f"Bearer {raw_secret}"},
        secret_refs={"provider_api_key": "env://OPENAI_API_KEY"},
        max_parallel_runs=2,
        cooldown_after_429_seconds=120,
        max_lease_duration_seconds=900,
        enabled=True,
    )

    payload = _manager_profile_payload(row)

    assert raw_secret not in repr(payload)
    assert payload["volume_ref"] == "codex_auth_volume"
    assert payload["volume_mount_path"] == "/home/app/.codex"
    assert payload["max_parallel_runs"] == 2
    assert payload["cooldown_after_429_seconds"] == 120
    assert payload["max_lease_duration_seconds"] == 900
    assert payload["env_template"]["OPENAI_API_KEY"] == "[REDACTED]"
    assert payload["file_templates"][0]["content"] == "[REDACTED]"
    assert payload["file_templates"][1]["contentTemplate"] == "[REDACTED]"
    assert payload["file_templates"][2]["content_template"]["api_key"] == "[REDACTED]"
    assert payload["command_behavior"]["authorization"] == "[REDACTED_AUTHORIZATION]"
    assert payload["secret_refs"] == {"provider_api_key": "env://OPENAI_API_KEY"}

@pytest.mark.asyncio
async def test_create_codex_oauth_profile_requires_volume_ref_and_mount_path(
    client_app: AsyncClient, _module_db
) -> None:
    payload = {
        "profile_id": "codex_oauth_missing_refs",
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "credential_source": "oauth_volume",
        "runtime_materialization_mode": "oauth_home",
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
    }

    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)

    assert response.status_code == 422
    assert "volume_ref is required" in response.text
    assert "volume_mount_path is required" in response.text

@pytest.mark.asyncio
async def test_provider_profile_update_rejects_non_owner(
    client_app: AsyncClient, _module_db
) -> None:
    profile_id = "profile_owned_by_someone_else"
    owner_id = uuid4()

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="owner_runtime",
                    provider_id="openai",
                    owner_user_id=owner_id,
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode="oauth_home",
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    enabled=True,
                )
            )
            await session.commit()

    other_user = _override_current_user(user_id=uuid4(), is_superuser=False)
    try:
        async with client_app as client:
            response = await client.patch(
                f"/api/v1/provider-profiles/{profile_id}",
                json={"enabled": False},
            )
    finally:
        app.dependency_overrides.clear()

    assert str(other_user.id) != str(owner_id)
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to manage this provider profile."

@pytest.mark.asyncio
async def test_provider_profile_update_allows_ownerless_shared_profile(
    client_app: AsyncClient, _module_db
) -> None:
    profile_id = "ownerless_shared_profile"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="shared_runtime",
                    provider_id="openai",
                    owner_user_id=None,
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode="oauth_home",
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    enabled=True,
                )
            )
            await session.commit()

    _override_current_user(user_id=uuid4(), is_superuser=False)
    try:
        async with client_app as client:
            response = await client.patch(
                f"/api/v1/provider-profiles/{profile_id}",
                json={"enabled": False},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["volume_mount_path"] == "/home/app/.codex"

@pytest.mark.asyncio
async def test_create_provider_profile(client_app: AsyncClient, _module_db):
    """Test creating a new provider profile."""
    payload = {
        "profile_id": "new_profile",
        "runtime_id": "claude_v1",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://secret_v1"},
        "max_parallel_runs": 5,
        "cooldown_after_429_seconds": 60,
        "rate_limit_policy": "queue",
        "default_model": "test-model-v2",
        "model_overrides": {"smart": "test-model-v3"},
        "enabled": True,
        "auth_state": "connected",
        "last_auth_method": "secret_ref",
    }
    
    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    assert data["profile_id"] == "new_profile"
    assert data["credential_source"] == "secret_ref"
    assert data["rate_limit_policy"] == "queue"
    assert data["default_model"] == "test-model-v2"
    assert data["model_overrides"] == {"smart": "test-model-v3"}
    assert data["is_default"] is True
    assert data["auth_state"] == "connected"
    assert data["disabled_reason"] is None
    assert data["last_auth_method"] == "secret_ref"


@pytest.mark.asyncio
async def test_create_enabled_provider_profile_clears_default_disabled_reason(
    client_app: AsyncClient, _module_db
) -> None:
    payload = {
        "profile_id": "enabled_profile_clears_disabled_reason",
        "runtime_id": "enabled_profile_clear_runtime",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://enabled_profile_secret"},
        "enabled": True,
        "auth_state": "connected",
        "last_auth_method": "secret_ref",
    }

    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["enabled"] is True
    assert data["auth_state"] == "connected"
    assert data["disabled_reason"] is None


@pytest.mark.asyncio
async def test_create_provider_profile_defaults_to_unconfigured_not_launchable(
    client_app: AsyncClient, _module_db
) -> None:
    payload = {
        "profile_id": "unconfigured_custom_profile",
        "runtime_id": "custom_runtime",
        "provider_id": "custom",
        "credential_source": "none",
        "runtime_materialization_mode": "composite",
    }

    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["enabled"] is False
    assert data["auth_state"] == "not_configured"
    assert data["disabled_reason"] == "missing_credentials"
    assert data["credential_source"] == "none"
    assert data["is_default"] is False
    readiness = data["readiness"]
    assert readiness["launch_ready"] is False
    checks = {check["id"]: check for check in readiness["checks"]}
    assert checks["enabled"]["status"] == "error"
    assert checks["auth_state"]["status"] == "error"

@pytest.mark.asyncio
async def test_create_second_profile_can_become_runtime_default(
    client_app: AsyncClient,
    _module_db,
):
    """Creating a second profile with is_default should move the runtime default."""
    first_payload = {
        "profile_id": "runtime_default_first",
        "runtime_id": "codex_cli",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://first_secret"},
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
        "last_auth_method": "secret_ref",
    }
    second_payload = {
        "profile_id": "runtime_default_second",
        "runtime_id": "codex_cli",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://second_secret"},
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
        "last_auth_method": "secret_ref",
        "is_default": True,
    }

    async with client_app as client:
        first_response = await client.post("/api/v1/provider-profiles", json=first_payload)
        second_response = await client.post("/api/v1/provider-profiles", json=second_payload)
        listed = await client.get("/api/v1/provider-profiles", params={"runtime_id": "codex_cli"})

    assert first_response.status_code == 201
    assert first_response.json()["is_default"] is True
    assert second_response.status_code == 201
    assert second_response.json()["is_default"] is True
    assert listed.status_code == 200

    profiles = {profile["profile_id"]: profile for profile in listed.json()}
    assert profiles["runtime_default_first"]["is_default"] is False
    assert profiles["runtime_default_second"]["is_default"] is True

@pytest.mark.asyncio
async def test_update_profile_can_become_runtime_default(
    client_app: AsyncClient,
    _module_db,
):
    first_payload = {
        "profile_id": "patch_runtime_default_first",
        "runtime_id": "patch_runtime_default",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://patch_first_secret"},
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
        "last_auth_method": "secret_ref",
        "is_default": True,
    }
    second_payload = {
        "profile_id": "patch_runtime_default_second",
        "runtime_id": "patch_runtime_default",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://patch_second_secret"},
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
        "last_auth_method": "secret_ref",
    }

    async with client_app as client:
        first_response = await client.post("/api/v1/provider-profiles", json=first_payload)
        second_response = await client.post("/api/v1/provider-profiles", json=second_payload)
        update_response = await client.patch(
            "/api/v1/provider-profiles/patch_runtime_default_second",
            json={"is_default": True},
        )
        listed = await client.get(
            "/api/v1/provider-profiles",
            params={"runtime_id": "patch_runtime_default"},
        )

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert update_response.status_code == 200
    assert update_response.json()["is_default"] is True
    assert listed.status_code == 200

    profiles = {profile["profile_id"]: profile for profile in listed.json()}
    assert profiles["patch_runtime_default_first"]["is_default"] is False
    assert profiles["patch_runtime_default_second"]["is_default"] is True


@pytest.mark.asyncio
async def test_update_profile_rejects_enabled_without_connected_auth_state(
    client_app: AsyncClient,
    _module_db,
) -> None:
    payload = {
        "profile_id": "patch_enabled_requires_connected_auth",
        "runtime_id": "patch_enabled_requires_connected",
        "credential_source": "none",
        "runtime_materialization_mode": "composite",
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        update_response = await client.patch(
            "/api/v1/provider-profiles/patch_enabled_requires_connected_auth",
            json={"enabled": True},
        )

    assert create_response.status_code == 201
    assert update_response.status_code == 422
    assert update_response.json()["detail"] == (
        "Enabled profiles require auth_state=connected"
    )


@pytest.mark.asyncio
async def test_update_profile_clears_disabled_reason_when_enabled(
    client_app: AsyncClient,
    _module_db,
) -> None:
    payload = {
        "profile_id": "patch_enabled_clears_disabled_reason",
        "runtime_id": "patch_enabled_clears",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://patch_enabled_secret"},
        "auth_state": "connected",
        "disabled_reason": "missing_credentials",
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        update_response = await client.patch(
            "/api/v1/provider-profiles/patch_enabled_clears_disabled_reason",
            json={"enabled": True},
        )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    data = update_response.json()
    assert data["enabled"] is True
    assert data["auth_state"] == "connected"
    assert data["disabled_reason"] is None


@pytest.mark.asyncio
async def test_update_profile_enabled_accepts_active_database_secret_ref(
    client_app: AsyncClient,
    _module_db,
) -> None:
    suffix = uuid4().hex
    profile_id = f"patch_enabled_db_secret_{suffix}"
    secret_slug = f"patch-enabled-db-secret-{suffix}"
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedSecret(
                slug=secret_slug,
                ciphertext="encrypted-test-value",
                status=SecretStatus.ACTIVE,
                details={},
            )
        )
        await session.commit()

    payload = {
        "profile_id": profile_id,
        "runtime_id": "patch_enabled_db_secret",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"OPENAI_API_KEY": f"db://{secret_slug}"},
        "auth_state": "connected",
        "disabled_reason": "missing_credentials",
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        update_response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"enabled": True},
        )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    data = update_response.json()
    assert data["enabled"] is True
    checks = {check["id"]: check for check in data["readiness"]["checks"]}
    assert checks["secret_refs"]["status"] == "pass"


@pytest.mark.asyncio
async def test_create_enabled_profile_rejects_missing_database_secret_ref(
    client_app: AsyncClient,
    _module_db,
) -> None:
    payload = {
        "profile_id": f"create_enabled_missing_db_secret_{uuid4().hex}",
        "runtime_id": "create_enabled_missing_db_secret",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"OPENAI_API_KEY": "db://missing-provider-secret"},
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
        "last_auth_method": "secret_ref",
    }

    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)

    assert response.status_code == 422
    assert "managed secret db://missing-provider-secret was not found" in response.text


@pytest.mark.asyncio
async def test_update_claude_anthropic_can_replace_minimax_runtime_default(
    client_app: AsyncClient,
    _module_db,
    monkeypatch,
) -> None:
    """Regression for switching the Claude Code runtime default on PostgreSQL."""

    async def _fake_sync_provider_profile_manager(
        *,
        session,
        runtime_id: str,
    ) -> None:
        assert runtime_id == "claude_code"

    monkeypatch.setattr(
        provider_profiles_router,
        "sync_provider_profile_manager",
        _fake_sync_provider_profile_manager,
    )

    async with db_base.async_session_maker() as session:
        session.add_all(
            [
                ManagedAgentProviderProfile(
                    profile_id="claude_minimax",
                    runtime_id="claude_code",
                    provider_id="minimax",
                    provider_label="MiniMax",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    secret_refs={"ANTHROPIC_AUTH_TOKEN": "env://MINIMAX_API_KEY"},
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    disabled_reason=None,
                    last_auth_method=ProviderProfileAuthMethod.SECRET_REF,
                    is_default=True,
                    priority=200,
                ),
                ManagedAgentProviderProfile(
                    profile_id="claude_anthropic",
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    provider_label="Anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="claude_auth_volume",
                    volume_mount_path="/home/app/.claude",
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    disabled_reason=None,
                    last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
                    is_default=False,
                    priority=100,
                ),
            ]
        )
        await session.commit()

    async with client_app as client:
        update_response = await client.patch(
            "/api/v1/provider-profiles/claude_anthropic",
            json={"is_default": True},
        )
        listed = await client.get(
            "/api/v1/provider-profiles",
            params={"runtime_id": "claude_code"},
        )

    assert update_response.status_code == 200
    assert update_response.json()["is_default"] is True
    assert listed.status_code == 200
    profiles = {profile["profile_id"]: profile for profile in listed.json()}
    assert profiles["claude_anthropic"]["is_default"] is True
    assert profiles["claude_minimax"]["is_default"] is False
    assert sum(1 for profile in listed.json() if profile["is_default"]) == 1


@pytest.mark.asyncio
async def test_create_provider_profile_invalid_secret_refs(client_app: AsyncClient, _module_db):
    """Test that creating a profile with raw secrets fails."""
    payload = {
        "profile_id": "invalid_profile",
        "runtime_id": "claude_v1",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "raw_secret_value"}, # not a valid ref
        "max_parallel_runs": 1,
    }
    
    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)
    
    assert response.status_code == 422
    assert "Invalid secret reference" in response.text

@pytest.mark.asyncio
async def test_provider_profile_response_includes_readiness_blockers(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = "oauth_missing_metadata_readiness"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref=None,
                    volume_mount_path=None,
                    enabled=False,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    readiness = response.json()["readiness"]
    assert readiness["status"] == "blocked"
    assert readiness["launch_ready"] is False
    checks = {check["id"]: check for check in readiness["checks"]}
    assert checks["enabled"]["status"] == "error"
    assert checks["oauth_volume"]["status"] == "error"
    assert "volume_ref" in checks["oauth_volume"]["message"]
    assert "volume_mount_path" in checks["oauth_volume"]["message"]


@pytest.mark.asyncio
async def test_provider_profile_readiness_reports_managed_secret_status(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = "missing_db_secret_readiness"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    secret_refs={"provider_api_key": "db://does-not-exist"},
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["launch_ready"] is False
    readiness = payload["readiness"]
    assert readiness["status"] == "blocked"
    checks = {check["id"]: check for check in readiness["checks"]}
    assert checks["secret_refs"]["status"] == "error"
    assert "provider_api_key" in checks["secret_refs"]["message"]
    assert "does-not-exist" in checks["secret_refs"]["message"]


@pytest.mark.asyncio
async def test_provider_profile_readiness_reports_invalid_stored_secret_ref(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = "invalid_stored_secret_ref_readiness"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    secret_refs={"provider_api_key": "not-a-secret-ref"},
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    readiness = response.json()["readiness"]
    assert readiness["status"] == "blocked"
    checks = {check["id"]: check for check in readiness["checks"]}
    assert checks["secret_refs"]["status"] == "error"
    assert "provider_api_key" in checks["secret_refs"]["message"]
    assert "invalid SecretRef" in checks["secret_refs"]["message"]


@pytest.mark.asyncio
async def test_provider_profile_readiness_redacts_provider_failure_text(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = "provider_failure_readiness_redaction"
    raw_token = "sk-ant-secret-readiness-token"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    secret_refs={"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
                    command_behavior={
                        "auth_readiness": {
                            "launch_ready": False,
                            "failure_reason": f"token={raw_token} expired",
                        }
                    },
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    response_text = response.text
    assert raw_token not in response_text
    readiness = response.json()["readiness"]
    checks = {check["id"]: check for check in readiness["checks"]}
    assert checks["provider_validation"]["status"] == "error"
    assert "[REDACTED]" in checks["provider_validation"]["message"]

@pytest.mark.asyncio
async def test_create_duplicate_profile(client_app: AsyncClient, _module_db):
    """Test creating a profile that already exists returns 409."""
    sample_profile = await get_or_create_sample_profile()
    payload = {
        "profile_id": sample_profile.profile_id,
        "runtime_id": "duplicate_runtime",
        "credential_source": "oauth_volume",
        "runtime_materialization_mode": "oauth_home",
    }
    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)
    assert response.status_code == 409

@pytest.mark.asyncio
async def test_list_profiles(client_app: AsyncClient, _module_db):
    """Test retrieving lists of profiles."""
    sample_profile = await get_or_create_sample_profile()
    async with client_app as client:
        response = await client.get("/api/v1/provider-profiles")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(p["profile_id"] == sample_profile.profile_id for p in data)

@pytest.mark.asyncio
async def test_get_single_profile(client_app: AsyncClient, _module_db):
    """Test retrieving a single profile by ID."""
    sample_profile = await get_or_create_sample_profile()
    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{sample_profile.profile_id}")
    assert response.status_code == 200
    assert response.json()["runtime_id"] == "gemini_pro_runtime"

@pytest.mark.asyncio
async def test_get_unknown_profile(client_app: AsyncClient, _module_db):
    """Test 404 on missing profile."""
    async with client_app as client:
        response = await client.get("/api/v1/provider-profiles/does_not_exist_xyz")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_update_profile(client_app: AsyncClient, _module_db):
    """Test patching an existing profile."""
    sample_profile = await get_or_create_sample_profile()
    payload = {
        "max_parallel_runs": 10,
        "enabled": False
    }
    async with client_app as client:
        response = await client.patch(f"/api/v1/provider-profiles/{sample_profile.profile_id}", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["max_parallel_runs"] == 10
    assert data["enabled"] is False

@pytest.mark.asyncio
async def test_delete_profile(client_app: AsyncClient, _module_db):
    """Test deleting a profile."""
    sample_profile = await get_or_create_sample_profile()
    async with client_app as client:
        response = await client.delete(f"/api/v1/provider-profiles/{sample_profile.profile_id}")
        assert response.status_code == 204
        
        # Verify it is gone
        check = await client.get(f"/api/v1/provider-profiles/{sample_profile.profile_id}")
        assert check.status_code == 404

@pytest.mark.asyncio
async def test_update_profile_syncs_provider_profile_manager(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
):
    sample_profile = await get_or_create_sample_profile()
    signals: list[tuple[str, dict]] = []
    started: list[dict] = []

    class _FakeHandle:
        async def signal(self, signal_name: str, payload: dict) -> None:
            signals.append((signal_name, payload))

    class _FakeTemporalClient:
        async def start_workflow(self, *args, **kwargs):
            started.append({"args": args, "kwargs": kwargs})

        def get_workflow_handle(self, workflow_id: str):
            assert workflow_id == f"provider-profile-manager:{sample_profile.runtime_id}"
            return _FakeHandle()

    class _FakeTemporalAdapter:
        async def get_client(self):
            return _FakeTemporalClient()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _FakeTemporalAdapter,
    )

    payload = {
        "enabled": False,
    }
    async with client_app as client:
        response = await client.patch(
            f"/api/v1/provider-profiles/{sample_profile.profile_id}",
            json=payload,
        )
    assert response.status_code == 200
    assert started, "Expected manager ensure/start attempt before sync"
    assert signals, "Expected sync_profiles signal after update"
    signal_name, signal_payload = signals[-1]
    assert signal_name == "sync_profiles"
    assert signal_payload["profiles"] == []

@pytest.mark.asyncio
async def test_claude_manual_auth_commit_stores_secret_ref_only(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "claude-anthropic-manual-auth"
    submitted_token = "sk-ant-test-route-token"
    validated_tokens: list[str] = []
    synced_runtimes: list[str] = []

    async def _fake_validate(token: str) -> None:
        validated_tokens.append(token)

    async def _fake_sync(*, session: AsyncSession, runtime_id: str) -> None:
        synced_runtimes.append(runtime_id)

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_claude_manual_token",
        _fake_validate,
    )
    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.sync_provider_profile_manager",
        _fake_sync,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    provider_label="Anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="claude_auth_volume",
                    volume_mount_path="/home/app/.claude",
                    secret_refs={"custom_tool": "env://CUSTOM_TOOL_SECRET"},
                    clear_env_keys=["OPENAI_API_KEY", "CUSTOM_ENV"],
                    env_template={
                        "CUSTOM_ENV": {"from_secret_ref": "custom_tool"},
                    },
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/manual-auth/commit",
            json={"token": submitted_token},
        )
        profile_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    response_text = response.text
    assert submitted_token not in response_text
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["status_label"] == "Anthropic API key ready"
    assert payload["readiness"]["connected"] is True
    assert payload["readiness"]["backing_secret_exists"] is True
    assert payload["readiness"]["launch_ready"] is True
    expected_secret_slug = provider_profiles_router._claude_manual_secret_slug(
        profile_id
    )
    expected_secret_ref = f"db://{expected_secret_slug}"
    assert payload["secret_ref"] == expected_secret_ref

    assert profile_response.status_code == 200
    profile_payload = profile_response.json()
    assert submitted_token not in profile_response.text
    assert profile_payload["credential_source"] == "secret_ref"
    assert profile_payload["runtime_materialization_mode"] == "api_key_env"
    assert profile_payload["volume_ref"] == "claude_auth_volume"
    assert profile_payload["volume_mount_path"] == "/home/app/.claude"
    assert profile_payload["secret_refs"] == {
        "custom_tool": "env://CUSTOM_TOOL_SECRET",
        "anthropic_api_key": expected_secret_ref,
    }
    assert profile_payload["env_template"] == {
        "CUSTOM_ENV": {"from_secret_ref": "custom_tool"},
        "ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"},
    }
    assert "ANTHROPIC_API_KEY" in profile_payload["clear_env_keys"]
    assert "ANTHROPIC_AUTH_TOKEN" in profile_payload["clear_env_keys"]
    assert "OPENAI_API_KEY" in profile_payload["clear_env_keys"]
    assert "CUSTOM_ENV" in profile_payload["clear_env_keys"]
    assert profile_payload["clear_env_keys"].count("OPENAI_API_KEY") == 1
    assert profile_payload["command_behavior"]["auth_strategy"] == "claude_credential_methods"
    assert profile_payload["command_behavior"]["auth_state"] == "connected"
    assert profile_payload["command_behavior"]["auth_actions"] == [
        "connect_oauth",
        "use_api_key",
        "validate_oauth",
        "disconnect_oauth",
    ]
    assert profile_payload["command_behavior"]["auth_status_label"] == "Anthropic API key ready"

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedSecret).where(
                ManagedSecret.slug == expected_secret_slug
            )
        )
        secret = result.scalar_one()

    assert secret.ciphertext == submitted_token
    assert validated_tokens == [submitted_token]
    assert synced_runtimes == ["claude_code"]

def test_claude_manual_auth_secret_slug_is_collision_resistant() -> None:
    first = provider_profiles_router._claude_manual_secret_slug("claude.anthropic")
    second = provider_profiles_router._claude_manual_secret_slug("claude_anthropic")

    assert first != second
    assert first.startswith("claude-anthropic-")
    assert second.startswith("claude-anthropic-")
    assert first.endswith("-token")
    assert second.endswith("-token")

@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "profile_id",
        "runtime_id",
        "provider_id",
        "api_key",
        "secret_role",
        "env_key",
        "clear_env_key",
        "status_label",
    ),
    [
        (
            "mm-875-anthropic-api-key",
            "claude_code",
            "anthropic",
            "sk-ant-mm875-route-token",
            "anthropic_api_key",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "Anthropic API key ready",
        ),
        (
            "mm-875-openai-api-key",
            "codex_cli",
            "openai",
            "sk-mm875-openai-route-token",
            "openai_api_key",
            "OPENAI_API_KEY",
            "MINIMAX_API_KEY",
            "OpenAI API key ready",
        ),
        (
            "mm-875-google-api-key",
            "gemini_cli",
            "google",
            "google-mm875-route-token",
            "google_api_key",
            "GEMINI_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "Google API key ready",
        ),
    ],
)
async def test_provider_api_key_setup_stores_secret_ref_mappings_only(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
    profile_id: str,
    runtime_id: str,
    provider_id: str,
    api_key: str,
    secret_role: str,
    env_key: str,
    clear_env_key: str,
    status_label: str,
) -> None:
    validated: list[tuple[str, str]] = []
    synced_runtimes: list[str] = []

    async def _fake_validate(provider: str, key: str) -> None:
        validated.append((provider, key))

    async def _fake_sync(*, session: AsyncSession, runtime_id: str) -> None:
        synced_runtimes.append(runtime_id)

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_provider_api_key",
        _fake_validate,
    )
    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.sync_provider_profile_manager",
        _fake_sync,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id=runtime_id,
                    provider_id=provider_id,
                    provider_label=provider_id.title(),
                    credential_source=ProviderCredentialSource.NONE,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    secret_refs={"custom_tool": "env://CUSTOM_TOOL_SECRET"},
                    clear_env_keys=["CUSTOM_ENV"],
                    env_template={
                        "CUSTOM_ENV": {"from_secret_ref": "custom_tool"},
                    },
                    enabled=False,
                    is_default=False,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/credentials/api-key",
            json={
                "api_key": api_key,
                "account_label": "MM-875 route test",
                "make_default": True,
                "enable_after_validation": True,
            },
        )
        profile_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    assert api_key not in response.text
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["status_label"] == status_label
    assert payload["readiness"]["connected"] is True
    assert payload["readiness"]["launch_ready"] is True
    expected_secret_slug = provider_profiles_router._provider_api_key_secret_slug(
        profile_id,
        secret_role,
    )
    expected_secret_ref = f"db://{expected_secret_slug}"
    assert payload["secret_ref"] == expected_secret_ref

    assert profile_response.status_code == 200
    profile_payload = profile_response.json()
    assert api_key not in profile_response.text
    assert profile_payload["credential_source"] == "secret_ref"
    assert profile_payload["runtime_materialization_mode"] == "api_key_env"
    assert profile_payload["secret_refs"] == {
        "custom_tool": "env://CUSTOM_TOOL_SECRET",
        secret_role: expected_secret_ref,
    }
    assert profile_payload["env_template"] == {
        "CUSTOM_ENV": {"from_secret_ref": "custom_tool"},
        env_key: {"from_secret_ref": secret_role},
    }
    assert clear_env_key in profile_payload["clear_env_keys"]
    assert profile_payload["account_label"] == "MM-875 route test"
    assert profile_payload["enabled"] is True
    assert profile_payload["is_default"] is True
    assert profile_payload["auth_state"] == "connected"
    assert profile_payload["disabled_reason"] is None
    assert profile_payload["first_authenticated_at"] is not None
    assert profile_payload["last_validated_at"] is not None
    assert profile_payload["last_auth_method"] == "secret_ref"
    assert profile_payload["command_behavior"]["auth_strategy"] == "api_key_env"
    assert profile_payload["command_behavior"]["auth_status_label"] == status_label

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedSecret).where(ManagedSecret.slug == expected_secret_slug)
        )
        secret = result.scalar_one()

    assert secret.ciphertext == api_key
    assert validated == [(provider_id, api_key)]
    assert synced_runtimes == [runtime_id]

@pytest.mark.asyncio
async def test_provider_api_key_setup_failed_validation_updates_state_without_secret(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "mm-875-openai-invalid-api-key"
    fallback_profile_id = "mm-875-openai-invalid-api-key-fallback"
    runtime_id = "codex_cli"
    raw_key = "sk-mm875-invalid-token"
    validated: list[tuple[str, str]] = []
    synced_runtimes: list[str] = []

    async def _fake_validate(provider: str, key: str) -> None:
        validated.append((provider, key))
        raise provider_profiles_router.HTTPException(
            status_code=401,
            detail="API key validation failed.",
        )

    async def _fake_sync(*, session: AsyncSession, runtime_id: str) -> None:
        synced_runtimes.append(runtime_id)

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_provider_api_key",
        _fake_validate,
    )
    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.sync_provider_profile_manager",
        _fake_sync,
    )

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedAgentProviderProfile).where(
                ManagedAgentProviderProfile.runtime_id == runtime_id
            )
        )
        for row in result.scalars():
            row.is_default = False
        await session.flush()
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id=runtime_id,
                    provider_id="openai",
                    provider_label="OpenAI",
                    credential_source=ProviderCredentialSource.NONE,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    enabled=True,
                    is_default=True,
                    priority=10_100,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                )
            )
        fallback = await session.get(ManagedAgentProviderProfile, fallback_profile_id)
        if fallback is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=fallback_profile_id,
                    runtime_id=runtime_id,
                    provider_id="openai",
                    provider_label="OpenAI",
                    credential_source=ProviderCredentialSource.NONE,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    enabled=True,
                    is_default=False,
                    priority=10_000,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/credentials/api-key",
            json={"api_key": raw_key},
        )
        profile_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")
        fallback_response = await client.get(
            f"/api/v1/provider-profiles/{fallback_profile_id}"
        )

    assert response.status_code == 401
    assert raw_key not in response.text
    assert response.json()["detail"] == "API key validation failed."
    profile_payload = profile_response.json()
    assert raw_key not in profile_response.text
    assert profile_payload["enabled"] is False
    assert profile_payload["is_default"] is False
    assert profile_payload["auth_state"] == "validation_failed"
    assert profile_payload["disabled_reason"] == "auth_invalid"
    assert profile_payload["secret_refs"] == {}
    assert profile_payload["env_template"] == {}
    assert profile_payload["command_behavior"]["auth_readiness"]["failure_reason"] == (
        "API key validation failed."
    )
    assert fallback_response.json()["is_default"] is True

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedSecret))
        secrets = result.scalars().all()

    assert all(secret.ciphertext != raw_key for secret in secrets)
    assert validated == [("openai", raw_key)]
    assert synced_runtimes == [runtime_id]

@pytest.mark.asyncio
async def test_provider_api_key_setup_transient_validation_error_preserves_profile(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "mm-875-openai-transient-api-key"
    raw_key = "sk-mm875-transient-token"
    synced_runtimes: list[str] = []

    async def _fake_validate(provider: str, key: str) -> None:
        assert (provider, key) == ("openai", raw_key)
        raise provider_profiles_router.HTTPException(
            status_code=502,
            detail="Provider validation temporarily unavailable.",
        )

    async def _fake_sync(*, session: AsyncSession, runtime_id: str) -> None:
        synced_runtimes.append(runtime_id)

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_provider_api_key",
        _fake_validate,
    )
    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.sync_provider_profile_manager",
        _fake_sync,
    )

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedAgentProviderProfile).where(
                ManagedAgentProviderProfile.runtime_id == "codex_cli"
            )
        )
        for row in result.scalars():
            row.is_default = False
        await session.flush()
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="codex_cli",
                    provider_id="openai",
                    provider_label="OpenAI",
                    credential_source=ProviderCredentialSource.NONE,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    enabled=True,
                    is_default=True,
                    priority=10_200,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/credentials/api-key",
            json={"api_key": raw_key},
        )
        profile_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 502
    assert raw_key not in response.text
    profile_payload = profile_response.json()
    assert profile_payload["enabled"] is True
    assert profile_payload["is_default"] is True
    assert profile_payload["auth_state"] == "connected"
    assert profile_payload["disabled_reason"] is None
    assert profile_payload["secret_refs"] == {}
    assert profile_payload["env_template"] == {}
    assert synced_runtimes == []

@pytest.mark.asyncio
async def test_provider_api_key_setup_can_validate_without_enabling(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "mm-875-google-validate-only"
    raw_key = "google-mm875-validate-only"

    async def _fake_validate(provider: str, key: str) -> None:
        assert (provider, key) == ("google", raw_key)

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_provider_api_key",
        _fake_validate,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="gemini_cli",
                    provider_id="google",
                    provider_label="Google",
                    credential_source=ProviderCredentialSource.NONE,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    enabled=False,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/credentials/api-key",
            json={
                "api_key": raw_key,
                "enable_after_validation": False,
                "make_default": False,
            },
        )
        profile_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    profile_payload = profile_response.json()
    assert raw_key not in profile_response.text
    assert profile_payload["credential_source"] == "secret_ref"
    assert profile_payload["auth_state"] == "connected"
    assert profile_payload["disabled_reason"] == "user_disabled"
    assert profile_payload["enabled"] is False
    assert profile_payload["is_default"] is False

@pytest.mark.asyncio
async def test_claude_oauth_lifecycle_actions_validate_and_disconnect(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "claude-anthropic-oauth-lifecycle"
    synced_runtimes: list[str] = []

    async def _fake_verify(
        *,
        runtime_id: str,
        volume_ref: str,
        volume_mount_path: str | None,
    ) -> dict[str, object]:
        assert runtime_id == "claude_code"
        assert volume_ref == "claude_auth_volume"
        assert volume_mount_path == "/home/app/.claude"
        return {"verified": True}

    async def _fake_sync(*, session: AsyncSession, runtime_id: str) -> None:
        synced_runtimes.append(runtime_id)

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _fake_verify,
    )
    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.sync_provider_profile_manager",
        _fake_sync,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    provider_label="Anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="claude_auth_volume",
                    volume_mount_path="/home/app/.claude",
                    enabled=True,
                    command_behavior={
                        "auth_strategy": "claude_credential_methods",
                        "auth_actions": [
                            "connect_oauth",
                            "use_api_key",
                            "validate_oauth",
                            "disconnect_oauth",
                        ],
                    },
                )
            )
            await session.commit()

    async with client_app as client:
        validate_response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/oauth/validate"
        )
        disconnect_response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/oauth/disconnect"
        )
        profile_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert validate_response.status_code == 200
    assert validate_response.json()["status"] == "ready"
    assert disconnect_response.status_code == 200
    assert disconnect_response.json()["status"] == "disconnected"
    profile_payload = profile_response.json()
    assert profile_payload["credential_source"] == "none"
    assert profile_payload["volume_ref"] is None
    assert profile_payload["volume_mount_path"] is None
    assert profile_payload["command_behavior"]["auth_actions"] == ["use_api_key"]
    assert profile_payload["command_behavior"]["auth_status_label"] == "Claude OAuth disconnected"
    assert synced_runtimes == ["claude_code", "claude_code"]

@pytest.mark.asyncio
async def test_validate_claude_manual_token_reuses_shared_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_clients: list[object] = []
    requested_tokens: list[str] = []

    class _FakeResponse:
        status_code = 200

    class _FakeClient:
        is_closed = False

        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout
            created_clients.append(self)

        async def get(self, _url: str, *, headers: dict[str, str]) -> _FakeResponse:
            requested_tokens.append(headers["x-api-key"])
            return _FakeResponse()

    monkeypatch.setattr(
        provider_profiles_router,
        "_claude_manual_validation_client",
        None,
    )
    monkeypatch.setattr(provider_profiles_router.httpx, "AsyncClient", _FakeClient)

    await provider_profiles_router.validate_claude_manual_token("sk-ant-test-one")
    await provider_profiles_router.validate_claude_manual_token("sk-ant-test-two")

    assert len(created_clients) == 1
    assert requested_tokens == ["sk-ant-test-one", "sk-ant-test-two"]

@pytest.mark.asyncio
async def test_claude_manual_auth_commit_rejects_malformed_token_without_persisting(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "claude-anthropic-bad-manual-auth"
    raw_token = "not-a-claude-token-secret"

    async def _unexpected_validate(token: str) -> None:
        raise AssertionError("malformed tokens should fail before upstream validation")

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_claude_manual_token",
        _unexpected_validate,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="claude_auth_volume",
                    volume_mount_path="/home/app/.claude",
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/manual-auth/commit",
            json={"token": raw_token},
        )

    assert response.status_code == 422
    assert raw_token not in response.text
    assert response.json()["detail"] == "Claude token validation failed."

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedSecret).where(
                ManagedSecret.slug
                == provider_profiles_router._claude_manual_secret_slug(profile_id)
            )
        )
        assert result.scalar_one_or_none() is None

@pytest.mark.asyncio
async def test_claude_manual_auth_commit_rejects_non_owner_without_validating_or_persisting(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "claude-anthropic-owned-manual-auth"
    owner_id = uuid4()
    raw_token = "sk-ant-test-non-owner-token"

    async def _unexpected_validate(token: str) -> None:
        raise AssertionError("unauthorized callers must fail before token validation")

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_claude_manual_token",
        _unexpected_validate,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    owner_user_id=owner_id,
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="claude_auth_volume",
                    volume_mount_path="/home/app/.claude",
                    enabled=True,
                )
            )
            await session.commit()

    other_user = _override_current_user(user_id=uuid4(), is_superuser=False)
    try:
        async with client_app as client:
            response = await client.post(
                f"/api/v1/provider-profiles/{profile_id}/manual-auth/commit",
                json={"token": raw_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert str(other_user.id) != str(owner_id)
    assert response.status_code == 403
    assert raw_token not in response.text
    assert response.json()["detail"] == "Not authorized to manage this provider profile."

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedSecret).where(
                ManagedSecret.slug
                == provider_profiles_router._claude_manual_secret_slug(profile_id)
            )
        )
        assert result.scalar_one_or_none() is None

@pytest.mark.asyncio
async def test_claude_manual_auth_commit_rejects_unsupported_profile_without_persisting(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "codex-unsupported-manual-auth"
    raw_token = "sk-ant-test-unsupported-profile-token"

    async def _unexpected_validate(token: str) -> None:
        raise AssertionError("unsupported profiles must fail before token validation")

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_claude_manual_token",
        _unexpected_validate,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/manual-auth/commit",
            json={"token": raw_token},
        )

    assert response.status_code == 422
    assert raw_token not in response.text
    assert response.json()["detail"] == (
        "Manual Claude auth is only supported for claude_code Anthropic profiles."
    )

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedSecret).where(
                ManagedSecret.slug
                == provider_profiles_router._claude_manual_secret_slug(profile_id)
            )
        )
        assert result.scalar_one_or_none() is None

@pytest.mark.asyncio
async def test_claude_oauth_validate_failure_redacts_secret_like_reason(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "claude-anthropic-oauth-validation-redaction"
    raw_secret = "sk-ant-test-validation-secret"
    raw_path = "/home/app/.claude/credentials.json"

    async def _fake_verify(
        *,
        runtime_id: str,
        volume_ref: str,
        volume_mount_path: str | None,
    ) -> dict[str, object]:
        assert runtime_id == "claude_code"
        assert volume_ref == "claude_auth_volume"
        assert volume_mount_path == "/home/app/.claude"
        return {
            "verified": False,
            "reason": f"token={raw_secret} in {raw_path}",
        }

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _fake_verify,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    provider_label="Anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="claude_auth_volume",
                    volume_mount_path="/home/app/.claude",
                    enabled=True,
                    command_behavior={
                        "auth_strategy": "claude_credential_methods",
                        "auth_actions": [
                            "connect_oauth",
                            "use_api_key",
                            "validate_oauth",
                            "disconnect_oauth",
                        ],
                    },
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/oauth/validate"
        )

    assert response.status_code == 400
    assert raw_secret not in response.text
    assert raw_path not in response.text
    detail = response.json()["detail"]
    assert "Claude OAuth validation failed:" in detail
    assert "[REDACTED]" in detail
    assert "[REDACTED_AUTH_PATH]" in detail

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, profile_id)
        assert profile is not None
        readiness = (profile.command_behavior or {}).get("auth_readiness", {})
        assert raw_secret not in str(readiness)
        assert raw_path not in str(readiness)
        assert readiness["failure_reason"] == "token=[REDACTED] in [REDACTED_AUTH_PATH]"
