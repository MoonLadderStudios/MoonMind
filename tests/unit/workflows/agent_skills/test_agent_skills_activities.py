import pytest

from moonmind.schemas.agent_skill_models import (
    SkillSelector,
    ResolvedSkillSet,
)
from moonmind.services.skill_resolution import SkillResolutionContext
from temporalio.testing import ActivityEnvironment
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities

pytestmark = [pytest.mark.asyncio]


async def test_resolve_skills_activity_returns_expected_payload():
    activities = AgentSkillsActivities()
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()
    
    selector = SkillSelector(include=[{"name": "read_file"}])
    result = await env.run(activities.resolve_skills, selector, "run-1", None, False)
    
    assert result.snapshot_id.startswith("skillset_")
    assert isinstance(result.skills, list)
    assert len(result.skills) == 1
    assert result.skills[0].skill_name == "read_file"
    assert result.skills[0].provenance.source_kind == "built_in"

async def test_build_prompt_index_activity_returns_bundle():
    activities = AgentSkillsActivities()
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()
    
    snapshot = ResolvedSkillSet(
        snapshot_id="snap-temp",
        resolved_at="2024-01-01T00:00:00Z",
        skills=[
            {
                "skill_name": "read_file",
                "provenance": {
                    "source_kind": "built_in"
                }
            }
        ]
    )
    
    result = await activities.build_prompt_index(snapshot)
    
    assert "Snapshot: snap-temp" in result
