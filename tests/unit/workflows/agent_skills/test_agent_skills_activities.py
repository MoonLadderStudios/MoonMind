from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
    RuntimeMaterializationMode,
    SkillSelector,
)
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities

pytestmark = [pytest.mark.asyncio]

async def test_resolve_skills_activity_returns_expected_payload():
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()
    
    selector = SkillSelector(include=[{"name": "read_file"}])
    with patch("moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve") as mock_resolve:
        mock_resolve.return_value = ResolvedSkillSet(
            snapshot_id="skillset_test_abc123",
            resolved_at=datetime.now(UTC),
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

async def test_resolve_skills_activity_passes_repo_and_local_policy():
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()

    selector = SkillSelector(include=[{"name": "repo_skill"}])
    with patch(
        "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve"
    ) as mock_resolve:
        mock_resolve.return_value = ResolvedSkillSet(
            snapshot_id="skillset_test_abc123",
            resolved_at=datetime.now(UTC),
            skills=[],
            policy_summary={
                "repo_skills_allowed": False,
                "local_skills_allowed": True,
            },
        )

        result = await env.run(
            activities.resolve_skills,
            selector,
            "run-1",
            "/tmp/workspace",
            True,
            False,
        )

    context = mock_resolve.call_args.args[1]
    assert context.allow_repo_skills is False
    assert context.allow_local_skills is True
    assert result.policy_summary["repo_skills_allowed"] is False
    assert result.policy_summary["local_skills_allowed"] is True

async def test_resolve_skills_activity_persists_file_backed_skill_content(
    tmp_path,
):
    skill_dir = tmp_path / "skills" / "pr-resolver"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# PR Resolver\n\nUse the resolved body.\n", encoding="utf-8")

    artifact_service = _RecordingArtifactService()
    activities = AgentSkillsActivities(artifact_service=artifact_service)
    env = ActivityEnvironment()
    selector = SkillSelector(include=[{"name": "pr-resolver"}])

    with patch(
        "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve"
    ) as mock_resolve:
        mock_resolve.return_value = ResolvedSkillSet(
            snapshot_id="skillset_test_abc123",
            resolved_at=datetime.now(UTC),
            skills=[
                ResolvedSkillEntry(
                    skill_name="pr-resolver",
                    version="1.0.0",
                    provenance=AgentSkillProvenance(
                        source_kind=AgentSkillSourceKind.BUILT_IN,
                        source_path=str(skill_dir),
                    ),
                )
            ],
        )

        result = await env.run(
            activities.resolve_skills,
            selector,
            "run-1",
            None,
            False,
            False,
        )

    assert result.skills[0].content_ref == "art-1"
    assert result.skills[0].content_digest.startswith("sha256:")
    assert result.manifest_ref == "art-2"
    assert artifact_service.payloads["art-1"] == skill_file.read_bytes()
    manifest = artifact_service.payloads["art-2"].decode("utf-8")
    assert '"content_ref": "art-1"' in manifest

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

async def test_materialize_activity_returns_materialization():
    import tempfile
    
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()
    
    snapshot = ResolvedSkillSet(
        snapshot_id="snap-materialize",
        resolved_at="2024-01-01T00:00:00Z",
        skills=[]
    )
    
    with tempfile.TemporaryDirectory() as tempdir:
        result = await env.run(
            activities.materialize,
            snapshot,
            "test_runtime",
            RuntimeMaterializationMode.WORKSPACE_MOUNTED,
            tempdir
        )
        
        assert result.runtime_id == "test_runtime"
        assert result.materialization_mode == RuntimeMaterializationMode.WORKSPACE_MOUNTED
        assert len(result.workspace_paths) == 1

async def test_materialize_activity_returns_canonical_agents_skills_metadata(
    tmp_path,
):
    activities = AgentSkillsActivities(
        artifact_service=_RecordingArtifactService(
            initial_payloads={"artifact-read-file": b"# Read File\n"}
        )
    )
    env = ActivityEnvironment()
    snapshot = ResolvedSkillSet(
        snapshot_id="snap-canonical",
        resolved_at=datetime.now(UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="read_file",
                content_ref="artifact-read-file",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.BUILT_IN
                ),
            )
        ],
    )

    result = await env.run(
        activities.materialize,
        snapshot,
        "codex",
        RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        str(tmp_path),
    )

    visible_path = tmp_path / ".agents" / "skills"
    assert result.workspace_paths == [str(visible_path)]
    assert result.metadata["visiblePath"] == str(visible_path)
    assert result.metadata["manifestPath"] == str(visible_path / "_manifest.json")
    assert result.metadata["activeSkills"] == ["read_file"]


class _RecordingArtifactService:
    def __init__(self, initial_payloads: dict[str, bytes] | None = None) -> None:
        self.payloads: dict[str, bytes] = dict(initial_payloads or {})
        self.created: list[dict[str, object]] = []

    async def create(self, **kwargs):
        artifact_id = f"art-{len(self.created) + 1}"
        self.created.append(kwargs)
        return SimpleNamespace(artifact_id=artifact_id), None

    async def write_complete(
        self,
        *,
        artifact_id: str,
        principal: str,
        payload: bytes,
        content_type: str,
    ):
        del principal, content_type
        self.payloads[artifact_id] = payload
        return SimpleNamespace(artifact_id=artifact_id)

    async def read(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool,
    ):
        del principal, allow_restricted_raw
        return SimpleNamespace(artifact_id=artifact_id), self.payloads[artifact_id]
