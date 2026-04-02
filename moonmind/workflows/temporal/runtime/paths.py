"""Helpers for managed-runtime filesystem layout."""

from __future__ import annotations

import os
from pathlib import Path


def managed_runtime_artifact_root() -> Path:
    """Return the normalized artifact root for managed-runtime files.

    ``MOONMIND_AGENT_RUNTIME_ARTIFACTS`` may point either at the agent-jobs
    root or directly at its ``artifacts`` directory.
    """

    root = Path(os.environ.get("MOONMIND_AGENT_RUNTIME_ARTIFACTS", "/work/agent_jobs"))
    if root.name != "artifacts":
        root = root / "artifacts"
    return root
