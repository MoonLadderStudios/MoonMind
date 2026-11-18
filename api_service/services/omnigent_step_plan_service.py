"""Compile distinct logical-step selections through the existing plan owner."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from moonmind.config.settings import settings
from moonmind.provider_profiles.model_tiers import (
    apply_tier_parameter_defaults,
    ensure_tier_metadata_is_credential_free,
)
from moonmind.runtime_intent import merge_runtime_selection, model_selection_fields
from moonmind.workflows.executions.model_resolver import resolve_model_effort

if TYPE_CHECKING:
    from api_service.services.omnigent_execution_plan_service import PersistedOmnigentExecutionPlan
    from moonmind.omnigent.harness_platform.execution_plan import (
        OmnigentExecutionPlanEnvelope,
        ProviderConfigurationSnapshotRef,
    )


# Both publication preferences have existing consumers. Neither grants writes
# or changes repository identity; other native options still need translation.
_STEP_LAUNCH_PARAMETER_KEYS = frozenset({"commitMessage", "publishBaseBranch"})


@dataclass(frozen=True)
class CompiledStepPlans:
    plans: tuple[PersistedOmnigentExecutionPlan, ...] = ()
    parameters: Mapping[str, Mapping[str, Any]] | None = None


async def compile_step_execution_plans(
    *,
    root_plan: OmnigentExecutionPlanEnvelope,
    recorded_step_parameters: Mapping[str, Mapping[str, Any]] | None = None,
    recorded_provider_configurations: Mapping[
        str, ProviderConfigurationSnapshotRef | None
    ] | None = None,
    provider_profile: Any,
    initial_parameters: Mapping[str, Any],
    **compilation_arguments: Any,
) -> CompiledStepPlans:
    """Freeze each selected account, harness and model before a step can launch.

    Pools still belong to the existing capacity owner; an unresolved pool is
    never pinned opportunistically or authorized by the root account's plan.
    """
    from api_service.services.omnigent_execution_plan_service import (
        compile_and_persist_execution_plan,
    )
    from moonmind.runtime_intent import has_profile_selector, provider_profile_ref_from_mapping
    from moonmind.workflows.executions.runtime_defaults import normalize_runtime_id

    task = initial_parameters.get("workflow") or initial_parameters.get("task")
    if not isinstance(task, Mapping):
        return CompiledStepPlans()
    parent_runtime = task.get("runtime")
    parent_runtime = parent_runtime if isinstance(parent_runtime, Mapping) else {}
    root_model = root_plan.payload.modelConfig
    step_plans = []
    step_parameters = {}
    for step in task.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        runtime = step.get("runtime")
        runtime = runtime if isinstance(runtime, Mapping) else {}
        profile_ref = provider_profile_ref_from_mapping(runtime) or provider_profile_ref_from_mapping(step)
        foreign_profile = profile_ref and profile_ref != provider_profile.profile_id
        if (
            has_profile_selector(runtime)
            or normalize_runtime_id(runtime.get("mode") or initial_parameters.get("targetRuntime")) != "omnigent"
            or not (foreign_profile or model_selection_fields(runtime))
        ):
            continue
        selected_provider = provider_profile
        selected_snapshot = compilation_arguments["agent_profile_snapshot"]
        step_id = str(step.get("id") or "").strip()
        if foreign_profile:
            from api_service.services.provider_profile_runtime import load_provider_profile_for_runtime
            from api_service.services.omnigent_agent_profile_selection import resolve_default_agent_profile_snapshot

            db_session = compilation_arguments.get("db_session")
            selected_provider = await load_provider_profile_for_runtime(
                session=db_session, profile_id=profile_ref, selected_runtime="omnigent"
            )
            if selected_provider is None:
                raise ValueError("The selected step Provider Profile is unavailable")
            saved = (recorded_step_parameters or {}).get(step_id, {})
            selected_snapshot = saved.get("agentProfileSnapshot")
            if not isinstance(selected_snapshot, Mapping):
                selected_snapshot = await resolve_default_agent_profile_snapshot(
                    db_session, provider_profile_ref=profile_ref,
                    launch_policy_ref=None,
                    consumer_type="workflow", consumer_id=f"{compilation_arguments['workflow_id']}:{step_id}", user=None,
                )
            if selected_snapshot.get("providerProfileRef") != profile_ref:
                raise ValueError("The step snapshot conflicts with its selected Provider Profile")
        saved = (recorded_step_parameters or {}).get(step_id, {})
        recorded_resolution = saved.get("modelTierResolution")
        # The frozen compiler supplies configuration snapshots only for a
        # successor. A fresh occurrence resolves current tier policy instead.
        frozen = (
            recorded_provider_configurations is not None
            and isinstance(recorded_resolution, Mapping)
            and "tierParameterDefaults" in recorded_resolution
        )
        if frozen:
            resolution = copy.deepcopy(dict(recorded_resolution))
            defaults = resolution["tierParameterDefaults"]
        else:
            selection = resolve_model_effort(
                runtime_id=selected_provider.runtime_id,
                profile=selected_provider,
                authored_runtime=merge_runtime_selection(parent_runtime, runtime),
                require_launch_ready=False,
                workflow_settings=settings.workflow,
            )
            resolution = selection.as_metadata()
            defaults = selection.tier_parameters
        model = resolution.get("resolvedModel")
        effort = resolution.get("resolvedEffort")
        explicit = merge_runtime_selection(parent_runtime, runtime).get("parameters") or {}
        if not isinstance(defaults, Mapping) or not isinstance(explicit, Mapping):
            raise ValueError("Selected step native tier options require their native route")
        if (set(defaults) | set(explicit)) - _STEP_LAUNCH_PARAMETER_KEYS:
            raise ValueError("Selected step native tier options require their native route")
        if recorded_provider_configurations is not None and saved and not frozen and (defaults or explicit):
            raise ValueError("Selected step launch options changed from its admitted inputs")
        if not foreign_profile and not (defaults or explicit or frozen) and (model, effort) == (
            root_model.qualifiedId, root_model.effort,
        ):
            continue
        if not step_id or step_id in step_parameters:
            raise ValueError("A specialized step requires a unique logical step ID")
        parameters = copy.deepcopy(dict(initial_parameters))
        for field in ("omnigentExecutionPlan", "omnigentStepExecutionPlans", "resolvedSkillsetRef"):
            parameters.pop(field, None)
        # The root's explicit rollout target constrains only its own plan.
        # A specialized plan validates the step's target against its own Profile.
        specialized_task = parameters.get("workflow") or parameters.get("task")
        specialized_runtime = copy.deepcopy(dict(parent_runtime))
        specialized_task["runtime"] = specialized_runtime
        specialized_runtime.pop("targetId", None)
        if runtime.get("targetId"):
            specialized_runtime["targetId"] = runtime["targetId"]
        if foreign_profile:
            from api_service.services.omnigent_agent_profile_selection import compile_agent_profile_snapshot_parameters

            parameters = compile_agent_profile_snapshot_parameters(parameters, snapshot=selected_snapshot)
        ensure_tier_metadata_is_credential_free(defaults)
        ensure_tier_metadata_is_credential_free(explicit)
        if frozen:
            # The saved step handoff owns supplied and absent launch choices.
            for key in _STEP_LAUNCH_PARAMETER_KEYS:
                if key == "publishBaseBranch" and key not in saved:
                    # Older step records did not carry the base. Their original
                    # immutable task inputs still own the inherited choice.
                    continue
                parameters.pop(key, None)
                if key in saved:
                    parameters[key] = copy.deepcopy(saved[key])
        else:
            # Match the planner's authored publication aliases before applying
            # tier defaults. Explicit step parameters still win below, including
            # null/default; successors use only their already frozen handoff.
            publication = task.get("publish")
            publication = publication if isinstance(publication, Mapping) else {}
            message = publication.get("commitMessage", initial_parameters.get("commitMessage"))
            if isinstance(message, str) and message.strip():
                parameters["commitMessage"] = message.strip()
            for candidate in (
                publication.get("prBaseBranch"), publication.get("baseBranch"),
                initial_parameters.get("publishBaseBranch"),
                initial_parameters.get("prBaseBranch"), initial_parameters.get("baseBranch"),
            ):
                if isinstance(candidate, str) and candidate.strip():
                    parameters["publishBaseBranch"] = candidate.strip()
                    break
            applied = apply_tier_parameter_defaults(
                parameters, tier_parameters=defaults, explicit_parameters=explicit,
            )
            # Only translated launch choices introduce this frozen metadata.
            # Model-only steps keep their existing readmission drift checks.
            if defaults or explicit:
                resolution["tierParameterDefaults"] = applied
        base = parameters.get("publishBaseBranch")
        if base is not None and not isinstance(base, str):
            raise ValueError("Selected step publication base must be a string or null")
        if not frozen and (defaults or explicit):
            resolution["stepPublicationBaseBranch"] = copy.deepcopy(parameters.get("publishBaseBranch"))
        if "stepPublicationBaseBranch" in resolution:
            if frozen and saved.get("publishBaseBranch") != resolution["stepPublicationBaseBranch"]:
                raise ValueError("Selected step publication base conflicts with its admitted inputs")
            parameters["publishBaseBranch"] = copy.deepcopy(resolution["stepPublicationBaseBranch"])
        message = parameters.get("commitMessage")
        if message is not None and not isinstance(message, str):
            raise ValueError("Selected step commit message must be a string or null")
        if not frozen and (defaults or explicit):
            resolution["stepCommitMessage"] = copy.deepcopy(message)
        if "stepCommitMessage" in resolution:
            if frozen and saved.get("commitMessage") != resolution["stepCommitMessage"]:
                raise ValueError("Selected step commit message conflicts with its admitted inputs")
            parameters["commitMessage"] = copy.deepcopy(resolution["stepCommitMessage"])
        parameters.update(model=model, effort=effort, modelSource=resolution.get("modelSource"))
        # The original snapshot remains unchanged. Resolved selection is the
        # same canonical metadata used by ordinary admission and the adapter.
        parameters["modelTierResolution"] = resolution
        specialized = await compile_and_persist_execution_plan(
            provider_profile=selected_provider,
            initial_parameters=parameters,
            _compile_step_plans=False,
            _recorded_provider_configuration_snapshot=(
                (recorded_provider_configurations or {}).get(step_id, "capture")
            ),
            **{**compilation_arguments, "agent_profile_snapshot": selected_snapshot,
               "model_tier_resolution": resolution},
        )
        step_plans.append(specialized)
        step_parameters[step_id] = {
            **specialized.execution_parameters,
            "model": model,
            "effort": effort,
            "modelTierResolution": resolution,
            **{
                key: copy.deepcopy(parameters[key])
                for key in _STEP_LAUNCH_PARAMETER_KEYS
                if key in parameters and (
                    key != "publishBaseBranch" or "stepPublicationBaseBranch" in resolution
                )
            },
        }
        if foreign_profile:
            step_parameters[step_id].update({
                key: parameters[key]
                for key in ("profileId", "agentProfileSnapshot", "agentProfile", "omnigent", "capture", "workspace")
                if key in parameters
            })
    if not step_parameters:
        return CompiledStepPlans()
    return CompiledStepPlans(plans=tuple(step_plans), parameters=step_parameters)
