from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
    SkillCatalogSearchResult,
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


def _entry(
    name: str,
    *,
    source_kind: AgentSkillSourceKind = AgentSkillSourceKind.BUILT_IN,
    version: str | None = "1.0.0",
    content_ref: str | None = None,
    source_path: str | None = None,
) -> ResolvedSkillEntry:
    return ResolvedSkillEntry(
        skill_name=name,
        version=version,
        content_ref=content_ref,
        content_digest="sha256:secret-digest" if content_ref else None,
        provenance=AgentSkillProvenance(
            source_kind=source_kind,
            source_path=source_path,
        ),
    )


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


async def test_enabled_query_returns_metadata_only_results() -> None:
    result = await SkillsOnDemandService(
        enabled=True,
        catalog_entries=[
            _entry(
                "jira-issue-updater",
                content_ref="artifact-body-ref",
                source_path="/hidden/SKILL.md",
            ),
            _entry("moonspec-plan"),
        ],
    ).query(
        SkillsOnDemandQueryRequest(query="jira", runtime_id="codex", max_results=5)
    )

    assert result.status == "ok"
    assert result.code is None
    assert result.metadata["result_count"] == 1
    assert result.results == [
        SkillCatalogSearchResult(
            name="jira-issue-updater",
            latest_version="1.0.0",
            source_kind=AgentSkillSourceKind.BUILT_IN,
            eligible=True,
            in_current_snapshot=False,
            eligibility_summary="Eligible for this runtime and deployment policy.",
        )
    ]
    serialized = result.model_dump(mode="json")
    assert "artifact-body-ref" not in str(serialized)
    assert "secret-digest" not in str(serialized)
    assert "/hidden/SKILL.md" not in str(serialized)


async def test_enabled_query_validates_blank_query() -> None:
    result = await SkillsOnDemandService(
        enabled=True,
        catalog_entries=[_entry("jira-issue-updater")],
    ).query(SkillsOnDemandQueryRequest(query="   "))

    assert result.status == "denied"
    assert result.code == "invalid_request"
    assert result.results == []
    assert result.metadata["denied"] is True


async def test_enabled_query_validates_blank_context_values() -> None:
    service = SkillsOnDemandService(
        enabled=True,
        catalog_entries=[_entry("jira-issue-updater")],
    )

    runtime_result = await service.query(
        SkillsOnDemandQueryRequest(query="jira", runtime_id=" ")
    )
    snapshot_result = await service.query(
        SkillsOnDemandQueryRequest(query="jira", current_snapshot_ref=" ")
    )

    assert runtime_result.status == "denied"
    assert runtime_result.code == "invalid_request"
    assert snapshot_result.status == "denied"
    assert snapshot_result.code == "invalid_request"


async def test_enabled_query_marks_policy_ineligible_matches() -> None:
    result = await SkillsOnDemandService(
        enabled=True,
        catalog_entries=[
            _entry(
                "local-jira-helper",
                source_kind=AgentSkillSourceKind.LOCAL,
            )
        ],
        allow_local_skills=False,
    ).query(SkillsOnDemandQueryRequest(query="jira"))

    assert result.status == "ok"
    assert len(result.results) == 1
    assert result.results[0].eligible is False
    assert result.results[0].eligibility_summary == (
        "Blocked because local Skill sources are disabled for this query."
    )


async def test_enabled_query_marks_current_snapshot_membership() -> None:
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[_entry("jira-issue-updater")],
    )

    result = await SkillsOnDemandService(
        enabled=True,
        catalog_entries=[_entry("jira-issue-updater")],
    ).query(
        SkillsOnDemandQueryRequest(
            query="jira",
            current_snapshot_ref="skillset-active",
            active_snapshot=active_snapshot,
        )
    )

    assert result.status == "ok"
    assert result.results[0].in_current_snapshot is True


async def test_enabled_query_respects_max_results() -> None:
    result = await SkillsOnDemandService(
        enabled=True,
        catalog_entries=[
            _entry("jira-a"),
            _entry("jira-b"),
            _entry("jira-c"),
        ],
    ).query(SkillsOnDemandQueryRequest(query="jira", max_results=2))

    assert result.status == "ok"
    assert [entry.name for entry in result.results] == ["jira-a", "jira-b"]
    assert result.metadata["result_count"] == 2


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

    async def fake_query_catalog(*args, **kwargs):
        return [_entry("jira-issue-updater", content_ref="artifact-body-ref")]

    with (
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.query_catalog",
            side_effect=fake_query_catalog,
        ) as mock_query_catalog,
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillMaterializer.materialize"
        ) as mock_materialize,
    ):
        result = await env.run(
            activities.query_on_demand,
            SkillsOnDemandQueryRequest(query="jira"),
        )

    assert result.status == "ok"
    assert result.results[0].name == "jira-issue-updater"
    assert result.results[0].eligible is True
    assert "artifact-body-ref" not in str(result.model_dump(mode="json"))
    mock_query_catalog.assert_awaited_once()
    mock_materialize.assert_not_called()
