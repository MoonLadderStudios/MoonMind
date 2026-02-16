"""Policy resolution for skills-first workflow stage execution."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping, Optional

from moonmind.config.settings import settings

from .contracts import StageExecutionDecision


def _stable_percent(run_id: str, stage_name: str) -> int:
    seed = f"{run_id}:{stage_name}".encode("utf-8")
    digest = sha256(seed).hexdigest()
    return int(digest[:8], 16) % 100


def _select_stage_skill(stage_name: str, context: Mapping[str, Any]) -> str:
    override = context.get("skill_overrides")
    if isinstance(override, Mapping):
        raw = override.get(stage_name)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()

    cfg = settings.spec_workflow
    stage_map = {
        "discover_next_phase": cfg.discover_skill,
        "submit_codex_job": cfg.submit_skill,
        "apply_and_publish": cfg.publish_skill,
    }
    selected = stage_map.get(stage_name)
    if selected:
        return selected
    return cfg.default_skill


def _skill_allowed(skill_name: str) -> bool:
    allowed = settings.spec_workflow.allowed_skills
    if not allowed:
        return True
    return skill_name in allowed


def resolve_stage_execution(
    *,
    stage_name: str,
    run_id: str,
    context: Mapping[str, Any],
) -> StageExecutionDecision:
    """Resolve whether a stage should run through skills policy or direct mode."""

    cfg = settings.spec_workflow
    selected_skill = _select_stage_skill(stage_name, context)
    if not _skill_allowed(selected_skill):
        selected_skill = cfg.default_skill

    canary_bucket = _stable_percent(run_id, stage_name)
    canary_enabled = canary_bucket < cfg.skills_canary_percent
    use_skills = bool(cfg.skills_enabled and canary_enabled)
    execution_path = "skill" if use_skills else "direct_only"

    return StageExecutionDecision(
        stage_name=stage_name,
        selected_skill=selected_skill,
        use_skills=use_skills,
        execution_path=execution_path,
        fallback_enabled=bool(cfg.skills_fallback_enabled),
        shadow_mode=bool(cfg.skills_shadow_mode),
    )


def get_stage_adapter(skill_name: str) -> Optional[str]:
    """Return the adapter id for a skill.

    This keeps the registry extensible while preserving current Speckit-backed
    execution as the default path.
    """

    if skill_name == "speckit":
        return "speckit"
    return None
