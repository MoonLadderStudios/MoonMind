from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
    RuntimeMaterializationMode,
    RuntimeSkillMaterialization,
    SkillsOnDemandRequest,
    SkillsOnDemandRequestedSkill,
)
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
]


def _entry(name: str, *, content_ref: str | None = None) -> ResolvedSkillEntry:
    return ResolvedSkillEntry(
        skill_name=name,
        version="1.0.0",
        content_ref=content_ref,
        provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.BUILT_IN),
    )


async def test_enabled_on_demand_request_resolves_and_materializes_derived_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_on_demand_enabled",
        True,
    )
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.repo_root",
        str(tmp_path),
    )
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        manifest_ref="manifest-active",
        resolved_at=datetime.now(UTC),
        skills=[_entry("moonspec-plan", content_ref="active-body-ref")],
    )
    resolved_additions = ResolvedSkillSet(
        snapshot_id="resolver-snapshot",
        resolved_at=datetime.now(UTC),
        skills=[_entry("jira-issue-updater", content_ref="requested-body-ref")],
    )
    materialization = RuntimeSkillMaterialization(
        runtime_id="codex",
        materialization_mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        workspace_paths=[str(tmp_path / ".agents" / "skills")],
        metadata={
            "visiblePath": str(tmp_path / ".agents" / "skills"),
            "manifestPath": str(
                tmp_path / "runtime" / "skills_active" / "skillset-derived" / "_manifest.json"
            ),
        },
    )
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()

    with (
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve",
            new=AsyncMock(return_value=resolved_additions),
        ) as mock_resolve,
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillMaterializer.materialize",
            new=AsyncMock(return_value=materialization),
        ) as mock_materialize,
    ):
        result = await env.run(
            activities.request_on_demand,
            SkillsOnDemandRequest(
                current_snapshot_ref="skillset-active",
                requested_skills=[
                    SkillsOnDemandRequestedSkill(name="jira-issue-updater")
                ],
                reason="Need Jira workflow",
                runtime_id="codex",
                step_id="step-1",
                active_snapshot=active_snapshot,
            ),
        )

    assert result.status == "activated"
    assert result.parent_snapshot_ref == "skillset-active"
    assert result.snapshot_id
    assert result.resolved_skillset_ref
    assert result.metadata["requested_skills"] == ["jira-issue-updater"]
    assert result.metadata["activated_skills"] == ["jira-issue-updater"]
    assert "requested-body-ref" not in str(result.model_dump(mode="json"))
    mock_resolve.assert_awaited_once()
    mock_materialize.assert_awaited_once()


async def test_enabled_on_demand_request_preserves_snapshot_on_materialization_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_on_demand_enabled",
        True,
    )
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.repo_root",
        str(tmp_path),
    )
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )
    resolved_additions = ResolvedSkillSet(
        snapshot_id="resolver-snapshot",
        resolved_at=datetime.now(UTC),
        skills=[_entry("jira-issue-updater", content_ref="requested-body-ref")],
    )
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()

    with (
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve",
            new=AsyncMock(return_value=resolved_additions),
        ),
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillMaterializer.materialize",
            new=AsyncMock(side_effect=RuntimeError("materialization exploded")),
        ),
    ):
        result = await env.run(
            activities.request_on_demand,
            SkillsOnDemandRequest(
                current_snapshot_ref="skillset-active",
                requested_skills=[
                    SkillsOnDemandRequestedSkill(name="jira-issue-updater")
                ],
                active_snapshot=active_snapshot,
            ),
        )

    assert result.status == "denied"
    assert result.code == "materialization_failed"
    assert result.active_snapshot_id == "skillset-active"
    assert result.parent_snapshot_ref == "skillset-active"
    assert result.snapshot_id is None
    assert result.resolved_skillset_ref is None
