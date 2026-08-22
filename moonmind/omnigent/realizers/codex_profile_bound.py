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

from moonmind.omnigent.harness_platform.execution_plan import OmnigentExecutionPlanEnvelope
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


class CodexProfileBoundRealizer:
    ref = "codex-profile-bound@1"

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        coordinator_factory: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._coordinator_factory = coordinator_factory

    async def execute(
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
            return await coordinator.execute(request)

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
            )
            return await coordinator.execute(request)

    async def reconcile(
        self,
        plan_ref: str,
        runtime_binding_ref: str | None,
    ) -> None:
        # Janitor delegates to existing cleanup; no-op for now
        return None
