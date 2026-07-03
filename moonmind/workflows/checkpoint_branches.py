"""Checkpoint Branch git binding helpers for MM-1090.

These helpers stay outside Temporal workflow code so launch activities and API
services can validate workspace/git isolation before starting repository-
mutating branch work.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from moonmind.schemas.temporal_models import WorkspacePolicy
from moonmind.workflows.automation.workspace import sanitize_branch_component

CHECKPOINT_BRANCH_WORKSPACE_RESTORE_CONTENT_TYPE = (
    "application/vnd.moonmind.checkpoint-branch.workspace-restore+json;version=1"
)
CHECKPOINT_BRANCH_GIT_BINDING_CONTENT_TYPE = (
    "application/vnd.moonmind.checkpoint-branch.git-binding+json;version=1"
)

CheckpointBranchCreationMode = Literal[
    "from_checkpoint_worktree",
    "from_checkpoint_patch",
    "from_last_accepted_commit",
    "fresh_from_source_branch",
    "external_provider_state",
]
CheckpointBranchBindingFailureCode = Literal[
    "detached_head",
    "git_base_commit_mismatch",
    "git_branch_collision",
    "provider_continuation_unsupported",
    "protected_branch_ref",
    "unknown_ref",
    "workspace_policy_incompatible",
    "invalid_binding",
]
MAX_GIT_BRANCH_LENGTH = 255
_GENERATED_BRANCH_COMPONENT_LENGTH = 40

_PROTECTED_REFS = frozenset(
    {
        "",
        "head",
        "main",
        "master",
        "trunk",
        "develop",
        "development",
        "release",
        "stable",
        "production",
    }
)
_HEX_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)


class CheckpointBranchGitBindingInput(BaseModel):
    """Validated input for preparing one checkpoint branch git binding."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workflow_id: str = Field(..., alias="workflowId", min_length=1, max_length=255)
    product_branch_id: str = Field(
        ..., alias="productBranchId", min_length=1, max_length=255
    )
    branch_turn_id: str = Field(..., alias="branchTurnId", min_length=1, max_length=255)
    source_checkpoint_ref: str = Field(
        ..., alias="sourceCheckpointRef", min_length=1, max_length=1024
    )
    source_checkpoint_digest: str | None = Field(
        None, alias="sourceCheckpointDigest", max_length=128
    )
    logical_step_id: str | None = Field(None, alias="logicalStepId", max_length=255)
    label: str | None = Field(None, max_length=255)
    repository: str = Field(..., min_length=1, max_length=512)
    base_branch: str = Field(..., alias="baseBranch", min_length=1, max_length=255)
    base_commit: str | None = Field(None, alias="baseCommit", max_length=128)
    resolved_base_commit: str | None = Field(
        None, alias="resolvedBaseCommit", max_length=128
    )
    workspace_policy: WorkspacePolicy = Field(..., alias="workspacePolicy")
    creation_mode: CheckpointBranchCreationMode = Field(..., alias="creationMode")
    idempotency_key: str = Field(
        ..., alias="idempotencyKey", min_length=1, max_length=512
    )
    requested_work_branch: str | None = Field(None, alias="requestedWorkBranch")
    worktree_ref: str | None = Field(None, alias="worktreeRef", max_length=1024)
    provider_workspace_ref: str | None = Field(
        None, alias="providerWorkspaceRef", max_length=1024
    )
    head_commit: str | None = Field(None, alias="headCommit", max_length=128)
    patch_ref: str | None = Field(None, alias="patchRef", max_length=1024)
    pull_request_url: str | None = Field(None, alias="pullRequestUrl", max_length=1024)
    provider_workspace_validated: bool = Field(
        False, alias="providerWorkspaceValidated"
    )

    @field_validator(
        "workflow_id",
        "product_branch_id",
        "branch_turn_id",
        "source_checkpoint_ref",
        "source_checkpoint_digest",
        "logical_step_id",
        "label",
        "repository",
        "base_branch",
        "base_commit",
        "resolved_base_commit",
        "idempotency_key",
        "requested_work_branch",
        "worktree_ref",
        "provider_workspace_ref",
        "head_commit",
        "patch_ref",
        "pull_request_url",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None


class CheckpointBranchGitBindingModel(BaseModel):
    """Compact binding record separating product branch id from git work branch."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    product_branch_id: str = Field(..., alias="productBranchId")
    branch_turn_id: str = Field(..., alias="branchTurnId")
    logical_step_id: str | None = Field(None, alias="logicalStepId")
    label: str | None = None
    idempotency_key: str = Field(..., alias="idempotencyKey")
    repository: str
    base_branch: str = Field(..., alias="baseBranch")
    base_commit: str | None = Field(None, alias="baseCommit")
    resolved_base_commit: str | None = Field(None, alias="resolvedBaseCommit")
    work_branch: str = Field(..., alias="workBranch")
    worktree_ref: str | None = Field(None, alias="worktreeRef")
    provider_workspace_ref: str | None = Field(None, alias="providerWorkspaceRef")
    head_commit: str | None = Field(None, alias="headCommit")
    patch_ref: str | None = Field(None, alias="patchRef")
    pull_request_url: str | None = Field(None, alias="pullRequestUrl")
    workspace_policy: WorkspacePolicy = Field(..., alias="workspacePolicy")
    creation_mode: CheckpointBranchCreationMode = Field(..., alias="creationMode")
    source_checkpoint_ref: str = Field(..., alias="sourceCheckpointRef")
    source_checkpoint_digest: str | None = Field(
        None, alias="sourceCheckpointDigest"
    )
    publish_status: str = Field("unpublished", alias="publishStatus")
    created_at: datetime = Field(..., alias="createdAt")


@dataclass(frozen=True)
class CheckpointBranchGitBindingResult:
    """Prepared binding and branch-level evidence payloads."""

    binding: CheckpointBranchGitBindingModel
    workspace_restore_payload: dict[str, Any]
    git_binding_payload: dict[str, Any]
    branch_metadata: dict[str, Any]
    branch_turn_metadata: dict[str, Any] | None
    step_execution_manifest_branch: dict[str, Any]
    diagnostics: dict[str, Any]


class CheckpointBranchGitBindingError(ValueError):
    """Fail-closed checkpoint branch binding validation error."""

    def __init__(self, failure_code: CheckpointBranchBindingFailureCode, message: str):
        super().__init__(message)
        self.failure_code = failure_code


def generate_checkpoint_branch_name(
    *,
    workflow_id: str,
    logical_step_id: str | None,
    checkpoint_ref: str,
    product_branch_id: str,
    label: str | None,
    idempotency_key: str,
) -> str:
    """Generate a deterministic sanitized checkpoint branch work ref."""

    workflow_slug = sanitize_branch_component(workflow_id)[
        :_GENERATED_BRANCH_COMPONENT_LENGTH
    ]
    step_slug = sanitize_branch_component(logical_step_id or "workflow")[
        :_GENERATED_BRANCH_COMPONENT_LENGTH
    ]
    checkpoint_source = f"{checkpoint_ref}:{idempotency_key}"
    checkpoint_short = hashlib.sha256(
        checkpoint_source.encode("utf-8")
    ).hexdigest()[:8]
    branch_slug = sanitize_branch_component(product_branch_id)[:16]
    label_slug = sanitize_branch_component(label or idempotency_key)[:48]
    return (
        f"mm/{workflow_slug}/{step_slug}/cp-{checkpoint_short}/"
        f"{branch_slug}-{label_slug}"
    )


def prepare_checkpoint_branch_git_binding(
    raw_input: Mapping[str, Any],
    *,
    known_refs: set[str] | frozenset[str],
    existing_bindings_by_work_branch: Mapping[str, Mapping[str, Any]] | None = None,
    current_ref: str | None = None,
    created_at: datetime | None = None,
) -> CheckpointBranchGitBindingResult:
    """Validate and build binding/artifact payloads for checkpoint branch launch."""

    try:
        model = CheckpointBranchGitBindingInput.model_validate(raw_input)
    except ValidationError as exc:
        raise CheckpointBranchGitBindingError(
            "invalid_binding", f"checkpoint branch git binding input invalid: {exc}"
        ) from exc

    _validate_ref_is_not_detached(current_ref)
    normalized_known_refs = {_normalize_ref(ref) for ref in known_refs}
    _validate_base_ref(model.base_branch, normalized_known_refs)
    _validate_base_commit(model.base_commit, model.resolved_base_commit)
    work_branch = model.requested_work_branch or generate_checkpoint_branch_name(
        workflow_id=model.workflow_id,
        logical_step_id=model.logical_step_id,
        checkpoint_ref=model.source_checkpoint_ref,
        product_branch_id=model.product_branch_id,
        label=model.label,
        idempotency_key=model.idempotency_key,
    )
    _validate_work_branch(work_branch, product_branch_id=model.product_branch_id)
    _validate_creation_mode_and_workspace_policy(model)
    _validate_workspace_isolation(model, work_branch)
    _validate_collision(
        model=model,
        work_branch=work_branch,
        existing_bindings_by_work_branch=existing_bindings_by_work_branch or {},
        normalized_known_refs=normalized_known_refs,
    )

    now = created_at or datetime.now(UTC)
    binding = CheckpointBranchGitBindingModel(
        productBranchId=model.product_branch_id,
        branchTurnId=model.branch_turn_id,
        logicalStepId=model.logical_step_id,
        label=model.label,
        idempotencyKey=model.idempotency_key,
        repository=model.repository,
        baseBranch=model.base_branch,
        baseCommit=model.base_commit,
        resolvedBaseCommit=model.resolved_base_commit,
        workBranch=work_branch,
        worktreeRef=model.worktree_ref,
        providerWorkspaceRef=model.provider_workspace_ref,
        headCommit=model.head_commit,
        patchRef=model.patch_ref,
        pullRequestUrl=model.pull_request_url,
        workspacePolicy=model.workspace_policy,
        creationMode=model.creation_mode,
        sourceCheckpointRef=model.source_checkpoint_ref,
        sourceCheckpointDigest=model.source_checkpoint_digest,
        createdAt=now,
    )
    binding_payload = binding.model_dump(by_alias=True, mode="json")
    workspace_baseline = _workspace_baseline_payload(model, work_branch)
    binding_payload["workspaceBaseline"] = workspace_baseline
    branch_metadata = {
        "branchId": model.product_branch_id,
        "gitWorkBranch": work_branch,
        "workspacePolicy": model.workspace_policy,
        "workspaceMode": model.creation_mode,
        "workspaceBaseline": workspace_baseline,
        "gitBinding": binding_payload,
    }
    turn_metadata = {
        "branchId": model.product_branch_id,
        "branchTurnId": model.branch_turn_id,
        "workspacePolicy": model.workspace_policy,
        "gitWorkBranch": work_branch,
        "workspaceBaseline": workspace_baseline,
    }
    workspace_restore = {
        "contentType": CHECKPOINT_BRANCH_WORKSPACE_RESTORE_CONTENT_TYPE,
        "workflowId": model.workflow_id,
        "productBranchId": model.product_branch_id,
        "branchTurnId": model.branch_turn_id,
        "logicalStepId": model.logical_step_id,
        "sourceCheckpointRef": model.source_checkpoint_ref,
        "sourceCheckpointDigest": model.source_checkpoint_digest,
        "workspacePolicy": model.workspace_policy,
        "creationMode": model.creation_mode,
        "repository": model.repository,
        "baseBranch": model.base_branch,
        "baseCommit": model.base_commit,
        "resolvedBaseCommit": model.resolved_base_commit,
        "workBranch": work_branch,
        "worktreeRef": model.worktree_ref,
        "providerWorkspaceRef": model.provider_workspace_ref,
        "workspaceBaseline": workspace_baseline,
        "createdAt": now.isoformat(),
    }
    git_binding = {
        "contentType": CHECKPOINT_BRANCH_GIT_BINDING_CONTENT_TYPE,
        **binding_payload,
    }
    diagnostics = {
        "workflowId": model.workflow_id,
        "productBranchId": model.product_branch_id,
        "branchTurnId": model.branch_turn_id,
        "logicalStepId": model.logical_step_id,
        "workspacePolicy": model.workspace_policy,
        "creationMode": model.creation_mode,
        "workspaceBaseline": workspace_baseline,
        "gitBinding": {
            "repository": model.repository,
            "baseBranch": model.base_branch,
            "baseCommit": model.base_commit,
            "resolvedBaseCommit": model.resolved_base_commit,
            "workBranch": work_branch,
            "worktreeRef": model.worktree_ref,
            "providerWorkspaceRef": model.provider_workspace_ref,
        },
        "evidence": {
            "workspaceRestoreArtifact": "runtime.branch.workspace_restore.json",
            "gitBindingArtifact": "runtime.branch.git_binding.json",
        },
    }
    manifest_branch = {
        "branchId": model.product_branch_id,
        "branchTurnId": model.branch_turn_id,
        "rootCheckpointRef": model.source_checkpoint_ref,
        "workspacePolicy": model.workspace_policy,
        "creationMode": model.creation_mode,
        "gitWorkBranch": work_branch,
        "repository": model.repository,
        "baseBranch": model.base_branch,
        "baseCommit": model.base_commit,
        "resolvedBaseCommit": model.resolved_base_commit,
        "worktreeRef": model.worktree_ref,
        "providerWorkspaceRef": model.provider_workspace_ref,
        "workspaceBaseline": workspace_baseline,
        "gitBindingArtifact": "runtime.branch.git_binding.json",
        "workspaceRestoreArtifact": "runtime.branch.workspace_restore.json",
    }
    return CheckpointBranchGitBindingResult(
        binding=binding,
        workspace_restore_payload=workspace_restore,
        git_binding_payload=git_binding,
        branch_metadata=branch_metadata,
        branch_turn_metadata=turn_metadata,
        step_execution_manifest_branch=manifest_branch,
        diagnostics=diagnostics,
    )


def _validate_ref_is_not_detached(current_ref: str | None) -> None:
    if current_ref is None:
        return
    ref = current_ref.strip()
    if not ref or ref.upper() == "HEAD" or _HEX_COMMIT_RE.fullmatch(ref):
        raise CheckpointBranchGitBindingError(
            "detached_head", "checkpoint branch launch requires an attached git ref"
        )


def _validate_base_ref(base_branch: str, normalized_known_refs: set[str]) -> None:
    normalized = _normalize_ref(base_branch)
    if _HEX_COMMIT_RE.fullmatch(base_branch):
        raise CheckpointBranchGitBindingError(
            "detached_head", "base ref must be a named branch, not a detached commit"
        )
    if not normalized_known_refs or normalized not in normalized_known_refs:
        raise CheckpointBranchGitBindingError(
            "unknown_ref", f"base ref {base_branch!r} is not a known repository ref"
        )


def _validate_base_commit(
    base_commit: str | None, resolved_base_commit: str | None
) -> None:
    if not base_commit or not resolved_base_commit:
        return
    if base_commit.lower() != resolved_base_commit.lower():
        raise CheckpointBranchGitBindingError(
            "git_base_commit_mismatch",
            "base commit does not match resolved repository ref",
        )


def _validate_work_branch(work_branch: str, *, product_branch_id: str) -> None:
    normalized = _normalize_ref(work_branch)
    branch_parts = work_branch.split("/")
    if len(work_branch) > MAX_GIT_BRANCH_LENGTH:
        raise CheckpointBranchGitBindingError(
            "protected_branch_ref",
            f"work branch {work_branch!r} exceeds maximum length of 255 characters",
        )
    if (
        normalized in _PROTECTED_REFS
        or normalized == _normalize_ref(product_branch_id)
        or work_branch.startswith("refs/")
        or work_branch.startswith("/")
        or work_branch.endswith("/")
        or work_branch.endswith(".")
        or work_branch.endswith(".lock")
        or any(part.startswith(".") for part in branch_parts)
        or any(part.endswith(".lock") for part in branch_parts)
    ):
        raise CheckpointBranchGitBindingError(
            "protected_branch_ref", f"work branch {work_branch!r} is not allowed"
        )
    if (
        work_branch.strip() != work_branch
        or "//" in work_branch
        or ".." in work_branch
        or "@{" in work_branch
        or "\\" in work_branch
        or any(part != sanitize_branch_component(part) for part in branch_parts)
    ):
        raise CheckpointBranchGitBindingError(
            "protected_branch_ref", f"work branch {work_branch!r} is not sanitized"
        )


_CREATION_MODE_WORKSPACE_POLICIES: dict[
    CheckpointBranchCreationMode, frozenset[WorkspacePolicy]
] = {
    "from_checkpoint_worktree": frozenset(
        {"restore_pre_execution", "continue_from_previous_execution"}
    ),
    "from_checkpoint_patch": frozenset(
        {"apply_previous_execution_diff_to_clean_baseline"}
    ),
    "from_last_accepted_commit": frozenset({"start_from_last_passed_commit"}),
    "fresh_from_source_branch": frozenset({"fresh_branch_from_source"}),
    "external_provider_state": frozenset({"continue_from_previous_execution"}),
}


def _validate_creation_mode_and_workspace_policy(
    model: CheckpointBranchGitBindingInput,
) -> None:
    allowed_policies = _CREATION_MODE_WORKSPACE_POLICIES[model.creation_mode]
    if model.workspace_policy not in allowed_policies:
        raise CheckpointBranchGitBindingError(
            "workspace_policy_incompatible",
            f"workspace policy {model.workspace_policy!r} is not compatible "
            f"with branch creation mode {model.creation_mode!r}",
        )
    provider_binding_requested = (
        model.creation_mode == "external_provider_state"
        or bool(model.provider_workspace_ref)
    )
    if provider_binding_requested and not (
        model.provider_workspace_ref and model.provider_workspace_validated
    ):
        raise CheckpointBranchGitBindingError(
            "provider_continuation_unsupported",
            "provider workspace binding requires adapter-supported validation",
        )


def _validate_workspace_isolation(
    model: CheckpointBranchGitBindingInput, work_branch: str
) -> None:
    isolated = bool(work_branch) or bool(model.worktree_ref) or bool(
        model.provider_workspace_ref
    )
    if not isolated:
        raise CheckpointBranchGitBindingError(
            "workspace_policy_incompatible",
            "repository-mutating checkpoint branches require "
            "git/worktree/provider isolation",
        )


def _validate_collision(
    *,
    model: CheckpointBranchGitBindingInput,
    work_branch: str,
    existing_bindings_by_work_branch: Mapping[str, Mapping[str, Any]],
    normalized_known_refs: set[str],
) -> None:
    existing = existing_bindings_by_work_branch.get(work_branch)
    if existing:
        existing_branch_id = str(
            existing.get("productBranchId") or existing.get("branch_id") or ""
        ).strip()
        existing_repository = str(existing.get("repository") or "").strip()
        mismatched_fields = [
            field_name
            for field_name, expected_value in _ownership_metadata(model).items()
            if str(existing.get(field_name) or "").strip() != str(expected_value)
        ]
        if (
            existing_branch_id == model.product_branch_id
            and existing_repository.lower() == model.repository.lower()
            and not mismatched_fields
        ):
            return
        raise CheckpointBranchGitBindingError(
            "git_branch_collision",
            "existing work branch belongs to a different checkpoint branch "
            f"binding or ownership metadata mismatch: {', '.join(mismatched_fields)}",
        )
    if _normalize_ref(work_branch) in normalized_known_refs:
        raise CheckpointBranchGitBindingError(
            "git_branch_collision",
            f"work branch {work_branch!r} already exists as a repository ref",
        )


def _normalize_ref(ref: str) -> str:
    return ref.strip().removeprefix("refs/heads/").lower()


def _ownership_metadata(
    model: CheckpointBranchGitBindingInput,
) -> dict[str, str | None]:
    return {
        "idempotencyKey": model.idempotency_key,
        "baseBranch": model.base_branch,
        "baseCommit": model.base_commit,
        "workspacePolicy": model.workspace_policy,
        "creationMode": model.creation_mode,
    }


def _workspace_baseline_payload(
    model: CheckpointBranchGitBindingInput, work_branch: str
) -> dict[str, str | None]:
    return {
        "repository": model.repository,
        "baseBranch": model.base_branch,
        "baseCommit": model.base_commit,
        "resolvedBaseCommit": model.resolved_base_commit,
        "workBranch": work_branch,
        "worktreeRef": model.worktree_ref,
        "providerWorkspaceRef": model.provider_workspace_ref,
        "workspacePolicy": model.workspace_policy,
        "creationMode": model.creation_mode,
        "sourceCheckpointRef": model.source_checkpoint_ref,
        "sourceCheckpointDigest": model.source_checkpoint_digest,
        "productBranchId": model.product_branch_id,
        "branchTurnId": model.branch_turn_id,
        "idempotencyKey": model.idempotency_key,
    }
