"""Record repository connections through the production connection service.

Launch-boundary tests use this instead of replacing the connection loader, so
the selection they observe is the one the deployment's recorded records drive
(MoonLadderStudios/MoonMind#4023).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.workflows.executions.repository_contract import RepositoryConnection


def github_pat_connection(connection_id: str, env_key: str) -> RepositoryConnection:
    """A system-scope GitHub connection whose PAT is the SecretRef ``env://<key>``."""

    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": connection_id,
            "provider": "git",
            "displayName": connection_id,
            "endpointRef": "https://github.com",
            "allowedOperations": ["read", "write", "branch_write", "review_request"],
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


async def record_repository_connections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *connections: RepositoryConnection,
):
    """Create ``connections`` in a fresh database the launch boundary reads.

    Returns the engine; callers dispose it when the test ends.
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api_service.db.models import (
        Base,
        RepositoryConnectionAssignment,
        RepositoryConnectionAuditEvent,
        RepositoryConnectionRecord,
        RepositoryRouteDefault,
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
                    RepositoryConnectionRecord.__table__,
                    RepositoryConnectionAssignment.__table__,
                    RepositoryRouteDefault.__table__,
                    RepositoryConnectionAuditEvent.__table__,
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
    monkeypatch.setattr("api_service.db.base.async_session_maker", sessions)
    return engine
