"""Record repository connections through the production connection service.

Launch-boundary tests use this instead of replacing the connection loader, so
the selection they observe is the one the deployment's recorded records drive
(MoonLadderStudios/MoonMind#4023).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from moonmind.workflows.executions.repository_contract import (
    RepositoryAssignment,
    RepositoryConnection,
)


def github_pat_connection(
    connection_id: str,
    env_key: str,
    *,
    operations: Sequence[str] = (
        "read",
        "write",
        "branch_write",
        "review_request",
        "merge_request",
    ),
) -> RepositoryConnection:
    """A system-scope GitHub connection whose PAT is the SecretRef ``env://<key>``."""

    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": connection_id,
            "provider": "git",
            "displayName": connection_id,
            "endpointRef": "https://github.com",
            "allowedOperations": list(operations),
            "clientPolicy": {
                "pinnedVersion": "2.46.0",
                "toolBundleRef": "repository-client:git-system",
                "executableSha256": "sha256:git",
            },
            "credential": {
                "source": "secret_ref",
                "credentialRef": {"provider": "env", "key": env_key},
            },
            "ownership": {"ownerRef": "system:deployment", "scopeType": "system"},
            "hostingService": "github",
        }
    )


def github_repository_assignment(
    connection_id: str,
    repository: str,
    *,
    operations: Sequence[str] = (
        "read",
        "write",
        "branch_write",
        "review_request",
        "merge_request",
    ),
) -> RepositoryAssignment:
    """A verified grant of ``connection_id`` to the GitHub ``owner/name``."""

    return RepositoryAssignment.model_validate(
        {
            "connectionId": connection_id,
            "identity": {
                "endpoint": "https://github.com",
                "providerRepoId": f"github-id:{repository.lower()}",
                "displayName": repository,
            },
            "operations": list(operations),
        }
    )


async def record_repository_connections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *connections: RepositoryConnection,
    assignments: Sequence[RepositoryAssignment] = (),
    managed_secrets: Mapping[str, str] | None = None,
    admitted_runs: Mapping[str, Mapping[str, Any]] | None = None,
):
    """Create ``connections`` in a fresh database the launch boundary reads.

    ``assignments`` are granted through the same service,
    ``managed_secrets`` maps active managed-secret slugs to their values, and
    ``admitted_runs`` maps run workflow IDs to their recorded canonical
    parameters. Returns the engine; callers dispose it when the test ends.
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api_service.db.models import (
        Base,
        ManagedSecret,
        RepositoryConnectionAssignment,
        RepositoryConnectionAuditEvent,
        RepositoryConnectionRecord,
        RepositoryRouteDefault,
        SecretStatus,
        TemporalExecutionCanonicalRecord,
        TemporalWorkflowType,
    )
    from api_service.services.repository_connections import (
        RepositoryConnectionService,
    )

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/repository-connections.db"
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[
                    ManagedSecret.__table__,
                    RepositoryConnectionRecord.__table__,
                    RepositoryConnectionAssignment.__table__,
                    RepositoryRouteDefault.__table__,
                    RepositoryConnectionAuditEvent.__table__,
                    TemporalExecutionCanonicalRecord.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    for index, repository_connection in enumerate(connections):
        async with sessions() as session:
            await RepositoryConnectionService(session).create_connection(
                repository_connection,
                actor_ref="system:deployment",
                request_id=f"test-connection-{index}",
                principal_ref="system:deployment",
                principal_scope=("system", None),
            )
    for index, assignment in enumerate(assignments):
        async with sessions() as session:
            await RepositoryConnectionService(session).set_assignment(
                assignment,
                actor_ref="system:deployment",
                request_id=f"test-assignment-{index}",
                principal_ref="system:deployment",
                principal_scope=("system", None),
            )
    if managed_secrets:
        async with sessions() as session:
            session.add_all(
                ManagedSecret(slug=slug, ciphertext=value, status=SecretStatus.ACTIVE)
                for slug, value in managed_secrets.items()
            )
            await session.commit()
    if admitted_runs:
        async with sessions() as session:
            session.add_all(
                TemporalExecutionCanonicalRecord(
                    workflow_id=workflow_id,
                    run_id=f"run-{index}",
                    workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                    entry="user_workflow",
                    parameters=dict(parameters),
                )
                for index, (workflow_id, parameters) in enumerate(admitted_runs.items())
            )
            await session.commit()
    monkeypatch.setattr("api_service.db.base.async_session_maker", sessions)
    # ``db://`` references resolve against the same database.
    monkeypatch.setattr("moonmind.auth.resolvers.db_resolver.async_session_maker", sessions)
    return engine


class TemporalParentClient:
    """The worker client's view of each workflow's recorded Temporal parent.

    Install it as ``temporalio.activity.client`` so a child workflow's
    Activity resolves its owning run the way production reads it.
    """

    def __init__(self, parents: Mapping[str, str]) -> None:
        self.parents = dict(parents)
        self.described: list[str] = []

    def get_workflow_handle(self, workflow_id: str):
        from types import SimpleNamespace

        async def describe():
            self.described.append(workflow_id)
            return SimpleNamespace(parent_id=self.parents.get(workflow_id))

        return SimpleNamespace(describe=describe)
