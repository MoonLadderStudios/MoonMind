"""Unit tests for the durable Omnigent control-receipt store.

Issue MoonLadderStudios/MoonMind#3636. Proves that pending intent is persisted
before the side effect, a duplicate request returns the prior result without
duplicating the mutation, and ``delivery_unknown`` outcomes are reconciled onto
the same row.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentControlReceipt
from moonmind.omnigent.control_receipts import (
    RECEIPT_STATUS_COMPLETED,
    RECEIPT_STATUS_DELIVERY_UNKNOWN,
    RECEIPT_STATUS_FAILED,
    RECEIPT_STATUS_PENDING,
    ControlReceiptConflict,
    ControlReceiptIntent,
    OmnigentControlReceiptStore,
)


@pytest_asyncio.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield OmnigentControlReceiptStore(factory)
    await engine.dispose()


def _intent(**overrides) -> ControlReceiptIntent:
    base = dict(
        actor_principal="user:123",
        control_type="continue_same_session",
        request_id="req-1",
        idempotency_key="idem-1",
        workflow_id="mm:wf-1",
        run_id="run-1",
        agent_run_id="ar-1",
        bridge_session_id="brs-1",
        provider_session_id="sess-1",
        expected_session_epoch=2,
        agent_profile_digest="sha256:agent-1",
        provider_profile_generation=4,
        launch_policy_ref="lp-1",
        launch_snapshot_ref="omnigent-launch:sha256:abc",
        policy_digest="sha256:policy-1",
    )
    base.update(overrides)
    return ControlReceiptIntent(**base)


@pytest.mark.asyncio
async def test_begin_persists_pending_intent(store):
    view, is_new = await store.begin(_intent())
    assert is_new is True
    assert view.status == RECEIPT_STATUS_PENDING
    assert view.is_terminal is False
    assert view.control_type == "continue_same_session"

    # The immutable-policy evidence is durably persisted on the row.
    async with store._session_factory() as session:
        row = await session.get(OmnigentControlReceipt, view.receipt_id)
        assert row.policy_digest == "sha256:policy-1"
        assert row.agent_profile_digest == "sha256:agent-1"
        assert row.provider_profile_generation == 4
        assert row.expected_session_epoch == 2
        assert row.actor_principal == "user:123"


@pytest.mark.asyncio
async def test_begin_blank_idempotency_key_rejected(store):
    with pytest.raises(ValueError):
        await store.begin(_intent(idempotency_key="  "))


@pytest.mark.asyncio
async def test_duplicate_returns_prior_result_without_new_side_effect(store):
    view, is_new = await store.begin(_intent())
    assert is_new is True
    await store.mark_dispatched(view.receipt_id)
    await store.finalize(
        view.receipt_id,
        status=RECEIPT_STATUS_COMPLETED,
        upstream_correlation="corr-1",
        result={"controlEventRef": "cer-1"},
    )

    # A retry under the same idempotency key must NOT be treated as new.
    dup, is_new = await store.begin(_intent(request_id="req-2"))
    assert is_new is False
    assert dup.receipt_id == view.receipt_id
    assert dup.status == RECEIPT_STATUS_COMPLETED
    assert dup.is_terminal is True
    assert dup.result == {"controlEventRef": "cer-1"}
    assert dup.upstream_correlation == "corr-1"


@pytest.mark.asyncio
async def test_reused_key_different_control_type_conflicts(store):
    await store.begin(_intent())
    with pytest.raises(ControlReceiptConflict):
        await store.begin(_intent(control_type="cancel_session"))


@pytest.mark.asyncio
async def test_finalize_is_immutable_once_terminal(store):
    view, _ = await store.begin(_intent())
    await store.finalize(view.receipt_id, status=RECEIPT_STATUS_COMPLETED, result={"a": 1})
    # A second finalize with a different outcome cannot rewrite the authority.
    again = await store.finalize(
        view.receipt_id, status=RECEIPT_STATUS_FAILED, result={"a": 2}
    )
    assert again.status == RECEIPT_STATUS_COMPLETED
    assert again.result == {"a": 1}
    assert again.completed_at is not None


@pytest.mark.asyncio
async def test_delivery_unknown_reconciles_to_terminal(store):
    view, _ = await store.begin(_intent())
    await store.mark_dispatched(view.receipt_id)
    unknown = await store.finalize(
        view.receipt_id, status=RECEIPT_STATUS_DELIVERY_UNKNOWN
    )
    assert unknown.status == RECEIPT_STATUS_DELIVERY_UNKNOWN
    assert unknown.is_terminal is False

    reconciled = await store.reconcile(
        view.receipt_id,
        status=RECEIPT_STATUS_COMPLETED,
        upstream_correlation="corr-late",
        result={"controlEventRef": "cer-late"},
    )
    assert reconciled.status == RECEIPT_STATUS_COMPLETED
    assert reconciled.is_terminal is True
    assert reconciled.completed_at is not None

    # After reconciliation a duplicate returns the terminal reconciled result.
    dup, is_new = await store.begin(_intent(request_id="req-retry"))
    assert is_new is False
    assert dup.status == RECEIPT_STATUS_COMPLETED
    assert dup.result == {"controlEventRef": "cer-late"}


@pytest.mark.asyncio
async def test_reconcile_does_not_touch_terminal_receipt(store):
    view, _ = await store.begin(_intent())
    await store.finalize(view.receipt_id, status=RECEIPT_STATUS_COMPLETED, result={"x": 1})
    reconciled = await store.reconcile(view.receipt_id, status=RECEIPT_STATUS_FAILED)
    assert reconciled.status == RECEIPT_STATUS_COMPLETED
    assert reconciled.result == {"x": 1}


@pytest.mark.asyncio
async def test_get_by_idempotency_key(store):
    assert await store.get_by_idempotency_key("missing") is None
    view, _ = await store.begin(_intent())
    fetched = await store.get_by_idempotency_key("idem-1")
    assert fetched is not None
    assert fetched.receipt_id == view.receipt_id
