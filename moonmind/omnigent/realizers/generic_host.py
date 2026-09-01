"""Production lifecycle for ``generic-omnigent-host@1``.

Harness-specific compatibility is selected by persisted catalog, Host Class,
and materializer data. This coordinator owns only the common fenced lifecycle;
the existing Omnigent session driver owns provider interaction after creation.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, Awaitable, Callable

from moonmind.omnigent.credential_materializers import (
    CredentialRuntimeHandle,
    credential_runtime_identity,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
    execution_support_identity,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.realizers.turn_delivery import (
    deliver_canonical_turn,
    execution_identity as _execution_identity,
)
from moonmind.omnigent.runtime_bindings import (
    RuntimeBindingSessionAuthoritySink,
    RuntimeBindingState,
    StableRuntimeBinding,
    stable_binding_id,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.temporal_activity_models import AcceptedRepositoryEvidence


logger = logging.getLogger(__name__)

#: The generic host realizer is one cleanup owner of a canonical session.
_GENERIC_HOST_CLEANUP_OWNER = "omnigent_generic_host"

#: Distinguishes "this owner lost the shared cleanup claim" from "no control
#: plane is wired", which must stay runnable in unit harnesses.
_CLEANUP_NOT_OWNED = object()


class GenericOmnigentHostRealizer:
    ref = "generic-omnigent-host@1"

    def __init__(
        self,
        *,
        runtime_binding_store: Any,
        provider_lease_coordinator: Any,
        credential_provisioning_service: Any,
        host_lease_repository: Any,
        host_runtime: Any,
        planned_host_resolver: Callable[
            [OmnigentExecutionPlanEnvelope], Awaitable[tuple[Any, Any]]
        ],
        session_driver: Callable[..., Awaitable[AgentRunResult]],
        session_cleanup_service: Any,
        workspace_publisher: Any,
        artifact_gateway: Any | None = None,
        turn_command_service: Any | None = None,
        cleanup_authority: Any | None = None,
        heartbeat_interval_seconds: float = 60.0,
        heartbeat_ttl_seconds: int = 900,
    ) -> None:
        dependencies = (
            runtime_binding_store,
            provider_lease_coordinator,
            credential_provisioning_service,
            host_lease_repository,
            host_runtime,
            planned_host_resolver,
            session_driver,
            session_cleanup_service,
            workspace_publisher,
        )
        if any(item is None for item in dependencies):
            raise HarnessPlatformError(
                "generic Omnigent realizer dependencies are incomplete",
                code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
            )
        self._runtime_bindings = runtime_binding_store
        self._provider_leases = provider_lease_coordinator
        self._credentials = credential_provisioning_service
        self._host_leases = host_lease_repository
        self._host_runtime = host_runtime
        self._resolve_host = planned_host_resolver
        self._session_driver = session_driver
        self._session_cleanup = session_cleanup_service
        self._workspace_publisher = workspace_publisher
        self._artifacts = artifact_gateway
        self._turn_commands = turn_command_service
        # The generic host is one cleanup owner of a canonical session. It shares
        # the control-plane cleanup aggregate with the legacy session supervisor
        # so an admitted turn's cleanup fence applies to a real janitor
        # (#3707 §4). ``None`` keeps unit harnesses that do not wire the control
        # plane runnable, exactly like ``turn_command_service``.
        self._cleanup_authority = cleanup_authority
        if heartbeat_interval_seconds <= 0 or heartbeat_ttl_seconds <= 0:
            raise ValueError("generic host heartbeat interval and TTL must be positive")
        self._heartbeat_interval = heartbeat_interval_seconds
        self._heartbeat_ttl = heartbeat_ttl_seconds

    async def execute(
        self,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
    ) -> AgentRunResult:
        """Fence one canonical delivery command around the generic lifecycle."""

        if plan.payload.executionRealizerRef != self.ref:
            raise HarnessPlatformError(
                f"plan realizer {plan.payload.executionRealizerRef} != {self.ref}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
            )
        completed = await self._runtime_bindings.get(
            stable_binding_id(
                execution_plan_ref=plan.planRef,
                idempotency_key=request.idempotency_key,
            )
        )
        if completed is not None and completed.state is RuntimeBindingState.cleaned:
            if completed.terminalResult is None:
                raise HarnessPlatformError(
                    "cleaned generic execution has no durable terminal result",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            return AgentRunResult.model_validate(completed.terminalResult)

        return await deliver_canonical_turn(
            self._turn_commands,
            request=request,
            plan=plan,
            command_type="execute_admitted_plan",
            operation=lambda: self._execute_lifecycle(request, plan),
        )

    async def _execute_lifecycle(
        self,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
    ) -> AgentRunResult:
        prior = await self._runtime_bindings.get(
            stable_binding_id(
                execution_plan_ref=plan.planRef,
                idempotency_key=request.idempotency_key,
            )
        )
        if prior is not None and prior.state is RuntimeBindingState.cleaned:
            if prior.terminalResult is None:
                raise HarnessPlatformError(
                    "cleaned generic execution has no durable terminal result",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            return AgentRunResult.model_validate(prior.terminalResult)
        workflow_id, step_execution_id = _execution_identity(request)
        acquired: tuple[Any, ...] = ()
        credential_handles: tuple[CredentialRuntimeHandle, ...] = ()
        binding: StableRuntimeBinding | None = None
        host_lease: Any | None = None
        host_context: dict[str, Any] | None = None
        prepared: Any | None = None
        result: AgentRunResult | None = None
        primary_error: BaseException | None = None
        cleanup_error: BaseException | None = None

        host_class, launch_policy = await self._resolve_host(plan)
        try:
            acquired = await self._provider_leases.acquire_all(
                plan=plan,
                workflow_id=workflow_id,
                step_execution_id=step_execution_id,
                idempotency_key=request.idempotency_key,
            )
            materializers = {
                slot: value.materializerRef
                for slot, value in plan.payload.credentialBindings.items()
            }
            provider_authority = {
                item.slot: {
                    **item.runtime_binding_value(
                        credential_runtime_ref=credential_runtime_identity(
                            item, materializers[item.slot]
                        )[0]
                    ),
                    "materializerRef": materializers[item.slot],
                }
                for item in acquired
            }
            # Acquired generations become immutable before SecretRef resolution.
            binding = await self._runtime_bindings.create_initial(
                execution_plan_ref=plan.planRef,
                idempotency_key=request.idempotency_key,
                provider_leases=provider_authority,
            )
            if binding.state is RuntimeBindingState.cleaned:
                # A completed binding is immutable. A duplicate execution must
                # use the already-published workflow result, never recreate its
                # released host or credential state.
                await self._provider_leases.release_all(acquired)
                acquired = ()
                if binding.terminalResult is not None:
                    return AgentRunResult.model_validate(binding.terminalResult)
                raise HarnessPlatformError(
                    "cleaned generic execution has no durable terminal result",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            if binding.state in {
                RuntimeBindingState.host_allocating,
                RuntimeBindingState.host_ready,
                RuntimeBindingState.session_creating,
                RuntimeBindingState.session_active,
                RuntimeBindingState.draining,
                RuntimeBindingState.cleanup_pending,
                RuntimeBindingState.failed,
            }:
                credential_handles = await self._credentials.load_cleanup_handles(
                    binding.providerLeases, binding.credentialRuntimeHandles
                )
                host_lease = (
                    await self._host_leases.get(binding.hostLeaseRef)
                    if binding.hostLeaseRef
                    else None
                )
                host_context = self._persisted_host_context(binding, host_lease)
                if binding.state in {
                    RuntimeBindingState.host_ready,
                    RuntimeBindingState.session_creating,
                    RuntimeBindingState.session_active,
                }:
                    return await self._resume_attested_host(
                        request=request,
                        plan=plan,
                        binding=binding,
                        host_lease=host_lease,
                        host_context=host_context,
                        credential_handles=credential_handles,
                        acquired=acquired,
                    )
                raise HarnessPlatformError(
                    "an interrupted generic host allocation requires fenced cleanup",
                    code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                )

            credential_handles = await self._credentials.materialize_all(
                request=request,
                plan=plan,
                acquired_leases=acquired,
                writer_image_ref=host_class.imageRef,
            )
            by_runtime_ref = {
                (handle.providerLeaseRef, handle.materializerRef): handle
                for handle in credential_handles
            }
            credential_map = {
                item.slot: by_runtime_ref[
                    (item.provider_lease_ref, materializers[item.slot])
                ].model_dump(by_alias=True, mode="json")
                for item in acquired
            }
            cleanup_authorities: list[str | dict[str, Any]] = [
                {
                    "kind": "credential",
                    "credentialRuntimeRef": handle.credentialRuntimeRef,
                    "cleanupRef": handle.cleanupRef,
                }
                for handle in credential_handles
            ]
            binding = await self._update_binding(
                binding,
                state=RuntimeBindingState.credentials_materialized,
                updates={
                    "credentialRuntimeHandles": credential_map,
                    "cleanupAuthorityRefs": cleanup_authorities,
                },
            )

            async def record_prepared(authority: dict[str, Any]) -> None:
                nonlocal binding
                assert binding is not None
                cleanup = list(binding.cleanupAuthorityRefs)
                if authority not in cleanup:
                    cleanup.append(authority)
                binding = await self._update_binding(
                    binding, updates={"cleanupAuthorityRefs": cleanup}
                )

            prepared = await self._host_runtime.prepare(
                request=request,
                plan=plan,
                host_class=host_class,
                launch_policy=launch_policy,
                authority_sink=record_prepared,
            )
            binding = await self._update_binding(
                binding, state=RuntimeBindingState.host_allocating
            )
            host_lease = await self._host_leases.acquire(
                execution_plan_ref=plan.planRef,
                runtime_binding_id=binding.bindingId,
                host_class_ref=host_class.ref,
                launch_policy_ref=launch_policy.ref,
                harness_id=plan.payload.harnessId,
                harness_implementation_ref=plan.payload.harnessImplementationRef,
                provider_profile_refs=tuple(
                    sorted({item.provider_profile_ref for item in acquired})
                ),
                ttl_seconds=int(launch_policy.limits["timeoutSeconds"]) + 900,
            )
            binding = await self._update_binding(
                binding,
                updates={
                    "hostBindingRef": host_lease.bindingRef,
                    "hostLeaseRef": host_lease.leaseRef,
                    "hostLeaseGeneration": host_lease.generation,
                },
            )

            async def record_host_launch(authority: dict[str, Any]) -> None:
                nonlocal binding, host_lease
                assert binding is not None and host_lease is not None
                host_lease = await self._host_leases.record_launch(
                    host_lease.leaseRef,
                    expected_generation=host_lease.generation,
                    cleanup_handle=authority,
                )
                cleanup = list(binding.cleanupAuthorityRefs)
                if authority not in cleanup:
                    cleanup.append(authority)
                binding = await self._update_binding(
                    binding, updates={"cleanupAuthorityRefs": cleanup}
                )

            host_context = await self._host_runtime.realize(
                request=request,
                plan=plan,
                runtime_binding_id=binding.bindingId,
                host_lease_ref=host_lease.leaseRef,
                host_lease_generation=host_lease.launchGeneration,
                host_class=host_class,
                launch_policy=launch_policy,
                prepared=prepared,
                credential_handles=[
                    handle.model_dump(by_alias=True, mode="json")
                    for handle in credential_handles
                ],
                authority_sink=record_host_launch,
            )
            host_lease = await self._host_leases.mark_ready(
                host_lease.leaseRef,
                expected_generation=host_lease.generation,
                omnigent_host_id=str(host_context["omnigentHostId"]),
                cleanup_handle={
                    "kind": "host",
                    "containerName": host_context["containerName"],
                    "stateVolumeRef": host_context["stateVolumeRef"],
                    "controlVolumeRef": host_context.get("controlVolumeRef"),
                    "launchGeneration": host_lease.launchGeneration,
                },
            )
            attestations = {
                key: str(value)
                for key, value in host_context.items()
                if key.endswith("AttestationRef") and value
            }
            binding = await self._update_binding(
                binding,
                state=RuntimeBindingState.host_ready,
                updates={
                    "omnigentHostId": host_context["omnigentHostId"],
                    "hostLeaseGeneration": host_lease.generation,
                    "attestationRefs": attestations,
                },
            )
            binding = await self._update_binding(
                binding, state=RuntimeBindingState.session_creating
            )
            sink = RuntimeBindingSessionAuthoritySink(self._runtime_bindings, binding)
            try:
                result = await self._drive_session(
                    request=self._bind_exact_host(request, plan, host_context, binding),
                    sink=sink,
                    host_lease=host_lease,
                )
            finally:
                binding = sink.binding
            if result.failure_class is None:
                result = await self._publish_repository(request, result)
            result = result.model_copy(
                update={
                    "metadata": {
                        **(result.metadata or {}),
                        "executionPlanRef": plan.planRef,
                        "runtimeBindingRef": binding.bindingId,
                        "supportCombinationIdentity": execution_support_identity(
                            plan
                        ),
                    }
                }
            )
            binding = await self._update_binding(
                binding,
                updates={
                    "terminalResult": result.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    )
                },
            )
        except BaseException as exc:
            primary_error = exc

        if binding is not None:
            try:
                binding, host_lease = await self._cleanup(
                    request=request,
                    binding=binding,
                    host_lease=host_lease,
                    host_context=host_context,
                    prepared=prepared,
                    credential_handles=credential_handles,
                    acquired=acquired,
                )
            except BaseException as exc:
                cleanup_error = exc
        elif acquired:
            try:
                await self._provider_leases.release_all(acquired)
            except BaseException as exc:
                cleanup_error = exc

        if primary_error is not None:
            raise primary_error
        if result is None:
            raise HarnessPlatformError(
                "generic Omnigent execution ended without a terminal result",
                code=HarnessPlatformFailure.OMNIGENT_GENERIC_DISPATCH_FAILED,
            )
        # Cleanup remains durably cleanup_pending for janitor recovery and does
        # not overwrite an objectively completed provider turn.
        _ = cleanup_error
        return result

    @staticmethod
    def _persisted_host_context(
        binding: StableRuntimeBinding,
        host_lease: Any | None,
    ) -> dict[str, Any] | None:
        cleanup = host_lease.cleanupHandle if host_lease is not None else None
        if not isinstance(cleanup, dict) or not binding.omnigentHostId:
            return None
        return {
            **cleanup,
            "omnigentHostId": binding.omnigentHostId,
            "hostId": binding.omnigentHostId,
            "workspacePath": "/workspaces/run",
            **dict(binding.attestationRefs),
        }

    async def _resume_attested_host(
        self,
        *,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        binding: StableRuntimeBinding,
        host_lease: Any | None,
        host_context: dict[str, Any] | None,
        credential_handles: tuple[CredentialRuntimeHandle, ...],
        acquired: tuple[Any, ...],
    ) -> AgentRunResult:
        if host_lease is None or host_context is None:
            raise HarnessPlatformError(
                "attested host retry authority is incomplete",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        primary_error: BaseException | None = None
        result: AgentRunResult | None = None
        current = binding
        try:
            if current.terminalResult is not None:
                result = AgentRunResult.model_validate(current.terminalResult)
            elif current.state is RuntimeBindingState.host_ready:
                current = await self._update_binding(
                    current, state=RuntimeBindingState.session_creating
                )
            if result is None:
                sink = RuntimeBindingSessionAuthoritySink(
                    self._runtime_bindings, current
                )
                try:
                    result = await self._drive_session(
                        request=self._bind_exact_host(
                            request, plan, host_context, current
                        ),
                        sink=sink,
                        host_lease=host_lease,
                    )
                finally:
                    current = sink.binding
                result = result.model_copy(
                    update={
                        "metadata": {
                            **(result.metadata or {}),
                            "executionPlanRef": plan.planRef,
                            "runtimeBindingRef": sink.binding.bindingId,
                            "supportCombinationIdentity": (
                                execution_support_identity(plan)
                            ),
                        }
                    }
                )
                if result.failure_class is None:
                    result = await self._publish_repository(request, result)
                current = await self._update_binding(
                    sink.binding,
                    updates={
                        "terminalResult": result.model_dump(
                            by_alias=True, mode="json", exclude_none=True
                        )
                    },
                )
        except BaseException as exc:
            primary_error = exc
        try:
            await self._cleanup(
                request=request,
                binding=current,
                host_lease=host_lease,
                host_context=host_context,
                prepared=None,
                credential_handles=credential_handles,
                acquired=acquired,
            )
        except BaseException:
            # Cleanup authority remains durable for janitor retry. Preserve the
            # primary provider boundary result when one exists.
            pass
        if primary_error is not None:
            raise primary_error
        if result is None:
            raise HarnessPlatformError(
                "generic Omnigent retry ended without a terminal result",
                code=HarnessPlatformFailure.OMNIGENT_GENERIC_DISPATCH_FAILED,
            )
        return result

    async def _publish_repository(
        self,
        request: AgentExecutionRequest,
        result: AgentRunResult,
    ) -> AgentRunResult:
        """Publish before cleanup releases the authoritative workspace host."""

        parameters = request.parameters if isinstance(request.parameters, dict) else {}
        publish_mode = str(parameters.get("publishMode") or "none").strip().lower()
        if publish_mode not in {"branch", "pr"}:
            return result
        workflow_id, step_execution_id = _execution_identity(request)
        publication = await self._workspace_publisher.publish_request_workspace(
            request=request,
            current_workflow_id=workflow_id,
            current_step_execution_id=step_execution_id,
        )
        push_status = str(publication.get("push_status") or "").strip().lower()
        # ``no_commits`` is a canonical terminal publication outcome, not a
        # dispatch failure. The publisher already proved the workspace head is
        # exactly the remote base head, so no repository work was lost, and the
        # durable workflow owns whether a step without commits satisfies its
        # publish contract -- exactly as it does for the managed-runtime push
        # boundary. Failing here instead strands workflow-owned side effects
        # (issue transitions, agent-created pull requests) behind an
        # unretryable provider error.
        if push_status not in {"pushed", "no_commits"}:
            raise HarnessPlatformError(
                "generic Omnigent execution produced no publishable repository output",
                code="OMNIGENT_REPOSITORY_OUTPUT_MISSING",
            )
        evidence = AcceptedRepositoryEvidence(
            pushStatus=push_status,
            branch=publication.get("push_branch"),
            baseBranch=publication.get("push_base_branch"),
            headSha=publication.get("push_head_sha"),
            commitsAheadOfBase=publication.get("push_commit_count"),
            repositoryChanged=push_status == "pushed",
            remoteVerified=publication.get("remote_verified"),
            authority="omnigent.generic_host_execution",
        )
        return result.model_copy(
            update={
                "metadata": {
                    **dict(result.metadata or {}),
                    **dict(publication),
                    "acceptedRepositoryEvidence": evidence.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                }
            }
        )

    async def _cleanup(
        self,
        *,
        request: AgentExecutionRequest,
        binding: StableRuntimeBinding,
        host_lease: Any | None,
        host_context: dict[str, Any] | None,
        prepared: Any | None,
        credential_handles: tuple[CredentialRuntimeHandle, ...],
        acquired: tuple[Any, ...],
    ) -> tuple[StableRuntimeBinding, Any | None]:
        if binding.state is RuntimeBindingState.session_active:
            binding = await self._update_binding(
                binding, state=RuntimeBindingState.draining
            )
        if binding.state not in {
            RuntimeBindingState.cleanup_pending,
            RuntimeBindingState.cleaned,
        }:
            binding = await self._update_binding(
                binding, state=RuntimeBindingState.cleanup_pending
            )
        if binding.state is RuntimeBindingState.cleaned:
            return binding, host_lease

        cleanup_claim = await self._claim_canonical_cleanup(
            self._canonical_session_id(request)
        )
        if cleanup_claim is _CLEANUP_NOT_OWNED:
            # Another owner holds this session's cleanup, or an admitted turn
            # already completed it: releasing the host, credentials, or provider
            # session here would race a live owner.
            raise HarnessPlatformError(
                "canonical cleanup authority is owned by another janitor",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        cleanup_evidence: dict[str, Any] = {}
        if binding.omnigentSessionId:
            cleanup_evidence["session"] = await self._session_cleanup.drain(
                binding.omnigentSessionId
            )
        if host_lease is not None and host_lease.status != "cleaned":
            host_lease = await self._host_leases.claim_cleanup(
                host_lease.leaseRef, expected_generation=host_lease.generation
            )
            context = host_context or host_lease.cleanupHandle
            if context is not None:
                cleanup_evidence["host"] = await self._publish_host_logs(
                    request,
                    await self._host_runtime.cleanup(
                        host_context=context,
                        host_lease_ref=host_lease.leaseRef,
                        host_lease_generation=host_lease.launchGeneration,
                    ),
                )
            host_lease = await self._host_leases.mark_cleaned(
                host_lease.leaseRef, expected_generation=host_lease.generation
            )
        if prepared is not None:
            await self._host_runtime.cleanup_prepared(prepared)
        else:
            await self._host_runtime.cleanup_authorities(binding.cleanupAuthorityRefs)
        cleanup_evidence["credentials"] = [
            item.model_dump(by_alias=True, mode="json")
            for item in await self._credentials.cleanup_all(credential_handles)
        ]
        evidence_ref: str | None = None
        if self._artifacts is not None:
            evidence_ref = await self._artifacts.write_json(
                request=request,
                name="generic-host-cleanup.json",
                payload={
                    "bindingId": binding.bindingId,
                    "executionPlanRef": binding.executionPlanRef,
                    "results": cleanup_evidence,
                    "providerCapacityReleaseOrder": "last",
                },
                link_type="evidence.cleanup",
            )
        # Provider capacity releases after all credential-consuming state.
        await self._provider_leases.release_all(acquired)
        await self._complete_canonical_cleanup(cleanup_claim)
        attestation_refs = dict(binding.attestationRefs)
        if evidence_ref:
            attestation_refs["cleanupAttestationRef"] = evidence_ref
        binding = await self._update_binding(
            binding,
            state=RuntimeBindingState.cleaned,
            updates={"attestationRefs": attestation_refs},
        )
        return binding, host_lease

    async def _publish_host_logs(
        self,
        request: AgentExecutionRequest,
        host_evidence: Any,
    ) -> Any:
        """Move the captured host log tail out of the evidence into an artifact.

        The cleanup evidence JSON is compact metadata; the log text itself is
        durable evidence and belongs in an artifact linked as ``hostLogsRef``.
        Publication failures are recorded, never raised: cleanup already
        removed the host and must not be reported as failed over evidence.
        """

        if not isinstance(host_evidence, dict) or "hostLogs" not in host_evidence:
            return host_evidence
        evidence = dict(host_evidence)
        logs = str(evidence.pop("hostLogs") or "")
        if not logs.strip():
            evidence["hostLogsRef"] = None
            evidence["hostLogsEmpty"] = True
            return evidence
        if self._artifacts is None:
            evidence["hostLogsRef"] = None
            evidence["hostLogsCaptureError"] = "no artifact gateway is configured"
            return evidence
        try:
            evidence["hostLogsRef"] = await self._artifacts.write_text(
                request=request,
                name="generic-host-logs.txt",
                payload=logs,
                link_type="evidence.host_logs",
            )
        except Exception as exc:  # noqa: BLE001 - evidence must not fail cleanup
            evidence["hostLogsRef"] = None
            evidence["hostLogsCaptureError"] = (
                f"artifact publication failed: {type(exc).__name__}"
            )
        return evidence

    def _canonical_session_id(self, request: AgentExecutionRequest) -> str:
        """Return the canonical session this realizer's turn bootstrapped."""

        from moonmind.omnigent.control_plane.identities import (
            canonical_omnigent_session_id,
        )

        workflow_id, step_execution_id = _execution_identity(request)
        return canonical_omnigent_session_id(
            workflow_id=workflow_id,
            step_execution_id=step_execution_id,
            agent_run_id=request.correlation_id,
        )

    async def _recovered_session_id(self, binding: StableRuntimeBinding) -> str:
        """Resolve the canonical session a recovery scan is about to clean up.

        Recovery has no admitted request, so it resolves the session through the
        provider session the turn boundary attached to it. A binding with no
        attached provider session never reached a canonical turn.
        """

        if self._cleanup_authority is None or not binding.omnigentSessionId:
            return ""
        return await self._cleanup_authority.resolve_session_id(
            binding.omnigentSessionId
        )

    async def _claim_canonical_cleanup(self, session_id: str) -> Any:
        """Return the shared cleanup claim, or the not-owned sentinel."""

        if self._cleanup_authority is None or not session_id:
            return None
        claim = await self._cleanup_authority.claim(
            session_id, owner_class=_GENERIC_HOST_CLEANUP_OWNER
        )
        return claim if claim is not None else _CLEANUP_NOT_OWNED

    async def _complete_canonical_cleanup(self, claim: Any) -> None:
        if claim is None or self._cleanup_authority is None:
            return
        await self._cleanup_authority.complete(claim)

    async def _update_binding(
        self,
        binding: StableRuntimeBinding,
        *,
        state: RuntimeBindingState | None = None,
        updates: dict[str, Any] | None = None,
    ) -> StableRuntimeBinding:
        return await self._runtime_bindings.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=state,
            updates=updates,
        )

    async def _drive_session(
        self,
        *,
        request: AgentExecutionRequest,
        sink: RuntimeBindingSessionAuthoritySink,
        host_lease: Any,
    ) -> AgentRunResult:
        """Keep both durable ownership leases fresh while a turn is active."""

        stop = asyncio.Event()

        async def heartbeat_loop() -> None:
            while True:
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=self._heartbeat_interval
                    )
                    return
                except TimeoutError:
                    await sink.heartbeat()
                    await self._host_leases.heartbeat(
                        host_lease.leaseRef,
                        expected_generation=host_lease.generation,
                        ttl_seconds=self._heartbeat_ttl,
                    )

        driver_task = asyncio.create_task(
            self._session_driver(request, session_authority_sink=sink)
        )
        heartbeat_task = asyncio.create_task(heartbeat_loop())
        try:
            done, _pending = await asyncio.wait(
                {driver_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done and not driver_task.done():
                driver_task.cancel()
                with suppress(asyncio.CancelledError):
                    await driver_task
                await heartbeat_task
                raise HarnessPlatformError(
                    "generic host heartbeat stopped before session completion",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            result = await driver_task
            stop.set()
            await heartbeat_task
            return result
        finally:
            stop.set()
            for task in (driver_task, heartbeat_task):
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    def _bind_exact_host(
        self,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        host_context: dict[str, Any],
        binding: StableRuntimeBinding,
    ) -> AgentExecutionRequest:
        host_id = str(host_context.get("omnigentHostId") or "").strip()
        if not host_id:
            raise HarnessPlatformError(
                "exact Omnigent host authority is missing",
                code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
            )
        parameters = dict(request.parameters or {})
        omnigent = dict(parameters.get("omnigent") or {})
        source = plan.payload.agentSource
        agent_id = str(
            source.get("upstreamId") or source.get("importedAgentId") or ""
        ).strip()
        if not agent_id:
            raise HarnessPlatformError(
                "planned Agent source has no runnable upstream identity",
                code=HarnessPlatformFailure.OMNIGENT_AGENT_SOURCE_UNAVAILABLE,
            )
        omnigent["endpointRef"] = plan.payload.endpointRef
        omnigent["agent"] = {
            "agentId": agent_id,
            "harnessOverride": plan.payload.harnessId,
        }
        session = dict(omnigent.get("session") or {})
        session.update(
            {
                "hostType": "external",
                "hostId": host_id,
                "workspace": str(
                    host_context.get("workspacePath") or "/workspaces/run"
                ),
                "modelOverride": plan.payload.modelConfig.qualifiedId,
                "reasoningEffort": plan.payload.modelConfig.effort,
                "labels": {
                    **dict(session.get("labels") or {}),
                    "moonmind.runtime_binding_id": binding.bindingId,
                    "moonmind.execution_plan_ref": plan.planRef,
                },
            }
        )
        omnigent["session"] = session
        omnigent["capture"] = dict(plan.payload.capturePolicy)
        primary_lease = binding.providerLeases.get("primary-model")
        if primary_lease is None and binding.providerLeases:
            primary_lease = binding.providerLeases[
                sorted(binding.providerLeases)[0]
            ]
        profile_authorization = {
            "hostClassRef": plan.payload.hostClassRef,
            "launchPolicyRef": plan.payload.launchPolicyRef,
            "executionRealizerRef": self.ref,
            "executionPlanRef": plan.planRef,
            "runtimeBindingRef": binding.bindingId,
            "runtimeBindingId": binding.bindingId,
            "hostBindingRef": binding.hostBindingRef,
            "hostLeaseRef": binding.hostLeaseRef,
            "endpointRef": plan.payload.endpointRef,
            "omnigentHostId": host_id,
        }
        if primary_lease is not None:
            profile_authorization.update(
                {
                    "providerProfileId": primary_lease.get(
                        "providerProfileRef"
                    ),
                    "providerLeaseRef": primary_lease.get("providerLeaseRef"),
                    "credentialGeneration": primary_lease.get(
                        "credentialGeneration"
                    ),
                }
            )
        omnigent["_moonmindProfileAuthorization"] = profile_authorization
        parameters["omnigent"] = omnigent
        parameters["executionPlanRef"] = plan.planRef
        parameters["runtimeBindingRef"] = binding.bindingId
        return request.model_copy(update={"parameters": parameters})

    async def reconcile(self, plan_ref: str, runtime_binding_ref: str | None) -> None:
        if not runtime_binding_ref:
            return
        binding = await self._runtime_bindings.get(runtime_binding_ref)
        if binding is None or binding.executionPlanRef != plan_ref:
            raise HarnessPlatformError(
                "runtime binding is unavailable for reconciliation",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        if binding.state is RuntimeBindingState.cleaned:
            return
        binding = await self._runtime_bindings.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=RuntimeBindingState.cleanup_pending,
            increment_fence=True,
        )
        host_lease = (
            await self._host_leases.get(binding.hostLeaseRef)
            if binding.hostLeaseRef
            else None
        )
        cleanup_handles = await self._credentials.load_cleanup_handles(
            binding.providerLeases, binding.credentialRuntimeHandles
        )
        cleanup_claim = await self._claim_canonical_cleanup(
            await self._recovered_session_id(binding)
        )
        if cleanup_claim is _CLEANUP_NOT_OWNED:
            # Recovery must not release a host or credentials that a live
            # cleanup owner -- or a newly admitted turn -- now owns.
            raise HarnessPlatformError(
                "canonical cleanup authority is owned by another janitor",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        if host_lease is not None and host_lease.status != "cleaned":
            if binding.omnigentSessionId:
                await self._session_cleanup.drain(binding.omnigentSessionId)
            claimed = await self._host_leases.claim_cleanup(
                host_lease.leaseRef, expected_generation=host_lease.generation
            )
            if claimed.cleanupHandle:
                await self._host_runtime.cleanup(
                    host_context=claimed.cleanupHandle,
                    host_lease_ref=claimed.leaseRef,
                    host_lease_generation=claimed.launchGeneration,
                )
            await self._host_leases.mark_cleaned(
                claimed.leaseRef, expected_generation=claimed.generation
            )
        await self._host_runtime.cleanup_authorities(binding.cleanupAuthorityRefs)
        await self._credentials.cleanup_all(cleanup_handles)
        await self._provider_leases.release_from_binding(binding.providerLeases)
        await self._complete_canonical_cleanup(cleanup_claim)
        await self._runtime_bindings.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=RuntimeBindingState.cleaned,
        )


__all__ = ["GenericOmnigentHostRealizer"]
