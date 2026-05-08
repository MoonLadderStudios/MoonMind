from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
    SkillsOnDemandQueryRequest,
    SkillsOnDemandRequest,
    SkillsOnDemandRequestedSkill,
)
from moonmind.services.skills_on_demand import (
    SKILLS_ON_DEMAND_DISABLED_CODE,
    SKILLS_ON_DEMAND_DISABLED_MESSAGE,
    SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_CODE,
    SkillsOnDemandService,
)
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities

pytestmark = [pytest.mark.asyncio]


async def test_disabled_query_returns_denied_without_catalog_results() -> None:
    result = await SkillsOnDemandService(enabled=False).query(
        SkillsOnDemandQueryRequest(query="jira")
    )

    assert result.status == "denied"
    assert result.code == SKILLS_ON_DEMAND_DISABLED_CODE
    assert result.message == SKILLS_ON_DEMAND_DISABLED_MESSAGE
    assert result.results == []


async def test_disabled_request_returns_denied_without_snapshot_mutation() -> None:
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="moonspec-implement",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.BUILT_IN
                ),
            )
        ],
    )

    result = await SkillsOnDemandService(enabled=False).request(
        SkillsOnDemandRequest(
            requested_skills=[
                SkillsOnDemandRequestedSkill(name="jira-issue-updater")
            ],
            active_snapshot=active_snapshot,
        )
    )

    assert result.status == "denied"
    assert result.code == SKILLS_ON_DEMAND_DISABLED_CODE
    assert result.message == SKILLS_ON_DEMAND_DISABLED_MESSAGE
    assert result.snapshot_id is None
    assert result.resolved_skillset_ref is None
    assert result.active_snapshot_id == "skillset-active"


async def test_enabled_query_returns_structured_not_implemented_result() -> None:
    result = await SkillsOnDemandService(enabled=True).query(
        SkillsOnDemandQueryRequest(query="jira")
    )

    assert result.status == "denied"
    assert result.code == SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_CODE
    assert result.results == []


async def test_enabled_request_returns_structured_not_implemented_result() -> None:
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )

    result = await SkillsOnDemandService(enabled=True).request(
        SkillsOnDemandRequest(
            requested_skills=[
                SkillsOnDemandRequestedSkill(name="jira-issue-updater")
            ],
            active_snapshot=active_snapshot,
        )
    )

    assert result.status == "denied"
    assert result.code == SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_CODE
    assert result.active_snapshot_id == "skillset-active"
    assert result.snapshot_id is None
    assert result.resolved_skillset_ref is None


async def test_disabled_activity_query_does_not_use_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_on_demand_enabled",
        False,
    )
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()

    with patch(
        "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve"
    ) as mock_resolve:
        result = await env.run(
            activities.query_on_demand,
            SkillsOnDemandQueryRequest(query="jira"),
        )

    assert result.status == "denied"
    assert result.code == SKILLS_ON_DEMAND_DISABLED_CODE
    mock_resolve.assert_not_called()


async def test_disabled_activity_request_does_not_materialize_new_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_on_demand_enabled",
        False,
    )
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()

    with patch(
        "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillMaterializer.materialize"
    ) as mock_materialize:
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
    assert result.active_snapshot_id == "skillset-active"
    mock_materialize.assert_not_called()


async def test_enabled_activity_query_returns_typed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_on_demand_enabled",
        True,
    )
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()

    result = await env.run(
        activities.query_on_demand,
        SkillsOnDemandQueryRequest(query="jira"),
    )

    assert result.status == "denied"
    assert result.code == SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_CODE
