"""Host-registration inventory reads scale independently of concurrency.

Source issue: MoonLadderStudios/MoonMind#3878.

Every registration waiter polls the same whole-inventory listing, so at
concurrency ``N`` the endpoint saw ``N`` identical requests per poll. These
tests pin the two properties that make the cost independent of ``N`` without
weakening correlation: concurrent readers share one in-flight request, and a
cached answer is never served past its TTL.
"""

from __future__ import annotations

import asyncio

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.registration import (
    OmnigentHostInventoryReader,
    OmnigentHostRegistrationService,
)


class _CountingClient:
    def __init__(self, hosts: list[dict] | None = None, *, delay: float = 0.0) -> None:
        self.calls = 0
        self.delay = delay
        self._hosts = hosts if hosts is not None else []

    async def list_hosts(self) -> list[dict]:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return list(self._hosts)


def _ready_host(name: str) -> dict:
    return {
        "host_id": f"host-{name}",
        "name": name,
        "owner": "local",
        "status": "online",
        "configured_harnesses": {"opencode-native": "ready"},
    }


@pytest.mark.asyncio
async def test_concurrent_readers_share_one_inventory_request() -> None:
    """N waiters polling at once must cost one listing, not N."""

    client = _CountingClient([_ready_host("a")], delay=0.01)
    reader = OmnigentHostInventoryReader(ttl_seconds=0.0)

    results = await asyncio.gather(
        *(reader.list_hosts(client) for _ in range(8))
    )

    assert client.calls == 1
    assert all(rows == [_ready_host("a")] for rows in results)


@pytest.mark.asyncio
async def test_a_cached_answer_is_reused_inside_its_ttl() -> None:
    client = _CountingClient([_ready_host("a")])
    reader = OmnigentHostInventoryReader(ttl_seconds=10.0)

    await reader.list_hosts(client, now=100.0)
    await reader.list_hosts(client, now=105.0)

    assert client.calls == 1


@pytest.mark.asyncio
async def test_a_stale_answer_is_never_served() -> None:
    """Staleness must stay inside the poll cycle the wait loop already tolerates."""

    client = _CountingClient([_ready_host("a")])
    reader = OmnigentHostInventoryReader(ttl_seconds=1.0)

    await reader.list_hosts(client, now=100.0)
    await reader.list_hosts(client, now=101.5)

    assert client.calls == 2


@pytest.mark.asyncio
async def test_callers_cannot_mutate_the_shared_cache() -> None:
    """One waiter editing its rows must not corrupt another's correlation."""

    client = _CountingClient([_ready_host("a")])
    reader = OmnigentHostInventoryReader(ttl_seconds=10.0)

    first = await reader.list_hosts(client, now=100.0)
    first.clear()
    second = await reader.list_hosts(client, now=100.5)

    assert second == [_ready_host("a")]


@pytest.mark.asyncio
async def test_registration_still_correlates_the_exact_launched_host() -> None:
    """Sharing the read changes cost, never which host is matched."""

    client = _CountingClient([_ready_host("other"), _ready_host("mine")])
    service = OmnigentHostRegistrationService(
        client=client,
        expected_owner="local",
        attempts=1,
        inventory_reader=OmnigentHostInventoryReader(ttl_seconds=0.0),
    )

    result = await service.wait_for_registration(
        correlation_name="mine", harness_id="opencode-native"
    )

    assert result["omnigentHostId"] == "host-mine"
    assert result["harnessReady"] is True


@pytest.mark.asyncio
async def test_ambiguous_correlation_still_fails_closed() -> None:
    duplicate = [_ready_host("mine"), _ready_host("mine")]
    service = OmnigentHostRegistrationService(
        client=_CountingClient(duplicate),
        expected_owner="local",
        attempts=1,
        inventory_reader=OmnigentHostInventoryReader(ttl_seconds=0.0),
    )

    with pytest.raises(HarnessPlatformError) as exc_info:
        await service.wait_for_registration(
            correlation_name="mine", harness_id="opencode-native"
        )

    assert exc_info.value.code == "OMNIGENT_HOST_REGISTRATION_TIMEOUT"


@pytest.mark.asyncio
async def test_two_registration_services_share_the_same_reader() -> None:
    """Concurrent executions build separate service graphs but one endpoint."""

    client = _CountingClient([_ready_host("mine")], delay=0.01)
    reader = OmnigentHostInventoryReader(ttl_seconds=5.0)
    services = [
        OmnigentHostRegistrationService(
            client=client,
            expected_owner="local",
            attempts=1,
            inventory_reader=reader,
        )
        for _ in range(4)
    ]

    await asyncio.gather(
        *(
            service.wait_for_registration(
                correlation_name="mine", harness_id="opencode-native"
            )
            for service in services
        )
    )

    assert client.calls == 1


class _FailingClient:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.calls = 0
        self.delay = delay

    async def list_hosts(self) -> list[dict]:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        raise RuntimeError("omnigent endpoint unavailable")


@pytest.mark.asyncio
async def test_a_failed_read_is_not_cached_and_is_raised_to_every_waiter() -> None:
    """A transport error must surface, not be swallowed into an empty inventory."""

    client = _FailingClient(delay=0.01)
    reader = OmnigentHostInventoryReader(ttl_seconds=10.0)

    results = await asyncio.gather(
        *(reader.list_hosts(client) for _ in range(3)), return_exceptions=True
    )

    assert client.calls == 1
    assert all(isinstance(item, RuntimeError) for item in results)
    # Nothing was cached, so the next poll really re-reads.
    with pytest.raises(RuntimeError):
        await reader.list_hosts(client)
    assert client.calls == 2


@pytest.mark.asyncio
async def test_cancelling_one_waiter_does_not_cancel_the_shared_read() -> None:
    """Otherwise one cancelled run would break registration for every other."""

    client = _CountingClient([_ready_host("a")], delay=0.05)
    reader = OmnigentHostInventoryReader(ttl_seconds=0.0)

    leader = asyncio.create_task(reader.list_hosts(client))
    await asyncio.sleep(0.01)
    follower = asyncio.create_task(reader.list_hosts(client))
    await asyncio.sleep(0.01)
    follower.cancel()

    with pytest.raises(asyncio.CancelledError):
        await follower
    assert await leader == [_ready_host("a")]
    assert client.calls == 1
