"""Admit one dispatch-selected native pool account through the shared compiler."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select

from api_service.db.models import (
    ManagedAgentProviderProfile,
    ProviderProfileSlotLease,
    TemporalExecutionCanonicalRecord,
)
from api_service.services.omnigent_agent_profile_selection import (
    compile_agent_profile_snapshot_parameters,
    resolve_default_agent_profile_snapshot,
)
from api_service.services.omnigent_execution_plan_service import (
    compile_and_persist_execution_plan,
)
from api_service.services.profile_execution_selection import (
    profile_harness_id,
    profile_route_runtime,
)
from moonmind.omnigent.bridge_artifacts import (
    link_verified_execution_plan_inputs_in_session,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    verify_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore
from moonmind.provider_profiles.plan_handoff import (
    bind_selected_pool_plan,
    selected_pool_grant_error,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    OmnigentExecutionPlanBinding,
)
from moonmind.runtime_identity import normalize_runtime_id
from moonmind.workflows.executions.runtime_target_selection import (
    AuthoringSurface,
    resolve_runtime_target_selection,
)
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ArtifactTransactionOwnership,
    TemporalArtifactRepository,
    TemporalArtifactService,
)


def _request_digest(request, original_profile):
    body = {
        "request": request.model_dump(
            mode="json", by_alias=True, exclude={"execution_budget"}
        ),
        "originalProfileRef": original_profile,
    }
    return (
        "sha256:"
        + sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _shared_request(request, *, snapshot, binding, plan, skill_ref):
    parameters = compile_agent_profile_snapshot_parameters(
        request.parameters, snapshot=snapshot
    )
    # Runtime requests carry the verified artifact binding, as the ordinary
    # UserWorkflow compiler does; the complete credential-policy document
    # stays in its persisted snapshot rather than a generic parameters map.
    parameters.pop("agentProfileSnapshot", None)
    parameters.update(
        targetRuntime="omnigent",
        profileId=request.execution_profile_ref,
        model=plan.payload.modelConfig.qualifiedId,
        effort=plan.payload.modelConfig.effort,
        omnigentExecutionPlan=binding.model_dump(mode="json", by_alias=True),
        resolvedSkillsetRef=skill_ref,
    )
    parameters["omnigent"] = deepcopy(parameters.get("omnigent") or {})
    parameters["omnigent"]["prompt"] = {
        **parameters["omnigent"].get("prompt", {}),
        "text": request.instruction_ref or "",
    }
    step = request.step_execution.model_copy(deep=True)
    step.omnigent_execution_plan = binding
    step.resolved_skillset_ref = skill_ref
    step.runtime_selection = {
        **(step.runtime_selection or {}),
        "runtimeId": "omnigent",
        "executionProfileRef": request.execution_profile_ref,
    }
    workspace = deepcopy(request.workspace_spec)
    locator = workspace.get("workspaceLocator")
    if (
        locator is None
        or isinstance(locator, Mapping)
        and locator.get("kind") == "managed_runtime"
    ):
        workspace["workspaceLocator"] = {
            "kind": "sandbox",
            "workspaceId": sha256(
                f"{step.workflow_id}:{step.step_execution_id}".encode()
            ).hexdigest()[:24],
            "relativePath": "repo",
        }
    body = request.model_dump(mode="json", by_alias=True)
    body.update(
        agentKind="external",
        agentId="omnigent",
        managedSession=None,
        omnigentExecutionPlan=binding.model_dump(mode="json", by_alias=True),
        resolvedSkillsetRef=skill_ref,
        stepExecution=step.model_dump(mode="json", by_alias=True),
        workspaceSpec=workspace,
        parameters=parameters,
    )
    return AgentExecutionRequest.model_validate(body)


async def prepare_selected_pool_plan(
    session, *, runtime_id: str, payload: dict[str, Any], client=None
):
    """Persist plan, input grants and lease binding in the same transaction.

    A retry reads the binding on the existing grant; it never recompiles a
    different plan after a lost commit acknowledgement. Native-only outcomes
    retain their current adapter, with no early pool selection or new lease.
    """
    request = AgentExecutionRequest.model_validate(payload["request"])
    if (
        request.agent_kind != "managed"
        or normalize_runtime_id(request.agent_id) != runtime_id
        or request.step_execution is None
    ):
        raise ValueError("pool admission requires its managed logical-step request")
    if (
        request.resolved_skillset_ref
        and request.step_execution.resolved_skillset_ref
        and request.resolved_skillset_ref
        != request.step_execution.resolved_skillset_ref
    ):
        raise ValueError("pool request has conflicting recorded Skill manifests")
    owner_id, owner_run_id = str(payload["owner_id"]), str(payload["runId"])
    profile_id = str(payload["profile_id"])
    if request.execution_profile_ref != profile_id:
        raise ValueError("pool grant conflicts with the selected request account")
    original_profile = payload.get("originalProfileRef")
    if original_profile and original_profile != profile_id:
        raise ValueError("pool grant conflicts with the authored exact account")
    workflow_id = request.step_execution.workflow_id
    if request.correlation_id != workflow_id:
        raise ValueError("pool request has conflicting workflow authority")
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    description = await (client or TemporalClientAdapter()).describe_workflow(
        owner_id, run_id=owner_run_id
    )
    info = description.raw_description.workflow_execution_info
    if (
        info.execution.workflow_id != owner_id
        or info.execution.run_id != owner_run_id
        or info.status != 1
        or info.type.name != "MoonMind.AgentRun"
        or info.parent_execution.workflow_id != workflow_id
        or info.parent_execution.run_id != request.step_execution.run_id
    ):
        raise ValueError("pool grant owner is not the running workflow child")
    lease = (
        (
            await session.execute(
                select(ProviderProfileSlotLease)
                .where(
                    ProviderProfileSlotLease.runtime_id == runtime_id,
                    ProviderProfileSlotLease.lease_id == owner_id,
                )
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )
    if lease is None:
        raise ValueError("the selected pool grant is unavailable")
    reason = selected_pool_grant_error(lease, payload, runtime_id=runtime_id)
    if reason:
        raise ValueError("pool plan handoff was fenced: " + reason)
    request_digest = _request_digest(request, original_profile)
    access = ArtifactAccessContext.for_execution(
        workflow_id, principal="service:execution-plan"
    )
    artifacts = TemporalArtifactService(
        TemporalArtifactRepository(session),
        transaction_ownership=ArtifactTransactionOwnership.CALLER,
    )
    saved = (lease.safe_metadata_json or {}).get("preparedPoolPlan")
    if lease.execution_plan_ref:
        if (
            not isinstance(saved, Mapping)
            or saved.get("requestDigest") != request_digest
        ):
            raise ValueError("pool plan conflicts with the recorded request")
        binding = OmnigentExecutionPlanBinding.model_validate(saved["binding"])
        plan = await SessionExecutionPlanStore(session).load(binding.plan_ref)
        if plan is None or plan.planRef != lease.execution_plan_ref:
            raise ValueError("the recorded pool plan is unavailable")
        plan = verify_execution_plan_envelope(plan)
        _artifact, body = await artifacts.read(
            artifact_id=plan.payload.agentProfileSnapshotRef.removeprefix("artifact:"),
            access=access,
        )
        snapshot, skill_ref = json.loads(body), saved["resolvedSkillsetRef"]
    else:
        profile = await session.get(ManagedAgentProviderProfile, profile_id)
        if profile is None or not profile.enabled or profile.auth_state != "connected":
            raise ValueError("the selected pool account is no longer launch-ready")
        if str(getattr(profile.runtime_id, "value", profile.runtime_id)) != runtime_id:
            raise ValueError("the selected pool account belongs to a different runtime")
        target = resolve_runtime_target_selection(
            surface=AuthoringSurface.workflow_create,
            requested_runtime=runtime_id,
            profile_runtime="omnigent",
            profile_harness_id=profile_harness_id(profile),
            record_metrics=False,
        )
        if not target.available or target.path_class != "generic_omnigent":
            return {"disposition": "native", "reason": "shared_harness_not_available"}
        if await profile_route_runtime(session, None, profile) != "omnigent":
            return {
                "disposition": "native",
                "reason": "selected_profile_has_no_equivalent_shared_configuration",
            }
        if (
            request.checkpoint_recovery
            or request.remediation_workspace
            or request.step_execution.runtime_context_policy != "fresh_agent_run"
        ):
            return {
                "disposition": "native",
                "reason": "recorded_native_context_requires_its_current_adapter",
            }
        source = request.workspace_spec.get("workspaceSource") or {}
        locator = request.workspace_spec.get("workspaceLocator") or {}
        if (
            source.get("kind") not in {None, "scratch", "git"}
            or not isinstance(locator, Mapping)
            or locator.get("kind") not in {None, "managed_runtime", "sandbox"}
            or any(
                key in request.workspace_spec
                for key in ("workspacePath", "workspaceRoot", "physicalPath")
            )
        ):
            return {
                "disposition": "native",
                "reason": "workspace_requires_its_recorded_native_owner",
            }
        canonical = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        snapshot_ref = str(
            (getattr(canonical, "memo", None) or {}).get("task_input_snapshot_ref")
            or ""
        )
        if canonical is None or not snapshot_ref:
            return {
                "disposition": "native",
                "reason": "retained_workflow_lacks_original_input_snapshot",
            }
        if (canonical.parameters or {}).get("agentProfileSnapshot"):
            return {
                "disposition": "native",
                "reason": "authored_configuration_requires_its_existing_admission",
            }
        original, _body = await artifacts.read(
            artifact_id=snapshot_ref.removeprefix("artifact:"), access=access
        )
        original_digest = "sha256:" + sha256(_body).hexdigest()
        principal = original.created_by_principal
        selected = request.model_copy(deep=True)
        from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher

        ManagedRuntimeLauncher._apply_resolved_tier_policy(
            request=selected, profile=profile, strategy=None
        )
        resolution = (
            (selected.parameters.get("metadata") or {}).get("moonmind") or {}
        ).get("modelEffortResolution") or {}
        if resolution.get("tierParameterDefaults") or (
            selected.parameters.get("runtime") or {}
        ).get("parameters"):
            return {
                "disposition": "native",
                "reason": "native_parameter_defaults_require_an_equivalent_handoff",
            }
        if selected.parameters.get("envOverrides"):
            return {
                "disposition": "native",
                "reason": "native_environment_overrides_require_their_current_adapter",
            }
        snapshot = await resolve_default_agent_profile_snapshot(
            session,
            provider_profile_ref=profile_id,
            launch_policy_ref=None,
            consumer_type="workflow",
            consumer_id=f"{workflow_id}:{request.step_execution.logical_step_id}",
            user=None,
        )
        parameters = compile_agent_profile_snapshot_parameters(
            canonical.parameters or {}, snapshot=snapshot
        )
        for key in (
            "requiredCapabilities",
            "githubOperations",
            "publishMode",
            "commitMessage",
            "publishBaseBranch",
            "repository",
        ):
            if key in selected.parameters:
                parameters[key] = deepcopy(selected.parameters[key])
        parameters.update(
            model=selected.parameters.get("model"),
            effort=selected.parameters.get("effort"),
            targetRuntime="omnigent",
        )
        if request.workspace_spec:
            parameters["workspaceSpec"] = deepcopy(request.workspace_spec)
        admitted = await compile_and_persist_execution_plan(
            session_factory=None,
            execution_plan_store=SessionExecutionPlanStore(session),
            artifact_service=artifacts,
            principal=principal,
            workflow_id=workflow_id,
            agent_profile_snapshot=snapshot,
            provider_profile=profile,
            initial_parameters=parameters,
            authored_request_ref=snapshot_ref,
            authored_request_digest=original_digest,
            task_input_snapshot_ref=snapshot_ref,
            task_input_snapshot_digest=original_digest,
            db_session=session,
            access=access,
            _compile_step_plans=False,
            recorded_skillset_ref=request.resolved_skillset_ref
            or request.step_execution.resolved_skillset_ref,
        )
        binding, plan, skill_ref = (
            admitted.binding,
            admitted.envelope,
            admitted.resolved_skillset_ref,
        )
        verified_access = access._with_verified_inputs(
            ref.removeprefix("artifact:") for ref in admitted.artifact_refs
        )
        for target, run in ((workflow_id, canonical.run_id), (owner_id, owner_run_id)):
            await link_verified_execution_plan_inputs_in_session(
                session=session,
                plan=plan,
                binding=binding,
                workflow_id=target,
                run_id=run,
                access=verified_access,
            )
        saved = {
            "requestDigest": request_digest,
            "binding": binding.model_dump(mode="json", by_alias=True),
            "resolvedSkillsetRef": skill_ref,
        }
    result = bind_selected_pool_plan(
        lease,
        {
            **payload,
            "executionPlanRef": binding.plan_ref,
            "stepExecutionId": request.step_execution.step_execution_id,
            "idempotencyKey": request.idempotency_key,
            "credential_generation": lease.credential_generation
            if lease.execution_plan_ref
            else profile.credential_generation,
            "preparedPoolPlan": saved,
        },
        runtime_id=runtime_id,
    )
    if not result.get("bound"):
        raise ValueError("pool plan handoff was fenced: " + str(result.get("reason")))
    shared = _shared_request(
        request, snapshot=snapshot, binding=binding, plan=plan, skill_ref=skill_ref
    )
    await session.commit()
    return {
        "disposition": "shared",
        "request": shared.model_dump(mode="json", by_alias=True),
        "leaseMetadata": {
            "executionPlanRef": binding.plan_ref,
            "stepExecutionId": request.step_execution.step_execution_id,
            "idempotencyKey": request.idempotency_key,
            "credentialGeneration": lease.credential_generation,
        },
    }
