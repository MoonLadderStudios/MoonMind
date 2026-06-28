"""Thin Omnigent HTTP/SSE transport client."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx


class OmnigentClientError(RuntimeError):
    """Structured transport error from Omnigent."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class OmnigentHttpClient:
    """HTTP client for the Omnigent server API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str | None = None,
        request_timeout_seconds: int = 30,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        headers = {}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(request_timeout_seconds, read=None),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_agents(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/api/agents")
        payload = response.json()
        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict) and isinstance(payload.get("agents"), list):
            return [dict(item) for item in payload["agents"] if isinstance(item, dict)]
        return []

    async def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._request("POST", "/v1/sessions", json=payload)
        return _ensure_mapping(response.json())

    async def get_session(self, session_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/v1/sessions/{session_id}")
        return _ensure_mapping(response.json())

    async def post_event(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(
            "POST", f"/v1/sessions/{session_id}/events", json=payload
        )
        if response.content:
            return _ensure_mapping(response.json())
        return {}

    async def stream_events(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        async with self._client.stream(
            "GET", f"/v1/sessions/{session_id}/stream"
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                raise OmnigentClientError(
                    f"Omnigent stream failed with HTTP {response.status_code}",
                    status_code=response.status_code,
                    response_body=body,
                )
            async for line in response.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                try:
                    payload = httpx.Response(200, content=line).json()
                except ValueError as exc:
                    raise OmnigentClientError("Malformed Omnigent SSE frame") from exc
                if isinstance(payload, dict):
                    yield payload

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise OmnigentClientError(str(exc)) from exc
        if response.status_code >= 400:
            raise OmnigentClientError(
                f"Omnigent request failed with HTTP {response.status_code}",
                status_code=response.status_code,
                response_body=response.text,
            )
        return response


def _ensure_mapping(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return dict(payload)
    raise OmnigentClientError("Omnigent response was not a JSON object")


__all__ = ["OmnigentClientError", "OmnigentHttpClient"]
