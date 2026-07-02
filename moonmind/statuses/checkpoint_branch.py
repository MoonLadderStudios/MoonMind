"""Checkpoint branch lifecycle status domain values."""

from __future__ import annotations

import enum
from typing import Literal


class CheckpointBranchState(str, enum.Enum):
    """Canonical lifecycle states for product-level checkpoint branches."""

    CREATED = "created"
    PREPARING = "preparing"
    ACTIVE = "active"
    BLOCKED = "blocked"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    PROMOTABLE = "promotable"
    PROMOTED = "promoted"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"


class CheckpointBranchTurnState(str, enum.Enum):
    """Canonical lifecycle states for one checkpoint branch turn."""

    CREATED = "created"
    PREPARING = "preparing"
    RUNNING = "running"
    CHECKING = "checking"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELED = "canceled"
    SUPERSEDED = "superseded"


CheckpointBranchStateValue = Literal[
    "created",
    "preparing",
    "active",
    "blocked",
    "failed",
    "succeeded",
    "promotable",
    "promoted",
    "archived",
    "superseded",
]
CheckpointBranchTurnStateValue = Literal[
    "created",
    "preparing",
    "running",
    "checking",
    "succeeded",
    "failed",
    "blocked",
    "canceled",
    "superseded",
]

CHECKPOINT_BRANCH_STATE_VALUES: frozenset[str] = frozenset(
    item.value for item in CheckpointBranchState
)
CHECKPOINT_BRANCH_TURN_STATE_VALUES: frozenset[str] = frozenset(
    item.value for item in CheckpointBranchTurnState
)

__all__ = [
    "CHECKPOINT_BRANCH_STATE_VALUES",
    "CHECKPOINT_BRANCH_TURN_STATE_VALUES",
    "CheckpointBranchState",
    "CheckpointBranchStateValue",
    "CheckpointBranchTurnState",
    "CheckpointBranchTurnStateValue",
]
