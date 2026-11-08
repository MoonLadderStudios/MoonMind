"""DNS reservations use the existing durable assessment admission owner."""

import copy

import pytest

from api_service.db.models import ContainerJobRecord
from api_service.services.assessment_authorizations import assessment_reserved_budget
from api_service.services.container_jobs import (
    ContainerJobScopeDeniedError,
    ContainerJobService,
)
from moonmind.security.workload_profiles import AdmittedAssessmentScope
from tests.unit.security.test_dns_assessment_admission import (
    SCOPE,
    request,
    settings,
    spec,
)
from tests.unit.services import test_assessment_job_authority as fixtures

session_factory = fixtures.session_factory
temporal = fixtures.temporal


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "counter", ["maxAttempts", "timeoutSeconds", "maxOutputBytes", "queriesPerSecond"]
)
async def test_failed_dns_job_keeps_original_reservation_and_exact_replay(
    session_factory, temporal, counter
):
    selected = spec()
    allocation = selected.workload_profile.dns_assessment.model_dump(
        by_alias=True, mode="json"
    )
    authority = copy.deepcopy(allocation)
    for name in ("maxAttempts", "timeoutSeconds", "maxOutputBytes", "queriesPerSecond"):
        if name != counter:
            # Each selected allowance may be split across two jobs, but the
            # counter under test is exhausted by the first reservation.
            authority[name] = allocation[name] * 2
    # Keep the model's fixed total ceiling while choosing a smaller job output.
    allocation["maxOutputBytes"] = 16384
    authority["maxOutputBytes"] = 16384 if counter == "maxOutputBytes" else 32768
    selected = spec(
        workloadProfile={
            **selected.workload_profile.model_dump(by_alias=True, mode="json"),
            "dnsAssessment": allocation,
        }
    )
    scope = AdmittedAssessmentScope.model_validate(
        {
            **SCOPE,
            "maxJobs": 10,
            "targets": [{"dnsAssessment": authority}],
            "maxRequestsPerSecond": authority["queriesPerSecond"],
        }
    )
    async with session_factory() as session:
        await fixtures.save_scope(session, scope)
        cap = fixtures.capability(scope)
        service = ContainerJobService(
            session, temporal=temporal, backend_settings=settings(), capability=cap
        )
        first_request = request(selected)
        accepted = await service.submit(owner=cap.owner, request=first_request)
        record = await session.get(ContainerJobRecord, accepted.job_id)
        record.state = "failed"
        await session.commit()
        replay = await service.submit(owner=cap.owner, request=first_request)
        assert replay.replayed and replay.job_id == accepted.job_id
        next_request = first_request.model_copy(
            update={"idempotency_key": "dns-second"}
        )
        with pytest.raises(ContainerJobScopeDeniedError, match="DNS query budget"):
            await service.submit(owner=cap.owner, request=next_request)
        totals = await assessment_reserved_budget(session, scope, include_dns=True)
        assert totals[0] == 1
        assert totals[2]["max_attempts"] == allocation["maxAttempts"]
