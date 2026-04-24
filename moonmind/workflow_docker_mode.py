"""Shared workflow Docker mode normalization and policy constants."""

from __future__ import annotations

from typing import Final, Literal

WorkflowDockerMode = Literal["disabled", "profiles", "unrestricted"]
WORKFLOW_DOCKER_MODE_VALUES: Final[tuple[WorkflowDockerMode, ...]] = (
    "disabled",
    "profiles",
    "unrestricted",
)
DEFAULT_WORKFLOW_DOCKER_MODE: Final[WorkflowDockerMode] = "profiles"

def normalize_workflow_docker_mode(value: object) -> WorkflowDockerMode:
    """Return the canonical deployment-owned workflow Docker mode."""

    normalized = str(value or DEFAULT_WORKFLOW_DOCKER_MODE).strip().lower()
    if not normalized:
        return DEFAULT_WORKFLOW_DOCKER_MODE
    if normalized not in WORKFLOW_DOCKER_MODE_VALUES:
        allowed = ", ".join(WORKFLOW_DOCKER_MODE_VALUES)
        raise ValueError(f"workflow_docker_mode must be one of: {allowed}")
    return normalized  # type: ignore[return-value]
