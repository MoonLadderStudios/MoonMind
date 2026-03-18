"""Execution wrapper for skills-first stage orchestration."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from .contracts import StageExecutionOutcome
from .registry import resolve_stage_execution

T = TypeVar("T")


def _set_context_execution(
    *,
    context: dict[str, object],
    stage_name: str,
    selected_skill: str,
    adapter_id: str | None,
    execution_path: str,
    used_skills: bool,
    used_fallback: bool,
    shadow_mode_requested: bool,
) -> None:
    execution = context.setdefault("skill_execution", {})
    if not isinstance(execution, dict):
        return
    execution[stage_name] = {
        "selectedTool": selected_skill,
        "adapterId": adapter_id,
        "executionPath": execution_path,
        "usedTools": used_skills,
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
    """Execute a stage with skills policy semantics."""

    decision = resolve_stage_execution(
        stage_name=stage_name,
        run_id=run_id,
        context=context,
    )

    execution_path = "skill" if decision.use_skills else "direct_only"

    _set_context_execution(
        context=context,
        stage_name=stage_name,
        selected_skill=decision.selected_skill,
        adapter_id=None,
        execution_path=execution_path,
        used_skills=decision.use_skills,
        used_fallback=False,
        shadow_mode_requested=decision.shadow_mode,
    )

    result = execute_direct()

    return StageExecutionOutcome(
        stage_name=stage_name,
        selected_skill=decision.selected_skill,
        adapter_id=None,
        execution_path=execution_path,
        used_fallback=False,
        used_skills=decision.use_skills,
        shadow_mode_requested=decision.shadow_mode,
        result=result,
    )
