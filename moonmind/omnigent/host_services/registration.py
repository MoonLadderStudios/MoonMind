"""Exact launched-host correlation against Omnigent inventory."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)


# Stock Omnigent first publishes an offline catalog row, then replaces it with
# the live host identity after the runner tunnel and harness discovery are
# ready. Cold startup on the supported local Compose path can exceed one
# minute, including the bounded direct-spawn fallback when the optional runner
# zygote is unavailable. Keep one shared, bounded registration policy for both
# host lifecycle implementations.
HOST_REGISTRATION_ATTEMPTS = 91
HOST_REGISTRATION_INTERVAL_SECONDS = 2.0
# MoonLadderStudios/MoonMind#3878: every waiter reads the same whole-inventory
# listing, so at concurrency ``N`` the endpoint sees ``N`` identical requests
# per poll. Coalescing concurrent readers behind one in-flight request, and
# reusing that answer for a fraction of the poll interval, makes the listing
# cost independent of ``N`` while keeping observed staleness well inside the
# one poll cycle the wait loop already tolerates.
HOST_INVENTORY_COALESCE_SECONDS = HOST_REGISTRATION_INTERVAL_SECONDS / 2


class OmnigentHostInventoryReader:
    """Single-flight, briefly cached whole-inventory reads shared by waiters.

    Two independent mechanisms collapse the request count, and both are needed:

    * readers that arrive while a request is in flight adopt that request's
      result, which is what makes a burst of ``N`` waiters cost one listing; and
    * a completed answer is reused for ``ttl_seconds``, which covers waiters
      that arrive just after one finishes rather than during it.

    Neither weakens correlation: the same rows are compared, and an answer is
    never served past its TTL. Cancelling one waiter never cancels the shared
    request, so the remaining waiters still get their answer.
    """

    def __init__(self, *, ttl_seconds: float = HOST_INVENTORY_COALESCE_SECONDS) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._cached: list[dict[str, Any]] | None = None
        self._cached_at: float | None = None
        self._inflight: asyncio.Future[list[dict[str, Any]]] | None = None

    async def list_hosts(
        self, client: Any, *, now: float | None = None
    ) -> list[dict[str, Any]]:
        if self._is_fresh(self._now(now)):
            return list(self._cached or [])
        inflight = self._inflight
        if inflight is not None:
            # Shielded so one cancelled waiter cannot cancel the shared request.
            return list(await asyncio.shield(inflight))
        return list(await self._fetch(client, now=now))

    async def _fetch(
        self, client: Any, *, now: float | None
    ) -> list[dict[str, Any]]:
        future: asyncio.Future[list[dict[str, Any]]] = (
            asyncio.get_running_loop().create_future()
        )
        # An unobserved exception on a future nobody awaited is only noise.
        future.add_done_callback(lambda done: done.cancelled() or done.exception())
        self._inflight = future
        try:
            hosts = await client.list_hosts()
        except BaseException as exc:
            self._inflight = None
            if not future.done():
                future.set_exception(exc)
            raise
        rows = [item for item in (hosts or []) if isinstance(item, dict)]
        self._inflight = None
        self._cached = rows
        self._cached_at = self._now(now)
        if not future.done():
            future.set_result(rows)
        return rows

    @staticmethod
    def _now(now: float | None) -> float:
        return now if now is not None else asyncio.get_running_loop().time()

    def _is_fresh(self, now: float) -> bool:
        if self._cached is None or self._cached_at is None:
            return False
        return (now - self._cached_at) < self._ttl


#: Process-wide reader. Concurrent executions build their own services, so the
#: coalescing point has to outlive any one execution's service graph.
_SHARED_INVENTORY_READER = OmnigentHostInventoryReader()


def _harness_readiness(host: dict[str, Any], harness_id: str) -> Any:
    readiness = host.get("configured_harnesses") or host.get("harnesses") or {}
    if isinstance(readiness, dict):
        return readiness.get(harness_id)
    return harness_id in {str(item) for item in readiness} if isinstance(
        readiness, list
    ) else False


def _harness_ready(
    host: dict[str, Any],
    harness_id: str,
    *,
    credentialless: bool,
) -> bool:
    value = _harness_readiness(host, harness_id)
    if value is True or str(value).lower() in {
            "ready",
            "available",
            "authenticated",
            "true",
    }:
        return True
    # A credentialless provider route deliberately has no host auth material.
    # Stock Omnigent therefore reports the installed OpenCode harness as
    # ``needs-auth`` even though the selected provider/model is usable. Exact
    # host attestation immediately follows registration and proves the selected
    # model through that host, so admit this structural readiness only when the
    # immutable plan selected the credentialless materializer.
    return credentialless and str(value).lower() == "needs-auth"


class OmnigentHostRegistrationService:
    def __init__(
        self,
        *,
        client: Any,
        expected_owner: str,
        attempts: int = HOST_REGISTRATION_ATTEMPTS,
        interval_seconds: float = HOST_REGISTRATION_INTERVAL_SECONDS,
        inventory_reader: OmnigentHostInventoryReader | None = None,
    ) -> None:
        self._client = client
        self._owner = expected_owner.strip()
        self._attempts = attempts
        self._interval = interval_seconds
        self._inventory = inventory_reader or _SHARED_INVENTORY_READER
        if not self._owner:
            raise HarnessPlatformError(
                "generic host registration requires an expected Omnigent owner",
                code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
            )

    async def wait_for_registration(
        self,
        *,
        correlation_name: str,
        harness_id: str,
        credentialless: bool = False,
    ) -> dict[str, Any]:
        for attempt in range(self._attempts):
            observed_at = datetime.now(UTC)
            hosts = await self._inventory.list_hosts(self._client)
            matches = [
                item
                for item in hosts
                if str(item.get("name") or "") == correlation_name
                and str(item.get("host_id") or item.get("id") or "").strip()
                and str(item.get("owner") or "") == self._owner
                and str(item.get("status") or "").lower() == "online"
            ]
            if len(matches) == 1 and _harness_ready(
                matches[0],
                harness_id,
                credentialless=credentialless,
            ):
                readiness = _harness_readiness(matches[0], harness_id)
                return {
                    "host": matches[0],
                    "omnigentHostId": str(
                        matches[0].get("host_id") or matches[0].get("id")
                    ),
                    "observedAt": observed_at.isoformat(),
                    "harnessReady": True,
                    "observedHarnessReadiness": readiness,
                    "credentialless": credentialless,
                }
            if len(matches) > 1:
                raise HarnessPlatformError(
                    "launched host correlation is ambiguous",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_REGISTRATION_TIMEOUT,
                )
            if attempt + 1 < self._attempts:
                await asyncio.sleep(self._interval)
        raise HarnessPlatformError(
            "exact launched Omnigent host did not register ready before timeout",
            code=HarnessPlatformFailure.OMNIGENT_HOST_REGISTRATION_TIMEOUT,
        )


__all__ = [
    "HOST_INVENTORY_COALESCE_SECONDS",
    "HOST_REGISTRATION_ATTEMPTS",
    "HOST_REGISTRATION_INTERVAL_SECONDS",
    "OmnigentHostInventoryReader",
    "OmnigentHostRegistrationService",
]
