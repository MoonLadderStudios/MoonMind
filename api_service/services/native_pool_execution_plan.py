"""Admit one dispatch-selected native pool account through the shared compiler."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from typing import Any, NamedTuple

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
    changed_admitted_intent,
    changed_repository_selection,
    compile_and_persist_execution_plan,
)
from api_service.services.profile_execution_selection import (
    profile_harness_id,
    profile_route_runtime,
)
from moonmind.core.artifacts import temporal_artifact_id
from moonmind.omnigent.bridge_artifacts import (
    link_verified_execution_plan_inputs_in_session,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
    verify_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore
from moonmind.provider_profiles.model_tiers import (
    apply_tier_parameter_defaults,
    ensure_tier_metadata_is_credential_free,
)
from moonmind.provider_profiles.plan_handoff import (
    bind_selected_pool_plan,
    selected_pool_grant_error,
)
from moonmind.runtime_identity import normalize_runtime_id
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    OmnigentExecutionPlanBinding,
)
from moonmind.schemas.temporal_models import (
    StepExecutionCheckpointModel,
    build_step_execution_id,
)
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

# These settings already have consumers on both launch paths. Unknown native
# options remain with their current adapter until their behavior is translated.
_LAUNCH_PARAMETER_KEYS = frozenset({
    "requiredCapabilities", "githubOperations", "publishMode", "commitMessage",
    "publishBaseBranch", "repository",
    "workBranch", "workBranchId", "workBranchOrigin",
})
_MODEL_SELECTION_KEYS = frozenset({"model", "effort", "modelTierResolution", "metadata"})


class _RecordedPoolStep(NamedTuple):
    plan: OmnigentExecutionPlanEnvelope
    snapshot: Mapping[str, Any]
    resolution: dict[str, Any]
    launch_parameters: dict[str, Any] | None


def _with_frozen_launch_parameters(parameters, saved):
    result = deepcopy(dict(parameters or {}))
    if saved is None:
        return result
    if not isinstance(saved, Mapping) or not set(saved) <= _LAUNCH_PARAMETER_KEYS:
        raise ValueError("recorded pool launch parameters are invalid")
    for key in _LAUNCH_PARAMETER_KEYS:
        result.pop(key, None)
    result.update(deepcopy(dict(saved)))
    return result


def _recorded_pool_resolution(value):
    required = {
        "resolvedModel",
        "resolvedEffort",
        "modelSource",
        "effortSource",
        "effectiveModelTier",
    }
    if not isinstance(value, Mapping) or not required <= value.keys():
        raise ValueError("recorded pool launch resolution is incomplete")
    defaults = value.get("tierParameterDefaults", {})
    if not isinstance(defaults, Mapping) or not set(defaults) <= _LAUNCH_PARAMETER_KEYS:
        raise ValueError("recorded pool launch defaults are invalid")
    ensure_tier_metadata_is_credential_free(value)
    return deepcopy(dict(value))


async def _retained_pool_launch(session, *, request, checkpoint, plan):
    """Read only the original grant's settings for plans predating tier capture.

    Released grants are retained for redelivery, not permanent configuration
    storage. Their absence cannot be filled from the account's current tiers.
    """
    source_id = build_step_execution_id(checkpoint.source)
    rows = (
        await session.scalars(
            select(ProviderProfileSlotLease).where(
                ProviderProfileSlotLease.runtime_id
                == normalize_runtime_id(request.agent_id),
                ProviderProfileSlotLease.profile_id == request.execution_profile_ref,
                ProviderProfileSlotLease.step_execution_id == source_id,
                ProviderProfileSlotLease.execution_plan_ref == plan.planRef,
            )
        )
    ).all()
    if len(rows) != 1:
        raise ValueError("recorded pool launch settings are unavailable or ambiguous")
    row = rows[0]
    metadata = row.safe_metadata_json
    saved = metadata.get("preparedPoolPlan") if isinstance(metadata, Mapping) else None
    if (
        row.owner_is_workflow is not True
        or row.owner_kind != "workflow"
        or row.purpose != "execution_direct"
        or not row.owner_id
        or row.owner_id != row.workflow_id
        or row.owner_id != row.lease_id
        or not str(row.idempotency_key or "").strip()
        or not isinstance(saved, Mapping)
        or not metadata.get("runId")
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(saved.get("requestDigest") or "")
        )
        or not isinstance(saved.get("launchParameters"), Mapping)
    ):
        raise ValueError("recorded pool launch grant evidence is incomplete")
    try:
        binding = OmnigentExecutionPlanBinding.model_validate(saved.get("binding"))
    except ValueError as exc:
        raise ValueError("recorded pool launch binding is invalid") from exc
    if (
        binding.plan_ref != plan.planRef
        or binding.plan_digest != checkpoint.plan_digest
        or temporal_artifact_id(binding.plan_artifact_ref)
        != temporal_artifact_id(checkpoint.plan_ref)
        or temporal_artifact_id(binding.task_input_snapshot_ref)
        != temporal_artifact_id(checkpoint.task_input_snapshot_ref)
        or binding.task_input_snapshot_digest
        != plan.payload.authority.taskInputSnapshotDigest
        or temporal_artifact_id(saved.get("resolvedSkillsetRef")) is None
        or temporal_artifact_id(saved.get("resolvedSkillsetRef"))
        != temporal_artifact_id(plan.payload.resolvedSkills.get("resolvedSkillSetRef"))
    ):
        raise ValueError(
            "recorded pool launch binding conflicts with its checkpoint plan"
        )
    resolution = _recorded_pool_resolution(saved.get("modelResolution"))
    model = plan.payload.modelConfig
    # Custom model metadata may retain an authored alias that compilation
    # resolved once through the original Profile. Current aliases cannot
    # reinterpret it; the verified plan still owns the launch model.
    if resolution["resolvedEffort"] != model.effort or (
        resolution["modelSource"] != "task_override"
        and resolution["resolvedModel"] != model.qualifiedId
    ):
        raise ValueError(
            "recorded pool launch resolution conflicts with its checkpoint plan"
        )
    launch = _with_frozen_launch_parameters({}, saved["launchParameters"])
    ensure_tier_metadata_is_credential_free(launch)
    if any(
        key not in launch or launch[key] != value
        for key, value in resolution.get("tierParameterDefaults", {}).items()
    ):
        raise ValueError(
            "recorded pool launch defaults conflict with its frozen parameters"
        )
    return resolution, launch


async def _recorded_step_plan(
    session, artifacts, access, request, *, snapshot_ref, snapshot_digest
):
    """Read a prior attempt's plan through its scoped, verified checkpoint."""
    metadata = request.parameters.get("metadata")
    hints = metadata.get("moonmind") if isinstance(metadata, Mapping) else None
    hints = hints if isinstance(hints, Mapping) else {}
    reference = hints.get("sharedStepCheckpointRef")
    if not reference:
        return None
    if (
        not isinstance(reference, str)
        or len(reference) > 1024
        or temporal_artifact_id(reference) is None
    ):
        raise ValueError("recorded pool checkpoint reference is invalid")
    step = request.step_execution
    if step.execution_ordinal <= 1 or hints.get("sharedCheckpointRequired") is not True:
        raise ValueError("recorded pool configuration requires a replacement step")
    _row, body = await artifacts.read(
        artifact_id=temporal_artifact_id(reference), access=access
    )
    if len(body) > 4 * 1024 * 1024:
        raise ValueError("recorded pool checkpoint exceeds its bound")
    checkpoint = StepExecutionCheckpointModel.model_validate_json(body)
    source = checkpoint.source
    if (
        checkpoint.boundary != "after_execution"
        or source.workflow_id != step.workflow_id
        or source.run_id != step.run_id
        or source.logical_step_id != step.logical_step_id
        or source.execution_ordinal >= step.execution_ordinal
        or temporal_artifact_id(checkpoint.task_input_snapshot_ref)
        != temporal_artifact_id(snapshot_ref)
        or not checkpoint.plan_ref
        or not checkpoint.plan_digest
    ):
        raise ValueError("recorded pool checkpoint belongs to another step or input")
    _row, body = await artifacts.read(
        artifact_id=temporal_artifact_id(checkpoint.plan_ref), access=access
    )
    plan = verify_execution_plan_envelope(
        OmnigentExecutionPlanEnvelope.model_validate_json(body)
    )
    if (
        "sha256:" + plan.planRef.rsplit(":", 1)[-1] != checkpoint.plan_digest
        or temporal_artifact_id(plan.payload.authority.taskInputSnapshotRef)
        != temporal_artifact_id(snapshot_ref)
        or plan.payload.authority.taskInputSnapshotDigest != snapshot_digest
    ):
        raise ValueError("recorded pool plan conflicts with its checkpoint or input")
    stored = await SessionExecutionPlanStore(session).load(plan.planRef)
    if stored is None:
        raise ValueError("checkpoint plan was not admitted by the execution-plan owner")
    primary = plan.payload.credentialBindings.get("primary-model")
    if primary is None or primary.providerProfileRef != request.execution_profile_ref:
        # A deliberate pool rotation admits the new account's configuration.
        return None
    _row, body = await artifacts.read(
        artifact_id=temporal_artifact_id(plan.payload.agentProfileSnapshotRef),
        access=access,
    )
    snapshot = json.loads(body)
    if (
        not isinstance(snapshot, Mapping)
        or snapshot.get("providerProfileRef") != request.execution_profile_ref
    ):
        raise ValueError("recorded pool Agent Profile conflicts with its account")
    resolution = plan.payload.modelConfig.tierResolution
    launch_parameters = None
    if resolution is None:
        resolution, launch_parameters = await _retained_pool_launch(
            session,
            request=request,
            checkpoint=checkpoint,
            plan=plan,
        )
    return _RecordedPoolStep(
        plan,
        snapshot,
        _recorded_pool_resolution(resolution),
        launch_parameters,
    )


def _authored_runtime_parameters(parameters):
    runtime = parameters.get("runtime")
    if not isinstance(runtime, Mapping):
        authored = parameters.get("workflow") or parameters.get("task")
        runtime = authored.get("runtime") if isinstance(authored, Mapping) else None
    explicit = runtime.get("parameters") if isinstance(runtime, Mapping) else None
    return explicit if isinstance(explicit, Mapping) else {}


def _restore_recorded_pool_selection(request, recorded):
    parameters = request.parameters
    if recorded.launch_parameters is None:
        apply_tier_parameter_defaults(
            parameters,
            tier_parameters=recorded.resolution.get("tierParameterDefaults", {}),
            explicit_parameters=_authored_runtime_parameters(parameters),
        )
    else:
        parameters = _with_frozen_launch_parameters(
            parameters, recorded.launch_parameters
        )
        request.parameters = parameters
    parameters["model"] = recorded.plan.payload.modelConfig.qualifiedId
    parameters["effort"] = recorded.plan.payload.modelConfig.effort
    _apply_model_resolution(
        parameters, recorded.resolution, request.execution_profile_ref
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


def _apply_model_resolution(parameters, model_resolution, profile_id):
    if isinstance(model_resolution, Mapping):
        metadata = deepcopy(parameters.get("metadata") or {})
        if not isinstance(metadata, dict):
            metadata = {}
        moonmind = metadata.setdefault("moonmind", {})
        if not isinstance(moonmind, dict):
            moonmind = {}
            metadata["moonmind"] = moonmind
        moonmind["modelEffortResolution"] = deepcopy(dict(model_resolution))
        parameters["metadata"] = metadata
        if model_resolution.get("effectiveModelTier") is not None:
            parameters["modelTierResolution"] = {
                **deepcopy(dict(model_resolution)),
                "providerProfileId": profile_id,
            }
        else:
            parameters.pop("modelTierResolution", None)


def _shared_request(
    request, *, snapshot, binding, plan, skill_ref, model_resolution=None,
    launch_parameters=None,
):
    parameters = compile_agent_profile_snapshot_parameters(
        _with_frozen_launch_parameters(request.parameters, launch_parameters), snapshot=snapshot,
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
    _apply_model_resolution(parameters, model_resolution, request.execution_profile_ref)
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
    if (
        request.step_execution.runtime_session_context is not None
        and request.step_execution.runtime_session_context.workflow_id != workflow_id
    ):
        raise ValueError("pool context belongs to another workflow")
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
        if (
            request.checkpoint_recovery
            or request.remediation_workspace
            or (
                request.step_execution.runtime_context_policy != "fresh_agent_run"
                and not (
                    runtime_id == "codex_cli"
                    and request.step_execution.runtime_context_policy
                    == "reuse_session_same_epoch"
                    and request.step_execution.runtime_session_context is not None
                )
            )
        ):
            return {
                "disposition": "native",
                "reason": "recorded_native_context_requires_its_current_adapter",
            }
        source = request.workspace_spec.get("workspaceSource") or {}
        locator = request.workspace_spec.get("workspaceLocator") or {}
        repository_handoff = (
            request.canonical_repository_workspace_handoff
            and source.get("kind") == "repository"
        )
        if repository_handoff:
            from moonmind.omnigent.repository_sources import normalize_repository_source
            from moonmind.omnigent.workspace_sources import (
                compile_workspace_source, has_repository_workspace_revision,
            )

            compiled_source = compile_workspace_source(request.workspace_spec)
            target = (
                source.get("repositoryTarget")
                or request.workspace_spec.get("repositoryTarget")
                or {}
            )
            # The shared repository materializer consumes admitted GitHub
            # identity/credentials. Local and other-provider native sources
            # keep their current adapter rather than acquiring a new meaning.
            repository_handoff = (
                isinstance(target, Mapping)
                and target.get("provider") in {None, "git"}
                and not has_repository_workspace_revision(request.workspace_spec)
                and normalize_repository_source(compiled_source.repository_ref or "")[1]
                == "github_https"
            )
        if (
            (source.get("kind") not in {None, "scratch", "git"} and not repository_handoff)
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
        recorded = await _recorded_step_plan(
            session, artifacts, access, request,
            snapshot_ref=snapshot_ref,
            snapshot_digest=original_digest,
        )
        if (
            recorded is None
            and await profile_route_runtime(session, None, profile) != "omnigent"
        ):
            return {
                "disposition": "native",
                "reason": "selected_profile_has_no_equivalent_shared_configuration",
            }
        selected = request.model_copy(deep=True)
        from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher

        if recorded:
            _restore_recorded_pool_selection(selected, recorded)
        else:
            ManagedRuntimeLauncher._apply_resolved_tier_policy(
                request=selected, profile=profile, strategy=None
            )
        resolution = (
            (selected.parameters.get("metadata") or {}).get("moonmind") or {}
        ).get("modelEffortResolution") or {}
        if selected.parameters.get("envOverrides"):
            return {
                "disposition": "native",
                "reason": "native_environment_overrides_require_their_current_adapter",
            }
        from moonmind.workflows.executions.repository_contract import (
            repository_work_branch_arguments,
        )

        try:
            work_branch = repository_work_branch_arguments(selected.parameters)
            if work_branch["work_branch_origin"] == "generated" and not work_branch["work_branch"]:
                raise ValueError("generated work-branch metadata requires its name")
        except ValueError:
            return {
                "disposition": "native",
                "reason": "native_repository_metadata_requires_its_current_adapter",
            }
        explicit_parameters = _authored_runtime_parameters(request.parameters)
        # Run already flattens explicit runtime parameters. Re-resolution can
        # leave their values unchanged even when the shared path lacks a
        # consumer, so a changed-value comparison cannot admit those choices.
        if isinstance(explicit_parameters, Mapping) and set(explicit_parameters) - _LAUNCH_PARAMETER_KEYS:
            return {
                "disposition": "native",
                "reason": "native_runtime_parameters_require_an_equivalent_handoff",
            }
        changed_parameters = {
            key for key in set(selected.parameters) | set(request.parameters)
            if (key in selected.parameters) != (key in request.parameters)
            or selected.parameters.get(key) != request.parameters.get(key)
        }
        expected_model_parameters = deepcopy(request.parameters)
        _apply_model_resolution(expected_model_parameters, resolution, profile_id)
        if (
            changed_parameters - _LAUNCH_PARAMETER_KEYS - _MODEL_SELECTION_KEYS
            or selected.parameters.get("metadata") != expected_model_parameters.get("metadata")
        ):
            return {
                "disposition": "native",
                "reason": "native_parameter_defaults_require_an_equivalent_handoff",
            }
        launch_parameters = {
            key: deepcopy(selected.parameters[key])
            for key in _LAUNCH_PARAMETER_KEYS if key in selected.parameters
        }
        snapshot = (
            recorded.snapshot
            if recorded
            else await resolve_default_agent_profile_snapshot(
                session,
                provider_profile_ref=profile_id,
                launch_policy_ref=None,
                consumer_type="workflow",
                consumer_id=f"{workflow_id}:{request.step_execution.logical_step_id}",
                user=None,
            )
        )
        parameters = compile_agent_profile_snapshot_parameters(
            _with_frozen_launch_parameters(canonical.parameters, launch_parameters), snapshot=snapshot,
        )
        parameters.update(
            model=selected.parameters.get("model"),
            effort=selected.parameters.get("effort"),
            targetRuntime="omnigent",
        )
        # A selected step can override the root's authored model choice.
        if resolution.get("modelSource"):
            parameters["modelSource"] = resolution["modelSource"]
        if request.workspace_spec:
            parameters["workspaceSpec"] = deepcopy(request.workspace_spec)
        if recorded:
            recorded_plan = recorded.plan
            parameters.update(
                model=recorded_plan.payload.modelConfig.qualifiedId,
                effort=recorded_plan.payload.modelConfig.effort,
                modelSource="recorded_step_plan",
            )
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
            model_tier_resolution=resolution,
            _compile_step_plans=False,
            recorded_skillset_ref=request.resolved_skillset_ref
            or request.step_execution.resolved_skillset_ref,
            **(
                {
                    "_recorded_provider_configuration_snapshot": recorded.plan.payload.providerConfigurationSnapshot
                }
                if recorded
                else {}
            ),
        )
        if recorded:
            changed = changed_admitted_intent(
                recorded.plan.payload, admitted.envelope.payload
            )
            changed += await changed_repository_selection(
                artifact_service=artifacts,
                principal=principal,
                recorded=recorded.plan.payload,
                admitted=admitted.envelope.payload,
                access=access._with_verified_inputs(
                    temporal_artifact_id(ref) for ref in admitted.artifact_refs
                ),
            )
            # The verified save replaces the interrupted workspace. Other
            # request authority remains the admitted step's authority.
            if any(field != "workspace" for field in changed):
                raise ValueError(
                    "replacement pool plan changes recorded intent: " + ", ".join(changed)
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
            "modelResolution": deepcopy(resolution),
            "launchParameters": launch_parameters,
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
        request, snapshot=snapshot, binding=binding, plan=plan, skill_ref=skill_ref,
        model_resolution=saved.get("modelResolution"),
        launch_parameters=saved.get("launchParameters"),
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
