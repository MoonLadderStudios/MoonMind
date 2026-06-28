"""HTTP/SSE client for Omnigent v1 session execution."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


class OmnigentClientError(RuntimeError):
    """Raised when the Omnigent gateway returns an adapter-visible error."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _scrub_error_message(text: str) -> str:
    lowered = text.lower()
    if "bearer" in lowered or "authorization" in lowered or "cookie" in lowered:
        return "Omnigent request failed (authorization error)"
    return text


class OmnigentHttpClient:
    """Thin async client for Omnigent transport endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None,
        request_timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = str(base_url).rstrip("/")
        self._token = str(token or "").strip()
        self._timeout = httpx.Timeout(request_timeout_seconds, read=None)
        self._client = client

    def _headers(self, *, stream: bool = False) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        try:
            if self._client is not None:
                response = await self._client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_payload,
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=self._headers(),
                        json=json_payload,
                    )
        except httpx.HTTPError as exc:
            raise OmnigentClientError(
                _scrub_error_message(f"Omnigent transport error: {exc!s}")
            ) from exc

        if response.status_code >= 400:
            body = response.text[:2048]
            raise OmnigentClientError(
                _scrub_error_message(f"Omnigent HTTP {response.status_code}: {body}"),
                status_code=response.status_code,
            )
        if not response.content:
            return {}
        try:
            value = response.json()
        except json.JSONDecodeError as exc:
            raise OmnigentClientError("Omnigent returned non-JSON response") from exc
        return value if isinstance(value, dict) else {"items": value}

    async def list_agents(self) -> dict[str, Any]:
        return await self._request_json("GET", "/api/agents")

    async def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request_json("POST", "/v1/sessions", json_payload=payload)

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return await self._request_json("GET", f"/v1/sessions/{session_id}")

    async def post_event(
        self,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            f"/v1/sessions/{session_id}/events",
            json_payload=payload,
        )

    async def stream_events(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        url = f"{self._base}/v1/sessions/{session_id}/stream"
        try:
            if self._client is not None:
                async with self._client.stream(
                    "GET",
                    url,
                    headers=self._headers(stream=True),
                ) as response:
                    async for event in self._iter_stream_response(response):
                        yield event
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    async with client.stream(
                        "GET",
                        url,
                        headers=self._headers(stream=True),
                    ) as response:
                        async for event in self._iter_stream_response(response):
                            yield event
        except httpx.HTTPError as exc:
            raise OmnigentClientError(
                _scrub_error_message(f"Omnigent stream transport error: {exc!s}")
            ) from exc

    async def _iter_stream_response(
        self,
        response: httpx.Response,
    ) -> AsyncIterator[dict[str, Any]]:
        if response.status_code >= 400:
            body = (await response.aread()).decode("utf-8", errors="replace")[:2048]
            raise OmnigentClientError(
                _scrub_error_message(f"Omnigent HTTP {response.status_code}: {body}"),
                status_code=response.status_code,
            )
        async for line in response.aiter_lines():
            event = parse_sse_line(line)
            if event is not None:
                yield event


def parse_sse_line(line: str) -> dict[str, Any] | None:
    """Parse one SSE data line for unit tests and the streaming client."""

    if not line or line.startswith(":") or not line.startswith("data:"):
        return None
    data_str = line[5:].strip()
    if data_str == "[DONE]":
        return {"type": "stream.done"}
    try:
        value = json.loads(data_str)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else {"data": value}


__all__ = [
    "OmnigentClientError",
    "OmnigentHttpClient",
    "parse_sse_line",
]
