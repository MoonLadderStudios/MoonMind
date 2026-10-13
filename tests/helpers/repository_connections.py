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
import pytest_asyncio

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


def github_app_connection(
    connection_id: str, repository: str = "MoonLadderStudios/MoonMind"
) -> RepositoryConnection:
    """A system-scope GitHub App connection admitted for ``repository``."""

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
                "source": "github_app",
                "appRef": "github-app:moonmind-test",
                "installationRef": "installation:123",
            },
            "ownership": {"ownerRef": "system:deployment", "scopeType": "system"},
            "hostingService": "github",
        }
    ).model_copy(update={"allowed_repository_ids": (repository,)})


def serve_repository_connections(
    monkeypatch: pytest.MonkeyPatch, connections: Mapping[str, RepositoryConnection]
) -> list[str]:
    """Serve ``connections`` as the launch's recorded connections.

    The mapping is read on every load, so a test can change a record
    mid-run. Returns the references loaded, in order.
    """

    loaded: list[str] = []

    async def _load(connection_ref: str, **_kwargs: Any):
        loaded.append(connection_ref)
        return connections.get(connection_ref)

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "load_repository_connection_for_launch",
        _load,
    )
    return loaded


def github_app_provider_edge(
    monkeypatch: pytest.MonkeyPatch,
    token: str | Sequence[str],
    *,
    repositories: Sequence[str] = ("MoonLadderStudios/MoonMind",),
    lifetime_seconds: float = 55 * 60,
) -> list[dict[str, Any]]:
    """Synthetic GitHub App provider at the production acquirer seam (#4022).

    A sequence issues its tokens in order, one per installation-token
    request, each expiring ``lifetime_seconds`` after issue. Returns the
    issuance payloads requested.
    """

    from datetime import datetime, timedelta, timezone

    from moonmind.auth import github_app_wiring as wiring

    issued: list[dict[str, Any]] = []
    tokens = [token] if isinstance(token, str) else list(token)

    async def _post(*, jwt: str, payload: dict):
        assert jwt == "test-app-jwt"
        issued.append(dict(payload))
        return {
            "token": tokens[0] if len(tokens) == 1 else tokens.pop(0),
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(seconds=lifetime_seconds)
            ).isoformat(),
            "permissions": dict(payload["permissions"]),
            "repositories": list(payload["repositories"]),
        }

    async def _get_installation(*, jwt: str):
        assert jwt == "test-app-jwt"
        return {
            "app_ref": "github-app:moonmind-test",
            "installation_ref": "installation:123",
            "account": "MoonLadderStudios",
            "repositories": list(repositories),
            "suspended": False,
        }

    real_factory = wiring.build_bound_acquirer_for_connection

    def _factory(connection, **kwargs):
        edge = {
            "resolve_secret": lambda _ref: b"fake-app-private-key",
            "make_jwt": lambda _key: "test-app-jwt",
            "http_post": _post,
            "get_installation": _get_installation,
            "app_id": "123456",
            "installation_id": "123",
            "expected_account": "MoonLadderStudios",
        }
        edge.update({k: v for k, v in kwargs.items() if v not in (None, "", (), [])})
        return real_factory(connection, **edge)

    monkeypatch.setattr(wiring, "build_bound_acquirer_for_connection", _factory)
    return issued


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


@pytest_asyncio.fixture
async def deployment_without_recorded_connections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A deployment that records no repository connection.

    Its default connection derives from the deployment's GitHub declaration,
    read through the production connection loader. A test may still record
    connections into the same database.
    """

    engine = await record_repository_connections(monkeypatch, tmp_path)
    yield
    await engine.dispose()


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
