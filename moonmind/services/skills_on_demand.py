"""Disabled-first Skills On Demand runtime controls."""

from moonmind.schemas.agent_skill_models import (
    SkillsOnDemandQueryRequest,
    SkillsOnDemandQueryResult,
    SkillsOnDemandRequest,
    SkillsOnDemandRequestResult,
)

SKILLS_ON_DEMAND_DISABLED_CODE = "feature_disabled"
SKILLS_ON_DEMAND_DISABLED_MESSAGE = (
    "Skills On Demand is disabled for this deployment."
)
SKILLS_ON_DEMAND_DISABLED_INSTRUCTION = (
    "- Skills On Demand is disabled for this run. Use only the active Skills "
    "already available under the active skill path provided by MoonMind."
)


class SkillsOnDemandService:
    """Handle runtime on-demand Skill operations with disabled default semantics."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

    async def query(
        self,
        request: SkillsOnDemandQueryRequest,
    ) -> SkillsOnDemandQueryResult:
        del request
        self._ensure_disabled_scope()
        return SkillsOnDemandQueryResult(
            status="denied",
            code=SKILLS_ON_DEMAND_DISABLED_CODE,
            message=SKILLS_ON_DEMAND_DISABLED_MESSAGE,
            results=[],
        )

    async def request(
        self,
        request: SkillsOnDemandRequest,
    ) -> SkillsOnDemandRequestResult:
        self._ensure_disabled_scope()
        active_snapshot = request.active_snapshot
        return SkillsOnDemandRequestResult(
            status="denied",
            code=SKILLS_ON_DEMAND_DISABLED_CODE,
            message=SKILLS_ON_DEMAND_DISABLED_MESSAGE,
            active_snapshot_id=active_snapshot.snapshot_id if active_snapshot else None,
            snapshot_id=None,
            resolved_skillset_ref=None,
        )

    def _ensure_disabled_scope(self) -> None:
        if self._enabled:
            raise RuntimeError("Skills On Demand enabled mode is not implemented")


def skills_on_demand_disabled_instruction(*, enabled: bool) -> str:
    """Return runtime guidance when on-demand Skill commands cannot be hidden."""

    return "" if enabled else SKILLS_ON_DEMAND_DISABLED_INSTRUCTION
