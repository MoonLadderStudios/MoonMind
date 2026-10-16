"""The shared table must not give the generic janitor legacy authority."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import (
    Base,
    OmnigentExecutionPlanRecord,
    OmnigentRuntimeBindingRecord,
)
from moonmind.omnigent.generic_host_janitor import GenericOmnigentHostJanitor
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.stores import (
    DbRuntimeBindingStore as LegacyStore,
)
from moonmind.omnigent.runtime_bindings import (
    DbRuntimeBindingStore,
    RuntimeBindingState,
)

PLAN_REF = "omnigent-execution-plan:sha256:" + "1" * 64


@pytest_asyncio.fixture
async def sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/inventory.db")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda connection: Base.metadata.create_all(
                connection,
                tables=[
                    OmnigentExecutionPlanRecord.__table__,
                    OmnigentRuntimeBindingRecord.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            OmnigentExecutionPlanRecord(
                plan_ref=PLAN_REF,
                schema_version="moonmind.omnigent-execution-plan-envelope.v1",
                payload_json={},
                harness_id="opencode-native",
                harness_implementation_ref="impl",
                host_class_ref="omnigent-opencode@1",
                launch_policy_ref="omnigent-on-demand@1",
                execution_realizer_ref="generic-omnigent-host@1",
            )
        )
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_cleaned", [False, True])
@pytest.mark.parametrize("owner_closed", [False, True])
async def test_inventory_keeps_legacy_records_out_of_generic_cleanup(
    sessions, legacy_cleaned, owner_closed, monkeypatch
):
    legacy = LegacyStore(sessions)
    old = await legacy.create_initial(
        execution_plan_ref=PLAN_REF,
        execution_scope_ref="legacy-workflow",
        provider_leases={
            "primary-model": {
                "providerProfileRef": "profile",
                "providerLeaseRef": "lease",
                "credentialGeneration": 1,
                "credentialRuntimeRef": "credential-runtime:lease:1",
            }
        },
    )
    if legacy_cleaned:
        await legacy.mark_cleanup_complete(
            old.runtimeBindingRef, expected_revision=1, expected_fencing_generation=1
        )
    before = await legacy.get_state(old.runtimeBindingRef)
    store = DbRuntimeBindingStore(sessions)

    class Earlier(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) - timedelta(minutes=10)

    with monkeypatch.context() as patch:
        patch.setattr("moonmind.omnigent.runtime_bindings.datetime", Earlier)
        stale = await store.create_initial(
            execution_plan_ref=PLAN_REF, idempotency_key="stale", provider_leases={}
        )
    recent = await store.create_initial(
        execution_plan_ref=PLAN_REF, idempotency_key="recent", provider_leases={}
    )
    await store.update(
        recent.bindingId,
        expected_revision=1,
        expected_fencing_generation=1,
        updates={"heartbeatAt": datetime.now(UTC)},
    )
    cleaned = await store.create_initial(
        execution_plan_ref=PLAN_REF, idempotency_key="cleaned", provider_leases={}
    )
    pending = await store.update(
        cleaned.bindingId,
        expected_revision=1,
        expected_fencing_generation=1,
        state=RuntimeBindingState.cleanup_pending,
    )
    await store.update(
        cleaned.bindingId,
        expected_revision=pending.revision,
        expected_fencing_generation=1,
        state=RuntimeBindingState.cleaned,
    )
    assert await store.list_recoverable(
        stale_before=datetime.now(UTC) - timedelta(minutes=5)
    ) == (stale,)
    realizer = AsyncMock()
    owner = AsyncMock(return_value=owner_closed)
    result = await GenericOmnigentHostJanitor(
        runtime_bindings=store,
        host_leases=AsyncMock(list_recoverable=AsyncMock(return_value=())),
        realizer=realizer,
        owner_has_closed=owner,
    ).run()
    assert result["failures"] == []
    assert result["runtimeBindingsExamined"] == 1
    assert result["reconciled"] == int(owner_closed)
    owner.assert_awaited_once_with(stale)
    if owner_closed:
        realizer.reconcile.assert_awaited_once_with(PLAN_REF, stale.bindingId)
    else:
        realizer.reconcile.assert_not_awaited()
    assert await legacy.get_state(old.runtimeBindingRef) == before


@pytest.mark.asyncio
async def test_inventory_does_not_hide_corrupt_stable_snapshot(sessions):
    store = DbRuntimeBindingStore(sessions)
    binding = await store.create_initial(
        execution_plan_ref=PLAN_REF, idempotency_key="corrupt", provider_leases={}
    )
    async with sessions() as session:
        row = await session.get(OmnigentRuntimeBindingRecord, binding.latestSnapshotRef)
        row.latest_snapshot_ref = "invalid"
        await session.commit()
    with pytest.raises(HarnessPlatformError, match="snapshot digest"):
        await store.list_recoverable(stale_before=datetime.now(UTC))
