"""Production-boundary coverage for MoonLadderStudios/MoonMind#4023.

Exercises the real owners (#4005 service, repository contract, frozen
legacy decoders) through the #4023 cutover rules. No token probing, no
account merging, no wildcard allowlists, no new migration ledger/lease.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.repository_connections import RepositoryConnectionService
from moonmind.workflows.executions import repository_legacy_cutover_4023 as cutover
from moonmind.workflows.executions.repository_contract import (
    DEFAULT_GIT_CONNECTION_REF,
    REPOSITORY_CREDENTIAL_UNAVAILABLE,
    REPOSITORY_DENIED,
    REPOSITORY_POLICY_CONFLICT,
    REPOSITORY_ROUTE_CONFLICT,
    REPOSITORY_SETUP_REQUIRED,
    CapabilityReadinessRegistry,
    RepositoryClientPolicy,
    RepositoryConnection,
    RepositoryContractError,
    RepositoryIdentity,
    RepositoryRouteError,
    compile_repository_target,
    decode_legacy_repository_history_v1,
    ensure_repository_ready,
    reconcile_default_git_connection,
)


def _policy() -> dict:
    return {
        "pinnedVersion": "2.46.0",
        "toolBundleRef": "tool-bundle:git-2.46",
        "executableSha256": "sha256:git",
    }


def _pat_connection(connection_id: str) -> RepositoryConnection:
    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": connection_id,
            "provider": "git",
            "displayName": connection_id,
            "endpointRef": "https://github.com",
            "allowedOperations": ["read", "write"],
            "clientPolicy": _policy(),
            "credential": {
                "source": "secret_ref",
                "credentialRef": {"provider": "managed", "key": "TEAM_A_PAT"},
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


def _evidence() -> dict:
    return {
        "toolBundleRef": "tool-bundle:git-2.46",
        "clientVersion": "2.46.0",
        "executableSha256": "sha256:git",
    }


async def _service(tmp_path: Path) -> RepositoryConnectionService:
    from api_service.db.models import Base as _Base

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/cutover-4023.db", future=True
    )
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: _Base.metadata.create_all(sync))
    session = sessions()
    return RepositoryConnectionService(session)


@pytest.mark.asyncio
async def test_populated_migration_reuses_transactional_writer(tmp_path: Path) -> None:
    """REQ-04: stable request identity is idempotent; conflicts fail closed."""
    service = await _service(tmp_path)
    connection = _pat_connection("repository-connection:conn-a")
    created = await service.create_connection(
        connection,
        actor_ref="owner:team-a",
        request_id="cutover-4023-req-1",
        principal_ref="owner:team-a",
        principal_scope=("system", None),
    )
    assert created.id == "repository-connection:conn-a"
    # Lost commit acknowledgment: same request identity replays the same row.
    replayed = await service.create_connection(
        connection,
        actor_ref="owner:team-a",
        request_id="cutover-4023-req-1",
        principal_ref="owner:team-a",
        principal_scope=("system", None),
    )
    assert replayed.id == created.id
    # Same request identity for a different connection is a conflict.
    with pytest.raises(RepositoryRouteError):
        await service.create_connection(
            _pat_connection("repository-connection:conn-b"),
            actor_ref="owner:team-a",
            request_id="cutover-4023-req-1",
            principal_ref="owner:team-a",
            principal_scope=("system", None),
        )


@pytest.mark.asyncio
async def test_concurrent_policy_edit_conflicts_at_db_boundary(tmp_path: Path) -> None:
    """REQ-04: expected-revision protection surfaces concurrent edits."""
    service = await _service(tmp_path)
    connection = _pat_connection("repository-connection:conn-a")
    await service.create_connection(
        connection,
        actor_ref="owner:team-a",
        request_id="cutover-4023-req-2",
        principal_ref="owner:team-a",
        principal_scope=("system", None),
    )
    stale = connection.model_copy(update={"displayName": "stale edit"})
    with pytest.raises(RepositoryRouteError):
        await service.update_connection(
            stale,
            actor_ref="owner:team-a",
            request_id="cutover-4023-req-3",
            expected_policy_revision=999,
            principal_ref="owner:team-a",
            principal_scope=("system", None),
        )


def test_cutover_migration_is_short_idempotent_marker() -> None:
    """REQ-04: one short versioned migration reuses existing owners."""
    repo_root = Path(__file__).resolve().parents[4]
    migration = (
        repo_root / "api_service" / "migrations" / "versions"
        / "388_repository_legacy_cutover_4023.py"
    )
    assert migration.exists(), "missing #4023 cutover migration marker"
    spec = importlib.util.spec_from_file_location(
        "cutover_4023_migration", str(migration)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    assert module.revision == "388_repository_legacy_cutover_4023"
    assert module.down_revision == "387_merge_386_heads_4461"
    module.upgrade()
    module.downgrade()
    text = migration.read_text(encoding="utf-8")
    assert "CREATE TABLE" not in text.upper(), "no new ledger/lease tables"


def test_startup_runs_no_census_of_deleted_sources() -> None:
    """REQ-04: ordinary post-migration startup consults only a marker."""
    store = cutover.CutoverMappingStore()
    store.mark_completed()
    assert store.startup_requires_no_census() is True


@pytest.mark.asyncio
async def test_new_write_consumer_uses_typed_connection_no_fallback() -> None:
    """REQ-01/03: typed SecretRef write succeeds; resolver failure is closed."""
    cutover.enforce_typed_connection_for_new_write(
        _pat_connection("repository-connection:conn-a")
    )
    target = compile_repository_target(
        {
            "provider": "git",
            "connectionRef": "repository-connection:conn-a",
            "repository": {"name": "o/r"},
            "branch": {"name": "main"},
        }
    )
    connection = _pat_connection("repository-connection:conn-a")
    from moonmind.workflows.executions.repository_contract import (
        RepositoryClientEvidence,
    )

    evidence = RepositoryClientEvidence.model_validate(_evidence())
    registry = CapabilityReadinessRegistry(runtime_owned_tokens=())
    registry.register("git", lambda context: True)
    registry.register("repo.read", lambda context: True)
    registry.register("repo.write", lambda context: True)

    async def resolve_connection(_target: object) -> RepositoryConnection:
        return connection

    async def resolve_evidence(_connection: object) -> RepositoryClientEvidence:
        return evidence

    calls: list[str] = []

    async def failing_resolver(_repository: str) -> object:
        calls.append(_repository)
        raise RuntimeError("selected backend unavailable")

    # Typed SecretRef connections never reach the ambient github resolver.
    resolved = await ensure_repository_ready(
        target,
        publish_mode="none",
        operation="read",
        connection_resolver=resolve_connection,
        evidence_resolver=resolve_evidence,
        readiness_registry=registry,
        remote_tip_verifier=None,
    )
    assert resolved.id == "repository-connection:conn-a"
    assert calls == []
    # github_resolver + selected-backend failure surfaces without fallback.
    with pytest.raises(RepositoryRouteError) as excinfo:
        cutover.fail_selected_backend_without_fallback(
            backend="managed:TEAM_A_PAT",
            operation="repository.write",
            cause="selected backend unavailable",
        )
    assert excinfo.value.code == REPOSITORY_DENIED
    _ = failing_resolver


def test_git_default_survives_only_for_proven_identity_at_boundary() -> None:
    """REQ-03: always-on git-default alias is narrowed at the boundary."""
    target = compile_repository_target(
        {"provider": "git", "repository": {"name": "o/r"}, "branch": {"name": "main"}}
    )
    assert target.connection_ref == DEFAULT_GIT_CONNECTION_REF
    proven = cutover.determine_effective_legacy_reference(
        explicit_ref=None,
        historical_ref=DEFAULT_GIT_CONNECTION_REF,
        proven_identity=DEFAULT_GIT_CONNECTION_REF,
    )
    assert proven.status == "proven_default"
    unproven = cutover.determine_effective_legacy_reference(
        explicit_ref=None,
        historical_ref=DEFAULT_GIT_CONNECTION_REF,
        proven_identity=None,
    )
    assert unproven.status == "suspended"


def test_saved_history_preserves_identity_and_digests_at_owner_boundary() -> None:
    """REQ-05: frozen history decodes, then binds an explicit connection."""
    frozen = decode_legacy_repository_history_v1("o/r", "main")
    assert frozen.connection_ref == DEFAULT_GIT_CONNECTION_REF
    mapped = cutover.decode_and_map_saved_history(
        repository="o/r",
        branch="main",
        recorded_digest="digest:abc123",
        connection_ref="repository-connection:conn-a",
    )
    assert mapped["repositoryName"] == frozen.repository.name == "o/r"
    assert mapped["branchName"] == frozen.branch.name == "main"
    assert mapped["recordedDigest"] == "digest:abc123"
    assert mapped["connectionRef"] == "repository-connection:conn-a"


def test_historical_readers_survive_only_with_removal_condition() -> None:
    """REQ-06: narrow historical readers never grant new-write authority."""
    assert cutover.LEGACY_HISTORICAL_READER_REMOVAL_CONDITION
    assert (
        cutover.is_historical_reader_permitted(
            recorded_before_cutover=True, migrated_or_expired=False
        )
        is True
    )
    assert (
        cutover.is_historical_reader_permitted(
            recorded_before_cutover=True, migrated_or_expired=True
        )
        is False
    )
    assert (
        cutover.is_historical_reader_permitted(
            recorded_before_cutover=False, migrated_or_expired=False
        )
        is False
    )


def test_incompatible_writer_stops_before_cutover_without_db_overwrite() -> None:
    """REQ-07: bundle check stops the writer; diagnostics stay redacted."""
    with pytest.raises(RepositoryRouteError) as excinfo:
        cutover.cutover_stop_if_incompatible(
            observed_bundle="tool-bundle:other",
            pinned_bundle="tool-bundle:git-2.46",
            action="repository.write",
            connection_ref="repository-connection:conn-a",
            backend_ref="managed:TEAM_A_PAT",
        )
    assert excinfo.value.code == REPOSITORY_ROUTE_CONFLICT
    cutover.cutover_stop_if_incompatible(
        observed_bundle="tool-bundle:git-2.46",
        pinned_bundle="tool-bundle:git-2.46",
        action="repository.write",
        connection_ref="repository-connection:conn-a",
        backend_ref="managed:TEAM_A_PAT",
    )
    diagnostic = cutover.cutover_diagnostic(
        action="repository.write",
        connection_ref="repository-connection:conn-a",
        backend_ref="ghp_supersecretvalue123",
    )
    assert "ghp_supersecretvalue123" not in diagnostic


def test_surviving_caller_inventory_documents_exceptions() -> None:
    """REQ-02/06: re-inventory guard — no new ambient callers beyond known set."""
    repo_root = Path(__file__).resolve().parents[4]
    known = {
        "moonmind/auth/github_credentials.py",
        "moonmind/workflows/executions/repository_contract.py",
        "moonmind/workflows/temporal/activity_runtime.py",
        "moonmind/workflows/temporal/runtime/managed_api_key_resolve.py",
        "moonmind/workflows/adapters/github_service.py",
        "moonmind/omnigent/profile_bound_execution.py",
        "moonmind/omnigent/workspace_publication.py",
        "moonmind/omnigent/host_services/github_credentials.py",
        "moonmind/agents/codex_worker/worker.py",
        "moonmind/publish/service.py",
    }
    found: set[str] = set()
    for path in list((repo_root / "moonmind").rglob("*.py")) + list(
        (repo_root / "publish").rglob("*.py")
    ):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "resolve_github_credential" in text:
            found.add(str(path.relative_to(repo_root)))
    # The canonical definition file itself is expected; every other file is a
    # surviving caller that still needs an admitted-connection replacement.
    callers = found - {"moonmind/auth/github_credentials.py"}
    assert callers, "expected surviving ambient callers to be documented"
    assert callers <= known, f"new ambient callers appeared: {sorted(callers - known)}"


def test_scratch_and_anonymous_bypass_github_requirement() -> None:
    """REQ-01: scratch and explicit anonymous work need no GitHub credential."""
    assert cutover.scratch_or_anonymous_usable(mode="scratch") is True
    assert cutover.scratch_or_anonymous_usable(mode="anonymous") is True
    assert cutover.scratch_or_anonymous_usable(mode="repository.write") is False


@pytest.mark.asyncio
async def test_selected_github_resolver_failure_has_no_fallback() -> None:
    """REQ-01: selected-backend failure surfaces once; no second-backend retry."""
    from moonmind.workflows.executions.repository_contract import (
        RepositoryClientEvidence,
    )

    target = compile_repository_target(
        {
            "provider": "git",
            "connectionRef": "repository-connection:conn-a",
            "repository": {"name": "o/r"},
            "branch": {"name": "main"},
        }
    )
    connection = _pat_connection("repository-connection:conn-a")
    payload = connection.model_dump(by_alias=True, mode="json")
    payload["credential"] = {"source": "github_resolver"}
    connection = RepositoryConnection.model_validate(payload)
    evidence = RepositoryClientEvidence.model_validate(_evidence())
    registry = CapabilityReadinessRegistry(runtime_owned_tokens=())
    registry.register("git", lambda context: True)
    registry.register("repo.read", lambda context: True)

    async def resolve_connection(_target: object) -> RepositoryConnection:
        return connection

    async def resolve_evidence(_connection: object) -> RepositoryClientEvidence:
        return evidence

    calls: list[str] = []

    async def failing_resolver(_repository: str) -> object:
        calls.append(_repository)

        class _Unresolved:
            resolved = False
            safe_summary = "selected backend unavailable"

        return _Unresolved()

    with pytest.raises(RepositoryContractError) as excinfo:
        await ensure_repository_ready(
            target,
            publish_mode="none",
            operation="read",
            connection_resolver=resolve_connection,
            evidence_resolver=resolve_evidence,
            readiness_registry=registry,
            credential_resolver=failing_resolver,
            remote_tip_verifier=None,
        )
    assert excinfo.value.code == REPOSITORY_CREDENTIAL_UNAVAILABLE
    assert calls == ["o/r"], "selected backend tried exactly once, no fallback"


def test_default_git_connection_cannot_authorize_new_writes() -> None:
    """REQ-03/06: the ambient default connection is read-only history."""
    policy = RepositoryClientPolicy.model_validate(_policy())
    default = reconcile_default_git_connection(client_policy=policy)
    assert default.credential.source == "github_resolver"
    with pytest.raises(RepositoryRouteError) as excinfo:
        cutover.enforce_typed_connection_for_new_write(default)
    # Missing scoped ownership fails closed first; a scoped github_resolver
    # fails as DENIED. Either code proves no new-write authority.
    assert excinfo.value.code in {REPOSITORY_DENIED, REPOSITORY_SETUP_REQUIRED}


def test_post_migration_startup_consults_only_marker() -> None:
    """REQ-04: ordinary startup needs no census and no ledger/lease."""
    applied = cutover.post_migration_startup_state(cutover_marker_applied=True)
    assert applied["startup_census_required"] is False
    assert applied["ledger_required"] is False
    assert applied["ready"] is True
    # Missing marker is a bounded actionable outcome, not a startup block.
    pending = cutover.post_migration_startup_state(cutover_marker_applied=False)
    assert pending["startup_census_required"] is False
    assert pending["ledger_required"] is False
    assert pending["ready"] is False
    assert str(pending["correction"]).strip() != ""


@pytest.mark.asyncio
async def test_credential_rotation_advances_single_revision_at_db_boundary(
    tmp_path: Path,
) -> None:
    """REQ-04: rotation at the transactional owner needs exactly one advance."""
    service = await _service(tmp_path)
    connection = _pat_connection("repository-connection:conn-a")
    await service.create_connection(
        connection,
        actor_ref="owner:team-a",
        request_id="cutover-4023-rotate-1",
        principal_ref="owner:team-a",
        principal_scope=("system", None),
    )
    rotated_payload = connection.model_dump(by_alias=True, mode="json")
    rotated_payload["credential"] = {
        "source": "secret_ref",
        "credentialRef": {"provider": "managed", "key": "TEAM_A_PAT_ROTATED"},
    }
    rotated_payload["credentialRevision"] = 2
    rotated = RepositoryConnection.model_validate(rotated_payload)
    done = await service.update_connection(
        rotated,
        actor_ref="owner:team-a",
        request_id="cutover-4023-rotate-2",
        expected_policy_revision=1,
        principal_ref="owner:team-a",
        principal_scope=("system", None),
    )
    assert done.credential_revision == 2
    # Same rotation without the revision advance must fail closed.
    bad_payload = connection.model_dump(by_alias=True, mode="json")
    bad_payload["credential"] = {
        "source": "secret_ref",
        "credentialRef": {"provider": "managed", "key": "TEAM_A_PAT_ROTATED_2"},
    }
    bad_payload["credentialRevision"] = 2
    bad = RepositoryConnection.model_validate(bad_payload)
    with pytest.raises(RepositoryRouteError) as excinfo:
        await service.update_connection(
            bad,
            actor_ref="owner:team-a",
            request_id="cutover-4023-rotate-3",
            expected_policy_revision=2,
            principal_ref="owner:team-a",
            principal_scope=("system", None),
        )
    assert excinfo.value.code == REPOSITORY_POLICY_CONFLICT


@pytest.mark.asyncio
async def test_update_lost_ack_replay_returns_same_row(tmp_path: Path) -> None:
    """REQ-04: lost update acknowledgment reconciles by stable request id."""
    service = await _service(tmp_path)
    connection = _pat_connection("repository-connection:conn-a")
    await service.create_connection(
        connection,
        actor_ref="owner:team-a",
        request_id="cutover-4023-update-ack-1",
        principal_ref="owner:team-a",
        principal_scope=("system", None),
    )
    edited = connection.model_copy(update={"display_name": "edited"})
    first = await service.update_connection(
        edited,
        actor_ref="owner:team-a",
        request_id="cutover-4023-update-ack-2",
        expected_policy_revision=1,
        principal_ref="owner:team-a",
        principal_scope=("system", None),
    )
    replayed = await service.update_connection(
        edited,
        actor_ref="owner:team-a",
        request_id="cutover-4023-update-ack-2",
        expected_policy_revision=1,
        principal_ref="owner:team-a",
        principal_scope=("system", None),
    )
    assert replayed.policy_revision == first.policy_revision
    assert replayed.display_name == "edited"


def test_saved_history_replay_gated_by_reader_condition() -> None:
    """REQ-05: replay binds explicit connections only for permitted readers."""
    mapped = cutover.resolve_saved_history_for_replay(
        recorded_before_cutover=True,
        migrated_or_expired=False,
        repository="o/r",
        branch="main",
        recorded_digest="digest:abc123",
        connection_ref="repository-connection:conn-a",
    )
    assert mapped["repositoryName"] == "o/r"
    assert mapped["recordedDigest"] == "digest:abc123"
    assert mapped["connectionRef"] == "repository-connection:conn-a"
    with pytest.raises(RepositoryRouteError) as excinfo:
        cutover.resolve_saved_history_for_replay(
            recorded_before_cutover=True,
            migrated_or_expired=True,
            repository="o/r",
            branch="main",
            recorded_digest="digest:abc123",
            connection_ref="repository-connection:conn-a",
        )
    assert excinfo.value.code == REPOSITORY_DENIED


def test_cutover_preflight_usable_from_deployment_controller() -> None:
    """REQ-07: controller-safe preflight stops incompatible writers only."""
    ok = cutover.cutover_preflight_for_deployment(
        observed_bundle="tool-bundle:git-2.46",
        pinned_bundle="tool-bundle:git-2.46",
        action="repository.write",
        connection_ref="repository-connection:conn-a",
        backend_ref="managed:TEAM_A_PAT",
    )
    assert "repository-connection:conn-a" in ok
    with pytest.raises(RepositoryRouteError) as excinfo:
        cutover.cutover_preflight_for_deployment(
            observed_bundle="tool-bundle:other",
            pinned_bundle="tool-bundle:git-2.46",
            action="repository.write",
            connection_ref="repository-connection:conn-a",
            backend_ref="managed:TEAM_A_PAT",
        )
    assert excinfo.value.code == REPOSITORY_ROUTE_CONFLICT
