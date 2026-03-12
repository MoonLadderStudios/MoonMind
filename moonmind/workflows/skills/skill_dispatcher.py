"""Legacy compatibility exports for skill-named dispatcher helpers."""

from __future__ import annotations

from .tool_dispatcher import ToolActivityDispatcher as SkillActivityDispatcher
from .tool_dispatcher import ToolDispatchError as SkillDispatchError
from .tool_dispatcher import execute_tool_activity as execute_skill_activity
from .tool_dispatcher import plan_validate_activity

__all__ = [
    "SkillActivityDispatcher",
    "SkillDispatchError",
    "execute_skill_activity",
    "plan_validate_activity",
]
