from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import Base, ManagedSecret, SecretStatus, SettingsOverride
from api_service.main import app

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

SETTINGS_USER_DEP = get_current_user()


@pytest_asyncio.fixture
async def secret_usage_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/secret-usage.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    original = db_base.async_session_maker
    db_base.async_session_maker = session_maker
    user = SimpleNamespace(
        id=uuid4(),
        email="secret-usage@example.com",
        is_superuser=True,
        settings_permissions={
            "settings.catalog.read",
            "settings.effective.read",
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
        await engine.dispose()


async def test_secret_usage_view_reports_settings_consumer_without_plaintext(
    secret_usage_db,
):
    async with secret_usage_db() as session:
        session.add(
            ManagedSecret(
                slug="github-pat-main",
                ciphertext="ghp_usage_plaintext",
                status=SecretStatus.ACTIVE,
                details={},
            )
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        stored = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"integrations.github.token_ref": "db://github-pat-main"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        usage = await client.get("/api/v1/secrets/github-pat-main/usage")

    assert stored.status_code == 200
    assert usage.status_code == 200
    body = usage.json()
    assert body["secretRef"] == "db://github-pat-main"
    assert body["usages"] == [
        {
            "consumerType": "setting_override",
            "objectName": "Workspace setting integrations.github.token_ref",
            "reference": "db://github-pat-main",
            "scope": "workspace",
            "settingKey": "integrations.github.token_ref",
        }
    ]
    assert "ghp_usage_plaintext" not in usage.text


async def test_secret_usage_view_reports_empty_and_missing_without_plaintext(
    secret_usage_db,
):
    async with secret_usage_db() as session:
        session.add(
            ManagedSecret(
                slug="unused-secret",
                ciphertext="unused_plaintext",
                status=SecretStatus.ACTIVE,
                details={},
            )
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        unused = await client.get("/api/v1/secrets/unused-secret/usage")
        missing = await client.get("/api/v1/secrets/missing-secret/usage")

    assert unused.status_code == 200
    assert unused.json() == {
        "secretRef": "db://unused-secret",
        "usages": [],
        "diagnostics": [],
    }
    assert "unused_plaintext" not in unused.text
    assert missing.status_code == 200
    assert missing.json()["secretRef"] == "db://missing-secret"
    assert missing.json()["usages"] == []
    assert missing.json()["diagnostics"][0]["code"] == "secret_ref_unresolved"
    assert "plaintext" not in missing.text


async def test_secret_usage_view_excludes_other_workspace_consumers(
    secret_usage_db,
):
    async with secret_usage_db() as session:
        session.add(
            ManagedSecret(
                slug="github-pat-main",
                ciphertext="ghp_usage_plaintext",
                status=SecretStatus.ACTIVE,
                details={},
            )
        )
        session.add(
            SettingsOverride(
                scope="workspace",
                workspace_id=uuid4(),
                key="integrations.github.token_ref",
                value_json="db://github-pat-main",
            )
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        usage = await client.get("/api/v1/secrets/github-pat-main/usage")

    assert usage.status_code == 200
    assert usage.json() == {
        "secretRef": "db://github-pat-main",
        "usages": [],
        "diagnostics": [],
    }
