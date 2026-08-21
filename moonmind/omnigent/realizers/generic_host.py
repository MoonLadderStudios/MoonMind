"""Harness-neutral production realizer for ``generic-omnigent-host@1``.

The realizer consumes an already-persisted execution plan. It coordinates
injected provider/credential, host, runtime-binding and session-driver
boundaries without interpreting a harness id.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import Any

from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
    execution_support_identity,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import (
    get_host_class,
    get_launch_policy,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GenericRealizerDependencies:
    """Infrastructure supplied at the outer composition boundary."""

    runtime_binding_store: Any
    runtime_authority: Any
    host_runtime: Any
    turn_command_service: Any
    execution_driver: Callable[
        [AgentExecutionRequest], Awaitable[AgentRunResult]
    ]


class GenericOmnigentHostRealizer:
    ref = "generic-omnigent-host@1"

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        plan_store: Any | None = None,
        runtime_binding_store: Any | None = None,
        runtime_authority: Any | None = None,
        host_runtime: Any | None = None,
        execution_driver: Callable[[AgentExecutionRequest], Awaitable[AgentRunResult]]
        | None = None,
        turn_command_service: Any | None = None,
        dependency_factory: Callable[[Any | None], GenericRealizerDependencies]
        | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._plan_store = plan_store
        self._runtime_binding_store = runtime_binding_store
        self._runtime_authority = runtime_authority
        self._host_runtime = host_runtime
        self._execution_driver = execution_driver
        self._turn_command_service = turn_command_service
        self._dependency_factory = dependency_factory

    async def execute(
        self,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
    ) -> AgentRunResult:
        if plan.payload.executionRealizerRef != self.ref:
            raise HarnessPlatformError(
                f"plan realizer {plan.payload.executionRealizerRef} != {self.ref}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
            )
        request_plan_ref = str(
            (request.parameters or {}).get("executionPlanRef") or ""
        ).strip()
        if request_plan_ref != plan.planRef:
            raise HarnessPlatformError(
                "runtime request does not name the admitted execution plan",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        if self._plan_store is not None:
            persisted = await self._plan_store.load(plan.planRef)
            if persisted != plan:
                raise HarnessPlatformError(
                    "execution plan is unavailable or differs from durable authority",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                )
        return await self._execute_generic(request, plan)

    def _production_dependencies(self) -> GenericRealizerDependencies:
        composed = (
            self._dependency_factory(self._session_factory)
            if self._dependency_factory is not None
            else None
        )
        values = GenericRealizerDependencies(
            runtime_binding_store=self._runtime_binding_store
            or (composed.runtime_binding_store if composed is not None else None),
            runtime_authority=self._runtime_authority
            or (composed.runtime_authority if composed is not None else None),
            host_runtime=self._host_runtime
            or (composed.host_runtime if composed is not None else None),
            turn_command_service=self._turn_command_service
            or (composed.turn_command_service if composed is not None else None),
            execution_driver=self._execution_driver
            or (composed.execution_driver if composed is not None else None),
        )
        missing = sorted(
            name
            for name, value in {
                "runtime binding store": values.runtime_binding_store,
                "runtime authority": values.runtime_authority,
                "host runtime": values.host_runtime,
                "turn command service": values.turn_command_service,
                "execution driver": values.execution_driver,
            }.items()
            if value is None
        )
        if missing:
            raise HarnessPlatformError(
                "generic realizer is not composed: " + ", ".join(missing),
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
            )
        return values

    async def _execute_generic(
        self,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
    ) -> AgentRunResult:
        host_class = get_host_class(plan.payload.hostClassRef)
        launch_policy = get_launch_policy(plan.payload.launchPolicyRef)
        dependencies = self._production_dependencies()
        binding_store = dependencies.runtime_binding_store
        runtime_authority = dependencies.runtime_authority
        host_runtime = dependencies.host_runtime
        turn_commands = dependencies.turn_command_service
        readiness = getattr(host_runtime, "assert_ready", None)
        if not callable(readiness):
            raise HarnessPlatformError(
                "generic host runtime lacks a pre-side-effect readiness boundary",
                code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
            )
        readiness()
        authority_readiness = getattr(runtime_authority, "assert_ready", None)
        if callable(authority_readiness):
            authority_readiness(plan)

        from moonmind.omnigent.control_plane.turn_commands import (
            CanonicalSessionBootstrap,
        )

        step = request.step_execution
        workflow_id = step.workflow_id if step is not None else request.correlation_id
        step_execution_id = (
            step.step_execution_id if step is not None else request.idempotency_key
        )
        command_claim = await turn_commands.claim(
            workflow_id=workflow_id,
            provider_session_ref="",
            chat_binding_id=None,
            command_type="execute_admitted_plan",
            idempotency_key=request.idempotency_key,
            payload_digest=plan.planRef,
            step_execution_id=step_execution_id,
            bootstrap=CanonicalSessionBootstrap(
                provider="omnigent",
                step_execution_id=step_execution_id,
                agent_run_id=request.correlation_id,
                source_idempotency_key=request.idempotency_key,
                execution_plan_ref=plan.planRef,
            ),
        )
        if not command_claim.owns_delivery:
            raise HarnessPlatformError(
                "canonical turn command is already settled or owned elsewhere; "
                "reconciliation is required before delivery",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )

        command_authority = {
            "commandId": command_claim.command_id,
            "claimToken": command_claim.claim_token,
            "sessionId": command_claim.session_id,
            "turnAttemptId": command_claim.turn_attempt_id,
            "expectedSessionRevision": (
                command_claim.expected_session_revision
            ),
            "fencingGeneration": command_claim.fencing_generation,
        }
        acquired = None
        host_realized = False
        existing_host_authority = False
        cleanup_deferred = False
        binding = None
        host_authority_binding_ref: str | None = None
        try:
            acquired = await runtime_authority.acquire(
                request=request,
                plan=plan,
                command_authority=command_authority,
            )
            handles_by_slot: dict[str, dict[str, Any]] = {}
            for handle in acquired.credential_handles:
                slot = str(handle.get("credentialSlot") or "").strip()
                if not slot and len(acquired.provider_leases) == 1:
                    slot = next(iter(acquired.provider_leases))
                if not slot or slot not in acquired.provider_leases:
                    raise HarnessPlatformError(
                        "credential runtime handle lacks its admitted slot identity",
                        code=(
                            HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
                        ),
                    )
                if slot in handles_by_slot:
                    raise HarnessPlatformError(
                        f"credential slot {slot} produced duplicate runtime handles",
                        code=(
                            HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
                        ),
                    )
                handles_by_slot[slot] = dict(handle)
            binding = await binding_store.create_initial(
                execution_plan_ref=plan.planRef,
                provider_leases=dict(acquired.provider_leases),
                credential_handles=handles_by_slot,
            )
            host_authority_binding_ref = binding.runtimeBindingRef
            await turn_commands.bind_runtime_authority(
                session_id=command_claim.session_id,
                execution_plan_ref=plan.planRef,
                runtime_binding_ref=binding.runtimeBindingRef,
            )
            existing_host_authority = binding.hostBindingRef is not None
            if existing_host_authority:
                raise HarnessPlatformError(
                    "an existing host binding requires reconciliation; refusing "
                    "to launch a duplicate host",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            host_context = await host_runtime.realize(
                request=request,
                plan=plan,
                host_class=host_class,
                launch_policy=launch_policy,
                credential_handles=list(acquired.credential_handles),
                runtime_binding_ref=binding.runtimeBindingRef,
                command_authority=command_authority,
            )
            host_realized = True
            required_host_fields = {
                "hostBindingRef": host_context.get("hostBindingRef"),
                "hostLeaseRef": host_context.get("hostLeaseRef"),
                "hostLeaseGeneration": host_context.get("hostLeaseGeneration"),
                "omnigentHostId": host_context.get("omnigentHostId")
                or host_context.get("hostId"),
            }
            missing = sorted(
                key for key, value in required_host_fields.items() if value in {None, ""}
            )
            if missing:
                raise HarnessPlatformError(
                    "host realization omitted exact runtime-binding authority: "
                    + ", ".join(missing),
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            binding = await binding_store.update_with_host(
                binding.runtimeBindingRef,
                host_binding_ref=str(required_host_fields["hostBindingRef"]),
                host_lease_ref=str(required_host_fields["hostLeaseRef"]),
                host_lease_generation=int(
                    required_host_fields["hostLeaseGeneration"]
                ),
                omnigent_host_id=str(required_host_fields["omnigentHostId"]),
                host_harness_attestation_ref=host_context.get(
                    "hostHarnessAttestationRef"
                ),
                exact_host_capability_decision_ref=host_context.get(
                    "exactHostCapabilityDecisionRef"
                ),
                workspace_resolution_ref=host_context.get(
                    "workspaceResolutionRef"
                ),
                model_option_attestation_ref=host_context.get(
                    "modelOptionAttestationRef"
                ),
                skill_delivery_attestation_ref=host_context.get(
                    "skillDeliveryAttestationRef"
                ),
            )
            await turn_commands.bind_runtime_authority(
                session_id=command_claim.session_id,
                execution_plan_ref=plan.planRef,
                runtime_binding_ref=binding.runtimeBindingRef,
            )
            enriched = self._bind_host_to_request(
                request,
                host_context,
                runtime_binding_ref=binding.runtimeBindingRef,
            )
            result = await dependencies.execution_driver(enriched)
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            provider_session_id = str(
                metadata.get("omnigentSessionId")
                or metadata.get("providerSessionId")
                or ""
            ).strip()
            if provider_session_id:
                binding = await binding_store.update_with_session(
                    binding.runtimeBindingRef,
                    omnigent_session_id=provider_session_id,
                )
                await turn_commands.bind_runtime_authority(
                    session_id=command_claim.session_id,
                    execution_plan_ref=plan.planRef,
                    runtime_binding_ref=binding.runtimeBindingRef,
                )
            checkpoint_capture = dict(metadata.get("omnigentCheckpointCapture") or {})
            primary_lease = binding.providerLeases.get("primary-model")
            if primary_lease is not None:
                checkpoint_capture.update(
                    {
                        "providerProfileId": primary_lease.providerProfileRef,
                        "credentialRef": (
                            "credential://provider-profile/"
                            f"{primary_lease.providerProfileRef}/generation/"
                            f"{primary_lease.credentialGeneration}"
                        ),
                        "credentialGeneration": primary_lease.credentialGeneration,
                        "providerLeaseRef": primary_lease.providerLeaseRef,
                        "hostBindingRef": binding.hostBindingRef,
                        "hostLeaseRef": binding.hostLeaseRef,
                        "hostLeaseGeneration": binding.hostLeaseGeneration,
                        "endpointRef": plan.payload.endpointRef,
                        "omnigentHostId": binding.omnigentHostId,
                        "omnigentSessionId": provider_session_id or None,
                        "bridgeSessionId": metadata.get("bridgeSessionId"),
                        "externalStateRef": metadata.get("externalStateRef"),
                        "captureManifestRef": metadata.get("captureManifestRef"),
                        "effectiveLaunchRef": binding.hostHarnessAttestationRef,
                        "executionProfileRef": plan.payload.agentProfileSnapshotRef,
                        "launchPolicyRef": plan.payload.launchPolicyRef,
                        "executionPlanRef": plan.planRef,
                        "runtimeBindingRef": binding.runtimeBindingRef,
                        "idempotencyKey": request.idempotency_key,
                    }
                )
            final_result = result.model_copy(
                update={
                    "metadata": {
                        **metadata,
                        "executionPlanRef": plan.planRef,
                        "runtimeBindingRef": binding.runtimeBindingRef,
                        "supportCombinationIdentity": execution_support_identity(
                            plan
                        ),
                        **(
                            {"omnigentCheckpointCapture": checkpoint_capture}
                            if checkpoint_capture
                            else {}
                        ),
                    }
                }
            )
            from moonmind.omnigent.control_plane.records import ControlPlaneOutcome

            await turn_commands.settle(
                idempotency_key=request.idempotency_key,
                outcome=ControlPlaneOutcome.APPLIED,
                provider_receipt_id=provider_session_id or None,
                result_ref=str(metadata.get("externalStateRef") or "") or None,
            )
            return final_result
        except Exception as exc:
            cleanup_deferred = (
                str(getattr(exc, "code", ""))
                == str(HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED)
            )
            from moonmind.omnigent.control_plane.records import ControlPlaneOutcome

            try:
                await turn_commands.settle(
                    idempotency_key=request.idempotency_key,
                    outcome=ControlPlaneOutcome.DELIVERY_UNKNOWN,
                )
            except Exception:
                logger.exception(
                    "Failed to park generic Omnigent command as delivery unknown"
                )
            raise
        finally:
            # Reverse authority order: stop the credential consumer before
            # releasing Provider Profile capacity. Cleanup is distinct durable
            # evidence and cannot replace the primary execution outcome.
            cleanup_complete = (
                not host_realized
                and not existing_host_authority
                and not cleanup_deferred
            )
            if host_realized and hasattr(host_runtime, "cleanup"):
                try:
                    await host_runtime.cleanup(
                        plan.planRef,
                        host_authority_binding_ref,
                        command_authority=command_authority,
                    )
                    cleanup_complete = True
                except Exception:
                    logger.exception("Generic Omnigent host cleanup remains pending")
            if acquired is not None and cleanup_complete:
                try:
                    await runtime_authority.release(
                        acquired,
                        command_authority=command_authority,
                    )
                except Exception:
                    logger.exception(
                        "Generic Omnigent provider authority release remains pending"
                    )
            elif acquired is not None:
                logger.error(
                    "Retaining Generic Omnigent provider authority until host cleanup completes"
                )

    def _bind_host_to_request(
        self,
        request: AgentExecutionRequest,
        host_context: dict[str, Any],
        *,
        runtime_binding_ref: str,
    ) -> AgentExecutionRequest:
        """Build the secret-free authorization block consumed by the driver."""

        parameters = dict(request.parameters or {})
        omnigent = dict(parameters.get("omnigent") or {})
        session = dict(omnigent.get("session") or {})
        session["hostType"] = "external"
        session["hostId"] = str(
            host_context.get("omnigentHostId") or host_context.get("hostId")
        )
        session["workspace"] = str(host_context.get("workspacePath"))
        omnigent["session"] = session
        omnigent["_moonmindProfileAuthorization"] = {
            "hostClassRef": host_context.get("hostClassRef"),
            "launchPolicyRef": host_context.get("launchPolicyRef"),
            "executionRealizerRef": self.ref,
        }
        parameters["omnigent"] = omnigent
        parameters["runtimeBindingRef"] = runtime_binding_ref
        return request.model_copy(update={"parameters": parameters})

    async def reconcile(
        self,
        plan_ref: str,
        runtime_binding_ref: str | None,
        *,
        command_authority: dict[str, Any],
    ) -> None:
        """Delegate cleanup generically; materializer behavior stays registered."""

        if self._host_runtime is not None and hasattr(self._host_runtime, "cleanup"):
            await self._host_runtime.cleanup(
                plan_ref,
                runtime_binding_ref,
                command_authority=command_authority,
            )


__all__ = ["GenericOmnigentHostRealizer", "GenericRealizerDependencies"]
