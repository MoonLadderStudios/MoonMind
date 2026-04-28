"""Unit/Integration tests for ManagedAgentProviderProfile CRUD API."""

from types import SimpleNamespace
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
    RuntimeMaterializationMode,
)
from api_service.main import app
from api_service.services.provider_profile_service import _manager_profile_payload

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
    readiness = response.json()["readiness"]
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
