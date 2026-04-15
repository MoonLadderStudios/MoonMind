"""Unit/Integration tests for ManagedAgentProviderProfile CRUD API."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import Base, ManagedAgentProviderProfile, ProviderCredentialSource, ManagedAgentRateLimitPolicy
from api_service.main import app


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


def _override_current_user(*, user_id=None, is_superuser: bool = False):
    user = SimpleNamespace(
        id=user_id if user_id is not None else uuid4(),
        email="provider-profile-test@example.com",
        is_active=True,
        is_superuser=is_superuser,
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
    assert response.json()["secret_refs"] == {"provider_api_key": "env://OPENAI_API_KEY"}


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
        "enabled": True
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
    }
    second_payload = {
        "profile_id": "runtime_default_second",
        "runtime_id": "codex_cli",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://second_secret"},
        "enabled": True,
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
        "is_default": True,
    }
    second_payload = {
        "profile_id": "patch_runtime_default_second",
        "runtime_id": "patch_runtime_default",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://patch_second_secret"},
        "enabled": True,
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
