"""Lease-authorized coordinator for profile-bound Omnigent execution."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy import select

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


def _failure_evidence(exc: Exception) -> tuple[str, str, str]:
    """Return stable launch classification and operator remediation."""

    code = str(getattr(exc, "code", type(exc).__name__))[:96]
    lowered = code.lower()
    if "credential" in lowered or "oauth" in lowered:
        return code, "configuration_error", "validate_codex_oauth"
    if "lease" in lowered:
        return code, "resource_unavailable", "wait_for_profile_lease"
    if "auth" in lowered:
        return code, "configuration_error", "repair_bridge_authentication"
    if "binding" in lowered or "harness" in lowered:
        return code, "configuration_error", "correct_host_binding"
    if "image" in lowered or "container" in lowered:
        return code, "configuration_error", "repair_host_image"
    if "network" in lowered or "endpoint" in lowered:
        return code, "integration_error", "repair_server_endpoint"
    if "policy" in lowered or "authorization" in lowered:
        return code, "authorization_error", "contact_administrator"
    return code, "integration_error", "retry_transient_upstream"


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
    ) -> None:
        self._session_factory = session_factory
        self._lease_client = lease_client
        self._hosts = host_repository
        self._runtime = host_runtime
        self._run_store = run_store
        self._execute = execution_runner
        self._artifact_gateway = artifact_gateway

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

        async def emit(
            stage: str,
            status: str,
            *,
            code: str | None = None,
            summary: str | None = None,
            failure_class: str | None = None,
            remediation_action: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            await self._run_store.record_lifecycle_event(
                request.idempotency_key,
                event_type=stage,
                status=status,
                event_identity=f"{request.idempotency_key}:{stage}:{status}",
                code=code,
                summary=summary,
                failure_class=failure_class,
                remediation_action=remediation_action,
                metadata={
                    "workflowId": workflow_id,
                    "stepExecutionId": step_execution_id,
                    **dict(metadata or {}),
                },
            )

        provider_lease: CredentialLease | None = None
        host_lease = None
        binding = None
        terminal_status = "completed"
        try:
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
            await emit(
                "profile_lease_acquired",
                "completed",
                metadata={
                    "providerProfileId": profile_id,
                    "providerLeaseId": provider_lease.lease_id,
                },
            )
            current_stage = "host_binding_resolution"
            await emit(current_stage, "started")
            binding = await self._hosts.get_binding_for_profile(profile_id)
            if binding is None:
                binding = await self._hosts.create_or_update_static_binding(
                    profile_id=profile_id,
                    endpoint_ref="default",
                    static_host_id=None,
                    host_launch_profile_ref=(
                        os.getenv("OMNIGENT_CODEX_HOST_LAUNCH_PROFILE") or None
                    ),
                )
            await emit(
                current_stage,
                "completed",
                metadata={"hostBindingRef": binding.binding_ref},
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
            )
            if host_lease.status == "allocating":
                host_lease = await self._hosts.transition_host_lease(
                    host_lease.lease_id,
                    expected_status="allocating",
                    new_status="starting",
                )
            current_stage = "container_start"
            await emit(current_stage, "started")
            preflight = await self._runtime.prepare_host(
                binding=binding,
                host_lease=host_lease,
                workspace_key=(
                    f"{workflow_id}:{step_execution_id or request.idempotency_key}"
                ),
                repository_url=self._repository_url(request),
            )
            await emit(current_stage, "completed")
            host_id = str(preflight["hostId"])
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
            )
            await emit(
                "credential_preflight", "ready", metadata={
                    "providerProfileId": profile_id,
                    "credentialGeneration": host_lease.credential_generation,
                    "omnigentHostId": host_id,
                },
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
                    },
                ),
                artifact_gateway=self._artifact_gateway,
                run_store=self._run_store,
            )
            await emit(current_stage, "completed", metadata={"omnigentHostId": host_id})
            await emit("session_running", "completed")
            if str(result.provider_error_code or "") == "429":
                await self._lease_client.record_cooldown(
                    runtime_id="codex_cli",
                    profile_id=profile_id,
                    owner_id=provider_lease.owner_id,
                    cooldown_seconds=profile.cooldown_after_429_seconds,
                    reason="provider_429",
                )
            return result
        except Exception as exc:
            terminal_status = "failed"
            if bridge_ready:
                code, failure_class, remediation = _failure_evidence(exc)
                await emit(
                    current_stage,
                    "failed",
                    code=code,
                    summary=str(exc),
                    failure_class=failure_class,
                    remediation_action=remediation,
                )
            raise
        finally:
            safe_to_release_provider = host_lease is None
            if host_lease is not None and binding is not None:
                try:
                    await emit("host_cleanup", "started")
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
                        metadata={"cleanupCompleted": True},
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
                        "host_cleanup", "failed",
                        code=type(cleanup_exc).__name__, summary=str(cleanup_exc),
                        failure_class="system_error",
                        remediation_action="inspect_cleanup_diagnostics",
                        metadata={"cleanupCompleted": False, "janitorRequired": True},
                    )
            if provider_lease is not None and safe_to_release_provider:
                await self._lease_client.release_lease(provider_lease)
                await emit(
                    "profile_lease_release",
                    "completed",
                    metadata={"leaseReleased": True},
                )
            await emit(
                "terminal",
                terminal_status,
                metadata={
                    "cleanupCompleted": safe_to_release_provider,
                    "leaseReleased": provider_lease is None or safe_to_release_provider,
                    "janitorRequired": (
                        provider_lease is not None and not safe_to_release_provider
                    ),
                },
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
        workspace = str(
            build_omnigent_selection(request).session.workspace or ""
        ).strip()
        return workspace if "://" in workspace or workspace.startswith("git@") else None


__all__ = ["OmnigentProfileBoundExecutionCoordinator"]
