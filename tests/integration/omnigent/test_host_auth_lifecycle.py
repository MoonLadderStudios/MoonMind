"""Production-database lifecycle evidence for embedded host authentication."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import OmnigentHostAuthProfileRecord
from moonmind.omnigent.host_auth_contracts import (
    HostAuthCredentialProfile,
    rotate_host_auth_profile,
)
from moonmind.omnigent.host_auth_store import HostAuthProfileStore

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest_asyncio.fixture
async def host_auth_store(control_plane_postgres_url):
    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(OmnigentHostAuthProfileRecord.__table__.create)
    try:
        yield HostAuthProfileStore(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


async def test_concurrent_rotation_has_one_generation_winner_on_postgres(
    host_auth_store,
) -> None:
    """The compose PostgreSQL backend must serialize generation-checked writers."""

    store = host_auth_store
    await store.put(HostAuthCredentialProfile("managed", "env://HOST_ONE", 1))
    ready_to_rotate = asyncio.Barrier(2)

    async def writer(secret_ref: str):
        current = await store.get_active()
        assert current is not None and current.current_generation == 1
        await ready_to_rotate.wait()
        candidate = rotate_host_auth_profile(
            current,
            new_secret_ref=secret_ref,
            overlap=timedelta(minutes=5),
        )
        try:
            return await store.put(candidate, expected_generation=1)
        except RuntimeError as exc:
            return exc

    results = await asyncio.gather(
        writer("env://HOST_TWO_A"),
        writer("env://HOST_TWO_B"),
    )

    winners = [item for item in results if isinstance(item, HostAuthCredentialProfile)]
    losers = [item for item in results if isinstance(item, RuntimeError)]
    assert len(winners) == len(losers) == 1
    durable = await store.get_active()
    assert durable is not None
    assert durable.current_generation == 2
    assert durable.current_secret_ref == winners[0].current_secret_ref
