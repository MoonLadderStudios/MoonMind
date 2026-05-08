import hashlib
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


def _entry(
    name: str,
    *,
    content_ref: str | None = None,
    content_digest: str | None = None,
) -> ResolvedSkillEntry:
    return ResolvedSkillEntry(
        skill_name=name,
        version="1.0.0",
        content_ref=content_ref,
        content_digest=content_digest,
        provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.BUILT_IN),
    )


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class _StaticArtifactService:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads
        self.created: list[dict] = []

    async def read(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool,
    ) -> tuple[object, bytes]:
        del principal, allow_restricted_raw
        return object(), self._payloads[artifact_id]

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return type("Artifact", (), {"artifact_id": f"artifact-{len(self.created)}"})(), None

    async def write_complete(self, **_kwargs) -> None:
        return None


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


async def test_enabled_on_demand_request_activates_after_real_verified_materialization(
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
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_cache_root",
        None,
    )
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_workspace_root",
        None,
    )
    payload = b"---\nname: jira-issue-updater\ndescription: updater\n---\n"
    artifact_service = _StaticArtifactService({"requested-body-ref": payload})
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )
    resolved_additions = ResolvedSkillSet(
        snapshot_id="resolver-snapshot",
        resolved_at=datetime.now(UTC),
        skills=[
            _entry(
                "jira-issue-updater",
                content_ref="requested-body-ref",
                content_digest=_digest(payload),
            )
        ],
    )
    activities = AgentSkillsActivities(artifact_service=artifact_service)
    env = ActivityEnvironment()

    with patch(
        "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve",
        new=AsyncMock(return_value=resolved_additions),
    ):
        result = await env.run(
            activities.request_on_demand,
            SkillsOnDemandRequest(
                current_snapshot_ref="skillset-active",
                requested_skills=[
                    SkillsOnDemandRequestedSkill(name="jira-issue-updater")
                ],
                active_snapshot=active_snapshot,
                runtime_id="codex",
            ),
        )

    visible_dir = tmp_path / ".agents" / "skills"
    assert result.status == "activated"
    assert result.metadata["activation_timing"] == "atomic"
    assert result.metadata["materialization_verified"] is True
    assert visible_dir.is_symlink()
    assert (
        visible_dir / "jira-issue-updater" / "SKILL.md"
    ).read_bytes() == payload


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


async def test_enabled_on_demand_request_preserves_snapshot_on_checksum_failure(
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
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_cache_root",
        None,
    )
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_workspace_root",
        None,
    )
    old_active_dir = tmp_path / "runtime" / "skills_active" / "skillset-active"
    old_skill = old_active_dir / "old-skill" / "SKILL.md"
    old_skill.parent.mkdir(parents=True)
    old_skill.write_text("old active skill\n", encoding="utf-8")
    alias = tmp_path / ".agents" / "skills"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(old_active_dir)
    payload = b"---\nname: jira-issue-updater\ndescription: updater\n---\n"
    artifact_service = _StaticArtifactService({"requested-body-ref": payload})
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )
    resolved_additions = ResolvedSkillSet(
        snapshot_id="resolver-snapshot",
        resolved_at=datetime.now(UTC),
        skills=[
            _entry(
                "jira-issue-updater",
                content_ref="requested-body-ref",
                content_digest="sha256:wrong",
            )
        ],
    )
    activities = AgentSkillsActivities(artifact_service=artifact_service)
    env = ActivityEnvironment()

    with patch(
        "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve",
        new=AsyncMock(return_value=resolved_additions),
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
    assert alias.is_symlink()
    assert alias.resolve() == old_active_dir.resolve()
    assert old_skill.read_text(encoding="utf-8") == "old active skill\n"


async def test_enabled_on_demand_request_returns_next_turn_when_repo_skills_present(
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
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_cache_root",
        None,
    )
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_workspace_root",
        None,
    )
    repo_skill = tmp_path / ".agents" / "skills" / "repo-skill" / "SKILL.md"
    repo_skill.parent.mkdir(parents=True)
    repo_skill.write_text("repo-authored source\n", encoding="utf-8")
    payload = b"---\nname: jira-issue-updater\ndescription: updater\n---\n"
    artifact_service = _StaticArtifactService({"requested-body-ref": payload})
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )
    resolved_additions = ResolvedSkillSet(
        snapshot_id="resolver-snapshot",
        resolved_at=datetime.now(UTC),
        skills=[
            _entry(
                "jira-issue-updater",
                content_ref="requested-body-ref",
                content_digest=_digest(payload),
            )
        ],
    )
    activities = AgentSkillsActivities(artifact_service=artifact_service)
    env = ActivityEnvironment()

    with patch(
        "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve",
        new=AsyncMock(return_value=resolved_additions),
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

    assert result.status == "activated"
    assert result.metadata["activation_timing"] == "next_turn"
    assert result.metadata["materialization_verified"] is True
    assert repo_skill.read_text(encoding="utf-8") == "repo-authored source\n"
    assert (tmp_path / ".agents" / "skills").is_dir()
    assert not (tmp_path / ".agents" / "skills").is_symlink()


async def test_enabled_on_demand_request_reports_runtime_refresh_failure(
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
    materialization = RuntimeSkillMaterialization(
        runtime_id="codex",
        materialization_mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        workspace_paths=[str(tmp_path / ".agents" / "skills")],
        metadata={
            "runtimeRefreshFailed": True,
            "runtimeRefreshMessage": "runtime refresh update failed",
        },
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
            new=AsyncMock(return_value=materialization),
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
    assert result.code == "runtime_refresh_failed"
    assert result.active_snapshot_id == "skillset-active"
    assert result.snapshot_id is None
