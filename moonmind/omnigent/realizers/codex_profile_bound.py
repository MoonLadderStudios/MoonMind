"""Codex compatibility realizer: `codex-profile-bound@1`.

Thin adapter that delegates to the existing
`OmnigentProfileBoundExecutionCoordinator`. This preserves:

- Codex Provider Profile format
- Codex OAuth volume, generation fencing
- host binding and lease rows
- startup and readiness scripts
- workspace projection
- mounted Skills and tools
- egress enforcement
- publication, checkpoints, janitor
- Codex support evidence

No behavior change for existing Codex workflows.
"""

from __future__ import annotations

from typing import Any

from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
    execution_support_identity,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


class CodexProfileBoundRealizer:
    ref = "codex-profile-bound@1"

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        coordinator_factory: Any | None = None,
        turn_command_service: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._coordinator_factory = coordinator_factory
        self._turn_commands = turn_command_service

    async def execute(
        self,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
    ) -> AgentRunResult:
        """Fence one canonical turn command around the Codex lifecycle.

        Codex and the generic Omnigent host share exactly one session and turn
        ownership model (#3707 AC10); only the wrapped lifecycle differs. The
        turn source is derived from the request by the shared wrapper, so a
        Codex remediation attempt claims under ``TurnSource.REMEDIATION``.
        """

        from moonmind.omnigent.realizers.turn_delivery import (
            deliver_canonical_turn,
        )

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
        # Validate plan is for codex-profile-bound
        if plan.payload.executionRealizerRef != self.ref:
            from moonmind.omnigent.harness_platform.failures import (
                HarnessPlatformError,
                HarnessPlatformFailure,
            )

            raise HarnessPlatformError(
                f"plan realizer {plan.payload.executionRealizerRef} != {self.ref}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
            )
        # Delegate to existing coordinator (import lazily to avoid cycles)
        from api_service.db.base import async_session_maker
        import httpx

        from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
        from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
        from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
        from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository
        from moonmind.omnigent.profile_bound_execution import (
            OmnigentProfileBoundExecutionCoordinator,
        )
        from moonmind.omnigent.settings import (
            resolved_api_token,
            resolved_proxy_forward_headers,
            resolved_server_url,
        )
        from moonmind.provider_profiles.lease_client import ProviderProfileLeaseClient
        from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
        from moonmind.workflows.temporal.client import TemporalClientAdapter
        from moonmind.omnigent.execute import run_omnigent_execution

        session_factory = self._session_factory or async_session_maker
        artifact_gateway = LocalOmnigentArtifactGateway()
        run_store = OmnigentBridgeSessionStore(session_factory)

        # If factory supplied (tests), use it
        if self._coordinator_factory is not None:
            coordinator = self._coordinator_factory(
                session_factory=session_factory,
                run_store=run_store,
                artifact_gateway=artifact_gateway,
                execution_plan=plan,
            )
            result = await coordinator.execute(request)
            return await self._bind_result_authority(
                request=request,
                plan=plan,
                result=result,
            )

        async with httpx.AsyncClient() as http_client:
            omnigent_client = OmnigentHttpClient(
                base_url=resolved_server_url(),
                api_token=resolved_api_token(),
                client=http_client,
                upstream_header_allowlist=resolved_proxy_forward_headers(),
            )
            from moonmind.repositories.lore_runtime import (
                build_lore_repository_adapter_from_environment,
            )

            lore_adapter = build_lore_repository_adapter_from_environment()
            host_repository = OmnigentOAuthHostRepository(session_factory)
            coordinator = OmnigentProfileBoundExecutionCoordinator(
                session_factory=session_factory,
                lease_client=ProviderProfileLeaseClient(TemporalClientAdapter()),
                host_repository=host_repository,
                host_runtime=OmnigentOAuthHostRuntime(
                    client=omnigent_client,
                    lore_repository_adapter=lore_adapter,
                ),
                run_store=run_store,
                execution_runner=run_omnigent_execution,
                artifact_gateway=artifact_gateway,
                execution_plan=plan,
                # Repository continuations submit through the same canonical
                # turn boundary as the initial turn (#3707 §1).
                turn_command_service=self._turn_commands,
            )
            result = await coordinator.execute(request)
            return await self._bind_result_authority(
                request=request,
                plan=plan,
                result=result,
            )

    async def _bind_result_authority(
        self,
        *,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        result: AgentRunResult,
    ) -> AgentRunResult:
        """Project plan identity without replacing Codex lifecycle authority."""

        request_plan_ref = str(
            (request.parameters or {}).get("executionPlanRef") or ""
        ).strip()
        if request_plan_ref != plan.planRef:
            from moonmind.omnigent.harness_platform.failures import (
                HarnessPlatformError,
                HarnessPlatformFailure,
            )

            raise HarnessPlatformError(
                "Codex request does not name the admitted execution plan",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        metadata = dict(result.metadata or {})
        metadata["executionPlanRef"] = plan.planRef
        metadata["supportCombinationIdentity"] = execution_support_identity(plan)
        capture = dict(metadata.get("omnigentCheckpointCapture") or {})
        if capture:
            capture["executionPlanRef"] = plan.planRef
        if capture:
            metadata["omnigentCheckpointCapture"] = capture
        return result.model_copy(update={"metadata": metadata})

    async def reconcile(
        self,
        plan_ref: str,
        runtime_binding_ref: str | None,
        *,
        command_authority: dict[str, object],
    ) -> None:
        # Janitor delegates to existing cleanup; no-op for now
        return None
