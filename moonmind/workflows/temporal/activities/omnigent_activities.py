"""Temporal activities for Omnigent streaming execution."""

from __future__ import annotations

import os
from pathlib import Path

from temporalio import activity

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


@activity.defn(name="integration.omnigent.execute")
async def omnigent_execute_activity(
    request: AgentExecutionRequest,
) -> AgentRunResult:
    """Run one Omnigent streaming execution."""

    from api_service.db.base import async_session_maker
    import httpx

    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
    from moonmind.omnigent.execute import run_omnigent_execution
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
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactRepository,
        TemporalArtifactService,
    )

    artifact_gateway = LocalOmnigentArtifactGateway()
    run_store = OmnigentBridgeSessionStore(async_session_maker)
    if not request.execution_profile_ref:
        return await run_omnigent_execution(
            request,
            artifact_gateway=artifact_gateway,
            run_store=run_store,
        )

    async with httpx.AsyncClient() as http_client:
        omnigent_client = OmnigentHttpClient(
            base_url=resolved_server_url(),
            api_token=resolved_api_token(),
            client=http_client,
            upstream_header_allowlist=resolved_proxy_forward_headers(),
        )
        class OnDemandTemporalArtifactService:
            async def create(self, **kwargs):
                async with async_session_maker() as artifact_session:
                    service = TemporalArtifactService(
                        TemporalArtifactRepository(artifact_session)
                    )
                    return await service.create(**kwargs)

            async def read(self, *, artifact_id: str, **kwargs):
                async with async_session_maker() as artifact_session:
                    service = TemporalArtifactService(
                        TemporalArtifactRepository(artifact_session)
                    )
                    return await service.read(artifact_id=artifact_id, **kwargs)

            async def write_complete(self, *, artifact_id: str, **kwargs):
                async with async_session_maker() as artifact_session:
                    service = TemporalArtifactService(
                        TemporalArtifactRepository(artifact_session)
                    )
                    return await service.write_complete(
                        artifact_id=artifact_id, **kwargs
                    )

        artifact_service = OnDemandTemporalArtifactService()
        from moonmind.omnigent.remediation_workspace import (
            ArtifactRemediationHeadLoader,
            ManagedServiceRemediationRestorer,
            SandboxRemediationWorkspaceOwner,
        )
        from moonmind.workflows.temporal.runtime.checkpoint_restore import (
            ManagedCheckpointRestoreService,
        )

        workspace_root = Path(
            os.environ.get("WORKFLOW_WORKSPACE_ROOT", "/work/agent_jobs")
        )
        restore_service = ManagedCheckpointRestoreService(
            authority_root=workspace_root / "temporal_sandbox",
            artifact_service=artifact_service,
        )

        coordinator = OmnigentProfileBoundExecutionCoordinator(
            session_factory=async_session_maker,
            lease_client=ProviderProfileLeaseClient(TemporalClientAdapter()),
            host_repository=OmnigentOAuthHostRepository(async_session_maker),
            host_runtime=OmnigentOAuthHostRuntime(client=omnigent_client),
            run_store=run_store,
            execution_runner=run_omnigent_execution,
            artifact_gateway=artifact_gateway,
            artifact_service=artifact_service,
            workspace_owner=SandboxRemediationWorkspaceOwner(
                workspace_root,
                restorer=ManagedServiceRemediationRestorer(restore_service),
                head_loader=ArtifactRemediationHeadLoader(artifact_service),
            ),
        )
        return await coordinator.execute(request)


@activity.defn(name="integration.omnigent.profile_bound_execute")
async def omnigent_profile_bound_execute_activity(
    request: AgentExecutionRequest,
) -> AgentRunResult:
    if not request.execution_profile_ref:
        raise ValueError(
            "profile-bound Omnigent execution requires executionProfileRef"
        )
    return await omnigent_execute_activity(request)


@activity.defn(name="integration.omnigent.oauth_host_janitor")
async def omnigent_oauth_host_janitor_activity(
    request: dict[str, object] | None = None,
) -> dict[str, object]:
    """Reconcile expired, missing, and orphaned OAuth hosts."""

    import httpx

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.oauth_host_janitor import OmnigentOAuthHostJanitor
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
    from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
    from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository
    from moonmind.omnigent.settings import (
        resolved_api_token,
        resolved_proxy_forward_headers,
        resolved_server_url,
    )
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    async with httpx.AsyncClient() as http_client:
        client = OmnigentHttpClient(
            base_url=resolved_server_url(),
            api_token=resolved_api_token(),
            client=http_client,
            upstream_header_allowlist=resolved_proxy_forward_headers(),
        )
        return await OmnigentOAuthHostJanitor(
            repository=OmnigentOAuthHostRepository(async_session_maker),
            runtime=OmnigentOAuthHostRuntime(client=client),
            client=client,
            run_store=OmnigentBridgeSessionStore(async_session_maker),
        ).run(
            profile_id=str((request or {}).get("profile_id") or "").strip() or None,
            force=bool((request or {}).get("force", False)),
        )


__all__ = [
    "omnigent_execute_activity",
    "omnigent_profile_bound_execute_activity",
    "omnigent_oauth_host_janitor_activity",
]
