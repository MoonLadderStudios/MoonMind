"""Catalog observations join admission while concurrent immutable identities converge."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from api_service.db.models import (
    OmnigentAgentProfile,
    OmnigentHarnessCatalogSnapshotRecord,
    OmnigentHarnessTrustRecord,
)
from moonmind.omnigent.harness_platform.catalog_service import (
    DbHarnessCatalogRepository,
    InMemoryHarnessCatalogRepository,
    OmnigentHarnessCatalogService,
)
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.omnigent.test_harness_catalog_repository import _FakeInventoryClient

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]
_TABLES = [
    OmnigentHarnessCatalogSnapshotRecord.__table__,
    OmnigentHarnessTrustRecord.__table__,
    OmnigentAgentProfile.__table__,
]


async def _observation(offset=0):
    return await OmnigentHarnessCatalogService(
        client=_FakeInventoryClient(),
        repository=InMemoryHarnessCatalogRepository(),
        endpoint_ref="default",
        omnigent_build_digest="sha256:" + "a" * 64,
        clock=lambda: datetime(2026, 10, 29, tzinfo=UTC) + timedelta(seconds=offset),
    ).synchronize()


@pytest.mark.parametrize("commit", [False, True])
async def test_catalog_and_caller_changes_share_one_transaction(commit):
    result = await _observation()
    async with isolated_postgres(_TABLES) as sessions:
        async with sessions() as caller:
            caller.add(
                OmnigentAgentProfile(
                    profile_id="caller-marker", display_name="Pending admission"
                )
            )
            await DbHarnessCatalogRepository.persist_in_session(caller, result)
            async with sessions() as observer:
                assert await observer.get(OmnigentAgentProfile, "caller-marker") is None
                assert (
                    await observer.get(
                        OmnigentHarnessCatalogSnapshotRecord, result.snapshot.catalogRef
                    )
                    is None
                )
                assert (
                    list(
                        (
                            await observer.scalars(select(OmnigentHarnessTrustRecord))
                        ).all()
                    )
                    == []
                )
            await (caller.commit() if commit else caller.rollback())
        async with sessions() as observer:
            assert (
                await observer.get(OmnigentAgentProfile, "caller-marker") is not None
            ) is commit
            assert (
                await observer.get(
                    OmnigentHarnessCatalogSnapshotRecord, result.snapshot.catalogRef
                )
                is not None
            ) is commit
            assert len(
                list((await observer.scalars(select(OmnigentHarnessTrustRecord))).all())
            ) == (len(result.trust_records) if commit else 0)


@pytest.mark.parametrize("same_observation", [True, False])
async def test_concurrent_catalog_admission_preserves_both_callers(same_observation):
    first = await _observation()
    second = first if same_observation else await _observation(1)
    async with isolated_postgres(_TABLES) as sessions:
        async with sessions() as setup:
            # Force both admissions to overlap their immutable snapshot insertion.
            await setup.execute(
                text(
                    """CREATE FUNCTION slow_catalog_insert() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN PERFORM pg_sleep(0.15); RETURN NEW; END $$"""
                )
            )
            await setup.execute(
                text(
                    """CREATE TRIGGER slow_catalog_insert BEFORE INSERT ON omnigent_harness_catalog_snapshots
                FOR EACH ROW EXECUTE FUNCTION slow_catalog_insert()"""
                )
            )
            await setup.commit()

        async def admit(name, result):
            async with sessions() as caller:
                caller.add(OmnigentAgentProfile(profile_id=name, display_name=name))
                await caller.flush()
                await DbHarnessCatalogRepository.persist_in_session(caller, result)
                assert caller.is_active
                await caller.commit()

        contenders = [
            asyncio.create_task(admit("first", first)),
            asyncio.create_task(admit("second", second)),
        ]
        try:
            await asyncio.wait_for(asyncio.gather(*contenders), timeout=15)
        finally:
            for contender in contenders:
                if not contender.done():
                    contender.cancel()
            await asyncio.gather(*contenders, return_exceptions=True)
        async with sessions() as observer:
            assert await observer.get(OmnigentAgentProfile, "first") is not None
            assert await observer.get(OmnigentAgentProfile, "second") is not None
            snapshots = list(
                (
                    await observer.scalars(select(OmnigentHarnessCatalogSnapshotRecord))
                ).all()
            )
            assert len(snapshots) == (1 if same_observation else 2)
            trust = list(
                (await observer.scalars(select(OmnigentHarnessTrustRecord))).all()
            )
            assert {row.implementation_ref for row in trust} == {
                row.implementationRef for row in first.trust_records
            }


@pytest.mark.parametrize("operator_state", ["blocked", "plugin_approved"])
async def test_catalog_admission_preserves_concurrent_operator_trust_decision(
    operator_state,
):
    first = await _observation()
    refresh = await _observation(1)
    implementation_ref = first.trust_records[0].implementationRef
    async with isolated_postgres(_TABLES) as sessions:
        async with sessions() as seed:
            await DbHarnessCatalogRepository.persist_in_session(seed, first)
            await seed.commit()
        started = asyncio.Event()

        async def admit():
            async with sessions() as caller:
                caller.add(
                    OmnigentAgentProfile(
                        profile_id="admission", display_name="Admission"
                    )
                )
                await caller.flush()
                started.set()
                await DbHarnessCatalogRepository.persist_in_session(caller, refresh)
                await caller.commit()

        async with sessions() as operator:
            row = await operator.get(
                OmnigentHarnessTrustRecord, implementation_ref, with_for_update=True
            )
            row.trust_state = operator_state
            await operator.flush()
            admission = asyncio.create_task(admit())
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                await asyncio.sleep(0.1)
                assert not admission.done()
                await operator.commit()
                await asyncio.wait_for(admission, timeout=10)
            finally:
                if not admission.done():
                    admission.cancel()
                    await asyncio.gather(admission, return_exceptions=True)
        async with sessions() as observer:
            row = await observer.get(OmnigentHarnessTrustRecord, implementation_ref)
            assert row.trust_state == operator_state
            assert row.catalog_ref == first.snapshot.catalogRef
            assert await observer.get(OmnigentAgentProfile, "admission") is not None
            assert (
                await observer.get(
                    OmnigentHarnessCatalogSnapshotRecord, refresh.snapshot.catalogRef
                )
                is not None
            )
