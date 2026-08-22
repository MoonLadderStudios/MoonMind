"""Temporal activities for Omnigent streaming execution."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from temporalio import activity

from moonmind.omnigent.control_plane import metrics as control_plane_metrics
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
        return await self._invoke("write_complete", artifact_id=artifact_id, **kwargs)


def _checkpoint_recovery_decision(
    recovery: dict[str, Any],
    *,
    live_authority: dict[str, Any] | None = None,
    cold_restore_authorized: bool | None = None,
    live_reattach_authorized: bool | None = None,
) -> dict[str, Any]:
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
    # Availability is authority-sensitive and must be supplied by the trusted
    # Activity after it has re-resolved current profile, lease, host, session,
    # cursor, and first-message state.  Payload booleans are deliberately
    # ignored: callers may request recovery, but cannot attest authority.
    live_valid = bool(
        live_reattach_authorized is True
        and live_authority
        and live_authority.get("provider_lease")
        and live_authority["provider_lease"].get("active") is True
        and live_authority.get("host_registered") is True
        and live_authority.get("session_valid") is True
        and live_authority.get("first_message_consistent") is True
        and live_authority.get("current_credential_generation")
        == live_authority.get("checkpoint_credential_generation")
    )
    if live_valid:
        return {
            "recoveryAction": "live_reattach",
            "reasonCodes": ["all_authority_valid"],
        }
    if cold_restore_authorized is True:
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

    recovery = request.checkpoint_recovery
    if not isinstance(recovery, dict):
        return None
    checkpoint_payload = recovery.get("omnigentCheckpoint")
    if checkpoint_payload is None:
        return None
    checkpoint = OmnigentCheckpointIdentity.model_validate(checkpoint_payload)
    if checkpoint.execution_plan_ref is not None:
        binding = request.omnigent_execution_plan
        if (
            binding is None
            or binding.plan_ref != checkpoint.execution_plan_ref
        ):
            raise ValueError(
                "checkpoint execution plan does not match the admitted request"
            )
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
    runtime_binding_current = True
    if checkpoint.execution_plan_ref is not None:
        from moonmind.omnigent.harness_platform.stores import (
            DbRuntimeBindingStore,
        )

        runtime_state = await DbRuntimeBindingStore(
            session_factory
        ).get_state(str(checkpoint.runtime_binding_ref or ""))
        runtime_binding_current = bool(
            runtime_state is not None
            and runtime_state.binding.executionPlanRef
            == checkpoint.execution_plan_ref
            and runtime_state.binding.runtimeBindingRef
            == checkpoint.runtime_binding_ref
            and runtime_state.revision
            == checkpoint.runtime_binding_revision
            and runtime_state.fencing_generation
            == checkpoint.runtime_binding_fencing_generation
            and runtime_state.binding.hostBindingRef
            == checkpoint.host_binding_ref
            and runtime_state.binding.hostLeaseRef
            == checkpoint.host_lease_ref
            and runtime_state.binding.omnigentHostId
            == checkpoint.omnigent_host_id
            and runtime_state.binding.omnigentSessionId
            == checkpoint.omnigent_session_id
            and any(
                authority.providerProfileRef
                == checkpoint.provider_profile_id
                and authority.providerLeaseRef
                == checkpoint.provider_lease_ref
                and authority.credentialGeneration
                == checkpoint.credential_generation
                for authority in runtime_state.binding.providerLeases.values()
            )
        )
    return {
        "provider_lease": provider_lease,
        "host_lease": host_lease,
        "host_registered": host_registered and runtime_binding_current,
        "session_valid": session_valid and runtime_binding_current,
        "first_message_consistent": (
            first_message_consistent and runtime_binding_current
        ),
        "runtime_binding_current": runtime_binding_current,
        "current_credential_generation": current_generation,
        "checkpoint_credential_generation": checkpoint.credential_generation,
    }


async def _try_generic_realizer_dispatch(
    request: AgentExecutionRequest,
    *,
    plan_store: Any | None = None,
    realizer_registry: Any | None = None,
    artifact_gateway: Any | None = None,
    run_store: Any | None = None,
) -> AgentRunResult | None:
    """Load or plan immutable generic authority, then dispatch its realizer."""

    from moonmind.omnigent.harness_platform.failures import (
        HarnessPlatformError,
        HarnessPlatformFailure,
        remediation_for,
    )

    binding = request.omnigent_execution_plan
    if binding is not None:
        try:
            from api_service.db.base import async_session_maker
            if plan_store is None:
                from moonmind.omnigent.harness_platform.stores import (
                    DbExecutionPlanStore,
                )

                plan_store = DbExecutionPlanStore(async_session_maker)
            persisted = await plan_store.load(binding.plan_ref)
            if persisted is None:
                raise ValueError("persisted Omnigent execution plan is unavailable")
            expected_digest = "sha256:" + persisted.planRef.rsplit(":", 1)[-1]
            if expected_digest != binding.plan_digest:
                raise ValueError(
                    "persisted Omnigent execution plan digest mismatch"
                )
            if realizer_registry is None:
                from moonmind.omnigent.realizers.registry import get_default_registry

                realizer_registry = get_default_registry()
            realizer = realizer_registry.require(
                persisted.payload.executionRealizerRef
            )
            return await realizer.execute(request, persisted)
        except Exception:
            return AgentRunResult(
                summary="Admitted Omnigent execution-plan dispatch failed.",
                failureClass="integration_error",
                providerErrorCode=(
                    HarnessPlatformFailure.OMNIGENT_GENERIC_DISPATCH_FAILED.value
                ),
                retryRecommendation=remediation_for(
                    HarnessPlatformFailure.OMNIGENT_GENERIC_DISPATCH_FAILED.value
                ),
            )

    params = request.parameters if isinstance(request.parameters, dict) else {}
    omnigent = (
        params.get("omnigent") if isinstance(params.get("omnigent"), dict) else {}
    )
    plan_ref = str(params.get("executionPlanRef") or "").strip()
    has_profile_ref = isinstance(omnigent.get("agentProfileRef"), dict)
    inline_profile = any(
        isinstance(value, dict)
        for value in (
            omnigent.get("agentProfile"),
            omnigent.get("agentProfileV2"),
            params.get("omnigentAgentProfileV2"),
            params.get("agentProfileV2"),
        )
    )
    # The immutable Agent Profile selection is the dispatch discriminator.
    # Harness names are catalog data and must never steer Temporal dispatch.
    attempted_generic = has_profile_ref or inline_profile or bool(
        params.get("_genericHarnessTest") or omnigent.get("_genericHarnessTest")
    )
    if not plan_ref and not attempted_generic:
        return None

    from moonmind.omnigent.settings import generic_host_enabled

    if not generic_host_enabled() and not plan_ref:
        return AgentRunResult(
            summary="Generic Omnigent host execution is not enabled for this deployment.",
            failureClass="configuration_error",
            providerErrorCode=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY.value,
            retryRecommendation="enable_generic_omnigent_after_setup",
        )

    try:
        from api_service.db.base import async_session_maker
        if plan_ref:
            if plan_store is None:
                from moonmind.omnigent.harness_platform.stores import (
                    DbExecutionPlanStore,
                )

                plan_store = DbExecutionPlanStore(async_session_maker)
            plan = await plan_store.load(plan_ref)
            if plan is None:
                return AgentRunResult(
                    summary="admitted Omnigent execution plan is unavailable",
                    failureClass="integration_error",
                    providerErrorCode="OMNIGENT_EXECUTION_PLAN_UNAVAILABLE",
                    retryRecommendation="retry_transient_upstream",
                )
            from moonmind.omnigent.harness_platform.execution_plan import (
                bind_runtime_request_authority,
            )

            plan = bind_runtime_request_authority(
                plan,
                resolved_skillset_ref=request.resolved_skillset_ref,
                model=params.get("model"),
                effort=params.get("effort") if "effort" in params else None,
            )
            if plan.planRef != plan_ref:
                plan = await plan_store.persist(plan)
                params = {**params, "executionPlanRef": plan.planRef}
                request = request.model_copy(update={"parameters": params})
        else:
            if inline_profile and not has_profile_ref:
                return AgentRunResult(
                    summary="generic Omnigent execution requires an immutable Agent Profile ref",
                    failureClass="configuration_error",
                    providerErrorCode=(
                        HarnessPlatformFailure.OMNIGENT_AGENT_PROFILE_INVALID.value
                    ),
                    retryRecommendation="select_immutable_agent_profile",
                )
            from moonmind.omnigent.production import (
                build_generic_omnigent_execution_services,
            )

            services = build_generic_omnigent_execution_services(
                session_factory=async_session_maker,
                artifact_gateway=artifact_gateway,
                run_store=run_store,
            )
            plan = await services.planning_service.plan(request)
            params = {**params, "executionPlanRef": plan.planRef}
            request = request.model_copy(update={"parameters": params})
            if realizer_registry is None:
                realizer_registry = services.realizer_registry

        if realizer_registry is None:
            from moonmind.omnigent.realizers.registry import get_default_registry

            realizer_registry = get_default_registry()
        realizer = realizer_registry.require(plan.payload.executionRealizerRef)
        return await realizer.execute(request, plan)
    except HarnessPlatformError as exc:
        code = str(exc.code)
        configuration_codes = {
            HarnessPlatformFailure.OMNIGENT_AGENT_PROFILE_INVALID.value,
            HarnessPlatformFailure.OMNIGENT_AGENT_SOURCE_UNAVAILABLE.value,
            HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE.value,
            HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_STALE.value,
            HarnessPlatformFailure.OMNIGENT_HARNESS_UNTRUSTED.value,
            HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE.value,
            HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE.value,
            HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE.value,
            HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY.value,
        }
        return AgentRunResult(
            summary=str(exc),
            failureClass=(
                "configuration_error"
                if code in configuration_codes
                else "integration_error"
            ),
            providerErrorCode=code,
            retryRecommendation=remediation_for(code),
        )
    except Exception:
        # Unknown implementation defects retain one generic boundary code; do
        # not copy exception text into workflow history because it may include
        # provider or infrastructure details. Known boundaries above preserve
        # their actionable typed failure codes.
        return AgentRunResult(
            summary="Generic Omnigent dispatch failed before a terminal provider result.",
            failureClass="integration_error",
            providerErrorCode=HarnessPlatformFailure.OMNIGENT_GENERIC_DISPATCH_FAILED.value,
            retryRecommendation=remediation_for(
                HarnessPlatformFailure.OMNIGENT_GENERIC_DISPATCH_FAILED.value
            ),
        )


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

    import httpx

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.bridge_artifacts import (
        LocalOmnigentArtifactGateway,
        TemporalOmnigentArtifactGateway,
    )
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

    artifact_gateway = LocalOmnigentArtifactGateway()
    run_store = OmnigentBridgeSessionStore(async_session_maker)

    # --- Generic Omnigent host realizer dispatch (Phase 1) ---
    # If the request carries a v2 Agent Profile or explicit harness catalog,
    # compile a secret-free execution plan and dispatch via trusted realizer
    # registry. This path is harness-neutral: no `if harness == "opencode"` branches.
    generic_dispatch = await _try_generic_realizer_dispatch(
        request,
        artifact_gateway=TemporalOmnigentArtifactGateway(async_session_maker),
        run_store=run_store,
    )
    if generic_dispatch is not None:
        return generic_dispatch

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
        branch_inputs = _checkpoint_branch_from_request(request)
        recovery_payload = request.checkpoint_recovery
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
            if not isinstance(recovery_payload, dict):
                raise ValueError("checkpoint recovery payload is invalid")
            decision = _checkpoint_recovery_decision(
                recovery_payload,
                live_authority=authority,
                live_reattach_authorized=bool(
                    checkpoint.validation.valid
                    and checkpoint.validation.live_reattach_available
                ),
                cold_restore_authorized=bool(
                    checkpoint.validation.valid
                    and checkpoint.validation.workspace_cold_restore_available
                    and authority["current_credential_generation"]
                    == checkpoint.credential_generation
                    and request.execution_profile_ref == checkpoint.provider_profile_id
                ),
            )
            recovery_payload["recoveryDecision"] = decision
            recovery_payload["recoveryAction"] = decision["recoveryAction"]
            if decision["recoveryAction"] == "resume_unavailable":
                reasons = decision.get("reasonCodes") or [
                    "checkpoint_authority_unavailable"
                ]
                raise ValueError(
                    "checkpoint resume unavailable: " + ",".join(map(str, reasons[:20]))
                )
            if decision["recoveryAction"] == "cold_restore":
                raise ValueError(
                    "checkpoint cold restore requires an owned workspace restoration "
                    "boundary before Omnigent launch"
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
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
    from moonmind.omnigent.harness_platform.stores import DbRuntimeBindingStore
    from moonmind.omnigent.oauth_host_janitor import OmnigentOAuthHostJanitor
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

    control_plane_metrics.increment(
        control_plane_metrics.JANITOR_OPERATIONS, janitor_outcome="claim"
    )
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
            runtime_binding_store=DbRuntimeBindingStore(async_session_maker),
            control_plane_store=OmnigentControlPlaneStore(async_session_maker),
        )
        payload = dict(request or {})
        action_kind = str(payload.get("actionKind") or "").strip()
        if action_kind:
            try:
                result = await janitor.run_action(
                    action_kind=action_kind,
                    profile_id=str(payload.get("profile_id") or "").strip(),
                    host_lease_ref=str(payload.get("hostLeaseRef") or "").strip(),
                    expected_host_state=(
                        str(payload.get("expectedHostState") or "").strip() or None
                    ),
                    request_id=str(payload.get("requestId") or "").strip(),
                )
            except Exception:
                control_plane_metrics.increment(
                    control_plane_metrics.JANITOR_OPERATIONS,
                    janitor_outcome="failure",
                )
                raise
            control_plane_metrics.increment(
                control_plane_metrics.JANITOR_OPERATIONS, janitor_outcome="success"
            )
            if isinstance(result, dict) and any(
                int(result.get(key) or 0) > 0
                for key in ("conflicts", "claimConflicts", "fencingConflicts")
            ):
                control_plane_metrics.increment(
                    control_plane_metrics.JANITOR_OPERATIONS,
                    janitor_outcome="conflict",
                )
            return result
        try:
            result = await janitor.run(
                profile_id=str((request or {}).get("profile_id") or "").strip() or None,
                force=bool((request or {}).get("force", False)),
            )
        except Exception:
            control_plane_metrics.increment(
                control_plane_metrics.JANITOR_OPERATIONS, janitor_outcome="failure"
            )
            raise
        control_plane_metrics.increment(
            control_plane_metrics.JANITOR_OPERATIONS, janitor_outcome="success"
        )
        if isinstance(result, dict) and any(
            int(result.get(key) or 0) > 0
            for key in ("conflicts", "claimConflicts", "fencingConflicts")
        ):
            control_plane_metrics.increment(
                control_plane_metrics.JANITOR_OPERATIONS,
                janitor_outcome="conflict",
            )
        from moonmind.omnigent.settings import generic_host_enabled

        if generic_host_enabled():
            from moonmind.omnigent.generic_host_janitor import (
                GenericOmnigentHostJanitor,
            )
            from moonmind.omnigent.production import (
                build_generic_omnigent_execution_services,
            )

            services = build_generic_omnigent_execution_services(
                session_factory=async_session_maker
            )
            generic_result = await GenericOmnigentHostJanitor(
                host_leases=services.host_lease_repository,
                runtime_bindings=services.runtime_binding_store,
                realizer=services.generic_realizer,
            ).run()
            result = {**result, "genericHost": generic_result}
        return result


__all__ = [
    "_checkpoint_recovery_from_request",
    "_resolve_live_recovery_authority",
    "omnigent_execute_activity",
    "omnigent_profile_bound_execute_activity",
    "omnigent_oauth_host_janitor_activity",
]
