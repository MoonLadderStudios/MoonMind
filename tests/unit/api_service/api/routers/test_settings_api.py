from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.auth_providers import get_current_user
from api_service.api.routers import settings as settings_router
from api_service.db import base as db_base
from api_service.db.models import Base, ManagedSecret, SecretStatus, SettingsOverride
from api_service.main import app
from api_service.services.settings_catalog import (
    SettingMigrationRule,
    SettingsCatalogService,
)


SETTINGS_USER_DEP = get_current_user()


def _install_settings_migration_rules(monkeypatch, rules):
    service_cls = SettingsCatalogService

    def _factory(*args, **kwargs):
        kwargs.setdefault("migration_rules", rules)
        return service_cls(*args, **kwargs)

    monkeypatch.setattr(settings_router, "SettingsCatalogService", _factory)


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


@pytest.fixture
def settings_user_override():
    def _apply(*, permissions=(), is_superuser=False, user_id=None, workspace_id=None):
        user = SimpleNamespace(
            id=user_id,
            email="settings-user@example.com",
            is_superuser=is_superuser,
            settings_permissions=set(permissions),
            workspace_id=workspace_id,
        )
        app.dependency_overrides[SETTINGS_USER_DEP] = lambda: user
        return user

    try:
        yield _apply
    finally:
        app.dependency_overrides.pop(SETTINGS_USER_DEP, None)


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
    assert descriptor["apply_mode"] == "next_task"
    assert descriptor["activation_state"] == "active"
    assert descriptor["active"] is True
    assert descriptor["pending_value"] is None
    assert descriptor["completion_guidance"] == (
        "New tasks will use this value when they are created."
    )
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
async def test_patch_deprecated_setting_key_is_rejected_without_echoing_value(
    settings_api_db,
    monkeypatch,
):
    _install_settings_migration_rules(
        monkeypatch,
        (
            SettingMigrationRule(
                old_key="test.removed_token",
                state="removed",
                message="test.removed_token was removed.",
            ),
        ),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"test.removed_token": "ghp_should_never_echo"},
                "expected_versions": {"test.removed_token": 1},
            },
        )

    assert response.status_code == 423
    assert response.json()["error"] == "read_only_setting"
    assert response.json()["key"] == "test.removed_token"
    assert "ghp_should_never_echo" not in response.text


@pytest.mark.asyncio
async def test_effective_setting_endpoint_resolves_renamed_override(
    settings_api_db,
    monkeypatch,
):
    _install_settings_migration_rules(
        monkeypatch,
        (
            SettingMigrationRule(
                old_key="workflow.legacy_publish_mode",
                new_key="workflow.default_publish_mode",
                state="renamed",
                message="workflow.legacy_publish_mode was renamed.",
            ),
        ),
    )
    async with settings_api_db() as session:
        session.add(
            SettingsOverride(
                scope="workspace",
                key="workflow.legacy_publish_mode",
                value_json="branch",
                value_version=5,
            )
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/effective/workflow.default_publish_mode",
            params={"scope": "workspace"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["value"] == "branch"
    assert body["source"] == "migrated_workspace_override"
    assert body["value_version"] == 5
    assert body["diagnostics"][0]["code"] == "setting_renamed_override"


@pytest.mark.asyncio
async def test_settings_diagnostics_include_deprecated_override_without_plaintext(
    settings_api_db,
    monkeypatch,
):
    _install_settings_migration_rules(
        monkeypatch,
        (
            SettingMigrationRule(
                old_key="workflow.removed_secret",
                state="removed",
                message="workflow.removed_secret was removed.",
            ),
        ),
    )
    async with settings_api_db() as session:
        session.add(
            SettingsOverride(
                scope="workspace",
                key="workflow.removed_secret",
                value_json="ghp_removed_plaintext",
                value_version=7,
            )
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/diagnostics",
            params={"scope": "workspace"},
        )

    assert response.status_code == 200
    value = response.json()["values"]["workflow.removed_secret"]
    assert value["source"] == "deprecated_override"
    assert value["diagnostics"][0]["code"] == "setting_deprecated_override"
    assert value["diagnostics"][0]["details"]["value_version"] == 7
    assert "ghp_removed_plaintext" not in response.text


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
    assert list(body["values"]) == [
        "integrations.github.token_ref",
        "workflow.default_provider_profile_ref",
    ]


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


@pytest.mark.asyncio
async def test_settings_catalog_requires_catalog_read_permission(settings_user_override):
    settings_user_override(permissions={"settings.effective.read"})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        denied = await client.get("/api/v1/settings/catalog")

    assert denied.status_code == 403
    assert denied.json()["error"] == "permission_denied"
    assert denied.json()["details"]["required_permission"] == "settings.catalog.read"


@pytest.mark.asyncio
async def test_settings_patch_requires_matching_scope_write_permission(
    settings_api_db,
    settings_user_override,
):
    settings_user_override(permissions={"settings.user.write"})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        denied = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
                "descriptor": {"audit": {"redact": False}},
                "permissions": ["settings.workspace.write"],
            },
        )

    assert denied.status_code == 403
    assert denied.json()["details"]["required_permission"] == "settings.workspace.write"


@pytest.mark.asyncio
async def test_settings_audit_endpoint_redacts_without_secret_metadata_permission(
    settings_api_db,
    settings_user_override,
):
    settings_user_override(
        permissions={"settings.workspace.write", "settings.audit.read"}
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"integrations.github.token_ref": "env://GITHUB_TOKEN"},
                "expected_versions": {"integrations.github.token_ref": 1},
                "reason": "configure token ref",
            },
        )
        audit = await client.get(
            "/api/v1/settings/audit",
            params={"key": "integrations.github.token_ref"},
        )

    assert audit.status_code == 200
    item = audit.json()["items"][0]
    assert item["key"] == "integrations.github.token_ref"
    assert item["apply_mode"] == "next_launch"
    assert item["affected_systems"] == ["github", "integrations"]
    assert item["new_value"] is None
    assert item["redacted"] is True
    assert "env://GITHUB_TOKEN" not in audit.text


@pytest.mark.asyncio
async def test_settings_audit_endpoint_exposes_secret_ref_with_metadata_permission(
    settings_api_db,
    settings_user_override,
):
    settings_user_override(
        permissions={
            "settings.workspace.write",
            "settings.audit.read",
            "secrets.metadata.read",
        }
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"integrations.github.token_ref": "env://GITHUB_TOKEN"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        audit = await client.get(
            "/api/v1/settings/audit",
            params={"key": "integrations.github.token_ref"},
        )

    assert audit.status_code == 200
    assert audit.json()["items"][0]["new_value"] == "env://GITHUB_TOKEN"


@pytest.mark.asyncio
async def test_settings_audit_endpoint_scopes_rows_to_current_workspace_and_user(
    settings_api_db,
    settings_user_override,
):
    workspace_id = uuid4()
    user_id = uuid4()
    settings_user_override(
        permissions={
            "settings.user.write",
            "settings.workspace.write",
            "settings.audit.read",
        },
        user_id=user_id,
        workspace_id=workspace_id,
    )

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
            "/api/v1/settings/user",
            json={
                "changes": {"workflow.default_provider_profile_ref": "codex-default"},
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )

    other_workspace = uuid4()
    other_user = uuid4()
    settings_user_override(
        permissions={
            "settings.user.write",
            "settings.workspace.write",
            "settings.audit.read",
        },
        user_id=other_user,
        workspace_id=other_workspace,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_publish_mode": "none"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )
        await client.patch(
            "/api/v1/settings/user",
            json={
                "changes": {"workflow.default_provider_profile_ref": "other-profile"},
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )

    settings_user_override(
        permissions={"settings.audit.read"},
        user_id=user_id,
        workspace_id=workspace_id,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        audit = await client.get("/api/v1/settings/audit")
        user_audit = await client.get(
            "/api/v1/settings/audit",
            params={"scope": "user"},
        )

    assert audit.status_code == 200
    values = {item["new_value"] for item in audit.json()["items"]}
    assert values == {"branch", "codex-default"}
    assert user_audit.status_code == 200
    user_items = user_audit.json()["items"]
    assert len(user_items) == 1
    assert user_items[0]["new_value"] == "codex-default"
    assert user_items[0]["actor_user_id"] == str(user_id)


@pytest.mark.asyncio
async def test_settings_diagnostics_endpoint_returns_actionable_sanitized_output(
    settings_api_db,
    settings_user_override,
):
    settings_user_override(
        permissions={"settings.workspace.write", "settings.effective.read"}
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"integrations.github.token_ref": "db://missing-token"},
                "expected_versions": {"integrations.github.token_ref": 1},
                "reason": "select managed secret",
            },
        )
        response = await client.get(
            "/api/v1/settings/diagnostics",
            params={"scope": "workspace", "key": "integrations.github.token_ref"},
        )

    assert response.status_code == 200
    value = response.json()["values"]["integrations.github.token_ref"]
    assert value["source"] == "workspace_override"
    assert value["recent_change"]["reason"] == "select managed secret"
    assert value["apply_mode"] == "next_launch"
    assert value["activation_state"] == "active"
    assert value["active"] is True
    assert value["completion_guidance"] == (
        "New launches will use this value the next time they start."
    )
    assert value["affected_process_or_worker"] == "github, integrations"
    assert value["diagnostics"][0]["code"] == "unresolved_secret_ref"
    assert value["diagnostics"][0]["details"]["launch_blocker"] is True
    assert "missing-token" not in response.text


@pytest.mark.asyncio
async def test_settings_diagnostics_endpoint_falls_back_without_db(monkeypatch):
    class FailingSessionMaker:
        def __call__(self):
            raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(settings_router, "_should_attempt_settings_db", lambda: True)
    monkeypatch.setattr(db_base, "async_session_maker", FailingSessionMaker())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/diagnostics",
            params={"scope": "workspace", "key": "workflow.default_publish_mode"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["values"]["workflow.default_publish_mode"]["source"] in {
        "config_or_default",
        "environment",
    }


@pytest.mark.asyncio
async def test_db_secret_ref_catalog_diagnostics_report_missing_and_inactive(settings_api_db):
    async with settings_api_db() as session:
        session.add(
            ManagedSecret(
                slug="active-github-token",
                ciphertext="active-plaintext",
                status=SecretStatus.ACTIVE,
                details={},
            )
        )
        session.add(
            ManagedSecret(
                slug="disabled-github-token",
                ciphertext="disabled-plaintext",
                status=SecretStatus.DISABLED,
                details={},
            )
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        active = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {
                    "integrations.github.token_ref": "db://active-github-token"
                },
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        disabled = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {
                    "integrations.github.token_ref": "db://disabled-github-token"
                },
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        missing = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {
                    "integrations.github.token_ref": "db://missing-github-token"
                },
                "expected_versions": {"integrations.github.token_ref": 2},
            },
        )

    active_value = active.json()["values"]["integrations.github.token_ref"]
    disabled_value = disabled.json()["values"]["integrations.github.token_ref"]
    missing_value = missing.json()["values"]["integrations.github.token_ref"]

    assert active.status_code == 200
    assert active_value["diagnostics"] == []
    assert "active-plaintext" not in active.text
    assert disabled.status_code == 200
    assert disabled_value["pending_value"] == "db://disabled-github-token"
    assert disabled_value["diagnostics"] == [
        {
            "code": "unresolved_secret_ref",
            "message": (
                "integrations.github.token_ref references a managed secret "
                "that is disabled."
            ),
                "severity": "error",
                "details": {
                    "ref_scheme": "db",
                    "status": "disabled",
                    "launch_blocker": True,
                },
        }
    ]
    assert "disabled-plaintext" not in disabled.text
    assert missing.status_code == 200
    assert missing_value["pending_value"] == "db://missing-github-token"
    assert missing_value["diagnostics"] == [
        {
            "code": "unresolved_secret_ref",
            "message": (
                "integrations.github.token_ref references a managed secret "
                "that does not exist."
            ),
                "severity": "error",
                "details": {
                    "ref_scheme": "db",
                    "status": "missing",
                    "launch_blocker": True,
            },
        }
    ]
