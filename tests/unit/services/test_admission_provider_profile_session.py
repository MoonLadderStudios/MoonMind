"""Shared profile session doubles preserve admission query shape and identity."""

import pytest
from sqlalchemy import select

from api_service.db.models import ManagedAgentProviderProfile
from api_service.services.provider_profile_projection import (
    provider_profile_label_snapshot,
)
from tests.unit.services.test_omnigent_agent_profile_selection import (
    _GenericV2Session,
    _Session,
)
from tests.unit.services.test_schedule_deployment_refresh import DeploymentSession


@pytest.fixture(params=["base", "generic", "deployment"])
def admission_session(request):
    if request.param == "base":
        return _Session()
    if request.param == "generic":
        return _GenericV2Session(provider_runtime_id="codex_cli")
    return DeploymentSession()


@pytest.mark.asyncio
@pytest.mark.parametrize("account_label", [None, "Recorded account"])
async def test_profile_snapshot_reads_requested_identity_and_label(
    admission_session, account_label
):
    session = admission_session
    session.provider.account_label = account_label
    session.provider.provider_label = "Recorded provider"
    profile_id = session.provider.profile_id

    assert await provider_profile_label_snapshot(
        session,
        {
            "profileId": profile_id,
            "workflow": {
                "steps": [
                    {"runtime": {"providerProfileRef": "absent-profile"}},
                ],
            },
        },
    ) == {profile_id: account_label or "Recorded provider"}
    assert (
        await provider_profile_label_snapshot(session, {"profileId": "absent-profile"})
        == {}
    )


@pytest.mark.asyncio
async def test_profile_session_returns_selected_columns_in_requested_order(
    admission_session,
):
    session = admission_session
    session.provider.account_label = "Account"
    session.provider.provider_label = "Provider"
    profile_id = session.provider.profile_id
    result = await session.execute(
        select(
            ManagedAgentProviderProfile.provider_label,
            ManagedAgentProviderProfile.profile_id,
            ManagedAgentProviderProfile.account_label,
        ).where(ManagedAgentProviderProfile.profile_id.in_([profile_id]))
    )
    assert result.all() == [("Provider", profile_id, "Account")]


@pytest.mark.asyncio
async def test_profile_session_keeps_configuration_version_query(admission_session):
    from api_service.db.models import OmnigentAgentProfile, OmnigentAgentProfileVersion

    session = admission_session
    result = await session.execute(
        select(OmnigentAgentProfile, OmnigentAgentProfileVersion)
    )
    assert result.all() == [(session.profile, session.version)]
