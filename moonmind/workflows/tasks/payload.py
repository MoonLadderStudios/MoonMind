"""Task payload compiler helpers for template metadata and capabilities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _normalize_capabilities(values: list[Any] | None) -> list[str]:
    if not values:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = str(raw or "").strip().lower()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _sanitize_template_application(entry: dict[str, Any]) -> dict[str, Any] | None:
    slug = str(entry.get("slug") or "").strip()
    version = str(entry.get("version") or "").strip()
    if not slug or not version:
        return None
    inputs = entry.get("inputs")
    if not isinstance(inputs, dict):
        inputs = {}
    step_ids = entry.get("stepIds")
    if not isinstance(step_ids, list):
        step_ids = []
    applied_at = str(entry.get("appliedAt") or "").strip()
    if not applied_at:
        applied_at = datetime.now(UTC).isoformat()
    payload = {
        "slug": slug,
        "version": version,
        "inputs": inputs,
        "appliedAt": applied_at,
    }
    if step_ids:
        payload["stepIds"] = [str(item).strip() for item in step_ids if str(item).strip()]
    return payload


def compile_task_payload_templates(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize applied template metadata and merge derived capabilities."""

    compiled = dict(payload)
    task_node = compiled.get("task")
    task_payload = dict(task_node) if isinstance(task_node, dict) else {}

    applied_raw = task_payload.get("appliedStepTemplates")
    applied_items = applied_raw if isinstance(applied_raw, list) else []
    sanitized_applications: list[dict[str, Any]] = []
    template_caps: list[str] = []
    for raw in applied_items:
        if not isinstance(raw, dict):
            continue
        sanitized = _sanitize_template_application(raw)
        if sanitized is None:
            continue
        sanitized_applications.append(sanitized)
        caps = raw.get("capabilities")
        if isinstance(caps, list):
            template_caps.extend(caps)

    if sanitized_applications:
        task_payload["appliedStepTemplates"] = sanitized_applications

    task_required = task_payload.get("requiredCapabilities")
    payload_required = compiled.get("requiredCapabilities")
    merged_caps = _normalize_capabilities(
        list(payload_required if isinstance(payload_required, list) else [])
        + list(task_required if isinstance(task_required, list) else [])
        + template_caps
    )
    if merged_caps:
        compiled["requiredCapabilities"] = merged_caps
        task_payload["requiredCapabilities"] = merged_caps

    compiled["task"] = task_payload
    return compiled


__all__ = ["compile_task_payload_templates"]
