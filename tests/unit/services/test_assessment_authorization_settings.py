import pytest
from sqlalchemy import select

from api_service.db.models import SettingsOverride
from api_service.services.settings_catalog import (
    SettingsCatalogService,
    SettingsValidationError,
)
from moonmind.security.assessment_authorizations import SETTINGS_KEY
from tests.unit.security.test_assessment_authorizations import authorization
from tests.unit.services.test_settings_catalog import (
    settings_session_maker as settings_session_maker,
)


@pytest.mark.asyncio
async def test_saved_authorizations_have_server_generation_and_optimistic_version(
    settings_session_maker,  # noqa: F811 - imported pytest fixture
):
    async with settings_session_maker() as session:
        service = SettingsCatalogService(env={}, session=session)
        document = {"authorizations": [authorization()]}
        with pytest.raises(SettingsValidationError) as error:
            await service.apply_overrides(changes={SETTINGS_KEY: document})
        assert error.value.first_issue.code == "expected_version_required"
        response = await service.apply_overrides(
            changes={SETTINGS_KEY: document}, expected_versions={SETTINGS_KEY: 1}
        )
        value = response.values[SETTINGS_KEY]
        assert value.value_version == 2
        first = value.value["authorizations"][0]
        assert first["generation"]
        with pytest.raises(ValueError, match="version_conflict"):
            await service.apply_overrides(
                changes={SETTINGS_KEY: document}, expected_versions={SETTINGS_KEY: 1}
            )
        await service.reset_override(SETTINGS_KEY)
        row = (
            await session.scalars(
                select(SettingsOverride).where(SettingsOverride.key == SETTINGS_KEY)
            )
        ).one()
        assert row.value_json == {"authorizations": []}
        assert row.is_reset is False
        assert row.value_version == 3
        from api_service.services.assessment_authorizations import (
            AssessmentAuthorizationDenied,
            load_assessment_authorization,
        )
        with pytest.raises(AssessmentAuthorizationDenied):
            await load_assessment_authorization(session, first["ref"])
        recreated = await service.apply_overrides(
            changes={SETTINGS_KEY: {"authorizations": [first]}},
            expected_versions={SETTINGS_KEY: 3},
        )
        assert (
            recreated.values[SETTINGS_KEY].value["authorizations"][0]["generation"]
            != first["generation"]
        )


def test_authorization_schema_rejects_unknown_fields_and_secret_values():
    service = SettingsCatalogService(env={})
    entry = service._entries_by_key[SETTINGS_KEY]
    value = {"authorizations": [authorization()]}
    assert (
        service._validation_issues_for_value(entry, value, boundary="write_request")
        == []
    )
    value["authorizations"][0]["authoritySource"] = "ghp_" + "A" * 40
    assert service._validation_issues_for_value(entry, value, boundary="write_request")
    value["authorizations"][0]["password"] = "not-permitted"
    assert service._validation_issues_for_value(entry, value, boundary="write_request")


def test_authorization_descriptor_exposes_native_authoring_fields_without_granting():
    import json

    service = SettingsCatalogService(env={})
    descriptor = service._descriptor(service._entries_by_key[SETTINGS_KEY])
    schema = descriptor.input_schema
    assert schema["type"] == "object"
    assert "engagement" not in schema["properties"]
    assert {"maxCpuMillis", "maxMemoryMiB"} <= set(schema["required"])
    assert "$ref" not in json.dumps(schema)
    assert "$defs" not in schema
    target = schema["properties"]["targets"]["items"]["properties"]
    assert target["webOrigin"]["type"] == "string"
    assert target["cloudProvider"]["enum"] == ["aws"]
    assert "default" not in target["cloudProvider"]
    assert "default" not in target["webOrigin"]
    assert target["webRoutes"]["items"]["properties"]["methods"]["items"]["enum"]
    grant = schema["properties"]["grants"]["items"]["properties"]
    assert {"ref", "purpose", "expiresAt", "roleArn"} <= set(grant)
    assert descriptor.effective_value == {"authorizations": []}
    other = next(entry for entry in service._registry if entry.key != SETTINGS_KEY)
    assert service._descriptor(other).input_schema is None


def test_shared_authorization_form_fixture_matches_native_schema():
    import json
    from pathlib import Path

    from moonmind.security.assessment_authorizations import (
        authorization_scope_input_schema,
    )

    fixture = (
        Path(__file__).resolve().parents[3]
        / "frontend/src/runtime/fixtures/assessment-authorization-scope.json"
    )
    assert json.loads(fixture.read_text()) == authorization_scope_input_schema()


@pytest.mark.asyncio
async def test_worker_pause_state_cannot_become_a_catalog_reset_marker(
    settings_session_maker,
):
    key = "operations.workers.pause_state"
    async with settings_session_maker() as session:
        session.add(
            SettingsOverride(
                key=key, value_json={"workersPaused": True}, value_version=7
            )
        )
        await session.commit()
        service = SettingsCatalogService(session=session, env={})
        assert key not in service._entries_by_key
        with pytest.raises(SettingsValidationError):
            await service.apply_overrides(changes={key: None})
        with pytest.raises(SettingsValidationError):
            await service.apply_overrides(
                changes={"workflow.default_runtime": "codex_cli"},
                expected_versions={key: 7},
            )
        with pytest.raises(KeyError):
            await service.reset_override(key)
        row = (
            await session.scalars(
                select(SettingsOverride).where(SettingsOverride.key == key)
            )
        ).one()
        assert row.value_json == {"workersPaused": True}
        assert row.value_version == 7
        assert row.is_reset is False
