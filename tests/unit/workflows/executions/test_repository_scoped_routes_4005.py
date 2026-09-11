"""Acceptance coverage for MoonLadderStudios/MoonMind#4005.

Scoped RepositoryConnections + transactional repository routes: one
database/service writer, metadata-only PAT wiring, zero-assignments-grant-
nothing, deterministic whole-bundle selection, transactional uniqueness,
rename/transfer/endpoint handling, authorization, and versioned snapshots.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    RepositoryConnectionAssignment,
    RepositoryConnectionAuditEvent,
    RepositoryConnectionRecord,
    RepositoryRouteDefault,
)
from api_service.services.repository_connections import RepositoryConnectionService
from moonmind.workflows.executions.repository_contract import (
    REPOSITORY_DENIED,
    REPOSITORY_POLICY_CONFLICT,
    REPOSITORY_ROUTE_AMBIGUOUS,
    REPOSITORY_ROUTE_CONFLICT,
    REPOSITORY_ROUTE_UNSUPPORTED,
    REPOSITORY_SETUP_REQUIRED,
    REPOSITORY_STALE_SNAPSHOT,
    RepositoryAssignment,
    RepositoryConnection,
    RepositoryIdentity,
    RepositoryRouteError,
    ScopedRouteCandidate,
    admit_legacy_free_connection,
    admit_scoped_route,
    authorize_connection_use,
    build_connection_audit_record,
    invalidate_on_owner_transfer,
    is_legacy_unrestricted_connection,
    load_connection_snapshot,
    load_repository_connection,
    normalize_endpoint,
    persist_repository_connection,
    publish_connection_snapshot,
    reconcile_default_git_connection,
    reconcile_verified_rename,
    route_diagnostic,
    route_key_for,
    scope_key_for,
    validate_connection_and_client,
    validate_endpoint_retarget,
    validate_scoped_connection_for_write,
)


def _policy() -> dict:
    return {
        "pinnedVersion": "2.46.0",
        "toolBundleRef": "tool-bundle:git-2.46",
        "executableSha256": "sha256:git",
    }


def _pat_connection(
    connection_id: str,
    *,
    owner: str = "owner:team-a",
    scope: str = "system",
    scope_ref: str | None = None,
    operations: tuple[str, ...] = ("read", "write"),
    secret_key: str = "TEAM_A_PAT",
) -> RepositoryConnection:
    ownership: dict = {
        "ownerRef": owner,
        "scopeType": scope,
        "allowedPrincipalRefs": [owner],
    }
    if scope_ref is not None:
        ownership["scopeRef"] = scope_ref
    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": connection_id,
            "provider": "git",
            "displayName": f"Connection {connection_id}",
            "endpointRef": "https://github.com",
            "allowedOperations": list(operations),
            "clientPolicy": _policy(),
            "credential": {
                "source": "secret_ref",
                "credentialRef": {"provider": "managed", "key": secret_key},
            },
            "lifecycle": "active",
            "policyRevision": 1,
            "credentialRevision": 1,
            "ownership": ownership,
            "hostingService": "github",
        }
    )


def _identity(
    repo_id: str, *, display: str = "MoonLadderStudios/MoonMind"
) -> RepositoryIdentity:
    return RepositoryIdentity(
        endpoint="https://github.com",
        providerRepoId=repo_id,
        displayName=display,
    )


def _assignment(
    connection_id: str, repo_id: str, *, operations=("read", "write")
) -> RepositoryAssignment:
    return RepositoryAssignment(
        connectionId=connection_id,
        identity=_identity(repo_id),
        operations=tuple(operations),
        revision=1,
        verified=True,
    )


@asynccontextmanager
async def route_db(tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/repository-routes-4005.db", future=True
    )
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
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
    try:
        yield sessions
    finally:
        await engine.dispose()


# -- impl-2: metadata-only PAT wiring --------------------------------------


def test_pat_credential_is_metadata_only_and_raw_bodies_rejected() -> None:
    from api_service.services.repository_connections import _credential_config

    connection = _pat_connection("repository-connection:team-a")
    dumped = connection.model_dump(by_alias=True, mode="json")
    assert dumped["credential"]["credentialRef"]["key"] == "TEAM_A_PAT"
    assert "token" not in dumped["credential"]
    validate_scoped_connection_for_write(connection)  # good connection passes

    bad = RepositoryConnection.model_validate(
        {
            **connection.model_dump(by_alias=True, mode="json"),
            "credential": {
                "source": "secret_ref",
                "credentialRef": {
                    "provider": "managed",
                    "key": "TEAM_A_PAT",
                    "extra": {"token": "ghp_" + "x" * 30},
                },
            },
        }
    )
    # Metadata-only enforcement lives at the writer boundary.
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
        _credential_config(bad)


def test_github_app_is_discriminated_variant() -> None:
    connection = RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": "repository-connection:app",
            "provider": "git",
            "displayName": "App connection",
            "endpointRef": "https://github.com",
            "allowedOperations": ["read"],
            "clientPolicy": _policy(),
            "credential": {
                "source": "github_app",
                "appRef": "github-app:moonmind",
                "installationRef": "installation:123",
            },
            "lifecycle": "active",
            "policyRevision": 1,
            "credentialRevision": 1,
            "ownership": {
                "ownerRef": "owner:team-a",
                "scopeType": "system",
                "allowedPrincipalRefs": ["owner:team-a"],
            },
            "hostingService": "github",
        }
    )
    assert connection.credential.source == "github_app"


# -- impl-3: identity and assignment semantics ------------------------------


def test_zero_assignments_grant_nothing_and_legacy_is_refused() -> None:
    identity = _identity("repo-id-1")
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_SETUP_REQUIRED"):
        admit_scoped_route(
            identity=identity,
            requested_operations=["read"],
            candidates=[],
            principal_ref="owner:team-a",
            principal_scope=("system", None),
        )
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_SETUP_REQUIRED"):
        admit_legacy_free_connection()
    # Historical semantic is preserved but classified: empty allowlist is
    # unrestricted at the legacy boundary only.
    legacy = reconcile_default_git_connection(
        client_policy=RepositoryConnection.model_validate(
            {
                "schemaVersion": "moonmind.repository-connection.v1",
                "id": "x",
                "provider": "git",
                "displayName": "x",
                "endpointRef": "https://github.com",
                "allowedOperations": ["read"],
                "clientPolicy": _policy(),
                "credential": {"source": "github_resolver"},
            }
        ).client_policy
    )
    assert is_legacy_unrestricted_connection(legacy) is True


def test_names_are_aliases_not_fabricated_ids() -> None:
    same_name_a = RepositoryIdentity(
        endpoint="https://github.example-a.com",
        providerRepoId="verified-id-1",
        displayName="acme/app",
    )
    same_name_b = RepositoryIdentity(
        endpoint="https://github.example-b.com",
        providerRepoId="verified-id-9",
        displayName="acme/app",
    )
    assert same_name_a.route_id() != same_name_b.route_id()
    with pytest.raises(ValueError):
        RepositoryIdentity(
            endpoint="https://github.com",
            providerRepoId="",
            canonicalRemote="",
            displayName="acme/app",
        )


def test_route_key_is_deterministic_for_bundle() -> None:
    identity = _identity("repo-id-1")
    assert route_key_for(
        scope_type="system",
        scope_ref=None,
        identity=identity,
        capability_bundle=["write", "read"],
    ) == route_key_for(
        scope_type="system",
        scope_ref=None,
        identity=identity,
        capability_bundle=["read", "write"],
    )


# -- impl-4: whole-bundle deterministic selection ----------------------------


def test_selection_requires_one_connection_for_whole_bundle() -> None:
    conn_a = _pat_connection("repository-connection:a", operations=("read", "write"))
    conn_b = _pat_connection("repository-connection:b", operations=("read", "write"))
    identity = _identity("repo-id-1")
    candidates = [
        ScopedRouteCandidate(connection=conn_a, assignment=_assignment(conn_a.id, "repo-id-1", operations=("read",))),
        ScopedRouteCandidate(connection=conn_b, assignment=_assignment(conn_b.id, "repo-id-1", operations=("write",))),
    ]
    # Neither connection covers the whole bundle -> setup-required, not a
    # split across credentials.
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_SETUP_REQUIRED"):
        admit_scoped_route(
            identity=identity,
            requested_operations=["read", "write"],
            candidates=candidates,
            principal_ref="owner:team-a",
            principal_scope=("system", None),
        )
    ambiguous = [
        ScopedRouteCandidate(connection=conn_a, assignment=_assignment(conn_a.id, "repo-id-1")),
        ScopedRouteCandidate(connection=conn_b, assignment=_assignment(conn_b.id, "repo-id-1")),
    ]
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_ROUTE_AMBIGUOUS"):
        admit_scoped_route(
            identity=identity,
            requested_operations=["read"],
            candidates=ambiguous,
            principal_ref="owner:team-a",
            principal_scope=("system", None),
        )


# -- impl-6: rename / transfer / endpoint -----------------------------------


def test_verified_rename_keeps_stable_object_and_transfer_invalidates() -> None:
    assignment = _assignment("repository-connection:a", "verified-id-1")
    renamed = reconcile_verified_rename(
        assignment, verified_provider_repo_id="verified-id-1", new_display_name="acme/renamed"
    )
    assert renamed.identity.display_name == "acme/renamed"
    assert renamed.identity.provider_repo_id == "verified-id-1"
    with pytest.raises(RepositoryRouteError):
        reconcile_verified_rename(
            assignment, verified_provider_repo_id="other-id", new_display_name="x"
        )
    transferred = invalidate_on_owner_transfer(assignment, new_owner_ref="owner:team-b")
    assert transferred.verified is False


def test_endpoint_retarget_requires_explicit_revision() -> None:
    validate_endpoint_retarget(
        current_endpoint="https://github.com",
        proposed_endpoint="https://github.com/",
        explicit_revision_path=False,
    )
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_ENDPOINT_RETARGET"):
        validate_endpoint_retarget(
            current_endpoint="https://github.com",
            proposed_endpoint="https://ghe.example.com",
            explicit_revision_path=False,
        )


# -- impl-7: authorization ---------------------------------------------------


def test_secret_possession_is_not_use_authority() -> None:
    connection = _pat_connection("repository-connection:a")
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
        authorize_connection_use(
            principal_ref="owner:intruder",
            principal_scope=("system", None),
            connection=connection,
            action="use",
            has_secret_possession=True,
        )


def test_denial_diagnostics_do_not_leak_metadata() -> None:
    assert route_diagnostic(REPOSITORY_DENIED, authorized_for_details=False) == REPOSITORY_DENIED
    assert (
        route_diagnostic(REPOSITORY_SETUP_REQUIRED, authorized_for_details=False)
        == REPOSITORY_DENIED
    )
    # Every route-state diagnostic maps to the same non-enumerating denial
    # for unauthorized callers (no connection-count oracle via AMBIGUOUS).
    for code in (
        REPOSITORY_ROUTE_AMBIGUOUS,
        REPOSITORY_ROUTE_CONFLICT,
        REPOSITORY_ROUTE_UNSUPPORTED,
        REPOSITORY_POLICY_CONFLICT,
    ):
        assert route_diagnostic(code, authorized_for_details=False) == REPOSITORY_DENIED
        assert route_diagnostic(code, authorized_for_details=True) == code
    # The local snapshot-file condition is identical for all callers.
    assert (
        route_diagnostic(REPOSITORY_STALE_SNAPSHOT, authorized_for_details=False)
        == REPOSITORY_STALE_SNAPSHOT
    )


def test_audit_record_is_metadata_only() -> None:
    connection = _pat_connection("repository-connection:a")
    record = build_connection_audit_record(
        actor_ref="owner:team-a",
        request_id="req-1",
        action="connection.create",
        connection=connection,
    )
    assert record.model_dump(by_alias=True)["requestId"] == "req-1"
    assert "TEAM_A_PAT" not in record.model_dump_json()


# -- impl-1/5: database writer, restart survival, transactions --------------


@pytest.mark.asyncio
async def test_two_pat_connections_and_assignments_survive_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "repository-routes-4005.db"
    engine_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(engine_url, future=True)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
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
    conn_a = _pat_connection("repository-connection:team-a", secret_key="TEAM_A_PAT")
    conn_b = _pat_connection("repository-connection:team-b", owner="owner:team-b", secret_key="TEAM_B_PAT")
    async with sessions() as session:
        service = RepositoryConnectionService(session)
        await service.create_connection(
            conn_a, actor_ref="owner:team-a", request_id="req-a",
            principal_ref="owner:team-a", principal_scope=("system", None),
        )
        await service.create_connection(
            conn_b, actor_ref="owner:team-b", request_id="req-b",
            principal_ref="owner:team-b", principal_scope=("system", None),
        )
        await service.set_assignment(
            _assignment(conn_a.id, "repo-id-1"), actor_ref="owner:team-a",
            request_id="req-a1", principal_ref="owner:team-a",
            principal_scope=("system", None),
        )
        await service.set_assignment(
            _assignment(conn_a.id, "repo-id-2"), actor_ref="owner:team-a",
            request_id="req-a2", principal_ref="owner:team-a",
            principal_scope=("system", None),
        )
        await service.set_assignment(
            _assignment(conn_b.id, "repo-id-2"), actor_ref="owner:team-b",
            request_id="req-b2", principal_ref="owner:team-b",
            principal_scope=("system", None),
        )
    await engine.dispose()

    # Restart: new engine over the same file must see everything (no second
    # editable policy store, no copied secrets).
    engine2 = create_async_engine(engine_url, future=True)
    sessions2 = sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
    async with sessions2() as session:
        rows = (await session.execute(select(RepositoryConnectionRecord))).scalars().all()
        assert {r.connection_id for r in rows} == {conn_a.id, conn_b.id}
        for row in rows:
            assert "ghp_" not in str(row.credential_config)
            assert row.credential_config["source"] == "secret_ref"
        assignments = (await session.execute(select(RepositoryConnectionAssignment))).scalars().all()
        assert len(assignments) == 3
        audits = (await session.execute(select(RepositoryConnectionAuditEvent))).scalars().all()
        assert len(audits) == 5
        service = RepositoryConnectionService(session)
        admitted = await service.list_admitted_routes(
            identity=_identity("repo-id-2"),
            principal_ref="owner:team-a",
            principal_scope=("system", None),
        )
        # team-a sees only its own connection's assignment.
        assert [c.id for c, _ in admitted] == [conn_a.id]
    await engine2.dispose()


@pytest.mark.asyncio
async def test_concurrent_default_change_and_disable_yield_valid_snapshot_or_conflict(
    tmp_path: Path,
) -> None:
    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            conn = _pat_connection("repository-connection:a")
            await service.create_connection(
                conn, actor_ref="owner:team-a", request_id="req-1",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            await service.set_assignment(
                _assignment(conn.id, "repo-id-1"), actor_ref="owner:team-a",
                request_id="req-2", principal_ref="owner:team-a",
                principal_scope=("system", None),
            )
            await service.set_route_default(
                scope_type="system", scope_ref=None, identity=_identity("repo-id-1"),
                capability_bundle=["read"], connection_id=conn.id,
                actor_ref="owner:team-a", request_id="req-3",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
        # Independent transaction disables the connection...
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            await service.disable_connection(
                conn.id, actor_ref="owner:team-a", request_id="req-4",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
        # ...so a later default write against the disabled connection fails
        # closed instead of leaving a dangling binding.
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
                await service.set_route_default(
                    scope_type="system", scope_ref=None, identity=_identity("repo-id-1"),
                    capability_bundle=["read"], connection_id=conn.id,
                    actor_ref="owner:team-a", request_id="req-5",
                    principal_ref="owner:team-a", principal_scope=("system", None),
                )
        # Assignment removal cascades the default: no dangling binding.
        conn2 = _pat_connection("repository-connection:b", owner="owner:team-b")
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            await service.create_connection(
                conn2, actor_ref="owner:team-b", request_id="req-6",
                principal_ref="owner:team-b", principal_scope=("system", None),
            )
            await service.set_assignment(
                _assignment(conn2.id, "repo-id-9"), actor_ref="owner:team-b",
                request_id="req-7", principal_ref="owner:team-b",
                principal_scope=("system", None),
            )
            await service.set_route_default(
                scope_type="system", scope_ref=None, identity=_identity("repo-id-9", display="x"),
                capability_bundle=["read"], connection_id=conn2.id,
                actor_ref="owner:team-b", request_id="req-8",
                principal_ref="owner:team-b", principal_scope=("system", None),
            )
            await service.remove_assignment(
                connection_id=conn2.id, identity=_identity("repo-id-9", display="x"),
                actor_ref="owner:team-b", request_id="req-9",
                principal_ref="owner:team-b", principal_scope=("system", None),
            )
            remaining = (
                await session.execute(select(RepositoryRouteDefault))
            ).scalars().all()
            assert [d for d in remaining if d.connection_id == conn2.id] == []


@pytest.mark.asyncio
async def test_revision_compare_and_workspace_scope_and_id_reuse(tmp_path: Path) -> None:
    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            ws = _pat_connection(
                "repository-connection:ws", owner="owner:ws",
                scope="workspace", scope_ref="workspace:1",
            )
            await service.create_connection(
                ws, actor_ref="owner:ws", request_id="req-1",
                principal_ref="owner:ws", principal_scope=("workspace", "workspace:1"),
            )
            # Stale expected revision fails with a policy conflict.
            with pytest.raises(
                RepositoryRouteError, match="REPOSITORY_POLICY_CONFLICT"
            ):
                await service.update_connection(
                    ws, actor_ref="owner:ws", request_id="req-2",
                    expected_policy_revision=999,
                    principal_ref="owner:ws",
                    principal_scope=("workspace", "workspace:1"),
                )
            # Wrong workspace scope is denied without metadata leakage.
            with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
                await service.disable_connection(
                    ws.id, actor_ref="owner:other", request_id="req-3",
                    principal_ref="owner:other",
                    principal_scope=("workspace", "workspace:other"),
                )
            # Deletion tombstones the id: reuse is refused.
            await service.delete_connection(
                ws.id, actor_ref="owner:ws", request_id="req-4",
                principal_ref="owner:ws",
                principal_scope=("workspace", "workspace:1"),
                has_active_bindings=lambda _: False,
            )
            with pytest.raises(RepositoryRouteError, match="REPOSITORY_ID_REUSE"):
                await service.create_connection(
                    ws, actor_ref="owner:ws", request_id="req-5",
                    principal_ref="owner:ws",
                    principal_scope=("workspace", "workspace:1"),
                )


@pytest.mark.asyncio
async def test_guessed_secretref_and_unauthorized_attach_fail(tmp_path: Path) -> None:
    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            conn = _pat_connection("repository-connection:a")
            await service.create_connection(
                conn, actor_ref="owner:team-a", request_id="req-1",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
                await service.set_assignment(
                    _assignment(conn.id, "repo-id-1"), actor_ref="owner:intruder",
                    request_id="req-2", principal_ref="owner:intruder",
                    principal_scope=("system", None),
                )


# -- impl-8: snapshots, legacy boundary, existing checks --------------------


def test_snapshot_publish_load_and_stale_rejection(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    connections = [_pat_connection("repository-connection:a")]
    snapshot = publish_connection_snapshot(connections, path, revision=3)
    assert load_connection_snapshot(path, minimum_revision=3) == snapshot
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_STALE_SNAPSHOT"):
        load_connection_snapshot(path, minimum_revision=4)
    # Digest tampering fails closed (never silently used).
    raw = path.read_text(encoding="utf-8").replace("team-a", "team-b")
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_STALE_SNAPSHOT"):
        load_connection_snapshot(path)


def test_existing_legacy_persist_load_and_client_checks_still_hold(tmp_path: Path) -> None:
    from moonmind.workflows.executions.repository_contract import (
        RepositoryClientEvidence,
        compile_repository_target,
    )

    target = compile_repository_target(
        {
            "provider": "git",
            "repository": {"name": "MoonLadderStudios/MoonMind"},
            "branch": {"name": "main"},
        }
    )
    connection = reconcile_default_git_connection(
        client_policy=_pat_connection("x").client_policy
    )
    path = tmp_path / "legacy.json"
    persist_repository_connection(connection, path)
    assert load_repository_connection(path, connection.id) == connection
    evidence = RepositoryClientEvidence(
        toolBundleRef="tool-bundle:git-2.46",
        clientVersion="2.46.0",
        executableSha256="sha256:git",
    )
    validate_connection_and_client(target, connection, evidence, operation="read")


# -- impl-9: automated-review remediation -------------------------------------


def _lore_connection(connection_id: str) -> RepositoryConnection:
    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": connection_id,
            "provider": "lore",
            "displayName": f"Lore {connection_id}",
            "endpointRef": "lore://tactics",
            "allowedOperations": ["read", "merge_request"],
            "clientPolicy": _policy(),
            "projection": {
                "provider": "github",
                "repository": "acme/app",
                "authority": "review_only",
                "statusSourceRef": "status:1",
            },
            "mergeCoordinator": {
                "endpointRef": "lore://tactics",
                "policyRef": "policy:1",
                "supportedProtocolVersion": "1",
            },
            "credential": {
                "source": "secret_ref",
                "credentialRef": {"provider": "managed", "key": "LORE_KEY"},
            },
            "lifecycle": "active",
            "policyRevision": 1,
            "credentialRevision": 1,
            "ownership": {
                "ownerRef": "owner:team-a",
                "scopeType": "system",
                "allowedPrincipalRefs": ["owner:team-a"],
            },
            "hostingService": "lore",
        }
    )


def test_scheme_aware_default_ports() -> None:
    assert normalize_endpoint("http://git.example:80") == "http://git.example"
    assert normalize_endpoint("https://git.example:443") == "https://git.example"
    # Non-default ports for the scheme are distinct network authorities.
    assert normalize_endpoint("http://git.example:443") == "http://git.example:443"
    assert normalize_endpoint("https://git.example:80") == "https://git.example:80"


def test_scope_key_for_system_and_workspace() -> None:
    assert scope_key_for("system", None) == "system"
    assert scope_key_for("workspace", "workspace:1") == "workspace:workspace:1"


def test_mutation_actions_require_owner_or_system() -> None:
    from moonmind.workflows.executions.repository_contract import (
        ConnectionOwnershipPolicy,
    )

    teammate = _pat_connection("repository-connection:shared").model_copy(
        update={
            "ownership": ConnectionOwnershipPolicy(
                ownerRef="owner:team-a",
                scopeType="system",
                allowedPrincipalRefs=["owner:team-a", "owner:team-b", "owner:ops"],
            )
        }
    )
    # Admitted teammate may use but not administer.
    authorize_connection_use(
        principal_ref="owner:team-b",
        principal_scope=("workspace", "workspace:1"),
        connection=teammate,
        action="use",
    )
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
        authorize_connection_use(
            principal_ref="owner:team-b",
            principal_scope=("workspace", "workspace:1"),
            connection=teammate,
            action="edit",
        )
    # Owner administers; an admitted system principal administers too.
    authorize_connection_use(
        principal_ref="owner:team-a",
        principal_scope=("workspace", "workspace:1"),
        connection=teammate,
        action="delete",
    )
    authorize_connection_use(
        principal_ref="owner:ops",
        principal_scope=("system", None),
        connection=teammate,
        action="disable",
    )
    # A system principal outside the admission list administers nothing.
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
        authorize_connection_use(
            principal_ref="owner:stranger",
            principal_scope=("system", None),
            connection=teammate,
            action="disable",
        )


def test_disabled_connection_permits_owner_admin_but_not_use() -> None:
    connection = _pat_connection("repository-connection:a")
    disabled = connection.model_copy(update={"lifecycle": "disabled"})
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
        authorize_connection_use(
            principal_ref="owner:team-a",
            principal_scope=("system", None),
            connection=disabled,
            action="use",
        )
    # Owner cleanup/reactivation paths stay available while disabled.
    for action in ("edit", "detach", "delete", "disable"):
        authorize_connection_use(
            principal_ref="owner:team-a",
            principal_scope=("system", None),
            connection=disabled,
            action=action,
        )
    deleted = connection.model_copy(update={"lifecycle": "deleted"})
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
        authorize_connection_use(
            principal_ref="owner:team-a",
            principal_scope=("system", None),
            connection=deleted,
            action="delete",
        )


def test_legacy_resolver_and_matrix_rejected_on_write() -> None:
    from moonmind.workflows.executions.repository_contract import (
        GitHubAppCredential,
        GitHubResolverCredential,
    )

    connection = _pat_connection("repository-connection:a")
    legacy = connection.model_copy(
        update={"credential": GitHubResolverCredential(source="github_resolver")}
    )
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
        validate_scoped_connection_for_write(legacy)
    # Complete provider x hosting x credential matrix.
    bad_host = connection.model_copy(update={"hosting_service": "lore"})
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
        validate_scoped_connection_for_write(bad_host)
    app_on_generic = connection.model_copy(
        update={
            "hosting_service": "generic_git",
            "credential": GitHubAppCredential(
                source="github_app",
                appRef="github-app:moonmind",
                installationRef="installation:123",
            ),
        }
    )
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
        validate_scoped_connection_for_write(app_on_generic)
    lore_on_generic = _lore_connection("repository-connection:lore").model_copy(
        update={"hosting_service": "generic_git"}
    )
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
        validate_scoped_connection_for_write(lore_on_generic)
    # Compatible combinations still validate.
    validate_scoped_connection_for_write(
        connection.model_copy(update={"hosting_service": "generic_git"})
    )
    validate_scoped_connection_for_write(_lore_connection("repository-connection:lore"))


def test_secret_extra_payload_rejected_regardless_of_key() -> None:
    from api_service.services.repository_connections import _credential_config

    connection = _pat_connection("repository-connection:a")
    smuggled = RepositoryConnection.model_validate(
        {
            **connection.model_dump(by_alias=True, mode="json"),
            "credential": {
                "source": "secret_ref",
                "credentialRef": {
                    "provider": "managed",
                    "key": "TEAM_A_PAT",
                    "extra": {"accessToken": "x" * 30},
                },
            },
        }
    )
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
        _credential_config(smuggled)


def test_snapshot_revision_and_producer_tamper_fail_closed(tmp_path: Path) -> None:
    import json

    path = tmp_path / "snapshot.json"
    publish_connection_snapshot(
        [_pat_connection("repository-connection:a")], path, revision=3
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["revision"] = 4
    path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_STALE_SNAPSHOT"):
        load_connection_snapshot(path)
    raw["revision"] = 3
    raw["producer"] = "someone-else.v1"
    path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    with pytest.raises(RepositoryRouteError, match="REPOSITORY_STALE_SNAPSHOT"):
        load_connection_snapshot(path)


@pytest.mark.asyncio
async def test_credential_rotation_requires_revision_advance(tmp_path: Path) -> None:
    from moonmind.workflows.executions.repository_contract import SecretRefCredential

    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            conn = _pat_connection("repository-connection:a")
            await service.create_connection(
                conn, actor_ref="owner:team-a", request_id="req-1",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            # Ordinary policy edit must not move the credential revision.
            bumped = conn.model_copy(
                update={"display_name": "Renamed", "credential_revision": 2}
            )
            with pytest.raises(
                RepositoryRouteError, match="REPOSITORY_POLICY_CONFLICT"
            ):
                await service.update_connection(
                    bumped, actor_ref="owner:team-a", request_id="req-2",
                    expected_policy_revision=1,
                    principal_ref="owner:team-a", principal_scope=("system", None),
                )
            # Credential change without an advance is refused.
            rotated = conn.model_copy(
                update={
                    "credential": SecretRefCredential(
                        source="secret_ref",
                        credentialRef={"provider": "managed", "key": "TEAM_B_PAT"},
                    )
                }
            )
            with pytest.raises(
                RepositoryRouteError, match="REPOSITORY_POLICY_CONFLICT"
            ):
                await service.update_connection(
                    rotated, actor_ref="owner:team-a", request_id="req-3",
                    expected_policy_revision=1,
                    principal_ref="owner:team-a", principal_scope=("system", None),
                )
            # Rotation with exactly one advance succeeds.
            rotated_once = rotated.model_copy(update={"credential_revision": 2})
            out = await service.update_connection(
                rotated_once, actor_ref="owner:team-a", request_id="req-4",
                expected_policy_revision=1,
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            assert out.policy_revision == 2
            assert out.credential_revision == 2
            assert out.credential.credential_ref.key == "TEAM_B_PAT"


@pytest.mark.asyncio
async def test_endpoint_retarget_invalidates_routes(tmp_path: Path) -> None:
    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            conn = _pat_connection("repository-connection:a")
            await service.create_connection(
                conn, actor_ref="owner:team-a", request_id="req-1",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            await service.set_assignment(
                _assignment(conn.id, "repo-id-1"), actor_ref="owner:team-a",
                request_id="req-2", principal_ref="owner:team-a",
                principal_scope=("system", None),
            )
            await service.set_route_default(
                scope_type="system", scope_ref=None, identity=_identity("repo-id-1"),
                capability_bundle=["read"], connection_id=conn.id,
                actor_ref="owner:team-a", request_id="req-3",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            retargeted = conn.model_copy(
                update={
                    "endpoint_ref": "https://ghe.example.com",
                    "credential_revision": 2,
                }
            )
            await service.update_connection(
                retargeted, actor_ref="owner:team-a", request_id="req-4",
                expected_policy_revision=1,
                principal_ref="owner:team-a", principal_scope=("system", None),
                explicit_endpoint_revision=True,
            )
            assignments = (
                await session.execute(select(RepositoryConnectionAssignment))
            ).scalars().all()
            assert assignments == []
            defaults = (
                await session.execute(select(RepositoryRouteDefault))
            ).scalars().all()
            assert defaults == []


@pytest.mark.asyncio
async def test_system_scope_creation_requires_system_principal(tmp_path: Path) -> None:
    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            conn = _pat_connection("repository-connection:sys", owner="owner:ws")
            with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
                await service.create_connection(
                    conn, actor_ref="owner:ws", request_id="req-1",
                    principal_ref="owner:ws",
                    principal_scope=("workspace", "workspace:1"),
                )


@pytest.mark.asyncio
async def test_route_default_scope_must_match_principal(tmp_path: Path) -> None:
    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            conn = _pat_connection(
                "repository-connection:a", owner="owner:a",
                scope="workspace", scope_ref="workspace:a",
            )
            await service.create_connection(
                conn, actor_ref="owner:a", request_id="req-1",
                principal_ref="owner:a",
                principal_scope=("workspace", "workspace:a"),
            )
            with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
                await service.set_route_default(
                    scope_type="workspace", scope_ref="workspace:b",
                    identity=_identity("repo-id-1"),
                    capability_bundle=["read"], connection_id=conn.id,
                    actor_ref="owner:a", request_id="req-2",
                    principal_ref="owner:a",
                    principal_scope=("workspace", "workspace:a"),
                )


@pytest.mark.asyncio
async def test_assignment_revision_conflict_and_replay_returns_stored(
    tmp_path: Path,
) -> None:
    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            conn = _pat_connection("repository-connection:a")
            await service.create_connection(
                conn, actor_ref="owner:team-a", request_id="req-1",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            first = await service.set_assignment(
                _assignment(conn.id, "repo-id-1"), actor_ref="owner:team-a",
                request_id="req-2", principal_ref="owner:team-a",
                principal_scope=("system", None),
            )
            assert first.revision == 1
            # Replaying the same request identity for a different payload
            # returns the persisted assignment instead of granting the new one.
            replayed = await service.set_assignment(
                _assignment(conn.id, "repo-id-1", operations=("read",)),
                actor_ref="owner:team-a", request_id="req-2",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            assert tuple(replayed.operations) == ("read", "write")
            assert replayed.revision == 1
            # Concurrent editors conflict instead of losing permission edits.
            second = await service.set_assignment(
                _assignment(conn.id, "repo-id-1", operations=("read",)),
                actor_ref="owner:team-a", request_id="req-3",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            assert second.revision == 2
            assert tuple(second.operations) == ("read",)
            with pytest.raises(
                RepositoryRouteError, match="REPOSITORY_POLICY_CONFLICT"
            ):
                await service.set_assignment(
                    _assignment(conn.id, "repo-id-1", operations=("read", "write")),
                    actor_ref="owner:team-a", request_id="req-4",
                    principal_ref="owner:team-a", principal_scope=("system", None),
                )


@pytest.mark.asyncio
async def test_delete_requires_binding_authority(tmp_path: Path) -> None:
    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            conn = _pat_connection("repository-connection:a")
            await service.create_connection(
                conn, actor_ref="owner:team-a", request_id="req-1",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            with pytest.raises(RepositoryRouteError, match="REPOSITORY_ROUTE_CONFLICT"):
                await service.delete_connection(
                    conn.id, actor_ref="owner:team-a", request_id="req-2",
                    principal_ref="owner:team-a", principal_scope=("system", None),
                    has_active_bindings=lambda _: True,
                )
            with pytest.raises(RepositoryRouteError, match="REPOSITORY_SETUP_REQUIRED"):
                await service.delete_connection(
                    conn.id, actor_ref="owner:team-a", request_id="req-3",
                    principal_ref="owner:team-a", principal_scope=("system", None),
                    has_active_bindings=None,  # type: ignore[arg-type]
                )
            await service.delete_connection(
                conn.id, actor_ref="owner:team-a", request_id="req-4",
                principal_ref="owner:team-a", principal_scope=("system", None),
                has_active_bindings=lambda _: False,
            )


@pytest.mark.asyncio
async def test_replay_is_reauthorized(tmp_path: Path) -> None:
    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            conn = _pat_connection("repository-connection:a")
            await service.create_connection(
                conn, actor_ref="owner:team-a", request_id="req-1",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            # Guessing a prior request identity discloses nothing to an
            # unadmitted principal.
            with pytest.raises(RepositoryRouteError, match="REPOSITORY_DENIED"):
                await service.create_connection(
                    conn, actor_ref="owner:intruder", request_id="req-1",
                    principal_ref="owner:intruder",
                    principal_scope=("workspace", "workspace:intruder"),
                )


@pytest.mark.asyncio
async def test_lore_projection_survives_round_trip(tmp_path: Path) -> None:
    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            conn = _lore_connection("repository-connection:lore")
            created = await service.create_connection(
                conn, actor_ref="owner:team-a", request_id="req-1",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            assert created.projection is not None
            assert created.merge_coordinator is not None
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            visible = await service.export_snapshot_connections(
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            (reloaded,) = [c for c in visible if c.id == conn.id]
            assert reloaded.projection is not None
            assert reloaded.projection.repository == "acme/app"
            assert reloaded.merge_coordinator is not None
            renamed = reloaded.model_copy(update={"display_name": "Lore renamed"})
            updated = await service.update_connection(
                renamed, actor_ref="owner:team-a", request_id="req-2",
                expected_policy_revision=1,
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            assert updated.projection is not None
            assert updated.merge_coordinator is not None


@pytest.mark.asyncio
async def test_system_default_uniqueness_single_row(tmp_path: Path) -> None:
    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            service = RepositoryConnectionService(session)
            conn = _pat_connection("repository-connection:a")
            await service.create_connection(
                conn, actor_ref="owner:team-a", request_id="req-1",
                principal_ref="owner:team-a", principal_scope=("system", None),
            )
            await service.set_assignment(
                _assignment(conn.id, "repo-id-1"), actor_ref="owner:team-a",
                request_id="req-2", principal_ref="owner:team-a",
                principal_scope=("system", None),
            )
            for request_id in ("req-3", "req-4"):
                await service.set_route_default(
                    scope_type="system", scope_ref=None,
                    identity=_identity("repo-id-1"),
                    capability_bundle=["read"], connection_id=conn.id,
                    actor_ref="owner:team-a", request_id=request_id,
                    principal_ref="owner:team-a", principal_scope=("system", None),
                )
            defaults = (
                await session.execute(select(RepositoryRouteDefault))
            ).scalars().all()
            assert len(defaults) == 1
            assert defaults[0].scope_key == "system"
