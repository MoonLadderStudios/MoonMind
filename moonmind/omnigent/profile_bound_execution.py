"""Lease-authorized coordinator for profile-bound Omnigent execution."""

from __future__ import annotations

import asyncio
import os
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
    OmnigentCheckpointIdentity,
    OmnigentRecoveryMode,
    recovery_mode,
    validate_cold_restore_target,
)
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
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
from moonmind.workflows.adapters.omnigent_agent_adapter import build_omnigent_selection


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
    ) -> None:
        self._session_factory = session_factory
        self._lease_client = lease_client
        self._hosts = host_repository
        self._runtime = host_runtime
        self._run_store = run_store
        self._execute = execution_runner
        self._artifact_gateway = artifact_gateway
        self._artifact_service = artifact_service or artifact_gateway

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
                workspace_locator=self._workspace_locator(request),
                current_workflow_id=workflow_id,
                current_step_execution_id=(
                    step_execution_id or request.idempotency_key
                ),
                repository_url=self._repository_url(request),
                repository_branch=self._repository_branch(request),
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
        checkpoint: OmnigentCheckpointIdentity,
        provider_lease: Mapping[str, Any] | None,
        host_lease: Mapping[str, Any] | None,
        host_registered: bool,
        session_valid: bool,
        first_message_consistent: bool,
        current_credential_generation: int,
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
            live_request = request.model_copy(
                update={
                    "idempotency_key": checkpoint.idempotency_key,
                    "input_refs": list(
                        dict.fromkeys(
                            [*request.input_refs, checkpoint.external_state_ref]
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
        parameters["checkpointRestore"] = {
            "mode": "cold_restore",
            "externalStateRef": checkpoint.external_state_ref,
            "sourceBridgeSessionId": checkpoint.bridge_session_id,
        }
        return await self.execute(
            request.model_copy(
                update={
                    "idempotency_key": cold_key,
                    "parameters": parameters,
                    "input_refs": list(
                        dict.fromkeys(
                            [*request.input_refs, checkpoint.external_state_ref]
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
        parameters["checkpointRestore"] = {
            "mode": "branch",
            "externalStateRef": checkpoint.external_state_ref,
            "sourceBridgeSessionId": checkpoint.bridge_session_id,
        }
        return await self.execute(
            request.model_copy(
                update={
                    "parameters": parameters,
                    "input_refs": list(
                        dict.fromkeys(
                            [*request.input_refs, checkpoint.external_state_ref]
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
    def _repository_url(request: AgentExecutionRequest) -> str | None:
        authored = str(
            request.workspace_spec.get("repository")
            or request.workspace_spec.get("repo")
            or ""
        ).strip()
        if authored:
            if "://" in authored or authored.startswith("git@"):
                return authored
            if authored.count("/") == 1:
                return f"https://github.com/{authored}.git"
        workspace = str(
            build_omnigent_selection(request).session.workspace or ""
        ).strip()
        return workspace if "://" in workspace or workspace.startswith("git@") else None

    @staticmethod
    def _repository_branch(request: AgentExecutionRequest) -> str | None:
        value = str(
            request.workspace_spec.get("branch")
            or request.workspace_spec.get("startingBranch")
            or ""
        ).strip()
        return value or None

    @staticmethod
    def _workspace_locator(request: AgentExecutionRequest) -> Mapping[str, Any]:
        locator = request.workspace_spec.get("workspaceLocator")
        if not isinstance(locator, Mapping):
            raise OmnigentOAuthHostError(
                "profile-bound Omnigent execution requires workspaceSpec.workspaceLocator",
                code="WORKSPACE_LOCATOR_REQUIRED",
            )
        return dict(locator)

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


__all__ = ["OmnigentProfileBoundExecutionCoordinator"]
