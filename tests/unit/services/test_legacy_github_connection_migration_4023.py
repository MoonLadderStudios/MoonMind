"""One recoverable legacy GitHub credential migration (MoonLadderStudios/MoonMind#4023).

Real SQLite database coverage through the existing #4005 connection writer:
populated mapping, rerun without a census, lost commit acknowledgment,
concurrent Settings edits, concurrent creators, credential rotation, and
bounded actionable outcomes for missing or conflicting evidence.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import api_service.services.legacy_github_connection as migration_module
from api_service.db.models import (
    Base,
    RepositoryConnectionAssignment,
    RepositoryConnectionAuditEvent,
    RepositoryConnectionRecord,
    RepositoryRouteDefault,
    SettingsOverride,
)
from api_service.services.legacy_github_connection import (
    LegacyGitHubMigrationOutcome,
    load_default_git_connection,
    migrate_legacy_github_connection,
)
from api_service.services.repository_connections import RepositoryConnectionService
from api_service.services.secrets import SecretsService
from moonmind.workflows.executions.repository_contract import (
    DEFAULT_GIT_CONNECTION_REF,
    REPOSITORY_SETUP_REQUIRED,
    RepositoryClientPolicy,
    RepositoryIdentity,
    RepositoryRouteError,
    ScopedRouteCandidate,
    admit_scoped_route,
)

_TOKEN_A = "ghp_migrationTokenAAAAAAAAAAAAAAAAAAAAAA"
_TOKEN_B = "ghp_migrationTokenBBBBBBBBBBBBBBBBBBBBBB"
_SETTING_KEY = "integrations.github.token_ref"


def _client_policy() -> RepositoryClientPolicy:
    return RepositoryClientPolicy(
        pinnedVersion="2.46.0",
        toolBundleRef="repository-client:git-system",
        executableSha256="sha256:git",
    )


_MIGRATION_TABLES = (
    RepositoryConnectionRecord.__table__,
    RepositoryConnectionAssignment.__table__,
    RepositoryRouteDefault.__table__,
    RepositoryConnectionAuditEvent.__table__,
    SettingsOverride.__table__,
)


async def _maker(tmp_path, name="issue4023.db", *, full_schema=False):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}", future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync: Base.metadata.create_all(
                sync, tables=None if full_schema else list(_MIGRATION_TABLES)
            )
        )
    return maker, engine


async def _migrate(maker, environ, *, settings_ref=None):
    async with maker() as session:
        return await migrate_legacy_github_connection(
            session,
            environ=environ,
            settings_ref=settings_ref,
            client_policy_factory=_client_policy,
        )


async def _records(maker):
    async with maker() as session:
        return (
            (await session.execute(select(RepositoryConnectionRecord))).scalars().all()
        )


async def _count(maker, model):
    async with maker() as session:
        return (
            await session.execute(select(func.count()).select_from(model))
        ).scalar_one()


async def _set_operator_ref(maker, value):
    async with maker() as session:
        row = (
            await session.execute(
                select(SettingsOverride).where(SettingsOverride.key == _SETTING_KEY)
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(
                SettingsOverride(scope="workspace", key=_SETTING_KEY, value_json=value)
            )
        else:
            row.value_json = value
            row.value_version += 1
        await session.commit()


def _assert_token_free(*values) -> None:
    rendered = json.dumps([str(value) for value in values])
    assert _TOKEN_A not in rendered
    assert _TOKEN_B not in rendered


# -- populated mapping and rerun ---------------------------------------------


@pytest.mark.asyncio
async def test_populated_env_token_maps_to_one_typed_default_connection(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        result = await _migrate(maker, {"GITHUB_TOKEN": _TOKEN_A, "GH_TOKEN": _TOKEN_B})

        assert result.outcome is LegacyGitHubMigrationOutcome.MIGRATED
        assert result.credential_ref == "env://GITHUB_TOKEN"
        records = await _records(maker)
        assert [r.connection_id for r in records] == [DEFAULT_GIT_CONNECTION_REF]
        record = records[0]
        assert record.credential_config == {
            "source": "secret_ref",
            "credentialRef": {"provider": "env", "key": "GITHUB_TOKEN", "extra": {}},
        }
        assert record.scope_type == "system"
        assert record.hosting_service == "github"
        # Legacy account and scope are preserved, not broadened: the same
        # operations the legacy default carried, and no assignments/route
        # defaults that routing could select as wildcard authority.
        assert list(record.allowed_operations) == [
            "read",
            "write",
            "branch_write",
            "review_request",
        ]
        assert await _count(maker, RepositoryConnectionAssignment) == 0
        assert await _count(maker, RepositoryRouteDefault) == 0
        audits = await _count(maker, RepositoryConnectionAuditEvent)
        assert audits == 1
        _assert_token_free(record.credential_config, result.safe_diagnostic())
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rerun_converges_without_duplicate_or_reading_legacy_sources(
    tmp_path, monkeypatch
):
    maker, engine = await _maker(tmp_path)
    try:
        first = await _migrate(
            maker, {"GITHUB_TOKEN_SECRET_REF": "db://github-pat-main"}
        )
        assert first.outcome is LegacyGitHubMigrationOutcome.MIGRATED

        def _census_forbidden(*_args, **_kwargs):
            raise AssertionError(
                "post-migration startup must not reread legacy sources"
            )

        monkeypatch.setattr(
            migration_module, "classify_legacy_github_credential", _census_forbidden
        )

        def _host_inspection_forbidden():
            raise AssertionError("post-migration startup must not inspect the host")

        # Different or removed legacy configuration after migration never
        # remaps the recorded identity.
        async with maker() as session:
            second = await migrate_legacy_github_connection(
                session,
                environ={"GITHUB_TOKEN": _TOKEN_B},
                settings_ref=None,
                client_policy_factory=_host_inspection_forbidden,
            )

        assert second.outcome is LegacyGitHubMigrationOutcome.ALREADY_PRESENT
        assert second.credential_ref == "db://github-pat-main"
        assert len(await _records(maker)) == 1
        assert await _count(maker, RepositoryConnectionAuditEvent) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_operator_setting_is_mapped_when_no_runtime_source_exists(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        await _set_operator_ref(maker, "db://github-pat-main")

        result = await _migrate(maker, {})

        assert result.outcome is LegacyGitHubMigrationOutcome.MIGRATED
        assert result.source_name == "settings:integrations.github.token_ref"
        async with maker() as session:
            connection = await load_default_git_connection(session)
        assert connection is not None
        assert connection.credential.source == "secret_ref"
        assert connection.credential.credential_ref.provider == "db"
        assert connection.credential.credential_ref.key == "github-pat-main"
    finally:
        await engine.dispose()


# -- missing or conflicting evidence -----------------------------------------


@pytest.mark.parametrize(
    ("environ", "operator_ref", "expected"),
    [
        ({}, None, LegacyGitHubMigrationOutcome.ABSENT),
        (
            {"GITHUB_TOKEN_SECRET_REF": "not-a-reference"},
            None,
            LegacyGitHubMigrationOutcome.UNREADABLE,
        ),
        (
            {"GITHUB_TOKEN": _TOKEN_A},
            "db://github-pat-other",
            LegacyGitHubMigrationOutcome.CONFLICTING,
        ),
    ],
)
@pytest.mark.asyncio
async def test_missing_or_conflicting_evidence_is_bounded_and_actionable(
    tmp_path, environ, operator_ref, expected
):
    maker, engine = await _maker(tmp_path)
    try:
        if operator_ref is not None:
            await _set_operator_ref(maker, operator_ref)

        result = await _migrate(maker, environ)

        assert result.outcome is expected
        assert result.correction
        diagnostic = result.safe_diagnostic()
        assert diagnostic["affectedAction"] == (
            "authenticated operations using repository-connection:git-default"
        )
        _assert_token_free(diagnostic)
        # No guessed mapping is written; nothing blocks later startup, and a
        # corrected configuration converges on the next run.
        assert await _records(maker) == []
        corrected = await _migrate(
            maker, {"GH_TOKEN": _TOKEN_B} if operator_ref is None else {}
        )
        assert corrected.outcome is LegacyGitHubMigrationOutcome.MIGRATED
    finally:
        await engine.dispose()


# -- interrupted / lost acknowledgment ----------------------------------------


@pytest.mark.asyncio
async def test_lost_commit_acknowledgment_reconciles_instead_of_repeating(
    tmp_path, monkeypatch
):
    maker, engine = await _maker(tmp_path)
    try:
        original_commit = AsyncSession.commit
        calls = {"count": 0}

        async def _commit_then_lose_ack(self):
            await original_commit(self)
            calls["count"] += 1
            if calls["count"] == 1:
                raise ConnectionResetError("commit acknowledgment lost")

        monkeypatch.setattr(AsyncSession, "commit", _commit_then_lose_ack)
        with pytest.raises(ConnectionResetError):
            await _migrate(maker, {"GITHUB_TOKEN": _TOKEN_A})
        monkeypatch.setattr(AsyncSession, "commit", original_commit)

        # The retry observes the committed effect rather than writing again.
        retry = await _migrate(maker, {"GITHUB_TOKEN": _TOKEN_A})

        assert retry.outcome is LegacyGitHubMigrationOutcome.ALREADY_PRESENT
        assert retry.credential_ref == "env://GITHUB_TOKEN"
        assert len(await _records(maker)) == 1
        assert await _count(maker, RepositoryConnectionAuditEvent) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_interrupted_write_before_commit_leaves_nothing_and_rerun_converges(
    tmp_path, monkeypatch
):
    maker, engine = await _maker(tmp_path)
    try:
        original_commit = AsyncSession.commit

        async def _crash_before_commit(self):
            raise RuntimeError("process stopped before commit")

        monkeypatch.setattr(AsyncSession, "commit", _crash_before_commit)
        with pytest.raises(RuntimeError):
            await _migrate(maker, {"GITHUB_TOKEN": _TOKEN_A})
        monkeypatch.setattr(AsyncSession, "commit", original_commit)
        assert await _records(maker) == []

        retry = await _migrate(maker, {"GITHUB_TOKEN": _TOKEN_A})

        assert retry.outcome is LegacyGitHubMigrationOutcome.MIGRATED
        assert len(await _records(maker)) == 1
    finally:
        await engine.dispose()


# -- concurrent edits, creators, and rotation --------------------------------


@pytest.mark.asyncio
async def test_concurrent_setting_edit_is_detected_and_rerun_converges(
    tmp_path, monkeypatch
):
    maker, engine = await _maker(tmp_path)
    try:
        await _set_operator_ref(maker, "db://github-pat-main")
        original_fence = migration_module._operator_setting_fence
        pending_edits = ["db://github-pat-rotated"]

        async def _edit_lands_before_write(session, *, lock):
            # The operator saves a different Settings value between the
            # migration's classification and its guarded write.
            if lock and pending_edits:
                await _set_operator_ref(maker, pending_edits.pop())
            return await original_fence(session, lock=lock)

        monkeypatch.setattr(
            migration_module, "_operator_setting_fence", _edit_lands_before_write
        )

        changed = await _migrate(maker, {})

        assert changed.outcome is LegacyGitHubMigrationOutcome.CONFIGURATION_CHANGED
        assert changed.correction
        assert await _records(maker) == []

        converged = await _migrate(maker, {})

        assert converged.outcome is LegacyGitHubMigrationOutcome.MIGRATED
        assert converged.credential_ref == "db://github-pat-rotated"
        assert len(await _records(maker)) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_creator_of_same_identity_converges_on_one_record(
    tmp_path, monkeypatch
):
    maker, engine = await _maker(tmp_path)
    try:
        original_lookup = migration_module._recorded_default_connection
        raced = {"done": False}

        async def _other_replica_wins(session):
            if not raced["done"]:
                raced["done"] = True
                await _migrate(maker, {"GITHUB_TOKEN": _TOKEN_A})
                return None
            return await original_lookup(session)

        monkeypatch.setattr(
            migration_module, "_recorded_default_connection", _other_replica_wins
        )

        result = await _migrate(maker, {"GITHUB_TOKEN": _TOKEN_A})

        # Both writers carry the same stable request identity, so the loser
        # replays the winner's committed mapping instead of adding another.
        assert result.outcome in {
            LegacyGitHubMigrationOutcome.MIGRATED,
            LegacyGitHubMigrationOutcome.ALREADY_PRESENT,
        }
        assert result.credential_ref == "env://GITHUB_TOKEN"
        assert len(await _records(maker)) == 1
        assert await _count(maker, RepositoryConnectionAuditEvent) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_different_identity_is_preserved_not_overwritten(
    tmp_path, monkeypatch
):
    maker, engine = await _maker(tmp_path)
    try:
        original_lookup = migration_module._recorded_default_connection
        raced = {"done": False}

        async def _operator_records_other(session):
            if not raced["done"]:
                raced["done"] = True
                await _migrate(
                    maker, {"GITHUB_TOKEN_SECRET_REF": "db://github-pat-main"}
                )
                return None
            return await original_lookup(session)

        monkeypatch.setattr(
            migration_module, "_recorded_default_connection", _operator_records_other
        )

        result = await _migrate(maker, {"GITHUB_TOKEN": _TOKEN_A})

        assert result.outcome is LegacyGitHubMigrationOutcome.CONFLICTING
        assert "db://github-pat-main" in (result.correction or "")
        records = await _records(maker)
        assert len(records) == 1
        assert records[0].credential_config["credentialRef"]["key"] == "github-pat-main"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rotation_keeps_reference_mapping_and_newer_connection_work(tmp_path):
    maker, engine = await _maker(tmp_path, full_schema=True)
    try:
        async with maker() as db:
            await SecretsService.create_secret(db, "github-pat-main", _TOKEN_A)

        first = await _migrate(
            maker, {"GITHUB_TOKEN_SECRET_REF": "db://github-pat-main"}
        )
        assert first.outcome is LegacyGitHubMigrationOutcome.MIGRATED

        # Managed-secret rotation changes the value behind the reference, not
        # the mapping: the record never captured token material.
        async with maker() as db:
            await SecretsService.rotate_secret(
                db, "github-pat-main", _TOKEN_B, validator=lambda _candidate: True
            )
        # A later connection credential rotation is newer shared work that a
        # rerun must never overwrite with the old legacy reference.
        async with maker() as session:
            connection = await load_default_git_connection(session)
            assert connection is not None
            rotated = connection.model_copy(
                update={
                    "credential": connection.credential.model_copy(
                        update={
                            "credential_ref": connection.credential.credential_ref.model_copy(
                                update={"key": "github-pat-next"}
                            )
                        }
                    ),
                    "credential_revision": connection.credential_revision + 1,
                }
            )
            await RepositoryConnectionService(session).update_connection(
                rotated,
                actor_ref="owner:operator",
                request_id="rotate-default-1",
                expected_policy_revision=connection.policy_revision,
                principal_ref="owner:operator",
                principal_scope=("system", None),
            )

        rerun = await _migrate(
            maker, {"GITHUB_TOKEN_SECRET_REF": "db://github-pat-main"}
        )

        assert rerun.outcome is LegacyGitHubMigrationOutcome.ALREADY_PRESENT
        records = await _records(maker)
        assert len(records) == 1
        assert records[0].credential_config["credentialRef"]["key"] == "github-pat-next"
        assert records[0].credential_revision == 2
        _assert_token_free(records[0].credential_config)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_deleted_default_connection_is_never_recreated(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        await _migrate(maker, {"GITHUB_TOKEN": _TOKEN_A})
        async with maker() as session:
            await RepositoryConnectionService(session).delete_connection(
                DEFAULT_GIT_CONNECTION_REF,
                actor_ref="owner:operator",
                request_id="delete-default-1",
                principal_ref="owner:operator",
                principal_scope=("system", None),
                has_active_bindings=lambda _connection_id: False,
            )

        rerun = await _migrate(maker, {"GITHUB_TOKEN": _TOKEN_A})

        assert rerun.outcome is LegacyGitHubMigrationOutcome.ALREADY_PRESENT
        async with maker() as session:
            assert await load_default_git_connection(session) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migrated_default_is_never_a_routed_wildcard(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        await _migrate(maker, {"GITHUB_TOKEN": _TOKEN_A})
        async with maker() as session:
            service = RepositoryConnectionService(session)
            identity = RepositoryIdentity(
                endpoint="https://github.com",
                providerRepoId="12345",
                displayName="MoonLadderStudios/MoonMind",
            )
            routes = await service.list_admitted_routes(
                identity=identity,
                principal_ref="owner:operator",
                principal_scope=("system", None),
            )
        assert routes == []
        with pytest.raises(RepositoryRouteError) as denied:
            admit_scoped_route(
                identity=identity,
                requested_operations=("read",),
                candidates=[
                    ScopedRouteCandidate(connection=c, assignment=a) for c, a in routes
                ],
                principal_ref="owner:operator",
                principal_scope=("system", None),
            )
        assert denied.value.code == REPOSITORY_SETUP_REQUIRED
    finally:
        await engine.dispose()
