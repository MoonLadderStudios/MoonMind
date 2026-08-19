"""Temporal activities for Omnigent streaming execution."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from temporalio import activity

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


class _OnDemandTemporalArtifactService:
    """Open one DB session per artifact operation at an Activity boundary."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def _invoke(self, method: str, **kwargs: Any) -> Any:
        from moonmind.workflows.temporal.artifacts import (
            TemporalArtifactRepository,
            TemporalArtifactService,
        )

        async with self._session_factory() as artifact_session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(artifact_session)
            )
            return await getattr(service, method)(**kwargs)

    async def create(self, **kwargs: Any) -> Any:
        return await self._invoke("create", **kwargs)

    async def read(self, *, artifact_id: str, **kwargs: Any) -> Any:
        return await self._invoke("read", artifact_id=artifact_id, **kwargs)

    async def get_metadata(self, *, artifact_id: str, **kwargs: Any) -> Any:
        return await self._invoke("get_metadata", artifact_id=artifact_id, **kwargs)

    async def read_chunks(self, *, artifact_id: str, **kwargs: Any) -> Any:
        return await self._invoke("read_chunks", artifact_id=artifact_id, **kwargs)

    async def write_complete(self, *, artifact_id: str, **kwargs: Any) -> Any:
        return await self._invoke(
            "write_complete", artifact_id=artifact_id, **kwargs
        )


def _checkpoint_recovery_dimensions(recovery: dict[str, Any]):
    """Compile the requested immutable recovery intent for the canonical gate."""

    from moonmind.omnigent.control_plane import ImmutableSessionDimensions

    requested = recovery.get("immutableRequested")
    if not isinstance(requested, dict):
        raise ValueError("checkpoint recovery immutableRequested is required")
    mapping = {
        "provider": "provider",
        "instructionDigest": "instruction_digest",
        "runtimeId": "runtime_id",
        "model": "model",
        "effort": "effort",
        "compatibilityProfile": "compatibility_profile",
        "providerProfileId": "provider_profile_id",
        "launchPolicyRef": "policy_ref",
        "imageManifestRef": "image_manifest_ref",
        "compatibilityRef": "compatibility_ref",
        "repository": "repository",
        "repositoryBranch": "branch",
        "workspaceRef": "workspace_ref",
        "publishMode": "publication_mode",
        "skillRef": "skill_ref",
        "runtimeAuthorityRef": "runtime_authority_ref",
        "intentDigest": "intent_digest",
    }
    # These fields existed in the first persisted recovery payload shape and
    # remain mandatory for in-flight histories.  New payloads carry every
    # concrete ImmutableSessionDimensions field; optional legacy omissions are
    # represented as unknown rather than fabricated authority.
    legacy_required = (
        "instructionDigest",
        "runtimeId",
        "model",
        "effort",
        "providerProfileId",
        "launchPolicyRef",
        "repositoryBranch",
        "publishMode",
    )
    missing = [
        source
        for source in legacy_required
        if not str(requested.get(source) or "").strip()
    ]
    if missing:
        raise ValueError(
            "checkpoint recovery immutable authority is incomplete: "
            + ", ".join(missing)
        )
    values = {
        target: str(requested[source]).strip()
        for source, target in mapping.items()
        if str(requested.get(source) or "").strip()
    }
    values.setdefault("provider", "omnigent")
    return ImmutableSessionDimensions(**values)


def _checkpoint_recovery_from_request(request: AgentExecutionRequest):
    """Return validated coordinator inputs for an evidence-gated resume."""

    from moonmind.omnigent.checkpoints import (
        CandidateWorkspaceAuthority,
        OmnigentCheckpointIdentity,
    )

    recovery = request.checkpoint_recovery
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

    recovery = request.checkpoint_recovery
    if not isinstance(recovery, dict):
        return None
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

    from sqlalchemy import select

    from api_service.db.models import (
        ManagedAgentProviderProfile,
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
    canonical = await run_store.get_canonical_session(checkpoint.bridge_session_id)
    host_expires_at = host.expires_at if host is not None else None
    if host_expires_at is not None and host_expires_at.tzinfo is None:
        host_expires_at = host_expires_at.replace(tzinfo=UTC)
    host_registered = bool(
        host
        and host.omnigent_host_id == checkpoint.omnigent_host_id
        and host.omnigent_session_id == checkpoint.omnigent_session_id
        and host.bridge_session_id == checkpoint.bridge_session_id
        and canonical is not None
        and canonical.host_binding_ref == checkpoint.host_binding_ref
        and canonical.host_lease_ref == checkpoint.host_lease_ref
        and canonical.provider_profile_id == checkpoint.provider_profile_id
        and host.status in {"ready", "assigned"}
        and host_expires_at
        and host_expires_at > now
    )
    cursor_present = bool(
        canonical
        and checkpoint.last_bridge_event_cursor
        and canonical.provider_event_cursor
        and str(checkpoint.last_bridge_event_cursor).isdecimal()
        and str(canonical.provider_event_cursor).isdecimal()
        and int(canonical.provider_event_cursor)
        >= int(checkpoint.last_bridge_event_cursor)
    )
    session_valid = bool(
        canonical
        and canonical.provider_session_ref == checkpoint.omnigent_session_id
        and not canonical.is_terminal
        and canonical.cleanup_state not in {"complete", "released"}
        and cursor_present
    )
    from moonmind.omnigent.bridge_store import _canonical_first_message_frontier

    first_message_consistent = bool(
        canonical
        and checkpoint.first_message_digest
        and checkpoint.first_message_id
        and canonical.snapshot_frontier
        == _canonical_first_message_frontier(
            checkpoint.first_message_id, checkpoint.first_message_digest
        )
    )
    return {
        "provider_lease": provider_lease,
        "host_lease": host_lease,
        "host_registered": host_registered,
        "session_valid": session_valid,
        "cursor_present": cursor_present,
        "first_message_consistent": first_message_consistent,
        "current_credential_generation": current_generation,
        "checkpoint_credential_generation": checkpoint.credential_generation,
    }


@activity.defn(name="integration.omnigent.execute")
async def omnigent_execute_activity(
    request: AgentExecutionRequest,
) -> AgentRunResult:
    from moonmind.omnigent.execute import omnigent_activity_heartbeat

    async with omnigent_activity_heartbeat():
        return await _omnigent_execute_activity(request)


async def _omnigent_execute_activity(
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
    from moonmind.repositories.lore_runtime import (
        build_lore_repository_adapter_from_environment,
    )
    from moonmind.provider_profiles.lease_client import ProviderProfileLeaseClient
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
    from moonmind.workflows.temporal.client import TemporalClientAdapter

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
        artifact_service = _OnDemandTemporalArtifactService(async_session_maker)
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
        recovery_payload = request.checkpoint_recovery
        if recovery_inputs is not None:
            checkpoint, candidate_workspace = recovery_inputs
            authority = await _resolve_live_recovery_authority(
                checkpoint=checkpoint,
                session_factory=async_session_maker,
                host_repository=host_repository,
                run_store=run_store,
            )
            if not isinstance(recovery_payload, dict):
                raise ValueError("checkpoint recovery payload is invalid")
            from moonmind.omnigent.control_plane import RecoveryEvidence

            intent_dimensions = _checkpoint_recovery_dimensions(recovery_payload)
            credential_generation_current = bool(
                authority.get("current_credential_generation")
                == checkpoint.credential_generation
                and request.execution_profile_ref == checkpoint.provider_profile_id
            )
            typed_decision = await run_store.decide_canonical_recovery(
                checkpoint.bridge_session_id,
                recovery_idempotency_key=request.idempotency_key,
                intent_dimensions=intent_dimensions,
                live_authority=RecoveryEvidence(
                    intent_dimensions=intent_dimensions,
                    session_dimensions=intent_dimensions,
                    provider_profile_lease_current=bool(
                        checkpoint.validation.valid
                        and checkpoint.validation.live_reattach_available
                        and authority.get("provider_lease")
                        and authority["provider_lease"].get("active") is True
                    ),
                    host_available=authority.get("host_registered") is True,
                    provider_session_reachable=authority.get("session_valid") is True,
                    cursor_present=authority.get("cursor_present") is True,
                    first_message_consistent=(
                        authority.get("first_message_consistent") is True
                    ),
                    credential_generation_current=credential_generation_current,
                    workspace_artifact_valid=bool(
                        credential_generation_current
                        and checkpoint.validation.valid
                        and checkpoint.validation.workspace_cold_restore_available
                        and checkpoint.workspace_checkpoint_ref
                        and checkpoint.head_ref
                    ),
                    session_evidence_valid=bool(
                        checkpoint.validation.valid
                        and checkpoint.external_state_ref
                        and (
                            checkpoint.capture_manifest_ref
                            or checkpoint.terminal_ref
                            or checkpoint.diagnostics_ref
                        )
                    ),
                ),
            )
            decision = {
                "recoveryAction": typed_decision.mode.value,
                "reasonCodes": [typed_decision.reason],
                "changedDimensions": list(typed_decision.changed_dimensions),
            }
            recovery_payload["recoveryDecision"] = decision
            recovery_payload["recoveryAction"] = decision["recoveryAction"]
            if decision["recoveryAction"] == "branch_required":
                if request.idempotency_key == checkpoint.idempotency_key:
                    raise ValueError(
                        "checkpoint branch requires a new idempotency key"
                    )
                return await coordinator.branch_from_checkpoint(
                    request=request,
                    checkpoint=checkpoint,
                    current_credential_generation=authority[
                        "current_credential_generation"
                    ],
                    candidate_workspace=candidate_workspace,
                )
            if decision["recoveryAction"] == "resume_unavailable":
                reasons = decision.get("reasonCodes") or [
                    "checkpoint_authority_unavailable"
                ]
                raise ValueError(
                    "checkpoint resume unavailable: "
                    + ",".join(map(str, reasons[:20]))
                )
            return await coordinator.recover_from_checkpoint(
                request=request,
                checkpoint=checkpoint,
                candidate_workspace=candidate_workspace,
                recovery_mode=decision["recoveryAction"],
                provider_lease=authority.get("provider_lease"),
                host_lease=authority.get("host_lease"),
                host_registered=authority.get("host_registered") is True,
                session_valid=authority.get("session_valid") is True,
                first_message_consistent=(
                    authority.get("first_message_consistent") is True
                ),
                current_credential_generation=int(
                    authority.get("current_credential_generation") or 0
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
    from moonmind.provider_profiles.lease_client import ProviderProfileLeaseClient
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
    from moonmind.workflows.temporal.client import TemporalClientAdapter

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
            lease_client=ProviderProfileLeaseClient(TemporalClientAdapter()),
            artifact_gateway=_OnDemandTemporalArtifactService(async_session_maker),
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
