"""Typed contracts for skills-first stage execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class StageExecutionDecision:
    """Resolved execution policy for one workflow stage."""

    stage_name: str
    selected_skill: str
    use_skills: bool
    execution_path: str
    fallback_enabled: bool
    shadow_mode: bool


@dataclass(frozen=True, slots=True)
class StageExecutionOutcome:
    """Execution result metadata for a workflow stage."""

    stage_name: str
    selected_skill: str
    execution_path: str
    used_fallback: bool
    used_skills: bool
    shadow_mode_requested: bool
    result: Any

    def to_payload(self) -> dict[str, Any]:
        """Return a serializable payload fragment for logs/task payloads."""

        return {
            "selectedSkill": self.selected_skill,
            "executionPath": self.execution_path,
            "usedSkills": self.used_skills,
            "usedFallback": self.used_fallback,
            "shadowModeRequested": self.shadow_mode_requested,
        }
