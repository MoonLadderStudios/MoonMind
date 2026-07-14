import asyncio

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base
from api_service.services.container_job_workspace_authorizer import (
    ContainerJobWorkspaceAuthorizationError,
    ContainerJobWorkspaceAuthorizer,
    WorkspaceOwnershipRecord,
)
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


@pytest.fixture
def temporal():
    adapter = AsyncMock()
    adapter.start_container_job.return_value = None
    adapter.signal_container_job_cancel.return_value = None
    return adapter


def managed_session_authorizer(
    *,
    session_id: str = "run",
    principal_id: str = "user-1",
    record: WorkspaceOwnershipRecord | None = None,
) -> ContainerJobWorkspaceAuthorizer:
    """Authorizer whose managed-session store holds one live owned record."""

    live = record if record is not None else WorkspaceOwnershipRecord(
        session_id=session_id, principal_id=principal_id
    )

    async def lookup(identity: str) -> WorkspaceOwnershipRecord | None:
        return live if identity == session_id else None

    return ContainerJobWorkspaceAuthorizer(managed_session_lookup=lookup)


@pytest.fixture
def authorizer() -> ContainerJobWorkspaceAuthorizer:
    return managed_session_authorizer()


def submission(*, key: str = "key", image: str = "alpine") -> ContainerJobSubmitRequest:
    return ContainerJobSubmitRequest(
        idempotencyKey=key,
        source={"source": "mcp", "callerRequestId": "r", "managedSessionId": "run"},
        spec={
            "image": image,
            "workspaceRef": {"kind": "moonmind-session", "sessionId": "run"},
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        },
    )


@pytest.mark.asyncio
async def test_create_or_replay_conflict_and_owner_scoped_reads(session_factory, temporal, authorizer) -> None:
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal, workspace_authorizer=authorizer)
        owner = OwnerIdentity(principalId="user-1", principalType="user")
        first = await service.submit(owner=owner, request=submission())
        second = await service.submit(owner=owner, request=submission())
        assert first.job_id == second.job_id and second.replayed
        # Exact idempotent replays return the durable job without starting a
        # duplicate workflow or reauthorizing mutable workspace state.
        assert temporal.start_container_job.await_count == 1
        started = temporal.start_container_job.await_args_list[0].args[0]
        assert started.job_id == first.job_id
        assert started.owner == owner
        with pytest.raises(ContainerJobIdempotencyConflictError):
            await service.submit(owner=owner, request=submission(image="busybox"))
        assert (await service.status(owner=owner, job_id=first.job_id)).state == "queued"
        with pytest.raises(ContainerJobNotFoundError):
            await service.status(owner=OwnerIdentity(principalId="user-2", principalType="user"), job_id=first.job_id)


@pytest.mark.asyncio
async def test_owner_type_is_part_of_identity_and_idempotency_scope(session_factory, temporal, authorizer) -> None:
    async with session_factory() as session:
        service = ContainerJobService(
            session,
            temporal=temporal,
            workspace_authorizer=managed_session_authorizer(principal_id="shared"),
        )
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
            result = await ContainerJobService(
                session,
                temporal=temporal,
                workspace_authorizer=managed_session_authorizer(
                    principal_id="concurrent-owner"
                ),
            ).submit(owner=owner, request=submission())
            await session.commit()
            return result

    first, second = await asyncio.gather(submit_once(), submit_once())
    assert first.job_id == second.job_id
    assert sorted([first.replayed, second.replayed]) == [False, True]


@pytest.mark.asyncio
async def test_terminal_observations_and_auxiliary_failures_project_independently(session_factory, temporal, authorizer) -> None:
    async with session_factory() as session:
        service = ContainerJobService(
            session,
            temporal=temporal,
            workspace_authorizer=managed_session_authorizer(principal_id="u"),
        )
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
async def test_owner_scoped_idempotent_and_terminal_cancel(session_factory, temporal, authorizer) -> None:
    async with session_factory() as session:
        service = ContainerJobService(
            session,
            temporal=temporal,
            workspace_authorizer=managed_session_authorizer(principal_id="u"),
        )
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


# --- AC3: durable ownership-record authorization at the submit boundary ------


def _managed_submission(*, session_id: str, managed_session_id: str) -> ContainerJobSubmitRequest:
    return ContainerJobSubmitRequest(
        idempotencyKey="k",
        source={"source": "mcp", "callerRequestId": "r", "managedSessionId": managed_session_id},
        spec={
            "image": "alpine",
            "workspaceRef": {"kind": "moonmind-session", "sessionId": session_id},
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        },
    )


@pytest.mark.asyncio
async def test_submit_absent_ownership_record_fails_closed(session_factory, temporal) -> None:
    async def lookup(identity):
        return None

    authorizer = ContainerJobWorkspaceAuthorizer(managed_session_lookup=lookup)
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal, workspace_authorizer=authorizer)
        with pytest.raises(ContainerJobWorkspaceAuthorizationError) as excinfo:
            await service.submit(
                owner=OwnerIdentity(principalId="u", principalType="user"), request=submission()
            )
    assert excinfo.value.code == "workspace_not_found"
    # No durable job identity or workflow is created for an unowned reference.
    temporal.start_container_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_terminally_deleted_record_fails_closed(session_factory, temporal) -> None:
    authorizer = managed_session_authorizer(
        record=WorkspaceOwnershipRecord(session_id="run", is_terminal=True)
    )
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal, workspace_authorizer=authorizer)
        with pytest.raises(ContainerJobWorkspaceAuthorizationError) as excinfo:
            await service.submit(
                owner=OwnerIdentity(principalId="u", principalType="user"), request=submission()
            )
    assert excinfo.value.code == "workspace_not_found"
    temporal.start_container_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_replay_survives_workspace_record_deletion(
    session_factory, temporal
) -> None:
    available = True

    async def lookup(identity):
        if not available:
            return None
        return WorkspaceOwnershipRecord(
            session_id=identity, principal_id="user-1"
        )

    owner = OwnerIdentity(principalId="user-1", principalType="user")
    request = submission()
    async with session_factory() as session:
        service = ContainerJobService(
            session,
            temporal=temporal,
            workspace_authorizer=ContainerJobWorkspaceAuthorizer(
                managed_session_lookup=lookup
            ),
        )
        first = await service.submit(owner=owner, request=request)
        available = False
        replay = await service.submit(owner=owner, request=request)
        assert replay.replayed is True
        assert replay.job_id == first.job_id
    assert temporal.start_container_job.await_count == 1


@pytest.mark.asyncio
async def test_submit_cross_user_ownership_record_denied(session_factory, temporal) -> None:
    authorizer = managed_session_authorizer(
        record=WorkspaceOwnershipRecord(session_id="run", principal_id="owner-a")
    )
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal, workspace_authorizer=authorizer)
        with pytest.raises(ContainerJobWorkspaceAuthorizationError) as excinfo:
            await service.submit(
                owner=OwnerIdentity(principalId="attacker", principalType="user"),
                request=submission(),
            )
    assert excinfo.value.code == "permission_denied"
    temporal.start_container_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_cross_session_reference_denied(session_factory, temporal) -> None:
    # Attacker names the victim's session as the locator but the API only
    # authenticated the attacker's own managed session id.
    async def lookup(identity):
        if identity == "victim":
            return WorkspaceOwnershipRecord(session_id="victim")
        return None

    authorizer = ContainerJobWorkspaceAuthorizer(managed_session_lookup=lookup)
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal, workspace_authorizer=authorizer)
        with pytest.raises(ContainerJobWorkspaceAuthorizationError) as excinfo:
            await service.submit(
                owner=OwnerIdentity(principalId="attacker", principalType="user"),
                request=_managed_submission(session_id="victim", managed_session_id="attacker"),
            )
    assert excinfo.value.code == "permission_denied"
    temporal.start_container_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_unconfigured_kind_fails_closed(session_factory, temporal) -> None:
    # Only the managed-session lookup is wired; an Omnigent reference cannot be
    # proven owned and must fail closed rather than fall open.
    authorizer = managed_session_authorizer()
    omnigent_request = ContainerJobSubmitRequest(
        idempotencyKey="k",
        source={"source": "omnigent", "omnigentSessionId": "sess"},
        spec={
            "image": "alpine",
            "workspaceRef": {"kind": "omnigent-session", "sessionId": "sess"},
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        },
    )
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal, workspace_authorizer=authorizer)
        with pytest.raises(ContainerJobWorkspaceAuthorizationError) as excinfo:
            await service.submit(
                owner=OwnerIdentity(principalId="u", principalType="user"),
                request=omnigent_request,
            )
    assert excinfo.value.code == "permission_denied"
    temporal.start_container_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_artifact_requires_authenticated_owner(session_factory, temporal) -> None:
    authorizer = ContainerJobWorkspaceAuthorizer()
    artifact_request = ContainerJobSubmitRequest(
        idempotencyKey="k",
        source={"source": "workflow"},
        spec={
            "image": "alpine",
            "workspaceRef": {"kind": "artifact-workspace", "artifactRef": "art"},
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        },
    )
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal, workspace_authorizer=authorizer)
        # Default system placeholder owner is never sufficient for an artifact ref.
        with pytest.raises(ContainerJobWorkspaceAuthorizationError) as excinfo:
            await service.submit(
                owner=OwnerIdentity(principalId="container_job", principalType="system"),
                request=artifact_request,
            )
        assert excinfo.value.code == "permission_denied"
        # A genuinely authenticated principal is accepted.
        accepted = await service.submit(
            owner=OwnerIdentity(principalId="real-user", principalType="user"),
            request=artifact_request,
        )
        assert accepted.job_id
    temporal.start_container_job.assert_awaited_once()
