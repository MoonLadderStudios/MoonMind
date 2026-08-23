"""Durable harness-catalog observation semantics.

Regression coverage for MoonLadderStudios/MoonMind#3451: re-synchronizing
unchanged endpoint inventory must record a fresh immutable observation instead
of failing on a content-uniqueness constraint, and observation history must be
bounded without dropping authority snapshots pinned by trust records or Agent
Profile versions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import (
    Base,
    OmnigentAgentProfile,
    OmnigentAgentProfileVersion,
    OmnigentExecutionPlanRecord,
    OmnigentHarnessCatalogSnapshotRecord,
    OmnigentHarnessTrustRecord,
)
from moonmind.omnigent.harness_platform.catalog_service import (
    DbHarnessCatalogRepository,
    OmnigentHarnessCatalogService,
)

_BUILD_DIGEST = "sha256:" + "a" * 64


class _FakeInventoryClient:
    async def get_version(self) -> str:
        return "1.2.3"

    async def list_harnesses(self) -> list[dict]:
        return [
            {
                "id": "opencode-native",
                "label": "OpenCode",
                "capabilities": {"integration_mode": "native-server"},
            }
        ]

    async def list_agents(self) -> list[dict]:
        return [{"id": "opencode-native-ui", "version": "9"}]

    async def list_hosts(self) -> list[dict]:
        return []


class _Clock:
    def __init__(self) -> None:
        self._now = datetime(2026, 8, 23, tzinfo=UTC)

    def tick(self) -> datetime:
        self._now += timedelta(minutes=5)
        return self._now


async def _create_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/catalog.db")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                OmnigentHarnessCatalogSnapshotRecord.__table__,
                OmnigentHarnessTrustRecord.__table__,
                OmnigentAgentProfile.__table__,
                OmnigentAgentProfileVersion.__table__,
                OmnigentExecutionPlanRecord.__table__,
            ],
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_identical_inventory_resynchronizes_as_new_observation(
    tmp_path,
) -> None:
    """A second sync of unchanged content must not fail or dedupe away."""

    engine, factory = await _create_db(tmp_path)
    clock = _Clock()
    repository = DbHarnessCatalogRepository(factory)
    service = OmnigentHarnessCatalogService(
        client=_FakeInventoryClient(),
        repository=repository,
        endpoint_ref="default",
        omnigent_build_digest=_BUILD_DIGEST,
        clock=clock.tick,
    )

    first = await repository.persist(await service.synchronize())
    second = await repository.persist(await service.synchronize())

    assert first.snapshot.catalogRef != second.snapshot.catalogRef
    assert first.snapshot.sourceDigest == second.snapshot.sourceDigest
    latest = await repository.latest("default")
    assert latest is not None
    assert latest.snapshot.catalogRef == second.snapshot.catalogRef
    assert latest.snapshot.observedAt > first.snapshot.observedAt

    async with factory() as session:
        count = len(
            list(
                (
                    await session.execute(
                        select(OmnigentHarnessCatalogSnapshotRecord)
                    )
                ).scalars()
            )
        )
    assert count == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_observation_history_is_bounded_but_authority_survives(
    tmp_path,
) -> None:
    """Pruning keeps the newest window plus trust/profile-pinned snapshots."""

    engine, factory = await _create_db(tmp_path)
    clock = _Clock()
    repository = DbHarnessCatalogRepository(factory)
    service = OmnigentHarnessCatalogService(
        client=_FakeInventoryClient(),
        repository=repository,
        endpoint_ref="default",
        omnigent_build_digest=_BUILD_DIGEST,
        clock=clock.tick,
    )

    first = await repository.persist(await service.synchronize())
    async with factory() as session:
        session.add(
            OmnigentAgentProfile(
                profile_id="profile-pin",
                display_name="Pin",
            )
        )
        session.add(
            OmnigentAgentProfileVersion(
                profile_id="profile-pin",
                version=1,
                digest="sha256:" + "b" * 64,
                document={"harness": {"catalogRef": first.snapshot.catalogRef}},
            )
        )
        await session.commit()

    for _ in range(60):
        await repository.persist(await service.synchronize())

    async with factory() as session:
        total = await session.scalar(
            select(func.count()).select_from(OmnigentHarnessCatalogSnapshotRecord)
        )
        surviving_refs = set(
            (
                await session.execute(
                    select(OmnigentHarnessCatalogSnapshotRecord.catalog_ref)
                )
            ).scalars()
        )
        trust_refs = set(
            (
                await session.execute(select(OmnigentHarnessTrustRecord.catalog_ref))
            ).scalars()
        )

    # The trust record keeps its original catalog association, so the first
    # observation survives regardless of its age; everything else outside the
    # newest window is pruned.
    assert first.snapshot.catalogRef in surviving_refs
    assert trust_refs == {first.snapshot.catalogRef}
    assert total <= 51
    await engine.dispose()
