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
        assert row.value_version == 3
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
