"""Compile distinct logical-step selections through the existing plan owner."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from moonmind.config.settings import settings
from moonmind.runtime_intent import merge_runtime_selection, model_selection_fields
from moonmind.workflows.executions.model_resolver import resolve_model_effort

if TYPE_CHECKING:
    from api_service.services.omnigent_execution_plan_service import PersistedOmnigentExecutionPlan
    from moonmind.omnigent.harness_platform.execution_plan import OmnigentExecutionPlanEnvelope


@dataclass(frozen=True)
class CompiledStepPlans:
    plans: tuple[PersistedOmnigentExecutionPlan, ...] = ()
    parameters: Mapping[str, Mapping[str, Any]] | None = None


async def compile_step_execution_plans(
    *,
    root_plan: OmnigentExecutionPlanEnvelope,
    recorded_step_parameters: Mapping[str, Mapping[str, Any]] | None = None,
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
        selection = resolve_model_effort(
            runtime_id=selected_provider.runtime_id,
            profile=selected_provider,
            authored_runtime=merge_runtime_selection(parent_runtime, runtime),
            require_launch_ready=False,
            workflow_settings=settings.workflow,
        )
        if not foreign_profile and (selection.model, selection.effort) == (
            root_model.qualifiedId, root_model.effort,
        ):
            continue
        if not step_id or step_id in step_parameters:
            raise ValueError("A specialized step requires a unique logical step ID")
        parameters = copy.deepcopy(dict(initial_parameters))
        for field in ("omnigentExecutionPlan", "omnigentStepExecutionPlans", "resolvedSkillsetRef"):
            parameters.pop(field, None)
        if foreign_profile:
            from api_service.services.omnigent_agent_profile_selection import compile_agent_profile_snapshot_parameters

            parameters = compile_agent_profile_snapshot_parameters(parameters, snapshot=selected_snapshot)
        parameters.update(
            model=selection.model,
            effort=selection.effort,
            modelSource=selection.model_source,
        )
        # The original snapshot remains unchanged. Resolved selection is the
        # same canonical metadata used by ordinary admission and the adapter.
        parameters["modelTierResolution"] = selection.as_metadata()
        specialized = await compile_and_persist_execution_plan(
            provider_profile=selected_provider,
            initial_parameters=parameters,
            _compile_step_plans=False,
            **{**compilation_arguments, "agent_profile_snapshot": selected_snapshot},
        )
        step_plans.append(specialized)
        step_parameters[step_id] = {
            **specialized.execution_parameters,
            "model": selection.model,
            "effort": selection.effort,
            "modelTierResolution": selection.as_metadata(),
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
