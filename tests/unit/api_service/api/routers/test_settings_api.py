import json
import logging
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.auth_providers import get_instance_access
from api_service.api.routers import settings as settings_router
from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ManagedSecret,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    SecretStatus,
    SettingsAuditEvent,
    SettingsOverride,
)
from api_service.main import app
from api_service.services.single_user_conversion import run_guarded_upgrade
from moonmind.security.operator_admission import OperatorAdmission
from api_service.services.settings_catalog import (
    _CATALOG_MIGRATION_RULES,
    SettingMigrationRule,
    SettingRegistryEntry,
    SettingsCatalogService,
)


SETTINGS_USER_DEP = get_instance_access


def _install_settings_migration_rules(monkeypatch, rules):
    service_cls = SettingsCatalogService
    merged_rules = (*_CATALOG_MIGRATION_RULES, *rules)

    def _factory(*args, **kwargs):
        kwargs.setdefault("migration_rules", merged_rules)
        return service_cls(*args, **kwargs)

    monkeypatch.setattr(settings_router, "SettingsCatalogService", _factory)


async def _settings_override_count(session_maker) -> int:
    async with session_maker() as session:
        rows = (await session.execute(select(SettingsOverride))).scalars().all()
        return len(rows)


async def _settings_audit_count(session_maker) -> int:
    async with session_maker() as session:
        rows = (await session.execute(select(SettingsAuditEvent))).scalars().all()
        return len(rows)


def _assert_settings_error_envelope(
    body,
    *,
    error: str,
    key: str | None = None,
) -> None:
    assert body["error"] == error
    assert "message" in body and body["message"]
    assert body["key"] == key
    assert "scope" not in body
    assert isinstance(body["details"], dict)


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


@pytest.fixture(autouse=True)
def _default_settings_test_principal():
    """Route-logic tests run as admitted operator without a person."""
    user = OperatorAdmission(via="loopback")
    missing = object()
    previous = app.dependency_overrides.get(SETTINGS_USER_DEP, missing)
    app.dependency_overrides[SETTINGS_USER_DEP] = lambda: user
    try:
        yield user
    finally:
        if previous is missing:
            app.dependency_overrides.pop(SETTINGS_USER_DEP, None)
        else:
            app.dependency_overrides[SETTINGS_USER_DEP] = previous


@pytest.mark.asyncio
async def test_settings_catalog_endpoint_returns_grouped_descriptors():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/catalog",
            params={"section": "user-workspace"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["section"] == "user-workspace"
    descriptor = next(
        item
        for item in body["categories"]["Workflow"]
        if item["key"] == "workflow.default_runtime"
    )
    assert descriptor["type"] == "enum"
    assert descriptor["ui"] == "select"
    assert descriptor["source"] in {"config_file", "environment"}
    assert descriptor["apply_mode"] == "next_workflow"
    assert descriptor["activation_state"] == "active"
    assert descriptor["active"] is True
    assert descriptor["pending_value"] is None
    assert descriptor["completion_guidance"] == (
        "New workflows will use this value when they are created."
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
            "/api/v1/settings",
            json={
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )
        await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"workflow.default_publish_mode": "none"},
                "expected_versions": {"workflow.default_publish_mode": 2},
            },
        )
        response = await client.get(
            "/api/v1/settings/catalog",
            params={"section": "user-workspace"},
        )

    assert response.status_code == 200
    descriptor = next(
        item
        for item in response.json()["categories"]["Workflow"]
        if item["key"] == "workflow.default_publish_mode"
    )
    assert descriptor["effective_value"] == "none"
    assert descriptor["source"] == "instance_override"
    assert descriptor["value_version"] == 3


@pytest.mark.asyncio
async def test_effective_setting_endpoint_returns_structured_unknown_key_error():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/effective/workflow.github_token",
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": "unknown_setting",
        "message": "Unknown setting: workflow.github_token.",
        "key": "workflow.github_token",
        "details": {},
    }


@pytest.mark.asyncio
async def test_settings_write_to_unexposed_key_returns_setting_not_exposed():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings",
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
            "/api/v1/settings",
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
        )

    assert response.status_code == 200
    body = response.json()
    assert body["value"] == "branch"
    assert body["source"] == "instance_override"
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
        )

    assert response.status_code == 200
    value = response.json()["values"]["workflow.removed_secret"]
    assert value["source"] == "deprecated_override"
    assert value["diagnostics"][0]["code"] == "setting_deprecated_override"
    assert value["diagnostics"][0]["details"]["value_version"] == 7
    assert "ghp_removed_plaintext" not in response.text


@pytest.mark.asyncio
async def test_effective_settings_endpoint_returns_one_instance_configuration():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/v1/settings/effective")

    assert response.status_code == 200
    body = response.json()
    assert "scope" not in body
    assert {
        "integrations.github.token_ref",
        "workflow.default_provider_profile_ref",
        "workflow.default_runtime",
    } <= set(body["values"])


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
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": "settings_db_unavailable",
        "message": "Settings persistence is unavailable.",
        "key": None,
        "details": {},
    }


@pytest.mark.asyncio
async def test_retired_scope_write_path_persists_nothing(settings_api_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={"changes": {"workflow.default_publish_mode": "branch"}},
        )
    async with settings_api_db() as session:
        rows = (await session.execute(select(SettingsOverride))).scalars().all()

    assert response.status_code == 405
    assert rows == []


@pytest.mark.asyncio
async def test_patch_workspace_setting_persists_override(settings_api_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
                "reason": "Use branch mode.",
            },
        )
        effective = await client.get(
            "/api/v1/settings/effective/workflow.default_publish_mode",
        )

    assert response.status_code == 200
    body = response.json()
    value = body["values"]["workflow.default_publish_mode"]
    assert value["value"] == "branch"
    assert value["source"] == "instance_override"
    assert value["value_version"] == 2
    assert effective.json()["source"] == "instance_override"


@pytest.mark.asyncio
async def test_clients_share_committed_configuration_and_conflict_on_stale_version(
    settings_api_db,
):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as first, AsyncClient(transport=transport, base_url="http://testserver") as second:
        stale = (
            await second.get("/api/v1/settings/effective/integrations.github.token_ref")
        ).json()
        written = await first.patch(
            "/api/v1/settings",
            json={
                "changes": {"integrations.github.token_ref": "env://INSTANCE_TOKEN"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        seen = await second.get(
            "/api/v1/settings/effective/integrations.github.token_ref"
        )
        updated = await first.patch(
            "/api/v1/settings",
            json={
                "changes": {"integrations.github.token_ref": "env://NEWER_TOKEN"},
                "expected_versions": {"integrations.github.token_ref": seen.json()["value_version"]},
            },
        )
        conflict = await second.patch(
            "/api/v1/settings",
            json={
                "changes": {"integrations.github.token_ref": "env://STALE_TOKEN"},
                "expected_versions": {
                    "integrations.github.token_ref": seen.json()["value_version"]
                },
            },
        )
        final = await second.get(
            "/api/v1/settings/effective/integrations.github.token_ref"
        )

    assert stale["source"] != "instance_override"
    assert written.status_code == 200
    assert seen.json()["source"] == "instance_override"
    assert seen.json()["value"] == "env://INSTANCE_TOKEN"
    assert updated.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "version_conflict"
    assert final.json()["value"] == "env://NEWER_TOKEN"
    assert final.json()["value_version"] == 3


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
            "/api/v1/settings",
            json={
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )
        response = await client.delete(
            "/api/v1/settings/workflow.default_publish_mode"
        )

    assert response.status_code == 200
    assert response.json()["source"] in {"config_file", "environment", "default"}
    async with settings_api_db() as session:
        secrets = (await session.execute(select(ManagedSecret))).scalars().all()
        assert len(secrets) == 1


@pytest.mark.asyncio
async def test_version_conflict_returns_error_and_does_not_partially_persist(settings_api_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings",
            json={
                "changes": {
                    "workflow.default_publish_mode": "branch",
                    "skills.policy_mode": "permissive",
                },
                "expected_versions": {
                    "workflow.default_publish_mode": 1,
                    "skills.policy_mode": 1,
                },
            },
        )
        conflict = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {
                    "workflow.default_publish_mode": "none",
                    "skills.policy_mode": "allowlist",
                },
                "expected_versions": {
                    "workflow.default_publish_mode": 99,
                    "skills.policy_mode": 1,
                },
            },
        )
        publish_mode = await client.get(
            "/api/v1/settings/effective/workflow.default_publish_mode",
        )
        policy = await client.get(
            "/api/v1/settings/effective/skills.policy_mode",
        )

    assert conflict.status_code == 409
    assert conflict.json()["error"] == "version_conflict"
    assert publish_mode.json()["value"] == "branch"
    assert policy.json()["value"] == "permissive"


@pytest.mark.asyncio
async def test_secret_ref_reference_allowed_but_raw_secret_rejected(settings_api_db):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        allowed = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"integrations.github.token_ref": "env://GITHUB_TOKEN"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        rejected = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"integrations.github.token_ref": "ghp_raw_plaintext"},
                "expected_versions": {"integrations.github.token_ref": 2},
            },
        )

    assert allowed.status_code == 200
    assert "env://GITHUB_TOKEN" in allowed.text
    assert rejected.status_code == 400
    assert rejected.json()["error"] == "invalid_setting_value"
    assert "ghp_raw_plaintext" not in rejected.text


@pytest.mark.asyncio
async def test_mm656_patch_settings_returns_structured_validation_error_and_no_mutation(
    settings_api_db,
):
    raw_secret_value = "gh" + "p_raw_plaintext"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        accepted = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )
        rejected = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {
                    "workflow.default_publish_mode": "not-supported",
                    "integrations.github.token_ref": raw_secret_value,
                },
                "expected_versions": {
                    "workflow.default_publish_mode": accepted.json()["values"][
                        "workflow.default_publish_mode"
                    ]["value_version"],
                    "integrations.github.token_ref": 1,
                },
            },
        )
        publish_mode = await client.get(
            "/api/v1/settings/effective/workflow.default_publish_mode",
        )
        token_ref = await client.get(
            "/api/v1/settings/effective/integrations.github.token_ref",
        )

    body = rejected.json()
    assert accepted.status_code == 200
    assert rejected.status_code == 400
    assert body["error"] == "invalid_setting_value"
    assert body["key"] == "workflow.default_publish_mode"
    assert body["details"]["code"] == "enum_value_invalid"
    assert body["details"]["boundary"] == "write_request"
    assert "persistence" in body["details"]["blocks"]
    assert raw_secret_value not in rejected.text
    assert publish_mode.json()["value"] == "branch"
    assert token_ref.json()["value"] != raw_secret_value


@pytest.mark.asyncio
async def test_mm656_patch_settings_rejects_missing_secret_ref_with_sanitized_details(
    settings_api_db,
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        rejected = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"integrations.github.token_ref": "db://missing-token"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )

    body = rejected.json()
    assert rejected.status_code == 400
    assert body["error"] == "secret_ref_not_resolvable"
    assert body["key"] == "integrations.github.token_ref"
    assert body["details"]["code"] == "secret_ref_unresolved"
    assert body["details"]["boundary"] == "write_request"
    assert body["details"]["details"] == {
        "ref_scheme": "db",
        "status": "missing",
        "launch_blocker": True,
        "blocks": ["launch", "readiness"],
    }
    assert "missing-token" not in rejected.text


@pytest.mark.asyncio
async def test_oversized_override_payload_rejected_and_effective_value_unchanged(
    settings_api_db,
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        rejected = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"workflow.default_provider_profile_ref": "x" * 20000},
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )
        effective = await client.get(
            "/api/v1/settings/effective/workflow.default_provider_profile_ref",
        )

    assert rejected.status_code == 400
    assert rejected.json()["error"] == "invalid_setting_value"
    assert "x" * 64 not in rejected.text
    assert effective.status_code == 200
    assert effective.json()["source"] in {"config_file", "environment", "default"}


@pytest.mark.asyncio
async def test_unsafe_override_payload_classes_are_rejected_and_redacted(
    settings_api_db,
):
    unsafe_values = [
        "oauth_session_blob={...}",
        "decrypted_credential_file=/tmp/credential.json",
        "generated_config password=plaintext",
        "token=plaintext",
        "secret=plaintext",
        "api_key=plaintext",
        "apikey=plaintext",
        "large_artifact:" + ("x" * 20000),
        "workflow_payload={\"steps\": []}",
        "operational command_history: rm -rf /",
    ]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for unsafe_value in unsafe_values:
            rejected = await client.patch(
                "/api/v1/settings",
                json={
                    "changes": {"workflow.default_provider_profile_ref": unsafe_value},
                    "expected_versions": {"workflow.default_provider_profile_ref": 1},
                },
            )
            assert rejected.status_code == 400
            assert rejected.json()["error"] == "invalid_setting_value"
            assert unsafe_value[:32] not in rejected.text


@pytest.mark.asyncio
async def test_provider_profile_ref_allows_literal_profile_ids_with_sensitive_words(
    settings_api_db,
):
    async with settings_api_db() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="oauth_session-prod",
                runtime_id="codex",
                provider_id="openai",
                enabled=True,
                auth_state=ProviderProfileAuthState.CONNECTED,
                disabled_reason=None,
                last_auth_method=ProviderProfileAuthMethod.MANUAL,
            )
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        accepted = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {
                    "workflow.default_provider_profile_ref": "oauth_session-prod"
                },
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )

    assert accepted.status_code == 200
    assert accepted.json()["values"]["workflow.default_provider_profile_ref"]["value"] == (
        "oauth_session-prod"
    )


@pytest.mark.asyncio
async def test_mm657_validate_accepts_changes_without_override_or_audit_mutation(
    settings_api_db,
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/settings/validate",
            json={
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["issues"] == []
    assert body["issues_by_key"] == {}
    assert await _settings_override_count(settings_api_db) == 0
    assert await _settings_audit_count(settings_api_db) == 0


@pytest.mark.asyncio
async def test_mm657_validate_returns_structured_issues_without_echoing_secret(
    settings_api_db,
):
    raw_secret_value = "gh" + "p_raw_plaintext"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/settings/validate",
            json={
                "changes": {
                    "workflow.default_publish_mode": "not-supported",
                    "integrations.github.token_ref": raw_secret_value,
                },
                "expected_versions": {
                    "workflow.default_publish_mode": 1,
                    "integrations.github.token_ref": 1,
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is False
    assert body["issues_by_key"]["workflow.default_publish_mode"][0]["code"] == (
        "enum_value_invalid"
    )
    assert body["issues_by_key"]["integrations.github.token_ref"][0]["code"] == (
        "unsafe_setting_payload"
    )
    assert raw_secret_value not in response.text
    assert await _settings_override_count(settings_api_db) == 0
    assert await _settings_audit_count(settings_api_db) == 0


@pytest.mark.asyncio
async def test_mm657_preview_returns_diff_warnings_reload_and_no_commit(
    settings_api_db,
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/settings/preview",
            json={
                "changes": {"skills.policy_mode": "allowlist"},
                "expected_versions": {"skills.policy_mode": 1},
            },
        )
        effective = await client.get(
            "/api/v1/settings/effective/skills.policy_mode",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    diff = body["diffs"][0]
    assert diff["key"] == "skills.policy_mode"
    assert diff["before"]["value"] != diff["after"]["value"]
    assert diff["after"]["value"] == "allowlist"
    assert diff["redacted"] is False
    assert body["reload_requirements"][0]["key"] == "skills.policy_mode"
    assert body["reload_requirements"][0]["requires_reload"] is True
    assert body["dependency_warnings"] == []
    assert effective.json()["value"] != "allowlist"
    assert await _settings_override_count(settings_api_db) == 0
    assert await _settings_audit_count(settings_api_db) == 0


@pytest.mark.asyncio
async def test_mm657_preview_reports_missing_references_without_secret_material(
    settings_api_db,
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/settings/preview",
            json={
                "changes": {"integrations.github.token_ref": "db://missing-token"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is False
    assert body["issues_by_key"]["integrations.github.token_ref"][0]["code"] == (
        "secret_ref_unresolved"
    )
    assert body["diffs"][0]["redacted"] is True
    assert body["diffs"][0]["after"]["value"] is None
    assert "missing-token" not in response.text
    assert await _settings_override_count(settings_api_db) == 0
    assert await _settings_audit_count(settings_api_db) == 0


@pytest.mark.asyncio
async def test_mm657_documented_error_code_envelope_matrix(
    settings_api_db,
    monkeypatch,
):
    locked_entry = SettingRegistryEntry(
        key="test.locked",
        title="Locked",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        default_value="built-in",
        operator_locked_value="operator-value",
        operator_lock_reason="Controlled by operator policy.",
        order=1,
    )

    def _factory(*args, **kwargs):
        kwargs["registry"] = (locked_entry,)
        kwargs["env"] = {}
        return SettingsCatalogService(*args, **kwargs)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        unknown = await client.post(
            "/api/v1/settings/validate",
            json={
                "changes": {"workflow.github_token": "env://TOKEN"},
                "expected_versions": {"workflow.github_token": 1},
            },
        )
        secret_ref = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"integrations.github.token_ref": "db://missing-token"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        provider_profile = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"workflow.default_provider_profile_ref": "missing-profile"},
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )
        version_conflict = await client.post(
            "/api/v1/settings/validate",
            json={
                "changes": {"workflow.default_runtime": "codex"},
                "expected_versions": {"workflow.default_runtime": 99},
            },
        )
        requires_confirmation = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"workflow.operation_mode": "maintenance"},
                "expected_versions": {"workflow.operation_mode": 1},
            },
        )

    monkeypatch.setattr(settings_router, "SettingsCatalogService", _factory)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        operator_locked = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"test.locked": "client-value"},
                "expected_versions": {"test.locked": 1},
            },
        )

    assert unknown.status_code == 404
    _assert_settings_error_envelope(
        unknown.json(),
        error="setting_not_exposed",
        key="workflow.github_token",
    )
    assert secret_ref.status_code == 400
    _assert_settings_error_envelope(
        secret_ref.json(),
        error="secret_ref_not_resolvable",
        key="integrations.github.token_ref",
    )
    assert "missing-token" not in secret_ref.text
    assert provider_profile.status_code == 400
    _assert_settings_error_envelope(
        provider_profile.json(),
        error="provider_profile_not_found",
        key="workflow.default_provider_profile_ref",
    )
    assert version_conflict.status_code == 409
    _assert_settings_error_envelope(
        version_conflict.json(),
        error="version_conflict",
        key="workflow.default_runtime",
    )
    assert requires_confirmation.status_code == 428
    _assert_settings_error_envelope(
        requires_confirmation.json(),
        error="requires_confirmation",
        key="workflow.operation_mode",
    )
    assert operator_locked.status_code == 423
    _assert_settings_error_envelope(
        operator_locked.json(),
        error="operator_locked",
        key="test.locked",
    )


@pytest.mark.asyncio
async def test_failed_settings_write_creates_redacted_audit_event_with_request_id(
    settings_api_db,
):
    raw_secret_value = "gh" + "p_raw_plaintext"
    request_id = "settings-write-req-1" * 20

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        rejected = await client.patch(
            "/api/v1/settings",
            headers={"x-request-id": request_id},
            json={
                "changes": {"integrations.github.token_ref": raw_secret_value},
                "expected_versions": {"integrations.github.token_ref": 1},
                "reason": "wire github token",
            },
        )
        audit = await client.get(
            "/api/v1/settings/audit",
            params={"key": "integrations.github.token_ref"},
        )

    assert rejected.status_code == 400
    assert rejected.json()["error"] == "invalid_setting_value"
    assert raw_secret_value not in rejected.text
    assert await _settings_override_count(settings_api_db) == 0
    assert await _settings_audit_count(settings_api_db) == 1
    assert audit.status_code == 200
    item = audit.json()["items"][0]
    assert item["event_type"] == "settings.override.rejected"
    assert item["key"] == "integrations.github.token_ref"
    assert item["validation_outcome"] == "rejected"
    assert item["request_id"] == request_id[:255]
    assert item["reason"] == "wire github token"
    assert item["apply_mode"] == "next_launch"
    assert item["redacted"] is True
    assert item["old_value"] is None
    assert item["new_value"] is None
    assert "stored_redacted" in item["redaction_reasons"]
    assert raw_secret_value not in audit.text


@pytest.mark.asyncio
async def test_rejected_write_audit_failure_is_logged(
    settings_api_db,
    monkeypatch,
    caplog,
):
    class FailingAuditService(SettingsCatalogService):
        async def record_rejected_write_audit(self, **kwargs):
            raise RuntimeError("audit unavailable")

    monkeypatch.setattr(settings_router, "SettingsCatalogService", FailingAuditService)

    with caplog.at_level(logging.ERROR, logger=settings_router.__name__):
        await settings_router._record_rejected_write_audit(
            changes={"integrations.github.token_ref": "db://github-token"},
            reason="test audit visibility",
            request_id="req-1",
        )

    assert "Failed to record rejected settings write audit event" in caplog.text
    assert "audit unavailable" in caplog.text


@pytest.mark.asyncio
async def test_settings_audit_endpoint_exposes_safe_secret_ref_metadata(
    settings_api_db,
):

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings",
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
async def test_settings_audit_endpoint_lists_one_instance_history(
    settings_api_db,
):
    async with settings_api_db() as session:
        session.add_all(
            [
                ManagedAgentProviderProfile(
                    profile_id="codex-default",
                    runtime_id="codex",
                    provider_id="openai",
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    disabled_reason=None,
                    last_auth_method=ProviderProfileAuthMethod.MANUAL,
                ),
                ManagedAgentProviderProfile(
                    profile_id="other-profile",
                    runtime_id="codex",
                    provider_id="openai",
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    disabled_reason=None,
                    last_auth_method=ProviderProfileAuthMethod.MANUAL,
                ),
            ]
        )
        await session.commit()

        session.add(
            SettingsAuditEvent(
                event_type="settings.override.updated",
                key="workflow.default_provider_profile_ref",
                scope="user",
                workspace_id=uuid4(),
                user_id=uuid4(),
                new_value_json="other-profile",
                redacted=False,
            )
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"workflow.default_publish_mode": "branch"},
                "expected_versions": {"workflow.default_publish_mode": 1},
            },
        )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"workflow.default_provider_profile_ref": "codex-default"},
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )
        audit = await client.get("/api/v1/settings/audit")

    assert audit.status_code == 200
    items = audit.json()["items"]
    assert {item["new_value"] for item in items} == {"branch", "codex-default"}
    assert all(item["actor_user_id"] is None for item in items)
    assert all("scope" not in item for item in items)


@pytest.mark.asyncio
async def test_settings_diagnostics_endpoint_returns_actionable_sanitized_output(
    settings_api_db,
):

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        rejected = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {"integrations.github.token_ref": "db://missing-token"},
                "expected_versions": {"integrations.github.token_ref": 1},
                "reason": "select managed secret",
            },
        )
        response = await client.get(
            "/api/v1/settings/diagnostics",
            params={"key": "integrations.github.token_ref"},
        )

    assert rejected.status_code == 400
    assert rejected.json()["details"]["code"] == "secret_ref_unresolved"
    assert response.status_code == 200
    value = response.json()["values"]["integrations.github.token_ref"]
    assert value["source"] in {"config_file", "environment", "default"}
    assert value["recent_change"] is None
    assert value["apply_mode"] == "next_launch"
    assert value["activation_state"] == "active"
    assert value["active"] is True
    assert value["completion_guidance"] == (
        "New launches will use this value the next time they start."
    )
    assert value["affected_process_or_worker"] == "github, integrations"
    assert value["diagnostics"][0]["code"] == "inherited_null"
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
            params={"key": "workflow.default_publish_mode"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["values"]["workflow.default_publish_mode"]["source"] in {
        "config_file",
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
            "/api/v1/settings",
            json={
                "changes": {
                    "integrations.github.token_ref": "db://active-github-token"
                },
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )
        disabled = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {
                    "integrations.github.token_ref": "db://disabled-github-token"
                },
                "expected_versions": {"integrations.github.token_ref": 2},
            },
        )
        missing = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {
                    "integrations.github.token_ref": "db://missing-github-token"
                },
                "expected_versions": {"integrations.github.token_ref": 2},
            },
        )

    active_value = active.json()["values"]["integrations.github.token_ref"]
    assert active.status_code == 200
    assert active_value["diagnostics"] == []
    assert "active-plaintext" not in active.text
    assert disabled.status_code == 400
    assert disabled.json()["details"]["code"] == "secret_ref_unresolved"
    assert disabled.json()["details"]["details"] == {
        "ref_scheme": "db",
        "status": "disabled",
        "launch_blocker": True,
        "blocks": ["launch", "readiness"],
    }
    assert "disabled-plaintext" not in disabled.text
    assert missing.status_code == 400
    assert missing.json()["details"]["code"] == "secret_ref_unresolved"
    assert missing.json()["details"]["details"] == {
        "ref_scheme": "db",
        "status": "missing",
        "launch_blocker": True,
        "blocks": ["launch", "readiness"],
    }


@pytest.mark.asyncio
async def test_mm655_effective_setting_endpoint_exposes_complete_contract(monkeypatch):
    configured_entry = SettingRegistryEntry(
        key="test.configured",
        title="Configured",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        default_value="built-in",
        settings_path=("settings", "configured"),
        apply_mode="worker_reload",
        requires_reload=True,
        applies_to=("worker", "workflow_creation"),
        order=1,
    )

    def _factory(*args, **kwargs):
        kwargs["registry"] = (configured_entry,)
        kwargs["settings"] = SimpleNamespace(
            settings=SimpleNamespace(configured="from-config")
        )
        kwargs["env"] = {}
        return SettingsCatalogService(*args, **kwargs)

    monkeypatch.setattr(settings_router, "SettingsCatalogService", _factory)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/effective/test.configured",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "config_file"
    assert body["default_value"] == "built-in"
    assert body["inheritance_state"] == "inherited"
    assert body["read_only"] is False
    assert body["read_only_reason"] is None
    assert body["requires_reload"] is True
    assert body["applies_to"] == ["worker", "workflow_creation"]


@pytest.mark.asyncio
async def test_mm655_operator_lock_endpoint_returns_read_only_reason(monkeypatch):
    locked_entry = SettingRegistryEntry(
        key="test.locked",
        title="Locked",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        default_value="built-in",
        operator_locked_value="operator-value",
        operator_lock_reason="Controlled by operator policy.",
        order=1,
    )

    def _factory(*args, **kwargs):
        kwargs["registry"] = (locked_entry,)
        kwargs["env"] = {}
        return SettingsCatalogService(*args, **kwargs)

    monkeypatch.setattr(settings_router, "SettingsCatalogService", _factory)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/effective/test.locked",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["value"] == "operator-value"
    assert body["source"] == "operator_lock"
    assert body["inheritance_state"] == "locked"
    assert body["read_only"] is True
    assert body["read_only_reason"] == "Controlled by operator policy."


@pytest.mark.asyncio
async def test_mm655_diagnostics_endpoint_reports_distinct_states(monkeypatch):
    policy_entry = SettingRegistryEntry(
        key="test.policy_blocked",
        title="Policy Blocked",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        default_value="blocked",
        policy_blocked_reason="Workspace policy blocks this value.",
        order=1,
    )

    def _factory(*args, **kwargs):
        kwargs["registry"] = (policy_entry,)
        kwargs["env"] = {}
        return SettingsCatalogService(*args, **kwargs)

    monkeypatch.setattr(settings_router, "SettingsCatalogService", _factory)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/diagnostics",
            params={"key": "test.policy_blocked"},
        )

    assert response.status_code == 200
    value = response.json()["values"]["test.policy_blocked"]
    assert value["diagnostics"][0]["code"] == "policy_blocked"
    assert value["source"] == "default"


@pytest.mark.asyncio
@pytest.mark.parametrize("inherited", ["claude_code", "omnigent", "codex", " codex_cli "])
@pytest.mark.parametrize("legacy", [False, True])
async def test_catalog_reset_preview_matches_delete_and_reloaded_value(settings_api_db, monkeypatch, inherited, legacy):
    monkeypatch.setenv("WORKFLOW_DEFAULT_RUNTIME", inherited)
    if legacy:
        async with settings_api_db() as session:
            session.add(SettingsOverride(key="workflow.default_task_runtime", value_json="claude_code", value_version=1))
            await session.commit()
    key = "workflow.default_runtime"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        saved = await client.patch("/api/v1/settings", json={"changes": {key: "omnigent"}})
        assert saved.status_code == 200
        counts = (await _settings_audit_count(settings_api_db), await _settings_override_count(settings_api_db))
        catalog = await client.get("/api/v1/settings/catalog?section=user-workspace")
        assert counts == (await _settings_audit_count(settings_api_db), await _settings_override_count(settings_api_db))
        descriptor = next(d for d in catalog.json()["categories"]["Workflow"] if d["key"] == key)
        assert descriptor["default_value"] == "omnigent"
        assert descriptor["reset_value"] == ("claude_code" if legacy else "codex_cli" if inherited.strip() in {"codex", "codex_cli"} else inherited)
        assert not any(d["code"] == "setting_renamed_override" for d in descriptor["diagnostics"])
        reset = await client.delete(f"/api/v1/settings/{key}")
        assert reset.status_code == 200
        assert reset.json()["value"] == descriptor["reset_value"]
        refreshed = await client.get("/api/v1/settings/catalog?section=user-workspace")
        restored = next(d for d in refreshed.json()["categories"]["Workflow"] if d["key"] == key)
        assert restored["effective_value"] == descriptor["reset_value"]
        assert restored["source"] == ("instance_override" if legacy else "environment")


@pytest.mark.asyncio
async def test_framework_catalog_lists_installed_readiness_and_skill(settings_api_db):

    async with settings_api_db() as session:
        await run_guarded_upgrade(session, operator_authorized=True)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://localhost",
    ) as client:
        response = await client.get("/api/v1/settings/framework-packs")
    assert response.status_code == 200, response.text
    packs = {item["id"]: item for item in response.json()["items"]}
    assert set(packs) == {
        "iso-27001-readiness",
        "soc2-readiness",
        "gli-19-readiness",
        "nj-dge-readiness",
        "pci-dss-readiness",
        "ukgc-rts-security",
        "owasp-asvs-l1",
        "network-best-practices",
        "web-api-methodology",
    }
    assert packs["web-api-methodology"]["requirementCount"] == 6
    # General best practices for networks: original statements, network plans only.
    network = packs["network-best-practices"]
    assert network["available"] and network["license"].startswith("Apache-2.0")
    assert network["subjects"]["network"]["available"]
    assert not network["subjects"]["application"]["available"]
    # General best practices for applications: ASVS 5.0.0 Level 1 plus the
    # shipped known-CVE, secret and misconfiguration checks. Levels 2 and 3
    # are not offered.
    choices = [item for item in packs.values() if item.get("choice")]
    assert [(item["id"], item["choice"]) for item in choices] == [
        ("owasp-asvs-l1", "general-best-practices")
    ]
    asvs = packs["owasp-asvs-l1"]
    assert (asvs["edition"], asvs["requirementCount"], asvs["available"]) == (
        "5.0.0",
        70,
        True,
    )
    assert asvs["subjects"]["application"]["available"]
    assert asvs["license"].startswith("CC-BY-SA-4.0")
    assert {check["id"] for check in asvs["companionChecks"]} == {
        "known-vulnerabilities",
        "secrets",
        "misconfiguration",
    }
    assert "KEV" in asvs["companionChecks"][0]["title"]
    assert {"agent_review", "not_assessed"} <= set(asvs["evidenceKinds"])
    assert not any("asvs-l2" in key or "asvs-l3" in key for key in packs)

    fixture_path = Path(__file__).resolve().parents[4] / "fixtures/security/readiness_form_fields.json"
    form_fields = json.loads(fixture_path.read_text())
    for pack_id, fields in form_fields.items():
        schema = packs[pack_id]["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert {name: schema["properties"][name] for name in fields} == fields

    # Packs that tag network requirements offer a network plan; others do not.
    network_packs = {
        "pci-dss-readiness",
        "iso-27001-readiness",
        "gli-19-readiness",
        "network-best-practices",
    }
    for pack_id, item in packs.items():
        assert item["inputSchema"]["properties"]
        assert item["requirementCount"] > 0
        assert item["subjects"]["network"]["available"] == (pack_id in network_packs)
        if pack_id not in network_packs:
            assert item["subjects"]["network"]["reason"]
        assert "requirements" not in item


@pytest.mark.asyncio
async def test_catalog_checks_saved_pack_identity_access_and_effectivity(
    settings_api_db, monkeypatch
):
    import hashlib

    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactStateError,
        TemporalArtifactNotFoundError,
    )
    from tests.unit.security.test_assessment_projects import project_document
    from tests.unit.security.test_framework_packs import payload

    async with settings_api_db() as session:
        await run_guarded_upgrade(session, operator_authorized=True)

    doc = project_document()
    saved = doc["projects"][0]["environments"][0]["frameworks"] = [
        {"id": "old", "edition": "legacy"}
    ]
    content = {}
    for kind, change in {
        "private": {"distribution": "restricted"},
        "missing": {},
        "denied": {},
        "mismatch": {},
        "wrongid": {},
        "draft": {"publicationStatus": "draft"},
        "superseded": {"publicationStatus": "superseded"},
        "expired": {"effectiveUntil": "2026-02-01"},
        "future": {"effectiveFrom": "2099-01-01"},
        "prohibited": {"distribution": "prohibited"},
    }.items():
        raw = json.dumps(
            {
                **payload(),
                "title": "private-title",
                "license": "private-license",
                **change,
            }
        ).encode()
        ref = "art_" + kind
        content[ref] = raw
        saved.append(
            {
                "packId": "different" if kind == "wrongid" else "example",
                "edition": "2026",
                "revision": "1",
                "artifactRef": ref,
                "sha256": (
                    "0" * 64 if kind == "mismatch" else hashlib.sha256(raw).hexdigest()
                ),
            }
        )

    async def metadata(*, artifact_id, access):
        assert access.is_operator
        if artifact_id == "art_missing":
            raise TemporalArtifactNotFoundError(artifact_id)
        return (
            SimpleNamespace(size_bytes=len(content[artifact_id])),
            [],
            False,
            SimpleNamespace(raw_access_allowed=artifact_id != "art_denied"),
        )

    async def read(*, artifact_id, access):
        assert access.is_operator and artifact_id not in {"art_denied", "art_missing"}
        return None, content[artifact_id]

    def reader(_repo):
        return SimpleNamespace(get_metadata=metadata, read=read)

    monkeypatch.setattr(
        "api_service.services.framework_catalog.TemporalArtifactService", reader
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://localhost",
    ) as client:
        key = "security.assessment_projects"
        effective = await client.get(f"/api/v1/settings/effective/{key}")
        assert effective.status_code == 200
        response = await client.patch(
            "/api/v1/settings",
            json={
                "changes": {key: doc},
                "expected_versions": {key: effective.json()["value_version"]},
                "reason": "Register standards",
            },
        )
        assert response.status_code == 200, response.text
        response = await client.get("/api/v1/settings/framework-packs")

        def unavailable(_repo):
            raise TemporalArtifactStateError("private-storage-diagnostic")

        monkeypatch.setattr(
            "api_service.services.framework_catalog.TemporalArtifactService",
            unavailable,
        )
        offline = await client.get("/api/v1/settings/framework-packs")
        assert offline.status_code == 200, offline.text
        offline_items = [
            p for p in offline.json()["items"] if p["source"] == "declared"
        ]
        assert len(offline_items) == len(content)
        assert all(not p["available"] and p["reason"] for p in offline_items)
        assert "private-storage-diagnostic" not in offline.text
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {"builtin", "skill", "declared"} == {item["source"] for item in items}
    declared = {
        item["artifactRef"]: item for item in items if item["source"] == "declared"
    }
    assert set(declared) == set(content)
    for registration in saved[1:]:
        item = declared[registration["artifactRef"]]
        assert item["id"] == registration["packId"]
        assert all(
            item[key] == registration[key] for key in ("revision", "edition", "sha256")
        )
        assert item["available"] == (item["artifactRef"] == "art_private")
        if not item["available"]:
            assert item["reason"]
        if item["artifactRef"] in {
            "art_private",
            "art_missing",
            "art_denied",
            "art_mismatch",
            "art_prohibited",
            "art_wrongid",
        }:
            assert "private-title" not in json.dumps(item)
            assert "private-license" not in json.dumps(item)
    assert declared["art_private"]["requirementCount"] == 1
    assert "deterministic_check" in declared["art_private"]["evidenceKinds"]
    assert "Secret description" not in response.text


@pytest.mark.asyncio
async def test_policy_pack_routes_map_deterministic_outcomes(
    settings_api_db, monkeypatch
):
    from api_service.services.policy_pack_drafts import PolicyPackError
    from moonmind.workflows.temporal.artifacts import TemporalArtifactNotFoundError

    async with settings_api_db() as session:
        await run_guarded_upgrade(session, operator_authorized=True)
    draft_body = {
        "policy": {"name": "Policy", "owner": "Security", "version": "1"},
        "documents": [{"artifactRef": "art_doc", "sha256": "a" * 64}],
        "responseArtifactRef": "art_response",
        "responseSha256": "b" * 64,
    }
    accept_body = {
        "draftArtifactRef": "art_draft",
        "draftSha256": "c" * 64,
        "review": {
            "reviewedBy": "CISO",
            "reviewedOn": "2026-11-20",
            "effectiveFrom": "2026-11-20",
        },
        "projectId": "acme",
        "environmentId": "production",
    }
    outcomes = iter(
        [
            PolicyPackError(
                "Drafting response art_response changed; its SHA-256 differs"
            ),
            {"draftArtifactRef": "art_new", "publicationStatus": "draft"},
        ]
    )
    accepted = iter(
        [
            ValueError("version_conflict"),
            TemporalArtifactNotFoundError("art_private_detail"),
            {"artifactRef": "art_pack", "publicationStatus": "published"},
        ]
    )

    async def fake(outcomes, *args, **kwargs):
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(
        settings_router, "draft_policy_pack", lambda *a, **k: fake(outcomes)
    )
    monkeypatch.setattr(
        settings_router, "accept_policy_pack", lambda *a, **k: fake(accepted)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://localhost",
    ) as client:
        url = "/api/v1/settings/framework-packs"
        invalid = await client.post(f"{url}/drafts", json={"documents": []})
        assert invalid.status_code == 422, invalid.text
        refused = await client.post(f"{url}/drafts", json=draft_body)
        assert refused.status_code == 422
        _assert_settings_error_envelope(refused.json(), error="invalid_framework_pack")
        assert "SHA-256 differs" in refused.json()["message"]
        drafted = await client.post(f"{url}/drafts", json=draft_body)
        assert (
            drafted.status_code == 200
            and drafted.json()["publicationStatus"] == "draft"
        )
        both = dict(
            accept_body,
            pack={
                "artifactRef": "art_pack",
                "packId": "p",
                "edition": "1",
                "revision": "1",
                "sha256": "d" * 64,
            },
        )
        assert (await client.post(f"{url}/accept", json=both)).status_code == 422
        conflict = await client.post(f"{url}/accept", json=accept_body)
        assert conflict.status_code == 409
        assert conflict.json()["error"] == "version_conflict"
        missing = await client.post(f"{url}/accept", json=accept_body)
        assert missing.status_code == 409
        assert missing.json()["error"] == "framework_pack_artifact_unavailable"
        assert "art_private_detail" not in missing.text
        published = await client.post(f"{url}/accept", json=accept_body)
        assert published.status_code == 200
        assert published.json()["publicationStatus"] == "published"
