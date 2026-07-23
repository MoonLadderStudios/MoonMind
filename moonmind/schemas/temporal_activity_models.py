"""Pydantic models for Temporal activity boundaries.

This module centralizes typed input/output models for Temporal activities,
ensuring strict boundaries and safe serialization of binary fields.

Phase 1 Policy Standards:
1. All new activity inputs should use Pydantic v2 models.
2. Activities should take a single structured argument (the input model)
   to keep stubs and `execute_activity` call sites symmetrical.
3. Avoid raw `bytes` in dicts. Use `Base64Bytes` (or explicitly encode
   to base64/utf-8 strings) so the JSON serializer does not accidentally
   create a `list[int]`.
"""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    model_validator,
)

from .temporal_artifact_models import ArtifactRefModel

def _decode_b64(v: str | bytes | list[int]) -> bytes:
    """Safely decode base64 strings, raw bytes, or legacy list[int] to bytes.

    The list[int] fallback supports in-flight histories where the Temporal JSON
    serializer previously encoded raw bytes as an integer array.
    """
    if isinstance(v, bytes):
        return v
    if isinstance(v, list):
        # Explicitly validate legacy list[int] input to produce clear errors instead
        # of relying on the lower-level exceptions from bytes(v).
        validated: list[int] = []
        for idx, item in enumerate(v):
            # Reject non-ints (including bools) and out-of-range values.
            if (
                not isinstance(item, int)
                or isinstance(item, bool)
                or not 0 <= item <= 255
            ):
                raise ValueError(
                    "Expected list[int] with values in range 0-255; "
                    f"found invalid element at index {idx}: {item!r}"
                )
            validated.append(item)
        return bytes(validated)
    if isinstance(v, str):
        try:
            return base64.b64decode(v, validate=True)
        except binascii.Error:
            # Fallback to UTF-8 encoded string for legacy compatibility
            # where string payloads were passed in cleartext
            return v.encode("utf-8")
    raise ValueError(f"Expected base64 string, bytes, or list[int]; got {type(v)}")

def _encode_b64(b: bytes) -> str:
    """Encode bytes to base64 string for safe JSON serialization."""
    return base64.b64encode(b).decode("ascii")

# A specialized type for binary fields at the Temporal activity boundary.
# It guarantees that Pydantic will serialize the bytes to a base64 string,
# avoiding the 'accidental list of ints' issue with the default Temporal JSON codec.
Base64Bytes = Annotated[
    bytes,
    PlainValidator(_decode_b64),
    PlainSerializer(_encode_b64, return_type=str),
]

class ArtifactReadInput(BaseModel):
    """Input parameters for the artifact.read activity."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_ref: str | ArtifactRefModel = Field(
        ...,
        description="The artifact ID or an artifact reference dict/model.",
    )
    principal: str = Field(..., description="The principal requesting the read.")

class ArtifactReadOutput(BaseModel):
    """Output payload from the artifact.read activity."""

    model_config = ConfigDict(populate_by_name=True)

    payload: Base64Bytes = Field(
        ...,
        description="The binary payload returned as base64 on the wire.",
    )

class ArtifactWriteCompleteInput(BaseModel):
    """Input parameters for the artifact.write_complete activity."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_id: str = Field(..., description="The ID of the artifact to complete.")
    principal: str = Field(..., description="The principal completing the write.")
    payload: Base64Bytes = Field(
        ...,
        description="The binary payload to write, serialized as base64 on the wire.",
    )
    content_type: str | None = Field(
        default=None,
        description="The optional MIME type of the content.",
    )

class PlanGenerateInput(BaseModel):
    """Input parameters for the plan.generate activity."""

    model_config = ConfigDict(populate_by_name=True)

    principal: str = Field(
        ..., description="The principal requesting the plan generation."
    )
    inputs_ref: str | ArtifactRefModel | None = Field(
        default=None,
        description="The artifact ID or an artifact reference dict/model for inputs.",
    )
    parameters: dict[str, Any] | None = Field(
        default=None,
        description="Optional parameters for plan generation.",
    )
    registry_snapshot_ref: str | ArtifactRefModel | None = Field(
        default=None,
        description="The artifact ID or reference for the skill registry snapshot.",
    )
    execution_ref: dict[str, Any] | None = Field(
        default=None,
        description="Execution linkage used by Temporal artifacts.",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Optional idempotency key.",
    )

class PlanGenerateOutput(BaseModel):
    """Output payload from the plan.generate activity."""

    model_config = ConfigDict(populate_by_name=True)

    plan_ref: ArtifactRefModel = Field(
        ...,
        description="The artifact reference to the generated plan.",
    )

class DependencyStatusSnapshotInput(BaseModel):
    """Input parameters for execution.dependency_status_snapshot."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_ids: list[str] = Field(default_factory=list, alias="workflowIds")


class ExecutionTerminalStateInput(BaseModel):
    """Input parameters for execution.record_terminal_state."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    state: Literal["completed", "failed", "canceled", "no_commit", "no_changes"] = (
        Field(..., alias="state")
    )
    close_status: Literal[
        "completed",
        "failed",
        "canceled",
        "terminated",
        "timed_out",
    ] | None = Field(None, alias="closeStatus")
    summary: str | None = Field(None, alias="summary")
    finish_outcome_code: str | None = Field(None, alias="finishOutcomeCode")
    finish_outcome_stage: str | None = Field(None, alias="finishOutcomeStage")
    finish_outcome_reason: str | None = Field(None, alias="finishOutcomeReason")
    finish_summary: dict[str, Any] | None = Field(None, alias="finishSummary")
    error_category: Literal[
        "user_error",
        "integration_error",
        "execution_error",
        "system_error",
    ] | None = Field(None, alias="errorCategory")


class ExternalAgentRunInput(BaseModel):
    """Public input for external provider run status/fetch/cancel activities."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    run_id: str = Field(
        ...,
        alias="runId",
        validation_alias=AliasChoices("runId", "run_id", "externalId", "external_id"),
        min_length=1,
    )

    @model_validator(mode="after")
    def _normalize(self) -> "ExternalAgentRunInput":
        self.run_id = self.run_id.strip()
        if not self.run_id:
            raise ValueError("runId must be nonblank")
        return self

class AgentRuntimeStatusInput(BaseModel):
    """Public input for agent_runtime.status."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    run_id: str = Field(
        ...,
        alias="runId",
        validation_alias=AliasChoices("runId", "run_id"),
        min_length=1,
    )
    agent_id: str = Field(
        default="managed",
        alias="agentId",
        validation_alias=AliasChoices("agentId", "agent_id", "agent"),
        min_length=1,
    )

    @model_validator(mode="after")
    def _normalize(self) -> "AgentRuntimeStatusInput":
        self.run_id = self.run_id.strip()
        self.agent_id = self.agent_id.strip() or "managed"
        if not self.run_id:
            raise ValueError("runId must be nonblank")
        return self

class AgentRuntimeFetchResultInput(AgentRuntimeStatusInput):
    """Public input for agent_runtime.fetch_result."""

    publish_mode: Literal["auto", "none", "pr", "branch"] = Field(
        default="none",
        alias="publishMode",
        validation_alias=AliasChoices("publishMode", "publish_mode"),
    )
    commit_message: str | None = Field(
        default=None,
        alias="commitMessage",
        validation_alias=AliasChoices("commitMessage", "commit_message"),
    )
    target_branch: str | None = Field(
        default=None,
        alias="targetBranch",
        validation_alias=AliasChoices("targetBranch", "target_branch"),
    )
    head_branch: str | None = Field(
        default=None,
        alias="headBranch",
        validation_alias=AliasChoices("headBranch", "head_branch"),
    )
    pr_resolver_expected: bool = Field(
        default=False,
        alias="prResolverExpected",
        validation_alias=AliasChoices("prResolverExpected", "pr_resolver_expected"),
    )
    pr_resolver_merge_gate_owned: bool = Field(
        default=False,
        alias="prResolverMergeGateOwned",
        validation_alias=AliasChoices(
            "prResolverMergeGateOwned",
            "pr_resolver_merge_gate_owned",
        ),
    )
    terminal_checkpoint_publication_enabled: bool = Field(
        default=False,
        alias="terminalCheckpointPublicationEnabled",
        validation_alias=AliasChoices(
            "terminalCheckpointPublicationEnabled",
            "terminal_checkpoint_publication_enabled",
        ),
    )
    no_remote_writes: bool = Field(False, alias="noRemoteWrites")
    read_only: bool = Field(False, alias="readOnly")
    dry_run: bool = Field(False, alias="dryRun")
    workspace_authoritative: bool = Field(True, alias="workspaceAuthoritative")
    terminal_checkpoint_capability_supported: bool = Field(
        True, alias="terminalCheckpointCapabilitySupported"
    )

    @model_validator(mode="after")
    def _normalize_fetch(self) -> "AgentRuntimeFetchResultInput":
        for field_name in ("commit_message", "target_branch", "head_branch"):
            value = getattr(self, field_name)
            if value is not None:
                normalized = value.strip()
                setattr(self, field_name, normalized or None)
        return self


class AgentRuntimeTerminalCheckpointInput(AgentRuntimeStatusInput):
    """Bounded request for best-effort controlled-failure publication."""

    failure_class: Literal[
        "user_error", "execution_error", "integration_error", "timed_out"
    ] = Field(..., alias="failureClass")
    target_branch: str | None = Field(None, alias="targetBranch")
    existing_branch: str | None = Field(None, alias="existingBranch")
    existing_head_sha: str | None = Field(None, alias="existingHeadSha")
    existing_pr_url: str | None = Field(None, alias="existingPrUrl")
    no_remote_writes: bool = Field(False, alias="noRemoteWrites")
    publication_enabled: bool = Field(True, alias="publicationEnabled")
    read_only: bool = Field(False, alias="readOnly")
    dry_run: bool = Field(False, alias="dryRun")
    workspace_authoritative: bool = Field(True, alias="workspaceAuthoritative")
    runtime_capability_supported: bool = Field(
        True, alias="runtimeCapabilitySupported"
    )
    source: Literal["live_workspace", "checkpoint_restore", "provider_native"] = (
        "live_workspace"
    )
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)

    @model_validator(mode="after")
    def _normalize_terminal_checkpoint(self) -> "AgentRuntimeTerminalCheckpointInput":
        for field_name in (
            "target_branch",
            "existing_branch",
            "existing_head_sha",
            "existing_pr_url",
            "idempotency_key",
        ):
            value = getattr(self, field_name)
            if isinstance(value, str):
                setattr(self, field_name, value.strip() or None)
        if not self.idempotency_key:
            raise ValueError("idempotencyKey must be nonblank")
        return self


class AgentRuntimeTerminalCheckpointResult(BaseModel):
    """Typed terminal-publication evidence; never changes primary failure."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    intent: Literal["terminal_checkpoint"] = "terminal_checkpoint"
    status: Literal[
        "pushed",
        "already_published",
        "no_changes",
        "skipped",
        "failed",
    ]
    reason_code: str = Field(..., alias="reasonCode")
    source: Literal["live_workspace", "checkpoint_restore", "provider_native"]
    attempted: bool
    commit_created: bool = Field(False, alias="commitCreated")
    branch_pushed: bool = Field(False, alias="branchPushed")
    branch_name: str | None = Field(None, alias="branchName")
    branch_url: str | None = Field(None, alias="branchUrl")
    head_sha: str | None = Field(None, alias="headSha")
    base_branch: str | None = Field(None, alias="baseBranch")
    pr_url: str | None = Field(None, alias="prUrl")
    remote_verified: bool = Field(False, alias="remoteVerified")
    idempotency_key: str = Field(..., alias="idempotencyKey")
    evidence_ref: str | None = Field(None, alias="evidenceRef")
    error: str | None = None


class AcceptedRepositoryEvidence(BaseModel):
    """Authoritative repository state emitted by the managed push boundary."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["accepted-repository-evidence/v1"] = Field(
        "accepted-repository-evidence/v1", alias="schemaVersion"
    )
    push_status: Literal["pushed", "no_commits"] = Field(..., alias="pushStatus")
    branch: str = Field(..., min_length=1)
    base_branch: str = Field(..., alias="baseBranch", min_length=1)
    head_sha: str = Field(..., alias="headSha", min_length=1)
    commits_ahead_of_base: int = Field(..., alias="commitsAheadOfBase", ge=0)
    repository_changed: bool = Field(..., alias="repositoryChanged")
    publication_authorized: Literal[True] = Field(
        True, alias="publicationAuthorized"
    )
    candidate_contaminated: Literal[False] = Field(
        False, alias="candidateContaminated"
    )
    remote_verified: Literal[True] = Field(True, alias="remoteVerified")
    authority: Literal["agent_runtime.fetch_result"] = "agent_runtime.fetch_result"

    @model_validator(mode="after")
    def _validate_push_evidence(self) -> "AcceptedRepositoryEvidence":
        self.branch = self.branch.strip()
        self.base_branch = self.base_branch.strip()
        self.head_sha = self.head_sha.strip()
        if not self.branch or not self.base_branch or not self.head_sha:
            raise ValueError("accepted repository evidence requires git identities")
        expected_changed = self.push_status == "pushed"
        if self.repository_changed != expected_changed:
            raise ValueError("push status and repositoryChanged disagree")
        if expected_changed and self.commits_ahead_of_base < 1:
            raise ValueError("pushed repository evidence requires commits over base")
        if not expected_changed and self.commits_ahead_of_base != 0:
            raise ValueError("no-commit repository evidence cannot be ahead of base")
        return self


class ResiliencePolicyCompileInput(BaseModel):
    """Input for the ``resilience.compile_policy`` activity (MM-880).

    Carries the per-run variable bits (identity, compile timestamp, cost
    attribution dimensions, and the resolved provider cooldown) so the activity
    can capture the remaining resilience values (attempts, timeouts, outbound
    scanning, observability, checkpoints, idempotency) from worker-side config
    once, at run start, into a deterministic versioned envelope.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workflow_id: str | None = Field(None, alias="workflowId")
    run_id: str | None = Field(None, alias="runId")
    policy_version: int = Field(1, alias="policyVersion", ge=1)
    compiled_at: str | None = Field(
        None,
        alias="compiledAt",
        description="ISO-8601 compile timestamp supplied by the workflow clock.",
    )
    runtime_id: str | None = Field(None, alias="runtimeId")
    provider_profile_id: str | None = Field(None, alias="providerProfileId")
    cooldown_after_429_seconds: int | None = Field(
        None,
        alias="cooldownAfter429Seconds",
        ge=0,
        description="Resolved provider cooldown captured from the profile snapshot.",
    )
    rate_limit_policy: dict[str, Any] = Field(
        default_factory=dict,
        alias="rateLimitPolicy",
        description="Resolved compact rate-limit policy captured from the profile snapshot.",
    )
    rate_limit_policy_ref: str | None = Field(None, alias="rateLimitPolicyRef")
    model: str | None = Field(None, alias="model")
    effort: str | None = Field(None, alias="effort")
    cost_center: str | None = Field(None, alias="costCenter")
    budget_ref: str | None = Field(None, alias="budgetRef")


class AgentRuntimeCancelInput(BaseModel):
    """Public input for agent_runtime.cancel."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    run_id: str = Field(
        ...,
        alias="runId",
        validation_alias=AliasChoices("runId", "run_id"),
        min_length=1,
    )
    agent_kind: Literal["managed", "external", "unknown"] = Field(
        default="unknown",
        alias="agentKind",
        validation_alias=AliasChoices("agentKind", "agent_kind"),
    )
    agent_id: str | None = Field(
        default=None,
        alias="agentId",
        validation_alias=AliasChoices("agentId", "agent_id", "agent"),
    )

    @model_validator(mode="after")
    def _normalize_cancel(self) -> "AgentRuntimeCancelInput":
        self.run_id = self.run_id.strip()
        if not self.run_id:
            raise ValueError("runId must be nonblank")
        if self.agent_id is not None:
            self.agent_id = self.agent_id.strip() or None
        return self
