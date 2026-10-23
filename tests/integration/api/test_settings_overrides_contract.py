from __future__ import annotations


import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.auth_providers import get_instance_access
from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ManagedSecret,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
    SecretStatus,
)
from api_service.main import app
from moonmind.security.operator_admission import OperatorAdmission

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

SETTINGS_USER_DEP = get_instance_access


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
    # Admitted operator: no person, account row or synthetic subject.
    app.dependency_overrides[SETTINGS_USER_DEP] = lambda: OperatorAdmission(
        via="loopback"
    )
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
            "/api/v1/settings/effective/integrations.github.token_ref"
        )
        instance = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"integrations.github.token_ref": "env://INSTANCE_TOKEN"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        reset = await client.delete("/api/v1/settings/integrations.github.token_ref")
        absent_reset = await client.delete(
            "/api/v1/settings/integrations.github.token_ref"
        )

    inherited_sources = {"config_file", "environment", "default", "no_default"}
    assert inherited.status_code == 200
    assert inherited.json()["source"] in inherited_sources
    assert instance.status_code == 200
    assert instance.json()["values"]["integrations.github.token_ref"]["source"] == (
        "instance_override"
    )
    event = instance.json()["change_events"][0]
    assert event["event_type"] == "setting_changed"
    assert event["key"] == "integrations.github.token_ref"
    assert "scope" not in event
    assert event["source"] == "instance_override"
    assert event["apply_mode"] == "next_launch"
    assert event["affected_systems"] == ["github", "integrations"]
    assert event["refresh_targets"] == ["settings_catalog"]
    assert event["changed_at"]
    assert reset.status_code == 200
    assert reset.json()["source"] in inherited_sources
    assert absent_reset.status_code == 200
    assert absent_reset.json()["source"] == reset.json()["source"]


async def test_settings_override_contract_rejects_stale_unsafe_and_oversized_writes(
    settings_contract_db,
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings",
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
            "/api/v1/settings",
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
            "/api/v1/settings",
            json={
                "changes": {"workflow.default_provider_profile_ref": "x" * 20000},
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )
        unsafe = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {
                    "workflow.default_provider_profile_ref": "oauth_session_blob={...}"
                },
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )
        publish_mode = await client.get(
            "/api/v1/settings/effective/workflow.default_publish_mode",
        )
        canary = await client.get(
            "/api/v1/settings/effective/skills.canary_percent",
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
            "/api/v1/settings",
            json={
                "changes": {"integrations.github.token_ref": "db://disabled-token"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        missing_profile = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"workflow.default_provider_profile_ref": "missing-profile"},
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )
        invalid_combo = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"workflow.default_runtime": "unsupported-runtime"},
                "expected_versions": {"workflow.default_runtime": 1},
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


async def test_mm661_settings_override_contract_rejects_not_ready_provider_profile_without_plaintext(
    settings_contract_db,
):
    async with settings_contract_db() as session:
        session.add(
            ManagedSecret(
                slug="not-ready-profile-secret",
                ciphertext="not-ready-secret-plaintext",
                status=SecretStatus.DISABLED,
                details={},
            )
        )
        session.add(
            ManagedAgentProviderProfile(
                profile_id="enabled-not-ready-profile",
                runtime_id="codex",
                provider_id="openai",
                credential_source=ProviderCredentialSource.SECRET_REF,
                runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                secret_refs={"provider_api_key": "db://not-ready-profile-secret"},
                enabled=True,
            )
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {
                    "workflow.default_provider_profile_ref": "enabled-not-ready-profile"
                },
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )

    assert response.status_code == 400
    assert response.json()["details"]["code"] == "provider_profile_not_ready"
    assert "not-ready-secret-plaintext" not in response.text
    assert "enabled-not-ready-profile" in response.text
