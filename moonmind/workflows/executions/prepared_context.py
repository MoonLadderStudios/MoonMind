"""Compact target-aware prepared context contracts for runtime steps."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.config.settings import settings
from moonmind.memory.context_pack import build_memory_context_pack
from moonmind.memory.procedural import fix_patterns_to_memory_proposals

TargetKind = Literal["objective", "step"]
RetrievalStatus = Literal["captured", "skipped", "unavailable"]
BranchRuntimeContextPolicy = Literal[
    "fresh_agent_run",
    "reuse_session_new_epoch",
    "reuse_session_same_epoch",
    "external_provider_continuation",
]
MemoryProposalState = Literal[
    "proposed",
    "accepted_for_run_context",
    "applied_to_repo",
    "rejected",
    "superseded",
]
EXECUTION_CONTEXT_BUILDER_VERSION = "execution-context-builder-v1"
BRANCH_TURN_CONTEXT_BUILDER_VERSION = "branch-turn-context-builder-v1"
MM_1089_TRACEABILITY = "MM-1089"
MINIMUM_BRANCH_ARTIFACT_NAMES: tuple[str, ...] = (
    "input.branch.root_checkpoint.json",
    "input.branch.initial_instructions.md",
    "runtime.branch.context_bundle.json",
    "runtime.branch.workspace_restore.json",
    "runtime.branch.git_binding.json",
    "output.branch.summary.json",
    "output.branch.latest_head.json",
)
MINIMUM_BRANCH_TURN_ARTIFACT_NAMES: tuple[str, ...] = (
    "input.branch_turn.instructions.md",
    "runtime.branch_turn.context_bundle.json",
    "runtime.branch_turn.agent_request.json",
    "runtime.branch_turn.agent_result.json",
    "output.branch_turn.step_execution_manifest.json",
    "output.branch_turn.checkpoint.json",
    "output.branch_turn.diagnostics.json",
)

_EFFORT_COST_UNITS = {
    "minimal": 1,
    "low": 1,
    "medium": 2,
    "high": 3,
    "max": 4,
}

_RUNTIME_COST_UNITS = {
    "codex": 3,
    "codex_cli": 3,
    "claude": 3,
    "claude_code": 3,
    "gemini_cli": 2,
    "jules": 2,
}

_INLINE_ATTACHMENT_KEYS = {
    "base64",
    "bytes",
    "content",
    "data",
    "dataUrl",
    "generatedMarkdown",
    "markdown",
}
_DATA_URL_RE = re.compile(r"data:[^\s'\")]+", re.IGNORECASE)
_SECRETISH_RE = re.compile(
    r"(ghp_|github_pat_|AIza|ATATT|AKIA|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)
_RAW_DIFF_RE = re.compile(r"(^diff --git |\n@@ [-+0-9, ]+@@)", re.MULTILINE)
_LARGE_TEXT_LIMIT = 2_000
_LARGE_LOG_KEYS = {
    "log",
    "logs",
    "rawLog",
    "rawLogs",
    "stdout",
    "stderr",
    "transcript",
}
_UNSAFE_PROVIDER_PAYLOAD_KEYS = {
    "providerPayload",
    "rawProviderPayload",
    "providerResponse",
    "rawProviderResponse",
    "providerRequest",
    "rawProviderRequest",
    "messages",
    "toolCalls",
    "tool_calls",
}
_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._:-]+")


class PreparedInputEntry(BaseModel):
    """One compact prepared input ref bound to objective or one logical step."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    artifact_id: str = Field(alias="artifactId")
    filename: str | None = None
    content_type: str | None = Field(default=None, alias="contentType")
    size_bytes: int | None = Field(default=None, alias="sizeBytes")
    target_kind: TargetKind = Field(alias="targetKind")
    raw_input_ref: str = Field(alias="rawInputRef")
    derived_context_ref: str | None = Field(default=None, alias="derivedContextRef")
    workspace_path: str | None = Field(default=None, alias="workspacePath")
    status: str = "prepared"
    step_ref: str | None = Field(default=None, alias="stepRef")
    step_ordinal: int | None = Field(default=None, alias="stepOrdinal")

    @model_validator(mode="after")
    def _validate_target_binding(self) -> "PreparedInputEntry":
        if self.target_kind == "step" and not self.step_ref:
            raise ValueError("stepRef is required for step prepared inputs")
        if self.target_kind == "objective" and self.step_ref:
            raise ValueError("objective prepared inputs must not include stepRef")
        return self


class PreparedInputManifest(BaseModel):
    """Bounded manifest for all prepared inputs on a task."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    manifest_ref: str = Field(alias="manifestRef")
    entries: list[PreparedInputEntry]

    @property
    def has_entries(self) -> bool:
        return bool(self.entries)


class StepPreparedContext(BaseModel):
    """Prepared context selected for exactly one logical runtime step."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    logical_step_id: str = Field(alias="logicalStepId")
    manifest_ref: str = Field(alias="manifestRef")
    objective_context_refs: list[str] = Field(
        default_factory=list,
        alias="objectiveContextRefs",
    )
    step_context_refs: list[str] = Field(default_factory=list, alias="stepContextRefs")
    raw_input_refs: list[str] = Field(default_factory=list, alias="rawInputRefs")

    @property
    def input_refs(self) -> list[str]:
        return _dedupe_refs(
            [
                *self.objective_context_refs,
                *self.step_context_refs,
                *self.raw_input_refs,
            ]
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            "manifestRef": self.manifest_ref,
            "logicalStepId": self.logical_step_id,
            "objectiveContextRefs": list(self.objective_context_refs),
            "stepContextRefs": list(self.step_context_refs),
            "rawInputRefs": list(self.raw_input_refs),
            "inputRefs": self.input_refs,
            "targetCounts": {
                "objective": len(self.objective_context_refs),
                "step": len(self.step_context_refs),
            },
        }


class RetrievalManifest(BaseModel):
    """Compact retrieval input manifest recorded for one attempt."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: RetrievalStatus = "captured"
    query: str | None = None
    selector: dict[str, Any] | None = None
    index_version: str | None = Field(default=None, alias="indexVersion")
    returned_refs: list[str] = Field(default_factory=list, alias="returnedRefs")
    filters: dict[str, Any] = Field(default_factory=dict)
    excluded_refs: list[str] = Field(default_factory=list, alias="excludedRefs")
    compact_summaries: list[str] = Field(
        default_factory=list,
        alias="compactSummaries",
    )
    retrieval_manifest_ref: str = Field(alias="retrievalManifestRef")

    @model_validator(mode="after")
    def _validate_manifest(self) -> "RetrievalManifest":
        if (
            self.status == "captured"
            and not self.query
            and not self.selector
            and not self.returned_refs
        ):
            raise ValueError(
                "retrieval manifest requires query, selector, or returnedRefs"
            )
        _reject_unsafe_values(self.model_dump(by_alias=True), path="retrieval")
        return self


class MemoryProposal(BaseModel):
    """Compact memory proposal with explicit promotion state."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    proposal_ref: str = Field(alias="proposalRef")
    state: MemoryProposalState
    summary: str | None = None
    policy_ref: str | None = Field(default=None, alias="policyRef")

    @field_validator("proposal_ref")
    @classmethod
    def _proposal_ref_required(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("proposalRef is required")
        return candidate

    @model_validator(mode="after")
    def _validate_policy_state(self) -> "MemoryProposal":
        if (
            self.state == "applied_to_repo"
            and not str(self.policy_ref or "").strip()
        ):
            raise ValueError(
                "applied_to_repo memory proposals require an explicit policyRef"
            )
        _reject_secretish_values(self.model_dump(by_alias=True), path="memory")
        return self


class MemoryManifest(BaseModel):
    """Compact memory manifest recorded for one attempt."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    proposals: list[MemoryProposal] = Field(default_factory=list)
    memory_manifest_ref: str = Field(alias="memoryManifestRef")


class ExecutionContextBundle(BaseModel):
    """Digest-addressed context envelope for one runtime Step Execution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    workflow_id: str = Field(alias="workflowId")
    run_id: str = Field(alias="runId")
    logical_step_id: str = Field(alias="logicalStepId")
    execution_ordinal: int = Field(alias="executionOrdinal")
    reason: str = "initial_execution"
    branch: dict[str, Any] | None = Field(default=None, alias="branch")
    instruction_refs: list[str] = Field(default_factory=list, alias="instructionRefs")
    instruction_digests: dict[str, str] = Field(
        default_factory=dict,
        alias="instructionDigests",
    )
    prepared_input_refs: list[str] = Field(
        default_factory=list,
        alias="preparedInputRefs",
    )
    task_input_snapshot_ref: str | None = Field(
        default=None,
        alias="taskInputSnapshotRef",
    )
    plan_ref: str | None = Field(default=None, alias="planRef")
    plan_digest: str | None = Field(default=None, alias="planDigest")
    workspace_policy: str | None = Field(default=None, alias="workspacePolicy")
    runtime_context_policy: BranchRuntimeContextPolicy | None = Field(
        default=None,
        alias="runtimeContextPolicy",
    )
    workspace_baseline: dict[str, Any] = Field(
        default_factory=dict,
        alias="workspaceBaseline",
    )
    checkpoint_refs: dict[str, Any] = Field(
        default_factory=dict,
        alias="checkpointRefs",
    )
    prior_evidence_refs: list[str] = Field(
        default_factory=list,
        alias="priorEvidenceRefs",
    )
    bounded_summaries: list[str] = Field(
        default_factory=list,
        alias="boundedSummaries",
    )
    branch_comparison_refs: list[str] = Field(
        default_factory=list,
        alias="branchComparisonRefs",
    )
    retrieval_manifest_ref: str | None = Field(
        default=None,
        alias="retrievalManifestRef",
    )
    memory_manifest_ref: str | None = Field(default=None, alias="memoryManifestRef")
    memory_context_ref: str | None = Field(default=None, alias="memoryContextRef")
    runtime_selection: dict[str, Any] = Field(
        default_factory=dict,
        alias="runtimeSelection",
    )
    quality_gate_profile: str | None = Field(
        default=None,
        alias="qualityGateProfile",
    )
    policy_refs: dict[str, Any] = Field(default_factory=dict, alias="policyRefs")
    cost_policy: dict[str, Any] = Field(default_factory=dict, alias="costPolicy")
    portability_provenance: dict[str, Any] = Field(
        default_factory=dict,
        alias="portabilityProvenance",
    )
    context_bundle_ref: str = Field(alias="contextBundleRef")
    context_bundle_digest: str = Field(alias="contextBundleDigest")
    builder_version: str = Field(alias="builderVersion")

    @field_validator("workflow_id", "run_id", "logical_step_id", "reason")
    @classmethod
    def _required_text(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("field must be a non-empty string")
        return candidate

    @field_validator("execution_ordinal")
    @classmethod
    def _positive_execution_ordinal(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("executionOrdinal must be positive")
        return value

    @model_validator(mode="after")
    def _validate_safe_content(self) -> "ExecutionContextBundle":
        _reject_unsafe_values(self.model_dump(by_alias=True), path="executionContext")
        return self

    def to_manifest_projection(self) -> dict[str, Any]:
        return {
            "context": {
                "contextBundleRef": self.context_bundle_ref,
                "contextBundleDigest": self.context_bundle_digest,
                "builderVersion": self.builder_version,
                "taskInputSnapshotRef": self.task_input_snapshot_ref,
                "planRef": self.plan_ref,
                "planDigest": self.plan_digest,
                "branch": self.branch,
                "instructionRefs": list(self.instruction_refs),
                "instructionDigests": dict(self.instruction_digests),
                "workspacePolicy": self.workspace_policy,
                "runtimeContextPolicy": self.runtime_context_policy,
                "workspaceBaseline": self.workspace_baseline,
                "checkpointRefs": self.checkpoint_refs,
                "priorEvidenceRefs": list(self.prior_evidence_refs),
                "boundedSummaries": list(self.bounded_summaries),
                "branchComparisonRefs": list(self.branch_comparison_refs),
                "retrievalManifestRef": self.retrieval_manifest_ref,
                "memoryManifestRef": self.memory_manifest_ref,
                "memoryContextRef": self.memory_context_ref,
                "qualityGateProfile": self.quality_gate_profile,
                "policyRefs": self.policy_refs,
                "costPolicy": self.cost_policy,
                "portabilityProvenance": self.portability_provenance,
            }
        }

    def with_retrieval_manifest_ref(
        self,
        retrieval_manifest_ref: str | None,
    ) -> "ExecutionContextBundle":
        """Return a copy with a swapped retrieval ref and recomputed digest.

        ``retrievalManifestRef`` is part of the digest input, so swapping it in
        place (for example to the persisted artifact id) would leave the
        digest-addressed ``contextBundleRef``/``contextBundleDigest`` stale.
        Recompute them here using the same convention as
        ``build_execution_context_bundle`` so the bundle stays internally
        consistent.
        """

        base_payload = self.model_dump(by_alias=True)
        base_payload.pop("contextBundleRef", None)
        base_payload.pop("contextBundleDigest", None)
        base_payload["retrievalManifestRef"] = retrieval_manifest_ref
        digest = _digest_payload(base_payload)
        payload = {
            **base_payload,
            "contextBundleRef": f"execution-context-bundle://{digest}",
            "contextBundleDigest": digest,
        }
        return ExecutionContextBundle.model_validate(payload)


class PreparedContextFailure(BaseModel):
    """Bounded failure diagnostic for prepared context generation."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    logical_step_id: str | None = Field(default=None, alias="logicalStepId")
    manifest_ref: str | None = Field(default=None, alias="manifestRef")
    reason: str
    message: str

    @classmethod
    def from_exception(
        cls,
        exc: BaseException,
        *,
        logical_step_id: str | None = None,
        manifest_ref: str | None = None,
    ) -> "PreparedContextFailure":
        return cls(
            logicalStepId=logical_step_id,
            manifestRef=manifest_ref,
            reason=type(exc).__name__,
            message=_bounded_message(str(exc)),
        )


def build_prepared_input_manifest(
    payload: Mapping[str, Any],
    *,
    # legacy_run contract — manifest ref value persisted in artifacts/history;
    # renames at the MoonMind.UserWorkflow v2 cutover (MM-730).
    manifest_ref: str = "prepared-context-manifest://task-inputs",
) -> PreparedInputManifest:
    """Build a compact manifest from objective and step input attachments."""

    task_payload = _task_payload(payload)
    entries: list[PreparedInputEntry] = []

    for attachment in _attachment_sequence(task_payload.get("inputAttachments")):
        entries.append(
            _entry_from_attachment(
                attachment,
                target_kind="objective",
            )
        )

    for index, step in enumerate(_step_sequence(task_payload.get("steps")), start=1):
        step_attachments = _attachment_sequence(step.get("inputAttachments"))
        if not step_attachments:
            continue
        step_ref = _step_ref(step, index)
        for attachment in step_attachments:
            entries.append(
                _entry_from_attachment(
                    attachment,
                    target_kind="step",
                    step_ref=step_ref,
                    step_ordinal=index,
                )
            )

    return PreparedInputManifest(manifestRef=manifest_ref, entries=entries)


def select_step_prepared_context(
    manifest: PreparedInputManifest,
    *,
    logical_step_id: str,
) -> StepPreparedContext:
    """Select objective entries plus entries bound to one logical step."""

    objective_refs: list[str] = []
    step_refs: list[str] = []
    raw_refs: list[str] = []

    for entry in manifest.entries:
        if entry.target_kind == "objective":
            objective_refs.append(entry.derived_context_ref or entry.raw_input_ref)
            raw_refs.append(entry.raw_input_ref)
        elif entry.step_ref == logical_step_id:
            step_refs.append(entry.derived_context_ref or entry.raw_input_ref)
            raw_refs.append(entry.raw_input_ref)

    return StepPreparedContext(
        logicalStepId=logical_step_id,
        manifestRef=manifest.manifest_ref,
        objectiveContextRefs=_dedupe_refs(objective_refs),
        stepContextRefs=_dedupe_refs(step_refs),
        rawInputRefs=_dedupe_refs(raw_refs),
    )


def build_execution_context_bundle(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    execution_ordinal: int | None = None,
    reason: str = "initial_execution",
    branch: Mapping[str, Any] | None = None,
    instruction_refs: Sequence[Any] | None = None,
    instruction_digests: Mapping[str, Any] | None = None,
    task_input_snapshot_ref: str | None = None,
    plan_ref: str | None = None,
    plan_digest: str | None = None,
    prepared_context: StepPreparedContext | None = None,
    workspace_policy: str | None = None,
    runtime_context_policy: BranchRuntimeContextPolicy | None = None,
    workspace_baseline: Mapping[str, Any] | None = None,
    checkpoint_refs: Mapping[str, Any] | None = None,
    prior_evidence_refs: Sequence[Any] | None = None,
    bounded_summaries: Sequence[Any] | None = None,
    branch_comparison_refs: Sequence[Any] | None = None,
    runtime_selection: Mapping[str, Any] | None = None,
    quality_gate_profile: str | None = None,
    policy_refs: Mapping[str, Any] | None = None,
    retrieval: Mapping[str, Any] | None = None,
    memory_proposals: Sequence[Mapping[str, Any]] | None = None,
    memory_context: Mapping[str, Any] | None = None,
    fix_patterns: Sequence[Mapping[str, Any]] | None = None,
    builder_version: str = EXECUTION_CONTEXT_BUILDER_VERSION,
) -> ExecutionContextBundle:
    """Build a compact, digest-addressed execution context bundle."""

    prepared_input_refs = (
        prepared_context.input_refs if prepared_context is not None else []
    )
    retrieval_manifest_ref = None
    if isinstance(retrieval, Mapping) and retrieval:
        retrieval_manifest_ref = (
            build_durable_retrieval_manifest_artifact(retrieval)["artifactRef"]
        )
    memory_manifest_ref = None
    effective_memory_proposals = list(memory_proposals or [])
    if fix_patterns:
        effective_memory_proposals.extend(
            fix_patterns_to_memory_proposals(fix_patterns)
        )
    if effective_memory_proposals:
        memory_manifest_ref = build_memory_manifest(
            effective_memory_proposals
        ).memory_manifest_ref
    memory_context_ref = None
    if isinstance(memory_context, Mapping) and memory_context:
        memory_candidates = memory_context.get("candidates") or []
        raw_token_budget = memory_context.get("tokenBudget")
        memory_token_budget = (
            settings.workflow.memory_context_budget_tokens
            if raw_token_budget is None
            else int(raw_token_budget)
        )
        memory_context_ref = build_memory_context_pack(
            memory_candidates,
            token_budget=memory_token_budget,
        ).memory_context_ref

    base_payload = {
        "schemaVersion": "v1",
        "workflowId": workflow_id,
        "runId": run_id,
        "logicalStepId": logical_step_id,
        "executionOrdinal": execution_ordinal or 1,
        "reason": reason,
        "branch": _optional_mapping(branch),
        "instructionRefs": _clean_existing_refs(instruction_refs),
        "instructionDigests": _compact_mapping(instruction_digests),
        "taskInputSnapshotRef": _optional_text(task_input_snapshot_ref),
        "planRef": _optional_text(plan_ref),
        "planDigest": _optional_text(plan_digest),
        "preparedInputRefs": list(prepared_input_refs),
        "workspacePolicy": _optional_text(workspace_policy),
        "runtimeContextPolicy": runtime_context_policy,
        "workspaceBaseline": _compact_mapping(workspace_baseline),
        "checkpointRefs": _compact_mapping(checkpoint_refs),
        "priorEvidenceRefs": _clean_existing_refs(prior_evidence_refs),
        "boundedSummaries": _bounded_summaries(bounded_summaries),
        "branchComparisonRefs": _clean_existing_refs(branch_comparison_refs),
        "retrievalManifestRef": retrieval_manifest_ref,
        "memoryManifestRef": memory_manifest_ref,
        "memoryContextRef": memory_context_ref,
        "runtimeSelection": dict(runtime_selection or {}),
        "qualityGateProfile": _optional_text(quality_gate_profile),
        "policyRefs": _compact_mapping(policy_refs),
        "costPolicy": _build_cost_policy(runtime_selection or {}),
        "portabilityProvenance": _build_portability_provenance(
            runtime_selection or {},
            prepared_input_refs=prepared_input_refs,
            memory_manifest_ref=memory_manifest_ref,
            memory_context_ref=memory_context_ref,
        ),
        "builderVersion": builder_version,
    }
    digest = _digest_payload(base_payload)
    payload = {
        **base_payload,
        "contextBundleRef": f"execution-context-bundle://{digest}",
        "contextBundleDigest": digest,
    }
    return ExecutionContextBundle.model_validate(payload)


def build_branch_turn_context_bundle(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    execution_ordinal: int,
    branch_id: str,
    branch_turn_id: str,
    source_checkpoint: Mapping[str, Any],
    instruction_artifact_ref: str,
    instruction_digest: str,
    runtime_context_policy: BranchRuntimeContextPolicy,
    workspace_policy: str,
    task_input_snapshot_ref: str | None = None,
    plan_ref: str | None = None,
    plan_digest: str | None = None,
    initial_instruction_ref: str | None = None,
    initial_instruction_digest: str | None = None,
    parent_branch_id: str | None = None,
    parent_turn_id: str | None = None,
    label: str | None = None,
    git_work_branch: str | None = None,
    workspace_baseline: Mapping[str, Any] | None = None,
    prior_evidence_refs: Sequence[Any] | None = None,
    bounded_summaries: Sequence[Any] | None = None,
    branch_comparison_refs: Sequence[Any] | None = None,
    runtime_selection: Mapping[str, Any] | None = None,
    policy_refs: Mapping[str, Any] | None = None,
    builder_version: str = BRANCH_TURN_CONTEXT_BUILDER_VERSION,
) -> ExecutionContextBundle:
    """Build the immutable context bundle for one Checkpoint Branch turn.

    This is the MM-1089 branch-turn launch contract: it requires a new Step
    Execution identity, pinned source checkpoint identity, declared workspace
    and runtime policies, and artifact-backed instructions with a digest.
    """

    checkpoint = _branch_source_checkpoint(source_checkpoint)
    turn_ref = _required_artifact_ref(
        instruction_artifact_ref,
        field_name="instruction_artifact_ref",
    )
    turn_digest = _required_digest(
        instruction_digest,
        field_name="instruction_digest",
    )
    instruction_refs = [turn_ref]
    instruction_digests = {turn_ref: turn_digest}
    if initial_instruction_ref is not None:
        initial_ref = _required_artifact_ref(
            initial_instruction_ref,
            field_name="initial_instruction_ref",
        )
        instruction_refs.insert(0, initial_ref)
        if initial_instruction_digest is None:
            raise ValueError(
                "initial_instruction_digest is required with initial_instruction_ref"
            )
        instruction_digests[initial_ref] = _required_digest(
            initial_instruction_digest,
            field_name="initial_instruction_digest",
        )

    branch = {
        "branchId": _required_text(branch_id, field_name="branch_id"),
        "branchTurnId": _required_text(branch_turn_id, field_name="branch_turn_id"),
        "label": _optional_text(label),
        "sourceCheckpoint": checkpoint,
        "sourceCheckpointRef": checkpoint["checkpointRef"],
        "sourceCheckpointDigest": checkpoint.get("checkpointDigest"),
        "rootCheckpointRef": checkpoint["checkpointRef"],
        "parentBranchId": _optional_text(parent_branch_id),
        "parentTurnId": _optional_text(parent_turn_id),
        "gitWorkBranch": _optional_text(git_work_branch),
        "traceability": MM_1089_TRACEABILITY,
    }
    checkpoint_refs = {
        "source": checkpoint["checkpointRef"],
        "sourceCheckpoint": checkpoint,
    }

    return build_execution_context_bundle(
        workflow_id=workflow_id,
        run_id=run_id,
        logical_step_id=logical_step_id,
        execution_ordinal=execution_ordinal,
        reason="checkpoint_branch",
        branch=branch,
        instruction_refs=instruction_refs,
        instruction_digests=instruction_digests,
        task_input_snapshot_ref=task_input_snapshot_ref,
        plan_ref=plan_ref,
        plan_digest=plan_digest,
        workspace_policy=workspace_policy,
        runtime_context_policy=runtime_context_policy,
        workspace_baseline=workspace_baseline,
        checkpoint_refs=checkpoint_refs,
        prior_evidence_refs=prior_evidence_refs,
        bounded_summaries=bounded_summaries,
        branch_comparison_refs=branch_comparison_refs,
        runtime_selection=runtime_selection,
        policy_refs=policy_refs,
        builder_version=builder_version,
    )


def branch_turn_step_execution_manifest_projection(
    bundle: ExecutionContextBundle,
) -> dict[str, Any]:
    """Return branch metadata for a Step Execution manifest extension."""

    branch = dict(bundle.branch or {})
    return {
        "branch": {
            "branchId": branch.get("branchId"),
            "branchTurnId": branch.get("branchTurnId"),
            "rootCheckpointRef": branch.get("rootCheckpointRef"),
            "parentBranchId": branch.get("parentBranchId"),
            "parentTurnId": branch.get("parentTurnId"),
            "gitWorkBranch": branch.get("gitWorkBranch"),
        }
    }


def build_branch_turn_artifact_manifest(
    *,
    branch_id: str,
    branch_turn_id: str,
    context_bundle: ExecutionContextBundle,
) -> dict[str, Any]:
    """Build the minimum named artifact plan for a branch and branch turn."""

    branch_text = _safe_segment(_required_text(branch_id, field_name="branch_id"))
    turn_text = _safe_segment(
        _required_text(branch_turn_id, field_name="branch_turn_id")
    )
    artifacts: list[dict[str, Any]] = []
    for name in MINIMUM_BRANCH_ARTIFACT_NAMES:
        artifacts.append(
            {
                "name": name,
                "artifactRef": f"artifact://checkpoint-branches/{branch_text}/{name}",
                "scope": "branch",
            }
        )
    for name in MINIMUM_BRANCH_TURN_ARTIFACT_NAMES:
        artifacts.append(
            {
                "name": name,
                "artifactRef": (
                    "artifact://checkpoint-branches/"
                    f"{branch_text}/turns/{turn_text}/{name}"
                ),
                "scope": "branch_turn",
            }
        )
    payload = {
        "schemaVersion": "v1",
        "traceability": MM_1089_TRACEABILITY,
        "branchId": branch_id,
        "branchTurnId": branch_turn_id,
        "contextBundleRef": context_bundle.context_bundle_ref,
        "contextBundleDigest": context_bundle.context_bundle_digest,
        "artifacts": artifacts,
    }
    payload["artifactManifestDigest"] = _digest_payload(payload)
    return payload


def _build_cost_policy(runtime_selection: Mapping[str, Any]) -> dict[str, Any]:
    runtime_id = _optional_text(
        runtime_selection.get("runtimeId") or runtime_selection.get("runtime")
    )
    model = _optional_text(runtime_selection.get("model"))
    effort = _optional_text(runtime_selection.get("effort"))
    runtime_units = _RUNTIME_COST_UNITS.get(str(runtime_id or "").lower(), 2)
    effort_units = _EFFORT_COST_UNITS.get(str(effort or "").lower(), 2 if effort else 1)
    model_units = 1
    model_key = str(model or "").lower()
    if any(marker in model_key for marker in ("pro", "opus", "gpt-5", "m2.7")):
        model_units = 2
    estimated_units = runtime_units * effort_units * model_units
    tier = "premium" if estimated_units >= 12 else "standard" if estimated_units >= 4 else "economy"
    return {
        "billingAwareRouting": True,
        "routingBasis": "step_runtime_selection",
        "runtimeId": runtime_id,
        "model": model,
        "effort": effort,
        "estimatedCostUnits": estimated_units,
        "costTier": tier,
    }


def _build_portability_provenance(
    runtime_selection: Mapping[str, Any],
    *,
    prepared_input_refs: Sequence[str],
    memory_manifest_ref: str | None,
    memory_context_ref: str | None,
) -> dict[str, Any]:
    runtime_id = _optional_text(
        runtime_selection.get("runtimeId") or runtime_selection.get("runtime")
    )
    model = _optional_text(runtime_selection.get("model"))
    effort = _optional_text(runtime_selection.get("effort"))
    return {
        "artifactPortability": "model_agnostic_refs",
        "memoryPortability": "model_provenance_attached",
        "runtimeId": runtime_id,
        "model": model,
        "effort": effort,
        "preparedInputRefCount": len(prepared_input_refs),
        "memoryManifestRef": memory_manifest_ref,
        "memoryContextRef": memory_context_ref,
        "modelSwitchSafe": True,
    }


def build_retrieval_manifest(retrieval: Mapping[str, Any]) -> RetrievalManifest:
    payload = {
        "status": _retrieval_status(retrieval.get("status")),
        "query": _optional_text(retrieval.get("query")),
        "selector": _optional_mapping(retrieval.get("selector")),
        "indexVersion": _optional_text(
            retrieval.get("indexVersion") or retrieval.get("index_version")
        ),
        "returnedRefs": _clean_existing_refs(
            retrieval.get("returnedRefs")
            or retrieval.get("returned_refs")
            or retrieval.get("retrievedRefs")
            or retrieval.get("retrieved_refs")
        ),
        "filters": _optional_mapping(retrieval.get("filters")) or {},
        "excludedRefs": _clean_existing_refs(
            retrieval.get("excludedRefs") or retrieval.get("excluded_refs")
        ),
        "compactSummaries": _bounded_summaries(
            retrieval.get("compactSummaries") or retrieval.get("compact_summaries")
        ),
    }
    digest = _digest_payload(payload)
    payload["retrievalManifestRef"] = f"attempt-retrieval-manifest://{digest}"
    return RetrievalManifest.model_validate(payload)


def build_durable_retrieval_manifest_artifact(
    retrieval: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an artifact-ready retrieval manifest with a stable ref."""

    manifest = build_retrieval_manifest(retrieval)
    payload = manifest.model_dump(by_alias=True, exclude_none=True)
    digest = _digest_payload(
        {
            key: value
            for key, value in payload.items()
            if key != "retrievalManifestRef"
        }
    )
    artifact_ref = f"artifact://retrieval-manifests/{digest}"
    payload["retrievalManifestDigest"] = digest
    payload["retrievalManifestRef"] = artifact_ref
    return {
        "artifactRef": artifact_ref,
        "contentType": "application/json",
        "payload": payload,
        "metadata": {
            "artifact_kind": "retrieval_manifest",
            "retrievalStatus": payload["status"],
            "retrievalManifestDigest": digest,
        },
    }


def build_memory_manifest(
    proposals: Sequence[Mapping[str, Any]],
) -> MemoryManifest:
    proposal_models = [
        MemoryProposal.model_validate(proposal)
        for proposal in proposals
        if isinstance(proposal, Mapping)
    ]
    payload = {
        "proposals": [
            proposal.model_dump(by_alias=True, exclude_none=True)
            for proposal in proposal_models
        ]
    }
    digest = _digest_payload(payload)
    payload["memoryManifestRef"] = f"attempt-memory-manifest://{digest}"
    return MemoryManifest.model_validate(payload)


def merge_prepared_input_refs(
    existing_refs: Sequence[Any] | None,
    prepared_context: StepPreparedContext,
) -> list[str]:
    """Merge node input refs with selected prepared context refs."""

    refs = _clean_existing_refs(existing_refs)
    refs.extend(prepared_context.input_refs)
    return _dedupe_refs(refs)


def merge_prepared_raw_input_refs(
    existing_refs: Sequence[Any] | None,
    prepared_context: StepPreparedContext,
) -> list[str]:
    """Merge node input refs with raw artifact refs selected for adapters."""

    refs = _clean_existing_refs(existing_refs)
    refs.extend(prepared_context.raw_input_refs)
    return _dedupe_refs(refs)


def _clean_existing_refs(existing_refs: Sequence[Any] | None) -> list[str]:
    refs: list[str] = []
    if isinstance(existing_refs, str):
        existing_values: Sequence[Any] = [existing_refs]
    elif existing_refs is None:
        existing_values = []
    else:
        existing_values = existing_refs
    for ref in existing_values:
        if isinstance(ref, str) and ref.strip():
            refs.append(ref.strip())
    return refs


def _branch_source_checkpoint(source_checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(source_checkpoint, Mapping):
        raise ValueError("source_checkpoint must be a mapping")
    _reject_unsafe_values(source_checkpoint, path="branch.sourceCheckpoint")
    payload = {
        "workflowId": _required_text(
            source_checkpoint.get("workflowId") or source_checkpoint.get("workflow_id"),
            field_name="source_checkpoint.workflowId",
        ),
        "runId": _required_text(
            source_checkpoint.get("runId") or source_checkpoint.get("run_id"),
            field_name="source_checkpoint.runId",
        ),
        "logicalStepId": _optional_text(
            source_checkpoint.get("logicalStepId")
            or source_checkpoint.get("logical_step_id")
        ),
        "sourceExecutionOrdinal": _optional_int(
            source_checkpoint.get("sourceExecutionOrdinal")
            or source_checkpoint.get("source_execution_ordinal")
            or source_checkpoint.get("executionOrdinal")
            or source_checkpoint.get("execution_ordinal")
        ),
        "checkpointBoundary": _required_text(
            source_checkpoint.get("checkpointBoundary")
            or source_checkpoint.get("checkpoint_boundary"),
            field_name="source_checkpoint.checkpointBoundary",
        ),
        "checkpointRef": _required_artifact_ref(
            source_checkpoint.get("checkpointRef")
            or source_checkpoint.get("checkpoint_ref"),
            field_name="source_checkpoint.checkpointRef",
        ),
        "checkpointDigest": _optional_digest(
            source_checkpoint.get("checkpointDigest")
            or source_checkpoint.get("checkpoint_digest")
        ),
    }
    _reject_unsafe_values(payload, path="branch.sourceCheckpoint")
    return {key: value for key, value in payload.items() if value is not None}


def build_recovery_prepared_artifact_refs(
    manifest: PreparedInputManifest | Mapping[str, Any] | None,
) -> list[str]:
    """Return compact prepared refs suitable for Recovery checkpoint evidence."""

    if manifest is None:
        return []
    if not isinstance(manifest, PreparedInputManifest):
        manifest = PreparedInputManifest.model_validate(manifest)
    refs: list[str] = []
    for entry in manifest.entries:
        if entry.derived_context_ref:
            refs.append(entry.derived_context_ref)
        refs.append(entry.raw_input_ref)
    return _dedupe_refs(refs)


def workflow_payload_has_input_attachments(payload: Mapping[str, Any] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    task_payload = _task_payload(payload)
    if _attachment_sequence(task_payload.get("inputAttachments")):
        return True
    return any(
        _attachment_sequence(step.get("inputAttachments"))
        for step in _step_sequence(task_payload.get("steps"))
    )


def _entry_from_attachment(
    attachment: Mapping[str, Any],
    *,
    target_kind: TargetKind,
    step_ref: str | None = None,
    step_ordinal: int | None = None,
) -> PreparedInputEntry:
    try:
        _reject_inline_attachment_content(attachment)
        _reject_secretish_values(
            attachment,
            path=_attachment_target_label(target_kind, step_ref),
        )
        artifact_id = _artifact_id(attachment)
    except ValueError as exc:
        raise ValueError(
            f"{_attachment_target_label(target_kind, step_ref)}: {exc}"
        ) from exc
    safe_artifact_id = _safe_segment(artifact_id)
    raw_input_ref = _artifact_ref(artifact_id)
    filename = _optional_text(attachment.get("filename") or attachment.get("name"))
    if target_kind == "objective":
        derived_context_ref = f"prepared-context://objective/{safe_artifact_id}"
        workspace_path = _workspace_path(
            artifact_id=artifact_id,
            filename=filename,
            target_kind=target_kind,
        )
    else:
        safe_step_ref = _safe_segment(step_ref or "")
        derived_context_ref = (
            f"prepared-context://steps/{safe_step_ref}/{safe_artifact_id}"
        )
        workspace_path = _workspace_path(
            artifact_id=artifact_id,
            filename=filename,
            target_kind=target_kind,
            step_ref=step_ref,
        )
    return PreparedInputEntry(
        artifactId=artifact_id,
        filename=filename,
        contentType=_optional_text(
            attachment.get("contentType") or attachment.get("mimeType")
        ),
        sizeBytes=_optional_int(
            attachment.get("sizeBytes")
            if attachment.get("sizeBytes") is not None
            else attachment.get("size")
        ),
        targetKind=target_kind,
        rawInputRef=raw_input_ref,
        derivedContextRef=derived_context_ref,
        workspacePath=workspace_path,
        stepRef=step_ref,
        stepOrdinal=step_ordinal,
    )


def _attachment_target_label(
    target_kind: TargetKind,
    step_ref: str | None,
) -> str:
    label = f"input attachment targetKind={target_kind}"
    if target_kind == "step" and step_ref:
        label = f"{label} stepRef={step_ref}"
    return label


def _task_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = payload.get("task")
    if isinstance(nested, Mapping):
        return nested
    return payload


def _attachment_sequence(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _step_sequence(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _step_ref(step: Mapping[str, Any], index: int) -> str:
    candidate = _optional_text(
        step.get("id")
        or step.get("stepRef")
        or step.get("ref")
    )
    if not candidate:
        raise ValueError(
            "stable stepRef is required for step inputAttachments; "
            f"task.steps[{index - 1}] must include id, stepRef, or ref"
        )
    return candidate


def _artifact_id(attachment: Mapping[str, Any]) -> str:
    for key in (
        "artifactId",
        "artifact_id",
        "artifactRef",
        "artifact_ref",
        "id",
        "ref",
    ):
        candidate = _optional_text(attachment.get(key))
        if candidate:
            if candidate.startswith("artifact://"):
                return candidate.removeprefix("artifact://")
            return candidate
    raise ValueError("input attachment is missing artifactId")


def _artifact_ref(artifact_id: str) -> str:
    if artifact_id.startswith("artifact://"):
        return artifact_id
    return f"artifact://{artifact_id}"


def _reject_inline_attachment_content(attachment: Mapping[str, Any]) -> None:
    for key, value in attachment.items():
        if key in _INLINE_ATTACHMENT_KEYS and value not in (None, ""):
            if key in {"generatedMarkdown", "markdown"}:
                raise ValueError("generated markdown is not allowed in prepared inputs")
            raise ValueError(
                "inline attachment content is not allowed in prepared inputs"
            )
        if isinstance(value, str) and _DATA_URL_RE.search(value):
            raise ValueError(
                "inline attachment content is not allowed in prepared inputs"
            )


def _reject_secretish_values(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_secretish_values(item, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_secretish_values(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and _SECRETISH_RE.search(value):
        raise ValueError(f"{path} contains raw secret material")


def _reject_unsafe_values(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if key_text in _UNSAFE_PROVIDER_PAYLOAD_KEYS and item not in (
                None,
                "",
                [],
                {},
            ):
                raise ValueError(f"{child_path} contains unsafe provider payload")
            if (
                key_text in _LARGE_LOG_KEYS
                and _unsafe_text_size(item) > _LARGE_TEXT_LIMIT
            ):
                raise ValueError(f"{child_path} contains large log content")
            _reject_unsafe_values(item, path=child_path)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_unsafe_values(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        if _SECRETISH_RE.search(value):
            raise ValueError(f"{path} contains raw secret material")
        if _RAW_DIFF_RE.search(value):
            raise ValueError(f"{path} contains raw diff content")


def _unsafe_text_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sum(_unsafe_text_size(item) for item in value)
    if isinstance(value, Mapping):
        return sum(_unsafe_text_size(item) for item in value.values())
    return 0


def _digest_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _optional_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return dict(value)


def _compact_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(value)


def _retrieval_status(value: Any) -> RetrievalStatus:
    candidate = str(value or "captured").strip().lower()
    if candidate in {"captured", "skipped", "unavailable"}:
        return candidate  # type: ignore[return-value]
    raise ValueError("retrieval status must be captured, skipped, or unavailable")


def _bounded_summaries(value: Any) -> list[str]:
    if isinstance(value, str):
        values: Sequence[Any] = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        values = []
    summaries: list[str] = []
    for item in values:
        candidate = _optional_text(item)
        if candidate:
            summaries.append(candidate[:500])
    return summaries


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _required_text(value: Any, *, field_name: str) -> str:
    candidate = _optional_text(value)
    if candidate is None:
        raise ValueError(f"{field_name} must be a non-empty string")
    _reject_unsafe_values(candidate, path=field_name)
    return candidate


def _required_artifact_ref(value: Any, *, field_name: str) -> str:
    candidate = _required_text(value, field_name=field_name)
    if not candidate.startswith("artifact://"):
        raise ValueError(f"{field_name} must be an artifact ref")
    return candidate


def _required_digest(value: Any, *, field_name: str) -> str:
    candidate = _required_text(value, field_name=field_name)
    if not candidate.startswith("sha256:"):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return candidate


def _optional_digest(value: Any) -> str | None:
    candidate = _optional_text(value)
    if candidate is None:
        return None
    return _required_digest(candidate, field_name="checkpointDigest")


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_segment(value: str) -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("-", value.strip()).strip("-")
    return cleaned or "input"


def _workspace_path(
    *,
    artifact_id: str,
    filename: str | None,
    target_kind: TargetKind,
    step_ref: str | None = None,
) -> str:
    output_name = (
        f"{_workspace_segment(artifact_id, fallback='artifact')}-"
        f"{_workspace_segment(filename, fallback='attachment')}"
    )
    if target_kind == "objective":
        return (Path(".moonmind") / "inputs" / "objective" / output_name).as_posix()
    if target_kind == "step" and step_ref:
        return (
            Path(".moonmind")
            / "inputs"
            / "steps"
            / _workspace_segment(step_ref, fallback="step")
            / output_name
        ).as_posix()
    raise ValueError("stable stepRef is required for step inputAttachments")


def _workspace_segment(value: Any, *, fallback: str) -> str:
    text = str(value or "").replace("\\", "/").strip()
    basename = Path(text).name
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._")
    return sanitized or fallback


def _dedupe_refs(refs: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for ref in refs:
        if ref and ref not in seen:
            seen.add(ref)
            result.append(ref)
    return result


def _bounded_message(message: str, *, max_chars: int = 180) -> str:
    scrubbed = _DATA_URL_RE.sub("[redacted-data-url]", message)
    scrubbed = _SECRETISH_RE.sub("[redacted-secret]", scrubbed)
    scrubbed = " ".join(scrubbed.split())
    if len(scrubbed) <= max_chars:
        return scrubbed
    return scrubbed[: max_chars - 3].rstrip() + "..."
