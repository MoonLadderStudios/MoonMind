from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.schemas.agent_skill_models import (
    ResolvedSkillSet,
    SkillsOnDemandRequest,
    SkillsOnDemandRequestedSkill,
)
from moonmind.services.skills_on_demand import SKILLS_ON_DEMAND_DISABLED_CODE
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
]


async def test_disabled_on_demand_activity_preserves_active_snapshot_without_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_on_demand_enabled",
        False,
    )
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-existing",
        resolved_at=datetime.now(UTC),
        skills=[],
    )
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()

    with patch(
        "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve"
    ) as mock_resolve:
        result = await env.run(
            activities.request_on_demand,
            SkillsOnDemandRequest(
                requested_skills=[
                    SkillsOnDemandRequestedSkill(name="jira-issue-updater")
                ],
                active_snapshot=active_snapshot,
            ),
        )

    assert result.status == "denied"
    assert result.code == SKILLS_ON_DEMAND_DISABLED_CODE
    assert result.active_snapshot_id == "skillset-existing"
    assert result.snapshot_id is None
    assert len(result.audit_events) == 1
    assert result.audit_events[0].event_type == "skills_on_demand.request"
    assert result.audit_events[0].result == "denied"
    assert result.audit_events[0].result_code == SKILLS_ON_DEMAND_DISABLED_CODE
    assert result.audit_events[0].parent_snapshot_id == "skillset-existing"
    assert result.failure_diagnostic is not None
    assert result.failure_diagnostic.current_snapshot_ref == "skillset-existing"
    mock_resolve.assert_not_called()
