"""Execution wrapper for skills-first stage orchestration."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from .contracts import StageExecutionOutcome
from .registry import get_stage_adapter, resolve_stage_execution
from .speckit_adapter import SkillAdapterError, run_speckit_stage

T = TypeVar("T")


def _set_context_execution(
    *,
    context: dict[str, object],
    stage_name: str,
    selected_skill: str,
    execution_path: str,
    used_skills: bool,
    used_fallback: bool,
    shadow_mode_requested: bool,
) -> None:
    execution = context.setdefault("skill_execution", {})
    if not isinstance(execution, dict):
        return
    execution[stage_name] = {
        "selectedSkill": selected_skill,
        "executionPath": execution_path,
        "usedSkills": used_skills,
        "usedFallback": used_fallback,
        "shadowModeRequested": shadow_mode_requested,
    }


def execute_stage(
    *,
    stage_name: str,
    run_id: str,
    context: dict[str, object],
    execute_direct: Callable[[], T],
) -> StageExecutionOutcome:
    """Execute a stage with skills policy and direct fallback semantics."""

    decision = resolve_stage_execution(
        stage_name=stage_name,
        run_id=run_id,
        context=context,
    )

    if not decision.use_skills:
        _set_context_execution(
            context=context,
            stage_name=stage_name,
            selected_skill=decision.selected_skill,
            execution_path="direct_only",
            used_skills=False,
            used_fallback=False,
            shadow_mode_requested=decision.shadow_mode,
        )
        result = execute_direct()
        return StageExecutionOutcome(
            stage_name=stage_name,
            selected_skill=decision.selected_skill,
            execution_path="direct_only",
            used_fallback=False,
            used_skills=False,
            shadow_mode_requested=decision.shadow_mode,
            result=result,
        )

    adapter_id = get_stage_adapter(decision.selected_skill)

    try:
        if adapter_id == "speckit":
            _set_context_execution(
                context=context,
                stage_name=stage_name,
                selected_skill=decision.selected_skill,
                execution_path="skill",
                used_skills=True,
                used_fallback=False,
                shadow_mode_requested=decision.shadow_mode,
            )
            result = run_speckit_stage(execute_direct=execute_direct)
        else:
            # Non-Speckit skills currently resolve through the compatibility path.
            _set_context_execution(
                context=context,
                stage_name=stage_name,
                selected_skill=decision.selected_skill,
                execution_path="skill",
                used_skills=True,
                used_fallback=False,
                shadow_mode_requested=decision.shadow_mode,
            )
            result = execute_direct()
        return StageExecutionOutcome(
            stage_name=stage_name,
            selected_skill=decision.selected_skill,
            execution_path="skill",
            used_fallback=False,
            used_skills=True,
            shadow_mode_requested=decision.shadow_mode,
            result=result,
        )
    except SkillAdapterError:
        if not decision.fallback_enabled:
            raise
        _set_context_execution(
            context=context,
            stage_name=stage_name,
            selected_skill=decision.selected_skill,
            execution_path="direct_fallback",
            used_skills=True,
            used_fallback=True,
            shadow_mode_requested=decision.shadow_mode,
        )
        fallback_result = execute_direct()
        return StageExecutionOutcome(
            stage_name=stage_name,
            selected_skill=decision.selected_skill,
            execution_path="direct_fallback",
            used_fallback=True,
            used_skills=True,
            shadow_mode_requested=decision.shadow_mode,
            result=fallback_result,
        )
