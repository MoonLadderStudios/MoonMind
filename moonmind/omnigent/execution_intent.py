"""Trusted compiler for the immutable Omnigent execution intent.

This is the one place that resolves an authored :class:`AgentExecutionRequest`
plus the API-resolved launch/provider/workspace authority into a single durable,
versioned :class:`CompiledOmnigentExecutionIntent` — before ``MoonMind.AgentRun``
or ``MoonMind.AgentSession`` begins any provider, host, lease, or workspace side
effect. It composes the existing typed substrate rather than reinventing it:

- the workspace/repository subset comes from the canonical
  :func:`compile_workspace_intent` / :class:`WorkspaceIntentRecord`;
- launch/deployment/image authority comes from the compiled effective-launch
  snapshot (its ``snapshotRef`` is the immutable image + runtime pin);
- the remediation-loop controller is validated into the typed
  :class:`RemediationLoopSpec` and pinned by digest, so critical lifecycle
  ownership no longer depends on a free-form ``annotations.remediationLoop``
  (the MoonLadderStudios/MoonMind#3684 failure class).

Issue reference: MoonLadderStudios/MoonMind#3706 (Omnigent control plane 5/11).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.omnigent_execution_intent import (
    EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
    EXECUTION_INTENT_UNSAFE_INPUT,
    CompiledOmnigentExecutionIntent,
    ExecutionIdentitySection,
    IntentFieldProvenance,
    LaunchAuthoritySection,
    ProvenanceSource,
    RemediationAuthoritySection,
    RuntimeSelectionSection,
    SessionContinuationSection,
    TimingFailureSection,
    WorkspaceAuthoritySection,
)
from moonmind.schemas.workspace_intent import WorkspaceIntentRecord
from moonmind.omnigent.workspace_intent import (
    WorkspaceIntentCompilationError,
    compile_workspace_intent,
)
from moonmind.workflows.temporal.remediation_loop import RemediationLoopSpec


class ExecutionIntentCompilationError(ValueError):
    """Fail-closed compilation error raised before any host mutation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _operation_class(intent: WorkspaceIntentRecord) -> str:
    """Derive the repository operation class from workspace authority."""

    publish_mode = (intent.publish_mode or "none").strip().lower()
    if publish_mode in {"pr", "pull_request"}:
        return "pull_request"
    if intent.repository_mutation and publish_mode not in {"", "none"}:
        return "publication"
    if intent.repository_mutation:
        return "controlled_mutation"
    return "read_only"


def _remediation_controller_digest(spec: RemediationLoopSpec) -> str:
    """Deterministic digest over the fully normalized remediation controller."""

    encoded = json.dumps(
        spec.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def compile_remediation_authority(
    controller: Mapping[str, Any] | RemediationLoopSpec | None,
    *,
    require: bool,
    restore_mode: str = "cold_restore",
    gate_result_ref: str | None = None,
    remaining_work_ref: str | None = None,
    branch_governing_dimensions: tuple[str, ...] = (),
) -> RemediationAuthoritySection | None:
    """Validate a remediation-loop controller into typed compiled authority.

    ``controller`` may be the typed spec or the raw mapping that historically
    travelled through ``annotations.remediationLoop``. When ``require`` is true a
    missing controller fails closed with
    :data:`EXECUTION_INTENT_INCOMPLETE_AUTHORITY` — the structural guarantee that
    replaces the silently-droppable free-form annotation (MoonMind#3684).
    """

    if controller is None:
        if require:
            raise ExecutionIntentCompilationError(
                EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
                "a remediation-loop controller is required for this run but was "
                "not present in the admitted authority",
            )
        return None
    if isinstance(controller, RemediationLoopSpec):
        spec = controller
    else:
        try:
            spec = RemediationLoopSpec.model_validate(dict(controller))
        except ValueError as exc:
            raise ExecutionIntentCompilationError(
                EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
                f"invalid remediation-loop controller: {exc}",
            ) from exc
    return RemediationAuthoritySection(
        loopId=spec.loop_id,
        controllerDigest=_remediation_controller_digest(spec),
        verifierOwner=spec.verification_tool.name,
        remediatorOwner=spec.remediation_tool.name,
        hardMaxAttempts=spec.budgets.hard_max_attempts,
        branchBudget=spec.continue_as_new_attempt_threshold,
        gateResultRef=gate_result_ref,
        remainingWorkRef=remaining_work_ref,
        checkpointBranchBehavior=spec.workspace_policy,
        restoreMode=restore_mode,
        branchGoverningDimensions=branch_governing_dimensions,
    )


def compile_execution_intent(
    request: AgentExecutionRequest,
    *,
    workspace_intent: WorkspaceIntentRecord,
    effective_launch: Mapping[str, Any],
    provider_runtime: str,
    provider_profile_id: str,
    workflow_id: str,
    step_execution_id: str,
    run_id: str | None = None,
    logical_step_id: str | None = None,
    agent_run_id: str | None = None,
    source_kind: str = "create",
    source_ref: str | None = None,
    remediation_controller: Mapping[str, Any] | RemediationLoopSpec | None = None,
    require_remediation: bool = False,
    remediation_restore_mode: str = "cold_restore",
    model: str | None = None,
    effort: str | None = None,
    execution_profile_digest: str | None = None,
    agent_profile_ref: str | None = None,
    agent_profile_digest: str | None = None,
    credential_generation: str | None = None,
    compatibility_profile: str | None = None,
    session_mode: str | None = None,
    provider_session_epoch: int | None = None,
    created_at: datetime | None = None,
    provenance_source: ProvenanceSource = "resolved",
    full_authority_proven: bool = True,
) -> CompiledOmnigentExecutionIntent:
    """Compile the complete immutable execution intent from resolved authority.

    Fails closed with :class:`ExecutionIntentCompilationError` when required
    launch/provider authority is missing or contradictory, before any host is
    selected or mutated.
    """

    if request.agent_kind != "external" or request.agent_id != "omnigent":
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
            "compiled execution intent requires external/omnigent runtime identity",
        )

    execution_profile_ref = str(
        effective_launch.get("executionProfileRef") or ""
    ).strip()
    launch_policy_ref = str(effective_launch.get("launchPolicyRef") or "").strip()
    effective_launch_ref = str(effective_launch.get("snapshotRef") or "").strip()
    host_mode = str(effective_launch.get("hostMode") or "").strip()
    if not (execution_profile_ref and launch_policy_ref and effective_launch_ref):
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
            "effective launch snapshot is missing profile/policy/snapshot authority",
        )
    if host_mode not in {"static_compose", "on_demand_docker"}:
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
            f"effective launch snapshot has unsupported host mode {host_mode!r}",
        )
    # Contradiction guard: the launch snapshot's provider profile must match the
    # provider profile the run was admitted against. Runtime code must never
    # silently reconcile a different profile.
    launch_provider_profile = str(
        effective_launch.get("providerProfileId") or ""
    ).strip()
    if launch_provider_profile and launch_provider_profile != provider_profile_id:
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
            "effective launch provider profile conflicts with the admitted "
            "provider profile",
        )

    harness = str(effective_launch.get("harness") or "").strip()
    if not harness:
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
            "effective launch snapshot is missing harness authority",
        )
    if provider_runtime not in {"codex_cli", "claude_code"}:
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
            f"unsupported provider runtime {provider_runtime!r}",
        )

    resolved_session_mode = session_mode or (
        "authorized_reuse"
        if source_kind in {"remediation", "continuation", "checkpoint"}
        else "fresh"
    )
    cleanup = effective_launch.get("cleanup")
    cleanup_policy = "drain"
    if isinstance(cleanup, Mapping) and str(cleanup.get("mode") or "").strip():
        cleanup_policy = str(cleanup["mode"]).strip()

    launch_capabilities = effective_launch.get("capabilities")
    allowed_continuation_kinds: list[str] = []
    if isinstance(launch_capabilities, Mapping):
        if launch_capabilities.get("reconnectSession"):
            allowed_continuation_kinds.append("reconnect")
        if launch_capabilities.get("replaceSession"):
            allowed_continuation_kinds.append("replace")

    session_seed = hashlib.sha256(
        f"{workflow_id}:{step_execution_id}:{request.idempotency_key}".encode("utf-8")
    ).hexdigest()

    remediation = compile_remediation_authority(
        remediation_controller,
        require=require_remediation,
        restore_mode=remediation_restore_mode,
    )

    identity = ExecutionIdentitySection(
        workflowId=workflow_id,
        runId=run_id,
        logicalStepId=logical_step_id,
        stepExecutionId=step_execution_id,
        agentRunId=agent_run_id,
        sessionSeed=session_seed,
        sourceKind=source_kind,
        sourceRef=source_ref,
        instructionRef=request.instruction_ref,
    )
    runtime = RuntimeSelectionSection(
        executionProfileRef=execution_profile_ref,
        executionProfileDigest=(
            execution_profile_digest
            or str(effective_launch.get("executionProfileDigest") or "").strip()
            or None
        ),
        agentProfileRef=agent_profile_ref,
        agentProfileDigest=agent_profile_digest,
        providerProfileId=provider_profile_id,
        credentialGeneration=credential_generation,
        providerRuntime=provider_runtime,
        harness=harness,
        model=model,
        effort=effort,
        compatibilityProfile=compatibility_profile,
    )
    launch = LaunchAuthoritySection(
        launchPolicyRef=launch_policy_ref,
        launchPolicyDigest=str(
            effective_launch.get("policyDigest")
            or (
                effective_launch.get("policyAuthority", {}).get("policyDigest")
                if isinstance(effective_launch.get("policyAuthority"), Mapping)
                else ""
            )
            or ""
        ).strip()
        or None,
        effectiveLaunchRef=effective_launch_ref,
        effectiveLaunchDigest=effective_launch_ref,
        hostMode=host_mode,
        serverImageRef=str(effective_launch.get("serverImageRef") or "") or None,
        hostImageRef=str(effective_launch.get("hostImageRef") or "") or None,
        networkRef=str(effective_launch.get("networkRef") or "") or None,
        egressProfileRef=str(effective_launch.get("egressProfileRef") or "") or None,
        runtimeCapabilityRequirements=tuple(workspace_intent.required_capabilities),
    )
    workspace = WorkspaceAuthoritySection(
        workspaceIntentDigest=workspace_intent.intent_digest or "",
        repository=workspace_intent.repository,
        repositoryKind=workspace_intent.repository_kind,
        connectionRef=workspace_intent.connection_ref,
        baseBranch=workspace_intent.starting_branch,
        targetBranch=workspace_intent.target_branch,
        checkoutCommit=workspace_intent.checkout_commit,
        operationClass=_operation_class(workspace_intent),
        workspaceLocatorKind=workspace_intent.workspace_locator.kind,
        workspaceAuthorityClass=(
            "mutation" if workspace_intent.repository_mutation else "read_only"
        ),
        attachmentRefs=tuple(workspace_intent.attachment_refs),
        checkpointRefs=tuple(workspace_intent.restore_input_refs),
        restoreRefs=tuple(workspace_intent.external_state_refs),
        publishMode=workspace_intent.publish_mode,
        noCommitPolicy=not workspace_intent.repository_mutation,
    )
    session = SessionContinuationSection(
        sessionMode=resolved_session_mode,
        initialTurnAttemptId=f"{request.idempotency_key}:turn:1",
        allowedContinuationKinds=tuple(allowed_continuation_kinds),
        providerSessionEpoch=provider_session_epoch,
        cleanupPolicy=cleanup_policy,
    )
    timing = TimingFailureSection(
        maxAttempts=1,
        requiredEvidence=("terminal_workspace_json",),
        cleanupLeaseReleaseOrder=(
            "validate_terminal_evidence",
            "release_provider_lease",
            "release_host_lease",
            "cleanup_workspace",
        ),
    )

    provenance = [
        IntentFieldProvenance(section="identity", source=provenance_source),
        IntentFieldProvenance(section="runtime", source=provenance_source),
        IntentFieldProvenance(section="launch", source=provenance_source),
        IntentFieldProvenance(section="workspace", source="resolved"),
        IntentFieldProvenance(section="session", source=provenance_source),
        IntentFieldProvenance(section="timing", source="default"),
    ]
    if remediation is not None:
        remediation_source: ProvenanceSource = (
            "legacy_derived"
            if provenance_source == "legacy_derived"
            else "authored"
        )
        provenance.append(
            IntentFieldProvenance(section="remediation", source=remediation_source)
        )

    try:
        return CompiledOmnigentExecutionIntent(
            createdAt=created_at or datetime.now(tz=UTC),
            identity=identity,
            runtime=runtime,
            launch=launch,
            workspace=workspace,
            session=session,
            remediation=remediation,
            timing=timing,
            provenance=tuple(provenance),
            fullAuthorityProven=full_authority_proven,
        )
    except ValueError as exc:
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_UNSAFE_INPUT, str(exc)
        ) from exc


def derive_execution_intent_from_request(
    request: AgentExecutionRequest,
    *,
    effective_launch: Mapping[str, Any],
    provider_runtime: str,
    provider_profile_id: str,
    workflow_id: str,
    step_execution_id: str,
    run_id: str | None = None,
    logical_step_id: str | None = None,
    agent_run_id: str | None = None,
    created_at: datetime | None = None,
) -> CompiledOmnigentExecutionIntent:
    """Bounded migration adapter: derive a v1 intent from an existing request.

    Existing (pre-#3706) request shapes carried the remediation controller in the
    free-form ``parameters.remediationLoop`` mapping. This adapter derives a full
    v1 intent from that shape, compiling the workspace subset with the canonical
    compiler and lifting the free-form remediation mapping into typed authority.
    Every field derived from a generic dictionary is tagged ``legacy_derived`` and
    the record never claims full v1 authority, so downstream consumers can tell a
    migrated intent from a natively-compiled one.

    Existing Temporal histories keep consuming their recorded payload shape; this
    intent is only for newly admitted feature generations.
    """

    try:
        workspace_intent = compile_workspace_intent(
            request,
            workflow_id=workflow_id,
            step_execution_id=step_execution_id,
            run_id=run_id,
            logical_step_id=logical_step_id,
        )
    except WorkspaceIntentCompilationError as exc:
        raise ExecutionIntentCompilationError(exc.code, str(exc)) from exc

    parameters = request.parameters if isinstance(request.parameters, Mapping) else {}
    legacy_controller = parameters.get("remediationLoop")
    remediation_controller = (
        legacy_controller if isinstance(legacy_controller, Mapping) else None
    )
    # A run that declares it needs remediation (a loop id is present) but whose
    # controller mapping was stripped is the #3684 class: fail closed rather than
    # silently admit a run the controller can never initialize.
    require_remediation = bool(
        parameters.get("remediationLoopId")
        or parameters.get("requiresRemediationLoop")
    )

    return compile_execution_intent(
        request,
        workspace_intent=workspace_intent,
        effective_launch=effective_launch,
        provider_runtime=provider_runtime,
        provider_profile_id=provider_profile_id,
        workflow_id=workflow_id,
        step_execution_id=step_execution_id,
        run_id=run_id,
        logical_step_id=logical_step_id,
        agent_run_id=agent_run_id,
        remediation_controller=remediation_controller,
        require_remediation=require_remediation,
        model=str(parameters.get("model") or "").strip() or None,
        effort=str(parameters.get("effort") or "").strip() or None,
        created_at=created_at,
        provenance_source="legacy_derived",
        full_authority_proven=False,
    )


__all__ = [
    "ExecutionIntentCompilationError",
    "compile_execution_intent",
    "compile_remediation_authority",
    "derive_execution_intent_from_request",
]
