import pytest
from unittest.mock import patch
from datetime import datetime
from moonmind.schemas.agent_skill_models import AgentSkillSourceKind, AgentSkillProvenance, ResolvedSkillEntry

from moonmind.schemas.agent_skill_models import (
    SkillSelector,
    ResolvedSkillSet,
)
from temporalio.testing import ActivityEnvironment
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities

pytestmark = [pytest.mark.asyncio]


async def test_resolve_skills_activity_returns_expected_payload():
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()
    
    selector = SkillSelector(include=[{"name": "read_file"}])
    with patch("moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve") as mock_resolve:
        mock_resolve.return_value = ResolvedSkillSet(
            snapshot_id="skillset_test_abc123",
            resolved_at=datetime.utcnow(),
            skills=[
                ResolvedSkillEntry(
                    skill_name="read_file",
                    provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.BUILT_IN)
                )
            ]
        )
        result = await env.run(activities.resolve_skills, selector, "run-1", None, False)
        
    assert result.snapshot_id.startswith("skillset_")
    assert isinstance(result.skills, list)
    assert len(result.skills) == 1
    assert result.skills[0].skill_name == "read_file"
    assert result.skills[0].provenance.source_kind == AgentSkillSourceKind.BUILT_IN

async def test_build_prompt_index_activity_returns_bundle():
    activities = AgentSkillsActivities()
    ActivityEnvironment()
    
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
