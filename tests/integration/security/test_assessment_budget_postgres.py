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


@pytest.mark.parametrize("same_request", [False, True])
@pytest.mark.parametrize(
    "counter",
    [
        "max_connections",
        "max_bytes",
        "max_connections_per_second",
        "max_concurrent_connections",
    ],
)
async def test_native_allocations_serialize_across_concurrent_workflows(
    control_plane_postgres_url, same_request, counter
):
    from dataclasses import replace

    from api_service.services.container_jobs import ContainerJobService
    from moonmind.schemas.container_job_models import ContainerJobSubmitRequest
    from moonmind.security.egress_policy import ScopedEgressPolicy
    from moonmind.security.workload_profiles import WorkloadProfile
    from tests.unit.security.test_native_network import native
    from tests.unit.security.test_workload_profiles import PROFILE, scoped, settings
    from tests.unit.services.test_assessment_job_authority import capability

    allocation = native()
    reserved_fields = (
        "max_connections",
        "max_bytes",
        "max_connections_per_second",
        "max_concurrent_connections",
    )
    authority = allocation.model_copy(
        update={
            "limits": allocation.limits.model_copy(
                update={
                    name: getattr(allocation.limits, name)
                    * (1 if name == counter else 3)
                    for name in reserved_fields
                }
            )
        }
    )
    profile = WorkloadProfile.model_validate(
        {
            **PROFILE,
            "networkMode": "bridge",
            "egressPolicyRef": "native",
            "nativeNetwork": authority.model_dump(),
        }
    )
    policy = ScopedEgressPolicy.model_validate(
        {
            "policyRef": "native",
            "allowedWorkflowIds": ["workflow-1", "workflow-2"],
            "allowedImageSourceRefs": ["repository-security"],
            "nativeNetwork": authority.model_dump(),
        }
    )
    backend = replace(
        settings(), workload_profiles=(profile,), egress_policies=(policy,)
    )
    auth = authorization(
        maxTotalJobSeconds=3000,
        scope={
            **SCOPE,
            "maxJobs": 10,
            "maxCpuMillis": 2000,
            "maxMemoryMiB": 2048,
            "networkRefs": ["native"],
            "targets": [*SCOPE["targets"], {"nativeNetwork": authority.model_dump()}],
        },
    )
    scope = authorization_scope(auth)
    engine = create_async_engine(control_plane_postgres_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            await save_scope(session, scope, auth=auth)
            await save_scope(session, scope, workflow="workflow-2")
        temporal = AsyncMock()
        ready = asyncio.Event()
        arrived = 0

        async def start(index):
            nonlocal arrived
            owner = "step-1" if same_request else f"step-{index}"
            workflow = "workflow-1" if same_request else f"workflow-{index}"
            cap = capability(scope, owner=owner, workflow=workflow)
            request = ContainerJobSubmitRequest(
                idempotencyKey="same" if same_request else f"native-{index}",
                source={"source": "workflow", "workflowId": workflow},
                spec=scoped(nativeNetwork=allocation.model_dump()),
            )
            async with sessions() as session:
                jobs = ContainerJobService(
                    session, temporal=temporal, backend_settings=backend, capability=cap
                )
                arrived += 1
                if arrived == 2:
                    ready.set()
                await ready.wait()
                try:
                    return await jobs.submit(owner=cap.owner, request=request)
                except ContainerJobScopeDeniedError as exc:
                    return exc

        results = await asyncio.wait_for(asyncio.gather(start(1), start(2)), 30)
        successes = [result for result in results if not isinstance(result, Exception)]
        if same_request:
            assert len(successes) == 2
            assert successes[0].job_id == successes[1].job_id
            assert sum(result.replayed for result in successes) == 1
        else:
            assert len(successes) == 1
            denied = [result for result in results if isinstance(result, Exception)]
            assert len(denied) == 1
            # Jobs/time have spare capacity, so only the selected native
            # allowance can be responsible for this admission refusal.
            assert "native network budget" in str(denied[0])
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(ContainerJobRecord)
                )
                == 1
            )
            record = await session.scalar(select(ContainerJobRecord))
            limits = record.request_json["spec"]["workloadProfile"]["nativeNetwork"][
                "limits"
            ]
            assert limits == allocation.limits.model_dump(by_alias=True)
    finally:
        await engine.dispose()
