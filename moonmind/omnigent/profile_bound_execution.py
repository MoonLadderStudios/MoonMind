"""Lease-authorized coordinator for profile-bound Omnigent execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy import select
from temporalio import activity

from api_service.db.models import ManagedAgentProviderProfile
from api_service.services.provider_profile_readiness import (
    provider_profile_launch_ready,
)
from api_service.services.omnigent_policies import (
    OmnigentPolicyService,
    PolicyConflict,
)
from moonmind.omnigent.authority_chain import (
    build_omnigent_authority_chain_evidence,
)
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.checkpoints import (
    CandidateWorkspaceAuthority,
    OmnigentCheckpointIdentity,
    OmnigentRecoveryMode,
    materialize_cold_restore_inputs,
    recovery_mode,
    validate_cold_restore_target,
)
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.remediation_workspace import (
    RemediationWorkspaceBinding,
    RemediationWorkspaceOwner,
    SandboxRemediationWorkspaceOwner,
)
from moonmind.omnigent.execution_profiles import selection_from_request
from moonmind.omnigent.mounted_tool_preflight import MountedToolPreflightError
from moonmind.omnigent.oauth_hosts import (
    OmnigentOAuthHostError,
    OmnigentOAuthHostRepository,
)
from moonmind.omnigent.workspace_intent import (
    WorkspaceIntentCompilationError,
    authored_checkout_commit,
    authored_github_mutation_required,
    authored_repository_mutation_required,
    authored_repository_source,
    authored_required_capabilities,
    authored_restore_input_refs,
    authored_starting_branch,
    authored_target_branch,
    compile_workspace_intent,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
    deterministic_lease_owner_id,
)
from moonmind.provider_profiles.oauth_policy import is_omnigent_oauth_profile
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.executions.runtime_capabilities import (
    RuntimeCapabilityError,
    resolve_runtime_execution_capabilities,
)


ExecutionRunner = Callable[..., Awaitable[AgentRunResult]]


def _activity_attempt() -> int:
    """Return the durable Temporal attempt, or one outside an Activity."""

    try:
        return max(1, int(activity.info().attempt))
    except RuntimeError:
        return 1


def _failure_evidence(exc: Exception) -> tuple[str, str, str]:
    """Return stable launch classification and operator remediation."""

    code = str(getattr(exc, "code", None) or type(exc).__name__)[:96]
    lowered = code.lower()
    if "policy" in lowered or "authorization" in lowered:
        return code, "authorization_error", "contact_administrator"
    if "profile_resolution" in lowered:
        return code, "configuration_error", "select_execution_profile"
    if "profile_readiness" in lowered:
        return code, "configuration_error", "validate_codex_oauth"
    if "credential" in lowered or "oauth" in lowered:
        return code, "configuration_error", "validate_codex_oauth"
    if "lease" in lowered:
        return code, "resource_unavailable", "wait_for_profile_lease"
    if "auth" in lowered:
        return code, "configuration_error", "repair_bridge_authentication"
    if "binding" in lowered or "harness" in lowered or "capability" in lowered:
        return code, "configuration_error", "correct_host_binding"
    if "image" in lowered or "container" in lowered:
        return code, "configuration_error", "repair_host_image"
    if "network" in lowered or "endpoint" in lowered:
        return code, "integration_error", "repair_server_endpoint"
    return code, "integration_error", "retry_transient_upstream"


def _prepare_host_failure_stage(exc: Exception) -> str | None:
    """Map a prepare-host failure to the boundary that actually reported it."""

    code = str(getattr(exc, "code", None) or "").lower()
    if any(
        marker in code
        for marker in ("credential_volume", "credential_owner", "credential_generation")
    ):
        return "credential_mount"
    if "oauth" in code or "credential" in code or "github_auth" in code:
        return "credential_preflight"
    if "host_registration" in code:
        return "host_registration"
    if "capability" in code or "harness" in code:
        return "harness_readiness"
    if "bridge_auth" in code or "server_endpoint" in code:
        return "bridge_authentication"
    return None


def _diagnostics_ref(value: object) -> str | None:
    """Extract only an already-persisted diagnostics reference from failures/results."""

    for name in ("diagnostics_ref", "diagnosticsRef", "artifact_ref"):
        ref = str(getattr(value, name, "") or "").strip()
        if ref:
            return ref[:1024]
    return None


def _request_identity(request: AgentExecutionRequest) -> tuple[str, str | None]:
    if request.step_execution is not None:
        return (
            request.step_execution.workflow_id,
            request.step_execution.step_execution_id,
        )
    parameters = request.parameters if isinstance(request.parameters, Mapping) else {}
    step = parameters.get("stepExecution")
    if not isinstance(step, Mapping):
        step = {}
    workflow_id = str(
        step.get("workflowId") or parameters.get("workflowId") or request.correlation_id
    ).strip()
    step_execution_id = str(step.get("stepExecutionId") or "").strip() or None
    return workflow_id, step_execution_id


def _bind_exact_host(
    request: AgentExecutionRequest,
    *,
    host_id: str,
    workspace_path: str,
    profile_authorization: Mapping[str, Any],
    harness: str,
    agent_name: str,
) -> AgentExecutionRequest:
    parameters = dict(request.parameters or {})
    raw = parameters.get("omnigent")
    omnigent = dict(raw) if isinstance(raw, Mapping) else {}
    raw_session = omnigent.get("session")
    session = dict(raw_session) if isinstance(raw_session, Mapping) else {}
    caller_host_id = str(session.get("hostId") or session.get("host_id") or "").strip()
    if caller_host_id and caller_host_id != host_id:
        raise OmnigentOAuthHostError(
            "caller-provided hostId does not match the profile binding",
            code="OMNIGENT_HOST_BINDING_MISMATCH",
        )
    session["hostType"] = "external"
    session["hostId"] = host_id
    session["workspace"] = workspace_path
    session.pop("host_id", None)
    omnigent["session"] = session
    agent = dict(omnigent.get("agent") or {})
    caller_harness = str(agent.get("harnessOverride") or "").strip()
    if caller_harness and caller_harness != harness:
        raise OmnigentOAuthHostError(
            "selected Omnigent harness conflicts with the execution profile",
            code="OMNIGENT_HARNESS_PROVIDER_MISMATCH",
        )
    agent["harnessOverride"] = harness
    agent["agentName"] = agent_name
    omnigent["agent"] = agent
    omnigent["_moonmindProfileAuthorization"] = dict(profile_authorization)
    parameters["omnigent"] = omnigent
    return request.model_copy(update={"parameters": parameters})


def _bind_candidate_workspace(
    request: AgentExecutionRequest,
    candidate: CandidateWorkspaceAuthority,
) -> AgentExecutionRequest:
    """Bind continuation to the exact MoonMind checkpoint, never a workspace root."""

    parameters = dict(request.parameters or {})
    parameters["candidateWorkspace"] = candidate.model_dump(by_alias=True, mode="json")
    return request.model_copy(
        update={
            "parameters": parameters,
            "input_refs": list(
                dict.fromkeys(
                    [
                        *request.input_refs,
                        candidate.head_ref,
                        candidate.checkpoint_ref,
                    ]
                )
            ),
        }
    )


def _compile_persisted_effective_launch(
    policy_snapshot: Mapping[str, Any],
    *,
    provider_profile_id: str,
) -> dict[str, Any]:
    """Compile the sole launch carrier from persisted immutable authority."""

    boundaries = policy_snapshot["boundaries"]
    host = boundaries["host"]
    execution = boundaries["execution"]
    endpoint = boundaries["endpoint"]
    resources = boundaries["resources"]
    network = boundaries["network"]
    workspace = boundaries["workspace"]
    session = boundaries["session"]
    retention = boundaries["retention"]
    payload = {
        "schemaVersion": 3,
        "executionProfileRef": execution["profileRef"],
        "launchPolicyRef": policy_snapshot["policyRef"],
        "providerProfileId": provider_profile_id,
        "endpointRef": endpoint["ref"],
        "agentName": execution["agentIdentities"][0],
        "harness": execution["harness"],
        "hostMode": host["mode"],
        "backendRef": host["backendRef"],
        "architectures": list(host["architectures"]),
        "serverImageRef": host["serverImageRef"],
        "hostImageRef": host["hostImageRef"],
        "networkRef": network["attachmentRef"],
        "egressProfileRef": network["egressProfileRef"],
        "enforcedEgress": bool(network["egressProfileRef"]),
        "limits": {
            "cpuMillis": resources["cpuMillis"],
            "memoryMiB": resources["memoryMiB"],
            "processes": resources["processes"],
            "timeoutSeconds": resources["timeoutSeconds"],
            "temporaryStorageMiB": resources["temporaryStorageMiB"],
        },
        "mountClasses": list(workspace["mountClasses"]),
        "repositoryMutation": bool(workspace["repositoryMutation"]),
        "runtimeUid": workspace["runtimeUid"],
        "runtimeGid": workspace["runtimeGid"],
        # The Codex host substrate is intentionally stricter than selectable
        # policy: it never permits a writable root filesystem.
        "readOnlyRoot": True,
        "capture": {
            **dict(boundaries["capture"]),
            "retentionDays": retention["days"],
        },
        "cleanup": {"mode": session["cleanup"], "janitor": True},
        "controlCapabilities": [
            "interrupt",
            "terminate",
            "clear_context",
        ],
        # Every policy section remains available to downstream enforcement
        # consumers without reconstructing authority from environment or code.
        "boundaries": dict(boundaries),
        "policyAuthority": dict(policy_snapshot),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["snapshotRef"] = "omnigent-launch:sha256:" + hashlib.sha256(
        canonical.encode()
    ).hexdigest()
    return payload


class OmnigentProfileBoundExecutionCoordinator:
    """Own the profile lease through host/session harvesting and cleanup."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
        lease_client: ProviderProfileLeaseClient,
        host_repository: OmnigentOAuthHostRepository,
        host_runtime: OmnigentOAuthHostRuntime,
        run_store: OmnigentBridgeSessionStore,
        execution_runner: ExecutionRunner,
        artifact_gateway: Any,
        artifact_service: Any | None = None,
        workspace_owner: RemediationWorkspaceOwner | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._lease_client = lease_client
        self._hosts = host_repository
        self._runtime = host_runtime
        self._run_store = run_store
        self._execute = execution_runner
        self._artifact_gateway = artifact_gateway
        self._artifact_service = artifact_service or artifact_gateway
        self._workspace_owner = workspace_owner or SandboxRemediationWorkspaceOwner(
            os.getenv("WORKFLOW_WORKSPACE_ROOT", "/work/agent_jobs")
        )

    async def execute(self, request: AgentExecutionRequest) -> AgentRunResult:
        profile_id = str(request.execution_profile_ref or "").strip()
        workflow_id, step_execution_id = _request_identity(request)
        await self._run_store.get_or_create(
            request=request,
            endpoint_ref="pending",
            agent_id=None,
            agent_name=None,
            target_metadata={
                "providerProfileId": profile_id or None,
                "workflowId": workflow_id,
                "stepExecutionId": step_execution_id,
                "attemptIdentity": request.idempotency_key,
            },
        )
        bridge_ready = True
        current_stage = "request_validated"
        active_stages: set[str] = set()
        attempt_identity = f"{request.idempotency_key}:attempt:{_activity_attempt()}"

        async def emit(
            stage: str,
            status: str,
            *,
            code: str | None = None,
            summary: str | None = None,
            failure_class: str | None = None,
            remediation_action: str | None = None,
            diagnostics_ref: str | None = None,
            metadata: dict[str, Any] | None = None,
            ignore_errors: bool = False,
        ) -> None:
            try:
                await self._run_store.record_lifecycle_event(
                    request.idempotency_key,
                    event_type=stage,
                    status=status,
                    event_identity=f"{attempt_identity}:{stage}:{status}",
                    code=code,
                    summary=summary,
                    failure_class=failure_class,
                    diagnostics_ref=diagnostics_ref,
                    remediation_action=remediation_action,
                    metadata={
                        "workflowId": workflow_id,
                        "stepExecutionId": step_execution_id,
                        **dict(metadata or {}),
                    },
                )
            except Exception:
                if not ignore_errors:
                    raise
            if status in {"started", "waiting"}:
                active_stages.add(stage)
            elif status in {"completed", "ready", "failed"}:
                active_stages.discard(stage)

        provider_lease: CredentialLease | None = None
        host_lease = None
        binding = None
        effective_launch: dict[str, Any] | None = None
        remediation_resolution: Mapping[str, Any] | None = None
        terminal_status = "completed"
        # Bounded, credential-free evidence accumulated across the run so the
        # unified authority chain (MoonLadderStudios/MoonMind#3561) can be emitted
        # once at terminal, covering both success and every failure path.
        authority_workspace_resolution: Mapping[str, Any] | None = None
        authority_result: AgentRunResult | None = None
        authority_bridge_session_id: str | None = None
        authority_cleanup_mode: str | None = None
        authority_reasons: list[dict[str, Any]] = []
        try:
            await emit("request_validated", "started")
            if not profile_id:
                raise OmnigentOAuthHostError(
                    "OAuth-backed Omnigent execution requires executionProfileRef",
                    code="profile_resolution_failed",
                )
            await emit("request_validated", "completed")
            current_stage = "profile_resolution"
            await emit(current_stage, "started")
            profile = await self._resolve_profile(profile_id)
            provider_runtime = str(
                getattr(profile.runtime_id, "value", profile.runtime_id)
            )
            await emit(
                current_stage,
                "completed",
                metadata={"providerProfileId": profile_id},
            )
            current_stage = "profile_readiness"
            await emit(current_stage, "started")
            if not provider_profile_launch_ready(profile):
                raise OmnigentOAuthHostError(
                    "Provider Profile is not launch ready",
                    code="profile_readiness_failed",
                )
            await emit(current_stage, "ready")
            # Resolve product-owned launch authority before acquiring a Provider
            # Profile lease or mutating host state. A persisted binding chooses
            # an immutable ref; environment input selects only the one-time
            # bootstrap default when no durable or authored selection exists.
            requested_target, requested_policy = selection_from_request(
                request.parameters
            )
            current_stage = "host_binding_resolution"
            await emit(current_stage, "started")
            binding = await self._hosts.get_binding_for_profile(profile_id)
            if binding is not None:
                provider_slug = (
                    "claude" if provider_runtime == "claude_code" else "codex"
                )
                selected_profile_ref = str(
                    binding.execution_profile_ref or f"omnigent-{provider_slug}@1"
                )
                selected_policy_ref = str(
                    binding.launch_policy_ref
                    or (
                        f"{provider_slug}-on-demand@1"
                        if binding.host_launch_profile_ref
                        else f"{provider_slug}-static@1"
                    )
                )
                if requested_target and (
                    selected_profile_ref != requested_target
                    or (requested_policy and selected_policy_ref != requested_policy)
                ):
                    raise OmnigentOAuthHostError(
                        "explicit launch selection conflicts with the durable host binding",
                        code="OMNIGENT_LAUNCH_POLICY_BINDING_CONFLICT",
                    )
            elif requested_target:
                selected_profile_ref = requested_target
                provider_slug = (
                    "claude" if provider_runtime == "claude_code" else "codex"
                )
                selected_policy_ref = requested_policy or f"{provider_slug}-static@1"
            else:
                provider_slug = (
                    "claude" if provider_runtime == "claude_code" else "codex"
                )
                bootstrap_on_demand = bool(
                    os.getenv(
                        "OMNIGENT_CLAUDE_HOST_LAUNCH_PROFILE"
                        if provider_runtime == "claude_code"
                        else "OMNIGENT_CODEX_HOST_LAUNCH_PROFILE"
                    )
                )
                selected_profile_ref = f"omnigent-{provider_slug}@1"
                selected_policy_ref = (
                    f"{provider_slug}-on-demand@1"
                    if bootstrap_on_demand
                    else f"{provider_slug}-static@1"
                )
            current_stage = "policy_authority_resolution"
            await emit(current_stage, "started")
            try:
                policy_snapshot = await self._resolve_policy_snapshot(
                    selected_policy_ref
                )
            except PolicyConflict as exc:
                raise OmnigentOAuthHostError(
                    str(exc), code="OMNIGENT_POLICY_AUTHORITY_UNAVAILABLE"
                ) from exc
            if (
                policy_snapshot["boundaries"]["execution"]["profileRef"]
                != selected_profile_ref
            ):
                raise OmnigentOAuthHostError(
                    "persisted policy execution profile conflicts with launch selection",
                    code="OMNIGENT_LAUNCH_POLICY_BINDING_CONFLICT",
                )
            effective_launch = _compile_persisted_effective_launch(
                policy_snapshot,
                provider_profile_id=profile_id,
            )
            if (
                self._repository_mutation_required(request)
                and not effective_launch["repositoryMutation"]
            ):
                raise OmnigentOAuthHostError(
                    "persisted policy denies required repository mutation",
                    code="OMNIGENT_REPOSITORY_MUTATION_DENIED",
                )
            await emit(
                current_stage,
                "completed",
                metadata={
                    "policyRef": policy_snapshot["policyRef"],
                    "policyDigest": policy_snapshot["policyDigest"],
                    "policySnapshotRef": policy_snapshot["snapshotRef"],
                    "validation": policy_snapshot["validation"],
                },
            )
            try:
                host_capabilities = resolve_runtime_execution_capabilities(
                    "omnigent"
                ).host_realization
                assert host_capabilities is not None
                host_capabilities.require_mode(
                    str(effective_launch["hostMode"]),
                    repository_mutation=self._repository_mutation_required(request),
                    github_credentials=self._github_mutation_required(request),
                )
            except RuntimeCapabilityError as exc:
                raise OmnigentOAuthHostError(
                    str(exc), code="OMNIGENT_HOST_MODE_CAPABILITY_UNSUPPORTED"
                ) from exc
            remediation = self._remediation_workspace(request)
            if remediation is not None:
                if request.agent_kind != "external" or request.agent_id != "omnigent":
                    raise OmnigentOAuthHostError(
                        "remediation workspace requires external/omnigent execution",
                        code="REMEDIATION_WORKSPACE_RUNTIME_MISMATCH",
                    )
                if remediation.execution_profile_ref != profile_id:
                    raise OmnigentOAuthHostError(
                        "remediation workspace execution profile does not match workflow authorization",
                        code="REMEDIATION_WORKSPACE_PROFILE_MISMATCH",
                    )
                if (
                    remediation.host_profile_ref
                    != str(effective_launch.get("executionProfileRef") or "")
                    or remediation.launch_policy_ref
                    != str(effective_launch.get("launchPolicyRef") or "")
                ):
                    raise OmnigentOAuthHostError(
                        "remediation workspace launch snapshot does not match effective launch",
                        code="REMEDIATION_WORKSPACE_LAUNCH_MISMATCH",
                    )
                current_stage = "remediation_workspace_admission"
                await emit(current_stage, "started")
                remediation_resolution = await self._workspace_owner.admit_and_resolve(
                    binding=remediation,
                    workflow_id=workflow_id,
                    step_execution_id=(step_execution_id or request.idempotency_key),
                )
                await emit(
                    current_stage,
                    "completed",
                    metadata={
                        "loopId": remediation.loop_id,
                        "branchRef": remediation.branch_ref,
                        "attemptOrdinal": remediation.attempt_ordinal,
                        "restoreEvidenceRef": remediation_resolution.get(
                            "restoreEvidenceRef"
                        ),
                        "workspaceState": remediation_resolution.get("workspaceState"),
                    },
                )
            await emit(
                "effective_launch_compiled",
                "completed",
                metadata={"effectiveLaunch": effective_launch},
            )
            # Compile every normal authoring surface's authored request into one
            # durable, versioned workspace-intent record before any host binding,
            # lease, or workspace mutation. Unsafe or inconsistent authored input
            # (runtime-specific bind paths, Docker authority, arbitrary host ids,
            # credential-shaped values) fails closed here. Remediation runs carry
            # a pre-materialized, separately-authorized workspace and are compiled
            # by their owner, so they are not recompiled here.
            workspace_intent = None
            if remediation_resolution is None:
                current_stage = "workspace_intent_compilation"
                await emit(current_stage, "started")
                try:
                    workspace_intent = compile_workspace_intent(
                        request,
                        workflow_id=workflow_id,
                        step_execution_id=(
                            step_execution_id or request.idempotency_key
                        ),
                        run_id=(
                            request.step_execution.run_id
                            if request.step_execution is not None
                            else None
                        ),
                        logical_step_id=(
                            request.step_execution.logical_step_id
                            if request.step_execution is not None
                            else None
                        ),
                    )
                except WorkspaceIntentCompilationError as exc:
                    raise OmnigentOAuthHostError(str(exc), code=exc.code) from exc
                # Durable, bounded, credential-free, path-safe compilation
                # evidence for Workflow Detail. A retry deterministically
                # reproduces the same intent digest.
                await self._run_store.record_lifecycle_event(
                    request.idempotency_key,
                    event_type="workspace_intent_compiled",
                    # Scope the durable event identity to the compiled intent
                    # digest. A deterministic retry reproduces the same digest and
                    # deduplicates; a conflicting resubmission under the same
                    # idempotency key (changed repository, branch, locator, or
                    # authority) produces a distinct digest and is recorded as a
                    # new event instead of silently retaining the stale evidence.
                    event_identity=(
                        f"workspace_intent_compiled:{workspace_intent.intent_digest}"
                    ),
                    metadata=workspace_intent.evidence(),
                )
                await emit(
                    current_stage,
                    "completed",
                    metadata={
                        "workspaceIntentDigest": workspace_intent.intent_digest,
                        "workspaceIntentSchemaVersion": (
                            workspace_intent.schema_version
                        ),
                        "locatorKind": workspace_intent.workspace_locator.kind,
                        "repositoryMutation": workspace_intent.repository_mutation,
                        "publishMode": workspace_intent.publish_mode,
                    },
                )
            owner_id = deterministic_lease_owner_id(
                profile_id=profile_id,
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                workflow_id=workflow_id,
                step_execution_id=step_execution_id,
                idempotency_key=request.idempotency_key,
            )
            current_stage = "profile_lease_wait"
            await emit(
                current_stage, "waiting", metadata={"providerProfileId": profile_id}
            )
            provider_lease = await self._lease_client.acquire_execution_lease(
                runtime_id=provider_runtime,
                profile_id=profile_id,
                owner_id=owner_id,
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                metadata={
                    "workflowId": workflow_id,
                    "stepExecutionId": step_execution_id,
                    "idempotencyKey": request.idempotency_key,
                    # The coordinator runs in an Activity. The deterministic
                    # lease owner is retry identity; workflowId remains safe
                    # diagnostic ownership metadata.
                    "ownerIsWorkflow": False,
                },
            )
            await emit(current_stage, "completed")
            current_stage = "profile_lease_acquired"
            await emit(current_stage, "started")
            await emit(
                current_stage,
                "completed",
                metadata={
                    "providerProfileId": profile_id,
                    "providerLeaseId": provider_lease.lease_id,
                },
            )
            current_stage = "host_binding_resolution"
            selected_on_demand = effective_launch["hostMode"] == "on_demand_docker"
            binding = await self._hosts.create_or_update_static_binding(
                profile_id=profile_id,
                endpoint_ref=str(effective_launch["endpointRef"]),
                static_host_id=binding.static_host_id if binding is not None else None,
                host_launch_profile_ref=(
                    binding.host_launch_profile_ref
                    if binding is not None
                    else (
                        (
                            str(effective_launch["launchPolicyRef"])
                            if requested_target
                            else os.getenv(
                                "OMNIGENT_CLAUDE_HOST_LAUNCH_PROFILE"
                                if provider_runtime == "claude_code"
                                else "OMNIGENT_CODEX_HOST_LAUNCH_PROFILE"
                            )
                        )
                        if selected_on_demand
                        else None
                    )
                ),
                execution_profile_ref=str(effective_launch["executionProfileRef"]),
                launch_policy_ref=str(effective_launch["launchPolicyRef"]),
                effective_launch_snapshot=effective_launch,
            )
            await emit(
                current_stage,
                "completed",
                metadata={
                    "hostBindingRef": binding.binding_ref,
                    "effectiveLaunchRef": effective_launch["snapshotRef"],
                },
            )
            current_stage = "host_lease_created"
            await emit(current_stage, "started")
            host_lease = await self._hosts.create_or_get_host_lease(
                binding=binding,
                provider_lease_id=provider_lease.lease_id,
                holder_workflow_id=workflow_id,
                agent_run_id=step_execution_id,
                idempotency_key=request.idempotency_key,
            )
            if host_lease.status in {"stopped", "failed"}:
                host_lease = await self._hosts.restart_host_lease(host_lease.lease_id)
            await emit(
                current_stage,
                "completed",
                metadata={"hostLeaseRef": host_lease.lease_id},
            )
            bridge = await self._run_store.bind_profile_authorization(
                request=request,
                endpoint_ref=binding.endpoint_ref,
                provider_profile_id=profile_id,
                provider_lease_id=provider_lease.lease_id,
                credential_generation=host_lease.credential_generation,
                host_binding_ref=binding.binding_ref,
                host_lease_ref=host_lease.lease_id,
                omnigent_host_id=binding.static_host_id,
                effective_launch_snapshot=effective_launch,
            )
            authority_bridge_session_id = str(
                getattr(bridge, "bridge_session_id", "") or ""
            ) or None
            if host_lease.status == "allocating":
                host_lease = await self._hosts.transition_host_lease(
                    host_lease.lease_id,
                    expected_status="allocating",
                    new_status="starting",
                )
            github_token = await self._github_token(request)
            current_stage = "container_start"
            await emit(current_stage, "started")
            preflight = await self._runtime.prepare_host(
                binding=binding,
                host_lease=host_lease,
                workspace_key=(
                    f"{workflow_id}:{step_execution_id or request.idempotency_key}"
                ),
                workspace_locator=(
                    remediation_resolution.get("workspaceLocator")
                    if remediation_resolution is not None
                    else workspace_intent.workspace_locator_payload()
                ),
                current_workflow_id=workflow_id,
                current_step_execution_id=(
                    step_execution_id or request.idempotency_key
                ),
                resolved_skillset_ref=request.resolved_skillset_ref,
                artifact_gateway=self._artifact_service,
                target_repository=str(
                    (request.parameters or {}).get("repository") or ""
                ).strip(),
                required_capabilities=self._required_capabilities(request),
                github_token=github_token,
                github_mutation_required=self._github_mutation_required(request),
                effective_launch=effective_launch,
                # A remediation workspace is already materialized and authorized by
                # the remediation owner, so never re-clone it here. Fresh normal
                # runs carry authored repository/branch intent that must be
                # materialized into the authoritative sandbox workspace.
                repository_source=(
                    ""
                    if remediation_resolution is not None
                    else (workspace_intent.repository or "")
                ),
                repository_provider=str(
                    (request.workspace_spec or {}).get("provider") or ""
                ).strip(),
                starting_branch=(
                    None
                    if remediation_resolution is not None
                    else workspace_intent.starting_branch
                ),
                target_branch=(
                    None
                    if remediation_resolution is not None
                    else workspace_intent.target_branch
                ),
                checkout_commit=(
                    None
                    if remediation_resolution is not None
                    else workspace_intent.checkout_commit
                ),
                # Host artifact materialization accepts only ``artifact://``
                # restore inputs. The compiler already partitioned provider
                # external-state refs into ``external_state_refs``; forward only
                # the artifact-backed restore refs so a checkpoint/continuation
                # carrying an external-state ref is not rejected before launch.
                restore_input_refs=(
                    ()
                    if remediation_resolution is not None
                    else tuple(workspace_intent.restore_input_refs)
                ),
            )
            await emit(current_stage, "completed")
            workspace_resolution = preflight.get("workspaceResolution")
            authority_workspace_resolution = (
                dict(workspace_resolution)
                if isinstance(workspace_resolution, Mapping)
                else None
            )
            if isinstance(workspace_resolution, Mapping) and workspace_resolution:
                # Durable, bounded, credential-free evidence of which workspace was
                # resolved and how it was materialized, for Workflow Detail.
                await self._run_store.record_lifecycle_event(
                    request.idempotency_key,
                    event_type="workspace_resolution",
                    metadata=dict(workspace_resolution),
                )
            await emit("credential_mount", "started")
            await emit(
                "credential_mount",
                "completed",
                metadata={
                    "credentialGeneration": host_lease.credential_generation,
                    "credentialMountPath": (
                        "/home/app/.claude"
                        if provider_runtime == "claude_code"
                        else "/home/app/.codex"
                    ),
                },
            )
            host_id = str(preflight["hostId"])
            await emit("host_registration", "started")
            await emit(
                "host_registration", "completed", metadata={"omnigentHostId": host_id}
            )
            await emit("harness_readiness", "started")
            await emit(
                "harness_readiness", "ready", metadata={"omnigentHostId": host_id}
            )
            await emit("bridge_authentication", "started")
            await emit("bridge_authentication", "completed")
            if binding.static_host_id is None and not binding.host_launch_profile_ref:
                binding = await self._hosts.create_or_update_static_binding(
                    profile_id=profile_id,
                    endpoint_ref=binding.endpoint_ref,
                    static_host_id=host_id,
                )
            if host_lease.status == "starting":
                host_lease = await self._hosts.transition_host_lease(
                    host_lease.lease_id,
                    expected_status="starting",
                    new_status="ready",
                    fields={"omnigent_host_id": host_id},
                )
            await self._run_store.bind_profile_authorization(
                request=request,
                endpoint_ref=binding.endpoint_ref,
                provider_profile_id=profile_id,
                provider_lease_id=provider_lease.lease_id,
                credential_generation=host_lease.credential_generation,
                host_binding_ref=binding.binding_ref,
                host_lease_ref=host_lease.lease_id,
                omnigent_host_id=host_id,
                effective_launch_snapshot=effective_launch,
            )
            await emit("credential_preflight", "started")
            await emit(
                "credential_preflight",
                "ready",
                metadata={
                    "providerProfileId": profile_id,
                    "credentialGeneration": host_lease.credential_generation,
                    "omnigentHostId": host_id,
                },
            )
            if preflight.get("mountedTools", {}).get("status") == "ready":
                await self._run_store.record_lifecycle_event(
                    request.idempotency_key,
                    event_type="mounted_tool_preflight_ready",
                    metadata=dict(preflight["mountedTools"]),
                )
            if host_lease.status == "ready":
                host_lease = await self._hosts.transition_host_lease(
                    host_lease.lease_id,
                    expected_status="ready",
                    new_status="assigned",
                    fields={"bridge_session_id": bridge.bridge_session_id},
                )
            current_stage = "session_creation"
            await emit(current_stage, "started", metadata={"omnigentHostId": host_id})
            await emit("first_message_prepare", "started")
            await emit("first_message_post", "started")
            await emit("session_running", "started")
            await emit("resource_harvest", "started")
            result = await self._execute(
                _bind_exact_host(
                    request,
                    host_id=host_id,
                    workspace_path=str(preflight["workspacePath"]),
                    profile_authorization={
                        "providerProfileId": profile_id,
                        "credentialGeneration": host_lease.credential_generation,
                        "providerLeaseRef": provider_lease.lease_id,
                        "hostBindingRef": binding.binding_ref,
                        "hostLeaseRef": host_lease.lease_id,
                        "endpointRef": binding.endpoint_ref,
                        "omnigentHostId": host_id,
                        "bridgeSessionId": bridge.bridge_session_id,
                        "effectiveLaunchRef": effective_launch["snapshotRef"],
                    },
                    harness=str(effective_launch["harness"]),
                    agent_name=str(effective_launch["agentName"]),
                ),
                artifact_gateway=self._artifact_gateway,
                run_store=self._run_store,
            )
            authority_result = result
            result_failed = bool(result.failure_class or result.provider_error_code)
            result_status = "failed" if result_failed else "completed"
            terminal_status = result_status
            if result_failed:
                # The runner returned a failed ``AgentRunResult`` instead of
                # raising, so the exception path never runs. Carry the provider
                # failure code, class, and remediation into the unified authority
                # chain; otherwise ``harvestState="failed"`` would surface with an
                # empty reasons list, dropping evidence already on the result.
                authority_reasons.append(
                    {
                        "stage": "resource_harvest",
                        "code": (
                            result.provider_error_code
                            or (
                                str(result.failure_class)
                                if result.failure_class
                                else "provider_run_failed"
                            )
                        ),
                        "failureClass": (
                            str(result.failure_class)
                            if result.failure_class
                            else None
                        ),
                        "remediationAction": result.retry_recommendation or None,
                    }
                )
            await emit("first_message_prepare", result_status)
            await emit("first_message_post", result_status)
            await emit(
                current_stage, result_status, metadata={"omnigentHostId": host_id}
            )
            await emit("session_running", result_status)
            await emit(
                "resource_harvest",
                result_status,
                code=result.provider_error_code,
                failure_class=(
                    str(result.failure_class) if result.failure_class else None
                ),
                diagnostics_ref=_diagnostics_ref(result),
            )
            if str(result.provider_error_code or "") == "429":
                await self._lease_client.record_cooldown(
                    runtime_id=provider_runtime,
                    profile_id=profile_id,
                    owner_id=provider_lease.owner_id,
                    cooldown_seconds=profile.cooldown_after_429_seconds,
                    reason="provider_429",
                )
                await emit(
                    "profile_cooldown",
                    "waiting",
                    code="provider_429",
                    remediation_action="retry_after_provider_cooldown",
                    metadata={"providerProfileId": profile_id},
                )
            return result
        except Exception as exc:
            terminal_status = "failed"
            if bridge_ready:
                code, failure_class, remediation = _failure_evidence(exc)
                authority_reasons.append(
                    {
                        "stage": current_stage,
                        "code": code,
                        "failureClass": failure_class,
                        "remediationAction": remediation,
                    }
                )
                if isinstance(exc, MountedToolPreflightError):
                    await self._run_store.record_lifecycle_event(
                        request.idempotency_key,
                        event_type="mounted_tool_preflight_blocked",
                        status="failed",
                        event_identity=(
                            f"{attempt_identity}:mounted_tool_preflight_blocked:failed"
                        ),
                        code=exc.code,
                        summary=str(exc),
                        metadata=exc.evidence,
                    )
                prepare_failure_stage = _prepare_host_failure_stage(exc)
                if prepare_failure_stage and prepare_failure_stage not in active_stages:
                    await emit(prepare_failure_stage, "started", ignore_errors=True)
                for stage in list(active_stages) or [current_stage]:
                    await emit(
                        stage,
                        "failed",
                        code=code,
                        summary=str(exc),
                        failure_class=failure_class,
                        remediation_action=remediation,
                        diagnostics_ref=_diagnostics_ref(exc),
                        ignore_errors=True,
                    )
            raise
        finally:
            safe_to_release_provider = host_lease is None
            if host_lease is not None and binding is not None:
                try:
                    cleanup_mode = (
                        "on_demand_remove"
                        if binding.host_launch_profile_ref
                        else "static_drain"
                    )
                    authority_cleanup_mode = cleanup_mode
                    await emit(
                        "host_cleanup",
                        "started",
                        metadata={
                            "sessionInterrupted": host_lease.status == "assigned",
                            "hostCleanupMode": cleanup_mode,
                        },
                        ignore_errors=True,
                    )
                    if host_lease.status == "assigned":
                        host_lease = await self._hosts.transition_host_lease(
                            host_lease.lease_id,
                            expected_status="assigned",
                            new_status="draining",
                        )
                    await self._runtime.stop_host(
                        binding=binding, host_lease=host_lease
                    )
                    await self._hosts.mark_host_lease_stopped(host_lease.lease_id)
                    safe_to_release_provider = True
                    await emit(
                        "host_cleanup",
                        "completed",
                        metadata={
                            "cleanupCompleted": True,
                            "sessionInterrupted": True,
                            "hostCleanupMode": cleanup_mode,
                            "stateResourcesCleaned": True,
                            "hostLeaseReleased": True,
                        },
                        ignore_errors=True,
                    )
                except Exception as cleanup_exc:
                    try:
                        await self._hosts.mark_host_lease_failed(
                            host_lease.lease_id,
                            code=type(cleanup_exc).__name__,
                            summary=str(cleanup_exc),
                        )
                    except Exception:
                        # Preserve the primary cleanup failure when best-effort
                        # persistence of that failure also becomes unavailable.
                        pass
                    authority_reasons.append(
                        {
                            "stage": "host_cleanup",
                            "code": type(cleanup_exc).__name__,
                            "failureClass": "system_error",
                            "remediationAction": "inspect_cleanup_diagnostics",
                        }
                    )
                    await emit(
                        "host_cleanup",
                        "failed",
                        code=type(cleanup_exc).__name__,
                        summary=str(cleanup_exc),
                        failure_class="system_error",
                        remediation_action="inspect_cleanup_diagnostics",
                        metadata={"cleanupCompleted": False, "janitorRequired": True},
                    )
            lease_released = provider_lease is None
            if provider_lease is not None:
                if safe_to_release_provider:
                    await emit(
                        "profile_lease_release",
                        "started",
                        metadata={"leaseReleased": False},
                        ignore_errors=True,
                    )
                    release_exc: Exception | None = None
                    for release_attempt in range(3):
                        try:
                            await self._lease_client.release_lease(provider_lease)
                            lease_released = True
                            release_exc = None
                            break
                        except Exception as exc:
                            release_exc = exc
                            if release_attempt < 2:
                                await asyncio.sleep(2**release_attempt)
                    if lease_released:
                        await emit(
                            "profile_lease_release",
                            "completed",
                            metadata={"leaseReleased": True},
                        )
                    elif release_exc is not None:
                        await emit(
                            "profile_lease_release",
                            "failed",
                            code=type(release_exc).__name__,
                            summary=str(release_exc),
                            failure_class="system_error",
                            remediation_action="inspect_cleanup_diagnostics",
                            diagnostics_ref=_diagnostics_ref(release_exc),
                            metadata={"leaseReleased": False, "janitorRequired": True},
                            ignore_errors=True,
                        )
                else:
                    await emit(
                        "profile_lease_release",
                        "waiting",
                        code="credential_cleanup_incomplete",
                        remediation_action="inspect_cleanup_diagnostics",
                        metadata={"leaseReleased": False, "janitorRequired": True},
                        ignore_errors=True,
                    )
            janitor_required = provider_lease is not None and not lease_released
            # Emit the single unified, bounded, credential-free authority chain
            # (MoonLadderStudios/MoonMind#3561) before terminal so Workflow Detail
            # exposes one workspace -> runtime -> publication -> terminal ->
            # cleanup -> lease projection for both success and failure paths.
            try:
                release_ordering: list[str] = []
                if host_lease is not None:
                    release_ordering.append(
                        "host_cleanup_completed"
                        if safe_to_release_provider
                        else "host_cleanup_incomplete"
                    )
                if provider_lease is not None:
                    release_ordering.append(
                        "provider_lease_released"
                        if lease_released
                        else "provider_lease_release_deferred"
                    )
                release_ordering.append("terminal")
                if janitor_required:
                    authority_reasons.append(
                        {
                            "stage": "profile_lease_release",
                            "code": "credential_cleanup_incomplete",
                            "failureClass": "system_error",
                            "remediationAction": "inspect_cleanup_diagnostics",
                        }
                    )
                authorization_evidence: dict[str, Any] = {}
                if profile_id:
                    authorization_evidence["providerProfileId"] = profile_id
                if provider_lease is not None:
                    authorization_evidence["providerLeaseRef"] = provider_lease.lease_id
                if binding is not None:
                    authorization_evidence["hostBindingRef"] = binding.binding_ref
                    authorization_evidence["endpointRef"] = binding.endpoint_ref
                if host_lease is not None:
                    authorization_evidence["hostLeaseRef"] = host_lease.lease_id
                    authorization_evidence["credentialGeneration"] = (
                        host_lease.credential_generation
                    )
                    if host_lease.omnigent_host_id:
                        authorization_evidence["omnigentHostId"] = (
                            host_lease.omnigent_host_id
                        )
                if effective_launch is not None:
                    authorization_evidence["effectiveLaunchRef"] = str(
                        effective_launch.get("snapshotRef") or ""
                    )
                if authority_bridge_session_id:
                    authorization_evidence["bridgeSessionId"] = (
                        authority_bridge_session_id
                    )
                authority_chain = build_omnigent_authority_chain_evidence(
                    effective_launch=effective_launch,
                    workspace_resolution=authority_workspace_resolution,
                    repository=self._repository_source(request) or None,
                    source_branch=self._starting_branch(request),
                    output_branch=self._target_branch(request),
                    publish_mode=str(
                        (request.parameters or {}).get("publishMode") or "none"
                    ),
                    required_capabilities=self._required_capabilities(request),
                    repository_mutation_required=self._repository_mutation_required(
                        request
                    ),
                    github_mutation_required=self._github_mutation_required(request),
                    profile_authorization=authorization_evidence,
                    result_output_refs=(
                        authority_result.output_refs
                        if authority_result is not None
                        else ()
                    ),
                    result_metadata=(
                        authority_result.metadata
                        if authority_result is not None
                        else None
                    ),
                    terminal_status=terminal_status,
                    cleanup_mode=authority_cleanup_mode,
                    cleanup_completed=safe_to_release_provider,
                    lease_released=lease_released,
                    janitor_required=janitor_required,
                    release_ordering=release_ordering,
                    reasons=authority_reasons,
                )
                await emit(
                    "authority_chain",
                    "completed",
                    metadata={"authorityChain": authority_chain},
                    ignore_errors=True,
                )
            except Exception:
                # Bounded evidence is best-effort and must never mask the primary
                # run outcome or its terminal record.
                pass
            await emit(
                "terminal",
                terminal_status,
                metadata={
                    "cleanupCompleted": safe_to_release_provider,
                    "leaseReleased": lease_released,
                    "janitorRequired": janitor_required,
                },
                ignore_errors=True,
            )

    async def recover_from_checkpoint(
        self,
        *,
        request: AgentExecutionRequest,
        checkpoint: OmnigentCheckpointIdentity,
        provider_lease: Mapping[str, Any] | None,
        host_lease: Mapping[str, Any] | None,
        host_registered: bool,
        session_valid: bool,
        first_message_consistent: bool,
        current_credential_generation: int,
        candidate_workspace: CandidateWorkspaceAuthority,
    ) -> AgentRunResult:
        """Live-reattach when safe; otherwise cold-restore on a new lease/session."""

        mode = recovery_mode(
            checkpoint,
            provider_lease=provider_lease,
            host_lease=host_lease,
            host_registered=host_registered,
            session_valid=session_valid,
            first_message_consistent=first_message_consistent,
        )
        if mode == OmnigentRecoveryMode.LIVE_REATTACH:
            if request.execution_profile_ref != checkpoint.provider_profile_id:
                raise ValueError("live reattach Provider Profile mismatch")
            profile = await self._resolve_profile(checkpoint.provider_profile_id)
            runtime_id = str(getattr(profile.runtime_id, "value", profile.runtime_id))
            harness = (
                "claude-native" if runtime_id == "claude_code" else "codex-native"
            )
            live_request = _bind_candidate_workspace(request, candidate_workspace)
            live_request = live_request.model_copy(
                update={
                    "idempotency_key": checkpoint.idempotency_key,
                    "input_refs": list(
                        dict.fromkeys(
                            [*live_request.input_refs, checkpoint.external_state_ref]
                        )
                    ),
                }
            )
            return await self._execute(
                _bind_exact_host(
                    live_request,
                    host_id=str(checkpoint.omnigent_host_id),
                    workspace_path="/workspaces/run",
                    profile_authorization=checkpoint.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    ),
                    harness=harness,
                    agent_name=("claude" if runtime_id == "claude_code" else "codex"),
                ),
                artifact_gateway=self._artifact_gateway,
                run_store=self._run_store,
            )

        validate_cold_restore_target(
            checkpoint,
            provider_profile_id=str(request.execution_profile_ref or ""),
            credential_generation=current_credential_generation,
        )
        cold_key = deterministic_lease_owner_id(
            profile_id=checkpoint.provider_profile_id,
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            idempotency_key=f"{checkpoint.idempotency_key}:cold:{request.idempotency_key}",
        )
        parameters = dict(request.parameters or {})
        restore_material = materialize_cold_restore_inputs(
            checkpoint, checkpoint.validation
        )
        parameters["checkpointRestore"] = {
            "mode": "cold_restore",
            "externalStateRef": checkpoint.external_state_ref,
            "sourceBridgeSessionId": checkpoint.bridge_session_id,
            "restoreMaterial": restore_material.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
            "candidateWorkspace": candidate_workspace.model_dump(
                by_alias=True, mode="json"
            ),
        }
        return await self.execute(
            request.model_copy(
                update={
                    "idempotency_key": cold_key,
                    "parameters": parameters,
                    "input_refs": list(
                        dict.fromkeys(
                            [
                                *request.input_refs,
                                checkpoint.external_state_ref,
                                candidate_workspace.head_ref,
                                candidate_workspace.checkpoint_ref,
                            ]
                        )
                    ),
                }
            )
        )

    async def branch_from_checkpoint(
        self,
        *,
        request: AgentExecutionRequest,
        checkpoint: OmnigentCheckpointIdentity,
        current_credential_generation: int,
        candidate_workspace: CandidateWorkspaceAuthority,
    ) -> AgentRunResult:
        """Create a new capacity-gated host lease and session from checkpoint refs."""

        validate_cold_restore_target(
            checkpoint,
            provider_profile_id=str(request.execution_profile_ref or ""),
            credential_generation=current_credential_generation,
        )
        if request.idempotency_key == checkpoint.idempotency_key:
            raise ValueError("checkpoint branch requires a new idempotency key")
        parameters = dict(request.parameters or {})
        restore_material = materialize_cold_restore_inputs(
            checkpoint, checkpoint.validation
        )
        parameters["checkpointRestore"] = {
            "mode": "branch",
            "externalStateRef": checkpoint.external_state_ref,
            "sourceBridgeSessionId": checkpoint.bridge_session_id,
            "restoreMaterial": restore_material.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
            "candidateWorkspace": candidate_workspace.model_dump(
                by_alias=True, mode="json"
            ),
        }
        return await self.execute(
            request.model_copy(
                update={
                    "parameters": parameters,
                    "input_refs": list(
                        dict.fromkeys(
                            [
                                *request.input_refs,
                                checkpoint.external_state_ref,
                                candidate_workspace.head_ref,
                                candidate_workspace.checkpoint_ref,
                            ]
                        )
                    ),
                }
            )
        )

    async def _resolve_profile(self, profile_id: str) -> ManagedAgentProviderProfile:
        async with self._session_factory() as session:
            profile = (
                await session.execute(
                    select(ManagedAgentProviderProfile).where(
                        ManagedAgentProviderProfile.profile_id == profile_id
                    )
                )
            ).scalar_one_or_none()
            if profile is None:
                raise OmnigentOAuthHostError(
                    "Provider Profile was not found", code="profile_resolution_failed"
                )
            if not is_omnigent_oauth_profile(
                runtime_id=profile.runtime_id,
                credential_source=profile.credential_source,
                materialization_mode=profile.runtime_materialization_mode,
            ):
                raise OmnigentOAuthHostError(
                    "Provider Profile is not a supported Omnigent OAuth profile",
                    code="profile_resolution_failed",
                )
            return profile

    async def _resolve_policy_snapshot(self, policy_ref: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            return await OmnigentPolicyService(session).resolve_runtime_snapshot(
                policy_ref
            )

    @classmethod
    def _repository_source(cls, request: AgentExecutionRequest) -> str:
        return authored_repository_source(request)

    @classmethod
    def _starting_branch(cls, request: AgentExecutionRequest) -> str | None:
        return authored_starting_branch(request)

    @classmethod
    def _target_branch(cls, request: AgentExecutionRequest) -> str | None:
        return authored_target_branch(request)

    @classmethod
    def _checkout_commit(cls, request: AgentExecutionRequest) -> str | None:
        return authored_checkout_commit(request)

    @classmethod
    def _restore_input_refs(cls, request: AgentExecutionRequest) -> tuple[str, ...]:
        return authored_restore_input_refs(request)

    @staticmethod
    def _remediation_workspace(
        request: AgentExecutionRequest,
    ) -> RemediationWorkspaceBinding | None:
        if request.remediation_workspace is None:
            return None
        return RemediationWorkspaceBinding.model_validate(
            request.remediation_workspace
        )

    @staticmethod
    def _required_capabilities(request: AgentExecutionRequest) -> tuple[str, ...]:
        return authored_required_capabilities(request)

    @classmethod
    async def _github_token(cls, request: AgentExecutionRequest) -> str | None:
        gh_required = "gh" in cls._required_capabilities(request)
        # A GitHub repository source must be cloned into the sandbox before the
        # host launches. Private repositories require a credential for that clone
        # even when mounted `gh` readiness is not a declared capability — for
        # example read-only or publishMode=none work derives `git` but not `gh`.
        # Resolve the clone credential from the authored GitHub repository source
        # independently of mounted-`gh` readiness so private materialization does
        # not silently fall back to an unauthenticated clone.
        clone_needs_credential = cls._github_repository_source(request) is not None
        if not gh_required and not clone_needs_credential:
            return None
        from moonmind.auth.github_credentials import resolve_github_credential

        repository = str((request.parameters or {}).get("repository") or "").strip()
        resolved = await resolve_github_credential(repo=repository or None)
        token = str(resolved.token or "").strip() if resolved else ""
        if not token:
            if gh_required:
                raise OmnigentOAuthHostError(
                    "GitHub credential is required for mounted gh readiness",
                    code="github_auth_unavailable",
                )
            # A public GitHub clone can proceed unauthenticated; a private clone
            # fails fast with an actionable error at materialization.
            return None
        return token

    @classmethod
    def _github_repository_source(cls, request: AgentExecutionRequest) -> str | None:
        """Return the authored GitHub HTTPS clone source, if any.

        Reuses the single canonical repository-source classifier so GitHub
        detection cannot drift between credential resolution and materialization.
        """
        source = cls._repository_source(request)
        if not source:
            return None
        from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime

        try:
            normalized, kind = OmnigentOAuthHostRuntime._normalize_repository_source(
                source
            )
        except OmnigentOAuthHostError:
            return None
        return normalized if kind == "github_https" else None

    @staticmethod
    def _github_mutation_required(request: AgentExecutionRequest) -> bool:
        return authored_github_mutation_required(request)

    @staticmethod
    def _repository_mutation_required(request: AgentExecutionRequest) -> bool:
        return authored_repository_mutation_required(request)


__all__ = ["OmnigentProfileBoundExecutionCoordinator"]
