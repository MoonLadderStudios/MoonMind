import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import settings as settings_router
from api_service.db import base as db_base
from api_service.db.models import Base, ManagedSecret
from api_service.main import app


@pytest.fixture
def settings_api_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/settings-api.db")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio_run = __import__("asyncio").run
    asyncio_run(_setup())
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    original = db_base.async_session_maker
    db_base.async_session_maker = session_maker
    try:
        yield session_maker
    finally:
        db_base.async_session_maker = original
        asyncio_run(engine.dispose())


@pytest.mark.asyncio
async def test_settings_catalog_endpoint_returns_grouped_descriptors():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/catalog",
            params={"section": "user-workspace", "scope": "workspace"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["section"] == "user-workspace"
    assert body["scope"] == "workspace"
    descriptor = next(
        item
        for item in body["categories"]["Workflow"]
        if item["key"] == "workflow.default_task_runtime"
    )
    assert descriptor["type"] == "enum"
    assert descriptor["ui"] == "select"
    assert descriptor["source"] in {"config_or_default", "environment"}
    assert descriptor["audit"] == {
        "store_old_value": True,
        "store_new_value": True,
        "redact": False,
    }


@pytest.mark.asyncio
async def test_settings_catalog_endpoint_reports_persisted_override_versions(settings_api_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )
        await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_publish_mode": "none"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )
        response = await client.get(
            "/api/v1/settings/catalog",
            params={"section": "user-workspace", "scope": "workspace"},
        )

    assert response.status_code == 200
    descriptor = next(
        item
        for item in response.json()["categories"]["Workflow"]
        if item["key"] == "workflow.default_publish_mode"
    )
    assert descriptor["effective_value"] == "none"
    assert descriptor["source"] == "workspace_override"
    assert descriptor["value_version"] == 2


@pytest.mark.asyncio
async def test_effective_setting_endpoint_returns_structured_unknown_key_error():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/effective/workflow.github_token",
            params={"scope": "workspace"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": "unknown_setting",
        "message": "Unknown setting: workflow.github_token.",
        "key": "workflow.github_token",
        "scope": "workspace",
        "details": {},
    }


@pytest.mark.asyncio
async def test_settings_write_to_unexposed_key_returns_setting_not_exposed():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.github_token": "raw-token"},
                "expected_versions": {},
                "reason": "attempt to mutate an unexposed field",
            },
        )

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "setting_not_exposed"
    assert body["key"] == "workflow.github_token"
    assert body["scope"] == "workspace"
    assert "raw-token" not in response.text


@pytest.mark.asyncio
async def test_effective_settings_endpoint_filters_by_scope():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/effective",
            params={"scope": "user"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "user"
    assert list(body["values"]) == ["integrations.github.token_ref"]


@pytest.mark.asyncio
async def test_effective_settings_endpoint_surfaces_db_read_failure(monkeypatch):
    class FailingSessionMaker:
        def __call__(self):
            raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(settings_router, "_should_attempt_settings_db", lambda: True)
    monkeypatch.setattr(db_base, "async_session_maker", FailingSessionMaker())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/effective",
            params={"scope": "workspace"},
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": "settings_db_unavailable",
        "message": "Settings persistence is unavailable.",
        "key": None,
        "scope": "workspace",
        "details": {},
    }


@pytest.mark.asyncio
async def test_effective_settings_endpoint_returns_structured_invalid_scope_error():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/effective",
            params={"scope": "organization"},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_scope"
    assert body["scope"] == "organization"
    assert body["details"]["allowed_scopes"] == [
        "operator",
        "system",
        "user",
        "workspace",
    ]


@pytest.mark.asyncio
async def test_patch_settings_invalid_scope_uses_settings_error_contract():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/organization",
            json={"changes": {"workflow.default_publish_mode": "branch"}},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_scope"
    assert body["scope"] == "organization"


@pytest.mark.asyncio
async def test_patch_workspace_setting_persists_override(settings_api_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
                "reason": "Use branch mode.",
            },
        )
        effective = await client.get(
            "/api/v1/settings/effective/workflow.default_publish_mode",
            params={"scope": "workspace"},
        )

    assert response.status_code == 200
    body = response.json()
    value = body["values"]["workflow.default_publish_mode"]
    assert value["value"] == "branch"
    assert value["source"] == "workspace_override"
    assert value["value_version"] == 1
    assert effective.json()["source"] == "workspace_override"


@pytest.mark.asyncio
async def test_patch_user_setting_wins_over_workspace_inheritance(settings_api_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"integrations.github.token_ref": "env://WORKSPACE_TOKEN"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        inherited = await client.get(
            "/api/v1/settings/effective/integrations.github.token_ref",
            params={"scope": "user"},
        )
        await client.patch(
            "/api/v1/settings/user",
            json={
                "changes": {"integrations.github.token_ref": "env://USER_TOKEN"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        user_value = await client.get(
            "/api/v1/settings/effective/integrations.github.token_ref",
            params={"scope": "user"},
        )

    assert inherited.json()["source"] == "workspace_override"
    assert inherited.json()["value"] == "env://WORKSPACE_TOKEN"
    assert user_value.json()["source"] == "user_override"
    assert user_value.json()["value"] == "env://USER_TOKEN"


@pytest.mark.asyncio
async def test_delete_reset_preserves_secret_and_audit(settings_api_db):
    async with settings_api_db() as session:
        session.add(
            ManagedSecret(
                slug="github-token",
                ciphertext="encrypted",
                details={"label": "GitHub"},
            )
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )
        response = await client.delete(
            "/api/v1/settings/workspace/workflow.default_publish_mode"
        )

    assert response.status_code == 200
    assert response.json()["source"] in {"config_or_default", "environment", "default"}
    async with settings_api_db() as session:
        secrets = (await session.execute(select(ManagedSecret))).scalars().all()
        assert len(secrets) == 1


@pytest.mark.asyncio
async def test_version_conflict_returns_error_and_does_not_partially_persist(settings_api_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {
                    "workflow.default_publish_mode": "branch",
                    "skills.canary_percent": 25,
                },
                "expected_versions": {
                    "workflow.default_publish_mode": 1,
                    "skills.canary_percent": 1,
                },
            },
        )
        conflict = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {
                    "workflow.default_publish_mode": "none",
                    "skills.canary_percent": 50,
                },
                "expected_versions": {
                    "workflow.default_publish_mode": 99,
                    "skills.canary_percent": 1,
                },
            },
        )
        publish_mode = await client.get(
            "/api/v1/settings/effective/workflow.default_publish_mode",
            params={"scope": "workspace"},
        )
        canary = await client.get(
            "/api/v1/settings/effective/skills.canary_percent",
            params={"scope": "workspace"},
        )

    assert conflict.status_code == 409
    assert conflict.json()["error"] == "version_conflict"
    assert publish_mode.json()["value"] == "branch"
    assert canary.json()["value"] == 25


@pytest.mark.asyncio
async def test_secret_ref_reference_allowed_but_raw_secret_rejected(settings_api_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        allowed = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"integrations.github.token_ref": "env://GITHUB_TOKEN"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        rejected = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"integrations.github.token_ref": "ghp_raw_plaintext"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )

    assert allowed.status_code == 200
    assert "env://GITHUB_TOKEN" in allowed.text
    assert rejected.status_code == 400
    assert rejected.json()["error"] == "invalid_setting_value"
    assert "ghp_raw_plaintext" not in rejected.text
