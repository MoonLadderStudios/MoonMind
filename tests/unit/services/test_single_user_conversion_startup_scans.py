"""Bound startup work without weakening authoritative conversion or recovery."""

from api_service.migrations.account_source import source_user_ids
from tests.helpers.account_source import add_source_user

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from api_service.db.models import (
    TemporalArtifact,
    TemporalArtifactRedactionLevel,
    TemporalArtifactUseClaim,
)
from api_service.services import single_user_conversion as conversion
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.helpers.account_source import User, create_source_schema

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/startup.db")
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(create_source_schema)
    try:
        yield maker
    finally:
        await engine.dispose()


async def seed(session):
    owner = uuid4()
    await add_source_user(session, User(id=owner, email=f"{owner}@example.invalid"))
    artifact = TemporalArtifact(
        artifact_id=f"retained-{uuid4().hex}",
        storage_key="retained-key",
        created_by_principal=str(owner),
        redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
    )
    session.add(artifact)
    await session.commit()
    return artifact.artifact_id, str(owner)


async def test_preflight_decides_from_its_single_inventory(monkeypatch):
    first = conversion.ConversionInventory()
    changed = conversion.ConversionInventory(user_ids=[str(uuid4()), str(uuid4())])
    collect = AsyncMock(side_effect=[first, changed])
    monkeypatch.setattr(conversion, "collect_inventory", collect)

    result = await conversion.preflight(AsyncMock(spec=AsyncSession))

    assert result.decision.disposition == "fresh_init"
    assert result.inventory is first
    assert result.fingerprint == first.fingerprint()
    assert collect.await_count == 1


async def test_repeated_artifact_owners_are_classified_once(tmp_path, monkeypatch):
    """Retained history size must not multiply identity classification work."""
    async with database(tmp_path) as maker, maker() as session:
        owner = uuid4()
        await add_source_user(session, User(id=owner, email="retained@example.invalid"))
        session.add_all(
            [
                TemporalArtifact(
                    artifact_id=f"history-{index}",
                    storage_key=f"key-{index}",
                    created_by_principal=str(owner),
                )
                for index in range(512)
            ]
        )
        await session.commit()
        classify = Mock(wraps=conversion._looks_like_uuid)
        monkeypatch.setattr(conversion, "_looks_like_uuid", classify)

        inventory = await conversion.collect_inventory(session)

        assert inventory.principals["temporal_artifacts.created_by_principal"] == [
            str(owner)
        ]
        assert (
            inventory.resource_counts["temporal_artifacts.created_by_principal"] == 512
        )
        assert classify.call_args_list.count(((str(owner),), {})) == 1


async def test_unresolved_principals_refuse_without_hashing_history(
    tmp_path,
    monkeypatch,
    caplog,
):
    async with database(tmp_path) as maker, maker() as session:
        session.add_all(
            [
                TemporalArtifact(
                    artifact_id=f"unresolved-{index}",
                    storage_key=f"key-{index}",
                    created_by_principal="unproven-retained-owner",
                )
                for index in range(128)
            ]
        )
        await session.commit()
        digest = AsyncMock(side_effect=AssertionError("refused history was hashed"))
        monkeypatch.setattr(conversion, "_table_content_digest", digest)

        result = await conversion.run_guarded_upgrade(
            session,
            operator_authorized=True,
        )

        assert not result.published
        assert result.decision.reason_code == "inventory_unavailable"
        assert (
            "principal:temporal_artifacts.created_by_principal"
            in result.decision.detail
        )
        assert (
            sum(
                record.message == "single-user conversion inventory unreadable: "
                "principal:temporal_artifacts.created_by_principal"
                for record in caplog.records
            )
            == 1
        )
        digest.assert_not_awaited()
        assert (
            await session.execute(select(conversion.SingleUserConversionRun))
        ).scalars().all() == []
        assert not any(
            (await session.execute(select(TemporalArtifact.operator_recovery_granted)))
            .scalars()
            .all()
        )


async def test_explicit_preflight_and_apply_each_scan_once(tmp_path, monkeypatch):
    async with database(tmp_path) as maker:
        async with maker() as session:
            await seed(session)
            collect = AsyncMock(wraps=conversion.collect_inventory)
            monkeypatch.setattr(conversion, "collect_inventory", collect)
            bound = await conversion.preflight(session)
            assert collect.await_count == 1
            collect.reset_mock()
            result = await conversion.apply_conversion(
                session, bound, operator_authorized=True,
                transforms=conversion.DEFAULT_SUBSYSTEM_TRANSFORMS,
            )
            assert result.published
            assert collect.await_count == 1
        # Explicit inspection stays authoritative even with a certified result.
        async with maker() as session:
            collect.reset_mock()
            bound = await conversion.preflight(session)
            assert collect.await_count == 1
            collect.reset_mock()
            result = await conversion.apply_conversion(
                session, bound, operator_authorized=True,
                transforms=conversion.DEFAULT_SUBSYSTEM_TRANSFORMS,
            )
            assert result.published
            assert collect.await_count == 1


async def test_refused_upgrade_scans_once_without_locking_writers(
    tmp_path, monkeypatch,
):
    async with database(tmp_path) as maker:
        async with maker() as session:
            await seed(session)
            await seed(session)
            collect = AsyncMock(wraps=conversion.collect_inventory)
            monkeypatch.setattr(conversion, "collect_inventory", collect)
            lock = AsyncMock(
                side_effect=AssertionError("a refusal must not block writers"),
            )
            monkeypatch.setattr(conversion, "_lock_conversion_evidence", lock)

            result = await conversion.run_guarded_upgrade(
                session, operator_authorized=True,
            )

            assert not result.published
            assert result.decision.disposition == "multi_person_refusal"
            assert collect.await_count == 1
            lock.assert_not_awaited()


async def test_artifact_evidence_fingerprint_binds_values_not_order(tmp_path):
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session)
            await seed(session)
            before = (await conversion.collect_inventory(session)).fingerprint()
            artifact = await session.get(TemporalArtifact, artifact_id)
            artifact.storage_key = "changed-key"
            await session.commit()
            changed = (await conversion.collect_inventory(session)).fingerprint()
            artifact.storage_key = "retained-key"
            await session.commit()
            restored = (await conversion.collect_inventory(session)).fingerprint()

    assert changed != before
    assert restored == before


@pytest.mark.parametrize("retained", [False, True])
async def test_default_restart_and_guard_replay_without_inventory(
    tmp_path, monkeypatch, retained,
):
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id = None
            if retained:
                artifact_id, _owner = await seed(session)
            initial_collect = AsyncMock(wraps=conversion.collect_inventory)
            monkeypatch.setattr(conversion, "collect_inventory", initial_collect)
            first = await conversion.run_guarded_upgrade(
                session, operator_authorized=True,
            )
            assert first.published
            assert initial_collect.await_count == 2
        async with maker() as restarted:
            collect = AsyncMock(
                side_effect=AssertionError("history must not be rescanned"),
            )
            monkeypatch.setattr(conversion, "collect_inventory", collect)
            again = await conversion.run_guarded_upgrade(
                restarted, operator_authorized=True,
            )
            empty_options = await conversion.run_guarded_upgrade(
                restarted, operator_authorized=True, alias_groups=[],
                collision_resolutions={}, deployment_owned_tables=set(),
            )
            guard = await conversion.startup_guard_decision(restarted)
            assert again.to_sanitized_dict() == first.to_sanitized_dict()
            assert empty_options.to_sanitized_dict() == first.to_sanitized_dict()
            assert guard.to_sanitized_dict() == first.decision.to_sanitized_dict()
            if artifact_id is not None:
                artifact = await restarted.get(TemporalArtifact, artifact_id)
                assert artifact.operator_recovery_granted
            collect.assert_not_awaited()


async def test_completed_startup_replay_still_requires_authorization(
    tmp_path, monkeypatch,
):
    async with database(tmp_path) as maker:
        async with maker() as session:
            await seed(session)
            result = await conversion.run_guarded_upgrade(
                session, operator_authorized=True,
            )
            assert result.published
        async with maker() as session:
            collect = AsyncMock(
                side_effect=AssertionError("unauthorized startup must not scan"),
            )
            monkeypatch.setattr(conversion, "collect_inventory", collect)
            execute = AsyncMock(
                side_effect=AssertionError("unauthorized startup must not query"),
            )
            monkeypatch.setattr(session, "execute", execute)
            with pytest.raises(conversion.ConversionAuthorizationError):
                await conversion.run_guarded_upgrade(session, operator_authorized=False)
            collect.assert_not_awaited()
            execute.assert_not_awaited()


@pytest.mark.parametrize(
    "new_input", ["aliases", "collisions", "deployment", "transforms"],
)
async def test_new_conversion_inputs_require_authoritative_scan(
    tmp_path, monkeypatch, new_input,
):
    async with database(tmp_path) as maker:
        async with maker() as session:
            _artifact_id, owner = await seed(session)
            result = await conversion.run_guarded_upgrade(
                session, operator_authorized=True,
            )
            assert result.published
        kwargs = {
            "aliases": {"alias_groups": [{owner}]},
            "collisions": {"collision_resolutions": {"setting": "retain"}},
            "deployment": {"deployment_owned_tables": {"temporal_artifacts"}},
            "transforms": {"transforms": dict(conversion.DEFAULT_SUBSYSTEM_TRANSFORMS)},
        }[new_input]
        async with maker() as session:
            collect = AsyncMock(
                side_effect=RuntimeError("authoritative inventory requested"),
            )
            monkeypatch.setattr(conversion, "collect_inventory", collect)
            with pytest.raises(RuntimeError, match="authoritative inventory requested"):
                await conversion.run_guarded_upgrade(
                    session, operator_authorized=True, **kwargs,
                )
            collect.assert_awaited_once()


@pytest.mark.parametrize(
    "invalid", ["unstamped", "false_marker", "numeric_marker", "numeric_publication",
                "numeric_eligible", "missing_artifact_outcome", "old_revision",
                "unpublished", "ineligible", "in_progress"],
)
async def test_uncertified_completion_does_not_bypass_inventory(
    tmp_path, monkeypatch, invalid,
):
    async with database(tmp_path) as maker:
        async with maker() as session:
            await seed(session)
            result = await conversion.run_guarded_upgrade(
                session, operator_authorized=True,
            )
            assert result.published
            row = (
                await session.execute(select(conversion.SingleUserConversionRun))
            ).scalar_one()
            stored = deepcopy(row.result_json)
            if invalid == "unstamped":
                stored.pop("production_transforms_completed", None)
            elif invalid == "false_marker":
                stored["production_transforms_completed"] = False
            elif invalid == "numeric_marker":
                stored["production_transforms_completed"] = 1
            elif invalid == "numeric_publication":
                stored["published"] = 1
            elif invalid == "numeric_eligible":
                stored["decision"]["eligible"] = 1
            elif invalid == "missing_artifact_outcome":
                stored["transforms"].pop("artifacts")
            elif invalid == "old_revision":
                stored["schema_revision"] = "prior_conversion_revision"
            elif invalid == "unpublished":
                stored["published"] = False
            elif invalid == "ineligible":
                stored["decision"]["eligible"] = False
            else:
                row.status = "in_progress"
            row.result_json = stored
            # True == 1 in Python; force the malformed JSON type to persist.
            flag_modified(row, "result_json")
            await session.commit()
        async with maker() as restarted:
            collect = AsyncMock(
                side_effect=RuntimeError("authoritative inventory requested"),
            )
            monkeypatch.setattr(conversion, "collect_inventory", collect)
            with pytest.raises(RuntimeError, match="authoritative inventory requested"):
                await conversion.run_guarded_upgrade(
                    restarted, operator_authorized=True,
                )
            collect.assert_awaited_once()


@pytest.mark.parametrize("interrupt_repair", [False, True])
@pytest.mark.parametrize("certified", [False, True])
async def test_default_repairs_pending_recovery_atomically(
    tmp_path,
    monkeypatch,
    interrupt_repair,
    certified,
):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")

    def artifact_service(session):
        return TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "bytes"),
        )

    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session)
            await artifact_service(session).write_complete(
                artifact_id=artifact_id, principal=owner, payload=b"retained bytes",
                content_type="application/octet-stream",
            )
            noop = AsyncMock(return_value={"recovery_grants": 0, "preserved": 1})
            custom = {**conversion.DEFAULT_SUBSYSTEM_TRANSFORMS, "artifacts": noop}
            first = await conversion.run_guarded_upgrade(
                session, operator_authorized=True,
                transforms=None if certified else custom,
            )
            assert first.published
            if certified:
                artifact = await session.get(TemporalArtifact, artifact_id)
                assert artifact.operator_recovery_granted is True
                artifact.operator_recovery_granted = False
                await session.commit()
            # Generic explicit custom replay remains idempotent.
            custom_replay = await conversion.run_guarded_upgrade(
                session, operator_authorized=True, transforms=custom,
            )
            assert custom_replay.digest == first.digest
            assert noop.await_count == (0 if certified else 1)
            artifact = await session.get(TemporalArtifact, artifact_id)
            assert not artifact.operator_recovery_granted
            with pytest.raises(TemporalArtifactAuthorizationError):
                await artifact_service(session).read(
                    artifact_id=artifact_id, principal="operator",
                )
            row = (
                await session.execute(select(conversion.SingleUserConversionRun))
            ).scalar_one()
            original_id = row.id
            original_result = deepcopy(row.result_json)
            assert bool(original_result.get("production_transforms_completed")) is certified

        if interrupt_repair:
            async with maker() as interrupted:
                engine = interrupted.get_bind()

                def fail_publication(_conn):
                    raise RuntimeError("interrupted repair publication")

                event.listen(engine, "commit", fail_publication)
                try:
                    with pytest.raises(RuntimeError, match="repair publication"):
                        await conversion.run_guarded_upgrade(
                            interrupted, operator_authorized=True,
                        )
                finally:
                    event.remove(engine, "commit", fail_publication)
            async with maker() as unchanged:
                artifact = await unchanged.get(TemporalArtifact, artifact_id)
                assert artifact.operator_recovery_granted is False
                row = await unchanged.get(
                    conversion.SingleUserConversionRun, original_id,
                )
                assert row.result_json == original_result
                assert row.status == "complete"
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await artifact_service(unchanged).read(
                        artifact_id=artifact_id, principal="operator",
                    )

        async with maker() as repaired:
            result = await conversion.run_guarded_upgrade(
                repaired, operator_authorized=True,
            )
            assert result.published
            assert result.digest == first.digest
            artifact = await repaired.get(TemporalArtifact, artifact_id)
            assert artifact.operator_recovery_granted is True
            assert (await artifact_service(repaired).read(
                artifact_id=artifact_id, principal="operator",
            ))[1] == b"retained bytes"
            row = (
                await repaired.execute(select(conversion.SingleUserConversionRun))
            ).scalar_one()
            assert row.id == original_id
            assert row.result_json["production_transforms_completed"] is True

        async with maker() as restarted:
            collect = AsyncMock(
                side_effect=AssertionError("certified repair rescanned"),
            )
            monkeypatch.setattr(conversion, "collect_inventory", collect)
            result = await conversion.run_guarded_upgrade(
                restarted, operator_authorized=True,
            )
            assert result.published
            assert result.digest == first.digest
            collect.assert_not_awaited()


@pytest.mark.parametrize(
    "mode, default_id",
    [("disabled", "other"), ("oidc", "owner"), ("disabled", "invalid")],
)
async def test_startup_converts_later_eligible_artifact(
    tmp_path,
    monkeypatch,
    mode,
    default_id,
):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with database(tmp_path) as maker:
        async with maker() as session:
            _artifact_id, owner = await seed(session)
            first = await conversion.run_guarded_upgrade(
                session, operator_authorized=True,
            )
            assert first.published
        async with maker() as writer:
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(writer),
                store=LocalTemporalArtifactStore(tmp_path / "bytes"),
            )
            new, _ = await artifacts.create(
                principal=owner, content_type="application/octet-stream",
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            )
            new = await artifacts.write_complete(
                artifact_id=new.artifact_id, principal=owner, payload=b"later bytes",
                content_type="application/octet-stream",
            )
            new_id = new.artifact_id
            assert new.operator_recovery_granted is False
        monkeypatch.setenv("AUTH_PROVIDER", str(mode or ""))
        monkeypatch.setenv(
            "DEFAULT_USER_ID",
            str(
                {
                    "owner": owner,
                    "other": str(uuid4()),
                    "invalid": "invalid-uuid",
                }[default_id]
                or ""
            ),
        )
        collect = AsyncMock(wraps=conversion.collect_inventory)
        monkeypatch.setattr(conversion, "collect_inventory", collect)
        async with maker() as inspected:
            guard = await conversion.startup_guard_decision(inspected)
            assert guard.eligible
            assert collect.await_count == 1
            row = await inspected.get(TemporalArtifact, new_id)
            assert row.operator_recovery_granted is False
        async with maker() as restarted:
            collect.reset_mock()
            again = await conversion.run_guarded_upgrade(
                restarted, operator_authorized=True,
            )
            assert again.published
            assert again.digest != first.digest
            assert collect.await_count == 2
            row = await restarted.get(TemporalArtifact, new_id)
            assert row.operator_recovery_granted is True
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(restarted),
                store=LocalTemporalArtifactStore(tmp_path / "bytes"),
            )
            assert (await artifacts.read(
                artifact_id=new_id, principal="operator",
            ))[1] == b"later bytes"
        async with maker() as unchanged:
            collect.reset_mock()
            assert (await conversion.run_guarded_upgrade(
                unchanged, operator_authorized=True,
            )).published
            collect.assert_not_awaited()


@pytest.mark.parametrize(
    "owner",
    [
        None,
        "unknown_uuid",
        "another_user",
        "malformed",
        "service:",
        "service:\t",
        "service:\u00a0",
        "SERVICE:agent",
        "user:",
        "user:not-a-uuid",
        "user:user:00000000-0000-0000-0000-000000000000",
        "USER:00000000-0000-0000-0000-000000000000",
        "prefixed_unknown",
        "prefixed_other",
    ],
)
async def test_later_unattributed_artifact_refuses_without_grant(
    tmp_path,
    monkeypatch,
    owner,
):
    owner_case = owner
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with database(tmp_path) as maker:
        async with maker() as session:
            old_id, _owner = await seed(session)
            first = await conversion.run_guarded_upgrade(
                session, operator_authorized=True,
            )
            assert first.published
        async with maker() as writer:
            if owner in {
                "unknown_uuid",
                "another_user",
                "prefixed_unknown",
                "prefixed_other",
            }:
                prefixed = owner.startswith("prefixed_")
                if owner in {"another_user", "prefixed_other"}:
                    other = User(id=uuid4(), email="another@example.invalid")
                    await add_source_user(writer, other)
                    owner = str(other.id)
                else:
                    owner = str(uuid4())
                if prefixed:
                    owner = f"user:{owner}"
            new = TemporalArtifact(
                artifact_id=f"pending-{uuid4().hex}", storage_key="pending-key",
                created_by_principal=owner,
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            )
            writer.add(new)
            await writer.commit()
            new_id = new.artifact_id
        async with maker() as restarted:
            result = await conversion.run_guarded_upgrade(
                restarted, operator_authorized=True,
            )
            assert result.published is False
            assert result.decision.eligible is False
            if owner_case == "prefixed_unknown":
                assert result.decision.reason_code == "unknown_principal"
            elif owner_case == "prefixed_other":
                assert result.decision.reason_code == "multi_person"
            elif owner_case and owner_case.lower().startswith("user:"):
                assert result.decision.reason_code == "inventory_unavailable"
            assert (await restarted.get(TemporalArtifact, new_id)).operator_recovery_granted is False
            assert (await restarted.get(TemporalArtifact, old_id)).operator_recovery_granted is True
            rows = (await restarted.execute(select(conversion.SingleUserConversionRun))).scalars().all()
            assert [row.preflight_digest for row in rows] == [first.digest]


async def test_disabled_direct_owner_and_machines_use_bounded_probe(
    tmp_path,
    monkeypatch,
):
    from api_service.auth_providers import instance_operator_principal
    default_id = instance_operator_principal()
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    monkeypatch.setenv("DEFAULT_USER_ID", str(default_id or ""))
    async with database(tmp_path) as maker:
        async with maker() as session:
            assert (await conversion.run_guarded_upgrade(
                session, operator_authorized=True,
            )).published
        async with maker() as writer:
            # No User row is needed for the disabled request creator's direct access.
            owners = [default_id, "operator", "system", "agent_workflow", "agent_runtime",
                      "service:agent", "workflow:run", "system:task", " \tservice:agent\n"]
            for index, owner in enumerate(owners):
                writer.add(TemporalArtifact(
                    artifact_id=f"later-{index}", storage_key=f"later-key-{index}",
                    created_by_principal=owner,
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                ))
            await writer.commit()
        async with maker() as restarted:
            collect = AsyncMock(side_effect=AssertionError("history must not be rescanned"))
            monkeypatch.setattr(conversion, "collect_inventory", collect)
            queries = []

            def capture_query(_conn, _cursor, statement, _parameters, context, _many):
                if statement.startswith("SELECT"):
                    queries.append((statement, context.compiled.statement))

            engine = restarted.get_bind()
            event.listen(engine, "before_cursor_execute", capture_query)
            try:
                assert (await conversion.run_guarded_upgrade(
                    restarted, operator_authorized=True,
                )).published
                assert (await conversion.startup_guard_decision(restarted)).eligible
            finally:
                event.remove(engine, "before_cursor_execute", capture_query)
            collect.assert_not_awaited()
            artifact_queries = [query for sql, query in queries if "FROM temporal_artifacts" in sql]
            assert len(artifact_queries) == 2
            for query in artifact_queries:
                assert list(query.selected_columns.keys()) == ["artifact_id"]
                assert query._limit_clause.value == 1
            assert not any("FROM temporal_artifact_" in sql for sql, _query in queries)
            assert (await restarted.get(TemporalArtifact, "later-0")).operator_recovery_granted is False


@pytest.mark.parametrize(
    "mode, configuration, eligible",
    [
        ("disabled", None, True),
        ("disabled", "zero", True),
        ("oidc", "zero", False),
        ("disabled", "nonzero", False),
        ("disabled", "invalid", False),
        ("disabled", "noncanonical", False),
    ],
)
async def test_mixed_history_preserves_only_reserved_deployment_scope(
    tmp_path,
    monkeypatch,
    mode,
    configuration,
    eligible,
):
    zero = conversion.DEPLOYMENT_SUBJECT_ID
    default_owner = str(uuid4()) if configuration == "nonzero" else zero
    configured = {
        None: None, "zero": zero, "nonzero": default_owner,
        "invalid": "invalid-uuid", "noncanonical": zero,
    }[configuration]
    if configuration == "noncanonical":
        default_owner = "0" * 32
    monkeypatch.setenv("DEFAULT_USER_ID", str(zero or ""))
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with database(tmp_path) as maker:
        async with maker() as first:
            original_id, owner = await seed(first)
            assert (await conversion.run_guarded_upgrade(
                first, operator_authorized=True,
            )).published
        async with maker() as writer:
            writer.add_all([
                TemporalArtifact(
                    artifact_id="disabled-created", storage_key="disabled-key",
                    created_by_principal=default_owner, size_bytes=11,
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                ),
                TemporalArtifact(
                    artifact_id="later-human", storage_key="later-human-key",
                    created_by_principal=owner, size_bytes=23,
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                ),
                TemporalArtifactUseClaim(
                    artifact_id=original_id, owner_principal=default_owner,
                    request_id="retained-disabled-download", operation_kind="download",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            ])
            await writer.commit()
            repository = TemporalArtifactRepository(writer)
            quota = {principal: await repository.quota_usage_for_scope(principal)
                     for principal in (default_owner, owner)}
            claim = (await writer.execute(select(TemporalArtifactUseClaim))).scalar_one()
            claim_id = claim.id
            claim_expiry = claim.expires_at.replace(tzinfo=None)
        monkeypatch.setenv("AUTH_PROVIDER", str(mode or ""))
        monkeypatch.setenv("DEFAULT_USER_ID", str(configured or ""))
        async with maker() as restarted:
            result = await conversion.run_guarded_upgrade(
                restarted, operator_authorized=True,
            )
            assert result.published is eligible, result.to_sanitized_dict()
            assert result.decision.eligible is eligible
            assert UUID(zero) not in await source_user_ids(restarted)
            if eligible:
                assert result.decision.person_count == 1
                assert result.transforms["artifacts"]["recovery_grants"] == 1
            else:
                assert result.decision.reason_code == "unknown_principal"
            default = await restarted.get(TemporalArtifact, "disabled-created")
            human = await restarted.get(TemporalArtifact, "later-human")
            assert default.created_by_principal == default_owner
            assert default.operator_recovery_granted is False
            assert human.created_by_principal == owner
            assert human.operator_recovery_granted is eligible
            claim = await restarted.get(TemporalArtifactUseClaim, claim_id)
            assert claim.owner_principal == default_owner
            assert claim.artifact_id == original_id
            assert claim.request_id == "retained-disabled-download"
            assert claim.operation_kind == "download"
            assert claim.expires_at.replace(tzinfo=None) == claim_expiry
            repository = TemporalArtifactRepository(restarted)
            assert {principal: await repository.quota_usage_for_scope(principal)
                    for principal in (default_owner, owner)} == quota
        if eligible:
            async with maker() as unchanged:
                collect = AsyncMock(side_effect=AssertionError("mixed recovery rescanned"))
                monkeypatch.setattr(conversion, "collect_inventory", collect)
                assert (await conversion.run_guarded_upgrade(
                    unchanged, operator_authorized=True,
                )).published
                collect.assert_not_awaited()


@pytest.mark.parametrize("scenario", ["alone", "second", "aliased", "later_collision"])
@pytest.mark.parametrize("owner_prefix", ["", "user:"])
async def test_reserved_zero_never_hides_a_real_user(
    tmp_path,
    monkeypatch,
    scenario,
    owner_prefix,
):
    zero = conversion.DEPLOYMENT_SUBJECT_ID
    monkeypatch.setenv("DEFAULT_USER_ID", str(zero or ""))
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    async with database(tmp_path) as maker:
        async with maker() as writer:
            owner = None
            if scenario != "alone":
                _original_id, owner = await seed(writer)
            if scenario == "later_collision":
                assert (await conversion.run_guarded_upgrade(
                    writer, operator_authorized=True,
                )).published
                # Pending historical work must use current identity inventory;
                # the direct-owner shortcut itself grants no new authority.
                writer.add(TemporalArtifact(
                    artifact_id="later-known-human", storage_key="later-known-key",
                    created_by_principal=owner,
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                ))
            await add_source_user(writer, User(id=UUID(zero), email="zero@example.invalid"))
            writer.add(
                TemporalArtifact(
                    artifact_id="real-zero-user",
                    storage_key="zero-user-key",
                    created_by_principal=f"{owner_prefix}{zero}",
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                )
            )
            await writer.commit()
        async with maker() as session:
            result = await conversion.run_guarded_upgrade(
                session, operator_authorized=True,
                alias_groups=[{zero, owner}] if scenario == "aliased" else None,
            )
            eligible = scenario in {"alone", "aliased"}
            assert result.published is eligible, result.to_sanitized_dict()
            assert result.decision.person_count == (1 if eligible else 2)
            if not eligible:
                assert result.decision.disposition == "multi_person_refusal"
            row = await session.get(TemporalArtifact, "real-zero-user")
            assert row.created_by_principal == f"{owner_prefix}{zero}"
            assert row.operator_recovery_granted is eligible
            assert UUID(zero) in await source_user_ids(session)


async def test_reserved_zero_eligibility_is_not_proof_of_a_real_user(
    tmp_path,
    monkeypatch,
):
    zero = conversion.DEPLOYMENT_SUBJECT_ID
    monkeypatch.setenv("DEFAULT_USER_ID", str(zero or ""))
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    async with database(tmp_path) as maker:
        async with maker() as session:
            for granted in (False, True):
                session.add(TemporalArtifact(
                    artifact_id=f"reserved-{granted}", storage_key=f"reserved-{granted}",
                    created_by_principal=zero, operator_recovery_granted=granted,
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                ))
            await session.commit()
            # eligible_ids may contain a retained secret holder without a User.
            # It must neither invent a grant nor revoke existing proof.
            outcome = await conversion.artifacts_transform(session, [zero])
            assert outcome == {"recovery_grants": 0, "preserved": 2}
            assert UUID(zero) not in await source_user_ids(session)
            for granted in (False, True):
                row = await session.get(TemporalArtifact, f"reserved-{granted}")
                assert row.created_by_principal == zero
                assert row.operator_recovery_granted is granted


async def test_container_user_evidence_recovers_without_rewriting_ownership(
    tmp_path,
    monkeypatch,
):
    from api_service.services.container_jobs import owner_artifact_principal
    from moonmind.schemas.container_job_models import OwnerIdentity

    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with database(tmp_path) as maker:
        async with maker() as writer:
            artifact_id, owner = await seed(writer)
            principal = owner_artifact_principal(
                OwnerIdentity(principal_type="user", principal_id=owner),
            )
            artifact = await writer.get(TemporalArtifact, artifact_id)
            artifact.created_by_principal = principal
            repository = TemporalArtifactRepository(writer)
            service = TemporalArtifactService(
                repository,
                store=LocalTemporalArtifactStore(tmp_path / "bytes"),
            )
            await service.write_complete(
                artifact_id=artifact_id,
                principal=principal,
                payload=b"job evidence",
                content_type="application/octet-stream",
            )
            claim = TemporalArtifactUseClaim(
                artifact_id=artifact_id,
                owner_principal=principal,
                request_id="container-download",
                operation_kind="download",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            writer.add(claim)
            await writer.commit()
            claim_id, expiry = claim.id, claim.expires_at.replace(tzinfo=None)
            quota = await repository.quota_usage_for_scope(principal)

        async with maker() as session:
            result = await conversion.run_guarded_upgrade(
                session,
                operator_authorized=True,
            )
            assert result.published, result.to_sanitized_dict()
            assert result.decision.person_count == 1
            artifact = await session.get(TemporalArtifact, artifact_id)
            assert artifact.created_by_principal == principal
            assert artifact.operator_recovery_granted
            claim = await session.get(TemporalArtifactUseClaim, claim_id)
            assert claim.owner_principal == principal
            assert claim.expires_at.replace(tzinfo=None) == expiry
            repository = TemporalArtifactRepository(session)
            assert await repository.quota_usage_for_scope(principal) == quota
            service = TemporalArtifactService(
                repository,
                store=LocalTemporalArtifactStore(tmp_path / "bytes"),
            )
            assert (
                await service.read(
                    artifact_id=artifact_id,
                    principal=principal,
                )
            )[1] == b"job evidence"
            assert (
                await service.read(
                    artifact_id=artifact_id,
                    principal="operator",
                )
            )[1] == b"job evidence"

        async with maker() as restarted:
            collect = AsyncMock(
                side_effect=AssertionError("completed recovery rescanned")
            )
            monkeypatch.setattr(conversion, "collect_inventory", collect)
            assert (
                await conversion.run_guarded_upgrade(
                    restarted,
                    operator_authorized=True,
                )
            ).published
            assert (await conversion.startup_guard_decision(restarted)).eligible
            collect.assert_not_awaited()
