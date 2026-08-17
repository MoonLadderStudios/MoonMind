"""Request/response provider adapters.

``HttpxProviderClient`` is the production adapter; ``InMemoryProviderClient`` is
a deterministic double for tests. Both translate transport concerns into the
canonical :class:`ProviderResponse`; provider-native status strings are handled
by the domain compatibility helpers, not leaked upward.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import httpx

from moonmind.omnigent.ports.provider import ProviderResponse


class HttpxProviderClient:
    """``ProviderClient`` backed by an ``httpx.AsyncClient``."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ProviderResponse:
        response = await self._client.request(
            method, path, json=json, headers=dict(headers or {})
        )
        try:
            body = response.json()
        except ValueError:
            body = {}
        return ProviderResponse(
            status_code=response.status_code,
            body=body if isinstance(body, dict) else {"value": body},
        )


class InMemoryProviderClient:
    """Deterministic ``ProviderClient`` double driven by a handler function."""

    def __init__(
        self,
        handler: Callable[[str, str, Mapping[str, Any] | None], ProviderResponse],
    ) -> None:
        self._handler = handler
        self.calls: list[tuple[str, str, Mapping[str, Any] | None]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ProviderResponse:
        self.calls.append((method, path, json))
        return self._handler(method, path, json)


__all__ = ["HttpxProviderClient", "InMemoryProviderClient"]
