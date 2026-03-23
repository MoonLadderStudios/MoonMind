"""Centralized constants for task job types.

Relocated from the deleted ``moonmind.workflows.agent_queue.job_types``
module as part of the single-substrate migration.
"""

from __future__ import annotations

from typing import FrozenSet

CANONICAL_TASK_JOB_TYPE = "task"
LEGACY_TASK_JOB_TYPES: FrozenSet[str] = frozenset({"codex_exec", "codex_skill"})

__all__ = [
    "CANONICAL_TASK_JOB_TYPE",
    "LEGACY_TASK_JOB_TYPES",
]
