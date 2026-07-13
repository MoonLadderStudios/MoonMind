import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base
from api_service.services.container_jobs import (
    ContainerJobIdempotencyConflictError,
    ContainerJobNotFoundError,
    ContainerJobService,
)
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobCancelRequest,
    ContainerJobState,
    ContainerJobSubmitRequest,
    ImageObservation,
    OwnerIdentity,
    TerminalOutcome,
)


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def submission(*, key: str = "key", image: str = "alpine") -> ContainerJobSubmitRequest:
    return ContainerJobSubmitRequest(
        idempotencyKey=key,
        source={"source": "mcp", "callerRequestId": "r"},
        spec={
            "image": {"reference": image},
            "workspace": {"kind": "managed_runtime", "runtimeId": "codex", "agentRunId": "run"},
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        },
    )


@pytest.mark.asyncio
async def test_create_or_replay_conflict_and_owner_scoped_reads(session_factory) -> None:
    async with session_factory() as session:
        service = ContainerJobService(session)
        owner = OwnerIdentity(principalId="user-1", principalType="user")
        first = await service.submit(owner=owner, request=submission())
        second = await service.submit(owner=owner, request=submission())
        assert first.job_id == second.job_id and second.replayed
        with pytest.raises(ContainerJobIdempotencyConflictError):
            await service.submit(owner=owner, request=submission(image="busybox"))
        assert (await service.status(owner_id="user-1", job_id=first.job_id)).state == "queued"
        with pytest.raises(ContainerJobNotFoundError):
            await service.status(owner_id="user-2", job_id=first.job_id)


@pytest.mark.asyncio
async def test_concurrent_duplicate_submission_has_one_durable_identity(session_factory) -> None:
    owner = OwnerIdentity(principalId="concurrent-owner", principalType="service")

    async def submit_once():
        async with session_factory() as session:
            result = await ContainerJobService(session).submit(owner=owner, request=submission())
            await session.commit()
            return result

    first, second = await asyncio.gather(submit_once(), submit_once())
    assert first.job_id == second.job_id
    assert sorted([first.replayed, second.replayed]) == [False, True]


@pytest.mark.asyncio
async def test_terminal_observations_and_auxiliary_failures_project_independently(session_factory) -> None:
    async with session_factory() as session:
        service = ContainerJobService(session)
        accepted = await service.submit(
            owner=OwnerIdentity(principalId="u", principalType="user"), request=submission()
        )
        await service.repository.record_observation(
            owner_id="u", job_id=accepted.job_id, state=ContainerJobState.FAILED,
            backend_kind="docker", backend_ref="configured-backend",
            image=ImageObservation(requestedReference="alpine", cachePresent=True),
            terminal=TerminalOutcome(exitCode=2, failureClass="execution", message="failed"),
            publication=AuxiliaryOutcome(state="failed", diagnosticsRef="artifact://publication"),
            cleanup=AuxiliaryOutcome(state="succeeded"),
            logs_ref="artifact://logs", artifacts_ref="artifact://outputs",
        )
        status = await service.status(owner_id="u", job_id=accepted.job_id)
        assert status.terminal.exit_code == 2
        assert status.publication.state == "failed"
        assert status.cleanup.state == "succeeded"
        assert status.logs_ref == "artifact://logs"


@pytest.mark.asyncio
async def test_owner_scoped_idempotent_and_terminal_cancel(session_factory) -> None:
    async with session_factory() as session:
        service = ContainerJobService(session)
        accepted = await service.submit(
            owner=OwnerIdentity(principalId="u", principalType="user"), request=submission()
        )
        request = ContainerJobCancelRequest(idempotencyKey="cancel-1")
        first = await service.cancel(owner_id="u", job_id=accepted.job_id, request=request)
        second = await service.cancel(owner_id="u", job_id=accepted.job_id, request=request)
        assert first.accepted and second.replayed and second.state == "canceling"
        await service.repository.record_observation(
            owner_id="u", job_id=accepted.job_id, state=ContainerJobState.SUCCEEDED
        )
        terminal = await service.cancel(
            owner_id="u", job_id=accepted.job_id,
            request=ContainerJobCancelRequest(idempotencyKey="cancel-2"),
        )
        assert not terminal.accepted and terminal.state == "succeeded"
        with pytest.raises(ContainerJobNotFoundError):
            await service.cancel(owner_id="other", job_id=accepted.job_id, request=request)
