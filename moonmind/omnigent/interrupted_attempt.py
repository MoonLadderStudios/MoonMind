"""Successor handoff of an interrupted agent attempt (MoonMind#4627).

Both execution owners -- the generic host realizer and the profile-bound
coordinator -- end a lost-host attempt with a returned
``retry_step_execution`` result. Before a successor Step Execution may write,
that result states whether the old host's stop is confirmed and carries the
compact restore identity of the attempt's verified save. An unconfirmed stop
starts no successor.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moonmind.omnigent.harness_platform.failures import successor_remediation
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult

SAVED_WORKSPACE_RESTORE_KEYS = (
    "kind",
    "baseCommit",
    "archiveRef",
    "archiveDigest",
    "manifestRef",
    "manifestDigest",
    "workspaceDigest",
    "checkpointRef",
)

# The Step Execution reason MoonMind.Run gives a runtime-recovered successor.
RECOVERED_SUCCESSOR_REASON = "runtime_recovered"


def compact_saved_workspace(saved: Any) -> dict[str, Any] | None:
    """Return the compact restore identity of a durable workspace save."""

    if not isinstance(saved, Mapping):
        return None
    compact = {key: saved[key] for key in SAVED_WORKSPACE_RESTORE_KEYS if key in saved}
    return compact if compact.get("archiveRef") else None


def is_interruption(result: AgentRunResult | None) -> bool:
    return result is not None and result.retry_recommendation == "retry_step_execution"


def gate_interrupted_result(
    result: AgentRunResult,
    *,
    saved: Any,
    predecessor_stop_confirmed: bool,
) -> AgentRunResult:
    """Gate a returned interruption on the old host's confirmed stop.

    The result carries the stop verdict and the saved workspace a successor
    restores. An expired lease or lost heartbeat does not stop a partitioned
    host, so an unconfirmed stop recommends the janitor, not a successor.
    """

    metadata = {
        **(result.metadata or {}),
        "predecessorStopConfirmed": predecessor_stop_confirmed,
    }
    compact = compact_saved_workspace(saved)
    if compact is not None:
        metadata["savedWorkspaceCheckpoint"] = compact
    return result.model_copy(
        update={
            "metadata": metadata,
            "retry_recommendation": successor_remediation(
                result.retry_recommendation or "",
                predecessor_stop_confirmed=predecessor_stop_confirmed,
            ),
        }
    )


def is_recovered_successor(request: AgentExecutionRequest) -> bool:
    """Whether this attempt replaces an interrupted one of the same step.

    A recovered successor is a fresh attempt, so it launches on the installed
    runtime rather than the image its interrupted predecessor ran.
    """

    step = request.step_execution
    return step is not None and step.reason == RECOVERED_SUCCESSOR_REASON


__all__ = [
    "RECOVERED_SUCCESSOR_REASON",
    "SAVED_WORKSPACE_RESTORE_KEYS",
    "compact_saved_workspace",
    "gate_interrupted_result",
    "is_interruption",
    "is_recovered_successor",
]
