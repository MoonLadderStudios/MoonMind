"""Thin HTTP/SSE client for Omnigent confirmed API operations."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import quote

import httpx

from moonmind.utils.logging import (
    SecretRedactor,
    redact_sensitive_payload,
    redact_sensitive_text,
)


class OmnigentClientError(RuntimeError):
    """Structured client error for non-2xx Omnigent responses or transport failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: Any | None = None,
        failure_class: str = "integration_error",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.failure_class = failure_class

    def diagnostics(self) -> dict[str, Any]:
        return {
            "statusCode": self.status_code,
            "failureClass": self.failure_class,
            "message": redact_sensitive_text(str(self)),
            "responseBody": redact_sensitive_payload(self.response_body),
        }


class OmnigentHttpClient:
    """Async client for Omnigent HTTP/SSE transport.

    The client is intentionally transport-only; Temporal workflow/activity
    concerns live at the adapter boundary.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str = "",
        timeout_seconds: float = 60.0,
        stream_timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = str(base_url).rstrip("/")
        self._api_token = api_token
        self._timeout = httpx.Timeout(timeout_seconds)
        self._stream_timeout = httpx.Timeout(
            timeout_seconds,
            read=stream_timeout_seconds,
        )
        self._transport = transport
        self._redactor = SecretRedactor(
            secrets=[api_token],
            placeholder="[REDACTED]",
        )

    def _headers(self, *, accept: str = "application/json") -> dict[str, str]:
        headers = {"Accept": accept}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    async def list_agents(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/agents")
        if isinstance(data, list):
            return [dict(item) for item in data if isinstance(item, Mapping)]
        if isinstance(data, Mapping) and isinstance(data.get("agents"), list):
            return [dict(item) for item in data["agents"] if isinstance(item, Mapping)]
        return []

    async def get_agent(self, agent_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/agents/{quote(agent_id, safe='')}")

    async def create_agent_bundle(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        files = {"bundle": (filename, content, content_type)}
        return await self._request("POST", "/api/agents", files=files)

    async def create_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/v1/sessions", json=dict(payload))

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/sessions/{quote(session_id, safe='')}")

    async def post_event(
        self,
        session_id: str,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/sessions/{quote(session_id, safe='')}/events",
            json=dict(event),
        )

    async def stream_events(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        path = f"/v1/sessions/{quote(session_id, safe='')}/stream"
        async with httpx.AsyncClient(
            timeout=self._stream_timeout,
            transport=self._transport,
        ) as client:
            try:
                async with client.stream(
                    "GET",
                    f"{self._base}{path}",
                    headers=self._headers(accept="text/event-stream"),
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        body = (await response.aread()).decode(
                            "utf-8",
                            errors="replace",
                        )
                        raise self._error_from_response(response.status_code, body)
                    async for line in response.aiter_lines():
                        event = parse_sse_line(line)
                        if event is not None:
                            yield event
            except httpx.HTTPError as exc:
                raise OmnigentClientError(
                    self._redact(f"Omnigent transport error: {exc!s}"),
                    failure_class="integration_error",
                ) from exc

    async def resolve_elicitation(
        self,
        session_id: str,
        elicitation_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/sessions/"
            f"{quote(session_id, safe='')}/elicitations/"
            f"{quote(elicitation_id, safe='')}/resolve",
            json=dict(payload),
        )

    async def list_changed_files(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/sessions/{quote(session_id, safe='')}"
            "/resources/environments/default/changes",
        )

    async def get_workspace_file(self, session_id: str, path: str) -> bytes:
        return await self._request_bytes(
            "GET",
            f"/v1/sessions/{quote(session_id, safe='')}"
            "/resources/environments/default/filesystem/"
            f"{quote(path, safe='')}",
        )

    async def list_session_files(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/sessions/{quote(session_id, safe='')}/resources/files",
        )

    async def get_session_file_content(self, session_id: str, file_id: str) -> bytes:
        return await self._request_bytes(
            "GET",
            f"/v1/sessions/{quote(session_id, safe='')}/resources/files/"
            f"{quote(file_id, safe='')}/content",
        )

    async def interrupt(self, session_id: str) -> dict[str, Any]:
        return await self.post_event(session_id, {"type": "interrupt"})

    async def stop_session(self, session_id: str) -> dict[str, Any]:
        return await self.post_event(session_id, {"type": "stop_session"})

    async def delete_session(
        self,
        session_id: str,
        *,
        delete_branch: bool = False,
    ) -> dict[str, Any]:
        query = "?delete_branch=true" if delete_branch else "?delete_branch=false"
        return await self._request(
            "DELETE",
            f"/v1/sessions/{quote(session_id, safe='')}{query}",
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            try:
                response = await client.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers(),
                    **kwargs,
                )
            except httpx.HTTPError as exc:
                raise OmnigentClientError(
                    self._redact(f"Omnigent transport error: {exc!s}"),
                    failure_class="integration_error",
                ) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise self._error_from_response(response.status_code, response.text)
        if not response.content:
            return {}
        try:
            parsed = response.json()
        except json.JSONDecodeError:
            return {"body": self._redact(response.text)}
        if isinstance(parsed, Mapping):
            return dict(redact_sensitive_payload(parsed))
        return {"body": redact_sensitive_payload(parsed)}

    async def _request_bytes(self, method: str, path: str) -> bytes:
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            try:
                response = await client.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers(),
                )
            except httpx.HTTPError as exc:
                raise OmnigentClientError(
                    self._redact(f"Omnigent transport error: {exc!s}"),
                    failure_class="integration_error",
                ) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise self._error_from_response(response.status_code, response.text)
        return response.content

    def _error_from_response(self, status_code: int, body: str) -> OmnigentClientError:
        response_body: Any
        try:
            response_body = json.loads(body)
        except json.JSONDecodeError:
            response_body = body[:4096]
        response_body = _scrub_payload_with_redactor(
            redact_sensitive_payload(response_body),
            redactor=self._redactor,
        )
        return OmnigentClientError(
            self._redact(f"Omnigent HTTP {status_code}"),
            status_code=status_code,
            response_body=response_body,
            failure_class="integration_error",
        )

    def _redact(self, value: str) -> str:
        return redact_sensitive_text(self._redactor.scrub(value))


def parse_sse_line(line: str) -> dict[str, Any] | None:
    """Parse one Omnigent SSE data line for tests and stream consumption."""

    if not line or line.startswith(":") or not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise OmnigentClientError(
            "Malformed Omnigent SSE frame",
            failure_class="integration_error",
        ) from exc
    if not isinstance(parsed, Mapping):
        raise OmnigentClientError(
            "Malformed Omnigent SSE frame",
            failure_class="integration_error",
        )
    return dict(redact_sensitive_payload(parsed))


def _scrub_payload_with_redactor(payload: Any, *, redactor: SecretRedactor) -> Any:
    if isinstance(payload, str):
        return redactor.scrub(payload)
    if isinstance(payload, Mapping):
        return {
            str(key): _scrub_payload_with_redactor(value, redactor=redactor)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [
            _scrub_payload_with_redactor(item, redactor=redactor)
            for item in payload
        ]
    return payload


__all__ = [
    "OmnigentClientError",
    "OmnigentHttpClient",
    "parse_sse_line",
]
