from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import Base, ManagedAgentProviderProfile, ManagedSecret, SecretStatus
from api_service.main import app

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

SETTINGS_USER_DEP = get_current_user()


@pytest_asyncio.fixture
async def settings_contract_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/settings-contract.db"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
        await engine.dispose()


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
    assert inherited.json()["source"] in {"config_file", "environment", "default"}
    assert workspace.status_code == 200
    assert workspace.json()["values"]["integrations.github.token_ref"]["source"] == (
        "workspace_override"
    )
    workspace_event = workspace.json()["change_events"][0]
    assert workspace_event["event_type"] == "setting_changed"
    assert workspace_event["key"] == "integrations.github.token_ref"
    assert workspace_event["scope"] == "workspace"
    assert workspace_event["source"] == "workspace_override"
    assert workspace_event["apply_mode"] == "next_launch"
    assert workspace_event["affected_systems"] == ["github", "integrations"]
    assert workspace_event["refresh_targets"] == ["settings_catalog"]
    assert workspace_event["changed_at"]
    assert user.status_code == 200
    assert user.json()["values"]["integrations.github.token_ref"]["source"] == (
        "user_override"
    )
    assert workspace_reset.status_code == 200
    assert user_after_workspace_reset.json()["value"] == "env://USER_TOKEN"
    assert user_after_workspace_reset.json()["source"] == "user_override"
    assert user_reset.status_code == 200
    assert user_reset.json()["source"] in {"config_file", "environment", "default"}
    assert absent_reset.status_code == 200
    assert absent_reset.json()["source"] in {
        "config_file",
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


async def test_mm656_settings_override_contract_rejects_invalid_references_and_policy(
    settings_contract_db,
):
    async with settings_contract_db() as session:
        session.add(
            ManagedSecret(
                slug="disabled-token",
                ciphertext="disabled-secret-plaintext",
                status=SecretStatus.DISABLED,
                details={},
            )
        )
        session.add(
            ManagedAgentProviderProfile(
                profile_id="disabled-profile",
                runtime_id="codex",
                provider_id="openai",
                enabled=False,
            )
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        invalid_secret = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"integrations.github.token_ref": "db://disabled-token"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        missing_profile = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_provider_profile_ref": "missing-profile"},
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )
        invalid_combo = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_task_runtime": "unsupported-runtime"},
                "expected_versions": {"workflow.default_task_runtime": 1},
            },
        )

    assert invalid_secret.status_code == 400
    assert invalid_secret.json()["details"]["code"] == "secret_ref_unresolved"
    assert invalid_secret.json()["details"]["boundary"] == "write_request"
    assert "disabled-secret-plaintext" not in invalid_secret.text
    assert missing_profile.status_code == 400
    assert missing_profile.json()["details"]["code"] == "provider_profile_not_found"
    assert invalid_combo.status_code == 400
    assert invalid_combo.json()["details"]["code"] in {
        "enum_value_invalid",
        "runtime_policy_denied",
    }
