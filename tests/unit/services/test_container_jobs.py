import asyncio

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base
from api_service.services.container_jobs import (
    ContainerJobAuthorizationError,
    ContainerJobIdempotencyConflictError,
    ContainerJobNotFoundError,
    ContainerJobService,
)
from api_service.services.registry_authorization import (
    PrivateImageAuthorizationPolicy,
    PrivateImageAuthorizationService,
)
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobCancelRequest,
    ContainerJobFailureClass,
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


@pytest.fixture
def temporal():
    adapter = AsyncMock()
    adapter.start_container_job.return_value = None
    adapter.signal_container_job_cancel.return_value = None
    return adapter


def submission(
    *, key: str = "key", image: str = "alpine", credential_ref: str | None = None
) -> ContainerJobSubmitRequest:
    return ContainerJobSubmitRequest(
        idempotencyKey=key,
        source={"source": "mcp", "callerRequestId": "r"},
        spec={
            "image": image,
            "workspaceRef": {"kind": "moonmind-session", "sessionId": "run"},
            "registryCredentialRef": credential_ref,
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        },
    )


def private_authorizer() -> PrivateImageAuthorizationService:
    policy = PrivateImageAuthorizationPolicy.model_validate(
        {
            "grants": [
                {
                    "credentialRef": "db://ghcr",
                    "registry": "ghcr.io",
                    "repositories": ["org/*"],
                    "principals": ["user-a"],
                }
            ]
        }
    )
    return PrivateImageAuthorizationService(policy)


@pytest.mark.asyncio
async def test_create_or_replay_conflict_and_owner_scoped_reads(session_factory, temporal) -> None:
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal)
        owner = OwnerIdentity(principalId="user-1", principalType="user")
        first = await service.submit(owner=owner, request=submission())
        second = await service.submit(owner=owner, request=submission())
        assert first.job_id == second.job_id and second.replayed
        assert temporal.start_container_job.await_count == 2
        started = temporal.start_container_job.await_args_list[0].args[0]
        assert started.job_id == first.job_id
        assert started.owner == owner
        with pytest.raises(ContainerJobIdempotencyConflictError):
            await service.submit(owner=owner, request=submission(image="busybox"))
        assert (await service.status(owner=owner, job_id=first.job_id)).state == "queued"
        with pytest.raises(ContainerJobNotFoundError):
            await service.status(owner=OwnerIdentity(principalId="user-2", principalType="user"), job_id=first.job_id)


@pytest.mark.asyncio
async def test_owner_type_is_part_of_identity_and_idempotency_scope(session_factory, temporal) -> None:
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal)
        user = OwnerIdentity(principalId="shared", principalType="user")
        system = OwnerIdentity(principalId="shared", principalType="system")
        user_job = await service.submit(owner=user, request=submission())
        system_job = await service.submit(owner=system, request=submission())
        assert user_job.job_id != system_job.job_id
        with pytest.raises(ContainerJobNotFoundError):
            await service.status(owner=system, job_id=user_job.job_id)


@pytest.mark.asyncio
async def test_concurrent_duplicate_submission_has_one_durable_identity(session_factory, temporal) -> None:
    owner = OwnerIdentity(principalId="concurrent-owner", principalType="service")

    async def submit_once():
        async with session_factory() as session:
            result = await ContainerJobService(session, temporal=temporal).submit(owner=owner, request=submission())
            await session.commit()
            return result

    first, second = await asyncio.gather(submit_once(), submit_once())
    assert first.job_id == second.job_id
    assert sorted([first.replayed, second.replayed]) == [False, True]


@pytest.mark.asyncio
async def test_terminal_observations_and_auxiliary_failures_project_independently(session_factory, temporal) -> None:
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal)
        accepted = await service.submit(
            owner=OwnerIdentity(principalId="u", principalType="user"), request=submission()
        )
        await service.repository.record_observation(
            owner=OwnerIdentity(principalId="u", principalType="user"), job_id=accepted.job_id, state=ContainerJobState.FAILED,
            backend_kind="docker", backend_ref="configured-backend",
            image=ImageObservation(requestedReference="alpine", cachePresent=True),
            terminal=TerminalOutcome(exitCode=2, failureClass="execution", message="failed"),
            publication=AuxiliaryOutcome(state="failed", diagnosticsRef="artifact://publication"),
            cleanup=AuxiliaryOutcome(state="succeeded"),
            logs_ref="artifact://logs", artifacts_ref="artifact://outputs",
        )
        status = await service.status(owner=OwnerIdentity(principalId="u", principalType="user"), job_id=accepted.job_id)
        assert status.terminal.exit_code == 2
        assert status.publication.state == "failed"
        assert status.cleanup.state == "succeeded"
        assert status.logs_ref == "artifact://logs"
        await service.repository.record_observation(
            owner=OwnerIdentity(principalId="u", principalType="user"),
            job_id=accepted.job_id,
            state=ContainerJobState.FAILED,
        )
        preserved = await service.status(owner=OwnerIdentity(principalId="u", principalType="user"), job_id=accepted.job_id)
        assert preserved.backend_kind == "docker"
        assert preserved.backend_ref == "configured-backend"


@pytest.mark.asyncio
async def test_authorized_private_image_flows_authorization_into_workflow(
    session_factory, temporal
) -> None:
    async with session_factory() as session:
        service = ContainerJobService(
            session, temporal=temporal, authorizer=private_authorizer()
        )
        owner = OwnerIdentity(principalId="user-a", principalType="user")
        accepted = await service.submit(
            owner=owner,
            request=submission(image="ghcr.io/org/app:1", credential_ref="db://ghcr"),
        )
        started = temporal.start_container_job.await_args_list[-1].args[0]
        assert started.registry_authorization is not None
        assert started.registry_authorization.authorized
        assert started.registry_authorization.scope == "org/*"
        status = await service.status(owner=owner, job_id=accepted.job_id)
        assert status.authorization is not None
        assert status.authorization.credential_ref == "db://ghcr"
        # The durable authorization observation carries no credential material.
        record = await service.repository.get_for_owner(
            owner=owner, job_id=accepted.job_id
        )
        assert "password" not in str(record.authorization_observation_json)
        assert "token" not in str(record.authorization_observation_json)


@pytest.mark.asyncio
async def test_denied_private_image_fails_closed_without_workflow_or_record(
    session_factory, temporal
) -> None:
    async with session_factory() as session:
        service = ContainerJobService(
            session, temporal=temporal, authorizer=private_authorizer()
        )
        # User B is not granted the credential -> denied at submission.
        with pytest.raises(ContainerJobAuthorizationError) as excinfo:
            await service.submit(
                owner=OwnerIdentity(principalId="user-b", principalType="user"),
                request=submission(
                    image="ghcr.io/org/app:1", credential_ref="db://ghcr"
                ),
            )
        assert (
            excinfo.value.authorization.failure_class
            == ContainerJobFailureClass.IMAGE_USE_DENIED
        )
        # Fail-closed: no workflow started and no durable queued record.
        temporal.start_container_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_cached_image_still_authorized_per_principal(
    session_factory, temporal
) -> None:
    # Cache presence must not become authorization: even after user A's job for a
    # private image is accepted, user B's independent request for the same image
    # is denied because authorization runs per submission, not per cached layer.
    async with session_factory() as session:
        service = ContainerJobService(
            session, temporal=temporal, authorizer=private_authorizer()
        )
        await service.submit(
            owner=OwnerIdentity(principalId="user-a", principalType="user"),
            request=submission(
                image="ghcr.io/org/app:1", credential_ref="db://ghcr", key="a"
            ),
        )
        with pytest.raises(ContainerJobAuthorizationError):
            await service.submit(
                owner=OwnerIdentity(principalId="user-b", principalType="user"),
                request=submission(
                    image="ghcr.io/org/app:1", credential_ref="db://ghcr", key="b"
                ),
            )


@pytest.mark.asyncio
async def test_owner_scoped_idempotent_and_terminal_cancel(session_factory, temporal) -> None:
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal)
        accepted = await service.submit(
            owner=OwnerIdentity(principalId="u", principalType="user"), request=submission()
        )
        request = ContainerJobCancelRequest(idempotencyKey="cancel-1")
        owner = OwnerIdentity(principalId="u", principalType="user")
        first = await service.cancel(owner=owner, job_id=accepted.job_id, request=request)
        second = await service.cancel(owner=owner, job_id=accepted.job_id, request=request)
        assert first.accepted and second.replayed and second.state == "canceling"
        temporal.signal_container_job_cancel.assert_awaited_once_with(accepted.job_id)
        await service.repository.record_observation(
            owner=owner, job_id=accepted.job_id, state=ContainerJobState.SUCCEEDED
        )
        terminal = await service.cancel(
            owner=owner, job_id=accepted.job_id,
            request=ContainerJobCancelRequest(idempotencyKey="cancel-2"),
        )
        assert not terminal.accepted and terminal.state == "succeeded"
        with pytest.raises(ContainerJobNotFoundError):
            await service.cancel(owner=OwnerIdentity(principalId="other", principalType="user"), job_id=accepted.job_id, request=request)
