"""Exact launched-host correlation against Omnigent inventory."""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

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
_HOST_REGISTRATION_BASE_DELAY_SECONDS = 0.5
_HOST_REGISTRATION_MAX_DELAY_SECONDS = 2.0
# MoonLadderStudios/MoonMind#3878: every waiter reads the same whole-inventory
# listing, so at concurrency ``N`` the endpoint sees ``N`` identical requests
# per poll. Coalescing concurrent readers behind one in-flight request makes the
# listing cost independent of ``N``.
#
# The default reuse window is zero. Reusing a completed answer would also serve
# it back to the *same* wait loop on its next poll, and a poll loop that can be
# answered from its own previous poll makes progress a function of wall-clock
# time rather than of what the endpoint now reports: shorten the poll interval,
# lengthen the window, or run the loop under a harness that does not advance the
# clock, and registration stalls until the attempt budget is spent. Callers that
# genuinely want a reuse window pass ``ttl_seconds`` explicitly.
HOST_INVENTORY_COALESCE_SECONDS = 0.0


class OmnigentHostInventoryReader:
    """Single-flight whole-inventory reads shared by concurrent waiters.

    Readers that arrive while a request is in flight adopt that request's
    result, which is what makes a burst of ``N`` waiters cost one listing.

    An optional ``ttl_seconds`` additionally reuses a completed answer for
    readers that arrive just after one finishes. It defaults to zero because a
    registration wait loop is the only caller and must observe fresh state on
    every poll; see ``HOST_INVENTORY_COALESCE_SECONDS``.

    Neither mechanism weakens correlation: the same rows are compared, and an
    answer is never served past its TTL. Cancelling one waiter never cancels the
    shared request, so the remaining waiters still get their answer.
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
        backend: Any | None = None,
    ) -> None:
        self._client = client
        self._owner = expected_owner.strip()
        self._attempts = attempts
        self._interval = interval_seconds
        self._inventory = inventory_reader or _SHARED_INVENTORY_READER
        self._backend = backend
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
        expected_host_id: str | None = None,
    ) -> dict[str, Any]:
        """Wait for the exact launched host to register ready.

        When ``expected_host_id`` is present (new launches), poll the exact
        host identity with bounded exponential backoff and jitter, verifying
        ID, name, owner, status, and harness readiness. A host found only
        under another ID is never accepted by name alone. When absent (old
        histories), fall back to the legacy list-and-filter path so replay
        remains compatible; the fallback is observable via ``lookupMode``.
        """
        if expected_host_id:
            return await self._wait_for_targeted_registration(
                correlation_name=correlation_name,
                harness_id=harness_id,
                credentialless=credentialless,
                expected_host_id=expected_host_id,
            )
        return await self._wait_for_compat_registration(
            correlation_name=correlation_name,
            harness_id=harness_id,
            credentialless=credentialless,
        )

    def _registration_delay(self, attempt: int) -> float:
        base = _HOST_REGISTRATION_BASE_DELAY_SECONDS * (2.0**min(attempt, 3))
        capped = min(base, _HOST_REGISTRATION_MAX_DELAY_SECONDS)
        jitter = random.uniform(-0.2 * capped, 0.2 * capped)
        return max(0.1, capped + jitter)

    async def _check_host_process(self, correlation_name: str) -> None:
        """A dead process cannot register; do not spend the readiness budget on it."""
        if self._backend is None:
            return
        code, out, _err = await self._backend.run(
            [
                "docker", "container", "inspect", "--format",
                "{{.State.Status}}|{{.State.ExitCode}}", correlation_name,
            ],
            check=False,
            timeout_seconds=10,
        )
        if code != 0:
            raise HarnessPlatformError(
                "launched host process inspection unavailable",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        status, _, exit_code = out.strip().partition("|")
        if status in {"exited", "dead", "removing"}:
            safe_exit = (
                str(int(exit_code)) if exit_code.lstrip("-").isdigit() else "unknown"
            )
            raise HarnessPlatformError(
                f"launched host exited before registration (exitCode={safe_exit}); "
                "inspect host logs",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if status not in {"created", "running", "restarting"}:
            raise HarnessPlatformError(
                "launched host process is not runnable",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )

    async def _wait_for_targeted_registration(
        self,
        *,
        correlation_name: str,
        harness_id: str,
        credentialless: bool,
        expected_host_id: str,
    ) -> dict[str, Any]:
        from moonmind.workflows.adapters.omnigent_client import OmnigentClientError

        for attempt in range(self._attempts):
            await self._check_host_process(correlation_name)
            try:
                host = await self._client.get_host(expected_host_id)
            except OmnigentClientError as exc:
                if exc.status_code == 404:
                    host = None
                else:
                    raise HarnessPlatformError(
                        "targeted host lookup failed before registration",
                        code=HarnessPlatformFailure.OMNIGENT_HOST_REGISTRATION_TIMEOUT,
                    ) from exc
            if isinstance(host, dict) and host:
                verified = self._verify_targeted_host(
                    host,
                    expected_host_id=expected_host_id,
                    correlation_name=correlation_name,
                    harness_id=harness_id,
                    credentialless=credentialless,
                )
                if verified is not None:
                    return verified
            if attempt + 1 < self._attempts:
                await asyncio.sleep(self._registration_delay(attempt))
        raise HarnessPlatformError(
            "exact launched Omnigent host did not register ready before timeout",
            code=HarnessPlatformFailure.OMNIGENT_HOST_REGISTRATION_TIMEOUT,
        )

    def _verify_targeted_host(
        self,
        host: dict[str, Any],
        *,
        expected_host_id: str,
        correlation_name: str,
        harness_id: str,
        credentialless: bool,
    ) -> dict[str, Any] | None:
        observed_at = datetime.now(UTC)
        host_id = str(host.get("host_id") or host.get("id") or "").strip()
        identity_matches = host_id == expected_host_id
        if not identity_matches:
            # Persisted launch specs may carry the dashed UUID emitted before
            # canonical host-ID generation. Omnigent registers that same UUID
            # as bare hex. Compare UUID values without accepting another host
            # or changing the persisted spec; retain the server ID as evidence.
            try:
                identity_matches = UUID(host_id) == UUID(expected_host_id)
            except ValueError:
                pass
        if not identity_matches:
            raise HarnessPlatformError(
                "launched host identity mismatch",
                code=HarnessPlatformFailure.OMNIGENT_HOST_REGISTRATION_TIMEOUT,
            )
        if str(host.get("name") or "") != correlation_name:
            raise HarnessPlatformError(
                "launched host name mismatch",
                code=HarnessPlatformFailure.OMNIGENT_HOST_REGISTRATION_TIMEOUT,
            )
        if str(host.get("owner") or "") != self._owner:
            raise HarnessPlatformError(
                "launched host owner mismatch",
                code=HarnessPlatformFailure.OMNIGENT_HOST_REGISTRATION_TIMEOUT,
            )
        if str(host.get("status") or "").lower() != "online":
            return None
        if not _harness_ready(host, harness_id, credentialless=credentialless):
            return None
        readiness = _harness_readiness(host, harness_id)
        return {
            "host": host,
            "omnigentHostId": host_id,
            "observedAt": observed_at.isoformat(),
            "harnessReady": True,
            "observedHarnessReadiness": readiness,
            "credentialless": credentialless,
            "lookupMode": "targeted",
        }

    async def _wait_for_compat_registration(
        self,
        *,
        correlation_name: str,
        harness_id: str,
        credentialless: bool,
    ) -> dict[str, Any]:
        for attempt in range(self._attempts):
            await self._check_host_process(correlation_name)
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
                    "lookupMode": "compat",
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
