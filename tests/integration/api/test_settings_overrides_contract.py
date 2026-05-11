from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import settings as settings_router
from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import Base
from api_service.main import app

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

SETTINGS_USER_DEP = get_current_user()


@pytest.fixture
def settings_contract_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/settings-contract.db"
    )

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio_run = __import__("asyncio").run
    asyncio_run(_setup())
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    original = db_base.async_session_maker
    db_base.async_session_maker = session_maker
    user = SimpleNamespace(
        id=uuid4(),
        email="settings-contract@example.com",
        is_superuser=True,
        settings_permissions={
            "settings.catalog.read",
            "settings.effective.read",
            "settings.user.write",
            "settings.workspace.write",
        },
        workspace_id=uuid4(),
    )
    app.dependency_overrides[SETTINGS_USER_DEP] = lambda: user
    try:
        yield session_maker
    finally:
        app.dependency_overrides.pop(SETTINGS_USER_DEP, None)
        db_base.async_session_maker = original
        asyncio_run(engine.dispose())


async def test_settings_override_contract_round_trips_and_resets(
    settings_contract_db,
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        inherited = await client.get(
            "/api/v1/settings/effective/integrations.github.token_ref",
            params={"scope": "user"},
        )
        workspace = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"integrations.github.token_ref": "env://WORKSPACE_TOKEN"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        user = await client.patch(
            "/api/v1/settings/user",
            json={
                "changes": {"integrations.github.token_ref": "env://USER_TOKEN"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        workspace_reset = await client.delete(
            "/api/v1/settings/workspace/integrations.github.token_ref"
        )
        user_after_workspace_reset = await client.get(
            "/api/v1/settings/effective/integrations.github.token_ref",
            params={"scope": "user"},
        )
        user_reset = await client.delete(
            "/api/v1/settings/user/integrations.github.token_ref"
        )
        absent_reset = await client.delete(
            "/api/v1/settings/user/integrations.github.token_ref"
        )

    assert inherited.status_code == 200
    assert inherited.json()["source"] in {"config_or_default", "environment", "default"}
    assert workspace.status_code == 200
    assert workspace.json()["values"]["integrations.github.token_ref"]["source"] == (
        "workspace_override"
    )
    assert user.status_code == 200
    assert user.json()["values"]["integrations.github.token_ref"]["source"] == (
        "user_override"
    )
    assert workspace_reset.status_code == 200
    assert user_after_workspace_reset.json()["value"] == "env://USER_TOKEN"
    assert user_after_workspace_reset.json()["source"] == "user_override"
    assert user_reset.status_code == 200
    assert user_reset.json()["source"] in {"config_or_default", "environment", "default"}
    assert absent_reset.status_code == 200
    assert absent_reset.json()["source"] in {
        "config_or_default",
        "environment",
        "default",
    }


async def test_settings_override_contract_rejects_stale_unsafe_and_oversized_writes(
    settings_contract_db,
):
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
        oversized = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_provider_profile_ref": "x" * 20000},
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )
        unsafe = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {
                    "workflow.default_provider_profile_ref": "oauth_session_blob={...}"
                },
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
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
    assert oversized.status_code == 400
    assert oversized.json()["error"] == "invalid_setting_value"
    assert "x" * 64 not in oversized.text
    assert unsafe.status_code == 400
    assert unsafe.json()["error"] == "invalid_setting_value"
    assert "oauth_session_blob" not in unsafe.text
    assert publish_mode.json()["value"] == "branch"
    assert canary.json()["value"] == 25
