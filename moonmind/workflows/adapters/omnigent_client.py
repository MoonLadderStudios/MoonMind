"""Thin HTTP/SSE client for Omnigent confirmed API operations."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any
from urllib.parse import quote

import httpx

from moonmind.omnigent.bridge_security import sanitize_proxy_headers
from moonmind.omnigent.failure_classification import classify_omnigent_http_status
from moonmind.utils.logging import (
    SecretRedactor,
    redact_sensitive_payload,
    redact_sensitive_text,
)

_AGENT_CATALOG_PAGE_SIZE = 1000
_MAX_AGENT_CATALOG_PAGES = 100
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 60.0
# A message event can synchronously wait for the runner's native-terminal
# bootstrap. The runner may spend 30 seconds resolving the harness version and
# another 30 seconds waiting for its loopback server; when a connect-time
# bootstrap loses that race, the serialized event ensure can repeat the same
# bounded work. Keep enough margin for both attempts so the provider returns
# its authoritative success or structured startup failure before MoonMind
# releases the on-demand host.
_DEFAULT_EVENT_TIMEOUT_SECONDS = 150.0
_MAX_SSE_LINE_BYTES = 1_000_000


def _bounded_int(env_key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(env_key) or "").strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def default_omnigent_pool_limits() -> httpx.Limits:
    """Return the bounded lifecycle-managed pool configuration.

    Defaults safely exceed maximum active Omnigent execution and control
    concurrency with headroom for registration, cleanup, readiness, catalog
    refresh, validation, and operator control traffic. All values are
    deployment-overridable within safe bounds via environment.
    """
    return httpx.Limits(
        max_connections=_bounded_int(
            "MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS", 100, 10, 500
        ),
        max_keepalive_connections=_bounded_int(
            "MOONMIND_OMNIGENT_HTTP_MAX_KEEPALIVE", 20, 1, 100
        ),
        keepalive_expiry=_bounded_int(
            "MOONMIND_OMNIGENT_HTTP_KEEPALIVE_EXPIRY_SECONDS", 30, 5, 300
        ),
    )


_SHARED_POOL_CLIENT: httpx.AsyncClient | None = None


def shared_pool_client() -> httpx.AsyncClient | None:
    """Return the worker-owned shared pool when initialized, else None."""
    return _SHARED_POOL_CLIENT


async def init_shared_pool_client(
    *,
    timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Create and remember one lifecycle-managed pool per worker process."""
    global _SHARED_POOL_CLIENT
    if _SHARED_POOL_CLIENT is not None:
        return _SHARED_POOL_CLIENT
    _SHARED_POOL_CLIENT = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        limits=default_omnigent_pool_limits(),
        transport=transport,
    )
    return _SHARED_POOL_CLIENT


async def aclose_shared_pool_client() -> None:
    """Close the worker-owned shared pool; safe to call when absent."""
    global _SHARED_POOL_CLIENT
    client, _SHARED_POOL_CLIENT = _SHARED_POOL_CLIENT, None
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            pass


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
        timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT_SECONDS,
        event_timeout_seconds: float = _DEFAULT_EVENT_TIMEOUT_SECONDS,
        stream_timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        limits: httpx.Limits | None = None,
        forward_headers: Mapping[str, Any] | None = None,
        upstream_header_allowlist: Iterable[str] | None = None,
    ) -> None:
        self._base = str(base_url).rstrip("/")
        self._api_token = api_token
        self._timeout = httpx.Timeout(timeout_seconds)
        self._event_timeout = httpx.Timeout(event_timeout_seconds)
        self._stream_timeout = httpx.Timeout(
            timeout_seconds,
            read=stream_timeout_seconds,
        )
        self._transport = transport
        self._client = client
        self._limits = limits or default_omnigent_pool_limits()
        # Proxy mode never leaks MoonMind internal auth headers upstream unless
        # the operator explicitly allowlists them (OmnigentBridge.md §16 rule 7).
        self._forward_headers = sanitize_proxy_headers(
            forward_headers or {},
            allowed_upstream_headers=upstream_header_allowlist or (),
        )
        self._redactor = SecretRedactor(
            secrets=[api_token],
            placeholder="[REDACTED]",
        )

    def _headers(self, *, accept: str = "application/json") -> dict[str, str]:
        # Forwarded (already sanitized) headers first, then MoonMind-owned
        # values so the upstream Omnigent credential always wins.
        headers = dict(self._forward_headers)
        headers["Accept"] = accept
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    async def list_agents(self) -> list[dict[str, Any]]:
        """Return the current stock Omnigent built-in-agent catalog.

        ``GET /v1/agents`` is a paginated, read-only list of agents that may be
        bound by ``POST /v1/sessions``.  MoonMind's profile projection models
        that bindability as the portable ``session.start`` capability, which
        the stock response does not repeat on every agent object.
        """

        agents: list[dict[str, Any]] = []
        after: str | None = None
        seen_cursors: set[str] = set()
        for _page_number in range(_MAX_AGENT_CATALOG_PAGES):
            path = f"/v1/agents?limit={_AGENT_CATALOG_PAGE_SIZE}"
            if after:
                path += f"&after={quote(after, safe='')}"
            data = await self._request("GET", path)
            if not isinstance(data, Mapping) or not isinstance(data.get("data"), list):
                if not agents:
                    return []
                raise OmnigentClientError(
                    "Omnigent agent catalog pagination returned an unsupported "
                    "response shape",
                    response_body=data,
                    failure_class="integration_error",
                )

            for item in data["data"]:
                if not isinstance(item, Mapping):
                    continue
                agent = dict(item)
                if "capabilities" not in agent:
                    agent["capabilities"] = ["session.start"]
                agents.append(agent)

            if data.get("has_more") is not True:
                return agents
            next_cursor = str(data.get("last_id") or "").strip()
            if not next_cursor or next_cursor in seen_cursors:
                raise OmnigentClientError(
                    "Omnigent agent catalog pagination did not advance its cursor",
                    response_body=data,
                    failure_class="integration_error",
                )
            seen_cursors.add(next_cursor)
            after = next_cursor

        raise OmnigentClientError(
            "Omnigent agent catalog exceeded the bounded pagination limit",
            failure_class="integration_error",
        )

    async def get_agent(self, agent_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/agents/{quote(agent_id, safe='')}")

    async def list_harnesses(self) -> list[dict[str, Any]]:
        """List harnesses from Omnigent catalog (generic, harness-neutral)."""
        data = await self._request("GET", "/v1/harnesses")
        if isinstance(data, list):
            return [dict(item) for item in data if isinstance(item, Mapping)]
        if isinstance(data, Mapping) and isinstance(data.get("harnesses"), list):
            return [
                dict(item) for item in data["harnesses"] if isinstance(item, Mapping)
            ]
        if isinstance(data, Mapping) and isinstance(data.get("data"), list):
            return [dict(item) for item in data["data"] if isinstance(item, Mapping)]
        raise OmnigentClientError(
            "Omnigent harness catalog has an unsupported response shape",
            response_body=data,
            failure_class="integration_error",
        )

    async def get_version(self) -> str:
        """Return the authenticated endpoint's installed package version."""

        data = await self._request("GET", "/api/version")
        version = str(data.get("version") or "").strip()
        if not version:
            raise OmnigentClientError(
                "Omnigent version endpoint did not return a version",
                response_body=data,
                failure_class="integration_error",
            )
        return version

    async def get_host(self, host_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/hosts/{quote(host_id, safe='')}")

    async def get_host_model_options(
        self, host_id: str, harness_id: str
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/hosts/{quote(host_id, safe='')}/harnesses/"
            f"{quote(harness_id, safe='')}/model-options",
        )

    async def detect_host_credentials(self, host_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"/v1/hosts/{quote(host_id, safe='')}/credentials/detect"
        )

    async def store_host_credential(
        self, host_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/hosts/{quote(host_id, safe='')}/credentials",
            json=dict(payload),
        )

    async def install_host_harness(
        self, host_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/hosts/{quote(host_id, safe='')}/harnesses/install",
            json=dict(payload),
        )

    async def list_hosts(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/v1/hosts")
        if isinstance(data, list):
            return [dict(item) for item in data if isinstance(item, Mapping)]
        if isinstance(data, Mapping) and isinstance(data.get("hosts"), list):
            return [dict(item) for item in data["hosts"] if isinstance(item, Mapping)]
        raise OmnigentClientError(
            "Omnigent host catalog has an unsupported response shape",
            response_body=data,
            failure_class="integration_error",
        )

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
        event_payload = dict(event)
        event_type = str(event_payload.get("type") or "").strip()
        return await self._request(
            "POST",
            f"/v1/sessions/{quote(session_id, safe='')}/events",
            json=event_payload,
            request_timeout=(
                self._event_timeout if event_type == "message" else self._timeout
            ),
        )

    async def stream_events(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        path = f"/v1/sessions/{quote(session_id, safe='')}/stream"
        if self._client is not None:
            try:
                async with self._client.stream(
                    "GET",
                    f"{self._base}{path}",
                    headers=self._headers(accept="text/event-stream"),
                    timeout=self._stream_timeout,
                ) as response:
                    async for event in self._iter_stream_response(response):
                        yield event
            except httpx.PoolTimeout as exc:
                raise OmnigentClientError(
                    self._redact(
                        "Omnigent transport pool exhausted; "
                        f"increase MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS: {exc}"
                    ),
                    failure_class="integration_error",
                ) from exc
            except httpx.HTTPError as exc:
                raise OmnigentClientError(
                    self._transport_error_message(exc),
                    failure_class="integration_error",
                ) from exc
            return

        shared = shared_pool_client() if self._transport is None else None
        if shared is not None:
            try:
                async with shared.stream(
                    "GET",
                    f"{self._base}{path}",
                    headers=self._headers(accept="text/event-stream"),
                    timeout=self._stream_timeout,
                ) as response:
                    async for event in self._iter_stream_response(response):
                        yield event
            except httpx.PoolTimeout as exc:
                raise OmnigentClientError(
                    self._redact(
                        "Omnigent transport pool exhausted; "
                        f"increase MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS: {exc}"
                    ),
                    failure_class="integration_error",
                ) from exc
            except httpx.HTTPError as exc:
                raise OmnigentClientError(
                    self._transport_error_message(exc),
                    failure_class="integration_error",
                ) from exc
            return

        async with httpx.AsyncClient(
            timeout=self._stream_timeout,
            limits=self._limits,
            transport=self._transport,
        ) as client:
            try:
                async with client.stream(
                    "GET",
                    f"{self._base}{path}",
                    headers=self._headers(accept="text/event-stream"),
                ) as response:
                    async for event in self._iter_stream_response(response):
                        yield event
            except httpx.PoolTimeout as exc:
                raise OmnigentClientError(
                    self._redact(
                        "Omnigent transport pool exhausted; "
                        f"increase MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS: {exc}"
                    ),
                    failure_class="integration_error",
                ) from exc
            except httpx.HTTPError as exc:
                raise OmnigentClientError(
                    self._transport_error_message(exc),
                    failure_class="integration_error",
                ) from exc

    async def _iter_stream_response(
        self,
        response: httpx.Response,
    ) -> AsyncIterator[dict[str, Any]]:
        if response.status_code < 200 or response.status_code >= 300:
            body = (await response.aread()).decode(
                "utf-8",
                errors="replace",
            )
            raise self._error_from_response(response.status_code, body)
        async for line in response.aiter_lines():
            if len(line) > _MAX_SSE_LINE_BYTES:
                raise OmnigentClientError(
                    "Omnigent SSE frame exceeds bounded line size",
                    failure_class="integration_error",
                )
            event = parse_sse_line(line)
            if event is not None:
                yield event

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

    async def list_workspace_files(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/sessions/{quote(session_id, safe='')}"
            "/resources/environments/default/filesystem",
        )

    async def get_workspace_file(self, session_id: str, path: str) -> bytes:
        return await self._request_bytes(
            "GET",
            f"/v1/sessions/{quote(session_id, safe='')}"
            "/resources/environments/default/filesystem/"
            f"{quote(path, safe='')}",
        )

    async def get_workspace_diff(self, session_id: str, path: str) -> bytes:
        return await self._request_bytes(
            "GET",
            f"/v1/sessions/{quote(session_id, safe='')}"
            "/resources/environments/default/diff/"
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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        request_timeout: httpx.Timeout | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        timeout = request_timeout or self._timeout
        if self._client is not None:
            try:
                response = await self._client.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers(),
                    timeout=timeout,
                    **kwargs,
                )
            except httpx.PoolTimeout as exc:
                raise OmnigentClientError(
                    self._redact(
                        "Omnigent transport pool exhausted; "
                        f"increase MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS: {exc}"
                    ),
                    failure_class="integration_error",
                ) from exc
            except httpx.HTTPError as exc:
                raise OmnigentClientError(
                    self._transport_error_message(exc),
                    failure_class="integration_error",
                ) from exc
            return self._parse_json_response(response)

        shared = shared_pool_client() if self._transport is None else None
        if shared is not None:
            try:
                response = await shared.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers(),
                    timeout=timeout,
                    **kwargs,
                )
            except httpx.PoolTimeout as exc:
                raise OmnigentClientError(
                    self._redact(
                        "Omnigent transport pool exhausted; "
                        f"increase MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS: {exc}"
                    ),
                    failure_class="integration_error",
                ) from exc
            except httpx.HTTPError as exc:
                raise OmnigentClientError(
                    self._transport_error_message(exc),
                    failure_class="integration_error",
                ) from exc
            return self._parse_json_response(response)

        async with httpx.AsyncClient(
            timeout=timeout,
            limits=self._limits,
            transport=self._transport,
        ) as client:
            try:
                response = await client.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers(),
                    **kwargs,
                )
            except httpx.PoolTimeout as exc:
                raise OmnigentClientError(
                    self._redact(
                        "Omnigent transport pool exhausted; "
                        f"increase MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS: {exc}"
                    ),
                    failure_class="integration_error",
                ) from exc
            except httpx.HTTPError as exc:
                raise OmnigentClientError(
                    self._transport_error_message(exc),
                    failure_class="integration_error",
                ) from exc
        return self._parse_json_response(response)

    def _parse_json_response(self, response: httpx.Response) -> dict[str, Any]:
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
        if self._client is not None:
            try:
                response = await self._client.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers(),
                    timeout=self._timeout,
                )
            except httpx.PoolTimeout as exc:
                raise OmnigentClientError(
                    self._redact(
                        "Omnigent transport pool exhausted; "
                        f"increase MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS: {exc}"
                    ),
                    failure_class="integration_error",
                ) from exc
            except httpx.HTTPError as exc:
                raise OmnigentClientError(
                    self._transport_error_message(exc),
                    failure_class="integration_error",
                ) from exc
            if response.status_code < 200 or response.status_code >= 300:
                raise self._error_from_response(response.status_code, response.text)
            return response.content

        shared = shared_pool_client() if self._transport is None else None
        if shared is not None:
            try:
                response = await shared.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers(),
                    timeout=self._timeout,
                )
            except httpx.PoolTimeout as exc:
                raise OmnigentClientError(
                    self._redact(
                        "Omnigent transport pool exhausted; "
                        f"increase MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS: {exc}"
                    ),
                    failure_class="integration_error",
                ) from exc
            except httpx.HTTPError as exc:
                raise OmnigentClientError(
                    self._transport_error_message(exc),
                    failure_class="integration_error",
                ) from exc
            if response.status_code < 200 or response.status_code >= 300:
                raise self._error_from_response(response.status_code, response.text)
            return response.content

        async with httpx.AsyncClient(
            timeout=self._timeout,
            limits=self._limits,
            transport=self._transport,
        ) as client:
            try:
                response = await client.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers(),
                )
            except httpx.PoolTimeout as exc:
                raise OmnigentClientError(
                    self._redact(
                        "Omnigent transport pool exhausted; "
                        f"increase MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS: {exc}"
                    ),
                    failure_class="integration_error",
                ) from exc
            except httpx.HTTPError as exc:
                raise OmnigentClientError(
                    self._transport_error_message(exc),
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
            failure_class=classify_omnigent_http_status(status_code),
        )

    def _redact(self, value: str) -> str:
        return redact_sensitive_text(self._redactor.scrub(value))

    def _transport_error_message(self, exc: httpx.HTTPError) -> str:
        """Return a useful redacted message even for empty httpx timeout text."""

        detail = str(exc).strip() or type(exc).__name__
        return self._redact(f"Omnigent transport error: {detail}")


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
            _scrub_payload_with_redactor(item, redactor=redactor) for item in payload
        ]
    return payload


__all__ = [
    "OmnigentClientError",
    "OmnigentHttpClient",
    "aclose_shared_pool_client",
    "default_omnigent_pool_limits",
    "init_shared_pool_client",
    "parse_sse_line",
    "shared_pool_client",
]
