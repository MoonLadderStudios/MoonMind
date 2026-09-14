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

    def __init__(self, message: str = "", *, code: str = "reviewer_unavailable") -> None:
        super().__init__(message)
        self.code = code


# Deployment-owned ceilings for the configured reviewer (MoonMind#3945).
# These bound resource use before unbounded provider I/O or JSON parsing.
# The timeout ceiling matches the production `step.review` Temporal route
# (activity_catalog.py: `TemporalActivityTimeouts(120, 300)`): the activity
# must finish within the 120s start-to-close budget.
REVIEW_RESPONSE_MAX_BYTES = 64_000
REVIEW_TIMEOUT_MIN_SECONDS = 1
REVIEW_TIMEOUT_MAX_SECONDS = 120
REVIEW_MAX_OUTPUT_TOKENS = 4096
REVIEW_ANTHROPIC_MAX_TOKENS = 4096


def _is_o_series_model(model: str) -> bool:
    """Return True for OpenAI o-series reasoning models.

    o-series Chat Completions models reject the legacy `max_tokens` field
    and require `max_completion_tokens`.
    """
    name = str(model or "").strip().lower()
    return name.startswith("o1") or name.startswith("o3") or name.startswith("o4")


class ConfiguredStepReviewer:
    def __init__(
        self, config: Any, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._config = config
        self._transport = transport

    def describe_route(self, model: str) -> dict[str, str]:
        """Return the admitted reviewer route without touching credentials."""
        provider = str(getattr(self._config, "default_chat_provider", "unknown") or "unknown").lower()
        if provider not in {"google", "openai", "anthropic"}:
            return {"provider": provider or "unknown", "model": str(model or "default")}
        provider_config = getattr(self._config, provider, None)
        selected = str(model or "default")
        if selected == "default" and provider_config is not None:
            selected = str(getattr(provider_config, f"{provider}_chat_model", selected) or selected)
        return {"provider": provider, "model": selected or "default"}

    async def review(self, *, prompt: str, model: str, timeout: int) -> str:
        try:
            timeout_value = int(timeout)
        except (TypeError, ValueError):
            raise ReviewerUnavailable(
                "Requested review timeout is not a number.",
                code="review_timeout_invalid",
            ) from None
        if timeout_value < REVIEW_TIMEOUT_MIN_SECONDS:
            raise ReviewerUnavailable(
                "Requested review timeout is not positive.",
                code="review_timeout_invalid",
            )
        if timeout_value > REVIEW_TIMEOUT_MAX_SECONDS:
            raise ReviewerUnavailable(
                "Requested review timeout exceeds the deployment ceiling.",
                code="review_timeout_over_budget",
            )
        provider = self._config.default_chat_provider.lower()
        if provider not in {"google", "openai", "anthropic"}:
            raise ReviewerUnavailable(
                "Configured reviewer provider is unsupported.",
                code="reviewer_misconfigured",
            )
        provider_config = getattr(self._config, provider)
        credential = getattr(provider_config, f"{provider}_api_key")
        if not getattr(provider_config, f"{provider}_enabled"):
            raise ReviewerUnavailable(
                "Configured reviewer provider is disabled.",
                code="reviewer_disabled",
            )
        if not credential:
            raise ReviewerUnavailable(
                "Configured reviewer provider has no credential.",
                code="reviewer_unavailable",
            )
        selected_model = (
            getattr(provider_config, f"{provider}_chat_model")
            if model == "default"
            else model
        )
        if not selected_model or not selected_model.strip():
            raise ReviewerUnavailable(
                "Configured reviewer has no model.",
                code="reviewer_misconfigured",
            )
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
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "maxOutputTokens": REVIEW_MAX_OUTPUT_TOKENS,
                },
            }
        elif provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers["Authorization"] = f"Bearer {credential}"
            body = {
                "model": selected_model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            }
            # o-series models require `max_completion_tokens`; other models
            # use the legacy `max_tokens` field.
            if _is_o_series_model(str(selected_model)):
                body["max_completion_tokens"] = REVIEW_MAX_OUTPUT_TOKENS
            else:
                body["max_tokens"] = REVIEW_MAX_OUTPUT_TOKENS
        else:
            url = "https://api.anthropic.com/v1/messages"
            headers.update({
                "x-api-key": credential,
                "anthropic-version": "2023-06-01",
            })
            body = {
                "model": selected_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": REVIEW_ANTHROPIC_MAX_TOKENS,
            }
        async with httpx.AsyncClient(
            transport=self._transport, timeout=timeout_value, follow_redirects=False
        ) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            if len(response.content) > REVIEW_RESPONSE_MAX_BYTES:
                raise ReviewerUnavailable(
                    "Configured reviewer response exceeds the deployment byte ceiling.",
                    code="reviewer_truncated",
                )
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
