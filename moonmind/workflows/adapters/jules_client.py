"""Async HTTP adapter for the Jules API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from moonmind.schemas.jules_models import (
    JulesCreateTaskRequest,
    JulesGetTaskRequest,
    JulesResolveTaskRequest,
    JulesTaskResponse,
)

logger = logging.getLogger(__name__)


class JulesClientError(RuntimeError):
    """Raised when Jules API requests fail.

    Carries structured metadata for error mapping.  The string
    representation is intentionally scrubbed to avoid leaking
    secrets (API keys, bearer tokens, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_path = request_path

    def __str__(self) -> str:
        parts = ["Jules API request failed"]
        if self.request_path:
            parts.append(f"path={self.request_path}")
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        return ": ".join(parts)


class JulesClient:
    """HTTP wrapper for Jules task management endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
        retry_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._retry_attempts = retry_attempts
        self._retry_delay_seconds = retry_delay_seconds
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            self._client = httpx.AsyncClient(
                base_url=base_url, timeout=timeout, headers=headers
            )
            self._owns_client = True

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_task(self, request: JulesCreateTaskRequest) -> JulesTaskResponse:
        data = await self._post_json(
            "/tasks",
            json=request.model_dump(by_alias=True, mode="json"),
        )
        return JulesTaskResponse.model_validate(data)

    async def resolve_task(self, request: JulesResolveTaskRequest) -> JulesTaskResponse:
        data = await self._post_json(
            f"/tasks/{request.task_id}/finish",
            json=request.model_dump(by_alias=True, mode="json", exclude={"task_id"}),
        )
        return JulesTaskResponse.model_validate(data)

    async def get_task(self, request: JulesGetTaskRequest) -> JulesTaskResponse:
        data = await self._get_json(f"/tasks/{request.task_id}")
        return JulesTaskResponse.model_validate(data)

    async def _post_json(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        return await self._request_with_retry("POST", path, json=json)

    async def _get_json(self, path: str) -> dict[str, Any]:
        return await self._request_with_retry("GET", path)

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self._retry_attempts + 1):
            try:
                response = await self._client.request(method, path, json=json)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    return payload
                raise JulesClientError(
                    f"Jules API response for {path} was not a JSON object",
                    request_path=path,
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                retryable = (500 <= status_code < 600) or status_code == 429
                last_error = exc
                if retryable and attempt < self._retry_attempts:
                    delay = self._retry_delay_seconds
                    if status_code == 429:
                        retry_after = exc.response.headers.get("Retry-After")
                        if retry_after is not None:
                            try:
                                delay = max(delay, float(retry_after))
                            except (ValueError, TypeError):
                                pass
                    logger.warning(
                        "Jules API request %s failed with HTTP %s (attempt %s/%s); retrying in %.1fs",
                        path,
                        status_code,
                        attempt,
                        self._retry_attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise JulesClientError(
                    f"HTTP {status_code}",
                    status_code=status_code,
                    request_path=path,
                ) from exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < self._retry_attempts:
                    logger.warning(
                        "Jules API request %s failed due to %s (attempt %s/%s); retrying in %.1fs",
                        path,
                        exc.__class__.__name__,
                        attempt,
                        self._retry_attempts,
                        self._retry_delay_seconds,
                    )
                    await asyncio.sleep(self._retry_delay_seconds)
                    continue
                break
            except httpx.HTTPError as exc:
                raise JulesClientError(
                    exc.__class__.__name__,
                    request_path=path,
                ) from exc
        raise JulesClientError(
            last_error.__class__.__name__ if last_error else "unknown error",
            request_path=path,
        ) from last_error


__all__ = [
    "JulesClient",
    "JulesClientError",
]
