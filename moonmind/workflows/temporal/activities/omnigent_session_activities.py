"""Bounded Activities for ``MoonMind.OmnigentSession``.

Source: MoonLadderStudios/MoonMind#3705. Each mutating Activity claims one
durable logical command and validates the canonical session revision and
supervisor fence before applying provider/profile/host state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

from moonmind.omnigent.control_plane.cleanup_authority import (
    CanonicalCleanupAuthority,
)
from moonmind.omnigent.harness_platform.harness_registry import (
    canonical_harness_id,
    find_harness_registration,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    OmnigentExecutionPlanBinding,
)
from moonmind.schemas.omnigent_session_models import (
    OMNIGENT_SESSION_COMPATIBILITY_VERSION,
    OMNIGENT_SESSION_FEATURE_GENERATION,
    OmnigentFailureAuthorityRequest,
    OmnigentPersistDecisionRequest,
    OmnigentPersistFailureRequest,
    OmnigentPersistSignalsRequest,
    OmnigentResolveIntentRequest,
    OmnigentSessionAdmissionDecision,
    OmnigentSessionAdmissionRequest,
    OmnigentSessionActivityRequest,
    OmnigentSessionTerminalResult,
    OmnigentSessionWorkflowInput,
)


_ARTIFACT_PRINCIPAL = "service:omnigent_session_supervisor"
_MAX_EVENT_BATCH = 100
_EVENT_READ_SECONDS = 10
_TERMINAL_PROVIDER_STATES = {
    "completed",
    "complete",
    "success",
    "succeeded",
    "failed",
    "error",
    "errored",
    "canceled",
    "cancelled",
    "timed_out",
    "timeout",
}
_RECONCILER_OBSERVATION_KEYS = frozenset(
    {
        "providerSession",
        "providerTurn",
        "eventFrontier",
        "host",
        "profileLease",
        "hostLease",
        "workspace",
        "evidence",
        "compatibility",
    }
)
_JANITOR_OWNER = "integration.omnigent.oauth_host_janitor"


def _digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _artifact_id(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized.startswith("artifact://"):
        return normalized.removeprefix("artifact://")
    return normalized


async def _write_json_artifact(
    *, name: str, artifact_type: str, payload: object
) -> str:
    from api_service.db.base import async_session_maker
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactRepository,
        TemporalArtifactService,
    )

    body = _json_bytes(payload)
    async with async_session_maker() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        artifact, _upload = await service.create(
            principal=_ARTIFACT_PRINCIPAL,
            content_type="application/json",
            size_bytes=len(body),
            metadata_json={"artifact_type": artifact_type, "name": name},
        )
        completed = await service.write_complete(
            artifact_id=artifact.artifact_id,
            principal=_ARTIFACT_PRINCIPAL,
            payload=body,
            content_type="application/json",
        )
        return str(completed.artifact_id)


async def _read_json_artifact(ref: str) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactRepository,
        TemporalArtifactService,
    )

    async with async_session_maker() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        _artifact, body = await service.read(
            artifact_id=_artifact_id(ref),
            principal=_ARTIFACT_PRINCIPAL,
            allow_restricted_raw=True,
        )
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Omnigent session artifact must contain a JSON object")
    return value


def _bind_terminal_evidence_ref(
    terminal: OmnigentSessionTerminalResult,
    evidence_ref: str,
    *,
    metadata_key: str | None = None,
) -> OmnigentSessionTerminalResult:
    """Project an externally owned evidence ref into the compact result."""

    metadata = dict(terminal.result.metadata)
    if metadata_key:
        metadata[metadata_key] = evidence_ref
    result = terminal.result.model_copy(
        update={
            "output_refs": list(
                dict.fromkeys([*terminal.result.output_refs, evidence_ref])
            ),
            "metadata": metadata,
        }
    )
    return terminal.model_copy(
        update={"result_ref": evidence_ref, "result": result}
    )


def _bind_terminal_plan_authority(
    terminal: OmnigentSessionTerminalResult,
    *,
    plan_binding: OmnigentExecutionPlanBinding | None,
    runtime_binding_state: Any | None,
) -> OmnigentSessionTerminalResult:
    """Project immutable plan and current fenced binding into a result."""

    if plan_binding is None:
        return terminal
    metadata = dict(terminal.result.metadata)
    metadata.update(
        {
            "executionPlanRef": plan_binding.plan_ref,
            "executionPlanDigest": plan_binding.plan_digest,
        }
    )
    if runtime_binding_state is not None:
        metadata.update(
            {
                "runtimeBindingRef": (
                    runtime_binding_state.binding.runtimeBindingRef
                ),
                "runtimeBindingRevision": runtime_binding_state.revision,
                "runtimeBindingFencingGeneration": (
                    runtime_binding_state.fencing_generation
                ),
                "runtimeBindingState": runtime_binding_state.state,
            }
        )
    return terminal.model_copy(
        update={
            "result": terminal.result.model_copy(update={"metadata": metadata})
        }
    )


async def omnigent_evaluate_session_admission_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze the bounded rollout decision before a new child is launched."""

    from moonmind.config.settings import settings

    request = OmnigentSessionAdmissionRequest.model_validate(payload)
    plan = None
    if request.omnigent_execution_plan is not None:
        plan = await _load_verified_execution_plan(request.omnigent_execution_plan)
        selected_profiles = {
            binding.providerProfileRef
            for binding in plan.payload.credentialBindings.values()
        }
        if request.execution_profile_ref not in selected_profiles:
            raise ValueError(
                "AgentRun Provider Profile conflicts with persisted execution plan"
            )
        from moonmind.omnigent.realizers.registry import get_default_registry

        get_default_registry().require(plan.payload.executionRealizerRef)
        _validate_plan_support_authority(plan)
        from moonmind.omnigent.session_supervisor_rollback import (
            SessionRollbackContext,
            resolve_rollback_effect,
            rollback_mode_from_settings,
        )

        rollback = resolve_rollback_effect(
            mode=rollback_mode_from_settings(settings.feature_flags),
            context=SessionRollbackContext(
                admittedViaSupervisor=True,
                recordedExecutionOwner=plan.payload.executionRealizerRef,
            ),
        )
        if not rollback.new_supervisor_admission_allowed:
            raise ValueError(
                "recorded rollback generation blocks new supervisor admission"
            )
        managed_lifecycle = (
            plan.payload.executionRealizerRef != "codex-profile-bound@1"
        )
        return OmnigentSessionAdmissionDecision(
            admitted=not managed_lifecycle,
            reasonCode=(
                "realizer_managed_lifecycle" if managed_lifecycle else "enabled"
            ),
            admissionMode="enabled",
            admittedFeatureGeneration=OMNIGENT_SESSION_FEATURE_GENERATION,
            executionRealizerRef=plan.payload.executionRealizerRef,
        ).model_dump(mode="json", by_alias=True)
    elif request.execution_plan_ref:
        from api_service.db.base import async_session_maker
        from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore

        plan = await DbExecutionPlanStore(async_session_maker).load(
            request.execution_plan_ref
        )
        if plan is None:
            raise ValueError("admitted Omnigent execution plan is unavailable")
    flags = settings.feature_flags
    mode = flags.omnigent_session_supervisor_admission_mode
    generation = str(flags.omnigent_session_supervisor_generation or "").strip()
    canary_owners = {
        item.strip()
        for item in flags.omnigent_session_supervisor_canary_owner_ids.split(",")
        if item.strip()
    }
    allowed_profiles = {
        item.strip()
        for item in (
            flags.omnigent_session_supervisor_allowed_execution_profile_refs.split(",")
        )
        if item.strip()
    }

    admitted = True
    reason = "enabled"
    effective_mode = mode
    if plan is not None:
        # Normal-product admission already persisted exact immutable authority.
        # It must not depend on a legacy rollout flag whose omission could route
        # the same request into a parallel lifecycle.
        effective_mode = "enabled"
        if plan.payload.executionRealizerRef != "codex-profile-bound@1":
            admitted = False
            reason = "realizer_managed_lifecycle"
    elif generation != OMNIGENT_SESSION_FEATURE_GENERATION:
        admitted = False
        reason = "feature_generation_mismatch"
    elif mode == "disabled":
        admitted = False
        reason = "new_selection_disabled"
    elif mode == "canary" and (
        not canary_owners or request.agent_run_id not in canary_owners
    ):
        admitted = False
        reason = "canary_owner_not_allowlisted"
    elif allowed_profiles and request.execution_profile_ref not in allowed_profiles:
        admitted = False
        reason = "execution_profile_not_allowlisted"
    elif mode == "canary":
        reason = "canary_selected"

    return OmnigentSessionAdmissionDecision(
        admitted=admitted,
        reasonCode=reason,
        admissionMode=effective_mode,
        admittedFeatureGeneration=OMNIGENT_SESSION_FEATURE_GENERATION,
    ).model_dump(mode="json", by_alias=True)


def _validate_plan_support_authority(plan: Any) -> None:
    """Validate immutable support identity against its mutable endpoint.

    Host Class and policy defaults are never re-selected here. The plan's
    qualified server build must still be the server currently deployed before
    a new runtime binding can acquire leases or launch the selected host.
    """

    from moonmind.omnigent.harness_platform.capabilities import (
        ClassAdmissionDecision,
    )
    from moonmind.omnigent.harness_platform.support import (
        compute_required_capabilities_digest,
        compute_support_combination_key,
    )

    support_identity = plan.payload.supportIdentity
    if support_identity is None:
        exact_fields = (
            plan.payload.hostImageRef,
            plan.payload.omnigentHostBuildDigest,
            plan.payload.hostArchitecture,
            plan.payload.effectiveLaunchSnapshotRef,
            plan.payload.effectiveLaunchSnapshotDigest,
        )
        if (
            plan.payload.executionRealizerRef == "codex-profile-bound@1"
            and not any(exact_fields)
        ):
            # Replay-visible Codex plans admitted before complete support
            # identity existed keep their recorded mature realizer. They are
            # never upgraded from mutable deployment defaults.
            return
        raise ValueError("execution plan lacks exact support identity")

    class_decision = ClassAdmissionDecision.model_validate(
        plan.payload.classAdmissionDecision
    )
    if class_decision.unknown:
        raise ValueError("support combination contains unknown admission evidence")
    source_body = json.dumps(
        plan.payload.agentSource,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    agent_source_ref = (
        "agent-source:sha256:" + hashlib.sha256(source_body).hexdigest()
    )
    binding_identity = plan.payload.credentialBindingSetRef.removeprefix(
        "omnigent-credential-bindings:"
    ).split("@", 1)[0]
    if compute_support_combination_key(support_identity) != (
        plan.payload.supportCombinationKey
    ):
        raise ValueError("persisted support-combination evidence is mismatched")
    expected_identity = {
        "omnigentHostBuildRef": plan.payload.omnigentHostBuildDigest,
        "harnessImplementationRef": plan.payload.harnessImplementationRef,
        "agentSourceRef": agent_source_ref,
        "materializerRefs": tuple(
            sorted(
                value.materializerRef
                for value in plan.payload.credentialBindings.values()
            )
        ),
        "providerCompatibilityClass": binding_identity,
        "hostClassRef": plan.payload.hostClassRef,
        "architecture": plan.payload.hostArchitecture,
        "launchPolicyRef": plan.payload.launchPolicyRef,
        "modelConfigDigest": plan.payload.modelConfig.modelConfigDigest,
        "executionRealizerRef": plan.payload.executionRealizerRef,
        "requiredCapabilitiesDigest": compute_required_capabilities_digest(
            list(class_decision.requiredSatisfied)
        ),
    }
    mismatched = [
        name
        for name, expected in expected_identity.items()
        if getattr(support_identity, name) != expected
    ]
    if mismatched:
        raise ValueError(
            "persisted support identity conflicts with execution authority: "
            + ", ".join(sorted(mismatched))
        )
    if (
        not plan.payload.hostImageRef
        or "@sha256:" not in plan.payload.hostImageRef
        or not plan.payload.effectiveLaunchSnapshotRef
        or not plan.payload.effectiveLaunchSnapshotDigest
    ):
        raise ValueError("persisted exact-artifact evidence is incomplete")
    from moonmind.omnigent.deployment_identity import (
        assert_plan_matches_deployed_server,
    )

    assert_plan_matches_deployed_server(plan.payload)


async def _load_intent_request(
    request: OmnigentSessionActivityRequest | Mapping[str, Any],
) -> AgentExecutionRequest:
    parsed = (
        request
        if isinstance(request, OmnigentSessionActivityRequest)
        else OmnigentSessionActivityRequest.model_validate(request)
    )
    payload = await _read_json_artifact(parsed.compiled_execution_intent_ref)
    body = _json_bytes(payload)
    if _digest_bytes(body) != parsed.compiled_execution_intent_digest:
        raise ValueError("agent execution request snapshot digest mismatch")
    raw_request = payload.get("request")
    if isinstance(raw_request, Mapping):
        # Replay path for request snapshots created before the compact
        # plan/task-snapshot contract. New snapshots never persist authored
        # request bodies here.
        agent_request = AgentExecutionRequest.model_validate(raw_request)
    else:
        binding_payload = payload.get("omnigentExecutionPlan")
        if not isinstance(binding_payload, Mapping):
            raise ValueError("intent snapshot lacks persisted execution-plan authority")
        binding_from_intent = OmnigentExecutionPlanBinding.model_validate(
            binding_payload
        )
        plan = await _load_verified_execution_plan(binding_from_intent)
        agent_request = await _reconstruct_plan_bound_request(
            binding=binding_from_intent,
            plan=plan,
            workflow_id=str(payload.get("workflowId") or ""),
            step_execution_id=str(payload.get("stepExecutionId") or ""),
            agent_run_id=str(payload.get("agentRunId") or ""),
            logical_step_id=(
                str(payload.get("logicalStepId") or "").strip() or None
            ),
            execution_instruction_ref=(
                str(payload.get("executionInstructionRef") or "").strip() or None
            ),
            execution_instruction_digest=(
                str(payload.get("executionInstructionDigest") or "").strip()
                or None
            ),
            execution_input_refs=list(payload.get("executionInputRefs") or []),
            execution_input_refs_digest=(
                str(payload.get("executionInputRefsDigest") or "").strip() or None
            ),
        )
    binding = parsed.omnigent_execution_plan or agent_request.omnigent_execution_plan
    if binding is not None:
        if agent_request.omnigent_execution_plan != binding:
            raise ValueError("request snapshot execution-plan authority mismatch")
        plan = await _load_verified_execution_plan(binding)
        agent_request = _bind_request_to_execution_plan(agent_request, plan)
    return agent_request


async def _reconstruct_plan_bound_request(
    *,
    binding: OmnigentExecutionPlanBinding,
    plan: Any,
    workflow_id: str,
    step_execution_id: str,
    agent_run_id: str,
    logical_step_id: str | None = None,
    execution_instruction_ref: str | None = None,
    execution_instruction_digest: str | None = None,
    execution_input_refs: list[str] | None = None,
    execution_input_refs_digest: str | None = None,
) -> AgentExecutionRequest:
    """Reload authored input and project it through immutable plan authority."""

    snapshot = await _read_json_artifact(binding.task_input_snapshot_ref)
    if _digest_bytes(_json_bytes(snapshot)) != binding.task_input_snapshot_digest:
        raise ValueError("task-input snapshot digest conflicts with execution plan")
    authority = plan.payload.authority
    if authority is None or (
        authority.taskInputSnapshotRef != binding.task_input_snapshot_ref
        or authority.taskInputSnapshotDigest != binding.task_input_snapshot_digest
    ):
        raise ValueError("task-input snapshot is not owned by the execution plan")

    draft = snapshot.get("draft")
    target = snapshot.get("target")
    if isinstance(draft, Mapping):
        workflow = dict(draft.get("workflow") or {})
        repository = str(draft.get("repository") or "").strip()
        required_capabilities = list(draft.get("requiredCapabilities") or [])
        authored_parameters: dict[str, Any] = {
            "repository": repository,
            "targetRuntime": str(draft.get("targetRuntime") or "omnigent"),
            "requiredCapabilities": required_capabilities,
            "workflow": workflow,
        }
    elif isinstance(target, Mapping):
        raw_parameters = target.get("initialParameters")
        if not isinstance(raw_parameters, Mapping):
            raise ValueError("scheduled task snapshot lacks authored parameters")
        authored_parameters = dict(raw_parameters)
        workflow_value = authored_parameters.get("workflow")
        workflow = (
            dict(workflow_value) if isinstance(workflow_value, Mapping) else {}
        )
        repository = str(authored_parameters.get("repository") or "").strip()
        required_capabilities = list(
            authored_parameters.get("requiredCapabilities") or []
        )
    else:
        raise ValueError("task-input snapshot has an unsupported authority shape")

    selected_workflow = workflow
    steps = workflow.get("steps")
    if logical_step_id and isinstance(steps, list):
        matches = [
            item
            for item in steps
            if isinstance(item, Mapping)
            and str(item.get("id") or "").strip() == logical_step_id
        ]
        if len(matches) != 1:
            raise ValueError(
                "task-input snapshot lacks the selected logical Step authority"
            )
        selected_workflow = dict(matches[0])
    instruction = str(selected_workflow.get("instructions") or "").strip()
    if not instruction:
        instruction = str(workflow.get("instructions") or "").strip()
    if not instruction:
        raise ValueError("task-input snapshot lacks authored execution instructions")
    if execution_instruction_ref is not None:
        if _digest_bytes(execution_instruction_ref.encode("utf-8")) != (
            execution_instruction_digest
        ):
            raise ValueError("execution instruction digest mismatch")
        instruction = execution_instruction_ref

    profile_refs = {
        item.providerProfileRef
        for item in plan.payload.credentialBindings.values()
    }
    if len(profile_refs) != 1:
        raise ValueError("execution plan has ambiguous Provider Profile authority")
    resolved_skillset_ref = str(
        plan.payload.resolvedSkills.get("resolvedSkillSetRef") or ""
    ).removeprefix("artifact:")
    registration = find_harness_registration(plan.payload.harnessId)
    if registration is None:
        raise ValueError("execution plan harness lacks a product execution target")
    execution_target = registration.executionTargetRef
    agent_source = plan.payload.agentSource
    source_kind = str(agent_source.get("kind") or "").strip()
    if source_kind == "upstream":
        planned_agent_id = str(agent_source.get("upstreamId") or "").strip()
    elif source_kind == "bundle":
        planned_agent_id = str(agent_source.get("importedAgentId") or "").strip()
    else:
        raise ValueError("execution plan has an unsupported Agent source kind")
    if not planned_agent_id:
        raise ValueError("execution plan Agent source identity is unavailable")

    model_config = plan.payload.modelConfig
    planned_model = str(model_config.qualifiedId or "").strip() or None
    planned_effort = str(model_config.effort or "").strip() or None
    for field, planned in (("model", planned_model), ("effort", planned_effort)):
        authored = str(authored_parameters.get(field) or "").strip() or None
        if authored is not None and authored != planned:
            raise ValueError(
                f"authored {field} conflicts with persisted execution plan"
            )
        if planned is not None:
            authored_parameters[field] = planned
    omnigent = dict(authored_parameters.get("omnigent") or {})
    session_parameters = dict(omnigent.get("session") or {})
    for field, planned in (
        ("modelOverride", planned_model),
        ("reasoningEffort", planned_effort),
    ):
        authored = str(session_parameters.get(field) or "").strip() or None
        if authored is not None and authored != planned:
            raise ValueError(
                f"authored Omnigent session {field} conflicts with persisted "
                "execution plan"
            )
        if planned is not None:
            session_parameters[field] = planned
    omnigent.update(
        {
            "executionTargetRef": execution_target,
            "launchPolicyRef": plan.payload.launchPolicyRef,
            "agent": {
                **dict(omnigent.get("agent") or {}),
                "agentId": planned_agent_id,
                "harnessOverride": plan.payload.harnessId,
            },
            "session": {
                "hostType": "managed",
                "allowEmptyWorkspace": True,
                **session_parameters,
            },
        }
    )
    authored_parameters.update(
        {
            "repository": repository,
            "targetRuntime": "omnigent",
            "requiredCapabilities": required_capabilities,
            "workflow": workflow,
            "omnigent": omnigent,
        }
    )
    publish = workflow.get("publish")
    if isinstance(publish, Mapping) and publish.get("mode"):
        authored_parameters["publishMode"] = publish["mode"]
    attachment_refs: list[str] = []
    for values in (
        snapshot.get("attachmentRefs") or [],
        workflow.get("inputAttachments") or [],
        selected_workflow.get("inputAttachments") or [],
    ):
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, Mapping):
                continue
            ref = str(
                item.get("artifactRef")
                or item.get("artifactId")
                or item.get("ref")
                or ""
            ).strip()
            if ref and ref not in attachment_refs:
                attachment_refs.append(ref)
    workspace_spec = (
        dict(workflow.get("workspace") or {})
        if isinstance(workflow.get("workspace"), Mapping)
        else {}
    )
    git_authority = workflow.get("git")
    if isinstance(git_authority, Mapping):
        for key in ("startingBranch", "targetBranch"):
            value = str(git_authority.get(key) or "").strip()
            if value:
                workspace_spec[key] = value
    execution_refs = [
        str(item).strip()
        for item in (execution_input_refs or [])
        if str(item).strip()
    ]
    if execution_refs:
        if _digest_bytes(
            json.dumps(
                execution_refs, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ) != execution_input_refs_digest:
            raise ValueError("execution input refs digest mismatch")
        for ref in execution_refs:
            if ref not in attachment_refs:
                attachment_refs.append(ref)
    if "workspaceLocator" not in workspace_spec:
        if not workflow_id or not step_execution_id:
            raise ValueError(
                "compact plan handoff lacks workspace owner identity"
            )
        workspace_id = hashlib.sha256(
            f"{workflow_id}:{step_execution_id}".encode("utf-8")
        ).hexdigest()[:24]
        workspace_spec["workspaceLocator"] = {
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        }
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=next(iter(profile_refs)),
        omnigentExecutionPlan=binding,
        correlationId=workflow_id,
        # The Step Execution is the durable side-effect/idempotency owner. It
        # also owns the sandbox workspace locator reconstructed above; using an
        # AgentRun-local synthetic key here would make the production workspace
        # resolver compute a different owner identity.
        idempotencyKey=step_execution_id,
        instructionRef=instruction,
        resolvedSkillsetRef=resolved_skillset_ref,
        inputRefs=[ref for ref in attachment_refs if ref],
        workspaceSpec=workspace_spec,
        parameters=authored_parameters,
        timeoutPolicy=(
            dict(workflow.get("timeoutPolicy") or {})
            if isinstance(workflow.get("timeoutPolicy"), Mapping)
            else {}
        ),
        retryPolicy=(
            dict(workflow.get("retryPolicy") or {})
            if isinstance(workflow.get("retryPolicy"), Mapping)
            else {}
        ),
    )


def _bind_request_to_execution_plan(
    request: AgentExecutionRequest,
    plan: Any,
) -> AgentExecutionRequest:
    """Apply immutable plan refs without re-resolving current defaults."""

    selected_profiles = {
        binding.providerProfileRef
        for binding in plan.payload.credentialBindings.values()
    }
    if request.execution_profile_ref not in selected_profiles:
        raise ValueError(
            "AgentRun Provider Profile conflicts with persisted execution plan"
        )
    planned_skill_ref = str(
        plan.payload.resolvedSkills.get("resolvedSkillSetRef") or ""
    ).strip()
    if planned_skill_ref.startswith("artifact:"):
        planned_skill_ref = planned_skill_ref.removeprefix("artifact:")
    if not planned_skill_ref:
        raise ValueError("persisted execution plan lacks Skill snapshot authority")
    if (
        request.resolved_skillset_ref is not None
        and request.resolved_skillset_ref != planned_skill_ref
    ):
        raise ValueError("AgentRun Skill snapshot conflicts with execution plan")
    if request.resolved_skillset_ref == planned_skill_ref:
        return request
    # Compatibility-safe for an already-persisted request snapshot created
    # before resolvedSkillsetRef was projected into AgentRun. The immutable plan
    # remains authoritative; no source is re-resolved.
    return request.model_copy(
        update={"resolved_skillset_ref": planned_skill_ref}
    )


async def _validate_plan_admission_authority(persisted: Any) -> None:
    """Load and validate support, replay, and rollback evidence from the plan."""

    authority = persisted.payload.authority
    admission_authority = persisted.payload.admissionAuthority
    if authority is not None and admission_authority is None:
        if persisted.payload.executionRealizerRef == "codex-profile-bound@1":
            # Product plans persisted before admission-evidence authority was
            # introduced keep their recorded Codex coordinator. They cannot be
            # switched to the generic realizer, and new compilation always
            # emits the evidence below.
            return
        raise ValueError("execution plan lacks persisted admission evidence")
    if admission_authority is None:
        return

    from moonmind.omnigent.session_supervisor_rollback import (
        SUPERVISOR_ROLLBACK_POLICY_VERSION,
    )

    if (
        admission_authority.featureGeneration
        != OMNIGENT_SESSION_FEATURE_GENERATION
    ):
        raise ValueError("execution plan feature generation is unsupported")
    if (
        admission_authority.replayCompatibilityVersion
        != OMNIGENT_SESSION_COMPATIBILITY_VERSION
    ):
        raise ValueError("execution plan replay compatibility is unsupported")
    if (
        admission_authority.rollbackPolicyVersion
        != SUPERVISOR_ROLLBACK_POLICY_VERSION
    ):
        raise ValueError("execution plan rollback policy is unsupported")
    support_ref = admission_authority.supportEvidenceRef.removeprefix(
        "artifact:"
    )
    support_evidence = await _read_json_artifact(support_ref)
    if _digest_bytes(_json_bytes(support_evidence)) != (
        admission_authority.supportEvidenceDigest
    ):
        raise ValueError("execution support evidence digest conflicts with the plan")
    if admission_authority.supportTier == "deployment_qualified":
        # Deployment-qualified plans carry locally-signed deployment evidence;
        # validating it against the protected schema would fail closed on the
        # very evidence admission accepted.
        from moonmind.omnigent.deployment_evidence import (
            assert_deployment_evidence_matches_plan,
            validate_deployment_evidence,
        )

        deployment_evidence = validate_deployment_evidence(support_evidence)
        assert_deployment_evidence_matches_plan(
            deployment_evidence, persisted.payload
        )
        return
    from moonmind.omnigent.execution_support_evidence import (
        assert_protected_evidence_matches_plan,
        validate_protected_execution_support_evidence,
    )

    protected_evidence = validate_protected_execution_support_evidence(
        support_evidence,
        expected_source_commit=(
            os.getenv("MOONMIND_SOURCE_COMMIT", "").strip() or None
        ),
    )
    assert_protected_evidence_matches_plan(protected_evidence, persisted.payload)


async def _load_verified_execution_plan(binding: OmnigentExecutionPlanBinding):
    """Load the DB and artifact copies and verify one exact plan envelope."""

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.harness_platform.execution_plan import (
        verify_execution_plan_envelope,
    )
    from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore

    persisted = await DbExecutionPlanStore(async_session_maker).load(binding.plan_ref)
    if persisted is None:
        raise ValueError("persisted Omnigent execution plan is unavailable")
    expected_digest = "sha256:" + persisted.planRef.rsplit(":", 1)[-1]
    if expected_digest != binding.plan_digest:
        raise ValueError("persisted Omnigent execution plan digest mismatch")
    artifact_payload = await _read_json_artifact(binding.plan_artifact_ref)
    artifact_plan = verify_execution_plan_envelope(artifact_payload)
    if artifact_plan != persisted:
        raise ValueError("execution plan artifact conflicts with durable plan authority")
    authority = persisted.payload.authority
    if authority is not None and (
        authority.taskInputSnapshotRef != binding.task_input_snapshot_ref
        or authority.taskInputSnapshotDigest
        != binding.task_input_snapshot_digest
    ):
        raise ValueError(
            "execution plan binding conflicts with task-input snapshot authority"
        )
    await _validate_plan_admission_authority(persisted)
    profile_ref = str(persisted.payload.agentProfileSnapshotRef or "").strip()
    if not profile_ref.startswith("artifact:"):
        raise ValueError("execution plan lacks Agent Profile artifact authority")
    profile_snapshot = await _read_json_artifact(
        profile_ref.removeprefix("artifact:")
    )
    profile_document = profile_snapshot.get("document")
    if not isinstance(profile_document, Mapping):
        raise ValueError("Agent Profile snapshot artifact is invalid")
    planned_profiles = {
        value.providerProfileRef
        for value in persisted.payload.credentialBindings.values()
    }
    if str(profile_snapshot.get("providerProfileRef") or "") not in planned_profiles:
        raise ValueError("Agent Profile artifact conflicts with Provider Profile plan")
    raw_snapshot_harness = profile_document.get("harness")
    if isinstance(raw_snapshot_harness, Mapping):
        # Generic (v2) documents carry the harness as an object with its id.
        raw_snapshot_harness = raw_snapshot_harness.get("id")
    snapshot_harness = canonical_harness_id(raw_snapshot_harness)
    if snapshot_harness != persisted.payload.harnessId:
        raise ValueError("Agent Profile artifact conflicts with planned harness")
    profile_source = profile_document.get("source")
    if not isinstance(profile_source, Mapping):
        raise ValueError("Agent Profile artifact lacks source authority")
    planned_source = persisted.payload.agentSource
    if planned_source.get("kind") == "upstream":
        if (
            str(profile_source.get("upstreamId") or "")
            != str(planned_source.get("upstreamId") or "")
            or str(profile_source.get("upstreamVersion") or "0.0.0")
            != str(planned_source.get("upstreamVersion") or "")
            or str(profile_snapshot.get("digest") or "")
            != str(planned_source.get("upstreamSnapshotDigest") or "")
        ):
            raise ValueError(
                "Agent Profile artifact conflicts with planned source identity"
            )
    elif planned_source.get("kind") == "bundle":
        if (
            str(profile_source.get("bundleArtifactRef") or "")
            != str(planned_source.get("bundleArtifactRef") or "")
            or str(profile_source.get("bundleDigest") or "")
            != str(planned_source.get("bundleDigest") or "")
            or str(profile_snapshot.get("digest") or "")
            != str(planned_source.get("importedContentDigest") or "")
        ):
            raise ValueError(
                "Agent Profile artifact conflicts with planned source identity"
            )
    else:
        raise ValueError("execution plan contains unsupported agent source authority")

    if persisted.payload.policySnapshotDigest is None:
        # Replay path for v1 plans created before exact launch snapshots were
        # artifact-backed.  These plans pinned the Agent Profile policy ref.
        if (
            str(profile_snapshot.get("policyRef") or "")
            != persisted.payload.policySnapshotRef
        ):
            raise ValueError("Agent Profile artifact conflicts with planned policy")
    else:
        policy_ref = str(persisted.payload.policySnapshotRef or "").strip()
        if not policy_ref.startswith("artifact:"):
            raise ValueError("execution plan lacks launch-policy artifact authority")
        policy_snapshot = await _read_json_artifact(
            policy_ref.removeprefix("artifact:")
        )
        if _digest_bytes(_json_bytes(policy_snapshot)) != (
            persisted.payload.policySnapshotDigest
        ):
            raise ValueError("launch-policy artifact digest conflicts with the plan")
        if str(policy_snapshot.get("policyRef") or "") != (
            persisted.payload.launchPolicyRef
        ):
            raise ValueError("launch-policy artifact conflicts with the plan")
        from moonmind.omnigent.policies import compile_policy_snapshot

        verified_policy = compile_policy_snapshot(
            policy_id=str(policy_snapshot.get("policyId") or ""),
            version=int(policy_snapshot.get("policyVersion") or 0),
            document=policy_snapshot.get("boundaries") or {},
            validation=policy_snapshot.get("validation") or {},
        )
        if verified_policy.get("snapshotRef") != policy_snapshot.get("snapshotRef"):
            raise ValueError("launch-policy snapshot identity is invalid")

        effective_ref = str(
            persisted.payload.effectiveLaunchSnapshotRef or ""
        ).strip()
        if not effective_ref.startswith("artifact:"):
            raise ValueError("execution plan lacks effective-launch artifact authority")
        effective_launch = await _read_json_artifact(
            effective_ref.removeprefix("artifact:")
        )
        if _digest_bytes(_json_bytes(effective_launch)) != (
            persisted.payload.effectiveLaunchSnapshotDigest
        ):
            raise ValueError("effective-launch artifact digest conflicts with the plan")
        effective_identity = dict(effective_launch)
        recorded_effective_ref = str(
            effective_identity.pop("snapshotRef", "") or ""
        )
        expected_effective_ref = "omnigent-launch:sha256:" + hashlib.sha256(
            json.dumps(
                effective_identity, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        if recorded_effective_ref != expected_effective_ref:
            raise ValueError("effective-launch snapshot identity is invalid")
        effective_architectures = effective_launch.get("architectures") or []
        effective_architecture = str(
            effective_architectures[0] if effective_architectures else ""
        )
        if effective_architecture and "/" not in effective_architecture:
            effective_architecture = f"linux/{effective_architecture}"
        if (
            str(effective_launch.get("launchPolicyRef") or "")
            != persisted.payload.launchPolicyRef
            or str(effective_launch.get("executionProfileRef") or "")
            != str(profile_snapshot.get("executionProfileRef") or "")
            or str(effective_launch.get("harness") or "")
            != persisted.payload.harnessId
            or str(effective_launch.get("hostImageRef") or "")
            != persisted.payload.hostImageRef
            or effective_architecture != persisted.payload.hostArchitecture
        ):
            raise ValueError("effective-launch artifact conflicts with the plan")

    planned_skills = persisted.payload.resolvedSkills
    skill_ref = str(planned_skills.get("resolvedSkillSetRef") or "").strip()
    if not skill_ref.startswith("artifact:"):
        raise ValueError("execution plan lacks resolved Skill artifact authority")
    skill_manifest = await _read_json_artifact(skill_ref.removeprefix("artifact:"))
    skill_manifest_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            skill_manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if skill_manifest_digest != str(
        planned_skills.get("resolvedSkillSetDigest") or ""
    ):
        raise ValueError("resolved Skill artifact digest conflicts with the plan")
    return persisted


async def _load_current_runtime_binding(
    *,
    execution_plan_ref: str,
    execution_scope_ref: str,
    recorded_runtime_binding_ref: str | None,
):
    """Resolve the current binding after a crash between CAS and projection."""

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.harness_platform.stores import DbRuntimeBindingStore

    store = DbRuntimeBindingStore(async_session_maker)
    state = (
        await store.get_state(recorded_runtime_binding_ref)
        if recorded_runtime_binding_ref
        else None
    )
    if state is None:
        state = await store.get_current_state(
            execution_plan_ref, execution_scope_ref
        )
    if state is None:
        raise ValueError("runtime binding authority is unavailable")
    if state.binding.executionPlanRef != execution_plan_ref:
        raise ValueError("runtime binding belongs to a different execution plan")
    if (
        state.binding.executionScopeRef is not None
        and state.binding.executionScopeRef != execution_scope_ref
    ):
        raise ValueError("runtime binding payload belongs to a different execution scope")
    if (
        state.execution_scope_ref
        and state.execution_scope_ref != execution_scope_ref
    ):
        raise ValueError("runtime binding belongs to a different execution scope")
    return store, state


async def _project_runtime_binding_to_execution(
    *, workflow_id: str, state: Any
) -> None:
    """Publish a safe, monotonic binding summary to Workflow Detail."""

    from api_service.db.base import async_session_maker
    from api_service.db.models import (
        TemporalExecutionCanonicalRecord,
        TemporalExecutionRecord,
    )

    if not workflow_id:
        raise ValueError("runtime-binding projection requires workflow authority")
    async with async_session_maker() as db:
        for model in (TemporalExecutionCanonicalRecord, TemporalExecutionRecord):
            execution = await db.get(model, workflow_id)
            if execution is None:
                continue
            memo = dict(execution.memo or {})
            projected_revision = int(
                memo.get("omnigent_runtime_binding_revision") or 0
            )
            if projected_revision > state.revision:
                raise ValueError(
                    "execution runtime-binding projection is ahead of authority"
                )
            if projected_revision == state.revision:
                projected_ref = str(
                    memo.get("omnigent_runtime_binding_ref") or ""
                )
                if projected_ref and projected_ref != state.binding.runtimeBindingRef:
                    raise ValueError(
                        "execution has conflicting runtime binding at same revision"
                    )
            memo.update(
                {
                    "omnigent_runtime_binding_ref": (
                        state.binding.runtimeBindingRef
                    ),
                    "omnigent_runtime_binding_revision": state.revision,
                    "omnigent_runtime_binding_fencing_generation": (
                        state.fencing_generation
                    ),
                    "omnigent_runtime_binding_state": state.state,
                }
            )
            execution.memo = memo
        await db.commit()


def _bounded_model_option_ids(payload: Mapping[str, Any]) -> list[str]:
    """Extract only bounded model identities from an untrusted host response."""

    candidates: list[Any] = []
    for key in ("models", "modelOptions", "options", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.extend(value[:256])
        elif isinstance(value, Mapping):
            candidates.extend(list(value.keys())[:256])
    identities: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            candidate = (
                candidate.get("qualifiedId")
                or candidate.get("id")
                or candidate.get("model")
                or candidate.get("name")
            )
        identity = str(candidate or "").strip()
        if identity and len(identity) <= 255 and identity not in identities:
            identities.append(identity)
    return identities


def _bounded_workspace_evidence(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {"status": "resolved"}
    forbidden_fragments = ("path", "root", "volume", "socket", "token", "secret")
    return {
        str(key): value
        for key, value in payload.items()
        if not any(fragment in str(key).lower() for fragment in forbidden_fragments)
        and isinstance(value, (str, int, float, bool, type(None)))
        and len(str(value)) <= 512
    }


async def _persist_host_runtime_evidence(
    *,
    request: AgentExecutionRequest,
    plan: Any,
    preflight: Mapping[str, Any],
    model_options: Mapping[str, Any],
    host_lease_generation: int,
) -> dict[str, str | list[str]]:
    """Validate and persist exact-host evidence before binding advancement."""

    from moonmind.omnigent.harness_platform.attestation import (
        HostHarnessAttestation,
        validate_exact_host_attestation,
    )
    from moonmind.omnigent.harness_platform.capabilities import (
        ClassAdmissionDecision,
        validate_exact_host_capabilities,
    )
    from moonmind.omnigent.harness_platform.catalog import (
        HarnessImplementationIdentity,
    )
    from moonmind.omnigent.harness_platform.failures import (
        HarnessPlatformError,
        HarnessPlatformFailure,
    )
    from moonmind.omnigent.harness_platform.skills import (
        ResolvedSkillSet,
        assert_skill_delivery_attestation,
    )

    support_identity = plan.payload.supportIdentity
    registration = preflight.get("hostRegistrationEvidence")
    if not isinstance(registration, Mapping):
        raise HarnessPlatformError(
            "exact host registration evidence is unavailable",
            code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
        )

    def observed(*keys: str) -> Any:
        for key in keys:
            value = registration.get(key)
            if value is not None:
                return value
        return None

    host_id = str(preflight.get("hostId") or "").strip()
    implementation_payload = observed(
        "harnessImplementation", "harness_implementation"
    )
    if not isinstance(implementation_payload, Mapping):
        raise HarnessPlatformError(
            "exact host omitted harness implementation identity",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    implementation = HarnessImplementationIdentity.model_validate(
        dict(implementation_payload)
    )
    if implementation.implementation_ref() != plan.payload.harnessImplementationRef:
        raise HarnessPlatformError(
            "exact host harness implementation conflicts with the plan",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    raw_capabilities = registration.get("capabilities")
    capabilities = (
        {
            str(key): value
            for key, value in raw_capabilities.items()
            if isinstance(value, (str, int, float, bool, type(None)))
        }
        if isinstance(raw_capabilities, Mapping)
        else {}
    )
    # Derived substrate attestations may augment the registered host's bounded
    # capability projection, but only when the owning runtime reports that its
    # checks completed.  Never invent positive capability evidence here.
    if preflight.get("workspaceMountAttested") is True:
        capabilities["workspace.bind"] = True
    if preflight.get("restrictedEgressAttested") is True:
        capabilities["restricted-egress"] = True
    attestation = HostHarnessAttestation.model_validate(
        {
            "hostId": host_id,
            "hostClassRef": plan.payload.hostClassRef,
            "hostImageRef": observed("imageRef", "image_ref")
            or (preflight.get("egressAttestation") or {}).get("hostImageRef"),
            "omnigentVersion": observed(
                "omnigentVersion", "omnigent_version"
            ),
            "omnigentBuildDigest": observed(
                "omnigentBuildDigest", "omnigent_build_digest"
            ),
            "harnessId": plan.payload.harnessId,
            "harnessImplementation": implementation.model_dump(
                mode="json", by_alias=True
            ),
            "runtimeDependencies": observed(
                "runtimeDependencies", "runtime_dependencies"
            )
            or [],
            "configured": True,
            "capabilities": capabilities,
            "architecture": registration.get("architecture"),
            "attestationGeneration": host_lease_generation,
            "observedAt": datetime.now(UTC),
        }
    )
    expected_vendor_runtimes: list[dict[str, str]] = []
    if support_identity is not None:
        for runtime_ref in support_identity.vendorRuntimeRefs:
            identity, separator, digest = str(runtime_ref).rpartition("#")
            name, version_separator, version = identity.rpartition("@")
            if not separator or not version_separator:
                raise HarnessPlatformError(
                    "planned vendor runtime identity is malformed",
                    code=HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
                )
            expected_vendor_runtimes.append(
                {"name": name, "version": version, "digest": digest}
            )
    required_capabilities = list(
        plan.payload.classAdmissionDecision.get("requiredSatisfied") or []
    )
    validate_exact_host_attestation(
        attestation,
        expectedHostClassRef=plan.payload.hostClassRef,
        expectedImageRef=(
            plan.payload.hostImageRef or attestation.hostImageRef
        ),
        expectedOmnigentBuildDigest=(
            plan.payload.omnigentHostBuildDigest
            or attestation.omnigentBuildDigest
        ),
        expectedHarnessId=plan.payload.harnessId,
        expectedImplementation=implementation.model_dump(
            mode="json", by_alias=True
        ),
        expectedVendorRuntimes=expected_vendor_runtimes,
        requiredCapabilities=required_capabilities,
        expectedArchitecture=(
            plan.payload.hostArchitecture
            or attestation.architecture
        ),
        expectedHostId=host_id,
        currentHostLeaseGeneration=host_lease_generation,
    )
    host_attestation_ref = await _write_json_artifact(
        name="omnigent.host-harness-attestation.json",
        artifact_type="omnigent.host_harness_attestation",
        payload=attestation.model_dump(mode="json", by_alias=True),
    )

    model_ids = _bounded_model_option_ids(model_options)
    selected_model = str(plan.payload.modelConfig.qualifiedId or "").strip()
    if selected_model and selected_model not in model_ids:
        raise HarnessPlatformError(
            f"planned model {selected_model!r} is unavailable on the exact host",
            code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
        )
    model_attestation_ref = await _write_json_artifact(
        name="omnigent.model-option-attestation.json",
        artifact_type="omnigent.model_option_attestation",
        payload={
            "schemaVersion": "moonmind.omnigent-model-option-attestation.v1",
            "hostId": host_id,
            "selectedModel": selected_model or None,
            "availableModelIds": model_ids,
            "modelConfigDigest": plan.payload.modelConfig.modelConfigDigest,
            "observedAt": datetime.now(UTC).isoformat(),
        },
    )

    planned_skills = ResolvedSkillSet.model_validate(plan.payload.resolvedSkills)
    actual_skill_ref = str(preflight.get("resolvedSkillsetRef") or "").strip()
    if planned_skills.resolvedSkillSetRef != f"artifact:{actual_skill_ref}":
        raise HarnessPlatformError(
            "exact host Skill projection conflicts with the execution plan",
            code=HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH,
        )
    if preflight.get("skillDeliveryAttested") is not True:
        raise HarnessPlatformError(
            "exact host omitted Skill delivery attestation",
            code=HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH,
        )
    assert_skill_delivery_attestation(
        planned=planned_skills,
        attested_delivery_ref=planned_skills.skillDeliveryRef,
        attested_digest=planned_skills.resolvedSkillSetDigest,
    )
    skill_attestation_ref = await _write_json_artifact(
        name="omnigent.skill-delivery-attestation.json",
        artifact_type="omnigent.skill_delivery_attestation",
        payload={
            "schemaVersion": "moonmind.omnigent-skill-delivery-attestation.v1",
            "hostId": host_id,
            "resolvedSkillSetRef": planned_skills.resolvedSkillSetRef,
            "resolvedSkillSetDigest": planned_skills.resolvedSkillSetDigest,
            "skillDeliveryRef": planned_skills.skillDeliveryRef,
        },
    )
    workspace_ref = await _write_json_artifact(
        name="omnigent.workspace-resolution.json",
        artifact_type="omnigent.workspace_resolution",
        payload={
            "schemaVersion": "moonmind.omnigent-workspace-resolution.v1",
            "workspaceIntentRef": plan.payload.workspaceIntentRef,
            "evidence": _bounded_workspace_evidence(
                preflight.get("workspaceResolution")
            ),
        },
    )
    exact_decision = validate_exact_host_capabilities(
        class_decision=ClassAdmissionDecision.model_validate(
            plan.payload.classAdmissionDecision
        ),
        attestation_capabilities={
            str(key): value is True for key, value in capabilities.items()
        },
        mount_attested=preflight.get("workspaceMountAttested") is True,
        network_attested=preflight.get("restrictedEgressAttested") is True,
        model_attested=(not selected_model or selected_model in model_ids),
        required_capabilities=required_capabilities,
    )
    exact_decision_ref = await _write_json_artifact(
        name="omnigent.exact-host-capability-decision.json",
        artifact_type="omnigent.exact_host_capability_decision",
        payload=exact_decision.model_dump(mode="json", by_alias=True),
    )
    cleanup_refs = [
        str(value)
        for value in (preflight.get("egressEvidenceRef"),)
        if str(value or "").strip()
    ]
    return {
        "host_harness_attestation_ref": host_attestation_ref,
        "exact_host_capability_decision_ref": exact_decision_ref,
        "workspace_resolution_ref": workspace_ref,
        "model_option_attestation_ref": model_attestation_ref,
        "skill_delivery_attestation_ref": skill_attestation_ref,
        "cleanup_authority_refs": cleanup_refs,
    }


async def _session_execution_authority_metadata(session: Any) -> dict[str, Any]:
    """Project exact admitted/runtime authority into every terminal result."""

    plan_ref = str(getattr(session, "execution_plan_ref", None) or "").strip()
    if not plan_ref:
        return {}
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.harness_platform.execution_plan import (
        execution_support_identity,
    )
    from moonmind.omnigent.harness_platform.stores import (
        DbExecutionPlanStore,
    )
    from moonmind.omnigent.runtime_bindings import DbRuntimeBindingStore

    plan = await DbExecutionPlanStore(async_session_maker).load(plan_ref)
    if plan is None:
        raise ValueError("canonical session execution plan is unavailable")
    metadata = {
        "executionPlanRef": plan.planRef,
        "supportCombinationIdentity": execution_support_identity(plan),
    }
    binding_ref = str(getattr(session, "runtime_binding_ref", None) or "").strip()
    if binding_ref:
        binding = await DbRuntimeBindingStore(async_session_maker).get(binding_ref)
        if binding is None or binding.executionPlanRef != plan.planRef:
            raise ValueError(
                "canonical session runtime binding conflicts with its execution plan"
            )
        metadata["runtimeBindingRef"] = binding.bindingId
    return metadata


def _omnigent_intent_snapshot_payload(
    *,
    resolved: OmnigentResolveIntentRequest,
    request: AgentExecutionRequest,
    session_id: str,
) -> tuple[dict[str, Any], str, str]:
    """Build the replay-versioned intent snapshot and artifact identity."""

    identity = {
        "sessionId": session_id,
        "workflowId": resolved.workflow_id,
        "stepExecutionId": resolved.step_execution_id,
        "agentRunId": resolved.agent_run_id,
    }
    if resolved.request is not None:
        return (
            {
                "schemaVersion": "omnigent-compiled-execution-intent/v1",
                **identity,
                "request": request.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                ),
            },
            "omnigent.compiled-execution-intent.json",
            "omnigent.compiled_execution_intent",
        )

    plan_binding = request.omnigent_execution_plan
    if plan_binding is None:
        raise ValueError("plan-bound intent snapshot lacks persisted plan authority")
    return (
        {
            "schemaVersion": "omnigent-plan-bound-intent-snapshot/v1",
            **identity,
            **(
                {"logicalStepId": resolved.logical_step_id}
                if resolved.logical_step_id is not None
                else {}
            ),
            **(
                {
                    "executionInstructionRef": resolved.execution_instruction_ref,
                    "executionInstructionDigest": resolved.execution_instruction_digest,
                }
                if resolved.execution_instruction_ref is not None
                else {}
            ),
            **(
                {
                    "executionInputRefs": resolved.execution_input_refs,
                    "executionInputRefsDigest": resolved.execution_input_refs_digest,
                }
                if resolved.execution_input_refs
                else {}
            ),
            "omnigentExecutionPlan": plan_binding.model_dump(
                mode="json", by_alias=True
            ),
        },
        "omnigent.agent-execution-request-snapshot.json",
        "omnigent.agent_execution_request_snapshot",
    )


async def omnigent_resolve_intent_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify persisted plan authority and snapshot the bounded run request."""

    from moonmind.omnigent.control_plane import (
        ConflictingSessionAuthorityError,
        FencingScope,
        OmnigentControlPlaneStore,
        compute_digest,
    )
    from moonmind.workflows.temporal.workflows.omnigent_session import (
        canonical_omnigent_session_id,
        canonical_omnigent_turn_attempt_id,
    )
    from api_service.db.base import async_session_maker

    resolved = OmnigentResolveIntentRequest.model_validate(payload)
    if resolved.request is not None:
        # Replay-only decode for Activity inputs already persisted in Temporal.
        request = AgentExecutionRequest.model_validate(resolved.request)
        plan_binding = request.omnigent_execution_plan
    else:
        plan_binding = resolved.omnigent_execution_plan
        if plan_binding is None:
            raise ValueError("resolve intent lacks persisted plan authority")
        execution_plan = await _load_verified_execution_plan(plan_binding)
        request = await _reconstruct_plan_bound_request(
            binding=plan_binding,
            plan=execution_plan,
            workflow_id=resolved.workflow_id,
            step_execution_id=resolved.step_execution_id,
            agent_run_id=resolved.agent_run_id,
            logical_step_id=resolved.logical_step_id,
            execution_instruction_ref=resolved.execution_instruction_ref,
            execution_instruction_digest=resolved.execution_instruction_digest,
            execution_input_refs=resolved.execution_input_refs,
            execution_input_refs_digest=resolved.execution_input_refs_digest,
        )
    if (
        request.agent_kind != "external"
        or request.agent_id.strip().lower() != "omnigent"
        or not request.execution_profile_ref
    ):
        raise ValueError(
            "Omnigent session supervision requires profile-bound external/omnigent"
        )
    execution_plan = None
    if request.omnigent_execution_plan is not None:
        execution_plan = await _load_verified_execution_plan(
            request.omnigent_execution_plan
        )
        request = _bind_request_to_execution_plan(request, execution_plan)

    if request.step_execution is not None:
        if request.step_execution.workflow_id != resolved.workflow_id:
            raise ValueError("resolved workflowId conflicts with request authority")
        if request.step_execution.step_execution_id != resolved.step_execution_id:
            raise ValueError(
                "resolved stepExecutionId conflicts with request authority"
            )

    session_id = canonical_omnigent_session_id(
        workflow_id=resolved.workflow_id,
        step_execution_id=resolved.step_execution_id,
        agent_run_id=resolved.agent_run_id,
    )
    turn_attempt_id = canonical_omnigent_turn_attempt_id(session_id)
    chat_binding_id = "omc_" + compute_digest(["chat", session_id])[:40]
    intent_payload, intent_artifact_name, intent_artifact_type = (
        _omnigent_intent_snapshot_payload(
            resolved=resolved,
            request=request,
            session_id=session_id,
        )
    )
    intent_body = _json_bytes(intent_payload)
    intent_digest = _digest_bytes(intent_body)
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        existing = await repos.sessions.get(session_id)
    intent_ref = str(existing.intent_ref or "") if existing is not None else ""
    if not intent_ref:
        intent_ref = await _write_json_artifact(
            name=intent_artifact_name,
            artifact_type=intent_artifact_type,
            payload=intent_payload,
        )
    if existing is None:
        try:
            await store.establish_session(
                session_id=session_id,
                moonmind_workflow_id=resolved.workflow_id,
                provider="omnigent",
                chat_binding_id=chat_binding_id,
                first_turn_attempt_id=turn_attempt_id,
                first_turn_idempotency_key=f"{session_id}:turn:1",
                step_execution_id=resolved.step_execution_id,
                moonmind_agent_run_id=resolved.agent_run_id,
                compatibility_profile=resolved.compatibility_version,
                intent_ref=intent_ref,
                intent_digest=intent_digest,
                execution_plan_ref=str(
                    (request.parameters or {}).get("executionPlanRef") or ""
                ).strip()
                or None,
                instruction_digest=compute_digest(request.instruction_ref or ""),
                metadata={
                    "featureGeneration": resolved.admitted_feature_generation,
                    "executionProfileRef": request.execution_profile_ref,
                    **(
                        {
                            "executionPlanRef": (
                                request.omnigent_execution_plan.plan_ref
                            ),
                            "executionPlanDigest": (
                                request.omnigent_execution_plan.plan_digest
                            ),
                            "executionPlanArtifactRef": (
                                request.omnigent_execution_plan.plan_artifact_ref
                            ),
                            "taskInputSnapshotRef": (
                                request.omnigent_execution_plan
                                .task_input_snapshot_ref
                            ),
                        }
                        if request.omnigent_execution_plan is not None
                        else {}
                    ),
                },
            )
        except ConflictingSessionAuthorityError:
            # A concurrent retry may have established the same deterministic
            # authority. Verify it below instead of creating another session.
            pass
    async with store.transaction() as repos:
        current = await repos.sessions.get(session_id)
    if current is None:
        raise RuntimeError("canonical Omnigent session was not established")
    if (
        current.moonmind_workflow_id != resolved.workflow_id
        or current.step_execution_id != resolved.step_execution_id
        or current.moonmind_agent_run_id != resolved.agent_run_id
    ):
        raise ConflictingSessionAuthorityError(
            "deterministic Omnigent session identity has conflicting authority"
        )
    # Concurrent initial resolvers may race before the canonical row exists;
    # the digest is the immutable authority and must remain identical. Normal
    # Activity retries reuse the already-bound ref above.
    if current.intent_digest != intent_digest:
        raise ConflictingSessionAuthorityError(
            "canonical Omnigent session has a different compiled intent digest"
        )
    if current.fencing_generation == 0:
        async with store.transaction() as repos:
            current = await repos.sessions.acquire_fencing_generation(
                session_id,
                FencingScope.SESSION_SUPERVISOR,
                expected_revision=current.revision,
            )

    result = OmnigentSessionWorkflowInput(
        sessionId=session_id,
        compiledExecutionIntentRef=current.intent_ref or intent_ref,
        compiledExecutionIntentDigest=intent_digest,
        omnigentExecutionPlan=request.omnigent_execution_plan,
        workflowId=resolved.workflow_id,
        stepExecutionId=resolved.step_execution_id,
        agentRunId=resolved.agent_run_id,
        initialTurnAttemptId=turn_attempt_id,
        admittedFeatureGeneration=resolved.admitted_feature_generation,
        compatibilityVersion=resolved.compatibility_version,
    )
    return result.model_dump(mode="json", by_alias=True, exclude_none=True)


def _observation_payload(
    observations: list[Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    merged: dict[str, Any] = {}
    latest: dict[str, tuple[datetime, Any]] = {}
    for observation in observations:
        bounded = dict(observation.bounded_index or {})
        for key, value in bounded.items():
            # Observation rows may also retain bounded executor diagnostics.
            # Only the closed reducer vocabulary enters ObservationSet.
            if key not in _RECONCILER_OBSERVATION_KEYS:
                continue
            current = latest.get(key)
            if current is None or observation.observed_at >= current[0]:
                latest[key] = (observation.observed_at, value)
    for key, (_at, value) in latest.items():
        merged[key] = value
    frontier = {
        "eventCursor": next(
            (
                value.get("lastCursor")
                for key, (_at, value) in reversed(tuple(latest.items()))
                if key == "eventFrontier" and isinstance(value, Mapping)
            ),
            None,
        ),
        "snapshotFrontier": next(
            (
                value.get("snapshotDigest")
                for key, (_at, value) in reversed(tuple(latest.items()))
                if key == "providerSession" and isinstance(value, Mapping)
            ),
            None,
        ),
    }
    return merged, frontier


def _durable_terminal_outcome(
    terminal_state: Any, terminal_outcome_enum: Any, classify: Any
) -> Any:
    """Map a durable terminal state onto the reducer's own classification.

    The reducer already owns terminal vocabulary: a timeout is a *failure*, and
    cancellation is reserved for explicit cancel states. Re-deriving that here
    instead of keeping a second status list is what keeps a later ``failed``
    provider snapshot from looking like a contradictory terminal on an already
    timed-out session and quarantining it mid-cleanup.
    """

    normalized = str(terminal_state or "").strip().lower()
    if not normalized:
        return None
    status_class = classify(normalized)
    if status_class.value == "terminal_success" or normalized in {
        "success",
        "complete",
    }:
        return terminal_outcome_enum.SUCCESS
    if status_class.value == "terminal_cancelled":
        return terminal_outcome_enum.CANCELLED
    return terminal_outcome_enum.FAILURE


async def omnigent_load_reconciliation_inputs_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
    from moonmind.omnigent.reconciler import (
        CompiledSessionIntent,
        DesiredLifecycle,
        DurableSessionState,
        LeaseState,
        ObservationSet,
        PriorDecisionSummary,
        SubmissionState,
        TerminalOutcome,
        classify_provider_status,
        current_phase,
    )

    session_id = str(payload.get("sessionId") or "").strip()
    intent_ref = str(payload.get("compiledExecutionIntentRef") or "").strip()
    intent_digest = str(payload.get("compiledExecutionIntentDigest") or "").strip()
    request_stub = OmnigentSessionActivityRequest(
        sessionId=session_id,
        compiledExecutionIntentRef=intent_ref,
        compiledExecutionIntentDigest=intent_digest,
        omnigentExecutionPlan=payload.get("omnigentExecutionPlan"),
        expectedRevision=1,
        fencingGeneration=0,
    )
    request = await _load_intent_request(request_stub)
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(session_id)
        if session is None:
            raise KeyError(f"Unknown canonical Omnigent session {session_id!r}")
        attempts = await repos.turn_attempts.list_for_session(
            session_id, limit=100, latest=True
        )
        observations = await repos.observations.list_for_session(
            session_id, limit=500, latest=True
        )
        commands = await repos.commands.list_for_session(
            session_id, limit=500, latest=True
        )
        decisions = await repos.decisions.list_for_session(
            session_id, limit=500, latest=True
        )

    active_turn = next(
        (
            attempt
            for attempt in attempts
            if attempt.turn_attempt_id == session.active_turn_attempt_id
        ),
        attempts[-1] if attempts else None,
    )
    submission = SubmissionState.NOT_SUBMITTED
    if active_turn is not None:
        if active_turn.state in {"dispatching", "delivery_unknown"}:
            submission = SubmissionState.IN_FLIGHT
        elif active_turn.state in {"accepted", "running", "terminal"}:
            submission = SubmissionState.ACCEPTED
    submit_commands = [
        command
        for command in commands
        if command.command_type == "submit_turn"
        and command.turn_attempt_id
        == (active_turn.turn_attempt_id if active_turn is not None else None)
    ]
    if any(command.delivery_ambiguous for command in submit_commands):
        submission = SubmissionState.IN_FLIGHT

    observation_mapping, frontier = _observation_payload(observations)
    observation_set = ObservationSet.model_validate(observation_mapping)
    last_decision = decisions[-1] if decisions else None
    prior = None
    if last_decision is not None:
        with suppress(Exception):
            prior = PriorDecisionSummary(
                kind=last_decision.decision_code,
                reasonCode=last_decision.reason_code,
                atRevision=last_decision.expected_revision or session.revision,
            )

    terminal_outcome = _durable_terminal_outcome(
        session.terminal_state, TerminalOutcome, classify_provider_status
    )
    runtime_binding_state = None
    if request.omnigent_execution_plan is not None:
        from moonmind.omnigent.harness_platform.stores import (
            DbRuntimeBindingStore,
        )

        runtime_binding_state = await DbRuntimeBindingStore(
            async_session_maker
        ).get_current_state(
            request.omnigent_execution_plan.plan_ref,
            str(session.moonmind_workflow_id or ""),
        )
    profile_held = bool(
        session.provider_profile_id
        and session.metadata.get("providerLeaseRef")
        and session.cleanup_state != "leases_released"
    )
    host_held = bool(
        session.host_lease_ref and session.cleanup_state != "leases_released"
    )
    durable = DurableSessionState(
        sessionId=session.session_id,
        revision=session.revision,
        ownerToken=f"omnigent-session:{session.session_id}",
        fencingGeneration=session.fencing_generation,
        runtimeBindingRef=(
            runtime_binding_state.binding.runtimeBindingRef
            if runtime_binding_state is not None
            else None
        ),
        runtimeBindingRevision=(
            runtime_binding_state.revision
            if runtime_binding_state is not None
            else None
        ),
        runtimeBindingFencingGeneration=(
            runtime_binding_state.fencing_generation
            if runtime_binding_state is not None
            else None
        ),
        desired=(
            DesiredLifecycle.CANCEL
            if session.desired_state in {"cancel", "cleanup", "timeout"}
            else DesiredLifecycle.RUN
        ),
        providerSessionAttached=bool(session.provider_session_ref),
        providerSessionId=session.provider_session_ref,
        attemptId=active_turn.turn_attempt_id if active_turn else None,
        submission=submission,
        # The reducer compares this counter before dispatching the active
        # attempt, so it represents attempts already consumed, excluding the
        # prepared active attempt itself.
        turnAttempts=max(0, len(attempts) - (1 if active_turn is not None else 0)),
        profileLease=(LeaseState.HELD if profile_held else LeaseState.NONE),
        hostLease=(LeaseState.HELD if host_held else LeaseState.NONE),
        lastCursor=session.provider_event_cursor or frontier.get("eventCursor"),
        lastSnapshotDigest=session.snapshot_frontier
        or frontier.get("snapshotFrontier"),
        terminalOutcome=terminal_outcome,
        terminalEvidenceRef=session.terminal_evidence_ref,
        evidenceHarvested=bool(session.terminal_evidence_ref),
        cleanupStarted=session.cleanup_state
        not in {"pending", "terminal_recorded"},
        cleanupComplete=session.cleanup_state
        in {"host_stopped", "leases_released", "complete"},
        failed=session.reconciled_state == "failed",
        quarantined=session.reconciled_state == "quarantined",
        nextDeadline=session.next_reconciliation_deadline,
        priorDecision=prior,
    )
    compiled = CompiledSessionIntent(
        sessionId=session.session_id,
        provider="omnigent",
        requiresProfileLease=True,
        requiresHost=True,
        requiresCleanup=True,
        maxTurnAttempts=max(1, int((request.retry_policy or {}).get("max_attempts", 1))),
        reconcileIntervalSeconds=30,
        turnPromptDigest=(
            active_turn.instruction_digest
            if active_turn and active_turn.instruction_digest
            else hashlib.sha256(str(request.instruction_ref or "").encode()).hexdigest()
        ),
    )
    response: dict[str, Any] = {
        "intent": compiled.model_dump(mode="json", by_alias=True),
        "durable": durable.model_dump(mode="json", by_alias=True),
        "observations": observation_set.model_dump(
            mode="json", by_alias=True, exclude_none=True
        ),
        "phase": current_phase(durable).value,
        "observationCount": len(observations),
        "decisionCount": len(decisions),
    }
    timeout_seconds = int(
        (request.timeout_policy or {}).get("timeout_seconds")
        or (request.timeout_policy or {}).get("timeoutSeconds")
        or 21_600
    )
    if session.created_at is not None:
        response["timeoutAt"] = (
            session.created_at + timedelta(seconds=max(1, timeout_seconds))
        ).isoformat()
    result_evidence_ref = str(
        session.metadata.get("workflowFailureEvidenceRef")
        or session.terminal_evidence_ref
        or ""
    ).strip()
    if result_evidence_ref:
        evidence = await _read_json_artifact(result_evidence_ref)
        raw_terminal = evidence.get("terminalResult")
        if not isinstance(raw_terminal, Mapping):
            raise ValueError("terminal evidence is missing its compact result")
        terminal = OmnigentSessionTerminalResult.model_validate(raw_terminal)
        metadata_key = (
            "workflowFailureEvidenceRef"
            if result_evidence_ref
            == str(session.metadata.get("workflowFailureEvidenceRef") or "").strip()
            else None
        )
        terminal = _bind_terminal_evidence_ref(
            terminal,
            result_evidence_ref,
            metadata_key=metadata_key,
        )
        terminal = _bind_terminal_plan_authority(
            terminal,
            plan_binding=request.omnigent_execution_plan,
            runtime_binding_state=runtime_binding_state,
        )
        response["terminalResult"] = terminal.model_dump(
            mode="json", by_alias=True
        )
    return response


async def omnigent_persist_decision_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import (
        FencingConflictError,
        OmnigentControlPlaneStore,
        RevisionConflictError,
        compute_digest,
    )

    request = OmnigentPersistDecisionRequest.model_validate(payload)
    decision = dict(request.decision)
    decision_id = request.decision_id
    if not decision_id:
        raise ValueError("persist_decision requires decisionId")
    command = decision.get("command")
    command_id = request.command_id
    input_state_digest = compute_digest(
        [request.expected_revision, request.fencing_generation]
    )
    observation_frontier_digest = compute_digest(
        decision.get("diagnostics") or {}
    )
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        existing = await repos.decisions.get(decision_id)
        if existing is None:
            session = await repos.sessions.load_for_update(request.session_id)
            if session is None:
                raise KeyError(request.session_id)
            if session.fencing_generation != request.fencing_generation:
                raise FencingConflictError(
                    "Omnigent decision supervisor fence changed"
                )
            if session.revision != request.expected_revision:
                raise RevisionConflictError(
                    "Omnigent decision expected revision is no longer current"
                )
            await repos.decisions.append(
                decision_id=decision_id,
                session_id=request.session_id,
                decision_code=str(decision["kind"]),
                input_state_digest=input_state_digest,
                observation_frontier_digest=observation_frontier_digest,
                expected_revision=request.expected_revision,
                fencing_generation=request.fencing_generation,
                reason_code=str(decision["reasonCode"]),
                resulting_command_id=command_id,
                next_deadline=(
                    datetime.fromisoformat(str(decision["nextDeadline"]).replace("Z", "+00:00"))
                    if decision.get("nextDeadline")
                    else None
                ),
                product_visible_transition=(
                    str(decision["kind"])
                    if decision.get("changesProductVisibleState")
                    else None
                ),
            )
        elif (
            existing.session_id != request.session_id
            or existing.decision_code != str(decision["kind"])
            or existing.input_state_digest != input_state_digest
            or existing.observation_frontier_digest
            != observation_frontier_digest
            or existing.expected_revision != request.expected_revision
            or existing.fencing_generation != request.fencing_generation
            or existing.reason_code != str(decision["reasonCode"])
            or existing.resulting_command_id != command_id
        ):
            raise ValueError(
                "persisted Omnigent decision has conflicting immutable identity"
            )
        if isinstance(command, Mapping) and command_id:
            await repos.commands.record(
                command_id=command_id,
                session_id=request.session_id,
                command_type=str(command["commandKind"]),
                idempotency_key=command_id,
                payload_digest=compute_digest(command),
                turn_attempt_id=command.get("attemptId"),
                expected_session_revision=request.expected_revision,
                fencing_generation=request.fencing_generation,
                owner_class="omnigent_session_workflow",
                retry_policy={"maxAttempts": 3},
            )
    return {"decisionId": decision_id, "commandId": command_id}


#: The legacy session supervisor is one cleanup owner of a canonical session.
#: It shares the control-plane cleanup aggregate with the generic host realizer
#: so an admitted turn's ``fence_for_turn`` fences a real janitor (#3707 §4).
_SESSION_SUPERVISOR_CLEANUP_OWNER = "omnigent_session_supervisor"


async def _claim_canonical_cleanup(session_id: str) -> Any:
    """Return this owner's shared cleanup claim, or ``None`` when it lost it.

    Every destructive step of the supervisor's cleanup sequence re-claims the
    same deterministic token: the claim is idempotent for its own owner, so the
    sequence resumes, while a live claim held by another janitor -- or cleanup
    that is already complete -- refuses the step instead of releasing a host,
    credential lease, or provider session a different owner now owns.
    """

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore

    return await CanonicalCleanupAuthority(
        OmnigentControlPlaneStore(async_session_maker)
    ).claim(session_id, owner_class=_SESSION_SUPERVISOR_CLEANUP_OWNER)


async def _claim_host_cleanup_authority(hosts: Any, host_lease_ref: str) -> Any:
    """Fence host cleanup, or return ``None`` when another owner won it.

    ``claim_host_lease_cleanup`` compare-and-swaps on the observed status *and*
    heartbeat, so a lease heartbeated (or already drained) since it was read does
    not hand cleanup authority to a second owner. A lost race is retried against
    freshly reloaded evidence rather than executed from the stale read.
    """

    from moonmind.omnigent.oauth_hosts import CLEANUP_CLAIMABLE_HOST_STATES

    for _attempt in range(3):
        current = await hosts.get_host_lease(host_lease_ref)
        if current is None or current.status not in CLEANUP_CLAIMABLE_HOST_STATES:
            return None
        claimed = await hosts.claim_host_lease_cleanup(
            current.lease_id,
            expected_status=current.status,
            expected_last_heartbeat_at=current.last_heartbeat_at,
            ttl_seconds=90,
        )
        if claimed is not None:
            return claimed
    return None


async def _claim_command(request: OmnigentSessionActivityRequest) -> tuple[Any, bool]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import (
        ControlPlaneOutcome,
        FencingConflictError,
        OmnigentControlPlaneStore,
        RevisionConflictError,
    )

    if not request.command_id:
        raise ValueError("bounded side effect requires commandId")
    execution_plan = (
        await _load_verified_execution_plan(request.omnigent_execution_plan)
        if request.omnigent_execution_plan is not None
        else None
    )
    store = OmnigentControlPlaneStore(async_session_maker)
    claim_token = f"omnigent-session:{request.session_id}:{request.command_id}"
    runtime_binding_ref = ""
    projected_runtime_revision: int | None = None
    projected_runtime_fence: int | None = None
    async with store.transaction() as repos:
        command = await repos.commands.get(request.command_id)
        if command is None:
            raise KeyError(f"Unknown Omnigent command {request.command_id!r}")
        session = await repos.sessions.load_for_update(request.session_id)
        if session is None:
            raise KeyError(f"Unknown canonical Omnigent session {request.session_id!r}")
        if command.session_id != request.session_id:
            raise ValueError("Omnigent command belongs to a different session")
        if execution_plan is not None:
            if session.metadata.get("executionPlanRef") != execution_plan.planRef:
                raise ValueError(
                    "canonical session conflicts with persisted execution plan"
                )
            runtime_binding_ref = str(
                session.metadata.get("runtimeBindingRef") or ""
            )
            projected_runtime_revision = session.metadata.get(
                "runtimeBindingRevision"
            )
            projected_runtime_fence = session.metadata.get(
                "runtimeBindingFencingGeneration"
            )
        if (
            command.expected_session_revision != request.expected_revision
            or command.fencing_generation != request.fencing_generation
        ):
            raise ValueError("Omnigent command authority does not match activity input")
        if session.fencing_generation != request.fencing_generation:
            raise FencingConflictError(
                "Omnigent command fencing generation is no longer current"
            )
        # A pending command has not crossed a side-effect boundary yet, so its
        # originating revision must still be current before it may be claimed.
        # A retry of this same durable claim may observe revision advances made
        # by its own ordered phases and resumes under the unchanged fence.
        if (
            command.status == "pending"
            and session.revision != request.expected_revision
        ):
            raise RevisionConflictError(
                "Omnigent command expected revision is no longer current"
            )
        claim = await repos.commands.claim_command(
            request.command_id,
            owner_class="omnigent_session_activity",
            claim_token=claim_token,
        )
    if execution_plan is not None and (
        runtime_binding_ref or request.runtime_binding_ref
    ):
        _runtime_store, runtime_state = await _load_current_runtime_binding(
            execution_plan_ref=execution_plan.planRef,
            execution_scope_ref=str(session.moonmind_workflow_id or ""),
            recorded_runtime_binding_ref=(
                runtime_binding_ref or request.runtime_binding_ref
            ),
        )
        if (
            projected_runtime_revision is not None
            and int(projected_runtime_revision) > runtime_state.revision
        ):
            raise ValueError("runtime-binding projection revision is ahead of authority")
        if (
            projected_runtime_fence is not None
            and int(projected_runtime_fence) != runtime_state.fencing_generation
        ):
            raise ValueError("runtime-binding projection fence conflicts with authority")
        if request.runtime_binding_ref is not None and (
            request.runtime_binding_ref
            != runtime_state.binding.runtimeBindingRef
            or request.runtime_binding_revision != runtime_state.revision
            or request.runtime_binding_fencing_generation
            != runtime_state.fencing_generation
        ):
            raise ValueError(
                "activity runtime-binding authority is obsolete"
            )
    return claim.record, claim.outcome is ControlPlaneOutcome.APPLIED


async def _settle_command(
    request: OmnigentSessionActivityRequest,
    *,
    result_ref: str | None = None,
    delivery_unknown: bool = False,
) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import ControlPlaneOutcome, OmnigentControlPlaneStore

    if not request.command_id:
        raise ValueError("bounded side effect requires commandId")
    store = OmnigentControlPlaneStore(async_session_maker)
    claim_token = f"omnigent-session:{request.session_id}:{request.command_id}"
    outcome = (
        ControlPlaneOutcome.DELIVERY_UNKNOWN
        if delivery_unknown
        else ControlPlaneOutcome.APPLIED
    )
    async with store.transaction() as repos:
        settled = await repos.commands.record_command_delivery(
            request.command_id,
            owner_class="omnigent_session_activity",
            claim_token=claim_token,
            outcome=outcome,
            result_ref=result_ref,
        )
    return {
        "commandId": request.command_id,
        "outcome": settled.outcome.value,
        "resultRef": result_ref,
    }


async def omnigent_load_failure_authority_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve only the current fence after reconciliation-input loading fails.

    This boundary deliberately does not read provider state or the compiled
    intent artifact. It compares the child's immutable identities and intent
    authority with the canonical session row, then returns the current
    revision/fence for the typed failure writer. A concurrent authority change
    is still rejected by that writer.
    """

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore

    request = OmnigentFailureAuthorityRequest.model_validate(payload)
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
    if session is None:
        raise KeyError(request.session_id)

    expected_authority = {
        "workflowId": request.workflow_id,
        "stepExecutionId": request.step_execution_id,
        "agentRunId": request.agent_run_id,
        "compiledExecutionIntentRef": request.compiled_execution_intent_ref,
        "compiledExecutionIntentDigest": request.compiled_execution_intent_digest,
    }
    actual_authority = {
        "workflowId": session.moonmind_workflow_id,
        "stepExecutionId": session.step_execution_id,
        "agentRunId": session.moonmind_agent_run_id,
        "compiledExecutionIntentRef": session.intent_ref,
        "compiledExecutionIntentDigest": session.intent_digest,
    }
    if actual_authority != expected_authority:
        raise ValueError("Omnigent failure authority conflicts with canonical session")
    return {
        "sessionId": session.session_id,
        "revision": session.revision,
        "fencingGeneration": session.fencing_generation,
    }


async def omnigent_persist_signal_intents_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import (
        FencingConflictError,
        OmnigentControlPlaneStore,
        RevisionConflictError,
        TurnSource,
        coerce_turn_source,
        compute_digest,
    )
    from moonmind.omnigent.control_plane.identities import (
        canonical_turn_command_key,
    )
    from moonmind.omnigent.control_plane.turn_commands import (
        CanonicalTurnCommandService,
    )

    request = OmnigentPersistSignalsRequest.model_validate(payload)
    execution_plan = (
        await _load_verified_execution_plan(request.omnigent_execution_plan)
        if request.omnigent_execution_plan is not None
        else None
    )
    store = OmnigentControlPlaneStore(async_session_maker)
    applied = 0
    async with store.transaction() as repos:
        session = await repos.sessions.load_for_update(request.session_id)
        if session is None:
            raise KeyError(request.session_id)

        # Workflow Chat, continuation, steering, cancellation, and cleanup
        # signals all enter through this journal. Once a plan-bound session has
        # live authority, reject an old supervisor task instead of applying its
        # intent to a replacement host/session binding.
        if execution_plan is not None:
            if session.metadata.get("executionPlanRef") != execution_plan.planRef:
                raise ValueError(
                    "signal session conflicts with persisted execution plan"
                )
            recorded_runtime_ref = str(
                session.metadata.get("runtimeBindingRef") or ""
            )
            if recorded_runtime_ref:
                _runtime_store, runtime_state = (
                    await _load_current_runtime_binding(
                        execution_plan_ref=execution_plan.planRef,
                        execution_scope_ref=str(
                            session.moonmind_workflow_id or ""
                        ),
                        recorded_runtime_binding_ref=recorded_runtime_ref,
                    )
                )
                if (
                    request.runtime_binding_ref
                    != runtime_state.binding.runtimeBindingRef
                    or request.runtime_binding_revision
                    != runtime_state.revision
                    or request.runtime_binding_fencing_generation
                    != runtime_state.fencing_generation
                ):
                    raise ValueError(
                        "signal runtime-binding authority is obsolete"
                    )
            elif request.runtime_binding_ref is not None:
                raise ValueError(
                    "signal supplied runtime authority before acquisition"
                )

        already_applied = True
        for item in request.signals:
            kind = str(item.get("kind") or "")
            raw = item.get("payload")
            signal = dict(raw) if isinstance(raw, Mapping) else {}
            if kind in {
                "cancel_or_interrupt_requested",
                "cleanup_requested",
                "timeout_requested",
            }:
                desired_state = (
                    "timeout" if kind == "timeout_requested" else "cancel"
                )
                already_applied = (
                    already_applied
                    and (
                        session.terminal_state is not None
                        or session.desired_state == desired_state
                    )
                )
            elif kind == "submit_authorized_continuation":
                instruction_ref = str(signal.get("instructionRef") or "").strip()
                request_id = str(signal.get("requestId") or "").strip()
                # The canonical command journal is the durable record of this
                # continuation, so replay convergence is decided from it rather
                # than from an attempt identity the signal names.
                recorded = (
                    await repos.commands.get_by_idempotency_key(
                        canonical_turn_command_key(
                            str(session.moonmind_workflow_id), request_id
                        )
                    )
                    if request_id
                    else None
                )
                recorded_turn_id = (
                    str(recorded.turn_attempt_id) if recorded is not None else ""
                )
                already_applied = already_applied and bool(
                    recorded_turn_id
                    and session.active_turn_attempt_id == recorded_turn_id
                    and session.metadata.get(f"turnInstructionRef:{recorded_turn_id}")
                    == instruction_ref
                )
            else:
                raise ValueError(f"unsupported Omnigent signal intent {kind!r}")
        if already_applied:
            return {"appliedIntentCount": len(request.signals)}
        if session.fencing_generation != request.fencing_generation:
            raise FencingConflictError(
                "Omnigent signal intent supervisor fence changed"
            )
        if session.revision != request.expected_revision:
            raise RevisionConflictError(
                "Omnigent signal intent expected revision is no longer current"
            )
        for item in request.signals:
            kind = str(item.get("kind") or "")
            raw = item.get("payload")
            signal = dict(raw) if isinstance(raw, Mapping) else {}
            if kind in {
                "cancel_or_interrupt_requested",
                "cleanup_requested",
                "timeout_requested",
            }:
                desired_state = (
                    "timeout" if kind == "timeout_requested" else "cancel"
                )
                if session.desired_state != desired_state:
                    session = await repos.sessions.update_lifecycle(
                        request.session_id,
                        expected_revision=session.revision,
                        expected_fencing_generation=session.fencing_generation,
                        desired_state=desired_state,
                    )
                applied += 1
            elif kind == "submit_authorized_continuation":
                instruction_ref = str(signal.get("instructionRef") or "").strip()
                request_id = str(signal.get("requestId") or "").strip()
                if not instruction_ref or not request_id:
                    raise ValueError("continuation signal is missing compact authority")
                # The turn source is the closed #3707 vocabulary. In-flight
                # histories predate the field, so an omitted source resolves to
                # the repository continuation it always represented; an unknown
                # value fails closed rather than writing free-form lineage.
                signal_source = coerce_turn_source(
                    signal.get("turnSource")
                    or TurnSource.REPOSITORY_CONTINUATION
                )
                # The signal-driven continuation is an ordinary instruction
                # source, so it claims through the shared canonical command
                # service instead of creating a turn attempt directly. Only that
                # boundary journals the command, compares immutable authority,
                # verifies the owning principal, and fences incompatible
                # cleanup; an instruction may name its request and instruction
                # refs but never attests its own turn identity.
                claim = await CanonicalTurnCommandService(
                    store
                ).claim_with_repositories(
                    repos,
                    workflow_id=str(session.moonmind_workflow_id),
                    provider_session_ref=str(session.provider_session_ref or ""),
                    chat_binding_id=session.chat_binding_id,
                    session_id=request.session_id,
                    command_type="submit_authorized_continuation",
                    turn_source=signal_source,
                    idempotency_key=request_id,
                    payload_digest=compute_digest(instruction_ref),
                    step_execution_id=session.step_execution_id,
                )
                turn_id = claim.turn_attempt_id
                session = await repos.sessions.get(request.session_id)
                session = await repos.sessions.bind_runtime_authority(
                    request.session_id,
                    expected_revision=session.revision,
                    expected_fencing_generation=session.fencing_generation,
                    metadata_patch={
                        f"turnInstructionRef:{turn_id}": instruction_ref,
                    },
                )
                applied += 1
    return {"appliedIntentCount": applied}


async def omnigent_ensure_provider_profile_lease_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from api_service.db.models import ManagedAgentProviderProfile
    from moonmind.omnigent.control_plane import (
        FencingScope,
        OmnigentControlPlaneStore,
    )
    from moonmind.provider_profiles.lease_client import (
        CredentialLeasePurpose,
        ProviderProfileLeaseClient,
        deterministic_lease_owner_id,
    )
    from moonmind.workflows.temporal.client import TemporalClientAdapter
    from moonmind.workflows.temporal.workflows.omnigent_session import (
        omnigent_session_workflow_id,
    )

    request = OmnigentSessionActivityRequest.model_validate(payload)
    command, should_execute = await _claim_command(request)
    if not should_execute:
        return {"commandId": request.command_id, "outcome": command.status}
    agent_request = await _load_intent_request(request)
    agent_plan_binding = getattr(agent_request, "omnigent_execution_plan", None)
    execution_plan = (
        await _load_verified_execution_plan(agent_plan_binding)
        if agent_plan_binding is not None
        else None
    )
    if execution_plan is not None:
        selected_profiles = {
            binding.providerProfileRef
            for binding in execution_plan.payload.credentialBindings.values()
        }
        if len(selected_profiles) != 1:
            raise ValueError(
                "session supervisor currently requires one selected Provider Profile"
            )
        profile_id = next(iter(selected_profiles))
        if (
            agent_request.execution_profile_ref
            and agent_request.execution_profile_ref != profile_id
        ):
            raise ValueError("request Provider Profile conflicts with execution plan")
    else:
        profile_id = str(agent_request.execution_profile_ref or "")
    async with async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, profile_id)
    if profile is None:
        raise ValueError("Provider Profile does not exist")
    if not profile.enabled or profile.auth_state != "connected":
        raise ValueError("Provider Profile is not launch ready")
    runtime_id = str(getattr(profile.runtime_id, "value", profile.runtime_id))
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session_authority = await repos.sessions.get(request.session_id)
        if session_authority is None:
            raise KeyError(request.session_id)
        if not session_authority.provider_profile_generation:
            session_authority = await repos.sessions.acquire_fencing_generation(
                request.session_id,
                FencingScope.PROVIDER_PROFILE_LEASE,
                expected_revision=session_authority.revision,
            )
    owner_id = deterministic_lease_owner_id(
        profile_id=profile_id,
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        workflow_id=request.session_id,
        step_execution_id=request.session_id,
    )
    lease_client = ProviderProfileLeaseClient(TemporalClientAdapter())
    lease = await lease_client.acquire_execution_lease(
        runtime_id=runtime_id,
        profile_id=profile_id,
        owner_id=owner_id,
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        # This runs in an Activity, so the manager records an activity-owned
        # grant and verifies the owning workflow is still running. Its metadata
        # allowlist keeps only these safe owner identities, so the durable
        # supervisor workflow ID must be sent explicitly; a session-scoped key
        # alone is dropped and the grant fails as owner-missing.
        metadata={
            "workflowId": omnigent_session_workflow_id(request.session_id),
            "stepExecutionId": (
                session_authority.step_execution_id or request.session_id
            ),
            "idempotencyKey": agent_request.idempotency_key,
            "ownerIsWorkflow": False,
        },
    )
    # A Provider Profile generation becomes authoritative only while the
    # execution lease is held.  Reload after acquisition so a generation that
    # rotated between readiness inspection and lease ownership can never be
    # copied into the runtime binding.
    async with async_session_maker() as session:
        acquired_profile = await session.get(
            ManagedAgentProviderProfile,
            profile_id,
        )
    if (
        acquired_profile is None
        or not acquired_profile.enabled
        or acquired_profile.auth_state != "connected"
    ):
        await lease_client.release_lease(lease)
        raise ValueError(
            "Provider Profile ceased to be launch ready after lease acquisition"
        )
    acquired_runtime_id = str(
        getattr(
            acquired_profile.runtime_id,
            "value",
            acquired_profile.runtime_id,
        )
    )
    if acquired_runtime_id != runtime_id:
        await lease_client.release_lease(lease)
        raise ValueError("Provider Profile runtime changed during acquisition")
    if (
        execution_plan is not None
        and execution_plan.payload.executionRealizerRef
        == "generic-omnigent-host@1"
    ):
        await lease_client.release_lease(lease)
        raise ValueError(
            "generic execution authority belongs to the AgentRun realizer, "
            "not the legacy session supervisor"
        )
    profile = acquired_profile

    async def await_before_session_ownership(awaitable: Any) -> Any:
        """Release acquired capacity when a pre-bind persistence step fails."""

        try:
            return await awaitable
        except BaseException:
            await lease_client.release_lease(lease)
            raise

    runtime_binding = None
    runtime_binding_state = None
    replacing_runtime_authority = False
    if execution_plan is not None:
        from moonmind.omnigent.harness_platform.stores import (
            DbRuntimeBindingStore,
        )
        from moonmind.omnigent.harness_platform.runtime_binding import (
            create_runtime_binding,
        )

        provider_leases: dict[str, dict[str, Any]] = {}
        for slot, binding in execution_plan.payload.credentialBindings.items():
            credential_runtime_ref = (
                f"credential-runtime:{lease.lease_id}:"
                f"{int(profile.credential_generation)}"
            )
            provider_leases[slot] = {
                "providerProfileRef": binding.providerProfileRef,
                "providerLeaseRef": lease.lease_id,
                "credentialGeneration": int(profile.credential_generation),
                "credentialRuntimeRef": credential_runtime_ref,
            }
        runtime_store = DbRuntimeBindingStore(async_session_maker)
        execution_scope_ref = str(session_authority.moonmind_workflow_id or "")
        current_runtime_state = await await_before_session_ownership(
            runtime_store.get_current_state(
                execution_plan.planRef, execution_scope_ref
            )
        )
        replacing_runtime_authority = bool(
            current_runtime_state is not None
            and current_runtime_state.binding.providerLeases
            != create_runtime_binding(
                executionPlanRef=execution_plan.planRef,
                providerLeases=provider_leases,
            ).providerLeases
        )
        if replacing_runtime_authority and any(
            value is not None
            for value in (
                session_authority.host_binding_ref,
                session_authority.host_lease_ref,
                session_authority.provider_session_ref,
            )
        ):
            await lease_client.release_lease(lease)
            raise ValueError(
                "Provider Profile rotation requires the bound host and session "
                "to be drained before reconciliation"
            )
        if current_runtime_state is None:
            runtime_binding = await await_before_session_ownership(
                runtime_store.create_initial(
                    execution_plan_ref=execution_plan.planRef,
                    execution_scope_ref=execution_scope_ref,
                    provider_leases=provider_leases,
                )
            )
        elif not replacing_runtime_authority:
            runtime_binding = current_runtime_state.binding
        else:
            runtime_binding = await await_before_session_ownership(
                runtime_store.reconcile_provider_leases(
                    current_runtime_state.binding.runtimeBindingRef,
                    provider_leases=provider_leases,
                    expected_revision=current_runtime_state.revision,
                    expected_fencing_generation=(
                        current_runtime_state.fencing_generation
                    ),
                )
            )
        runtime_binding_state = await await_before_session_ownership(
            runtime_store.get_state(runtime_binding.runtimeBindingRef)
        )
        if runtime_binding_state is None:
            await lease_client.release_lease(lease)
            raise RuntimeError("persisted runtime binding could not be reloaded")
        if (
            session_authority.credential_generation is not None
            and session_authority.credential_generation
            != int(profile.credential_generation)
        ):
            replacing_runtime_authority = True
        if replacing_runtime_authority:
            provider_generation = int(
                session_authority.provider_profile_generation or 0
            )
            if provider_generation > runtime_binding_state.fencing_generation:
                await lease_client.release_lease(lease)
                raise ValueError(
                    "session Provider Profile fence is ahead of runtime binding"
                )
            if provider_generation < runtime_binding_state.fencing_generation:
                if (
                    provider_generation + 1
                    != runtime_binding_state.fencing_generation
                ):
                    await lease_client.release_lease(lease)
                    raise ValueError(
                        "runtime binding skipped a Provider Profile fencing generation"
                    )
                try:
                    async with store.transaction() as repos:
                        session_authority = (
                            await repos.sessions.acquire_fencing_generation(
                                request.session_id,
                                FencingScope.PROVIDER_PROFILE_LEASE,
                                expected_revision=session_authority.revision,
                            )
                        )
                except BaseException:
                    await lease_client.release_lease(lease)
                    raise
    try:
        async with store.transaction() as repos:
            runtime_metadata = {
                "providerLeaseRef": lease.lease_id,
                "providerLeaseOwnerId": lease.owner_id,
                "providerRuntimeId": runtime_id,
                **(
                    {
                        "runtimeBindingRef": runtime_binding.runtimeBindingRef,
                        "runtimeBindingRevision": runtime_binding_state.revision,
                        "runtimeBindingFencingGeneration": (
                            runtime_binding_state.fencing_generation
                        ),
                        "executionPlanRef": execution_plan.planRef,
                    }
                    if runtime_binding is not None
                    and runtime_binding_state is not None
                    and execution_plan is not None
                    else {}
                ),
            }
            if replacing_runtime_authority:
                updated = await repos.sessions.replace_provider_runtime_authority(
                    request.session_id,
                    expected_revision=session_authority.revision,
                    expected_fencing_generation=request.fencing_generation,
                    expected_provider_profile_generation=int(
                        session_authority.provider_profile_generation or 0
                    ),
                    provider_profile_id=profile_id,
                    credential_generation=int(profile.credential_generation),
                    metadata_patch=runtime_metadata,
                )
            else:
                updated = await repos.sessions.bind_runtime_authority(
                    request.session_id,
                    expected_revision=session_authority.revision,
                    expected_fencing_generation=request.fencing_generation,
                    provider_profile_id=profile_id,
                    provider_profile_generation=(
                        session_authority.provider_profile_generation
                    ),
                    credential_generation=int(profile.credential_generation),
                    metadata_patch=runtime_metadata,
                )
    except BaseException:
        await lease_client.release_lease(lease)
        raise
    if runtime_binding_state is not None:
        await _project_runtime_binding_to_execution(
            workflow_id=str(updated.moonmind_workflow_id or ""),
            state=runtime_binding_state,
        )
    settled = await _settle_command(request)
    settled.update(
        {
            "revision": updated.revision,
            "providerProfileGeneration": updated.provider_profile_generation,
            **(
                {
                    "runtimeBindingRef": runtime_binding.runtimeBindingRef,
                    "runtimeBindingRevision": runtime_binding_state.revision,
                    "runtimeBindingFencingGeneration": (
                        runtime_binding_state.fencing_generation
                    ),
                }
                if runtime_binding is not None and runtime_binding_state is not None
                else {}
            ),
        }
    )
    return settled


async def _omnigent_client_context():
    import httpx
    from moonmind.omnigent.settings import (
        resolved_api_token,
        resolved_proxy_forward_headers,
        resolved_server_url,
    )
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    http_client = httpx.AsyncClient()
    client = OmnigentHttpClient(
        base_url=resolved_server_url(),
        api_token=resolved_api_token(),
        client=http_client,
        upstream_header_allowlist=resolved_proxy_forward_headers(),
    )
    return http_client, client


async def _resolve_legacy_launch_policy_snapshot(
    *, policy_service: Any, launch_policy_ref: str, resolve_default: bool
) -> tuple[str, dict[str, Any]]:
    """Resolve exact replay authority, following defaults only when unbound."""

    if not resolve_default:
        snapshot = await policy_service.resolve_runtime_snapshot(launch_policy_ref)
        return launch_policy_ref, snapshot
    policy_id, separator, _version = launch_policy_ref.rpartition("@")
    if not separator:
        raise ValueError("default launch policy reference is invalid")
    snapshot = await policy_service.resolve_default_runtime_snapshot(policy_id)
    return str(snapshot["policyRef"]), snapshot


async def omnigent_ensure_host_activity(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Materialize and bind one exact host without returning mutable paths."""

    from api_service.db.base import async_session_maker
    from api_service.services.omnigent_policies import OmnigentPolicyService
    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
    from moonmind.omnigent.control_plane import (
        FencingScope,
        OmnigentControlPlaneStore,
    )
    from moonmind.omnigent.execution_profiles import (
        PROFILES,
        default_execution_profile_ref_for_runtime,
        selection_from_request,
    )
    from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
    from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository
    from moonmind.omnigent.profile_bound_execution import (
        OmnigentProfileBoundExecutionCoordinator,
        _compile_persisted_effective_launch,
        compile_follow_up_retrieval_policy,
        enforce_required_follow_up_retrieval,
    )
    from moonmind.omnigent.settings import resolved_server_url
    from moonmind.omnigent.workspace_intent import compile_workspace_intent

    request = OmnigentSessionActivityRequest.model_validate(payload)
    command, should_execute = await _claim_command(request)
    if not should_execute:
        return {"commandId": request.command_id, "outcome": command.status}
    agent_request = await _load_intent_request(request)
    agent_plan_binding = getattr(agent_request, "omnigent_execution_plan", None)
    execution_plan = (
        await _load_verified_execution_plan(agent_plan_binding)
        if agent_plan_binding is not None
        else None
    )
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
    if session is None or not session.provider_profile_id:
        raise ValueError("Provider Profile lease authority is missing")
    if not session.host_lease_generation:
        async with store.transaction() as repos:
            session = await repos.sessions.acquire_fencing_generation(
                request.session_id,
                FencingScope.HOST_LEASE,
                expected_revision=session.revision,
            )
    provider_lease_id = str(session.metadata.get("providerLeaseRef") or "")
    if not provider_lease_id:
        raise ValueError("Provider Profile lease reference is missing")

    if (
        execution_plan is not None
        and execution_plan.payload.executionRealizerRef
        == "generic-omnigent-host@1"
    ):
        raise ValueError(
            "generic execution authority belongs to the AgentRun realizer, "
            "not the legacy session supervisor"
        )

    hosts = OmnigentOAuthHostRepository(async_session_maker)
    binding = await hosts.get_binding_for_profile(session.provider_profile_id)
    runtime_id = str(session.metadata.get("providerRuntimeId") or "codex_cli")
    runtime_execution_profile_ref = default_execution_profile_ref_for_runtime(
        runtime_id
    )
    requested_target, requested_policy = selection_from_request(
        agent_request.parameters
    )
    resolve_policy_default = False
    if execution_plan is not None:
        planned_registration = find_harness_registration(
            execution_plan.payload.harnessId
        )
        if planned_registration is None:
            raise ValueError("planned harness has no product execution profile")
        execution_profile_ref = planned_registration.executionTargetRef
        launch_policy_ref = execution_plan.payload.launchPolicyRef
        if requested_target and requested_target != execution_profile_ref:
            raise ValueError("authored host selection conflicts with execution plan")
        if requested_policy and requested_policy != launch_policy_ref:
            raise ValueError("authored launch policy conflicts with execution plan")
        if binding is not None and (
            binding.execution_profile_ref not in {None, execution_profile_ref}
            or binding.launch_policy_ref not in {None, launch_policy_ref}
        ):
            raise ValueError("durable host binding conflicts with execution plan")
    elif binding is not None:
        execution_profile_ref = str(
            binding.execution_profile_ref
            or runtime_execution_profile_ref
        )
        launch_policy_ref = str(
            binding.launch_policy_ref
            or requested_policy
            or PROFILES[execution_profile_ref].default_policy_ref
        )
        resolve_policy_default = not binding.launch_policy_ref and not requested_policy
        if requested_target and requested_target != execution_profile_ref:
            raise ValueError("authored host selection conflicts with durable binding")
    else:
        execution_profile_ref = (
            requested_target or runtime_execution_profile_ref
        )
        launch_policy_ref = (
            requested_policy or PROFILES[execution_profile_ref].default_policy_ref
        )
        resolve_policy_default = not requested_policy

    parameters = dict(agent_request.parameters or {})
    if (
        execution_plan is not None
        and execution_plan.payload.effectiveLaunchSnapshotRef is not None
    ):
        effective_launch = await _read_json_artifact(
            execution_plan.payload.effectiveLaunchSnapshotRef.removeprefix(
                "artifact:"
            )
        )
        if (
            effective_launch.get("providerProfileId")
            != session.provider_profile_id
        ):
            raise ValueError(
                "effective launch conflicts with acquired Provider Profile"
            )
    else:
        # Replay path for histories admitted before exact launch snapshots were
        # stored with the plan. New product submissions never consult current
        # policy state here.
        async with async_session_maker() as db_session:
            policy_service = OmnigentPolicyService(db_session)
            launch_policy_ref, policy_snapshot = (
                await _resolve_legacy_launch_policy_snapshot(
                    policy_service=policy_service,
                    launch_policy_ref=launch_policy_ref,
                    resolve_default=resolve_policy_default,
                )
            )
        authored_follow_up = (
            parameters.get("followUpRetrieval")
            if isinstance(parameters.get("followUpRetrieval"), Mapping)
            else {}
        )
        follow_up = compile_follow_up_retrieval_policy(
            policy_snapshot,
            parameters,
            repository=str(parameters.get("repository") or "").strip(),
            tenant_id=str(
                authored_follow_up.get("tenantId")
                or parameters.get("tenantId")
                or "default"
            ).strip(),
        )
        enforce_required_follow_up_retrieval(authored_follow_up, follow_up)
        effective_launch = _compile_persisted_effective_launch(
            policy_snapshot,
            provider_profile_id=session.provider_profile_id,
            follow_up_retrieval=follow_up,
        )
    if effective_launch["executionProfileRef"] != execution_profile_ref:
        raise ValueError("launch policy conflicts with selected execution profile")
    if binding is None:
        binding = await hosts.create_or_update_static_binding(
            profile_id=session.provider_profile_id,
            endpoint_ref=str(effective_launch.get("endpointRef") or resolved_server_url()),
            host_launch_profile_ref=(
                launch_policy_ref
                if effective_launch.get("hostMode") == "on_demand_docker"
                else None
            ),
            execution_profile_ref=execution_profile_ref,
            launch_policy_ref=launch_policy_ref,
            effective_launch_snapshot=effective_launch,
        )
    elif binding.effective_launch_snapshot is None:
        binding = await hosts.create_or_update_static_binding(
            profile_id=session.provider_profile_id,
            endpoint_ref=binding.endpoint_ref,
            static_host_id=binding.static_host_id,
            host_launch_profile_ref=binding.host_launch_profile_ref,
            execution_profile_ref=execution_profile_ref,
            launch_policy_ref=launch_policy_ref,
            effective_launch_snapshot=effective_launch,
        )
    else:
        bound_effective_launch = dict(binding.effective_launch_snapshot)
        if (
            execution_plan is not None
            and bound_effective_launch.get("snapshotRef")
            != effective_launch.get("snapshotRef")
        ):
            raise ValueError(
                "durable host binding conflicts with planned launch authority"
            )
        effective_launch = bound_effective_launch

    lease = await hosts.create_or_get_host_lease(
        binding=binding,
        provider_lease_id=provider_lease_id,
        holder_workflow_id=request.session_id,
        agent_run_id=session.moonmind_agent_run_id,
        idempotency_key=request.session_id,
        ttl_seconds=5400,
    )
    if lease.status in {"stopped", "failed"}:
        lease = await hosts.restart_host_lease(lease.lease_id)
    if lease.status == "allocating":
        lease = await hosts.transition_host_lease(
            lease.lease_id,
            expected_status="allocating",
            new_status="starting",
        )

    workflow_id = session.moonmind_workflow_id
    step_execution_id = session.step_execution_id or request.session_id
    if agent_request.remediation_workspace is not None:
        workspace_locator = dict(
            agent_request.remediation_workspace.get("destinationWorkspaceLocator")
            or {}
        )
        repository_source = ""
        restore_input_refs: tuple[str, ...] = ()
        attachment_refs: tuple[str, ...] = ()
    else:
        workspace_intent = compile_workspace_intent(
            agent_request,
            workflow_id=workflow_id,
            step_execution_id=step_execution_id,
        )
        workspace_locator = workspace_intent.workspace_locator_payload()
        repository_source = workspace_intent.repository or ""
        restore_input_refs = tuple(workspace_intent.restore_input_refs)
        attachment_refs = tuple(workspace_intent.attachment_refs)
    github_token = await OmnigentProfileBoundExecutionCoordinator._github_token(
        agent_request
    )
    bridge_store = OmnigentBridgeSessionStore(async_session_maker)
    bridge = await bridge_store.bind_profile_authorization(
        request=agent_request,
        endpoint_ref=binding.endpoint_ref,
        provider_profile_id=session.provider_profile_id,
        provider_lease_id=provider_lease_id,
        credential_generation=lease.credential_generation,
        host_binding_ref=binding.binding_ref,
        host_lease_ref=lease.lease_id,
        omnigent_host_id=lease.omnigent_host_id,
        effective_launch_snapshot=effective_launch,
    )
    http_client, client = await _omnigent_client_context()
    try:
        preflight = await OmnigentOAuthHostRuntime(client=client).prepare_host(
            binding=binding,
            host_lease=lease,
            workspace_key=f"{workflow_id}:{step_execution_id}",
            workspace_locator=workspace_locator,
            current_workflow_id=workflow_id,
            current_step_execution_id=step_execution_id,
            resolved_skillset_ref=agent_request.resolved_skillset_ref,
            artifact_gateway=LocalOmnigentArtifactGateway(),
            evidence_request=agent_request,
            cleanup_authority_store=bridge_store,
            target_repository=str(parameters.get("repository") or "").strip(),
            required_capabilities=(
                OmnigentProfileBoundExecutionCoordinator._required_capabilities(
                    agent_request
                )
            ),
            github_token=github_token,
            github_mutation_required=(
                OmnigentProfileBoundExecutionCoordinator._github_mutation_required(
                    agent_request
                )
            ),
            effective_launch=effective_launch,
            repository_source=repository_source,
            repository_provider=str(
                (agent_request.workspace_spec or {}).get("provider") or ""
            ).strip(),
            repository_connection_ref=str(
                (agent_request.workspace_spec or {}).get("connectionRef") or ""
            ).strip(),
            repository_client_evidence=dict(
                (agent_request.workspace_spec or {}).get("clientEvidence") or {}
            ),
            starting_branch=(
                None
                if agent_request.remediation_workspace is not None
                else workspace_intent.starting_branch
            ),
            target_branch=(
                None
                if agent_request.remediation_workspace is not None
                else workspace_intent.target_branch
            ),
            checkout_commit=(
                None
                if agent_request.remediation_workspace is not None
                else workspace_intent.checkout_commit
            ),
            restore_input_refs=restore_input_refs,
            workspace_checkpoint_restore_ref=str(
                (agent_request.workspace_spec or {}).get(
                    "workspaceCheckpointRestoreRef"
                )
                or ""
            ).strip()
            or None,
            attachment_refs=attachment_refs,
        )
        preflight_host_id = str(preflight.get("hostId") or "").strip()
        if not preflight_host_id:
            raise ValueError("exact host preflight omitted host identity")
        model_options = await client.get_host_model_options(preflight_host_id)
        if not isinstance(model_options, Mapping):
            raise ValueError("exact host model options have an unsupported shape")
    finally:
        await http_client.aclose()
    host_id = str(preflight["hostId"])
    bridge = await bridge_store.bind_profile_authorization(
        request=agent_request,
        endpoint_ref=binding.endpoint_ref,
        provider_profile_id=session.provider_profile_id,
        provider_lease_id=provider_lease_id,
        credential_generation=lease.credential_generation,
        host_binding_ref=binding.binding_ref,
        host_lease_ref=lease.lease_id,
        omnigent_host_id=host_id,
        effective_launch_snapshot=effective_launch,
        workspace=str(preflight.get("workspacePath") or "").strip() or None,
    )
    if lease.status == "starting":
        lease = await hosts.transition_host_lease(
            lease.lease_id,
            expected_status="starting",
            new_status="ready",
            fields={"omnigent_host_id": host_id},
        )
    if lease.status == "ready":
        lease = await hosts.transition_host_lease(
            lease.lease_id,
            expected_status="ready",
            new_status="assigned",
            fields={"bridge_session_id": bridge.bridge_session_id},
        )
    runtime_binding = None
    runtime_binding_state = None
    if execution_plan is not None:
        runtime_evidence = await _persist_host_runtime_evidence(
            request=agent_request,
            plan=execution_plan,
            preflight=preflight,
            model_options=model_options,
            host_lease_generation=int(session.host_lease_generation or 0),
        )
        current_runtime_ref = str(session.metadata.get("runtimeBindingRef") or "")
        runtime_store, current_runtime_state = await _load_current_runtime_binding(
            execution_plan_ref=execution_plan.planRef,
            execution_scope_ref=str(session.moonmind_workflow_id or ""),
            recorded_runtime_binding_ref=current_runtime_ref,
        )
        runtime_binding = await runtime_store.update_with_host(
            current_runtime_state.binding.runtimeBindingRef,
            host_binding_ref=binding.binding_ref,
            host_lease_ref=lease.lease_id,
            host_lease_generation=int(session.host_lease_generation or 0),
            omnigent_host_id=host_id,
            host_harness_attestation_ref=str(
                runtime_evidence["host_harness_attestation_ref"]
            ),
            exact_host_capability_decision_ref=str(
                runtime_evidence["exact_host_capability_decision_ref"]
            ),
            workspace_resolution_ref=str(
                runtime_evidence["workspace_resolution_ref"]
            ),
            model_option_attestation_ref=str(
                runtime_evidence["model_option_attestation_ref"]
            ),
            skill_delivery_attestation_ref=str(
                runtime_evidence["skill_delivery_attestation_ref"]
            ),
            cleanup_authority_refs=list(
                runtime_evidence["cleanup_authority_refs"]
            ),
            expected_revision=current_runtime_state.revision,
            expected_fencing_generation=current_runtime_state.fencing_generation,
        )
        runtime_binding_state = await runtime_store.get_state(
            runtime_binding.runtimeBindingRef
        )
        if runtime_binding_state is None:
            raise RuntimeError("advanced runtime binding could not be reloaded")
    async with store.transaction() as repos:
        updated = await repos.sessions.bind_runtime_authority(
            request.session_id,
            expected_revision=session.revision,
            expected_fencing_generation=request.fencing_generation,
            host_binding_ref=binding.binding_ref,
            host_lease_ref=lease.lease_id,
            host_lease_generation=session.host_lease_generation,
            credential_generation=lease.credential_generation,
            execution_plan_ref=session.execution_plan_ref,
            metadata_patch={
                "omnigentHostRef": host_id,
                "hostHarness": str(effective_launch["harness"]),
                "endpointRef": binding.endpoint_ref,
                "effectiveLaunchRef": str(effective_launch["snapshotRef"]),
                "egressAttestation": preflight.get("egressAttestation"),
                "egressEvidenceRef": preflight.get("egressEvidenceRef"),
                **(
                    {
                        "runtimeBindingRef": runtime_binding.runtimeBindingRef,
                        "runtimeBindingRevision": runtime_binding_state.revision,
                        "runtimeBindingFencingGeneration": (
                            runtime_binding_state.fencing_generation
                        ),
                        "runtimeBindingState": runtime_binding_state.state,
                    }
                    if runtime_binding is not None
                    and runtime_binding_state is not None
                    else {}
                ),
            },
        )
    if runtime_binding_state is not None:
        await _project_runtime_binding_to_execution(
            workflow_id=str(updated.moonmind_workflow_id or ""),
            state=runtime_binding_state,
        )
    settled = await _settle_command(request)
    settled.update(
        {
            "revision": updated.revision,
            "hostLeaseGeneration": updated.host_lease_generation,
            **(
                {
                    "runtimeBindingRef": runtime_binding.runtimeBindingRef,
                    "runtimeBindingRevision": runtime_binding_state.revision,
                }
                if runtime_binding is not None and runtime_binding_state is not None
                else {}
            ),
        }
    )
    return settled


async def omnigent_ensure_provider_session_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
    from moonmind.workflows.adapters.omnigent_agent_adapter import (
        OmnigentAgentSelection,
        build_omnigent_selection,
        build_omnigent_session_create_payload,
        resolve_omnigent_target,
    )

    request = OmnigentSessionActivityRequest.model_validate(payload)
    command, should_execute = await _claim_command(request)
    if not should_execute:
        return {"commandId": request.command_id, "outcome": command.status}
    agent_request = await _load_intent_request(request)
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
    if session is None:
        raise KeyError(request.session_id)
    if session.provider_session_ref:
        bridge_store = OmnigentBridgeSessionStore(async_session_maker)
        bridge = await bridge_store.get_existing(agent_request.idempotency_key)
        if (
            bridge is None
            or bridge.omnigent_session_id != session.provider_session_ref
        ):
            raise ValueError(
                "provider session exists without matching bridge authority"
            )
        bridge = await bridge_store.record_session_created(
            agent_request.idempotency_key,
            session_id=session.provider_session_ref,
            agent_id=bridge.omnigent_agent_id,
            endpoint_ref=bridge.omnigent_endpoint_ref,
        )
        if not bridge.chat_binding_id:
            raise ValueError("provider session lacks durable chat binding authority")
        runtime_binding = None
        runtime_binding_state = None
        if getattr(agent_request, "omnigent_execution_plan", None) is not None:
            current_runtime_ref = str(
                session.metadata.get("runtimeBindingRef") or ""
            )
            plan_binding = agent_request.omnigent_execution_plan
            runtime_store, runtime_binding_state = (
                await _load_current_runtime_binding(
                    execution_plan_ref=plan_binding.plan_ref,
                    execution_scope_ref=str(session.moonmind_workflow_id or ""),
                    recorded_runtime_binding_ref=current_runtime_ref,
                )
            )
            runtime_binding = runtime_binding_state.binding
            if runtime_binding.omnigentSessionId is None:
                runtime_binding = await runtime_store.update_with_session(
                    runtime_binding_state.binding.runtimeBindingRef,
                    omnigent_session_id=session.provider_session_ref,
                    omnigent_runner_ref=bridge.omnigent_runner_id,
                    chat_binding_ref=bridge.chat_binding_id,
                    expected_revision=runtime_binding_state.revision,
                    expected_fencing_generation=(
                        runtime_binding_state.fencing_generation
                    ),
                )
                runtime_binding_state = await runtime_store.get_state(
                    runtime_binding.runtimeBindingRef
                )
                if runtime_binding_state is None:
                    raise RuntimeError(
                        "reconciled runtime binding could not be reloaded"
                    )
            elif runtime_binding.omnigentSessionId != session.provider_session_ref:
                raise ValueError(
                    "provider session conflicts with recorded runtime binding"
                )

        metadata_patch = {
            "bridgeSessionRef": bridge.bridge_session_id,
            **(
                {
                    "runtimeBindingRef": runtime_binding.runtimeBindingRef,
                    "runtimeBindingRevision": runtime_binding_state.revision,
                    "runtimeBindingFencingGeneration": (
                        runtime_binding_state.fencing_generation
                    ),
                    "runtimeBindingState": runtime_binding_state.state,
                }
                if runtime_binding is not None
                and runtime_binding_state is not None
                else {}
            ),
        }
        if any(
            session.metadata.get(key) != value
            for key, value in metadata_patch.items()
        ):
            async with store.transaction() as repos:
                session = await repos.sessions.bind_runtime_authority(
                    request.session_id,
                    expected_revision=session.revision,
                    expected_fencing_generation=request.fencing_generation,
                    execution_plan_ref=session.execution_plan_ref,
                    metadata_patch=metadata_patch,
                )
        if runtime_binding_state is not None:
            await _project_runtime_binding_to_execution(
                workflow_id=str(session.moonmind_workflow_id or ""),
                state=runtime_binding_state,
            )
        settled = await _settle_command(request)
        settled["revision"] = session.revision
        if runtime_binding is not None and runtime_binding_state is not None:
            settled.update(
                {
                    "runtimeBindingRef": runtime_binding.runtimeBindingRef,
                    "runtimeBindingRevision": runtime_binding_state.revision,
                }
            )
        return settled
    host_id = str(session.metadata.get("omnigentHostRef") or "")
    if not host_id:
        raise ValueError("ready Omnigent host authority is missing")

    selection = build_omnigent_selection(agent_request)
    http_client, client = await _omnigent_client_context()
    try:
        from moonmind.omnigent.execute import _session_authority_observation

        agents = await client.list_agents()

        async def list_agents() -> list[dict[str, Any]]:
            return agents

        async def unsupported_bundle(*_args: Any, **_kwargs: Any) -> object:
            raise ValueError("agent bundle upload is not supported here")

        target = await resolve_omnigent_target(
            selection,
            list_agents=list_agents,
            upload_agent_bundle=unsupported_bundle,
            default_agent=OmnigentAgentSelection(agent_name="Codex"),
        )
        bridge_store = OmnigentBridgeSessionStore(async_session_maker)
        bridge = await bridge_store.get_or_create(
            request=agent_request,
            endpoint_ref=str(session.metadata.get("endpointRef") or "default"),
            agent_id=target.agent_id,
            agent_name=target.agent_name,
            target_metadata={
                "canonicalSessionId": request.session_id,
                "hostId": host_id,
            },
        )
        provider_request = agent_request.model_copy(deep=True)
        parameters = dict(provider_request.parameters or {})
        omnigent = dict(parameters.get("omnigent") or {})
        session_parameters = dict(omnigent.get("session") or {})
        session_parameters.update({"hostType": "external", "hostId": host_id})
        if not bridge.workspace:
            raise ValueError(
                "ready host authority is missing its resolved workspace"
            )
        session_parameters["workspace"] = bridge.workspace
        omnigent["session"] = session_parameters
        parameters["omnigent"] = omnigent
        provider_request = provider_request.model_copy(
            update={"parameters": parameters}
        )
        create_payload = build_omnigent_session_create_payload(
            request=provider_request,
            selection=build_omnigent_selection(provider_request),
            target=target,
        )
        create_payload["idempotency_key"] = request.session_id
        created = await client.create_session(create_payload)
        provider_session_id = str(
            created.get("id") or created.get("sessionId") or created.get("session_id") or ""
        ).strip()
        if not provider_session_id:
            raise RuntimeError("Omnigent create session response omitted session identity")
        provider_snapshot = await client.get_session(provider_session_id)
        session_capabilities, session_status = _session_authority_observation(
            provider_snapshot
        )
    except Exception:
        # Session creation carries the canonical session id as the provider
        # idempotency key. Leave the durable claim resumable so an Activity
        # retry replays that same logical command instead of creating a second
        # session or parking an unreconcilable attachment ambiguity.
        raise
    finally:
        await http_client.aclose()

    bridge = await bridge_store.attach_session(
        agent_request.idempotency_key, provider_session_id
    )
    bridge = await bridge_store.record_session_created(
        agent_request.idempotency_key,
        session_id=provider_session_id,
        agent_id=bridge.omnigent_agent_id,
        endpoint_ref=bridge.omnigent_endpoint_ref,
        capabilities=session_capabilities,
        session_status=session_status,
    )
    if not bridge.chat_binding_id:
        raise ValueError("provider session lacks durable chat binding authority")
    runtime_binding = None
    runtime_binding_state = None
    if getattr(agent_request, "omnigent_execution_plan", None) is not None:
        current_runtime_ref = str(session.metadata.get("runtimeBindingRef") or "")
        plan_binding = agent_request.omnigent_execution_plan
        runtime_store, current_runtime_state = await _load_current_runtime_binding(
            execution_plan_ref=plan_binding.plan_ref,
            execution_scope_ref=str(session.moonmind_workflow_id or ""),
            recorded_runtime_binding_ref=current_runtime_ref,
        )
        runtime_binding = await runtime_store.update_with_session(
            current_runtime_state.binding.runtimeBindingRef,
            omnigent_session_id=provider_session_id,
            omnigent_runner_ref=bridge.omnigent_runner_id,
            chat_binding_ref=bridge.chat_binding_id,
            expected_revision=current_runtime_state.revision,
            expected_fencing_generation=current_runtime_state.fencing_generation,
        )
        runtime_binding_state = await runtime_store.get_state(
            runtime_binding.runtimeBindingRef
        )
        if runtime_binding_state is None:
            raise RuntimeError("session-bound runtime binding could not be reloaded")
    async with store.transaction() as repos:
        attached = await repos.sessions.attach_provider_session(
            request.session_id,
            provider_session_id,
            expected_revision=request.expected_revision,
            expected_fencing_generation=request.fencing_generation,
        )
        attached = await repos.sessions.bind_runtime_authority(
            request.session_id,
            expected_revision=attached.revision,
            expected_fencing_generation=attached.fencing_generation,
            execution_plan_ref=attached.execution_plan_ref,
            metadata_patch={
                "bridgeSessionRef": bridge.bridge_session_id,
                **(
                    {
                        "runtimeBindingRef": runtime_binding.runtimeBindingRef,
                        "runtimeBindingRevision": runtime_binding_state.revision,
                        "runtimeBindingFencingGeneration": (
                            runtime_binding_state.fencing_generation
                        ),
                        "runtimeBindingState": runtime_binding_state.state,
                    }
                    if runtime_binding is not None
                    and runtime_binding_state is not None
                    else {}
                ),
            },
        )
    if runtime_binding_state is not None:
        await _project_runtime_binding_to_execution(
            workflow_id=str(attached.moonmind_workflow_id or ""),
            state=runtime_binding_state,
        )
    settled = await _settle_command(request)
    settled.update(
        {
            "revision": attached.revision,
            **(
                {
                    "runtimeBindingRef": runtime_binding.runtimeBindingRef,
                    "runtimeBindingRevision": runtime_binding_state.revision,
                }
                if runtime_binding is not None and runtime_binding_state is not None
                else {}
            ),
        }
    )
    return settled


async def omnigent_submit_turn_activity(payload: Mapping[str, Any]) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
    from moonmind.omnigent.execute import (
        _persisted_pre_dispatch_item_ids,
        _build_omnigent_first_message,
        _first_message_text,
        _first_message_marker,
        _snapshot_item_ids,
        _snapshot_contains_first_message_marker,
    )
    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.workflows.adapters.omnigent_agent_adapter import build_omnigent_selection

    request = OmnigentSessionActivityRequest.model_validate(payload)
    command, should_execute = await _claim_command(request)
    if not should_execute:
        return {"commandId": request.command_id, "outcome": command.status}
    agent_request = await _load_intent_request(request)
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
        turn = (
            await repos.turn_attempts.get(request.turn_attempt_id)
            if request.turn_attempt_id
            else None
        )
    if session is None or not session.provider_session_ref or turn is None:
        raise ValueError("provider session and turn authority are required")
    bridge_store = OmnigentBridgeSessionStore(async_session_maker)
    active_instruction_ref = str(
        session.metadata.get(f"turnInstructionRef:{turn.turn_attempt_id}") or ""
    ).strip()
    if active_instruction_ref:
        original_bridge = await bridge_store.get_existing(
            agent_request.idempotency_key
        )
        if original_bridge is None:
            raise ValueError("canonical Omnigent bridge authority is missing")
        agent_request = agent_request.model_copy(
            update={
                "instruction_ref": active_instruction_ref,
                "idempotency_key": turn.idempotency_key,
            }
        )
        continuation_bridge = await bridge_store.get_or_create(
            request=agent_request,
            endpoint_ref=original_bridge.omnigent_endpoint_ref,
            agent_id=original_bridge.omnigent_agent_id,
            agent_name=original_bridge.omnigent_agent_name,
            target_metadata={
                "canonicalSessionId": request.session_id,
                "continuationTurnAttemptId": turn.turn_attempt_id,
            },
            workflow_id=session.moonmind_workflow_id,
            agent_run_id=session.moonmind_agent_run_id,
        )
        await bridge_store.attach_session(
            continuation_bridge.idempotency_key,
            session.provider_session_ref,
        )
    durable_bridge = await bridge_store.get_existing(
        agent_request.idempotency_key
    )
    if durable_bridge is not None and session.metadata.get(
        "bridgeSessionRef"
    ) != durable_bridge.bridge_session_id:
        # The active turn's bridge row is durable correlation authority for
        # transcript and terminal evidence. Continuations receive a distinct
        # bridge row, so advance the canonical session projection before the
        # provider message side effect.
        async with store.transaction() as repos:
            current = await repos.sessions.get(request.session_id)
            if current is None:
                raise KeyError(request.session_id)
            session = await repos.sessions.bind_runtime_authority(
                request.session_id,
                expected_revision=current.revision,
                expected_fencing_generation=request.fencing_generation,
                metadata_patch={
                    "bridgeSessionRef": durable_bridge.bridge_session_id,
                },
            )
    if durable_bridge is not None and durable_bridge.first_message_state in {
        "posted",
        "terminal",
    }:
        async with store.transaction() as repos:
            current_turn = await repos.turn_attempts.get(turn.turn_attempt_id)
            if current_turn and current_turn.state == "prepared":
                await repos.turn_attempts.advance_state(
                    turn.turn_attempt_id,
                    "accepted",
                    expected_revision=current_turn.revision,
                    expected_fencing_generation=request.fencing_generation,
                )
        return await _settle_command(request)

    selection = build_omnigent_selection(agent_request)
    message = await _build_omnigent_first_message(
        request=agent_request,
        prompt=selection.prompt,
        artifact_gateway=LocalOmnigentArtifactGateway(),
    )
    marker = _first_message_marker(request=agent_request)
    if marker not in _first_message_text(message):
        message["data"]["content"][0]["text"] = (
            f"{_first_message_text(message)}\n\n{marker}".strip()
        )
    digest = hashlib.sha256(_json_bytes(message)).hexdigest()
    await bridge_store.mark_prepared(
        agent_request.idempotency_key, digest=digest, marker=marker
    )
    http_client, client = await _omnigent_client_context()
    try:
        durable_bridge = await bridge_store.get_existing(
            agent_request.idempotency_key
        )
        baseline_item_ids = (
            _persisted_pre_dispatch_item_ids(durable_bridge)
            if durable_bridge is not None
            else None
        )
        if baseline_item_ids is None and durable_bridge is not None:
            initial_snapshot = await client.get_session(
                session.provider_session_ref
            )
            baseline_item_ids = _snapshot_item_ids(initial_snapshot)
            if baseline_item_ids is not None:
                durable_bridge = (
                    await bridge_store.record_first_message_item_frontier(
                        agent_request.idempotency_key,
                        item_ids=sorted(baseline_item_ids),
                    )
                )
        if durable_bridge is not None and durable_bridge.first_message_state == "posting":
            snapshot = await client.get_session(session.provider_session_ref)
            if not _snapshot_contains_first_message_marker(
                snapshot, digest=digest, marker=marker
            ):
                await _settle_command(request, delivery_unknown=True)
                return {"commandId": request.command_id, "outcome": "delivery_unknown"}
            response = None
        else:
            await bridge_store.mark_posting(agent_request.idempotency_key)
            response = await client.post_event(session.provider_session_ref, message)
        await bridge_store.mark_posted(agent_request.idempotency_key, response=response)
    except Exception:
        await _settle_command(request, delivery_unknown=True)
        raise
    finally:
        await http_client.aclose()

    async with store.transaction() as repos:
        current_turn = await repos.turn_attempts.get(turn.turn_attempt_id)
        if current_turn is not None and current_turn.state == "prepared":
            await repos.turn_attempts.advance_state(
                turn.turn_attempt_id,
                "accepted",
                expected_revision=current_turn.revision,
                expected_fencing_generation=request.fencing_generation,
            )
    return await _settle_command(request)


async def omnigent_heartbeat_host_lease_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Renew the session's durable host lease for one supervisor poll cycle.

    The lease TTL is long, but the janitor reclaims an assigned host after 90
    seconds without a heartbeat, so a normal turn longer than that would have its
    host stopped underneath the supervisor. The supervisor calls this every poll
    cycle (at most ``SNAPSHOT_INTERVAL_SECONDS`` apart) to hold the same renewal
    cadence the legacy coordinator maintained.

    This is a renewal, not authority: when the lease is gone, terminal, or
    already claimed for cleanup by another owner, report that outcome so the
    reconciler acts on durable evidence instead of failing the session.
    """

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
    from moonmind.omnigent.host_failures import OmnigentOAuthHostError
    from moonmind.omnigent.oauth_hosts import (
        HEARTBEAT_HOST_STATES,
        HOST_CLEANUP_CLAIMED_ERROR_CODE,
        OmnigentOAuthHostRepository,
    )

    request = OmnigentSessionActivityRequest.model_validate(payload)
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
    if session is None:
        raise KeyError(request.session_id)
    if not session.host_lease_ref:
        return {"hostLeaseHeartbeat": "not_attached"}
    if session.cleanup_state == "leases_released":
        return {"hostLeaseHeartbeat": "released"}
    hosts = OmnigentOAuthHostRepository(async_session_maker)
    lease = await hosts.get_host_lease(session.host_lease_ref)
    if lease is None:
        return {"hostLeaseHeartbeat": "missing"}
    if lease.status not in HEARTBEAT_HOST_STATES:
        # Draining, stopped, or failed leases are owned by cleanup, not by
        # renewal. Renewing here would fight the owner that won the fence.
        return {"hostLeaseHeartbeat": "not_renewable", "status": lease.status}
    try:
        renewed = await hosts.heartbeat_host_lease(lease.lease_id)
    except OmnigentOAuthHostError as exc:
        if getattr(exc, "code", None) == HOST_CLEANUP_CLAIMED_ERROR_CODE:
            return {"hostLeaseHeartbeat": "cleanup_claimed"}
        raise
    return {
        "hostLeaseHeartbeat": "renewed",
        "status": renewed.status,
        "hostLeaseRef": renewed.lease_id,
    }


async def omnigent_read_event_batch_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore, compute_digest
    from moonmind.omnigent.bridge_events import normalize_omnigent_observation

    request = OmnigentSessionActivityRequest.model_validate(payload)
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
    if session is None or not session.provider_session_ref:
        return {
            "observationCount": 0,
            "eventCursor": session.provider_event_cursor if session else None,
        }
    http_client, client = await _omnigent_client_context()
    events: list[dict[str, Any]] = []

    async def collect_bounded_batch() -> None:
        async for event in client.stream_events(session.provider_session_ref):
            if isinstance(event, Mapping):
                events.append(dict(event))
            if len(events) >= _MAX_EVENT_BATCH:
                break

    try:
        try:
            await asyncio.wait_for(
                collect_bounded_batch(),
                timeout=_EVENT_READ_SECONDS,
            )
        except asyncio.TimeoutError:
            pass
        except Exception:
            # Provider streams are wake/latency optimizations, never terminal
            # authority. A bounded disconnect must still allow the supervisor
            # to perform its authoritative snapshot in the same iteration.
            return {
                "observationCount": 0,
                "eventCursor": session.provider_event_cursor,
                "readStatus": "unavailable",
            }
    finally:
        await http_client.aclose()
    if not events:
        return {"observationCount": 0, "eventCursor": session.provider_event_cursor}

    cursor = session.provider_event_cursor
    terminal_seen = False
    async with store.transaction() as repos:
        for index, event in enumerate(events, start=1):
            status = normalize_omnigent_observation(event)
            terminal_seen = terminal_seen or status in _TERMINAL_PROVIDER_STATES
            source_cursor = str(
                event.get("cursor")
                or event.get("sequence")
                or event.get("id")
                or f"batch-{index}"
            )
            cursor = source_cursor
            digest = compute_digest(event)
            await repos.observations.append(
                observation_id="oob_" + uuid5(NAMESPACE_URL, f"{request.session_id}:{digest}").hex,
                session_id=request.session_id,
                observation_type="provider_event",
                source="provider_stream_batch",
                observed_at=datetime.now(UTC),
                deduplication_key=f"provider-event:{digest}",
                source_digest=digest,
                bounded_index={
                    "eventFrontier": {
                        "observedAt": datetime.now(UTC).isoformat(),
                        "lastCursor": source_cursor,
                        "terminalEventSeen": terminal_seen,
                        "runningEventAfterCursor": status == "running",
                    }
                },
            )
        current = await repos.sessions.get(request.session_id)
        if current is not None:
            await repos.sessions.advance_observation_frontier(
                request.session_id,
                expected_revision=current.revision,
                expected_fencing_generation=request.fencing_generation,
                provider_event_cursor=cursor,
            )
    return {"observationCount": len(events), "eventCursor": cursor}


async def omnigent_observe_snapshot_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
    from moonmind.omnigent.bridge_events import normalize_omnigent_observation
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore, compute_digest
    from moonmind.omnigent.execute import (
        _MARKED_TOOL_ONLY_QUIET_PERIOD_SECONDS,
        _MARKED_TURN_QUIET_PERIOD_SECONDS,
        _marked_turn_item_state,
        _persisted_pre_dispatch_item_ids,
        _snapshot_projects_inactive_turn,
    )

    request = OmnigentSessionActivityRequest.model_validate(payload)
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
        active_turn = (
            await repos.turn_attempts.get(session.active_turn_attempt_id)
            if session is not None and session.active_turn_attempt_id
            else None
        )
        prior_observations = await repos.observations.list_for_session(
            request.session_id, limit=500, latest=True
        )
    if session is None:
        raise KeyError(request.session_id)

    def resource_observations(observed_at: datetime) -> dict[str, Any]:
        lifecycle_consumer_active = session.cleanup_state not in {
            "host_stopped",
            "leases_released",
            "complete",
        }
        host_owned = bool(session.host_lease_ref)
        profile_owned = bool(
            session.provider_profile_id
            and session.metadata.get("providerLeaseRef")
        )
        return {
            "host": {
                "observedAt": observed_at.isoformat(),
                "registered": host_owned and lifecycle_consumer_active,
                "runnerReady": host_owned and lifecycle_consumer_active,
            },
            "profileLease": {
                "observedAt": observed_at.isoformat(),
                "held": profile_owned
                and session.cleanup_state != "leases_released",
                "consumerActive": profile_owned
                and lifecycle_consumer_active,
            },
            "hostLease": {
                "observedAt": observed_at.isoformat(),
                "held": host_owned
                and session.cleanup_state != "leases_released",
                "consumerActive": host_owned and lifecycle_consumer_active,
            },
            "evidence": {
                "observedAt": observed_at.isoformat(),
                "terminalEvidenceAvailable": bool(session.terminal_state),
                "artifactsAvailable": bool(session.terminal_evidence_ref),
            },
        }

    async def persist_resource_observation(
        *, read_status: str
    ) -> dict[str, Any]:
        observed_at = datetime.now(UTC)
        bounded = resource_observations(observed_at)
        digest = compute_digest(
            [
                request.session_id,
                session.revision,
                session.cleanup_state,
                session.terminal_evidence_ref,
            ]
        )
        async with store.transaction() as repos:
            await repos.observations.append(
                observation_id="oob_"
                + uuid5(
                    NAMESPACE_URL,
                    f"{request.session_id}:resource:{digest}",
                ).hex,
                session_id=request.session_id,
                observation_type="control_plane_resource_snapshot",
                source="canonical_control_plane",
                observed_at=observed_at,
                deduplication_key=f"control-plane-resource:{digest}",
                source_digest=digest,
                bounded_index=bounded,
            )
        return {
            "observationCount": 1,
            "snapshotFrontier": session.snapshot_frontier,
            "readStatus": read_status,
        }

    if not session.provider_session_ref:
        return await persist_resource_observation(read_status="not_attached")
    http_client, client = await _omnigent_client_context()
    try:
        try:
            snapshot = await client.get_session(session.provider_session_ref)
        except Exception:
            # A provider restart or transport outage is not terminal evidence.
            # Durable host/lease observations still let ordered cleanup release
            # capacity after the provider disappears; provider terminality
            # remains unavailable and is never invented.
            return await persist_resource_observation(
                read_status="unavailable"
            )
    finally:
        await http_client.aclose()
    status = normalize_omnigent_observation(snapshot)
    observed_at = datetime.now(UTC)
    digest = compute_digest(snapshot)
    bridge_store = OmnigentBridgeSessionStore(async_session_maker)
    bridge_ref = str(session.metadata.get("bridgeSessionRef") or "").strip()
    bridge = (
        await bridge_store.get_bridge_session(bridge_ref)
        if bridge_ref
        else None
    )
    if bridge is None and active_turn is not None:
        # Replay compatibility for a session recorded before active bridge
        # identity was projected into canonical session metadata.
        bridge = await bridge_store.get_existing(active_turn.idempotency_key)
    marker = str(getattr(bridge, "first_message_marker", "") or "").strip()
    baseline_item_ids = (
        _persisted_pre_dispatch_item_ids(bridge) if bridge is not None else None
    )
    turn_state = (
        _marked_turn_item_state(
            snapshot,
            marker=marker,
            baseline_item_ids=baseline_item_ids,
        )
        if marker
        else {
            "boundarySource": None,
            "progress": False,
            "terminalAssistantAfterWork": False,
            "unfinishedToolCall": False,
            "signature": None,
        }
    )
    structural_candidate = bool(
        turn_state["progress"]
        and _snapshot_projects_inactive_turn(snapshot)
        and not turn_state["unfinishedToolCall"]
    )
    signature = turn_state["signature"]
    required_quiet_seconds = (
        _MARKED_TURN_QUIET_PERIOD_SECONDS
        if turn_state["terminalAssistantAfterWork"]
        else _MARKED_TOOL_ONLY_QUIET_PERIOD_SECONDS
    )
    previous_candidate: Mapping[str, Any] | None = None
    previous_observed_at: datetime | None = None
    for observation in reversed(prior_observations):
        if observation.observation_type != "provider_snapshot":
            continue
        candidate = dict(observation.bounded_index or {}).get(
            "snapshotCandidate"
        )
        if isinstance(candidate, Mapping):
            previous_candidate = candidate
            previous_observed_at = observation.observed_at
        # Only the immediately preceding authoritative snapshot may extend a
        # quiet window. Any intervening non-candidate snapshot resets it.
        break
    if previous_observed_at is not None and previous_observed_at.tzinfo is None:
        previous_observed_at = previous_observed_at.replace(tzinfo=UTC)
    turn_complete = bool(
        structural_candidate
        and isinstance(signature, tuple)
        and previous_candidate is not None
        and previous_candidate.get("attemptId")
        == (active_turn.turn_attempt_id if active_turn is not None else None)
        and previous_candidate.get("signature") == list(signature)
        and previous_observed_at is not None
        and (observed_at - previous_observed_at).total_seconds()
        >= required_quiet_seconds
    )
    correlated_failure = bool(
        status in {
            "failed",
            "error",
            "errored",
            "canceled",
            "cancelled",
            "timed_out",
            "timeout",
        }
        and turn_state["boundarySource"] is not None
        and turn_state["progress"]
    )
    effective_status = status
    if status in {"completed", "complete", "success", "succeeded"} and not turn_complete:
        # A stock interactive session can project a transient completed edge
        # before later tools. Until the marked turn is quiescent, expose idle
        # so the reducer requests more transcript evidence.
        effective_status = "idle"
    elif status in _TERMINAL_PROVIDER_STATES and not (
        turn_complete or correlated_failure
    ):
        # An uncorrelated terminal can be a replay from a prior turn/provider
        # epoch. It is a wake source, not current-turn terminal authority.
        effective_status = "running"
    outcome = None
    if effective_status in {"completed", "complete", "success", "succeeded", "idle"}:
        outcome = "success"
    elif effective_status in {"canceled", "cancelled"}:
        outcome = "cancelled"
    elif effective_status in _TERMINAL_PROVIDER_STATES:
        outcome = "failure"
    bounded: dict[str, Any] = {
        **resource_observations(observed_at),
        "providerSession": {
            "observedAt": observed_at.isoformat(),
            "present": True,
            "providerSessionId": session.provider_session_ref,
            "rawStatus": effective_status,
            "openToolCall": bool(
                snapshot.get("active_tool_call")
                or snapshot.get("activeToolCall")
            ),
            "cursor": session.provider_event_cursor,
            "snapshotDigest": digest,
        },
        "compatibility": {
            "observedAt": observed_at.isoformat(),
            "compatibilityVersion": "v1",
            "runtimeReady": True,
        },
    }
    if structural_candidate and isinstance(signature, tuple):
        bounded["snapshotCandidate"] = {
            "attemptId": (
                active_turn.turn_attempt_id if active_turn is not None else None
            ),
            "signature": list(signature),
            "terminalAssistantAfterWork": bool(
                turn_state["terminalAssistantAfterWork"]
            ),
        }
    if turn_complete or correlated_failure:
        bounded["providerTurn"] = {
            "observedAt": observed_at.isoformat(),
            "attemptId": session.active_turn_attempt_id,
            "turnComplete": True,
            "rawStatus": effective_status,
            "outcome": outcome,
        }
    # A quiet period is confirmed by observing the *same* provider snapshot
    # twice, so the digest alone cannot identify the observation: the confirming
    # read would dedup against the earlier pending one and its
    # providerTurn.turnComplete would never persist, leaving reconciliation to
    # poll forever on the original idle snapshot. Discriminate on the turn
    # confirmation the observation actually carries, which still collapses
    # retries of the same read because a retry recomputes the same marker.
    turn_confirmation = (
        f"turn:{session.active_turn_attempt_id}:complete"
        if turn_complete or correlated_failure
        else "turn:pending"
    )
    resource_frontier = compute_digest(
        {
            "cleanupState": session.cleanup_state,
            "terminalState": session.terminal_state,
            "terminalEvidenceRef": session.terminal_evidence_ref,
            "hostLeaseRef": session.host_lease_ref,
            "providerLeaseRef": session.metadata.get("providerLeaseRef"),
        }
    )
    async with store.transaction() as repos:
        await repos.observations.append(
            observation_id=(
                "oob_"
                + uuid5(
                    NAMESPACE_URL,
                    f"{request.session_id}:snapshot:{digest}:"
                    f"{turn_confirmation}:{resource_frontier}",
                ).hex
            ),
            session_id=request.session_id,
            observation_type="provider_snapshot",
            source="provider_authoritative_snapshot",
            observed_at=observed_at,
            deduplication_key=(
                f"provider-snapshot:{digest}:{turn_confirmation}:"
                f"{resource_frontier}"
            ),
            source_digest=digest,
            bounded_index=bounded,
        )
        current = await repos.sessions.get(request.session_id)
        if current is not None:
            await repos.sessions.advance_observation_frontier(
                request.session_id,
                expected_revision=current.revision,
                expected_fencing_generation=request.fencing_generation,
                snapshot_frontier=digest,
            )
    return {"observationCount": 1, "snapshotFrontier": digest}


async def omnigent_record_terminal_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore

    request = OmnigentSessionActivityRequest.model_validate(payload)
    command, should_execute = await _claim_command(request)
    if not should_execute:
        return {"commandId": request.command_id, "outcome": command.status}
    terminal = {
        "success": "completed",
        "failure": "failed",
        "cancelled": "canceled",
    }.get(str(request.terminal_outcome or ""), "failed")
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        current = await repos.sessions.get(request.session_id)
        commands = await repos.commands.list_for_session(
            request.session_id, limit=500, latest=True
        )
        if current is not None and current.desired_state == "timeout":
            delivery_ambiguous = any(
                command.command_type == "submit_turn"
                and command.turn_attempt_id == current.active_turn_attempt_id
                and command.delivery_ambiguous
                for command in commands
            )
            terminal = (
                "delivery_unknown" if delivery_ambiguous else "timed_out"
            )
        recorded = await repos.sessions.mark_terminal(
            request.session_id,
            terminal,
            expected_revision=request.expected_revision,
            expected_fencing_generation=request.fencing_generation,
        )
        turn = (
            await repos.turn_attempts.get(request.turn_attempt_id)
            if request.turn_attempt_id
            else None
        )
        if turn is not None and not turn.is_terminal:
            await repos.turn_attempts.mark_terminal(
                turn.turn_attempt_id,
                terminal,
                expected_revision=turn.revision,
                expected_fencing_generation=request.fencing_generation,
                attempt_outcome=request.terminal_outcome,
            )
    settled = await _settle_command(request)
    settled["revision"] = recorded.revision
    return settled


async def omnigent_persist_failure_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist exhausted-work evidence and a janitor-safe cleanup handoff.

    The Activity records only typed reason codes and refs. It never copies an
    exception message into durable state. A pre-terminal failure becomes the
    canonical compact terminal evidence. A cleanup failure preserves the
    primary terminal result and records an independently discoverable handoff.
    """

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import (
        ControlPlaneOutcome,
        FencingConflictError,
        OmnigentControlPlaneStore,
        RevisionConflictError,
    )

    request = OmnigentPersistFailureRequest.model_validate(payload)
    store = OmnigentControlPlaneStore(async_session_maker)
    evidence_metadata_key = (
        "cleanupEvidenceRef"
        if request.status == "cleanup_incomplete"
        else "workflowFailureEvidenceRef"
    )
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
        if session is None:
            raise KeyError(request.session_id)
        command = (
            await repos.commands.get(request.command_id)
            if request.command_id
            else None
        )
    if session.fencing_generation != request.fencing_generation:
        raise FencingConflictError(
            "Omnigent failure recorder supervisor fence changed"
        )
    if (
        session.intent_ref != request.compiled_execution_intent_ref
        or session.intent_digest != request.compiled_execution_intent_digest
    ):
        raise ValueError("Omnigent failure recorder intent authority changed")
    if command is not None:
        if command.session_id != request.session_id:
            raise ValueError("Omnigent failure command belongs to another session")
        if command.fencing_generation != request.fencing_generation:
            raise FencingConflictError(
                "Omnigent failure command supervisor fence changed"
            )
        if command.status == "applied":
            # The side effect and its command receipt are authoritative. A lost
            # Activity response must cause reconciliation, never manufacture a
            # contradictory terminal failure.
            return {
                "failurePersisted": False,
                "reconcileRequired": True,
                "commandOutcome": "already_applied",
            }
    status = request.status
    if command is not None and command.delivery_ambiguous:
        status = "delivery_unknown"
    execution_authority_metadata = await _session_execution_authority_metadata(
        session
    )

    existing_ref = str(session.metadata.get(evidence_metadata_key) or "").strip()
    if existing_ref:
        existing = await _read_json_artifact(existing_ref)
        raw_terminal = existing.get("terminalResult")
        if not isinstance(raw_terminal, Mapping):
            raise ValueError("persisted Omnigent failure evidence is incomplete")
        terminal = OmnigentSessionTerminalResult.model_validate(raw_terminal)
        terminal = _bind_terminal_evidence_ref(
            terminal,
            existing_ref,
            metadata_key=evidence_metadata_key,
        )
        return {
            "terminalResultRef": existing_ref,
            "terminalResult": terminal.model_dump(mode="json", by_alias=True),
            "cleanupOwner": (
                _JANITOR_OWNER if status == "cleanup_incomplete" else None
            ),
        }
    if session.revision != request.expected_revision:
        raise RevisionConflictError(
            "Omnigent failure recorder expected revision changed"
        )

    primary_terminal: OmnigentSessionTerminalResult | None = None
    primary_ref = str(
        (
            session.metadata.get("workflowFailureEvidenceRef")
            if status == "cleanup_incomplete"
            else None
        )
        or session.terminal_evidence_ref
        or ""
    ).strip()
    if primary_ref:
        primary_evidence = await _read_json_artifact(primary_ref)
        raw_primary = primary_evidence.get("terminalResult")
        if isinstance(raw_primary, Mapping):
            primary_terminal = _bind_terminal_evidence_ref(
                OmnigentSessionTerminalResult.model_validate(raw_primary),
                primary_ref,
            )

    failure_class = (
        "integration_error"
        if status
        in {
            "integration_unavailable",
            "delivery_unknown",
            "reconciliation_quarantined",
            "cleanup_incomplete",
        }
        else "execution_error"
    )
    if primary_terminal is not None and status == "cleanup_incomplete":
        primary_result = primary_terminal.result
        metadata = dict(primary_result.metadata)
        metadata.update(
            {
                "omnigentSessionStatus": status,
                "primaryOmnigentSessionStatus": primary_terminal.status,
                "reasonCode": request.reason_code,
            }
        )
        metadata.update(
            {
                "cleanupOwner": _JANITOR_OWNER,
                "janitorRequired": True,
                **execution_authority_metadata,
            }
        )
        result = primary_result.model_copy(update={"metadata": metadata})
    elif primary_terminal is not None:
        primary_result = primary_terminal.result
        metadata = dict(primary_result.metadata)
        metadata.update(
            {
                "omnigentSessionStatus": status,
                "primaryOmnigentSessionStatus": primary_terminal.status,
                "reasonCode": request.reason_code,
                **execution_authority_metadata,
            }
        )
        result = primary_result.model_copy(
            update={
                "summary": (
                    "Omnigent session stopped with durable "
                    f"{status.replace('_', ' ')} evidence"
                ),
                "failure_class": failure_class,
                "metadata": metadata,
            }
        )
    else:
        result = AgentRunResult(
            summary=(
                "Omnigent session stopped with durable "
                f"{status.replace('_', ' ')} evidence"
            ),
            failureClass=failure_class,
            metadata={
                "canonicalSessionId": request.session_id,
                "omnigentSessionStatus": status,
                "reasonCode": request.reason_code,
                **execution_authority_metadata,
                **(
                    {
                        "cleanupOwner": _JANITOR_OWNER,
                        "janitorRequired": True,
                    }
                    if status == "cleanup_incomplete"
                    else {}
                ),
            },
        )
    runtime_state = None
    if request.omnigent_execution_plan is not None:
        execution_plan = await _load_verified_execution_plan(
            request.omnigent_execution_plan
        )
        recorded_runtime_ref = str(
            session.metadata.get("runtimeBindingRef")
            or request.runtime_binding_ref
            or ""
        )
        if recorded_runtime_ref:
            _runtime_store, runtime_state = (
                await _load_current_runtime_binding(
                    execution_plan_ref=execution_plan.planRef,
                    execution_scope_ref=str(
                        session.moonmind_workflow_id or ""
                    ),
                    recorded_runtime_binding_ref=recorded_runtime_ref,
                )
            )
            if request.runtime_binding_ref is not None and (
                request.runtime_binding_ref
                != runtime_state.binding.runtimeBindingRef
                or request.runtime_binding_revision != runtime_state.revision
                or request.runtime_binding_fencing_generation
                != runtime_state.fencing_generation
            ):
                raise ValueError(
                    "failure recorder runtime-binding authority is obsolete"
                )
    terminal = OmnigentSessionTerminalResult(status=status, result=result)
    terminal = _bind_terminal_plan_authority(
        terminal,
        plan_binding=request.omnigent_execution_plan,
        runtime_binding_state=runtime_state,
    )
    failure_ref = await _write_json_artifact(
        name=(
            "omnigent.session.cleanup-incomplete.json"
            if status == "cleanup_incomplete"
            else "omnigent.session.failure.json"
        ),
        artifact_type=(
            "omnigent.session_cleanup_handoff"
            if status == "cleanup_incomplete"
            else "omnigent.session_failure"
        ),
        payload={
            "schemaVersion": "omnigent-session-failure-evidence/v1",
            "sessionId": request.session_id,
            "failedActivity": request.failed_activity,
            "reasonCode": request.reason_code,
            "decisionId": request.decision_id,
            "commandId": request.command_id,
            "primaryTerminalState": session.terminal_state,
            "cleanupOwner": (
                _JANITOR_OWNER if status == "cleanup_incomplete" else None
            ),
            "janitorRequired": status == "cleanup_incomplete",
            "terminalResult": terminal.model_dump(mode="json", by_alias=True),
        },
    )

    async with store.transaction() as repos:
        current = await repos.sessions.get(request.session_id)
        if current is None:
            raise KeyError(request.session_id)
        if current.fencing_generation != request.fencing_generation:
            raise FencingConflictError(
                "Omnigent failure recorder supervisor fence changed"
            )
        if current.revision != request.expected_revision:
            raise RevisionConflictError(
                "Omnigent failure recorder expected revision changed"
            )
        if command is not None:
            command_result = await repos.commands.record_command_failure(
                command.command_id,
                owner_class="omnigent_session_activity",
                claim_token=(
                    f"omnigent-session:{request.session_id}:{command.command_id}"
                ),
                result_ref=failure_ref,
            )
            if command_result.outcome is ControlPlaneOutcome.DELIVERY_UNKNOWN:
                status = "delivery_unknown"
        if status == "cleanup_incomplete":
            current = await repos.sessions.bind_runtime_authority(
                request.session_id,
                expected_revision=current.revision,
                expected_fencing_generation=current.fencing_generation,
                metadata_patch={
                    evidence_metadata_key: failure_ref,
                    "cleanupOwner": _JANITOR_OWNER,
                    "janitorRequired": True,
                    "cleanupFailedActivity": request.failed_activity,
                },
            )
            current = await repos.sessions.update_lifecycle(
                request.session_id,
                expected_revision=current.revision,
                expected_fencing_generation=current.fencing_generation,
                cleanup_state="cleanup_incomplete",
                historical_read_state="artifact",
            )
            await repos.cleanup.record_janitor_handoff(
                request.session_id,
                owner_class=_JANITOR_OWNER,
            )
        else:
            if current.terminal_state is None:
                current = await repos.sessions.mark_terminal(
                    request.session_id,
                    status,
                    expected_revision=current.revision,
                    expected_fencing_generation=current.fencing_generation,
                    terminal_evidence_ref=failure_ref,
                )
            elif current.terminal_evidence_ref is None:
                current = await repos.sessions.attach_terminal_evidence(
                    request.session_id,
                    terminal_evidence_ref=failure_ref,
                    expected_revision=current.revision,
                    expected_fencing_generation=current.fencing_generation,
                )
            current = await repos.sessions.bind_runtime_authority(
                request.session_id,
                expected_revision=current.revision,
                expected_fencing_generation=current.fencing_generation,
                metadata_patch={evidence_metadata_key: failure_ref},
            )

    terminal = _bind_terminal_evidence_ref(
        terminal,
        failure_ref,
        metadata_key=evidence_metadata_key,
    )
    result_metadata = dict(terminal.result.metadata)
    preserved_refs = [*terminal.result.output_refs]
    if primary_terminal is not None and session.terminal_evidence_ref:
        preserved_refs.append(session.terminal_evidence_ref)
    preserved_refs.append(failure_ref)
    result = terminal.result.model_copy(
        update={
            "output_refs": list(dict.fromkeys(preserved_refs)),
            "metadata": result_metadata,
        }
    )
    terminal = terminal.model_copy(update={"status": status, "result": result})
    return {
        "terminalResultRef": failure_ref,
        "terminalResult": terminal.model_dump(mode="json", by_alias=True),
        "cleanupOwner": _JANITOR_OWNER if status == "cleanup_incomplete" else None,
    }


async def omnigent_harvest_evidence_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore

    request = OmnigentSessionActivityRequest.model_validate(payload)
    command, should_execute = await _claim_command(request)
    if not should_execute:
        return {
            "commandId": request.command_id,
            "outcome": command.status,
            "resultRef": command.result_ref,
        }
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
        observations = await repos.observations.list_for_session(
            request.session_id, limit=500, latest=True
        )
    if session is None or session.terminal_state is None:
        raise ValueError("terminal session authority is required before harvesting")
    existing_evidence_ref = str(
        session.metadata.get("harvestedEvidenceRef") or ""
    )
    if existing_evidence_ref:
        return {
            "commandId": request.command_id,
            "outcome": "already_harvested",
            "harvestedEvidenceRef": existing_evidence_ref,
            "revision": session.revision,
        }
    snapshot_index, _frontier = _observation_payload(observations)
    evidence_ref = await _write_json_artifact(
        name="omnigent.session.harvest.json",
        artifact_type="omnigent.session_harvest",
        payload={
            "schemaVersion": "omnigent-session-harvest/v1",
            "sessionId": request.session_id,
            "providerSessionRef": session.provider_session_ref,
            "terminalState": session.terminal_state,
            "observationFrontier": snapshot_index,
        },
    )
    async with store.transaction() as repos:
        attached = await repos.sessions.bind_runtime_authority(
            request.session_id,
            expected_revision=request.expected_revision,
            expected_fencing_generation=request.fencing_generation,
            metadata_patch={"harvestedEvidenceRef": evidence_ref},
        )
    return {
        "commandId": request.command_id,
        "outcome": "harvested",
        "harvestedEvidenceRef": evidence_ref,
        "revision": attached.revision,
    }


async def omnigent_publish_workspace_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish the authoritative workspace, then attach the final result."""

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
    from moonmind.omnigent.execution_profiles import selection_from_request
    from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
    from moonmind.omnigent.profile_bound_execution import (
        OmnigentProfileBoundExecutionCoordinator,
    )
    from moonmind.omnigent.workspace_intent import compile_workspace_intent

    request = OmnigentSessionActivityRequest.model_validate(payload)
    command, should_execute = await _claim_command(request)
    if not should_execute:
        return {
            "commandId": request.command_id,
            "outcome": command.status,
            "terminalResultRef": command.result_ref,
        }
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
    if session is None or session.terminal_state is None:
        raise ValueError("terminal session authority is required for publication")
    if session.terminal_evidence_ref:
        settled = await _settle_command(
            request, result_ref=session.terminal_evidence_ref
        )
        settled["terminalResultRef"] = session.terminal_evidence_ref
        return settled
    harvested_ref = str(session.metadata.get("harvestedEvidenceRef") or "")
    if not harvested_ref:
        raise ValueError("terminal evidence must be harvested before publication")
    agent_request = await _load_intent_request(request)
    workflow_id = session.moonmind_workflow_id
    step_execution_id = session.step_execution_id or request.session_id
    if agent_request.remediation_workspace is not None:
        workspace_locator = dict(
            agent_request.remediation_workspace.get("destinationWorkspaceLocator")
            or {}
        )
        publish_mode = str(
            (agent_request.parameters or {}).get("publishMode") or "none"
        )
        base_branch = str(
            (agent_request.workspace_spec or {}).get("startingBranch") or ""
        ) or None
    else:
        workspace_intent = compile_workspace_intent(
            agent_request,
            workflow_id=workflow_id,
            step_execution_id=step_execution_id,
        )
        workspace_locator = workspace_intent.workspace_locator_payload()
        publish_mode = workspace_intent.publish_mode
        base_branch = workspace_intent.starting_branch
    if publish_mode == "none":
        publication = {"status": "skipped", "publishMode": "none"}
    else:
        github_token = await (
            OmnigentProfileBoundExecutionCoordinator._github_token(
                agent_request
            )
        )
        http_client, client = await _omnigent_client_context()
        try:
            publication = await OmnigentOAuthHostRuntime(
                client=client
            ).publish_workspace(
                workspace_locator=workspace_locator,
                current_workflow_id=workflow_id,
                current_step_execution_id=step_execution_id,
                publication_identity=agent_request.idempotency_key,
                publish_mode=publish_mode,
                base_branch=base_branch,
                repository=str(
                    (agent_request.parameters or {}).get("repository") or ""
                ).strip(),
                github_token=github_token,
            )
        finally:
            await http_client.aclose()
    publication_ref = str(session.metadata.get("publicationEvidenceRef") or "")
    if not publication_ref:
        publication_ref = await _write_json_artifact(
            name="omnigent.session.publication.json",
            artifact_type="omnigent.session_publication",
            payload={
                "schemaVersion": "omnigent-session-publication/v1",
                "sessionId": request.session_id,
                "publication": publication,
            },
        )
        async with store.transaction() as repos:
            session = await repos.sessions.bind_runtime_authority(
                request.session_id,
                expected_revision=session.revision,
                expected_fencing_generation=request.fencing_generation,
                metadata_patch={"publicationEvidenceRef": publication_ref},
            )
    execution_plan = (
        await _load_verified_execution_plan(request.omnigent_execution_plan)
        if request.omnigent_execution_plan is not None
        else None
    )
    runtime_state = None
    if execution_plan is not None:
        from moonmind.omnigent.harness_platform.stores import (
            DbRuntimeBindingStore,
        )

        runtime_state = await DbRuntimeBindingStore(
            async_session_maker
        ).get_current_state(
            execution_plan.planRef,
            str(session.moonmind_workflow_id or ""),
        )
        if runtime_state is None:
            raise ValueError(
                "terminal capture requires the current runtime binding"
            )
    async with store.transaction() as repos:
        attempts = await repos.turn_attempts.list_for_session(
            request.session_id, limit=100, latest=False
        )
    first_turn = attempts[0] if attempts else None
    external_state_ref = await _write_json_artifact(
        name="omnigent.session.external-state.json",
        artifact_type="omnigent.session_external_state",
        payload={
            "schemaVersion": "omnigent-session-external-state/v1",
            "omnigentSessionId": session.provider_session_ref,
            "lastCommittedBridgeEventCursor": session.provider_event_cursor,
            "firstMessage": {
                "digest": (
                    first_turn.instruction_digest if first_turn is not None else None
                ),
                "responseIdentifiers": {
                    "itemId": (
                        first_turn.provider_item_id
                        if first_turn is not None
                        else None
                    )
                },
            },
            "runtimeBindingRef": (
                runtime_state.binding.runtimeBindingRef
                if runtime_state is not None
                else None
            ),
        },
    )
    checkpoint_capture: dict[str, Any] = {}
    if execution_plan is not None and runtime_state is not None:
        runtime_binding = runtime_state.binding
        provider_authority = runtime_binding.providerLeases.get(
            "primary-model"
        )
        if provider_authority is None:
            raise ValueError(
                "terminal capture lacks primary Provider Profile authority"
            )
        execution_target, _policy = selection_from_request(
            agent_request.parameters
        )
        checkpoint_capture = {
            "executionPlanRef": execution_plan.planRef,
            "runtimeBindingRef": runtime_binding.runtimeBindingRef,
            "runtimeBindingRevision": runtime_state.revision,
            "runtimeBindingFencingGeneration": (
                runtime_state.fencing_generation
            ),
            "providerProfileId": provider_authority.providerProfileRef,
            "credentialRef": (
                "credential://provider-profile/"
                f"{provider_authority.providerProfileRef}/generation/"
                f"{provider_authority.credentialGeneration}"
            ),
            "credentialGeneration": provider_authority.credentialGeneration,
            "providerLeaseRef": provider_authority.providerLeaseRef,
            "hostBindingRef": runtime_binding.hostBindingRef,
            "hostLeaseRef": runtime_binding.hostLeaseRef,
            "hostLeaseGeneration": runtime_binding.hostLeaseGeneration,
            "endpointRef": execution_plan.payload.endpointRef,
            "omnigentHostId": runtime_binding.omnigentHostId,
            "omnigentSessionId": runtime_binding.omnigentSessionId,
            "bridgeSessionId": session.metadata.get("bridgeSessionRef"),
            "externalStateRef": external_state_ref,
            "idempotencyKey": agent_request.idempotency_key,
            "captureManifestRef": harvested_ref,
            "executionProfileRef": execution_target,
            "launchPolicyRef": execution_plan.payload.launchPolicyRef,
            "workspaceLocator": workspace_locator,
            "sourceBranch": (
                (agent_request.workspace_spec or {}).get("startingBranch")
                or "detached"
            ),
            "outputBranch": (
                (agent_request.workspace_spec or {}).get("targetBranch")
            ),
            "publicationState": publish_mode,
        }
    status = (
        "completed"
        if session.terminal_state == "completed"
        else "delivery_unknown"
        if session.terminal_state == "delivery_unknown"
        else "timed_out"
        if session.terminal_state == "timed_out"
        else "canceled"
        if session.terminal_state == "canceled"
        else "execution_failed"
    )
    result = AgentRunResult(
        outputRefs=[harvested_ref, publication_ref],
        summary=f"Omnigent session {session.terminal_state}",
        failureClass=(
            None
            if session.terminal_state == "completed"
            else "integration_error"
            if session.terminal_state == "delivery_unknown"
            else "canceled"
            if session.terminal_state == "canceled"
            else "timed_out"
            if session.terminal_state == "timed_out"
            else "execution_error"
        ),
        metadata={
            "canonicalSessionId": request.session_id,
            "providerSessionRef": session.provider_session_ref,
            "chatBindingId": session.chat_binding_id,
            "terminalState": session.terminal_state,
            "publicationEvidenceRef": publication_ref,
            "externalStateRef": external_state_ref,
            **(
                {"omnigentCheckpointCapture": checkpoint_capture}
                if checkpoint_capture
                else {}
            ),
            **(await _session_execution_authority_metadata(session)),
        },
    )
    terminal = OmnigentSessionTerminalResult(status=status, result=result)
    terminal_ref = await _write_json_artifact(
        name="omnigent.session.terminal.json",
        artifact_type="omnigent.session_terminal",
        payload={
            "schemaVersion": "omnigent-session-terminal-evidence/v1",
            "sessionId": request.session_id,
            "terminalResult": terminal.model_dump(mode="json", by_alias=True),
        },
    )
    async with store.transaction() as repos:
        await repos.sessions.attach_terminal_evidence(
            request.session_id,
            terminal_evidence_ref=terminal_ref,
            expected_revision=session.revision,
            expected_fencing_generation=request.fencing_generation,
        )
    settled = await _settle_command(request, result_ref=terminal_ref)
    settled["terminalResultRef"] = terminal_ref
    return settled


async def omnigent_stop_provider_session_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore

    request = OmnigentSessionActivityRequest.model_validate(payload)
    command, should_execute = await _claim_command(request)
    if not should_execute:
        return {"commandId": request.command_id, "outcome": command.status}
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
    if session is None:
        raise KeyError(request.session_id)
    if await _claim_canonical_cleanup(request.session_id) is None:
        return {"commandId": request.command_id, "outcome": "cleanup_not_owned"}
    if session.provider_session_ref:
        http_client, client = await _omnigent_client_context()
        try:
            with suppress(Exception):
                await client.interrupt(session.provider_session_ref)
            await client.stop_session(session.provider_session_ref)
        finally:
            await http_client.aclose()
    async with store.transaction() as repos:
        current = await repos.sessions.get(request.session_id)
        if current is not None and current.cleanup_state == "pending":
            await repos.sessions.update_lifecycle(
                request.session_id,
                expected_revision=current.revision,
                expected_fencing_generation=current.fencing_generation,
                cleanup_state="provider_stopped",
            )
    return {"commandId": request.command_id, "outcome": "provider_stopped"}


async def omnigent_stop_host_activity(payload: Mapping[str, Any]) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
    from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
    from moonmind.omnigent.oauth_hosts import (
        CLEANUP_CLAIMABLE_HOST_STATES,
        OmnigentOAuthHostRepository,
    )

    request = OmnigentSessionActivityRequest.model_validate(payload)
    command, should_execute = await _claim_command(request)
    if not should_execute:
        return {"commandId": request.command_id, "outcome": command.status}
    store = OmnigentControlPlaneStore(async_session_maker)
    hosts = OmnigentOAuthHostRepository(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
    if session is None:
        raise KeyError(request.session_id)
    if await _claim_canonical_cleanup(request.session_id) is None:
        return {"commandId": request.command_id, "outcome": "cleanup_not_owned"}
    cleanup_evidence: Mapping[str, Any] = {}
    plan = (
        await _load_verified_execution_plan(request.omnigent_execution_plan)
        if request.omnigent_execution_plan is not None
        else None
    )
    if (
        plan is not None
        and plan.payload.executionRealizerRef == "generic-omnigent-host@1"
    ):
        raise ValueError(
            "generic host cleanup belongs to the AgentRun realizer, "
            "not the legacy session supervisor"
        )
    elif session.host_lease_ref:
        lease = await hosts.get_host_lease(session.host_lease_ref)
        if lease is not None and lease.status in CLEANUP_CLAIMABLE_HOST_STATES:
            # Cleanup must be fenced, not assumed. The janitor or another
            # recovery owner may already have claimed this lease by draining it;
            # stopping the host anyway would let two owners delete resources and
            # release credential capacity concurrently. Win the same
            # compare-and-swap the legacy coordinator uses, or leave the host to
            # whoever did win cleanup authority.
            lease = await _claim_host_cleanup_authority(hosts, lease.lease_id)
        if lease is not None:
            binding = await hosts.validate_binding(lease.binding_ref)
            agent_request = await _load_intent_request(request)
            http_client, client = await _omnigent_client_context()
            try:
                cleanup_evidence = await OmnigentOAuthHostRuntime(
                    client=client
                ).stop_host(
                    binding=binding,
                    host_lease=lease,
                    effective_launch=binding.effective_launch_snapshot,
                    egress_evidence=(
                        dict(session.metadata["egressAttestation"])
                        if isinstance(
                            session.metadata.get("egressAttestation"), Mapping
                        )
                        else None
                    ),
                    launch_evidence_ref=str(
                        session.metadata.get("egressEvidenceRef") or ""
                    )
                    or None,
                    evidence_request=agent_request,
                    artifact_gateway=LocalOmnigentArtifactGateway(),
                )
            finally:
                await http_client.aclose()
            if lease.status != "stopped":
                await hosts.mark_host_lease_stopped(lease.lease_id)
    async with store.transaction() as repos:
        current = await repos.sessions.get(request.session_id)
        if current is not None and current.cleanup_state != "host_stopped":
            updated = await repos.sessions.update_lifecycle(
                request.session_id,
                expected_revision=current.revision,
                expected_fencing_generation=current.fencing_generation,
                cleanup_state="host_stopped",
            )
            cleanup_ref = str((cleanup_evidence or {}).get("evidenceRef") or "")
            if cleanup_ref:
                updated = await repos.sessions.bind_runtime_authority(
                    request.session_id,
                    expected_revision=updated.revision,
                    expected_fencing_generation=request.fencing_generation,
                    metadata_patch={"egressTerminalEvidenceRef": cleanup_ref},
                )
        else:
            updated = current
    settled = await _settle_command(request)
    if updated is not None:
        settled["revision"] = updated.revision
    return settled


async def omnigent_release_leases_activity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
    from moonmind.provider_profiles.lease_client import (
        CredentialLease,
        CredentialLeasePurpose,
        ProviderProfileLeaseClient,
    )
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    request = OmnigentSessionActivityRequest.model_validate(payload)
    command, should_execute = await _claim_command(request)
    if not should_execute:
        return {"commandId": request.command_id, "outcome": command.status}
    store = OmnigentControlPlaneStore(async_session_maker)
    async with store.transaction() as repos:
        session = await repos.sessions.get(request.session_id)
    if session is None:
        raise KeyError(request.session_id)
    if session.cleanup_state not in {"host_stopped", "leases_released", "complete"}:
        raise ValueError("host cleanup must complete before Provider Profile release")
    cleanup_claim = await _claim_canonical_cleanup(request.session_id)
    if cleanup_claim is None:
        return {"commandId": request.command_id, "outcome": "cleanup_not_owned"}
    runtime_store = None
    runtime_state = None
    if request.omnigent_execution_plan is not None:
        runtime_ref = str(session.metadata.get("runtimeBindingRef") or "")
        if not runtime_ref:
            raise ValueError("plan-bound cleanup lacks runtime binding authority")
        runtime_store, runtime_state = await _load_current_runtime_binding(
            execution_plan_ref=request.omnigent_execution_plan.plan_ref,
            execution_scope_ref=str(session.moonmind_workflow_id or ""),
            recorded_runtime_binding_ref=runtime_ref,
        )
        plan = await _load_verified_execution_plan(
            request.omnigent_execution_plan
        )
        if plan.payload.executionRealizerRef == "generic-omnigent-host@1":
            raise ValueError(
                "generic credential cleanup belongs to the AgentRun realizer, "
                "not the legacy session supervisor"
            )
    lease_ref = str(session.metadata.get("providerLeaseRef") or "")
    owner_id = str(session.metadata.get("providerLeaseOwnerId") or "")
    runtime_id = str(session.metadata.get("providerRuntimeId") or "")
    if lease_ref and owner_id and runtime_id and session.cleanup_state != "leases_released":
        await ProviderProfileLeaseClient(TemporalClientAdapter()).release_lease(
            CredentialLease(
                profile_id=str(session.provider_profile_id or ""),
                runtime_id=runtime_id,
                lease_id=lease_ref,
                owner_id=owner_id,
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            )
        )
    async with store.transaction() as repos:
        current = await repos.sessions.get(request.session_id)
        if current is not None and current.cleanup_state != "leases_released":
            updated = await repos.sessions.update_lifecycle(
                request.session_id,
                expected_revision=current.revision,
                expected_fencing_generation=current.fencing_generation,
                cleanup_state="leases_released",
                historical_read_state="artifact",
            )
        else:
            updated = current
    settled = await _settle_command(request)
    if runtime_store is not None and runtime_state is not None:
        completed_binding = await runtime_store.mark_cleanup_complete(
            runtime_state.binding.runtimeBindingRef,
            expected_revision=runtime_state.revision,
            expected_fencing_generation=runtime_state.fencing_generation,
        )
        completed_state = await runtime_store.get_state(
            completed_binding.runtimeBindingRef
        )
        if completed_state is None:
            raise RuntimeError("cleanup-complete runtime binding is unavailable")
        await _project_runtime_binding_to_execution(
            workflow_id=str(session.moonmind_workflow_id or ""),
            state=completed_state,
        )
        settled.update(
            {
                "runtimeBindingRef": completed_binding.runtimeBindingRef,
                "runtimeBindingRevision": completed_state.revision,
                "runtimeBindingFencingGeneration": (
                    completed_state.fencing_generation
                ),
            }
        )
    if updated is not None:
        settled["revision"] = updated.revision
    # Provider capacity was the last thing this owner released, so settle the
    # shared cleanup aggregate. A turn admitted since the claim advanced the
    # generation and fences this completion, which is exactly the outcome the
    # canonical boundary promises.
    settled["cleanupComplete"] = await CanonicalCleanupAuthority(store).complete(
        cleanup_claim
    )
    return settled


__all__ = [
    "omnigent_evaluate_session_admission_activity",
    "omnigent_resolve_intent_activity",
    "omnigent_load_reconciliation_inputs_activity",
    "omnigent_load_failure_authority_activity",
    "omnigent_persist_decision_activity",
    "omnigent_persist_signal_intents_activity",
    "omnigent_ensure_provider_profile_lease_activity",
    "omnigent_ensure_host_activity",
    "omnigent_ensure_provider_session_activity",
    "omnigent_submit_turn_activity",
    "omnigent_heartbeat_host_lease_activity",
    "omnigent_read_event_batch_activity",
    "omnigent_observe_snapshot_activity",
    "omnigent_record_terminal_activity",
    "omnigent_persist_failure_activity",
    "omnigent_harvest_evidence_activity",
    "omnigent_publish_workspace_activity",
    "omnigent_stop_provider_session_activity",
    "omnigent_stop_host_activity",
    "omnigent_release_leases_activity",
]
