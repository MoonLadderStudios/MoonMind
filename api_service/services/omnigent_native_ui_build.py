"""Verification of the actual deployed Omnigent server and compiled UI."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from moonmind.omnigent.native_ui import (
    NativeUiCompatibility,
    evaluate_deployed_native_ui_manifest,
)
from moonmind.omnigent.settings import (
    resolved_api_token,
    resolved_native_ui_version,
    resolved_server_url,
)

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30.0
_cache_lock = asyncio.Lock()
_cached_verification: tuple[
    tuple[bool, str, str], float, NativeUiCompatibility
] | None = None


async def verify_deployed_native_ui(*, enabled: bool) -> NativeUiCompatibility:
    """Read and verify the upstream's objective build manifest."""

    global _cached_verification

    base = resolved_server_url().rstrip("/")
    expected = resolved_native_ui_version()
    cache_key = (enabled, base, expected)
    now = time.monotonic()
    if (
        _cached_verification is not None
        and _cached_verification[0] == cache_key
        and _cached_verification[1] > now
    ):
        return _cached_verification[2]

    async with _cache_lock:
        now = time.monotonic()
        if (
            _cached_verification is not None
            and _cached_verification[0] == cache_key
            and _cached_verification[1] > now
        ):
            return _cached_verification[2]
        result = await _fetch_and_verify(
            enabled=enabled,
            base=base,
            expected=expected,
        )
        _cached_verification = (cache_key, now + _CACHE_TTL_SECONDS, result)
        return result


async def _fetch_and_verify(
    *, enabled: bool, base: str, expected: str
) -> NativeUiCompatibility:
    manifest: dict[str, Any] | None = None
    if enabled and base:
        headers = {"Accept": "application/json"}
        token = resolved_api_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{base}/api/hosted-build-manifest", headers=headers
                )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    manifest = payload
        except (httpx.HTTPError, ValueError):
            logger.info("Omnigent deployed native UI manifest is unavailable")
    result = evaluate_deployed_native_ui_manifest(
        manifest,
        expected_version=expected,
        enabled=enabled,
    )
    if enabled and not result.ready:
        logger.info(
            "Omnigent deployed native UI is incompatible: reason=%s",
            result.reason,
        )
    return result
