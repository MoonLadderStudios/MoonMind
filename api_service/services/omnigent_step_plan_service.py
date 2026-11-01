"""Compile distinct logical-step selections through the existing plan owner."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from moonmind.config.settings import settings
from moonmind.runtime_intent import merge_runtime_selection, model_selection_fields
from moonmind.workflows.executions.model_resolver import resolve_model_effort

if TYPE_CHECKING:
    from api_service.services.omnigent_execution_plan_service import PersistedOmnigentExecutionPlan


async def compile_step_execution_plans(
    *,
    admitted: PersistedOmnigentExecutionPlan,
    provider_profile: Any,
    initial_parameters: Mapping[str, Any],
    **compilation_arguments: Any,
) -> PersistedOmnigentExecutionPlan:
    """Freeze a distinct model/effort selection before any step can launch.

    Account and capacity inheritance still belong to their existing owners.
    A step with independent account/pool intent is never authorized by this
    model-only specialization of the root plan.
    """
    from api_service.services.omnigent_execution_plan_service import (
        compile_and_persist_execution_plan,
    )
    from moonmind.runtime_intent import has_profile_selector, provider_profile_ref_from_mapping

    task = initial_parameters.get("workflow") or initial_parameters.get("task")
    if not isinstance(task, Mapping):
        return admitted
    parent_runtime = task.get("runtime")
    parent_runtime = parent_runtime if isinstance(parent_runtime, Mapping) else {}
    root_model = admitted.envelope.payload.modelConfig
    step_plans = []
    step_parameters = {}
    for step in task.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        runtime = step.get("runtime")
        if not isinstance(runtime, Mapping) or not model_selection_fields(runtime):
            continue
        profile_ref = provider_profile_ref_from_mapping(runtime)
        if has_profile_selector(runtime) or (
            profile_ref and profile_ref != provider_profile.profile_id
        ):
            continue
        selection = resolve_model_effort(
            runtime_id=provider_profile.runtime_id,
            profile=provider_profile,
            authored_runtime=merge_runtime_selection(parent_runtime, runtime),
            require_launch_ready=False,
            workflow_settings=settings.workflow,
        )
        if (selection.model, selection.effort) == (
            root_model.qualifiedId, root_model.effort,
        ):
            continue
        step_id = str(step.get("id") or "").strip()
        if not step_id or step_id in step_parameters:
            raise ValueError("A specialized step requires a unique logical step ID")
        parameters = copy.deepcopy(dict(initial_parameters))
        for field in ("omnigentExecutionPlan", "omnigentStepExecutionPlans", "resolvedSkillsetRef"):
            parameters.pop(field, None)
        parameters.update(
            model=selection.model,
            effort=selection.effort,
            modelSource=selection.model_source,
        )
        # The original snapshot remains unchanged. Resolved selection is the
        # same canonical metadata used by ordinary admission and the adapter.
        parameters["modelTierResolution"] = selection.as_metadata()
        specialized = await compile_and_persist_execution_plan(
            provider_profile=provider_profile,
            initial_parameters=parameters,
            _compile_step_plans=False,
            **compilation_arguments,
        )
        step_plans.append(specialized)
        step_parameters[step_id] = {
            **specialized.execution_parameters,
            "model": selection.model,
            "effort": selection.effort,
            "modelTierResolution": selection.as_metadata(),
        }
    if not step_parameters:
        return admitted
    return replace(
        admitted,
        step_plans=tuple(step_plans),
        step_parameters=step_parameters,
        artifact_refs=tuple(dict.fromkeys([
            *admitted.artifact_refs,
            *(ref for plan in step_plans for ref in plan.artifact_refs),
        ])),
    )
