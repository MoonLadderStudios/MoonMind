"""Recorded destination policy guards the real saved-work publication effects."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker
from temporalio.exceptions import ApplicationError

from api_service.db.models import RepositoryConnectionAuditEvent
from api_service.services.repository_connections import RepositoryConnectionService
from moonmind.auth import github_credentials
from moonmind.workflows.executions.repository_contract import DEFAULT_GIT_CONNECTION_REF
from moonmind.workflows.temporal.artifacts import find_saved_work_publication_decisions
from moonmind.workflows.temporal.runtime import managed_api_key_resolve
from tests.helpers.repository_connections import (
    github_pat_connection,
    github_repository_assignment,
    record_repository_connections,
)
from tests.unit.publish.test_saved_work_publication_journey import (
    REPOSITORY,
    _WorkerLost,
    journey,
)

_REAL_ACCESS = managed_api_key_resolve.select_github_access_for_launch
_REAL_AMBIENT = github_credentials.resolve_github_credential
_OPERATIONS = ("read", "write", "branch_write", "review_request")


@asynccontextmanager
async def recorded_destination(
    tmp_path, monkeypatch, *, assignment=None, operations=_OPERATIONS
):
    """Use real connection records and credentials, with only GitHub transport local."""
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "base\n"}
    ) as state:
        monkeypatch.setattr(
            managed_api_key_resolve, "select_github_access_for_launch", _REAL_ACCESS
        )
        monkeypatch.setattr(
            github_credentials, "resolve_github_credential", _REAL_AMBIENT
        )
        monkeypatch.setenv("SAVED_DESTINATION_PAT_A", "synthetic-destination-a")
        monkeypatch.setenv("SAVED_DESTINATION_PAT_B", "synthetic-destination-b")
        monkeypatch.setenv("GITHUB_TOKEN", "synthetic-ambient-not-authorized")
        connection = github_pat_connection(
            DEFAULT_GIT_CONNECTION_REF, "SAVED_DESTINATION_PAT_A"
        ).model_copy(update={"allowed_operations": operations})
        engine = await record_repository_connections(
            monkeypatch,
            tmp_path,
            connection,
            assignments=() if assignment is None else (assignment,),
        )
        try:
            yield state, engine, connection
        finally:
            await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repository", "operations", "objective"),
    [
        (None, _OPERATIONS, "branch"),
        ("Other/repository", _OPERATIONS, "branch"),
        (REPOSITORY, ("read",), "branch"),
        (REPOSITORY, ("read", "write"), "branch"),
        (REPOSITORY, ("read", "write", "branch_write"), "pr"),
    ],
)
async def test_saved_publication_rejects_unassigned_or_insufficient_destination(
    tmp_path, monkeypatch, repository, operations, objective
):
    assignment = (
        None
        if repository is None
        else github_repository_assignment(
            DEFAULT_GIT_CONNECTION_REF, repository, operations=operations
        )
    )
    async with recorded_destination(tmp_path, monkeypatch, assignment=assignment) as (
        state,
        _,
        _,
    ):
        before = state.saved_bytes()
        credential_reads = []
        resolve = github_credentials.resolve_connection_github_credential

        async def read_credential(connection, *, repo):
            credential_reads.append(connection.id)
            return await resolve(connection, repo=repo)

        monkeypatch.setattr(
            github_credentials, "resolve_connection_github_credential", read_credential
        )
        with pytest.raises(ApplicationError) as exc:
            await state.run(
                state.contract(
                    objective=objective, baseBranch="main", strategy="additive_import"
                )
            )
        assert exc.value.type == "PUBLICATION_AUTHORITY_UNAVAILABLE"
        assert state.git_commands == []
        assert state.provider.creates == []
        assert credential_reads == []
        assert state.saved_objects_unchanged(before)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["credential", "policy"])
async def test_lost_prepare_ack_rejects_changed_authority_without_new_decision(
    tmp_path, monkeypatch, change
):
    assignment = github_repository_assignment(DEFAULT_GIT_CONNECTION_REF, REPOSITORY)
    async with recorded_destination(tmp_path, monkeypatch, assignment=assignment) as (
        state,
        engine,
        connection,
    ):
        contract = state.contract(
            objective="pr", baseBranch="main", strategy="additive_import"
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        captured = {}

        async def decisions():
            return await find_saved_work_publication_decisions(
                state.service,
                workflow_id="mm:source:saved-work-publication:x",
                operation_key=contract["publicationIdempotencyKey"],
            )

        async def lose_ack_after_authority_change(attempt):
            if attempt != 1:
                return
            captured["decisions"] = await decisions()
            assert len(captured["decisions"]) == 1
            captured["objects"] = state.saved_bytes()
            captured["git_commands"] = list(state.git_commands)
            updated = connection.model_copy(
                update={"display_name": "Updated destination policy"}
            )
            if change == "credential":
                updated = github_pat_connection(
                    DEFAULT_GIT_CONNECTION_REF, "SAVED_DESTINATION_PAT_B"
                ).model_copy(update={"credential_revision": 2})
            async with sessions() as session:
                await RepositoryConnectionService(session).update_connection(
                    updated,
                    actor_ref="system:deployment",
                    request_id="test-destination-update",
                    expected_policy_revision=1,
                    principal_ref="system:deployment",
                    principal_scope=("system", None),
                )
            raise _WorkerLost()

        state.after["publication_recovery.saved_work_prepare"] = (
            lose_ack_after_authority_change
        )
        with pytest.raises(ApplicationError) as exc:
            await state.run(contract)
        assert exc.value.type == "PUBLICATION_AUTHORITY_CHANGED"
        assert state.attempts["publication_recovery.saved_work_prepare"] == 2
        assert await decisions() == captured["decisions"]
        assert state.git_commands == captured["git_commands"]
        assert state.pushes() == [] and state.provider.creates == []
        assert state.saved_objects_unchanged(captured["objects"])


@pytest.mark.asyncio
async def test_branch_publication_needs_no_review_request_grant(tmp_path, monkeypatch):
    operations = ("read", "write", "branch_write")
    assignment = github_repository_assignment(
        DEFAULT_GIT_CONNECTION_REF, REPOSITORY, operations=operations
    )
    async with recorded_destination(
        tmp_path, monkeypatch, assignment=assignment, operations=operations
    ) as (state, _, _):
        result = await state.run(
            state.contract(
                objective="branch", baseBranch="main", strategy="additive_import"
            )
        )
        assert result["outcome"] == "published"
        assert result["push"]["remoteVerified"] is True
        assert state.provider.creates == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operations", [_OPERATIONS, ("read",)])
async def test_migrated_unscoped_default_keeps_only_its_recorded_operations(
    tmp_path, monkeypatch, operations
):
    async with recorded_destination(tmp_path, monkeypatch, operations=operations) as (
        state,
        engine,
        _,
    ):
        # Reproduce migration 391's classified audit identity in this fixture.
        async with engine.begin() as db:
            await db.execute(
                update(RepositoryConnectionAuditEvent)
                .where(RepositoryConnectionAuditEvent.request_id == "test-connection-0")
                .values(request_id="migration:391:legacy-github-credential")
            )
        contract = state.contract(
            objective="branch", baseBranch="main", strategy="additive_import"
        )
        if operations == ("read",):
            with pytest.raises(ApplicationError) as exc:
                await state.run(contract)
            assert exc.value.type == "PUBLICATION_AUTHORITY_UNAVAILABLE"
            assert state.git_commands == []
        else:
            result = await state.run(contract)
            assert result["outcome"] == "published"
            assert result["push"]["remoteVerified"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["saved_work_push", "saved_work_pull_request"])
@pytest.mark.parametrize("change", ["credential", "policy"])
async def test_recorded_authority_change_requires_readmission_before_effect(
    tmp_path, monkeypatch, phase, change
):
    assignment = github_repository_assignment(DEFAULT_GIT_CONNECTION_REF, REPOSITORY)
    async with recorded_destination(tmp_path, monkeypatch, assignment=assignment) as (
        state,
        engine,
        connection,
    ):
        before = state.saved_bytes()
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def change_recorded_authority(_attempt):
            updated = connection.model_copy(
                update={"display_name": "Updated destination policy"}
            )
            if change == "credential":
                updated = github_pat_connection(
                    DEFAULT_GIT_CONNECTION_REF, "SAVED_DESTINATION_PAT_B"
                ).model_copy(update={"credential_revision": 2})
            async with sessions() as session:
                await RepositoryConnectionService(session).update_connection(
                    updated,
                    actor_ref="system:deployment",
                    request_id="test-destination-update",
                    expected_policy_revision=1,
                    principal_ref="system:deployment",
                    principal_scope=("system", None),
                )

        state.hooks[f"publication_recovery.{phase}"] = change_recorded_authority
        with pytest.raises(ApplicationError) as exc:
            await state.run(
                state.contract(
                    objective="pr", baseBranch="main", strategy="additive_import"
                )
            )
        assert exc.value.type == "PUBLICATION_AUTHORITY_CHANGED"
        assert len(state.pushes()) == (0 if phase == "saved_work_push" else 1)
        assert state.provider.creates == []
        assert state.saved_objects_unchanged(before)
