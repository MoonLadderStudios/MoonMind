"""Temporal activities for Omnigent streaming execution."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from temporalio import activity

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


_IMMUTABLE_RECOVERY_DIMENSIONS = (
    "instructionDigest",
    "runtimeId",
    "model",
    "effort",
    "providerProfileId",
    "launchPolicyRef",
    "repositoryBranch",
    "publishMode",
)


def _checkpoint_recovery_decision(recovery: dict[str, Any]) -> dict[str, Any]:
    """Classify recovery from bounded, caller-independent authority evidence.

    The decision is intentionally compact so the request/history can retain the
    exact terminal rationale without persisting mutable host details. Immutable
    input changes always win over live/cold availability.
    """

    source = recovery.get("immutableSource")
    requested = recovery.get("immutableRequested")
    if not isinstance(source, dict) or not isinstance(requested, dict):
        return {
            "recoveryAction": "resume_unavailable",
            "reasonCodes": ["immutable_authority_missing"],
        }
    missing = [
        dimension
        for dimension in _IMMUTABLE_RECOVERY_DIMENSIONS
        if dimension not in source or dimension not in requested
    ]
    if missing:
        return {
            "recoveryAction": "resume_unavailable",
            "reasonCodes": [
                f"immutable_{dimension}_missing" for dimension in missing[:20]
            ],
        }
    changed = [
        dimension
        for dimension in _IMMUTABLE_RECOVERY_DIMENSIONS
        if source[dimension] != requested[dimension]
    ]
    if changed:
        return {
            "recoveryAction": "branch_required",
            "reasonCodes": [
                f"immutable_{dimension}_changed" for dimension in changed[:20]
            ],
        }
    if recovery.get("liveReattachAvailable") is True:
        return {
            "recoveryAction": "live_reattach",
            "reasonCodes": ["all_authority_valid"],
        }
    if recovery.get("coldRestoreAvailable") is True:
        return {
            "recoveryAction": "cold_restore",
            "reasonCodes": ["live_authority_unavailable"],
        }
    reasons = recovery.get("unavailableReasonCodes")
    bounded_reasons = (
        [str(reason)[:120] for reason in reasons[:20]]
        if isinstance(reasons, list) and reasons
        else ["checkpoint_authority_unavailable"]
    )
    return {"recoveryAction": "resume_unavailable", "reasonCodes": bounded_reasons}


def _checkpoint_recovery_from_request(request: AgentExecutionRequest):
    """Return validated coordinator inputs for an evidence-gated resume."""

    from moonmind.omnigent.checkpoints import (
        CandidateWorkspaceAuthority,
        OmnigentCheckpointIdentity,
    )

    recovery = (request.parameters or {}).get("checkpointRecovery")
    if not isinstance(recovery, dict):
        return None
    checkpoint_payload = recovery.get("omnigentCheckpoint")
    if checkpoint_payload is None:
        return None
    checkpoint = OmnigentCheckpointIdentity.model_validate(checkpoint_payload)
    candidate_workspace = CandidateWorkspaceAuthority(
        loopId=f"{checkpoint.workflow_id}:{checkpoint.logical_step_id}",
        attemptOrdinal=checkpoint.attempt_ordinal,
        headRef=checkpoint.head_ref,
        headDigest=checkpoint.head_digest,
        checkpointRef=checkpoint.workspace_checkpoint_ref,
        checkpointDigest=checkpoint.workspace_checkpoint_digest,
    )
    return checkpoint, candidate_workspace


def _checkpoint_branch_from_request(request: AgentExecutionRequest):
    """Return branch inputs only for an explicit, immutable-input-changing turn.

    Recovery and branch execution intentionally share checkpoint validation, but
    they do not share lease/session identity.  The explicit action keeps a normal
    resume from accidentally creating a new branch and gives the production
    Activity a typed call site for ``branch_from_checkpoint``.
    """

    recovery = (request.parameters or {}).get("checkpointRecovery")
    if not isinstance(recovery, dict):
        return None
    if "immutableSource" in recovery or "immutableRequested" in recovery:
        decision = _checkpoint_recovery_decision(recovery)
        recovery["recoveryDecision"] = decision
        recovery["recoveryAction"] = decision["recoveryAction"]
    if recovery.get("recoveryAction") != "branch_required":
        return None
    parsed = _checkpoint_recovery_from_request(request)
    if parsed is None:
        raise ValueError("checkpoint branch requires validated checkpoint evidence")
    checkpoint, candidate_workspace = parsed
    if request.idempotency_key == checkpoint.idempotency_key:
        raise ValueError("checkpoint branch requires a new idempotency key")
    return checkpoint, candidate_workspace


async def _resolve_live_recovery_authority(
    *, checkpoint: Any, session_factory: Any, host_repository: Any, run_store: Any
) -> dict[str, Any]:
    """Resolve every mutable authority used by the live-reattach gate.

    Missing, expired, ambiguous, or mismatched state is represented as false
    evidence so ``recovery_mode`` fails closed to cold restore.  Credential
    generation is always loaded from the current Provider Profile and is never
    copied from checkpoint evidence.
    """

    from sqlalchemy import func, select

    from api_service.db.models import (
        ManagedAgentProviderProfile,
        OmnigentBridgeSessionEvent,
        ProviderProfileSlotLease,
    )

    now = datetime.now(UTC)
    async with session_factory() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, checkpoint.provider_profile_id
        )
        current_generation = int(profile.credential_generation) if profile else 0
        provider_row = None
        if checkpoint.provider_lease_ref:
            provider_rows = list(
                (
                    await session.execute(
                        select(ProviderProfileSlotLease).where(
                            ProviderProfileSlotLease.lease_id
                            == checkpoint.provider_lease_ref,
                            ProviderProfileSlotLease.profile_id
                            == checkpoint.provider_profile_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            # A duplicated lease identity is ambiguous authority even if both
            # rows otherwise look current.  Fail closed instead of allowing a
            # live session to keep consuming the OAuth profile.
            provider_row = provider_rows[0] if len(provider_rows) == 1 else None
        latest_sequence = int(
            (
                await session.execute(
                    select(func.max(OmnigentBridgeSessionEvent.sequence)).where(
                        OmnigentBridgeSessionEvent.bridge_session_id
                        == checkpoint.bridge_session_id
                    )
                )
            ).scalar()
            or 0
        )

    provider_lease = None
    if provider_row is not None:
        expires_at = provider_row.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        provider_lease = {
            "leaseId": provider_row.lease_id,
            "active": bool(
                expires_at
                and expires_at > now
                and provider_row.owner_id
                and provider_row.idempotency_key == checkpoint.idempotency_key
            ),
        }

    host = (
        await host_repository.get_host_lease(checkpoint.host_lease_ref)
        if checkpoint.host_lease_ref
        else None
    )
    host_lease = (
        host.model_dump(by_alias=True, mode="json", exclude_none=True)
        if host is not None
        else None
    )
    bridge = await run_store.get_bridge_session(checkpoint.bridge_session_id)
    host_expires_at = host.expires_at if host is not None else None
    if host_expires_at is not None and host_expires_at.tzinfo is None:
        host_expires_at = host_expires_at.replace(tzinfo=UTC)
    host_registered = bool(
        host
        and host.omnigent_host_id == checkpoint.omnigent_host_id
        and host.omnigent_session_id == checkpoint.omnigent_session_id
        and host.bridge_session_id == checkpoint.bridge_session_id
        and host.status in {"ready", "assigned"}
        and host_expires_at
        and host_expires_at > now
    )
    session_valid = bool(
        bridge
        and bridge.omnigent_host_id == checkpoint.omnigent_host_id
        and bridge.omnigent_session_id == checkpoint.omnigent_session_id
        and bridge.status == "active"
        and (
            checkpoint.last_bridge_event_cursor is None
            or (
                checkpoint.last_bridge_event_cursor.isdecimal()
                and latest_sequence >= int(checkpoint.last_bridge_event_cursor)
            )
        )
    )
    first_message_consistent = bool(
        bridge
        and checkpoint.first_message_digest
        and bridge.first_message_digest == checkpoint.first_message_digest
        and checkpoint.first_message_id
        in {bridge.first_message_item_id, bridge.first_message_pending_id}
        and bridge.first_message_state in {"posted", "terminal"}
    )
    return {
        "provider_lease": provider_lease,
        "host_lease": host_lease,
        "host_registered": host_registered,
        "session_valid": session_valid,
        "first_message_consistent": first_message_consistent,
        "current_credential_generation": current_generation,
    }


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
    from moonmind.repositories.lore_runtime import (
        build_lore_repository_adapter_from_environment,
    )
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

            async def get_metadata(self, *, artifact_id: str, **kwargs):
                async with async_session_maker() as artifact_session:
                    service = TemporalArtifactService(
                        TemporalArtifactRepository(artifact_session)
                    )
                    return await service.get_metadata(
                        artifact_id=artifact_id, **kwargs
                    )

            async def read_chunks(self, *, artifact_id: str, **kwargs):
                async with async_session_maker() as artifact_session:
                    service = TemporalArtifactService(
                        TemporalArtifactRepository(artifact_session)
                    )
                    return await service.read_chunks(
                        artifact_id=artifact_id, **kwargs
                    )

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

        lore_repository_adapter = build_lore_repository_adapter_from_environment()
        host_repository = OmnigentOAuthHostRepository(async_session_maker)
        coordinator = OmnigentProfileBoundExecutionCoordinator(
            session_factory=async_session_maker,
            lease_client=ProviderProfileLeaseClient(TemporalClientAdapter()),
            host_repository=host_repository,
            host_runtime=OmnigentOAuthHostRuntime(
                client=omnigent_client,
                lore_repository_adapter=lore_repository_adapter,
            ),
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
        recovery_inputs = _checkpoint_recovery_from_request(request)
        branch_inputs = _checkpoint_branch_from_request(request)
        recovery_payload = (request.parameters or {}).get("checkpointRecovery")
        recovery_decision = (
            recovery_payload.get("recoveryDecision")
            if isinstance(recovery_payload, dict)
            else None
        )
        if (
            isinstance(recovery_decision, dict)
            and recovery_decision.get("recoveryAction") == "resume_unavailable"
        ):
            reasons = recovery_decision.get("reasonCodes") or [
                "checkpoint_authority_unavailable"
            ]
            raise ValueError(
                "checkpoint resume unavailable: " + ",".join(map(str, reasons[:20]))
            )
        if branch_inputs is not None:
            checkpoint, candidate_workspace = branch_inputs
            authority = await _resolve_live_recovery_authority(
                checkpoint=checkpoint,
                session_factory=async_session_maker,
                host_repository=host_repository,
                run_store=run_store,
            )
            return await coordinator.branch_from_checkpoint(
                request=request,
                checkpoint=checkpoint,
                current_credential_generation=authority[
                    "current_credential_generation"
                ],
                candidate_workspace=candidate_workspace,
            )
        if recovery_inputs is not None:
            checkpoint, candidate_workspace = recovery_inputs
            authority = await _resolve_live_recovery_authority(
                checkpoint=checkpoint,
                session_factory=async_session_maker,
                host_repository=host_repository,
                run_store=run_store,
            )
            return await coordinator.recover_from_checkpoint(
                request=request,
                checkpoint=checkpoint,
                candidate_workspace=candidate_workspace,
                **authority,
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
    "_checkpoint_recovery_from_request",
    "_resolve_live_recovery_authority",
    "omnigent_execute_activity",
    "omnigent_profile_bound_execute_activity",
    "omnigent_oauth_host_janitor_activity",
]
