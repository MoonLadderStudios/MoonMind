"""Low-level Jira REST client with bounded retries and sanitized errors."""

from __future__ import annotations

import asyncio
import logging as stdlib_logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import httpx

from moonmind.integrations.jira.auth import ResolvedJiraConnection
from moonmind.integrations.jira.errors import JiraToolError
import moonmind.utils.logging as moonmind_logging

logger = stdlib_logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

class JiraClient:
    """Async Jira REST wrapper for trusted managed-agent tool execution."""

    def __init__(
        self,
        *,
        connection: ResolvedJiraConnection,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._connection = connection
        self._redactor = moonmind_logging.SecretRedactor(connection.redaction_values)
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            timeout = httpx.Timeout(
                connect=connection.connect_timeout_seconds,
                read=connection.read_timeout_seconds,
                write=connection.read_timeout_seconds,
                pool=connection.connect_timeout_seconds,
            )
            self._client = httpx.AsyncClient(
                base_url=connection.base_url,
                headers=connection.headers,
                timeout=timeout,
            )
            self._owns_client = True

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request_json(
        self,
        *,
        method: str,
        path: str,
        action: str,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> Any:
        """Perform one Jira request with bounded retry handling."""

        response = await self._request_raw(
            method=method,
            path=path,
            action=action,
            params=params,
            json_body=json_body,
            context=context,
        )
        try:
            return self._decode_response(response)
        except Exception as exc:
            raise JiraToolError(
                "Jira response could not be decoded.",
                code="jira_request_failed",
                status_code=502,
                action=action,
            ) from exc

    async def request_bytes(
        self,
        *,
        method: str,
        path: str,
        action: str,
        params: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> tuple[bytes, str]:
        """Perform one Jira request and return raw bytes plus content type."""

        response = await self._request_raw(
            method=method,
            path=path,
            action=action,
            params=params,
            context=context,
        )
        return (
            bytes(response.content or b""),
            response.headers.get("content-type", "").split(";", 1)[0].strip(),
        )

    async def _request_raw(
        self,
        *,
        method: str,
        path: str,
        action: str,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        """Perform one Jira request and return the successful raw response."""

        attempts = max(self._connection.retry_attempts, 1)
        retry_delay = 1.0
        request_id: str | None = None
        safe_context = dict(context or {})

        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.request(
                    method=method,
                    url=self._resolve_request_path(path),
                    params=params,
                    json=json_body,
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt < attempts:
                    logger.info(
                        "jira_retrying action=%s reason=transport attempt=%s request_id=%s context=%s",
                        action,
                        attempt,
                        request_id,
                        safe_context,
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                raise JiraToolError(
                    "Jira request could not reach Atlassian.",
                    code="jira_request_failed",
                    status_code=502,
                    action=action,
                ) from exc

            request_id = (
                response.headers.get("x-arequestid")
                or response.headers.get("x-request-id")
                or response.headers.get("atl-traceid")
            )

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < attempts:
                wait_seconds = self._retry_after_seconds(response) or retry_delay
                logger.info(
                    "jira_retrying action=%s status=%s attempt=%s request_id=%s context=%s",
                    action,
                    response.status_code,
                    attempt,
                    request_id,
                    safe_context,
                )
                await asyncio.sleep(wait_seconds)
                retry_delay = max(wait_seconds * 2, retry_delay * 2)
                continue

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt >= attempts:
                if response.status_code == 429:
                    raise JiraToolError(
                        "Jira rate limited the request after the configured retries were exhausted.",
                        code="jira_rate_limited",
                        status_code=429,
                        action=action,
                    )
                raise JiraToolError(
                    "Jira was temporarily unavailable after the configured retries were exhausted.",
                    code="jira_request_failed",
                    status_code=502,
                    action=action,
                )

            if response.status_code >= 400:
                self._log_failure(
                    action=action,
                    response=response,
                    request_id=request_id,
                    context=safe_context,
                )
                auth_error = await self._auth_error_if_credentials_rejected(
                    action=action,
                    response=response,
                )
                if auth_error is not None:
                    raise auth_error
                raise self._response_error(
                    action=action,
                    response=response,
                )

            logger.info(
                "jira_request_completed action=%s status=%s request_id=%s context=%s",
                action,
                response.status_code,
                request_id,
                safe_context,
            )
            return response

        raise JiraToolError(
            "Jira request failed unexpectedly before a response could be returned.",
            code="jira_request_failed",
            status_code=502,
            action=action,
        )

    def _decode_response(self, response: httpx.Response) -> Any:
        if not response.content:
            return {}
        content_type = response.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            return response.json()
        return response.text

    async def _auth_error_if_credentials_rejected(
        self,
        *,
        action: str,
        response: httpx.Response,
    ) -> JiraToolError | None:
        """Disambiguate Jira 404s that are actually bad credentials.

        Jira Cloud commonly returns 404 for issue/project reads when the caller
        is anonymous or unauthorized. Probe a low-cost authenticated endpoint
        before surfacing a misleading "not found" error.
        """

        if response.status_code not in {403, 404}:
            return None
        if action == "verify_connection" and response.request.url.path.endswith(
            "/myself"
        ):
            return None
        try:
            if self._connection.auth_mode == "service_account_scoped":
                auth_response = await self._client.request(
                    method="GET",
                    url=self._resolve_request_path("/project/search"),
                    params={"maxResults": 1},
                )
            else:
                auth_response = await self._client.request(
                    method="GET",
                    url=self._resolve_request_path("/myself"),
                )
        except (httpx.TransportError, httpx.TimeoutException):
            return None
        if auth_response.status_code in {401, 403}:
            return JiraToolError(
                "Jira credentials are invalid, expired, or use the wrong auth mode.",
                code="jira_auth_failed",
                status_code=401 if auth_response.status_code == 401 else 403,
                action=action,
            )
        return None

    def _resolve_request_path(self, path: str) -> str:
        if not path.startswith("agile:"):
            return path

        agile_suffix = path.removeprefix("agile:").strip()
        if not agile_suffix.startswith("/"):
            agile_suffix = f"/{agile_suffix}"

        parsed_base = urlparse(self._connection.base_url)
        origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
        base_path = parsed_base.path.rstrip("/")
        marker = "/rest/api/"
        if marker in f"{base_path}/":
            prefix = base_path.split(marker, 1)[0]
            return f"{origin}{prefix}/rest/agile/1.0{agile_suffix}"

        return f"{origin}/rest/agile/1.0{agile_suffix}"

    def _retry_after_seconds(self, response: httpx.Response) -> float | None:
        raw = str(response.headers.get("Retry-After", "")).strip()
        if not raw:
            return None
        try:
            seconds = float(raw)
        except ValueError:
            return None
        return max(seconds, 0.0)

    def _log_failure(
        self,
        *,
        action: str,
        response: httpx.Response,
        request_id: str | None,
        context: Mapping[str, Any] | None,
    ) -> None:
        logger.warning(
            "jira_request_failed action=%s status=%s request_id=%s context=%s body=%s",
            action,
            response.status_code,
            request_id,
            dict(context or {}),
            self._redactor.scrub(response.text),
        )

    def _response_error(
        self,
        *,
        action: str,
        response: httpx.Response,
    ) -> JiraToolError:
        status = response.status_code
        if status == 401:
            return JiraToolError(
                "Jira credentials are invalid, expired, or use the wrong auth mode.",
                code="jira_auth_failed",
                status_code=401,
                action=action,
            )
        if status == 403:
            return JiraToolError(
                "Jira denied the request because the credential lacks permission or scope.",
                code="jira_permission_denied",
                status_code=403,
                action=action,
            )
        if status == 404:
            return JiraToolError(
                "Jira could not find the requested issue, project, or endpoint binding.",
                code="jira_not_found",
                status_code=404,
                action=action,
            )
        if status in {400, 422}:
            if action == "create_issue_link" and "already exists" in response.text.lower():
                return JiraToolError(
                    "Jira issue link already exists.",
                    code="jira_conflict_existing_link",
                    status_code=409,
                    action=action,
                )
            return JiraToolError(
                "Jira rejected the request because one or more fields or workflow values were invalid.",
                code="jira_validation_failed",
                status_code=422,
                action=action,
            )
        return JiraToolError(
            f"Jira request failed with HTTP {status}.",
            code="jira_request_failed",
            status_code=502,
            action=action,
        )
