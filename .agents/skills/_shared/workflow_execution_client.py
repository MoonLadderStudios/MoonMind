"""Portable request-authoring rules for MoonMind execution API clients.

This module intentionally uses only the Python standard library.  It is copied
into resolved skill snapshots and must remain importable outside MoonMind.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SUPPORTED_CHILD_PUBLISH_MODES = frozenset(
    {"auto", "none", "branch", "pr", "pr_with_merge_automation"}
)
IDEMPOTENCY_KEY_MAX_LENGTH = 128


def normalize_publish_mode(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in SUPPORTED_CHILD_PUBLISH_MODES else "pr"


def normalize_runtime_id(value: str | None) -> str | None:
    candidate = str(value or "").strip().lower().replace("-", "_")
    aliases = {"codex": "codex_cli", "claude": "claude_code"}
    return aliases.get(candidate, candidate) or None


def child_idempotency_key(
    *, batch_scope: str, provider: str, ref: str, target_kind: str, target_slug: str
) -> str:
    components = {
        "scope": batch_scope.strip(),
        "provider": provider,
        "ref": ref,
        "targetKind": target_kind,
        "targetSlug": target_slug,
    }
    if not components["scope"]:
        raise ValueError("batch_scope must be non-empty")
    canonical = json.dumps(components, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    safe_ref = re.sub(r"[^A-Za-z0-9_.#/-]+", "_", ref)[:32]
    key = f"batch-workflows:{provider}:{safe_ref}:sha256:{digest}"
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        key = f"batch-workflows:sha256:{digest}"
    return key


def validate_execution_envelope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") != "task":
        raise ValueError("execution envelope must be a task object")
    payload = value.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("task"), dict):
        raise ValueError("execution envelope payload.task is required")
    return value
