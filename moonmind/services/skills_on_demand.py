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
SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_CODE = "enabled_mode_not_implemented"
SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_MESSAGE = (
    "Skills On Demand enabled mode is not implemented for this deployment."
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
        code, message = self._denial_contract()
        return SkillsOnDemandQueryResult(
            status="denied",
            code=code,
            message=message,
            results=[],
        )

    async def request(
        self,
        request: SkillsOnDemandRequest,
    ) -> SkillsOnDemandRequestResult:
        code, message = self._denial_contract()
        active_snapshot = request.active_snapshot
        return SkillsOnDemandRequestResult(
            status="denied",
            code=code,
            message=message,
            active_snapshot_id=active_snapshot.snapshot_id if active_snapshot else None,
            snapshot_id=None,
            resolved_skillset_ref=None,
        )

    def _denial_contract(self) -> tuple[str, str]:
        if self._enabled:
            return (
                SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_CODE,
                SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_MESSAGE,
            )
        return SKILLS_ON_DEMAND_DISABLED_CODE, SKILLS_ON_DEMAND_DISABLED_MESSAGE


def skills_on_demand_disabled_instruction(*, enabled: bool) -> str:
    """Return runtime guidance when on-demand Skill commands cannot be hidden."""

    return "" if enabled else SKILLS_ON_DEMAND_DISABLED_INSTRUCTION
