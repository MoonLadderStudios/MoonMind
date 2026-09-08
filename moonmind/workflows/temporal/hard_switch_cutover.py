"""Canonical Temporal user-workflow start contract.

The MM-730 hard switch is complete: new user Workflow Executions always start
as ``MoonMind.UserWorkflow`` on the configured v2 Task Queue. Startup resolves
that contract from ``TemporalSettings`` queue values alone — it no longer
depends on the historical cutover record or release-note prose
(MoonLadderStudios/MoonMind#3951).

Replay of retained histories is owned by the worker topology: the workflow
fleet keeps polling the legacy ``TEMPORAL_WORKFLOW_TASK_QUEUE`` alongside the
start queue (see ``activity_catalog.get_workflow_poll_task_queues``) until
pre-cutover histories drain. Historical ``no_changes``/``NO_CHANGES`` outcomes
stay decodable through ``moonmind.statuses.compat``, which is the retained
historical-read loader, not a new-write alias.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from moonmind.config.settings import TemporalSettings


#: Historical workflow type replaced by the MM-730 hard switch. Never served
#: by current worker builds; retained here so retirement audits can name what
#: was removed without grepping history.
LEGACY_USER_WORKFLOW_TYPE = "MoonMind.Run"
RENAMED_USER_WORKFLOW_TYPE = "MoonMind.UserWorkflow"
RENAMED_USER_WORKFLOW_CONTRACT = "renamed_contract"

_VALID_CONTRACT_MODES = {RENAMED_USER_WORKFLOW_CONTRACT}


class HardSwitchCutoverError(ValueError):
    """Raised when the user-workflow start contract is misconfigured."""


@dataclass(frozen=True, slots=True)
class UserWorkflowStartContract:
    """Temporal start contract for user Workflow Executions."""

    workflow_type: str
    task_queue: str
    contract_mode: str


def normalize_user_workflow_contract_mode(value: Any) -> str:
    """Normalize the configured user-workflow contract mode."""

    normalized = str(value or RENAMED_USER_WORKFLOW_CONTRACT).strip().lower()
    if normalized not in _VALID_CONTRACT_MODES:
        raise HardSwitchCutoverError(
            "TEMPORAL_USER_WORKFLOW_CONTRACT_MODE must be one of "
            f"{', '.join(sorted(_VALID_CONTRACT_MODES))}"
        )
    return normalized


def resolve_user_workflow_start_contract(
    temporal_settings: TemporalSettings,
) -> UserWorkflowStartContract:
    """Resolve the Temporal workflow type and queue for new user starts.

    The contract is derived from configured queue names only. No cutover
    record or release-note file is consulted.
    """

    normalize_user_workflow_contract_mode(
        temporal_settings.user_workflow_contract_mode
    )
    task_queue = str(temporal_settings.user_workflow_v2_task_queue).strip()
    if not task_queue:
        raise HardSwitchCutoverError(
            "TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE is required for "
            "renamed_contract mode"
        )
    if task_queue == str(temporal_settings.workflow_task_queue).strip():
        raise HardSwitchCutoverError(
            "TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE must be distinct from "
            "TEMPORAL_WORKFLOW_TASK_QUEUE for renamed_contract mode"
        )
    return UserWorkflowStartContract(
        workflow_type=RENAMED_USER_WORKFLOW_TYPE,
        task_queue=task_queue,
        contract_mode=RENAMED_USER_WORKFLOW_CONTRACT,
    )


def registered_user_workflow_type(temporal_settings: TemporalSettings) -> str:
    """Return the single user workflow type this worker build should serve."""

    return resolve_user_workflow_start_contract(temporal_settings).workflow_type
