"""Centralized constants for task job types.

Relocated from the deleted ``moonmind.workflows.agent_queue.job_types``
module as part of the single-substrate migration.
"""

from __future__ import annotations

from typing import FrozenSet

# legacy_run contract — the serialized job-type discriminator value "task"
# renames at the MoonMind.UserWorkflow v2 cutover (MM-730).
CANONICAL_WORKFLOW_JOB_TYPE = "task"
LEGACY_WORKFLOW_JOB_TYPES: FrozenSet[str] = frozenset({"codex_exec", "codex_skill"})

__all__ = [
    "CANONICAL_WORKFLOW_JOB_TYPE",
    "LEGACY_WORKFLOW_JOB_TYPES",
]
