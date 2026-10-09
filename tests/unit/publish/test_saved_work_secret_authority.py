"""Mutable managed-secret rotation fences immutable publication admissions."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from temporalio.exceptions import ApplicationError

from api_service.db.models import Base
from api_service.services.repository_connections import RepositoryConnectionService
from api_service.services.secrets import SecretsService
from moonmind.workflows.executions.repository_contract import DEFAULT_GIT_CONNECTION_REF
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
    find_saved_work_publication_decisions,
)
from moonmind.workflows.temporal.runtime import managed_api_key_resolve
from tests.helpers.repository_connections import (
    github_pat_connection,
    github_repository_assignment,
    record_repository_connections,
)
from tests.unit.publish.test_saved_work_destination_authority import _REAL_ACCESS
from tests.unit.publish.test_saved_work_publication_journey import (
    REPOSITORY,
    _WorkerLost,
    journey,
)


@asynccontextmanager
async def managed_destination(tmp_path, monkeypatch, slug):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "base\n"}
    ) as state:
        monkeypatch.setattr(
            managed_api_key_resolve, "select_github_access_for_launch", _REAL_ACCESS
        )
        connection = github_pat_connection(DEFAULT_GIT_CONNECTION_REF, "unused")
        connection = connection.model_copy(
            update={
                "credential": connection.credential.model_copy(
                    update={
                        "credential_ref": connection.credential.credential_ref.model_copy(
                            update={"provider": "db", "key": slug}
                        )
                    }
                )
            }
        )
        engine = await record_repository_connections(
            monkeypatch,
            tmp_path,
            connection,
            assignments=(github_repository_assignment(connection.id, REPOSITORY),),
            managed_secrets={slug: "synthetic-managed-before"},
        )
        async with engine.begin() as db:
            await db.run_sync(Base.metadata.create_all)
        try:
            yield state, async_sessionmaker(engine, expire_on_commit=False)
        finally:
            await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["GITHUB_TOKEN", "GITHUB_PAT"])
@pytest.mark.parametrize("phase", ["prepare", "push", "pull_request"])
async def test_managed_secret_rotation_fences_retry_before_effect(
    tmp_path, monkeypatch, slug, phase
):
    async with managed_destination(tmp_path, monkeypatch, slug) as (state, sessions):
        contract = state.contract(
            objective="pr", baseBranch="main", strategy="additive_import"
        )
        captured = {}

        async def decisions():
            return await find_saved_work_publication_decisions(
                state.service,
                workflow_id="mm:source:saved-work-publication:x",
                operation_key=contract["publicationIdempotencyKey"],
            )

        async def rotate(attempt):
            if attempt != 1:
                return
            captured["decisions"] = await decisions()
            captured["objects"] = state.saved_bytes()
            captured["commands"] = list(state.git_commands)
            assert len(captured["decisions"]) == 1
            async with sessions() as db:
                service = RepositoryConnectionService(db)
                before = await service.get_connection(
                    DEFAULT_GIT_CONNECTION_REF,
                    principal_ref="system:deployment",
                    principal_scope=("system", None),
                )
                rotated = await SecretsService.rotate_secret(
                    db,
                    slug,
                    "synthetic-managed-after",
                    validator=lambda _candidate: True,
                    request_id="test-managed-rotation",
                    expected_credential_revision=1,
                )
                assert rotated.credential_revision == 2
            # Simulate a new acquisition session, independent of notifications.
            async with sessions() as db:
                after = await RepositoryConnectionService(db).get_connection(
                    DEFAULT_GIT_CONNECTION_REF,
                    principal_ref="system:deployment",
                    principal_scope=("system", None),
                )
                assert after.credential_revision == before.credential_revision
                assert after.policy_revision == before.policy_revision
            if phase == "prepare":
                raise _WorkerLost()

        activity_name = f"publication_recovery.saved_work_{phase}"
        (state.after if phase == "prepare" else state.hooks)[activity_name] = rotate
        with pytest.raises(ApplicationError) as exc:
            await state.run(contract)
        assert exc.value.type == "PUBLICATION_AUTHORITY_CHANGED"
        assert await decisions() == captured["decisions"]
        assert state.git_commands == captured["commands"]
        assert state.saved_objects_unchanged(captured["objects"])
        assert len(state.pushes()) == (1 if phase == "pull_request" else 0)
        assert state.provider.creates == []
        if phase == "prepare":
            assert state.attempts[activity_name] == 2
        # A new runtime and artifact repository, with a new database session,
        # must recover the same retained admission rather than reacquire the
        # rotated identity as a fresh decision after a worker restart.
        artifact_sessions = async_sessionmaker(
            state.service._repository._session.bind, expire_on_commit=False
        )
        async with artifact_sessions() as db:
            restarted_service = TemporalArtifactService(
                TemporalArtifactRepository(db),
                store=state.service._store,
                default_namespace=state.service._default_namespace,
            )
            restarted_runtime = TemporalAgentRuntimeActivities(
                artifact_service=restarted_service,
                client_adapter=object(),
                workspace_root=tmp_path / "restarted-worker",
            )
            with pytest.raises(ApplicationError) as restarted:
                await restarted_runtime.publication_recovery_saved_work_prepare(
                    {
                        "contract": contract,
                        "destinationWorkflowId": "mm:source:saved-work-publication:x",
                        "destinationRunId": "restarted-publication-run",
                    }
                )
            assert restarted.value.type == "PUBLICATION_AUTHORITY_CHANGED"
        assert await decisions() == captured["decisions"]
        assert state.git_commands == captured["commands"]
        assert state.saved_objects_unchanged(captured["objects"])


@pytest.mark.asyncio
async def test_atomic_managed_secret_read_carries_matching_revisions(
    tmp_path, monkeypatch
):
    async with managed_destination(tmp_path, monkeypatch, "GITHUB_TOKEN") as (
        _state,
        sessions,
    ):
        read = SecretsService.get_secret_with_revision
        returned = []

        async def rotate_after_atomic_read(cls, db, slug):
            snapshot = await read(db, slug)
            async with sessions() as writer:
                await SecretsService.rotate_secret(
                    writer,
                    slug,
                    "synthetic-managed-after",
                    validator=lambda _candidate: True,
                    request_id="test-between-read-and-consume",
                    expected_credential_revision=1,
                )
            returned.append(snapshot)
            return snapshot

        monkeypatch.setattr(
            SecretsService,
            "get_secret_with_revision",
            classmethod(rotate_after_atomic_read),
        )
        access = await managed_api_key_resolve.select_github_access_for_launch(
            DEFAULT_GIT_CONNECTION_REF,
            repository=REPOSITORY,
            required_operations=("read", "write", "branch_write"),
        )
        assert access.credential.token == "synthetic-managed-before"
        assert (
            access.credential.reference_revision
            == "db://GITHUB_TOKEN:credential:1:policy:1"
        )
        assert len(returned) == 1
        # The revision identity never includes or hashes credential material.
        assert "synthetic" not in access.credential.reference_revision
