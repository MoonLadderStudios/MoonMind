"""Preset expansion helpers for workflow execution submissions."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from moonmind.workflows.executions.preset_goal_scheduler import (
    goal_from_payloads,
    schedule_preset_from_goal,
    workflow_is_already_authored,
)


def preset_seed_dir() -> Path:
    import api_service

    return Path(api_service.__file__).resolve().parent / "data" / "presets"


def _coerce_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def template_slug_from_task(task_payload: Mapping[str, Any]) -> str:
    template_payload = _coerce_mapping(
        task_payload.get("taskTemplate") or task_payload.get("task_template")
    )
    return str(
        template_payload.get("slug")
        or template_payload.get("name")
        or template_payload.get("id")
        or ""
    ).strip()


def has_unexpanded_task_template(
    initial_parameters: Mapping[str, Any] | None,
) -> bool:
    parameters = _coerce_mapping(initial_parameters)
    task_payload = _coerce_mapping(parameters.get("workflow")) or _coerce_mapping(
        parameters.get("task")
    )
    if not task_payload:
        return False
    raw_steps = task_payload.get("steps")
    if isinstance(raw_steps, list) and raw_steps:
        return False
    return bool(template_slug_from_task(task_payload))


async def expand_preset_for_child_run(
    *,
    session: Any,
    initial_parameters: Mapping[str, Any] | None,
    allow_goal_schedule: bool = True,
) -> dict[str, Any]:
    """Expand stored preset provenance into executable workflow steps."""

    parameters = _coerce_mapping(initial_parameters)
    workflow_payload = _coerce_mapping(parameters.get("workflow"))
    if workflow_payload:
        payload_key = "workflow"
        task_payload = workflow_payload
    else:
        payload_key = "task"
        task_payload = _coerce_mapping(parameters.get("task"))
    if not task_payload:
        return parameters
    raw_steps = task_payload.get("steps")
    if isinstance(raw_steps, list) and raw_steps:
        return parameters

    template_payload = _coerce_mapping(
        task_payload.get("taskTemplate") or task_payload.get("task_template")
    )
    template_slug = template_slug_from_task(task_payload)
    if not template_slug:
        if not allow_goal_schedule:
            return parameters
        goal = goal_from_payloads(
            task_payload=task_payload,
            parameter_payload=parameters,
        )
        schedule = (
            None
            if workflow_is_already_authored(task_payload)
            else schedule_preset_from_goal(goal)
        )
        if schedule is None:
            return parameters
        template_slug = schedule.slug
        template_payload = {
            "slug": schedule.slug,
            "version": schedule.version,
            "scope": "global",
        }
        existing_inputs = _coerce_mapping(task_payload.get("inputs"))
        task_payload["inputs"] = {**schedule.inputs, **existing_inputs}
        task_payload["goal"] = schedule.goal
        task_payload.setdefault("instructions", schedule.goal)
        task_payload["taskTemplate"] = dict(template_payload)
        task_payload["presetSchedule"] = {
            "source": "goal",
            "reason": schedule.reason,
            "presetSlug": schedule.slug,
            "presetVersion": schedule.version,
            "jiraIssueKey": schedule.issue_key,
        }

    from api_service.services.presets.catalog import (
        ExpandOptions,
        PresetCatalogService,
        PresetNotFoundError,
    )

    template_version = str(template_payload.get("version") or "1.0.0").strip()
    template_scope = str(template_payload.get("scope") or "global").strip() or "global"
    template_scope_ref = (
        str(template_payload.get("scopeRef") or template_payload.get("scope_ref") or "")
        .strip()
        or None
    )
    template_inputs = _coerce_mapping(task_payload.get("inputs"))
    repository = (
        task_payload.get("repository")
        or parameters.get("repository")
        or parameters.get("repo")
    )
    template_context: dict[str, Any] = {}
    if isinstance(repository, str) and repository.strip():
        template_context["repository"] = repository.strip()
        template_context["repo"] = repository.strip()
    target_runtime = parameters.get("targetRuntime")
    if isinstance(target_runtime, str) and target_runtime.strip():
        template_context["targetRuntime"] = target_runtime.strip()
    catalog = PresetCatalogService(session)
    expand_kwargs = {
        "slug": template_slug,
        "scope": template_scope,
        "scope_ref": template_scope_ref,
        "version": template_version,
        "inputs": template_inputs,
        "context": template_context,
        "options": ExpandOptions(should_enforce_step_limit=True),
    }
    try:
        expanded = await catalog.expand_template(**expand_kwargs)
    except PresetNotFoundError:
        await catalog.sync_seed_templates(seed_dir=preset_seed_dir())
        expanded = await catalog.expand_template(**expand_kwargs)

    expanded_steps = expanded.get("steps") if isinstance(expanded, Mapping) else None
    if not isinstance(expanded_steps, list) or not expanded_steps:
        raise RuntimeError(
            f"Preset '{template_slug}' expansion produced no executable steps."
        )

    applied_template = _coerce_mapping(expanded.get("appliedTemplate"))
    applied_template_payload: dict[str, Any] = {
        "slug": str(applied_template.get("slug") or template_slug),
        "version": str(applied_template.get("version") or template_version),
        "inputs": _coerce_mapping(applied_template.get("inputs")) or template_inputs,
        "stepIds": list(applied_template.get("stepIds") or []),
        "appliedAt": str(applied_template.get("appliedAt") or ""),
        "capabilities": list(expanded.get("capabilities") or []),
    }
    composition = applied_template.get("composition") or expanded.get("composition")
    if isinstance(composition, Mapping):
        applied_template_payload["composition"] = dict(composition)
    authored_presets = applied_template.get("authoredPresets") or expanded.get(
        "authoredPresets"
    )
    if isinstance(authored_presets, list):
        task_payload["authoredPresets"] = [
            dict(item) if isinstance(item, Mapping) else item
            for item in authored_presets
        ]
    task_payload["steps"] = list(expanded_steps)
    task_payload["appliedStepTemplates"] = [applied_template_payload]
    task_payload["taskTemplate"] = {
        **template_payload,
        "slug": str(applied_template.get("slug") or template_slug),
        "version": str(applied_template.get("version") or template_version),
        "scope": template_scope,
    }
    parameters[payload_key] = task_payload
    parameters["stepCount"] = len(expanded_steps)
    return parameters
