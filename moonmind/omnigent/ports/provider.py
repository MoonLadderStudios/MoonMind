"""Provider ports: HTTP request/response and event stream boundaries.

Provider-native vocabulary lives behind these ports. Adapters translate provider
payloads into canonical domain observations; the application layer never sees a
raw provider status string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    status_code: int
    body: Mapping[str, Any]


@runtime_checkable
class ProviderClient(Protocol):
    """Request/response provider boundary (session create, message delivery)."""

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ProviderResponse: ...


@runtime_checkable
class ProviderStream(Protocol):
    """Streaming provider boundary yielding raw provider event payloads."""

    def events(
        self, path: str, *, after: int = 0
    ) -> AsyncIterator[Mapping[str, Any]]: ...


__all__ = ["ProviderClient", "ProviderResponse", "ProviderStream"]
