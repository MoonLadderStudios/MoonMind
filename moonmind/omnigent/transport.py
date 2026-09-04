"""Lifecycle-managed pooled HTTP/SSE transport for Omnigent calls.

Source issue: MoonLadderStudios/MoonMind#3878.

Production previously built an ``httpx.AsyncClient`` per call, or at best per
execution, so nothing was pooled across the many short control calls one run
makes and nothing was pooled across concurrent runs. At concurrency ``N`` that
multiplies TCP and TLS setup by ``N`` and by the number of calls per run, and
it makes connection count an emergent property of call sites rather than a
governed limit.

This module owns one pooled client per process with explicit connection limits,
and an explicit close. Both properties matter: a pool without a close leaks
sockets across worker shutdown, and a pool without limits just moves the
unbounded behavior one layer down.

The pool is injected, never discovered. Callers that are handed one use it and
never close it; callers that are not keep their own short-lived client. That
keeps ownership unambiguous — the process that opened the pool is the process
that closes it — and keeps tests free of a client bound to a dead event loop.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Mapping

import httpx

OMNIGENT_HTTP_MAX_CONNECTIONS_ENV = "MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS"
OMNIGENT_HTTP_MAX_KEEPALIVE_ENV = "MOONMIND_OMNIGENT_HTTP_MAX_KEEPALIVE_CONNECTIONS"
OMNIGENT_HTTP_KEEPALIVE_EXPIRY_ENV = "MOONMIND_OMNIGENT_HTTP_KEEPALIVE_EXPIRY_SECONDS"

#: Sized for the documented generic-host ceiling: each concurrent run holds one
#: long-lived SSE stream plus a small number of in-flight control calls.
OMNIGENT_HTTP_DEFAULT_MAX_CONNECTIONS = 64
OMNIGENT_HTTP_DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 32
OMNIGENT_HTTP_DEFAULT_KEEPALIVE_EXPIRY_SECONDS = 30.0


def _positive_number(
    value: object | None, *, default: float, env_name: str
) -> float:
    cleaned = str(value or "").strip()
    if not cleaned:
        return default
    try:
        parsed = float(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"invalid {env_name} value {cleaned!r}: expected a positive number"
        ) from exc
    if parsed <= 0:
        raise ValueError(
            f"invalid {env_name} value {cleaned!r}: expected a positive number"
        )
    return parsed


def resolved_transport_limits(
    *, env: Mapping[str, Any] | None = None
) -> httpx.Limits:
    """Return the operator-governed connection-pool limits."""

    source = env if env is not None else os.environ
    return httpx.Limits(
        max_connections=int(
            _positive_number(
                source.get(OMNIGENT_HTTP_MAX_CONNECTIONS_ENV),
                default=OMNIGENT_HTTP_DEFAULT_MAX_CONNECTIONS,
                env_name=OMNIGENT_HTTP_MAX_CONNECTIONS_ENV,
            )
        ),
        max_keepalive_connections=int(
            _positive_number(
                source.get(OMNIGENT_HTTP_MAX_KEEPALIVE_ENV),
                default=OMNIGENT_HTTP_DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
                env_name=OMNIGENT_HTTP_MAX_KEEPALIVE_ENV,
            )
        ),
        keepalive_expiry=_positive_number(
            source.get(OMNIGENT_HTTP_KEEPALIVE_EXPIRY_ENV),
            default=OMNIGENT_HTTP_DEFAULT_KEEPALIVE_EXPIRY_SECONDS,
            env_name=OMNIGENT_HTTP_KEEPALIVE_EXPIRY_ENV,
        ),
    )


class OmnigentTransportPool:
    """One pooled ``httpx.AsyncClient`` with an explicit close.

    The client is created lazily on first use and bound to the event loop that
    created it. Rebinding is refused rather than silently re-pooled: a pool
    handed to a second loop would hand out connections that loop cannot drive,
    which fails far from the mistake.
    """

    def __init__(
        self,
        *,
        limits: httpx.Limits | None = None,
        env: Mapping[str, Any] | None = None,
    ) -> None:
        self._limits = limits or resolved_transport_limits(env=env)
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def limits(self) -> httpx.Limits:
        return self._limits

    @property
    def closed(self) -> bool:
        return self._client is None

    def client(self) -> httpx.AsyncClient:
        """Return the pooled client, creating it on the current loop."""

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._client is None:
            self._client = httpx.AsyncClient(limits=self._limits)
            self._loop = running
            return self._client
        if running is not None and self._loop is not None and running is not self._loop:
            raise RuntimeError(
                "Omnigent transport pool is bound to a different event loop; "
                "build one pool per worker process"
            )
        return self._client

    async def aclose(self) -> None:
        client, self._client, self._loop = self._client, None, None
        if client is not None:
            await client.aclose()


@asynccontextmanager
async def omnigent_httpx_client(
    pool: OmnigentTransportPool | None,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a pooled client when one is injected, else an owned short-lived one.

    A pooled client outlives the block and is never closed here; an owned one
    is closed on exit. Callers therefore do not need to know which they got.
    """

    if pool is not None:
        yield pool.client()
        return
    async with httpx.AsyncClient() as client:
        yield client


__all__ = [
    "OMNIGENT_HTTP_DEFAULT_KEEPALIVE_EXPIRY_SECONDS",
    "OMNIGENT_HTTP_DEFAULT_MAX_CONNECTIONS",
    "OMNIGENT_HTTP_DEFAULT_MAX_KEEPALIVE_CONNECTIONS",
    "OMNIGENT_HTTP_KEEPALIVE_EXPIRY_ENV",
    "OMNIGENT_HTTP_MAX_CONNECTIONS_ENV",
    "OMNIGENT_HTTP_MAX_KEEPALIVE_ENV",
    "OmnigentTransportPool",
    "omnigent_httpx_client",
    "resolved_transport_limits",
]
