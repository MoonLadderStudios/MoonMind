"""Production-database lifecycle evidence for embedded host authentication."""

from __future__ import annotations

import asyncio

import pytest

from api_service.db.base import async_session_maker
from moonmind.omnigent.host_auth_profile import HostAuthCredentialProfile
from moonmind.omnigent.host_auth_store import HostAuthProfileStore

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def test_concurrent_rotation_has_one_generation_winner_on_postgres() -> None:
    """The compose PostgreSQL backend must serialize generation-checked writers."""

    store = HostAuthProfileStore(async_session_maker)
    await store.put(HostAuthCredentialProfile("managed", "env://HOST_ONE", 1))
    start = asyncio.Event()

    async def writer(secret_ref: str):
        current = await store.get_active()
        assert current is not None and current.current_generation == 1
        await start.wait()
        candidate = HostAuthCredentialProfile(
            profile_id=current.profile_id,
            current_secret_ref=secret_ref,
            current_generation=2,
            previous_secret_ref=current.current_secret_ref,
            previous_generation=1,
        )
        try:
            return await store.put(candidate, expected_generation=1)
        except RuntimeError as exc:
            return exc

    pending = [
        asyncio.create_task(writer("env://HOST_TWO_A")),
        asyncio.create_task(writer("env://HOST_TWO_B")),
    ]
    await asyncio.sleep(0)
    start.set()
    results = await asyncio.gather(*pending)

    winners = [item for item in results if isinstance(item, HostAuthCredentialProfile)]
    losers = [item for item in results if isinstance(item, RuntimeError)]
    assert len(winners) == len(losers) == 1
    durable = await store.get_active()
    assert durable is not None
    assert durable.current_generation == 2
    assert durable.current_secret_ref == winners[0].current_secret_ref

