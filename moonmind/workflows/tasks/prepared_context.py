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
MemoryProposalState = Literal[
    "proposed",
    "accepted_for_run_context",
    "applied_to_repo",
    "rejected",
    "superseded",
]
EXECUTION_CONTEXT_BUILDER_VERSION = "execution-context-builder-v1"

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

    query: str | None = None
    selector: dict[str, Any] | None = None
    index_version: str | None = Field(default=None, alias="indexVersion")
    returned_refs: list[str] = Field(default_factory=list, alias="returnedRefs")
    filters: dict[str, Any] = Field(default_factory=dict)
    compact_summaries: list[str] = Field(
        default_factory=list,
        alias="compactSummaries",
    )
    retrieval_manifest_ref: str = Field(alias="retrievalManifestRef")

    @model_validator(mode="after")
    def _validate_manifest(self) -> "RetrievalManifest":
        if not self.query and not self.selector and not self.returned_refs:
            raise ValueError(
                "retrieval manifest requires query, selector, or returnedRefs"
            )
        _reject_secretish_values(self.model_dump(by_alias=True), path="retrieval")
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
    prepared_input_refs: list[str] = Field(
        default_factory=list,
        alias="preparedInputRefs",
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
        _reject_secretish_values(self.model_dump(by_alias=True), path="executionContext")
        return self

    def to_manifest_projection(self) -> dict[str, Any]:
        return {
            "context": {
                "contextBundleRef": self.context_bundle_ref,
                "contextBundleDigest": self.context_bundle_digest,
                "builderVersion": self.builder_version,
                "retrievalManifestRef": self.retrieval_manifest_ref,
                "memoryManifestRef": self.memory_manifest_ref,
                "memoryContextRef": self.memory_context_ref,
                "costPolicy": self.cost_policy,
                "portabilityProvenance": self.portability_provenance,
            }
        }


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
    prepared_context: StepPreparedContext | None = None,
    runtime_selection: Mapping[str, Any] | None = None,
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
            build_retrieval_manifest(retrieval).retrieval_manifest_ref
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
        "preparedInputRefs": list(prepared_input_refs),
        "retrievalManifestRef": retrieval_manifest_ref,
        "memoryManifestRef": memory_manifest_ref,
        "memoryContextRef": memory_context_ref,
        "runtimeSelection": dict(runtime_selection or {}),
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
        "preparedInputRefCount": len(list(prepared_input_refs)),
        "memoryManifestRef": memory_manifest_ref,
        "memoryContextRef": memory_context_ref,
        "modelSwitchSafe": True,
    }


def build_retrieval_manifest(retrieval: Mapping[str, Any]) -> RetrievalManifest:
    payload = {
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
        "compactSummaries": _bounded_summaries(
            retrieval.get("compactSummaries") or retrieval.get("compact_summaries")
        ),
    }
    digest = _digest_payload(payload)
    payload["retrievalManifestRef"] = f"attempt-retrieval-manifest://{digest}"
    return RetrievalManifest.model_validate(payload)


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


def task_payload_has_input_attachments(payload: Mapping[str, Any] | None) -> bool:
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
