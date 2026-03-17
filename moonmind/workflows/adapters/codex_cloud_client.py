"""Async HTTP adapter for the Codex Cloud API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CodexCloudClientError(RuntimeError):
    """Raised when Codex Cloud API requests fail.

    The string representation is intentionally scrubbed to avoid
    leaking secrets (API keys, bearer tokens, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_path: str | None = None,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_path = request_path
        self.ambiguous = ambiguous

    def __str__(self) -> str:
        parts = ["Codex Cloud API request failed"]
        if self.request_path:
            parts.append(f"path={self.request_path}")
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.ambiguous:
            parts.append("ambiguous")
        return ": ".join(parts)


# Codex Cloud normalized status set.
_CODEX_CLOUD_STATUS_MAP: dict[str, str] = {
    "accepted": "queued",
    "assigned": "queued",
    "canceled": "canceled",
    "cancelled": "canceled",
    "completed": "succeeded",
    "created": "queued",
    "done": "succeeded",
    "error": "failed",
    "failed": "failed",
    "finished": "succeeded",
    "in_progress": "running",
    "open": "queued",
    "pending": "queued",
    "processing": "running",
    "queued": "queued",
    "running": "running",
    "started": "running",
    "submitted": "queued",
    "succeeded": "succeeded",
    "success": "succeeded",
    "timed_out": "failed",
    "timeout": "failed",
}


def normalize_codex_cloud_status(raw_status: str | None) -> str:
    """Map raw Codex Cloud status values to the provider-neutral status set."""

    normalized = str(raw_status or "").strip().lower()
    if not normalized:
        return "unknown"
    return _CODEX_CLOUD_STATUS_MAP.get(normalized, "unknown")


class CodexCloudClient:
    """HTTP wrapper for Codex Cloud task management endpoints."""

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

    async def create_task(
        self,
        *,
        title: str,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new task and return the provider response."""

        payload: dict[str, Any] = {
            "title": title,
            "description": description,
        }
        if metadata:
            payload["metadata"] = metadata
        return await self._post_json("/tasks", json=payload)

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """Get a task by its provider ID."""

        return await self._get_json(f"/tasks/{task_id}")

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Attempt to cancel a task."""

        return await self._post_json(
            f"/tasks/{task_id}/cancel",
            json={},
        )

    async def _post_json(
        self, path: str, *, json: dict[str, Any]
    ) -> dict[str, Any]:
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
                raise CodexCloudClientError(
                    f"Codex Cloud response for {path} was not a JSON object",
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
                        "Codex Cloud request %s failed with HTTP %s (attempt %s/%s); retrying in %.1fs",
                        path,
                        status_code,
                        attempt,
                        self._retry_attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise CodexCloudClientError(
                    f"HTTP {status_code}",
                    status_code=status_code,
                    request_path=path,
                ) from exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < self._retry_attempts:
                    logger.warning(
                        "Codex Cloud request %s failed due to %s (attempt %s/%s); retrying in %.1fs",
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
                raise CodexCloudClientError(
                    exc.__class__.__name__,
                    request_path=path,
                ) from exc
        raise CodexCloudClientError(
            last_error.__class__.__name__ if last_error else "unknown error",
            request_path=path,
        ) from last_error


__all__ = [
    "CodexCloudClient",
    "CodexCloudClientError",
    "normalize_codex_cloud_status",
]
