"""Pooled, lifecycle-managed Omnigent HTTP/SSE transport.

Source issue: MoonLadderStudios/MoonMind#3878.

A pool is only useful if it is actually reused, actually bounded, and actually
closed. Each of those three is tested here, plus the property that a caller
without a pool keeps its own short-lived client — so no test or script has to
manage a lifecycle it never opted into.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from moonmind.omnigent.transport import (
    OMNIGENT_HTTP_DEFAULT_KEEPALIVE_EXPIRY_SECONDS,
    OMNIGENT_HTTP_DEFAULT_MAX_CONNECTIONS,
    OMNIGENT_HTTP_DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
    OMNIGENT_HTTP_KEEPALIVE_EXPIRY_ENV,
    OMNIGENT_HTTP_MAX_CONNECTIONS_ENV,
    OMNIGENT_HTTP_MAX_KEEPALIVE_ENV,
    OmnigentTransportPool,
    omnigent_httpx_client,
    resolved_transport_limits,
)


@pytest.mark.asyncio
async def test_pool_hands_out_the_same_client_across_calls() -> None:
    """Reuse is the point: a new client per call pools nothing."""

    pool = OmnigentTransportPool()
    try:
        assert pool.client() is pool.client()
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_pool_applies_bounded_connection_limits() -> None:
    """A pool without limits just moves unbounded behavior one layer down."""

    limits = httpx.Limits(max_connections=3, max_keepalive_connections=2)
    pool = OmnigentTransportPool(limits=limits)
    try:
        assert pool.limits is limits
        assert pool.client() is not None
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_close_is_idempotent_and_reopens_cleanly() -> None:
    pool = OmnigentTransportPool()
    first = pool.client()

    await pool.aclose()
    assert pool.closed is True
    await pool.aclose()

    second = pool.client()
    assert second is not first
    await pool.aclose()


def test_a_pool_bound_to_another_loop_fails_loudly() -> None:
    """A silently re-pooled client hands out connections its loop cannot drive."""

    pool = OmnigentTransportPool()

    async def _bind() -> None:
        pool.client()

    async def _reuse() -> None:
        pool.client()

    asyncio.run(_bind())

    with pytest.raises(RuntimeError, match="different event loop"):
        asyncio.run(_reuse())

    asyncio.run(pool.aclose())


@pytest.mark.asyncio
async def test_injected_pool_survives_the_context_block() -> None:
    """Callers handed a pool must not close it; the process owns that."""

    pool = OmnigentTransportPool()
    try:
        async with omnigent_httpx_client(pool) as client:
            assert client is pool.client()
        assert client.is_closed is False
        assert pool.closed is False
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_absent_pool_yields_an_owned_short_lived_client() -> None:
    """No pool means no lifecycle to manage: the block owns and closes it."""

    async with omnigent_httpx_client(None) as client:
        assert isinstance(client, httpx.AsyncClient)
        assert client.is_closed is False

    assert client.is_closed is True


def test_limits_default_without_configuration() -> None:
    limits = resolved_transport_limits(env={})

    assert limits.max_connections == OMNIGENT_HTTP_DEFAULT_MAX_CONNECTIONS
    assert (
        limits.max_keepalive_connections
        == OMNIGENT_HTTP_DEFAULT_MAX_KEEPALIVE_CONNECTIONS
    )
    assert limits.keepalive_expiry == OMNIGENT_HTTP_DEFAULT_KEEPALIVE_EXPIRY_SECONDS


def test_limits_are_operator_configurable() -> None:
    limits = resolved_transport_limits(
        env={
            OMNIGENT_HTTP_MAX_CONNECTIONS_ENV: "10",
            OMNIGENT_HTTP_MAX_KEEPALIVE_ENV: "5",
            OMNIGENT_HTTP_KEEPALIVE_EXPIRY_ENV: "15",
        }
    )

    assert limits.max_connections == 10
    assert limits.max_keepalive_connections == 5
    assert limits.keepalive_expiry == 15.0


@pytest.mark.parametrize("raw", ["0", "-2", "nope"])
def test_invalid_limits_fail_fast(raw: str) -> None:
    with pytest.raises(ValueError, match=OMNIGENT_HTTP_MAX_CONNECTIONS_ENV):
        resolved_transport_limits(env={OMNIGENT_HTTP_MAX_CONNECTIONS_ENV: raw})
