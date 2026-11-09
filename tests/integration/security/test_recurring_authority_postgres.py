"""PostgreSQL concurrency for source-less occurrence reservations."""

import asyncio

import pytest
from sqlalchemy import select

from api_service.db.models import AssessmentOccurrenceRecord, SettingsOverride
from api_service.services.assessment_authorizations import (
    AssessmentAuthorizationDenied,
    admit_assessment_occurrence,
)
from moonmind.security.assessment_authorizations import (
    SETTINGS_KEY,
    authorization_scope,
)
from tests.integration import test_execution_input_admission_postgres as admission_fixtures

sessions = admission_fixtures.sessions
conversion_postgres_url = admission_fixtures.conversion_postgres_url
from tests.unit.services.test_assessment_job_authority import authorization

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize("duplicate", [False, True])
async def test_concurrent_native_reservations_do_not_reset_or_double_consume(
    sessions, duplicate
):
    auth = authorization(
        recurring={
            "minIntervalSeconds": 60,
            "maxOccurrences": 1,
            "maxJobsPerOccurrence": 1,
            "maxJobSecondsPerOccurrence": 120,
            "maxRunSeconds": 120,
        }
    )
    parameters = {
        "assessmentScope": authorization_scope(auth).model_dump(
            mode="json", by_alias=True
        )
    }
    async with sessions() as session:
        session.add(
            SettingsOverride(
                key=SETTINGS_KEY,
                value_json={
                    "authorizations": [auth.model_dump(mode="json", by_alias=True)]
                },
            )
        )
        await session.commit()

    async def reserve(workflow_id):
        async with sessions() as session:
            try:
                result = await admit_assessment_occurrence(
                    session, workflow_id=workflow_id, scheduled_parameters=parameters
                )
                await session.commit()
                return result
            except AssessmentAuthorizationDenied:
                await session.rollback()
                return None

    # No canonical rows or mutable schedule definitions participate. The
    # scheduled-Activity admission fixture supplies these verified start inputs.
    results = await asyncio.gather(
        reserve("original"), reserve("original" if duplicate else "other")
    )
    assert sum(result is not None for result in results) == (2 if duplicate else 1)
    if duplicate:
        assert results[0] == results[1]
    async with sessions() as session:
        rows = list(await session.scalars(select(AssessmentOccurrenceRecord)))
        assert len(rows) == 1
        assert rows[0].authority_json["ordinal"] == 1
