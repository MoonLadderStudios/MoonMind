"""Legacy compatibility exports for skill-named dispatcher helpers."""

from __future__ import annotations

from .tool_dispatcher import *  # noqa: F401,F403
from .tool_dispatcher import (
    ToolActivityDispatcher as SkillActivityDispatcher,
    ToolDispatchError as SkillDispatchError,
    execute_tool_activity as execute_skill_activity,
)

__all__ = [
    "SkillActivityDispatcher",
    "SkillDispatchError",
    "execute_skill_activity",
    "plan_validate_activity",
]
