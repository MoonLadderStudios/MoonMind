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
        if not profile_id:
            raise OmnigentOAuthHostError(
                "OAuth-backed Omnigent execution requires executionProfileRef",
                code="profile_resolution_failed",
            )
        workflow_id, step_execution_id = _request_identity(request)
        bridge_ready = False
        provider_lease: CredentialLease | None = None
        host_lease = None
        binding = None
        try:
            profile = await self._resolve_profile(profile_id)
            owner_id = deterministic_lease_owner_id(
                profile_id=profile_id,
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                workflow_id=workflow_id,
                step_execution_id=step_execution_id,
                idempotency_key=request.idempotency_key,
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
            host_lease = await self._hosts.create_or_get_host_lease(
                binding=binding,
                provider_lease_id=provider_lease.lease_id,
                holder_workflow_id=workflow_id,
                agent_run_id=step_execution_id,
                idempotency_key=request.idempotency_key,
            )
            if host_lease.status in {"stopped", "failed"}:
                host_lease = await self._hosts.restart_host_lease(host_lease.lease_id)
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
            bridge_ready = True
            await self._run_store.record_lifecycle_event(
                request.idempotency_key,
                event_type="profile_lease_acquired",
                metadata={
                    "providerProfileId": profile_id,
                    "providerLeaseId": provider_lease.lease_id,
                    "hostBindingRef": binding.binding_ref,
                    "hostLeaseRef": host_lease.lease_id,
                },
            )
            if host_lease.status == "allocating":
                host_lease = await self._hosts.transition_host_lease(
                    host_lease.lease_id,
                    expected_status="allocating",
                    new_status="starting",
                )
            preflight = await self._runtime.prepare_host(
                binding=binding,
                host_lease=host_lease,
                workspace_key=(
                    f"{workflow_id}:{step_execution_id or request.idempotency_key}"
                ),
                repository_url=self._repository_url(request),
                resolved_skillset_ref=request.resolved_skillset_ref,
                artifact_gateway=self._artifact_service,
            )
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
            await self._run_store.record_lifecycle_event(
                request.idempotency_key,
                event_type="credential_preflight_ready",
                metadata={
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
            if bridge_ready:
                await self._run_store.record_lifecycle_event(
                    request.idempotency_key,
                    event_type="cleanup_started",
                    code=getattr(exc, "code", type(exc).__name__),
                    summary=str(exc),
                )
            raise
        finally:
            safe_to_release_provider = host_lease is None
            if host_lease is not None and binding is not None:
                try:
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
                    if bridge_ready:
                        await self._run_store.record_lifecycle_event(
                            request.idempotency_key,
                            event_type="cleanup_completed",
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
                    if bridge_ready:
                        await self._run_store.record_lifecycle_event(
                            request.idempotency_key,
                            event_type="cleanup_failed",
                            code=type(cleanup_exc).__name__,
                            summary=str(cleanup_exc),
                        )
            if provider_lease is not None and safe_to_release_provider:
                await self._lease_client.release_lease(provider_lease)

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
            if not provider_profile_launch_ready(profile):
                raise OmnigentOAuthHostError(
                    "Provider Profile is not launch ready",
                    code="profile_resolution_failed",
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
