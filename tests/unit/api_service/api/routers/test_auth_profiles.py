"""Unit/Integration tests for ManagedAgentAuthProfile CRUD API."""

import pytest
from uuid import uuid4
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base, ManagedAgentAuthProfile, ManagedAgentAuthMode, ManagedAgentRateLimitPolicy
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


async def get_or_create_sample_profile() -> ManagedAgentAuthProfile:
    """Helper to create a baseline profile in the test DB."""
    profile_id = "test_gemini_profile"
    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentAuthProfile, profile_id)
        if existing:
            return existing
            
        profile = ManagedAgentAuthProfile(
            profile_id=profile_id,
            runtime_id="gemini_pro_runtime",
            auth_mode=ManagedAgentAuthMode.OAUTH,
            volume_ref="gemini_auth_volume",
            account_label="test_account",
            max_parallel_runs=2,
            cooldown_after_429_seconds=120,
            rate_limit_policy=ManagedAgentRateLimitPolicy.BACKOFF,
            enabled=True,
        )
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        return profile


@pytest.mark.asyncio
async def test_create_auth_profile(client_app: AsyncClient, _module_db):
    """Test creating a new auth profile."""
    payload = {
        "profile_id": "new_profile",
        "runtime_id": "claude_v1",
        "auth_mode": "api_key",
        "api_key_ref": "secret_v1",
        "max_parallel_runs": 5,
        "cooldown_after_429_seconds": 60,
        "rate_limit_policy": "queue",
        "enabled": True
    }
    
    async with client_app as client:
        response = await client.post("/api/v1/auth-profiles", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    assert data["profile_id"] == "new_profile"
    assert data["auth_mode"] == "api_key"
    assert data["rate_limit_policy"] == "queue"


@pytest.mark.asyncio
async def test_create_duplicate_profile(client_app: AsyncClient, _module_db):
    """Test creating a profile that already exists returns 409."""
    sample_profile = await get_or_create_sample_profile()
    payload = {
        "profile_id": sample_profile.profile_id,
        "runtime_id": "duplicate_runtime",
        "auth_mode": "oauth",
    }
    async with client_app as client:
        response = await client.post("/api/v1/auth-profiles", json=payload)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_list_profiles(client_app: AsyncClient, _module_db):
    """Test retrieving lists of profiles."""
    sample_profile = await get_or_create_sample_profile()
    async with client_app as client:
        response = await client.get("/api/v1/auth-profiles")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(p["profile_id"] == sample_profile.profile_id for p in data)


@pytest.mark.asyncio
async def test_get_single_profile(client_app: AsyncClient, _module_db):
    """Test retrieving a single profile by ID."""
    sample_profile = await get_or_create_sample_profile()
    async with client_app as client:
        response = await client.get(f"/api/v1/auth-profiles/{sample_profile.profile_id}")
    assert response.status_code == 200
    assert response.json()["runtime_id"] == "gemini_pro_runtime"


@pytest.mark.asyncio
async def test_get_unknown_profile(client_app: AsyncClient, _module_db):
    """Test 404 on missing profile."""
    async with client_app as client:
        response = await client.get("/api/v1/auth-profiles/does_not_exist_xyz")
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
        response = await client.patch(f"/api/v1/auth-profiles/{sample_profile.profile_id}", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["max_parallel_runs"] == 10
    assert data["enabled"] is False


@pytest.mark.asyncio
async def test_delete_profile(client_app: AsyncClient, _module_db):
    """Test deleting a profile."""
    sample_profile = await get_or_create_sample_profile()
    async with client_app as client:
        response = await client.delete(f"/api/v1/auth-profiles/{sample_profile.profile_id}")
        assert response.status_code == 204
        
        # Verify it is gone
        check = await client.get(f"/api/v1/auth-profiles/{sample_profile.profile_id}")
        assert check.status_code == 404
