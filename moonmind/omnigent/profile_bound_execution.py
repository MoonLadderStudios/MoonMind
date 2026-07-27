"""Lease-authorized coordinator for profile-bound Omnigent execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy import select
from temporalio import activity

from api_service.db.models import ManagedAgentProviderProfile
from api_service.services.provider_profile_readiness import (
    provider_profile_launch_ready,
)
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.checkpoints import (
    CandidateWorkspaceAuthority,
    OmnigentCheckpointManifest,
    OmnigentRecoveryMode,
    OmnigentRestoreValidation,
    recovery_mode,
    validate_cold_restore_target,
    validate_restore_material,
)
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.remediation_workspace import (
    RemediationWorkspaceBinding,
    RemediationWorkspaceOwner,
    SandboxRemediationWorkspaceOwner,
)
from moonmind.omnigent.execution_profiles import (
    compile_effective_launch,
    selection_from_request,
)
from moonmind.omnigent.mounted_tool_preflight import MountedToolPreflightError
from moonmind.omnigent.oauth_hosts import (
    OmnigentOAuthHostError,
    OmnigentOAuthHostRepository,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
    deterministic_lease_owner_id,
)
from moonmind.provider_profiles.oauth_policy import is_codex_oauth_profile
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.executions.runtime_capabilities import (
    RuntimeCapabilityError,
    resolve_runtime_execution_capabilities,
)


ExecutionRunner = Callable[..., Awaitable[AgentRunResult]]
ColdRestoreMaterializer = Callable[
    [Mapping[str, Any], AgentExecutionRequest], Awaitable[Mapping[str, Any]]
]


_CHECKPOINT_CAPTURE_REQUIRED_FIELDS = (
    "executionProfileRef",
    "launchPolicyRef",
    "lastBridgeEventCursor",
    "firstMessageIdentity",
    "firstMessageDigest",
    "resourceManifestRef",
    "captureManifestRef",
    "patchCapability",
    "workspaceLocator",
    "baselineCommit",
    "headCommit",
    "headRef",
    "checkpointRef",
    "sourceBranch",
    "publicationState",
)


def build_runtime_checkpoint_capture(
    *,
    request: AgentExecutionRequest,
    result: AgentRunResult,
    provider_profile_id: str,
    credential_generation: int,
    provider_lease_ref: str,
    host_binding_ref: str,
    host_lease_ref: str,
    endpoint_ref: str,
    omnigent_host_id: str,
    bridge_session_id: str,
    effective_launch_ref: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Join bridge harvest and coordinator authority into a v2 capture input.

    The bridge may only contribute durable, path-free evidence. The coordinator
    overwrites identity fields that it authoritatively owns. Missing evidence is
    reported explicitly instead of manufacturing a resumable checkpoint.
    """

    metadata = dict(result.metadata or {})
    supplied = metadata.get("omnigentCheckpointEvidence")
    evidence = dict(supplied) if isinstance(supplied, Mapping) else {}
    identity = dict(evidence.get("identity") or {})
    external_state_ref = str(
        evidence.get("externalStateRef")
        or metadata.get("externalStateRef")
        or ""
    ).strip()
    diagnostics_ref = str(result.diagnostics_ref or "").strip()
    terminal_ref = str(
        evidence.get("terminalRef")
        or metadata.get("finalSnapshotRef")
        or ""
    ).strip()
    session_id = str(
        evidence.get("omnigentSessionId")
        or metadata.get("omnigentSessionId")
        or ""
    ).strip()
    identity.update(
        {
            "providerProfileId": provider_profile_id,
            "credentialGeneration": credential_generation,
            "providerLeaseRef": provider_lease_ref,
            "hostBindingRef": host_binding_ref,
            "hostLeaseRef": host_lease_ref,
            "endpointRef": endpoint_ref,
            "omnigentHostId": omnigent_host_id,
            "omnigentSessionId": session_id,
            "bridgeSessionId": bridge_session_id,
            "externalStateRef": external_state_ref,
            "idempotencyKey": request.idempotency_key,
            "terminalRef": terminal_ref,
            "diagnosticsRef": diagnostics_ref,
            "effectiveLaunchRef": effective_launch_ref,
        }
    )
    evidence["identity"] = identity
    evidence.setdefault("captureManifestRef", metadata.get("captureManifestRef"))
    evidence.setdefault("resourceManifestRef", metadata.get("captureManifestRef"))
    evidence.setdefault("sourceEffectiveLaunchRef", effective_launch_ref)
    evidence.setdefault("instructionRefs", list(request.input_refs))
    evidence.setdefault("contextRefs", [])
    missing = [
        field
        for field in _CHECKPOINT_CAPTURE_REQUIRED_FIELDS
        if not evidence.get(field)
    ]
    for field in (
        "externalStateRef",
        "omnigentSessionId",
        "terminalRef",
        "diagnosticsRef",
    ):
        if not identity.get(field):
            missing.append(f"identity.{field}")
    non_artifact_refs = [
        field
        for field in (
            "externalStateRef",
            "terminalRef",
            "diagnosticsRef",
        )
        if identity.get(field)
        and not str(identity[field]).startswith("artifact://")
    ]
    if non_artifact_refs:
        missing.extend(f"nonArtifact.{field}" for field in non_artifact_refs)
    if missing:
        return None, sorted(set(missing))
    evidence.pop("externalStateRef", None)
    return evidence, []


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
    harness = str(agent.get("harnessOverride") or "").strip()
    if harness and harness != "codex-native":
        raise OmnigentOAuthHostError(
            "selected Omnigent harness is not Codex compatible",
            code="OMNIGENT_CODEX_HARNESS_UNAVAILABLE",
        )
    agent["harnessOverride"] = "codex-native"
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
        cold_restore_materializer: ColdRestoreMaterializer | None = None,
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
        self._cold_restore_materializer = cold_restore_materializer

    async def _put_checkpoint_evidence(
        self,
        payload: bytes,
        *,
        kind: str,
        content_type: str,
    ) -> str:
        if self._artifact_service is None:
            raise ValueError("checkpoint evidence artifact service is unavailable")
        artifact, _upload = await self._artifact_service.create(
            principal="service:omnigent_checkpoint_capture",
            content_type=content_type,
            metadata_json={"artifact_kind": kind},
        )
        await self._artifact_service.write_complete(
            artifact_id=artifact.artifact_id,
            principal="service:omnigent_checkpoint_capture",
            payload=payload,
            content_type=content_type,
        )
        return f"artifact://{artifact.artifact_id}"

    async def _harvest_checkpoint_evidence(
        self,
        *,
        request: AgentExecutionRequest,
        result: AgentRunResult,
        workspace_path: str,
        workspace_locator: Mapping[str, Any],
        effective_launch: Mapping[str, Any],
    ) -> AgentRunResult:
        """Persist workspace/profile evidence while the authorized host is live."""

        metadata = dict(result.metadata or {})
        evidence = dict(metadata.get("omnigentCheckpointEvidence") or {})
        try:
            resolved = str(os.path.realpath(workspace_path))
            git = ["git", "-c", f"safe.directory={resolved}", "-C", resolved]

            def git_output(*args: str, binary: bool = False) -> bytes | str:
                completed = subprocess.run(
                    [*git, *args],
                    check=True,
                    capture_output=True,
                )
                return completed.stdout if binary else completed.stdout.decode().strip()

            baseline = str(
                (request.workspace_spec or {}).get("baseCommit")
                or git_output("rev-list", "--max-parents=0", "HEAD")
            )
            head_commit = str(git_output("rev-parse", "HEAD"))
            source_branch = str(git_output("branch", "--show-current") or "detached")
            patch = git_output("diff", "--binary", baseline, binary=True)
            if not isinstance(patch, bytes):
                raise ValueError("workspace patch capture returned invalid bytes")
            patch_ref = await self._put_checkpoint_evidence(
                patch,
                kind="omnigent_workspace_patch",
                content_type="application/vnd.moonmind.git-patch",
            )
            launch_payload = json.dumps(
                dict(effective_launch),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            launch_ref = await self._put_checkpoint_evidence(
                launch_payload,
                kind="omnigent_effective_launch",
                content_type="application/json",
            )
            first_message_ref = str(metadata.get("firstMessageRequestRef") or "")
            first_message_digest = ""
            if first_message_ref.startswith("artifact://"):
                _artifact, first_message = await self._artifact_service.read(
                    artifact_id=first_message_ref.removeprefix("artifact://"),
                    principal="service:omnigent_checkpoint_capture",
                    allow_restricted_raw=True,
                )
                first_message_digest = (
                    f"sha256:{hashlib.sha256(first_message).hexdigest()}"
                )
            context_refs: list[str] = []
            if (
                request.step_execution is not None
                and str(request.step_execution.context_bundle_ref or "").startswith(
                    "artifact://"
                )
            ):
                context_refs.append(str(request.step_execution.context_bundle_ref))
            evidence.update(
                {
                    "executionProfileRef": launch_ref,
                    "launchPolicyRef": launch_ref,
                    "lastBridgeEventCursor": str(
                        metadata.get("sseEventsCaptured") or "terminal"
                    ),
                    "firstMessageIdentity": first_message_ref,
                    "firstMessageDigest": first_message_digest,
                    "resourceManifestRef": metadata.get("captureManifestRef"),
                    "captureManifestRef": metadata.get("captureManifestRef"),
                    "patchCapability": "git_patch_v1",
                    "workspaceLocator": dict(workspace_locator),
                    "baselineCommit": baseline,
                    "headCommit": head_commit,
                    "headRef": patch_ref,
                    "checkpointRef": patch_ref,
                    "instructionRefs": [
                        ref
                        for ref in request.input_refs
                        if ref.startswith("artifact://")
                    ],
                    "contextRefs": context_refs,
                    "sourceBranch": source_branch,
                    "outputBranch": source_branch,
                    "publicationState": "unpublished",
                }
            )
            metadata["omnigentCheckpointEvidence"] = evidence
        except Exception as exc:
            metadata["omnigentCheckpointEvidenceHarvest"] = {
                "status": "degraded",
                "reason": type(exc).__name__,
            }
        return result.model_copy(update={"metadata": metadata})

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
            # Profile lease or mutating host state.  A previously persisted
            # binding is retry authority; environment input is bootstrap-only.
            requested_target, requested_policy = selection_from_request(
                request.parameters
            )
            current_stage = "host_binding_resolution"
            await emit(current_stage, "started")
            binding = await self._hosts.get_binding_for_profile(profile_id)
            if binding is not None and binding.effective_launch_snapshot is not None:
                effective_launch = dict(binding.effective_launch_snapshot)
                if requested_target and (
                    effective_launch.get("executionProfileRef") != requested_target
                    or effective_launch.get("launchPolicyRef") != requested_policy
                ):
                    raise OmnigentOAuthHostError(
                        "explicit launch selection conflicts with the durable host binding",
                        code="OMNIGENT_LAUNCH_POLICY_BINDING_CONFLICT",
                    )
                if effective_launch.get("schemaVersion") != 2:
                    effective_launch = compile_effective_launch(
                        profile_ref=str(
                            binding.execution_profile_ref or "omnigent-codex@1"
                        ),
                        policy_ref=str(
                            binding.launch_policy_ref
                            or (
                                "codex-on-demand@1"
                                if binding.host_launch_profile_ref
                                else "codex-static@1"
                            )
                        ),
                        provider_profile_id=profile_id,
                    )
                    binding = binding.model_copy(
                        update={"effective_launch_snapshot": effective_launch}
                    )
            elif requested_target:
                effective_launch = compile_effective_launch(
                    profile_ref=requested_target,
                    policy_ref=requested_policy,
                    provider_profile_id=profile_id,
                )
                if binding is not None:
                    bound_mode = (
                        "on_demand_docker"
                        if binding.host_launch_profile_ref
                        else "static_compose"
                    )
                    if effective_launch["hostMode"] != bound_mode:
                        raise OmnigentOAuthHostError(
                            "explicit launch policy conflicts with the durable host binding",
                            code="OMNIGENT_LAUNCH_POLICY_BINDING_CONFLICT",
                        )
            else:
                bootstrap_on_demand = (
                    bool(binding.host_launch_profile_ref)
                    if binding is not None
                    else bool(os.getenv("OMNIGENT_CODEX_HOST_LAUNCH_PROFILE"))
                )
                effective_launch = compile_effective_launch(
                    profile_ref="omnigent-codex@1",
                    policy_ref=(
                        "codex-on-demand@1"
                        if bootstrap_on_demand
                        else "codex-static@1"
                    ),
                    provider_profile_id=profile_id,
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
                if (
                    str(effective_launch.get("harness") or "codex-native")
                    != "codex-native"
                ):
                    raise OmnigentOAuthHostError(
                        "remediation workspace requires codex-native",
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
                runtime_id="codex_cli",
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
            if binding is None:
                selected_on_demand = effective_launch["hostMode"] == "on_demand_docker"
                binding = await self._hosts.create_or_update_static_binding(
                    profile_id=profile_id,
                    endpoint_ref=str(effective_launch["endpointRef"]),
                    static_host_id=None,
                    host_launch_profile_ref=(
                        (
                            "codex-on-demand@1"
                            if requested_target
                            else os.getenv("OMNIGENT_CODEX_HOST_LAUNCH_PROFILE")
                        )
                        if selected_on_demand
                        else None
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
                    else self._workspace_locator(request)
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
            )
            checkpoint_restore = (request.parameters or {}).get(
                "checkpointRestore"
            )
            if isinstance(checkpoint_restore, Mapping):
                if self._cold_restore_materializer is None:
                    raise ValueError(
                        "checkpoint cold restore materialization boundary "
                        "is unavailable"
                    )
                restore_input = {
                    **dict(checkpoint_restore),
                    "workspacePath": str(preflight["workspacePath"]),
                    "workspaceLocator": (
                        remediation_resolution.get("workspaceLocator")
                        if remediation_resolution is not None
                        else self._workspace_locator(request)
                    ),
                    "effectiveLaunchRef": str(effective_launch["snapshotRef"]),
                    "credentialGeneration": host_lease.credential_generation,
                }
                materialized = await self._cold_restore_materializer(
                    restore_input,
                    request,
                )
                if not materialized.get("restoreEvidenceRef"):
                    raise ValueError(
                        "cold restore did not return materialization evidence"
                    )
                await emit(
                    "checkpoint_cold_restore",
                    "completed",
                    metadata={
                        "restoreEvidenceRef": materialized["restoreEvidenceRef"],
                        "workspaceLocator": restore_input["workspaceLocator"],
                    },
                )
            await emit(current_stage, "completed")
            await emit("credential_mount", "started")
            await emit(
                "credential_mount",
                "completed",
                metadata={
                    "credentialGeneration": host_lease.credential_generation,
                    "credentialMountPath": "/home/app/.codex",
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
                ),
                artifact_gateway=self._artifact_gateway,
                run_store=self._run_store,
            )
            result_failed = bool(result.failure_class or result.provider_error_code)
            result_status = "failed" if result_failed else "completed"
            terminal_status = result_status
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
            result = await self._harvest_checkpoint_evidence(
                request=request,
                result=result,
                workspace_path=str(preflight["workspacePath"]),
                workspace_locator=(
                    remediation_resolution.get("workspaceLocator")
                    if remediation_resolution is not None
                    else self._workspace_locator(request)
                ),
                effective_launch=effective_launch,
            )
            checkpoint_capture, degraded_reasons = build_runtime_checkpoint_capture(
                request=request,
                result=result,
                provider_profile_id=profile_id,
                credential_generation=host_lease.credential_generation,
                provider_lease_ref=provider_lease.lease_id,
                host_binding_ref=binding.binding_ref,
                host_lease_ref=host_lease.lease_id,
                endpoint_ref=binding.endpoint_ref,
                omnigent_host_id=host_id,
                bridge_session_id=bridge.bridge_session_id,
                effective_launch_ref=str(effective_launch["snapshotRef"]),
            )
            checkpoint_metadata = dict(result.metadata or {})
            if checkpoint_capture is not None:
                checkpoint_metadata["omnigentCheckpointCapture"] = checkpoint_capture
                checkpoint_metadata["omnigentCheckpointCaptureStatus"] = {
                    "status": "complete",
                    "boundary": "terminal_harvest",
                    "degradedReasons": [],
                }
            else:
                checkpoint_metadata["omnigentCheckpointCaptureStatus"] = {
                    "status": "degraded",
                    "boundary": "terminal_harvest",
                    "degradedReasons": degraded_reasons,
                }
            result = result.model_copy(update={"metadata": checkpoint_metadata})
            if str(result.provider_error_code or "") == "429":
                await self._lease_client.record_cooldown(
                    runtime_id="codex_cli",
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
            await emit(
                "terminal",
                terminal_status,
                metadata={
                    "cleanupCompleted": safe_to_release_provider,
                    "leaseReleased": lease_released,
                    "janitorRequired": (
                        provider_lease is not None and not lease_released
                    ),
                },
                ignore_errors=True,
            )

    async def recover_from_checkpoint(
        self,
        *,
        request: AgentExecutionRequest,
        checkpoint: OmnigentCheckpointManifest,
        provider_lease: Mapping[str, Any] | None,
        host_lease: Mapping[str, Any] | None,
        host_registered: bool,
        session_valid: bool,
        first_message_consistent: bool,
        current_credential_generation: int,
        candidate_workspace: CandidateWorkspaceAuthority,
    ) -> AgentRunResult:
        """Live-reattach when safe; otherwise cold-restore on a new lease/session."""

        validation = await self._validate_checkpoint_restore(
            request=request,
            checkpoint=checkpoint,
            candidate_workspace=candidate_workspace,
            current_credential_generation=current_credential_generation,
            provider_lease=provider_lease,
            host_lease=host_lease,
            host_registered=host_registered,
            session_valid=session_valid,
        )
        if not validation.valid:
            raise ValueError(
                f"checkpoint recovery denied: {validation.reason_code or 'invalid'}"
            )
        identity = checkpoint.identity
        mode = recovery_mode(
            identity,
            provider_lease=provider_lease,
            host_lease=host_lease,
            host_registered=host_registered,
            session_valid=session_valid,
            first_message_consistent=first_message_consistent,
        )
        if mode == OmnigentRecoveryMode.LIVE_REATTACH:
            if not validation.live_reattach.available:
                raise ValueError("checkpoint live reattach authority is unavailable")
            if request.execution_profile_ref != identity.provider_profile_id:
                raise ValueError("live reattach Provider Profile mismatch")
            live_request = _bind_candidate_workspace(request, candidate_workspace)
            live_request = live_request.model_copy(
                update={
                    "idempotency_key": identity.idempotency_key,
                    "input_refs": list(
                        dict.fromkeys(
                            [*live_request.input_refs, identity.external_state_ref]
                        )
                    ),
                }
            )
            result = await self._execute(
                _bind_exact_host(
                    live_request,
                    host_id=str(identity.omnigent_host_id),
                    workspace_path="/workspaces/run",
                    profile_authorization=identity.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    ),
                ),
                artifact_gateway=self._artifact_gateway,
                run_store=self._run_store,
            )
            return self._with_restore_validation(result, validation)

        validate_cold_restore_target(
            identity,
            provider_profile_id=str(request.execution_profile_ref or ""),
            credential_generation=current_credential_generation,
        )
        if not validation.workspace_cold_restore.available:
            raise ValueError("checkpoint workspace cold restore authority is unavailable")
        cold_key = deterministic_lease_owner_id(
            profile_id=identity.provider_profile_id,
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            idempotency_key=f"{identity.idempotency_key}:cold:{request.idempotency_key}",
        )
        parameters = dict(request.parameters or {})
        parameters["checkpointRestore"] = {
            "mode": "cold_restore",
            "externalStateRef": identity.external_state_ref,
            "sourceBridgeSessionId": identity.bridge_session_id,
            "baselineCommit": checkpoint.baseline_commit,
            "headCommit": checkpoint.head_commit,
            "headRef": checkpoint.head_ref,
            "checkpointRef": checkpoint.checkpoint_ref,
            "diffRef": checkpoint.diff_ref,
            "patchCapability": checkpoint.patch_capability,
            "instructionRefs": checkpoint.instruction_refs,
            "contextRefs": checkpoint.context_refs,
            "sourceEffectiveLaunchRef": checkpoint.source_effective_launch_ref,
            "candidateWorkspace": candidate_workspace.model_dump(
                by_alias=True, mode="json"
            ),
        }
        result = await self.execute(
            request.model_copy(
                update={
                    "idempotency_key": cold_key,
                    "parameters": parameters,
                    "input_refs": list(
                        dict.fromkeys(
                            [
                                *request.input_refs,
                                identity.external_state_ref,
                                checkpoint.head_ref,
                                checkpoint.checkpoint_ref,
                                *([checkpoint.diff_ref] if checkpoint.diff_ref else []),
                                *checkpoint.instruction_refs,
                                *checkpoint.context_refs,
                                candidate_workspace.head_ref,
                                candidate_workspace.checkpoint_ref,
                            ]
                        )
                    ),
                }
            )
        )
        return self._with_restore_validation(result, validation)

    async def branch_from_checkpoint(
        self,
        *,
        request: AgentExecutionRequest,
        checkpoint: OmnigentCheckpointManifest,
        current_credential_generation: int,
        candidate_workspace: CandidateWorkspaceAuthority,
    ) -> AgentRunResult:
        """Create a new capacity-gated host lease and session from checkpoint refs."""

        validation = await self._validate_checkpoint_restore(
            request=request,
            checkpoint=checkpoint,
            candidate_workspace=candidate_workspace,
            current_credential_generation=current_credential_generation,
        )
        if not validation.valid or not validation.branch_creation.available:
            raise ValueError("checkpoint branch authority is unavailable")
        identity = checkpoint.identity
        validate_cold_restore_target(
            identity,
            provider_profile_id=str(request.execution_profile_ref or ""),
            credential_generation=current_credential_generation,
        )
        if request.idempotency_key == identity.idempotency_key:
            raise ValueError("checkpoint branch requires a new idempotency key")
        parameters = dict(request.parameters or {})
        parameters["checkpointRestore"] = {
            "mode": "branch",
            "externalStateRef": identity.external_state_ref,
            "sourceBridgeSessionId": identity.bridge_session_id,
            "baselineCommit": checkpoint.baseline_commit,
            "headCommit": checkpoint.head_commit,
            "headRef": checkpoint.head_ref,
            "checkpointRef": checkpoint.checkpoint_ref,
            "diffRef": checkpoint.diff_ref,
            "patchCapability": checkpoint.patch_capability,
            "instructionRefs": checkpoint.instruction_refs,
            "contextRefs": checkpoint.context_refs,
            "sourceEffectiveLaunchRef": checkpoint.source_effective_launch_ref,
            "candidateWorkspace": candidate_workspace.model_dump(
                by_alias=True, mode="json"
            ),
        }
        result = await self.execute(
            request.model_copy(
                update={
                    "parameters": parameters,
                    "input_refs": list(
                        dict.fromkeys(
                            [
                                *request.input_refs,
                                identity.external_state_ref,
                                checkpoint.head_ref,
                                checkpoint.checkpoint_ref,
                                *([checkpoint.diff_ref] if checkpoint.diff_ref else []),
                                *checkpoint.instruction_refs,
                                *checkpoint.context_refs,
                                candidate_workspace.head_ref,
                                candidate_workspace.checkpoint_ref,
                            ]
                        )
                    ),
                }
            )
        )
        return self._with_restore_validation(result, validation)

    @staticmethod
    def _with_restore_validation(
        result: AgentRunResult,
        validation: OmnigentRestoreValidation,
    ) -> AgentRunResult:
        """Persist the current recovery decision beside terminal runtime evidence."""

        metadata = dict(result.metadata or {})
        metadata["omnigentRestoreValidation"] = validation.model_dump(
            by_alias=True,
            mode="json",
            exclude_none=True,
        )
        return result.model_copy(update={"metadata": metadata})

    async def _validate_checkpoint_restore(
        self,
        *,
        request: AgentExecutionRequest,
        checkpoint: OmnigentCheckpointManifest,
        candidate_workspace: CandidateWorkspaceAuthority,
        current_credential_generation: int,
        provider_lease: Mapping[str, Any] | None = None,
        host_lease: Mapping[str, Any] | None = None,
        host_registered: bool = False,
        session_valid: bool = False,
    ) -> OmnigentRestoreValidation:
        """Validate all artifact and lineage authority before recovery side effects."""

        launch = request.step_execution
        if launch is None:
            raise ValueError("checkpoint recovery requires Step Execution lineage")
        if (
            candidate_workspace.head_ref != checkpoint.head_ref
            or candidate_workspace.head_digest != checkpoint.head_digest
            or candidate_workspace.checkpoint_ref != checkpoint.checkpoint_ref
            or candidate_workspace.checkpoint_digest != checkpoint.checkpoint_digest
        ):
            raise ValueError("candidate workspace authority does not match checkpoint")
        artifacts = {
            ref: await self._artifact_gateway.read_bytes(ref)
            for ref in checkpoint.artifact_digests
        }
        return validate_restore_material(
            checkpoint,
            workflow_id=launch.workflow_id,
            run_id=launch.run_id,
            logical_step_id=launch.logical_step_id,
            step_execution_id=launch.step_execution_id,
            attempt_ordinal=launch.execution_ordinal,
            boundary=checkpoint.boundary,
            provider_profile_id=str(request.execution_profile_ref or ""),
            credential_generation=current_credential_generation,
            repository_baseline=checkpoint.baseline_commit,
            repository_head=checkpoint.head_commit,
            artifact_reader=artifacts.__getitem__,
            current_provider_lease_ref=str(
                (provider_lease or {}).get("lease_id")
                or (provider_lease or {}).get("leaseId")
                or ""
            )
            or None,
            current_host_lease_ref=str(
                (host_lease or {}).get("lease_id")
                or (host_lease or {}).get("leaseId")
                or ""
            )
            or None,
            host_registered=host_registered,
            session_valid=session_valid,
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
            if not is_codex_oauth_profile(
                runtime_id=profile.runtime_id,
                credential_source=profile.credential_source,
                materialization_mode=profile.runtime_materialization_mode,
            ):
                raise OmnigentOAuthHostError(
                    "Provider Profile is not Codex OAuth",
                    code="profile_resolution_failed",
                )
            return profile

    @staticmethod
    def _workspace_locator(request: AgentExecutionRequest) -> Mapping[str, Any]:
        if request.remediation_workspace is not None:
            binding = RemediationWorkspaceBinding.model_validate(
                request.remediation_workspace
            )
            return binding.destination_workspace_locator.model_dump(
                by_alias=True, mode="json"
            )
        locator = request.workspace_spec.get("workspaceLocator")
        if not isinstance(locator, Mapping):
            raise OmnigentOAuthHostError(
                "profile-bound Omnigent execution requires workspaceSpec.workspaceLocator",
                code="WORKSPACE_LOCATOR_REQUIRED",
            )
        return dict(locator)

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
        raw = (request.parameters or {}).get("requiredCapabilities")
        if not isinstance(raw, list):
            return ()
        return tuple(
            dict.fromkeys(
                str(value).strip().lower() for value in raw if str(value).strip()
            )
        )

    @staticmethod
    async def _github_token(request: AgentExecutionRequest) -> str | None:
        if "gh" not in OmnigentProfileBoundExecutionCoordinator._required_capabilities(
            request
        ):
            return None
        from moonmind.auth.github_credentials import resolve_github_credential

        repository = str((request.parameters or {}).get("repository") or "").strip()
        resolved = await resolve_github_credential(repo=repository or None)
        token = str(resolved.token or "").strip() if resolved else ""
        if not token:
            raise OmnigentOAuthHostError(
                "GitHub credential is required for mounted gh readiness",
                code="github_auth_unavailable",
            )
        return token

    @staticmethod
    def _github_mutation_required(request: AgentExecutionRequest) -> bool:
        if "gh" not in OmnigentProfileBoundExecutionCoordinator._required_capabilities(
            request
        ):
            return False
        parameters = request.parameters or {}
        publish_mode = str(parameters.get("publishMode") or "none").strip().lower()
        if publish_mode not in {"", "none"}:
            return True
        skill = parameters.get("skill")
        if not isinstance(skill, Mapping):
            return False
        side_effect = skill.get("sideEffect")
        return isinstance(side_effect, Mapping) and bool(
            str(side_effect.get("kind") or "").strip()
        )

    @staticmethod
    def _repository_mutation_required(request: AgentExecutionRequest) -> bool:
        parameters = request.parameters or {}
        if bool(parameters.get("repositoryMutationRequired")):
            return True
        publish_mode = str(parameters.get("publishMode") or "none").strip().lower()
        if publish_mode not in {"", "none"}:
            return True
        skill = parameters.get("skill")
        if isinstance(skill, Mapping):
            side_effect = skill.get("sideEffect")
            if isinstance(side_effect, Mapping) and str(
                side_effect.get("kind") or ""
            ).strip():
                return True
        return False


__all__ = ["OmnigentProfileBoundExecutionCoordinator"]
