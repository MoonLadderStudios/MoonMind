"""HTTP client for OpenClaw OpenAI-compatible streaming chat completions."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

class OpenClawClientError(RuntimeError):
    """Raised when the OpenClaw gateway returns an error response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

def _scrub_error_message(text: str) -> str:
    lowered = text.lower()
    if "bearer" in lowered or "authorization" in lowered:
        return "OpenClaw gateway request failed (authorization error)"
    return text

class OpenClawHttpClient:
    """Async client for ``POST /v1/chat/completions`` with ``stream: true``."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        total_timeout_seconds: float,
    ) -> None:
        self._base = str(base_url).rstrip("/")
        self._token = token
        # Long total budget; no read timeout so autonomous loops are not cut off.
        self._timeout = httpx.Timeout(total_timeout_seconds, read=None)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    async def stream_chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Yield incremental text deltas from an SSE chat completion stream."""

        payload = {"model": model, "messages": messages, "stream": True}
        url = f"{self._base}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=self._headers(),
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", errors="replace")[
                            :2048
                        ]
                        msg = _scrub_error_message(
                            f"OpenClaw HTTP {response.status_code}: {body}"
                        )
                        raise OpenClawClientError(
                            msg, status_code=response.status_code
                        )

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith(":"):
                            continue
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data_json = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        choices = data_json.get("choices") or []
                        if not choices:
                            continue
                        delta = (choices[0] or {}).get("delta") or {}
                        content = delta.get("content")
                        if content:
                            yield str(content)
        except httpx.HTTPError as exc:
            raise OpenClawClientError(
                _scrub_error_message(f"OpenClaw transport error: {exc!s}")
            ) from exc

def parse_sse_lines_for_deltas(lines: list[str]) -> list[str]:
    """Parse SSE lines for unit tests (same rules as ``stream_chat_completion``)."""

    out: list[str] = []
    for line in lines:
        if not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            break
        try:
            data_json = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        choices = data_json.get("choices") or []
        if not choices:
            continue
        delta = (choices[0] or {}).get("delta") or {}
        content = delta.get("content")
        if content:
            out.append(str(content))
    return out

__all__ = [
    "OpenClawClientError",
    "OpenClawHttpClient",
    "parse_sse_lines_for_deltas",
]
