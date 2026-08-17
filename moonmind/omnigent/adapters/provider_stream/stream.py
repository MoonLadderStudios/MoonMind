"""Streaming provider adapter.

``IterableProviderStream`` adapts any async iterable of raw provider event
payloads to the ``ProviderStream`` port, applying ``after`` cursor filtering.
Real transports (SSE/WebSocket) plug their async iterator in here; the domain
never sees the transport.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping


class IterableProviderStream:
    def __init__(
        self, source: Mapping[str, "AsyncIterator[Mapping[str, Any]]"]
    ) -> None:
        # Map of upstream path -> async iterator of raw provider payloads.
        self._source = source

    async def _iter(
        self, path: str, after: int
    ) -> AsyncIterator[Mapping[str, Any]]:
        iterator = self._source.get(path)
        if iterator is None:
            return
        index = 0
        async for payload in iterator:
            index += 1
            if index > after:
                yield payload

    def events(
        self, path: str, *, after: int = 0
    ) -> AsyncIterator[Mapping[str, Any]]:
        return self._iter(path, after)


__all__ = ["IterableProviderStream"]
