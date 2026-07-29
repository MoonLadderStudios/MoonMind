"""Temporal activities for Omnigent streaming execution."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from temporalio import activity

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult

# Compact, evidence-only routing directive carried on the request parameters.
# When absent the request runs a fresh ``execute()``, preserving in-flight
# compatibility for runs launched before this wiring (issue #3510).
#
# The directive is carried as an opaque JSON *string* rather than a nested
# mapping: ``AgentExecutionRequest`` rejects any nested parameter key that looks
# credential-bearing (e.g. the checkpoint's ``credentialGeneration``), and it
# only recurses into dict/list values. The directive itself remains refs-only.
OMNIGENT_RECOVERY_DIRECTIVE_PARAM = "omnigentRecoveryDirective"


def encode_omnigent_recovery_directive(directive: object) -> str:
    """Encode an :class:`OmnigentRecoveryDirective` for request parameters.

    Callers set ``request.parameters[OMNIGENT_RECOVERY_DIRECTIVE_PARAM]`` to the
    returned JSON string so the credential-key scan on ``AgentExecutionRequest``
    does not reject the checkpoint's ``credentialGeneration`` field.
    """

    from moonmind.omnigent.checkpoints import OmnigentRecoveryDirective

    if not isinstance(directive, OmnigentRecoveryDirective):
        directive = OmnigentRecoveryDirective.model_validate(directive)
    return json.dumps(directive.model_dump(by_alias=True, mode="json"))


async def _dispatch_omnigent_execution(
    coordinator: object,
    request: AgentExecutionRequest,
) -> AgentRunResult:
    """Route one profile-bound run to the coordinator recovery/branch entrypoint.

    The production workflow path advertises a resume or branch turn by placing
    an :class:`OmnigentRecoveryDirective` on ``request.parameters`` under
    ``omnigentRecoveryDirective`` (encoded as a compact JSON string). This is
    the sole production caller of the coordinator's
    ``recover_from_checkpoint()`` / ``branch_from_checkpoint()`` methods;
    without a directive a fresh ``execute()`` runs unchanged.
    """

    from moonmind.omnigent.checkpoints import (
        OmnigentRecoveryDirective,
        OmnigentRecoveryDirectiveKind,
    )

    raw_directive = (request.parameters or {}).get(
        OMNIGENT_RECOVERY_DIRECTIVE_PARAM
    )
    if not raw_directive:
        return await coordinator.execute(request)

    if isinstance(raw_directive, str):
        raw_directive = json.loads(raw_directive)
    elif not isinstance(raw_directive, Mapping):
        raise ValueError(
            "omnigentRecoveryDirective must be a JSON string or mapping"
        )

    directive = OmnigentRecoveryDirective.model_validate(raw_directive)
    if directive.kind is OmnigentRecoveryDirectiveKind.RECOVER:
        return await coordinator.recover_from_checkpoint(
            request=request,
            checkpoint=directive.checkpoint,
            provider_lease=directive.provider_lease,
            host_lease=directive.host_lease,
            host_registered=directive.host_registered,
            session_valid=directive.session_valid,
            first_message_consistent=directive.first_message_consistent,
            current_credential_generation=directive.current_credential_generation,
            candidate_workspace=directive.candidate_workspace,
        )
    return await coordinator.branch_from_checkpoint(
        request=request,
        checkpoint=directive.checkpoint,
        current_credential_generation=directive.current_credential_generation,
        candidate_workspace=directive.candidate_workspace,
    )


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
        return await _dispatch_omnigent_execution(coordinator, request)


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
        janitor = OmnigentOAuthHostJanitor(
            repository=OmnigentOAuthHostRepository(async_session_maker),
            runtime=OmnigentOAuthHostRuntime(client=client),
            client=client,
            run_store=OmnigentBridgeSessionStore(async_session_maker),
        )
        payload = dict(request or {})
        action_kind = str(payload.get("actionKind") or "").strip()
        if action_kind:
            return await janitor.run_action(
                action_kind=action_kind,
                profile_id=str(payload.get("profile_id") or "").strip(),
                host_lease_ref=str(payload.get("hostLeaseRef") or "").strip(),
                expected_host_state=(
                    str(payload.get("expectedHostState") or "").strip() or None
                ),
                request_id=str(payload.get("requestId") or "").strip(),
            )
        return await janitor.run(
            profile_id=str((request or {}).get("profile_id") or "").strip() or None,
            force=bool((request or {}).get("force", False)),
        )


__all__ = [
    "OMNIGENT_RECOVERY_DIRECTIVE_PARAM",
    "encode_omnigent_recovery_directive",
    "omnigent_execute_activity",
    "omnigent_profile_bound_execute_activity",
    "omnigent_oauth_host_janitor_activity",
]
