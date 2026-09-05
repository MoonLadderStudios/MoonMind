"""Provider adapter for the LLM fleet's configured step reviewer.

Uses the existing chat provider, enablement, model and credential settings.
Provider credentials never come from the workflow payload. There is no fallback
provider or implicit model substitution.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx


class ReviewerUnavailable(ValueError):
    """The selected deployment reviewer has no usable authority."""


class ConfiguredStepReviewer:
    def __init__(
        self, config: Any, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._config = config
        self._transport = transport

    async def review(self, *, prompt: str, model: str, timeout: int) -> str:
        provider = self._config.default_chat_provider
        if provider not in {"google", "openai", "anthropic"}:
            raise ReviewerUnavailable("Configured reviewer provider is unsupported.")
        provider_config = getattr(self._config, provider)
        credential = getattr(provider_config, f"{provider}_api_key")
        if not getattr(provider_config, f"{provider}_enabled") or not credential:
            raise ReviewerUnavailable(
                "Configured reviewer provider is disabled or has no credential."
            )
        selected_model = (
            getattr(provider_config, f"{provider}_chat_model")
            if model == "default"
            else model
        )
        if not selected_model or not selected_model.strip():
            raise ReviewerUnavailable("Configured reviewer has no model.")
        headers = {"Content-Type": "application/json"}
        if provider == "google":
            # Header authentication keeps credentials out of URLs and diagnostics.
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{quote(selected_model, safe='')}:generateContent"
            )
            headers["x-goog-api-key"] = credential
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            }
        elif provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers["Authorization"] = f"Bearer {credential}"
            body = {
                "model": selected_model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            }
        else:
            url = "https://api.anthropic.com/v1/messages"
            headers.update({
                "x-api-key": credential,
                "anthropic-version": "2023-06-01",
            })
            body = {
                "model": selected_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096,
            }
        async with httpx.AsyncClient(
            transport=self._transport, timeout=timeout, follow_redirects=False
        ) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            result = response.json()
        if provider == "google":
            return "".join(
                part.get("text", "")
                for part in result["candidates"][0]["content"]["parts"]
            )
        if provider == "openai":
            return result["choices"][0]["message"]["content"]
        return "".join(
            part.get("text", "")
            for part in result["content"]
            if part.get("type") == "text"
        )
