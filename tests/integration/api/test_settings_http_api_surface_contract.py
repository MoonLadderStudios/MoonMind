from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import Base, SettingsAuditEvent, SettingsOverride
from api_service.main import app

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

SETTINGS_USER_DEP = get_current_user()


@pytest_asyncio.fixture
async def settings_http_api_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/settings-http.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    original = db_base.async_session_maker
    db_base.async_session_maker = session_maker
    user = SimpleNamespace(
        id=uuid4(),
        email="settings-http-api@example.com",
        is_superuser=True,
        settings_permissions={
            "settings.catalog.read",
            "settings.effective.read",
            "settings.user.write",
            "settings.workspace.write",
            "settings.audit.read",
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


async def _row_counts(session_maker) -> tuple[int, int]:
    async with session_maker() as session:
        overrides = (await session.execute(select(SettingsOverride))).scalars().all()
        audits = (await session.execute(select(SettingsAuditEvent))).scalars().all()
    return len(overrides), len(audits)


async def test_mm657_catalog_contract_exposes_all_documented_sections(
    settings_http_api_db,
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        provider = await client.get(
            "/api/v1/settings/catalog",
            params={"section": "providers-secrets", "scope": "user"},
        )
        user_workspace = await client.get(
            "/api/v1/settings/catalog",
            params={"section": "user-workspace", "scope": "workspace"},
        )
        operations = await client.get(
            "/api/v1/settings/catalog",
            params={"section": "operations", "scope": "workspace"},
        )

    assert provider.status_code == 200
    assert provider.json()["section"] == "providers-secrets"
    assert provider.json()["categories"]
    assert user_workspace.status_code == 200
    assert user_workspace.json()["section"] == "user-workspace"
    assert user_workspace.json()["categories"]
    assert operations.status_code == 200
    assert operations.json()["section"] == "operations"
    assert operations.json()["categories"]


async def test_mm657_validate_and_preview_do_not_commit_or_audit(
    settings_http_api_db,
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        validate = await client.post(
            "/api/v1/settings/validate",
            json={
                "scope": "workspace",
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )
        preview = await client.post(
            "/api/v1/settings/preview",
            json={
                "scope": "workspace",
                "changes": {"skills.policy_mode": "allowlist"},
                "expected_versions": {"skills.policy_mode": 1},
            },
        )
        publish_mode = await client.get(
            "/api/v1/settings/effective/workflow.default_publish_mode",
            params={"scope": "workspace"},
        )
        skill_policy = await client.get(
            "/api/v1/settings/effective/skills.policy_mode",
            params={"scope": "workspace"},
        )

    assert validate.status_code == 200
    assert validate.json()["accepted"] is True
    assert preview.status_code == 200
    assert preview.json()["accepted"] is True
    assert preview.json()["diffs"][0]["after"]["value"] == "allowlist"
    assert preview.json()["reload_requirements"][0]["key"] == "skills.policy_mode"
    assert publish_mode.json()["value"] != "branch"
    assert skill_policy.json()["value"] != "allowlist"
    assert await _row_counts(settings_http_api_db) == (0, 0)


async def test_mm657_all_api_families_are_contract_covered(settings_http_api_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        catalog = await client.get("/api/v1/settings/catalog")
        effective_all = await client.get("/api/v1/settings/effective")
        effective_one = await client.get(
            "/api/v1/settings/effective/workflow.default_publish_mode"
        )
        validate = await client.post(
            "/api/v1/settings/validate",
            json={
                "scope": "workspace",
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )
        preview = await client.post(
            "/api/v1/settings/preview",
            json={
                "scope": "workspace",
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )
        update = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )
        reset = await client.delete(
            "/api/v1/settings/workspace/workflow.default_publish_mode"
        )
        audit = await client.get("/api/v1/settings/audit")

    assert catalog.status_code == 200
    assert effective_all.status_code == 200
    assert effective_one.status_code == 200
    assert validate.status_code == 200
    assert preview.status_code == 200
    assert update.status_code == 200
    assert reset.status_code == 200
    assert audit.status_code == 200
    assert audit.json()["items"]


async def test_mm657_validate_preview_structured_errors_and_redaction(
    settings_http_api_db,
):
    limited_user = SimpleNamespace(
        id=uuid4(),
        email="limited-settings-http-api@example.com",
        is_superuser=False,
        settings_permissions={"settings.user.write"},
        workspace_id=uuid4(),
    )
    app.dependency_overrides[SETTINGS_USER_DEP] = lambda: limited_user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        denied = await client.post(
            "/api/v1/settings/preview",
            json={
                "scope": "workspace",
                "changes": {"workflow.default_publish_mode": "branch"},
            },
        )

    privileged_user = SimpleNamespace(
        id=uuid4(),
        email="privileged-settings-http-api@example.com",
        is_superuser=True,
        settings_permissions={"settings.workspace.write"},
        workspace_id=uuid4(),
    )
    app.dependency_overrides[SETTINGS_USER_DEP] = lambda: privileged_user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        missing_ref = await client.post(
            "/api/v1/settings/preview",
            json={
                "scope": "workspace",
                "changes": {"integrations.github.token_ref": "db://missing-token"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        invalid_key = await client.post(
            "/api/v1/settings/validate",
            json={
                "scope": "workspace",
                "changes": {"workflow.github_token": "env://TOKEN"},
                "expected_versions": {"workflow.github_token": 1},
            },
        )
        secret_ref = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"integrations.github.token_ref": "db://missing-token"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        provider_profile = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_provider_profile_ref": "missing-profile"},
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )
        scope_not_allowed = await client.post(
            "/api/v1/settings/validate",
            json={
                "scope": "user",
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )
        requires_confirmation = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.operation_mode": "maintenance"},
                "expected_versions": {"workflow.operation_mode": 1},
            },
        )

    assert denied.status_code == 403
    assert denied.json()["error"] == "permission_denied"
    assert missing_ref.status_code == 200
    assert missing_ref.json()["accepted"] is False
    assert missing_ref.json()["issues_by_key"]["integrations.github.token_ref"][0][
        "code"
    ] == "secret_ref_unresolved"
    assert missing_ref.json()["diffs"][0]["redacted"] is True
    assert "missing-token" not in missing_ref.text
    assert invalid_key.status_code == 404
    assert invalid_key.json()["error"] == "setting_not_exposed"
    assert secret_ref.status_code == 400
    assert secret_ref.json()["error"] == "secret_ref_not_resolvable"
    assert "missing-token" not in secret_ref.text
    assert provider_profile.status_code == 400
    assert provider_profile.json()["error"] == "provider_profile_not_found"
    assert scope_not_allowed.status_code == 400
    assert scope_not_allowed.json()["error"] == "scope_not_allowed"
    assert requires_confirmation.status_code == 428
    assert requires_confirmation.json()["error"] == "requires_confirmation"
