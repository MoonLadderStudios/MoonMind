"""Versioned, immutable, artifact-backed compiled Omnigent execution intent.

This module defines ``CompiledOmnigentExecutionIntent`` — the single, strict,
versioned contract (schema id ``moonmind.omnigent.compiled-execution-intent/v1``)
that carries the *complete* immutable authority needed to start and recover an
Omnigent run *before* ``MoonMind.AgentRun`` or ``MoonMind.AgentSession`` begins
any provider, host, lease, or workspace side effect.

Issue reference: MoonLadderStudios/MoonMind#3706 (Omnigent control plane 5/11).

Design constraints (from the issue):

- Strict models with ``extra="forbid"``; every section is ``frozen`` so admitted
  authority can never drift after compilation.
- Nested sections are independently versioned (``sectionVersion``) so a contract
  that must evolve can do so without rewriting the whole document.
- ``intentDigest`` is deterministic over the governing values (independent of
  ``createdAt`` and the digest itself) so equivalent authored requests — and a
  retry of the same request — always produce the same immutable intent.
- The document never carries a raw credential body or host/socket authority; the
  shared :func:`~moonmind.schemas.workspace_intent.assert_no_unsafe_leaves` guard
  rejects such values, and only safe refs and digests are exposed in projections.
- Authority-critical lifecycle ownership (repository, provider/model/profile,
  launch/image, session/continuation, and the remediation-loop controller) is a
  typed field here, not an optional entry inside a generic ``parameters`` or
  ``annotations`` dictionary. The remediation-loop controller in particular is a
  typed projection pinned to the full validated controller by ``controllerDigest``
  (closing the MoonMind#3684 annotation-loss failure class at admission).

The compiler that populates this contract lives at
``moonmind/omnigent/execution_intent.py``; runtime code may derive compact views
from an admitted intent but must never silently re-resolve or broaden it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.schemas.workspace_intent import assert_no_unsafe_leaves

# The exact, canonical schema id required by the issue. It is both a stable
# identity string (persisted in evidence and artifacts) and the version gate the
# compatibility policy keys off.
EXECUTION_INTENT_SCHEMA_ID = "moonmind.omnigent.compiled-execution-intent/v1"
EXECUTION_INTENT_SCHEMA_VERSION = "v1"
EXECUTION_INTENT_PRODUCER_VERSION = "omnigent-execution-intent@1"

EXECUTION_INTENT_UNSAFE_INPUT = "OMNIGENT_EXECUTION_INTENT_UNSAFE_INPUT"
EXECUTION_INTENT_DIGEST_MISMATCH = "OMNIGENT_EXECUTION_INTENT_DIGEST_MISMATCH"
EXECUTION_INTENT_UNKNOWN_VERSION = "OMNIGENT_EXECUTION_INTENT_UNKNOWN_VERSION"
EXECUTION_INTENT_INCOMPLETE_AUTHORITY = "OMNIGENT_EXECUTION_INTENT_INCOMPLETE_AUTHORITY"


# Provenance sources: which authority proved a compiled value. ``authored`` and
# ``resolved`` are trusted current authority; ``durable_binding`` came from an
# existing persisted record; ``legacy_derived`` is a bounded migration-adapter
# derivation that must never be claimed as full v1 authority; ``default`` is a
# safe schema default.
ProvenanceSource = Literal[
    "authored",
    "resolved",
    "durable_binding",
    "legacy_derived",
    "default",
]


class _Section(BaseModel):
    """Base for every immutable, strict, independently-versioned section."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class ExecutionIdentitySection(_Section):
    """Product and execution identity, session seed, and source lineage."""

    section_version: Literal["v1"] = Field("v1", alias="sectionVersion")
    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str | None = Field(None, alias="runId")
    logical_step_id: str | None = Field(None, alias="logicalStepId")
    step_execution_id: str = Field(..., alias="stepExecutionId", min_length=1)
    agent_run_id: str | None = Field(None, alias="agentRunId")
    # Canonical session identity seed — a deterministic seed the runtime derives
    # a provider session identity from, never a live provider session id.
    session_seed: str = Field(..., alias="sessionSeed", min_length=1)
    # Where this generation came from and its lineage back to its source.
    source_kind: Literal[
        "create", "rerun", "edit", "remediation", "checkpoint", "continuation"
    ] = Field(..., alias="sourceKind")
    source_ref: str | None = Field(None, alias="sourceRef")
    # The original authored task-input snapshot pinned by ref and digest.
    task_input_snapshot_ref: str | None = Field(None, alias="taskInputSnapshotRef")
    task_input_snapshot_digest: str | None = Field(
        None, alias="taskInputSnapshotDigest"
    )
    instruction_ref: str | None = Field(None, alias="instructionRef")
    instruction_digest: str | None = Field(None, alias="instructionDigest")


class RuntimeSelectionSection(_Section):
    """Runtime and provider selection authority."""

    section_version: Literal["v1"] = Field("v1", alias="sectionVersion")
    agent_kind: Literal["external"] = Field("external", alias="agentKind")
    agent_id: Literal["omnigent"] = Field("omnigent", alias="agentId")
    execution_profile_ref: str = Field(..., alias="executionProfileRef", min_length=1)
    execution_profile_digest: str | None = Field(
        None, alias="executionProfileDigest"
    )
    agent_profile_ref: str | None = Field(None, alias="agentProfileRef")
    agent_profile_digest: str | None = Field(None, alias="agentProfileDigest")
    provider_profile_id: str = Field(..., alias="providerProfileId", min_length=1)
    # Expectation only: which credential generation the run was admitted against,
    # never a credential body.
    credential_generation: str | None = Field(None, alias="credentialGeneration")
    provider_runtime: Literal["codex_cli", "claude_code"] = Field(
        ..., alias="providerRuntime"
    )
    harness: str = Field(..., alias="harness", min_length=1)
    model: str | None = None
    effort: str | None = None
    compatibility_profile: str | None = Field(None, alias="compatibilityProfile")


class LaunchAuthoritySection(_Section):
    """Launch and deployment authority, pinned to an immutable launch snapshot."""

    section_version: Literal["v1"] = Field("v1", alias="sectionVersion")
    launch_policy_ref: str = Field(..., alias="launchPolicyRef", min_length=1)
    launch_policy_digest: str | None = Field(None, alias="launchPolicyDigest")
    # The effective launch snapshot ref and digest are the immutable image and
    # runtime authority; runtime code must not re-resolve images from the env.
    effective_launch_ref: str = Field(..., alias="effectiveLaunchRef", min_length=1)
    effective_launch_digest: str = Field(
        ..., alias="effectiveLaunchDigest", min_length=1
    )
    host_mode: Literal["static_compose", "on_demand_docker"] = Field(
        ..., alias="hostMode"
    )
    server_image_ref: str | None = Field(None, alias="serverImageRef")
    host_image_ref: str | None = Field(None, alias="hostImageRef")
    ui_image_ref: str | None = Field(None, alias="uiImageRef")
    network_ref: str | None = Field(None, alias="networkRef")
    egress_profile_ref: str | None = Field(None, alias="egressProfileRef")
    # Declared runtime capability requirements (http, sse, websocket, mounted
    # tools, repository capabilities, ...).
    runtime_capability_requirements: tuple[str, ...] = Field(
        default_factory=tuple, alias="runtimeCapabilityRequirements"
    )
    build_manifest_ref: str | None = Field(None, alias="buildManifestRef")

    @field_validator("runtime_capability_requirements", mode="before")
    @classmethod
    def _normalize_capabilities(cls, value: Any) -> tuple[str, ...]:
        return _dedupe_lower(value)


class WorkspaceAuthoritySection(_Section):
    """Repository and workspace authority, bound to the workspace-intent digest."""

    section_version: Literal["v1"] = Field("v1", alias="sectionVersion")
    # The workspace-intent record (its own versioned contract) pinned by digest.
    workspace_intent_ref: str | None = Field(None, alias="workspaceIntentRef")
    workspace_intent_digest: str = Field(
        ..., alias="workspaceIntentDigest", min_length=1
    )
    repository: str | None = Field(None, alias="repository", max_length=2000)
    repository_kind: str | None = Field(None, alias="repositoryKind", max_length=50)
    connection_ref: str | None = Field(None, alias="connectionRef")
    base_branch: str | None = Field(None, alias="baseBranch")
    target_branch: str | None = Field(None, alias="targetBranch")
    checkout_commit: str | None = Field(None, alias="checkoutCommit")
    operation_class: Literal[
        "read_only", "controlled_mutation", "publication", "pull_request"
    ] = Field(..., alias="operationClass")
    workspace_locator_kind: str = Field(
        ..., alias="workspaceLocatorKind", min_length=1
    )
    workspace_authority_class: str = Field(
        ..., alias="workspaceAuthorityClass", min_length=1
    )
    attachment_refs: tuple[str, ...] = Field(
        default_factory=tuple, alias="attachmentRefs"
    )
    checkpoint_refs: tuple[str, ...] = Field(
        default_factory=tuple, alias="checkpointRefs"
    )
    restore_refs: tuple[str, ...] = Field(default_factory=tuple, alias="restoreRefs")
    publish_mode: str = Field("none", alias="publishMode")
    no_commit_policy: bool = Field(False, alias="noCommitPolicy")

    @field_validator(
        "attachment_refs", "checkpoint_refs", "restore_refs", mode="before"
    )
    @classmethod
    def _normalize_refs(cls, value: Any) -> tuple[str, ...]:
        return _dedupe(value)


class SessionContinuationSection(_Section):
    """Session, turn, and continuation policy."""

    section_version: Literal["v1"] = Field("v1", alias="sectionVersion")
    session_mode: Literal["fresh", "authorized_reuse"] = Field(
        ..., alias="sessionMode"
    )
    initial_turn_attempt_id: str = Field(
        ..., alias="initialTurnAttemptId", min_length=1
    )
    first_message_digest: str | None = Field(None, alias="firstMessageDigest")
    marker_policy: str = Field("required", alias="markerPolicy")
    allowed_continuation_kinds: tuple[str, ...] = Field(
        default_factory=tuple, alias="allowedContinuationKinds"
    )
    provider_session_epoch: int | None = Field(None, alias="providerSessionEpoch")
    chat_binding_policy: str = Field(
        "bind_on_launch", alias="chatBindingPolicy"
    )
    terminal_evidence_kind: str = Field(
        "workspace_json", alias="terminalEvidenceKind"
    )
    cleanup_policy: str = Field(..., alias="cleanupPolicy", min_length=1)
    historical_read_policy: str = Field(
        "allow_bounded", alias="historicalReadPolicy"
    )

    @field_validator("allowed_continuation_kinds", mode="before")
    @classmethod
    def _normalize_kinds(cls, value: Any) -> tuple[str, ...]:
        return _dedupe(value)


class RemediationAuthoritySection(_Section):
    """Typed remediation-loop and checkpoint authority.

    This is the typed replacement for the free-form ``annotations.remediationLoop``
    that MoonMind#3684 could strip. The full validated controller is pinned by
    ``controllerDigest`` (computed over the normalized ``RemediationLoopSpec``),
    and the compact governing fields are typed so no runtime component has to
    re-read an untyped annotation to know a controller was required.
    """

    section_version: Literal["v1"] = Field("v1", alias="sectionVersion")
    controller_kind: Literal["remediation_loop"] = Field(
        "remediation_loop", alias="controllerKind"
    )
    loop_id: str = Field(..., alias="loopId", min_length=1)
    # Digest over the full normalized RemediationLoopSpec the compiler validated.
    controller_digest: str = Field(..., alias="controllerDigest", min_length=1)
    verifier_owner: str = Field(..., alias="verifierOwner", min_length=1)
    remediator_owner: str = Field(..., alias="remediatorOwner", min_length=1)
    hard_max_attempts: int = Field(..., alias="hardMaxAttempts", ge=1)
    branch_budget: int | None = Field(None, alias="branchBudget", ge=1)
    gate_result_ref: str | None = Field(None, alias="gateResultRef")
    remaining_work_ref: str | None = Field(None, alias="remainingWorkRef")
    checkpoint_branch_behavior: str = Field(
        "continue_from_loop_head", alias="checkpointBranchBehavior"
    )
    restore_mode: Literal["live_reattach", "cold_restore"] = Field(
        ..., alias="restoreMode"
    )
    # The immutable dimensions whose change forces a new checkpoint branch.
    branch_governing_dimensions: tuple[str, ...] = Field(
        default_factory=tuple, alias="branchGoverningDimensions"
    )

    @field_validator("branch_governing_dimensions", mode="before")
    @classmethod
    def _normalize_dimensions(cls, value: Any) -> tuple[str, ...]:
        return _dedupe(value)


class TimingFailureSection(_Section):
    """Timing and failure policy."""

    section_version: Literal["v1"] = Field("v1", alias="sectionVersion")
    execution_deadline_seconds: int | None = Field(
        None, alias="executionDeadlineSeconds", ge=1
    )
    no_progress_seconds: int | None = Field(None, alias="noProgressSeconds", ge=1)
    observation_cadence_seconds: int | None = Field(
        None, alias="observationCadenceSeconds", ge=1
    )
    reconciliation_cadence_seconds: int | None = Field(
        None, alias="reconciliationCadenceSeconds", ge=1
    )
    retry_classes: tuple[str, ...] = Field(
        default_factory=tuple, alias="retryClasses"
    )
    max_attempts: int = Field(1, alias="maxAttempts", ge=1)
    cancellation_policy: str = Field(
        "cooperative", alias="cancellationPolicy"
    )
    required_evidence: tuple[str, ...] = Field(
        default_factory=tuple, alias="requiredEvidence"
    )
    fail_closed: bool = Field(True, alias="failClosed")
    # Ordered lease/cleanup release ordering so cleanup never releases authority
    # before terminal evidence is validated.
    cleanup_lease_release_order: tuple[str, ...] = Field(
        default_factory=tuple, alias="cleanupLeaseReleaseOrder"
    )

    @field_validator(
        "retry_classes", "required_evidence", "cleanup_lease_release_order", mode="before"
    )
    @classmethod
    def _normalize_lists(cls, value: Any) -> tuple[str, ...]:
        return _dedupe(value)


class IntentFieldProvenance(_Section):
    """Records which authority proved a compiled section.

    Per the migration policy, the compiler must never claim full v1 authority
    when a required value cannot be proven; ``legacy_derived`` marks a bounded
    compatibility-adapter derivation.
    """

    section: str = Field(..., min_length=1)
    source: ProvenanceSource
    note: str | None = None


class CompiledOmnigentExecutionIntent(BaseModel):
    """The single immutable, versioned compiled execution-intent contract."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    schema_id: Literal[
        "moonmind.omnigent.compiled-execution-intent/v1"
    ] = Field(EXECUTION_INTENT_SCHEMA_ID, alias="schemaId")
    schema_version: Literal["v1"] = Field(
        EXECUTION_INTENT_SCHEMA_VERSION, alias="schemaVersion"
    )
    producer_version: str = Field(
        EXECUTION_INTENT_PRODUCER_VERSION, alias="producerVersion", min_length=1
    )
    intent_digest: str | None = Field(None, alias="intentDigest")
    created_at: datetime = Field(..., alias="createdAt")

    identity: ExecutionIdentitySection
    runtime: RuntimeSelectionSection
    launch: LaunchAuthoritySection
    workspace: WorkspaceAuthoritySection
    session: SessionContinuationSection
    remediation: RemediationAuthoritySection | None = None
    timing: TimingFailureSection

    provenance: tuple[IntentFieldProvenance, ...] = Field(default_factory=tuple)
    # ``False`` when any required value was a bounded legacy derivation rather
    # than proven current or durable authority.
    full_authority_proven: bool = Field(True, alias="fullAuthorityProven")

    @field_validator("provenance", mode="before")
    @classmethod
    def _tuple_provenance(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return (value,)

    @model_validator(mode="after")
    def _finalize(self) -> "CompiledOmnigentExecutionIntent":
        dumped = self.model_dump(by_alias=True, mode="json", exclude_none=True)
        assert_no_unsafe_leaves(dumped, error_code=EXECUTION_INTENT_UNSAFE_INPUT)
        computed = self.compute_digest()
        if self.intent_digest is None:
            object.__setattr__(self, "intent_digest", computed)
        elif self.intent_digest != computed:
            raise ValueError(
                f"{EXECUTION_INTENT_DIGEST_MISMATCH}: intentDigest does not match "
                "the governing execution-intent values"
            )
        return self

    def _fingerprint_payload(self) -> dict[str, Any]:
        """Identity-independent content the digest is derived from.

        ``createdAt`` and ``intentDigest`` are excluded so equivalent authored
        requests — including a retry — always produce the same immutable digest.
        """

        dumped = self.model_dump(by_alias=True, mode="json", exclude_none=False)
        dumped.pop("intentDigest", None)
        dumped.pop("createdAt", None)
        return dumped

    def compute_digest(self) -> str:
        """Return the deterministic ``sha256:`` digest of the governing values."""

        encoded = json.dumps(
            self._fingerprint_payload(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def evidence(self) -> dict[str, Any]:
        """Bounded, credential-free, path-safe compilation evidence.

        Suitable for a durable lifecycle event and Workflow Detail. Repository
        identity is redacted for a local source so raw worker-local paths never
        leak; only safe refs and digests are exposed.
        """

        repository_evidence: str | None
        if self.workspace.repository_kind == "local":
            repository_evidence = "[local-source]"
        else:
            repository_evidence = self.workspace.repository
        return {
            "schemaId": self.schema_id,
            "schemaVersion": self.schema_version,
            "producerVersion": self.producer_version,
            "intentDigest": self.intent_digest,
            "workflowId": self.identity.workflow_id,
            "stepExecutionId": self.identity.step_execution_id,
            "agentRunId": self.identity.agent_run_id,
            "sourceKind": self.identity.source_kind,
            "executionProfileRef": self.runtime.execution_profile_ref,
            "executionProfileDigest": self.runtime.execution_profile_digest,
            "providerProfileId": self.runtime.provider_profile_id,
            "providerRuntime": self.runtime.provider_runtime,
            "harness": self.runtime.harness,
            "model": self.runtime.model,
            "effort": self.runtime.effort,
            "launchPolicyRef": self.launch.launch_policy_ref,
            "effectiveLaunchRef": self.launch.effective_launch_ref,
            "effectiveLaunchDigest": self.launch.effective_launch_digest,
            "hostMode": self.launch.host_mode,
            "repository": repository_evidence,
            "repositoryKind": self.workspace.repository_kind,
            "operationClass": self.workspace.operation_class,
            "workspaceIntentDigest": self.workspace.workspace_intent_digest,
            "publishMode": self.workspace.publish_mode,
            "sessionMode": self.session.session_mode,
            "remediationLoopId": (
                self.remediation.loop_id if self.remediation else None
            ),
            "remediationControllerDigest": (
                self.remediation.controller_digest if self.remediation else None
            ),
            "fullAuthorityProven": self.full_authority_proven,
        }

    def redacted_projection(self) -> dict[str, Any]:
        """Return the safe diagnostic projection (refs and digests only)."""

        return self.evidence()


def _dedupe(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    seen: dict[str, None] = {}
    for value in values:
        candidate = str(value).strip()
        if candidate:
            seen.setdefault(candidate, None)
    return tuple(seen)


def _dedupe_lower(values: Any) -> tuple[str, ...]:
    return _dedupe(
        [str(item).strip().lower() for item in _dedupe(values)]
    )


class ExecutionIntentCompatibility(BaseModel):
    """Result of resolving a persisted execution-intent document's version."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    disposition: Literal["admit", "historical_read", "reject"]
    schema_id: str | None = Field(None, alias="schemaId")
    schema_version: str | None = Field(None, alias="schemaVersion")
    reason: str


def resolve_execution_intent_compatibility(
    document: Any, *, purpose: Literal["admission", "historical_read"]
) -> ExecutionIntentCompatibility:
    """Resolve the compatibility disposition for a persisted intent document.

    Explicit, tested behavior for every unknown version (issue AC7):

    - The exact ``v1`` schema is ``admit`` for admission and ``historical_read``
      for a read/replay purpose.
    - An unknown or newer schema id/version fails closed (``reject``) for a new
      admission — runtime code must never silently default missing authority.
    - The same unknown version *degrades* to ``historical_read`` for a read/replay
      purpose, so an already-running history remains inspectable without granting
      it fresh admission authority.
    - A structurally invalid document (missing schema id) always fails closed.
    """

    if not isinstance(document, dict):
        return ExecutionIntentCompatibility(
            disposition="reject",
            reason=f"{EXECUTION_INTENT_UNKNOWN_VERSION}: intent document is not a mapping",
        )
    schema_id = document.get("schemaId") or document.get("schema_id")
    schema_version = document.get("schemaVersion") or document.get("schema_version")
    if not schema_id:
        return ExecutionIntentCompatibility(
            disposition="reject",
            schemaVersion=str(schema_version) if schema_version else None,
            reason=f"{EXECUTION_INTENT_UNKNOWN_VERSION}: intent document has no schemaId",
        )
    if schema_id == EXECUTION_INTENT_SCHEMA_ID:
        disposition = "admit" if purpose == "admission" else "historical_read"
        return ExecutionIntentCompatibility(
            disposition=disposition,
            schemaId=str(schema_id),
            schemaVersion=str(schema_version) if schema_version else None,
            reason="known v1 execution intent",
        )
    # Unknown/newer version: fail closed on admission, degrade on read.
    if purpose == "admission":
        return ExecutionIntentCompatibility(
            disposition="reject",
            schemaId=str(schema_id),
            schemaVersion=str(schema_version) if schema_version else None,
            reason=(
                f"{EXECUTION_INTENT_UNKNOWN_VERSION}: cannot admit unknown "
                f"execution-intent schema {schema_id!r}"
            ),
        )
    return ExecutionIntentCompatibility(
        disposition="historical_read",
        schemaId=str(schema_id),
        schemaVersion=str(schema_version) if schema_version else None,
        reason=(
            f"unknown execution-intent schema {schema_id!r} readable for history only"
        ),
    )


__all__ = [
    "EXECUTION_INTENT_SCHEMA_ID",
    "EXECUTION_INTENT_SCHEMA_VERSION",
    "EXECUTION_INTENT_PRODUCER_VERSION",
    "EXECUTION_INTENT_UNSAFE_INPUT",
    "EXECUTION_INTENT_DIGEST_MISMATCH",
    "EXECUTION_INTENT_UNKNOWN_VERSION",
    "EXECUTION_INTENT_INCOMPLETE_AUTHORITY",
    "ProvenanceSource",
    "ExecutionIdentitySection",
    "RuntimeSelectionSection",
    "LaunchAuthoritySection",
    "WorkspaceAuthoritySection",
    "SessionContinuationSection",
    "RemediationAuthoritySection",
    "TimingFailureSection",
    "IntentFieldProvenance",
    "CompiledOmnigentExecutionIntent",
    "ExecutionIntentCompatibility",
    "resolve_execution_intent_compatibility",
]
