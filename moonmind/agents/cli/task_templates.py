"""CLI-friendly helpers for task template catalog APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(slots=True)
class TaskTemplateClient:
    """Small HTTP client for template list/expand API usage in CLI flows."""

    base_url: str
    token: str | None = None
    timeout_seconds: int = 30

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def list_templates(
        self,
        *,
        scope: str,
        scope_ref: str | None = None,
        tag: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"scope": scope}
        if scope_ref:
            params["scopeRef"] = scope_ref
        if tag:
            params["tag"] = tag
        if search:
            params["search"] = search
        response = requests.get(
            f"{self.base_url.rstrip('/')}/api/task-step-templates",
            headers=self._headers(),
            params=params,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("items", []) if isinstance(payload, dict) else []

    def expand_template(
        self,
        *,
        slug: str,
        scope: str,
        version: str,
        inputs: dict[str, Any],
        scope_ref: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"scope": scope}
        if scope_ref:
            params["scopeRef"] = scope_ref
        response = requests.post(
            f"{self.base_url.rstrip('/')}/api/task-step-templates/{slug}:expand",
            headers=self._headers(),
            params=params,
            json={
                "version": version,
                "inputs": dict(inputs or {}),
                "context": dict(context or {}),
                "options": {"enforceStepLimit": True},
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected template expand response payload.")
        return payload


def merge_expanded_steps(
    *,
    existing_steps: list[dict[str, Any]],
    expanded_steps: list[dict[str, Any]],
    mode: str = "append",
) -> list[dict[str, Any]]:
    """Merge expanded steps into an existing task payload step list."""

    normalized_mode = str(mode or "append").strip().lower()
    if normalized_mode not in {"append", "replace"}:
        raise ValueError("mode must be 'append' or 'replace'")

    base = list(existing_steps or [])
    incoming = list(expanded_steps or [])
    if normalized_mode == "replace":
        return incoming
    return [*base, *incoming]


__all__ = ["TaskTemplateClient", "merge_expanded_steps"]
