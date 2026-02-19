"""Centralized constants for Agent Queue job types."""

from __future__ import annotations

from typing import FrozenSet

CANONICAL_TASK_JOB_TYPE = "task"
LEGACY_TASK_JOB_TYPES: FrozenSet[str] = frozenset({"codex_exec", "codex_skill"})
MANIFEST_JOB_TYPE = "manifest"

SUPPORTED_QUEUE_JOB_TYPES: FrozenSet[str] = frozenset(
    {CANONICAL_TASK_JOB_TYPE, MANIFEST_JOB_TYPE, *LEGACY_TASK_JOB_TYPES}
)

__all__ = [
    "CANONICAL_TASK_JOB_TYPE",
    "LEGACY_TASK_JOB_TYPES",
    "MANIFEST_JOB_TYPE",
    "SUPPORTED_QUEUE_JOB_TYPES",
]
