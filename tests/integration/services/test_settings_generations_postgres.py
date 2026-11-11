"""Overlapping settings transactions on production PostgreSQL semantics."""

import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import SettingsAuditEvent, SettingsOverride
from api_service.services.settings_catalog import (
    SettingRegistryEntry,
    SettingsCatalogService,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci, pytest.mark.asyncio]

REGISTRY = tuple(
    SettingRegistryEntry(
        key=key,
        title=key,
        category="Test",
        section="user-workspace",
        value_type="integer",
        ui="number",
        default_value=0,
        order=index,
    )
    for index, key in enumerate(("test.changed", "test.companion"))
)


@pytest_asyncio.fixture
async def settings_pg(control_plane_postgres_url):
    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as connection:
        for table in (SettingsOverride.__table__, SettingsAuditEvent.__table__):
            await connection.run_sync(table.create)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_missing_unchanged_companion_cannot_change_before_pair_commit(
    settings_pg,
):
    reached_snapshot = asyncio.Event()
    release_pair = asyncio.Event()
    async with settings_pg() as pair_session, settings_pg() as other_session:
        pair = SettingsCatalogService(
            session=pair_session,
            registry=REGISTRY,
            env={},
            change_publisher=AsyncMock(),
        )
        other = SettingsCatalogService(
            session=other_session,
            registry=REGISTRY,
            env={},
            change_publisher=AsyncMock(),
        )

        async def pause_after_preconditions(_values):
            reached_snapshot.set()
            await release_pair.wait()

        pair._prime_referenced_resources = pause_after_preconditions
        pending = asyncio.create_task(
            pair.apply_overrides(
                changes={"test.changed": 5},
                expected_versions={"test.changed": 1, "test.companion": 1},
            )
        )
        try:
            await asyncio.wait_for(reached_snapshot.wait(), timeout=5)
            # FOR UPDATE did not lock this missing companion. Its first writer
            # commits between the pair's precondition read and attempted commit.
            saved = await asyncio.wait_for(
                other.apply_overrides(
                    changes={"test.companion": 9},
                    expected_versions={"test.companion": 1},
                ),
                timeout=5,
            )
            assert saved.values["test.companion"].value_version == 2
            release_pair.set()
            with pytest.raises(ValueError, match="version_conflict"):
                await asyncio.wait_for(pending, timeout=5)
        finally:
            release_pair.set()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    async with settings_pg() as check:
        rows = (await check.scalars(select(SettingsOverride))).all()
        assert [(row.key, row.value_json, row.value_version) for row in rows] == [
            ("test.companion", 9, 2)
        ]
        audits = (await check.scalars(select(SettingsAuditEvent))).all()
        assert [row.key for row in audits] == ["test.companion"]


async def test_opposite_first_writers_order_all_changed_and_guarded_inserts(
    settings_pg,
):
    both_read = asyncio.Event()
    arrived = 0

    async def meet_after_preconditions(_values):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both_read.set()
        await both_read.wait()

    async def write(key, companion):
        async with settings_pg() as session:
            service = SettingsCatalogService(
                session=session, registry=REGISTRY, env={}, change_publisher=AsyncMock()
            )
            service._prime_referenced_resources = meet_after_preconditions
            try:
                return await service.apply_overrides(
                    changes={key: 5}, expected_versions={key: 1, companion: 1}
                )
            except ValueError as error:
                assert str(error) == "version_conflict"
                return None

    results = await asyncio.wait_for(
        asyncio.gather(
            write("test.changed", "test.companion"),
            write("test.companion", "test.changed"),
        ),
        timeout=10,
    )
    assert sum(result is not None for result in results) == 1
    winner = next(result for result in results if result is not None)
    changed_key = next(iter(winner.values))
    async with settings_pg() as session:
        rows = {
            row.key: row
            for row in (await session.scalars(select(SettingsOverride))).all()
        }
        assert set(rows) == {"test.changed", "test.companion"}
        assert rows[changed_key].value_json == 5
        assert rows[changed_key].value_version == 2
        unchanged = next(row for key, row in rows.items() if key != changed_key)
        assert unchanged.is_reset
        assert unchanged.value_json is None
        assert unchanged.value_version == 1
        audits = (await session.scalars(select(SettingsAuditEvent))).all()
        assert [row.key for row in audits] == [changed_key]
