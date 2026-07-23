import asyncio
import json

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base
from api_service.services.container_jobs import (
    ContainerJobAuthorizationError,
    ContainerJobEvidenceUnavailableError,
    ContainerJobIdempotencyConflictError,
    ContainerJobNotFoundError,
    ContainerJobService,
    owner_artifact_principal,
)
from api_service.services.registry_authorization import (
    PrivateImageAuthorizationPolicy,
    PrivateImageAuthorizationService,
)
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobCancelRequest,
    ContainerJobFailureClass,
    ContainerJobLogQuery,
    ContainerJobState,
    ContainerJobSubmitRequest,
    ImageObservation,
    OwnerIdentity,
    TerminalOutcome,
)


class _FakeArtifact:
    def __init__(self, *, sha256=None, size_bytes=None, metadata_json=None):
        self.sha256 = sha256
        self.size_bytes = size_bytes
        self.metadata_json = metadata_json or {}


class _FakeArtifactReader:
    """Minimal stand-in for TemporalArtifactService reads."""

    def __init__(self, *, blobs=None, artifacts=None):
        self._blobs = blobs or {}
        self._artifacts = artifacts or {}
        self.read_principals: list[str] = []

    async def read(self, *, artifact_id, principal, allow_restricted_raw=False):
        self.read_principals.append(principal)
        if artifact_id not in self._blobs:
            raise KeyError(artifact_id)
        return self._artifacts.get(artifact_id, _FakeArtifact()), self._blobs[artifact_id]

    async def get_metadata(self, *, artifact_id, principal):
        self.read_principals.append(principal)
        if artifact_id not in self._artifacts:
            raise KeyError(artifact_id)
        return self._artifacts[artifact_id], [], False, None


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
            "workspaceRef": {"kind": "sandbox", "workspaceId": "run"},
            "registryCredentialRef": credential_ref,
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        },
    )


def source_submission(
    *, source_ref: str = "moonmind-python-tests"
) -> ContainerJobSubmitRequest:
    return ContainerJobSubmitRequest(
        idempotencyKey="source-key",
        source={"source": "mcp", "callerRequestId": "source-request"},
        spec={
            "imageSourceRef": source_ref,
            "workspaceRef": {"kind": "sandbox", "workspaceId": "run"},
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
                    "principals": ["user:user-a"],
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
async def test_deployment_image_source_bypasses_caller_registry_authorization(
    session_factory, temporal
) -> None:
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal)
        owner = OwnerIdentity(principalId="user-1", principalType="user")

        accepted = await service.submit(owner=owner, request=source_submission())

        started = temporal.start_container_job.await_args.args[0]
        assert started.job_id == accepted.job_id
        assert started.request.spec.image_source_ref == "moonmind-python-tests"
        assert started.registry_authorization is None


@pytest.mark.asyncio
async def test_unknown_deployment_image_source_fails_before_durable_submission(
    session_factory, temporal
) -> None:
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal)

        with pytest.raises(ValueError, match="not configured"):
            await service.submit(
                owner=OwnerIdentity(principalId="user-1", principalType="user"),
                request=source_submission(source_ref="unknown-source"),
            )

        temporal.start_container_job.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_logs_return_bounded_owner_scoped_pages(session_factory, temporal) -> None:
    owner = OwnerIdentity(principalId="log-owner", principalType="user")
    payload = b"line-0\nline-1\nline-2\n[stderr]\nerr-0\n"
    reader = _FakeArtifactReader(blobs={"artifact://logs": payload})
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal, artifacts=reader)
        accepted = await service.submit(owner=owner, request=submission())

        empty = await service.logs(owner=owner, job_id=accepted.job_id)
        assert empty.entries == [] and empty.next_cursor is None

        await service.repository.record_observation(
            owner=owner, job_id=accepted.job_id, state=ContainerJobState.SUCCEEDED,
            logs_ref="artifact://logs",
        )
        first = await service.logs(
            owner=owner, job_id=accepted.job_id, query=ContainerJobLogQuery(limit=2)
        )
        assert [e.text for e in first.entries] == ["line-0", "line-1"]
        assert first.entries[0].stream == "stdout"
        assert first.next_cursor == "2"

        rest = await service.logs(
            owner=owner, job_id=accepted.job_id,
            query=ContainerJobLogQuery(cursor=first.next_cursor, limit=100),
        )
        assert [e.text for e in rest.entries] == ["line-2", "[stderr]", "err-0"]
        assert rest.entries[-1].stream == "stderr"
        assert rest.next_cursor is None
        # Reads use the same artifact principal the workflow published under.
        assert reader.read_principals == [owner_artifact_principal(owner)] * 2

        with pytest.raises(ValueError, match="valid offset"):
            await service.logs(
                owner=owner,
                job_id=accepted.job_id,
                query=ContainerJobLogQuery(cursor="abc"),
            )
        with pytest.raises(ValueError, match="non-negative"):
            await service.logs(
                owner=owner,
                job_id=accepted.job_id,
                query=ContainerJobLogQuery(cursor="-1"),
            )

        with pytest.raises(ContainerJobNotFoundError):
            await service.logs(
                owner=OwnerIdentity(principalId="intruder", principalType="user"),
                job_id=accepted.job_id,
            )


@pytest.mark.asyncio
async def test_artifacts_return_references_and_publication(session_factory, temporal) -> None:
    owner = OwnerIdentity(principalId="art-owner", principalType="user")
    sha = "a" * 64
    reader = _FakeArtifactReader(
        blobs={
            "artifact://manifest": json.dumps(
                {
                    "jobId": "container-job:" + "0" * 32,
                    "artifacts": [
                        {
                            "name": "outputs.tar.gz",
                            "artifactRef": "artifact://outputs",
                            "sizeBytes": 42,
                            "sha256": sha,
                            "collectionStatus": "collected",
                        }
                    ],
                    "publication": {"state": "succeeded"},
                }
            ).encode()
        },
    )
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal, artifacts=reader)
        accepted = await service.submit(owner=owner, request=submission())

        none_yet = await service.artifacts(owner=owner, job_id=accepted.job_id)
        assert none_yet.artifacts == [] and none_yet.publication.state == "not_attempted"

        await service.repository.record_observation(
            owner=owner, job_id=accepted.job_id, state=ContainerJobState.SUCCEEDED,
            publication=AuxiliaryOutcome(state="succeeded"),
            artifacts_ref="artifact://manifest",
        )
        page = await service.artifacts(owner=owner, job_id=accepted.job_id)
        assert page.publication.state == "succeeded"
        assert len(page.artifacts) == 1
        entry = page.artifacts[0]
        assert entry.artifact_ref == "artifact://outputs"
        assert entry.size_bytes == 42 and entry.sha256 == sha
        assert entry.name == "outputs.tar.gz"
        assert reader.read_principals == [owner_artifact_principal(owner)]


@pytest.mark.asyncio
async def test_incomplete_or_missing_evidence_raises_stable_error(session_factory, temporal) -> None:
    owner = OwnerIdentity(principalId="ev-owner", principalType="user")
    reader = _FakeArtifactReader(
        artifacts={"artifact://outputs": _FakeArtifact(sha256=None, size_bytes=None)}
    )
    async with session_factory() as session:
        service = ContainerJobService(session, temporal=temporal, artifacts=reader)
        accepted = await service.submit(owner=owner, request=submission())
        await service.repository.record_observation(
            owner=owner, job_id=accepted.job_id, state=ContainerJobState.SUCCEEDED,
            logs_ref="artifact://missing", artifacts_ref="artifact://outputs",
        )
        with pytest.raises(ContainerJobEvidenceUnavailableError):
            await service.logs(owner=owner, job_id=accepted.job_id)
        with pytest.raises(ContainerJobEvidenceUnavailableError):
            await service.artifacts(owner=owner, job_id=accepted.job_id)
