"""Real PostgreSQL serialization at the assessment reservation boundary."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base, ContainerJobRecord
from api_service.services.container_jobs import ContainerJobScopeDeniedError
from moonmind.security.assessment_authorizations import authorization_scope
from moonmind.security.workload_profiles import AdmittedAssessmentScope
from tests.unit.security.test_workload_profiles import SCOPE
from tests.unit.services.test_assessment_job_authority import (
    authorization,
    save_scope,
    submit,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize("same_request", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
async def test_reservation_serializes_concurrent_submissions(
    control_plane_postgres_url, same_request, legacy
):
    engine = create_async_engine(control_plane_postgres_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        auth = authorization(
            maxTotalJobSeconds=300,
            scope={**SCOPE, "maxJobs": 1, "maxCpuMillis": 2000, "maxMemoryMiB": 2048},
        )
        scope = (
            AdmittedAssessmentScope.model_validate({**SCOPE, "maxJobs": 1})
            if legacy
            else authorization_scope(auth)
        )
        async with sessions() as session:
            await save_scope(session, scope, auth=None if legacy else auth)
            # Different canonical workflow rows ensure the shared Settings
            # reservation lock, not a common source-row lock, prevents overspend.
            await save_scope(session, scope, workflow="workflow-2")
        temporal = AsyncMock()
        ready = asyncio.Event()
        arrived = 0

        async def start(index):
            nonlocal arrived
            async with sessions() as session:
                arrived += 1
                if arrived == 2:
                    ready.set()
                await ready.wait()
                try:
                    return await submit(
                        session,
                        temporal,
                        scope,
                        owner="step-1" if same_request else f"step-{index}",
                        workflow="workflow-1" if same_request else f"workflow-{index}",
                        key="same" if same_request else f"job-{index}",
                    )
                except ContainerJobScopeDeniedError as exc:
                    return exc

        results = await asyncio.gather(start(1), start(2))
        successes = [r for r in results if not isinstance(r, Exception)]
        if same_request:
            assert len(successes) == 2
            assert successes[0].job_id == successes[1].job_id
            assert sum(r.replayed for r in successes) == 1
        else:
            assert len(successes) == 1
            assert (
                sum(isinstance(r, ContainerJobScopeDeniedError) for r in results) == 1
            )
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(ContainerJobRecord)
                )
                == 1
            )
    finally:
        await engine.dispose()
