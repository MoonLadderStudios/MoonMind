"""Schemas for bounded manifest-ingest runtime APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


ManifestFailurePolicy = Literal["fail_fast", "continue_and_report", "best_effort"]
ManifestLifecycleState = Literal[
    "initializing",
    "executing",
    "finalizing",
    "succeeded",
    "failed",
    "canceled",
]
ManifestNodeState = Literal[
    "pending",
    "ready",
    "running",
    "succeeded",
    "failed",
    "canceled",
]
ManifestUpdateMode = Literal["REPLACE_FUTURE", "APPEND"]


class RequestedByModel(BaseModel):
    """Immutable requested-by identity propagated through manifest ingest."""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["user", "system"] = Field(..., alias="type")
    id: str = Field(..., alias="id")


class ManifestExecutionPolicyModel(BaseModel):
    """Bounded execution policy payload exposed on manifest ingest detail APIs."""

    model_config = ConfigDict(populate_by_name=True)

    failure_policy: ManifestFailurePolicy = Field(
        "fail_fast",
        alias="failurePolicy",
    )
    max_concurrency: int = Field(50, alias="maxConcurrency", ge=1, le=500)
    concurrency_defaulted: bool = Field(False, alias="concurrencyDefaulted")
    default_task_queues: Optional[dict[str, str]] = Field(
        None,
        alias="defaultTaskQueues",
    )
    schedule_batch_size: Optional[int] = Field(None, alias="scheduleBatchSize", ge=1)


class ManifestNodeCountsModel(BaseModel):
    """Aggregate node counts returned by manifest ingest status APIs."""

    model_config = ConfigDict(populate_by_name=True)

    pending: int = Field(0, alias="pending", ge=0)
    ready: int = Field(0, alias="ready", ge=0)
    running: int = Field(0, alias="running", ge=0)
    succeeded: int = Field(0, alias="succeeded", ge=0)
    failed: int = Field(0, alias="failed", ge=0)
    canceled: int = Field(0, alias="canceled", ge=0)


class ManifestStatusSnapshotModel(BaseModel):
    """Bounded manifest ingest status snapshot."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    run_id: Optional[str] = Field(None, alias="runId")
    state: ManifestLifecycleState = Field(..., alias="state")
    phase: str = Field(..., alias="phase")
    paused: bool = Field(False, alias="paused")
    max_concurrency: int = Field(..., alias="maxConcurrency", ge=1, le=500)
    failure_policy: ManifestFailurePolicy = Field(..., alias="failurePolicy")
    counts: ManifestNodeCountsModel = Field(
        default_factory=ManifestNodeCountsModel,
        alias="counts",
    )
    requested_by: Optional[RequestedByModel] = Field(None, alias="requestedBy")
    execution_policy: Optional[ManifestExecutionPolicyModel] = Field(
        None,
        alias="executionPolicy",
    )
    manifest_artifact_ref: Optional[str] = Field(None, alias="manifestArtifactRef")
    plan_artifact_ref: Optional[str] = Field(None, alias="planArtifactRef")
    summary_artifact_ref: Optional[str] = Field(None, alias="summaryArtifactRef")
    run_index_artifact_ref: Optional[str] = Field(None, alias="runIndexArtifactRef")
    checkpoint_artifact_ref: Optional[str] = Field(
        None,
        alias="checkpointArtifactRef",
    )
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")


class ManifestNodeModel(BaseModel):
    """One bounded manifest node summary row."""

    model_config = ConfigDict(populate_by_name=True)

    node_id: str = Field(..., alias="nodeId")
    state: ManifestNodeState = Field(..., alias="state")
    title: Optional[str] = Field(None, alias="title")
    workflow_type: str = Field("MoonMind.Run", alias="workflowType")
    child_workflow_id: Optional[str] = Field(None, alias="childWorkflowId")
    child_run_id: Optional[str] = Field(None, alias="childRunId")
    result_artifact_ref: Optional[str] = Field(None, alias="resultArtifactRef")
    requested_by: Optional[RequestedByModel] = Field(None, alias="requestedBy")
    started_at: Optional[datetime] = Field(None, alias="startedAt")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")


class ManifestPlanNodeModel(BaseModel):
    """Normalized compiled-plan node used for artifact-backed orchestration."""

    model_config = ConfigDict(populate_by_name=True)

    node_id: str = Field(..., alias="nodeId")
    title: str = Field(..., alias="title")
    source_type: str = Field(..., alias="sourceType")
    source_id: str = Field(..., alias="sourceId")
    required_capabilities: list[str] = Field(
        default_factory=list,
        alias="requiredCapabilities",
    )
    runtime_hints: dict[str, Any] = Field(default_factory=dict, alias="runtimeHints")
    dependencies: list[str] = Field(default_factory=list, alias="dependencies")


class CompiledManifestPlanModel(BaseModel):
    """Canonical manifest-ingest plan artifact payload."""

    model_config = ConfigDict(populate_by_name=True)

    manifest_ref: str = Field(..., alias="manifestRef")
    manifest_digest: str = Field(..., alias="manifestDigest")
    action: Literal["plan", "run"] = Field("run", alias="action")
    requested_by: RequestedByModel = Field(..., alias="requestedBy")
    execution_policy: ManifestExecutionPolicyModel = Field(
        ...,
        alias="executionPolicy",
    )
    nodes: list[ManifestPlanNodeModel] = Field(default_factory=list, alias="nodes")
    edges: list[dict[str, str]] = Field(default_factory=list, alias="edges")
    required_capabilities: list[str] = Field(
        default_factory=list,
        alias="requiredCapabilities",
    )
    options: dict[str, Any] = Field(default_factory=dict, alias="options")


class ManifestNodePageModel(BaseModel):
    """Cursor-paginated manifest node page."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[ManifestNodeModel] = Field(default_factory=list, alias="items")
    next_cursor: Optional[str] = Field(None, alias="nextCursor")
    count: int = Field(0, alias="count", ge=0)


class ManifestRunIndexEntryModel(BaseModel):
    """Lineage row for the canonical run-index artifact."""

    model_config = ConfigDict(populate_by_name=True)

    node_id: str = Field(..., alias="nodeId")
    state: ManifestNodeState = Field(..., alias="state")
    child_workflow_id: Optional[str] = Field(None, alias="childWorkflowId")
    child_run_id: Optional[str] = Field(None, alias="childRunId")
    workflow_type: str = Field("MoonMind.Run", alias="workflowType")
    parent_close_policy: str = Field("REQUEST_CANCEL", alias="parentClosePolicy")
    result_artifact_ref: Optional[str] = Field(None, alias="resultArtifactRef")


class ManifestRunIndexModel(BaseModel):
    """Canonical run-index artifact payload."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    manifest_ref: str = Field(..., alias="manifestRef")
    counts: ManifestNodeCountsModel = Field(..., alias="counts")
    items: list[ManifestRunIndexEntryModel] = Field(default_factory=list, alias="items")


class ManifestIngestSummaryModel(BaseModel):
    """Bounded summary artifact payload for one ingest execution."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    state: ManifestLifecycleState = Field(..., alias="state")
    phase: str = Field(..., alias="phase")
    manifest_ref: str = Field(..., alias="manifestRef")
    plan_ref: Optional[str] = Field(None, alias="planRef")
    counts: ManifestNodeCountsModel = Field(..., alias="counts")
    failed_node_ids: list[str] = Field(default_factory=list, alias="failedNodeIds")


class ManifestUpdateResultModel(BaseModel):
    """Structured details about one manifest-specific update."""

    model_config = ConfigDict(populate_by_name=True)

    accepted_node_ids: list[str] = Field(default_factory=list, alias="acceptedNodeIds")
    rejected_node_ids: list[str] = Field(default_factory=list, alias="rejectedNodeIds")


class ManifestNodeMutationRequestModel(BaseModel):
    """Validation helper for manifest node-targeting updates."""

    model_config = ConfigDict(populate_by_name=True)

    node_ids: list[str] = Field(default_factory=list, alias="nodeIds")

    @field_validator("node_ids")
    @classmethod
    def _validate_node_ids(cls, value: list[str]) -> list[str]:
        normalized = [str(item).strip() for item in value if str(item).strip()]
        if not normalized:
            raise ValueError("nodeIds must include at least one node identifier")
        return list(dict.fromkeys(normalized))


class ManifestUpdateManifestRequestModel(BaseModel):
    """Validation helper for manifest replacement or append updates."""

    model_config = ConfigDict(populate_by_name=True)

    new_manifest_artifact_ref: str = Field(..., alias="newManifestArtifactRef")
    mode: ManifestUpdateMode = Field(..., alias="mode")

    @field_validator("new_manifest_artifact_ref")
    @classmethod
    def _validate_new_manifest_ref(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("newManifestArtifactRef is required")
        return normalized


def manifest_node_counts_from_nodes(
    nodes: list[ManifestNodeModel] | list[dict[str, Any]],
) -> ManifestNodeCountsModel:
    """Derive bounded node counts from a node list payload."""

    counts = ManifestNodeCountsModel()
    for item in nodes:
        state = (
            item.state
            if isinstance(item, ManifestNodeModel)
            else str(item.get("state", "")).strip().lower()
        )
        if state == "pending":
            counts.pending += 1
        elif state == "ready":
            counts.ready += 1
        elif state == "running":
            counts.running += 1
        elif state == "succeeded":
            counts.succeeded += 1
        elif state == "failed":
            counts.failed += 1
        elif state == "canceled":
            counts.canceled += 1
    return counts
