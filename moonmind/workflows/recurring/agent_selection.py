"""Replace the agent selection a saved schedule target was authored with.

A saved target carries the authored selection together with what admission
resolved and compiled from it: the Backend, the resolved model pair, the Agent
Profile snapshot and the execution plan. Editing the selection has to remove
those results first; kept, they would outvote the edit, because launch reads
the resolved pair and the plan rather than the authored fields. The result is
the target as a new schedule would submit it, ready for the same admission.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from moonmind.runtime_intent import (
    MODEL_SELECTION_KEYS,
    PROVIDER_PROFILE_REF_KEYS,
    is_custom_selection,
    merge_runtime_selection,
    provider_profile_ref_from_mapping,
)

_TASK_OVERRIDE = "task_override"

#: Compiled by schedule admission from the selection being replaced.
_COMPILED_PARAMETER_KEYS = (
    "omnigentExecutionPlan",
    "resolvedSkillsetRef",
    "omnigentStepExecutionPlans",
)
_COMPILED_TARGET_KEYS = ("omnigentAuthorityArtifactRefs", "runtimeProviderTarget")
#: Bound to one Provider Profile; a different profile needs its own.
_PROFILE_AUTHORITY_KEYS = ("agentProfile", "agentProfileSnapshot")
_BACKEND_KEYS = ("targetRuntime", "target_runtime")
#: Resolutions recorded on a runtime block next to the authored selection.
_RESOLVED_RUNTIME_KEYS = (
    "resolvedModel",
    "resolvedEffort",
    "modelTierResolution",
    "tierPreview",
)
_RESOLVED_STEP_RUNTIME_KEYS = (
    *_RESOLVED_RUNTIME_KEYS,
    "requestedModel",
    "modelSource",
    "inheritedProfileId",
)


class ScheduleAgentSelectionError(ValueError):
    """The saved target or the requested selection cannot be combined."""


def _workflow_key(parameters: Mapping[str, Any]) -> str | None:
    for key in ("workflow", "task"):
        if isinstance(parameters.get(key), Mapping):
            return key
    return None


def saved_provider_profile_ref(target: Mapping[str, Any]) -> str | None:
    """Return the Provider Profile a saved target launches with, if it names one."""

    parameters = target.get("initialParameters")
    if not isinstance(parameters, Mapping):
        return None
    key = _workflow_key(parameters)
    task = parameters[key] if key else {}
    for source in (task.get("runtime"), task, parameters, target):
        ref = provider_profile_ref_from_mapping(source)
        if ref:
            return ref
    return None


def replace_schedule_agent_selection(
    target: Mapping[str, Any], selection: Mapping[str, Any]
) -> dict[str, Any]:
    """Return ``target`` as authored with ``selection``, without admission results.

    ``selection`` may name a ``profileId``, and either a ``modelTier`` or a
    complete ``model``/``effort`` pair (null means Runtime default). An omitted
    part keeps what the schedule saved: the profile, or the model selection.

    A changed Provider Profile also gives up the Backend, launch policy and
    Agent Profile snapshot chosen for the previous one, since each belongs to
    one account. An unchanged profile keeps its snapshot, so its execution
    configuration is not reselected by a model edit.
    """

    profile_ref = str(selection.get("profileId") or "").strip() or None
    model_selection = {
        key: selection[key] for key in MODEL_SELECTION_KEYS if key in selection
    }
    replaces_model = "modelTier" in model_selection or is_custom_selection(
        model_selection
    )
    if model_selection and not replaces_model:
        raise ScheduleAgentSelectionError(
            "runtime must select a modelTier or both model and effort"
        )
    if "modelTier" in model_selection and len(model_selection) > 1:
        raise ScheduleAgentSelectionError(
            "runtime.modelTier cannot be combined with model or effort"
        )
    if profile_ref is None and not replaces_model:
        raise ScheduleAgentSelectionError(
            "runtime must select a profileId or a model selection"
        )

    updated = copy.deepcopy(dict(target))
    parameters = updated.get("initialParameters")
    key = _workflow_key(parameters) if isinstance(parameters, dict) else None
    if key is None:
        raise ScheduleAgentSelectionError(
            "this schedule has no workflow to select an agent for"
        )
    task = dict(parameters[key])
    saved_runtime = task.get("runtime")
    runtime = dict(saved_runtime) if isinstance(saved_runtime, Mapping) else {}
    profile_changed = (
        profile_ref is not None and profile_ref != saved_provider_profile_ref(updated)
    )
    saved_backend = str(
        runtime.get("mode") or parameters.get("targetRuntime") or ""
    ).strip()
    cutover = parameters.get("runtimeCutover")
    backend_authored = (
        isinstance(cutover, Mapping) and cutover.get("selectionSource") == "authored"
    )

    for name in _COMPILED_PARAMETER_KEYS:
        parameters.pop(name, None)
    for name in _COMPILED_TARGET_KEYS:
        updated.pop(name, None)

    # Mirrors model_resolver.resolve_recorded_selection: a recorded source
    # other than an authored override marks the flat pair as a resolution.
    recorded = parameters.get("modelTierResolution")
    model_resolved = parameters.get("modelSource") not in {None, _TASK_OVERRIDE}
    effort_resolved = (
        isinstance(recorded, Mapping) and recorded.get("effortSource") != _TASK_OVERRIDE
    )
    if replaces_model:
        for name in (*MODEL_SELECTION_KEYS, "requestedModel"):
            parameters.pop(name, None)
    else:
        if model_resolved:
            requested = parameters.pop("requestedModel", None)
            if requested is None:
                parameters.pop("model", None)
            else:
                parameters["model"] = requested
        if effort_resolved:
            parameters.pop("effort", None)
    parameters.pop("modelSource", None)
    parameters.pop("modelTierResolution", None)

    for name in _RESOLVED_RUNTIME_KEYS:
        runtime.pop(name, None)
    authored: dict[str, Any] = dict(model_selection)
    if profile_changed:
        authored["profileId"] = profile_ref
        for name in ("executionConfiguration", *_PROFILE_AUTHORITY_KEYS):
            runtime.pop(name, None)
        for holder in (updated, parameters):
            for name in _PROFILE_AUTHORITY_KEYS:
                holder.pop(name, None)
        for name in PROVIDER_PROFILE_REF_KEYS:
            parameters.pop(name, None)
            task.pop(name, None)
        omnigent = parameters.get("omnigent")
        if isinstance(omnigent, Mapping):
            kept = {
                name: value
                for name, value in omnigent.items()
                if name
                not in {"executionTargetRef", "launchPolicyRef", "agentProfileRef"}
            }
            if kept:
                parameters["omnigent"] = kept
            else:
                parameters.pop("omnigent")
    runtime = merge_runtime_selection(runtime, authored)
    if profile_changed or not backend_authored:
        # The next admission resolves the Backend this profile normally uses.
        runtime.pop("mode", None)
        parameters.pop("runtimeCutover", None)
        for name in _BACKEND_KEYS:
            parameters.pop(name, None)
            task.pop(name, None)
    task["runtime"] = runtime

    steps = task.get("steps")
    if isinstance(steps, list):
        task["steps"] = [_authored_step(step, saved_backend) for step in steps]
    parameters[key] = task
    return updated


def _authored_step(step: Any, saved_backend: str) -> Any:
    """Drop what admission recorded for a step that follows the workflow."""

    if not isinstance(step, Mapping) or not isinstance(step.get("runtime"), Mapping):
        return step
    runtime = dict(step["runtime"])
    for name in _RESOLVED_STEP_RUNTIME_KEYS:
        runtime.pop(name, None)
    if (
        provider_profile_ref_from_mapping(runtime) is None
        and str(runtime.get("mode") or "").strip() == saved_backend
    ):
        # Recorded from the workflow's Backend; the step follows the new one.
        runtime.pop("mode", None)
    return {**step, "runtime": runtime}


__all__ = [
    "ScheduleAgentSelectionError",
    "replace_schedule_agent_selection",
    "saved_provider_profile_ref",
]
