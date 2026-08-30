"""Lease-authorized coordinator for profile-bound Omnigent execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from moonmind.omnigent.authority_chain import (
    build_omnigent_authority_chain_evidence,
)
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.codex_execution_decisions import (
    bind_candidate_workspace,
    bind_cold_restore_workspace_spec,
    bind_exact_host,
    classify_launch_failure_evidence,
    compile_follow_up_retrieval_policy,
    enforce_required_follow_up_retrieval,
    max_budget_enforcement_rejection,
    persisted_diagnostics_ref,
    prepare_host_failure_stage,
    request_identity,
)
from moonmind.omnigent.checkpoints import (
    CandidateWorkspaceAuthority,
    OmnigentCheckpointIdentity,
    SessionResumeDecision,
    materialize_cold_restore_inputs,
    recovery_mode,
    validate_cold_restore_target,
)
from moonmind.omnigent.execute import OmnigentSessionStillRunningError
from moonmind.omnigent.execution_ports import (
    ExecutionAttemptPort,
    ExecutionPolicyAuthorityPort,
    ExecutionPolicyAuthorityUnavailableError,
    ProfileBoundHostPorts,
    ProviderProfileAuthorityPort,
)
from moonmind.omnigent.control_plane import metrics as control_plane_metrics
from moonmind.omnigent.control_plane.records import compute_digest
from moonmind.omnigent.control_plane import spans as control_plane_spans
from moonmind.omnigent.remediation_workspace import (
    RemediationWorkspaceBinding,
    RemediationWorkspaceOwner,
    SandboxRemediationWorkspaceOwner,
)
from moonmind.omnigent.repository_sources import (
    RepositorySourceError,
    normalize_repository_source,
)
from moonmind.omnigent.execution_profiles import (
    PROFILES,
    selection_from_request,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.stores import DbRuntimeBindingStore
from moonmind.omnigent.mounted_tool_preflight import MountedToolPreflightError
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.oauth_hosts import (
    HEARTBEAT_HOST_STATES,
    HOST_CLEANUP_CLAIMED_ERROR_CODE,
    HOST_PROFILE_BUSY_ERROR_CODE,
    OmnigentOAuthHostRepository,
)
from moonmind.omnigent.stock_agents import (
    CLAUDE_STOCK_AGENT_NAME,
    CODEX_STOCK_AGENT_NAME,
)
from moonmind.omnigent.workspace_intent import (
    WorkspaceIntentCompilationError,
    authored_checkout_commit,
    authored_github_mutation_required,
    authored_repository_mutation_required,
    authored_repository_source,
    authored_required_capabilities,
    authored_restore_input_refs,
    authored_starting_branch,
    authored_target_branch,
    compile_workspace_intent,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
    deterministic_lease_owner_id,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    OmnigentHostLease,
    RepositoryOutcomePolicy,
)
from moonmind.schemas.temporal_activity_models import AcceptedRepositoryEvidence
from moonmind.security.execution_fanout_capabilities import (
    ExecutionFanoutCapabilityError,
    require_execution_fanout_authorization,
)
from moonmind.workflows.executions.runtime_capabilities import (
    RuntimeCapabilityError,
    resolve_runtime_execution_capabilities,
)


ExecutionRunner = Callable[..., Awaitable[AgentRunResult]]
HeartbeatResult = TypeVar("HeartbeatResult")
HOST_LEASE_HEARTBEAT_INTERVAL_SECONDS = 30.0
# The deployment janitor reconciles abandoned OAuth hosts on a five-minute
# cadence.  Cover one complete cadence plus scheduling slack so an exact rerun
# submitted immediately after cancellation can wait for the same profile
# instead of surfacing a transient uniqueness failure to the operator.
HOST_PROFILE_BUSY_WAIT_SECONDS = 360.0
HOST_PROFILE_BUSY_POLL_SECONDS = 5.0
REPOSITORY_PUBLICATION_CONTINUATION_LIMIT = 8

_REPOSITORY_PUBLICATION_CONTINUATION_PROMPT = """\
Continue the current task from this same session. The previous turn ended before
authoritative completion evidence was produced. Do not restart analysis that is
already complete. Finish the implementation, run the relevant tests, and leave
the repository changes ready for MoonMind's publisher. Only finish when the
original task is complete.
"""

#: Instruction digest for the fixed continuation prompt. Every continuation
#: carries the same instruction, so the digest is stable and the turn identity
#: comes from the continuation's distinct idempotency key.
_REPOSITORY_PUBLICATION_CONTINUATION_DIGEST = compute_digest(
    _REPOSITORY_PUBLICATION_CONTINUATION_PROMPT
)

logger = logging.getLogger(__name__)


def _trusted_no_commit_repository_policy(
    request: AgentExecutionRequest,
) -> RepositoryOutcomePolicy | None:
    """Validate the exact workflow-owned authority required for no-commit success."""

    parameters = request.parameters if isinstance(request.parameters, Mapping) else {}
    raw_policy = parameters.get("repositoryOutcomePolicy")
    if not isinstance(raw_policy, Mapping):
        return None
    try:
        policy = RepositoryOutcomePolicy.model_validate(raw_policy)
    except (TypeError, ValueError):
        return None
    if policy.assessed_repository != authored_repository_source(request):
        return None
    if policy.assessed_branch != authored_starting_branch(request):
        return None
    return policy


def _compile_persisted_effective_launch(
    policy_snapshot: Mapping[str, Any],
    *,
    provider_profile_id: str,
    follow_up_retrieval: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile the sole launch carrier from persisted immutable authority."""

    boundaries = policy_snapshot["boundaries"]
    host = boundaries["host"]
    execution = boundaries["execution"]
    endpoint = boundaries["endpoint"]
    resources = boundaries["resources"]
    network = boundaries["network"]
    workspace = boundaries["workspace"]
    session = boundaries["session"]
    retention = boundaries["retention"]
    from moonmind.omnigent.effective_capabilities import CAPABILITY_NAMES

    # Resolve the selected profile once and persist its normalized identity and
    # grants.  The registry is launch-time input only; downstream enforcement
    # never reads mutable profile state.
    selected_profile = PROFILES.get(str(execution["profileRef"]))
    profile_authority = (
        selected_profile.model_dump(by_alias=True, mode="json")
        if selected_profile is not None
        else dict(execution)
    )
    execution_profile_digest = "sha256:" + hashlib.sha256(
        json.dumps(profile_authority, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    profile_capabilities = dict.fromkeys(CAPABILITY_NAMES, True)
    if selected_profile is not None:
        profile_capabilities["changeModel"] = selected_profile.model is None
        profile_capabilities["changeEffort"] = selected_profile.reasoning is None
    session_capabilities = dict.fromkeys(CAPABILITY_NAMES, True)
    launch_capabilities = dict.fromkeys(CAPABILITY_NAMES, False)
    for capability in (
        "viewTranscript", "readResources", "viewTerminal", "viewSubagents",
        "sendMessage", "queueMessage", "resolveElicitation", "harvestEvidence",
    ):
        launch_capabilities[capability] = True
    launch_capabilities["interruptTurn"] = bool(session["interruption"])
    launch_capabilities["stopSession"] = bool(session["cancellation"])
    launch_capabilities["replaceSession"] = bool(session["create"])
    launch_capabilities["reconnectSession"] = bool(session["continuation"])
    launch_capabilities["cleanupSession"] = session["cleanup"] in {"drain", "remove"}
    for capability in ("uploadFiles", "mutateWorkspace"):
        launch_capabilities[capability] = bool(workspace["repositoryMutation"])
    payload = {
        "schemaVersion": 3,
        "executionProfileRef": execution["profileRef"],
        "executionProfileDigest": execution_profile_digest,
        "executionProfileAuthority": profile_authority,
        "launchPolicyRef": policy_snapshot["policyRef"],
        "providerProfileId": provider_profile_id,
        "endpointRef": endpoint["ref"],
        "agentName": execution["agentIdentities"][0],
        "harness": execution["harness"],
        "hostMode": host["mode"],
        "backendRef": host["backendRef"],
        "architectures": list(host["architectures"]),
        "serverImageRef": host["serverImageRef"],
        "hostImageRef": host["hostImageRef"],
        "networkRef": network["attachmentRef"],
        "egressProfileRef": network["egressProfileRef"],
        "enforcedEgress": bool(network["egressProfileRef"]),
        # Distinguishes launches that must have the post-launch bridge authority
        # from pre-upgrade leases that require the janitor's bounded cutover.
        "egressCleanupAuthorityRequired": True,
        "limits": {
            "cpuMillis": resources["cpuMillis"],
            "memoryMiB": resources["memoryMiB"],
            "processes": resources["processes"],
            "timeoutSeconds": resources["timeoutSeconds"],
            "temporaryStorageMiB": resources["temporaryStorageMiB"],
        },
        "mountClasses": list(workspace["mountClasses"]),
        "repositoryMutation": bool(workspace["repositoryMutation"]),
        "runtimeUid": workspace["runtimeUid"],
        "runtimeGid": workspace["runtimeGid"],
        # The Codex host substrate is intentionally stricter than selectable
        # policy: it never permits a writable root filesystem.
        "readOnlyRoot": True,
        "capture": {
            **dict(boundaries["capture"]),
            "retentionDays": retention["days"],
        },
        "cleanup": {"mode": session["cleanup"], "janitor": True},
        "controlCapabilities": [
            "interrupt",
            "terminate",
            "clear_context",
        ],
        "agentProfileCapabilities": profile_capabilities,
        "capabilities": launch_capabilities,
        "sessionStateCapabilities": session_capabilities,
        # Every policy section remains available to downstream enforcement
        # consumers without reconstructing authority from environment or code.
        "boundaries": dict(boundaries),
        "policyAuthority": dict(policy_snapshot),
    }
    # The retrieval gateway reads follow-up (in-session) retrieval authority from
    # this top-level block. It must be inside the digest so a mutated capability
    # policy is rejected by ``validate_effective_launch_snapshot``.
    payload["followUpRetrieval"] = (
        dict(follow_up_retrieval)
        if isinstance(follow_up_retrieval, Mapping)
        else {"enabled": False}
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["snapshotRef"] = "omnigent-launch:sha256:" + hashlib.sha256(
        canonical.encode()
    ).hexdigest()
    return payload


class OmnigentProfileBoundExecutionCoordinator:
    """Own the profile lease through host/session harvesting and cleanup."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
        lease_client: ProviderProfileLeaseClient,
        host_repository: OmnigentOAuthHostRepository,
        host_runtime: ProfileBoundHostPorts,
        run_store: OmnigentBridgeSessionStore,
        execution_runner: ExecutionRunner,
        artifact_gateway: Any,
        artifact_service: Any | None = None,
        workspace_owner: RemediationWorkspaceOwner | None = None,
        execution_plan: OmnigentExecutionPlanEnvelope | None = None,
        provider_profile_authority: ProviderProfileAuthorityPort | None = None,
        policy_authority: ExecutionPolicyAuthorityPort | None = None,
        execution_attempts: ExecutionAttemptPort | None = None,
        turn_command_service: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._lease_client = lease_client
        self._hosts = host_repository
        # Four separate host capabilities, held separately. A deployment may
        # bind them to one adapter; the coordinator never depends on that.
        self._host_preparation = host_runtime
        self._workspace_publication = host_runtime
        self._session_inspection = host_runtime
        self._host_release = host_runtime
        self._run_store = run_store
        self._execute = execution_runner
        self._artifact_gateway = artifact_gateway
        self._artifact_service = artifact_service or artifact_gateway
        self._workspace_owner = workspace_owner or SandboxRemediationWorkspaceOwner(
            os.getenv("WORKFLOW_WORKSPACE_ROOT", "/work/agent_jobs")
        )
        self._execution_plan = execution_plan
        # Ports, not persistence. The composition root supplies the production
        # adapters; omitting one selects the same deployment adapter rather
        # than a different execution path (see #3711 boundary contract).
        if (
            provider_profile_authority is None
            or policy_authority is None
            or execution_attempts is None
        ):
            from moonmind.omnigent.execution_adapters import (
                DbExecutionPolicyAuthority,
                DbProviderProfileAuthority,
                TemporalExecutionAttempt,
            )

            provider_profile_authority = (
                provider_profile_authority
                or DbProviderProfileAuthority(session_factory)
            )
            policy_authority = policy_authority or DbExecutionPolicyAuthority(
                session_factory
            )
            execution_attempts = execution_attempts or TemporalExecutionAttempt()
        self._profile_authority = provider_profile_authority
        self._policy_authority = policy_authority
        self._attempts = execution_attempts
        self._turn_commands = turn_command_service

    @staticmethod
    def _canonical_digest(value: Mapping[str, Any]) -> str:
        body = json.dumps(
            dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(body).hexdigest()

    def _require_recorded_plan_request(
        self, request: AgentExecutionRequest
    ) -> OmnigentExecutionPlanEnvelope | None:
        """Validate immutable Codex authority before coordinator persistence."""

        plan = self._execution_plan
        if plan is None:
            # Existing histories may still use the legacy coordinator directly.
            # New realizer-dispatched plans always provide this argument.
            return None
        binding = request.omnigent_execution_plan
        if (
            binding is None
            or binding.plan_ref != plan.planRef
            or binding.plan_digest
            != "sha256:" + plan.planRef.rsplit(":", 1)[-1]
        ):
            raise HarnessPlatformError(
                "Codex request does not carry the admitted execution plan",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        credential = plan.payload.credentialBindings.get("primary-model")
        if (
            credential is None
            or credential.providerProfileRef != request.execution_profile_ref
        ):
            raise HarnessPlatformError(
                "Codex Provider Profile conflicts with the admitted plan",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        if plan.payload.executionRealizerRef != "codex-profile-bound@1":
            raise HarnessPlatformError(
                "Codex coordinator received a plan for another realizer",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
            )
        requested_target, requested_policy = selection_from_request(
            request.parameters
        )
        if requested_policy and requested_policy != plan.payload.launchPolicyRef:
            raise HarnessPlatformError(
                "Codex launch policy conflicts with the admitted plan",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        if requested_target:
            profile = PROFILES.get(requested_target)
            if (
                profile is None
                or profile.harness != plan.payload.harnessId
                or profile.default_policy_ref != plan.payload.launchPolicyRef
            ):
                raise HarnessPlatformError(
                    "Codex execution target conflicts with the admitted plan",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                )
        return plan

    def _require_recorded_launch(
        self,
        *,
        policy_snapshot: Mapping[str, Any],
        effective_launch: Mapping[str, Any],
    ) -> None:
        plan = self._execution_plan
        if plan is None:
            return
        payload = plan.payload
        if (
            payload.policySnapshotDigest is None
            or payload.effectiveLaunchSnapshotDigest is None
            or self._canonical_digest(policy_snapshot)
            != payload.policySnapshotDigest
            or self._canonical_digest(effective_launch)
            != payload.effectiveLaunchSnapshotDigest
            or str(effective_launch.get("launchPolicyRef") or "")
            != payload.launchPolicyRef
            or str(effective_launch.get("harness") or "") != payload.harnessId
            or str(effective_launch.get("hostImageRef") or "")
            != str(payload.hostImageRef or "")
        ):
            raise HarnessPlatformError(
                "Codex launch authority has drifted from the admitted plan",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )

    async def _write_plan_runtime_evidence(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: Mapping[str, Any],
    ) -> str:
        return await self._artifact_gateway.write_json(
            request=request,
            name=name,
            payload=dict(payload),
            link_type="omnigent_runtime_authority",
        )

    async def _execute_with_host_lease_heartbeat(
        self,
        execution: Awaitable[HeartbeatResult],
        *,
        host_lease_ref: str,
        ttl_seconds: int,
    ) -> HeartbeatResult:
        """Keep the durable host lease live for one owned runtime operation."""

        async def heartbeat() -> None:
            while True:
                await self._hosts.heartbeat_host_lease(
                    host_lease_ref,
                    ttl_seconds=ttl_seconds,
                )
                await asyncio.sleep(HOST_LEASE_HEARTBEAT_INTERVAL_SECONDS)

        execution_task = asyncio.create_task(execution)
        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            done, _pending = await asyncio.wait(
                {execution_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if execution_task in done:
                return await execution_task
            # A lease heartbeat must run until provider execution completes.
            # Propagate its failure so the execution is canceled instead of
            # being silently interrupted later by the stale-lease janitor.
            try:
                heartbeat_task.result()
            except Exception:
                control_plane_metrics.increment(
                    control_plane_metrics.LEASE_RENEWAL_CONFLICTS,
                    lease_scope="host",
                )
                raise
            raise RuntimeError("host lease heartbeat stopped unexpectedly")
        finally:
            for task in (execution_task, heartbeat_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                execution_task,
                heartbeat_task,
                return_exceptions=True,
            )

    async def _claim_host_cleanup(
        self, host_lease_ref: str
    ) -> OmnigentHostLease | None:
        """Acquire cleanup authority after the final operation heartbeat."""

        for _attempt in range(3):
            current = await self._hosts.get_host_lease(host_lease_ref)
            if current is None:
                raise OmnigentOAuthHostError("host lease does not exist")
            if current.status not in HEARTBEAT_HOST_STATES:
                return None
            claimed = await self._hosts.claim_host_lease_cleanup(
                current.lease_id,
                expected_status=current.status,
                expected_last_heartbeat_at=current.last_heartbeat_at,
                ttl_seconds=90,
            )
            if claimed is not None:
                return claimed
            await asyncio.sleep(0)
        return None

    async def _create_host_lease_after_profile_idle(
        self,
        *,
        binding: Any,
        provider_lease: CredentialLease,
        workflow_id: str,
        step_execution_id: str | None,
        idempotency_key: str,
        emit: Callable[..., Awaitable[None]],
    ) -> OmnigentHostLease:
        """Wait for canceled-run host reconciliation before claiming the profile."""

        deadline = asyncio.get_running_loop().time() + HOST_PROFILE_BUSY_WAIT_SECONDS
        wait_attempt = 0
        while True:
            try:
                return await self._hosts.create_or_get_host_lease(
                    binding=binding,
                    provider_lease_id=provider_lease.lease_id,
                    holder_workflow_id=workflow_id,
                    agent_run_id=step_execution_id,
                    idempotency_key=idempotency_key,
                )
            except OmnigentOAuthHostError as exc:
                if exc.code != HOST_PROFILE_BUSY_ERROR_CODE:
                    raise
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise
                wait_attempt += 1
                retry_after = min(HOST_PROFILE_BUSY_POLL_SECONDS, remaining)
                await emit(
                    "host_lease_created",
                    "waiting",
                    code=HOST_PROFILE_BUSY_ERROR_CODE,
                    remediation_action="wait_for_host_cleanup",
                    metadata={
                        "providerProfileId": binding.provider_profile_id,
                        "retryAfterSeconds": retry_after,
                        "waitAttempt": wait_attempt,
                    },
                    ignore_errors=True,
                )
                await asyncio.sleep(retry_after)

    async def _claim_continuation_turn(
        self,
        *,
        request: AgentExecutionRequest,
        source_request: AgentExecutionRequest,
        workflow_id: str,
        step_execution_id: str | None,
        recorded_plan: OmnigentExecutionPlanEnvelope | None,
        provider_profile_id: str,
        credential_generation: int | None,
        runtime_binding_ref: str | None,
    ) -> Any:
        """Admit one repository continuation through the canonical boundary.

        Repository-output continuations used to allocate their own bridge row and
        never produced a canonical turn attempt, which made them an independent
        submission path (#3707 §1). They now claim the same fenced turn command
        every other instruction source uses: one canonical session, one chat
        binding, one immutable execution plan, and a distinct turn-attempt
        identity per continuation.
        """

        if self._turn_commands is None:
            return None
        from moonmind.omnigent.control_plane.turn_admission import (
            CanonicalTurnAdmissionRejected,
        )
        from moonmind.omnigent.control_plane.turn_commands import (
            CanonicalSessionBootstrap,
        )
        from moonmind.omnigent.control_plane.turn_sources import TurnSource
        from moonmind.omnigent.turn_authority import canonical_turn_authority

        plan_ref = recorded_plan.planRef if recorded_plan is not None else None
        requested_authority = (
            canonical_turn_authority(
                request,
                recorded_plan,
                runtime_binding_ref=runtime_binding_ref,
                provider_profile_id=provider_profile_id or None,
                provider_profile_generation=credential_generation,
            )
            if recorded_plan is not None
            else None
        )
        try:
            claim = await self._turn_commands.claim(
                workflow_id=workflow_id,
                provider_session_ref="",
                chat_binding_id=None,
                command_type="repository_output_continuation",
                turn_source=TurnSource.REPOSITORY_CONTINUATION,
                idempotency_key=request.idempotency_key,
                payload_digest=_REPOSITORY_PUBLICATION_CONTINUATION_DIGEST,
                step_execution_id=step_execution_id,
                bootstrap=CanonicalSessionBootstrap(
                    provider="omnigent",
                    step_execution_id=step_execution_id or source_request.correlation_id,
                    agent_run_id=source_request.correlation_id,
                    source_idempotency_key=source_request.idempotency_key,
                    execution_plan_ref=plan_ref,
                ),
                requested_authority=requested_authority,
            )
        except CanonicalTurnAdmissionRejected as exc:
            raise HarnessPlatformError(
                "repository continuation admission returned "
                f"{exc.decision.value}; the canonical session was not mutated",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            ) from exc
        if not claim.owns_delivery:
            # ``ALREADY_APPLIED``, ``FENCING_CONFLICT``, and ``NOT_OWNER`` all
            # mean this attempt does not own the provider-facing side effect.
            # Returning the claim anyway submitted the continuation regardless
            # and could duplicate a billed provider turn on an activity replay
            # of an already-settled command.
            raise HarnessPlatformError(
                "repository continuation command is already settled or owned; "
                "reconciliation is required",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        return claim

    async def _settle_continuation_turn(
        self,
        *,
        claim: Any,
        workflow_id: str,
        idempotency_key: str,
        result: AgentRunResult,
    ) -> None:
        """Settle the continuation's canonical command with its real outcome."""

        if claim is None or self._turn_commands is None:
            return
        from moonmind.omnigent.control_plane.records import ControlPlaneOutcome

        outcome = (
            ControlPlaneOutcome.DELIVERY_UNKNOWN
            if result.failure_class is not None
            else ControlPlaneOutcome.APPLIED
        )
        try:
            await self._turn_commands.settle(
                workflow_id=workflow_id,
                idempotency_key=idempotency_key,
                outcome=outcome,
                provider_receipt_id=str(
                    (result.metadata or {}).get("omnigentSessionId") or ""
                )
                or None,
                result_ref=str(
                    (result.metadata or {}).get("externalStateRef") or ""
                )
                or None,
            )
        except Exception:
            logger.exception(
                "Omnigent repository continuation command settlement remains pending"
            )

    async def execute(self, request: AgentExecutionRequest) -> AgentRunResult:
        recorded_plan = self._require_recorded_plan_request(request)
        budget_rejection = max_budget_enforcement_rejection(request)
        if budget_rejection is not None:
            return budget_rejection
        profile_id = str(request.execution_profile_ref or "").strip()
        workflow_id, step_execution_id = request_identity(request)
        await self._run_store.get_or_create(
            request=request,
            endpoint_ref="pending",
            agent_id=None,
            agent_name=None,
            target_metadata={
                "providerProfileId": profile_id or None,
                "workflowId": workflow_id,
                "stepExecutionId": step_execution_id,
                "attemptIdentity": request.idempotency_key,
            },
        )
        bridge_ready = True
        current_stage = "request_validated"
        active_stages: set[str] = set()
        attempt_identity = f"{request.idempotency_key}:attempt:{self._attempts.current_attempt()}"
        deferred_bridge_terminals: list[dict[str, Any]] = []

        async def emit(
            stage: str,
            status: str,
            *,
            code: str | None = None,
            summary: str | None = None,
            failure_class: str | None = None,
            remediation_action: str | None = None,
            diagnostics_ref: str | None = None,
            metadata: dict[str, Any] | None = None,
            ignore_errors: bool = False,
        ) -> None:
            try:
                await self._run_store.record_lifecycle_event(
                    request.idempotency_key,
                    event_type=stage,
                    status=status,
                    event_identity=f"{attempt_identity}:{stage}:{status}",
                    code=code,
                    summary=summary,
                    failure_class=failure_class,
                    diagnostics_ref=diagnostics_ref,
                    remediation_action=remediation_action,
                    metadata={
                        "workflowId": workflow_id,
                        "stepExecutionId": step_execution_id,
                        **dict(metadata or {}),
                    },
                )
            except Exception:
                if not ignore_errors:
                    raise
            if status in {"started", "waiting"}:
                active_stages.add(stage)
            elif status in {"completed", "ready", "failed"}:
                active_stages.discard(stage)

        def collect_deferred_bridge_terminal(
            provider_result: AgentRunResult,
        ) -> AgentRunResult:
            metadata = dict(provider_result.metadata or {})
            deferred = metadata.pop("deferredBridgeTerminal", None)
            if isinstance(deferred, Mapping):
                idempotency_key = str(
                    deferred.get("idempotencyKey") or ""
                ).strip()
                status = str(deferred.get("status") or "").strip()
                terminal_refs = deferred.get("terminalRefs")
                if idempotency_key and status and isinstance(terminal_refs, Mapping):
                    deferred_bridge_terminals.append(
                        {
                            "idempotencyKey": idempotency_key,
                            "status": status,
                            "terminalRefs": dict(terminal_refs),
                        }
                    )
            return provider_result.model_copy(update={"metadata": metadata})

        provider_lease: CredentialLease | None = None
        runtime_binding_ref: str | None = None
        host_lease = None
        binding = None
        effective_launch: dict[str, Any] | None = None
        remediation_resolution: Mapping[str, Any] | None = None
        workspace_locator_payload: Mapping[str, Any] | None = None
        terminal_status = "completed"
        attempt_cleanup_deferred_code: str | None = None
        # Bounded, credential-free evidence accumulated across the run so the
        # unified authority chain (MoonLadderStudios/MoonMind#3561) can be emitted
        # once at terminal, covering both success and every failure path.
        authority_workspace_resolution: Mapping[str, Any] | None = None
        authority_result: AgentRunResult | None = None
        authority_bridge_session_id: str | None = None
        authority_idempotency_key = request.idempotency_key
        authority_cleanup_mode: str | None = None
        authority_cleanup_evidence: dict[str, Any] = {}
        preflight: Mapping[str, Any] = {}
        authority_reasons: list[dict[str, Any]] = []
        try:
            await emit("request_validated", "started")
            fanout_authorization = self._execution_fanout_authorization(request)
            try:
                require_execution_fanout_authorization(
                    self._required_capabilities(request),
                    fanout_authorization,
                )
            except ExecutionFanoutCapabilityError as exc:
                raise OmnigentOAuthHostError(
                    str(exc), code="authorization_denied"
                ) from exc
            if not profile_id:
                raise OmnigentOAuthHostError(
                    "OAuth-backed Omnigent execution requires executionProfileRef",
                    code="profile_resolution_failed",
                )
            await emit("request_validated", "completed")
            current_stage = "profile_resolution"
            await emit(current_stage, "started")
            profile = await self._profile_authority.resolve(profile_id)
            provider_runtime = profile.runtime_id
            await emit(
                current_stage,
                "completed",
                metadata={"providerProfileId": profile_id},
            )
            current_stage = "profile_readiness"
            await emit(current_stage, "started")
            if not profile.launch_ready:
                raise OmnigentOAuthHostError(
                    "Provider Profile is not launch ready",
                    code="profile_readiness_failed",
                )
            await emit(current_stage, "ready")
            # Resolve product-owned launch authority before acquiring a Provider
            # Profile lease or mutating host state. A persisted binding chooses
            # an immutable ref; environment input selects only the one-time
            # bootstrap default when no durable or authored selection exists.
            requested_target, requested_policy = selection_from_request(
                request.parameters
            )
            current_stage = "host_binding_resolution"
            await emit(current_stage, "started")
            binding = await self._hosts.get_binding_for_profile(profile_id)
            if binding is not None:
                provider_slug = (
                    "claude" if provider_runtime == "claude_code" else "codex"
                )
                selected_profile_ref = str(
                    binding.execution_profile_ref or f"omnigent-{provider_slug}@1"
                )
                selected_policy_ref = str(
                    binding.launch_policy_ref
                    or (
                        f"{provider_slug}-on-demand@1"
                        if binding.host_launch_profile_ref
                        else f"{provider_slug}-static@1"
                    )
                )
                if requested_target and (
                    selected_profile_ref != requested_target
                    or (requested_policy and selected_policy_ref != requested_policy)
                ):
                    raise OmnigentOAuthHostError(
                        "explicit launch selection conflicts with the durable host binding",
                        code="OMNIGENT_LAUNCH_POLICY_BINDING_CONFLICT",
                    )
            elif requested_target:
                selected_profile_ref = requested_target
                selected_policy_ref = requested_policy or PROFILES[
                    selected_profile_ref
                ].default_policy_ref
            else:
                provider_slug = (
                    "claude" if provider_runtime == "claude_code" else "codex"
                )
                selected_profile_ref = f"omnigent-{provider_slug}@1"
                selected_policy_ref = PROFILES[
                    selected_profile_ref
                ].default_policy_ref
            current_stage = "policy_authority_resolution"
            await emit(current_stage, "started")
            try:
                policy_snapshot = await self._policy_authority.resolve_runtime_snapshot(
                    selected_policy_ref
                )
            except ExecutionPolicyAuthorityUnavailableError as exc:
                raise OmnigentOAuthHostError(
                    str(exc), code="OMNIGENT_POLICY_AUTHORITY_UNAVAILABLE"
                ) from exc
            if (
                policy_snapshot["boundaries"]["execution"]["profileRef"]
                != selected_profile_ref
            ):
                raise OmnigentOAuthHostError(
                    "persisted policy execution profile conflicts with launch selection",
                    code="OMNIGENT_LAUNCH_POLICY_BINDING_CONFLICT",
                )
            launch_parameters = (
                request.parameters if isinstance(request.parameters, dict) else {}
            )
            launch_workspace_spec = (
                request.workspace_spec
                if isinstance(request.workspace_spec, dict)
                else {}
            )
            authored_follow_up = (
                launch_parameters.get("followUpRetrieval")
                if isinstance(launch_parameters.get("followUpRetrieval"), dict)
                else {}
            )
            follow_up_repository = str(
                authored_follow_up.get("repository")
                or launch_parameters.get("repository")
                or launch_workspace_spec.get("repository")
                or ""
            ).strip()
            # MoonMind has no separate tenancy authority today (single-tenant by
            # default), so fall back to a deployment-configurable tenant rather
            # than leaving follow-up retrieval permanently unavailable once an
            # operator opts in. Multi-tenant deployments author an explicit
            # tenantId, which always takes precedence.
            follow_up_tenant = str(
                authored_follow_up.get("tenantId")
                or launch_parameters.get("tenant")
                or launch_parameters.get("tenantId")
                or os.getenv("MOONMIND_FOLLOWUP_RETRIEVAL_DEFAULT_TENANT", "default")
            ).strip()
            follow_up_block = compile_follow_up_retrieval_policy(
                policy_snapshot,
                launch_parameters,
                repository=follow_up_repository,
                tenant_id=follow_up_tenant,
            )
            enforce_required_follow_up_retrieval(authored_follow_up, follow_up_block)
            with control_plane_spans.omnigent_span(
                control_plane_spans.COMPATIBILITY_VERIFY,
                runtime=provider_runtime,
                compatibility_digest=policy_snapshot.get("policyDigest"),
            ):
                effective_launch = _compile_persisted_effective_launch(
                    policy_snapshot,
                    provider_profile_id=profile_id,
                    follow_up_retrieval=follow_up_block,
                )
            # New Codex plans are admitted from immutable artifact digests. The
            # legacy coordinator may resolve mutable catalog rows only to prove
            # that they still equal that authority, and must do so before lease
            # acquisition or any host/provider mutation.
            self._require_recorded_launch(
                policy_snapshot=policy_snapshot,
                effective_launch=effective_launch,
            )
            if (
                self._repository_mutation_required(request)
                and not effective_launch["repositoryMutation"]
            ):
                raise OmnigentOAuthHostError(
                    "persisted policy denies required repository mutation",
                    code="OMNIGENT_REPOSITORY_MUTATION_DENIED",
                )
            await emit(
                current_stage,
                "completed",
                metadata={
                    "policyRef": policy_snapshot["policyRef"],
                    "policyDigest": policy_snapshot["policyDigest"],
                    "policySnapshotRef": policy_snapshot["snapshotRef"],
                    "validation": policy_snapshot["validation"],
                },
            )
            try:
                host_capabilities = resolve_runtime_execution_capabilities(
                    "omnigent"
                ).host_realization
                assert host_capabilities is not None
                host_capabilities.require_mode(
                    str(effective_launch["hostMode"]),
                    repository_mutation=self._repository_mutation_required(request),
                    github_credentials=self._github_mutation_required(request),
                )
            except RuntimeCapabilityError as exc:
                raise OmnigentOAuthHostError(
                    str(exc), code="OMNIGENT_HOST_MODE_CAPABILITY_UNSUPPORTED"
                ) from exc
            remediation = self._remediation_workspace(request)
            if remediation is not None:
                if (
                    request.agent_kind != "external"
                    or request.agent_id != "omnigent"
                ):
                    raise OmnigentOAuthHostError(
                        "remediation workspace requires external/omnigent execution",
                        code="REMEDIATION_WORKSPACE_RUNTIME_MISMATCH",
                    )
                if remediation.execution_profile_ref != profile_id:
                    raise OmnigentOAuthHostError(
                        "remediation workspace execution profile does not match workflow authorization",
                        code="REMEDIATION_WORKSPACE_PROFILE_MISMATCH",
                    )
                if (
                    remediation.host_profile_ref
                    != str(effective_launch.get("executionProfileRef") or "")
                    or remediation.launch_policy_ref
                    != str(effective_launch.get("launchPolicyRef") or "")
                ):
                    raise OmnigentOAuthHostError(
                        "remediation workspace launch snapshot does not match effective launch",
                        code="REMEDIATION_WORKSPACE_LAUNCH_MISMATCH",
                    )
                current_stage = "remediation_workspace_admission"
                await emit(current_stage, "started")
                remediation_resolution = await self._workspace_owner.admit_and_resolve(
                    binding=remediation,
                    workflow_id=workflow_id,
                    step_execution_id=(step_execution_id or request.idempotency_key),
                )
                await emit(
                    current_stage,
                    "completed",
                    metadata={
                        "loopId": remediation.loop_id,
                        "branchRef": remediation.branch_ref,
                        "attemptOrdinal": remediation.attempt_ordinal,
                        "restoreEvidenceRef": remediation_resolution.get(
                            "restoreEvidenceRef"
                        ),
                        "workspaceState": remediation_resolution.get("workspaceState"),
                    },
                )
            await emit(
                "effective_launch_compiled",
                "completed",
                metadata={"effectiveLaunch": effective_launch},
            )
            # Compile every normal authoring surface's authored request into one
            # durable, versioned workspace-intent record before any host binding,
            # lease, or workspace mutation. Unsafe or inconsistent authored input
            # (runtime-specific bind paths, Docker authority, arbitrary host ids,
            # credential-shaped values) fails closed here. Remediation runs carry
            # a pre-materialized, separately-authorized workspace and are compiled
            # by their owner, so they are not recompiled here.
            workspace_intent = None
            if remediation_resolution is None:
                current_stage = "workspace_intent_compilation"
                await emit(current_stage, "started")
                try:
                    with control_plane_spans.omnigent_span(
                        control_plane_spans.INTENT_COMPILE,
                        runtime=provider_runtime,
                        attempt_ordinal=self._attempts.current_attempt(),
                    ):
                        workspace_intent = compile_workspace_intent(
                            request,
                            workflow_id=workflow_id,
                            step_execution_id=(
                                step_execution_id or request.idempotency_key
                            ),
                            run_id=(
                                request.step_execution.run_id
                                if request.step_execution is not None
                                else None
                            ),
                            logical_step_id=(
                                request.step_execution.logical_step_id
                                if request.step_execution is not None
                                else None
                            ),
                        )
                except WorkspaceIntentCompilationError as exc:
                    raise OmnigentOAuthHostError(str(exc), code=exc.code) from exc
                # Durable, bounded, credential-free, path-safe compilation
                # evidence for Workflow Detail. A retry deterministically
                # reproduces the same intent digest.
                await self._run_store.record_lifecycle_event(
                    request.idempotency_key,
                    event_type="workspace_intent_compiled",
                    # Scope the durable event identity to the compiled intent
                    # digest. A deterministic retry reproduces the same digest and
                    # deduplicates; a conflicting resubmission under the same
                    # idempotency key (changed repository, branch, locator, or
                    # authority) produces a distinct digest and is recorded as a
                    # new event instead of silently retaining the stale evidence.
                    event_identity=(
                        f"workspace_intent_compiled:{workspace_intent.intent_digest}"
                    ),
                    metadata=workspace_intent.evidence(),
                )
                await emit(
                    current_stage,
                    "completed",
                    metadata={
                        "workspaceIntentDigest": workspace_intent.intent_digest,
                        "workspaceIntentSchemaVersion": (
                            workspace_intent.schema_version
                        ),
                        "locatorKind": workspace_intent.workspace_locator.kind,
                        "repositoryMutation": workspace_intent.repository_mutation,
                        "publishMode": workspace_intent.publish_mode,
                    },
                )
            owner_id = deterministic_lease_owner_id(
                profile_id=profile_id,
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                workflow_id=workflow_id,
                step_execution_id=step_execution_id,
                idempotency_key=request.idempotency_key,
            )
            current_stage = "profile_lease_wait"
            await emit(
                current_stage, "waiting", metadata={"providerProfileId": profile_id}
            )
            lease_started = time.monotonic()
            with control_plane_spans.omnigent_span(
                control_plane_spans.PROFILE_LEASE_ENSURE,
                runtime=provider_runtime,
                attempt_ordinal=self._attempts.current_attempt(),
            ):
                provider_lease = await self._lease_client.acquire_execution_lease(
                    runtime_id=provider_runtime,
                    profile_id=profile_id,
                    owner_id=owner_id,
                    purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                    metadata={
                        "workflowId": workflow_id,
                        "stepExecutionId": step_execution_id,
                        "idempotencyKey": request.idempotency_key,
                        "ownerIsWorkflow": False,
                    },
                )
            control_plane_metrics.observe(
                control_plane_metrics.LEASE_ACQUIRE_LATENCY,
                time.monotonic() - lease_started,
                lease_scope="provider_profile",
            )
            await emit(current_stage, "completed")
            current_stage = "profile_lease_acquired"
            await emit(current_stage, "started")
            await emit(
                current_stage,
                "completed",
                metadata={
                    "providerProfileId": profile_id,
                    "providerLeaseId": provider_lease.lease_id,
                },
            )
            if recorded_plan is not None:
                runtime_binding_store = DbRuntimeBindingStore(
                    self._session_factory
                )
                runtime_binding = await runtime_binding_store.create_initial(
                    execution_plan_ref=recorded_plan.planRef,
                    execution_scope_ref=workflow_id,
                    provider_leases={
                        "primary-model": {
                            "providerProfileRef": profile_id,
                            "providerLeaseRef": provider_lease.lease_id,
                            "credentialGeneration": profile.credential_generation,
                            "credentialRuntimeRef": (
                                "credential://provider-profile/"
                                f"{profile_id}/generation/"
                                f"{profile.credential_generation}"
                            ),
                        }
                    },
                )
                runtime_binding_ref = runtime_binding.runtimeBindingRef
            current_stage = "host_binding_resolution"
            selected_on_demand = effective_launch["hostMode"] == "on_demand_docker"
            binding = await self._hosts.create_or_update_static_binding(
                profile_id=profile_id,
                endpoint_ref=str(effective_launch["endpointRef"]),
                static_host_id=binding.static_host_id if binding is not None else None,
                host_launch_profile_ref=(
                    binding.host_launch_profile_ref
                    if binding is not None
                    else (
                        (
                            str(effective_launch["launchPolicyRef"])
                            if requested_target
                            else os.getenv(
                                "OMNIGENT_CLAUDE_HOST_LAUNCH_PROFILE"
                                if provider_runtime == "claude_code"
                                else "OMNIGENT_CODEX_HOST_LAUNCH_PROFILE"
                            )
                        )
                        if selected_on_demand
                        else None
                    )
                ),
                execution_profile_ref=str(effective_launch["executionProfileRef"]),
                launch_policy_ref=str(effective_launch["launchPolicyRef"]),
                effective_launch_snapshot=effective_launch,
            )
            await emit(
                current_stage,
                "completed",
                metadata={
                    "hostBindingRef": binding.binding_ref,
                    "effectiveLaunchRef": effective_launch["snapshotRef"],
                },
            )
            current_stage = "host_lease_created"
            await emit(current_stage, "started")
            host_lease_started = time.monotonic()
            host_lease = await self._create_host_lease_after_profile_idle(
                binding=binding,
                provider_lease=provider_lease,
                workflow_id=workflow_id,
                step_execution_id=step_execution_id,
                idempotency_key=request.idempotency_key,
                emit=emit,
            )
            if host_lease.status in {"stopped", "failed"}:
                host_lease = await self._hosts.restart_host_lease(host_lease.lease_id)
            control_plane_metrics.observe(
                control_plane_metrics.LEASE_ACQUIRE_LATENCY,
                time.monotonic() - host_lease_started,
                lease_scope="host",
            )
            await emit(
                current_stage,
                "completed",
                metadata={"hostLeaseRef": host_lease.lease_id},
            )
            bridge = await self._run_store.bind_profile_authorization(
                request=request,
                endpoint_ref=binding.endpoint_ref,
                provider_profile_id=profile_id,
                provider_lease_id=provider_lease.lease_id,
                credential_generation=host_lease.credential_generation,
                host_binding_ref=binding.binding_ref,
                host_lease_ref=host_lease.lease_id,
                omnigent_host_id=binding.static_host_id,
                effective_launch_snapshot=effective_launch,
            )
            authority_bridge_session_id = str(
                getattr(bridge, "bridge_session_id", "") or ""
            ) or None
            if host_lease.status == "allocating":
                host_lease = await self._hosts.transition_host_lease(
                    host_lease.lease_id,
                    expected_status="allocating",
                    new_status="starting",
                )
            github_token = await self._github_token(request)
            current_stage = "container_start"
            await emit(current_stage, "started")
            workspace_locator_payload = (
                remediation_resolution.get("workspaceLocator")
                if remediation_resolution is not None
                else workspace_intent.workspace_locator_payload()
            )
            preflight_operation = self._host_preparation.prepare_host(
                binding=binding,
                host_lease=host_lease,
                workspace_key=(
                    f"{workflow_id}:{step_execution_id or request.idempotency_key}"
                ),
                workspace_locator=workspace_locator_payload,
                current_workflow_id=workflow_id,
                current_step_execution_id=(
                    step_execution_id or request.idempotency_key
                ),
                resolved_skillset_ref=request.resolved_skillset_ref,
                artifact_gateway=self._artifact_service,
                evidence_request=request,
                cleanup_authority_store=self._run_store,
                target_repository=str(
                    (request.parameters or {}).get("repository") or ""
                ).strip(),
                required_capabilities=self._required_capabilities(request),
                execution_fanout_authorization=fanout_authorization,
                github_token=github_token,
                github_mutation_required=self._github_mutation_required(request),
                effective_launch=effective_launch,
                # A remediation workspace is already materialized and authorized by
                # the remediation owner, so never re-clone it here. Fresh normal
                # runs carry authored repository/branch intent that must be
                # materialized into the authoritative sandbox workspace.
                repository_source=(
                    ""
                    if remediation_resolution is not None
                    else (workspace_intent.repository or "")
                ),
                repository_provider=str(
                    (request.workspace_spec or {}).get("provider") or ""
                ).strip(),
                repository_connection_ref=str(
                    (request.workspace_spec or {}).get("connectionRef") or ""
                ).strip(),
                repository_client_evidence=dict(
                    (request.workspace_spec or {}).get("clientEvidence") or {}
                ),
                starting_branch=(
                    None
                    if remediation_resolution is not None
                    else workspace_intent.starting_branch
                ),
                target_branch=(
                    None
                    if remediation_resolution is not None
                    else workspace_intent.target_branch
                ),
                checkout_commit=(
                    None
                    if remediation_resolution is not None
                    else workspace_intent.checkout_commit
                ),
                # Host artifact materialization accepts only ``artifact://``
                # restore inputs. The compiler already partitioned provider
                # external-state refs into ``external_state_refs``; forward only
                # the artifact-backed restore refs so a checkpoint/continuation
                # carrying an external-state ref is not rejected before launch.
                restore_input_refs=(
                    ()
                    if remediation_resolution is not None
                    else tuple(workspace_intent.restore_input_refs)
                ),
                workspace_checkpoint_restore_ref=str(
                    (request.workspace_spec or {}).get("workspaceCheckpointRestoreRef")
                    or ""
                ).strip()
                or None,
                # A remediation workspace is already materialized with its own
                # inputs; only fresh normal runs project declared attachments
                # through the owning-worker boundary.
                attachment_refs=(
                    ()
                    if remediation_resolution is not None
                    else self._attachment_refs(request)
                ),
            )
            with control_plane_spans.omnigent_span(
                control_plane_spans.HOST_ENSURE,
                runtime=provider_runtime,
                host_mode=effective_launch.get("hostMode"),
                attempt_ordinal=self._attempts.current_attempt(),
            ):
                preflight = await self._execute_with_host_lease_heartbeat(
                    preflight_operation,
                    host_lease_ref=host_lease.lease_id,
                    ttl_seconds=int(effective_launch["limits"]["timeoutSeconds"]),
                )
            await emit(current_stage, "completed")
            workspace_resolution = preflight.get("workspaceResolution")
            authority_workspace_resolution = (
                dict(workspace_resolution)
                if isinstance(workspace_resolution, Mapping)
                else None
            )
            if isinstance(workspace_resolution, Mapping) and workspace_resolution:
                # Durable, bounded, credential-free evidence of which workspace was
                # resolved and how it was materialized, for Workflow Detail.
                await self._run_store.record_lifecycle_event(
                    request.idempotency_key,
                    event_type="workspace_resolution",
                    metadata=dict(workspace_resolution),
                )
            await emit("credential_mount", "started")
            await emit(
                "credential_mount",
                "completed",
                metadata={
                    "credentialGeneration": host_lease.credential_generation,
                    "credentialMountPath": (
                        "/home/app/.claude"
                        if provider_runtime == "claude_code"
                        else "/home/app/.codex"
                    ),
                },
            )
            host_id = str(preflight["hostId"])
            await emit("host_registration", "started")
            await emit(
                "host_registration", "completed", metadata={"omnigentHostId": host_id}
            )
            await emit("harness_readiness", "started")
            await emit(
                "harness_readiness", "ready", metadata={"omnigentHostId": host_id}
            )
            await emit("bridge_authentication", "started")
            await emit("bridge_authentication", "completed")
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
                effective_launch_snapshot=effective_launch,
            )
            await emit("credential_preflight", "started")
            await emit(
                "credential_preflight",
                "ready",
                metadata={
                    "providerProfileId": profile_id,
                    "credentialGeneration": host_lease.credential_generation,
                    "omnigentHostId": host_id,
                },
            )
            if preflight.get("mountedTools", {}).get("status") == "ready":
                await self._run_store.record_lifecycle_event(
                    request.idempotency_key,
                    event_type="mounted_tool_preflight_ready",
                    metadata=dict(preflight["mountedTools"]),
                )
            if recorded_plan is not None and runtime_binding_ref is not None:
                # Persist the exact live observations as separate bounded
                # artifacts. They are derived from the completed host preflight,
                # never manufactured by the immutable plan itself.
                host_attestation_ref = await self._write_plan_runtime_evidence(
                    request=request,
                    name="codex-host-harness-attestation.json",
                    payload={
                        "planRef": recorded_plan.planRef,
                        "hostId": host_id,
                        "hostRegistration": dict(
                            preflight.get("hostRegistrationEvidence") or {}
                        ),
                        "harness": preflight.get("harness")
                        or effective_launch.get("harness"),
                        "egressEvidenceRef": preflight.get("egressEvidenceRef"),
                    },
                )
                capability_ref = await self._write_plan_runtime_evidence(
                    request=request,
                    name="codex-exact-host-capability-decision.json",
                    payload={
                        "planRef": recorded_plan.planRef,
                        "hostId": host_id,
                        "classAdmissionDecision": (
                            recorded_plan.payload.classAdmissionDecision
                        ),
                        "mountedTools": dict(preflight.get("mountedTools") or {}),
                    },
                )
                workspace_ref = await self._write_plan_runtime_evidence(
                    request=request,
                    name="codex-workspace-resolution.json",
                    payload={
                        "planRef": recorded_plan.planRef,
                        "hostId": host_id,
                        "workspaceResolution": dict(workspace_resolution or {}),
                    },
                )
                model_ref = await self._write_plan_runtime_evidence(
                    request=request,
                    name="codex-model-option-attestation.json",
                    payload={
                        "planRef": recorded_plan.planRef,
                        "hostId": host_id,
                        "model": recorded_plan.payload.modelConfig.model_dump(
                            mode="json", by_alias=True
                        ),
                        "preflightStatus": preflight.get("status"),
                    },
                )
                skill_ref = await self._write_plan_runtime_evidence(
                    request=request,
                    name="codex-skill-delivery-attestation.json",
                    payload={
                        "planRef": recorded_plan.planRef,
                        "hostId": host_id,
                        "resolvedSkills": recorded_plan.payload.resolvedSkills,
                        "activeSkillsPath": preflight.get("activeSkillsPath"),
                        "skillDeliveryAttested": preflight.get(
                            "skillDeliveryAttested"
                        ),
                    },
                )
                runtime_state = await DbRuntimeBindingStore(
                    self._session_factory
                ).get_state(runtime_binding_ref)
                if runtime_state is None:
                    raise HarnessPlatformError(
                        "Codex runtime binding disappeared before host attestation",
                        code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                    )
                updated_binding = await DbRuntimeBindingStore(
                    self._session_factory
                ).update_with_host(
                    runtime_binding_ref,
                    host_binding_ref=binding.binding_ref,
                    host_lease_ref=host_lease.lease_id,
                    # The OAuth lane binds one deterministic host lease to one
                    # acquired credential generation; that generation is its
                    # durable replacement fence.
                    host_lease_generation=host_lease.credential_generation,
                    omnigent_host_id=host_id,
                    host_harness_attestation_ref=host_attestation_ref,
                    exact_host_capability_decision_ref=capability_ref,
                    workspace_resolution_ref=workspace_ref,
                    model_option_attestation_ref=model_ref,
                    skill_delivery_attestation_ref=skill_ref,
                    cleanup_authority_refs=[
                        value
                        for value in (
                            str(preflight.get("egressEvidenceRef") or ""),
                            f"host-lease:{host_lease.lease_id}",
                        )
                        if value
                    ],
                    expected_revision=runtime_state.revision,
                    expected_fencing_generation=(
                        runtime_state.fencing_generation
                    ),
                )
                runtime_binding_ref = updated_binding.runtimeBindingRef
            if host_lease.status == "ready":
                host_lease = await self._hosts.transition_host_lease(
                    host_lease.lease_id,
                    expected_status="ready",
                    new_status="assigned",
                    fields={"bridge_session_id": bridge.bridge_session_id},
                )
            current_stage = "session_creation"
            await emit(current_stage, "started", metadata={"omnigentHostId": host_id})
            await emit("first_message_prepare", "started")
            await emit("first_message_post", "started")
            await emit("session_running", "started")
            await emit("resource_harvest", "started")
            result = await self._execute_with_host_lease_heartbeat(
                self._execute(
                    bind_exact_host(
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
                            "effectiveLaunchRef": effective_launch["snapshotRef"],
                        },
                        harness=str(effective_launch["harness"]),
                        agent_name=str(effective_launch["agentName"]),
                    ),
                    artifact_gateway=self._artifact_gateway,
                    run_store=self._run_store,
                    defer_bridge_terminal=True,
                ),
                host_lease_ref=host_lease.lease_id,
                ttl_seconds=int(effective_launch["limits"]["timeoutSeconds"]),
            )
            result = collect_deferred_bridge_terminal(result)
            if recorded_plan is not None and runtime_binding_ref is not None:
                provider_session_id = str(
                    (result.metadata or {}).get("omnigentSessionId") or ""
                ).strip()
                if provider_session_id:
                    bridge_row = await self._run_store.get_existing(
                        request.idempotency_key
                    )
                    if (
                        bridge_row is not None
                        and bridge_row.omnigent_session_id is None
                    ):
                        bridge_row = await self._run_store.attach_session(
                            request.idempotency_key, provider_session_id
                        )
                    chat_binding_ref = await self._run_store.ensure_chat_binding_id(
                        bridge.bridge_session_id
                    )
                    runtime_state = await DbRuntimeBindingStore(
                        self._session_factory
                    ).get_state(runtime_binding_ref)
                    if runtime_state is None or not chat_binding_ref:
                        raise HarnessPlatformError(
                            "Codex session authority could not be bound",
                            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                        )
                    updated_binding = await DbRuntimeBindingStore(
                        self._session_factory
                    ).update_with_session(
                        runtime_binding_ref,
                        omnigent_session_id=provider_session_id,
                        omnigent_runner_ref=(
                            str(bridge_row.omnigent_runner_id)
                            if bridge_row is not None
                            and bridge_row.omnigent_runner_id
                            else None
                        ),
                        chat_binding_ref=chat_binding_ref,
                        expected_revision=runtime_state.revision,
                        expected_fencing_generation=(
                            runtime_state.fencing_generation
                        ),
                    )
                    runtime_binding_ref = updated_binding.runtimeBindingRef
            publish_mode = str(
                (request.parameters or {}).get("publishMode") or "none"
            ).strip().lower()
            if result.failure_class is None and publish_mode in {"branch", "pr"}:
                publication_stage = "repository_publication"
                no_commit_policy = _trusted_no_commit_repository_policy(request)
                for continuation_index in range(
                    REPOSITORY_PUBLICATION_CONTINUATION_LIMIT + 1
                ):
                    session_id = str(
                        (result.metadata or {}).get("omnigentSessionId") or ""
                    ).strip()
                    try:
                        completion = await self._session_inspection.inspect_session_completion(
                            session_id
                        )
                    except Exception as exc:
                        code = str(
                            getattr(exc, "code", None)
                            or "OMNIGENT_SESSION_COMPLETION_EVIDENCE_MISSING"
                        )[:96]
                        await emit(
                            publication_stage,
                            "failed",
                            code=code,
                            summary=(
                                "Repository publication was blocked because "
                                "terminal session evidence could not be verified."
                            ),
                            failure_class="integration_error",
                            remediation_action="retry_agent_execution",
                            ignore_errors=True,
                        )
                        result = result.model_copy(
                            update={
                                "failure_class": "integration_error",
                                "provider_error_code": code,
                                "retry_recommendation": "retry",
                                "summary": (
                                    "Omnigent terminal session evidence was "
                                    "unavailable before repository publication."
                                ),
                            }
                        )
                        break

                    publication: Mapping[str, Any] = {"push_status": "not_attempted"}
                    if completion.get("terminalAssistantAfterWork") is True:
                        await emit(
                            publication_stage,
                            "started",
                            metadata={
                                "continuationCount": continuation_index,
                                "terminalAssistantAfterWork": True,
                            },
                        )
                        try:
                            with control_plane_spans.omnigent_span(
                                control_plane_spans.WORKSPACE_PUBLISH,
                                runtime=provider_runtime,
                                attempt_ordinal=self._attempts.current_attempt(),
                            ):
                                publication = await self._workspace_publication.publish_workspace(
                                    workspace_locator=workspace_locator_payload or {},
                                    current_workflow_id=workflow_id,
                                    current_step_execution_id=(
                                        step_execution_id or request.idempotency_key
                                    ),
                                    publication_identity=request.idempotency_key,
                                    publish_mode=publish_mode,
                                    base_branch=self._starting_branch(request),
                                    repository=str(
                                        (request.parameters or {}).get("repository") or ""
                                    ).strip(),
                                    github_token=github_token,
                                )
                        except Exception as exc:
                            code = str(
                                getattr(exc, "code", None)
                                or "OMNIGENT_REPOSITORY_PUBLICATION_FAILED"
                            )[:96]
                            await emit(
                                publication_stage,
                                "failed",
                                code=code,
                                summary=(
                                    "Repository publication failed before host cleanup."
                                ),
                                failure_class="integration_error",
                                remediation_action="retry_repository_publication",
                                ignore_errors=True,
                            )
                            result = result.model_copy(
                                update={
                                    "failure_class": "integration_error",
                                    "provider_error_code": code,
                                    "retry_recommendation": "retry",
                                    "summary": (
                                        "Omnigent repository publication failed "
                                        "before the remote branch was verified."
                                    ),
                                    "metadata": {
                                        **dict(result.metadata or {}),
                                        "push_status": "failed",
                                    },
                                }
                            )
                            break

                    publication_metadata = dict(publication)
                    push_status = str(
                        publication.get("push_status") or ""
                    ).strip().lower()
                    no_commit_accepted = (
                        push_status == "no_commits" and no_commit_policy is not None
                    )
                    if push_status == "pushed" or no_commit_accepted:
                        evidence = AcceptedRepositoryEvidence(
                            pushStatus=push_status,
                            branch=publication.get("push_branch"),
                            baseBranch=publication.get("push_base_branch"),
                            headSha=publication.get("push_head_sha"),
                            commitsAheadOfBase=publication.get("push_commit_count"),
                            repositoryChanged=push_status == "pushed",
                            remoteVerified=publication.get("remote_verified"),
                            authority="omnigent.profile_bound_execution",
                        )
                        publication_metadata["acceptedRepositoryEvidence"] = (
                            evidence.model_dump(
                                mode="json", by_alias=True, exclude_none=True
                            )
                        )
                        result = result.model_copy(
                            update={
                                "metadata": {
                                    **dict(result.metadata or {}),
                                    **publication_metadata,
                                    "repositoryContinuationCount": continuation_index,
                                }
                            }
                        )
                        await emit(
                            publication_stage,
                            "completed",
                            metadata={
                                "pushStatus": push_status,
                                "branch": publication.get("push_branch"),
                                "baseBranch": publication.get("push_base_branch"),
                                "headSha": publication.get("push_head_sha"),
                                "commitsAheadOfBase": publication.get(
                                    "push_commit_count"
                                ),
                                "remoteVerified": publication.get(
                                    "remote_verified"
                                ),
                                "continuationCount": continuation_index,
                                **(
                                    {
                                        "noCommitAuthority": (
                                            no_commit_policy.authority
                                        ),
                                        "assessmentArtifactRef": (
                                            no_commit_policy.assessment_artifact_ref
                                        ),
                                    }
                                    if no_commit_accepted
                                    else {}
                                ),
                            },
                        )
                        break

                    if continuation_index >= REPOSITORY_PUBLICATION_CONTINUATION_LIMIT:
                        await emit(
                            publication_stage,
                            "failed",
                            code="OMNIGENT_REPOSITORY_OUTPUT_MISSING",
                            summary=(
                                "Bounded same-session continuation completed "
                                "without publishable repository output."
                            ),
                            failure_class="execution_error",
                            remediation_action="retry_agent_execution",
                        )
                        result = result.model_copy(
                            update={
                                "failure_class": "execution_error",
                                "provider_error_code": (
                                    "OMNIGENT_REPOSITORY_OUTPUT_MISSING"
                                ),
                                "retry_recommendation": "retry",
                                "summary": (
                                    "Agent execution completed without repository "
                                    "changes required by publishMode."
                                ),
                                "metadata": {
                                    **dict(result.metadata or {}),
                                    **publication_metadata,
                                    "repositoryContinuationCount": continuation_index,
                                    "terminalAssistantAfterWork": bool(
                                        completion.get(
                                            "terminalAssistantAfterWork", False
                                        )
                                    ),
                                },
                            }
                        )
                        break

                    continuation_number = continuation_index + 1
                    continuation_stage = (
                        f"repository_continuation_{continuation_number}"
                    )
                    await emit(
                        continuation_stage,
                        "started",
                        metadata={
                            "priorItemCount": int(completion.get("itemCount") or 0),
                            "priorToolResultCount": int(
                                completion.get("toolResultCount") or 0
                            ),
                            "terminalAssistantAfterWork": bool(
                                completion.get("terminalAssistantAfterWork", False)
                            ),
                        },
                    )
                    continuation_request = request.model_copy(
                        deep=True,
                        update={
                            "idempotency_key": (
                                f"{request.idempotency_key}:repository-continuation:"
                                f"{continuation_number}"
                            )
                        },
                    )
                    # Every repository continuation is a distinct canonical
                    # turn attempt on the *same* canonical session (#3707 AC1/
                    # AC2). Claiming before any provider mutation also fences
                    # incompatible cleanup for this generation.
                    continuation_claim = await self._claim_continuation_turn(
                        request=continuation_request,
                        source_request=request,
                        workflow_id=workflow_id,
                        step_execution_id=step_execution_id,
                        recorded_plan=recorded_plan,
                        provider_profile_id=profile_id,
                        credential_generation=host_lease.credential_generation,
                        runtime_binding_ref=runtime_binding_ref,
                    )
                    continuation_bridge = (
                        await self._run_store.bind_profile_authorization(
                            request=continuation_request,
                            endpoint_ref=binding.endpoint_ref,
                            provider_profile_id=profile_id,
                            provider_lease_id=provider_lease.lease_id,
                            credential_generation=host_lease.credential_generation,
                            host_binding_ref=binding.binding_ref,
                            host_lease_ref=host_lease.lease_id,
                            omnigent_host_id=host_id,
                            effective_launch_snapshot=effective_launch,
                        )
                    )
                    authority_bridge_session_id = str(
                        continuation_bridge.bridge_session_id
                    )
                    authority_idempotency_key = continuation_request.idempotency_key
                    continuation_result = await self._execute_with_host_lease_heartbeat(
                        self._execute(
                            bind_exact_host(
                                continuation_request,
                                host_id=host_id,
                                workspace_path=str(preflight["workspacePath"]),
                                profile_authorization={
                                    "providerProfileId": profile_id,
                                    "credentialGeneration": (
                                        host_lease.credential_generation
                                    ),
                                    "providerLeaseRef": provider_lease.lease_id,
                                    "hostBindingRef": binding.binding_ref,
                                    "hostLeaseRef": host_lease.lease_id,
                                    "endpointRef": binding.endpoint_ref,
                                    "omnigentHostId": host_id,
                                    "bridgeSessionId": (
                                        continuation_bridge.bridge_session_id
                                    ),
                                    "effectiveLaunchRef": effective_launch["snapshotRef"],
                                },
                                harness=str(effective_launch["harness"]),
                                agent_name=str(effective_launch["agentName"]),
                            ),
                            artifact_gateway=self._artifact_gateway,
                            run_store=self._run_store,
                            resume_session_id=session_id,
                            first_message_text=(
                                _REPOSITORY_PUBLICATION_CONTINUATION_PROMPT
                            ),
                            defer_bridge_terminal=True,
                        ),
                        host_lease_ref=host_lease.lease_id,
                        ttl_seconds=int(
                            effective_launch["limits"]["timeoutSeconds"]
                        ),
                    )
                    result = collect_deferred_bridge_terminal(continuation_result)
                    await self._settle_continuation_turn(
                        claim=continuation_claim,
                        workflow_id=workflow_id,
                        idempotency_key=continuation_request.idempotency_key,
                        result=result,
                    )
                    await emit(
                        continuation_stage,
                        "failed" if result.failure_class else "completed",
                        code=result.provider_error_code,
                        failure_class=(
                            str(result.failure_class)
                            if result.failure_class
                            else None
                        ),
                        metadata={"continuationNumber": continuation_number},
                    )
                    if result.failure_class is not None:
                        break
            # Publish the compact, reference-only session/host plane needed by
            # the canonical Step Execution checkpoint writer.  Workspace
            # evidence is deliberately added later by the workspace capture
            # activity; neither boundary is allowed to infer the other plane.
            result_metadata = dict(result.metadata or {})
            runtime_binding_authority = None
            if recorded_plan is not None and runtime_binding_ref is not None:
                runtime_binding_authority = await DbRuntimeBindingStore(
                    self._session_factory
                ).get_state(runtime_binding_ref)
                if runtime_binding_authority is None:
                    raise HarnessPlatformError(
                        "Codex runtime binding disappeared before capture",
                        code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                    )
                result_metadata.update(
                    {
                        "executionPlanRef": recorded_plan.planRef,
                        "executionPlanDigest": (
                            "sha256:" + recorded_plan.planRef.rsplit(":", 1)[-1]
                        ),
                        "runtimeBindingRef": (
                            runtime_binding_authority.binding.runtimeBindingRef
                        ),
                        "runtimeBindingRevision": (
                            runtime_binding_authority.revision
                        ),
                        "runtimeBindingFencingGeneration": (
                            runtime_binding_authority.fencing_generation
                        ),
                        "supportCombinationIdentity": (
                            recorded_plan.payload.supportIdentity.model_dump(
                                mode="json", by_alias=True
                            )
                            if recorded_plan.payload.supportIdentity is not None
                            else None
                        ),
                    }
                )
            result_metadata["omnigentCheckpointCapture"] = {
                "providerProfileId": profile_id,
                "credentialRef": (
                    f"credential://provider-profile/{profile_id}/generation/"
                    f"{host_lease.credential_generation}"
                ),
                "credentialGeneration": host_lease.credential_generation,
                "providerLeaseRef": provider_lease.lease_id,
                "hostBindingRef": binding.binding_ref,
                "hostLeaseRef": host_lease.lease_id,
                "hostLeaseGeneration": host_lease.credential_generation,
                "endpointRef": binding.endpoint_ref,
                "omnigentHostId": host_id,
                "bridgeSessionId": (
                    authority_bridge_session_id or bridge.bridge_session_id
                ),
                "effectiveLaunchRef": effective_launch["snapshotRef"],
                "executionProfileRef": effective_launch["executionProfileRef"],
                "launchPolicyRef": effective_launch["launchPolicyRef"],
                "executionPlanRef": str(
                    (request.parameters or {}).get("executionPlanRef") or ""
                ).strip()
                or None,
                # The full attestation includes the compiled launch-policy
                # boundary and is already durable behind this reference. Keep
                # the workflow result plane reference-only so terminal evidence
                # enrichment cannot overflow AgentRunResult metadata.
                "egressEvidenceRef": preflight.get("egressEvidenceRef"),
                # Stamp the immutable policy-authority evidence resolved for this
                # launch so the Step Execution checkpoint can prove which compiled
                # policy snapshot governed the run at cold-restore time. Only the
                # six compact fields are carried (never the boundaries block) to
                # stay within the compact-history bound.
                "policyId": policy_snapshot["policyId"],
                "policyVersion": policy_snapshot["policyVersion"],
                "policyRef": policy_snapshot["policyRef"],
                "policyDigest": policy_snapshot["policyDigest"],
                "policySnapshotRef": policy_snapshot["snapshotRef"],
                "policyValidation": policy_snapshot["validation"],
                "externalStateRef": result_metadata.get("externalStateRef"),
                "captureManifestRef": result_metadata.get("captureManifestRef"),
                "terminalRef": next(iter(result.output_refs), None),
                "diagnosticsRef": result.diagnostics_ref,
                "omnigentSessionId": result_metadata.get("omnigentSessionId"),
                "idempotencyKey": authority_idempotency_key,
                "sourceBranch": self._starting_branch(request) or "detached",
                "outputBranch": self._target_branch(request),
                "publicationState": str(
                    (request.parameters or {}).get("publishMode") or "none"
                ),
                **(
                    {
                        "executionPlanRef": recorded_plan.planRef,
                        "runtimeBindingRef": (
                            runtime_binding_authority.binding.runtimeBindingRef
                        ),
                        "runtimeBindingRevision": (
                            runtime_binding_authority.revision
                        ),
                        "runtimeBindingFencingGeneration": (
                            runtime_binding_authority.fencing_generation
                        ),
                        "hostHarnessAttestationRef": (
                            runtime_binding_authority.binding.hostHarnessAttestationRef
                        ),
                        "exactHostCapabilityDecisionRef": (
                            runtime_binding_authority.binding.exactHostCapabilityDecisionRef
                        ),
                        "workspaceResolutionRef": (
                            runtime_binding_authority.binding.workspaceResolutionRef
                        ),
                        "modelOptionAttestationRef": (
                            runtime_binding_authority.binding.modelOptionAttestationRef
                        ),
                        "skillDeliveryAttestationRef": (
                            runtime_binding_authority.binding.skillDeliveryAttestationRef
                        ),
                    }
                    if recorded_plan is not None
                    and runtime_binding_authority is not None
                    else {}
                ),
            }
            result = result.model_copy(update={"metadata": result_metadata})
            authority_result = result
            result_failed = bool(result.failure_class or result.provider_error_code)
            result_status = "failed" if result_failed else "completed"
            terminal_status = result_status
            if result_failed:
                # The runner returned a failed ``AgentRunResult`` instead of
                # raising, so the exception path never runs. Carry the provider
                # failure code, class, and remediation into the unified authority
                # chain; otherwise ``harvestState="failed"`` would surface with an
                # empty reasons list, dropping evidence already on the result.
                authority_reasons.append(
                    {
                        "stage": "resource_harvest",
                        "code": (
                            result.provider_error_code
                            or (
                                str(result.failure_class)
                                if result.failure_class
                                else "provider_run_failed"
                            )
                        ),
                        "failureClass": (
                            str(result.failure_class)
                            if result.failure_class
                            else None
                        ),
                        "remediationAction": result.retry_recommendation or None,
                    }
                )
            await emit("first_message_prepare", result_status)
            await emit("first_message_post", result_status)
            await emit(
                current_stage, result_status, metadata={"omnigentHostId": host_id}
            )
            await emit("session_running", result_status)
            await emit(
                "resource_harvest",
                result_status,
                code=result.provider_error_code,
                failure_class=(
                    str(result.failure_class) if result.failure_class else None
                ),
                diagnostics_ref=persisted_diagnostics_ref(result),
            )
            if str(result.provider_error_code or "") == "429":
                await self._lease_client.record_cooldown(
                    runtime_id=provider_runtime,
                    profile_id=profile_id,
                    owner_id=provider_lease.owner_id,
                    cooldown_seconds=profile.cooldown_after_429_seconds,
                    reason="provider_429",
                )
                await emit(
                    "profile_cooldown",
                    "waiting",
                    code="provider_429",
                    remediation_action="retry_after_provider_cooldown",
                    metadata={"providerProfileId": profile_id},
                )
            return result
        except (Exception, asyncio.CancelledError) as exc:
            prepared_host_evidence = getattr(
                exc, "prepared_host_evidence", None
            )
            if isinstance(prepared_host_evidence, Mapping):
                preflight = dict(prepared_host_evidence)
                launch_evidence_ref = str(
                    preflight.get("egressEvidenceRef") or ""
                ).strip()
                if launch_evidence_ref:
                    authority_cleanup_evidence["launchEvidenceRef"] = (
                        launch_evidence_ref
                    )
            if isinstance(exc, asyncio.CancelledError) and not isinstance(
                prepared_host_evidence, Mapping
            ):
                attempt_cleanup_deferred_code = "activity_cancelled"
            elif isinstance(exc, OmnigentSessionStillRunningError):
                attempt_cleanup_deferred_code = "ambiguous_terminal_state"
            elif (
                isinstance(exc, OmnigentOAuthHostError)
                and exc.code == HOST_CLEANUP_CLAIMED_ERROR_CODE
            ):
                # The janitor won the durable cleanup claim. Its draining lease
                # and Provider Profile release are now one authority chain; the
                # coordinator must not enter a second finally-cleanup path.
                attempt_cleanup_deferred_code = HOST_CLEANUP_CLAIMED_ERROR_CODE
            terminal_status = "failed"
            if bridge_ready:
                code, failure_class, remediation = classify_launch_failure_evidence(exc)
                authority_reasons.append(
                    {
                        "stage": current_stage,
                        "code": code,
                        "failureClass": failure_class,
                        "remediationAction": remediation,
                    }
                )
                workspace_denial = getattr(exc, "workspace_denial_evidence", None)
                if isinstance(workspace_denial, Mapping) and workspace_denial:
                    # Durable, bounded, credential-free evidence that a workspace
                    # materialization was denied: the failed authority class, stable
                    # reason code, whether owned partial state was created, and the
                    # reconciliation requirement for the next retry.
                    await emit(
                        "workspace_materialization_denied",
                        "failed",
                        code=code,
                        summary=str(exc),
                        metadata=dict(workspace_denial),
                        ignore_errors=True,
                    )
                if isinstance(exc, MountedToolPreflightError):
                    await self._run_store.record_lifecycle_event(
                        request.idempotency_key,
                        event_type="mounted_tool_preflight_blocked",
                        status="failed",
                        event_identity=(
                            f"{attempt_identity}:mounted_tool_preflight_blocked:failed"
                        ),
                        code=exc.code,
                        summary=str(exc),
                        metadata=exc.evidence,
                    )
                prepare_failure_stage = prepare_host_failure_stage(exc)
                if prepare_failure_stage and prepare_failure_stage not in active_stages:
                    await emit(prepare_failure_stage, "started", ignore_errors=True)
                for stage in list(active_stages) or [current_stage]:
                    await emit(
                        stage,
                        "failed",
                        code=code,
                        summary=str(exc),
                        failure_class=failure_class,
                        remediation_action=remediation,
                        diagnostics_ref=persisted_diagnostics_ref(exc),
                        ignore_errors=True,
                    )
            raise
        finally:
            safe_to_release_provider = host_lease is None
            if host_lease is not None and binding is not None:
                if attempt_cleanup_deferred_code is None:
                    try:
                        claimed_cleanup_lease = await self._claim_host_cleanup(
                            host_lease.lease_id
                        )
                    except Exception as claim_exc:
                        attempt_cleanup_deferred_code = type(claim_exc).__name__
                    else:
                        if claimed_cleanup_lease is None:
                            attempt_cleanup_deferred_code = (
                                HOST_CLEANUP_CLAIMED_ERROR_CODE
                            )
                        else:
                            host_lease = claimed_cleanup_lease
                if attempt_cleanup_deferred_code is not None:
                    # A Temporal timeout or ambiguous terminal observation can
                    # schedule a retry against the same durable bridge. Leave the
                    # deterministic host and credential lease for that retry (or
                    # the janitor) so the retry cannot be redirected to a new host.
                    authority_cleanup_mode = "retry_or_janitor_reconciliation"
                    authority_reasons.append(
                        {
                            "stage": "host_cleanup",
                            "code": attempt_cleanup_deferred_code,
                            "failureClass": "system_error",
                            "remediationAction": "retry_or_reconcile_stale_host",
                        }
                    )
                    await emit(
                        "host_cleanup",
                        "waiting",
                        code=attempt_cleanup_deferred_code,
                        remediation_action="retry_or_reconcile_stale_host",
                        metadata={
                            "cleanupCompleted": False,
                            "janitorRequired": True,
                        },
                        ignore_errors=True,
                    )
                else:
                    try:
                        cleanup_mode = (
                            "on_demand_remove"
                            if binding.host_launch_profile_ref
                            else "static_drain"
                        )
                        authority_cleanup_mode = cleanup_mode
                        await emit(
                            "host_cleanup",
                            "started",
                            metadata={
                                "sessionInterrupted": host_lease.status == "assigned",
                                "hostCleanupMode": cleanup_mode,
                            },
                            ignore_errors=True,
                        )
                        if host_lease.status == "assigned":
                            host_lease = await self._hosts.transition_host_lease(
                                host_lease.lease_id,
                                expected_status="assigned",
                                new_status="draining",
                            )
                        with control_plane_spans.omnigent_span(
                            control_plane_spans.CLEANUP_EXECUTE,
                            runtime=provider_runtime,
                            host_mode=effective_launch.get("hostMode"),
                        ):
                            cleanup_evidence = await self._host_release.stop_host(
                                binding=binding,
                                host_lease=host_lease,
                                effective_launch=effective_launch,
                                egress_evidence=(
                                    preflight.get("egressAttestation")
                                    if isinstance(preflight, Mapping)
                                    else None
                                ),
                                launch_evidence_ref=(
                                    str(preflight.get("egressEvidenceRef") or "") or None
                                    if isinstance(preflight, Mapping)
                                    else None
                                ),
                                evidence_request=request,
                                artifact_gateway=self._artifact_service,
                            )
                        authority_cleanup_evidence = dict(cleanup_evidence or {})
                        await self._hosts.mark_host_lease_stopped(host_lease.lease_id)
                        safe_to_release_provider = True
                        await emit(
                            "host_cleanup",
                            "completed",
                            metadata={
                                "cleanupCompleted": True,
                                "sessionInterrupted": True,
                                "hostCleanupMode": cleanup_mode,
                                "stateResourcesCleaned": True,
                                "hostLeaseReleased": True,
                                "egressEvidenceRef": (
                                    cleanup_evidence.get("evidenceRef")
                                    if isinstance(cleanup_evidence, Mapping)
                                    else None
                                ),
                                "egressLaunchEvidenceRef": (
                                    cleanup_evidence.get("launchEvidenceRef")
                                    if isinstance(cleanup_evidence, Mapping)
                                    else None
                                ),
                            },
                            ignore_errors=True,
                        )
                    except Exception as cleanup_exc:
                        error_cleanup_evidence = getattr(
                            cleanup_exc, "cleanup_evidence", None
                        )
                        if isinstance(error_cleanup_evidence, Mapping):
                            authority_cleanup_evidence.update(
                                dict(error_cleanup_evidence)
                            )
                        error_evidence_ref = str(
                            authority_cleanup_evidence.get("evidenceRef")
                            or getattr(cleanup_exc, "egress_evidence_ref", None)
                            or ""
                        ).strip()
                        launch_evidence_ref = str(
                            authority_cleanup_evidence.get("launchEvidenceRef")
                            or (
                                preflight.get("egressEvidenceRef")
                                if isinstance(preflight, Mapping)
                                else None
                            )
                            or ""
                        ).strip()
                        if error_evidence_ref:
                            authority_cleanup_evidence["evidenceRef"] = (
                                error_evidence_ref
                            )
                        if launch_evidence_ref:
                            authority_cleanup_evidence["launchEvidenceRef"] = (
                                launch_evidence_ref
                            )
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
                        authority_reasons.append(
                            {
                                "stage": "host_cleanup",
                                "code": type(cleanup_exc).__name__,
                                "failureClass": "system_error",
                                "remediationAction": "inspect_cleanup_diagnostics",
                            }
                        )
                        await emit(
                            "host_cleanup",
                            "failed",
                            code=type(cleanup_exc).__name__,
                            summary=str(cleanup_exc),
                            failure_class="system_error",
                            remediation_action="inspect_cleanup_diagnostics",
                            metadata={
                                "cleanupCompleted": False,
                                "janitorRequired": True,
                                "egressEvidenceRef": error_evidence_ref or None,
                                "egressLaunchEvidenceRef": (
                                    launch_evidence_ref or None
                                ),
                            },
                        )
            for deferred_terminal in deferred_bridge_terminals:
                try:
                    await self._run_store.mark_terminal(
                        deferred_terminal["idempotencyKey"],
                        status=deferred_terminal["status"],
                        terminal_refs=deferred_terminal["terminalRefs"],
                    )
                except Exception as terminal_exc:
                    authority_reasons.append(
                        {
                            "stage": "terminal_evidence_commit",
                            "code": type(terminal_exc).__name__,
                            "failureClass": "system_error",
                            "remediationAction": "inspect_cleanup_diagnostics",
                        }
                    )
            lease_released = provider_lease is None
            if provider_lease is not None:
                if safe_to_release_provider:
                    await emit(
                        "profile_lease_release",
                        "started",
                        metadata={"leaseReleased": False},
                        ignore_errors=True,
                    )
                    release_exc: Exception | None = None
                    for release_attempt in range(3):
                        try:
                            await self._lease_client.release_lease(provider_lease)
                            lease_released = True
                            release_exc = None
                            break
                        except Exception as exc:
                            release_exc = exc
                            if release_attempt < 2:
                                await asyncio.sleep(2**release_attempt)
                    if lease_released:
                        await emit(
                            "profile_lease_release",
                            "completed",
                            metadata={"leaseReleased": True},
                        )
                    elif release_exc is not None:
                        await emit(
                            "profile_lease_release",
                            "failed",
                            code=type(release_exc).__name__,
                            summary=str(release_exc),
                            failure_class="system_error",
                            remediation_action="inspect_cleanup_diagnostics",
                            diagnostics_ref=persisted_diagnostics_ref(release_exc),
                            metadata={"leaseReleased": False, "janitorRequired": True},
                            ignore_errors=True,
                        )
                else:
                    await emit(
                        "profile_lease_release",
                        "waiting",
                        code="credential_cleanup_incomplete",
                        remediation_action="inspect_cleanup_diagnostics",
                        metadata={"leaseReleased": False, "janitorRequired": True},
                        ignore_errors=True,
                    )
            janitor_required = provider_lease is not None and not lease_released
            if (
                recorded_plan is not None
                and runtime_binding_ref is not None
                and safe_to_release_provider
                and lease_released
            ):
                runtime_store = DbRuntimeBindingStore(self._session_factory)
                runtime_state = await runtime_store.get_state(
                    runtime_binding_ref
                )
                if runtime_state is not None:
                    completed_binding = await runtime_store.mark_cleanup_complete(
                        runtime_binding_ref,
                        expected_revision=runtime_state.revision,
                        expected_fencing_generation=(
                            runtime_state.fencing_generation
                        ),
                    )
                    runtime_binding_ref = completed_binding.runtimeBindingRef
                    completed_state = await runtime_store.get_state(
                        runtime_binding_ref
                    )
                    if authority_result is not None and completed_state is not None:
                        authority_result.metadata.update(
                            {
                                "runtimeBindingRef": runtime_binding_ref,
                                "runtimeBindingRevision": completed_state.revision,
                                "runtimeBindingFencingGeneration": (
                                    completed_state.fencing_generation
                                ),
                                "runtimeBindingState": completed_state.state,
                            }
                        )
            # Emit the single unified, bounded, credential-free authority chain
            # (MoonLadderStudios/MoonMind#3561) before terminal so Workflow Detail
            # exposes one workspace -> runtime -> publication -> terminal ->
            # cleanup -> lease projection for both success and failure paths.
            try:
                release_ordering: list[str] = []
                if host_lease is not None:
                    release_ordering.append(
                        "host_cleanup_completed"
                        if safe_to_release_provider
                        else "host_cleanup_incomplete"
                    )
                if provider_lease is not None:
                    release_ordering.append(
                        "provider_lease_released"
                        if lease_released
                        else "provider_lease_release_deferred"
                    )
                release_ordering.append("terminal")
                if janitor_required:
                    authority_reasons.append(
                        {
                            "stage": "profile_lease_release",
                            "code": "credential_cleanup_incomplete",
                            "failureClass": "system_error",
                            "remediationAction": "inspect_cleanup_diagnostics",
                        }
                    )
                authorization_evidence: dict[str, Any] = {}
                if profile_id:
                    authorization_evidence["providerProfileId"] = profile_id
                if provider_lease is not None:
                    authorization_evidence["providerLeaseRef"] = provider_lease.lease_id
                if binding is not None:
                    authorization_evidence["hostBindingRef"] = binding.binding_ref
                    authorization_evidence["endpointRef"] = binding.endpoint_ref
                if host_lease is not None:
                    authorization_evidence["hostLeaseRef"] = host_lease.lease_id
                    authorization_evidence["credentialGeneration"] = (
                        host_lease.credential_generation
                    )
                    if host_lease.omnigent_host_id:
                        authorization_evidence["omnigentHostId"] = (
                            host_lease.omnigent_host_id
                        )
                if effective_launch is not None:
                    authorization_evidence["effectiveLaunchRef"] = str(
                        effective_launch.get("snapshotRef") or ""
                    )
                if authority_bridge_session_id:
                    authorization_evidence["bridgeSessionId"] = (
                        authority_bridge_session_id
                    )
                if isinstance(preflight, Mapping) and preflight.get(
                    "egressEvidenceRef"
                ):
                    authorization_evidence["egressLaunchEvidenceRef"] = str(
                        preflight["egressEvidenceRef"]
                    )
                cleanup_launch_ref = str(
                    authority_cleanup_evidence.get("launchEvidenceRef") or ""
                ).strip()
                cleanup_terminal_ref = str(
                    authority_cleanup_evidence.get("evidenceRef") or ""
                ).strip()
                if cleanup_launch_ref:
                    authorization_evidence["egressLaunchEvidenceRef"] = (
                        cleanup_launch_ref
                    )
                if cleanup_terminal_ref:
                    authorization_evidence["egressTerminalEvidenceRef"] = (
                        cleanup_terminal_ref
                    )
                authority_chain = build_omnigent_authority_chain_evidence(
                    effective_launch=effective_launch,
                    workspace_resolution=authority_workspace_resolution,
                    repository=self._repository_source(request) or None,
                    source_branch=self._starting_branch(request),
                    output_branch=self._target_branch(request),
                    publish_mode=str(
                        (request.parameters or {}).get("publishMode") or "none"
                    ),
                    required_capabilities=self._required_capabilities(request),
                    repository_mutation_required=self._repository_mutation_required(
                        request
                    ),
                    github_mutation_required=self._github_mutation_required(request),
                    profile_authorization=authorization_evidence,
                    egress_attestation=(
                        preflight.get("egressAttestation")
                        if isinstance(preflight, Mapping)
                        else None
                    ),
                    result_output_refs=(
                        authority_result.output_refs
                        if authority_result is not None
                        else ()
                    ),
                    result_metadata=(
                        authority_result.metadata
                        if authority_result is not None
                        else None
                    ),
                    terminal_status=terminal_status,
                    cleanup_mode=authority_cleanup_mode,
                    cleanup_completed=safe_to_release_provider,
                    lease_released=lease_released,
                    janitor_required=janitor_required,
                    release_ordering=release_ordering,
                    reasons=authority_reasons,
                )
                if authority_result is not None:
                    # ``return result`` is evaluated before this ``finally``
                    # block. Mutate that same canonical envelope so callers
                    # receive the final cleanup/release authority, not the
                    # runner's pre-cleanup snapshot. The durable lifecycle
                    # event remains the reconciliation source of truth.
                    authority_result.metadata["authorityChain"] = authority_chain
                await emit(
                    "authority_chain",
                    "completed",
                    metadata={"authorityChain": authority_chain},
                    ignore_errors=True,
                )
            except Exception:
                # Bounded evidence is best-effort and must never mask the primary
                # run outcome or its terminal record.
                pass
            terminal_launch_evidence_ref = str(
                authority_cleanup_evidence.get("launchEvidenceRef")
                or (
                    preflight.get("egressEvidenceRef")
                    if isinstance(preflight, Mapping)
                    else None
                )
                or ""
            ).strip()
            await emit(
                "terminal",
                terminal_status,
                metadata={
                    "cleanupCompleted": safe_to_release_provider,
                    "leaseReleased": lease_released,
                    "janitorRequired": janitor_required,
                    "egressLaunchEvidenceRef": terminal_launch_evidence_ref or None,
                    "egressEvidenceRef": str(
                        authority_cleanup_evidence.get("evidenceRef") or ""
                    ).strip()
                    or None,
                },
                ignore_errors=True,
            )

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
        candidate_workspace: CandidateWorkspaceAuthority,
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
        if mode == SessionResumeDecision.LIVE_REATTACH:
            if request.execution_profile_ref != checkpoint.provider_profile_id:
                raise ValueError("live reattach Provider Profile mismatch")
            profile = await self._profile_authority.resolve(
                checkpoint.provider_profile_id
            )
            runtime_id = profile.runtime_id
            harness = (
                "claude-native" if runtime_id == "claude_code" else "codex-native"
            )
            live_request = bind_candidate_workspace(request, candidate_workspace)
            live_request = live_request.model_copy(
                update={
                    "idempotency_key": checkpoint.idempotency_key,
                    "input_refs": list(
                        dict.fromkeys(
                            [*live_request.input_refs, checkpoint.external_state_ref]
                        )
                    ),
                }
            )
            return await self._execute(
                bind_exact_host(
                    live_request,
                    host_id=str(checkpoint.omnigent_host_id),
                    workspace_path="/workspaces/run",
                    profile_authorization=checkpoint.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    ),
                    harness=harness,
                    agent_name=(
                        CLAUDE_STOCK_AGENT_NAME
                        if runtime_id == "claude_code"
                        else CODEX_STOCK_AGENT_NAME
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
        restore_material = materialize_cold_restore_inputs(
            checkpoint, checkpoint.validation
        )
        parameters["checkpointRestore"] = {
            "mode": "cold_restore",
            "externalStateRef": checkpoint.external_state_ref,
            "sourceBridgeSessionId": checkpoint.bridge_session_id,
            "restoreMaterial": restore_material.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
            "candidateWorkspace": candidate_workspace.model_dump(
                by_alias=True, mode="json"
            ),
        }
        workspace_spec = bind_cold_restore_workspace_spec(
            request.workspace_spec,
            restore_material=restore_material,
            candidate_workspace=candidate_workspace,
        )
        return await self.execute(
            request.model_copy(
                update={
                    "idempotency_key": cold_key,
                    "parameters": parameters,
                    "workspace_spec": workspace_spec,
                    "input_refs": list(
                        dict.fromkeys(
                            [
                                *request.input_refs,
                                *restore_material.immutable_input_refs,
                                checkpoint.external_state_ref,
                                candidate_workspace.head_ref,
                                candidate_workspace.checkpoint_ref,
                            ]
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
        candidate_workspace: CandidateWorkspaceAuthority,
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
        restore_material = materialize_cold_restore_inputs(
            checkpoint, checkpoint.validation
        )
        parameters["checkpointRestore"] = {
            "mode": "branch",
            "externalStateRef": checkpoint.external_state_ref,
            "sourceBridgeSessionId": checkpoint.bridge_session_id,
            "restoreMaterial": restore_material.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
            "candidateWorkspace": candidate_workspace.model_dump(
                by_alias=True, mode="json"
            ),
        }
        workspace_spec = bind_cold_restore_workspace_spec(
            request.workspace_spec,
            restore_material=restore_material,
            candidate_workspace=candidate_workspace,
        )
        return await self.execute(
            request.model_copy(
                update={
                    "parameters": parameters,
                    "workspace_spec": workspace_spec,
                    "input_refs": list(
                        dict.fromkeys(
                            [
                                *request.input_refs,
                                *restore_material.immutable_input_refs,
                                checkpoint.external_state_ref,
                                candidate_workspace.head_ref,
                                candidate_workspace.checkpoint_ref,
                            ]
                        )
                    ),
                }
            )
        )

    @classmethod
    def _repository_source(cls, request: AgentExecutionRequest) -> str:
        return authored_repository_source(request)

    @classmethod
    def _starting_branch(cls, request: AgentExecutionRequest) -> str | None:
        return authored_starting_branch(request)

    @classmethod
    def _target_branch(cls, request: AgentExecutionRequest) -> str | None:
        return authored_target_branch(request)

    @classmethod
    def _checkout_commit(cls, request: AgentExecutionRequest) -> str | None:
        return authored_checkout_commit(request)

    @classmethod
    def _restore_input_refs(cls, request: AgentExecutionRequest) -> tuple[str, ...]:
        return authored_restore_input_refs(request)

    @classmethod
    def _attachment_refs(cls, request: AgentExecutionRequest) -> tuple[str, ...]:
        """Return canonical prepared attachment refs from the execution request.

        Attachments are durable artifact refs (validated at the owning-worker
        boundary); the ordered, de-duplicated ref list is materialized into the
        authorized workspace alongside the repository and restore inputs.
        """
        raw = request.input_refs
        return tuple(
            dict.fromkeys(
                str(value).strip() for value in raw if str(value).strip()
            )
        )

    @staticmethod
    def _remediation_workspace(
        request: AgentExecutionRequest,
    ) -> RemediationWorkspaceBinding | None:
        if request.remediation_workspace is None:
            return None
        return RemediationWorkspaceBinding.model_validate(
            request.remediation_workspace
        )

    @staticmethod
    def _required_capabilities(request: AgentExecutionRequest) -> tuple[str, ...]:
        return authored_required_capabilities(request)

    @staticmethod
    def _execution_fanout_authorization(
        request: AgentExecutionRequest,
    ) -> Mapping[str, Any] | None:
        step_execution = request.step_execution
        if step_execution is None:
            return None
        policy = step_execution.skill_source_policy
        if "executionFanout" not in policy:
            return None
        evidence = policy.get("executionFanout")
        if not isinstance(evidence, Mapping):
            raise OmnigentOAuthHostError(
                "execution fan-out authorization evidence is malformed",
                code="authorization_denied",
            )
        return evidence

    @classmethod
    async def _github_token(cls, request: AgentExecutionRequest) -> str | None:
        gh_required = "gh" in cls._required_capabilities(request)
        # A GitHub repository source must be cloned into the sandbox before the
        # host launches. Private repositories require a credential for that clone
        # even when mounted `gh` readiness is not a declared capability — for
        # example read-only or publishMode=none work derives `git` but not `gh`.
        # Resolve the clone credential from the authored GitHub repository source
        # independently of mounted-`gh` readiness so private materialization does
        # not silently fall back to an unauthenticated clone.
        clone_needs_credential = cls._github_repository_source(request) is not None
        if not gh_required and not clone_needs_credential:
            return None
        from moonmind.auth.github_credentials import resolve_github_credential

        repository = str((request.parameters or {}).get("repository") or "").strip()
        resolved = await resolve_github_credential(repo=repository or None)
        token = str(resolved.token or "").strip() if resolved else ""
        if not token:
            if gh_required:
                raise OmnigentOAuthHostError(
                    "GitHub credential is required for mounted gh readiness",
                    code="github_auth_unavailable",
                )
            # A public GitHub clone can proceed unauthenticated; a private clone
            # fails fast with an actionable error at materialization.
            return None
        return token

    @classmethod
    def _github_repository_source(cls, request: AgentExecutionRequest) -> str | None:
        """Return the authored GitHub HTTPS clone source, if any.

        Reuses the single canonical repository-source classifier so GitHub
        detection cannot drift between credential resolution and materialization.
        """
        source = cls._repository_source(request)
        if not source:
            return None
        try:
            normalized, kind = normalize_repository_source(source)
        except RepositorySourceError:
            return None
        return normalized if kind == "github_https" else None

    @staticmethod
    def _github_mutation_required(request: AgentExecutionRequest) -> bool:
        return authored_github_mutation_required(request)

    @staticmethod
    def _repository_mutation_required(request: AgentExecutionRequest) -> bool:
        return authored_repository_mutation_required(request)


__all__ = ["OmnigentProfileBoundExecutionCoordinator"]
