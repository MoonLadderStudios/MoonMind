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
    ) -> None:
        self._client = client
        self._owner = expected_owner.strip()
        self._attempts = attempts
        self._interval = interval_seconds
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
            hosts = await self._client.list_hosts()
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
    "HOST_REGISTRATION_ATTEMPTS",
    "HOST_REGISTRATION_INTERVAL_SECONDS",
    "OmnigentHostRegistrationService",
]
