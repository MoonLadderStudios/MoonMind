"""Admission-time compiler for the immutable Omnigent execution intent.

Source issue: MoonLadderStudios/MoonMind#3706
([Omnigent control plane 5/11] Compile typed immutable execution intent and
lifecycle authority before admission).

This module owns the *compilation boundary*: it turns an authored
:class:`~moonmind.schemas.agent_runtime_models.AgentExecutionRequest` (user
selectable intent only) plus the authority the API resolved (repository
readiness and default branch, profile eligibility, policy versions, image
digests, compatibility, workspace authority, required capabilities) into one
strict, immutable :class:`~moonmind.schemas.omnigent_execution_intent.CompiledOmnigentExecutionIntent`.

Two entrypoints:

* :func:`compile_execution_intent` — the canonical admission path. It rejects
  incomplete or contradictory authority *before* an execution is created, so no
  provider, host, lease, or workspace side effect can begin from unproven
  authority. It also refuses authored input that tries to smuggle a
  runtime-owned resolved value (image digests, the resolved repository target,
  policy digests) through the generic maps.
* :func:`derive_execution_intent_from_request` — the bounded migration adapter.
  It derives a best-effort v1 intent from an existing request shape for supported
  legacy submissions, records which sections are durable versus legacy
  compatibility derivations, and never claims full v1 authority when a required
  value cannot be proven (``provenance.claims_full_authority`` is ``False``).

The #3684 incident — the dashboard stripping ``annotations.remediationLoop``
before submission so Temporal never initialized the controller — is fixed here:
remediation-loop ownership is read from typed/annotation intent once and pinned
into the typed :class:`RemediationCheckpointPolicy`, where a later free-form
transform can no longer drop it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.omnigent_execution_intent import (
    EXECUTION_INTENT_CONTRADICTORY_AUTHORITY,
    EXECUTION_INTENT_DIGEST_MISMATCH,
    EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
    EXECUTION_INTENT_PAYLOAD_TOO_LARGE,
    EXECUTION_INTENT_UNSAFE_INPUT,
    AuthorityProvenance,
    CompiledOmnigentExecutionIntent,
    ExecutionIdentity,
    ExecutionIntentProvenance,
    ExecutionLineageKind,
    LaunchDeploymentAuthority,
    ReattachPolicy,
    RemediationCheckpointPolicy,
    RepositoryOperationClass,
    RepositoryWorkspaceAuthority,
    RuntimeProviderSelection,
    SessionContinuationPolicy,
    SessionMode,
    TimingFailurePolicy,
)

# Authored keys that are runtime-owned resolved authority: the compiler resolves
# them, an author must not. Compared after normalizing separators/casing away.
_RUNTIME_OWNED_AUTHORED_KEYS: frozenset[str] = frozenset(
    {
        "resolvedrepositorytarget",
        "serverimagedigest",
        "uiimagedigest",
        "hostimagedigest",
        "launchpolicydigest",
        "effectivelaunchsnapshotdigest",
        "agentprofiledigest",
        "credentialgeneration",
        "compatibilityprofile",
    }
)


class ExecutionIntentCompilationError(ValueError):
    """Fail-closed compilation error raised before any side effect begins."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------------------
# Resolved authority (step 2 of the compilation boundary)
# ---------------------------------------------------------------------------


class ResolvedExecutionAuthority(BaseModel):
    """The authority the API resolved before admission.

    Every value here is durable, resolved authority: a persisted profile/policy
    ref, a resolved image digest, a readiness-resolved repository target, a
    resolved workspace locator. The compiler combines it with user-selectable
    intent from the authored request; it never re-resolves these at runtime.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # Identity resolved / seeded at admission.
    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str | None = Field(None, alias="runId")
    logical_step_id: str | None = Field(None, alias="logicalStepId")
    step_execution_id: str = Field(..., alias="stepExecutionId", min_length=1)
    agent_run_id: str = Field(..., alias="agentRunId", min_length=1)
    canonical_session_seed: str = Field(
        ..., alias="canonicalSessionSeed", min_length=1
    )
    task_input_snapshot_ref: str = Field(
        ..., alias="taskInputSnapshotRef", min_length=1
    )
    task_input_snapshot_digest: str = Field(
        ..., alias="taskInputSnapshotDigest", min_length=1
    )
    instruction_ref: str | None = Field(None, alias="instructionRef")
    instruction_digest: str | None = Field(None, alias="instructionDigest")

    # Runtime / provider selection.
    execution_profile_ref: str = Field(..., alias="executionProfileRef", min_length=1)
    execution_profile_version: str = Field(
        ..., alias="executionProfileVersion", min_length=1
    )
    agent_profile_ref: str = Field(..., alias="agentProfileRef", min_length=1)
    agent_profile_digest: str = Field(..., alias="agentProfileDigest", min_length=1)
    provider_profile_ref: str | None = Field(None, alias="providerProfileRef")
    provider_profile_id: str = Field(..., alias="providerProfileId", min_length=1)
    credential_generation: str = Field(
        ..., alias="credentialGeneration", min_length=1
    )
    provider_runtime: str = Field(..., alias="providerRuntime", min_length=1)
    provider_harness: str = Field(..., alias="providerHarness", min_length=1)
    compatibility_profile: str = Field(
        ..., alias="compatibilityProfile", min_length=1
    )
    #: Models/efforts the resolved profile is eligible for. Empty means the API
    #: did not constrain the choice.
    allowed_models: tuple[str, ...] = Field(default_factory=tuple, alias="allowedModels")
    default_model: str = Field(..., alias="defaultModel", min_length=1)
    default_effort: str | None = Field(None, alias="defaultEffort")

    # Launch / deployment authority.
    launch_policy_ref: str = Field(..., alias="launchPolicyRef", min_length=1)
    launch_policy_digest: str = Field(..., alias="launchPolicyDigest", min_length=1)
    effective_launch_snapshot_ref: str = Field(
        ..., alias="effectiveLaunchSnapshotRef", min_length=1
    )
    effective_launch_snapshot_digest: str = Field(
        ..., alias="effectiveLaunchSnapshotDigest", min_length=1
    )
    host_mode: str = Field(..., alias="hostMode", min_length=1)
    server_image_digest: str = Field(..., alias="serverImageDigest", min_length=1)
    ui_image_digest: str | None = Field(None, alias="uiImageDigest")
    host_image_digest: str = Field(..., alias="hostImageDigest", min_length=1)
    network_policy_ref: str | None = Field(None, alias="networkPolicyRef")
    egress_policy_ref: str | None = Field(None, alias="egressPolicyRef")
    runtime_capabilities: tuple[str, ...] = Field(
        default_factory=tuple, alias="runtimeCapabilities"
    )
    compatibility_manifest_ref: str | None = Field(
        None, alias="compatibilityManifestRef"
    )
    build_manifest_ref: str | None = Field(None, alias="buildManifestRef")

    # Repository / workspace authority (readiness already resolved).
    repository_provider: str = Field(..., alias="repositoryProvider", min_length=1)
    repository: str | None = Field(None, alias="repository")
    connection_ref: str | None = Field(None, alias="connectionRef")
    #: Resolved default/base branch. Readiness is resolved before admission so a
    #: submission cannot race the default-branch resolution.
    base_branch: str = Field(..., alias="baseBranch", min_length=1)
    checkout_commit: str | None = Field(None, alias="checkoutCommit")
    workspace_locator: dict[str, Any] = Field(..., alias="workspaceLocator")
    workspace_authority_class: str = Field(
        ..., alias="workspaceAuthorityClass", min_length=1
    )
    checkpoint_ref: str | None = Field(None, alias="checkpointRef")
    checkpoint_digest: str | None = Field(None, alias="checkpointDigest")
    restore_ref: str | None = Field(None, alias="restoreRef")
    restore_digest: str | None = Field(None, alias="restoreDigest")
    #: Repository operation classes the resolved authority permits. Empty means
    #: any class is permitted (read-only default is always allowed).
    allowed_operation_classes: tuple[str, ...] = Field(
        default_factory=tuple, alias="allowedOperationClasses"
    )

    # Session / continuation authority.
    initial_turn_attempt_id: str = Field(
        ..., alias="initialTurnAttemptId", min_length=1
    )
    first_message_marker_policy: str = Field(
        ..., alias="firstMessageMarkerPolicy", min_length=1
    )
    allowed_continuation_kinds: tuple[str, ...] = Field(
        default_factory=tuple, alias="allowedContinuationKinds"
    )
    chat_binding_policy: str = Field(..., alias="chatBindingPolicy", min_length=1)
    terminal_evidence_contract: str = Field(
        ..., alias="terminalEvidenceContract", min_length=1
    )
    cleanup_policy: str = Field(..., alias="cleanupPolicy", min_length=1)
    historical_read_policy: str = Field(
        ..., alias="historicalReadPolicy", min_length=1
    )

    # Remediation defaults / permissions.
    remediation_loop_permitted: bool = Field(
        True, alias="remediationLoopPermitted"
    )
    verifier_owner: str | None = Field(None, alias="verifierOwner")
    remediator_owner: str | None = Field(None, alias="remediatorOwner")
    checkpoint_branch_behavior: str = Field(
        ..., alias="checkpointBranchBehavior", min_length=1
    )
    immutable_dimensions: tuple[str, ...] = Field(
        default_factory=tuple, alias="immutableDimensions"
    )

    # Timing / failure authority.
    execution_deadline_seconds: int = Field(
        ..., alias="executionDeadlineSeconds", gt=0
    )
    no_progress_timeout_seconds: int = Field(
        ..., alias="noProgressTimeoutSeconds", gt=0
    )
    observation_cadence_seconds: int = Field(
        ..., alias="observationCadenceSeconds", gt=0
    )
    reconcile_cadence_seconds: int = Field(
        ..., alias="reconcileCadenceSeconds", gt=0
    )
    retry_classes: tuple[str, ...] = Field(default_factory=tuple, alias="retryClasses")
    max_attempts: int = Field(1, alias="maxAttempts", gt=0)
    cancellation_policy: str = Field(..., alias="cancellationPolicy", min_length=1)
    required_evidence: tuple[str, ...] = Field(
        default_factory=tuple, alias="requiredEvidence"
    )
    cleanup_lease_release_order: tuple[str, ...] = Field(
        default_factory=tuple, alias="cleanupLeaseReleaseOrder"
    )


# ---------------------------------------------------------------------------
# Authored-intent readers (user-selectable dimensions only)
# ---------------------------------------------------------------------------


def _parameters(request: AgentExecutionRequest) -> Mapping[str, Any]:
    parameters = request.parameters
    return parameters if isinstance(parameters, Mapping) else {}


def _spec(request: AgentExecutionRequest) -> Mapping[str, Any]:
    spec = request.workspace_spec
    return spec if isinstance(spec, Mapping) else {}


def _annotations(request: AgentExecutionRequest) -> Mapping[str, Any]:
    annotations = _parameters(request).get("annotations")
    return annotations if isinstance(annotations, Mapping) else {}


def authored_model(request: AgentExecutionRequest) -> str | None:
    value = str(_parameters(request).get("model") or "").strip()
    return value or None


def authored_effort(request: AgentExecutionRequest) -> str | None:
    value = str(_parameters(request).get("effort") or "").strip()
    return value or None


def authored_target_branch(request: AgentExecutionRequest) -> str | None:
    value = str(_spec(request).get("targetBranch") or "").strip()
    return value or None


def _authored_publish_mode(request: AgentExecutionRequest) -> str:
    value = str(_parameters(request).get("publishMode") or "none").strip().lower()
    return value or "none"


def authored_operation_class(
    request: AgentExecutionRequest,
) -> RepositoryOperationClass:
    """Resolve the authored repository operation class from user intent.

    Publish/PR intent implies a publishing class; an explicit write operation or
    a declared skill side effect implies controlled mutation; otherwise the run
    is read-only. This is user-selectable intent, cross-checked against resolved
    permissions in :func:`compile_execution_intent`.
    """

    parameters = _parameters(request)
    explicit = str(parameters.get("repositoryOperationClass") or "").strip().lower()
    if explicit:
        try:
            return RepositoryOperationClass(explicit)
        except ValueError as exc:
            raise ExecutionIntentCompilationError(
                EXECUTION_INTENT_CONTRADICTORY_AUTHORITY,
                f"unknown repositoryOperationClass {explicit!r}",
            ) from exc

    publish_mode = _authored_publish_mode(request)
    if publish_mode in {"pr", "pull_request"}:
        return RepositoryOperationClass.PULL_REQUEST
    if publish_mode not in {"", "none"}:
        return RepositoryOperationClass.PUBLICATION

    if str(parameters.get("repositoryOperation") or "").strip().lower() == "write":
        return RepositoryOperationClass.CONTROLLED_MUTATION
    skill = parameters.get("skill")
    if isinstance(skill, Mapping):
        side_effect = skill.get("sideEffect")
        if isinstance(side_effect, Mapping) and str(
            side_effect.get("kind") or ""
        ).strip():
            return RepositoryOperationClass.CONTROLLED_MUTATION
    return RepositoryOperationClass.READ_ONLY


def authored_publication_policy(request: AgentExecutionRequest) -> str:
    return _authored_publish_mode(request)


def authored_no_commit_policy(request: AgentExecutionRequest) -> bool:
    return bool(_parameters(request).get("noCommit"))


def authored_attachment_refs(request: AgentExecutionRequest) -> tuple[str, ...]:
    raw = _spec(request).get("attachmentRefs")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(
        dict.fromkeys(str(value).strip() for value in raw if str(value).strip())
    )


def authored_remediation_loop(request: AgentExecutionRequest) -> Mapping[str, Any]:
    """Read remediation-loop intent from typed and annotation locations.

    This is the #3684 fix: whether the intent arrives as a typed
    ``parameters.remediationLoop`` or the historical free-form
    ``parameters.annotations.remediationLoop``, it is read here once and pinned
    into the typed contract, where a later dashboard transform cannot strip it.
    """

    for candidate in (
        _parameters(request).get("remediationLoop"),
        _annotations(request).get("remediationLoop"),
    ):
        if isinstance(candidate, Mapping):
            return candidate
    return {}


def authored_session_mode(request: AgentExecutionRequest) -> SessionMode:
    value = str(_parameters(request).get("sessionMode") or "").strip().lower()
    if value in {"authorized_reuse", "reuse"}:
        return SessionMode.AUTHORIZED_REUSE
    return SessionMode.FRESH


def authored_lineage_kind(request: AgentExecutionRequest) -> ExecutionLineageKind:
    raw = _parameters(request).get("lineageKind")
    if not raw and request.step_execution is not None:
        raw = request.step_execution.reason
    value = str(raw or "").strip().lower()
    mapping = {
        "initial_execution": ExecutionLineageKind.CREATE,
        "create": ExecutionLineageKind.CREATE,
        "rerun": ExecutionLineageKind.RERUN,
        "edit": ExecutionLineageKind.EDIT,
        "remediation": ExecutionLineageKind.REMEDIATION,
        "checkpoint": ExecutionLineageKind.CHECKPOINT,
        "continuation": ExecutionLineageKind.CONTINUATION,
    }
    return mapping.get(value, ExecutionLineageKind.CREATE)


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


def _assert_no_runtime_owned_authored_keys(payload: Any) -> None:
    """Fail closed if an author tries to set runtime-owned resolved authority."""

    if isinstance(payload, Mapping):
        for key, nested in payload.items():
            normalized = "".join(
                ch for ch in str(key).strip().lower() if ch.isalnum()
            )
            if normalized in _RUNTIME_OWNED_AUTHORED_KEYS:
                raise ExecutionIntentCompilationError(
                    EXECUTION_INTENT_UNSAFE_INPUT,
                    f"authored input must not carry runtime-owned key {key!r}; "
                    "the compiler resolves this authority",
                )
            _assert_no_runtime_owned_authored_keys(nested)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _assert_no_runtime_owned_authored_keys(item)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_remediation_policy(
    request: AgentExecutionRequest,
    resolved: ResolvedExecutionAuthority,
) -> RemediationCheckpointPolicy:
    loop = authored_remediation_loop(request)
    enabled = bool(loop.get("enabled")) if loop else False

    if enabled and not resolved.remediation_loop_permitted:
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_CONTRADICTORY_AUTHORITY,
            "remediation loop requested but not permitted by resolved authority",
        )

    verifier = (
        str(loop.get("verifierOwner") or "").strip()
        or resolved.verifier_owner
        or None
    )
    remediator = (
        str(loop.get("remediatorOwner") or "").strip()
        or resolved.remediator_owner
        or None
    )
    if enabled and not (verifier and remediator):
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
            "an enabled remediation loop requires a verifier and remediator owner",
        )

    reattach = str(loop.get("reattachPolicy") or "").strip().lower()
    reattach_policy = (
        ReattachPolicy.LIVE_REATTACH
        if reattach == "live_reattach"
        else ReattachPolicy.COLD_RESTORE
    )
    return RemediationCheckpointPolicy(
        remediationLoopEnabled=enabled,
        verifierOwner=verifier,
        remediatorOwner=remediator,
        maxAttempts=int(loop.get("maxAttempts") or 1) if enabled else 1,
        maxBranches=int(loop.get("maxBranches") or 1) if enabled else 1,
        gateResultRef=str(loop.get("gateResultRef") or "").strip() or None,
        remainingWorkRef=str(loop.get("remainingWorkRef") or "").strip() or None,
        checkpointBranchBehavior=resolved.checkpoint_branch_behavior,
        reattachPolicy=reattach_policy,
        immutableDimensions=list(resolved.immutable_dimensions),
    )


def _resolve_operation_class(
    request: AgentExecutionRequest,
    resolved: ResolvedExecutionAuthority,
) -> RepositoryOperationClass:
    operation_class = authored_operation_class(request)
    allowed = {value.lower() for value in resolved.allowed_operation_classes}
    if (
        allowed
        and operation_class is not RepositoryOperationClass.READ_ONLY
        and operation_class.value not in allowed
    ):
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_CONTRADICTORY_AUTHORITY,
            f"operation class {operation_class.value!r} is not permitted by "
            "resolved repository authority",
        )
    return operation_class


def _resolve_model_effort(
    request: AgentExecutionRequest,
    resolved: ResolvedExecutionAuthority,
) -> tuple[str, str | None]:
    model = authored_model(request) or resolved.default_model
    if resolved.allowed_models and model not in resolved.allowed_models:
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_CONTRADICTORY_AUTHORITY,
            f"model {model!r} is not eligible for the resolved profile",
        )
    effort = authored_effort(request) or resolved.default_effort
    return model, effort


# ---------------------------------------------------------------------------
# Canonical admission compiler
# ---------------------------------------------------------------------------


def compile_execution_intent(
    request: AgentExecutionRequest,
    *,
    resolved: ResolvedExecutionAuthority,
    created_at: datetime | None = None,
) -> CompiledOmnigentExecutionIntent:
    """Compile one admitted execution intent from authored + resolved authority.

    Fails closed with :class:`ExecutionIntentCompilationError` when authored
    input smuggles a runtime-owned resolved value, or when authority is
    incomplete or contradictory — before any execution is created.
    """

    if request.agent_kind != "external" or request.agent_id != "omnigent":
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_CONTRADICTORY_AUTHORITY,
            "compiled execution intent is only for the external omnigent runtime",
        )

    # An author supplies user-selectable intent only; runtime-owned resolved
    # authority must not be smuggled through the generic authoring maps.
    _assert_no_runtime_owned_authored_keys(request.workspace_spec)
    _assert_no_runtime_owned_authored_keys(request.parameters)

    model, effort = _resolve_model_effort(request, resolved)
    operation_class = _resolve_operation_class(request, resolved)

    # Identity is durable resolved authority. If the authored launch envelope
    # carries identity too, it must agree — a mismatch is contradictory authority
    # rather than a silently preferred value.
    launch_envelope = request.step_execution
    if launch_envelope is not None:
        if launch_envelope.workflow_id != resolved.workflow_id or (
            launch_envelope.step_execution_id != resolved.step_execution_id
        ):
            raise ExecutionIntentCompilationError(
                EXECUTION_INTENT_CONTRADICTORY_AUTHORITY,
                "authored step-execution identity disagrees with resolved identity",
            )

    try:
        identity = ExecutionIdentity(
            workflowId=resolved.workflow_id,
            runId=resolved.run_id,
            logicalStepId=resolved.logical_step_id,
            stepExecutionId=resolved.step_execution_id,
            agentRunId=resolved.agent_run_id,
            canonicalSessionSeed=resolved.canonical_session_seed,
            lineageKind=authored_lineage_kind(request),
            sourceExecutionRef=str(
                _parameters(request).get("sourceExecutionRef") or ""
            ).strip()
            or None,
            taskInputSnapshotRef=resolved.task_input_snapshot_ref,
            taskInputSnapshotDigest=resolved.task_input_snapshot_digest,
            instructionRef=resolved.instruction_ref or request.instruction_ref,
            instructionDigest=resolved.instruction_digest,
        )
        runtime = RuntimeProviderSelection(
            executionProfileRef=resolved.execution_profile_ref,
            executionProfileVersion=resolved.execution_profile_version,
            agentProfileRef=resolved.agent_profile_ref,
            agentProfileDigest=resolved.agent_profile_digest,
            providerProfileRef=resolved.provider_profile_ref,
            providerProfileId=resolved.provider_profile_id,
            credentialGeneration=resolved.credential_generation,
            providerRuntime=resolved.provider_runtime,
            providerHarness=resolved.provider_harness,
            model=model,
            effort=effort,
            compatibilityProfile=resolved.compatibility_profile,
        )
        launch = LaunchDeploymentAuthority(
            launchPolicyRef=resolved.launch_policy_ref,
            launchPolicyDigest=resolved.launch_policy_digest,
            effectiveLaunchSnapshotRef=resolved.effective_launch_snapshot_ref,
            effectiveLaunchSnapshotDigest=resolved.effective_launch_snapshot_digest,
            hostMode=resolved.host_mode,
            serverImageDigest=resolved.server_image_digest,
            uiImageDigest=resolved.ui_image_digest,
            hostImageDigest=resolved.host_image_digest,
            networkPolicyRef=resolved.network_policy_ref,
            egressPolicyRef=resolved.egress_policy_ref,
            runtimeCapabilities=list(resolved.runtime_capabilities),
            compatibilityManifestRef=resolved.compatibility_manifest_ref,
            buildManifestRef=resolved.build_manifest_ref,
        )
        repository = RepositoryWorkspaceAuthority(
            repositoryProvider=resolved.repository_provider,
            repository=resolved.repository,
            connectionRef=resolved.connection_ref,
            baseBranch=resolved.base_branch,
            targetBranch=authored_target_branch(request),
            checkoutCommit=resolved.checkout_commit,
            operationClass=operation_class,
            workspaceLocator=resolved.workspace_locator,
            workspaceAuthorityClass=resolved.workspace_authority_class,
            attachmentRefs=list(authored_attachment_refs(request)),
            checkpointRef=resolved.checkpoint_ref,
            checkpointDigest=resolved.checkpoint_digest,
            restoreRef=resolved.restore_ref,
            restoreDigest=resolved.restore_digest,
            publicationPolicy=authored_publication_policy(request),
            noCommitPolicy=authored_no_commit_policy(request),
        )
        session = SessionContinuationPolicy(
            sessionMode=authored_session_mode(request),
            initialTurnAttemptId=resolved.initial_turn_attempt_id,
            firstMessageMarkerPolicy=resolved.first_message_marker_policy,
            allowedContinuationKinds=list(resolved.allowed_continuation_kinds),
            chatBindingPolicy=resolved.chat_binding_policy,
            terminalEvidenceContract=resolved.terminal_evidence_contract,
            cleanupPolicy=resolved.cleanup_policy,
            historicalReadPolicy=resolved.historical_read_policy,
        )
        remediation = _build_remediation_policy(request, resolved)
        timing = TimingFailurePolicy(
            executionDeadlineSeconds=resolved.execution_deadline_seconds,
            noProgressTimeoutSeconds=resolved.no_progress_timeout_seconds,
            observationCadenceSeconds=resolved.observation_cadence_seconds,
            reconcileCadenceSeconds=resolved.reconcile_cadence_seconds,
            retryClasses=list(resolved.retry_classes),
            maxAttempts=resolved.max_attempts,
            cancellationPolicy=resolved.cancellation_policy,
            requiredEvidence=list(resolved.required_evidence),
            cleanupLeaseReleaseOrder=list(resolved.cleanup_lease_release_order),
        )
        return CompiledOmnigentExecutionIntent(
            createdAt=created_at or datetime.now(tz=UTC),
            identity=identity,
            runtime=runtime,
            launch=launch,
            repository=repository,
            session=session,
            remediation=remediation,
            timing=timing,
            provenance=ExecutionIntentProvenance(claimsFullAuthority=True),
        )
    except ExecutionIntentCompilationError:
        raise
    except ValueError as exc:
        # Nested contract and finalize validators raise the stable
        # EXECUTION_INTENT_* codes, but pydantic wraps them in a ValidationError
        # whose message embeds (not prefixes) the code. Recover the specific code
        # so the fail-closed error stays actionable.
        message = str(exc)
        code = next(
            (
                candidate
                for candidate in (
                    EXECUTION_INTENT_UNSAFE_INPUT,
                    EXECUTION_INTENT_PAYLOAD_TOO_LARGE,
                    EXECUTION_INTENT_DIGEST_MISMATCH,
                    EXECUTION_INTENT_CONTRADICTORY_AUTHORITY,
                    EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
                )
                if candidate in message
            ),
            EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
        )
        raise ExecutionIntentCompilationError(code, message) from exc


# ---------------------------------------------------------------------------
# Bounded migration adapter for existing request shapes
# ---------------------------------------------------------------------------


def derive_execution_intent_from_request(
    request: AgentExecutionRequest,
    *,
    resolved: ResolvedExecutionAuthority | None = None,
    legacy_sections: frozenset[str] | None = None,
    created_at: datetime | None = None,
) -> CompiledOmnigentExecutionIntent:
    """Derive a v1 intent from an existing request shape, recording provenance.

    Supports migrating a legacy submission that predates the compiled intent.
    ``legacy_sections`` names the sections whose authority could only be
    derived (not durably resolved); those sections are stamped
    :data:`AuthorityProvenance.LEGACY_DERIVED` and, when any exist,
    ``claims_full_authority`` is ``False`` so a consumer refuses to treat the
    intent as proven full v1 authority.

    A ``resolved`` authority is still required to prove the durable values a v1
    intent cannot invent (profile/policy/image digests, workspace authority);
    the adapter never fabricates those.
    """

    if resolved is None:
        raise ExecutionIntentCompilationError(
            EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
            "cannot derive a v1 intent without resolved durable authority; "
            "never claim full v1 authority when a required value is unproven",
        )

    intent = compile_execution_intent(
        request, resolved=resolved, created_at=created_at
    )

    legacy = {section.strip().lower() for section in (legacy_sections or frozenset())}
    if not legacy:
        return intent

    def _mark(section: str) -> AuthorityProvenance:
        return (
            AuthorityProvenance.LEGACY_DERIVED
            if section in legacy
            else AuthorityProvenance.DURABLE
        )

    provenance = ExecutionIntentProvenance(
        identity=_mark("identity"),
        runtime=_mark("runtime"),
        launch=_mark("launch"),
        repository=_mark("repository"),
        session=_mark("session"),
        remediation=_mark("remediation"),
        timing=_mark("timing"),
        claimsFullAuthority=False,
    )
    # Rebuild with the recorded provenance; provenance is excluded from the
    # digest, so the finalized digest matches the fully-resolved intent. Route
    # through model_validate so the digest is recomputed and stamped rather than
    # left unset (model_copy does not re-run validation).
    data = intent.model_dump(by_alias=True, mode="json")
    data["provenance"] = provenance.model_dump(by_alias=True, mode="json")
    data.pop("intentDigest", None)
    return CompiledOmnigentExecutionIntent.model_validate(data)


__all__ = [
    "ExecutionIntentCompilationError",
    "ResolvedExecutionAuthority",
    "compile_execution_intent",
    "derive_execution_intent_from_request",
    "authored_model",
    "authored_effort",
    "authored_operation_class",
    "authored_remediation_loop",
    "authored_session_mode",
    "authored_target_branch",
]
