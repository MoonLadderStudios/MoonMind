"""Shared Omnigent control-plane integration fixtures.

Source: MoonLadderStudios/MoonMind#3703 / #3709.

Binds an :class:`OmnigentControlPlaneStore` over the seven control-plane tables.
The ephemeral-PostgreSQL provisioning (``control_plane_postgres_url``) lives in
``tests/integration/conftest.py`` so every integration suite shares one owner.
Both the decisive-invariant coverage in ``test_control_plane_postgres.py`` and the
fault-injection replay binding in ``test_control_plane_faultlab.py`` consume these
fixtures.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentChatBindingAlias,
    OmnigentCleanupAuthority,
    OmnigentCommand,
    OmnigentCredentialRuntimeRecord,
    OmnigentExecutionPlanRecord,
    OmnigentHostBindingRecordV2,
    OmnigentHostLeaseRecordV2,
    OmnigentObservation,
    OmnigentReconciliationDecision,
    OmnigentRuntimeBindingRecord,
    OmnigentSession,
    OmnigentTurnAttempt,
)
from moonmind.omnigent.control_plane import OmnigentControlPlaneStore

_CONTROL_PLANE_TABLES = [
    # MoonLadderStudios/MoonMind#3701: the canonical session now has foreign
    # keys to the immutable admission authority, so PostgreSQL integration
    # fixtures must exercise those real handoff tables too.
    ManagedAgentProviderProfile.__table__,
    OmnigentExecutionPlanRecord.__table__,
    OmnigentRuntimeBindingRecord.__table__,
    OmnigentHostBindingRecordV2.__table__,
    OmnigentHostLeaseRecordV2.__table__,
    OmnigentCredentialRuntimeRecord.__table__,
    OmnigentSession.__table__,
    OmnigentTurnAttempt.__table__,
    OmnigentObservation.__table__,
    OmnigentCommand.__table__,
    OmnigentReconciliationDecision.__table__,
    OmnigentChatBindingAlias.__table__,
    OmnigentCleanupAuthority.__table__,
]


def _create_tables(sync_conn) -> None:
    for table in _CONTROL_PLANE_TABLES:
        table.create(sync_conn, checkfirst=True)


def _drop_tables(sync_conn) -> None:
    for table in reversed(_CONTROL_PLANE_TABLES):
        table.drop(sync_conn, checkfirst=True)


@pytest_asyncio.fixture()
async def pg_store(control_plane_postgres_url):
    """Bind an :class:`OmnigentControlPlaneStore` over an ephemeral PostgreSQL."""

    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(_create_tables)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield OmnigentControlPlaneStore(maker)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_tables)
        await engine.dispose()
