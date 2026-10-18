"""Decoder for the retired checkpoint-Resume admission decision.

Linked failed-step recovery executions carried this frozen decision in
``workflow.resume``. Nothing creates those executions any more: a failed run
is retried in place by resetting its Temporal history (``retry_from_step``).
The model stays only so the run workflow's recovery input still validates
until that path is removed from ``MoonMind.UserWorkflow``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from moonmind.workflows.executions.runtime_capabilities import RuntimeExecutionCapabilities

PromotionState = Literal[
    "disabled", "shadow_capture", "shadow_restore", "internal", "limited",
    "broad", "ga", "paused",
]


class CheckpointResumeReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    runtime_id: str = Field(alias="runtimeId")
    deployment_generation: str = Field(alias="deploymentGeneration")
    capture_route_ready: bool = Field(alias="captureRouteReady")
    restore_route_ready: bool = Field(alias="restoreRouteReady")
    artifact_store_ready: bool = Field(alias="artifactStoreReady")
    managed_run_store_ready: bool = Field(alias="managedRunStoreReady")
    capability_set_version: str = Field(alias="capabilitySetVersion")
    capability_digest: str = Field(alias="capabilityDigest")
    checked_at: datetime = Field(alias="checkedAt")


class AdmittedCheckpointResumeDecision(BaseModel):
    """Replay-safe snapshot a linked recovery execution was started with."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    admitted: bool
    reason_code: str = Field(alias="reasonCode")
    promotion_state: PromotionState = Field(alias="promotionState")
    runtime_capabilities: RuntimeExecutionCapabilities = Field(alias="runtimeCapabilities")
    readiness: CheckpointResumeReadiness
    checkpoint_kind: str = Field(alias="checkpointKind")
    checkpoint_boundary: str = Field(alias="checkpointBoundary")
    resume_phase: str = Field(alias="resumePhase")
    capture_activity: str | None = Field(alias="captureActivity")
    restore_activity: str | None = Field(alias="restoreActivity")
