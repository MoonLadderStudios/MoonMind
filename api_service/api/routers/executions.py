"""Temporal execution lifecycle API router."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import quote, urlsplit
from uuid import uuid4

logger = logging.getLogger(__name__)

from functools import lru_cache

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import ListSearchAttributesRequest
from temporalio.client import Client
from temporalio.service import RPCError

from api_service.api.dependencies import resolve_template_scope_for_user
from api_service.auth_providers import get_current_user
from api_service.core import sync as execution_sync
from api_service.db.base import get_async_session
from api_service.db.models import (
    AgentSkillDefinition,
    ManagedAgentProviderProfile,
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
    TemporalExecutionRecord,
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalArtifactStatus,
    TemporalArtifactRetentionClass,
    User,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchArtifact,
    WorkflowCheckpointBranchOperation,
    WorkflowCheckpointBranchTurn,
)
from api_service.services.checkpoint_branches import prepare_checkpoint_branch_workspace
from api_service.services.control_stop_continuation import (
    SqlControlStopContinuationRepository,
    TemporalControlStopContinuationStarter,
)
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
from moonmind.config.settings import settings
from moonmind.statuses.compat import (
    canonicalize_finish_outcome_code_alias,
    normalize_no_commit_finish_summary,
)
from moonmind.utils.metrics import get_metrics_emitter
from moonmind.workflows.report_output import normalize_report_output_primary_path
from moonmind.workflows.executions.preset_expansion import (
    expand_preset_for_child_run,
    has_unexpanded_task_template,
)
from moonmind.workflows.executions.routing import _coerce_bool
from moonmind.workflows.executions.title_derivation import (
    is_generic_title,
    synthesize_workflow_title,
)
from moonmind.runtime_intent import (
    RuntimeIntentValidationError,
    validate_runtime_tier_intent,
)
from moonmind.schemas.manifest_ingest_models import (
    ManifestNodePageModel,
    ManifestStatusSnapshotModel,
)
from moonmind.schemas.temporal_artifact_models import ArtifactRefModel
from moonmind.utils.logging import redact_sensitive_text
from moonmind.schemas.temporal_models import (
    CancelExecutionRequest,
    ConfigureIntegrationMonitoringRequest,
    CreateExecutionRequest,
    ExecutionActionCapabilityModel,
    ExecutionDependencySummaryModel,
    ExecutionDebugFieldsModel,
    ExecutionFacetItemModel,
    ExecutionFacetResponse,
    ExecutionMergeAutomationModel,
    ExecutionMergeAutomationResolverChildModel,
    ExecutionProjectionDiagnosticModel,
    ExecutionListResponse,
    ExecutionListItemModel,
    ExecutionMetricsCostModel,
    ExecutionMetricsDurationModel,
    ExecutionMetricsResponse,
    ExecutionModel,
    ExecutionProgressModel,
    ExecutionReportProjectionModel,
    ExecutionRefreshEnvelope,
    ExecutionRelatedRunModel,
    ExecutionResumeSummaryModel,
    ExecutionSkillEvidenceSummaryModel,
    ExecutionSkillLifecycleIntentModel,
    ExecutionSkillProvenanceModel,
    ExecutionSkillRuntimeModel,
    FailedRunRecoveryManifestModel,
    RecoverExecutionResponse,
    RecoverFromFailedStepRequest,
    RecoverFromFailedStepResponse,
    RecoverFromSelectedStepRequest,
    RecoveryEligibilityDiagnosticModel,
    WorkflowInputSnapshotDescriptorModel,
    PollIntegrationRequest,
    RescheduleExecutionRequest,
    ScheduleCreatedResponse,
    ScheduleParameters,
    SignalExecutionRequest,
    StepExecutionDetailModel,
    StepExecutionListModel,
    StepExecutionManifestModel,
    StepExecutionProjectionModel,
    StepLedgerSnapshotModel,
    UpdateExecutionRequest,
    UpdateExecutionResponse,
    AGENT_RUN_ID_MEMO_KEYS,
    AGENT_RUN_ID_PARAM_KEYS,
    AGENT_RUN_ID_SEARCH_ATTR_KEYS,
    normalize_dependency_ids,
)
from moonmind.schemas.workflow_recovery_models import WorkflowRecoveryTargetModel
from moonmind.schemas.checkpoint_branch_models import (
    CheckpointBranchArchiveRequest,
    CheckpointBranchApiSourceModel,
    CheckpointBranchCompareResponse,
    CheckpointBranchContinueRequest,
    CheckpointBranchCreateRequest,
    CheckpointBranchForkRequest,
    CheckpointBranchInstructionsModel,
    CheckpointBranchListResponse,
    CheckpointBranchModel,
    CheckpointBranchPromoteRequest,
    CheckpointBranchPublishRequest,
    CheckpointBranchTurnLaunchRequest,
    CheckpointBranchTurnListResponse,
    CheckpointBranchTurnModel,
    CheckpointListResponse,
    CheckpointSummaryModel,
)
from moonmind.workflows.checkpoint_branches import (
    CheckpointBranchGitBindingError,
    CheckpointBranchContextBundleError,
    build_checkpoint_branch_turn_context_bundle,
)
from moonmind.workflows.temporal import (
    TemporalExecutionNotFoundError,
    TemporalExecutionRecoveryCheckpointError,
    TemporalExecutionService,
    TemporalExecutionValidationError,
    build_manifest_status_snapshot,
)
from moonmind.workflows.temporal.step_ledger import build_initial_step_rows
from moonmind.workflows.temporal.title_search import tokenize_title
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    build_artifact_ref,
)
from moonmind.workflows.temporal.report_artifacts import build_report_projection_summary
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.client import TemporalClientAdapter, query_workflow
from moonmind.workflows.temporal.hard_switch_cutover import (
    resolve_user_workflow_start_contract,
)
from moonmind.workflows.executions.model_resolver import (
    ResolvedModelEffort,
    resolve_effective_model,
    resolve_model_effort,
)
from moonmind.workflows.executions.preset_goal_scheduler import (
    GoalPresetSchedule,
    goal_from_payloads,
    schedule_preset_from_goal,
    workflow_is_already_authored,
)
from moonmind.workflows.executions.runtime_defaults import normalize_runtime_id
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.executions.checkpoint_resume_admission import (
    CheckpointResumeReadiness,
    evaluate_checkpoint_resume_admission,
    rollout_policy_from_settings,
)
from moonmind.workflows.executions.checkpoint_promotion import (
    bounded_checkpoint_metric_tags,
)
from moonmind.workflows.executions.runtime_inheritance import (
    RuntimeInheritanceError,
    apply_inherited_runtime_to_payload,
    resolve_child_runtime_inheritance,
)
from moonmind.workflows.temporal.publication_recovery import (
    PublicationRecoveryContract,
    PublicationRecoveryRolloutPolicy,
    publication_action_eligibility,
    publication_recovery_workflow_id,
)
from moonmind.services.skill_step_inputs import validate_skill_step_inputs
from moonmind.services.control_stop_continuation import (
    admit_control_stop_continuation,
)
from moonmind.workflows.executions.control_stop_continuation import (
    ContinuationBudgetGrant,
    ControlStopContinuationContract,
    ControlStopContinuationError,
)
from api_service.api.execution_principal import (
    execution_principal_dependency,
    resolve_execution_principal,
)
from moonmind.workflows.executions.execution_contract import (
    WorkflowContractError,
    WorkflowInputAttachmentRef,
    WorkflowProposalPolicy,
    WorkflowSkillSelectors,
    allows_repository_publish_for_skill_context,
    build_authoritative_workflow_input_snapshot,
    is_non_repository_side_effect_skill,
    is_self_managed_publish_skill,
    reject_workflow_capability_identity_versions,
    resolve_publish_mode_for_skill,
)
from api_service.api.schemas import CreateJobRequest
from moonmind.workflows import get_temporal_artifact_service

router = APIRouter(prefix="/api/executions", tags=["executions"])
_TEMPORAL_SOURCE = "temporal"
_ALLOWED_OWNER_TYPES = {"user", "system", "service"}
_SUPPORTED_TASK_RUNTIMES = frozenset({
    "codex_cli",
    "claude_code",
    "codex_cloud",
    "jules",
    "omnigent",
    # Legacy aliases accepted and normalized below.
    "codex",
    "claude",
})
_TEMPORAL_SCOPE_QUERIES = {
    "default": 'WorkflowType="MoonMind.UserWorkflow" AND mm_entry="user_workflow"',
}
_DASHBOARD_STATUS_BY_STATE: dict[MoonMindWorkflowState, str] = {
    MoonMindWorkflowState.SCHEDULED: "queued",
    MoonMindWorkflowState.INITIALIZING: "queued",
    MoonMindWorkflowState.WAITING_ON_DEPENDENCIES: "waiting",
    MoonMindWorkflowState.PLANNING: "running",
    MoonMindWorkflowState.AWAITING_SLOT: "queued",
    MoonMindWorkflowState.EXECUTING: "running",
    MoonMindWorkflowState.PROPOSALS: "running",
    MoonMindWorkflowState.AWAITING_EXTERNAL: "awaiting_action",
    MoonMindWorkflowState.FINALIZING: "running",
    MoonMindWorkflowState.NO_COMMIT: "completed",
    MoonMindWorkflowState.COMPLETED: "completed",
    MoonMindWorkflowState.FAILED: "failed",
    MoonMindWorkflowState.CANCELED: "canceled",
}
_MAX_TASK_TITLE_LENGTH = 150
_MAX_TASK_SUMMARY_LENGTH = 180
_TASK_SUMMARY_ELLIPSIS = "..."
_PENDING_REMEDIATION_APPROVAL_STATUSES = frozenset(
    {"awaiting_approval", "approval_required"}
)
_GITHUB_PULL_REQUEST_PATH_PATTERN = re.compile(
    r"^/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+$",
    re.IGNORECASE,
)
_EXECUTION_FILTER_VALUE_LIMIT = 50
_EXECUTION_FILTER_VALUE_MAX_LENGTH = 200
_EXECUTION_TEXT_FILTER_MAX_LENGTH = 200
_EXECUTION_FACET_PAGE_SIZE_LIMIT = 200
_EXECUTION_METRICS_SAMPLE_SIZE_LIMIT = 500
_EXECUTION_COST_KEYS = frozenset(
    {
        "costEstimateUsd",
        "cost_estimate_usd",
        "estimatedCostUsd",
        "estimated_cost_usd",
        "costUsd",
        "cost_usd",
        "totalCostUsd",
        "total_cost_usd",
        "mm_cost_estimate_usd",
        "moonmind.cost_estimate_usd",
    }
)
_EXECUTION_SORT_FIELDS = {
    "workflowId": "WorkflowId",
    "targetRuntime": "mm_target_runtime",
    "targetSkill": "mm_target_skill",
    "repository": "mm_repo",
    "integration": "mm_integration",
    "status": "mm_state",
    "scheduledFor": "mm_scheduled_for",
    "createdAt": "StartTime",
    "closedAt": "CloseTime",
}
_EXECUTION_UPDATED_SORT_BUCKET_SECONDS = 60
_EXECUTION_FACET_ATTRS = {
    "status": "mm_state",
    "targetRuntime": "mm_target_runtime",
    "targetSkill": "mm_target_skill",
    "repository": "mm_repo",
    "integration": "mm_integration",
}
_OPTIONAL_TEMPORAL_SEARCH_ATTRIBUTES = {
    "mm_target_runtime": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
    "mm_target_skill": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
    "mm_title": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
}
_UNSORTABLE_TEMPORAL_SEARCH_ATTRIBUTES = frozenset(
    {
        "mm_target_runtime",
        "mm_target_skill",
    }
)
_OPTIONAL_TEMPORAL_SEARCH_ATTRIBUTES_CACHE_TTL_SECONDS = 300.0
_optional_temporal_search_attributes_cache: dict[
    tuple[str, int], tuple[float, frozenset[str]]
] = {}
_EXECUTION_FILTER_ATTR_BY_ALIAS = {
    "targetRuntime": "mm_target_runtime",
    "targetRuntimeIn": "mm_target_runtime",
    "targetRuntimeNotIn": "mm_target_runtime",
    "targetSkillIn": "mm_target_skill",
    "targetSkillNotIn": "mm_target_skill",
    "titleContains": "mm_title",
}
_EXECUTION_FACET_FILTER_ALIASES = {
    "status": frozenset({"state", "stateIn", "stateNotIn"}),
    "targetRuntime": frozenset(
        {"targetRuntime", "targetRuntimeIn", "targetRuntimeNotIn", "targetRuntimeBlank"}
    ),
    "targetSkill": frozenset({"targetSkillIn", "targetSkillNotIn", "targetSkillBlank"}),
    "repository": frozenset(
        {"repo", "repoExact", "repoIn", "repoNotIn", "repoContains", "repoBlank"}
    ),
    "integration": frozenset({"integration"}),
}
_TEMPORAL_STATUS_VALUES = (
    "scheduled",
    "initializing",
    "waiting_on_dependencies",
    "planning",
    "awaiting_slot",
    "executing",
    "proposals",
    "awaiting_external",
    "finalizing",
    "completed",
    "failed",
    "canceled",
)
_PROGRESS_BUCKET_VALUES = frozenset({"not_started", "in_progress", "complete"})
_PROGRESS_SIGNAL_VALUES = frozenset(
    {
        "executing",
        "awaiting_external",
        "reviewing",
        "has_failed_steps",
        "has_skipped_steps",
        "has_canceled_steps",
    }
)


def _registered_search_attribute_map(response: object) -> dict[str, Any]:
    for attr_name in (
        "custom_attributes",
        "custom_search_attributes",
        "search_attributes",
    ):
        attrs = getattr(response, attr_name, None)
        if isinstance(attrs, Mapping):
            return dict(attrs)
    return {}


def _search_attribute_type_value(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    metadata_type = getattr(raw, "type", None)
    if isinstance(metadata_type, int):
        return metadata_type
    indexed_value_type = getattr(raw, "indexed_value_type", None)
    if isinstance(indexed_value_type, int):
        return indexed_value_type
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized == "keyword":
            return int(IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD)
        if normalized in {"keywordlist", "keyword_list", "keyword list"}:
            return int(IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST)
    return None


async def _detect_optional_temporal_search_attributes(
    client: Client,
) -> frozenset[str]:
    namespace = getattr(client, "namespace", "default")
    operator_service = getattr(client, "operator_service", None)
    if operator_service is None:
        logger.info("Temporal Search Attribute registry check unavailable: no operator service")
        return frozenset()

    cache_key = (namespace, id(operator_service))
    now = asyncio.get_running_loop().time()
    cached = _optional_temporal_search_attributes_cache.get(cache_key)
    if (
        cached is not None
        and now - cached[0] < _OPTIONAL_TEMPORAL_SEARCH_ATTRIBUTES_CACHE_TTL_SECONDS
    ):
        return cached[1]

    try:
        response = await operator_service.list_search_attributes(
            ListSearchAttributesRequest(namespace=namespace)
        )
    except Exception as exc:
        logger.info("Temporal Search Attribute registry check unavailable: %s", exc)
        if cached is not None:
            return cached[1]
        return frozenset()

    attrs = _registered_search_attribute_map(response)
    usable: set[str] = set()
    for name, expected_type in _OPTIONAL_TEMPORAL_SEARCH_ATTRIBUTES.items():
        if _search_attribute_type_value(attrs.get(name)) == int(expected_type):
            usable.add(name)
        elif name in attrs:
            expected_type_name = getattr(expected_type, "name", str(expected_type))
            logger.error(
                "Temporal Search Attribute %s has unexpected type; expected %s.",
                name,
                expected_type_name,
            )
    logger.info(
        "Temporal optional Search Attributes usable: %s",
        ", ".join(sorted(usable)) or "none",
    )
    result = frozenset(usable)
    _optional_temporal_search_attributes_cache[cache_key] = (now, result)
    return result


def _requested_unavailable_filter_aliases(
    request: Request,
    usable_search_attributes: frozenset[str],
) -> frozenset[str]:
    unavailable: set[str] = set()
    for alias, attr_name in _EXECUTION_FILTER_ATTR_BY_ALIAS.items():
        if alias in request.query_params and attr_name not in usable_search_attributes:
            unavailable.add(alias)
    return frozenset(unavailable)
_PROGRESS_FILTER_ALIASES = frozenset(
    {
        "progressPctFrom",
        "progressPctTo",
        "progressBucketIn",
        "progressBucketNotIn",
        "progressSignalIn",
        "progressSignalNotIn",
        "progressStepTitleContains",
        "progressBlank",
    }
)
_NON_TERMINAL_MM_STATES = frozenset(
    {
        "scheduled",
        "initializing",
        "waiting_on_dependencies",
        "planning",
        "awaiting_slot",
        "executing",
        "proposals",
        "awaiting_external",
        "finalizing",
    }
)
_TERMINAL_EXECUTION_STATUSES_BY_MM_STATE = {
    "completed": ("Completed",),
    "failed": ("Failed", "Terminated", "TimedOut"),
    "canceled": ("Canceled",),
}


class RemediationApprovalStateModel(BaseModel):
    requestId: str | None = None
    actionKind: str | None = None
    riskTier: str | None = None
    preconditions: str | None = None
    blastRadius: str | None = None
    decision: str
    decisionActor: str | None = None
    decisionAt: datetime | None = None
    canDecide: bool = False
    auditRef: str | None = None

class RemediationLiveObservationModel(BaseModel):
    status: str | None = None
    label: str | None = None
    sequenceCursor: str | None = None
    reconnectState: str | None = None
    epoch: str | None = None
    fallbackReason: str | None = None

class RemediationLockOutcomeModel(BaseModel):
    state: str | None = None
    holder: str | None = None
    releasedAt: datetime | str | None = None

class RemediationNextActionBaselineModel(BaseModel):
    checkpointRef: str
    workspaceDigest: str
    headVersion: int

class RemediationCheckpointBranchLinkModel(BaseModel):
    workflowId: str
    branchId: str
    branchTurnId: str | None = None
    operation: str | None = None
    idempotencyKey: str | None = None
    checkpointRef: str | None = None
    contextArtifactRef: str | None = None
    loopId: str | None = None
    rootCheckpointRef: str | None = None
    rootWorkspaceDigest: str | None = None
    headCheckpointRef: str | None = None
    headWorkspaceDigest: str | None = None
    headStepExecutionId: str | None = None
    headAttemptOrdinal: int | None = None
    headVersion: int | None = None
    headStatus: str | None = None
    latestVerificationRef: str | None = None
    latestVerificationVerdict: str | None = None
    supersedesCheckpointRef: str | None = None
    remainingWorkRef: str | None = None
    nextActionBaseline: RemediationNextActionBaselineModel | None = None
    createdAt: datetime | str | None = None

    @field_validator(
        "rootCheckpointRef",
        "headCheckpointRef",
        "latestVerificationRef",
        "supersedesCheckpointRef",
        "remainingWorkRef",
    )
    @classmethod
    def remediation_refs_are_artifact_refs(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("artifact://"):
            raise ValueError("remediation head references must be artifact refs")
        return value

    @field_validator("rootWorkspaceDigest", "headWorkspaceDigest")
    @classmethod
    def remediation_digests_are_sha256(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("sha256:"):
            raise ValueError("remediation workspace digests must be sha256 digests")
        return value

    @model_validator(mode="after")
    def next_baseline_matches_head(self) -> "RemediationCheckpointBranchLinkModel":
        baseline = self.nextActionBaseline
        if baseline is not None and baseline.model_dump() != {
            "checkpointRef": self.headCheckpointRef,
            "workspaceDigest": self.headWorkspaceDigest,
            "headVersion": self.headVersion,
        }:
            raise ValueError("next action baseline must match the persisted head")
        return self

class RemediationLinkSummaryModel(BaseModel):
    remediationWorkflowId: str
    remediationRunId: str
    targetWorkflowId: str
    targetRunId: str
    mode: str
    authorityMode: str
    status: str
    activeLockScope: str | None = None
    activeLockHolder: str | None = None
    latestActionSummary: str | None = None
    resolution: str | None = None
    contextArtifactRef: str | None = None
    selectedSteps: list[str] | None = None
    currentTargetState: str | None = None
    allowedActions: list[str] | None = None
    evidenceDegraded: bool | None = None
    unavailableEvidenceClasses: list[str] | None = None
    liveObservation: RemediationLiveObservationModel | None = None
    lockOutcome: RemediationLockOutcomeModel | None = None
    approvalState: RemediationApprovalStateModel | None = None
    checkpointBranches: list[RemediationCheckpointBranchLinkModel] = Field(
        default_factory=list
    )
    createdAt: datetime
    updatedAt: datetime

class RemediationLinksResponseModel(BaseModel):
    direction: str
    items: list[RemediationLinkSummaryModel]


class RemediationCollectionItemModel(BaseModel):
    remediationWorkflowId: str
    title: str
    status: str
    attentionRequired: bool = False
    targetWorkflowId: str
    targetTitle: str
    authorityMode: str
    mode: str
    latestActionSummary: str | None = None
    resolution: str | None = None
    createdAt: datetime
    updatedAt: datetime


class RemediationCollectionResponseModel(BaseModel):
    items: list[RemediationCollectionItemModel]

class RemediationApprovalDecisionRequest(BaseModel):
    decision: str
    comment: str | None = None

class RemediationApprovalDecisionResponse(BaseModel):
    accepted: bool
    workflowId: str
    requestId: str
    decision: str


class PublicationRecoveryResponse(BaseModel):
    sourceWorkflowId: str
    sourceRunId: str
    workflowId: str
    runId: str
    publicationIdempotencyKey: str
    rolloutGeneration: str

class RemediationCheckpointBranchRepairRequest(BaseModel):
    checkpointRef: str
    instructions: CheckpointBranchInstructionsModel
    idempotencyKey: str = Field(..., min_length=1, max_length=512)
    label: str | None = None
    workspacePolicy: Literal[
        "apply_previous_execution_diff_to_clean_baseline",
        "start_from_last_passed_commit",
        "fresh_branch_from_source",
    ] = "apply_previous_execution_diff_to_clean_baseline"
    runtimeContextPolicy: Literal["fresh_agent_run"] = "fresh_agent_run"
    gitWorkBranch: str | None = None
    maxBudgetUsd: float | None = None
_PROTECTED_BRANCH_REFS = {"head", "main", "master", "develop", "trunk", "prod", "production"}
_SAFE_PROMOTION_SIDE_EFFECT_STATES = {
    "none",
    "isolated",
    "compensated",
    "approved",
    "accepted",
}
_PASSING_GATE_VERDICTS = {"passed", "pass", "success", "fully_implemented"}


def _operation_digest(payload: BaseModel | Mapping[str, Any]) -> str:
    if isinstance(payload, BaseModel):
        raw = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    else:
        raw = dict(payload)
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _scoped_operation_digest(
    payload: BaseModel, *, scope: Mapping[str, Any]
) -> str:
    return _operation_digest(
        {
            "scope": dict(scope),
            "payload": payload.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
        }
    )


def _new_checkpoint_branch_id() -> str:
    return f"cbr_{uuid4().hex[:24]}"


def _new_checkpoint_branch_turn_id() -> str:
    return f"cbt_{uuid4().hex[:24]}"


def _instruction_identity(instructions: Any) -> tuple[str, str]:
    instruction_ref = str(getattr(instructions, "instruction_ref", "") or "").strip()
    instruction_digest = str(
        getattr(instructions, "instruction_digest", "") or ""
    ).strip()
    text = str(getattr(instructions, "text", "") or "")
    if instruction_ref and instruction_digest:
        return instruction_ref, instruction_digest
    digest = f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"
    return (
        f"inline://checkpoint-branch-instruction/{digest.removeprefix('sha256:')}",
        digest,
    )


def _checkpoint_branch_head_identity(branch: WorkflowCheckpointBranch) -> str:
    return (
        branch.current_head_commit
        or branch.current_head_checkpoint_ref
        or branch.source_checkpoint_ref
    )


def _require_comparable_checkpoint_lineage(
    branch: WorkflowCheckpointBranch, other: WorkflowCheckpointBranch
) -> None:
    branch_base = str(branch.source_checkpoint_ref or "").strip()
    other_base = str(other.source_checkpoint_ref or "").strip()
    if not branch_base or not other_base:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "checkpoint_invalidity",
                "reason": "base_checkpoint_ref_required",
            },
        )
    if branch_base != other_base:
        branch_head = str(branch.current_head_checkpoint_ref or "").strip()
        other_head = str(other.current_head_checkpoint_ref or "").strip()
        if (
            other.parent_branch_id == branch.branch_id
            and branch_head
            and branch_head == other_base
        ) or (
            branch.parent_branch_id == other.branch_id
            and other_head
            and other_head == branch_base
        ):
            return
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "incompatible_checkpoint_lineage",
                "reason": "base_checkpoint_ref_mismatch",
            },
        )
    branch_digest = str(branch.source_checkpoint_digest or "").strip()
    other_digest = str(other.source_checkpoint_digest or "").strip()
    if branch_digest and other_digest and branch_digest != other_digest:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "incompatible_checkpoint_lineage",
                "reason": "base_checkpoint_digest_mismatch",
            },
        )


def _checkpoint_branch_creation_mode(workspace_policy: str) -> str:
    if workspace_policy == "apply_previous_execution_diff_to_clean_baseline":
        return "from_checkpoint_patch"
    if workspace_policy == "start_from_last_passed_commit":
        return "from_last_accepted_commit"
    if workspace_policy == "fresh_branch_from_source":
        return "fresh_from_source_branch"
    return "from_checkpoint_worktree"


def _checkpoint_branch_git_context(record: Any) -> dict[str, Any]:
    params = getattr(record, "parameters", None)
    if not isinstance(params, Mapping):
        params = {}
    memo = getattr(record, "memo", None)
    if not isinstance(memo, Mapping):
        memo = {}
    search_attributes = getattr(record, "search_attributes", None)
    if not isinstance(search_attributes, Mapping):
        search_attributes = {}
    workflow_payload = params.get("workflow")
    if not isinstance(workflow_payload, Mapping):
        workflow_payload = {}
    task_payload = params.get("task")
    if not isinstance(task_payload, Mapping):
        task_payload = workflow_payload or params
    git_payload = task_payload.get("git")
    if not isinstance(git_payload, Mapping):
        git_payload = workflow_payload.get("git")
    if not isinstance(git_payload, Mapping):
        git_payload = params.get("git")
    if not isinstance(git_payload, Mapping):
        git_payload = {}

    repository = (
        _coerce_temporal_scalar(git_payload.get("repository"))
        or _coerce_temporal_scalar(task_payload.get("repository"))
        or _coerce_temporal_scalar(workflow_payload.get("repository"))
        or _coerce_temporal_scalar(params.get("repository"))
        or _coerce_temporal_scalar(params.get("repo"))
        or _coerce_temporal_scalar(search_attributes.get("mm_repository"))
        or _coerce_temporal_scalar(search_attributes.get("mm_repo"))
        or _coerce_temporal_scalar(search_attributes.get("repository"))
        or _coerce_temporal_scalar(memo.get("repository"))
    )
    base_branch = (
        _coerce_temporal_scalar(git_payload.get("baseBranch"))
        or _coerce_temporal_scalar(git_payload.get("startingBranch"))
        or _coerce_temporal_scalar(git_payload.get("branch"))
        or _coerce_temporal_scalar(task_payload.get("startingBranch"))
        or _coerce_temporal_scalar(workflow_payload.get("startingBranch"))
        or _coerce_temporal_scalar(params.get("startingBranch"))
        or _coerce_temporal_scalar(git_payload.get("defaultBranch"))
        or _coerce_temporal_scalar(params.get("defaultBranch"))
    )
    base_commit = (
        _coerce_temporal_scalar(git_payload.get("baseCommit"))
        or _coerce_temporal_scalar(git_payload.get("startingCommit"))
        or _coerce_temporal_scalar(workflow_payload.get("baseCommit"))
        or _coerce_temporal_scalar(params.get("baseCommit"))
        or _coerce_temporal_scalar(search_attributes.get("mm_base_commit"))
    )
    resolved_base_commit = (
        _coerce_temporal_scalar(git_payload.get("resolvedBaseCommit"))
        or _coerce_temporal_scalar(workflow_payload.get("resolvedBaseCommit"))
        or _coerce_temporal_scalar(params.get("resolvedBaseCommit"))
        or base_commit
    )
    current_ref = (
        _coerce_temporal_scalar(git_payload.get("currentRef"))
        or _coerce_temporal_scalar(workflow_payload.get("currentRef"))
        or _coerce_temporal_scalar(params.get("currentRef"))
        or base_branch
    )
    raw_known_refs = (
        git_payload.get("knownRefs")
        or git_payload.get("known_refs")
        or workflow_payload.get("knownRefs")
        or params.get("knownRefs")
        or []
    )
    known_refs = (
        {str(ref).strip() for ref in raw_known_refs if str(ref or "").strip()}
        if isinstance(raw_known_refs, list | tuple | set)
        else set()
    )
    if not repository or not base_branch:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_binding",
                "reason": "repository_git_context_missing",
            },
        )
    return {
        "repository": repository,
        "baseBranch": base_branch,
        "baseCommit": base_commit,
        "resolvedBaseCommit": resolved_base_commit,
        "knownRefs": known_refs,
        "currentRef": current_ref,
    }


async def _write_checkpoint_branch_preparation_artifact(
    artifact_kind: str,
    payload: Mapping[str, Any],
    content_type: str,
) -> tuple[str, str]:
    digest = _operation_digest(
        {
            "artifactKind": artifact_kind,
            "contentType": content_type,
            "payload": payload,
        }
    )
    artifact_ref = (
        "artifact://checkpoint-branch-preparation/"
        f"{digest.removeprefix('sha256:')}/{artifact_kind}"
    )
    return artifact_ref, digest


def _checkpoint_branch_operation_artifact_ref(
    *,
    operation: str,
    workflow_id: str,
    branch_id: str,
    artifact_kind: str,
    digest: str,
) -> str:
    operation_slug = {
        "checkpoint_branch.compare": "comparisons",
        "checkpoint_branch.promote": "promotions",
    }.get(
        operation,
        operation.removeprefix("checkpoint_branch.").replace(".", "-") + "s",
    )
    return (
        f"artifact://checkpoint-branch-{operation_slug}/"
        f"{workflow_id}/{branch_id}/{digest.removeprefix('sha256:')}/"
        f"{artifact_kind}"
    )


async def _record_checkpoint_branch_artifact_ref(
    *,
    session: AsyncSession,
    branch_id: str,
    artifact_kind: str,
    artifact_ref: str,
    digest: str | None = None,
    branch_turn_id: str | None = None,
) -> None:
    result = await session.execute(
        select(WorkflowCheckpointBranchArtifact).where(
            WorkflowCheckpointBranchArtifact.branch_id == branch_id,
            WorkflowCheckpointBranchArtifact.branch_turn_id == branch_turn_id,
            WorkflowCheckpointBranchArtifact.artifact_kind == artifact_kind,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.artifact_ref = artifact_ref
        existing.digest = digest
        return
    session.add(
        WorkflowCheckpointBranchArtifact(
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
            artifact_kind=artifact_kind,
            artifact_ref=artifact_ref,
            digest=digest,
        )
    )


async def _prepare_checkpoint_branch_launch(
    *,
    session: AsyncSession,
    record: Any,
    workflow_id: str,
    branch_id: str,
    branch_turn_id: str,
    source_checkpoint_ref: str,
    source_checkpoint_digest: str | None,
    logical_step_id: str | None,
    label: str | None,
    workspace_policy: str,
    runtime_context_policy: str | None,
    idempotency_key: str,
    instruction_ref: str,
    instruction_digest: str,
    requested_work_branch: str | None = None,
    parent_branch_id: str | None = None,
    parent_turn_id: str | None = None,
    source_run_id: str | None = None,
    source_execution_ordinal: int | None = None,
    source_checkpoint_boundary: str = "after_execution",
) -> None:
    git_context = _checkpoint_branch_git_context(record)
    try:
        await prepare_checkpoint_branch_workspace(
            session=session,
            binding_input={
                "workflowId": workflow_id,
                "productBranchId": branch_id,
                "branchTurnId": branch_turn_id,
                "sourceCheckpointRef": source_checkpoint_ref,
                "sourceCheckpointDigest": source_checkpoint_digest,
                "logicalStepId": logical_step_id,
                "label": label,
                "repository": git_context["repository"],
                "baseBranch": git_context["baseBranch"],
                "baseCommit": git_context["baseCommit"],
                "resolvedBaseCommit": git_context["resolvedBaseCommit"],
                "workspacePolicy": workspace_policy,
                "creationMode": _checkpoint_branch_creation_mode(workspace_policy),
                "idempotencyKey": idempotency_key,
                "requestedWorkBranch": requested_work_branch,
            },
            known_refs=git_context["knownRefs"],
            current_ref=git_context["currentRef"],
            instruction_ref=instruction_ref,
            instruction_digest=instruction_digest,
            artifact_writer=_write_checkpoint_branch_preparation_artifact,
            source_checkpoint_boundary=source_checkpoint_boundary,
            root_workflow_id=workflow_id,
            source_run_id=source_run_id,
            source_execution_ordinal=source_execution_ordinal,
            parent_branch_id=parent_branch_id,
            parent_turn_id=parent_turn_id,
            runtime_context_policy=runtime_context_policy,
        )
    except CheckpointBranchGitBindingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.failure_code, "reason": exc.failure_code},
        ) from exc


def _branch_comparison_record(
    *,
    workflow_id: str,
    branch: WorkflowCheckpointBranch,
    other: WorkflowCheckpointBranch,
) -> dict[str, Any]:
    branch_gate_evidence = (branch.promotion_evidence or {}).get("gateEvidence") or {}
    other_gate_evidence = (other.promotion_evidence or {}).get("gateEvidence") or {}
    branch_gate_verdict = str(
        branch_gate_evidence.get("verdict")
        or branch_gate_evidence.get("status")
        or "unknown"
    )
    other_gate_verdict = str(
        other_gate_evidence.get("verdict")
        or other_gate_evidence.get("status")
        or "unknown"
    )
    summary_text = (
        f"Branch {branch.branch_id} is {branch.state}; "
        f"comparison branch {other.branch_id} is {other.state}. "
        f"Quality verdicts: {branch.branch_id}={branch_gate_verdict}, "
        f"{other.branch_id}={other_gate_verdict}."
    )
    summary = {
        "text": summary_text,
        "branchState": branch.state,
        "againstState": other.state,
        "branchHeadStepExecutionId": branch.current_head_step_execution_id,
        "againstHeadStepExecutionId": other.current_head_step_execution_id,
        "branchHeadCheckpointRef": branch.current_head_checkpoint_ref,
        "againstHeadCheckpointRef": other.current_head_checkpoint_ref,
        "branchHeadCommit": branch.current_head_commit,
        "againstHeadCommit": other.current_head_commit,
    }
    quality = {
        "branchGateVerdict": branch_gate_verdict,
        "againstGateVerdict": other_gate_verdict,
    }
    gate_verdict_summaries = {
        branch.branch_id: branch_gate_verdict,
        other.branch_id: other_gate_verdict,
    }
    base_checkpoint_ref: str | dict[str, str | None]
    if branch.source_checkpoint_ref == other.source_checkpoint_ref:
        base_checkpoint_ref = branch.source_checkpoint_ref
    else:
        base_checkpoint_ref = {
            "branch": branch.source_checkpoint_ref,
            "against": other.source_checkpoint_ref,
        }
    branch_diff_ref = _checkpoint_branch_operation_artifact_ref(
        operation="checkpoint_branch.compare",
        workflow_id=workflow_id,
        branch_id=branch.branch_id,
        artifact_kind=f"output.branch_comparison.{other.branch_id}.left_diff.patch",
        digest=_operation_digest(
            {
                "branchId": branch.branch_id,
                "baseCommit": branch.git_base_commit,
                "headCommit": branch.current_head_commit,
            }
        ),
    )
    against_diff_ref = _checkpoint_branch_operation_artifact_ref(
        operation="checkpoint_branch.compare",
        workflow_id=workflow_id,
        branch_id=branch.branch_id,
        artifact_kind=f"output.branch_comparison.{other.branch_id}.right_diff.patch",
        digest=_operation_digest(
            {
                "branchId": other.branch_id,
                "baseCommit": other.git_base_commit,
                "headCommit": other.current_head_commit,
            }
        ),
    )
    range_diff_ref = _checkpoint_branch_operation_artifact_ref(
        operation="checkpoint_branch.compare",
        workflow_id=workflow_id,
        branch_id=branch.branch_id,
        artifact_kind=f"output.branch_comparison.{other.branch_id}.range_diff.patch",
        digest=_operation_digest(
            {
                "branchId": branch.branch_id,
                "againstBranchId": other.branch_id,
                "branchHeadCommit": branch.current_head_commit,
                "againstHeadCommit": other.current_head_commit,
            }
        ),
    )
    diagnostics_ref = _checkpoint_branch_operation_artifact_ref(
        operation="checkpoint_branch.compare",
        workflow_id=workflow_id,
        branch_id=branch.branch_id,
        artifact_kind=f"output.branch_comparison.{other.branch_id}.diagnostics.json",
        digest=_operation_digest(
            {
                "branchId": branch.branch_id,
                "againstBranchId": other.branch_id,
                "branchHead": _checkpoint_branch_head_identity(branch),
                "againstHead": _checkpoint_branch_head_identity(other),
                "recordType": "checkpoint_branch_comparison_diagnostics",
            }
        ),
    )
    evidence_refs = {
        key: value
        for key, value in {
            "baseCheckpointRef": base_checkpoint_ref,
            "branchCheckpointRef": branch.current_head_checkpoint_ref
            or branch.source_checkpoint_ref,
            "againstCheckpointRef": other.current_head_checkpoint_ref
            or other.source_checkpoint_ref,
            "branchDiffRef": branch_diff_ref,
            "againstDiffRef": against_diff_ref,
            "rangeDiffRef": range_diff_ref,
            "branchGitRange": {
                "base": branch.git_base_commit,
                "head": branch.current_head_commit,
            }
            if branch.git_base_commit or branch.current_head_commit
            else None,
            "againstGitRange": {
                "base": other.git_base_commit,
                "head": other.current_head_commit,
            }
            if other.git_base_commit or other.current_head_commit
            else None,
            "branchPromotionEvidence": branch.promotion_evidence,
            "againstPromotionEvidence": other.promotion_evidence,
        }.items()
        if value
    }
    record_payload = {
        "schemaVersion": 2,
        "recordType": "checkpoint_branch_comparison",
        "workflowId": workflow_id,
        "branchId": branch.branch_id,
        "againstBranchId": other.branch_id,
        "branchIds": [branch.branch_id, other.branch_id],
        "baseCheckpointRef": base_checkpoint_ref,
        "summaryText": summary_text,
        "summary": summary,
        "quality": quality,
        "gateVerdictSummaries": gate_verdict_summaries,
        "git": {
            "leftDiffRef": branch_diff_ref,
            "rightDiffRef": against_diff_ref,
            "rangeDiffRef": range_diff_ref,
        },
        "diffRefs": {
            "branchDiffRef": branch_diff_ref,
            "againstDiffRef": against_diff_ref,
            "rangeDiffRef": range_diff_ref,
        },
        "evidenceRefs": evidence_refs,
        "diagnosticsRefs": [diagnostics_ref],
        "createdAt": datetime.now(UTC).isoformat(),
    }
    record_digest = _operation_digest(record_payload)
    summary_ref = (
        _checkpoint_branch_operation_artifact_ref(
            operation="checkpoint_branch.compare",
            workflow_id=workflow_id,
            branch_id=branch.branch_id,
            artifact_kind=f"output.branch_comparison.{other.branch_id}.summary.json",
            digest=record_digest,
        )
    )
    metadata_ref = (
        _checkpoint_branch_operation_artifact_ref(
            operation="checkpoint_branch.compare",
            workflow_id=workflow_id,
            branch_id=branch.branch_id,
            artifact_kind=f"output.branch_comparison.{other.branch_id}.metadata.json",
            digest=record_digest,
        )
    )
    record_payload["summaryRef"] = summary_ref
    record_payload["boundedSummaryRefs"] = [summary_ref]
    record_payload["artifactRefs"] = {
        "output.branch_comparison.summary.json": summary_ref,
        "output.branch_comparison.metadata.json": metadata_ref,
        "output.branch_comparison.left_diff.patch": branch_diff_ref,
        "output.branch_comparison.right_diff.patch": against_diff_ref,
        "output.branch_comparison.range_diff.patch": range_diff_ref,
        "output.branch_comparison.diagnostics.json": diagnostics_ref,
    }
    record_payload["digest"] = record_digest
    return record_payload


def _checkpoint_ref_artifact_value(ref: object) -> str:
    if isinstance(ref, Mapping):
        ref = ref.get("artifactRef") or ref.get("checkpointRef") or ref.get("ref")
    return str(ref or "").strip()


async def _latest_branch_turn_for_step_execution(
    *,
    session: AsyncSession,
    branch_id: str,
    step_execution_id: str,
) -> WorkflowCheckpointBranchTurn | None:
    result = await session.execute(
        select(WorkflowCheckpointBranchTurn)
        .where(
            WorkflowCheckpointBranchTurn.branch_id == branch_id,
            WorkflowCheckpointBranchTurn.created_step_execution_id == step_execution_id,
        )
        .order_by(
            WorkflowCheckpointBranchTurn.created_at.desc(),
            WorkflowCheckpointBranchTurn.branch_turn_id.desc(),
        )
    )
    return result.scalars().first()


def _promotion_record_payload(
    *,
    workflow_id: str,
    branch: WorkflowCheckpointBranch,
    turn: WorkflowCheckpointBranchTurn | None,
    payload: CheckpointBranchPromoteRequest,
    promoted_at: datetime,
) -> dict[str, Any]:
    accepted_output_refs = dict(payload.accepted_output_refs or {})
    accepted_output_refs["headStepExecutionId"] = payload.expected_head_step_execution_id
    if branch.current_head_checkpoint_ref:
        accepted_output_refs["headCheckpointRef"] = branch.current_head_checkpoint_ref
    if turn and turn.step_execution_manifest_ref:
        accepted_output_refs.setdefault(
            "stepExecutionManifestRef", turn.step_execution_manifest_ref
        )
    downstream_invalidation = dict(payload.downstream_invalidation or {})
    downstream_invalidation.setdefault("status", "not_required")
    downstream_invalidation.setdefault("supersededBranchIds", [])
    git_evidence = {
        key: value
        for key, value in {
            "repository": branch.git_repository,
            "baseBranch": branch.git_base_branch,
            "workBranch": branch.git_work_branch,
            "baseCommit": branch.git_base_commit,
            "headCommit": payload.expected_head_commit or branch.current_head_commit,
            "pullRequestUrl": branch.pull_request_url,
            "publishStatus": branch.publish_status,
        }.items()
        if value
    }
    policy_evidence = dict(payload.policy_evidence or {})
    policy_evidence.setdefault(
        "policyRequiresApproval", payload.policy_requires_approval
    )
    policy_evidence.setdefault("freshHeadValidated", True)
    policy_evidence.setdefault("passedGatesRequired", True)
    return {
        "schemaVersion": 1,
        "recordType": "checkpoint_branch_promotion",
        "workflowId": workflow_id,
        "branchId": branch.branch_id,
        "branchTurnId": turn.branch_turn_id if turn else None,
        "stepExecutionId": payload.expected_head_step_execution_id,
        "acceptedOutputRefs": accepted_output_refs,
        "gitEvidence": git_evidence,
        "gateEvidence": payload.gate_evidence,
        "sideEffectDisposition": payload.side_effect_disposition,
        "downstreamInvalidation": downstream_invalidation,
        "approvalEvidence": payload.approval_evidence,
        "policyEvidence": policy_evidence,
        "promotedAt": promoted_at.isoformat(),
    }


def _branch_to_model(branch: WorkflowCheckpointBranch) -> CheckpointBranchModel:
    return CheckpointBranchModel.model_validate(branch)


def _turn_to_model(turn: WorkflowCheckpointBranchTurn) -> CheckpointBranchTurnModel:
    return CheckpointBranchTurnModel.model_validate(turn)


def _branch_turn_immutable_snapshot(
    turn: WorkflowCheckpointBranchTurn,
) -> dict[str, Any]:
    return {
        "instructionRef": turn.instruction_ref,
        "instructionDigest": turn.instruction_digest,
        "sourceCheckpointRef": turn.source_checkpoint_ref,
        "sourceCheckpointDigest": turn.source_checkpoint_digest,
        "sourceStateKind": turn.source_state_kind,
        "sourceStateRef": turn.source_state_ref,
        "sourceStateDigest": turn.source_state_digest,
        "workspacePolicy": turn.workspace_policy,
        "runtimeContextPolicy": turn.runtime_context_policy,
    }


def _require_branch_turn_immutable_snapshot(
    turn: WorkflowCheckpointBranchTurn,
    snapshot: Mapping[str, Any] | None,
) -> None:
    if not snapshot:
        return
    current = _branch_turn_immutable_snapshot(turn)
    for key, expected in snapshot.items():
        if current.get(key) != expected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "immutable_launch_field",
                    "field": key,
                },
            )


def _branch_turn_artifact_ref(
    *,
    branch_turn_id: str,
    artifact_name: str,
    payload_digest: str,
) -> str:
    return (
        f"artifact://checkpoint-branch-turns/{branch_turn_id}/"
        f"{artifact_name}/{payload_digest.removeprefix('sha256:')}.json"
    )


def _branch_turn_launch_manifest_payload(
    *,
    workflow_id: str,
    branch: WorkflowCheckpointBranch,
    turn: WorkflowCheckpointBranchTurn,
    payload: CheckpointBranchTurnLaunchRequest,
    context_bundle_ref: str,
) -> dict[str, Any]:
    return {
        "workflowId": workflow_id,
        "runId": branch.source_run_id,
        "logicalStepId": branch.logical_step_id,
        "executionOrdinal": branch.source_execution_ordinal,
        "stepExecutionId": payload.created_step_execution_id,
        "reason": "checkpoint_branch",
        "status": "running",
        "branch": {
            "branchId": branch.branch_id,
            "branchTurnId": turn.branch_turn_id,
            "rootCheckpointRef": turn.source_checkpoint_ref,
            "sourceStateKind": turn.source_state_kind,
            "sourceStateRef": turn.source_state_ref,
            "sourceStateDigest": turn.source_state_digest,
            "parentBranchId": branch.parent_branch_id,
            "parentTurnId": turn.parent_turn_id or branch.parent_turn_id,
            "gitWorkBranch": branch.git_work_branch or turn.git_work_branch,
        },
        "inputs": {
            "instructionRef": turn.instruction_ref,
            "instructionDigest": turn.instruction_digest,
            "contextBundleRef": context_bundle_ref,
        },
    }


def _is_protected_ref(ref: str | None) -> bool:
    normalized = str(ref or "").strip().removeprefix("refs/heads/").lower()
    return normalized in _PROTECTED_BRANCH_REFS or normalized.startswith("release/")


def _checkpoint_summaries_from_record(record: Any) -> list[CheckpointSummaryModel]:
    seen: set[tuple[str, str]] = set()
    items: list[CheckpointSummaryModel] = []

    def add(
        ref: object,
        *,
        boundary: object = "after_execution",
        run_id: object | None = None,
        logical_step_id: object | None = None,
        execution_ordinal: object | None = None,
        digest: object | None = None,
    ) -> None:
        if isinstance(ref, Mapping) and digest is None:
            digest = ref.get("checkpointDigest") or ref.get("digest")
        ref_value = _checkpoint_ref_artifact_value(ref)
        boundary_value = str(boundary or "after_execution").strip()
        if not ref_value.startswith("artifact://") or not boundary_value:
            return
        key = (ref_value, boundary_value)
        ordinal_value: int | None = None
        if isinstance(execution_ordinal, int) and not isinstance(
            execution_ordinal, bool
        ):
            ordinal_value = execution_ordinal
        elif isinstance(execution_ordinal, str) and execution_ordinal.isdigit():
            ordinal_value = int(execution_ordinal)
        digest_value = str(digest or "").strip() or None
        if key in seen:
            if digest_value:
                for item in items:
                    if (
                        item.checkpoint_ref == ref_value
                        and item.checkpoint_boundary == boundary_value
                        and not item.checkpoint_digest
                    ):
                        item.checkpoint_digest = digest_value
                        break
            return
        seen.add(key)
        items.append(
            CheckpointSummaryModel(
                checkpointRef=ref_value,
                checkpointBoundary=boundary_value,
                runId=str(run_id or getattr(record, "run_id", "") or "") or None,
                logicalStepId=str(logical_step_id or "").strip() or None,
                executionOrdinal=ordinal_value,
                checkpointDigest=digest_value,
            )
        )

    memo = dict(getattr(record, "memo", None) or {})
    params = dict(getattr(record, "parameters", None) or {})
    finish = dict(getattr(record, "finish_summary_json", None) or {})

    for key, boundary in (
        ("recovery_checkpoint_ref", "before_recovery_restoration"),
        ("recoveryCheckpointRef", "before_recovery_restoration"),
        ("step_checkpoint_ref", "after_execution"),
        ("stepCheckpointRef", "after_execution"),
        ("stateCheckpointRef", "after_execution"),
        ("checkpointRef", "after_execution"),
    ):
        add(memo.get(key), boundary=boundary)

    recovery_manifest = finish.get("recoveryManifest")
    if isinstance(recovery_manifest, Mapping):
        add(
            recovery_manifest.get("checkpointRef"),
            boundary=recovery_manifest.get("boundary") or "before_recovery_restoration",
            digest=recovery_manifest.get("checkpointDigest"),
        )

    def walk(value: object, *, logical_step_id: object | None = None) -> None:
        if isinstance(value, Mapping):
            step_id = (
                value.get("logicalStepId") or value.get("stepId") or logical_step_id
            )
            ordinal = value.get("executionOrdinal") or value.get("attempt")
            refs = value.get("checkpointRefsByBoundary")
            if isinstance(refs, Mapping):
                for boundary, ref in refs.items():
                    add(
                        ref,
                        boundary=boundary,
                        logical_step_id=step_id,
                        execution_ordinal=ordinal,
                    )
            for key, boundary in (
                ("checkpointRef", value.get("checkpointBoundary") or "after_execution"),
                ("stateCheckpointRef", "after_execution"),
                ("stepCheckpointRef", "after_execution"),
                ("checkpointBeforeRef", "before_execution"),
                ("checkpointAfterRef", "after_execution"),
            ):
                add(
                    value.get(key),
                    boundary=boundary,
                    logical_step_id=step_id,
                    execution_ordinal=ordinal,
                    digest=value.get("checkpointDigest"),
                )
            for child in value.values():
                walk(child, logical_step_id=step_id)
        elif isinstance(value, list):
            for child in value:
                walk(child, logical_step_id=logical_step_id)

    walk(params)
    walk(finish)
    return items


def _validate_branch_source(
    *,
    workflow_id: str,
    record: Any,
    source: Any,
) -> None:
    source_workflow_id = str(getattr(source, "workflow_id", "") or "").strip()
    if source_workflow_id and source_workflow_id != workflow_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_source", "reason": "source_workflow_mismatch"},
        )
    if str(getattr(source, "run_id", "") or "") != str(
        getattr(record, "run_id", "") or ""
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_source", "reason": "source_run_mismatch"},
        )
    checkpoints = _checkpoint_summaries_from_record(record)
    if not checkpoints:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "checkpoint_missing", "reason": "checkpoint_missing"},
        )
    matching = [
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.checkpoint_ref == source.checkpoint_ref
        and checkpoint.checkpoint_boundary == source.checkpoint_boundary
    ]
    if not matching:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "checkpoint_invalid", "reason": "checkpoint_invalid"},
        )
    if source.checkpoint_digest:
        known_digests = {
            checkpoint.checkpoint_digest
            for checkpoint in matching
            if checkpoint.checkpoint_digest
        }
        if known_digests and source.checkpoint_digest not in known_digests:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "checkpoint_digest_mismatch",
                    "reason": "checkpoint_digest_mismatch",
                },
            )


def _validate_branch_policy(
    *,
    workspace_policy: str,
    runtime_context_policy: str,
    publish_mode: str | None = None,
    git_work_branch: str | None = None,
    max_budget_usd: float | None = None,
) -> None:
    if runtime_context_policy == "external_provider_continuation":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "provider_continuation_unsupported",
                "reason": "provider_continuation_unsupported",
            },
        )
    if publish_mode and publish_mode != "none" and not git_work_branch:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_publish_request",
                "reason": "protected_ref_unknown",
            },
        )
    if _is_protected_ref(git_work_branch):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "protected_branch_ref", "reason": "protected_branch_ref"},
        )
    if workspace_policy == "continue_from_previous_execution" and publish_mode not in {
        None,
        "none",
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "workspace_policy_incompatible",
                "reason": "workspace_policy_incompatible",
            },
        )
    if max_budget_usd == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "budget_exhausted", "reason": "budget_exhausted"},
        )


def _validate_branch_turn_launch_policy(
    branch: WorkflowCheckpointBranch,
    turn: WorkflowCheckpointBranchTurn,
) -> None:
    if "external_provider_continuation" in {
        branch.runtime_context_policy,
        turn.runtime_context_policy,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "provider_continuation_unsupported",
                "reason": "provider_continuation_unsupported",
            },
        )


async def _load_checkpoint_branch(
    session: AsyncSession,
    *,
    workflow_id: str,
    branch_id: str,
    include_archived: bool = True,
) -> WorkflowCheckpointBranch:
    result = await session.execute(
        select(WorkflowCheckpointBranch).where(
            WorkflowCheckpointBranch.workflow_id == workflow_id,
            WorkflowCheckpointBranch.branch_id == branch_id,
        )
    )
    branch = result.scalar_one_or_none()
    if branch is None or (not include_archived and branch.state == "archived"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "checkpoint_branch_not_found"},
        )
    return branch


_PR_URL_CANDIDATE_SOURCES = (
    ("memo", "pull_request_url"),
    ("memo", "pullRequestUrl"),
    ("search_attributes", "mm_pull_request_url"),
    ("search_attributes", "pullRequestUrl"),
    ("params", "prUrl"),
    ("params", "pullRequestUrl"),
)
_TASK_EDITING_UPDATE_NAMES = {"UpdateInputs", "RequestRerun"}
_TASK_INPUT_SNAPSHOT_CONTENT_TYPE = (
    "application/vnd.moonmind.task-input-snapshot+json;version=1"
)
_TASK_INPUT_SNAPSHOT_LINK_TYPE = "input.original_snapshot"
_WORKFLOW_INPUT_SNAPSHOT_VERSION = 1

def _bounded_metric_tag(value: object | None, *, fallback: str = "unknown") -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return fallback
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", normalized)[:80] or fallback

def _emit_task_editing_metric(
    event: str,
    *,
    update_name: str,
    workflow_type: object | None,
    state: object | None,
    result: str | None = None,
    reason: str | None = None,
    applied: str | None = None,
) -> None:
    tags = {
        "event": _bounded_metric_tag(event),
        "update_name": _bounded_metric_tag(update_name),
        "workflow_type": _bounded_metric_tag(_enum_value(workflow_type)),
        "state": _bounded_metric_tag(_enum_value(state)),
    }
    if result:
        tags["result"] = _bounded_metric_tag(result)
    if reason:
        tags["reason"] = _bounded_metric_tag(reason)
    if applied:
        tags["applied"] = _bounded_metric_tag(applied)
    try:
        get_metrics_emitter().increment(
            "temporal_workflow_editing.event",
            tags=tags,
        )
    except Exception:
        return

def _enum_value(value: object | None) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)

@lru_cache(maxsize=1)
def get_temporal_client_adapter() -> TemporalClientAdapter:
    return TemporalClientAdapter()


def get_control_stop_continuation_starter(
    adapter: TemporalClientAdapter = Depends(get_temporal_client_adapter),
) -> TemporalControlStopContinuationStarter:
    """Compose the production continuation starter from the shared Temporal adapter."""

    return TemporalControlStopContinuationStarter(adapter)

async def get_temporal_client(
    adapter: TemporalClientAdapter = Depends(get_temporal_client_adapter),
) -> Client:
    return await adapter.get_client()

def _is_execution_admin(user: User | None) -> bool:
    return bool(user and getattr(user, "is_superuser", False))

def _owner_id(user: User | None) -> str | None:
    value = getattr(user, "id", None)
    return str(value) if value is not None else None


def _effective_execution_owner_scope(
    *,
    user: User,
    owner_type: str | None,
    owner_id: str | None,
) -> tuple[str | None, str | None]:
    if _is_execution_admin(user):
        return owner_type, owner_id

    normalized_owner_type = str(owner_type or "").strip().lower()
    if owner_type is not None and normalized_owner_type != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "execution_forbidden",
                "message": "Cannot list non-user executions.",
            },
        )
    if owner_id is not None and owner_id != _owner_id(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "execution_forbidden",
                "message": "Cannot list executions for another user.",
            },
        )
    return ("user" if normalized_owner_type == "user" else None), _owner_id(user)


def _normalize_temporal_list_scope(
    scope: str | None,
    *,
    workflow_type: str | None,
    entry: str | None,
) -> Literal["default"]:
    """Return the product-facing Temporal list scope."""

    normalized = str(scope or "").strip().lower()
    if normalized:
        logger.info(
            "Ignoring retired execution list scope=%r on workflow-list boundary",
            normalized[:64],
        )
    return "default"


def _is_user_workflow_list_type(workflow_type: str | None) -> bool:
    return str(workflow_type or "").strip() == "MoonMind.UserWorkflow"


def _is_user_workflow_list_entry(entry: str | None) -> bool:
    return str(entry or "").strip().lower() == "user_workflow"


def _escape_temporal_value(value: str) -> str:
    return value.replace('"', '\\"')


def _split_temporal_values(raw: str | list[str] | None, *, alias: str) -> list[str]:
    if not raw:
        return []
    parts = raw if isinstance(raw, list) else [raw]
    seen: set[str] = set()
    values: list[str] = []
    for item in parts:
        for part in str(item or "").split(","):
            value = part.strip()
            if not value or value in seen:
                continue
            if len(value) > _EXECUTION_FILTER_VALUE_MAX_LENGTH:
                raise TemporalExecutionValidationError(
                    f"{alias} values must be {_EXECUTION_FILTER_VALUE_MAX_LENGTH} characters or fewer."
                )
            seen.add(value)
            values.append(value)
    if len(values) > _EXECUTION_FILTER_VALUE_LIMIT:
        raise TemporalExecutionValidationError(
            f"{alias} accepts at most {_EXECUTION_FILTER_VALUE_LIMIT} values."
        )
    return values


def _raw_query_values(request: Request, alias: str, fallback: str | None) -> list[str]:
    values = request.query_params.getlist(alias)
    if not values and fallback is not None:
        values = [fallback]
    return _split_temporal_values(values, alias=alias)


def _validate_non_contradictory_values(
    include_alias: str,
    include_values: list[str],
    exclude_alias: str,
    exclude_values: list[str],
) -> None:
    if include_values and exclude_values:
        raise TemporalExecutionValidationError(
            f"Cannot combine {include_alias} and {exclude_alias}."
        )


def _normalize_text_filter(raw: str | None, *, alias: str) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if len(value) > _EXECUTION_TEXT_FILTER_MAX_LENGTH:
        raise TemporalExecutionValidationError(
            f"{alias} must be {_EXECUTION_TEXT_FILTER_MAX_LENGTH} characters or fewer."
        )
    return value


def _validate_blank_mode(raw: str | None, *, alias: str) -> str | None:
    value = str(raw or "").strip().lower()
    if not value:
        return None
    if value not in {"include", "exclude"}:
        raise TemporalExecutionValidationError(
            f"{alias} must be one of: include, exclude."
        )
    return value


def _normalize_progress_percent(raw: str | None, *, alias: str) -> float | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise TemporalExecutionValidationError(
            f"{alias} must be a number from 0 to 100."
        ) from exc
    if parsed < 0 or parsed > 100:
        raise TemporalExecutionValidationError(
            f"{alias} must be a number from 0 to 100."
        )
    return parsed


def _validate_progress_values(
    values: list[str],
    *,
    alias: str,
    allowed: frozenset[str],
) -> None:
    unsupported = sorted(value for value in values if value not in allowed)
    if unsupported:
        raise TemporalExecutionValidationError(
            f"{alias} must use supported values: {', '.join(sorted(allowed))}."
        )


def _validate_progress_request_params(
    request: Request,
    *,
    sort: str | None,
    sort_dir: str | None,
) -> None:
    progress_pct_from = _normalize_progress_percent(
        request.query_params.get("progressPctFrom"),
        alias="progressPctFrom",
    )
    progress_pct_to = _normalize_progress_percent(
        request.query_params.get("progressPctTo"),
        alias="progressPctTo",
    )
    if (
        progress_pct_from is not None
        and progress_pct_to is not None
        and progress_pct_from > progress_pct_to
    ):
        raise TemporalExecutionValidationError(
            "progressPctFrom must be before or equal to progressPctTo."
        )
    progress_bucket_in = _raw_query_values(
        request, "progressBucketIn", request.query_params.get("progressBucketIn")
    )
    progress_bucket_not_in = _raw_query_values(
        request, "progressBucketNotIn", request.query_params.get("progressBucketNotIn")
    )
    progress_signal_in = _raw_query_values(
        request, "progressSignalIn", request.query_params.get("progressSignalIn")
    )
    progress_signal_not_in = _raw_query_values(
        request, "progressSignalNotIn", request.query_params.get("progressSignalNotIn")
    )
    _validate_non_contradictory_values(
        "progressBucketIn",
        progress_bucket_in,
        "progressBucketNotIn",
        progress_bucket_not_in,
    )
    _validate_non_contradictory_values(
        "progressSignalIn",
        progress_signal_in,
        "progressSignalNotIn",
        progress_signal_not_in,
    )
    _validate_progress_values(
        progress_bucket_in + progress_bucket_not_in,
        alias="progressBucketIn",
        allowed=_PROGRESS_BUCKET_VALUES,
    )
    _validate_progress_values(
        progress_signal_in + progress_signal_not_in,
        alias="progressSignalIn",
        allowed=_PROGRESS_SIGNAL_VALUES,
    )
    _normalize_text_filter(
        request.query_params.get("progressStepTitleContains"),
        alias="progressStepTitleContains",
    )
    _validate_blank_mode(request.query_params.get("progressBlank"), alias="progressBlank")
    if sort in {"progress", "progressPct"}:
        direction = str(sort_dir or "desc").strip().lower()
        if direction not in {"asc", "desc"}:
            raise TemporalExecutionValidationError("sortDir must be one of: asc, desc.")


def _execution_progress_pct(execution: ExecutionModel) -> float | None:
    progress = execution.progress
    if progress is None or progress.total <= 0:
        return None
    return max(0.0, min(100.0, (progress.completed / progress.total) * 100.0))


def _execution_progress_bucket(execution: ExecutionModel) -> str | None:
    progress = execution.progress
    if progress is None or progress.total <= 0:
        return None
    if progress.completed >= progress.total:
        return "complete"
    active = (
        progress.executing > 0
        or progress.awaiting_external > 0
        or progress.reviewing > 0
    )
    terminal = progress.failed > 0 or progress.skipped > 0 or progress.canceled > 0
    if progress.completed > 0 or active or terminal:
        return "in_progress"
    return "not_started"


def _execution_progress_signals(execution: ExecutionModel) -> set[str]:
    progress = execution.progress
    if progress is None:
        return set()
    signals: set[str] = set()
    if progress.executing > 0:
        signals.add("executing")
    if progress.awaiting_external > 0:
        signals.add("awaiting_external")
    if progress.reviewing > 0:
        signals.add("reviewing")
    if progress.failed > 0:
        signals.add("has_failed_steps")
    if progress.skipped > 0:
        signals.add("has_skipped_steps")
    if progress.canceled > 0:
        signals.add("has_canceled_steps")
    return signals


def _execution_matches_progress_request(
    execution: ExecutionModel,
    request: Request,
) -> bool:
    pct = _execution_progress_pct(execution)
    is_blank = pct is None
    blank_mode = _validate_blank_mode(
        request.query_params.get("progressBlank"),
        alias="progressBlank",
    )
    if blank_mode == "include" and not is_blank:
        return False
    if blank_mode == "exclude" and is_blank:
        return False
    pct_from = _normalize_progress_percent(
        request.query_params.get("progressPctFrom"),
        alias="progressPctFrom",
    )
    pct_to = _normalize_progress_percent(
        request.query_params.get("progressPctTo"),
        alias="progressPctTo",
    )
    if pct_from is not None and (pct is None or pct < pct_from):
        return False
    if pct_to is not None and (pct is None or pct > pct_to):
        return False
    bucket_in = set(
        _raw_query_values(
            request, "progressBucketIn", request.query_params.get("progressBucketIn")
        )
    )
    bucket_not_in = set(
        _raw_query_values(
            request,
            "progressBucketNotIn",
            request.query_params.get("progressBucketNotIn"),
        )
    )
    bucket = _execution_progress_bucket(execution)
    if bucket_in and bucket not in bucket_in:
        return False
    if bucket_not_in and bucket in bucket_not_in:
        return False
    signal_in = set(
        _raw_query_values(
            request, "progressSignalIn", request.query_params.get("progressSignalIn")
        )
    )
    signal_not_in = set(
        _raw_query_values(
            request,
            "progressSignalNotIn",
            request.query_params.get("progressSignalNotIn"),
        )
    )
    signals = _execution_progress_signals(execution)
    if signal_in and not signals.intersection(signal_in):
        return False
    if signal_not_in and signals.intersection(signal_not_in):
        return False
    step_title_contains = _normalize_text_filter(
        request.query_params.get("progressStepTitleContains"),
        alias="progressStepTitleContains",
    )
    if step_title_contains:
        title = (execution.progress.current_step_title if execution.progress else None) or ""
        if step_title_contains.lower() not in title.lower():
            return False
    return True


def _request_has_progress_filters(request: Request) -> bool:
    return any(request.query_params.get(alias) for alias in _PROGRESS_FILTER_ALIASES)


def _sort_executions_by_progress(
    items: list[ExecutionModel],
    *,
    direction: str,
) -> list[ExecutionModel]:
    reverse = direction == "desc"

    def key(item: ExecutionModel) -> tuple[int, float, int, int, float, str]:
        pct = _execution_progress_pct(item)
        progress = item.progress
        updated_at = progress.updated_at if progress else item.updated_at
        return (
            1 if pct is None else 0,
            pct or 0.0,
            progress.completed if progress else 0,
            progress.total if progress else 0,
            updated_at.timestamp() if updated_at else 0.0,
            item.workflow_id,
        )

    non_blank = [item for item in items if _execution_progress_pct(item) is not None]
    blank = [item for item in items if _execution_progress_pct(item) is None]
    return sorted(non_blank, key=key, reverse=reverse) + sorted(
        blank,
        key=lambda item: item.workflow_id,
        reverse=True,
    )


def _normalize_date_bound(raw: str | None, *, alias: str, end_of_day: bool = False) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    candidate = f"{value}T00:00:00Z" if len(value) == 10 else value
    try:
        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TemporalExecutionValidationError(
            f"{alias} must be an ISO 8601 date or timestamp."
        ) from exc
    if len(value) == 10:
        suffix = "T23:59:59.999999Z" if end_of_day else "T00:00:00Z"
        return f"{value}{suffix}"
    return value.replace("+00:00", "Z")


def _datetime_range_parts(
    attr: str,
    start_raw: str | None,
    end_raw: str | None,
    *,
    start_alias: str,
    end_alias: str,
) -> list[str]:
    parts: list[str] = []
    start_value = _normalize_date_bound(start_raw, alias=start_alias)
    end_value = _normalize_date_bound(end_raw, alias=end_alias, end_of_day=True)
    if start_value and end_value:
        start_dt = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_value.replace("Z", "+00:00"))
        if start_dt > end_dt:
            raise TemporalExecutionValidationError(
                f"{start_alias} must be before or equal to {end_alias}."
            )
    if start_value:
        parts.append(f'{attr}>="{_escape_temporal_value(start_value)}"')
    if end_value:
        parts.append(f'{attr}<="{_escape_temporal_value(end_value)}"')
    return parts


def _datetime_range_clause_from_values(
    attr: str,
    start_value: str | None,
    end_value: str | None,
) -> str:
    parts: list[str] = []
    if start_value:
        parts.append(f'{attr}>="{_escape_temporal_value(start_value)}"')
    if end_value:
        parts.append(f'{attr}<="{_escape_temporal_value(end_value)}"')
    return " AND ".join(parts)


def _append_updated_temporal_filter(
    query_parts: list[str],
    start_raw: str | None,
    end_raw: str | None,
) -> None:
    start_value = _normalize_date_bound(start_raw, alias="updatedFrom")
    end_value = _normalize_date_bound(end_raw, alias="updatedTo", end_of_day=True)
    if start_value and end_value:
        start_dt = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_value.replace("Z", "+00:00"))
        if start_dt > end_dt:
            raise TemporalExecutionValidationError(
                "updatedFrom must be before or equal to updatedTo."
            )
    if not start_value and not end_value:
        return
    close_clause = _datetime_range_clause_from_values("CloseTime", start_value, end_value)
    scheduled_clause = _datetime_range_clause_from_values("mm_scheduled_for", start_value, end_value)
    start_clause = _datetime_range_clause_from_values("StartTime", start_value, end_value)
    query_parts.append(
        "("
        f"(CloseTime IS NOT NULL AND {close_clause})"
        " OR "
        f"(CloseTime IS NULL AND mm_scheduled_for IS NOT NULL AND {scheduled_clause})"
        " OR "
        f"(CloseTime IS NULL AND mm_scheduled_for IS NULL AND {start_clause})"
        ")"
    )


def _any_temporal_query(attr: str, values: list[str]) -> str | None:
    if not values:
        return None
    if len(values) == 1:
        return f'{attr}="{_escape_temporal_value(values[0])}"'
    return "(" + " OR ".join(
        f'{attr}="{_escape_temporal_value(value)}"' for value in values
    ) + ")"


def _state_include_clause(values: list[str]) -> str | None:
    """Build a Temporal visibility clause matching the effective workflow state.

    Filtering only on the ``mm_state`` search attribute is unsafe for terminal
    states: when a workflow is canceled, terminated, or times out without
    re-entering the workflow code, ``mm_state`` is left at whatever value the
    workflow last published (often a non-terminal one like
    ``waiting_on_dependencies``). The executions list serializer then derives
    the displayed state from the Temporal ``ExecutionStatus``, which means a
    closed workflow can leak into "AWAITING DEP" or any other non-terminal
    filter result. Anchor non-terminal states to ``ExecutionStatus="Running"``
    and map terminal states to their Temporal ``ExecutionStatus`` so the
    server-side filter agrees with the rendered status.
    """
    if not values:
        return None
    deduped = list(dict.fromkeys(v for v in values if v))
    non_terminal = [value for value in deduped if value in _NON_TERMINAL_MM_STATES]
    terminal_statuses = list(dict.fromkeys(
        status
        for value in deduped
        for status in _TERMINAL_EXECUTION_STATUSES_BY_MM_STATE.get(value, ())
    ))
    unknown = [
        expanded
        for value in deduped
        for expanded in (
            ("no_commit", "no_changes") if value == "no_commit" else (value,)
        )
        if value not in _NON_TERMINAL_MM_STATES
        and value not in _TERMINAL_EXECUTION_STATUSES_BY_MM_STATE
    ]
    parts: list[str] = []
    mm_clause = _any_temporal_query("mm_state", non_terminal)
    if mm_clause is not None:
        parts.append(f'({mm_clause} AND ExecutionStatus="Running")')
    status_clause = _any_temporal_query("ExecutionStatus", terminal_statuses)
    if status_clause is not None:
        parts.append(status_clause)
    unknown_clause = _any_temporal_query("mm_state", unknown)
    if unknown_clause is not None:
        parts.append(unknown_clause)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return "(" + " OR ".join(parts) + ")"


def _append_state_temporal_filter(
    query_parts: list[str],
    *,
    exact: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> None:
    exact_value = str(exact or "").strip()
    if exact_value:
        if len(exact_value) > _EXECUTION_FILTER_VALUE_MAX_LENGTH:
            raise TemporalExecutionValidationError(
                f"mm_state exact value must be {_EXECUTION_FILTER_VALUE_MAX_LENGTH} characters or fewer."
            )
        clause = _state_include_clause([exact_value])
        if clause is not None:
            query_parts.append(clause)
        return
    include_clause = _state_include_clause(include or [])
    if include_clause is not None:
        query_parts.append(include_clause)
    for value in exclude or []:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        if normalized in _NON_TERMINAL_MM_STATES:
            escaped = _escape_temporal_value(normalized)
            query_parts.append(
                f'(mm_state!="{escaped}" OR ExecutionStatus!="Running")'
            )
            continue
        terminal_statuses = _TERMINAL_EXECUTION_STATUSES_BY_MM_STATE.get(
            normalized, ()
        )
        if terminal_statuses:
            for status_value in terminal_statuses:
                escaped = _escape_temporal_value(status_value)
                query_parts.append(f'ExecutionStatus!="{escaped}"')
            continue
        excluded_values = (
            ("no_commit", "no_changes") if normalized == "no_commit" else (normalized,)
        )
        for excluded_value in excluded_values:
            query_parts.append(
                f'mm_state!="{_escape_temporal_value(excluded_value)}"'
            )


def _append_exact_or_multi_temporal_filter(
    query_parts: list[str],
    attr: str,
    *,
    exact: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> None:
    exact_value = str(exact or "").strip()
    if exact_value:
        if len(exact_value) > _EXECUTION_FILTER_VALUE_MAX_LENGTH:
            raise TemporalExecutionValidationError(
                f"{attr} exact value must be {_EXECUTION_FILTER_VALUE_MAX_LENGTH} characters or fewer."
            )
        query_parts.append(f'{attr}="{_escape_temporal_value(exact_value)}"')
    include_query = _any_temporal_query(attr, include or [])
    if include_query and not exact_value:
        query_parts.append(include_query)
    for value in exclude or []:
        query_parts.append(f'{attr}!="{_escape_temporal_value(value)}"')


def _append_datetime_temporal_filter(
    query_parts: list[str],
    attr: str,
    start_raw: str | None,
    end_raw: str | None,
    *,
    start_alias: str,
    end_alias: str,
    blank_mode: str | None = None,
) -> None:
    range_parts = _datetime_range_parts(
        attr,
        start_raw,
        end_raw,
        start_alias=start_alias,
        end_alias=end_alias,
    )
    blank_value = _validate_blank_mode(blank_mode, alias=f"{attr} blank")
    if blank_value == "include":
        null_query = f"{attr} IS NULL"
        if range_parts:
            range_query = " AND ".join(range_parts)
            query_parts.append(f"({null_query} OR ({range_query}))")
        else:
            query_parts.append(null_query)
        return
    if blank_value == "exclude":
        query_parts.append(f"{attr} IS NOT NULL")
    query_parts.extend(range_parts)


def _append_prefix_temporal_filter(
    query_parts: list[str],
    attr: str,
    raw: str | None,
    *,
    alias: str,
) -> None:
    # Temporal SQL (PostgreSQL) visibility does not support the LIKE operator;
    # substring matching is unavailable. STARTS_WITH is the supported prefix
    # operator for Keyword attributes (and built-ins like WorkflowId).
    value = _normalize_text_filter(raw, alias=alias)
    if value:
        query_parts.append(f'{attr} STARTS_WITH "{_escape_temporal_value(value)}"')


def _append_word_match_temporal_filter(
    query_parts: list[str],
    attr: str,
    raw: str | None,
    *,
    alias: str,
) -> None:
    # Temporal SQL visibility supports neither LIKE nor substring matching, and
    # the custom-Keyword budget is full, so the workflow title is stored as a
    # KeywordList of word tokens (mm_title) and matched by membership with `=`.
    # Tokenize the operator's text the same way the workflow does (see
    # title_search.tokenize_title) and AND the tokens so every typed word must
    # be present in the title.
    value = _normalize_text_filter(raw, alias=alias)
    if not value:
        return
    tokens = tokenize_title(value)
    if not tokens:
        raise TemporalExecutionValidationError(
            f"{alias} must contain at least one alphanumeric word token."
        )
    for token in tokens:
        query_parts.append(f'{attr} = "{_escape_temporal_value(token)}"')


def _build_temporal_execution_query(
    *,
    request: Request,
    workflow_type: str | None,
    state: str | None,
    state_in: str | None,
    state_not_in: str | None,
    entry: str | None,
    repo: str | None,
    repo_exact: str | None,
    repo_in: str | None,
    repo_not_in: str | None,
    integration: str | None,
    target_runtime: str | None,
    target_runtime_in: str | None,
    target_runtime_not_in: str | None,
    target_skill_in: str | None,
    target_skill_not_in: str | None,
    scheduled_from: str | None,
    scheduled_to: str | None,
    scheduled_blank: str | None,
    updated_from: str | None,
    updated_to: str | None,
    created_from: str | None,
    created_to: str | None,
    finished_from: str | None,
    finished_to: str | None,
    finished_blank: str | None,
    scope: str | None,
    owner_type: str | None,
    owner_id: str | None,
    sort: str | None = None,
    sort_dir: str | None = None,
    exclude_aliases: frozenset[str] = frozenset(),
    include_order: bool = False,
    usable_search_attributes: frozenset[str] | None = None,
) -> tuple[str, str]:
    query_parts: list[str] = []
    usable_attrs = usable_search_attributes or frozenset()

    def excluded(alias: str) -> bool:
        return alias in exclude_aliases

    def attr_available(attr_name: str) -> bool:
        return attr_name not in _OPTIONAL_TEMPORAL_SEARCH_ATTRIBUTES or attr_name in usable_attrs

    state_in_values = [] if excluded("stateIn") else _raw_query_values(request, "stateIn", state_in)
    state_not_in_values = [] if excluded("stateNotIn") else _raw_query_values(request, "stateNotIn", state_not_in)
    repo_in_values = [] if excluded("repoIn") else _raw_query_values(request, "repoIn", repo_in)
    repo_not_in_values = [] if excluded("repoNotIn") else _raw_query_values(request, "repoNotIn", repo_not_in)
    target_runtime_available = attr_available("mm_target_runtime")
    target_skill_available = attr_available("mm_target_skill")
    target_runtime_in_values = [] if excluded("targetRuntimeIn") or not target_runtime_available else _raw_query_values(request, "targetRuntimeIn", target_runtime_in)
    target_runtime_not_in_values = [] if excluded("targetRuntimeNotIn") or not target_runtime_available else _raw_query_values(request, "targetRuntimeNotIn", target_runtime_not_in)
    target_skill_in_values = [] if excluded("targetSkillIn") or not target_skill_available else _raw_query_values(request, "targetSkillIn", target_skill_in)
    target_skill_not_in_values = [] if excluded("targetSkillNotIn") or not target_skill_available else _raw_query_values(request, "targetSkillNotIn", target_skill_not_in)

    _validate_non_contradictory_values("stateIn", state_in_values, "stateNotIn", state_not_in_values)
    _validate_non_contradictory_values("repoIn", repo_in_values, "repoNotIn", repo_not_in_values)
    _validate_non_contradictory_values(
        "targetRuntimeIn",
        target_runtime_in_values,
        "targetRuntimeNotIn",
        target_runtime_not_in_values,
    )
    _validate_non_contradictory_values(
        "targetSkillIn",
        target_skill_in_values,
        "targetSkillNotIn",
        target_skill_not_in_values,
    )
    _validate_progress_request_params(request, sort=sort, sort_dir=sort_dir)

    temporal_scope = _normalize_temporal_list_scope(
        scope,
        workflow_type=workflow_type,
        entry=entry,
    )
    scope_query = _TEMPORAL_SCOPE_QUERIES[temporal_scope]
    if scope_query:
        query_parts.append(scope_query)
    if workflow_type and not _is_user_workflow_list_type(workflow_type):
        logger.info(
            "Ignoring workflowType=%s for ordinary workflow-list temporal query",
            workflow_type,
        )
    elif workflow_type and not scope_query:
        query_parts.append(f'WorkflowType="{_escape_temporal_value(workflow_type)}"')
    legacy_state_exact = None
    if state and not excluded("state") and not (state_in_values or state_not_in_values):
        legacy_state_exact = state
    _append_state_temporal_filter(
        query_parts,
        exact=legacy_state_exact,
        include=state_in_values,
        exclude=state_not_in_values,
    )
    if entry and not _is_user_workflow_list_entry(entry):
        logger.info("Ignoring entry=%s for ordinary workflow-list temporal query", entry)
    elif entry and not scope_query:
        query_parts.append(f'mm_entry="{_escape_temporal_value(entry)}"')
    if owner_type:
        query_parts.append(f'mm_owner_type="{_escape_temporal_value(owner_type)}"')
    if owner_id:
        query_parts.append(f'mm_owner_id="{_escape_temporal_value(owner_id)}"')
    _append_exact_or_multi_temporal_filter(
        query_parts,
        "mm_repo",
        exact=None if excluded("repoExact") or excluded("repo") else repo_exact or repo,
        include=repo_in_values,
        exclude=repo_not_in_values,
    )
    if not excluded("repoContains"):
        _append_prefix_temporal_filter(
            query_parts, "mm_repo", request.query_params.get("repoContains"), alias="repoContains"
        )
    if integration and not excluded("integration"):
        query_parts.append(f'mm_integration="{_escape_temporal_value(integration)}"')
    if target_runtime_available:
        _append_exact_or_multi_temporal_filter(
            query_parts,
            "mm_target_runtime",
            exact=None if excluded("targetRuntime") else target_runtime,
            include=target_runtime_in_values,
            exclude=target_runtime_not_in_values,
        )
    if target_skill_available:
        _append_exact_or_multi_temporal_filter(
            query_parts,
            "mm_target_skill",
            include=target_skill_in_values,
            exclude=target_skill_not_in_values,
        )
    if not excluded("workflowId"):
        _append_exact_or_multi_temporal_filter(
            query_parts,
            "WorkflowId",
            exact=request.query_params.get("workflowId"),
        )
    if not excluded("workflowIdContains"):
        _append_prefix_temporal_filter(
            query_parts,
            "WorkflowId",
            request.query_params.get("workflowIdContains"),
            alias="workflowIdContains",
        )
    if attr_available("mm_title") and not excluded("titleContains"):
        _append_word_match_temporal_filter(
            query_parts,
            "mm_title",
            request.query_params.get("titleContains"),
            alias="titleContains",
        )
    _append_datetime_temporal_filter(
        query_parts,
        "mm_scheduled_for",
        None if excluded("scheduledFrom") else scheduled_from,
        None if excluded("scheduledTo") else scheduled_to,
        start_alias="scheduledFrom",
        end_alias="scheduledTo",
        blank_mode=None if excluded("scheduledBlank") else scheduled_blank,
    )
    if not excluded("updatedFrom") and not excluded("updatedTo"):
        _append_updated_temporal_filter(query_parts, updated_from, updated_to)
    _append_datetime_temporal_filter(
        query_parts,
        "StartTime",
        None if excluded("createdFrom") else created_from,
        None if excluded("createdTo") else created_to,
        start_alias="createdFrom",
        end_alias="createdTo",
    )
    _append_datetime_temporal_filter(
        query_parts,
        "CloseTime",
        None if excluded("finishedFrom") else finished_from,
        None if excluded("finishedTo") else finished_to,
        start_alias="finishedFrom",
        end_alias="finishedTo",
        blank_mode=None if excluded("finishedBlank") else finished_blank,
    )

    count_query = " AND ".join(query_parts) if query_parts else ""
    list_query = count_query
    if include_order and sort:
        if sort in {"progress", "progressPct"}:
            return count_query, list_query
        sort_attr = _EXECUTION_SORT_FIELDS.get(sort)
        if sort_attr is None:
            raise TemporalExecutionValidationError(
                "sort must be one of: " + ", ".join(sorted(_EXECUTION_SORT_FIELDS)) + "."
            )
        if sort_attr in _UNSORTABLE_TEMPORAL_SEARCH_ATTRIBUTES:
            return count_query, list_query
        if sort_attr in _OPTIONAL_TEMPORAL_SEARCH_ATTRIBUTES and sort_attr not in usable_attrs:
            return count_query, list_query
        direction = str(sort_dir or "desc").strip().lower()
        if direction not in {"asc", "desc"}:
            raise TemporalExecutionValidationError("sortDir must be one of: asc, desc.")
        order_clause = f"ORDER BY {sort_attr} {direction.upper()}"
        list_query = f"{count_query} {order_clause}".strip()
    elif sort_dir and str(sort_dir).strip().lower() not in {"asc", "desc"}:
        raise TemporalExecutionValidationError("sortDir must be one of: asc, desc.")
    return count_query, list_query


def _and_temporal_query(base_query: str, clause: str) -> str:
    return f"{base_query} AND {clause}" if base_query else clause


def _decode_execution_facet_page_token(next_page_token: str | None) -> bytes | None:
    if not next_page_token:
        return None
    try:
        return base64.b64decode(next_page_token, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TemporalExecutionValidationError(
            "nextPageToken must be a valid base64 token."
        ) from exc


def _decode_execution_status_facet_offset(next_page_token: str | None) -> int:
    token_bytes = _decode_execution_facet_page_token(next_page_token)
    if token_bytes is None:
        return 0
    try:
        offset = int(token_bytes.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TemporalExecutionValidationError(
            "nextPageToken must be a valid status facet page token."
        ) from exc
    if offset < 0:
        raise TemporalExecutionValidationError(
            "nextPageToken must be a valid status facet page token."
        )
    return offset


def _encode_execution_status_facet_offset(offset: int | None) -> str | None:
    if offset is None:
        return None
    return base64.b64encode(str(offset).encode("ascii")).decode("utf-8")


def _facet_label(facet: str, value: str) -> str:
    if facet == "targetRuntime":
        labels = {
            "codex_cli": "Codex CLI",
            "claude_code": "Claude Code",
            "codex_cloud": "Codex Cloud",
        }
        return labels.get(value, value.replace("_", " ").title())
    if facet == "status":
        return value.replace("_", " ").title()
    return value


async def _facet_value_from_workflow(workflow: object, facet: str) -> str:
    search_attributes = getattr(workflow, "search_attributes", {}) or {}
    if facet == "status":
        return _coerce_temporal_scalar(search_attributes.get("mm_state"))
    if facet == "repository":
        return _coerce_temporal_scalar(
            search_attributes.get("mm_repository") or search_attributes.get("mm_repo")
        )
    if facet == "integration":
        return _coerce_temporal_scalar(search_attributes.get("mm_integration"))
    if facet == "targetRuntime":
        value = _coerce_temporal_scalar(
            search_attributes.get("mm_target_runtime")
            or search_attributes.get("mm_runtime")
            or search_attributes.get("runtime")
        )
        if value:
            return value
    if facet == "targetSkill":
        value = _coerce_temporal_scalar(
            search_attributes.get("mm_target_skill")
            or search_attributes.get("mm_skill_id")
            or search_attributes.get("mm_skill")
            or search_attributes.get("skillId")
            or search_attributes.get("skill")
        )
        if value:
            return value
    memo_fn = getattr(workflow, "memo", None)
    memo: object | None = None
    if callable(memo_fn):
        maybe_memo = memo_fn()
        if hasattr(maybe_memo, "__await__"):
            memo = await maybe_memo
        else:
            memo = maybe_memo
    if not isinstance(memo, Mapping):
        return ""
    if facet == "targetRuntime":
        return _coerce_temporal_scalar(
            memo.get("targetRuntime") or memo.get("target_runtime")
        )
    if facet == "targetSkill":
        return _coerce_temporal_scalar(
            memo.get("targetSkill")
            or memo.get("target_skill")
            or memo.get("skillId")
            or memo.get("skill")
        )
    return ""


def _canonicalize_execution_identifier(raw_identifier: str) -> tuple[str, bool]:
    canonical = TemporalExecutionRecord.canonicalize_identifier(raw_identifier)
    return canonical, canonical != raw_identifier

def _mark_execution_alias_usage(
    response: Response, *, raw_identifier: str, canonical_identifier: str
) -> None:
    if raw_identifier == canonical_identifier:
        return
    response.headers["Deprecation"] = "true"
    response.headers["X-MoonMind-Canonical-WorkflowId"] = canonical_identifier
    response.headers["X-MoonMind-Deprecated-Identifier"] = raw_identifier

def _compatibility_refreshed_at(record, now: datetime | None = None) -> datetime:
    refreshed_at = (
        getattr(record, "last_synced_at", None)
        or getattr(record, "updated_at", None)
        or getattr(record, "started_at", None)
        or getattr(record, "created_at", None)
        or (now if now is not None else datetime.now(UTC))
    )
    if isinstance(refreshed_at, str):
        refreshed_at = datetime.fromisoformat(refreshed_at.replace("Z", "+00:00"))
    if refreshed_at.tzinfo is not None:
        return refreshed_at
    return refreshed_at.replace(tzinfo=UTC)

def _manifest_attr(manifest_status, field: str, default=None):
    return getattr(manifest_status, field, default) if manifest_status else default

def _normalize_owner_type(record, search_attributes: dict[str, object]) -> str:
    owner_type = str(search_attributes.get("mm_owner_type") or "").strip().lower()
    if owner_type in _ALLOWED_OWNER_TYPES:
        return owner_type
    owner_id = str(record.owner_id or "").strip().lower()
    return "system" if owner_id == "system" or not owner_id else "user"

def _coerce_temporal_scalar(value: object | None) -> str:
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _coerce_temporal_scalar(item)
            if text:
                return text
        return ""
    if isinstance(value, Mapping):
        return ""
    if value is None:
        return ""
    return str(value).strip()

def _dedupe_non_blank(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped

def _skill_selector_names(raw: object | None) -> list[str] | None:
    if isinstance(raw, list):
        return _dedupe_non_blank([_coerce_temporal_scalar(item) for item in raw]) or None
    if not isinstance(raw, Mapping):
        return None

    names: list[str] = []
    sets = raw.get("sets")
    if isinstance(sets, list):
        names.extend(_coerce_temporal_scalar(item) for item in sets)
    include = raw.get("include")
    if isinstance(include, list):
        for item in include:
            if isinstance(item, Mapping):
                names.append(_coerce_temporal_scalar(item.get("name")))
            else:
                names.append(_coerce_temporal_scalar(item))
    return _dedupe_non_blank(names) or None

def _preset_primary_skill_name(task_payload: Mapping[str, Any]) -> str | None:
    task_template = task_payload.get("taskTemplate") or task_payload.get(
        "task_template"
    )
    if isinstance(task_template, Mapping):
        candidate = _coerce_temporal_scalar(
            task_template.get("slug")
            or task_template.get("name")
            or task_template.get("id")
        )
        if candidate:
            return candidate

    applied_templates = task_payload.get("appliedStepTemplates") or task_payload.get(
        "applied_step_templates"
    )
    if isinstance(applied_templates, list):
        for item in reversed(applied_templates):
            if not isinstance(item, Mapping):
                continue
            candidate = _coerce_temporal_scalar(
                item.get("slug") or item.get("name") or item.get("id")
            )
            if candidate:
                return candidate

    return None

def _first_mapping(*candidates: object | None) -> Mapping[str, Any]:
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            return candidate
    return {}

def _coerce_skill_bool(value: object | None) -> bool | None:
    if isinstance(value, bool):
        return value
    return None

def _mapping_value(raw: Mapping[str, Any], *keys: str) -> object | None:
    for key in keys:
        if key in raw:
            return raw[key]
    return None

def _selected_skill_evidence(raw: object | None) -> list[ExecutionSkillEvidenceSummaryModel]:
    if not isinstance(raw, list):
        return []
    evidence: list[ExecutionSkillEvidenceSummaryModel] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        name = _coerce_temporal_scalar(
            item.get("name")
            or item.get("skillName")
            or item.get("skill_name")
        )
        if not name:
            continue
        evidence.append(
            ExecutionSkillEvidenceSummaryModel(
                name=name,
                sourceKind=(
                    _coerce_temporal_scalar(
                        item.get("sourceKind") or item.get("source_kind")
                    )
                    or None
                ),
                sourcePath=(
                    _coerce_temporal_scalar(
                        item.get("sourcePath") or item.get("source_path")
                    )
                    or None
                ),
                contentRef=(
                    _coerce_temporal_scalar(
                        item.get("contentRef") or item.get("content_ref")
                    )
                    or None
                ),
                contentDigest=(
                    _coerce_temporal_scalar(
                        item.get("contentDigest") or item.get("content_digest")
                    )
                    or None
                ),
            )
        )
    return evidence

def _skill_provenance_from_evidence(
    evidence: list[ExecutionSkillEvidenceSummaryModel],
) -> list[ExecutionSkillProvenanceModel]:
    provenance: list[ExecutionSkillProvenanceModel] = []
    for entry in evidence:
        if not entry.source_kind and not entry.source_path:
            continue
        provenance.append(
            ExecutionSkillProvenanceModel(
                name=entry.name,
                sourceKind=entry.source_kind,
                sourcePath=entry.source_path,
            )
        )
    return provenance

def _skill_source_provenance(
    raw: object | None,
    evidence: list[ExecutionSkillEvidenceSummaryModel],
) -> list[ExecutionSkillProvenanceModel]:
    provenance: list[ExecutionSkillProvenanceModel] = []
    seen: set[tuple[str, str | None, str | None]] = set()

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            name = _coerce_temporal_scalar(
                item.get("name")
                or item.get("skillName")
                or item.get("skill_name")
            )
            if not name:
                continue
            source_kind = (
                _coerce_temporal_scalar(
                    item.get("sourceKind") or item.get("source_kind")
                )
                or None
            )
            source_path = (
                _coerce_temporal_scalar(
                    item.get("sourcePath") or item.get("source_path")
                )
                or None
            )
            key = (name, source_kind, source_path)
            if key in seen:
                continue
            seen.add(key)
            provenance.append(
                ExecutionSkillProvenanceModel(
                    name=name,
                    sourceKind=source_kind,
                    sourcePath=source_path,
                )
            )

    for entry in _skill_provenance_from_evidence(evidence):
        key = (entry.name, entry.source_kind, entry.source_path)
        if key in seen:
            continue
        seen.add(key)
        provenance.append(entry)
    return provenance

def _projection_diagnostic(raw: object | None) -> ExecutionProjectionDiagnosticModel | None:
    if not isinstance(raw, Mapping):
        return None
    diagnostic = ExecutionProjectionDiagnosticModel(
        path=_coerce_temporal_scalar(raw.get("path")) or None,
        objectKind=(
            _coerce_temporal_scalar(raw.get("objectKind") or raw.get("object_kind"))
            or None
        ),
        attemptedAction=(
            _coerce_temporal_scalar(
                raw.get("attemptedAction") or raw.get("attempted_action")
            )
            or None
        ),
        remediation=_coerce_temporal_scalar(raw.get("remediation")) or None,
        cause=_coerce_temporal_scalar(raw.get("cause")) or None,
    )
    if any(
        [
            diagnostic.path,
            diagnostic.object_kind,
            diagnostic.attempted_action,
            diagnostic.remediation,
            diagnostic.cause,
        ]
    ):
        return diagnostic
    return None

def _skill_lifecycle_intent(
    *,
    params: Mapping[str, Any],
    task_skills: list[str] | None,
    resolved_skillset_ref: str | None,
) -> ExecutionSkillLifecycleIntentModel | None:
    raw = _first_mapping(params.get("skillLifecycleIntent"))
    source = _coerce_temporal_scalar(raw.get("source")) or "proposal"
    resolution_mode = _coerce_temporal_scalar(raw.get("resolutionMode"))
    explanation = _coerce_temporal_scalar(raw.get("explanation"))
    selectors = _skill_selector_names(raw.get("selectors")) or task_skills or []
    lifecycle_ref = (
        _coerce_temporal_scalar(
            raw.get("resolvedSkillsetRef") or raw.get("resolved_skillset_ref")
        )
        or resolved_skillset_ref
    )

    if not resolution_mode:
        if lifecycle_ref:
            resolution_mode = "snapshot-reuse"
        elif selectors:
            resolution_mode = "selector-based"
        else:
            resolution_mode = "inherited-defaults"
    if not explanation:
        if resolution_mode == "snapshot-reuse":
            explanation = (
                "Execution reuses the resolved skill snapshot unless explicit "
                "re-resolution is requested."
            )
        elif selectors:
            explanation = "Execution resolves the selected skills when the run starts."
        else:
            explanation = "Execution inherits deployment skill defaults explicitly."

    if not (selectors or lifecycle_ref or raw):
        return None
    return ExecutionSkillLifecycleIntentModel(
        source=source,
        selectors=selectors,
        resolvedSkillsetRef=lifecycle_ref,
        resolutionMode=resolution_mode,
        explanation=explanation,
    )

def _skill_runtime_evidence(
    *,
    params: Mapping[str, Any],
    task_payload: Mapping[str, Any],
    task_skills: list[str] | None,
    resolved_skillset_ref: str | None,
) -> ExecutionSkillRuntimeModel | None:
    materialized = _first_mapping(
        params.get("skillRuntime"),
        params.get("skillsMaterialized"),
        task_payload.get("skillRuntime"),
    )
    selected_skills = _skill_selector_names(
        materialized.get("selectedSkills") if materialized else None
    )
    if selected_skills is None:
        selected_skills = _skill_selector_names(
            materialized.get("activeSkills") if materialized else None
        )
    if selected_skills is None:
        selected_skills = task_skills or []

    evidence = _selected_skill_evidence(
        (
            materialized.get("selectedEvidence")
            or materialized.get("selectedVersions")
            or materialized.get("skills")
        )
        if materialized
        else None
    )
    provenance = _skill_source_provenance(
        (
            materialized.get("sourceProvenance")
            or materialized.get("source_provenance")
        )
        if materialized
        else None,
        evidence,
    )
    task_skills_payload = _first_mapping(task_payload.get("skills"))
    materialization_mode = (
        _coerce_temporal_scalar(
            materialized.get("materializationMode")
            or materialized.get("materialization_mode")
        )
        or _coerce_temporal_scalar(
            task_skills_payload.get("materializationMode")
            or task_skills_payload.get("materialization_mode")
            or task_payload.get("materializationMode")
            or task_payload.get("materialization_mode")
        )
        or None
    )
    lifecycle_intent = _skill_lifecycle_intent(
        params=params,
        task_skills=task_skills,
        resolved_skillset_ref=resolved_skillset_ref,
    )
    runtime_ref = (
        _coerce_temporal_scalar(
            materialized.get("resolvedSkillsetRef")
            or materialized.get("resolved_skillset_ref")
        )
        or resolved_skillset_ref
    )

    if not any([runtime_ref, selected_skills, evidence, materialized, lifecycle_intent]):
        return None

    return ExecutionSkillRuntimeModel(
        resolvedSkillsetRef=runtime_ref,
        selectedSkills=selected_skills,
        selectedEvidence=evidence,
        sourceProvenance=provenance,
        materializationMode=materialization_mode,
        visiblePath=(
            _coerce_temporal_scalar(
                materialized.get("visiblePath") or materialized.get("visible_path")
            )
            or None
        ),
        backingPath=(
            _coerce_temporal_scalar(
                materialized.get("backingPath") or materialized.get("backing_path")
            )
            or None
        ),
        readOnly=_coerce_skill_bool(
            _mapping_value(materialized, "readOnly", "read_only")
        ),
        manifestRef=(
            _coerce_temporal_scalar(
                materialized.get("manifestRef")
                or materialized.get("manifest_ref")
                or materialized.get("manifestPath")
                or materialized.get("manifest_path")
            )
            or None
        ),
        promptIndexRef=(
            _coerce_temporal_scalar(
                materialized.get("promptIndexRef")
                or materialized.get("prompt_index_ref")
            )
            or None
        ),
        activationSummaryRef=(
            _coerce_temporal_scalar(
                materialized.get("activationSummaryRef")
                or materialized.get("activation_summary_ref")
            )
            or None
        ),
        diagnostics=_projection_diagnostic(materialized.get("diagnostics")),
        lifecycleIntent=lifecycle_intent,
    )

def _normalize_entry_value(value: object | None) -> str | None:
    candidate = _coerce_temporal_scalar(value).lower()
    if not candidate:
        return None
    if candidate == "user_workflow":
        return candidate
    if candidate == "run":
        return "user_workflow"
    if candidate == "manifest":
        return candidate

    # Some Temporal payloads surface keyword attributes as arrays; if those are
    # later stringified we may see values like "['run']" or '["run"]'.
    if candidate.startswith("[") and candidate.endswith("]"):
        inner = candidate[1:-1].strip()
        if inner:
            first = inner.split(",", 1)[0].strip().strip("'\"")
            if first == "user_workflow":
                return first
            if first == "run":
                return "user_workflow"
            if first == "manifest":
                return first
    return None

def _normalize_github_pull_request_url(value: object | None) -> str | None:
    candidate = _coerce_temporal_scalar(value)
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or parsed.netloc.lower() != "github.com":
        return None
    normalized_path = parsed.path.rstrip("/")
    if not _GITHUB_PULL_REQUEST_PATH_PATTERN.fullmatch(normalized_path):
        return None
    return f"https://github.com{normalized_path}"

def _extract_execution_pr_url(
    memo: Mapping[str, object],
    search_attributes: Mapping[str, object],
    params: Mapping[str, object],
) -> str | None:
    sources = {
        "memo": memo,
        "search_attributes": search_attributes,
        "params": params,
    }
    for source_name, key in _PR_URL_CANDIDATE_SOURCES:
        value = sources[source_name].get(key)
        normalized = _normalize_github_pull_request_url(value)
        if normalized:
            return normalized
    return None

def _resolve_execution_entry(record, search_attributes: dict[str, object]) -> str:
    entry = _normalize_entry_value(search_attributes.get("mm_entry"))
    if entry:
        return entry

    entry = _normalize_entry_value(getattr(record, "entry", ""))
    if entry:
        return entry

    workflow_type = str(
        getattr(getattr(record, "workflow_type", None), "value", "")
    ).lower()
    if workflow_type.endswith("manifestingest"):
        return "manifest"
    return "user_workflow"

async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> TemporalExecutionService:
    return TemporalExecutionService(
        session,
        namespace=settings.temporal.namespace,
        integration_task_queue=settings.temporal.activity_integrations_task_queue,
        integration_poll_initial_seconds=(
            settings.temporal.integration_poll_initial_seconds
        ),
        integration_poll_max_seconds=settings.temporal.integration_poll_max_seconds,
        integration_poll_jitter_ratio=settings.temporal.integration_poll_jitter_ratio,
        run_continue_as_new_step_threshold=(
            settings.temporal.run_continue_as_new_step_threshold
        ),
        run_continue_as_new_wait_cycle_threshold=(
            settings.temporal.run_continue_as_new_wait_cycle_threshold
        ),
        manifest_continue_as_new_phase_threshold=(
            settings.temporal.manifest_continue_as_new_phase_threshold
        ),
    )

def _ensure_actions_enabled() -> None:
    """FastAPI dependency: raise 403 when Temporal execution actions are disabled."""
    if not settings.temporal_dashboard.actions_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "actions_disabled",
                "message": "Temporal execution actions are disabled.",
            },
        )

def _ensure_submit_enabled() -> None:
    """FastAPI dependency: raise 503 when Temporal execution submission is disabled."""
    if not settings.temporal_dashboard.submit_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "temporal_submit_disabled",
                "message": (
                    "Temporal workflow submission is disabled "
                    "(temporal_dashboard.submit_enabled=False). "
                    "The legacy queue execution substrate is no longer supported. "
                    "Enable Temporal submission to proceed."
                ),
            },
        )

def _derive_full_workflow_instructions(task_payload: Mapping[str, Any]) -> str | None:
    sections: list[str] = []
    task_instructions = str(task_payload.get("instructions") or "").strip()
    if task_instructions:
        sections.append(task_instructions)

    raw_steps = task_payload.get("steps")
    if isinstance(raw_steps, list):
        for index, item in enumerate(raw_steps, start=1):
            if not isinstance(item, Mapping):
                continue
            step_instructions = str(item.get("instructions") or "").strip()
            if not step_instructions:
                continue
            step_title = str(item.get("title") or "").strip()
            label = f"Step {index}"
            if step_title:
                label = f"{label}: {step_title}"
            sections.append(f"{label}\n{step_instructions}")

    if sections:
        return "\n\n".join(sections)
    return None

def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        candidate = str(item or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized

def _normalize_merge_automation_visibility_payload(
    payload: Any,
) -> ExecutionMergeAutomationModel | None:
    if not isinstance(payload, Mapping):
        return None
    normalized = dict(payload)
    workflow_id = (
        _coerce_temporal_scalar(normalized.get("workflowId"))
        or _coerce_temporal_scalar(normalized.get("childWorkflowId"))
        or _coerce_temporal_scalar(normalized.get("mergeAutomationWorkflowId"))
    )
    if workflow_id:
        normalized["workflowId"] = workflow_id
        normalized["childWorkflowId"] = workflow_id

    normalized["resolverChildWorkflowIds"] = _normalize_string_list(
        normalized.get("resolverChildWorkflowIds")
    )
    blockers = normalized.get("blockers")
    normalized["blockers"] = blockers if isinstance(blockers, list) else []
    artifact_refs = normalized.get("artifactRefs")
    if isinstance(artifact_refs, Mapping):
        artifact_refs = dict(artifact_refs)
        artifact_refs["gateSnapshots"] = _normalize_string_list(
            artifact_refs.get("gateSnapshots")
        )
        artifact_refs["resolverAttempts"] = _normalize_string_list(
            artifact_refs.get("resolverAttempts")
        )
        normalized["artifactRefs"] = artifact_refs
    elif artifact_refs is not None:
        normalized["artifactRefs"] = None

    normalized["resolverChildren"] = (
        normalized.get("resolverChildren")
        if isinstance(normalized.get("resolverChildren"), list)
        else []
    )
    normalized.setdefault("enabled", True)

    try:
        return ExecutionMergeAutomationModel.model_validate(normalized)
    except ValidationError as exc:
        logger.warning(
            "Invalid merge automation visibility payload: %s",
            exc,
            exc_info=True,
        )
        return None

def _bounded_execution_progress_from_sources(
    *,
    record: object,
    memo: Mapping[str, object] | None,
    finish_summary: Mapping[str, object] | None,
) -> ExecutionProgressModel | None:
    sources = (
        getattr(record, "progress", None),
        memo.get("progress") if isinstance(memo, Mapping) else None,
        finish_summary.get("progress") if isinstance(finish_summary, Mapping) else None,
    )
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        payload = {
            "total": source.get("total"),
            "pending": source.get("pending"),
            "ready": source.get("ready"),
            "executing": source.get("executing")
            if source.get("executing") is not None
            else source.get("running"),
            "awaitingExternal": source.get("awaitingExternal")
            if source.get("awaitingExternal") is not None
            else source.get("awaiting_external"),
            "reviewing": source.get("reviewing"),
            "completed": source.get("completed")
            if source.get("completed") is not None
            else source.get("succeeded"),
            "failed": source.get("failed"),
            "skipped": source.get("skipped"),
            "canceled": source.get("canceled"),
            "currentStepTitle": source.get("currentStepTitle")
            if source.get("currentStepTitle") is not None
            else source.get("current_step_title"),
            "updatedAt": source.get("updatedAt")
            or source.get("updated_at")
            or getattr(record, "updated_at", None),
        }
        try:
            return ExecutionProgressModel.model_validate(payload)
        except ValidationError:
            logger.warning(
                "Invalid bounded progress payload for execution %s",
                getattr(record, "workflow_id", "<unknown>"),
                exc_info=True,
            )
    return None


def _serialize_execution_list_item(record) -> ExecutionListItemModel:
    close_status = _enum_value(getattr(record, "close_status", None))
    if close_status == TemporalExecutionCloseStatus.COMPLETED.value:
        temporal_status = "completed"
    elif close_status == TemporalExecutionCloseStatus.CANCELED.value:
        temporal_status = "canceled"
    elif close_status in {
        TemporalExecutionCloseStatus.FAILED.value,
        TemporalExecutionCloseStatus.TERMINATED.value,
        TemporalExecutionCloseStatus.TIMED_OUT.value,
    }:
        temporal_status = "failed"
    else:
        temporal_status = "running"

    memo = dict(getattr(record, "memo", None) or {})
    search_attributes = dict(getattr(record, "search_attributes", None) or {})
    state_value = _enum_value(getattr(record, "state", None)) or ""
    workflow_type_value = _enum_value(getattr(record, "workflow_type", None)) or ""
    continue_as_new_cause = memo.get("continue_as_new_cause") or search_attributes.get(
        "mm_continue_as_new_cause"
    )
    raw_state = state_value
    owner_type = _normalize_owner_type(record, search_attributes)
    owner_id = str(
        search_attributes.get("mm_owner_id")
        or getattr(record, "owner_id", None)
        or "system"
    )
    title = str(memo.get("title") or "").strip() or workflow_type_value

    waiting_reason = (
        str(getattr(record, "waiting_reason", "") or "").strip()
        or str(memo.get("waiting_reason") or "").strip()
        or (
            str(memo.get("summary") or "").strip()
            if raw_state == "awaiting_external"
            else ""
        )
    )
    attention_required = bool(
        getattr(record, "attention_required", False)
        or memo.get("attention_required")
        or False
    )
    if raw_state == "awaiting_external":
        attention_required = True
    if (
        raw_state
        not in {"awaiting_slot", "awaiting_external", "waiting_on_dependencies"}
        and not bool(getattr(record, "paused", False))
        and not attention_required
    ):
        waiting_reason = None

    params_raw = getattr(record, "parameters", None)
    params = dict(params_raw) if isinstance(params_raw, Mapping) else {}
    target_runtime = _coerce_temporal_scalar(params.get("targetRuntime")) or None
    if not target_runtime:
        runtime_nested = params.get("runtime")
        if isinstance(runtime_nested, Mapping):
            target_runtime = _coerce_temporal_scalar(runtime_nested.get("mode")) or None
    if not target_runtime:
        target_runtime = (
            _coerce_temporal_scalar(search_attributes.get("mm_target_runtime"))
            or _coerce_temporal_scalar(search_attributes.get("mm_runtime"))
            or _coerce_temporal_scalar(search_attributes.get("runtime"))
            or _coerce_temporal_scalar(memo.get("targetRuntime"))
            or _coerce_temporal_scalar(memo.get("target_runtime"))
        ) or None

    task_payload = _workflow_payload_from_parameters(params)
    task_runtime_payload = task_payload.get("runtime")
    if not isinstance(task_runtime_payload, Mapping):
        task_runtime_payload = {}
    if not target_runtime:
        target_runtime = (
            _coerce_temporal_scalar(task_runtime_payload.get("mode"))
            or _coerce_temporal_scalar(task_runtime_payload.get("runtime"))
        ) or None

    tool_params = (
        task_payload.get("tool") if isinstance(task_payload.get("tool"), Mapping) else {}
    )
    skill_params = (
        task_payload.get("skill") if isinstance(task_payload.get("skill"), Mapping) else {}
    )
    target_skill = _preset_primary_skill_name(task_payload) or (
        str(
            tool_params.get("name")
            or tool_params.get("id")
            or skill_params.get("name")
            or skill_params.get("id")
            or ""
        ).strip()
        or None
    )
    if not target_skill:
        target_skill = (
            _coerce_temporal_scalar(params.get("targetSkill"))
            or _coerce_temporal_scalar(params.get("target_skill"))
            or _coerce_temporal_scalar(params.get("skillId"))
            or _coerce_temporal_scalar(params.get("skill"))
            or _coerce_temporal_scalar(params.get("selectedSkill"))
            or _coerce_temporal_scalar(params.get("selected_skill"))
            or _coerce_temporal_scalar(search_attributes.get("mm_target_skill"))
            or _coerce_temporal_scalar(search_attributes.get("mm_skill_id"))
            or _coerce_temporal_scalar(search_attributes.get("mm_skill"))
            or _coerce_temporal_scalar(search_attributes.get("skillId"))
            or _coerce_temporal_scalar(search_attributes.get("skill"))
            or _coerce_temporal_scalar(memo.get("targetSkill"))
            or _coerce_temporal_scalar(memo.get("target_skill"))
            or _coerce_temporal_scalar(memo.get("skillId"))
            or _coerce_temporal_scalar(memo.get("skill"))
        ) or None

    task_skills = _skill_selector_names(task_payload.get("skills"))
    if task_skills is None:
        template_primary_skill = _preset_primary_skill_name(task_payload)
        if template_primary_skill:
            task_skills = [template_primary_skill]

    git_payload = task_payload.get("git")
    if not isinstance(git_payload, Mapping):
        git_payload = {}
    repository = (
        _coerce_temporal_scalar(git_payload.get("repository"))
        or _coerce_temporal_scalar(task_payload.get("repository"))
        or _coerce_temporal_scalar(params.get("repository"))
        or _coerce_temporal_scalar(params.get("repo"))
        or _coerce_temporal_scalar(task_payload.get("repo"))
        or _coerce_temporal_scalar(search_attributes.get("mm_repository"))
        or _coerce_temporal_scalar(search_attributes.get("mm_repo"))
        or _coerce_temporal_scalar(search_attributes.get("repository"))
        or _coerce_temporal_scalar(memo.get("repository"))
    ) or None

    dependencies_block = (
        memo.get("dependencies")
        if isinstance(memo.get("dependencies"), Mapping)
        else {}
    )
    depends_on = normalize_dependency_ids(task_payload.get("dependsOn"))
    if not depends_on:
        depends_on = normalize_dependency_ids(
            dependencies_block.get("declaredIds") or memo.get("depends_on")
        )

    memo_finish_summary = _finish_summary_from_memo(memo)
    finish_summary_json = getattr(record, "finish_summary_json", None)
    finish_summary = (
        dict(finish_summary_json)
        if isinstance(finish_summary_json, Mapping)
        else memo_finish_summary
    )
    progress = _bounded_execution_progress_from_sources(
        record=record,
        memo=memo,
        finish_summary=finish_summary if isinstance(finish_summary, Mapping) else None,
    )
    started_at = getattr(record, "started_at", None)
    updated_at = getattr(record, "updated_at", None) or started_at or datetime.now(UTC)
    created_at = getattr(record, "created_at", None) or started_at or updated_at
    queued_at = getattr(record, "queued_at", None) or created_at
    scheduled_for = getattr(record, "scheduled_for", None)
    workflow_id = str(getattr(record, "workflow_id", "") or "").strip()
    dashboard_status = _DASHBOARD_STATUS_BY_STATE.get(
        getattr(record, "state", None),
        "queued",
    )

    return ExecutionListItemModel(
        source=_TEMPORAL_SOURCE,
        workflow_id=workflow_id,
        run_id=str(getattr(record, "run_id", "") or ""),
        workflow_type=workflow_type_value,
        entry=_resolve_execution_entry(record, search_attributes),
        owner_type=owner_type,
        owner_id=owner_id,
        title=title,
        status=dashboard_status,
        dashboard_status=dashboard_status,
        state=state_value,
        raw_state=raw_state,
        temporal_status=temporal_status,
        close_status=close_status,
        waiting_reason=str(waiting_reason) if waiting_reason else None,
        attention_required=attention_required,
        target_runtime=target_runtime,
        target_skill=target_skill,
        task_skills=task_skills,
        repository=repository,
        progress=progress,
        scheduled_for=scheduled_for,
        created_at=created_at,
        started_at=started_at,
        queued_at=queued_at,
        updated_at=updated_at,
        closed_at=getattr(record, "closed_at", None),
        depends_on=depends_on,
        blocked_on_dependencies=raw_state == "waiting_on_dependencies",
        detail_href=f"/workflows/{workflow_id}",
        redirect_path=f"/workflows/{workflow_id}?source=temporal",
        latest_run_view=True,
        continue_as_new_cause=_coerce_temporal_scalar(continue_as_new_cause) or None,
        ui_query_model="compatibility_adapter",
        stale_state=False,
        refreshed_at=_compatibility_refreshed_at(record),
    )


def _serialize_execution(
    record, *, include_artifact_refs: bool = True, user: Optional["User"] = None
) -> ExecutionModel:
    temporal_status = "running"
    close_status = _enum_value(record.close_status)
    memo = dict(record.memo or {})
    search_attributes = dict(record.search_attributes or {})
    integration_state = getattr(record, "integration_state", None)
    state_value = _enum_value(record.state) or ""
    workflow_type_value = _enum_value(record.workflow_type) or ""
    continue_as_new_cause = memo.get("continue_as_new_cause") or search_attributes.get(
        "mm_continue_as_new_cause"
    )
    if record.close_status is TemporalExecutionCloseStatus.COMPLETED:
        temporal_status = "completed"
    elif record.close_status is TemporalExecutionCloseStatus.CANCELED:
        temporal_status = "canceled"
    elif record.close_status in {
        TemporalExecutionCloseStatus.FAILED,
        TemporalExecutionCloseStatus.TERMINATED,
        TemporalExecutionCloseStatus.TIMED_OUT,
    }:
        temporal_status = "failed"

    raw_state = state_value
    manifest_status = None
    if workflow_type_value == "MoonMind.ManifestIngest":
        manifest_status = build_manifest_status_snapshot(record)
    owner_type = _normalize_owner_type(record, search_attributes)
    owner_id = str(search_attributes.get("mm_owner_id") or record.owner_id or "system")
    entry = _resolve_execution_entry(record, search_attributes)
    title = str(memo.get("title") or "").strip() or workflow_type_value
    summary = str(memo.get("summary") or "").strip() or "Execution updated."
    waiting_reason = (
        str(getattr(record, "waiting_reason", "") or "").strip()
        or str(memo.get("waiting_reason") or "").strip()
        or (
            str(memo.get("summary") or "").strip()
            if raw_state == "awaiting_external"
            else ""
        )
    )
    attention_required = bool(
        getattr(record, "attention_required", False)
        or memo.get("attention_required")
        or False
    )
    if raw_state == "awaiting_external":
        attention_required = True
    if (
        raw_state
        not in {"awaiting_slot", "awaiting_external", "waiting_on_dependencies"}
        and not bool(getattr(record, "paused", False))
        and not attention_required
    ):
        waiting_reason = None
    dashboard_status = _DASHBOARD_STATUS_BY_STATE.get(record.state, "queued")
    actions = _build_action_capabilities(record)
    intervention_audit = _parse_intervention_audit_entries(memo)
    debug_fields = _build_debug_fields(
        record=record,
        temporal_status=temporal_status,
        close_status=close_status,
        waiting_reason=waiting_reason,
        attention_required=attention_required,
    )

    params_raw = getattr(record, "parameters", None)
    params = dict(params_raw) if isinstance(params_raw, dict) else {}
    agent_run_id = None
    # The sources are checked in order of preference.
    sources_to_check = (
        [memo.get(k) for k in AGENT_RUN_ID_MEMO_KEYS]
        + [search_attributes.get(k) for k in AGENT_RUN_ID_SEARCH_ATTR_KEYS]
        + [params.get(k) for k in AGENT_RUN_ID_PARAM_KEYS]
    )
    for value in sources_to_check:
        if value:
            candidate = str(value).strip()
            if candidate:
                agent_run_id = candidate
                break
    target_runtime, param_model, param_effort = [
        str(params.get(key) or "").strip() or None
        for key in ["targetRuntime", "model", "effort"]
    ]
    param_requested_model = str(params.get("requestedModel") or "").strip() or None
    param_resolved_model = str(params.get("resolvedModel") or "").strip() or None
    param_model_source = str(params.get("modelSource") or "").strip() or None
    param_profile_id = str(params.get("profileId") or "").strip() or None
    if not target_runtime:
        runtime_nested = params.get("runtime")
        if isinstance(runtime_nested, dict):
            target_runtime = str(runtime_nested.get("mode") or "").strip() or None
    if not target_runtime:
        target_runtime = (
            _coerce_temporal_scalar(search_attributes.get("mm_target_runtime"))
            or _coerce_temporal_scalar(search_attributes.get("mm_runtime"))
            or _coerce_temporal_scalar(search_attributes.get("runtime"))
            or _coerce_temporal_scalar(memo.get("targetRuntime"))
            or _coerce_temporal_scalar(memo.get("target_runtime"))
        ) or None

    task_params = _workflow_payload_from_parameters(params)
    tool_params = (
        task_params.get("tool") if isinstance(task_params.get("tool"), dict) else {}
    )
    skill_params = (
        task_params.get("skill") if isinstance(task_params.get("skill"), dict) else {}
    )
    preset_primary_skill = _preset_primary_skill_name(task_params)
    target_skill = preset_primary_skill or (
        str(
            tool_params.get("name")
            or tool_params.get("id")
            or skill_params.get("name")
            or skill_params.get("id")
            or ""
        ).strip()
        or None
    )
    if not target_skill:
        target_skill = (
            _coerce_temporal_scalar(params.get("targetSkill"))
            or _coerce_temporal_scalar(params.get("target_skill"))
            or _coerce_temporal_scalar(params.get("skillId"))
            or _coerce_temporal_scalar(params.get("skill"))
            or _coerce_temporal_scalar(params.get("selectedSkill"))
            or _coerce_temporal_scalar(params.get("selected_skill"))
            or _coerce_temporal_scalar(search_attributes.get("mm_target_skill"))
            or _coerce_temporal_scalar(search_attributes.get("mm_skill_id"))
            or _coerce_temporal_scalar(search_attributes.get("mm_skill"))
            or _coerce_temporal_scalar(search_attributes.get("skillId"))
            or _coerce_temporal_scalar(search_attributes.get("skill"))
            or _coerce_temporal_scalar(memo.get("targetSkill"))
            or _coerce_temporal_scalar(memo.get("target_skill"))
            or _coerce_temporal_scalar(memo.get("skillId"))
            or _coerce_temporal_scalar(memo.get("skill"))
        ) or None

    task_payload = _workflow_payload_from_parameters(params)

    task_runtime_payload = task_payload.get("runtime")
    if not isinstance(task_runtime_payload, dict):
        task_runtime_payload = {}
    if not target_runtime:
        target_runtime = (
            _coerce_temporal_scalar(task_runtime_payload.get("mode"))
            or _coerce_temporal_scalar(task_runtime_payload.get("runtime"))
        ) or None
    if not param_model:
        param_model = _coerce_temporal_scalar(task_runtime_payload.get("model")) or None
    if not param_resolved_model:
        param_resolved_model = (
            _coerce_temporal_scalar(task_runtime_payload.get("resolvedModel"))
            or _coerce_temporal_scalar(task_runtime_payload.get("resolved_model"))
        ) or None
    if not param_requested_model:
        param_requested_model = (
            _coerce_temporal_scalar(task_runtime_payload.get("requestedModel"))
            or _coerce_temporal_scalar(task_runtime_payload.get("requested_model"))
        ) or None
    # Ensure model and resolved_model are populated whenever any model
    # signal is available so the dashboard workflow detail page
    # consistently surfaces a Model fact instead of intermittently
    # hiding it for executions whose params omit one of the aliases.
    effective_model = param_model or param_resolved_model or param_requested_model
    param_model = effective_model
    param_resolved_model = param_resolved_model or effective_model
    if not param_effort:
        param_effort = _coerce_temporal_scalar(task_runtime_payload.get("effort")) or None
    if not param_profile_id:
        param_profile_id = (
            _coerce_temporal_scalar(task_runtime_payload.get("profileId"))
            or _coerce_temporal_scalar(task_runtime_payload.get("profile_id"))
            or _coerce_temporal_scalar(task_runtime_payload.get("executionProfileRef"))
            or _coerce_temporal_scalar(task_runtime_payload.get("execution_profile_ref"))
        ) or None
    params_for_priority = params if isinstance(params, Mapping) else {}
    task_payload_for_priority = task_payload if isinstance(task_payload, Mapping) else {}
    priority = _int_or_none(
        params_for_priority.get("priority")
        if params_for_priority.get("priority") is not None
        else task_payload_for_priority.get("priority")
    )

    dependencies_block = (
        memo.get("dependencies") if isinstance(memo.get("dependencies"), dict) else {}
    )
    depends_on = normalize_dependency_ids(task_payload.get("dependsOn"))
    if not depends_on:
        depends_on = normalize_dependency_ids(
            dependencies_block.get("declaredIds") or memo.get("depends_on")
        )
    has_dependencies = bool(
        dependencies_block.get("declaredIds")
        or memo.get("has_dependencies")
        or depends_on
    )
    dependency_wait_occurred = bool(
        dependencies_block.get("waited")
        if "waited" in dependencies_block
        else memo.get("dependency_wait_occurred") or False
    )
    raw_dependency_wait_duration = (
        dependencies_block.get("waitDurationMs")
        if "waitDurationMs" in dependencies_block
        else memo.get("dependency_wait_duration_ms")
    )
    dependency_wait_duration_ms = None
    if raw_dependency_wait_duration is not None:
        try:
            dependency_wait_duration_ms = int(raw_dependency_wait_duration)
        except (TypeError, ValueError):
            dependency_wait_duration_ms = None
    dependency_resolution = (
        str(
            dependencies_block.get("resolution")
            if "resolution" in dependencies_block
            else memo.get("dependency_resolution")
            or ""
        ).strip()
        or None
    )
    failed_dependency_id = str(
        dependencies_block.get("failedDependencyId")
        if "failedDependencyId" in dependencies_block
        else memo.get("failed_dependency_id")
        or ""
    ).strip() or None
    dependency_outcomes = (
        list(dependencies_block.get("outcomes"))
        if isinstance(dependencies_block.get("outcomes"), list)
        else []
    )

    resolved_skillset_ref = (
        str(
            params.get("resolvedSkillsetRef")
            or params.get("resolved_skillset_ref")
            or ""
        ).strip()
        or None
    )

    task_skills = _skill_selector_names(task_payload.get("skills"))
    if task_skills is None:
        template_primary_skill = _preset_primary_skill_name(task_payload)
        if template_primary_skill:
            task_skills = [template_primary_skill]
    skill_runtime = _skill_runtime_evidence(
        params=params,
        task_payload=task_payload,
        task_skills=task_skills,
        resolved_skillset_ref=resolved_skillset_ref,
    )

    git_payload = task_payload.get("git")
    if not isinstance(git_payload, dict):
        git_payload = {}

    publish_payload = _normalize_publish_payload(task_payload.get("publish"))

    # Precedence: task.git.startingBranch > task.git.branch >
    # task.startingBranch > params.startingBranch
    starting_branch = (
        _coerce_temporal_scalar(git_payload.get("startingBranch"))
        or _coerce_temporal_scalar(git_payload.get("branch"))
        or _coerce_temporal_scalar(task_payload.get("startingBranch"))
        or _coerce_temporal_scalar(params.get("startingBranch"))
        or None
    )
    # Only show the "(default)" fallback when git context exists in the payload.
    has_git_context = bool(git_payload) or any(
        task_payload.get(k) or params.get(k)
        for k in ("startingBranch", "targetBranch", "defaultBranch", "branch")
    )
    if not starting_branch and has_git_context:
        default_branch = str(
            git_payload.get("defaultBranch") or params.get("defaultBranch") or "main"
        ).strip()
        starting_branch = f"{default_branch} (default)"

    # Precedence: task.git.targetBranch > task.targetBranch > params.targetBranch
    target_branch = str(
        git_payload.get("targetBranch")
        or task_payload.get("targetBranch")
        or params.get("targetBranch")
        or ""
    ).strip() or None

    repository = (
        _coerce_temporal_scalar(git_payload.get("repository"))
        or _coerce_temporal_scalar(task_payload.get("repository"))
        or _coerce_temporal_scalar(params.get("repository"))
        or _coerce_temporal_scalar(params.get("repo"))
        or _coerce_temporal_scalar(task_payload.get("repo"))
    ) or None
    if not repository:
        repository = (
            _coerce_temporal_scalar(search_attributes.get("mm_repository"))
            or _coerce_temporal_scalar(search_attributes.get("mm_repo"))
            or _coerce_temporal_scalar(search_attributes.get("repository"))
            or _coerce_temporal_scalar(memo.get("repository"))
            or _coerce_temporal_scalar(params.get("repository"))
            or _coerce_temporal_scalar(params.get("repo"))
            or _coerce_temporal_scalar(git_payload.get("repository"))
            or _coerce_temporal_scalar(task_payload.get("repository"))
        ) or None

    _ALLOWED_PUBLISH_MODES = {"branch", "pr", "none", "pr_with_merge_automation"}
    raw_publish_mode = str(
        params.get("publishMode") or publish_payload.get("mode") or ""
    ).strip() or None
    publish_mode = raw_publish_mode if raw_publish_mode in _ALLOWED_PUBLISH_MODES else None
    merge_automation_enabled = _merge_automation_enabled_from_parameters(params)
    if publish_mode == "pr" and merge_automation_enabled:
        publish_mode = "pr_with_merge_automation"
    merge_automation = _normalize_merge_automation_visibility_payload(
        memo.get("merge_automation") or memo.get("mergeAutomation")
    )
    pr_url = _extract_execution_pr_url(memo, search_attributes, params)
    is_admin = _is_execution_admin(user)
    steps_href = (
        settings.temporal_dashboard.steps_endpoint.replace(
            "{workflowId}", record.workflow_id
        )
        if workflow_type_value == "MoonMind.UserWorkflow"
        else None
    )
    resume_summary = _build_recovery_summary(record, actions=actions)
    related_runs = _build_related_runs(record, params=params)
    target_diagnostics = _build_target_diagnostics(
        record,
        params=params,
        resume_summary=resume_summary,
    )
    memo_finish_summary = _finish_summary_from_memo(memo)
    finish_summary_json = getattr(record, "finish_summary_json", None)
    finish_summary = (
        dict(finish_summary_json)
        if isinstance(finish_summary_json, dict)
        else memo_finish_summary
    )
    finish_summary = _normalize_no_commit_finish_summary(finish_summary)
    finish_outcome_code = canonicalize_finish_outcome_code_alias(
        getattr(record, "finish_outcome_code", None),
        logger=logger,
    )
    proposal_summary = _proposal_summary_from_memo(memo)
    if isinstance(finish_summary, Mapping):
        finish_proposals = finish_summary.get("proposals")
        if isinstance(finish_proposals, Mapping):
            proposal_summary = dict(finish_proposals)
    proposal_outcomes = _proposal_outcomes_from_summary(proposal_summary)
    run_metrics = _run_metrics_from_summary(
        record=record,
        finish_summary=finish_summary,
        close_status=close_status,
        state_value=state_value,
    )
    improvement_signals = _improvement_signals_from_summary(
        finish_summary=finish_summary,
        proposal_summary=proposal_summary,
    )
    log_context = _execution_log_context(
        record=record,
        memo=memo,
        search_attributes=search_attributes,
        params=params,
    )
    recommended_next_action = _recommended_next_action(
        finish_summary=finish_summary,
        close_status=close_status,
        state_value=state_value,
        proposal_summary=proposal_summary,
        pr_url=pr_url,
    )
    progress = _bounded_execution_progress_from_sources(
        record=record,
        memo=memo,
        finish_summary=finish_summary if isinstance(finish_summary, Mapping) else None,
    )

    started_at = getattr(record, "started_at", None)
    created_at = getattr(record, "created_at", None) or started_at or record.updated_at
    scheduled_for = getattr(record, "scheduled_for", None)

    recovery_eligibility = _project_recovery_eligibility(record, target_runtime)
    return ExecutionModel(
        task_id=None,
        agent_run_id=agent_run_id,
        progress=progress,
        namespace=record.namespace,
        source=_TEMPORAL_SOURCE,
        workflow_id=record.workflow_id,
        run_id=record.run_id,
        temporal_run_id=record.run_id,
        legacy_run_id=None,
        workflow_type=workflow_type_value,
        entry=entry or record.entry,
        owner_type=owner_type,
        owner_id=owner_id,
        title=title,
        summary=summary,
        task_instructions=_derive_full_workflow_instructions(task_payload),
        status=dashboard_status,
        dashboard_status=dashboard_status,
        state=state_value,
        raw_state=raw_state,
        temporal_status=temporal_status,
        close_status=close_status,
        waiting_reason=str(waiting_reason) if waiting_reason else None,
        attention_required=attention_required,
        intervention_audit=intervention_audit,
        search_attributes=search_attributes,
        memo=memo,
        input_parameters=params,
        input_artifact_ref=getattr(record, "input_ref", None),
        task_input_snapshot=_workflow_input_snapshot_descriptor_from_record(record),
        target_runtime=target_runtime,
        target_skill=target_skill,
        model=param_model,
        requested_model=param_requested_model,
        resolved_model=param_resolved_model,
        model_source=param_model_source,
        profile_id=param_profile_id,
        effort=param_effort,
        priority=priority,
        starting_branch=starting_branch,
        target_branch=target_branch,
        output_branch=_verified_output_branch(finish_summary),
        repository=repository,
        pr_url=pr_url,
        publish_mode=publish_mode,
        merge_automation=merge_automation,
        resolved_skillset_ref=resolved_skillset_ref,
        task_skills=task_skills,
        skill_runtime=skill_runtime,
        artifact_refs=(
            list(record.artifact_refs or []) if include_artifact_refs else []
        ),
        manifest_artifact_ref=_manifest_attr(
            manifest_status,
            "manifest_artifact_ref",
            getattr(record, "manifest_ref", None),
        ) if is_admin else None,
        plan_artifact_ref=_manifest_attr(
            manifest_status,
            "plan_artifact_ref",
            getattr(record, "plan_ref", None),
        ),
        summary_artifact_ref=_manifest_attr(manifest_status, "summary_artifact_ref"),
        run_index_artifact_ref=_manifest_attr(
            manifest_status, "run_index_artifact_ref"
        ) if is_admin else None,
        checkpoint_artifact_ref=_manifest_attr(
            manifest_status, "checkpoint_artifact_ref"
        ) if is_admin else None,
        requested_by=_manifest_attr(manifest_status, "requested_by"),
        execution_policy=_manifest_attr(manifest_status, "execution_policy"),
        phase=_manifest_attr(manifest_status, "phase"),
        paused=_manifest_attr(manifest_status, "paused"),
        counts=_manifest_attr(manifest_status, "counts"),
        artifacts_count=len(record.artifact_refs or []),
        scheduled_for=scheduled_for,
        created_at=created_at,
        queued_at=created_at,
        steps_href=steps_href,
        actions=actions,
        resume=resume_summary,
        recovery_eligibility=recovery_eligibility,
        related_runs=related_runs,
        recurrence=_execution_recurrence_provenance(params, memo),
        target_diagnostics=target_diagnostics,
        run_metrics=run_metrics,
        improvement_signals=improvement_signals,
        recommended_next_action=recommended_next_action,
        log_context=log_context,
        proposal_summary=proposal_summary,
        proposal_outcomes=proposal_outcomes,
        finish_outcome_code=finish_outcome_code,
        finish_summary=finish_summary,
        debug_fields=debug_fields,
        redirect_path=f"/workflows/{record.workflow_id}?source=temporal",
        integration=(
            dict(integration_state) if isinstance(integration_state, dict) else None
        ),
        latest_run_view=True,
        continue_as_new_cause=continue_as_new_cause,
        depends_on=depends_on,
        has_dependencies=has_dependencies,
        dependency_wait_occurred=dependency_wait_occurred,
        dependency_wait_duration_ms=dependency_wait_duration_ms,
        dependency_resolution=dependency_resolution,
        failed_dependency_id=failed_dependency_id,
        blocked_on_dependencies=raw_state == "waiting_on_dependencies",
        dependency_outcomes=dependency_outcomes,
        started_at=started_at,
        updated_at=record.updated_at,
        closed_at=record.closed_at,
        detail_href=f"/workflows/{record.workflow_id}",
        ui_query_model="compatibility_adapter",
        stale_state=False,
        refreshed_at=_compatibility_refreshed_at(record),
    )


def _finish_summary_from_memo(memo: Mapping[str, Any]) -> dict[str, Any] | None:
    finish_summary = memo.get("finishSummary") or memo.get("finish_summary")
    if isinstance(finish_summary, Mapping):
        return dict(finish_summary)
    return None


def _verified_output_branch(
    finish_summary: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Project only independently verified publication evidence."""
    if not isinstance(finish_summary, Mapping):
        return None
    publish = finish_summary.get("publish")
    context = finish_summary.get("publishContext")
    if not isinstance(publish, Mapping):
        publish = {}
    if not isinstance(context, Mapping):
        context = {}
    terminal = publish.get("terminalPublication")
    if not isinstance(terminal, Mapping):
        terminal = context.get("terminalPublication")
    source = terminal if isinstance(terminal, Mapping) else {**context, **publish}
    remote_verified = source.get("remoteVerified") is True
    status_value = str(source.get("status") or publish.get("status") or "").strip()
    if not remote_verified or status_value not in {"pushed", "already_published", "published"}:
        return None
    name = str(
        source.get("branchName")
        or source.get("branch")
        or context.get("branch")
        or ""
    ).strip()
    if not name:
        return None
    intent_value = source.get("intent")
    if intent_value not in {"normal", "terminal_checkpoint"}:
        intent_value = "normal"
    result: dict[str, Any] = {
        "name": name,
        "headSha": source.get("headSha") or context.get("headSha"),
        "baseBranch": source.get("baseBranch") or context.get("baseRef"),
        "intent": intent_value,
        "status": status_value,
        "evidenceRef": source.get("evidenceRef"),
    }
    url = source.get("branchUrl")
    if isinstance(url, str) and url.startswith("https://github.com/"):
        result["url"] = url
    return {key: value for key, value in result.items() if value not in (None, "")}


def _proposal_summary_from_memo(memo: Mapping[str, Any]) -> dict[str, Any] | None:
    direct = memo.get("proposals")
    if isinstance(direct, Mapping):
        return dict(direct)
    finish_summary = _finish_summary_from_memo(memo)
    if isinstance(finish_summary, Mapping):
        proposals = finish_summary.get("proposals")
        if isinstance(proposals, Mapping):
            return dict(proposals)
    return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _run_metrics_from_summary(
    *,
    record: Any,
    finish_summary: Mapping[str, Any] | None,
    close_status: str | None,
    state_value: str,
) -> dict[str, Any]:
    timestamps = (
        finish_summary.get("timestamps")
        if isinstance(finish_summary, Mapping)
        else None
    )
    if not isinstance(timestamps, Mapping):
        timestamps = {}
    finish_outcome = (
        finish_summary.get("finishOutcome")
        if isinstance(finish_summary, Mapping)
        else None
    )
    if not isinstance(finish_outcome, Mapping):
        finish_outcome = {}
    cost = (
        finish_summary.get("cost")
        if isinstance(finish_summary, Mapping)
        else None
    )
    if not isinstance(cost, Mapping):
        cost = {"status": "not_recorded", "amountUsd": None}
    normalized_close_status = str(close_status or "").strip().lower()
    terminal = normalized_close_status in {
        "completed",
        "failed",
        "canceled",
        "terminated",
        "timed_out",
    } or state_value in {"completed", "failed", "canceled"}
    success = normalized_close_status == "completed" or (
        not normalized_close_status and state_value == "completed"
    )
    duration_ms = _int_or_none(timestamps.get("durationMs"))
    if duration_ms is None:
        started_at = getattr(record, "started_at", None)
        closed_at = getattr(record, "closed_at", None)
        updated_at = getattr(record, "updated_at", None)
        end_at = closed_at or updated_at
        if started_at is not None and end_at is not None:
            try:
                duration_ms = max(
                    0, int((end_at - started_at).total_seconds() * 1000)
                )
            except (TypeError, AttributeError):
                duration_ms = None
    success_rate_sample = (
        {"success": 1 if success else 0, "sampleSize": 1}
        if terminal
        else {"success": 0, "sampleSize": 0}
    )
    return {
        "durationMs": duration_ms,
        "outcomeCode": finish_outcome.get("code"),
        "success": success,
        "successRateSample": success_rate_sample,
        "cost": dict(cost),
    }


_SIGNAL_TAG_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("retry", ("retry", "reattempt", "self heal", "self-heal")),
    ("loop_detected", ("loop", "repeated", "no progress", "stuck")),
    ("flaky_test", ("flaky", "nondeterministic test")),
    ("missing_ref", ("missing file", "missing ref", "not found")),
    ("artifact_gap", ("artifact", "diagnostic", "summary missing")),
)


def _signal_tags_from_text(*values: object) -> list[str]:
    text = " ".join(str(value or "").lower() for value in values if value)
    tags: list[str] = []
    for tag, needles in _SIGNAL_TAG_KEYWORDS:
        if any(needle in text for needle in needles):
            tags.append(tag)
    return tags


def _compact_signal(
    *,
    code: str,
    source: str,
    summary: str,
    tags: list[str],
    severity: str = "medium",
) -> dict[str, Any]:
    return {
        "code": code[:96],
        "source": source,
        "summary": summary[:500],
        "severity": severity,
        "tags": tags[:6],
    }


def _improvement_signals_from_summary(
    *,
    finish_summary: Mapping[str, Any] | None,
    proposal_summary: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    if isinstance(finish_summary, Mapping):
        run_quality = finish_summary.get("runQuality")
        if isinstance(run_quality, Mapping):
            raw_tags = run_quality.get("tags")
            tags = [
                str(tag).strip()
                for tag in (raw_tags if isinstance(raw_tags, list) else [])
                if str(tag).strip()
            ]
            if not tags:
                tags = _signal_tags_from_text(
                    run_quality.get("code"),
                    run_quality.get("reason"),
                    run_quality.get("message"),
                )
            signals.append(
                _compact_signal(
                    code=str(run_quality.get("code") or "run_quality"),
                    source="finish_summary.runQuality",
                    summary=str(
                        run_quality.get("reason")
                        or run_quality.get("message")
                        or "Run quality signal captured."
                    ),
                    tags=tags or ["artifact_gap"],
                    severity=str(run_quality.get("severity") or "high"),
                )
            )
        failure = finish_summary.get("failure")
        if isinstance(failure, Mapping):
            tags = _signal_tags_from_text(
                failure.get("category"),
                failure.get("message"),
                failure.get("rootCauseType"),
            )
            if tags:
                signals.append(
                    _compact_signal(
                        code=str(failure.get("category") or "failure_signal"),
                        source="finish_summary.failure",
                        summary=str(failure.get("message") or "Failure signal captured."),
                        tags=tags,
                        severity="high",
                    )
                )
        last_step = finish_summary.get("lastStep")
        if isinstance(last_step, Mapping):
            tags = _signal_tags_from_text(
                last_step.get("summary"),
                last_step.get("lastError"),
            )
            if tags:
                signals.append(
                    _compact_signal(
                        code=str(last_step.get("lastError") or "last_step_signal"),
                        source="finish_summary.lastStep",
                        summary=str(last_step.get("summary") or "Step signal captured."),
                        tags=tags,
                    )
                )
    if isinstance(proposal_summary, Mapping):
        errors = proposal_summary.get("errors") or []
        if isinstance(errors, list) and errors:
            signals.append(
                _compact_signal(
                    code="proposal_stage_errors",
                    source="finish_summary.proposals",
                    summary=str(errors[0]),
                    tags=["artifact_gap"],
                )
            )
    return signals


def _execution_log_context(
    *,
    record: Any,
    memo: Mapping[str, Any],
    search_attributes: Mapping[str, Any],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    worker_id = (
        _coerce_temporal_scalar(memo.get("workerId"))
        or _coerce_temporal_scalar(memo.get("worker_id"))
        or _coerce_temporal_scalar(search_attributes.get("mm_worker_id"))
        or _coerce_temporal_scalar(params.get("workerId"))
        or _coerce_temporal_scalar(params.get("worker_id"))
    )
    return {
        "workflowId": record.workflow_id,
        "runId": record.run_id,
        "workerId": worker_id,
        "namespace": record.namespace,
    }


def _recommended_next_action(
    *,
    finish_summary: Mapping[str, Any] | None,
    close_status: str | None,
    state_value: str,
    proposal_summary: Mapping[str, Any] | None,
    pr_url: str | None,
) -> str:
    finish_outcome = (
        finish_summary.get("finishOutcome")
        if isinstance(finish_summary, Mapping)
        else None
    )
    code = ""
    if isinstance(finish_outcome, Mapping):
        code = str(finish_outcome.get("code") or "").strip().upper()
    submitted_count = (
        _int_or_none(proposal_summary.get("submittedCount"))
        if proposal_summary
        else None
    )
    if submitted_count and submitted_count > 0:
        return "Review generated improvement proposals."
    if code in {"PUBLISHED_PR", "PUBLISHED_BRANCH"}:
        return (
            "Review published output."
            if not pr_url
            else "Review published pull request."
        )
    if canonicalize_finish_outcome_code_alias(code) == "NO_COMMIT":
        return "No follow-up required unless the outcome is unexpected."
    if code == "PUBLISH_DISABLED":
        return "Review generated artifacts; publishing was disabled."
    if code == "CANCELLED" or state_value == "canceled":
        return "Review cancellation reason and rerun if needed."
    if code == "FAILED" or str(close_status or "").lower() == "failed":
        return "Review failure diagnostics and create a follow-up proposal if needed."
    return "Monitor execution until a terminal outcome is available."


def _proposal_outcomes_from_summary(
    proposal_summary: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if not proposal_summary:
        return []
    outcomes: list[dict[str, Any]] = []
    outcome_index: dict[tuple[str, str], dict[str, Any]] = {}

    def merge_outcome(item: Mapping[str, Any], *, default_status: str) -> None:
        outcome = dict(item)
        outcome.setdefault("deliveryStatus", default_status)
        external_url = str(outcome.get("externalUrl") or "").strip()
        external_key = str(outcome.get("externalKey") or "").strip()
        keys: list[tuple[str, str]] = []
        if external_url:
            keys.append(("url", external_url))
        if external_key:
            keys.append(("key", external_key))
        if not keys:
            outcomes.append(outcome)
            return
        existing = next(
            (outcome_index[key] for key in keys if key in outcome_index), None
        )
        if existing is None:
            for key in keys:
                outcome_index[key] = outcome
            outcomes.append(outcome)
            return
        existing.update({k: v for k, v in outcome.items() if v is not None})
        for key in keys:
            outcome_index[key] = existing

    for item in proposal_summary.get("externalLinks") or []:
        if not isinstance(item, Mapping):
            continue
        merge_outcome(item, default_status="delivered")
    for item in proposal_summary.get("dedupUpdates") or []:
        if not isinstance(item, Mapping):
            continue
        merge_outcome(item, default_status="updated")
    for item in proposal_summary.get("deliveryFailures") or []:
        if not isinstance(item, Mapping):
            continue
        merge_outcome(item, default_status="failed")
    return outcomes


def _recovery_checkpoint_ref_from_record(record) -> str | None:
    memo = dict(getattr(record, "memo", None) or {})
    search_attributes = dict(getattr(record, "search_attributes", None) or {})
    params = dict(getattr(record, "parameters", None) or {})
    recovery_manifest = _recovery_manifest_summary_from_record(record)
    recovery_block = params.get("recoverySource")
    if not isinstance(recovery_block, Mapping):
        recovery_block = params.get("recovery_source")
    if not isinstance(recovery_block, Mapping):
        recovery_block = {}
    for value in (
        memo.get("recovery_checkpoint_ref"),
        memo.get("recoveryCheckpointRef"),
        search_attributes.get("mm_recovery_checkpoint_ref"),
        recovery_manifest.get("checkpointRef"),
        recovery_manifest.get("checkpoint_ref"),
        _nested_recovery_manifest_text(
            recovery_manifest,
            "validation",
            "checkpointRef",
        ),
        _nested_recovery_manifest_text(
            recovery_manifest,
            "validation",
            "checkpoint_ref",
        ),
        _nested_recovery_manifest_text(
            recovery_manifest,
            "recoveryEligibility",
            "checkpointRef",
        ),
        _nested_recovery_manifest_text(
            recovery_manifest,
            "recoveryEligibility",
            "checkpoint_ref",
        ),
        recovery_block.get("recoveryCheckpointRef"),
        recovery_block.get("recovery_checkpoint_ref"),
    ):
        candidate = str(value or "").strip()
        if candidate:
            return candidate
    return None


def _recovery_manifest_summary_from_record(record) -> Mapping[str, Any]:
    finish_summary = getattr(record, "finish_summary_json", None)
    if not isinstance(finish_summary, Mapping):
        return {}
    recovery_manifest = finish_summary.get("recoveryManifest")
    if not isinstance(recovery_manifest, Mapping):
        recovery_manifest = finish_summary.get("recovery_manifest")
    return recovery_manifest if isinstance(recovery_manifest, Mapping) else {}


def _project_recovery_eligibility(
    record, target_runtime: str | None
) -> RecoveryEligibilityDiagnosticModel | None:
    raw = _recovery_manifest_summary_from_record(record).get("recoveryEligibility")
    if not isinstance(raw, Mapping):
        return None
    projected = dict(raw)
    flags = settings.feature_flags
    projected_runtime = normalize_runtime_id(target_runtime or "codex_cli")
    try:
        capabilities = resolve_runtime_execution_capabilities(projected_runtime)
    except ValueError:
        capabilities = None
    projected.update(
        runtimeId=projected_runtime,
        deploymentGeneration=(
            str(flags.checkpoint_resume_deployment_generation or "").strip()
            or "unavailable"
        ),
        capabilitySetVersion=(
            capabilities.capability_set_version if capabilities else None
        ),
        capabilityDigest=(capabilities.capability_digest if capabilities else None),
        checkpointKind="worktree_archive",
        promotionState=flags.checkpoint_resume_promotion_state,
    )
    if (
        flags.checkpoint_resume_promotion_state
        in {"disabled", "shadow_capture", "shadow_restore", "paused"}
        or not flags.checkpoint_resume_action_enabled
    ):
        projected.update(
            eligible=False,
            defaultAction="full_retry",
            disabledReasonCode="rollout_action_hidden",
            operatorGuidance="full_retry",
        )
    try:
        return RecoveryEligibilityDiagnosticModel.model_validate(projected)
    except ValidationError:
        return None


def _nested_recovery_manifest_text(
    manifest: Mapping[str, Any],
    block_key: str,
    key: str,
) -> str | None:
    block = manifest.get(block_key)
    if not isinstance(block, Mapping):
        return None
    value = block.get(key)
    candidate = str(value or "").strip()
    return candidate or None


def _recovery_manifest_summary_allows_resume(
    record,
    checkpoint_ref: str | None = None,
    failed_step_id: str | None = None,
    manifest_ref: str | None = None,
) -> bool:
    manifest = _recovery_manifest_summary_from_record(record)
    if not manifest:
        return False
    if not _coerce_bool(manifest.get("resumeAllowed"), default=False):
        return False
    validation_result = str(
        manifest.get("validationResult")
        or manifest.get("validation_result")
        or _nested_recovery_manifest_text(manifest, "validation", "result")
        or ""
    ).strip().lower()
    if validation_result != "valid":
        return False
    if checkpoint_ref is None:
        checkpoint_ref = _recovery_checkpoint_ref_from_record(record)
    if failed_step_id is None:
        failed_step_id = _recovery_failed_step_id_from_record(record)
    if manifest_ref is None:
        manifest_ref = _recovery_manifest_ref_from_record(record)
    return bool(checkpoint_ref and failed_step_id and manifest_ref)


def _recovery_manifest_ref_from_record(record) -> str | None:
    memo = dict(getattr(record, "memo", None) or {})
    search_attributes = dict(getattr(record, "search_attributes", None) or {})
    params = dict(getattr(record, "parameters", None) or {})
    recovery_manifest = _recovery_manifest_summary_from_record(record)
    recovery_block = params.get("recoverySource")
    if not isinstance(recovery_block, Mapping):
        recovery_block = params.get("recovery_source")
    if not isinstance(recovery_block, Mapping):
        recovery_block = {}
    for value in (
        recovery_manifest.get("manifestRef"),
        recovery_manifest.get("manifest_ref"),
        recovery_manifest.get("failedRunRecoveryManifestRef"),
        recovery_manifest.get("failed_run_recovery_manifest_ref"),
        memo.get("failed_run_recovery_manifest_ref"),
        memo.get("failedRunRecoveryManifestRef"),
        memo.get("recovery_manifest_ref"),
        memo.get("recoveryManifestRef"),
        search_attributes.get("mm_failed_run_recovery_manifest_ref"),
        recovery_block.get("failedRunRecoveryManifestRef"),
        recovery_block.get("failed_run_recovery_manifest_ref"),
    ):
        candidate = str(value or "").strip()
        if candidate:
            return candidate
    return None


def _canonical_recovery_manifest_ref(
    request: RecoverFromFailedStepRequest | RecoverFromSelectedStepRequest,
    canonical: TemporalExecutionCanonicalRecord,
) -> str | None:
    manifest_ref = _recovery_manifest_ref_from_record(canonical)
    requested_ref = str(request.failed_run_recovery_manifest_ref or "").strip()
    if requested_ref and manifest_ref and requested_ref != manifest_ref:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Recovery request fields do not match recovery manifest evidence.",
                "reason": "recovery_manifest_inconsistent",
                "fields": ["failedRunRecoveryManifestRef"],
            },
        )
    return manifest_ref


def _recovery_failed_step_id_from_record(record) -> str | None:
    memo = dict(getattr(record, "memo", None) or {})
    params = dict(getattr(record, "parameters", None) or {})
    recovery_manifest = _recovery_manifest_summary_from_record(record)
    recovery_block = params.get("recoverySource")
    if not isinstance(recovery_block, Mapping):
        recovery_block = params.get("recovery_source")
    if not isinstance(recovery_block, Mapping):
        recovery_block = {}
    for value in (
        memo.get("resume_failed_step_id"),
        memo.get("resumeFailedStepId"),
        recovery_manifest.get("failedLogicalStepId"),
        recovery_manifest.get("failed_logical_step_id"),
        recovery_block.get("failedStepId"),
        recovery_block.get("failed_step_id"),
    ):
        candidate = str(value or "").strip()
        if candidate:
            return candidate
    return None

def _recovery_source_block_from_record(record) -> Mapping[str, Any]:
    params = dict(getattr(record, "parameters", None) or {})
    recovery_block = params.get("recoverySource")
    if not isinstance(recovery_block, Mapping):
        recovery_block = params.get("recovery_source")
    if not isinstance(recovery_block, Mapping):
        return {}
    return recovery_block

def _first_nonempty_text(*values: Any) -> str | None:
    for value in values:
        candidate = str(value or "").strip()
        if candidate:
            return candidate
    return None

def _recovery_completed_step_refs_from_record(record) -> list[str]:
    memo = dict(getattr(record, "memo", None) or {})
    search_attributes = dict(getattr(record, "search_attributes", None) or {})
    recovery_block = _recovery_source_block_from_record(record)
    candidates = (
        memo.get("resume_completed_step_refs"),
        memo.get("resumeCompletedStepRefs"),
        memo.get("resume_preserved_step_refs"),
        memo.get("resumePreservedStepRefs"),
        search_attributes.get("mm_recovery_completed_step_refs"),
        recovery_block.get("completedStepRefs"),
        recovery_block.get("completed_step_refs"),
    )
    refs: list[str] = []
    for value in candidates:
        if isinstance(value, str):
            parts = [item.strip() for item in value.split(",")]
        elif isinstance(value, list):
            parts = [str(item or "").strip() for item in value]
        else:
            parts = []
        refs.extend(part for part in parts if part)
    if refs:
        return refs
    preserved_steps = recovery_block.get("preservedSteps") or recovery_block.get(
        "preserved_steps"
    )
    if not isinstance(preserved_steps, list):
        return []
    for step in preserved_steps:
        if not isinstance(step, Mapping):
            continue
        artifacts = step.get("artifacts")
        if isinstance(artifacts, Mapping):
            refs.extend(
                str(value or "").strip()
                for value in artifacts.values()
                if str(value or "").strip()
            )
    return refs

def _recovery_workspace_checkpoint_ref_from_record(record) -> str | None:
    memo = dict(getattr(record, "memo", None) or {})
    search_attributes = dict(getattr(record, "search_attributes", None) or {})
    recovery_block = _recovery_source_block_from_record(record)
    workspace = recovery_block.get("recoveryWorkspace") or recovery_block.get(
        "recovery_workspace"
    )
    if not isinstance(workspace, Mapping):
        workspace = {}
    return _first_nonempty_text(
        memo.get("recovery_workspace_checkpoint_ref"),
        memo.get("recoveryWorkspaceCheckpointRef"),
        memo.get("recovery_workspace_ref"),
        memo.get("recoveryWorkspaceRef"),
        search_attributes.get("mm_recovery_workspace_checkpoint_ref"),
        search_attributes.get("mm_recovery_workspace_ref"),
        recovery_block.get("recoveryWorkspaceCheckpointRef"),
        recovery_block.get("recovery_workspace_checkpoint_ref"),
        workspace.get("checkpointRef"),
        workspace.get("checkpoint_ref"),
        workspace.get("ref"),
    )

def _recovery_plan_identity_from_record(record) -> str | None:
    memo = dict(getattr(record, "memo", None) or {})
    search_attributes = dict(getattr(record, "search_attributes", None) or {})
    recovery_block = _recovery_source_block_from_record(record)
    return _first_nonempty_text(
        getattr(record, "plan_ref", None),
        memo.get("resume_plan_ref"),
        memo.get("resumePlanRef"),
        memo.get("resume_plan_digest"),
        memo.get("resumePlanDigest"),
        memo.get("plan_ref"),
        memo.get("plan_digest"),
        _coerce_artifact_ref(memo.get("plan_artifact_ref")),
        _coerce_artifact_ref(memo.get("planArtifactRef")),
        search_attributes.get("mm_recovery_plan_ref"),
        search_attributes.get("mm_recovery_plan_digest"),
        recovery_block.get("sourcePlanRef"),
        recovery_block.get("source_plan_ref"),
        recovery_block.get("sourcePlanDigest"),
        recovery_block.get("source_plan_digest"),
    )


def _recovery_evidence_marked_stale(record) -> bool:
    memo = dict(getattr(record, "memo", None) or {})
    search_attributes = dict(getattr(record, "search_attributes", None) or {})
    recovery_block = dict(_recovery_source_block_from_record(record) or {})
    for value in (
        memo.get("resume_evidence_stale"),
        memo.get("resumeEvidenceStale"),
        search_attributes.get("mm_recovery_evidence_stale"),
        recovery_block.get("resumeEvidenceStale"),
        recovery_block.get("resume_evidence_stale"),
    ):
        if isinstance(value, bool):
            if value:
                return True
            continue
        if value is None:
            continue
        if str(value).strip().lower() in {"1", "true", "yes", "stale"}:
            return True
    return False


def _recovery_evidence_disabled_reason(record) -> str | None:
    if _recovery_evidence_marked_stale(record):
        return "stale_recovery_evidence"
    checkpoint_ref = _recovery_checkpoint_ref_from_record(record)
    failed_step_id = _recovery_failed_step_id_from_record(record)
    if _recovery_manifest_summary_allows_resume(
        record,
        checkpoint_ref=checkpoint_ref,
        failed_step_id=failed_step_id,
    ):
        if not _recovery_plan_identity_from_record(record):
            return "plan_identity_missing"
        return None
    if not checkpoint_ref:
        return "recovery_checkpoint_missing"
    if not failed_step_id:
        return "failed_step_identity_missing"
    if not _recovery_completed_step_refs_from_record(record):
        return "completed_step_refs_missing"
    if not _recovery_workspace_checkpoint_ref_from_record(record):
        return "workspace_checkpoint_missing"
    if not _recovery_plan_identity_from_record(record):
        return "plan_identity_missing"
    return None

def _build_recovery_summary(
    record,
    *,
    actions: ExecutionActionCapabilityModel,
) -> ExecutionResumeSummaryModel | None:
    if _enum_value(getattr(record, "workflow_type", None)) != "MoonMind.UserWorkflow":
        return None
    return ExecutionResumeSummaryModel(
        available=actions.can_failed_step_resume,
        checkpointRef=_recovery_checkpoint_ref_from_record(record),
        failedStepId=_recovery_failed_step_id_from_record(record),
        sourceRunId=str(getattr(record, "run_id", "") or "").strip() or None,
        disabledReason=actions.disabled_reasons.get("canResumeFromFailedStep"),
    )

def _related_run_href(workflow_id: str) -> str:
    return f"/workflows/{quote(workflow_id, safe='')}?source=temporal"


def _execution_sort_timestamp(value: datetime | None) -> float:
    return value.timestamp() if value is not None else 0


def _execution_updated_sort_bucket(execution: ExecutionModel) -> int:
    return int(
        _execution_sort_timestamp(execution.updated_at)
        // _EXECUTION_UPDATED_SORT_BUCKET_SECONDS
    )


def _execution_queued_sort_timestamp(execution: ExecutionModel) -> float:
    return _execution_sort_timestamp(execution.queued_at or execution.created_at)


def _build_related_runs(
    record,
    *,
    params: Mapping[str, Any],
) -> list[ExecutionRelatedRunModel]:
    related_runs: list[ExecutionRelatedRunModel] = []
    recovery_source = params.get("recoverySource")
    if not isinstance(recovery_source, Mapping):
        recovery_source = params.get("recovery_source")
    if isinstance(recovery_source, Mapping):
        source_workflow_id = str(
            recovery_source.get("sourceWorkflowId")
            or recovery_source.get("source_workflow_id")
            or ""
        ).strip()
        if source_workflow_id:
            source_run_id = str(
                recovery_source.get("sourceRunId")
                or recovery_source.get("source_run_id")
                or ""
            ).strip() or None
            related_runs.append(
                ExecutionRelatedRunModel(
                    workflowId=source_workflow_id,
                    runId=source_run_id,
                    relationship="Recovered from failed step",
                    status="failed",
                    href=_related_run_href(source_workflow_id),
                )
            )

    task_payload = _workflow_payload_from_parameters(params)
    comparison_source = task_payload.get("comparison")
    if isinstance(comparison_source, Mapping):
        source_workflow_id = str(
            comparison_source.get("sourceWorkflowId")
            or comparison_source.get("source_workflow_id")
            or ""
        ).strip()
        if source_workflow_id:
            source_run_id = str(
                comparison_source.get("sourceRunId")
                or comparison_source.get("source_run_id")
                or ""
            ).strip() or None
            related_runs.append(
                ExecutionRelatedRunModel(
                    workflowId=source_workflow_id,
                    runId=source_run_id,
                    relationship="Comparison source",
                    href=_related_run_href(source_workflow_id),
                )
            )

    return related_runs


def _execution_related_run_metadata(record: TemporalExecutionRecord) -> dict[str, Any]:
    params = record.parameters if isinstance(record.parameters, Mapping) else {}
    task_payload = _workflow_payload_from_parameters(params)
    runtime_payload = task_payload.get("runtime")
    if not isinstance(runtime_payload, Mapping):
        runtime_payload = {}
    state_value = _enum_value(getattr(record, "state", None)) or None
    close_status = _enum_value(getattr(record, "close_status", None)) or None
    return {
        "run_id": str(getattr(record, "run_id", "") or "").strip() or None,
        "status": close_status or state_value,
        "target_runtime": (
            _coerce_temporal_scalar(params.get("targetRuntime"))
            or _coerce_temporal_scalar(runtime_payload.get("mode"))
            or None
        ),
        "model": _coerce_temporal_scalar(params.get("model")) or None,
        "requested_model": _coerce_temporal_scalar(params.get("requestedModel")) or None,
        "resolved_model": _coerce_temporal_scalar(params.get("resolvedModel")) or None,
        "effort": (
            _coerce_temporal_scalar(params.get("effort"))
            or _coerce_temporal_scalar(runtime_payload.get("effort"))
            or None
        ),
        "created_at": getattr(record, "created_at", None),
    }


def _execution_recurrence_provenance(
    params: Mapping[str, Any] | None,
    memo: Mapping[str, Any] | None = None,
) -> dict[str, str] | None:
    if not isinstance(params, Mapping):
        params = {}
    system_payload = params.get("system")
    recurrence_payload = (
        system_payload.get("recurrence") if isinstance(system_payload, Mapping) else None
    )
    definition_id = (
        str(recurrence_payload.get("definitionId") or "").strip()
        if isinstance(recurrence_payload, Mapping)
        else ""
    )
    if not definition_id and isinstance(memo, Mapping):
        definition_id = str(memo.get("definitionId") or "").strip()
    if not definition_id:
        return None
    return {
        "definitionId": definition_id,
        "href": f"/schedules/{quote(definition_id, safe='')}",
    }


def _execution_record_visible_to_user(
    record: TemporalExecutionRecord,
    user: User,
) -> bool:
    search_attributes = dict(getattr(record, "search_attributes", None) or {})
    record_owner_type = _enum_value(getattr(record, "owner_type", None))
    if record_owner_type is None:
        record_owner_type = _normalize_owner_type(record, search_attributes)
    record_owner_id = str(getattr(record, "owner_id", "") or "").strip()
    if not record_owner_id:
        record_owner_id = _coerce_temporal_scalar(search_attributes.get("mm_owner_id"))
    return record_owner_type == "user" and record_owner_id == _owner_id(user)


async def _hydrate_related_run_metadata(
    execution: ExecutionModel,
    *,
    session: AsyncSession | None,
    user: User | None = None,
) -> ExecutionModel:
    if session is None or not execution.related_runs:
        return execution
    hydrated: list[ExecutionRelatedRunModel] = []
    for related_run in execution.related_runs:
        try:
            record = await session.get(TemporalExecutionRecord, related_run.workflow_id)
        except Exception as exc:
            logger.warning(
                "Failed to hydrate related run %s for execution %s: %s",
                related_run.workflow_id,
                execution.workflow_id,
                exc,
                exc_info=True,
            )
            hydrated.append(related_run)
            continue
        if record is None:
            hydrated.append(related_run)
            continue
        if (
            user is not None
            and not _is_execution_admin(user)
            and not _execution_record_visible_to_user(record, user)
        ):
            hydrated.append(related_run)
            continue
        metadata = _execution_related_run_metadata(record)
        hydrated.append(
            related_run.model_copy(
                update={
                    key: value
                    for key, value in metadata.items()
                    if value is not None
                }
            )
        )
    return execution.model_copy(update={"related_runs": hydrated})

def _target_diagnostics_block(
    *,
    params: Mapping[str, Any],
    memo: Mapping[str, Any],
    search_attributes: Mapping[str, Any],
) -> Mapping[str, Any]:
    for source in (params, memo, search_attributes):
        for key in ("targetDiagnostics", "target_diagnostics"):
            value = source.get(key)
            if isinstance(value, Mapping):
                return value
    return {}

def _attachment_ref_from_payload(value: Mapping[str, Any]) -> str | None:
    for key in ("artifactRef", "artifact_ref", "artifactId", "artifact_id", "ref"):
        candidate = str(value.get(key) or "").strip()
        if candidate:
            return candidate
    return None

def _normalize_target_attachment(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        ref = value.strip()
        if not ref:
            return None
        return {"artifactRef": ref, "previewAvailable": True}
    if not isinstance(value, Mapping):
        return None
    artifact_ref = _attachment_ref_from_payload(value)
    filename = str(
        value.get("filename") or value.get("name") or value.get("fileName") or ""
    ).strip() or None
    content_type = str(
        value.get("contentType")
        or value.get("content_type")
        or value.get("mimeType")
        or ""
    ).strip() or None
    size_bytes = value.get("sizeBytes", value.get("size_bytes"))
    try:
        normalized_size = int(size_bytes) if size_bytes is not None else None
    except (TypeError, ValueError):
        normalized_size = None
    return {
        "artifactRef": artifact_ref,
        "filename": filename,
        "contentType": content_type,
        "sizeBytes": normalized_size,
        "previewAvailable": bool(value.get("previewAvailable", bool(artifact_ref))),
    }

def _target_attachment_payloads(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    candidates: list[Any] = []
    for key in (
        "attachmentRefs",
        "attachment_refs",
        "attachments",
        "inputAttachments",
        "input_attachments",
    ):
        raw = value.get(key)
        if isinstance(raw, list):
            candidates.extend(raw)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        attachment = _normalize_target_attachment(item)
        if attachment is None:
            continue
        identity = str(
            attachment.get("artifactRef") or attachment.get("filename") or len(seen)
        )
        if identity in seen:
            continue
        seen.add(identity)
        normalized.append(attachment)
    return normalized

def _normalize_target_refs(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    refs: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, str):
            ref = item.strip()
            if ref:
                refs.append({"refKind": "artifact", "artifactRef": ref})
            continue
        if not isinstance(item, Mapping):
            continue
        ref_kind = str(
            item.get("refKind") or item.get("ref_kind") or item.get("kind") or "artifact"
        ).strip()
        artifact_ref = str(
            item.get("artifactRef") or item.get("artifact_ref") or item.get("ref") or ""
        ).strip() or None
        path = str(item.get("path") or "").strip() or None
        if ref_kind and (artifact_ref or path):
            refs.append({"refKind": ref_kind, "artifactRef": artifact_ref, "path": path})
    return refs

def _normalize_attachment_failure_phase(value: Any) -> str:
    phase = str(value or "").strip().lower().replace("-", "_")
    if phase in {
        "upload",
        "validation",
        "materialization",
        "context_generation",
        "degraded",
    }:
        return phase
    if phase in {"context", "context_generation_failed", "generated_context"}:
        return "context_generation"
    if phase in {"materialize", "prepare", "download", "download_failed"}:
        return "materialization"
    if phase in {"validate", "invalid", "schema"}:
        return "validation"
    if phase in {"create", "submit", "submitted"}:
        return "upload"
    return "degraded"

def _normalize_target_failures(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    failures: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        message = str(item.get("message") or item.get("summary") or "").strip()
        if not message:
            continue
        phase = _normalize_attachment_failure_phase(
            item.get("phase") or item.get("kind")
        )
        evidence_ref = str(
            item.get("evidenceRef") or item.get("evidence_ref") or item.get("artifactRef") or ""
        ).strip() or None
        failures.append(
            {"phase": phase, "message": message, "evidenceRef": evidence_ref}
        )
    return failures

def _target_step_id(value: Mapping[str, Any], fallback: str) -> str:
    return str(
        value.get("id")
        or value.get("stepId")
        or value.get("step_id")
        or value.get("logicalStepId")
        or fallback
    ).strip()

def _target_step_label(value: Mapping[str, Any], step_id: str, index: int) -> str:
    return (
        str(value.get("title") or value.get("label") or value.get("name") or "").strip()
        or step_id
        or f"Step {index}"
    )

def _merge_target_overlay(
    targets: list[dict[str, Any]],
    *,
    target_kind: str,
    step_id: str | None,
    overlay: Mapping[str, Any],
) -> None:
    if not overlay:
        return
    for target in targets:
        if target.get("targetKind") != target_kind:
            continue
        if target_kind == "step" and target.get("stepId") != step_id:
            continue
        if overlay.get("label"):
            target["label"] = str(overlay.get("label"))
        target["attachments"] = [
            *target.get("attachments", []),
            *_target_attachment_payloads(overlay),
        ]
        target["refs"] = [
            *target.get("refs", []),
            *_normalize_target_refs(overlay.get("refs")),
        ]
        target["failures"] = [
            *target.get("failures", []),
            *_normalize_target_failures(overlay.get("failures")),
        ]
        return

    label = str(overlay.get("label") or "").strip()
    if not label:
        label = "Task objective" if target_kind == "objective" else step_id or "Step"
    targets.append(
        {
            "targetKind": target_kind,
            "stepId": step_id,
            "label": label,
            "attachments": _target_attachment_payloads(overlay),
            "refs": _normalize_target_refs(overlay.get("refs")),
            "failures": _normalize_target_failures(overlay.get("failures")),
        }
    )

def _preserved_steps_from_recovery_source(
    recovery_source: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw_steps = recovery_source.get("preservedSteps") or recovery_source.get(
        "preserved_steps"
    )
    if not isinstance(raw_steps, list):
        return []
    preserved_steps: list[dict[str, Any]] = []
    for item in raw_steps:
        if not isinstance(item, Mapping):
            continue
        logical_step_id = str(
            item.get("logicalStepId")
            or item.get("logical_step_id")
            or item.get("stepId")
            or item.get("step_id")
            or ""
        ).strip()
        if not logical_step_id:
            continue
        source_execution_ordinal = item.get("sourceExecutionOrdinal", item.get("attempt"))
        try:
            normalized_attempt = (
                int(source_execution_ordinal) if source_execution_ordinal is not None else None
            )
        except (TypeError, ValueError):
            normalized_attempt = None
        preserved_steps.append(
            {
                "logicalStepId": logical_step_id,
                "title": str(item.get("title") or "").strip() or None,
                "sourceExecutionOrdinal": normalized_attempt,
                "sourceWorkflowId": str(
                    item.get("sourceWorkflowId")
                    or item.get("source_workflow_id")
                    or recovery_source.get("sourceWorkflowId")
                    or recovery_source.get("source_workflow_id")
                    or ""
                ).strip()
                or None,
                "sourceRunId": str(
                    item.get("sourceRunId")
                    or item.get("source_run_id")
                    or recovery_source.get("sourceRunId")
                    or recovery_source.get("source_run_id")
                    or ""
                ).strip()
                or None,
            }
        )
    return preserved_steps

def _failed_recovery_phase(disabled_reason: str | None) -> str | None:
    if not disabled_reason:
        return None
    if disabled_reason == "workspace_checkpoint_missing":
        return "workspace_restoration"
    if disabled_reason == "completed_step_refs_missing":
        return "preserved_output_injection"
    if disabled_reason in {
        "recovery_checkpoint_missing",
        "failed_step_identity_missing",
        "plan_identity_missing",
    }:
        return "checkpoint_validation"
    return None

def _normalize_failed_recovery_phase(value: Any) -> str | None:
    phase = str(value or "").strip().lower().replace("-", "_")
    if phase in {
        "workspace_restoration",
        "checkpoint_validation",
        "preserved_output_injection",
        "failed_step_execution",
    }:
        return phase
    return None

def _target_diagnostics_recovery_block(
    diagnostics_block: Mapping[str, Any],
) -> Mapping[str, Any]:
    recovery = diagnostics_block.get("recovery")
    if isinstance(recovery, Mapping):
        return recovery
    return {}

def _mapping_str_value(value: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        candidate = str(value.get(key) or "").strip()
        if candidate:
            return candidate
    return None

def _build_target_diagnostics(
    record,
    *,
    params: Mapping[str, Any],
    resume_summary: ExecutionResumeSummaryModel | None,
) -> dict[str, Any] | None:
    memo = dict(getattr(record, "memo", None) or {})
    search_attributes = dict(getattr(record, "search_attributes", None) or {})
    task_payload = _workflow_payload_from_parameters(params)
    diagnostics_block = _target_diagnostics_block(
        params=params,
        memo=memo,
        search_attributes=search_attributes,
    )

    targets: list[dict[str, Any]] = []
    objective_attachments = _target_attachment_payloads(task_payload)
    raw_steps = task_payload.get("steps") if isinstance(task_payload, Mapping) else None
    step_targets: list[dict[str, Any]] = []
    if isinstance(raw_steps, list):
        for index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, Mapping):
                continue
            step_id = _target_step_id(raw_step, f"step-{index}")
            step_targets.append(
                {
                    "targetKind": "step",
                    "stepId": step_id,
                    "label": _target_step_label(raw_step, step_id, index),
                    "attachments": _target_attachment_payloads(raw_step),
                    "refs": [],
                    "failures": [],
                }
            )

    has_any_target_attachments = bool(objective_attachments) or any(
        step_target["attachments"] for step_target in step_targets
    )
    if objective_attachments or diagnostics_block or has_any_target_attachments:
        targets.append(
            {
                "targetKind": "objective",
                "label": "Task objective",
                "attachments": objective_attachments,
                "refs": [],
                "failures": [],
            }
        )

    for step_target in step_targets:
        if step_target["attachments"] or diagnostics_block or has_any_target_attachments:
            targets.append(step_target)

    raw_overlay_targets = diagnostics_block.get("targets")
    if isinstance(raw_overlay_targets, list):
        for overlay in raw_overlay_targets:
            if not isinstance(overlay, Mapping):
                continue
            target_kind = str(overlay.get("targetKind") or overlay.get("target_kind") or "").strip()
            if target_kind not in {"objective", "step"}:
                continue
            step_id = (
                str(overlay.get("stepId") or overlay.get("step_id") or "").strip()
                or None
            )
            _merge_target_overlay(
                targets,
                target_kind=target_kind,
                step_id=step_id,
                overlay=overlay,
            )

    recovery_source = _recovery_source_block_from_record(record)
    diagnostics_recovery = _target_diagnostics_recovery_block(diagnostics_block)
    source_workflow_id = _mapping_str_value(
        recovery_source, "sourceWorkflowId", "source_workflow_id"
    ) or _mapping_str_value(
        diagnostics_recovery, "sourceWorkflowId", "source_workflow_id"
    )
    source_run_id = _mapping_str_value(
        recovery_source, "sourceRunId", "source_run_id"
    ) or _mapping_str_value(diagnostics_recovery, "sourceRunId", "source_run_id")
    failed_recovery_phase = _normalize_failed_recovery_phase(
        _mapping_str_value(
            diagnostics_recovery, "failedRecoveryPhase", "failed_recovery_phase"
        )
    ) or _failed_recovery_phase(
        resume_summary.disabled_reason if resume_summary else None
    )
    checkpoint_ref = (
        resume_summary.checkpoint_ref if resume_summary else None
    ) or _mapping_str_value(diagnostics_recovery, "checkpointRef", "checkpoint_ref")
    preserved_steps = _preserved_steps_from_recovery_source(
        recovery_source
    ) or _preserved_steps_from_recovery_source(diagnostics_recovery)
    recovery = None
    has_recovery_evidence = bool(
        recovery_source
        or diagnostics_recovery
        or (
            resume_summary
            and (
                checkpoint_ref
                or resume_summary.failed_step_id
                or failed_recovery_phase
            )
        )
    )
    if has_recovery_evidence:
        recovery = {
            "resumed": bool(
                source_workflow_id
                or source_run_id
                or recovery_source
                or diagnostics_recovery.get("resumed")
            ),
            "sourceWorkflowId": source_workflow_id,
            "sourceRunId": source_run_id,
            "checkpointRef": checkpoint_ref,
            "preservedSteps": preserved_steps,
            "failedRecoveryPhase": failed_recovery_phase,
        }

    degraded_reason = str(
        diagnostics_block.get("degradedReason")
        or diagnostics_block.get("degraded_reason")
        or ""
    ).strip() or None

    if not targets and not recovery and not degraded_reason:
        return None
    return {
        "targets": targets,
        "recovery": recovery,
        "degradedReason": degraded_reason,
    }

async def _enrich_execution_dependencies(
    execution: ExecutionModel,
    *,
    service: TemporalExecutionService,
) -> ExecutionModel:
    if execution.entry != "user_workflow":
        return execution

    prerequisite_ids = list(execution.depends_on)
    prerequisites = (
        await service.enrich_dependency_summaries(prerequisite_ids)
        if prerequisite_ids
        else []
    )
    dependent_edges = await service.list_dependents(execution.workflow_id)
    dependent_ids = [edge.dependent_workflow_id for edge in dependent_edges]
    dependents = (
        await service.enrich_dependency_summaries(dependent_ids)
        if dependent_ids
        else []
    )
    return execution.model_copy(
        update={
            "prerequisites": [
                ExecutionDependencySummaryModel(
                    workflowId=item.workflow_id,
                    title=item.title,
                    summary=item.summary,
                    state=item.state,
                    closeStatus=item.close_status,
                    workflowType=item.workflow_type,
                )
                for item in prerequisites
            ],
            "dependents": [
                ExecutionDependencySummaryModel(
                    workflowId=item.workflow_id,
                    title=item.title,
                    summary=item.summary,
                    state=item.state,
                    closeStatus=item.close_status,
                    workflowType=item.workflow_type,
                )
                for item in dependents
            ],
        }
    )

async def _resolver_child_observability(
    *,
    temporal_client: Client,
    workflow_id: str,
) -> ExecutionMergeAutomationResolverChildModel:
    agent_run_id: str | None = None
    child_status: str | None = None
    try:
        payload = await _query_workflow_for_detail(
            temporal_client,
            workflow_id,
            "get_step_ledger",
        )
    except Exception as exc:
        logger.debug(
            "Failed to query resolver child step ledger for %s: %s",
            workflow_id,
            exc,
        )
        payload = None
    if isinstance(payload, Mapping):
        steps = payload.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, Mapping):
                    continue
                if child_status is None:
                    child_status = _coerce_temporal_scalar(step.get("status")) or None
                refs = step.get("refs")
                if not isinstance(refs, Mapping):
                    continue
                agent_run_id = (
                    _coerce_temporal_scalar(refs.get("agentRunId"))
                    or _coerce_temporal_scalar(refs.get("agent_run_id"))
                    or None
                )
                if agent_run_id:
                    break
    return ExecutionMergeAutomationResolverChildModel(
        workflowId=workflow_id,
        agentRunId=agent_run_id,
        status=child_status,
        detailHref=f"/workflows/{quote(workflow_id, safe='')}?source=temporal",
    )


async def _query_workflow_for_detail(
    temporal_client: Client,
    workflow_id: str,
    query_name: str,
    *,
    timeout_seconds: float | None = None,
) -> Any:
    effective_timeout_seconds = float(
        timeout_seconds
        if timeout_seconds is not None
        else settings.temporal_dashboard.live_query_timeout_seconds
    )
    return await asyncio.wait_for(
        query_workflow(temporal_client, workflow_id, query_name),
        timeout=effective_timeout_seconds,
    )


async def _enrich_execution_merge_automation(
    execution: ExecutionModel,
    *,
    temporal_client: Client,
) -> ExecutionModel:
    merge_automation = execution.merge_automation
    if merge_automation is None:
        return execution
    workflow_id = merge_automation.workflow_id or merge_automation.child_workflow_id
    payload = merge_automation.model_dump(by_alias=True, exclude_none=True)
    if workflow_id:
        try:
            live_payload = await _query_workflow_for_detail(
                temporal_client,
                workflow_id,
                "summary",
            )
        except Exception as exc:
            logger.debug(
                "Failed to query merge automation summary for %s: %s",
                workflow_id,
                exc,
            )
        else:
            if isinstance(live_payload, Mapping):
                payload.update(dict(live_payload))
                payload["workflowId"] = workflow_id
                payload["childWorkflowId"] = workflow_id
                payload["enabled"] = True

    normalized = _normalize_merge_automation_visibility_payload(payload)
    if normalized is None:
        return execution

    resolver_ids = list(normalized.resolver_child_workflow_ids)
    if resolver_ids:
        resolver_children = await asyncio.gather(
            *(
                _resolver_child_observability(
                    temporal_client=temporal_client,
                    workflow_id=child_workflow_id,
                )
                for child_workflow_id in resolver_ids[:10]
            )
        )
        normalized = normalized.model_copy(
            update={"resolver_children": resolver_children}
        )

    return execution.model_copy(update={"merge_automation": normalized})

def _build_execution_artifact_ref_model(artifact: Any) -> ArtifactRefModel:
    compact_ref = build_artifact_ref(artifact)
    return ArtifactRefModel(
        artifact_ref_v=compact_ref.artifact_ref_v,
        artifact_id=compact_ref.artifact_id,
        sha256=compact_ref.sha256,
        size_bytes=compact_ref.size_bytes,
        content_type=compact_ref.content_type,
        encryption=compact_ref.encryption,
        diagnostics=None,
    )

def _select_complete_execution_artifact(artifacts: list[Any]) -> Any | None:
    for artifact in artifacts:
        if getattr(artifact, "status", None) is TemporalArtifactStatus.COMPLETE:
            return artifact
    return None

async def _hydrate_execution_report_projection(
    execution: ExecutionModel,
    *,
    session: AsyncSession | None,
    user: User,
) -> ExecutionModel:
    if session is None or execution.entry != "user_workflow" or not execution.run_id:
        return execution

    principal = str(getattr(user, "id", "") or "system")
    try:
        artifact_service = get_temporal_artifact_service(session)
        report_roles = (
            "report.primary",
            "report.summary",
            "report.structured",
            "report.evidence",
        )
        (
            primary_artifacts,
            summary_artifacts,
            structured_artifacts,
            evidence_artifacts,
        ) = await asyncio.gather(
            *(
                artifact_service.list_for_execution(
                    namespace=execution.namespace,
                    workflow_id=execution.workflow_id,
                    run_id=execution.run_id,
                    principal=principal,
                    link_type=role,
                    latest_only=True,
                )
                for role in report_roles
            )
        )
        primary_artifact = _select_complete_execution_artifact(primary_artifacts)
        if primary_artifact is None:
            return execution.model_copy(
                update={
                    "report_projection": ExecutionReportProjectionModel(
                        hasReport=False
                    )
                }
            )

        summary_artifact = _select_complete_execution_artifact(summary_artifacts)
        structured_artifact = _select_complete_execution_artifact(structured_artifacts)
        evidence_artifact = _select_complete_execution_artifact(evidence_artifacts)

        primary_ref_model = _build_execution_artifact_ref_model(primary_artifact)
        summary_ref_model = (
            _build_execution_artifact_ref_model(summary_artifact)
            if summary_artifact is not None
            else None
        )
        structured_ref_model = (
            _build_execution_artifact_ref_model(structured_artifact)
            if structured_artifact is not None
            else None
        )
        evidence_ref_model = (
            _build_execution_artifact_ref_model(evidence_artifact)
            if evidence_artifact is not None
            else None
        )
        metadata_json = (
            primary_artifact.metadata_json
            if isinstance(getattr(primary_artifact, "metadata_json", None), Mapping)
            else {}
        )
        projection_bundle: dict[str, Any] = {
            "report_bundle_v": 1,
            "evidence_refs": [],
            "primary_report_ref": primary_ref_model.model_dump(
                by_alias=True, exclude_none=True
            ),
        }
        if summary_ref_model is not None:
            projection_bundle["summary_ref"] = summary_ref_model.model_dump(
                by_alias=True, exclude_none=True
            )
        if structured_ref_model is not None:
            projection_bundle["structured_ref"] = structured_ref_model.model_dump(
                by_alias=True, exclude_none=True
            )
        if evidence_ref_model is not None:
            projection_bundle["evidence_refs"] = [
                evidence_ref_model.model_dump(by_alias=True, exclude_none=True)
            ]
        report_type = metadata_json.get("report_type")
        if report_type is not None:
            projection_bundle["report_type"] = report_type
        report_scope = metadata_json.get("report_scope")
        if report_scope is not None:
            projection_bundle["report_scope"] = report_scope

        projection_metadata: dict[str, dict[str, int]] = {}
        for key in ("finding_counts", "severity_counts"):
            value = metadata_json.get(key)
            if isinstance(value, Mapping):
                projection_metadata[key] = dict(value)

        projection_payload = build_report_projection_summary(
            projection_bundle,
            metadata=projection_metadata or None,
        )
        projection = ExecutionReportProjectionModel.model_validate(projection_payload)
        return execution.model_copy(update={"report_projection": projection})
    except Exception as exc:
        logger.warning(
            "Failed to hydrate report projection for execution %s: %s",
            execution.workflow_id,
            exc,
            exc_info=True,
        )
        return execution

async def _hydrate_provider_profile_metadata(
    execution: ExecutionModel, session: AsyncSession | None
) -> ExecutionModel:
    profile_id = str(execution.profile_id or "").strip()
    if not profile_id or session is None:
        return execution
    try:
        profile = await session.get(ManagedAgentProviderProfile, profile_id)
    except Exception as exc:
        logger.warning(
            "Failed to hydrate provider profile metadata for execution %s profile %s: %s",
            execution.workflow_id,
            profile_id,
            exc,
            exc_info=True,
        )
        return execution
    if profile is None:
        return execution
    return execution.model_copy(
        update={
            "provider_id": str(profile.provider_id or "").strip() or None,
            "provider_label": str(profile.provider_label or "").strip() or None,
        }
    )

def _managed_run_store_root() -> str:
    return os.path.join(
        os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs"),
        "managed_runs",
    )

def _resolve_agent_run_ids_from_managed_store(
    workflow_ids: tuple[str, ...]
) -> dict[str, str]:
    normalized_workflow_ids = tuple(
        dict.fromkeys(
            str(workflow_id or "").strip()
            for workflow_id in workflow_ids
            if str(workflow_id or "").strip()
        )
    )
    if not normalized_workflow_ids:
        return {}
    try:
        store = ManagedRunStore(_managed_run_store_root())
        records = store.find_latest_by_workflow_ids(normalized_workflow_ids)
    except Exception:
        logger.warning(
            "Failed to resolve managed agent run ids for workflows %s from run store",
            normalized_workflow_ids,
            exc_info=True,
        )
        return {}
    resolved: dict[str, str] = {}
    for workflow_id, record in records.items():
        run_id = str(record.run_id or "").strip()
        if run_id:
            resolved[workflow_id] = run_id
    return resolved

async def _enrich_step_ledger_agent_run_refs(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return payload
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    missing_agent_child_workflow_ids: list[str] = []
    seen_child_workflow_ids: set[str] = set()

    for step in steps:
        if not isinstance(step, Mapping):
            continue
        tool = step.get("tool")
        if (
            not isinstance(tool, Mapping)
            or str(tool.get("type") or "").strip() != "agent_runtime"
        ):
            continue
        refs = step.get("refs")
        if not isinstance(refs, Mapping):
            continue
        existing_agent_run_id = str(
            refs.get("agentRunId") or refs.get("agent_run_id") or ""
        ).strip()
        child_workflow_id = str(
            refs.get("childWorkflowId") or refs.get("child_workflow_id") or ""
        ).strip()
        if (
            existing_agent_run_id
            or not child_workflow_id
            or child_workflow_id in seen_child_workflow_ids
        ):
            continue
        seen_child_workflow_ids.add(child_workflow_id)
        missing_agent_child_workflow_ids.append(child_workflow_id)

    if not missing_agent_child_workflow_ids:
        return payload

    resolved_by_child_workflow = await asyncio.to_thread(
        _resolve_agent_run_ids_from_managed_store,
        tuple(missing_agent_child_workflow_ids),
    )
    if not resolved_by_child_workflow:
        return payload

    enriched_steps: list[Any] = []
    changed = False

    for step in steps:
        if not isinstance(step, Mapping):
            enriched_steps.append(step)
            continue
        tool = step.get("tool")
        if (
            not isinstance(tool, Mapping)
            or str(tool.get("type") or "").strip() != "agent_runtime"
        ):
            enriched_steps.append(step)
            continue
        refs = step.get("refs")
        if not isinstance(refs, Mapping):
            enriched_steps.append(step)
            continue
        existing_agent_run_id = str(
            refs.get("agentRunId") or refs.get("agent_run_id") or ""
        ).strip()
        child_workflow_id = str(
            refs.get("childWorkflowId") or refs.get("child_workflow_id") or ""
        ).strip()
        if existing_agent_run_id or not child_workflow_id:
            enriched_steps.append(step)
            continue
        agent_run_id = resolved_by_child_workflow.get(child_workflow_id)
        if not agent_run_id:
            enriched_steps.append(step)
            continue

        enriched_refs = dict(refs)
        enriched_refs["agentRunId"] = agent_run_id
        enriched_step = dict(step)
        enriched_step["refs"] = enriched_refs
        enriched_steps.append(enriched_step)
        changed = True

    if not changed:
        return payload
    enriched_payload = dict(payload)
    enriched_payload["steps"] = enriched_steps
    return enriched_payload


def _fallback_step_tool_payload(step: Mapping[str, Any]) -> dict[str, str]:
    step_type = str(step.get("type") or "").strip()
    tool = step.get("tool") if isinstance(step.get("tool"), Mapping) else None
    skill = step.get("skill") if isinstance(step.get("skill"), Mapping) else None
    source = tool or skill or {}
    source_type = str(
        source.get("type")
        or source.get("kind")
        or ("tool" if tool is not None else "skill")
    ).strip()
    if step_type in {"tool", "skill", "agent_runtime"}:
        source_type = step_type
    name = str(
        source.get("name")
        or source.get("id")
        or step.get("targetRuntime")
        or step.get("runtime")
        or ""
    ).strip()
    version = str(source.get("version") or "").strip()
    return {
        "type": source_type or "skill",
        "name": name,
        "version": version,
    }


def _fallback_step_title(
    step: Mapping[str, Any],
    *,
    step_id: str,
    tool_payload: Mapping[str, str],
) -> str:
    return (
        str(
            step.get("title")
            or step.get("name")
            or tool_payload.get("name")
            or step_id
        ).strip()
        or step_id
    )


def _fallback_current_step_order(record: Any) -> int | None:
    memo = getattr(record, "memo", None)
    if not isinstance(memo, Mapping):
        return None
    structured = memo.get("mm_current_step_order")
    if isinstance(structured, bool):
        structured = None
    if isinstance(structured, int):
        return structured if structured > 0 else None
    if isinstance(structured, str):
        candidate = structured.strip()
        if candidate:
            try:
                order = int(candidate)
            except ValueError:
                order = None
            if order is not None:
                return order if order > 0 else None
    # Compatibility fallback for in-flight workflows that predate
    # ``mm_current_step_order``; the human-readable ``summary`` carries the
    # current step as ``step <n>/<total>``.
    summary = str(memo.get("summary") or "").strip()
    match = re.search(r"\bstep\s+(\d+)\s*/\s*\d+\b", summary, re.IGNORECASE)
    if match is None:
        return None
    try:
        order = int(match.group(1))
    except ValueError:
        return None
    return order if order > 0 else None


def _fallback_step_ledger_from_record(record: Any) -> StepLedgerSnapshotModel | None:
    close_status = _enum_value(getattr(record, "close_status", None))
    if close_status:
        return None
    parameters = getattr(record, "parameters", None)
    if not isinstance(parameters, Mapping):
        return None
    task = _workflow_payload_from_parameters(parameters)
    if not task:
        return None
    raw_steps = task.get("steps")
    if not isinstance(raw_steps, list):
        return None

    normalized_steps = [step for step in raw_steps if isinstance(step, Mapping)]
    if not normalized_steps:
        return None

    ordered_nodes: list[dict[str, Any]] = []
    dependency_map: dict[str, list[str]] = {}

    for index, step in enumerate(normalized_steps, start=1):
        step_id = str(
            step.get("id")
            or step.get("logicalStepId")
            or step.get("logical_step_id")
            or f"step-{index:04d}"
        ).strip()
        if not step_id:
            continue
        tool_payload = _fallback_step_tool_payload(step)
        title = _fallback_step_title(
            step,
            step_id=step_id,
            tool_payload=tool_payload,
        )
        ordered_nodes.append(
            {
                "id": step_id,
                "title": title,
                "tool": tool_payload,
                "inputs": {"title": title},
            }
        )
        dependency_map[step_id] = normalize_dependency_ids(
            step.get("dependsOn")
            or step.get("depends_on")
            or step.get("dependencies")
        )

    if not ordered_nodes:
        return None

    updated_at = _compatibility_refreshed_at(record)
    rows = build_initial_step_rows(
        ordered_nodes=ordered_nodes,
        dependency_map=dependency_map,
        updated_at=updated_at,
    )

    current_order = _fallback_current_step_order(record)
    if current_order is not None:
        waiting_reason = str(
            getattr(record, "waiting_reason", None)
            or (getattr(record, "memo", {}) or {}).get("waiting_reason")
            or ""
        ).strip()
        summary = str((getattr(record, "memo", {}) or {}).get("summary") or "").strip()
        for row in rows:
            if row.get("order") != current_order:
                continue
            execution_ordinal = max(
                int(row.get("executionOrdinal") or row.get("attempt") or 0),
                1,
            )
            row["status"] = "awaiting_external" if waiting_reason else "executing"
            row["executionOrdinal"] = execution_ordinal
            row["startedAt"] = row.get("startedAt") or updated_at.isoformat()
            row["updatedAt"] = updated_at.isoformat()
            row["waitingReason"] = waiting_reason or None
            row["attentionRequired"] = bool(
                waiting_reason or getattr(record, "attention_required", False)
            )
            row["summary"] = summary or row.get("summary")
            break

    snapshot = {
        "workflowId": str(getattr(record, "workflow_id", "") or "").strip(),
        "runId": str(getattr(record, "run_id", "") or "").strip(),
        "runScope": "latest",
        "steps": rows,
    }
    try:
        return StepLedgerSnapshotModel.model_validate(snapshot)
    except ValidationError:
        logger.warning(
            "Failed to build fallback step ledger for %s",
            getattr(record, "workflow_id", "<unknown>"),
            exc_info=True,
        )
        return None


def _step_ledger_query_timeout_seconds(fallback_record: Any | None) -> float:
    live_timeout_seconds = float(
        settings.temporal_dashboard.live_query_timeout_seconds
    )
    close_status = (
        _enum_value(getattr(fallback_record, "close_status", None))
        if fallback_record is not None
        else None
    )
    if close_status:
        return max(
            live_timeout_seconds,
            float(
                settings.temporal_dashboard.terminal_step_ledger_query_timeout_seconds
            ),
        )
    return live_timeout_seconds


async def _load_execution_progress(
    *,
    temporal_client: Client,
    workflow_id: str,
) -> tuple[ExecutionProgressModel | None, str | None]:
    try:
        payload = await _query_workflow_for_detail(
            temporal_client,
            workflow_id,
            "get_progress",
        )
    except TimeoutError:
        logger.warning(
            "Timed out querying execution progress for %s after %.3fs",
            workflow_id,
            settings.temporal_dashboard.live_query_timeout_seconds,
        )
        return None, None
    except Exception as exc:
        logger.warning(
            "Failed to query execution progress for %s: %s",
            workflow_id,
            exc,
            exc_info=True,
        )
        return None, None

    if payload is None:
        return None, None

    queried_run_id = None
    if isinstance(payload, Mapping):
        queried_run_id = (
            _coerce_temporal_scalar(payload.get("runId"))
            or _coerce_temporal_scalar(payload.get("run_id"))
            or None
        )

    try:
        return ExecutionProgressModel.model_validate(payload), queried_run_id
    except ValidationError as exc:
        logger.warning(
            "Invalid execution progress payload for %s: %s",
            workflow_id,
            exc,
            exc_info=True,
        )
        return None, None


def _execution_uses_live_workflow_queries(execution: ExecutionModel) -> bool:
    if execution.workflow_type != "MoonMind.UserWorkflow":
        return False
    if execution.close_status:
        return False
    return execution.temporal_status == "running"

async def _load_execution_step_ledger(
    *,
    temporal_client: Client,
    workflow_id: str,
    fallback_record: Any | None = None,
) -> StepLedgerSnapshotModel:
    try:
        payload = await _query_workflow_for_detail(
            temporal_client,
            workflow_id,
            "get_step_ledger",
            timeout_seconds=_step_ledger_query_timeout_seconds(fallback_record),
        )
    except (RPCError, TimeoutError) as exc:
        logger.warning(
            "Failed to query execution step ledger for %s: %s",
            workflow_id,
            exc,
            exc_info=not isinstance(exc, TimeoutError),
        )
        fallback = (
            _fallback_step_ledger_from_record(fallback_record)
            if fallback_record is not None
            else None
        )
        if fallback is not None:
            logger.info(
                "Serving fallback step ledger for %s from stored workflow parameters",
                workflow_id,
            )
            return fallback
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "temporal_unavailable",
                "message": "Temporal step query unavailable.",
            },
        ) from exc

    enriched_payload = await _enrich_step_ledger_agent_run_refs(payload)

    try:
        return StepLedgerSnapshotModel.model_validate(enriched_payload)
    except ValidationError as exc:
        logger.warning(
            "Invalid execution step ledger payload for %s: %s",
            workflow_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "invalid_execution_query_payload",
                "message": "Execution step ledger query returned an invalid payload.",
            },
        ) from exc


def _step_execution_manifest_refs(row: Any) -> list[str]:
    refs: list[str] = []
    for source in (getattr(row, "refs", None), getattr(row, "artifacts", None)):
        if source is None:
            continue
        raw_refs = getattr(source, "step_execution_manifest_refs", None)
        if isinstance(raw_refs, list):
            refs.extend(str(ref).strip() for ref in raw_refs if str(ref).strip())
        latest_ref = (
            getattr(source, "latest_step_execution_manifest_ref", None)
            or getattr(source, "step_execution_manifest_ref", None)
        )
        if latest_ref:
            refs.append(str(latest_ref).strip())
    return list(dict.fromkeys(refs))


def _find_step_ledger_row(
    ledger: StepLedgerSnapshotModel,
    logical_step_id: str,
) -> Any:
    for row in ledger.steps:
        if row.logical_step_id == logical_step_id:
            return row
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "step_not_found",
            "message": "Step was not found in the latest execution ledger.",
        },
    )


def _is_ref_key(value: object) -> bool:
    normalized = str(value or "").replace("_", "").replace("-", "").lower()
    return normalized.endswith("ref") or normalized.endswith("refs")


def _camel_to_snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _field_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    if hasattr(value, key):
        return getattr(value, key)
    snake_key = _camel_to_snake(key)
    if hasattr(value, snake_key):
        return getattr(value, snake_key)
    return None


def _bounded_ref_projection(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _bounded_ref_projection(
            value.model_dump(by_alias=True, exclude_none=True)
        )
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key, nested in value.items():
            if _is_ref_key(key):
                projected[str(key)] = nested
                continue
            nested_projection = _bounded_ref_projection(nested)
            if nested_projection not in ({}, []):
                projected[str(key)] = nested_projection
        return projected
    if isinstance(value, list):
        projected_list = [
            nested_projection
            for item in value
            if (nested_projection := _bounded_ref_projection(item)) not in ({}, [])
        ]
        return projected_list
    return {}


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _safe_display_text(value: str | None) -> str | None:
    if value is None:
        return None
    return redact_sensitive_text(value)


def _quality_gate_verdict(checks: list[dict[str, Any]]) -> str | None:
    for check in checks:
        if not isinstance(check, (Mapping, BaseModel)):
            continue
        kind = str(
            _field_value(check, "kind") or _field_value(check, "name") or ""
        ).lower()
        if "gate" not in kind and "quality" not in kind:
            continue
        verdict = _first_text(
            _field_value(check, "verdict"),
            _field_value(check, "status"),
            _field_value(check, "result"),
        )
        if verdict:
            return verdict
    for check in checks:
        if not isinstance(check, (Mapping, BaseModel)):
            continue
        verdict = _first_text(
            _field_value(check, "verdict"),
            _field_value(check, "status"),
            _field_value(check, "result"),
        )
        if verdict:
            return verdict
    return None


def _evidence_ref_status(
    *,
    category: str,
    status: str,
    artifact_ref: str | None = None,
    boundary: str | None = None,
    reason_code: str | None = None,
    label: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "status": status,
        "artifactRef": artifact_ref,
        "boundary": boundary,
        "reasonCode": reason_code,
        "label": label,
        "summary": _safe_display_text(summary),
    }


def _ref_evidence(
    value: Any,
    *,
    category: str,
    missing_reason: str,
    label: str | None = None,
) -> dict[str, Any]:
    artifact_ref = _first_text(value)
    if artifact_ref:
        return _evidence_ref_status(
            category=category,
            status="available",
            artifact_ref=artifact_ref,
            label=label,
        )
    return _evidence_ref_status(
        category=category,
        status="missing",
        reason_code=missing_reason,
        label=label,
    )


def _checkpoint_evidence_by_boundary(
    workspace: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not workspace:
        return {}
    boundaries = {
        "before_execution": ("checkpointBeforeRef", "Before execution checkpoint"),
        "after_execution": ("checkpointAfterRef", "After execution checkpoint"),
        "state": ("stateCheckpointRef", "State checkpoint"),
        "workspace": ("workspaceCheckpointRef", "Workspace checkpoint"),
        "step": ("stepCheckpointRef", "Step checkpoint"),
    }
    refs: dict[str, dict[str, Any]] = {}
    for boundary, (key, label) in boundaries.items():
        artifact_ref = _first_text(_field_value(workspace, key))
        if artifact_ref:
            refs[boundary] = _evidence_ref_status(
                category="checkpoint",
                status="available",
                artifact_ref=artifact_ref,
                boundary=boundary,
                label=label,
            )
    return refs


def _gate_summary(checks: list[dict[str, Any]]) -> dict[str, Any] | None:
    verdict = _quality_gate_verdict(checks)
    artifact_ref = None
    summary = None
    for check in checks:
        if not isinstance(check, (Mapping, BaseModel)):
            continue
        artifact_ref = artifact_ref or _first_text(
            _field_value(check, "artifactRef"),
            _field_value(check, "verdictRef"),
        )
        summary = summary or _safe_display_text(
            _first_text(_field_value(check, "summary"))
        )
    if not verdict and not artifact_ref and not summary:
        return None
    return {"verdict": verdict, "summary": summary, "artifactRef": artifact_ref}


def _side_effect_summary(
    side_effects: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not side_effects:
        return None
    refs = _bounded_ref_projection(side_effects)
    artifact_refs = (
        {
            str(key): str(value)
            for key, value in refs.items()
            if isinstance(key, str) and isinstance(value, str) and value.strip()
        }
        if isinstance(refs, Mapping)
        else {}
    )
    summary = _safe_display_text(_first_text(_field_value(side_effects, "summary")))
    status_value = _first_text(_field_value(side_effects, "status"))
    allowed_statuses = {
        "available",
        "missing",
        "invalid",
        "unauthorized",
        "expired",
        "legacy",
        "incompatible",
        "skipped",
        "unavailable",
    }
    status = (
        status_value
        if status_value in allowed_statuses
        else ("available" if artifact_refs else "skipped")
    )
    if not artifact_refs and not summary and status == "skipped":
        return None
    return {"status": status, "artifactRefs": artifact_refs, "summary": summary}


def _environment_diagnostic_refs(
    workspace: Mapping[str, Any] | None,
    execution: Mapping[str, Any] | None,
    outputs: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    specs = (
        ("provider_lease", "providerLeaseDiagnosticsRef", "provider_lease_diagnostics"),
        ("sidecar", "sidecarDiagnosticsRef", "sidecar_diagnostics"),
        ("ghcr", "ghcrDiagnosticsRef", "ghcr_diagnostics"),
        ("preflight", "preflightDiagnosticsRef", "preflight_diagnostics"),
        ("environment", "environmentDiagnosticsRef", "environment_diagnostics"),
        ("system_error", "systemDiagnosticsRef", "system_error_diagnostics"),
    )
    diagnostics: list[dict[str, Any]] = []
    for kind, camel_key, reason in specs:
        artifact_ref = _first_text(
            _field_value(workspace, camel_key) if workspace else None,
            _field_value(execution, camel_key) if execution else None,
            _field_value(outputs, camel_key) if outputs else None,
        )
        if not artifact_ref:
            continue
        diagnostics.append(
            {
                "kind": kind,
                "status": "available",
                "diagnosticsRef": artifact_ref,
                "reasonCode": reason,
                "summary": (
                    f"{kind.replace('_', ' ').title()} diagnostics are available "
                    "for environment repair."
                ),
            }
        )
    return diagnostics


def _step_evidence_summary_payload(manifest: StepExecutionManifestModel) -> dict[str, Any]:
    return {
        "logicalStepId": manifest.logical_step_id,
        "executionOrdinal": manifest.execution_ordinal,
        "checkpointRefsByBoundary": _checkpoint_evidence_by_boundary(manifest.workspace),
        "contextBundleRef": _ref_evidence(
            _field_value(manifest.context, "contextBundleRef"),
            category="context",
            missing_reason="context_bundle_missing",
            label="Context bundle",
        ),
        "retrievalManifestRef": _ref_evidence(
            _field_value(manifest.context, "retrievalManifestRef"),
            category="retrieval",
            missing_reason="retrieval_manifest_missing",
            label="Retrieval manifest",
        ),
        "memoryManifestRef": _ref_evidence(
            _field_value(manifest.context, "memoryManifestRef"),
            category="memory",
            missing_reason="memory_manifest_missing",
            label="Memory manifest",
        ),
        "gateSummary": _gate_summary(manifest.checks),
        "terminalDisposition": manifest.terminal_disposition,
        "sideEffectSummary": _side_effect_summary(manifest.side_effects),
        "diagnosticRefs": _environment_diagnostic_refs(
            manifest.workspace,
            manifest.execution,
            manifest.outputs,
        ),
    }


def _is_environment_blocked_manifest(manifest: StepExecutionManifestModel) -> bool:
    if manifest.status == "blocked" or manifest.terminal_disposition == "blocked":
        return True
    summary = _first_text(
        _field_value(manifest.outputs, "summary"),
        _field_value(manifest.execution, "summary"),
    )
    return bool(summary and "environment" in summary.lower())


def _recovery_eligibility_payload(manifest: StepExecutionManifestModel) -> dict[str, Any]:
    required_boundary = "before_execution"
    checkpoint_ref = (
        _first_text(_field_value(manifest.workspace, "checkpointBeforeRef"))
        if manifest.workspace
        else None
    )
    diagnostics = _environment_diagnostic_refs(
        manifest.workspace,
        manifest.execution,
        manifest.outputs,
    )
    source_workflow_id = (
        _first_text(_field_value(manifest.lineage, "sourceWorkflowId"))
        if manifest.lineage
        else None
    )
    source_run_id = (
        _first_text(_field_value(manifest.lineage, "sourceRunId"))
        if manifest.lineage
        else None
    )
    if diagnostics and _is_environment_blocked_manifest(manifest):
        return {
            "eligible": False,
            "requestedAction": "resume_from_workspace_checkpoint",
            "defaultAction": "fix_environment",
            "disabledReasonCode": "environment_invalid",
            "requiredBoundary": required_boundary,
            "checkpointRef": None,
            "sourceWorkflowId": source_workflow_id,
            "sourceRunId": source_run_id,
            "operatorGuidance": "fix_environment",
            "evidence": [
                _evidence_ref_status(
                    category=item["kind"] if item["kind"] != "system_error" else "environment",
                    status=item["status"],
                    artifact_ref=item["diagnosticsRef"],
                    reason_code=item["reasonCode"],
                    label=f"{item['kind'].replace('_', ' ').title()} diagnostics",
                    summary=item["summary"],
                )
                for item in diagnostics
                if item["kind"] != "system_error"
            ],
        }
    if checkpoint_ref:
        return {
            "eligible": False,
            "requestedAction": "resume_from_workspace_checkpoint",
            "defaultAction": "full_retry",
            "disabledReasonCode": "CHECKPOINT_CAPABILITY_SNAPSHOT_MISSING",
            "checkpointBoundary": required_boundary,
            "checkpointRef": checkpoint_ref,
            "sourceWorkflowId": source_workflow_id,
            "sourceRunId": source_run_id,
            "operatorGuidance": "full_retry",
            "evidence": [
                _evidence_ref_status(
                    category="checkpoint",
                    status="available",
                    artifact_ref=checkpoint_ref,
                    boundary=required_boundary,
                    label="Before execution checkpoint",
                )
            ],
        }
    return {
        "eligible": False,
        "requestedAction": "resume_from_workspace_checkpoint",
        "defaultAction": "full_retry",
        "disabledReasonCode": "CHECKPOINT_ARTIFACT_INVALID",
        "checkpointBoundary": required_boundary,
        "checkpointRef": None,
        "sourceWorkflowId": source_workflow_id,
        "sourceRunId": source_run_id,
        "operatorGuidance": "full_retry",
        "evidence": [
            _evidence_ref_status(
                category="checkpoint",
                status="missing",
                boundary=required_boundary,
                reason_code="missing_required_checkpoint_boundary",
                label="Before execution checkpoint",
            )
        ],
    }


def _preserved_step_provenance_payload(
    manifest: StepExecutionManifestModel,
) -> list[dict[str, Any]]:
    recovery_source = manifest.recovery_source or {}
    raw_steps = _field_value(recovery_source, "preservedSteps")
    if not isinstance(raw_steps, list):
        return []
    preserved: list[dict[str, Any]] = []
    for item in raw_steps:
        if isinstance(item, BaseModel):
            source = item.model_dump(by_alias=True, exclude_none=True)
        elif isinstance(item, Mapping):
            source = item
        else:
            continue
        logical_step_id = _first_text(
            _field_value(source, "logicalStepId"),
            _field_value(source, "stepId"),
        )
        if not logical_step_id:
            continue
        output_refs = _bounded_ref_projection(_field_value(source, "outputRefs"))
        preserved.append(
            {
                "logicalStepId": logical_step_id,
                "title": _safe_display_text(_first_text(_field_value(source, "title"))),
                "sourceWorkflowId": _first_text(
                    _field_value(source, "sourceWorkflowId"),
                    _field_value(recovery_source, "sourceWorkflowId"),
                    _field_value(manifest.lineage, "sourceWorkflowId")
                    if manifest.lineage
                    else None,
                ),
                "sourceRunId": _first_text(
                    _field_value(source, "sourceRunId"),
                    _field_value(recovery_source, "sourceRunId"),
                    _field_value(manifest.lineage, "sourceRunId")
                    if manifest.lineage
                    else None,
                ),
                "sourceExecutionOrdinal": _field_value(source, "sourceExecutionOrdinal"),
                "stateCheckpointRef": _first_text(_field_value(source, "stateCheckpointRef")),
                "outputRefs": output_refs if isinstance(output_refs, Mapping) else {},
            }
        )
    return preserved


def _runtime_child_refs(execution: Any) -> dict[str, Any]:
    refs = dict(_bounded_ref_projection(execution))
    for key in ("childWorkflowId", "childRunId"):
        value = _field_value(execution, key)
        if isinstance(value, str) and value.strip():
            refs[key] = value.strip()
    agent_run_id = (
        _field_value(execution, "agentRunId")
        or _field_value(execution, "agent_run_id")
    )
    if isinstance(agent_run_id, str) and agent_run_id.strip():
        refs["agentRunId"] = agent_run_id.strip()
    return refs


def _step_execution_projection_payload(
    manifest: StepExecutionManifestModel,
    *,
    manifest_artifact_ref: str,
) -> dict[str, Any]:
    workspace = manifest.workspace
    execution = manifest.execution
    side_effects = manifest.side_effects
    outputs = manifest.outputs
    checks = manifest.checks
    runtime_child_refs = _runtime_child_refs(execution)
    source_execution_ordinal = (
        manifest.lineage.source_execution_ordinal if manifest.lineage is not None else None
    )
    summary = _safe_display_text(
        _first_text(
            _field_value(outputs, "summary"),
            _field_value(execution, "summary"),
        )
    )
    return {
        "manifestArtifactRef": manifest_artifact_ref,
        "stepExecutionId": manifest.step_execution_id,
        "workflowId": manifest.workflow_id,
        "runId": manifest.run_id,
        "logicalStepId": manifest.logical_step_id,
        "executionOrdinal": manifest.execution_ordinal,
        "sourceExecutionOrdinal": source_execution_ordinal,
        "lineage": manifest.lineage,
        "branch": manifest.branch,
        "reason": manifest.reason,
        "status": manifest.status,
        "terminalDisposition": manifest.terminal_disposition,
        "startedAt": manifest.started_at,
        "updatedAt": manifest.updated_at,
        "summary": summary,
        "runtimeChildRefs": runtime_child_refs,
        "workspacePolicy": _first_text(
            _field_value(workspace, "workspacePolicy"),
            _field_value(workspace, "policy"),
        ),
        "gitDisposition": _first_text(
            _field_value(side_effects, "gitDisposition"),
            _field_value(workspace, "gitDisposition"),
        ),
        "qualityGateVerdict": _quality_gate_verdict(checks),
        "manifestRefs": {"manifestArtifactRef": manifest_artifact_ref},
        "outputRefs": _bounded_ref_projection(outputs),
        "stepEvidence": _step_evidence_summary_payload(manifest),
        "recoveryEligibility": _recovery_eligibility_payload(manifest),
    }


def _step_execution_detail_payload(
    manifest: StepExecutionManifestModel,
    *,
    manifest_artifact_ref: str,
) -> dict[str, Any]:
    payload = _step_execution_projection_payload(
        manifest,
        manifest_artifact_ref=manifest_artifact_ref,
    )
    payload.update(
        {
            "inputRefs": _bounded_ref_projection(manifest.input),
            "contextRefs": _bounded_ref_projection(manifest.context),
            "workspaceRefs": _bounded_ref_projection(manifest.workspace),
            "executionRefs": _runtime_child_refs(manifest.execution),
            "checkRefs": _bounded_ref_projection(manifest.checks),
            "sideEffectRefs": _bounded_ref_projection(manifest.side_effects),
            "dependencyEffectRefs": _bounded_ref_projection(
                manifest.dependency_effects
            ),
            "preservedStepProvenance": _preserved_step_provenance_payload(manifest),
        }
    )
    return payload


async def _read_step_execution_manifest(
    *,
    artifact_service: Any,
    artifact_ref: str,
    principal: str,
) -> tuple[StepExecutionManifestModel | None, dict[str, Any] | None]:
    artifact_id = _artifact_id_from_ref(artifact_ref)
    if not artifact_id:
        return None, _step_execution_compatibility_decision(
            failure_code="invalid_step_execution_manifest_ref",
            message="Step Execution manifest ref is invalid.",
        )
    try:
        _artifact, body = await artifact_service.read(
            artifact_id=artifact_id,
            principal=principal,
            allow_restricted_raw=True,
        )
        payload = json.loads(body.decode("utf-8"))
        return StepExecutionManifestModel.model_validate(payload), None
    except (PermissionError, TemporalArtifactAuthorizationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "step_execution_manifest_unauthorized",
                "message": "Not authorized to read Step Execution manifest evidence.",
            },
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, _step_execution_compatibility_decision(
            failure_code="malformed_step_execution_manifest",
            message="Step Execution manifest artifact is malformed.",
        )
    except ValidationError:
        return None, _step_execution_compatibility_decision(
            failure_code="invalid_step_execution_manifest",
            message="Step Execution manifest artifact is invalid.",
        )


def _step_execution_compatibility_decision(
    *,
    failure_code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "valid": False,
        "decision": "invalid",
        "failureCode": failure_code,
        "message": _safe_display_text(message) or "Step Execution value is invalid.",
    }


def _degraded_step_execution_projection_payload(
    *,
    ledger: StepLedgerSnapshotModel,
    logical_step_id: str,
    manifest_artifact_ref: str,
    compatibility_decision: Mapping[str, Any],
    fallback_ordinal: int,
) -> dict[str, Any]:
    # A degraded manifest carries no trustworthy ordinal of its own, and
    # ``row.execution_ordinal`` is the row-level latest attempt count rather
    # than this ref's ordinal. Using it would map a degraded older ref onto
    # the latest ordinal, shadowing the valid attempt at that ordinal and
    # producing duplicate ordinals. Use the per-ref fallback index instead so
    # each degraded ref keeps its own position.
    execution_ordinal = fallback_ordinal
    return {
        "manifestArtifactRef": manifest_artifact_ref,
        "stepExecutionId": (
            f"{ledger.workflow_id}:{ledger.run_id}:{logical_step_id}:"
            f"execution:{execution_ordinal}:invalid"
        ),
        "workflowId": ledger.workflow_id,
        "runId": ledger.run_id,
        "logicalStepId": logical_step_id,
        "executionOrdinal": execution_ordinal,
        "reason": None,
        "status": None,
        "summary": "Step Execution manifest is unavailable as a valid current projection.",
        "manifestRefs": {"manifestArtifactRef": manifest_artifact_ref},
        "outputRefs": {},
        "stepEvidence": None,
        "recoveryEligibility": None,
        "compatibilityDecision": dict(compatibility_decision),
    }


def _build_action_capabilities(record) -> ExecutionActionCapabilityModel:
    raw_state = str(record.state.value).strip().lower()
    workflow_type_value = _enum_value(getattr(record, "workflow_type", None))
    memo = dict(getattr(record, "memo", None) or {})
    persisted_finish_summary = getattr(record, "finish_summary_json", None)
    finish_summary = (
        dict(persisted_finish_summary)
        if isinstance(persisted_finish_summary, Mapping)
        else (_finish_summary_from_memo(memo) or {})
    )
    control_stop = (
        finish_summary.get("controlStop")
        if isinstance(finish_summary, Mapping)
        else None
    )
    control_stop = control_stop if isinstance(control_stop, Mapping) else {}
    metrics = control_stop.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    auxiliary_outcomes = control_stop.get("auxiliaryOutcomes")
    auxiliary_outcomes = (
        auxiliary_outcomes if isinstance(auxiliary_outcomes, Mapping) else {}
    )
    waiting_reason = (
        str(getattr(record, "waiting_reason", "") or "").strip()
        or str(memo.get("waiting_reason") or "").strip()
    )
    is_operator_paused = (
        bool(getattr(record, "paused", False)) or waiting_reason == "operator_paused"
    )
    if not settings.temporal_dashboard.actions_enabled:
        return ExecutionActionCapabilityModel(
            disabled_reasons={
                action: "actions_disabled"
                for action in (
                    "setTitle",
                    "updateInputs",
                    "editForRerun",
                    "rerun",
                    "approve",
                    "pause",
                    "resume",
                    "canResumeFromFailedStep",
                    "cancel",
                    "reject",
                    "sendMessage",
                    "bypassDependencies",
                )
            }
        )

    temporal_workflow_editing_enabled = bool(
        settings.temporal_dashboard.temporal_workflow_editing_enabled
    )
    state_actions = {
        "scheduled": {
            "can_set_title",
            "can_update_inputs",
            "can_pause",
            "can_cancel",
        },
        "initializing": {
            "can_set_title",
            "can_update_inputs",
            "can_pause",
            "can_cancel",
        },
        "waiting_on_dependencies": {
            "can_set_title",
            "can_update_inputs",
            "can_pause",
            "can_cancel",
            "can_bypass_dependencies",
        },
        "awaiting_slot": {
            "can_set_title",
            "can_update_inputs",
            "can_pause",
            "can_cancel",
        },
        "planning": {
            "can_set_title",
            "can_update_inputs",
            "can_pause",
            "can_cancel",
        },
        "executing": {
            "can_set_title",
            "can_update_inputs",
            "can_pause",
            "can_cancel",
        },
        "proposals": {
            "can_set_title",
            "can_update_inputs",
            "can_pause",
            "can_cancel",
        },
        "awaiting_external": {
            "can_approve",
            "can_pause",
            "can_resume",
            "can_cancel",
            "can_reject",
            "can_send_message",
        },
        "finalizing": {"can_pause", "can_cancel"},
        "completed": {"can_edit_for_rerun", "can_rerun"},
        "failed": {"can_edit_for_rerun", "can_rerun"},
        "canceled": {"can_edit_for_rerun", "can_rerun"},
        "terminated": {"can_edit_for_rerun", "can_rerun"},
        "timed_out": {"can_edit_for_rerun", "can_rerun"},
    }
    enabled = state_actions.get(raw_state, set())
    if is_operator_paused and raw_state not in {
        "completed",
        "failed",
        "canceled",
        "terminated",
        "timed_out",
    }:
        enabled = (enabled - {"can_pause"}) | {"can_resume"}
    has_workflow_input_snapshot = bool(
        _workflow_input_snapshot_ref_from_memo(dict(getattr(record, "memo", None) or {}))
    )
    resume_evidence_disabled_reason = _recovery_evidence_disabled_reason(record)
    if (
        workflow_type_value != "MoonMind.UserWorkflow"
        or not temporal_workflow_editing_enabled
        or not has_workflow_input_snapshot
    ):
        enabled = enabled - {"can_update_inputs", "can_edit_for_rerun", "can_rerun"}
    if (
        workflow_type_value == "MoonMind.UserWorkflow"
        and temporal_workflow_editing_enabled
        and raw_state == "failed"
        and has_workflow_input_snapshot
        and resume_evidence_disabled_reason is None
    ):
        enabled = enabled | {"can_failed_step_resume"}
    git_publication = auxiliary_outcomes.get("gitPublication")
    if raw_state == "failed":
        enabled = enabled | {"can_full_retry"}
        if bool(metrics.get("remediationAdmitted")):
            enabled = enabled | {"can_continue_remediation"}
        publication_contract = (
            git_publication.get("recoveryContract")
            if isinstance(git_publication, Mapping)
            else None
        )
        publication_eligible, _ = publication_action_eligibility(
            publication_contract
        )
        if (
            isinstance(git_publication, Mapping)
            and git_publication.get("status") == "failed"
            and publication_eligible
        ):
            enabled = enabled | {"can_retry_publication"}
    capability_values = {
        "can_set_title": "canSetTitle",
        "can_update_inputs": "canUpdateInputs",
        "can_edit_for_rerun": "canEditForRerun",
        "can_rerun": "canRerun",
        "can_approve": "canApprove",
        "can_pause": "canPause",
        "can_resume": "canResume",
        "can_failed_step_resume": "canResumeFromFailedStep",
        "can_continue_remediation": "canContinueRemediation",
        "can_retry_publication": "canRetryPublication",
        "can_full_retry": "canFullRetry",
        "can_cancel": "canCancel",
        "can_reject": "canReject",
        "can_send_message": "canSendMessage",
        "can_bypass_dependencies": "canBypassDependencies",
    }
    disabled_reasons = {}
    for field_name, alias in capability_values.items():
        if field_name in enabled:
            continue
        if field_name in {
            "can_update_inputs",
            "can_edit_for_rerun",
            "can_rerun",
            "can_failed_step_resume",
        }:
            if workflow_type_value != "MoonMind.UserWorkflow":
                disabled_reasons[alias] = "unsupported_workflow_type"
                continue
            if not temporal_workflow_editing_enabled:
                disabled_reasons[alias] = "temporal_workflow_editing_disabled"
                continue
            if field_name == "can_failed_step_resume":
                if raw_state != "failed":
                    disabled_reasons[alias] = "state_not_eligible"
                    continue
                if not has_workflow_input_snapshot:
                    disabled_reasons[alias] = "original_task_input_snapshot_missing"
                    continue
                if resume_evidence_disabled_reason is not None:
                    disabled_reasons[alias] = resume_evidence_disabled_reason
                    continue
            if field_name == "can_update_inputs" and not has_workflow_input_snapshot:
                disabled_reasons[alias] = "original_task_input_snapshot_missing"
                continue
            if field_name in {"can_edit_for_rerun", "can_rerun"}:
                if not has_workflow_input_snapshot:
                    disabled_reasons[alias] = "original_task_input_snapshot_missing"
                    continue
        if field_name == "can_pause" and is_operator_paused:
            disabled_reasons[alias] = "already_paused"
            continue
        if field_name == "can_continue_remediation" and raw_state == "failed":
            disabled_reasons[alias] = (
                "remediation_not_admitted"
                if control_stop.get("kind") == "workflow_gate"
                else "control_stop_not_available"
            )
            continue
        if field_name == "can_retry_publication" and raw_state == "failed":
            git_publication = auxiliary_outcomes.get("gitPublication")
            if not isinstance(git_publication, Mapping):
                disabled_reasons[alias] = "publication_retry_not_applicable"
            elif git_publication.get("status") != "failed":
                disabled_reasons[alias] = "publication_not_failed"
            else:
                _, reason = publication_action_eligibility(
                    git_publication.get("recoveryContract")
                )
                disabled_reasons[alias] = (
                    reason or "publication_retry_not_eligible"
                )
            continue
        disabled_reasons[alias] = "state_not_eligible"
    common_control_stop_evidence = {
        "candidateRef": control_stop.get("workspaceHeadRef"),
        "remainingWorkRef": control_stop.get("remainingWorkRef"),
    }
    continuation_evidence = {
        **common_control_stop_evidence,
        **{
            destination: control_stop[source]
            for source, destination in (
                ("controlStopId", "controlStopId"),
                ("sourceBudget", "sourceBudget"),
                ("continuationBudgetGrant", "continuationBudget"),
                ("destinationWorkflowId", "destinationWorkflowId"),
                ("restorationEvidenceRef", "restorationEvidenceRef"),
                ("restorationEvidenceDigest", "restorationEvidenceDigest"),
                ("hostSessionLifecycle", "hostSessionLifecycle"),
            )
            if source in control_stop
        },
    }
    action_evidence = {
        **{
            action: (
                continuation_evidence
                if action == "continueRemediation"
                else common_control_stop_evidence
            )
            for action in (
                "editForRerun",
                "fullRetry",
                "continueRemediation",
            )
            if control_stop
        },
        **(
            {
                "retryPublication": {
                    "candidateRef": (
                        git_publication.get("recoveryContract", {})
                        .get("continuation", {})
                        .get("candidateRef")
                    ),
                    "publicationIntent": git_publication.get(
                        "recoveryContract", {}
                    ).get("intent"),
                    "publicationRecoveryWorkflowId": git_publication.get(
                        "recoveryWorkflowId"
                    ),
                    "publicationResult": git_publication.get(
                        "recoveryResult"
                    ),
                }
            }
            if isinstance(git_publication, Mapping)
            else {}
        ),
    }
    if workflow_type_value == "MoonMind.PublicationRecoveryV1":
        action_evidence["publicationRecovery"] = {
            "sourceWorkflowId": memo.get("source_workflow_id"),
            "sourceRunId": memo.get("source_run_id"),
            "semanticContext": memo.get("publication_semantic_context"),
            "phase": memo.get("publication_recovery_phase", "contract_validation"),
            "result": memo.get("publication_recovery_result"),
            "publicationOutcome": memo.get("publication_outcome"),
            "implementationRerun": False,
            "verificationRerun": False,
        }
    return ExecutionActionCapabilityModel(
        can_set_title="can_set_title" in enabled,
        can_update_inputs="can_update_inputs" in enabled,
        can_edit_for_rerun="can_edit_for_rerun" in enabled,
        can_rerun="can_rerun" in enabled,
        can_approve="can_approve" in enabled,
        can_pause="can_pause" in enabled,
        can_resume="can_resume" in enabled,
        can_failed_step_resume="can_failed_step_resume" in enabled,
        can_continue_remediation="can_continue_remediation" in enabled,
        can_retry_publication="can_retry_publication" in enabled,
        can_full_retry="can_full_retry" in enabled,
        action_evidence=action_evidence,
        can_cancel="can_cancel" in enabled,
        can_reject="can_reject" in enabled,
        can_send_message="can_send_message" in enabled,
        can_bypass_dependencies="can_bypass_dependencies" in enabled,
        disabled_reasons=disabled_reasons,
    )

def _build_debug_fields(
    *,
    record,
    temporal_status: str,
    close_status: str | None,
    waiting_reason: str | None,
    attention_required: bool,
) -> ExecutionDebugFieldsModel | None:
    if not settings.temporal_dashboard.debug_fields_enabled:
        return None
    return ExecutionDebugFieldsModel(
        workflow_id=record.workflow_id,
        run_id=record.run_id,
        temporal_run_id=record.run_id,
        legacy_run_id=None,
        namespace=record.namespace,
        temporal_status=temporal_status,
        raw_state=record.state.value,
        close_status=close_status,
        waiting_reason=waiting_reason,
        attention_required=attention_required,
    )

def _normalize_no_commit_finish_summary(
    finish_summary: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    return normalize_no_commit_finish_summary(finish_summary, logger=logger)

def _coerce_artifact_ref(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, dict):
        for key in ("artifactId", "artifact_id", "id"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
    return None

def _parse_intervention_audit_entries(
    memo: Mapping[str, object],
) -> list[dict[str, object]]:
    raw_entries = memo.get("intervention_audit")
    if not isinstance(raw_entries, list):
        return []

    entries: list[dict[str, object]] = []
    for item in raw_entries:
        if not isinstance(item, Mapping):
            continue
        action = str(item.get("action") or "").strip()
        transport = str(item.get("transport") or "").strip()
        summary = str(item.get("summary") or "").strip()
        created_at_raw = item.get("createdAt")
        if not action or not transport or not summary or not isinstance(created_at_raw, str):
            continue
        try:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        entries.append(
            {
                "action": action,
                "transport": transport,
                "summary": summary,
                "detail": str(item.get("detail") or "").strip() or None,
                "createdAt": created_at,
            }
        )
    return entries

def _invalid_workflow_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "invalid_execution_request",
            "message": message,
        },
    )


def _validate_runtime_tier_intent_for_submit(
    runtime_payload: Mapping[str, Any] | None,
    *,
    field_name: str,
) -> dict[str, Any]:
    try:
        return validate_runtime_tier_intent(runtime_payload, field_name=field_name)
    except RuntimeIntentValidationError as exc:
        raise _invalid_workflow_request(str(exc)) from exc


def _resolve_runtime_model_effort(
    *,
    runtime_id: str | None,
    profile: Any | None,
    runtime_payload: Mapping[str, Any],
    requested_model: str | None,
) -> tuple[str | None, str, str | None, dict[str, Any] | None]:
    requested_tier = _runtime_model_tier(runtime_payload)
    requested_effort = runtime_payload.get("effort")
    if requested_effort is not None and not isinstance(requested_effort, str):
        raise _invalid_workflow_request("runtime.effort must be a string.")
    tier_fallback = str(runtime_payload.get("tierFallback") or "clamp")
    advisory_preview = (
        runtime_payload.get("tierPreview")
        if isinstance(runtime_payload.get("tierPreview"), Mapping)
        else None
    )

    if profile is not None and (requested_tier is not None or _profile_has_model_tiers(profile)):
        try:
            resolved = resolve_model_effort(
                runtime_id=runtime_id,
                profile=profile,
                requested_model_tier=requested_tier,
                requested_model=requested_model,
                requested_effort=requested_effort,
                tier_fallback=tier_fallback,
                advisory_preview=advisory_preview,
                workflow_settings=settings.workflow,
            )
        except ValueError as exc:
            raise _invalid_workflow_request(str(exc)) from exc
        return (
            resolved.model,
            resolved.model_source,
            resolved.effort,
            _model_tier_resolution_payload(resolved, profile=profile),
        )

    resolved_model, model_source = resolve_effective_model(
        runtime_id=runtime_id,
        profile=profile,
        requested_model=requested_model,
        workflow_settings=settings.workflow,
    )
    return resolved_model, model_source, requested_effort, None


async def _load_default_provider_profile_for_runtime(
    *,
    session: Any,
    runtime_id: str | None,
) -> ManagedAgentProviderProfile | None:
    if session is None or not runtime_id:
        return None
    execute = getattr(session, "execute", None)
    if not callable(execute):
        return None
    stmt = (
        select(ManagedAgentProviderProfile)
        .where(
            ManagedAgentProviderProfile.runtime_id == runtime_id,
            ManagedAgentProviderProfile.enabled.is_(True),
        )
        .order_by(
            ManagedAgentProviderProfile.is_default.desc(),
            ManagedAgentProviderProfile.priority.desc(),
            ManagedAgentProviderProfile.profile_id.asc(),
        )
        .limit(1)
    )
    result = await execute(stmt)
    return result.scalars().first()


def _runtime_model_tier(runtime_payload: Mapping[str, Any]) -> int | None:
    if "modelTier" not in runtime_payload:
        return None
    value = runtime_payload.get("modelTier")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid_workflow_request(
            "modelTier must be an integer greater than or equal to 1."
        )
    if value < 1:
        raise _invalid_workflow_request(
            "modelTier must be an integer greater than or equal to 1."
        )
    return value


def _profile_has_model_tiers(profile: Any) -> bool:
    tiers = (
        profile.get("model_tiers")
        if isinstance(profile, Mapping)
        else getattr(profile, "model_tiers", None)
    )
    return isinstance(tiers, list) and bool(tiers)


def _model_tier_resolution_payload(
    resolved: ResolvedModelEffort,
    *,
    profile: Any,
) -> dict[str, Any]:
    payload = resolved.as_metadata()
    profile_id = (
        profile.get("profile_id")
        if isinstance(profile, Mapping)
        else getattr(profile, "profile_id", None)
    )
    if profile_id:
        payload["providerProfileId"] = str(profile_id)
    return payload


def _reject_submit_version_identity(task_payload: dict[str, Any]) -> None:
    try:
        reject_workflow_capability_identity_versions(
            task_payload,
            field_path="payload.workflow",
        )
    except WorkflowContractError as exc:
        raise _invalid_workflow_request(str(exc)) from exc

def _validation_error_code(message: str) -> str:
    if message.startswith("Dependency not found:"):
        return "dependency_not_found"
    if message.startswith("Dependency unauthorized:"):
        return "dependency_unauthorized"
    if message.startswith("Dependency ") and "must use workflowId, not runId." in message:
        return "dependency_invalid_identifier"
    if message.startswith("Dependency ") and message.endswith("must be a workflowId."):
        return "dependency_invalid_identifier"
    if message.startswith("Dependency ") and "workflow, not a MoonMind.UserWorkflow workflow." in message:
        return "dependency_unsupported_workflow_type"
    if message.startswith("Workflow cannot depend on itself:"):
        return "dependency_self_reference"
    if message == "dependsOn can have a maximum of 10 items.":
        return "dependency_limit_exceeded"
    return "invalid_execution_request"

def _coerce_string_list(value: Any, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid_workflow_request(f"{field_name} must be a JSON array of strings.")

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise _invalid_workflow_request(
                f"{field_name} must be a JSON array of strings."
            )
        candidate = item.strip()
        if candidate:
            normalized.append(candidate)
    return normalized

def _coerce_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}

def _coerce_step_count(value: Any) -> int:
    if value is None:
        return 0
    if not isinstance(value, list):
        raise _invalid_workflow_request("payload.workflow.steps must be a JSON array.")
    return len(value)

def _default_task_step_id(index: int) -> str:
    return f"step-{index + 1}"

def _task_step_id_from_payload(step: Mapping[str, Any], index: int) -> str:
    for key in ("id", "stepId", "stepRef", "ref"):
        candidate = str(step.get(key) or "").strip()
        if candidate:
            return candidate
    return _default_task_step_id(index)

_ATTACHMENT_REF_KEYS = frozenset(
    {"artifactId", "filename", "contentType", "sizeBytes"}
)
_FORBIDDEN_ATTACHMENT_CONTENT_TYPES = frozenset({"image/svg+xml"})

def _normalized_attachment_content_type(value: object) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()

def _allowed_attachment_content_types() -> set[str]:
    configured = {
        _normalized_attachment_content_type(item)
        for item in settings.workflow.agent_job_attachment_allowed_content_types
    }
    configured.discard("")
    configured.difference_update(_FORBIDDEN_ATTACHMENT_CONTENT_TYPES)
    return configured or {"image/png", "image/jpeg", "image/webp"}

def _normalize_attachment_ref(raw: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise _invalid_workflow_request(f"{field_name} must be an object.")
    unsupported = sorted(str(key) for key in raw.keys() if key not in _ATTACHMENT_REF_KEYS)
    if unsupported:
        raise _invalid_workflow_request(
            f"{field_name} contains unsupported fields: {', '.join(unsupported)}."
        )
    artifact_id = str(raw.get("artifactId") or "").strip()
    if not artifact_id:
        raise _invalid_workflow_request(f"{field_name}.artifactId is required.")
    if not artifact_id.startswith("art_"):
        raise _invalid_workflow_request(f"{field_name}.artifactId must be a MoonMind artifact id.")
    filename = str(raw.get("filename") or "").strip()
    if not filename:
        raise _invalid_workflow_request(f"{field_name}.filename is required.")
    content_type = _normalized_attachment_content_type(raw.get("contentType"))
    if not content_type:
        raise _invalid_workflow_request(f"{field_name}.contentType is required.")
    if content_type in _FORBIDDEN_ATTACHMENT_CONTENT_TYPES:
        raise _invalid_workflow_request(f"{content_type} is not supported for input attachments.")
    allowed = _allowed_attachment_content_types()
    if content_type not in allowed:
        supported = ", ".join(sorted(allowed))
        raise _invalid_workflow_request(
            f"{field_name}.contentType must be one of: {supported}."
        )
    size_value = raw.get("sizeBytes")
    if isinstance(size_value, bool):
        raise _invalid_workflow_request(f"{field_name}.sizeBytes must be a non-negative integer.")
    try:
        size_bytes = int(size_value)
    except (TypeError, ValueError) as exc:
        raise _invalid_workflow_request(
            f"{field_name}.sizeBytes must be a non-negative integer."
        ) from exc
    if size_bytes < 0:
        raise _invalid_workflow_request(f"{field_name}.sizeBytes must be a non-negative integer.")
    return {
        "artifactId": artifact_id,
        "filename": filename,
        "contentType": content_type,
        "sizeBytes": size_bytes,
    }

def _normalize_attachment_ref_list(raw: Any, *, field_name: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _invalid_workflow_request(f"{field_name} must be a JSON array.")
    return [
        _normalize_attachment_ref(item, field_name=f"{field_name}[{index}]")
        for index, item in enumerate(raw)
    ]

async def _validate_and_collect_task_input_attachments(
    *,
    task_payload: Mapping[str, Any],
    session: AsyncSession | None,
    principal: str,
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]], list[dict[str, Any]]]:
    objective_refs = _normalize_attachment_ref_list(
        task_payload.get("inputAttachments") or task_payload.get("input_attachments"),
        field_name="payload.workflow.inputAttachments",
    )
    step_refs: dict[int, list[dict[str, Any]]] = {}
    raw_steps = task_payload.get("steps")
    if isinstance(raw_steps, list):
        for index, step in enumerate(raw_steps):
            if not isinstance(step, Mapping):
                continue
            refs = _normalize_attachment_ref_list(
                step.get("inputAttachments") or step.get("input_attachments"),
                field_name=f"payload.workflow.steps[{index}].inputAttachments",
            )
            if refs:
                step_refs[index] = refs

    attachment_index: list[dict[str, Any]] = []
    for ref in objective_refs:
        attachment_index.append({**ref, "targetKind": "objective"})
    for index, refs in step_refs.items():
        step = raw_steps[index] if isinstance(raw_steps, list) else {}
        step_ref = ""
        if isinstance(step, Mapping):
            step_ref = _task_step_id_from_payload(step, index)
        for ref in refs:
            attachment_index.append(
                {
                    **ref,
                    "targetKind": "step",
                    "stepOrdinal": index,
                    **({"stepRef": step_ref} if step_ref else {}),
                }
            )

    if not attachment_index:
        return objective_refs, step_refs, []
    if not settings.workflow.agent_job_attachment_enabled:
        raise _invalid_workflow_request("input attachment policy is disabled.")

    unique: dict[str, dict[str, Any]] = {}
    for ref in attachment_index:
        artifact_id = ref["artifactId"]
        existing = unique.get(artifact_id)
        if existing is not None:
            raise _invalid_workflow_request(
                f"input attachment {artifact_id} is declared more than once."
            )
        unique.setdefault(artifact_id, ref)

    if len(unique) > settings.workflow.agent_job_attachment_max_count:
        raise _invalid_workflow_request(
            "too many input attachments "
            f"({len(unique)}/{settings.workflow.agent_job_attachment_max_count})."
        )
    max_bytes = int(settings.workflow.agent_job_attachment_max_bytes)
    total_bytes = 0
    for ref in unique.values():
        size_bytes = int(ref["sizeBytes"])
        if size_bytes > max_bytes:
            raise _invalid_workflow_request(
                f"input attachment {ref['artifactId']} exceeds max bytes ({max_bytes})."
            )
        total_bytes += size_bytes
    total_limit = int(settings.workflow.agent_job_attachment_total_bytes)
    if total_bytes > total_limit:
        raise _invalid_workflow_request(
            f"input attachments exceed total bytes ({total_limit})."
        )

    if session is not None:
        artifact_ids = list(unique)
        artifact_result = await session.execute(
            select(TemporalArtifact).where(
                TemporalArtifact.artifact_id.in_(artifact_ids)
            )
        )
        artifacts_by_id = {
            artifact.artifact_id: artifact
            for artifact in artifact_result.scalars().all()
        }
        for ref in unique.values():
            artifact = artifacts_by_id.get(ref["artifactId"])
            if artifact is None:
                raise _invalid_workflow_request(
                    f"input attachment artifact was not found: {ref['artifactId']}."
                )
            if artifact.status is not TemporalArtifactStatus.COMPLETE:
                raise _invalid_workflow_request(
                    "input attachment artifact must be complete before execution start: "
                    f"{ref['artifactId']} is {artifact.status.value}."
                )
            owner = str(getattr(artifact, "created_by_principal", None) or "").strip()
            if (
                settings.oidc.AUTH_PROVIDER != "disabled"
                and owner
                and owner != principal
                and not principal.startswith("service:")
            ):
                raise _invalid_workflow_request(
                    f"input attachment artifact is not authorized for this execution: {ref['artifactId']}."
                )
            artifact_content_type = _normalized_attachment_content_type(
                artifact.content_type
            )
            if artifact_content_type and artifact_content_type != ref["contentType"]:
                raise _invalid_workflow_request(
                    f"input attachment {ref['artifactId']} content type mismatch."
                )
            if artifact.size_bytes is not None and int(artifact.size_bytes) != int(
                ref["sizeBytes"]
            ):
                raise _invalid_workflow_request(
                    f"input attachment {ref['artifactId']} size mismatch."
                )

    return objective_refs, step_refs, attachment_index

def _normalize_task_skill_selectors(
    raw: Any, *, field_name: str
) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise _invalid_workflow_request(f"{field_name} must be an object.")
    try:
        return WorkflowSkillSelectors.model_validate(dict(raw)).model_dump(
            by_alias=True,
            exclude_none=True,
        )
    except (WorkflowContractError, ValidationError, ValueError) as exc:
        raise _invalid_workflow_request(str(exc)) from exc

def _normalize_workflow_proposal_policy(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise _invalid_workflow_request("payload.workflow.proposalPolicy must be an object.")
    try:
        return WorkflowProposalPolicy.model_validate(dict(raw)).model_dump(
            by_alias=True,
            exclude_none=True,
        )
    except (WorkflowContractError, ValidationError, ValueError) as exc:
        raise _invalid_workflow_request(str(exc)) from exc

def _normalize_task_input_attachments(
    raw: Any, *, field_name: str
) -> list[dict[str, Any]]:
    if raw is None or raw == "":
        return []
    if not isinstance(raw, list):
        raise _invalid_workflow_request(f"{field_name} must be a JSON array.")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        try:
            attachment = WorkflowInputAttachmentRef.model_validate(item).model_dump(
                by_alias=True,
                exclude_none=True,
            )
        except (WorkflowContractError, ValidationError, ValueError) as exc:
            raise _invalid_workflow_request(f"{field_name}[{index}]: {exc}") from exc
        normalized.append(attachment)
    return normalized

def _normalize_task_steps(task_payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_edges = task_payload.get("edges")
    if isinstance(raw_edges, list):
        if raw_edges:
            raise _invalid_workflow_request(
                "payload.workflow.edges is no longer supported. Authored workflow "
                "steps are ordered by their steps[] position; use "
                "payload.workflow.dependsOn only for dependencies between workflow executions."
            )
        task_payload.pop("edges", None)
    elif raw_edges is not None:
        raise _invalid_workflow_request(
            "payload.workflow.edges is no longer supported. Authored workflow "
            "steps are ordered by their steps[] position; use "
            "payload.workflow.dependsOn only for dependencies between workflow executions."
        )

    raw_steps = task_payload.get("steps")
    if raw_steps is None:
        return []
    if not isinstance(raw_steps, list):
        raise _invalid_workflow_request("payload.workflow.steps must be a JSON array.")
    if len(raw_steps) > 50:
        raise _invalid_workflow_request("payload.workflow.steps can have a maximum of 50 items.")

    normalized_steps: list[dict[str, Any]] = []
    forbidden = {
        "targetRuntime",
        "target_runtime",
        "model",
        "effort",
        "providerProfile",
        "profileId",
        "repository",
        "repo",
        "git",
        "publish",
        "container",
    }

    for index, item in enumerate(raw_steps):
        if not isinstance(item, Mapping):
            raise _invalid_workflow_request(
                f"payload.workflow.steps[{index}] must be an object."
            )
        step_payload = dict(item)
        blocked = sorted(
            key for key in step_payload.keys() if str(key).strip() in forbidden
        )
        if blocked:
            formatted = ", ".join(blocked)
            raise _invalid_workflow_request(
                f"payload.workflow.steps[{index}] may not define workflow-scoped overrides: {formatted}"
            )
        for graph_key in ("dependsOn", "depends_on", "dependencies"):
            if graph_key not in step_payload:
                continue
            graph_value = step_payload.get(graph_key)
            if graph_value in (None, "", [], (), {}):
                step_payload.pop(graph_key, None)
                continue
            raise _invalid_workflow_request(
                f"payload.workflow.steps[{index}].{graph_key} is no longer supported. "
                "Authored workflow steps are ordered by their steps[] position; use "
                "payload.workflow.dependsOn only for dependencies between workflow executions."
            )

        normalized_step: dict[str, Any] = {}
        for key in ("id", "title", "instructions"):
            value = step_payload.get(key)
            if isinstance(value, str) and value.strip():
                normalized_step[key] = value.strip()
        normalized_step["id"] = _task_step_id_from_payload(step_payload, index)

        normalized_skills = _normalize_task_skill_selectors(
            step_payload.get("skills"),
            field_name=f"payload.workflow.steps[{index}].skills",
        )
        if normalized_skills is not None:
            normalized_step["skills"] = normalized_skills

        runtime_payload = step_payload.get("runtime")
        if runtime_payload is not None:
            if not isinstance(runtime_payload, Mapping):
                raise _invalid_workflow_request(
                    f"payload.workflow.steps[{index}].runtime must be an object."
                )
            validated_runtime = _validate_runtime_tier_intent_for_submit(
                runtime_payload,
                field_name=f"payload.workflow.steps[{index}].runtime",
            )
            normalized_runtime: dict[str, Any] = {}
            for source_key, target_key in (
                ("mode", "mode"),
                ("targetRuntime", "mode"),
                ("target_runtime", "mode"),
                ("model", "model"),
                ("effort", "effort"),
                ("modelTier", "modelTier"),
                ("tierFallback", "tierFallback"),
                ("profileId", "profileId"),
                ("providerProfile", "providerProfile"),
                ("providerProfileRef", "providerProfileRef"),
                ("executionProfileRef", "executionProfileRef"),
                ("execution_profile_ref", "executionProfileRef"),
            ):
                value = validated_runtime.get(source_key)
                if value is not None and not isinstance(value, (Mapping, list)):
                    normalized_value = str(value).strip()
                    if not normalized_value:
                        continue
                    if target_key == "mode":
                        normalized_value = normalize_runtime_id(normalized_value)
                        if normalized_value not in _SUPPORTED_TASK_RUNTIMES:
                            raise _invalid_workflow_request(
                                "Unsupported payload.workflow.steps"
                                f"[{index}].runtime.mode: {value!r}. "
                                "Must be one of: codex_cli, claude_code, "
                                "codex_cloud, jules."
                            )
                    if target_key == "modelTier":
                        normalized_runtime[target_key] = value
                    else:
                        normalized_runtime[target_key] = normalized_value
            for key in ("modelTier", "tierFallback"):
                if key in validated_runtime:
                    normalized_runtime[key] = validated_runtime[key]
            for key in ("profileSelector", "hardOverrideAudit"):
                value = validated_runtime.get(key)
                if isinstance(value, Mapping):
                    normalized_runtime[key] = dict(value)
            omnigent = validated_runtime.get("omnigent")
            if isinstance(omnigent, Mapping):
                normalized_runtime["omnigent"] = dict(omnigent)
            if normalized_runtime:
                normalized_step["runtime"] = normalized_runtime

        normalized_input_attachments = _normalize_task_input_attachments(
            step_payload.get("inputAttachments")
            or step_payload.get("input_attachments"),
            field_name=f"payload.workflow.steps[{index}].inputAttachments",
        )
        if normalized_input_attachments:
            normalized_step["inputAttachments"] = normalized_input_attachments

        raw_type = str(step_payload.get("type") or "").strip().lower()
        if raw_type:
            if raw_type not in {"tool", "skill"}:
                raise _invalid_workflow_request(
                    f"payload.workflow.steps[{index}].type / workflow.steps[].type "
                    "must be one of: tool, skill."
                )
            normalized_step["type"] = raw_type

        raw_tool = (
            step_payload.get("tool")
            if isinstance(step_payload.get("tool"), Mapping)
            else None
        )
        raw_skill = (
            step_payload.get("skill")
            if isinstance(step_payload.get("skill"), Mapping)
            else None
        )
        step_type = raw_type or "skill"
        if step_type == "tool":
            if raw_skill is not None:
                raise _invalid_workflow_request(
                    f"payload.workflow.steps[{index}].skill must be omitted for Tool steps."
                )
            if raw_tool is None:
                raise _invalid_workflow_request(
                    f"payload.workflow.steps[{index}].tool is required for Tool steps."
                )
            tool_id = str(raw_tool.get("id") or raw_tool.get("name") or "").strip()
            if not tool_id:
                raise _invalid_workflow_request(
                    f"payload.workflow.steps[{index}].tool.id or "
                    f"payload.workflow.steps[{index}].tool.name is required."
                )
            normalized_tool: dict[str, Any] = {"id": tool_id}
            raw_inputs = raw_tool.get("inputs")
            if not isinstance(raw_inputs, dict):
                raw_inputs = raw_tool.get("args")
            if isinstance(raw_inputs, dict):
                normalized_tool["inputs"] = dict(raw_inputs)
            raw_caps = raw_tool.get("requiredCapabilities")
            if raw_caps is not None:
                normalized_caps = _coerce_string_list(
                    raw_caps,
                    field_name=(
                        f"payload.workflow.steps[{index}].tool.requiredCapabilities"
                    ),
                )
                if normalized_caps:
                    normalized_tool["requiredCapabilities"] = normalized_caps
            normalized_step["tool"] = normalized_tool
        else:
            selected_skill = raw_skill
            if selected_skill is None and raw_tool is not None:
                tool_type = str(
                    raw_tool.get("type") or raw_tool.get("kind") or ""
                ).strip().lower()
                if tool_type not in {"", "skill"}:
                    raise _invalid_workflow_request(
                        f"payload.workflow.steps[{index}].tool must be omitted for Skill steps."
                    )
                selected_skill = raw_tool
            if raw_type == "skill" and selected_skill is None:
                raise _invalid_workflow_request(
                    f"payload.workflow.steps[{index}].skill is required for Skill steps."
                )
            if selected_skill is not None:
                skill_id = str(
                    selected_skill.get("id") or selected_skill.get("name") or ""
                ).strip()
                if skill_id:
                    normalized_skill: dict[str, Any] = {"id": skill_id}
                    raw_inputs = selected_skill.get("inputs")
                    if not isinstance(raw_inputs, dict):
                        raw_inputs = selected_skill.get("args")
                    if isinstance(raw_inputs, dict):
                        normalized_skill["inputs"] = dict(raw_inputs)
                    _copy_skill_contract_metadata(
                        source=selected_skill,
                        target=normalized_skill,
                    )
                    saved_digest = str(
                        selected_skill.get("inputContractDigest") or ""
                    ).strip()
                    current_digest = str(
                        selected_skill.get("currentInputContractDigest") or ""
                    ).strip()
                    if current_digest and current_digest != saved_digest:
                        normalized_skill["currentInputContractDigest"] = current_digest
                        normalized_step.setdefault("diagnostics", []).append(
                            {
                                "code": "skill_input_contract_digest_mismatch",
                                "severity": "warning",
                                "path": (
                                    f"payload.workflow.steps[{index}]."
                                    "skill.inputContractDigest"
                                ),
                                "message": (
                                    "Skill input contract changed since this "
                                    "draft was saved; submitted values were "
                                    "preserved and revalidated against the "
                                    "current contract."
                                ),
                                "recoverable": True,
                            }
                        )
                        try:
                            get_metrics_emitter().increment(
                                "capability_input_contract.event",
                                tags={
                                    "event": "digest_mismatch",
                                    "kind": "skill",
                                    "skill": _bounded_metric_tag(skill_id),
                                },
                            )
                        except Exception:
                            logger.debug(
                                "Failed to emit skill input contract digest mismatch metric",
                                exc_info=True,
                            )
                    raw_caps = selected_skill.get("requiredCapabilities")
                    if raw_caps is not None:
                        normalized_caps = _coerce_string_list(
                            raw_caps,
                            field_name=(
                                f"payload.workflow.steps[{index}].skill.requiredCapabilities"
                            ),
                        )
                        if normalized_caps:
                            normalized_skill["requiredCapabilities"] = normalized_caps
                    normalized_step["skill"] = normalized_skill

        for key, value in step_payload.items():
            normalized_key = str(key).strip()
            if normalized_key in {
                "id",
                "title",
                "instructions",
                "type",
                "inputAttachments",
                "input_attachments",
                "runtime",
                "diagnostics",
                "skill",
                "skills",
                "tool",
                "preset",
            }:
                continue
            normalized_step[normalized_key] = value

        normalized_steps.append(normalized_step)

    task_payload["steps"] = normalized_steps
    return normalized_steps

def _copy_skill_contract_metadata(
    *,
    source: Mapping[str, Any],
    target: dict[str, Any],
) -> None:
    for metadata_key in ("inputSchema", "uiSchema", "defaults"):
        metadata_value = source.get(metadata_key)
        if isinstance(metadata_value, Mapping):
            target[metadata_key] = dict(metadata_value)
    for evidence_key in (
        "contentRef",
        "contentDigest",
        "inputContractDigest",
    ):
        evidence_value = source.get(evidence_key)
        if isinstance(evidence_value, str) and evidence_value.strip():
            target[evidence_key] = evidence_value.strip()


def _copy_trusted_skill_publish_metadata(
    *,
    metadata: Mapping[str, Any],
    target: dict[str, Any],
) -> None:
    publish = metadata.get("publish")
    if isinstance(publish, Mapping):
        target["publish"] = dict(publish)
    side_effect = metadata.get("sideEffect")
    if not isinstance(side_effect, Mapping):
        side_effect = metadata.get("side_effect")
    if isinstance(side_effect, Mapping):
        target["sideEffect"] = dict(side_effect)


def _skill_names_for_metadata_enrichment(
    *,
    normalized_tool: Mapping[str, Any] | None,
    steps: list[dict[str, Any]],
) -> set[str]:
    names: set[str] = set()
    if isinstance(normalized_tool, Mapping):
        name = str(normalized_tool.get("name") or "").strip()
        if name:
            names.add(name)
    for step in steps:
        skill = step.get("skill") if isinstance(step.get("skill"), Mapping) else None
        if not skill:
            continue
        name = str(skill.get("id") or skill.get("name") or "").strip()
        if name:
            names.add(name)
    return names


async def _load_deployment_skill_metadata(
    *,
    session: AsyncSession | None,
    skill_names: set[str],
) -> dict[str, dict[str, Any]]:
    if session is None or not skill_names:
        return {}
    stmt = select(AgentSkillDefinition).where(
        AgentSkillDefinition.slug.in_(sorted(skill_names))
    )
    try:
        result = await session.execute(stmt)
    except Exception as ex:
        logger.warning(
            "Deployment skill metadata unavailable; continuing without catalog publish metadata.",
            extra={
                "skill_count": len(skill_names),
                "error_type": type(ex).__name__,
            },
        )
        return {}
    definitions = list(result.scalars().all())
    artifact_refs = {
        str(definition.artifact_ref)
        for definition in definitions
        if str(definition.artifact_ref or "").strip()
    }
    if not artifact_refs:
        return {}
    metadata_by_skill: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        artifact_ref = str(definition.artifact_ref or "").strip()
        if not artifact_ref:
            continue
        try:
            artifact = await session.get(TemporalArtifact, artifact_ref)
        except Exception as ex:
            logger.warning(
                "Deployment skill metadata artifact unavailable; continuing without catalog publish metadata.",
                extra={
                    "skill": str(definition.slug),
                    "error_type": type(ex).__name__,
                },
            )
            continue
        metadata = getattr(artifact, "metadata_json", None)
        if isinstance(metadata, Mapping):
            metadata_by_skill[str(definition.slug)] = dict(metadata)
    return metadata_by_skill


async def _enrich_deployment_skill_metadata(
    *,
    session: AsyncSession | None,
    normalized_tool: dict[str, Any] | None,
    steps: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    metadata_by_skill = await _load_deployment_skill_metadata(
        session=session,
        skill_names=_skill_names_for_metadata_enrichment(
            normalized_tool=normalized_tool,
            steps=steps,
        ),
    )
    if normalized_tool is not None:
        metadata = metadata_by_skill.get(str(normalized_tool.get("name") or ""))
        if isinstance(metadata, Mapping):
            _copy_trusted_skill_publish_metadata(
                metadata=metadata,
                target=normalized_tool,
            )
    for step in steps:
        skill = step.get("skill") if isinstance(step.get("skill"), dict) else None
        if skill is None:
            continue
        skill_id = str(skill.get("id") or skill.get("name") or "").strip()
        metadata = metadata_by_skill.get(skill_id)
        if isinstance(metadata, Mapping):
            _copy_trusted_skill_publish_metadata(metadata=metadata, target=skill)
    return metadata_by_skill


async def _resolve_step_runtime_selections(
    *,
    steps: list[dict[str, Any]],
    task_runtime: Mapping[str, Any] | None,
    task_target_runtime: str | None,
    task_profile_id: str | None,
    session: Any,
) -> None:
    if not steps:
        return
    task_runtime = task_runtime or {}

    for index, step in enumerate(steps):
        runtime_payload = (
            step.get("runtime") if isinstance(step.get("runtime"), Mapping) else {}
        )
        if not runtime_payload:
            continue

        raw_step_runtime = runtime_payload.get("mode") or task_target_runtime
        canonical_step_runtime: str | None = None
        if raw_step_runtime:
            normalized_rt = normalize_runtime_id(raw_step_runtime)
            if normalized_rt not in _SUPPORTED_TASK_RUNTIMES:
                raise _invalid_workflow_request(
                    f"Unsupported payload.workflow.steps[{index}].runtime.mode: "
                    f"{raw_step_runtime!r}. Must be one of: codex_cli, "
                    "claude_code, codex_cloud, jules."
                )
            canonical_step_runtime = normalized_rt

        raw_step_profile_id = str(
            runtime_payload.get("profileId")
            or runtime_payload.get("providerProfile")
            or runtime_payload.get("providerProfileRef")
            or runtime_payload.get("executionProfileRef")
            or ""
        ).strip() or None
        raw_requested_model: str | None = runtime_payload.get("model") or None
        if raw_requested_model is not None:
            raw_requested_model = str(raw_requested_model)

        effective_profile_id = raw_step_profile_id or task_profile_id
        provider_profile = None
        if effective_profile_id and session is not None:
            from api_service.db.models import ManagedAgentProviderProfile

            provider_profile = await session.get(
                ManagedAgentProviderProfile, effective_profile_id
            )
            if provider_profile is None and raw_step_profile_id:
                raise _invalid_workflow_request(
                    f"Provider profile not found for payload.workflow.steps[{index}]: "
                    f"{raw_step_profile_id!r}."
                )
            if provider_profile is None and task_profile_id and not raw_step_profile_id:
                raise _invalid_workflow_request(
                    f"Provider profile not found for inherited task profile on "
                    f"payload.workflow.steps[{index}]: {task_profile_id!r}."
                )

        (
            resolved_model,
            model_source,
            resolved_effort,
            model_tier_resolution,
        ) = _resolve_runtime_model_effort(
            runtime_id=canonical_step_runtime,
            profile=provider_profile,
            runtime_payload=runtime_payload,
            requested_model=raw_requested_model,
        )

        resolved_runtime = dict(runtime_payload)
        if canonical_step_runtime:
            resolved_runtime["mode"] = canonical_step_runtime
        if resolved_model:
            resolved_runtime["model"] = resolved_model
        if resolved_effort:
            resolved_runtime["effort"] = resolved_effort
        if raw_requested_model is not None:
            resolved_runtime["requestedModel"] = raw_requested_model
        resolved_runtime["modelSource"] = model_source
        if model_tier_resolution is not None:
            resolved_runtime["modelTierResolution"] = model_tier_resolution
        if raw_step_profile_id:
            resolved_runtime["profileId"] = raw_step_profile_id
            resolved_runtime["providerProfile"] = raw_step_profile_id
            resolved_runtime["providerProfileRef"] = raw_step_profile_id
        elif task_profile_id and not raw_step_profile_id:
            resolved_runtime.setdefault("inheritedProfileId", task_profile_id)
        if "effort" not in resolved_runtime and isinstance(
            task_runtime.get("effort"), str
        ):
            resolved_runtime["effort"] = task_runtime["effort"]
        step["runtime"] = resolved_runtime


def _preset_seed_dir() -> Path:
    import api_service

    return Path(api_service.__file__).resolve().parent / "data" / "presets"


def _apply_goal_schedule_metadata(
    *,
    task_payload: dict[str, Any],
    schedule: GoalPresetSchedule,
    expanded: Mapping[str, Any],
) -> None:
    applied_template = (
        expanded.get("appliedTemplate")
        if isinstance(expanded.get("appliedTemplate"), Mapping)
        else {}
    )
    applied_template_payload = {
        "slug": str(applied_template.get("slug") or schedule.slug),
        **(
            {
                "presetDigest": str(applied_template.get("presetDigest")).strip(),
            }
            if str(applied_template.get("presetDigest") or "").strip()
            else {}
        ),
        "inputs": (
            dict(applied_template.get("inputs"))
            if isinstance(applied_template.get("inputs"), Mapping)
            else dict(schedule.inputs)
        ),
        "stepIds": list(applied_template.get("stepIds") or []),
        "appliedAt": str(applied_template.get("appliedAt") or ""),
        "capabilities": list(expanded.get("capabilities") or []),
    }
    composition = applied_template.get("composition") or expanded.get("composition")
    if isinstance(composition, Mapping):
        applied_template_payload["composition"] = dict(composition)

    authored_presets = applied_template.get("authoredPresets") or expanded.get(
        "authoredPresets"
    )
    if isinstance(authored_presets, list):
        task_payload["authoredPresets"] = [
            dict(item) if isinstance(item, Mapping) else item
            for item in authored_presets
        ]

    task_payload["appliedStepTemplates"] = [applied_template_payload]
    task_payload["taskTemplate"] = {
        "slug": str(applied_template.get("slug") or schedule.slug),
        "scope": "global",
    }
    task_payload["presetSchedule"] = {
        "source": "goal",
        "reason": schedule.reason,
        "presetSlug": schedule.slug,
        "jiraIssueKey": schedule.issue_key,
    }
    preset_digest = str(applied_template.get("presetDigest") or "").strip()
    if preset_digest:
        task_payload["taskTemplate"]["presetDigest"] = preset_digest


async def _expand_goal_preset_for_workflow_submission(
    *,
    task_payload: dict[str, Any],
    request_payload: Mapping[str, Any],
    session: Any,
    user: Any,
) -> None:
    if has_unexpanded_task_template({"workflow": task_payload}):
        from api_service.services.presets.catalog import (
            PresetNotFoundError,
            PresetValidationError,
        )

        template_payload = dict(task_payload.get("taskTemplate") or {})
        template_scope, template_scope_ref = resolve_template_scope_for_user(
            user=user,
            scope=str(template_payload.get("scope") or "global"),
            scope_ref=(
                template_payload.get("scopeRef")
                or template_payload.get("scope_ref")
            ),
            write=False,
        )
        template_payload["scope"] = template_scope
        if template_scope_ref is None:
            template_payload.pop("scopeRef", None)
            template_payload.pop("scope_ref", None)
        else:
            template_payload["scopeRef"] = template_scope_ref
            template_payload.pop("scope_ref", None)
        task_payload["taskTemplate"] = template_payload
        try:
            expanded_parameters = await expand_preset_for_child_run(
                session=session,
                initial_parameters={
                    "workflow": task_payload,
                    "repository": request_payload.get("repository"),
                    "targetRuntime": request_payload.get("targetRuntime"),
                },
                allow_goal_schedule=False,
                user_id=getattr(user, "id", None),
            )
        except PresetNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "template_not_found",
                    "message": str(exc),
                },
            ) from exc
        except (PresetValidationError, RuntimeError) as exc:
            raise _invalid_workflow_request(str(exc)) from exc
        expanded_task = expanded_parameters.get("workflow")
        if not isinstance(expanded_task, Mapping):
            raise _invalid_workflow_request(
                "Explicit preset expansion did not produce a workflow payload."
            )
        task_payload.clear()
        task_payload.update(expanded_task)
        return

    if workflow_is_already_authored(task_payload):
        return

    schedule = schedule_preset_from_goal(
        goal_from_payloads(task_payload=task_payload, parameter_payload=request_payload)
    )
    if schedule is None:
        return

    from api_service.services.presets.catalog import (
        ExpandOptions,
        PresetCatalogService,
        PresetNotFoundError,
    )

    catalog = PresetCatalogService(session)
    existing_inputs = (
        dict(task_payload.get("inputs")) if isinstance(task_payload.get("inputs"), Mapping) else {}
    )
    template_inputs = {**schedule.inputs, **existing_inputs}
    context: dict[str, Any] = {}
    repository = request_payload.get("repository") or task_payload.get("repository")
    if isinstance(repository, str) and repository.strip():
        context["repository"] = repository.strip()
        context["repo"] = repository.strip()
    runtime_payload = (
        task_payload.get("runtime") if isinstance(task_payload.get("runtime"), Mapping) else {}
    )
    target_runtime = (
        request_payload.get("targetRuntime")
        or runtime_payload.get("mode")
        or normalize_runtime_id(settings.workflow.default_runtime)
    )
    if isinstance(target_runtime, str) and target_runtime.strip():
        context["targetRuntime"] = target_runtime.strip()

    expand_kwargs = {
        "slug": schedule.slug,
        "scope": "global",
        "scope_ref": None,
        "inputs": template_inputs,
        "context": context,
        "options": ExpandOptions(should_enforce_step_limit=True),
        "user_id": getattr(user, "id", None),
    }
    try:
        expanded = await catalog.expand_template(**expand_kwargs)
    except PresetNotFoundError:
        await catalog.sync_seed_templates(seed_dir=_preset_seed_dir())
        expanded = await catalog.expand_template(**expand_kwargs)

    expanded_steps = expanded.get("steps") if isinstance(expanded, Mapping) else None
    if not isinstance(expanded_steps, list) or not expanded_steps:
        raise _invalid_workflow_request(
            f"Goal preset '{schedule.slug}' expansion produced no executable steps."
        )

    task_payload["goal"] = schedule.goal
    task_payload.setdefault("instructions", schedule.goal)
    task_payload["inputs"] = template_inputs
    task_payload["steps"] = list(expanded_steps)
    expanded_publish = (
        expanded.get("publish") if isinstance(expanded, Mapping) else None
    )
    if isinstance(expanded_publish, Mapping) and not isinstance(
        task_payload.get("publish"), Mapping
    ):
        task_payload["publish"] = dict(expanded_publish)
    expanded_checkpoint_branching = (
        expanded.get("checkpointBranching") if isinstance(expanded, Mapping) else None
    )
    if isinstance(expanded_checkpoint_branching, Mapping):
        task_payload["checkpointBranching"] = dict(expanded_checkpoint_branching)
    _apply_goal_schedule_metadata(
        task_payload=task_payload,
        schedule=schedule,
        expanded=expanded,
    )

def _normalize_publish_payload(raw_publish: Any) -> dict[str, Any]:
    publish_payload = _coerce_mapping(raw_publish)
    if not publish_payload:
        return {}

    normalized: dict[str, Any] = {}
    for key in (
        "mode",
        "prBaseBranch",
        "baseBranch",
        "commitMessage",
        "prTitle",
        "prBody",
        "verificationSkipReason",
        "verification",
        "mergeAutomation",
    ):
        if key not in publish_payload:
            continue
        value = publish_payload.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        normalized[key] = value

    if "baseBranch" in normalized and "prBaseBranch" not in normalized:
        normalized["prBaseBranch"] = normalized["baseBranch"]
    return normalized

_REPORT_OUTPUT_STRING_KEYS = frozenset(
    {
        "reportType",
        "report_type",
        "title",
        "description",
        "primaryPath",
        "primary_path",
    }
)
_REPORT_OUTPUT_MAX_STRING_CHARS = 512

def _normalize_report_output_payload(
    *raw_payloads: Any,
) -> dict[str, Any]:
    for raw_payload in raw_payloads:
        report_payload = _coerce_mapping(raw_payload)
        if not report_payload:
            continue
        try:
            enabled = _coerce_bool(report_payload.get("enabled"), default=False)
            required = _coerce_bool(report_payload.get("required"), default=True)
        except ValueError as exc:
            raise _invalid_workflow_request(
                f"reportOutput boolean value is invalid: {exc}"
            ) from exc
        if not enabled:
            return {"enabled": False}

        raw_report_type = (
            report_payload.get("reportType")
            or report_payload.get("report_type")
            or "agent_run_report"
        )
        if not isinstance(raw_report_type, str):
            raise _invalid_workflow_request("reportOutput.reportType must be a string.")
        report_type = raw_report_type.strip() or "agent_run_report"
        if len(report_type) > _REPORT_OUTPUT_MAX_STRING_CHARS:
            raise _invalid_workflow_request(
                f"reportOutput.reportType must be "
                f"{_REPORT_OUTPUT_MAX_STRING_CHARS} characters or fewer."
            )
        normalized: dict[str, Any] = {
            "enabled": True,
            "required": required,
            "reportType": report_type,
        }
        for key in _REPORT_OUTPUT_STRING_KEYS:
            if key in {"reportType", "report_type"}:
                continue
            value = report_payload.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                raise _invalid_workflow_request(f"reportOutput.{key} must be a string.")
            text = value.strip()
            if not text:
                continue
            canonical_key = {
                "primary_path": "primaryPath",
            }.get(key, key)
            if canonical_key == "primaryPath":
                text = normalize_report_output_primary_path(text)
            if len(text) > _REPORT_OUTPUT_MAX_STRING_CHARS:
                raise _invalid_workflow_request(
                    f"reportOutput.{canonical_key} must be "
                    f"{_REPORT_OUTPUT_MAX_STRING_CHARS} characters or fewer."
                )
            normalized[canonical_key] = text
        return normalized
    return {}

def _report_output_instruction(report_output: Mapping[str, Any]) -> str:
    if not _coerce_bool(report_output.get("enabled"), default=False):
        return ""
    primary_path = str(report_output.get("primaryPath") or "").strip()
    path_sentence = (
        f" Also write the same report to `{primary_path}` when the runtime "
        "workspace makes that path available."
        if primary_path
        else ""
    )
    return (
        "\n\nMoonMind report output contract:\n"
        "- Finish with a concise final report suitable for a `report.primary` artifact.\n"
        "- If the task asks a question or requests a topic report, answer that "
        "request directly in the final report body.\n"
        "- Include outcome, commands or tools run, key evidence, failures or "
        "skipped work, and recommended next action.\n"
        "- Do not include secrets, raw credentials, cookies, tokens, or full "
        "environment dumps.\n"
        f"{path_sentence}"
    )

def _workflow_publish_skill_id(
    task_payload: Mapping[str, Any],
    normalized_tool: Mapping[str, Any] | None,
) -> object:
    if isinstance(normalized_tool, Mapping):
        tool_name = normalized_tool.get("name")
        if tool_name:
            return tool_name
    skill_payload = _coerce_mapping(task_payload.get("skill"))
    return skill_payload.get("id") or skill_payload.get("name")

def _first_present_publish_mode(
    *candidates: tuple[Mapping[str, Any], str],
) -> object | None:
    for source, key in candidates:
        if key in source and source[key] is not None:
            return source[key]
    return None

def _resolve_workflow_publish_payload(
    *,
    payload: Mapping[str, Any],
    task_payload: Mapping[str, Any],
    normalized_tool: Mapping[str, Any] | None,
    skill_publish_metadata: Mapping[str, Any] | None = None,
    skill_side_effect_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    task_publish = _normalize_publish_payload(task_payload.get("publish"))
    top_publish = _normalize_publish_payload(payload.get("publish"))

    task_requested_mode = _first_present_publish_mode(
        (task_publish, "mode"),
        (task_payload, "publishMode"),
        (task_payload, "publish_mode"),
    )
    requested_mode = _first_present_publish_mode(
        ({"mode": task_requested_mode}, "mode"),
        (top_publish, "mode"),
        (payload, "publishMode"),
        (payload, "publish_mode"),
    )
    skill_id = _workflow_publish_skill_id(task_payload, normalized_tool)
    allow_repository_publish = allows_repository_publish_for_skill_context(task_payload)
    if (
        task_requested_mode is None
        and is_non_repository_side_effect_skill(skill_id)
        and not allow_repository_publish
    ):
        requested_mode = None
    if (
        requested_mode is None
        or (isinstance(requested_mode, str) and not requested_mode.strip())
    ) and is_self_managed_publish_skill(skill_id):
        requested_mode = "none"
    try:
        publish_mode = resolve_publish_mode_for_skill(
            skill_id,
            requested_mode,
            allow_repository_publish=allow_repository_publish,
            publish_metadata=skill_publish_metadata,
            side_effect_metadata=skill_side_effect_metadata,
        )
    except WorkflowContractError as exc:
        raise _invalid_workflow_request(str(exc)) from exc

    resolved = dict(task_publish or top_publish)
    resolved["mode"] = publish_mode
    return resolved

_GENERATED_JIRA_PR_HEAD_BRANCH_RE = re.compile(
    r"^(?:moonmind/jira-(?:orchestrate|implement)-[a-z][a-z0-9]*-\d+(?:[-_].*)?|"
    r"(?:run-)?jira-(?:orchestrate|implement)(?:-[a-z0-9]+)*-mm-\d+"
    r"(?:-[a-z0-9]+)*(?:-[0-9a-f]{8})?)$",
    re.IGNORECASE,
)


def _validate_pr_base_branch_submission(
    *,
    publish_payload: Mapping[str, Any] | None,
    task_payload: Mapping[str, Any] | None,
    git_payload: Mapping[str, Any] | None,
) -> None:
    publish = publish_payload or {}
    task = task_payload or {}
    git = git_payload or {}
    if str(publish.get("mode") or "").strip().lower() != "pr":
        return

    for field_name, value in (
        ("payload.workflow.publish.prBaseBranch", publish.get("prBaseBranch")),
        ("payload.workflow.publish.baseBranch", publish.get("baseBranch")),
        ("payload.workflow.git.branch", git.get("branch")),
        ("payload.workflow.git.startingBranch", git.get("startingBranch")),
        ("payload.workflow.branch", task.get("branch")),
        ("payload.workflow.startingBranch", task.get("startingBranch")),
    ):
        branch = str(value or "").strip()
        if not branch:
            continue
        if _GENERATED_JIRA_PR_HEAD_BRANCH_RE.match(branch):
            raise _invalid_workflow_request(
                f"{field_name} is the PR base branch for publishMode 'pr', "
                "but it looks like a generated Jira work/head branch. Use an existing "
                "base branch such as 'main'; MoonMind creates the PR head branch separately."
            )


def _normalize_merge_automation_payload(raw_merge_automation: Any) -> dict[str, Any]:
    return _coerce_mapping(raw_merge_automation)

def _merge_automation_enabled_from_parameters(
    parameters: Mapping[str, Any],
) -> bool:
    raw_publish_payload = _coerce_mapping(parameters.get("publish"))
    publish_payload = _normalize_publish_payload(raw_publish_payload)
    task_payload = _workflow_payload_from_parameters(parameters)
    task_publish = _coerce_mapping(task_payload.get("publish"))
    candidates = (
        publish_payload.get("mergeAutomation")
        or raw_publish_payload.get("merge_automation"),
        task_payload.get("mergeAutomation") or task_payload.get("merge_automation"),
        task_publish.get("mergeAutomation") or task_publish.get("merge_automation"),
        parameters.get("mergeAutomation") or parameters.get("merge_automation"),
    )

    return any(
        isinstance(candidate, Mapping)
        and _coerce_bool(candidate.get("enabled"), default=False)
        for candidate in candidates
    )

def _normalize_story_output_payload(raw_story_output: Any) -> dict[str, Any]:
    story_output = _coerce_mapping(raw_story_output)
    if not story_output:
        return {}

    mode = str(
        story_output.get("mode") or story_output.get("target") or ""
    ).strip().lower()
    if not mode:
        mode = "jira" if _coerce_mapping(story_output.get("jira")) else "docs_tmp"
    if mode not in {"jira", "docs_tmp", "docs"}:
        raise _invalid_workflow_request(
            "payload.workflow.storyOutput.mode must be one of: jira, docs_tmp, docs."
        )

    normalized: dict[str, Any] = {"mode": "docs_tmp" if mode == "docs" else mode}
    for key in (
        "storyBreakdownPath",
        "storyBreakdownMarkdownPath",
        "fallback",
        "onFailure",
    ):
        value = story_output.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = value.strip()

    jira_payload = _coerce_mapping(story_output.get("jira"))
    if jira_payload:
        normalized_jira: dict[str, Any] = {}
        for key in (
            "projectKey",
            "issueTypeId",
            "issueTypeName",
            "boardId",
            "dependencyMode",
            "parentIssueKey",
            "sourceIssueKey",
        ):
            value = jira_payload.get(key)
            if isinstance(value, str) and value.strip():
                normalized_jira[key] = value.strip()
        for key in ("labels", "fields"):
            if key in jira_payload:
                normalized_jira[key] = jira_payload[key]
        normalized["jira"] = normalized_jira

    return normalized

def _normalize_task_tool(task_payload: dict[str, Any]) -> dict[str, Any] | None:
    tool_payload = (
        task_payload.get("tool") if isinstance(task_payload.get("tool"), dict) else {}
    )
    skill_payload = (
        task_payload.get("skill") if isinstance(task_payload.get("skill"), dict) else {}
    )
    selected_payload = tool_payload or skill_payload
    if not selected_payload:
        return None

    tool_type = str(
        selected_payload.get("type") or selected_payload.get("kind") or "skill"
    ).strip()
    if tool_type and tool_type.lower() != "skill":
        raise _invalid_workflow_request(
            "payload.workflow.tool.type must be 'skill' for Temporal-backed submit."
        )

    name = str(selected_payload.get("name") or selected_payload.get("id") or "").strip()
    if not name:
        return None
    normalized: dict[str, Any] = {
        "type": "skill",
        "name": name,
    }

    inline_inputs = selected_payload.get("inputs")
    if not isinstance(inline_inputs, dict):
        inline_inputs = selected_payload.get("args")
    if isinstance(inline_inputs, dict) and inline_inputs:
        normalized["inputs"] = dict(inline_inputs)
    _copy_skill_contract_metadata(source=selected_payload, target=normalized)
    return normalized

_PENTEST_TOOL_NAME = "security.pentest.run"
_PENTEST_ALLOWED_ROLES = frozenset({"admin", "security_operator"})
_PENTEST_PRIVILEGED_INPUT_FIELDS = frozenset(
    {
        "image",
        "runner_image",
        "provider_secret",
        "provider_secret_ref",
        "provider_secret_id",
        "provider_runtime_state",
        "secret",
        "secrets",
        "env",
        "environment",
        "environment_variables",
        "network",
        "network_mode",
        "mount",
        "mounts",
        "host_mount",
        "host_mounts",
        "capability",
        "capabilities",
        "docker_args",
        "docker",
        "command",
        "raw_command",
        "args",
    }
)

def _tool_payload_name(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    return str(payload.get("name") or payload.get("id") or "").strip()

def _is_pentest_tool_payload(payload: Mapping[str, Any] | None) -> bool:
    return _tool_payload_name(payload).lower() == _PENTEST_TOOL_NAME

def _pentest_input_payloads_for_submission(
    *,
    task_payload: Mapping[str, Any],
    normalized_tool: Mapping[str, Any] | None,
    normalized_steps: list[dict[str, Any]],
) -> list[Mapping[str, Any]]:
    payloads: list[Mapping[str, Any]] = []
    if _is_pentest_tool_payload(normalized_tool):
        if isinstance(normalized_tool.get("inputs"), Mapping):
            payloads.append(normalized_tool["inputs"])
        if isinstance(task_payload.get("inputs"), Mapping):
            payloads.append(task_payload["inputs"])

    for step in normalized_steps:
        step_tool = step.get("tool") if isinstance(step.get("tool"), Mapping) else None
        step_skill = (
            step.get("skill") if isinstance(step.get("skill"), Mapping) else None
        )
        selected = step_tool if _is_pentest_tool_payload(step_tool) else step_skill
        if not _is_pentest_tool_payload(selected):
            continue
        for key in ("inputs", "args"):
            value = selected.get(key) if isinstance(selected, Mapping) else None
            if isinstance(value, Mapping):
                payloads.append(value)
    return payloads

def _submission_contains_pentest_tool(
    *,
    task_payload: Mapping[str, Any],
    normalized_tool: Mapping[str, Any] | None,
    normalized_steps: list[dict[str, Any]],
) -> bool:
    if _is_pentest_tool_payload(normalized_tool):
        return True
    for step in normalized_steps:
        step_tool = step.get("tool") if isinstance(step.get("tool"), Mapping) else None
        step_skill = (
            step.get("skill") if isinstance(step.get("skill"), Mapping) else None
        )
        if _is_pentest_tool_payload(step_tool) or _is_pentest_tool_payload(step_skill):
            return True
    return False

def _normalize_user_role_name(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("name") or value.get("role") or value.get("id")
    elif value is not None and not isinstance(value, str):
        value = (
            getattr(value, "name", None)
            or getattr(value, "role", None)
            or getattr(value, "id", None)
            or value
        )
    return str(value or "").strip().lower()

def _role_values_from_claims(claims: Any) -> list[Any]:
    if not isinstance(claims, Mapping):
        return []

    values: list[Any] = []
    for key in ("roles", "role_names", "groups", "role"):
        value = claims.get(key)
        if isinstance(value, (list, tuple, set, frozenset)):
            values.extend(value)
        elif value is not None:
            values.append(value)

    realm_access = claims.get("realm_access")
    if isinstance(realm_access, Mapping):
        realm_roles = realm_access.get("roles")
        if isinstance(realm_roles, (list, tuple, set, frozenset)):
            values.extend(realm_roles)

    resource_access = claims.get("resource_access")
    if isinstance(resource_access, Mapping):
        for resource_claims in resource_access.values():
            if not isinstance(resource_claims, Mapping):
                continue
            resource_roles = resource_claims.get("roles")
            if isinstance(resource_roles, (list, tuple, set, frozenset)):
                values.extend(resource_roles)

    return values

def _effective_user_roles(user: Any, request: Any | None = None) -> set[str]:
    roles: set[str] = set()
    for attr_name in ("roles", "role_names", "groups"):
        raw = getattr(user, attr_name, None)
        if isinstance(raw, str):
            roles.update(
                role for role in (_normalize_user_role_name(raw),) if role
            )
        elif isinstance(raw, (list, tuple, set, frozenset)):
            roles.update(
                role
                for item in raw
                for role in (_normalize_user_role_name(item),)
                if role
            )
    role = _normalize_user_role_name(getattr(user, "role", None))
    if role:
        roles.add(role)
    for claim_attr in ("claims", "oidc_claims", "token_claims"):
        roles.update(
            role
            for value in _role_values_from_claims(getattr(user, claim_attr, None))
            for role in (_normalize_user_role_name(value),)
            if role
        )
    if request is not None:
        request_state = getattr(request, "state", None)
        for claim_attr in ("claims", "oidc_claims", "token_claims"):
            roles.update(
                role
                for value in _role_values_from_claims(
                    getattr(request_state, claim_attr, None)
                )
                for role in (_normalize_user_role_name(value),)
                if role
            )
        scope = getattr(request, "scope", None)
        if isinstance(scope, Mapping):
            roles.update(
                role
                for value in _role_values_from_claims(scope.get("claims"))
                for role in (_normalize_user_role_name(value),)
                if role
            )
    if bool(getattr(user, "is_superuser", False)):
        roles.add("admin")
    return roles

def _validate_pentest_submission_boundary(
    *,
    task_payload: Mapping[str, Any],
    normalized_tool: Mapping[str, Any] | None,
    normalized_steps: list[dict[str, Any]],
    user: Any,
    request: Any | None = None,
) -> None:
    if not _submission_contains_pentest_tool(
        task_payload=task_payload,
        normalized_tool=normalized_tool,
        normalized_steps=normalized_steps,
    ):
        return

    if not settings.pentest.enabled:
        raise _invalid_workflow_request(
            "security.pentest.run submission is disabled by MOONMIND_PENTEST_ENABLED."
        )

    user_roles = _effective_user_roles(user, request=request)
    if user_roles.isdisjoint(_PENTEST_ALLOWED_ROLES):
        raise _invalid_workflow_request(
            "security.pentest.run submission requires an admin or security_operator role."
        )

    for inputs in _pentest_input_payloads_for_submission(
        task_payload=task_payload,
        normalized_tool=normalized_tool,
        normalized_steps=normalized_steps,
    ):
        blocked = sorted(
            str(key)
            for key in inputs.keys()
            if str(key).strip().lower() in _PENTEST_PRIVILEGED_INPUT_FIELDS
        )
        if blocked:
            raise _invalid_workflow_request(
                "security.pentest.run submission contains privileged input fields "
                f"that are not user-editable: {', '.join(blocked)}."
            )

_PR_RESOLVER_SELECTOR_ERROR = (
    "pr-resolver workflow requires a structured PR selector: "
    "payload.workflow.inputs.pr, payload.workflow.inputs.branch, "
    "payload.workflow.tool.inputs.pr/branch, or "
    "payload.workflow.git.startingBranch, or a non-default "
    "payload.workflow.git.branch."
)
_PR_RESOLVER_DEFAULT_BRANCH_NAMES = frozenset(
    {"main", "master", "develop", "development", "trunk"}
)


def _pr_resolver_default_branch_names(
    *payloads: Mapping[str, Any],
) -> set[str]:
    names = set(_PR_RESOLVER_DEFAULT_BRANCH_NAMES)
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        for key in (
            "defaultBranch",
            "default_branch",
            "repositoryDefaultBranch",
            "repository_default_branch",
        ):
            value = str(payload.get(key) or "").strip().lower()
            if value:
                names.add(value)
    return names


def _pr_resolver_authored_branch_selector(
    *,
    task_payload: Mapping[str, Any],
    normalized_task: Mapping[str, Any],
    task_inputs: Mapping[str, Any],
    normalized_inputs: Mapping[str, Any],
    task_git: Mapping[str, Any],
    normalized_git: Mapping[str, Any],
) -> str:
    default_names = _pr_resolver_default_branch_names(
        task_payload,
        normalized_task,
        task_inputs,
        normalized_inputs,
        task_git,
        normalized_git,
    )
    for value in (
        task_git.get("branch"),
        normalized_git.get("branch"),
        task_payload.get("branch"),
        normalized_task.get("branch"),
    ):
        text = str(value or "").strip()
        if text and text.lower() not in default_names:
            return text
    return ""


def _pr_resolver_structured_selector(
    *,
    task_payload: dict[str, Any],
    normalized_task_for_planner: dict[str, Any] | None = None,
) -> str:
    normalized_task = (
        normalized_task_for_planner
        if isinstance(normalized_task_for_planner, dict)
        else {}
    )
    task_inputs = _coerce_mapping(task_payload.get("inputs"))
    normalized_inputs = _coerce_mapping(normalized_task.get("inputs"))
    task_git = _coerce_mapping(task_payload.get("git"))
    normalized_git = _coerce_mapping(normalized_task.get("git"))
    tool_payload = _coerce_mapping(task_payload.get("tool"))
    skill_payload = _coerce_mapping(task_payload.get("skill"))
    tool_inputs = _coerce_mapping(
        tool_payload.get("inputs") or tool_payload.get("args")
    )
    skill_inputs = _coerce_mapping(
        skill_payload.get("inputs") or skill_payload.get("args")
    )

    for value in (
        task_inputs.get("pr"),
        normalized_inputs.get("pr"),
        tool_inputs.get("pr"),
        skill_inputs.get("pr"),
        task_inputs.get("startingBranch"),
        normalized_inputs.get("startingBranch"),
        tool_inputs.get("startingBranch"),
        skill_inputs.get("startingBranch"),
        task_git.get("startingBranch"),
        normalized_git.get("startingBranch"),
        task_payload.get("startingBranch"),
        normalized_task.get("startingBranch"),
        task_inputs.get("branch"),
        normalized_inputs.get("branch"),
        tool_inputs.get("branch"),
        skill_inputs.get("branch"),
        _pr_resolver_authored_branch_selector(
            task_payload=task_payload,
            normalized_task=normalized_task,
            task_inputs=task_inputs,
            normalized_inputs=normalized_inputs,
            task_git=task_git,
            normalized_git=normalized_git,
        ),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _validate_workflow_runtime_requirements(
    *,
    task_payload: dict[str, Any],
    normalized_tool: dict[str, Any] | None,
    normalized_task_for_planner: dict[str, Any],
) -> None:
    tool_name = str((normalized_tool or {}).get("name") or "").strip().lower()
    if tool_name != "pr-resolver":
        return

    if _pr_resolver_structured_selector(
        task_payload=task_payload,
        normalized_task_for_planner=normalized_task_for_planner,
    ):
        return

    raise _invalid_workflow_request(_PR_RESOLVER_SELECTOR_ERROR)


def _derive_task_title(
    task_payload: dict[str, Any],
    *,
    normalized_tool: Mapping[str, Any] | None = None,
    normalized_steps: Sequence[Mapping[str, Any]] = (),
) -> str | None:
    current_title = str(task_payload.get("title") or "").strip()
    instructions = str(task_payload.get("instructions") or "").strip()
    has_tool_context = normalized_tool is not None or any(
        isinstance(task_payload.get(key), Mapping)
        for key in ("tool", "skill", "workflow")
    )
    if (
        instructions
        and not has_tool_context
        and not normalized_steps
        and (not current_title or is_generic_title(current_title))
    ):
        normalized = " ".join(instructions[: _MAX_TASK_TITLE_LENGTH * 2].split())
        if normalized:
            return normalized[:_MAX_TASK_TITLE_LENGTH]
    synthesized = synthesize_workflow_title(
        current_title=current_title,
        task_payload=task_payload,
        normalized_tool=normalized_tool,
        normalized_steps=normalized_steps,
    )
    if synthesized:
        return synthesized
    raw_steps = task_payload.get("steps")
    if isinstance(raw_steps, list):
        for item in raw_steps:
            if not isinstance(item, Mapping):
                continue
            step_title = str(item.get("title") or "").strip()
            if step_title:
                return step_title[:_MAX_TASK_TITLE_LENGTH]
    if not instructions:
        return None
    normalized = " ".join(instructions[: _MAX_TASK_TITLE_LENGTH * 2].split())
    if not normalized:
        return None
    return normalized[:_MAX_TASK_TITLE_LENGTH]


def _derive_workflow_summary(
    task_payload: dict[str, Any], input_artifact_ref: str | None
) -> str:
    instructions = str(task_payload.get("instructions") or "").strip()
    if not instructions:
        raw_steps = task_payload.get("steps")
        if isinstance(raw_steps, list):
            for item in raw_steps:
                if not isinstance(item, Mapping):
                    continue
                step_instructions = str(item.get("instructions") or "").strip()
                if step_instructions:
                    instructions = step_instructions
                    break
    if instructions:
        normalized = " ".join(instructions.split())
        if len(normalized) > _MAX_TASK_SUMMARY_LENGTH:
            preview_length = _MAX_TASK_SUMMARY_LENGTH - len(_TASK_SUMMARY_ELLIPSIS)
            return f"{normalized[:preview_length]}{_TASK_SUMMARY_ELLIPSIS}"
        return normalized
    if input_artifact_ref:
        return f"Task instructions stored in artifact {input_artifact_ref}."
    return "Execution initialized."

def _workflow_input_snapshot_ref_from_memo(
    memo: Mapping[str, Any],
) -> str | None:
    value = memo.get("task_input_snapshot_ref") or memo.get("taskInputSnapshotRef")
    candidate = str(value or "").strip()
    return candidate or None

def _workflow_input_snapshot_descriptor_from_record(
    record,
) -> WorkflowInputSnapshotDescriptorModel:
    memo = dict(getattr(record, "memo", None) or {})
    artifact_ref = _workflow_input_snapshot_ref_from_memo(memo)
    if artifact_ref:
        return WorkflowInputSnapshotDescriptorModel(
            available=True,
            artifactRef=artifact_ref,
            snapshotVersion=int(
                memo.get("task_input_snapshot_version")
                or memo.get("taskInputSnapshotVersion")
                or _WORKFLOW_INPUT_SNAPSHOT_VERSION
            ),
            sourceKind=str(
                memo.get("task_input_snapshot_source_kind")
                or memo.get("taskInputSnapshotSourceKind")
                or "unknown"
            ),
            reconstructionMode="authoritative",
            disabledReasons={},
            fallbackEvidenceRefs=[],
        )
    fallback_refs = [
        str(ref).strip()
        for ref in (
            getattr(record, "input_ref", None),
            getattr(record, "plan_ref", None),
        )
        if str(ref or "").strip()
    ]
    parameters = getattr(record, "parameters", None)
    task_payload = (
        _workflow_payload_from_parameters(parameters)
        if isinstance(parameters, Mapping)
        else {}
    )
    attachment_aware = bool(
        task_payload.get("inputAttachments") or task_payload.get("attachmentRefs")
    )
    if not attachment_aware:
        for step in task_payload.get("steps") or []:
            if isinstance(step, Mapping) and (
                step.get("inputAttachments") or step.get("attachmentRefs")
            ):
                attachment_aware = True
                break
    disabled_reasons = {
        "draft": "original_task_input_snapshot_missing",
    }
    if attachment_aware:
        disabled_reasons["attachments"] = "original_task_input_snapshot_missing"
    return WorkflowInputSnapshotDescriptorModel(
        available=False,
        artifactRef=None,
        snapshotVersion=None,
        sourceKind="unknown",
        reconstructionMode=(
            "degraded_read_only"
            if fallback_refs or attachment_aware
            else "unavailable"
        ),
        disabledReasons=disabled_reasons,
        fallbackEvidenceRefs=fallback_refs,
    )

def _build_original_workflow_input_snapshot_payload(
    *,
    source_kind: str,
    payload: Mapping[str, Any],
    task_payload: Mapping[str, Any],
    attachment_refs: list[dict[str, Any]] | None = None,
    source_workflow_id: str | None = None,
    source_run_id: str | None = None,
) -> dict[str, Any]:
    draft = {
        "workflowShape": _derive_task_snapshot_shape(task_payload),
        "repository": payload.get("repository"),
        "targetRuntime": payload.get("targetRuntime"),
        "requiredCapabilities": list(payload.get("requiredCapabilities") or []),
        "workflow": dict(task_payload),
        "authoredWorkflowInput": build_authoritative_workflow_input_snapshot(
            task_payload=task_payload,
            repository=payload.get("repository"),
            target_runtime=payload.get("targetRuntime"),
            required_capabilities=payload.get("requiredCapabilities"),
            dependency_declarations=payload.get("dependencies"),
            attachment_refs=attachment_refs,
        ),
    }
    return {
        "snapshotVersion": _WORKFLOW_INPUT_SNAPSHOT_VERSION,
        "source": {
            "kind": source_kind,
            **({"sourceWorkflowId": source_workflow_id} if source_workflow_id else {}),
            **({"sourceRunId": source_run_id} if source_run_id else {}),
        },
        "draft": draft,
        "largeContentRefs": {},
        "attachmentRefs": list(attachment_refs or []),
        "lineage": {},
        "excluded": {
            "schedule": (
                "Schedule controls are creation-only and are not editable through "
                "workflow edit/rerun."
            )
        },
    }

def _derive_task_snapshot_shape(task_payload: Mapping[str, Any]) -> str:
    instructions = str(task_payload.get("instructions") or "").strip()
    steps = task_payload.get("steps")
    if isinstance(steps, list) and steps:
        return "multi_step"
    if task_payload.get("inputArtifactRef"):
        return "artifact_backed"
    if task_payload.get("appliedStepTemplates"):
        return "template_derived"
    if not instructions and (
        isinstance(task_payload.get("tool"), Mapping)
        or isinstance(task_payload.get("skill"), Mapping)
        or task_payload.get("skills")
    ):
        return "skill_only"
    return "inline_instructions"

def _snapshot_source_payload_from_parameters(
    parameters: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    task: dict[str, Any] = _workflow_payload_from_parameters(parameters)
    instructions = str(parameters.get("instructions") or "").strip()
    if instructions and not str(task.get("instructions") or "").strip():
        task["instructions"] = instructions
    capabilities_value = parameters.get("requiredCapabilities")
    payload = {
        "repository": parameters.get("repository"),
        "targetRuntime": parameters.get("targetRuntime"),
        "requiredCapabilities": (
            list(capabilities_value) if isinstance(capabilities_value, list) else []
        ),
    }
    return payload, task


def _artifact_id_from_ref(value: Any) -> str | None:
    ref = _coerce_artifact_ref(value)
    if not ref:
        return None
    ref = ref.removeprefix("artifact://")
    ref = ref.removeprefix("input/")
    return ref.strip() or None


async def _hydrate_recovery_checkpoint_payload(
    *,
    session: AsyncSession,
    user: User,
    checkpoint_ref: str | None,
) -> Mapping[str, Any]:
    artifact_id = _artifact_id_from_ref(checkpoint_ref)
    if not artifact_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery is not available for this execution.",
                "reason": "recovery_checkpoint_missing",
            },
        )
    try:
        artifact_service = get_temporal_artifact_service(session)
        _artifact, body = await artifact_service.read(
            artifact_id=artifact_id,
            principal=str(getattr(user, "id", "") or "system"),
            allow_restricted_raw=True,
        )
        decoded = json.loads(body.decode("utf-8"))
    except (PermissionError, TemporalArtifactAuthorizationError) as exc:
        logger.warning(
            "Failed to hydrate Recovery checkpoint artifact %s: %s",
            artifact_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery is not available for this execution.",
                "reason": "checkpoint_unauthorized",
            },
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "Failed to hydrate Recovery checkpoint artifact %s: %s",
            artifact_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery is not available for this execution.",
                "reason": "checkpoint_corrupted",
            },
        ) from exc
    except Exception as exc:
        logger.warning(
            "Failed to hydrate Recovery checkpoint artifact %s: %s",
            artifact_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery is not available for this execution.",
                "reason": "recovery_checkpoint_missing",
            },
        ) from exc
    if not isinstance(decoded, Mapping):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery is not available for this execution.",
                "reason": "recovery_checkpoint_missing",
            },
        )
    return decoded


async def _hydrate_failed_run_recovery_manifest_payload(
    *,
    session: AsyncSession,
    user: User,
    manifest_ref: str | None,
) -> Mapping[str, Any]:
    artifact_id = _artifact_id_from_ref(manifest_ref)
    if not artifact_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery manifest is required.",
                "reason": "recovery_manifest_missing",
            },
        )
    try:
        artifact_service = get_temporal_artifact_service(session)
        _artifact, body = await artifact_service.read(
            artifact_id=artifact_id,
            principal=str(getattr(user, "id", "") or "system"),
            allow_restricted_raw=True,
        )
        decoded = json.loads(body.decode("utf-8"))
    except (PermissionError, TemporalArtifactAuthorizationError) as exc:
        logger.warning(
            "Failed to hydrate failed-run recovery manifest artifact %s: %s",
            artifact_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery manifest is not readable.",
                "reason": "recovery_manifest_unauthorized",
            },
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        logger.warning(
            "Failed to hydrate failed-run recovery manifest artifact %s: %s",
            artifact_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery manifest is invalid.",
                "reason": "recovery_manifest_corrupted",
            },
        ) from exc
    except Exception as exc:
        logger.warning(
            "Failed to hydrate failed-run recovery manifest artifact %s: %s",
            artifact_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery manifest is not readable.",
                "reason": "recovery_manifest_missing",
            },
        ) from exc
    if not isinstance(decoded, Mapping):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery manifest is invalid.",
                "reason": "recovery_manifest_corrupted",
            },
        )
    try:
        manifest = FailedRunRecoveryManifestModel.model_validate(decoded)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery manifest is invalid.",
                "reason": "recovery_manifest_corrupted",
            },
        ) from exc
    if not manifest.resume_allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery manifest blocks resume.",
                "reason": manifest.blocked_reason or "recovery_manifest_blocks_resume",
            },
        )
    return decoded


def _reject_recovery_manifest_mismatch(
    request: RecoverFromFailedStepRequest | RecoverFromSelectedStepRequest,
    *,
    canonical: TemporalExecutionCanonicalRecord,
    manifest_payload: Mapping[str, Any],
    checkpoint_ref: str,
) -> None:
    manifest = FailedRunRecoveryManifestModel.model_validate(manifest_payload)
    expected_run_id = str(canonical.run_id or "")
    mismatches: list[str] = []
    if manifest.workflow_id != canonical.workflow_id:
        mismatches.append("sourceWorkflowId")
    if manifest.run_id != expected_run_id:
        mismatches.append("sourceRunId")
    if manifest.validation.checkpoint_ref != checkpoint_ref:
        mismatches.append("recoveryCheckpointRef")
    logical_step_id = getattr(request, "logical_step_id", None)
    if logical_step_id and logical_step_id != manifest.failed_logical_step_id:
        mismatches.append("logicalStepId")
    source_execution_ordinal = getattr(request, "source_execution_ordinal", None)
    if (
        source_execution_ordinal is not None
        and source_execution_ordinal != manifest.failed_execution_ordinal
    ):
        mismatches.append("sourceExecutionOrdinal")
    if mismatches:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Recovery request fields do not match recovery manifest evidence.",
                "reason": "recovery_manifest_inconsistent",
                "fields": sorted(set(mismatches)),
            },
        )


def _recovery_not_available_reason(exc: Exception) -> str:
    message = str(exc).lower()
    if "selected start step" in message:
        return "selected_step_not_eligible"
    if "does not match" in message:
        return "checkpoint_inconsistent"
    if "plan" in message:
        return "plan_identity_missing"
    if "workspace" in message:
        return "workspace_checkpoint_missing"
    if "preserved step" in message or "completed" in message:
        return "completed_step_refs_missing"
    if "failed step" in message:
        return "failed_step_identity_missing"
    if "snapshot" in message:
        return "original_task_input_snapshot_missing"
    if "payload" in message or "invalid" in message:
        return "checkpoint_corrupted"
    return "recovery_checkpoint_missing"


def _snapshot_workflow_from_artifact_payload(
    artifact_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    draft = artifact_payload.get("draft")
    source = draft if isinstance(draft, Mapping) else artifact_payload
    workflow_value = source.get("workflow") if isinstance(source, Mapping) else None
    if not isinstance(workflow_value, Mapping) and isinstance(source, Mapping):
        workflow_value = source.get("task")
    task = dict(workflow_value) if isinstance(workflow_value, Mapping) else {}
    instructions = str(source.get("instructions") or "").strip()
    if instructions and not str(task.get("instructions") or "").strip():
        task["instructions"] = instructions
    capabilities_value = source.get("requiredCapabilities")
    target_runtime = source.get("targetRuntime") or artifact_payload.get(
        "targetRuntime"
    )
    if not target_runtime and isinstance(source.get("runtime"), str):
        target_runtime = source.get("runtime")
    payload = {
        "repository": source.get("repository") or artifact_payload.get("repository"),
        "targetRuntime": target_runtime,
        "requiredCapabilities": (
            list(capabilities_value) if isinstance(capabilities_value, list) else []
        ),
    }
    return payload, task


def _merge_workflow_preserving_artifact_instructions(
    artifact_task: Mapping[str, Any],
    parameter_task: Mapping[str, Any],
) -> dict[str, Any]:
    merged = {**dict(artifact_task), **dict(parameter_task)}
    artifact_instructions = str(artifact_task.get("instructions") or "").strip()
    parameter_instructions = str(parameter_task.get("instructions") or "").strip()
    if artifact_instructions and not parameter_instructions:
        merged["instructions"] = artifact_task.get("instructions")

    artifact_steps = artifact_task.get("steps")
    parameter_steps = parameter_task.get("steps")
    if isinstance(artifact_steps, list) and isinstance(parameter_steps, list):
        merged_steps: list[Any] = []
        for index, parameter_step in enumerate(parameter_steps):
            if not isinstance(parameter_step, Mapping):
                merged_steps.append(parameter_step)
                continue
            artifact_step = (
                artifact_steps[index]
                if index < len(artifact_steps)
                and isinstance(artifact_steps[index], Mapping)
                else {}
            )
            step = {**dict(artifact_step), **dict(parameter_step)}
            artifact_step_instructions = str(
                artifact_step.get("instructions") or ""
            ).strip()
            parameter_step_instructions = str(
                parameter_step.get("instructions") or ""
            ).strip()
            if artifact_step_instructions and not parameter_step_instructions:
                step["instructions"] = artifact_step.get("instructions")
            merged_steps.append(step)
        merged["steps"] = merged_steps
    return merged


async def _snapshot_source_payload_from_parameters_and_artifact(
    *,
    session: AsyncSession,
    user: User,
    record: TemporalExecutionRecord | TemporalExecutionCanonicalRecord,
    parameters: Mapping[str, Any],
    input_artifact_ref: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    parameter_payload, parameter_task = _snapshot_source_payload_from_parameters(
        parameters
    )
    artifact_ref = (
        input_artifact_ref
        or str(parameters.get("inputArtifactRef") or "").strip()
        or str(getattr(record, "input_ref", None) or "").strip()
    )
    artifact_id = _artifact_id_from_ref(artifact_ref)
    if not artifact_id:
        return parameter_payload, parameter_task
    try:
        artifact_service = get_temporal_artifact_service(session)
        _artifact, body = await artifact_service.read(
            artifact_id=artifact_id,
            principal=str(getattr(user, "id", "") or "system"),
            allow_restricted_raw=True,
        )
        decoded = json.loads(body.decode("utf-8"))
    except Exception as exc:
        logger.warning(
            "Failed to hydrate workflow input snapshot from artifact %s: %s",
            artifact_id,
            exc,
        )
        return parameter_payload, parameter_task
    if not isinstance(decoded, Mapping):
        return parameter_payload, parameter_task

    artifact_payload, artifact_task = _snapshot_workflow_from_artifact_payload(decoded)
    payload = {
        **artifact_payload,
        **{
            key: value
            for key, value in parameter_payload.items()
            if value not in (None, [], "")
        },
    }
    if artifact_task and parameter_task:
        return payload, _merge_workflow_preserving_artifact_instructions(
            artifact_task,
            parameter_task,
        )
    return payload, parameter_task or artifact_task

async def _persist_original_workflow_input_snapshot(
    *,
    session: AsyncSession,
    record,
    user: User,
    payload: Mapping[str, Any],
    task_payload: Mapping[str, Any],
    attachment_refs: list[dict[str, Any]] | None = None,
    source_kind: str,
    source_workflow_id: str | None = None,
    source_run_id: str | None = None,
) -> str:
    if not isinstance(
        record, (TemporalExecutionRecord, TemporalExecutionCanonicalRecord)
    ):
        return ""
    canonical_record = (
        record
        if isinstance(record, TemporalExecutionCanonicalRecord)
        else await session.get(TemporalExecutionCanonicalRecord, record.workflow_id)
    )
    if canonical_record is None:
        return ""
    snapshot_payload = _build_original_workflow_input_snapshot_payload(
        source_kind=source_kind,
        payload=payload,
        task_payload=task_payload,
        attachment_refs=attachment_refs,
        source_workflow_id=source_workflow_id,
        source_run_id=source_run_id,
    )
    artifact_service = get_temporal_artifact_service(session)
    principal = str(getattr(user, "id", "") or "system")
    body = json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    artifact, _upload = await artifact_service.create(
        principal=principal,
        content_type=_TASK_INPUT_SNAPSHOT_CONTENT_TYPE,
        size_bytes=len(body),
        retention_class=TemporalArtifactRetentionClass.LONG,
        link={
            "namespace": canonical_record.namespace,
            "workflow_id": canonical_record.workflow_id,
            "run_id": canonical_record.run_id,
            "link_type": _TASK_INPUT_SNAPSHOT_LINK_TYPE,
            "label": "Original task input snapshot",
        },
        metadata_json={
            "artifact_class": _TASK_INPUT_SNAPSHOT_LINK_TYPE,
            "snapshot_version": _WORKFLOW_INPUT_SNAPSHOT_VERSION,
            "workflow_type": "MoonMind.UserWorkflow",
            "source_kind": source_kind,
            "draft_shape": snapshot_payload["draft"]["workflowShape"],
            "schema_name": "OriginalTaskInputSnapshot",
            "created_by": principal,
            "attachment_refs": list(attachment_refs or []),
        },
    )
    completed = await artifact_service.write_complete(
        artifact_id=artifact.artifact_id,
        principal=principal,
        payload=body,
        content_type=_TASK_INPUT_SNAPSHOT_CONTENT_TYPE,
    )

    records_to_update = [canonical_record]
    if record is not canonical_record:
        records_to_update.append(record)
    for target_record in records_to_update:
        memo = dict(target_record.memo or {})
        memo["task_input_snapshot_ref"] = completed.artifact_id
        memo["task_input_snapshot_version"] = _WORKFLOW_INPUT_SNAPSHOT_VERSION
        memo["task_input_snapshot_source_kind"] = source_kind
        target_record.memo = memo
        refs = list(target_record.artifact_refs or [])
        for attachment_ref in attachment_refs or []:
            attachment_id = str(attachment_ref.get("artifactId") or "").strip()
            if attachment_id and attachment_id not in refs:
                refs.append(attachment_id)
        if completed.artifact_id not in refs:
            refs.append(completed.artifact_id)
            target_record.artifact_refs = refs
        else:
            target_record.artifact_refs = refs
    return completed.artifact_id


async def _persist_original_workflow_input_snapshot_from_parameters(
    *,
    session: AsyncSession,
    record: TemporalExecutionRecord | TemporalExecutionCanonicalRecord,
    user: User,
    parameters: Mapping[str, Any],
    attachment_refs: list[dict[str, Any]] | None = None,
    source_kind: str,
    source_workflow_id: str | None = None,
    source_run_id: str | None = None,
    input_artifact_ref: str | None = None,
) -> str:
    workflow_type_value = _enum_value(getattr(record, "workflow_type", None))
    if workflow_type_value != "MoonMind.UserWorkflow":
        return ""
    snapshot_payload, snapshot_task = (
        await _snapshot_source_payload_from_parameters_and_artifact(
            session=session,
            user=user,
            record=record,
            parameters=parameters,
            input_artifact_ref=input_artifact_ref,
        )
    )
    if not snapshot_task:
        return ""
    return await _persist_original_workflow_input_snapshot(
        session=session,
        record=record,
        user=user,
        payload=snapshot_payload,
        task_payload=snapshot_task,
        attachment_refs=attachment_refs,
        source_kind=source_kind,
        source_workflow_id=source_workflow_id,
        source_run_id=source_run_id,
    )

async def _reuse_original_task_input_snapshot_from_source(
    *,
    session: AsyncSession,
    source_record: TemporalExecutionRecord | TemporalExecutionCanonicalRecord,
    target_record: TemporalExecutionRecord | TemporalExecutionCanonicalRecord,
) -> str:
    source_memo = dict(getattr(source_record, "memo", None) or {})
    snapshot_ref = _workflow_input_snapshot_ref_from_memo(source_memo)
    if not snapshot_ref:
        return ""
    raw_version = source_memo.get("task_input_snapshot_version")
    if raw_version is None:
        raw_version = source_memo.get("taskInputSnapshotVersion")
    try:
        snapshot_version = (
            int(raw_version)
            if raw_version is not None
            else _WORKFLOW_INPUT_SNAPSHOT_VERSION
        )
    except (TypeError, ValueError):
        snapshot_version = _WORKFLOW_INPUT_SNAPSHOT_VERSION
    records_to_update: list[
        TemporalExecutionRecord | TemporalExecutionCanonicalRecord
    ] = []
    if isinstance(
        target_record,
        (TemporalExecutionRecord, TemporalExecutionCanonicalRecord),
    ):
        records_to_update.append(target_record)
        if not isinstance(target_record, TemporalExecutionCanonicalRecord):
            canonical_record = await session.get(
                TemporalExecutionCanonicalRecord,
                target_record.workflow_id,
            )
            if canonical_record is not None:
                records_to_update.append(canonical_record)
    linked_execution_keys: set[tuple[str, str, str]] = set()
    for target in records_to_update:
        memo = dict(target.memo or {})
        memo["task_input_snapshot_ref"] = snapshot_ref
        memo["task_input_snapshot_version"] = snapshot_version
        memo["task_input_snapshot_source_kind"] = "rerun"
        target.memo = memo
        refs = list(target.artifact_refs or [])
        if snapshot_ref not in refs:
            refs.append(snapshot_ref)
            target.artifact_refs = refs
        execution_key = (target.namespace, target.workflow_id, target.run_id)
        if execution_key in linked_execution_keys:
            continue
        linked_execution_keys.add(execution_key)
        exists = await session.execute(
            select(TemporalArtifactLink.id).where(
                TemporalArtifactLink.artifact_id == snapshot_ref,
                TemporalArtifactLink.namespace == target.namespace,
                TemporalArtifactLink.workflow_id == target.workflow_id,
                TemporalArtifactLink.run_id == target.run_id,
                TemporalArtifactLink.link_type == _TASK_INPUT_SNAPSHOT_LINK_TYPE,
            ).limit(1)
        )
        if exists.scalar_one_or_none() is None:
            session.add(
                TemporalArtifactLink(
                    id=uuid4(),
                    artifact_id=snapshot_ref,
                    namespace=target.namespace,
                    workflow_id=target.workflow_id,
                    run_id=target.run_id,
                    link_type=_TASK_INPUT_SNAPSHOT_LINK_TYPE,
                    label="Original task input snapshot",
                )
            )
    return snapshot_ref

async def _attach_input_attachment_artifacts_to_execution(
    *,
    session: AsyncSession | None,
    record,
    attachment_refs: list[dict[str, Any]],
) -> None:
    if session is None or not attachment_refs:
        return
    if not isinstance(
        record, (TemporalExecutionRecord, TemporalExecutionCanonicalRecord)
    ):
        return
    unique_ids = list(dict.fromkeys(str(ref.get("artifactId") or "") for ref in attachment_refs))
    unique_ids = [artifact_id for artifact_id in unique_ids if artifact_id]
    if not unique_ids:
        return

    existing_refs = list(record.artifact_refs or [])
    changed_refs = False
    for artifact_id in unique_ids:
        if artifact_id not in existing_refs:
            existing_refs.append(artifact_id)
            changed_refs = True
        session.add(
            TemporalArtifactLink(
                id=uuid4(),
                artifact_id=artifact_id,
                namespace=record.namespace,
                workflow_id=record.workflow_id,
                run_id=record.run_id,
                link_type="input.attachment",
                label=next(
                    (
                        str(ref.get("filename") or "").strip()
                        for ref in attachment_refs
                        if ref.get("artifactId") == artifact_id
                    ),
                    None,
                )
                or None,
            )
        )
    if changed_refs:
        record.artifact_refs = existing_refs
    await session.flush()

def _workflow_payload_from_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    workflow_payload = parameters.get("workflow")
    if isinstance(workflow_payload, Mapping):
        return dict(workflow_payload)
    legacy_task_payload = parameters.get("task")
    if isinstance(legacy_task_payload, Mapping):
        return dict(legacy_task_payload)
    return {}


async def _create_execution_from_workflow_request(
    *,
    request: CreateJobRequest,
    service: TemporalExecutionService,
    user: User,
    session: Any = None,
    principal_context: dict[str, Any] | None = None,
) -> ExecutionModel | ScheduleCreatedResponse:
    request_type = str(request.type).strip().lower()
    if request_type not in {"task", "workflow"}:
        raise _invalid_workflow_request(
            "Only task-shaped submit requests can be mapped to Temporal executions."
        )

    payload = request.payload if isinstance(request.payload, dict) else {}
    task_node = payload.get("task")
    workflow_node = payload.get("workflow")
    if isinstance(task_node, dict) and isinstance(workflow_node, dict):
        raise _invalid_workflow_request(
            "Temporal submit requests must include only one of payload.task or payload.workflow."
        )
    task_payload = {}
    if isinstance(task_node, dict):
        task_payload = task_node
    elif isinstance(workflow_node, dict):
        task_payload = workflow_node
    if not task_payload:
        required_field = (
            "payload.workflow" if request_type == "workflow" else "payload.task"
        )
        shape_name = "Workflow-shaped" if request_type == "workflow" else "Task-shaped"
        raise _invalid_workflow_request(
            f"{shape_name} Temporal submit requests require {required_field}."
        )
    _reject_submit_version_identity(task_payload)

    # Resolve child-agent runtime inheritance before downstream normalization
    # consumes targetRuntime / task.runtime fields.  When inheritance applies,
    # we stamp the parent's effective runtime onto the request payload so the
    # rest of the create path sees it exactly as it would an explicit request.
    principal = await resolve_execution_principal(
        user=user,
        service=service,
        request=(principal_context or {}).get("request"),
        workflow_id_header=(principal_context or {}).get("workflow_id_header"),
        run_id_header=(principal_context or {}).get("run_id_header"),
        agent_run_id_header=(principal_context or {}).get("agent_run_id_header"),
    )
    try:
        inherited = await resolve_child_runtime_inheritance(
            request_payload=payload,
            task_payload=task_payload,
            principal=principal,
            service=service,
        )
    except RuntimeInheritanceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if inherited is not None:
        apply_inherited_runtime_to_payload(
            payload=payload,
            task_payload=task_payload,
            inherited=inherited,
        )

    # --- Schedule routing ---
    schedule: ScheduleParameters | None = None
    raw_schedule = getattr(request, "schedule", None) or payload.get("schedule")
    if isinstance(raw_schedule, dict):
        schedule = ScheduleParameters.model_validate(raw_schedule)
    elif isinstance(raw_schedule, ScheduleParameters):
        schedule = raw_schedule

    route = await _resolve_schedule_routing(
        schedule,
        request_payload=payload,
        user=user,
        session=session,
    )
    if route.recurring_response is not None:
        return route.recurring_response

    start_delay = route.start_delay
    scheduled_for_dt = route.scheduled_for

    required_capabilities = _coerce_string_list(
        payload.get("requiredCapabilities"),
        field_name="payload.requiredCapabilities",
    )

    if "dependsOn" in task_payload:
        depends_on_source = task_payload.get("dependsOn")
        field_name = "payload.workflow.dependsOn"
    else:
        depends_on_source = payload.get("dependsOn")
        field_name = "payload.dependsOn"

    raw_depends_on = _coerce_string_list(
        depends_on_source,
        field_name=field_name
    )

    depends_on = list(dict.fromkeys(d.strip() for d in raw_depends_on if d.strip()))

    if len(depends_on) > 10:
        raise _invalid_workflow_request(f"{field_name} can have a maximum of 10 items.")

    if session is not None:
        await _expand_goal_preset_for_workflow_submission(
            task_payload=task_payload,
            request_payload=payload,
            session=session,
            user=user,
        )
        _reject_submit_version_identity(task_payload)

    (
        objective_attachment_refs,
        step_attachment_refs,
        attachment_index,
    ) = await _validate_and_collect_task_input_attachments(
        task_payload=task_payload,
        session=session,
        principal=str(user.id),
    )
    step_count = _coerce_step_count(task_payload.get("steps"))
    normalized_steps = _normalize_task_steps(task_payload)
    if step_attachment_refs:
        for index, refs in step_attachment_refs.items():
            if index < len(normalized_steps):
                normalized_steps[index]["inputAttachments"] = refs

    raw_repository = payload.get("repository")
    if raw_repository is not None and not isinstance(raw_repository, str):
        raise _invalid_workflow_request("payload.repository must be a string.")
    repository = raw_repository.strip() if isinstance(raw_repository, str) else None
    if repository == "":
        repository = None
    integration = (
        str(
            payload.get("integration")
            or (payload.get("metadata") or {}).get("integration")
            or ""
        ).strip()
        or None
    )
    input_artifact_ref = _coerce_artifact_ref(
        task_payload.get("inputArtifactRef") or payload.get("inputArtifactRef")
    )
    plan_artifact_ref = _coerce_artifact_ref(
        task_payload.get("planArtifactRef") or payload.get("planArtifactRef")
    )
    manifest_artifact_ref = _coerce_artifact_ref(
        task_payload.get("manifestArtifactRef") or payload.get("manifestArtifactRef")
    )
    runtime_from_task = task_payload.get("runtime")
    runtime_payload = (
        _validate_runtime_tier_intent_for_submit(
            runtime_from_task,
            field_name="payload.workflow.runtime",
        )
        if isinstance(runtime_from_task, dict)
        else {}
    )
    merge_automation_payload = _normalize_merge_automation_payload(
        payload.get("mergeAutomation") or payload.get("merge_automation")
    )
    task_merge_automation_payload = _normalize_merge_automation_payload(
        task_payload.get("mergeAutomation") or task_payload.get("merge_automation")
    )
    story_output_payload = _normalize_story_output_payload(
        task_payload.get("storyOutput") or task_payload.get("story_output")
    )
    report_output_payload = _normalize_report_output_payload(
        task_payload.get("reportOutput"),
        task_payload.get("report_output"),
        payload.get("reportOutput"),
        payload.get("report_output"),
    )
    normalized_tool = _normalize_task_tool(task_payload)
    _validate_pentest_submission_boundary(
        task_payload=task_payload,
        normalized_tool=normalized_tool,
        normalized_steps=normalized_steps,
        user=user,
        request=(principal_context or {}).get("request"),
    )
    deployment_skill_metadata = await _enrich_deployment_skill_metadata(
        session=session,
        normalized_tool=normalized_tool,
        steps=normalized_steps,
    )
    publish_skill_id = _workflow_publish_skill_id(task_payload, normalized_tool)
    publish_skill_metadata = deployment_skill_metadata.get(
        str(publish_skill_id or "").strip()
    )
    publish_metadata = (
        publish_skill_metadata.get("publish")
        if isinstance(publish_skill_metadata, Mapping)
        and isinstance(publish_skill_metadata.get("publish"), Mapping)
        else None
    )
    side_effect_metadata = None
    if isinstance(publish_skill_metadata, Mapping):
        side_effect_metadata = publish_skill_metadata.get("sideEffect")
        if not isinstance(side_effect_metadata, Mapping):
            side_effect_metadata = publish_skill_metadata.get("side_effect")
        if not isinstance(side_effect_metadata, Mapping):
            side_effect_metadata = None
    publish_payload = _resolve_workflow_publish_payload(
        payload=payload,
        task_payload=task_payload,
        normalized_tool=normalized_tool,
        skill_publish_metadata=publish_metadata,
        skill_side_effect_metadata=side_effect_metadata,
    )
    normalized_task_skills = _normalize_task_skill_selectors(
        task_payload.get("skills"),
        field_name="payload.workflow.skills",
    )
    normalized_proposal_policy = _normalize_workflow_proposal_policy(
        task_payload.get("proposalPolicy")
    )
    propose_tasks = _coerce_bool(task_payload.get("proposeTasks"), default=False)
    normalized_task_for_planner: dict[str, Any] = {}
    instructions = str(task_payload.get("instructions") or "").strip()
    if report_output_payload.get("enabled"):
        instructions = (
            instructions + _report_output_instruction(report_output_payload)
        ).strip()
    if depends_on:
        normalized_task_for_planner["dependsOn"] = depends_on
    if instructions:
        normalized_task_for_planner["instructions"] = instructions
    if report_output_payload:
        normalized_task_for_planner["reportOutput"] = dict(report_output_payload)
    normalized_task_for_planner["proposeTasks"] = propose_tasks
    if normalized_proposal_policy is not None:
        normalized_task_for_planner["proposalPolicy"] = normalized_proposal_policy
    normalized_input_attachments = _normalize_task_input_attachments(
        task_payload.get("inputAttachments")
        or task_payload.get("input_attachments"),
        field_name="payload.workflow.inputAttachments",
    )
    if normalized_input_attachments:
        normalized_task_for_planner["inputAttachments"] = normalized_input_attachments
    if normalized_task_skills is not None:
        normalized_task_for_planner["skills"] = normalized_task_skills
    if normalized_tool is not None:
        normalized_task_for_planner["tool"] = normalized_tool
        # Keep legacy shape for compatibility while tool is canonical.
        normalized_skill = {
            "name": normalized_tool["name"],
        }
        for metadata_key in ("publish", "sideEffect"):
            metadata_value = normalized_tool.get(metadata_key)
            if isinstance(metadata_value, Mapping):
                normalized_skill[metadata_key] = dict(metadata_value)
        normalized_task_for_planner["skill"] = normalized_skill
        if isinstance(normalized_tool.get("inputs"), dict):
            normalized_task_for_planner["inputs"] = dict(normalized_tool["inputs"])
    if isinstance(task_payload.get("inputs"), dict):
        normalized_task_for_planner["inputs"] = dict(task_payload["inputs"])
    if objective_attachment_refs:
        normalized_task_for_planner["inputAttachments"] = objective_attachment_refs
    if runtime_payload:
        normalized_task_for_planner["runtime"] = dict(runtime_payload)
    task_title = str(task_payload.get("title") or "").strip()
    if task_title:
        normalized_task_for_planner["title"] = task_title
    if publish_payload:
        normalized_task_for_planner["publish"] = dict(publish_payload)
    if task_merge_automation_payload:
        normalized_task_for_planner["mergeAutomation"] = dict(
            task_merge_automation_payload
        )
    if story_output_payload:
        normalized_task_for_planner["storyOutput"] = dict(story_output_payload)
    remediation_payload = task_payload.get("remediation")
    if remediation_payload is not None:
        normalized_task_for_planner["remediation"] = (
            dict(remediation_payload)
            if isinstance(remediation_payload, Mapping)
            else remediation_payload
        )
    git_payload = (
        task_payload.get("git") if isinstance(task_payload.get("git"), dict) else {}
    )
    if isinstance(task_payload.get("git"), Mapping) and isinstance(
        task_payload["git"].get("targetBranch"), str
    ) and task_payload["git"]["targetBranch"].strip():
        raise _invalid_workflow_request(
            "payload.workflow.git.targetBranch is not supported; use "
            "payload.workflow.git.branch."
        )
    if isinstance(task_payload.get("targetBranch"), str) and task_payload[
        "targetBranch"
    ].strip():
        raise _invalid_workflow_request(
            "payload.workflow.targetBranch is not supported; use payload.workflow.git.branch."
        )
    _validate_pr_base_branch_submission(
        publish_payload=publish_payload,
        task_payload=task_payload,
        git_payload=git_payload,
    )
    if git_payload:
        normalized_git_payload: dict[str, str] = {}
        for git_key in ("startingBranch", "branch"):
            git_value = git_payload.get(git_key)
            if isinstance(git_value, str) and git_value.strip():
                normalized_git_payload[git_key] = git_value.strip()
        if normalized_git_payload:
            normalized_task_for_planner["git"] = normalized_git_payload
    for key in ("repoRef", "startingBranch", "branch"):
        value = task_payload.get(key)
        if isinstance(value, str) and value.strip():
            normalized_task_for_planner[key] = value.strip()
    if isinstance(task_payload.get("taskTemplate"), Mapping):
        normalized_task_for_planner["taskTemplate"] = dict(task_payload["taskTemplate"])
    if isinstance(task_payload.get("presetSchedule"), Mapping):
        normalized_task_for_planner["presetSchedule"] = dict(
            task_payload["presetSchedule"]
        )
    for key in ("authoredPresets", "appliedStepTemplates"):
        value = task_payload.get(key)
        if isinstance(value, list):
            normalized_task_for_planner[key] = [
                dict(item) if isinstance(item, Mapping) else item for item in value
            ]

    _validate_workflow_runtime_requirements(
        task_payload=task_payload,
        normalized_tool=normalized_tool,
        normalized_task_for_planner=normalized_task_for_planner,
    )
    derived_task_title = _derive_task_title(
        task_payload,
        normalized_tool=normalized_tool,
        normalized_steps=normalized_steps,
    )
    if derived_task_title:
        normalized_task_for_planner["title"] = derived_task_title

    # --- Model resolution ---
    raw_target_runtime = (
        payload.get("targetRuntime")
        or runtime_payload.get("mode")
        or settings.workflow.default_runtime
        or ""
    )
    raw_profile_id = str(
        runtime_payload.get("providerProfileRef")
        or runtime_payload.get("profileId")
        or runtime_payload.get("providerProfile")
        or task_payload.get("providerProfileRef")
        or task_payload.get("profileId")
        or task_payload.get("providerProfile")
        or payload.get("providerProfileRef")
        or payload.get("profileId")
        or payload.get("providerProfile")
        or ""
    ).strip() or None
    # Preserve the original requested model byte-for-byte (Compatibility Policy:
    # codex.model and codex.effort inputs must not be modified).
    raw_requested_model: str | None = runtime_payload.get("model") or None
    if raw_requested_model is not None:
        raw_requested_model = str(raw_requested_model)

    # Normalize targetRuntime to canonical form and validate it.
    canonical_target_runtime: str | None = None
    if raw_target_runtime:
        normalized_rt = normalize_runtime_id(raw_target_runtime)
        if normalized_rt not in _SUPPORTED_TASK_RUNTIMES:
            raise _invalid_workflow_request(
                f"Unsupported targetRuntime: {raw_target_runtime!r}. "
                "Must be one of: codex_cli, claude_code, codex_cloud, jules, omnigent."
            )
        canonical_target_runtime = normalized_rt

    if canonical_target_runtime:
        normalized_runtime_for_planner = dict(
            normalized_task_for_planner.get("runtime") or {}
        )
        normalized_runtime_for_planner["mode"] = canonical_target_runtime
        if isinstance(runtime_payload.get("profileSelector"), Mapping):
            normalized_runtime_for_planner["profileSelector"] = dict(
                runtime_payload["profileSelector"]
            )
        normalized_task_for_planner["runtime"] = normalized_runtime_for_planner

    # Load provider profile when a profileId is supplied. For tier-aware
    # submissions without an exact profile, use the same runtime default profile
    # selection order as the ProviderProfileManager.
    _provider_profile = None
    if raw_profile_id and session is not None:
        _provider_profile = await session.get(ManagedAgentProviderProfile, raw_profile_id)
        if _provider_profile is None:
            raise _invalid_workflow_request(
                f"Provider profile not found: {raw_profile_id!r}."
            )
    elif _runtime_model_tier(runtime_payload) is not None:
        _provider_profile = await _load_default_provider_profile_for_runtime(
            session=session,
            runtime_id=canonical_target_runtime,
        )

    (
        resolved_model,
        model_source,
        resolved_effort,
        model_tier_resolution,
    ) = _resolve_runtime_model_effort(
        runtime_id=canonical_target_runtime,
        profile=_provider_profile,
        runtime_payload=runtime_payload,
        requested_model=raw_requested_model,
    )

    await _resolve_step_runtime_selections(
        steps=normalized_steps,
        task_runtime=runtime_payload,
        task_target_runtime=canonical_target_runtime,
        task_profile_id=raw_profile_id if _provider_profile is not None else None,
        session=session,
    )
    if normalized_steps:
        normalized_task_for_planner["steps"] = normalized_steps

    known_refs = {
        str(value).strip()
        for value in (
            _coerce_mapping(normalized_task_for_planner.get("git")).get(
                "startingBranch"
            ),
            _coerce_mapping(normalized_task_for_planner.get("git")).get("branch"),
            normalized_task_for_planner.get("startingBranch"),
            normalized_task_for_planner.get("branch"),
        )
        if str(value or "").strip()
    }

    initial_parameters = {
        "requestType": request.type,
        "repository": repository,
        "requiredCapabilities": required_capabilities,
        "priority": request.priority,
        "maxAttempts": request.max_attempts,
        "targetRuntime": canonical_target_runtime,
        "model": resolved_model,
        "requestedModel": raw_requested_model,
        "modelSource": model_source,
        "profileId": raw_profile_id if _provider_profile is not None else None,
        "effort": resolved_effort,
        "publishMode": publish_payload["mode"],
        "stepCount": step_count,
    }
    if isinstance(payload.get("omnigent"), Mapping):
        initial_parameters["omnigent"] = dict(payload["omnigent"])
    if "modelTier" in runtime_payload:
        initial_parameters["modelTier"] = runtime_payload.get("modelTier")
    if "tierFallback" in runtime_payload:
        initial_parameters["tierFallback"] = runtime_payload.get("tierFallback")
    if isinstance(runtime_payload.get("profileSelector"), Mapping):
        initial_parameters["profileSelector"] = dict(
            runtime_payload["profileSelector"]
        )
        initial_parameters.setdefault("runtime", {})["profileSelector"] = dict(
            runtime_payload["profileSelector"]
        )
    if model_tier_resolution is not None:
        initial_parameters["modelTierResolution"] = model_tier_resolution
    if known_refs:
        initial_parameters["knownRefs"] = sorted(known_refs)
    if story_output_payload:
        initial_parameters["storyOutput"] = dict(story_output_payload)
    if report_output_payload:
        initial_parameters["reportOutput"] = dict(report_output_payload)
    if merge_automation_payload:
        initial_parameters["mergeAutomation"] = dict(merge_automation_payload)
    if instructions:
        initial_parameters["instructions"] = instructions
    if normalized_task_for_planner:
        initial_parameters["workflow"] = normalized_task_for_planner
    skill_validation = await validate_skill_step_inputs(
        initial_parameters=initial_parameters,
        session=session,
    )
    if not skill_validation.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_skill_step_inputs",
                "message": "Skill step inputs failed validation.",
                "errors": skill_validation.error_dicts(),
            },
        )
    initial_parameters = skill_validation.parameters

    try:
        start_contract = resolve_user_workflow_start_contract(settings.temporal)
        record = await service.create_execution(
            workflow_type=start_contract.workflow_type,
            owner_id=user.id,
            title=derived_task_title,
            input_artifact_ref=input_artifact_ref,
            plan_artifact_ref=plan_artifact_ref,
            manifest_artifact_ref=manifest_artifact_ref,
            failure_policy=None,
            initial_parameters=initial_parameters,
            idempotency_key=task_payload.get("idempotencyKey")
            or payload.get("idempotencyKey"),
            repository=repository,
            integration=integration,
            summary=_derive_workflow_summary(task_payload, input_artifact_ref),
            start_delay=start_delay,
            scheduled_for=scheduled_for_dt,
        )
    except TemporalExecutionValidationError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": _validation_error_code(message),
                "message": message,
            },
        ) from exc

    await _attach_input_attachment_artifacts_to_execution(
        session=session,
        record=record,
        attachment_refs=attachment_index,
    )

    snapshot_ref = await _persist_original_workflow_input_snapshot_from_parameters(
        session=session,
        record=record,
        user=user,
        parameters=initial_parameters,
        attachment_refs=attachment_index,
        source_kind="create",
        input_artifact_ref=input_artifact_ref,
    )
    if snapshot_ref:
        await session.commit()
    if isinstance(record, (TemporalExecutionRecord, TemporalExecutionCanonicalRecord)):
        await session.refresh(record)
    execution = _serialize_execution(record, user=user)
    return execution

async def _create_execution_from_manifest_request(
    *,
    request: CreateJobRequest,
    service: TemporalExecutionService,
    user: User,
) -> ExecutionModel:
    if str(request.type).strip().lower() != "manifest":
        raise _invalid_workflow_request(
            "Only manifest-shaped submit requests can be mapped to Temporal manifest executions."
        )

    payload = request.payload if isinstance(request.payload, dict) else {}
    manifest_payload = (
        payload.get("manifest") if isinstance(payload.get("manifest"), dict) else {}
    )
    if not manifest_payload:
        raise _invalid_workflow_request(
            "Manifest-shaped Temporal submit requests require payload.manifest."
        )

    name = str(manifest_payload.get("name", "inline")).strip()
    action = str(manifest_payload.get("action", "run")).strip()
    options = manifest_payload.get("options", {})
    idempotency_key = str(payload.get("idempotencyKey") or "").strip() or None

    try:
        record = await service.create_execution(
            workflow_type="MoonMind.ManifestIngest",
            owner_id=user.id,
            title=f"Manifest: {name}",
            summary=f"Manifest execution for {name} ({action})",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "manifestName": name,
                "action": action,
                "options": options,
                "systemPayload": {"manifest": manifest_payload},
            },
            idempotency_key=idempotency_key,
        )
    except TemporalExecutionValidationError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": _validation_error_code(message),
                "message": message,
            },
        ) from exc

    return _serialize_execution(record, user=user)

async def _get_owned_execution(
    *,
    service: TemporalExecutionService,
    workflow_id: str,
    user: User,
    include_orphaned_projection: bool = False,
    use_cancel_target_fallback: bool = False,
):
    try:
        if use_cancel_target_fallback:
            record = await service.describe_cancel_target_execution(workflow_id)
        else:
            record = await service.describe_execution(
                workflow_id,
                include_orphaned=include_orphaned_projection,
            )
    except TemporalExecutionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "execution_not_found",
                "message": str(exc),
            },
        ) from exc

    if _is_execution_admin(user):
        return record

    search_attributes = dict(getattr(record, "search_attributes", None) or {})
    record_owner_type = _enum_value(getattr(record, "owner_type", None))
    if record_owner_type is None:
        record_owner_type = _normalize_owner_type(
            record, search_attributes
        )
    record_owner_id = str(getattr(record, "owner_id", "") or "").strip()
    if not record_owner_id:
        record_owner_id = _coerce_temporal_scalar(search_attributes.get("mm_owner_id"))

    if record_owner_type != "user" or record_owner_id != _owner_id(user):
        # Fallback to parent workflow ownership for child workflows missing search_attributes
        if not record_owner_id:
            parent_id = None
            if ":agent:" in workflow_id:
                parent_id = workflow_id.split(":agent:")[0]
            else:
                parts = workflow_id.split(":")
                if workflow_id.startswith("mm:") and len(parts) >= 2:
                    parent_id = f"{parts[0]}:{parts[1]}"
                elif len(parts) >= 1:
                    parent_id = parts[0]
            
            if parent_id and parent_id != workflow_id:
                try:
                    parent_record = await service.describe_execution(
                        parent_id,
                        include_orphaned=include_orphaned_projection,
                    )
                    parent_attrs = dict(getattr(parent_record, "search_attributes", None) or {})
                    p_type = _enum_value(getattr(parent_record, "owner_type", None))
                    if p_type is None:
                        p_type = _normalize_owner_type(parent_record, parent_attrs)
                    p_id = str(getattr(parent_record, "owner_id", "") or "").strip()
                    if not p_id:
                        p_id = _coerce_temporal_scalar(parent_attrs.get("mm_owner_id"))
                    
                    if p_type == "user" and p_id == _owner_id(user):
                        return record
                except Exception:
                    pass

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "execution_not_found",
                "message": f"Workflow execution {workflow_id} was not found",
            },
        )

    return record

def _compute_schedule_delay(
    scheduled_for: datetime,
) -> timedelta:
    """Return a positive timedelta from *now* to *scheduled_for*.

    Raises ``HTTPException`` if the target is in the past or negative.
    """
    now = datetime.now(UTC)
    delay = scheduled_for - now
    if delay.total_seconds() < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "schedule_in_past",
                "message": "scheduledFor must be a future datetime.",
            },
        )
    return delay

def _first_mapping_value(
    source: Mapping[str, Any],
    keys: tuple[str, ...],
) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return None

def _recurring_workflow_payload(
    parameters: Mapping[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    for key in ("workflow", "task"):
        task_payload = parameters.get(key)
        if isinstance(task_payload, Mapping):
            return key, dict(task_payload)
    return None, {}


async def _resolve_recurring_runtime_metadata(
    request_payload: Mapping[str, Any],
    *,
    session: Any | None,
) -> dict[str, Any]:
    workflow_type = str(
        request_payload.get("workflowType")
        or request_payload.get("workflow_type")
        or "MoonMind.UserWorkflow"
    ).strip()
    if workflow_type != "MoonMind.UserWorkflow":
        return {}

    parameter_payload = request_payload
    raw_initial_parameters = request_payload.get("initialParameters")
    if raw_initial_parameters is None:
        raw_initial_parameters = request_payload.get("initial_parameters")
    if isinstance(raw_initial_parameters, Mapping):
        parameter_payload = raw_initial_parameters

    _task_key, task_payload = _recurring_workflow_payload(parameter_payload)
    if not task_payload and parameter_payload is not request_payload:
        _task_key, task_payload = _recurring_workflow_payload(request_payload)

    raw_runtime_payload = (
        task_payload.get("runtime")
        if isinstance(task_payload.get("runtime"), Mapping)
        else parameter_payload.get("runtime")
        if isinstance(parameter_payload.get("runtime"), Mapping)
        else {}
    )
    runtime_payload = _validate_runtime_tier_intent_for_submit(
        raw_runtime_payload,
        field_name="payload.workflow.runtime",
    )
    steps_payload = task_payload.get("steps")
    if isinstance(steps_payload, Sequence) and not isinstance(steps_payload, (str, bytes)):
        for index, step_payload in enumerate(steps_payload):
            if not isinstance(step_payload, Mapping):
                continue
            step_runtime = step_payload.get("runtime")
            if isinstance(step_runtime, Mapping):
                _validate_runtime_tier_intent_for_submit(
                    step_runtime,
                    field_name=f"payload.workflow.steps[{index}].runtime",
                )
    raw_target_runtime = (
        request_payload.get("targetRuntime")
        or parameter_payload.get("targetRuntime")
        or runtime_payload.get("mode")
        or settings.workflow.default_runtime
        or ""
    )
    canonical_target_runtime: str | None = None
    if raw_target_runtime:
        normalized_rt = normalize_runtime_id(raw_target_runtime)
        if normalized_rt not in _SUPPORTED_TASK_RUNTIMES:
            raise _invalid_workflow_request(
                f"Unsupported targetRuntime: {raw_target_runtime!r}. "
                "Must be one of: codex_cli, claude_code, codex_cloud, jules, omnigent."
            )
        canonical_target_runtime = normalized_rt

    raw_profile_id = None
    for candidate_profile_id in (
        runtime_payload.get("providerProfileRef"),
        runtime_payload.get("profileId"),
        runtime_payload.get("providerProfile"),
        runtime_payload.get("executionProfileRef"),
        task_payload.get("providerProfileRef"),
        task_payload.get("profileId"),
        task_payload.get("providerProfile"),
        parameter_payload.get("providerProfileRef"),
        parameter_payload.get("profileId"),
        parameter_payload.get("providerProfile"),
        request_payload.get("providerProfileRef"),
        request_payload.get("profileId"),
        request_payload.get("providerProfile"),
    ):
        if candidate_profile_id is None:
            continue
        normalized_profile_id = str(candidate_profile_id).strip()
        if normalized_profile_id:
            raw_profile_id = normalized_profile_id
            break
    raw_requested_model: str | None = runtime_payload.get("model") or None
    if raw_requested_model is None:
        raw_requested_model = parameter_payload.get(
            "requestedModel"
        ) or parameter_payload.get("model")
    if raw_requested_model is not None:
        raw_requested_model = str(raw_requested_model)

    provider_profile = None
    session_get = getattr(session, "get", None)
    if raw_profile_id and callable(session_get):
        provider_profile = await session_get(
            ManagedAgentProviderProfile,
            raw_profile_id,
        )
        if provider_profile is None:
            raise _invalid_workflow_request(
                f"Provider profile not found: {raw_profile_id!r}."
            )
    elif _runtime_model_tier(runtime_payload) is not None:
        provider_profile = await _load_default_provider_profile_for_runtime(
            session=session,
            runtime_id=canonical_target_runtime,
        )

    (
        resolved_model,
        model_source,
        resolved_effort,
        model_tier_resolution,
    ) = _resolve_runtime_model_effort(
        runtime_id=canonical_target_runtime,
        profile=provider_profile,
        runtime_payload=runtime_payload,
        requested_model=raw_requested_model,
    )
    metadata: dict[str, Any] = {
        "targetRuntime": canonical_target_runtime,
        "model": resolved_model,
        "requestedModel": raw_requested_model,
        "modelSource": model_source,
    }
    if model_tier_resolution is not None:
        metadata["modelTierResolution"] = model_tier_resolution
    if raw_profile_id:
        metadata["profileId"] = raw_profile_id
    if resolved_effort is not None:
        metadata["effort"] = resolved_effort
    elif "effort" in runtime_payload:
        metadata["effort"] = runtime_payload.get("effort")
    elif "effort" in parameter_payload:
        metadata["effort"] = parameter_payload.get("effort")
    if "modelTier" in runtime_payload:
        metadata["modelTier"] = runtime_payload.get("modelTier")
    if "tierFallback" in runtime_payload:
        metadata["tierFallback"] = runtime_payload.get("tierFallback")
    if isinstance(runtime_payload.get("profileSelector"), Mapping):
        metadata["profileSelector"] = dict(runtime_payload["profileSelector"])
    return metadata


def _stamp_recurring_runtime_metadata(
    initial_parameters: dict[str, Any],
    runtime_metadata: Mapping[str, Any] | None,
) -> None:
    if not runtime_metadata:
        return

    if runtime_metadata.get("targetRuntime"):
        initial_parameters["targetRuntime"] = runtime_metadata["targetRuntime"]
    if runtime_metadata.get("model"):
        initial_parameters["model"] = runtime_metadata["model"]
    if runtime_metadata.get("requestedModel") is not None:
        initial_parameters["requestedModel"] = runtime_metadata["requestedModel"]
    if runtime_metadata.get("modelSource"):
        initial_parameters["modelSource"] = runtime_metadata["modelSource"]
    if runtime_metadata.get("modelTierResolution") is not None:
        initial_parameters["modelTierResolution"] = runtime_metadata[
            "modelTierResolution"
        ]
    if runtime_metadata.get("profileId"):
        initial_parameters["profileId"] = runtime_metadata["profileId"]
    if "effort" in runtime_metadata:
        initial_parameters["effort"] = runtime_metadata.get("effort")
    if "modelTier" in runtime_metadata:
        initial_parameters["modelTier"] = runtime_metadata.get("modelTier")
    if "tierFallback" in runtime_metadata:
        initial_parameters["tierFallback"] = runtime_metadata.get("tierFallback")

    task_key, task_payload = _recurring_workflow_payload(initial_parameters)
    if task_key is None:
        return

    runtime_payload = (
        dict(task_payload.get("runtime"))
        if isinstance(task_payload.get("runtime"), Mapping)
        else {}
    )
    if runtime_metadata.get("targetRuntime"):
        runtime_payload["mode"] = runtime_metadata["targetRuntime"]
    if runtime_metadata.get("model"):
        runtime_payload["model"] = runtime_metadata["model"]
    if runtime_metadata.get("modelTierResolution") is not None:
        runtime_payload["modelTierResolution"] = runtime_metadata[
            "modelTierResolution"
        ]
    if "effort" in runtime_metadata:
        runtime_payload["effort"] = runtime_metadata.get("effort")
    if runtime_metadata.get("profileId"):
        profile_id = runtime_metadata["profileId"]
        runtime_payload["profileId"] = profile_id
        runtime_payload["providerProfile"] = profile_id
        runtime_payload["providerProfileRef"] = profile_id
    if "modelTier" in runtime_metadata:
        runtime_payload["modelTier"] = runtime_metadata.get("modelTier")
    if "tierFallback" in runtime_metadata:
        runtime_payload["tierFallback"] = runtime_metadata.get("tierFallback")
    if isinstance(runtime_metadata.get("profileSelector"), Mapping):
        runtime_payload["profileSelector"] = dict(runtime_metadata["profileSelector"])
    if runtime_payload:
        task_payload["runtime"] = runtime_payload
        initial_parameters[task_key] = task_payload


def _build_recurring_target(
    request_payload: dict[str, Any],
    *,
    runtime_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Transform a workflow request payload into a RecurringWorkflowsService target.

    Constructs the Temporal workflow-start target expected by
    ``RecurringWorkflowsService.create_definition()``.
    """
    target_payload = dict(request_payload)
    target_payload.pop("schedule", None)
    workflow_type = str(
        target_payload.get("workflowType")
        or target_payload.get("workflow_type")
        or "MoonMind.UserWorkflow"
    ).strip()
    has_initial_parameters = (
        "initialParameters" in target_payload or "initial_parameters" in target_payload
    )
    if has_initial_parameters:
        initial_parameters = (
            target_payload.get("initialParameters")
            if "initialParameters" in target_payload
            else target_payload.get("initial_parameters")
        )
        target: dict[str, Any] = {
            "workflowType": workflow_type,
            "initialParameters": (
                dict(initial_parameters)
                if isinstance(initial_parameters, Mapping)
                else initial_parameters
            ),
        }
        if isinstance(target["initialParameters"], dict):
            _stamp_recurring_runtime_metadata(
                target["initialParameters"],
                runtime_metadata,
            )
            if isinstance(target_payload.get("omnigent"), Mapping):
                target["initialParameters"]["omnigent"] = dict(
                    target_payload["omnigent"]
                )
        for source_keys, target_key in (
            (("title",), "title"),
            (("inputArtifactRef", "input_artifact_ref"), "inputArtifactRef"),
            (("planArtifactRef", "plan_artifact_ref"), "planArtifactRef"),
            (
                ("manifestArtifactRef", "manifest_ref", "manifest_artifact_ref"),
                "manifestArtifactRef",
            ),
            (("failurePolicy", "failure_policy"), "failurePolicy"),
        ):
            value = _first_mapping_value(target_payload, source_keys)
            if value is not None:
                target[target_key] = value
        return target

    root_propose_tasks = target_payload.pop("proposeTasks", None)
    root_proposal_policy = target_payload.pop("proposalPolicy", None)
    task_key, task_payload = _recurring_workflow_payload(target_payload)
    if task_key is not None:
        propose_tasks_value = (
            task_payload["proposeTasks"]
            if "proposeTasks" in task_payload
            else root_propose_tasks
        )
        proposal_policy_value = (
            task_payload["proposalPolicy"]
            if "proposalPolicy" in task_payload
            else root_proposal_policy
        )
        task_payload["proposeTasks"] = _coerce_bool(
            propose_tasks_value,
            default=False,
        )
        normalized_proposal_policy = _normalize_workflow_proposal_policy(
            proposal_policy_value
        )
        if normalized_proposal_policy is not None:
            task_payload["proposalPolicy"] = normalized_proposal_policy
        else:
            task_payload.pop("proposalPolicy", None)
        target_payload[task_key] = task_payload
    _stamp_recurring_runtime_metadata(target_payload, runtime_metadata)
    return {
        "workflowType": workflow_type,
        "title": str(target_payload.get("title") or "").strip() or None,
        "initialParameters": target_payload,
        "inputArtifactRef": _first_mapping_value(
            target_payload,
            ("inputArtifactRef", "input_artifact_ref"),
        ),
        "planArtifactRef": _first_mapping_value(
            target_payload,
            ("planArtifactRef", "plan_artifact_ref"),
        ),
        "manifestArtifactRef": _first_mapping_value(
            target_payload,
            ("manifestArtifactRef", "manifest_ref", "manifest_artifact_ref"),
        ),
        "failurePolicy": _first_mapping_value(
            target_payload,
            ("failurePolicy", "failure_policy"),
        ),
    }


async def _handle_recurring_schedule(
    *,
    schedule: ScheduleParameters,
    request_payload: dict[str, Any],
    user: User,
    session: Any,
) -> ScheduleCreatedResponse:
    """Delegate recurring schedule creation to RecurringWorkflowsService."""
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowValidationError,
        RecurringWorkflowsService,
    )

    scope_type = schedule.scope_type or "personal"
    if scope_type == "global" and not _is_execution_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "operator_role_required",
                "message": "Operator privileges are required for global schedules.",
            },
        )
    svc = RecurringWorkflowsService(session)
    runtime_metadata = await _resolve_recurring_runtime_metadata(
        request_payload,
        session=session,
    )
    target = _build_recurring_target(
        request_payload,
        runtime_metadata=runtime_metadata,
    )
    try:
        definition = await svc.create_definition(
            name=schedule.name or "Inline schedule",
            description=schedule.description,
            enabled=schedule.enabled,
            schedule_type="cron",
            cron=schedule.cron or "",
            timezone=schedule.timezone or "UTC",
            scope_type=scope_type,
            scope_ref=None,
            owner_user_id=user.id,
            target=target,
            policy=schedule.policy,
        )
    except RecurringWorkflowValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_recurring_workflow",
                "message": str(exc),
            },
        ) from exc
    return ScheduleCreatedResponse(
        definitionId=str(definition.id),
        name=definition.name,
        cron=definition.cron,
        timezone=definition.timezone,
        nextRunAt=definition.next_run_at,
        redirectPath=f"/schedules/{definition.id}",
    )

class _ScheduleRouteResult:
    """Result of ``_resolve_schedule_routing``."""

    __slots__ = ("start_delay", "scheduled_for", "recurring_response")

    def __init__(
        self,
        *,
        start_delay: timedelta | None = None,
        scheduled_for: datetime | None = None,
        recurring_response: ScheduleCreatedResponse | None = None,
    ) -> None:
        self.start_delay = start_delay
        self.scheduled_for = scheduled_for
        self.recurring_response = recurring_response

async def _resolve_schedule_routing(
    schedule: ScheduleParameters | None,
    *,
    request_payload: dict[str, Any],
    user: User,
    session: Any | None,
) -> _ScheduleRouteResult:
    """Shared schedule routing for both Temporal and task-shaped requests.

    Returns a ``_ScheduleRouteResult`` with either:
    * ``recurring_response`` populated (caller should return it),
    * ``start_delay`` / ``scheduled_for`` populated (caller passes to service), or
    * all ``None`` (immediate execution, no schedule).
    """
    if schedule is None:
        return _ScheduleRouteResult()

    if schedule.mode == "recurring":
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_execution_request",
                    "message": "Recurring schedules require a database session.",
                },
            )
        response = await _handle_recurring_schedule(
            schedule=schedule,
            request_payload=request_payload,
            user=user,
            session=session,
        )
        return _ScheduleRouteResult(recurring_response=response)

    if schedule.mode == "once":
        if schedule.scheduled_for is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_execution_request",
                    "message": "scheduledFor is required when schedule.mode is 'once'.",
                },
            )
        scheduled_for_dt = schedule.scheduled_for
        delay = _compute_schedule_delay(scheduled_for_dt)
        return _ScheduleRouteResult(
            start_delay=delay,
            scheduled_for=scheduled_for_dt,
        )

    return _ScheduleRouteResult()

@router.post(
    "/{workflow_id}/remediation",
    response_model=ExecutionModel | ScheduleCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_remediation_execution(
    workflow_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
    _submit_enabled: None = Depends(_ensure_submit_enabled),
    principal_context: dict[str, Any] = Depends(execution_principal_dependency),
) -> ExecutionModel | ScheduleCreatedResponse:
    body = payload if isinstance(payload, dict) else {}
    task_payload = (
        dict(body.get("workflow")) if isinstance(body.get("workflow"), Mapping) else {}
    )

    instructions = str(
        body.get("instructions") or task_payload.get("instructions") or ""
    ).strip()
    if instructions:
        task_payload["instructions"] = instructions

    runtime_payload = body.get("runtime")
    if isinstance(runtime_payload, Mapping):
        task_payload["runtime"] = dict(runtime_payload)

    if "remediation" in body:
        remediation_payload = body.get("remediation")
        if not isinstance(remediation_payload, Mapping):
            raise _invalid_workflow_request("workflow.remediation must be an object")
    else:
        remediation_payload = task_payload.get("remediation")
        if remediation_payload is not None and not isinstance(
            remediation_payload, Mapping
        ):
            raise _invalid_workflow_request("workflow.remediation must be an object")
    remediation = (
        dict(remediation_payload) if isinstance(remediation_payload, Mapping) else {}
    )
    target = (
        dict(remediation.get("target"))
        if isinstance(remediation.get("target"), Mapping)
        else {}
    )
    target["workflowId"] = workflow_id
    remediation["target"] = target
    task_payload["remediation"] = remediation

    request_payload: dict[str, Any] = {
        "repository": body.get("repository"),
        "integration": body.get("integration"),
        "requiredCapabilities": body.get("requiredCapabilities"),
        "workflow": task_payload,
    }
    for key in (
        "dependsOn",
        "inputArtifactRef",
        "planArtifactRef",
        "manifestArtifactRef",
        "targetRuntime",
        "publish",
        "publishMode",
        "publish_mode",
        "profileId",
        "providerProfile",
        "idempotencyKey",
        "schedule",
        "runtimeInheritance",
        "parentWorkflowId",
    ):
        if key in body:
            request_payload[key] = body[key]

    request_data: dict[str, Any] = {"type": "workflow", "payload": request_payload}
    if "priority" in body:
        request_data["priority"] = body["priority"]
    if "maxAttempts" in body:
        request_data["maxAttempts"] = body["maxAttempts"]
    elif "max_attempts" in body:
        request_data["maxAttempts"] = body["max_attempts"]
    if "schedule" in body:
        request_data["schedule"] = body["schedule"]
    request = CreateJobRequest.model_validate(request_data)
    return await _create_execution_from_workflow_request(
        request=request,
        service=service,
        user=user,
        session=session,
        principal_context=principal_context,
    )

def _bounded_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list | tuple | set):
        return None
    items = [str(item) for item in value if item is not None]
    return items or None

def _bounded_live_observation(value: Any) -> RemediationLiveObservationModel | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "status",
        "label",
        "sequenceCursor",
        "reconnectState",
        "epoch",
        "fallbackReason",
    }
    bounded = {key: val for key in allowed if (val := value.get(key)) is not None}
    return RemediationLiveObservationModel.model_validate(bounded) if bounded else None

def _bounded_lock_outcome(value: Any) -> RemediationLockOutcomeModel | None:
    if not isinstance(value, dict):
        return None
    allowed = {"state", "holder", "releasedAt"}
    bounded = {key: value.get(key) for key in allowed if key in value}
    return RemediationLockOutcomeModel.model_validate(bounded) if bounded else None

def _bounded_checkpoint_branch_links(
    value: Any,
) -> list[RemediationCheckpointBranchLinkModel]:
    if not isinstance(value, list | tuple):
        return []
    links: list[RemediationCheckpointBranchLinkModel] = []
    for item in value[:10]:
        if not isinstance(item, Mapping):
            continue
        try:
            links.append(RemediationCheckpointBranchLinkModel.model_validate(item))
        except ValidationError:
            continue
    return links

def _remediation_approval_state_from_link(
    link: Any,
    *,
    authority_mode: str,
    status_value: str,
) -> RemediationApprovalStateModel | None:
    raw_state = getattr(link, "approval_state", None)
    if isinstance(raw_state, dict):
        return RemediationApprovalStateModel.model_validate(raw_state)

    approval_pending = status_value in _PENDING_REMEDIATION_APPROVAL_STATUSES
    if authority_mode != "approval_gated":
        return None
    return RemediationApprovalStateModel(
        requestId=(
            _remediation_approval_request_id(
                str(getattr(link, "remediation_workflow_id", ""))
            )
            if approval_pending
            else None
        ),
        decision=("pending" if approval_pending else "not_required"),
        canDecide=approval_pending,
    )

def _serialize_remediation_link_summary(link: Any) -> RemediationLinkSummaryModel:
    authority_mode = str(getattr(link, "authority_mode", "") or "")
    status_value = str(getattr(link, "status", "") or "")
    approval_state = _remediation_approval_state_from_link(
        link,
        authority_mode=authority_mode,
        status_value=status_value,
    )

    return RemediationLinkSummaryModel(
        remediationWorkflowId=str(getattr(link, "remediation_workflow_id", "")),
        remediationRunId=str(getattr(link, "remediation_run_id", "")),
        targetWorkflowId=str(getattr(link, "target_workflow_id", "")),
        targetRunId=str(getattr(link, "target_run_id", "")),
        mode=str(getattr(link, "mode", "")),
        authorityMode=authority_mode,
        status=status_value,
        activeLockScope=getattr(link, "active_lock_scope", None),
        activeLockHolder=getattr(link, "active_lock_holder", None),
        latestActionSummary=getattr(link, "latest_action_summary", None),
        resolution=getattr(link, "outcome", None),
        contextArtifactRef=getattr(link, "context_artifact_ref", None),
        selectedSteps=_bounded_string_list(getattr(link, "selected_steps", None)),
        currentTargetState=getattr(link, "current_target_state", None),
        allowedActions=_bounded_string_list(getattr(link, "allowed_actions", None)),
        evidenceDegraded=getattr(link, "evidence_degraded", None),
        unavailableEvidenceClasses=_bounded_string_list(
            getattr(link, "unavailable_evidence_classes", None)
        ),
        liveObservation=_bounded_live_observation(
            getattr(link, "live_observation", None)
        ),
        lockOutcome=_bounded_lock_outcome(getattr(link, "lock_outcome", None)),
        approvalState=approval_state,
        checkpointBranches=_bounded_checkpoint_branch_links(
            getattr(link, "checkpoint_branch_links", None)
        ),
        createdAt=getattr(link, "created_at", None),
        updatedAt=getattr(link, "updated_at", None),
    )

def _remediation_approval_request_id(remediation_workflow_id: str) -> str:
    return f"{remediation_workflow_id}:approval"

def _context_selected_checkpoint(
    context_payload: Mapping[str, Any],
    checkpoint_ref: str,
) -> Mapping[str, Any] | None:
    def _artifact_ref_value(value: Any) -> str:
        if isinstance(value, Mapping):
            value = value.get("ref") or value.get("artifactRef") or value
        return str(value or "").strip()

    selected_steps = context_payload.get("selectedSteps")
    if not isinstance(selected_steps, list):
        return None
    for item in selected_steps:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("checkpointRef") or "").strip() == checkpoint_ref:
            return item
        artifact_refs = item.get("artifactRefs")
        if isinstance(artifact_refs, list) and checkpoint_ref in {
            _artifact_ref_value(ref) for ref in artifact_refs
        }:
            return item
    return None

def _checkpoint_summary_for_ref(
    record: Any,
    checkpoint_ref: str,
) -> CheckpointSummaryModel | None:
    for checkpoint in _checkpoint_summaries_from_record(record):
        if checkpoint.checkpoint_ref == checkpoint_ref:
            return checkpoint
    return None


async def _read_remediation_context_payload(
    *,
    session: AsyncSession,
    context_artifact_ref: str,
    target_workflow_id: str,
    target_run_id: str,
    principal: str,
) -> dict[str, Any]:
    artifact_service = get_temporal_artifact_service(session)
    artifact, body = await artifact_service.read(
        artifact_id=context_artifact_ref,
        principal=principal,
    )
    metadata = artifact.metadata_json if isinstance(artifact.metadata_json, Mapping) else {}
    if metadata.get("artifact_type") != "remediation.context":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_remediation_context",
                "reason": "artifact_type_mismatch",
            },
        )
    if body is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_remediation_context",
                "reason": "empty_body",
            },
        )
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_remediation_context",
                "reason": "invalid_json",
            },
        ) from exc
    if not isinstance(decoded, dict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_remediation_context",
                "reason": "payload_not_object",
            },
        )
    target = decoded.get("target")
    target_mapping = target if isinstance(target, Mapping) else {}
    if (
        target_mapping.get("workflowId") != target_workflow_id
        or target_mapping.get("runId") != target_run_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_remediation_context",
                "reason": "target_mismatch",
            },
        )
    return decoded

@router.get("/remediations", response_model=RemediationCollectionResponseModel)
async def list_remediation_collection(
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RemediationCollectionResponseModel:
    """List only remediation relationships whose two executions are visible."""
    async def build_item(link: Any) -> RemediationCollectionItemModel | None:
        try:
            remediation, target = await asyncio.gather(
                _get_owned_execution(
                    service=service,
                    workflow_id=link.remediation_workflow_id,
                    user=user,
                ),
                _get_owned_execution(
                    service=service,
                    workflow_id=link.target_workflow_id,
                    user=user,
                ),
            )
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                return None
            raise
        return RemediationCollectionItemModel(
            remediationWorkflowId=link.remediation_workflow_id,
            title=str(
                (getattr(remediation, "memo", None) or {}).get("title")
                or link.remediation_workflow_id
            ),
            status=str(link.status),
            attentionRequired=bool(
                getattr(remediation, "attention_required", False)
            ),
            targetWorkflowId=link.target_workflow_id,
            targetTitle=str(
                (getattr(target, "memo", None) or {}).get("title")
                or link.target_workflow_id
            ),
            authorityMode=link.authority_mode,
            mode=link.mode,
            latestActionSummary=link.latest_action_summary,
            resolution=link.outcome,
            createdAt=link.created_at,
            updatedAt=link.updated_at,
        )

    results = await asyncio.gather(
        *(build_item(link) for link in await service.list_remediation_links())
    )
    items = [item for item in results if item is not None]
    return RemediationCollectionResponseModel(items=items)

@router.get(
    "/{workflow_id}/remediations",
    response_model=RemediationLinksResponseModel,
)
async def list_execution_remediations(
    workflow_id: str,
    direction: str = Query("inbound"),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RemediationLinksResponseModel:
    if direction not in {"inbound", "outbound"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_remediation_direction",
                "message": "direction must be 'inbound' or 'outbound'.",
            },
        )

    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    if direction == "inbound":
        links = await service.list_remediations_for_target(workflow_id)
    else:
        links = await service.list_remediation_targets(workflow_id)

    return RemediationLinksResponseModel(
        direction=direction,
        items=[_serialize_remediation_link_summary(link) for link in links],
    )

@router.post(
    "/{workflow_id}/remediation/checkpoint-branches",
    response_model=CheckpointBranchModel,
    status_code=status.HTTP_201_CREATED,
)
async def create_remediation_checkpoint_branch(
    workflow_id: str,
    payload: RemediationCheckpointBranchRepairRequest,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> CheckpointBranchModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    links = await service.list_remediation_targets(workflow_id)
    link = links[0] if links else None
    if link is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "remediation_link_not_found"},
        )
    if not link.context_artifact_ref:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "remediation_context_missing"},
        )
    target_record = await _get_owned_execution(
        service=service, workflow_id=link.target_workflow_id, user=user
    )
    if target_record.run_id != link.target_run_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "stale_remediation_target",
                "message": "Remediation target run no longer matches the pinned run.",
            },
        )
    checkpoint_ref = str(payload.checkpointRef or "").strip()
    if not checkpoint_ref.startswith("artifact://"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_checkpoint_ref",
                "message": "checkpointRef must be an artifact ref.",
            },
        )
    context_payload = await _read_remediation_context_payload(
        session=session,
        context_artifact_ref=link.context_artifact_ref,
        target_workflow_id=link.target_workflow_id,
        target_run_id=link.target_run_id,
        principal=_owner_id(user),
    )
    selected_checkpoint = _context_selected_checkpoint(context_payload, checkpoint_ref)
    if selected_checkpoint is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "checkpoint_invalidity",
                "reason": "checkpoint_not_selected_in_remediation_context",
            },
        )
    target_checkpoint = _checkpoint_summary_for_ref(target_record, checkpoint_ref)
    workspace_policy = payload.workspacePolicy
    runtime_context_policy = payload.runtimeContextPolicy
    _validate_branch_policy(
        workspace_policy=workspace_policy,
        runtime_context_policy=runtime_context_policy,
        publish_mode="none",
        git_work_branch=payload.gitWorkBranch,
        max_budget_usd=payload.maxBudgetUsd,
    )
    source = CheckpointBranchApiSourceModel.model_validate(
        {
            "workflowId": link.target_workflow_id,
            "runId": link.target_run_id,
            "logicalStepId": selected_checkpoint.get("logicalStepId"),
            "executionOrdinal": selected_checkpoint.get("executionOrdinal"),
            "checkpointBoundary": selected_checkpoint.get("checkpointBoundary")
            or (
                target_checkpoint.checkpoint_boundary
                if target_checkpoint is not None
                else None
            )
            or "after_execution",
            "checkpointRef": checkpoint_ref,
            "checkpointDigest": selected_checkpoint.get("checkpointDigest")
            or (
                target_checkpoint.checkpoint_digest
                if target_checkpoint is not None
                else None
            ),
        }
    )
    _validate_branch_source(
        workflow_id=link.target_workflow_id,
        record=target_record,
        source=source,
    )
    request_digest = _operation_digest(
        {
            "remediationWorkflowId": workflow_id,
            "contextArtifactRef": link.context_artifact_ref,
            "checkpointRef": checkpoint_ref,
            "instructions": payload.instructions.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
            "workspacePolicy": workspace_policy,
            "runtimeContextPolicy": runtime_context_policy,
            "gitWorkBranch": payload.gitWorkBranch,
            "maxBudgetUsd": payload.maxBudgetUsd,
        }
    )
    existing_op = await session.execute(
        select(WorkflowCheckpointBranchOperation).where(
            WorkflowCheckpointBranchOperation.workflow_id == link.target_workflow_id,
            WorkflowCheckpointBranchOperation.idempotency_key == payload.idempotencyKey,
        )
    )
    operation = existing_op.scalar_one_or_none()
    if operation is not None:
        if (
            operation.operation != "checkpoint_branch.create"
            or operation.request_digest != request_digest
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "idempotency_key_conflict"},
            )
        branch = await _load_checkpoint_branch(
            session,
            workflow_id=link.target_workflow_id,
            branch_id=str(operation.branch_id),
        )
        return _branch_to_model(branch)

    instruction_ref, instruction_digest = _instruction_identity(payload.instructions)
    branch_id = _new_checkpoint_branch_id()
    branch_turn_id = _new_checkpoint_branch_turn_id()
    await _prepare_checkpoint_branch_launch(
        session=session,
        record=target_record,
        workflow_id=link.target_workflow_id,
        branch_id=branch_id,
        branch_turn_id=branch_turn_id,
        source_checkpoint_ref=checkpoint_ref,
        source_checkpoint_digest=source.checkpoint_digest,
        logical_step_id=source.logical_step_id,
        label=payload.label or "Remediation checkpoint branch",
        workspace_policy=workspace_policy,
        runtime_context_policy=runtime_context_policy,
        idempotency_key=payload.idempotencyKey,
        instruction_ref=instruction_ref,
        instruction_digest=instruction_digest,
        requested_work_branch=payload.gitWorkBranch,
        source_run_id=link.target_run_id,
        source_execution_ordinal=source.execution_ordinal,
        source_checkpoint_boundary=source.checkpoint_boundary,
    )
    branch = await session.get(WorkflowCheckpointBranch, branch_id)
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_binding", "reason": "branch_prepare_failed"},
        )
    branch.state = "created"
    branch.branch_kind = "root"
    branch.current_head_checkpoint_ref = checkpoint_ref
    branch.publish_status = "unpublished"
    branch.idempotency_key = payload.idempotencyKey
    branch.created_by = getattr(user, "email", None) or _owner_id(user)
    branch.diagnostics = {
        **(branch.diagnostics or {}),
        "remediationWorkflowId": workflow_id,
        "remediationContextRef": link.context_artifact_ref,
        "repairActionKind": "checkpoint_branch.create_from_remediation_context",
    }
    session.add(
        WorkflowCheckpointBranchOperation(
            workflow_id=link.target_workflow_id,
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
            operation="checkpoint_branch.create",
            idempotency_key=payload.idempotencyKey,
            request_digest=request_digest,
            response_payload={
                "branchId": branch_id,
                "branchTurnId": branch_turn_id,
                "checkpointRef": checkpoint_ref,
                "remediation": {
                    "workflowId": workflow_id,
                    "runId": link.remediation_run_id,
                    "contextArtifactRef": link.context_artifact_ref,
                    "checkpointRef": checkpoint_ref,
                    "actionKind": "checkpoint_branch.create_from_remediation_context",
                    "runtimeContextPolicy": runtime_context_policy,
                },
            },
        )
    )
    link.latest_action_summary = f"Created checkpoint branch {branch_id}."
    await session.commit()
    await session.refresh(branch)
    return _branch_to_model(branch)

@router.post(
    "/{workflow_id}/remediation/approvals/{request_id}",
    response_model=RemediationApprovalDecisionResponse,
)
async def record_remediation_approval_decision(
    workflow_id: str,
    request_id: str,
    payload: RemediationApprovalDecisionRequest,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RemediationApprovalDecisionResponse:
    if payload.decision not in {"approved", "rejected"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_remediation_approval_decision",
                "message": "decision must be 'approved' or 'rejected'.",
            },
        )
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    try:
        result = await service.record_remediation_approval_decision(
            remediation_workflow_id=workflow_id,
            request_id=request_id,
            decision=payload.decision,
            comment=payload.comment,
            actor=getattr(user, "email", None) or str(getattr(user, "id", "")),
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_remediation_approval_decision",
                "message": str(exc),
            },
        ) from exc
    return RemediationApprovalDecisionResponse.model_validate(result)

@router.post("", response_model=ExecutionModel | ScheduleCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_execution(
    payload: dict[str, Any] = Body(...),
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
    _submit_enabled: None = Depends(_ensure_submit_enabled),
    principal_context: dict[str, Any] = Depends(execution_principal_dependency),
) -> ExecutionModel | ScheduleCreatedResponse:
    from moonmind.config.settings import settings

    if not settings.temporal_dashboard.submit_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "temporal_submit_disabled",
                "message": "Temporal workflow submission is disabled (temporal_dashboard.submit_enabled=False). "
                "The legacy queue execution substrate is no longer supported. "
                "Enable Temporal submission to proceed.",
            },
        )

    try:
        if "type" in payload and "payload" in payload:
            request = CreateJobRequest.model_validate(payload)
            return await _create_execution_from_workflow_request(
                request=request,
                service=service,
                user=user,
                session=session,
                principal_context=principal_context,
            )

        request = CreateExecutionRequest.model_validate(payload)

        # --- Schedule routing ---
        route = await _resolve_schedule_routing(
            request.schedule,
            request_payload=payload,
            user=user,
            session=session,
        )
        if route.recurring_response is not None:
            return route.recurring_response

        skill_validation = await validate_skill_step_inputs(
            initial_parameters=request.initial_parameters,
            session=session,
        )
        if not skill_validation.valid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "invalid_skill_step_inputs",
                    "message": "Skill step inputs failed validation.",
                    "errors": skill_validation.error_dicts(),
                },
            )

        record = await service.create_execution(
            workflow_type=request.workflow_type,
            owner_id=user.id,
            owner_type="user",
            title=request.title,
            input_artifact_ref=request.input_artifact_ref,
            plan_artifact_ref=request.plan_artifact_ref,
            manifest_artifact_ref=request.manifest_artifact_ref,
            failure_policy=request.failure_policy,
            initial_parameters=skill_validation.parameters,
            idempotency_key=request.idempotency_key,
            start_delay=route.start_delay,
            scheduled_for=route.scheduled_for,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_request",
                "message": str(exc),
            },
        ) from exc
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_request",
                "message": str(exc),
            },
        ) from exc

    snapshot_ref = await _persist_original_workflow_input_snapshot_from_parameters(
        session=session,
        record=record,
        user=user,
        parameters=dict(getattr(record, "parameters", None) or {}),
        source_kind="create",
        input_artifact_ref=getattr(record, "input_ref", None),
    )
    if snapshot_ref:
        await session.commit()
        await session.refresh(record)

    return _serialize_execution(record, user=user)

@router.get("", response_model=ExecutionListResponse)
async def list_executions(
    *,
    request: Request,
    workflow_type: Optional[str] = Query(None, alias="workflowType"),
    owner_type: Optional[str] = Query(None, alias="ownerType"),
    state: Optional[str] = Query(None, alias="state"),
    state_in: Optional[str] = Query(None, alias="stateIn"),
    state_not_in: Optional[str] = Query(None, alias="stateNotIn"),
    owner_id: Optional[str] = Query(None, alias="ownerId"),
    entry: Optional[str] = Query(None, alias="entry"),
    repo: Optional[str] = Query(None, alias="repo"),
    repo_exact: Optional[str] = Query(None, alias="repoExact"),
    repo_in: Optional[str] = Query(None, alias="repoIn"),
    repo_not_in: Optional[str] = Query(None, alias="repoNotIn"),
    integration: Optional[str] = Query(None, alias="integration"),
    target_runtime: Optional[str] = Query(None, alias="targetRuntime"),
    target_runtime_in: Optional[str] = Query(None, alias="targetRuntimeIn"),
    target_runtime_not_in: Optional[str] = Query(None, alias="targetRuntimeNotIn"),
    target_skill_in: Optional[str] = Query(None, alias="targetSkillIn"),
    target_skill_not_in: Optional[str] = Query(None, alias="targetSkillNotIn"),
    scheduled_from: Optional[str] = Query(None, alias="scheduledFrom"),
    scheduled_to: Optional[str] = Query(None, alias="scheduledTo"),
    scheduled_blank: Optional[str] = Query(None, alias="scheduledBlank"),
    updated_from: Optional[str] = Query(None, alias="updatedFrom"),
    updated_to: Optional[str] = Query(None, alias="updatedTo"),
    created_from: Optional[str] = Query(None, alias="createdFrom"),
    created_to: Optional[str] = Query(None, alias="createdTo"),
    finished_from: Optional[str] = Query(None, alias="finishedFrom"),
    finished_to: Optional[str] = Query(None, alias="finishedTo"),
    finished_blank: Optional[str] = Query(None, alias="finishedBlank"),
    scope: Optional[str] = Query(None, alias="scope"),
    sort: Optional[str] = Query(None, alias="sort"),
    sort_dir: Optional[str] = Query(None, alias="sortDir"),
    page_size: int = Query(50, alias="pageSize", ge=1, le=200),
    next_page_token: Optional[str] = Query(None, alias="nextPageToken"),
    source: Optional[str] = Query(None),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
    temporal_client: Client = Depends(get_temporal_client),
) -> ExecutionListResponse:
    effective_owner_type, effective_owner = _effective_execution_owner_scope(
        user=user,
        owner_type=owner_type,
        owner_id=owner_id,
    )
    usable_search_attributes: frozenset[str] = frozenset()

    if source == "temporal":
        try:
            from api_service.core.sync import (
                map_temporal_state_to_projection,
                merged_parameters_for_projection,
            )

            client = temporal_client
            usable_search_attributes = await _detect_optional_temporal_search_attributes(client)
            unavailable_filter_aliases = _requested_unavailable_filter_aliases(
                request,
                usable_search_attributes,
            )
            if unavailable_filter_aliases:
                logger.info(
                    "Skipping Temporal execution list query because filters require "
                    "unregistered Search Attributes: %s",
                    ", ".join(sorted(unavailable_filter_aliases)),
                )
                return ExecutionListResponse(
                    items=[],
                    nextPageToken=None,
                    count=None,
                    countMode="estimated_or_unknown",
                    staleState=False,
                    degradedCount=True,
                    refreshedAt=datetime.now(UTC),
                )
            count_query, list_query = _build_temporal_execution_query(
                request=request,
                workflow_type=workflow_type,
                state=state,
                state_in=state_in,
                state_not_in=state_not_in,
                entry=entry,
                repo=repo,
                repo_exact=repo_exact,
                repo_in=repo_in,
                repo_not_in=repo_not_in,
                integration=integration,
                target_runtime=target_runtime,
                target_runtime_in=target_runtime_in,
                target_runtime_not_in=target_runtime_not_in,
                target_skill_in=target_skill_in,
                target_skill_not_in=target_skill_not_in,
                scheduled_from=scheduled_from,
                scheduled_to=scheduled_to,
                scheduled_blank=scheduled_blank,
                updated_from=updated_from,
                updated_to=updated_to,
                created_from=created_from,
                created_to=created_to,
                finished_from=finished_from,
                finished_to=finished_to,
                finished_blank=finished_blank,
                scope=scope,
                owner_type=effective_owner_type,
                owner_id=effective_owner,
                sort=sort,
                sort_dir=sort_dir,
                include_order=True,
                usable_search_attributes=usable_search_attributes,
            )

            import base64
            token_bytes = base64.b64decode(next_page_token) if next_page_token else None

            iterator = client.list_workflows(
                query=list_query,
                page_size=page_size,
                next_page_token=token_bytes,
            )
            await iterator.fetch_next_page()

            count_value: int | None = None
            count_mode = "exact"
            degraded_count = False
            try:
                count_info = await asyncio.wait_for(
                    client.count_workflows(query=count_query),
                    timeout=settings.temporal_dashboard.list_count_timeout_seconds,
                )
                count_value = count_info.count
            except Exception as exc:
                count_mode = "estimated_or_unknown"
                degraded_count = True
                logger.warning(
                    "Temporal execution list count degraded for query_present=%s: %s",
                    bool(count_query),
                    exc,
                )

            page = iterator.current_page or []
            canonical_map: dict[str, TemporalExecutionCanonicalRecord] = {}
            if page:
                workflow_ids = [wf.id for wf in page]
                stmt = select(TemporalExecutionCanonicalRecord).where(
                    TemporalExecutionCanonicalRecord.workflow_id.in_(workflow_ids)
                )
                canonical_rows = (await session.execute(stmt)).scalars().all()
                canonical_map = {row.workflow_id: row for row in canonical_rows}

            items: list[ExecutionModel] = []
            if page:
                live_progress_queries: list[tuple[int, str]] = []
                for wf in page:
                    payload = await map_temporal_state_to_projection(wf)
                    canonical_record = canonical_map.get(wf.id)
                    payload["parameters"] = merged_parameters_for_projection(
                        payload, canonical_record
                    )
                    if (
                        payload.get("scheduled_for") is None
                        and canonical_record is not None
                    ):
                        payload["scheduled_for"] = getattr(
                            canonical_record, "scheduled_for", None
                        )
                    if (
                        payload.get("state") is MoonMindWorkflowState.SCHEDULED
                        and payload.get("scheduled_for") is not None
                    ):
                        payload["started_at"] = None
                    # We need a record-like object for serialization
                    from types import SimpleNamespace

                    record_obj = SimpleNamespace(**payload)
                    if (
                        not hasattr(record_obj, "updated_at")
                        or record_obj.updated_at is None
                    ):
                        record_obj.updated_at = (
                            getattr(record_obj, "started_at", None) or datetime.now(UTC)
                        )
                    execution = _serialize_execution_list_item(record_obj)
                    if _execution_uses_live_workflow_queries(execution):
                        live_progress_queries.append((len(items), wf.id))
                    items.append(execution)

                if live_progress_queries:
                    progress_results = await asyncio.gather(
                        *(
                            _load_execution_progress(
                                temporal_client=temporal_client,
                                workflow_id=workflow_id,
                            )
                            for _, workflow_id in live_progress_queries
                        )
                    )
                    for (item_index, _workflow_id), (
                        progress,
                        queried_run_id,
                    ) in zip(live_progress_queries, progress_results, strict=True):
                        update: dict[str, object] = {"progress": progress}
                        if queried_run_id:
                            update["run_id"] = queried_run_id
                            update["temporal_run_id"] = queried_run_id
                        items[item_index] = items[item_index].model_copy(update=update)

                if _request_has_progress_filters(request):
                    items = [
                        item
                        for item in items
                        if _execution_matches_progress_request(item, request)
                    ]

                if sort in {"progress", "progressPct"}:
                    items = _sort_executions_by_progress(
                        items,
                        direction=str(sort_dir or "desc").strip().lower(),
                    )
                else:
                    items.sort(
                        key=lambda item: (
                            (
                                0
                                if item.state == MoonMindWorkflowState.SCHEDULED.value
                                else 1
                            ),
                            (
                                -item.scheduled_for.timestamp()
                                if item.state == MoonMindWorkflowState.SCHEDULED.value
                                and item.scheduled_for is not None
                                else float("inf")
                            ),
                            -_execution_updated_sort_bucket(item),
                            -_execution_queued_sort_timestamp(item),
                            item.workflow_id,
                        )
                    )

            new_token_str = None
            if iterator.next_page_token:
                new_token_str = base64.b64encode(iterator.next_page_token).decode("utf-8")

            return ExecutionListResponse(
                items=items,
                next_page_token=new_token_str,
                count=count_value,
                count_mode=count_mode,
                degraded_count=degraded_count,
                refreshed_at=datetime.now(UTC),
            )
        except TemporalExecutionValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "invalid_execution_query",
                    "message": str(exc),
                },
            ) from exc
        except RPCError as exc:
            logger.warning(
                "Failed to list Temporal executions directly: %s", exc, exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "temporal_unavailable",
                    "message": "Temporal service unavailable.",
                },
            ) from exc

    try:
        result = await service.list_executions(
            workflow_type=workflow_type,
            state=state,
            entry=entry,
            owner_type=effective_owner_type,
            owner_id=effective_owner,
            repo=repo,
            integration=integration,
            page_size=page_size,
            next_page_token=next_page_token,
        )

        if settings.temporal.temporal_authoritative_read_enabled and result.items:
            from api_service.core.sync import sync_temporal_executions_safely

            try:
                client = temporal_client
                result.items = await sync_temporal_executions_safely(
                    session, result.items, client
                )
            except Exception as exc:
                logger.warning(
                    "Failed to sync executions from Temporal: %s", exc, exc_info=True
                )

    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_query",
                "message": str(exc),
            },
        ) from exc

    now = datetime.now(UTC)
    return ExecutionListResponse(
        items=[
            _serialize_execution_list_item(item)
            for item in result.items
        ],
        next_page_token=result.next_page_token,
        count=result.count,
        count_mode="exact",
        degraded_count=False,
        refreshed_at=max(
            (_compatibility_refreshed_at(item, now=now) for item in result.items),
            default=None,
        ),
    )

def _coerce_metric_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        candidate = float(value)
    elif isinstance(value, str):
        text = value.strip().replace("$", "").replace(",", "")
        if not text:
            return None
        try:
            candidate = float(text)
        except ValueError:
            return None
    else:
        return None
    if candidate < 0:
        return None
    return candidate


def _extract_cost_estimate_usd(*payloads: Any) -> float | None:
    stack = [(payload, False) for payload in payloads]
    while stack:
        value, cost_context = stack.pop()
        if cost_context:
            candidate = _coerce_metric_float(value)
            if candidate is not None:
                return candidate
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key) in _EXECUTION_COST_KEYS:
                    candidate = _coerce_metric_float(nested)
                    if candidate is not None:
                        return candidate
                    if isinstance(nested, dict | list | tuple):
                        stack.append((nested, True))
                elif isinstance(nested, dict | list | tuple):
                    stack.append((nested, False))
        elif isinstance(value, (list, tuple)):
            stack.extend((nested, cost_context) for nested in value)
    return None


def _duration_seconds_from_payload(payload: Mapping[str, Any]) -> float | None:
    closed_at = payload.get("closed_at")
    started_at = payload.get("started_at") or payload.get("created_at")
    if not isinstance(closed_at, datetime) or not isinstance(started_at, datetime):
        return None
    duration = (closed_at - started_at).total_seconds()
    return duration if duration >= 0 else None


def _duration_metrics(values: list[float]) -> ExecutionMetricsDurationModel:
    if not values:
        return ExecutionMetricsDurationModel()
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[midpoint]
    else:
        median = (ordered[midpoint - 1] + ordered[midpoint]) / 2
    return ExecutionMetricsDurationModel(
        averageSeconds=sum(ordered) / len(ordered),
        medianSeconds=median,
        minSeconds=ordered[0],
        maxSeconds=ordered[-1],
        observedCount=len(ordered),
    )


def _cost_metrics(values: list[float]) -> ExecutionMetricsCostModel:
    if not values:
        return ExecutionMetricsCostModel()
    total = sum(values)
    return ExecutionMetricsCostModel(
        totalEstimateUsd=total,
        averageEstimateUsd=total / len(values),
        observedCount=len(values),
    )


@router.get("/metrics", response_model=ExecutionMetricsResponse)
async def get_execution_metrics(
    *,
    request: Request,
    workflow_type: Optional[str] = Query(None, alias="workflowType"),
    owner_type: Optional[str] = Query(None, alias="ownerType"),
    state: Optional[str] = Query(None, alias="state"),
    state_in: Optional[str] = Query(None, alias="stateIn"),
    state_not_in: Optional[str] = Query(None, alias="stateNotIn"),
    owner_id: Optional[str] = Query(None, alias="ownerId"),
    entry: Optional[str] = Query(None, alias="entry"),
    repo: Optional[str] = Query(None, alias="repo"),
    repo_exact: Optional[str] = Query(None, alias="repoExact"),
    repo_in: Optional[str] = Query(None, alias="repoIn"),
    repo_not_in: Optional[str] = Query(None, alias="repoNotIn"),
    integration: Optional[str] = Query(None, alias="integration"),
    target_runtime: Optional[str] = Query(None, alias="targetRuntime"),
    target_runtime_in: Optional[str] = Query(None, alias="targetRuntimeIn"),
    target_runtime_not_in: Optional[str] = Query(None, alias="targetRuntimeNotIn"),
    target_skill_in: Optional[str] = Query(None, alias="targetSkillIn"),
    target_skill_not_in: Optional[str] = Query(None, alias="targetSkillNotIn"),
    scheduled_from: Optional[str] = Query(None, alias="scheduledFrom"),
    scheduled_to: Optional[str] = Query(None, alias="scheduledTo"),
    scheduled_blank: Optional[str] = Query(None, alias="scheduledBlank"),
    updated_from: Optional[str] = Query(None, alias="updatedFrom"),
    updated_to: Optional[str] = Query(None, alias="updatedTo"),
    created_from: Optional[str] = Query(None, alias="createdFrom"),
    created_to: Optional[str] = Query(None, alias="createdTo"),
    finished_from: Optional[str] = Query(None, alias="finishedFrom"),
    finished_to: Optional[str] = Query(None, alias="finishedTo"),
    finished_blank: Optional[str] = Query(None, alias="finishedBlank"),
    scope: Optional[str] = Query(None, alias="scope"),
    source: Optional[str] = Query(None),
    sample_size: int = Query(
        200,
        alias="sampleSize",
        ge=1,
        le=_EXECUTION_METRICS_SAMPLE_SIZE_LIMIT,
    ),
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
    temporal_client: Client = Depends(get_temporal_client),
) -> ExecutionMetricsResponse:
    if source not in {None, _TEMPORAL_SOURCE}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_query",
                "message": "metrics currently support source=temporal only.",
            },
        )

    effective_owner_type, effective_owner = _effective_execution_owner_scope(
        user=user,
        owner_type=owner_type,
        owner_id=owner_id,
    )
    usable_search_attributes: frozenset[str] = frozenset()

    def build_query(
        *,
        metric_state_in: str | None = None,
        include_order: bool = False,
    ) -> str | None:
        metric_state_values = _split_temporal_values(
            metric_state_in,
            alias="metric_state_in",
        )
        state_values = _raw_query_values(request, "stateIn", state_in)
        state_not_values = _raw_query_values(request, "stateNotIn", state_not_in)
        if metric_state_values:
            exact_state = str(state or "").strip()
            if exact_state and not (state_values or state_not_values):
                metric_state_values = [
                    value for value in metric_state_values if value == exact_state
                ]
            if state_values:
                allowed = set(state_values)
                metric_state_values = [
                    value for value in metric_state_values if value in allowed
                ]
            if state_not_values:
                blocked = set(state_not_values)
                metric_state_values = [
                    value for value in metric_state_values if value not in blocked
                ]
            if not metric_state_values:
                return None
            metric_state_in = ",".join(metric_state_values)
        count_query, list_query = _build_temporal_execution_query(
            request=request,
            workflow_type=workflow_type,
            state=None if metric_state_values else state,
            state_in=metric_state_in or state_in,
            state_not_in=None if metric_state_values else state_not_in,
            entry=entry,
            repo=repo,
            repo_exact=repo_exact,
            repo_in=repo_in,
            repo_not_in=repo_not_in,
            integration=integration,
            target_runtime=target_runtime,
            target_runtime_in=target_runtime_in,
            target_runtime_not_in=target_runtime_not_in,
            target_skill_in=target_skill_in,
            target_skill_not_in=target_skill_not_in,
            scheduled_from=scheduled_from,
            scheduled_to=scheduled_to,
            scheduled_blank=scheduled_blank,
            updated_from=updated_from,
            updated_to=updated_to,
            created_from=created_from,
            created_to=created_to,
            finished_from=finished_from,
            finished_to=finished_to,
            finished_blank=finished_blank,
            scope=scope,
            owner_type=effective_owner_type,
            owner_id=effective_owner,
            sort="closedAt",
            sort_dir="desc",
            include_order=include_order,
            usable_search_attributes=usable_search_attributes,
        )
        return list_query if include_order else count_query

    try:
        client = temporal_client
        usable_search_attributes = await _detect_optional_temporal_search_attributes(client)
        unavailable_filter_aliases = _requested_unavailable_filter_aliases(
            request,
            usable_search_attributes,
        )
        if unavailable_filter_aliases:
            return ExecutionMetricsResponse(
                totalRuns=0,
                runningRuns=0,
                completedRuns=0,
                failedRuns=0,
                canceledRuns=0,
                terminalRuns=0,
                successRate=None,
                sampleSize=0,
                countMode="exact",
                refreshedAt=datetime.now(UTC),
            )

        async def count_matching_workflows(query: str | None) -> int:
            if query is None:
                return 0
            count_result = await client.count_workflows(query=query)
            return int(count_result.count)

        total, completed, failed, canceled = await asyncio.gather(
            count_matching_workflows(build_query()),
            count_matching_workflows(build_query(metric_state_in="completed")),
            count_matching_workflows(build_query(metric_state_in="failed")),
            count_matching_workflows(build_query(metric_state_in="canceled")),
        )

        terminal_query = build_query(
            metric_state_in="completed,failed,canceled",
            include_order=True,
        )
        page = []
        if terminal_query is not None:
            iterator = client.list_workflows(
                query=terminal_query,
                page_size=sample_size,
            )
            await iterator.fetch_next_page()
            page = iterator.current_page or []
        canonical_map: dict[str, TemporalExecutionCanonicalRecord] = {}
        if page:
            workflow_ids = [wf.id for wf in page]
            stmt = select(TemporalExecutionCanonicalRecord).where(
                TemporalExecutionCanonicalRecord.workflow_id.in_(workflow_ids)
            )
            canonical_rows = (await session.execute(stmt)).scalars().all()
            canonical_map = {row.workflow_id: row for row in canonical_rows}

        durations: list[float] = []
        costs: list[float] = []
        from api_service.core.sync import (
            map_temporal_state_to_projection,
            merged_parameters_for_projection,
        )

        for wf in page:
            payload = await map_temporal_state_to_projection(wf)
            canonical = canonical_map.get(wf.id)
            payload["parameters"] = merged_parameters_for_projection(payload, canonical)
            duration = _duration_seconds_from_payload(payload)
            if duration is not None:
                durations.append(duration)
            cost = _extract_cost_estimate_usd(
                payload.get("search_attributes"),
                payload.get("memo"),
                payload.get("parameters"),
            )
            if cost is not None:
                costs.append(cost)

        terminal = completed + failed + canceled
        success_rate = completed / terminal if terminal else None
        return ExecutionMetricsResponse(
            totalRuns=total,
            completedRuns=completed,
            failedRuns=failed,
            canceledRuns=canceled,
            terminalRuns=terminal,
            successRate=success_rate,
            duration=_duration_metrics(durations),
            cost=_cost_metrics(costs),
            sampleSize=len(page),
            countMode="exact",
            refreshedAt=datetime.now(UTC),
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_query",
                "message": str(exc),
            },
        ) from exc
    except RPCError as exc:
        logger.warning("Failed to read Temporal execution metrics: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "temporal_unavailable",
                "message": "Temporal service unavailable.",
            },
        ) from exc

@router.get("/facets", response_model=ExecutionFacetResponse)
async def list_execution_facets(
    *,
    request: Request,
    facet: Literal[
        "status",
        "targetRuntime",
        "targetSkill",
        "repository",
        "integration",
    ] = Query(..., alias="facet"),
    workflow_type: Optional[str] = Query(None, alias="workflowType"),
    owner_type: Optional[str] = Query(None, alias="ownerType"),
    state: Optional[str] = Query(None, alias="state"),
    state_in: Optional[str] = Query(None, alias="stateIn"),
    state_not_in: Optional[str] = Query(None, alias="stateNotIn"),
    owner_id: Optional[str] = Query(None, alias="ownerId"),
    entry: Optional[str] = Query(None, alias="entry"),
    repo: Optional[str] = Query(None, alias="repo"),
    repo_exact: Optional[str] = Query(None, alias="repoExact"),
    repo_in: Optional[str] = Query(None, alias="repoIn"),
    repo_not_in: Optional[str] = Query(None, alias="repoNotIn"),
    integration: Optional[str] = Query(None, alias="integration"),
    target_runtime: Optional[str] = Query(None, alias="targetRuntime"),
    target_runtime_in: Optional[str] = Query(None, alias="targetRuntimeIn"),
    target_runtime_not_in: Optional[str] = Query(None, alias="targetRuntimeNotIn"),
    target_skill_in: Optional[str] = Query(None, alias="targetSkillIn"),
    target_skill_not_in: Optional[str] = Query(None, alias="targetSkillNotIn"),
    scheduled_from: Optional[str] = Query(None, alias="scheduledFrom"),
    scheduled_to: Optional[str] = Query(None, alias="scheduledTo"),
    scheduled_blank: Optional[str] = Query(None, alias="scheduledBlank"),
    updated_from: Optional[str] = Query(None, alias="updatedFrom"),
    updated_to: Optional[str] = Query(None, alias="updatedTo"),
    created_from: Optional[str] = Query(None, alias="createdFrom"),
    created_to: Optional[str] = Query(None, alias="createdTo"),
    finished_from: Optional[str] = Query(None, alias="finishedFrom"),
    finished_to: Optional[str] = Query(None, alias="finishedTo"),
    finished_blank: Optional[str] = Query(None, alias="finishedBlank"),
    scope: Optional[str] = Query(None, alias="scope"),
    search: Optional[str] = Query(None, alias="search"),
    page_size: int = Query(50, alias="pageSize", ge=1, le=_EXECUTION_FACET_PAGE_SIZE_LIMIT),
    next_page_token: Optional[str] = Query(None, alias="nextPageToken"),
    source: Optional[str] = Query(None),
    user: User = Depends(get_current_user()),
    temporal_client: Client = Depends(get_temporal_client),
) -> ExecutionFacetResponse:
    if source not in {None, _TEMPORAL_SOURCE}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_query",
                "message": "facets currently support source=temporal only.",
            },
        )
    if _is_execution_admin(user):
        effective_owner_type = owner_type
        effective_owner = owner_id
    else:
        normalized_owner_type = str(owner_type or "").strip().lower()
        if owner_type is not None and owner_type != "user":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "execution_forbidden",
                    "message": "Cannot list non-user executions.",
                },
            )
        if owner_id is not None and owner_id != _owner_id(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "execution_forbidden",
                    "message": "Cannot list executions for another user.",
                },
            )
        effective_owner = _owner_id(user)
        effective_owner_type = "user" if normalized_owner_type == "user" else None

    try:
        facet_attr = _EXECUTION_FACET_ATTRS.get(facet)
        if facet_attr is None:
            raise TemporalExecutionValidationError(
                "facet must be one of: " + ", ".join(sorted(_EXECUTION_FACET_ATTRS)) + "."
            )
        if facet != "status" and next_page_token:
            _decode_execution_facet_page_token(next_page_token)
        usable_search_attributes = await _detect_optional_temporal_search_attributes(temporal_client)
        if (
            facet_attr in _OPTIONAL_TEMPORAL_SEARCH_ATTRIBUTES
            and facet_attr not in usable_search_attributes
        ):
            return ExecutionFacetResponse(
                facet=facet,
                items=[],
                blankCount=None,
                truncated=False,
                nextPageToken=None,
                countMode="estimated_or_unknown",
                source="current_page_fallback",
            )
        unavailable_filter_aliases = _requested_unavailable_filter_aliases(
            request,
            usable_search_attributes,
        )
        if unavailable_filter_aliases:
            return ExecutionFacetResponse(
                facet=facet,
                items=[],
                blankCount=None,
                truncated=False,
                nextPageToken=None,
                countMode="estimated_or_unknown",
                source="current_page_fallback",
            )
        search_value = _normalize_text_filter(search, alias="search")
        base_query, _ = _build_temporal_execution_query(
            request=request,
            workflow_type=workflow_type,
            state=state,
            state_in=state_in,
            state_not_in=state_not_in,
            entry=entry,
            repo=repo,
            repo_exact=repo_exact,
            repo_in=repo_in,
            repo_not_in=repo_not_in,
            integration=integration,
            target_runtime=target_runtime,
            target_runtime_in=target_runtime_in,
            target_runtime_not_in=target_runtime_not_in,
            target_skill_in=target_skill_in,
            target_skill_not_in=target_skill_not_in,
            scheduled_from=scheduled_from,
            scheduled_to=scheduled_to,
            scheduled_blank=scheduled_blank,
            updated_from=updated_from,
            updated_to=updated_to,
            created_from=created_from,
            created_to=created_to,
            finished_from=finished_from,
            finished_to=finished_to,
            finished_blank=finished_blank,
            scope=scope,
            owner_type=effective_owner_type,
            owner_id=effective_owner,
            exclude_aliases=_EXECUTION_FACET_FILTER_ALIASES.get(facet, frozenset()),
            usable_search_attributes=usable_search_attributes,
        )
        client = temporal_client

        if facet == "status":
            matching_values = [
                value
                for value in _TEMPORAL_STATUS_VALUES
                if not search_value or search_value.lower() in value.lower()
            ]
            status_offset = _decode_execution_status_facet_offset(next_page_token)
            values = matching_values[status_offset : status_offset + page_size]
            next_status_offset = status_offset + page_size
            status_next_page_token = (
                _encode_execution_status_facet_offset(next_status_offset)
                if next_status_offset < len(matching_values)
                else None
            )
            items = []
            for value in values:
                count_info = await client.count_workflows(
                    query=_and_temporal_query(
                        base_query,
                        f'{facet_attr}="{_escape_temporal_value(value)}"',
                    )
                )
                items.append(
                    ExecutionFacetItemModel(
                        value=value,
                        label=_facet_label(facet, value),
                        count=count_info.count,
                    )
                )
            return ExecutionFacetResponse(
                facet=facet,
                items=items,
                blankCount=None,
                truncated=status_next_page_token is not None,
                nextPageToken=status_next_page_token,
                countMode="exact",
                source="authoritative",
            )

        token_bytes = _decode_execution_facet_page_token(next_page_token)
        iterator = client.list_workflows(
            query=base_query,
            page_size=page_size,
            next_page_token=token_bytes,
        )
        await iterator.fetch_next_page()

        seen: set[str] = set()
        values: list[str] = []
        for workflow in iterator.current_page or []:
            value = (await _facet_value_from_workflow(workflow, facet)).strip()
            if not value or value in seen:
                continue
            if search_value and search_value.lower() not in value.lower():
                continue
            seen.add(value)
            values.append(value)

        items = []
        for value in values[:page_size]:
            count_info = await client.count_workflows(
                query=_and_temporal_query(
                    base_query,
                    f'{facet_attr}="{_escape_temporal_value(value)}"',
                )
            )
            items.append(
                ExecutionFacetItemModel(
                    value=value,
                    label=_facet_label(facet, value),
                    count=count_info.count,
                )
            )
        blank_info = await client.count_workflows(
            query=_and_temporal_query(base_query, f"{facet_attr} IS NULL")
        )
        next_token = (
            base64.b64encode(iterator.next_page_token).decode("utf-8")
            if iterator.next_page_token
            else None
        )
        return ExecutionFacetResponse(
            facet=facet,
            items=items,
            blankCount=blank_info.count,
            truncated=bool(iterator.next_page_token),
            nextPageToken=next_token,
            countMode="exact",
            source="authoritative",
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_query",
                "message": str(exc),
            },
        ) from exc
    except RPCError as exc:
        logger.warning("Failed to list Temporal execution facets: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "temporal_unavailable",
                "message": "Temporal service unavailable.",
            },
        ) from exc

@router.get(
    "/{workflow_id}/steps/{logical_step_id}/step-executions",
    response_model=StepExecutionListModel,
)
async def describe_execution_step_executions(
    workflow_id: str,
    logical_step_id: str,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
    temporal_client: Client = Depends(get_temporal_client),
) -> StepExecutionListModel:
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    record = await _get_owned_execution(
        service=service,
        workflow_id=workflow_id,
        user=user,
    )
    workflow_type_value = _enum_value(getattr(record, "workflow_type", None)) or ""
    if workflow_type_value != "MoonMind.UserWorkflow":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_query",
                "message": (
                    "Step Execution reads are only supported for MoonMind.UserWorkflow "
                    "executions."
                ),
            },
        )
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    ledger = await _load_execution_step_ledger(
        temporal_client=temporal_client,
        workflow_id=canonical_workflow_id,
        fallback_record=record,
    )
    row = _find_step_ledger_row(ledger, logical_step_id)
    artifact_service = get_temporal_artifact_service(session)
    principal = str(getattr(user, "id", "") or "system")
    manifest_refs = _step_execution_manifest_refs(row)
    manifest_results = await asyncio.gather(
        *(
            _read_step_execution_manifest(
                artifact_service=artifact_service,
                artifact_ref=manifest_ref,
                principal=principal,
            )
            for manifest_ref in manifest_refs
        )
    )
    step_executions: list[StepExecutionProjectionModel] = []
    for index, ((manifest, decision), manifest_ref) in enumerate(
        zip(manifest_results, manifest_refs, strict=True),
        start=1,
    ):
        payload = (
            _step_execution_projection_payload(
                manifest,
                manifest_artifact_ref=manifest_ref,
            )
            if manifest is not None
            else _degraded_step_execution_projection_payload(
                ledger=ledger,
                logical_step_id=logical_step_id,
                manifest_artifact_ref=manifest_ref,
                compatibility_decision=decision
                or _step_execution_compatibility_decision(
                    failure_code="invalid_step_execution_manifest",
                    message="Step Execution manifest artifact is invalid.",
                ),
                fallback_ordinal=index,
            )
        )
        step_executions.append(StepExecutionProjectionModel.model_validate(payload))
    step_executions.sort(key=lambda item: item.execution_ordinal)
    return StepExecutionListModel(
        workflowId=ledger.workflow_id,
        runId=ledger.run_id,
        runScope=ledger.run_scope,
        logicalStepId=logical_step_id,
        stepExecutions=step_executions,
    )


@router.get(
    "/{workflow_id}/steps/{logical_step_id}/step-executions/{execution_ordinal}",
    response_model=StepExecutionDetailModel,
)
async def describe_execution_step_execution(
    workflow_id: str,
    logical_step_id: str,
    execution_ordinal: int,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
    temporal_client: Client = Depends(get_temporal_client),
) -> StepExecutionDetailModel:
    if execution_ordinal < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_step_execution",
                "message": "Step Execution number must be greater than zero.",
            },
        )
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    record = await _get_owned_execution(
        service=service,
        workflow_id=workflow_id,
        user=user,
    )
    workflow_type_value = _enum_value(getattr(record, "workflow_type", None)) or ""
    if workflow_type_value != "MoonMind.UserWorkflow":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_query",
                "message": (
                    "Step Execution reads are only supported for MoonMind.UserWorkflow "
                    "executions."
                ),
            },
        )
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    ledger = await _load_execution_step_ledger(
        temporal_client=temporal_client,
        workflow_id=canonical_workflow_id,
        fallback_record=record,
    )
    row = _find_step_ledger_row(ledger, logical_step_id)
    artifact_service = get_temporal_artifact_service(session)
    principal = str(getattr(user, "id", "") or "system")
    manifest_refs = _step_execution_manifest_refs(row)
    manifest_results = await asyncio.gather(
        *(
            _read_step_execution_manifest(
                artifact_service=artifact_service,
                artifact_ref=manifest_ref,
                principal=principal,
            )
            for manifest_ref in manifest_refs
        )
    )
    for index, ((manifest, decision), manifest_ref) in enumerate(
        zip(manifest_results, manifest_refs, strict=True),
        start=1,
    ):
        # Degraded refs have no manifest ordinal; fall back to the per-ref
        # index so matching mirrors the list projection. Using
        # ``row.execution_ordinal`` here would let a degraded older ref claim
        # the latest ordinal and shadow the valid attempt at that ordinal.
        candidate_ordinal = (
            manifest.execution_ordinal if manifest is not None else index
        )
        if candidate_ordinal != execution_ordinal:
            continue
        payload = (
            _step_execution_detail_payload(
                manifest,
                manifest_artifact_ref=manifest_ref,
            )
            if manifest is not None
            else _degraded_step_execution_projection_payload(
                ledger=ledger,
                logical_step_id=logical_step_id,
                manifest_artifact_ref=manifest_ref,
                compatibility_decision=decision
                or _step_execution_compatibility_decision(
                    failure_code="invalid_step_execution_manifest",
                    message="Step Execution manifest artifact is invalid.",
                ),
                fallback_ordinal=index,
            )
        )
        return StepExecutionDetailModel.model_validate(payload)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "step_execution_not_found",
            "message": "Step Execution was not found in the latest execution ledger.",
        },
    )


@router.get(
    "/{workflow_id}/steps",
    response_model=StepLedgerSnapshotModel,
)
async def describe_execution_steps(
    workflow_id: str,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
    temporal_client: Client = Depends(get_temporal_client),
) -> StepLedgerSnapshotModel:
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if settings.temporal.temporal_authoritative_read_enabled:
        try:
            await execution_sync.fetch_and_sync_execution(
                session,
                canonical_workflow_id,
                temporal_client,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.warning(
                "Failed to sync execution %s from Temporal for step ledger: %s",
                canonical_workflow_id,
                exc,
                exc_info=True,
            )
    record = await _get_owned_execution(
        service=service,
        workflow_id=workflow_id,
        user=user,
        include_orphaned_projection=True,
    )
    workflow_type_value = _enum_value(getattr(record, "workflow_type", None)) or ""
    if workflow_type_value != "MoonMind.UserWorkflow":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_query",
                "message": "Step-ledger reads are only supported for MoonMind.UserWorkflow executions.",
            },
        )
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    return await _load_execution_step_ledger(
        temporal_client=temporal_client,
        workflow_id=canonical_workflow_id,
        fallback_record=record,
    )

@router.get("/{workflow_id}/checkpoints", response_model=CheckpointListResponse)
async def list_execution_checkpoints(
    workflow_id: str,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> CheckpointListResponse:
    record = await _get_owned_execution(
        service=service, workflow_id=workflow_id, user=user
    )
    return CheckpointListResponse(items=_checkpoint_summaries_from_record(record))


@router.get(
    "/{workflow_id}/checkpoint-branches",
    response_model=CheckpointBranchListResponse,
)
async def list_checkpoint_branches(
    workflow_id: str,
    active: bool = Query(True),
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> CheckpointBranchListResponse:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    conditions = [WorkflowCheckpointBranch.workflow_id == workflow_id]
    if active:
        conditions.append(WorkflowCheckpointBranch.state != "archived")
    result = await session.execute(
        select(WorkflowCheckpointBranch)
        .where(*conditions)
        .order_by(WorkflowCheckpointBranch.created_at.desc())
    )
    return CheckpointBranchListResponse(
        items=[_branch_to_model(branch) for branch in result.scalars().all()]
    )


@router.post(
    "/{workflow_id}/checkpoint-branches",
    response_model=CheckpointBranchModel,
    status_code=status.HTTP_201_CREATED,
)
async def create_checkpoint_branch(
    workflow_id: str,
    payload: CheckpointBranchCreateRequest,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> CheckpointBranchModel:
    record = await _get_owned_execution(
        service=service, workflow_id=workflow_id, user=user
    )
    _validate_branch_source(
        workflow_id=workflow_id, record=record, source=payload.source
    )
    _validate_branch_policy(
        workspace_policy=payload.workspace_policy,
        runtime_context_policy=payload.runtime_context_policy,
        publish_mode=payload.publish_mode,
        git_work_branch=payload.git_work_branch,
        max_budget_usd=payload.max_budget_usd,
    )
    request_digest = _operation_digest(payload)
    existing_op = await session.execute(
        select(WorkflowCheckpointBranchOperation).where(
            WorkflowCheckpointBranchOperation.workflow_id == workflow_id,
            WorkflowCheckpointBranchOperation.idempotency_key
            == payload.idempotency_key,
        )
    )
    operation = existing_op.scalar_one_or_none()
    if operation is not None:
        if (
            operation.operation != "checkpoint_branch.create"
            or operation.request_digest != request_digest
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "idempotency_key_conflict"},
            )
        branch = await _load_checkpoint_branch(
            session,
            workflow_id=workflow_id,
            branch_id=str(operation.branch_id),
        )
        return _branch_to_model(branch)

    instruction_ref, instruction_digest = _instruction_identity(payload.instructions)
    branch_id = _new_checkpoint_branch_id()
    branch_turn_id = _new_checkpoint_branch_turn_id()
    await _prepare_checkpoint_branch_launch(
        session=session,
        record=record,
        workflow_id=workflow_id,
        branch_id=branch_id,
        branch_turn_id=branch_turn_id,
        source_checkpoint_ref=payload.source.checkpoint_ref,
        source_checkpoint_digest=payload.source.checkpoint_digest,
        logical_step_id=payload.source.logical_step_id,
        label=payload.label,
        workspace_policy=payload.workspace_policy,
        runtime_context_policy=payload.runtime_context_policy,
        idempotency_key=payload.idempotency_key,
        instruction_ref=instruction_ref,
        instruction_digest=instruction_digest,
        requested_work_branch=payload.git_work_branch,
        source_run_id=payload.source.run_id,
        source_execution_ordinal=payload.source.execution_ordinal,
        source_checkpoint_boundary=payload.source.checkpoint_boundary,
    )
    branch = await session.get(WorkflowCheckpointBranch, branch_id)
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_binding", "reason": "branch_prepare_failed"},
        )
    branch.state = "created"
    branch.branch_kind = "root"
    branch.current_head_checkpoint_ref = payload.source.checkpoint_ref
    branch.publish_status = "unpublished"
    branch.idempotency_key = payload.idempotency_key
    branch.created_by = getattr(user, "email", None) or _owner_id(user)
    session.add(
        WorkflowCheckpointBranchOperation(
            workflow_id=workflow_id,
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
            operation="checkpoint_branch.create",
            idempotency_key=payload.idempotency_key,
            request_digest=request_digest,
            response_payload={
                "branchId": branch_id,
                "branchTurnId": branch_turn_id,
            },
        )
    )
    await session.commit()
    await session.refresh(branch)
    return _branch_to_model(branch)


@router.get(
    "/{workflow_id}/checkpoint-branches/{branch_id}",
    response_model=CheckpointBranchModel,
)
async def describe_checkpoint_branch(
    workflow_id: str,
    branch_id: str,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> CheckpointBranchModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    return _branch_to_model(
        await _load_checkpoint_branch(
            session, workflow_id=workflow_id, branch_id=branch_id
        )
    )


@router.get(
    "/{workflow_id}/checkpoint-branches/{branch_id}/turns",
    response_model=CheckpointBranchTurnListResponse,
)
async def list_checkpoint_branch_turns(
    workflow_id: str,
    branch_id: str,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> CheckpointBranchTurnListResponse:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    await _load_checkpoint_branch(session, workflow_id=workflow_id, branch_id=branch_id)
    result = await session.execute(
        select(WorkflowCheckpointBranchTurn)
        .where(WorkflowCheckpointBranchTurn.branch_id == branch_id)
        .order_by(WorkflowCheckpointBranchTurn.created_at.asc())
    )
    return CheckpointBranchTurnListResponse(
        items=[_turn_to_model(turn) for turn in result.scalars().all()]
    )


@router.post(
    "/{workflow_id}/checkpoint-branches/{branch_id}/turns/{branch_turn_id}/launch",
    response_model=CheckpointBranchTurnModel,
)
async def launch_checkpoint_branch_turn(
    workflow_id: str,
    branch_id: str,
    branch_turn_id: str,
    payload: CheckpointBranchTurnLaunchRequest,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> CheckpointBranchTurnModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    branch = await _load_checkpoint_branch(
        session, workflow_id=workflow_id, branch_id=branch_id
    )
    turn_result = await session.execute(
        select(WorkflowCheckpointBranchTurn).where(
            WorkflowCheckpointBranchTurn.branch_turn_id == branch_turn_id,
            WorkflowCheckpointBranchTurn.branch_id == branch.branch_id,
        )
    )
    turn = turn_result.scalar_one_or_none()
    if turn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "checkpoint_branch_turn_not_found"},
        )
    launch_key = build_branch_turn_launch_idempotency_key(
        workflow_id=workflow_id,
        branch_id=branch.branch_id,
        branch_turn_id=turn.branch_turn_id,
    )
    request_digest = _scoped_operation_digest(
        payload,
        scope={
            "branchId": branch.branch_id,
            "branchTurnId": turn.branch_turn_id,
            "operation": "checkpoint_branch.turn.launch",
        },
    )
    existing_op = (
        await session.execute(
            select(WorkflowCheckpointBranchOperation).where(
                WorkflowCheckpointBranchOperation.workflow_id == workflow_id,
                WorkflowCheckpointBranchOperation.idempotency_key == launch_key,
            )
        )
    ).scalar_one_or_none()
    if existing_op is not None:
        if (
            existing_op.operation != "checkpoint_branch.turn.launch"
            or existing_op.branch_id != branch.branch_id
            or existing_op.branch_turn_id != turn.branch_turn_id
            or existing_op.request_digest != request_digest
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "idempotency_key_conflict"},
            )
        _require_branch_turn_immutable_snapshot(
            turn,
            existing_op.response_payload.get("immutableLaunchFields"),
        )
        return _turn_to_model(turn)

    _validate_branch_turn_launch_policy(branch, turn)

    context_payload = {
        "workflowId": workflow_id,
        "runId": branch.source_run_id,
        "logicalStepId": branch.logical_step_id,
        "executionOrdinal": branch.source_execution_ordinal,
        "branch": {
            "branchId": branch.branch_id,
            "branchTurnId": turn.branch_turn_id,
            "sourceCheckpointRef": turn.source_checkpoint_ref,
            "sourceStateKind": turn.source_state_kind,
            "sourceStateRef": turn.source_state_ref,
            "sourceStateDigest": turn.source_state_digest,
            "parentBranchId": branch.parent_branch_id,
            "parentTurnId": turn.parent_turn_id or branch.parent_turn_id,
            "gitWorkBranch": branch.git_work_branch or turn.git_work_branch,
        },
        "workspacePolicy": turn.workspace_policy,
        "workspaceBaseline": payload.workspace_baseline
        or {
            "kind": "checkpoint_ref",
            "checkpointRef": turn.source_checkpoint_ref,
        },
        "instructionRefs": [turn.instruction_ref],
        "instructionDigests": [turn.instruction_digest],
        "priorEvidenceRefs": payload.prior_evidence_refs,
        "boundedSummaries": payload.bounded_summaries,
        "builderMetadata": payload.builder_metadata
        or {
            "version": "checkpoint-branch-launch-api-v1",
            "digest": "sha256:checkpoint-branch-launch-api-v1",
        },
    }
    try:
        context_bundle = build_checkpoint_branch_turn_context_bundle(context_payload)
    except CheckpointBranchContextBundleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_branch_turn_context", "reason": str(exc)},
        ) from exc
    context_digest = _operation_digest(context_bundle)
    context_bundle_ref = _branch_turn_artifact_ref(
        branch_turn_id=turn.branch_turn_id,
        artifact_name="context-bundle",
        payload_digest=context_digest,
    )
    manifest_payload = _branch_turn_launch_manifest_payload(
        workflow_id=workflow_id,
        branch=branch,
        turn=turn,
        payload=payload,
        context_bundle_ref=context_bundle_ref,
    )
    manifest_digest = _operation_digest(manifest_payload)
    manifest_ref = _branch_turn_artifact_ref(
        branch_turn_id=turn.branch_turn_id,
        artifact_name="step-execution-manifest",
        payload_digest=manifest_digest,
    )
    diagnostics_ref = (
        payload.diagnostics_ref
        or _branch_turn_artifact_ref(
            branch_turn_id=turn.branch_turn_id,
            artifact_name="diagnostics",
            payload_digest=_operation_digest(
                {
                    "workflowId": workflow_id,
                    "branchId": branch.branch_id,
                    "branchTurnId": turn.branch_turn_id,
                    "launchIdempotencyKey": launch_key,
                }
            ),
        )
    )
    try:
        launched = await CheckpointBranchService(session).launch_turn(
            workflow_id=workflow_id,
            branch_id=branch.branch_id,
            branch_turn_id=turn.branch_turn_id,
            context_bundle_ref=context_bundle_ref,
            step_execution_manifest_ref=manifest_ref,
            checkpoint_ref=None,
            diagnostics_ref=diagnostics_ref,
            idempotency_key=launch_key,
            created_step_execution_id=payload.created_step_execution_id,
            runtime_agent_run_id=payload.runtime_agent_run_id,
            provider_session_id=payload.provider_session_id,
            agent_request_ref=payload.runtime_request_ref,
            agent_result_ref=payload.runtime_result_ref,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "branch_turn_launch_rejected", "reason": str(exc)},
        ) from exc

    session.add(
        WorkflowCheckpointBranchOperation(
            workflow_id=workflow_id,
            branch_id=branch.branch_id,
            branch_turn_id=turn.branch_turn_id,
            operation="checkpoint_branch.turn.launch",
            idempotency_key=launch_key,
            request_digest=request_digest,
            response_payload={
                "branchId": branch.branch_id,
                "branchTurnId": turn.branch_turn_id,
                "contextBundleRef": context_bundle_ref,
                "stepExecutionManifestRef": manifest_ref,
                "diagnosticsRef": diagnostics_ref,
                "contextBundle": context_bundle,
                "manifest": manifest_payload,
                "immutableLaunchFields": _branch_turn_immutable_snapshot(launched),
            },
        )
    )
    await session.commit()
    await session.refresh(launched)
    return _turn_to_model(launched)


async def _record_branch_turn_operation(
    *,
    workflow_id: str,
    record: Any,
    branch: WorkflowCheckpointBranch,
    payload: CheckpointBranchContinueRequest,
    operation_name: str,
    session: AsyncSession,
    parent_turn_id: str | None = None,
) -> CheckpointBranchTurnModel:
    _validate_branch_policy(
        workspace_policy=payload.workspace_policy,
        runtime_context_policy=payload.runtime_context_policy,
        max_budget_usd=payload.max_budget_usd,
    )
    if branch.state in {"archived", "promoted", "superseded"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_branch_state", "state": branch.state},
        )
    request_digest = _scoped_operation_digest(
        payload, scope={"branchId": branch.branch_id}
    )
    existing_op = await session.execute(
        select(WorkflowCheckpointBranchOperation).where(
            WorkflowCheckpointBranchOperation.workflow_id == workflow_id,
            WorkflowCheckpointBranchOperation.idempotency_key
            == payload.idempotency_key,
        )
    )
    operation = existing_op.scalar_one_or_none()
    if operation is not None:
        if (
            operation.operation != operation_name
            or operation.branch_id != branch.branch_id
            or operation.request_digest != request_digest
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "idempotency_key_conflict"},
            )
        result = await session.execute(
            select(WorkflowCheckpointBranchTurn).where(
                WorkflowCheckpointBranchTurn.branch_turn_id == operation.branch_turn_id
            )
        )
        turn = result.scalar_one()
        return _turn_to_model(turn)

    instruction_ref, instruction_digest = _instruction_identity(payload.instructions)
    branch_turn_id = _new_checkpoint_branch_turn_id()
    source_checkpoint_ref = (
        branch.current_head_checkpoint_ref or branch.source_checkpoint_ref
    )
    await _prepare_checkpoint_branch_launch(
        session=session,
        record=record,
        workflow_id=workflow_id,
        branch_id=branch.branch_id,
        branch_turn_id=branch_turn_id,
        source_checkpoint_ref=source_checkpoint_ref,
        source_checkpoint_digest=branch.source_checkpoint_digest,
        logical_step_id=branch.logical_step_id,
        label=payload.label or branch.label,
        workspace_policy=payload.workspace_policy,
        runtime_context_policy=payload.runtime_context_policy,
        idempotency_key=payload.idempotency_key,
        instruction_ref=instruction_ref,
        instruction_digest=instruction_digest,
        requested_work_branch=branch.git_work_branch,
        parent_branch_id=branch.parent_branch_id,
        parent_turn_id=parent_turn_id,
        source_run_id=branch.source_run_id,
        source_execution_ordinal=branch.source_execution_ordinal,
        source_checkpoint_boundary=branch.source_checkpoint_boundary,
    )
    turn = await session.get(WorkflowCheckpointBranchTurn, branch_turn_id)
    if turn is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_binding", "reason": "branch_turn_prepare_failed"},
        )
    branch.state = "active"
    branch.label = payload.label or branch.label
    branch.workspace_policy = payload.workspace_policy
    branch.runtime_context_policy = payload.runtime_context_policy
    session.add(
        WorkflowCheckpointBranchOperation(
            workflow_id=workflow_id,
            branch_id=branch.branch_id,
            branch_turn_id=branch_turn_id,
            operation=operation_name,
            idempotency_key=payload.idempotency_key,
            request_digest=request_digest,
            response_payload={
                "branchId": branch.branch_id,
                "branchTurnId": branch_turn_id,
            },
        )
    )
    await session.commit()
    await session.refresh(turn)
    return _turn_to_model(turn)


@router.post(
    "/{workflow_id}/checkpoint-branches/{branch_id}/continue",
    response_model=CheckpointBranchTurnModel,
    status_code=status.HTTP_201_CREATED,
)
async def continue_checkpoint_branch(
    workflow_id: str,
    branch_id: str,
    payload: CheckpointBranchContinueRequest,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> CheckpointBranchTurnModel:
    record = await _get_owned_execution(
        service=service, workflow_id=workflow_id, user=user
    )
    branch = await _load_checkpoint_branch(
        session, workflow_id=workflow_id, branch_id=branch_id
    )
    return await _record_branch_turn_operation(
        workflow_id=workflow_id,
        record=record,
        branch=branch,
        payload=payload,
        operation_name="checkpoint_branch.continue",
        session=session,
    )


@router.post(
    "/{workflow_id}/checkpoint-branches/{branch_id}/fork",
    response_model=CheckpointBranchModel,
    status_code=status.HTTP_201_CREATED,
)
async def fork_checkpoint_branch(
    workflow_id: str,
    branch_id: str,
    payload: CheckpointBranchForkRequest,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> CheckpointBranchModel:
    record = await _get_owned_execution(
        service=service, workflow_id=workflow_id, user=user
    )
    parent = await _load_checkpoint_branch(
        session, workflow_id=workflow_id, branch_id=branch_id
    )
    _validate_branch_policy(
        workspace_policy=payload.workspace_policy,
        runtime_context_policy=payload.runtime_context_policy,
        max_budget_usd=payload.max_budget_usd,
    )
    if parent.state in {"archived", "superseded"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_branch_state", "state": parent.state},
        )
    parent_turn = None
    if payload.parent_turn_id:
        parent_turn_result = await session.execute(
            select(WorkflowCheckpointBranchTurn).where(
                WorkflowCheckpointBranchTurn.branch_turn_id == payload.parent_turn_id
            )
        )
        parent_turn = parent_turn_result.scalar_one_or_none()
        if parent_turn is None or parent_turn.branch_id != parent.branch_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "invalid_parent_turn"},
            )
    fork_source_ref = (
        parent_turn.source_checkpoint_ref
        if parent_turn is not None
        else parent.current_head_checkpoint_ref or parent.source_checkpoint_ref
    )
    fork_source_digest = (
        parent_turn.source_checkpoint_digest
        if parent_turn is not None
        else parent.source_checkpoint_digest
    )
    request_digest = _scoped_operation_digest(
        payload, scope={"parentBranchId": parent.branch_id}
    )
    existing_op = await session.execute(
        select(WorkflowCheckpointBranchOperation).where(
            WorkflowCheckpointBranchOperation.workflow_id == workflow_id,
            WorkflowCheckpointBranchOperation.idempotency_key
            == payload.idempotency_key,
        )
    )
    operation = existing_op.scalar_one_or_none()
    if operation is not None:
        if (
            operation.operation != "checkpoint_branch.fork"
            or operation.request_digest != request_digest
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "idempotency_key_conflict"},
            )
        branch = await _load_checkpoint_branch(
            session,
            workflow_id=workflow_id,
            branch_id=str(operation.branch_id),
        )
        return _branch_to_model(branch)

    instruction_ref, instruction_digest = _instruction_identity(payload.instructions)
    forked_branch_id = _new_checkpoint_branch_id()
    branch_turn_id = _new_checkpoint_branch_turn_id()
    await _prepare_checkpoint_branch_launch(
        session=session,
        record=record,
        workflow_id=workflow_id,
        branch_id=forked_branch_id,
        branch_turn_id=branch_turn_id,
        source_checkpoint_ref=fork_source_ref,
        source_checkpoint_digest=fork_source_digest,
        logical_step_id=parent.logical_step_id,
        label=payload.label or f"Fork of {parent.label}",
        workspace_policy=payload.workspace_policy,
        runtime_context_policy=payload.runtime_context_policy,
        idempotency_key=payload.idempotency_key,
        instruction_ref=instruction_ref,
        instruction_digest=instruction_digest,
        parent_branch_id=parent.branch_id,
        parent_turn_id=payload.parent_turn_id,
        source_run_id=parent.source_run_id,
        source_execution_ordinal=parent.source_execution_ordinal,
        source_checkpoint_boundary=parent.source_checkpoint_boundary,
    )
    forked = await session.get(WorkflowCheckpointBranch, forked_branch_id)
    if forked is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_binding", "reason": "fork_prepare_failed"},
        )
    forked.state = "created"
    forked.root_workflow_id = parent.root_workflow_id
    forked.branch_kind = "child_fork"
    forked.current_head_checkpoint_ref = fork_source_ref
    forked.publish_status = "unpublished"
    forked.idempotency_key = payload.idempotency_key
    forked.created_by = getattr(user, "email", None) or _owner_id(user)
    session.add(
        WorkflowCheckpointBranchOperation(
            workflow_id=workflow_id,
            branch_id=forked_branch_id,
            branch_turn_id=branch_turn_id,
            operation="checkpoint_branch.fork",
            idempotency_key=payload.idempotency_key,
            request_digest=request_digest,
            response_payload={
                "branchId": forked_branch_id,
                "branchTurnId": branch_turn_id,
            },
        )
    )
    await session.commit()
    await session.refresh(forked)
    return _branch_to_model(forked)


@router.get(
    "/{workflow_id}/checkpoint-branches/{branch_id}/compare",
    response_model=CheckpointBranchCompareResponse,
)
async def compare_checkpoint_branches(
    workflow_id: str,
    branch_id: str,
    against: str = Query(...),
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> CheckpointBranchCompareResponse:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    branch = await _load_checkpoint_branch(
        session, workflow_id=workflow_id, branch_id=branch_id
    )
    other = await _load_checkpoint_branch(
        session, workflow_id=workflow_id, branch_id=against
    )
    _require_comparable_checkpoint_lineage(branch, other)
    comparison_record = _branch_comparison_record(
        workflow_id=workflow_id, branch=branch, other=other
    )
    request_digest = _operation_digest(
        {
            "workflowId": workflow_id,
            "branchId": branch.branch_id,
            "againstBranchId": other.branch_id,
        }
    )
    branch_head = _checkpoint_branch_head_identity(branch)
    other_head = _checkpoint_branch_head_identity(other)
    branch_promotion_evidence_digest = _operation_digest(branch.promotion_evidence or {})
    other_promotion_evidence_digest = _operation_digest(other.promotion_evidence or {})
    idempotency_key = (
        f"checkpoint_branch.compare:v{comparison_record.get('schemaVersion', 1)}:"
        f"{branch.branch_id}:{branch_head}:"
        f"promotion:{branch_promotion_evidence_digest}:"
        f"against:{other.branch_id}:{other_head}:"
        f"promotion:{other_promotion_evidence_digest}"
    )
    existing_op = await session.execute(
        select(WorkflowCheckpointBranchOperation).where(
            WorkflowCheckpointBranchOperation.workflow_id == workflow_id,
            WorkflowCheckpointBranchOperation.idempotency_key == idempotency_key,
        )
    )
    operation = existing_op.scalar_one_or_none()
    if operation is None:
        comparison_artifact_refs = comparison_record.get("artifactRefs") or {}
        for artifact_kind, artifact_ref in comparison_artifact_refs.items():
            await _record_checkpoint_branch_artifact_ref(
                session=session,
                branch_id=branch.branch_id,
                artifact_kind=(
                    f"{artifact_kind}:{other.branch_id}:"
                    f"{comparison_record.get('digest') or ''}"
                ),
                artifact_ref=str(artifact_ref),
                digest=str(comparison_record.get("digest") or ""),
            )
        session.add(
            WorkflowCheckpointBranchOperation(
                workflow_id=workflow_id,
                branch_id=branch.branch_id,
                operation="checkpoint_branch.compare",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                response_payload=comparison_record,
            )
        )
        await session.commit()
    else:
        comparison_record = dict(operation.response_payload or comparison_record)
    return CheckpointBranchCompareResponse(
        branchId=branch.branch_id,
        againstBranchId=other.branch_id,
        workflowId=workflow_id,
        branchState=branch.state,
        againstState=other.state,
        branchHeadStepExecutionId=branch.current_head_step_execution_id,
        againstHeadStepExecutionId=other.current_head_step_execution_id,
        summaryRef=comparison_record["summaryRef"],
        diagnosticsRefs=list(comparison_record.get("diagnosticsRefs") or []),
        comparisonRecord=comparison_record,
    )


@router.post(
    "/{workflow_id}/checkpoint-branches/{branch_id}/promote",
    response_model=CheckpointBranchModel,
)
async def promote_checkpoint_branch(
    workflow_id: str,
    branch_id: str,
    payload: CheckpointBranchPromoteRequest,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> CheckpointBranchModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    branch = await _load_checkpoint_branch(
        session, workflow_id=workflow_id, branch_id=branch_id
    )
    request_digest = _operation_digest(payload)
    existing_op = await session.execute(
        select(WorkflowCheckpointBranchOperation).where(
            WorkflowCheckpointBranchOperation.workflow_id == workflow_id,
            WorkflowCheckpointBranchOperation.idempotency_key
            == payload.idempotency_key,
        )
    )
    operation = existing_op.scalar_one_or_none()
    if operation is not None:
        if (
            operation.operation != "checkpoint_branch.promote"
            or operation.request_digest != request_digest
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "idempotency_key_conflict"},
            )
        if operation.response_payload.get("outcome") == "rejected":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": operation.response_payload.get("code"),
                    "reason": operation.response_payload.get("reason"),
                },
            )
        return _branch_to_model(branch)

    async def _reject_promotion(*, code: str, reason: str) -> None:
        session.add(
            WorkflowCheckpointBranchOperation(
                workflow_id=workflow_id,
                branch_id=branch.branch_id,
                operation="checkpoint_branch.promote",
                idempotency_key=payload.idempotency_key,
                request_digest=request_digest,
                response_payload={
                    "outcome": "rejected",
                    "code": code,
                    "reason": reason,
                    "branchId": branch.branch_id,
                },
            )
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": code, "reason": reason},
        )

    if branch.state in {"archived", "superseded"}:
        await _reject_promotion(
            code="invalid_branch_state",
            reason=branch.state,
        )
    if not branch.current_head_step_execution_id:
        await _reject_promotion(
            code="checkpoint_head_missing",
            reason="checkpoint_head_missing",
        )
    if (
        payload.expected_head_step_execution_id
        != branch.current_head_step_execution_id
    ):
        await _reject_promotion(
            code="expected_head_mismatch",
            reason="expected_head_step_execution_mismatch",
        )
    if not branch.current_head_checkpoint_ref:
        await _reject_promotion(
            code="checkpoint_invalidity",
            reason="head_checkpoint_ref_required",
        )
    if branch.current_head_commit and not payload.expected_head_commit:
        await _reject_promotion(
            code="expected_head_mismatch",
            reason="expected_head_commit_required",
        )
    if (
        payload.expected_head_commit
        and payload.expected_head_commit != branch.current_head_commit
    ):
        await _reject_promotion(
            code="expected_head_mismatch",
            reason="expected_head_commit_mismatch",
        )
    if payload.policy_evidence.get("freshHeadValidated") is not True:
        await _reject_promotion(
            code="expected_head_mismatch",
            reason="fresh_branch_head_validation_required",
        )
    budget_state = str(
        payload.policy_evidence.get("budgetStatus")
        or payload.policy_evidence.get("budget_state")
        or ""
    ).strip().lower()
    if payload.policy_evidence.get("budgetExhausted") is True or budget_state in {
        "budget_exhausted",
        "exhausted",
    }:
        await _reject_promotion(
            code="budget_exhausted",
            reason="budget_exhausted",
        )
    accepted_head_step_execution_id = payload.accepted_output_refs.get(
        "headStepExecutionId"
    )
    if (
        accepted_head_step_execution_id
        and accepted_head_step_execution_id != branch.current_head_step_execution_id
    ):
        await _reject_promotion(
            code="accepted_output_refs_mismatch",
            reason="head_step_execution_id_mismatch",
        )
    accepted_head_checkpoint_ref = payload.accepted_output_refs.get("headCheckpointRef")
    if (
        accepted_head_checkpoint_ref
        and branch.current_head_checkpoint_ref
        and accepted_head_checkpoint_ref != branch.current_head_checkpoint_ref
    ):
        await _reject_promotion(
            code="accepted_output_refs_mismatch",
            reason="head_checkpoint_ref_mismatch",
        )
    gate_verdict = str(
        payload.gate_evidence.get("verdict")
        or payload.gate_evidence.get("status")
        or ""
    ).strip().lower()
    if gate_verdict not in _PASSING_GATE_VERDICTS:
        await _reject_promotion(
            code="side_effect_policy_blocked",
            reason="gate_evidence_not_passing",
        )
    side_effect_state = str(
        payload.side_effect_disposition.get("status")
        or payload.side_effect_disposition.get("disposition")
        or ""
    )
    if side_effect_state not in _SAFE_PROMOTION_SIDE_EFFECT_STATES:
        await _reject_promotion(
            code="side_effect_policy_blocked",
            reason="side_effect_disposition_required",
        )
    if payload.policy_requires_approval and not payload.approval_evidence:
        await _reject_promotion(
            code="approval_required",
            reason="approval_required",
        )
    promoted_sibling_result = await session.execute(
        select(WorkflowCheckpointBranch).where(
            WorkflowCheckpointBranch.workflow_id == workflow_id,
            WorkflowCheckpointBranch.branch_id != branch.branch_id,
            WorkflowCheckpointBranch.source_checkpoint_ref == branch.source_checkpoint_ref,
            WorkflowCheckpointBranch.state == "promoted",
        )
    )
    if promoted_sibling_result.scalar_one_or_none() is not None:
        await _reject_promotion(
            code="promoted_branch_conflict",
            reason="promoted_branch_conflict",
        )
    now = datetime.now(UTC)
    promoted_turn = await _latest_branch_turn_for_step_execution(
        session=session,
        branch_id=branch.branch_id,
        step_execution_id=payload.expected_head_step_execution_id,
    )
    promotion_record = _promotion_record_payload(
        workflow_id=workflow_id,
        branch=branch,
        turn=promoted_turn,
        payload=payload,
        promoted_at=now,
    )
    promotion_digest = _operation_digest(promotion_record)
    promotion_record_ref = _checkpoint_branch_operation_artifact_ref(
        operation="checkpoint_branch.promote",
        workflow_id=workflow_id,
        branch_id=branch.branch_id,
        artifact_kind="output.branch_promotion.record.json",
        digest=promotion_digest,
    )
    downstream_invalidation_ref = _checkpoint_branch_operation_artifact_ref(
        operation="checkpoint_branch.promote",
        workflow_id=workflow_id,
        branch_id=branch.branch_id,
        artifact_kind="output.branch_promotion.downstream_invalidation.json",
        digest=promotion_digest,
    )
    promotion_record["artifactRefs"] = {
        "output.branch_promotion.record.json": promotion_record_ref,
        "output.branch_promotion.downstream_invalidation.json": (
            downstream_invalidation_ref
        ),
    }
    promotion_record["promotionRecordRef"] = promotion_record_ref
    promotion_record["downstreamInvalidationRef"] = downstream_invalidation_ref
    promotion_record["digest"] = promotion_digest
    branch.state = "promoted"
    branch.current_head_step_execution_id = payload.expected_head_step_execution_id
    branch.current_head_commit = (
        payload.expected_head_commit or branch.current_head_commit
    )
    branch.promotion_evidence = {
        "promotionRecordRef": promotion_record_ref,
        "downstreamInvalidationRef": downstream_invalidation_ref,
        "acceptedOutputRefs": promotion_record["acceptedOutputRefs"],
        "gitEvidence": promotion_record["gitEvidence"],
        "gateEvidence": payload.gate_evidence,
        "sideEffectDisposition": payload.side_effect_disposition,
        "downstreamInvalidation": promotion_record["downstreamInvalidation"],
        "approvalEvidence": payload.approval_evidence,
        "policyEvidence": promotion_record["policyEvidence"],
        "artifactRefs": promotion_record["artifactRefs"],
    }
    branch.promoted_at = now
    await _record_checkpoint_branch_artifact_ref(
        session=session,
        branch_id=branch.branch_id,
        branch_turn_id=promoted_turn.branch_turn_id if promoted_turn else None,
        artifact_kind="output.branch_promotion.record.json",
        artifact_ref=promotion_record_ref,
        digest=promotion_digest,
    )
    await _record_checkpoint_branch_artifact_ref(
        session=session,
        branch_id=branch.branch_id,
        branch_turn_id=promoted_turn.branch_turn_id if promoted_turn else None,
        artifact_kind="output.branch_promotion.downstream_invalidation.json",
        artifact_ref=downstream_invalidation_ref,
        digest=promotion_digest,
    )
    session.add(
        WorkflowCheckpointBranchOperation(
            workflow_id=workflow_id,
            branch_id=branch.branch_id,
            branch_turn_id=promoted_turn.branch_turn_id if promoted_turn else None,
            operation="checkpoint_branch.promote",
            idempotency_key=payload.idempotency_key,
            request_digest=request_digest,
            response_payload=promotion_record,
        )
    )
    await session.commit()
    await session.refresh(branch)
    return _branch_to_model(branch)


@router.post(
    "/{workflow_id}/checkpoint-branches/{branch_id}/archive",
    response_model=CheckpointBranchModel,
)
async def archive_checkpoint_branch(
    workflow_id: str,
    branch_id: str,
    payload: CheckpointBranchArchiveRequest,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> CheckpointBranchModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    branch = await _load_checkpoint_branch(
        session, workflow_id=workflow_id, branch_id=branch_id
    )
    request_digest = _operation_digest(payload)
    existing_op = await session.execute(
        select(WorkflowCheckpointBranchOperation).where(
            WorkflowCheckpointBranchOperation.workflow_id == workflow_id,
            WorkflowCheckpointBranchOperation.idempotency_key
            == payload.idempotency_key,
        )
    )
    operation = existing_op.scalar_one_or_none()
    if operation is not None:
        if (
            operation.operation != "checkpoint_branch.archive"
            or operation.request_digest != request_digest
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "idempotency_key_conflict"},
            )
        return _branch_to_model(branch)
    branch.state = "archived"
    branch.archived_at = datetime.now(UTC)
    branch.archive_reason = payload.reason
    session.add(
        WorkflowCheckpointBranchOperation(
            workflow_id=workflow_id,
            branch_id=branch.branch_id,
            operation="checkpoint_branch.archive",
            idempotency_key=payload.idempotency_key,
            request_digest=request_digest,
            response_payload={"branchId": branch.branch_id, "state": "archived"},
        )
    )
    await session.commit()
    await session.refresh(branch)
    return _branch_to_model(branch)


@router.post(
    "/{workflow_id}/checkpoint-branches/{branch_id}/publish",
    response_model=CheckpointBranchModel,
)
async def publish_checkpoint_branch(
    workflow_id: str,
    branch_id: str,
    payload: CheckpointBranchPublishRequest,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> CheckpointBranchModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    branch = await _load_checkpoint_branch(
        session, workflow_id=workflow_id, branch_id=branch_id
    )
    if branch.state in {"archived", "superseded"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_branch_state", "state": branch.state},
        )
    if payload.provider != "github":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "provider_continuation_unsupported"},
        )
    if _is_protected_ref(payload.head_branch):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "protected_branch_ref", "reason": "protected_branch_ref"},
        )
    request_digest = _operation_digest(payload)
    existing_op = await session.execute(
        select(WorkflowCheckpointBranchOperation).where(
            WorkflowCheckpointBranchOperation.workflow_id == workflow_id,
            WorkflowCheckpointBranchOperation.idempotency_key
            == payload.idempotency_key,
        )
    )
    operation = existing_op.scalar_one_or_none()
    if operation is not None:
        if (
            operation.operation != "checkpoint_branch.publish"
            or operation.request_digest != request_digest
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "idempotency_key_conflict"},
            )
        return _branch_to_model(branch)
    branch.git_repository = payload.repository
    branch.git_base_branch = payload.base_branch
    branch.git_work_branch = payload.head_branch
    branch.publish_status = "published"
    session.add(
        WorkflowCheckpointBranchOperation(
            workflow_id=workflow_id,
            branch_id=branch.branch_id,
            operation="checkpoint_branch.publish",
            idempotency_key=payload.idempotency_key,
            request_digest=request_digest,
            response_payload={
                "branchId": branch.branch_id,
                "publishStatus": "published",
            },
        )
    )
    await session.commit()
    await session.refresh(branch)
    return _branch_to_model(branch)


@router.get("/{workflow_id}", response_model=ExecutionModel)
async def describe_execution(
    workflow_id: str,
    response: Response,
    source: Optional[str] = Query(None),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
    temporal_client: Client = Depends(get_temporal_client),
) -> ExecutionModel:
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)

    source_is_temporal = source == "temporal"
    use_projection_read = source_is_temporal
    temporal_sync_unavailable = False

    if settings.temporal.temporal_authoritative_read_enabled or source_is_temporal:
        try:
            client = temporal_client
            await execution_sync.fetch_and_sync_execution(
                session,
                canonical_workflow_id,
                client,
            )
            await session.commit()
            # Return the synced projection to avoid clobbering it with stale source data.
            use_projection_read = True
        except RPCError as exc:
            temporal_sync_unavailable = True
            await session.rollback()
            logger.warning(
                "Failed to sync execution %s from Temporal: %s",
                canonical_workflow_id,
                exc,
                exc_info=True,
            )
        except Exception as exc:
            await session.rollback()
            logger.warning(
                "Failed to sync execution %s from Temporal: %s",
                canonical_workflow_id,
                exc,
                exc_info=True,
            )

    try:
        record = await _get_owned_execution(
            service=service,
            workflow_id=workflow_id,
            user=user,
            include_orphaned_projection=use_projection_read,
        )
    except HTTPException as exc:
        if (
            source_is_temporal
            and temporal_sync_unavailable
            and exc.status_code == status.HTTP_404_NOT_FOUND
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "temporal_unavailable",
                    "message": "Temporal service unavailable.",
                },
            ) from exc
        raise
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    execution = _serialize_execution(record, user=user)
    execution = await _enrich_execution_dependencies(execution, service=service)
    execution = await _hydrate_related_run_metadata(
        execution,
        session=session,
        user=user,
    )
    execution = await _hydrate_provider_profile_metadata(execution, session)
    if execution.workflow_type == "MoonMind.UserWorkflow":
        if _execution_uses_live_workflow_queries(execution):
            progress, queried_run_id = await _load_execution_progress(
                temporal_client=temporal_client,
                workflow_id=canonical_workflow_id,
            )
            update: dict[str, object] = {"progress": progress}
            if queried_run_id:
                update["run_id"] = queried_run_id
                update["temporal_run_id"] = queried_run_id
            execution = execution.model_copy(update=update)
        execution = await _enrich_execution_merge_automation(
            execution,
            temporal_client=temporal_client,
        )
    execution = await _hydrate_execution_report_projection(
        execution,
        session=session,
        user=user,
    )
    if not execution.agent_run_id:
        agent_run_ids = await asyncio.to_thread(
            _resolve_agent_run_ids_from_managed_store,
            (execution.workflow_id,),
        )
        agent_run_id = agent_run_ids.get(execution.workflow_id)
        if agent_run_id:
            execution = execution.model_copy(update={"agent_run_id": agent_run_id})
    return execution


class ContinueRemediationBudgetProposal(BaseModel):
    """Operator-selected limits bounded by the server-owned grant."""

    model_config = {"populate_by_name": True, "extra": "forbid"}

    max_attempts: int = Field(alias="maxAttempts", ge=1)
    max_consecutive_no_progress_attempts: int = Field(
        alias="maxConsecutiveNoProgressAttempts", ge=1
    )


class ContinueRemediationRequest(BaseModel):
    """Select the frozen control-stop evidence owned by the source execution."""

    model_config = {"populate_by_name": True, "extra": "forbid"}

    proposed_continuation_budget: ContinueRemediationBudgetProposal | None = Field(
        None, alias="proposedContinuationBudget"
    )
    instruction_changes_ref: str | None = Field(
        None, alias="instructionChangesRef", min_length=1
    )
    instruction_changes_digest: str | None = Field(
        None, alias="instructionChangesDigest", min_length=1
    )

    @model_validator(mode="after")
    def _paired_instruction_changes(self) -> "ContinueRemediationRequest":
        if bool(self.instruction_changes_ref) != bool(
            self.instruction_changes_digest
        ):
            raise ValueError(
                "instruction changes require both a reference and digest"
            )
        return self


class ContinueRemediationResponse(BaseModel):
    """Stable linked destination returned for both first and duplicate submissions."""

    model_config = {"populate_by_name": True}

    source_workflow_id: str = Field(alias="sourceWorkflowId")
    source_run_id: str = Field(alias="sourceRunId")
    control_stop_id: str = Field(alias="controlStopId")
    destination_workflow_id: str = Field(alias="destinationWorkflowId")
    workspace_head_ref: str = Field(alias="workspaceHeadRef")
    remaining_work_ref: str = Field(alias="remainingWorkRef")
    created: bool


@router.post(
    "/{workflow_id}/actions/continue-remediation",
    response_model=ContinueRemediationResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
)
async def continue_remediation(
    workflow_id: str,
    payload: ContinueRemediationRequest,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
    starter: TemporalControlStopContinuationStarter = Depends(
        get_control_stop_continuation_starter
    ),
    _actions_enabled: None = Depends(_ensure_actions_enabled),
) -> ContinueRemediationResponse:
    """Admit one deterministic continuation from authoritative frozen evidence."""

    continuation_metrics = get_metrics_emitter()
    continuation_metrics.increment(
        "control_stop_continuation.admission_total",
        tags={"outcome": "attempt"},
    )
    source = await _get_owned_execution(
        service=service,
        workflow_id=workflow_id,
        user=user,
    )
    source_run_id = str(getattr(source, "run_id", "") or "").strip()
    if not source_run_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "control_stop_source_run_missing",
                "message": "The source execution has no authoritative run identity.",
            },
        )

    try:
        repository = SqlControlStopContinuationRepository(session)
        control_stop_id, evidence = await repository.load_source_identity(
            source_workflow_id=source.workflow_id,
            source_run_id=source_run_id,
        )
        contract = ControlStopContinuationContract.model_validate(
            dict(evidence.contract_payload)
        )
        selected_budget = contract.continuation_budget
        if payload.proposed_continuation_budget is not None:
            selected_budget = ContinuationBudgetGrant(
                grantId=contract.continuation_budget.grant_id,
                maxAttempts=payload.proposed_continuation_budget.max_attempts,
                maxConsecutiveNoProgressAttempts=(
                    payload.proposed_continuation_budget
                    .max_consecutive_no_progress_attempts
                ),
            )
        reservation = await admit_control_stop_continuation(
            source_workflow_id=source.workflow_id,
            source_run_id=source_run_id,
            control_stop_id=control_stop_id,
            continuation_budget=selected_budget,
            instruction_changes_ref=payload.instruction_changes_ref,
            instruction_changes_digest=payload.instruction_changes_digest,
            repository=repository,
            starter=starter,
        )
    except (ControlStopContinuationError, ValidationError) as exc:
        continuation_metrics.increment(
            "control_stop_continuation.admission_total",
            tags={"outcome": "rejected"},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "control_stop_continuation_rejected",
                "message": str(exc),
            },
        ) from exc

    continuation_metrics.increment(
        "control_stop_continuation.admission_total",
        tags={"outcome": "created" if reservation.created else "reconciled"},
    )
    return ContinueRemediationResponse(
        sourceWorkflowId=reservation.source_workflow_id,
        sourceRunId=reservation.source_run_id,
        controlStopId=reservation.control_stop_id,
        destinationWorkflowId=reservation.destination_workflow_id,
        workspaceHeadRef=reservation.workspace_head_ref,
        remainingWorkRef=reservation.remaining_work_ref,
        created=reservation.created,
    )


@router.post(
    "/{workflow_id}/update",
    response_model=UpdateExecutionResponse,
    response_model_exclude_none=True,
)
async def update_execution(
    workflow_id: str,
    payload: UpdateExecutionRequest,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
    _actions_enabled: None = Depends(_ensure_actions_enabled),
) -> UpdateExecutionResponse:
    record = await _get_owned_execution(
        service=service,
        workflow_id=workflow_id,
        user=user,
    )
    is_task_editing_update = payload.update_name in _TASK_EDITING_UPDATE_NAMES
    if is_task_editing_update:
        _emit_task_editing_metric(
            "submit_attempt",
            update_name=payload.update_name,
            workflow_type=getattr(record, "workflow_type", None),
            state=getattr(record, "state", None),
        )
        logger.info(
            "Temporal task editing update attempt",
            extra={
                "event": "temporal_workflow_editing.submit_attempt",
                "workflow_id": record.workflow_id,
                "update_name": payload.update_name,
                "workflow_type": _enum_value(getattr(record, "workflow_type", None)),
                "state": _enum_value(getattr(record, "state", None)),
                "has_input_artifact_ref": bool(payload.input_artifact_ref),
                "has_parameters_patch": bool(payload.parameters_patch),
            },
        )

    try:
        update_result = await service.update_execution(
            workflow_id=workflow_id,
            update_name=payload.update_name,
            input_artifact_ref=payload.input_artifact_ref,
            plan_artifact_ref=payload.plan_artifact_ref,
            parameters_patch=payload.parameters_patch,
            title=payload.title,
            new_manifest_artifact_ref=payload.new_manifest_artifact_ref,
            mode=payload.mode,
            max_concurrency=payload.max_concurrency,
            node_ids=payload.node_ids,
            idempotency_key=payload.idempotency_key,
        )
    except TemporalExecutionValidationError as exc:
        if is_task_editing_update:
            _emit_task_editing_metric(
                "submit_result",
                update_name=payload.update_name,
                workflow_type=getattr(record, "workflow_type", None),
                state=getattr(record, "state", None),
                result="failure",
                reason="validation",
            )
            logger.info(
                "Temporal task editing update rejected",
                extra={
                    "event": "temporal_workflow_editing.submit_result",
                    "workflow_id": record.workflow_id,
                    "update_name": payload.update_name,
                    "result": "failure",
                    "reason": "validation",
                },
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_update_request",
                "message": str(exc),
            },
        ) from exc

    response_workflow_id = (
        str(update_result.get("workflow_id") or "").strip() or record.workflow_id
    )
    refreshed_record = await service.describe_execution(response_workflow_id)
    snapshot_ref = ""
    if is_task_editing_update:
        exact_rerun = (
            payload.update_name == "RequestRerun"
            and payload.input_artifact_ref is None
            and payload.plan_artifact_ref is None
            and payload.parameters_patch is None
        )
        if exact_rerun:
            snapshot_ref = await _reuse_original_task_input_snapshot_from_source(
                session=session,
                source_record=record,
                target_record=refreshed_record,
            )
        else:
            snapshot_ref = await _persist_original_workflow_input_snapshot_from_parameters(
                session=session,
                record=refreshed_record,
                user=user,
                parameters=dict(getattr(refreshed_record, "parameters", None) or {}),
                source_kind=(
                    "rerun" if payload.update_name == "RequestRerun" else "edit"
                ),
                source_workflow_id=record.workflow_id,
                source_run_id=getattr(record, "run_id", None),
                input_artifact_ref=payload.input_artifact_ref,
            )
    if is_task_editing_update:
        accepted = bool(update_result.get("accepted", True))
        applied = str(update_result.get("applied") or "").strip() or None
        result = "success" if accepted else "failure"
        reason = None if accepted else "rejected"
        _emit_task_editing_metric(
            "submit_result",
            update_name=payload.update_name,
            workflow_type=getattr(refreshed_record, "workflow_type", None),
            state=getattr(refreshed_record, "state", None),
            result=result,
            reason=reason,
            applied=applied,
        )
        logger.info(
            "Temporal task editing update result",
            extra={
                "event": "temporal_workflow_editing.submit_result",
                "workflow_id": refreshed_record.workflow_id,
                "update_name": payload.update_name,
                "result": result,
                "reason": reason,
                "applied": applied,
            },
        )
    if snapshot_ref:
        await session.flush()
        if isinstance(
            refreshed_record,
            (TemporalExecutionRecord, TemporalExecutionCanonicalRecord),
        ):
            await session.refresh(refreshed_record)
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )

    execution = _serialize_execution(refreshed_record, user=user)
    refreshed_at = _compatibility_refreshed_at(refreshed_record)
    if snapshot_ref:
        await session.commit()

    return UpdateExecutionResponse(
        **update_result,
        execution=execution,
        refresh=ExecutionRefreshEnvelope(
            patched_execution=True,
            list_stale=True,
            refetch_suggested=True,
            refreshed_at=refreshed_at,
        ),
    )

@router.get(
    "/{workflow_id}/manifest-status",
    response_model=ManifestStatusSnapshotModel,
)
async def describe_manifest_status(
    workflow_id: str,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ManifestStatusSnapshotModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    try:
        return await service.describe_manifest_status(workflow_id)
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_manifest_status_request",
                "message": str(exc),
            },
        ) from exc

@router.get(
    "/{workflow_id}/manifest-nodes",
    response_model=ManifestNodePageModel,
)
async def list_manifest_node_page(
    workflow_id: str,
    state: Optional[str] = Query(None, alias="state"),
    cursor: Optional[str] = Query(None, alias="cursor"),
    limit: int = Query(50, alias="limit", ge=1, le=200),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ManifestNodePageModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    try:
        return await service.list_manifest_nodes(
            workflow_id,
            state=state,
            cursor=cursor,
            limit=limit,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_manifest_nodes_request",
                "message": str(exc),
            },
        ) from exc

@router.post(
    "/{workflow_id}/integration",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def configure_integration_monitoring(
    workflow_id: str,
    payload: ConfigureIntegrationMonitoringRequest,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    try:
        record = await service.configure_integration_monitoring(
            workflow_id=workflow_id,
            integration_name=payload.integration_name,
            correlation_id=payload.correlation_id,
            external_operation_id=payload.external_operation_id,
            normalized_status=payload.normalized_status,
            provider_status=payload.provider_status,
            callback_supported=payload.callback_supported,
            callback_correlation_key=payload.callback_correlation_key,
            recommended_poll_seconds=payload.recommended_poll_seconds,
            external_url=payload.external_url,
            provider_summary=payload.provider_summary,
            result_refs=payload.result_refs,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_integration_monitoring_request",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record, user=user)

@router.post(
    "/{workflow_id}/integration/poll",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def record_integration_poll(
    workflow_id: str,
    payload: PollIntegrationRequest,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    try:
        record = await service.record_integration_poll(
            workflow_id=workflow_id,
            normalized_status=payload.normalized_status,
            provider_status=payload.provider_status,
            observed_at=payload.observed_at,
            recommended_poll_seconds=payload.recommended_poll_seconds,
            external_url=payload.external_url,
            provider_summary=payload.provider_summary,
            result_refs=payload.result_refs,
            completed_wait_cycles=payload.completed_wait_cycles,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_integration_poll_request",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record, user=user)

@router.post(
    "/{workflow_id}/signal",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def signal_execution(
    workflow_id: str,
    payload: SignalExecutionRequest,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    _actions_enabled: None = Depends(_ensure_actions_enabled),
) -> ExecutionModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    try:
        record = await service.signal_execution(
            workflow_id=workflow_id,
            signal_name=payload.signal_name,
            payload=payload.payload,
            payload_artifact_ref=payload.payload_artifact_ref,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "signal_rejected",
                "message": str(exc),
            },
        ) from exc

    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    return _serialize_execution(record, user=user)

@router.post(
    "/{workflow_id}/cancel",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_execution(
    workflow_id: str,
    response: Response,
    payload: CancelExecutionRequest | None = None,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    _actions_enabled: None = Depends(_ensure_actions_enabled),
) -> ExecutionModel:
    await _get_owned_execution(
        service=service,
        workflow_id=workflow_id,
        user=user,
        include_orphaned_projection=True,
        use_cancel_target_fallback=True,
    )

    request = payload or CancelExecutionRequest()
    record = await service.cancel_execution(
        workflow_id=workflow_id,
        reason=request.reason,
        graceful=request.graceful,
        action=request.action,
    )
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    return _serialize_execution(record, user=user)

@router.post(
    "/{workflow_id}/reschedule",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reschedule_execution(
    workflow_id: str,
    payload: RescheduleExecutionRequest,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    _actions_enabled: None = Depends(_ensure_actions_enabled),
) -> ExecutionModel:
    record = await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    if record.state != MoonMindWorkflowState.SCHEDULED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "reschedule_rejected",
                "message": f"Cannot reschedule workflow in state {record.state.value}",
            },
        )
    
    try:
        await get_temporal_client_adapter().send_reschedule_signal(
            record.workflow_id, payload.scheduled_for
        )
    except Exception as exc:
        logger.warning("Failed to send reschedule signal for workflow %s", record.workflow_id, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "signal_failed",
                "message": "Failed to send reschedule signal to Temporal.",
            },
        ) from exc

    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    
    # We shouldn't rely strictly on describing the Execution immediately because Temporal signal is async.
    # But let's return the updated record locally updated for the user.
    if isinstance(record, TemporalExecutionRecord):
        record.scheduled_for = payload.scheduled_for
        await service._session.commit()
        await service._session.refresh(record)

    return _serialize_execution(record, user=user)

@router.post(
    "/{workflow_id}/rerun",
    response_model=ExecutionModel,
    status_code=status.HTTP_201_CREATED,
)
async def rerun_execution(
    workflow_id: str,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
    _submit_enabled: None = Depends(_ensure_submit_enabled),
) -> ExecutionModel:
    """Rerun an existing failed/completed workflow with the same parameters.

    This endpoint fetches the original execution's parameters and creates
    a new workflow execution with identical settings.
    """
    from uuid import uuid4 as _uuid4

    # Fetch the original execution
    original = await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    # Fetch the canonical record to get full initial_parameters
    canonical = await session.get(TemporalExecutionCanonicalRecord, workflow_id)

    if canonical is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "canonical_record_not_found",
                "message": f"Canonical record not found for workflow {workflow_id}. Cannot rerun.",
            },
        )

    # Use canonical parameters with rerun-specific sanitization to avoid carrying
    # task dependency edges and recovery metadata from a prior execution.
    initial_params = service._full_rerun_parameters(canonical.parameters or {})

    # Generate a new idempotency key based on the original workflow ID
    new_idempotency_key = f"rerun:{workflow_id}:{_uuid4()}"

    try:
        record = await service.create_execution(
            workflow_type=canonical.workflow_type.value,
            owner_id=canonical.owner_id or user.id,
            owner_type=canonical.owner_type.value if canonical.owner_type else "user",
            title=canonical.memo.get("title") if canonical.memo else None,
            input_artifact_ref=canonical.input_ref,
            plan_artifact_ref=canonical.plan_ref,
            manifest_artifact_ref=canonical.manifest_ref,
            failure_policy=None,
            initial_parameters=initial_params,
            idempotency_key=new_idempotency_key,
            repository=None,
            integration=None,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "rerun_validation_failed",
                "message": str(exc),
            },
        ) from exc

    snapshot_ref = await _persist_original_workflow_input_snapshot_from_parameters(
        session=session,
        record=record,
        user=user,
        parameters=dict(initial_params),
        source_kind="rerun",
        source_workflow_id=canonical.workflow_id,
        source_run_id=canonical.run_id,
        input_artifact_ref=canonical.input_ref,
    )

    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )

    execution = _serialize_execution(record, user=user)
    if snapshot_ref:
        await session.commit()
    return execution


_RECOVERY_PAYLOAD_FORBIDDEN_FIELDS = {
    "task",
    "instructions",
    "steps",
    "attachments",
    "inputAttachments",
    "runtime",
    "targetRuntime",
    "publishMode",
    "branch",
    "startingBranch",
    "targetBranch",
    "presets",
    "dependencies",
    "model",
    "requestedModel",
    "effort",
    "parametersPatch",
    "inputArtifactRef",
    "planArtifactRef",
    "manifestArtifactRef",
}


def _reject_recovery_task_payload_edits(
    request: RecoverFromFailedStepRequest,
    *,
    action: str,
) -> None:
    extras = request.model_extra or {}
    forbidden = sorted(key for key in extras if key in _RECOVERY_PAYLOAD_FORBIDDEN_FIELDS)
    if forbidden:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "recovery_payload_not_allowed",
                "message": (
                    f"{action} does not accept edited workflow payload fields. "
                    "Use an execution update for changes."
                ),
                "fields": forbidden,
            },
        )


def _checkpoint_failed_step_execution(checkpoint_payload: Mapping[str, Any]) -> int | None:
    failed_step = checkpoint_payload.get("failedStep")
    if not isinstance(failed_step, Mapping):
        return None
    value = failed_step.get("executionOrdinal")
    if value is None:
        value = failed_step.get("attempt")
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _reject_recovery_contract_mismatch(
    request: RecoverFromFailedStepRequest,
    *,
    canonical: TemporalExecutionCanonicalRecord,
    checkpoint_payload: Mapping[str, Any],
) -> None:
    expected_run_id = str(canonical.run_id or "")
    mismatches: list[str] = []
    if request.source_workflow_id and request.source_workflow_id != canonical.workflow_id:
        mismatches.append("sourceWorkflowId")
    if request.source_run_id and request.source_run_id != expected_run_id:
        mismatches.append("sourceRunId")

    source = checkpoint_payload.get("source")
    if isinstance(source, Mapping):
        if (
            request.source_workflow_id
            and request.source_workflow_id != source.get("workflowId")
        ):
            mismatches.append("sourceWorkflowId")
        if request.source_run_id and request.source_run_id != source.get("runId"):
            mismatches.append("sourceRunId")

    failed_step = checkpoint_payload.get("failedStep")
    if isinstance(failed_step, Mapping):
        if (
            request.logical_step_id
            and request.logical_step_id != failed_step.get("logicalStepId")
        ):
            mismatches.append("logicalStepId")
        if request.source_execution_ordinal is not None:
            checkpoint_execution = _checkpoint_failed_step_execution(checkpoint_payload)
            if checkpoint_execution != request.source_execution_ordinal:
                mismatches.append("sourceExecutionOrdinal")

    if (
        request.task_input_snapshot_ref
        and request.task_input_snapshot_ref != checkpoint_payload.get("taskInputSnapshotRef")
    ):
        mismatches.append("taskInputSnapshotRef")
    if request.plan_ref and request.plan_ref != checkpoint_payload.get("planRef"):
        mismatches.append("planRef")
    if request.plan_digest and request.plan_digest != checkpoint_payload.get("planDigest"):
        mismatches.append("planDigest")

    if mismatches:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Recovery request fields do not match checkpoint evidence.",
                "reason": "checkpoint_inconsistent",
                "fields": sorted(set(mismatches)),
            },
        )


def _checkpoint_resume_admission_for_request(
    *, canonical: TemporalExecutionCanonicalRecord,
    checkpoint_payload: Mapping[str, Any], repository: str | None,
):
    """Revalidate mutable rollout/readiness once, immediately before creation."""

    params = canonical.parameters if isinstance(canonical.parameters, Mapping) else {}
    workflow = params.get("workflow") if isinstance(params.get("workflow"), Mapping) else {}
    runtime = workflow.get("runtime") if isinstance(workflow.get("runtime"), Mapping) else {}
    runtime_id = normalize_runtime_id(
        params.get("targetRuntime")
        or runtime.get("mode")
        or runtime.get("id")
        or runtime.get("runtimeId")
        or params.get("runtimeId")
        or ""
    )
    capabilities = resolve_runtime_execution_capabilities(runtime_id)
    flags = settings.feature_flags
    generation = str(flags.checkpoint_resume_deployment_generation or "").strip()
    readiness = CheckpointResumeReadiness(
        runtimeId=runtime_id,
        deploymentGeneration=generation or "unavailable",
        captureRouteReady=flags.checkpoint_resume_capture_route_ready,
        restoreRouteReady=flags.checkpoint_resume_restore_route_ready,
        artifactStoreReady=flags.checkpoint_resume_artifact_store_ready,
        managedRunStoreReady=flags.checkpoint_resume_managed_run_store_ready,
        capabilitySetVersion=capabilities.capability_set_version,
        capabilityDigest=capabilities.capability_digest,
        checkedAt=datetime.now(UTC),
    )
    workspace = checkpoint_payload.get("recoveryWorkspace")
    workspace = workspace if isinstance(workspace, Mapping) else {}
    raw_size = workspace.get("archiveBytes", checkpoint_payload.get("archiveBytes", 0))
    try:
        archive_bytes = int(raw_size)
    except (TypeError, ValueError):
        archive_bytes = 0
    decision = evaluate_checkpoint_resume_admission(
        capabilities=capabilities,
        policy=rollout_policy_from_settings(flags),
        readiness=readiness,
        checkpoint_kind="worktree_archive",
        checkpoint_boundary="before_recovery_restoration",
        resume_phase="retry_restoration",
        archive_bytes=archive_bytes,
        owner_id=str(canonical.owner_id or "") or None,
        repository=repository,
    )
    metric_tags = bounded_checkpoint_metric_tags(
        runtime_id=runtime_id,
        generation=readiness.deployment_generation,
        outcome=decision.reason_code,
    )
    metrics = get_metrics_emitter()
    metrics.increment("checkpoint_resume.eligibility_total", tags=metric_tags)
    metrics.increment("checkpoint_resume.admission_total", tags=metric_tags)
    if not decision.admitted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Checkpoint Resume is not admitted by current rollout readiness.",
                "reason": decision.reason_code,
            },
        )
    return decision


def _canonical_execution_repository(
    canonical: TemporalExecutionCanonicalRecord,
) -> str | None:
    params = canonical.parameters if isinstance(canonical.parameters, Mapping) else {}
    task = params.get("task") if isinstance(params.get("task"), Mapping) else {}
    value = params.get("repository") or task.get("repository")
    candidate = str(value or "").strip()
    return candidate or None


def _csv_setting(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _publication_recovery_policy() -> PublicationRecoveryRolloutPolicy:
    flags = settings.feature_flags
    return PublicationRecoveryRolloutPolicy(
        enabled=flags.publication_recovery_enabled,
        shadow=flags.publication_recovery_shadow,
        canaryRepositories=_csv_setting(
            flags.publication_recovery_canary_repositories
        ),
        canaryOwnerIds=_csv_setting(flags.publication_recovery_canary_owner_ids),
        allowedModes=_csv_setting(flags.publication_recovery_allowed_modes),
        generation=flags.publication_recovery_generation,
    )


def _publication_recovery_contract_from_record(
    canonical: TemporalExecutionCanonicalRecord,
) -> PublicationRecoveryContract:
    finish_summary = (
        canonical.finish_summary_json
        if isinstance(canonical.finish_summary_json, Mapping)
        else {}
    )
    control_stop = finish_summary.get("controlStop")
    control_stop = control_stop if isinstance(control_stop, Mapping) else {}
    auxiliary = control_stop.get("auxiliaryOutcomes")
    auxiliary = auxiliary if isinstance(auxiliary, Mapping) else {}
    publication = auxiliary.get("gitPublication")
    publication = publication if isinstance(publication, Mapping) else {}
    payload = publication.get("recoveryContract")
    eligible, reason = publication_action_eligibility(payload)
    if publication.get("status") != "failed" or not eligible:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "publication_retry_not_available",
                "message": "Publication recovery is not available for this execution.",
                "reason": reason or "publication_not_failed",
            },
        )
    try:
        return PublicationRecoveryContract.model_validate(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "publication_retry_not_available",
                "message": "Publication recovery evidence is invalid.",
                "reason": "publication_recovery_contract_invalid",
            },
        ) from exc


@router.post(
    "/{workflow_id}/retry-publication",
    response_model=PublicationRecoveryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def retry_execution_publication(
    workflow_id: str,
    service: TemporalExecutionService = Depends(_get_service),
    adapter: TemporalClientAdapter = Depends(get_temporal_client_adapter),
    user: User = Depends(get_current_user()),
    _submit_enabled: None = Depends(_ensure_submit_enabled),
) -> PublicationRecoveryResponse:
    """Start or reconcile exactly one publication-only linked workflow."""

    canonical = await _get_owned_execution(
        service=service, workflow_id=workflow_id, user=user
    )
    contract = _publication_recovery_contract_from_record(canonical)
    if (
        contract.source_workflow_id != workflow_id
        or contract.source_run_id != str(canonical.run_id or "")
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "publication_retry_not_available",
                "message": "Publication recovery source identity does not match.",
                "reason": "publication_source_mismatch",
            },
        )
    policy = _publication_recovery_policy()
    reason = policy.admission_reason(
        repository=contract.intent.repository,
        owner_id=_owner_id(user),
        mode=contract.intent.mode,
    )
    if reason is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "publication_retry_not_admitted",
                "message": "Publication recovery is not admitted by current rollout policy.",
                "reason": reason,
            },
        )
    destination_id = publication_recovery_workflow_id(contract)
    started = await adapter.start_workflow(
        workflow_type="MoonMind.PublicationRecoveryV1",
        workflow_id=destination_id,
        input_args=contract.model_dump(mode="json", by_alias=True),
        memo={
            "source_workflow_id": contract.source_workflow_id,
            "source_run_id": contract.source_run_id,
            "publication_idempotency_key": (
                contract.continuation.publication_idempotency_key
            ),
            "publication_recovery_generation": policy.generation,
            "publication_semantic_context": contract.target.semantic_context,
            "publication_recovery_phase": "contract_validation",
            "publication_no_implementation_rerun": True,
            "publication_no_verification_rerun": True,
        },
    )
    return PublicationRecoveryResponse(
        sourceWorkflowId=contract.source_workflow_id,
        sourceRunId=contract.source_run_id,
        workflowId=started.workflow_id,
        runId=started.run_id,
        publicationIdempotencyKey=(
            contract.continuation.publication_idempotency_key
        ),
        rolloutGeneration=policy.generation,
    )


@router.post(
    "/{workflow_id}/recover",
    response_model=RecoverExecutionResponse,
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
async def recover_execution(
    workflow_id: str,
    request: WorkflowRecoveryTargetModel = Body(...),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    _submit_enabled: None = Depends(_ensure_submit_enabled),
) -> RecoverExecutionResponse:
    """Create a typed recovery destination without mutating its terminal source."""

    canonical = await _get_owned_execution(
        service=service, workflow_id=workflow_id, user=user
    )
    if request.source.workflow_id != canonical.workflow_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "recovery_not_available",
                "message": "Typed recovery source workflow does not match.",
                "reasons": ["RECOVERY_TARGET_IDENTITY_MISMATCH"],
            },
        )
    try:
        result = await service.create_typed_recovery_execution(
            canonical,
            recovery_target=request,
        )
    except TemporalExecutionRecoveryCheckpointError as exc:
        reasons = [reason for reason in str(exc).split(",") if reason]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "recovery_not_available",
                "message": "Typed recovery admission failed.",
                "reasons": reasons,
            },
        ) from exc
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "recovery_not_available",
                "message": str(exc),
                "reasons": ["RECOVERY_TARGET_IDENTITY_MISMATCH"],
            },
        ) from exc
    return RecoverExecutionResponse.model_validate(result)


@router.post(
    "/{workflow_id}/recover-from-failed-step",
    response_model=RecoverFromFailedStepResponse,
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
async def recover_execution_from_failed_step(
    workflow_id: str,
    request: RecoverFromFailedStepRequest = Body(...),
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
    _submit_enabled: None = Depends(_ensure_submit_enabled),
) -> RecoverFromFailedStepResponse:
    _reject_recovery_task_payload_edits(request, action="Recover from failed step")

    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    canonical = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
    if canonical is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "execution_not_found",
                "message": "Source execution was not found or is not visible.",
            },
        )
    checkpoint_ref = request.recovery_checkpoint_ref or _recovery_checkpoint_ref_from_record(
        canonical
    )
    manifest_ref = _canonical_recovery_manifest_ref(request, canonical)
    if _recovery_evidence_marked_stale(canonical):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery is not available for this execution.",
                "reason": "stale_recovery_evidence",
            },
        )
    manifest_payload = await _hydrate_failed_run_recovery_manifest_payload(
        session=session,
        user=user,
        manifest_ref=manifest_ref,
    )
    _reject_recovery_manifest_mismatch(
        request,
        canonical=canonical,
        manifest_payload=manifest_payload,
        checkpoint_ref=str(checkpoint_ref or "").strip(),
    )
    checkpoint_payload = await _hydrate_recovery_checkpoint_payload(
        session=session,
        user=user,
        checkpoint_ref=checkpoint_ref,
    )
    _reject_recovery_contract_mismatch(
        request,
        canonical=canonical,
        checkpoint_payload=checkpoint_payload,
    )
    admission = _checkpoint_resume_admission_for_request(
        canonical=canonical,
        checkpoint_payload=checkpoint_payload,
        repository=_canonical_execution_repository(canonical),
    )
    try:
        result = await service.create_failed_step_recovery_execution(
            canonical,
            recovery_checkpoint_ref=request.recovery_checkpoint_ref,
            idempotency_key=request.idempotency_key,
            checkpoint_payload=checkpoint_payload,
            failed_run_recovery_manifest_ref=manifest_ref,
            failed_run_recovery_manifest=manifest_payload,
            admitted_checkpoint_resume_decision=admission,
        )
    except TemporalExecutionRecoveryCheckpointError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery is not available for this execution.",
                "reason": _recovery_not_available_reason(exc),
            },
        ) from exc
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Failed-step recovery is not available for this execution.",
                "reason": "state_not_eligible",
            },
        ) from exc
    await session.commit()
    return RecoverFromFailedStepResponse.model_validate(result)

@router.post(
    "/{workflow_id}/recover-from-selected-step",
    response_model=RecoverFromFailedStepResponse,
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
async def recover_execution_from_selected_step(
    workflow_id: str,
    request: RecoverFromSelectedStepRequest = Body(...),
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
    _submit_enabled: None = Depends(_ensure_submit_enabled),
) -> RecoverFromFailedStepResponse:
    _reject_recovery_task_payload_edits(request, action="Recover from selected step")

    canonical = await _get_owned_execution(
        service=service, workflow_id=workflow_id, user=user
    )
    if request.source_workflow_id != canonical.workflow_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Selected-step recovery source workflow does not match.",
                "reason": "checkpoint_inconsistent",
            },
        )
    if request.source_run_id != str(canonical.run_id or ""):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Selected-step recovery source run does not match.",
                "reason": "checkpoint_inconsistent",
            },
        )
    checkpoint_ref = request.recovery_checkpoint_ref or _recovery_checkpoint_ref_from_record(
        canonical
    )
    manifest_ref = _canonical_recovery_manifest_ref(request, canonical)
    if not checkpoint_ref:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Selected-step recovery requires a valid checkpoint reference.",
                "reason": "checkpoint_missing",
            },
        )
    if _recovery_evidence_marked_stale(canonical):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Selected-step recovery is not available for this execution.",
                "reason": "stale_recovery_evidence",
            },
        )
    manifest_payload = await _hydrate_failed_run_recovery_manifest_payload(
        session=session,
        user=user,
        manifest_ref=manifest_ref,
    )
    _reject_recovery_manifest_mismatch(
        request,
        canonical=canonical,
        manifest_payload=manifest_payload,
        checkpoint_ref=str(checkpoint_ref or "").strip(),
    )
    checkpoint_payload = await _hydrate_recovery_checkpoint_payload(
        session=session,
        user=user,
        checkpoint_ref=checkpoint_ref,
    )
    _reject_recovery_contract_mismatch(
        request,
        canonical=canonical,
        checkpoint_payload=checkpoint_payload,
    )
    admission = _checkpoint_resume_admission_for_request(
        canonical=canonical,
        checkpoint_payload=checkpoint_payload,
        repository=_canonical_execution_repository(canonical),
    )
    try:
        result = await service.create_failed_step_recovery_execution(
            canonical,
            recovery_checkpoint_ref=checkpoint_ref,
            idempotency_key=request.idempotency_key,
            checkpoint_payload=checkpoint_payload,
            failed_run_recovery_manifest_ref=manifest_ref,
            failed_run_recovery_manifest=manifest_payload,
            selected_start_step_id=request.selected_start_step_id,
            admitted_checkpoint_resume_decision=admission,
        )
    except TemporalExecutionRecoveryCheckpointError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Selected-step recovery is not available for this execution.",
                "reason": _recovery_not_available_reason(exc),
            },
        ) from exc
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_not_available",
                "message": "Selected-step recovery is not available for this execution.",
                "reason": "state_not_eligible",
            },
        ) from exc
    await session.commit()
    return RecoverFromFailedStepResponse.model_validate(result)

__all__ = ["router"]
