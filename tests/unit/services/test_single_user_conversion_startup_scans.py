"""Bound startup work without weakening authoritative conversion or recovery."""

from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from api_service.db.models import (
    Base,
    TemporalArtifact,
    TemporalArtifactRedactionLevel,
    User,
)
from api_service.services import single_user_conversion as conversion
from moonmind.security import auth_modes_4120
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/startup.db")
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


async def seed(session):
    owner = uuid4()
    session.add(User(id=owner, email="retained@example.invalid"))
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
async def test_default_repairs_uncertified_completion_atomically(
    tmp_path, monkeypatch, interrupt_repair,
):
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "oidc")

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
                session, operator_authorized=True, transforms=custom,
            )
            assert first.published
            # Generic explicit custom replay remains idempotent.
            custom_replay = await conversion.run_guarded_upgrade(
                session, operator_authorized=True, transforms=custom,
            )
            assert custom_replay.digest == first.digest
            noop.assert_awaited_once()
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
            assert not original_result.get("production_transforms_completed")

        if interrupt_repair:
            async with maker() as interrupted:
                engine = interrupted.get_bind()

                def fail_publication(_conn, _cursor, statement, *_args):
                    if statement.startswith("UPDATE single_user_conversion_runs"):
                        raise RuntimeError("interrupted repair publication")

                event.listen(engine, "before_cursor_execute", fail_publication)
                try:
                    with pytest.raises(RuntimeError, match="repair publication"):
                        await conversion.run_guarded_upgrade(
                            interrupted, operator_authorized=True,
                        )
                finally:
                    event.remove(engine, "before_cursor_execute", fail_publication)
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


async def test_startup_replay_does_not_grant_new_historical_artifact(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "oidc")
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
        async with maker() as restarted:
            again = await conversion.run_guarded_upgrade(
                restarted, operator_authorized=True,
            )
            assert again.digest == first.digest
            row = await restarted.get(TemporalArtifact, new_id)
            assert row.operator_recovery_granted is False
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(restarted),
                store=LocalTemporalArtifactStore(tmp_path / "bytes"),
            )
            with pytest.raises(TemporalArtifactAuthorizationError):
                await artifacts.read(artifact_id=new_id, principal="operator")
