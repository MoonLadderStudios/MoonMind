import json
import io
import tarfile
from datetime import datetime, UTC
from pathlib import Path

import pytest

from moonmind.schemas.agent_skill_models import (
    AgentSkillFormat,
    AgentSkillSourceKind,
    AgentSkillProvenance,
    ResolvedSkillEntry,
    ResolvedSkillSet,
    RuntimeMaterializationMode,
)
from moonmind.services.skill_materialization import AgentSkillMaterializer

@pytest.mark.asyncio
async def test_materializer_projects_selected_skill_to_agents_skills(tmp_path: Path):
    artifact_service = _StaticArtifactService(
        {"artifact-my-skill": b"---\nname: my_skill\ndescription: test\n---\n"}
    )
    materializer = AgentSkillMaterializer(
        str(tmp_path), artifact_service=artifact_service
    )

    skillset = ResolvedSkillSet(
        snapshot_id="test_snap_123",
        resolved_at=datetime.now(tz=UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="my_skill",
                version="1.0.0",
                content_ref="artifact-my-skill",
                content_digest="sha256:abc123",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.DEPLOYMENT
                ),
            )
        ],
    )

    result = await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )

    visible_dir = tmp_path / ".agents" / "skills"
    backing_dir = tmp_path / "skills_active"
    manifest_path = visible_dir / "_manifest.json"

    assert result.runtime_id == "test_runtime"
    assert result.materialization_mode == RuntimeMaterializationMode.WORKSPACE_MOUNTED
    assert visible_dir.is_symlink()
    assert visible_dir.resolve() == backing_dir.resolve()
    assert manifest_path.exists()
    assert (visible_dir / "my_skill" / "SKILL.md").read_text(
        encoding="utf-8"
    ).startswith("---\nname: my_skill")
    assert str(visible_dir) in result.workspace_paths

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == {
        "backing_path": str(backing_dir),
        "materialization_mode": "workspace_mounted",
        "resolved_at": skillset.resolved_at.isoformat(),
        "runtime_id": "test_runtime",
        "skills": [
            {
                "content_digest": "sha256:abc123",
                "content_ref": "artifact-my-skill",
                "name": "my_skill",
                "source_kind": "deployment",
                "version": "1.0.0",
            }
        ],
        "snapshot_id": "test_snap_123",
        "visible_path": str(visible_dir),
    }
    assert result.metadata["visiblePath"] == str(visible_dir)
    assert result.metadata["backingPath"] == str(backing_dir)
    assert result.metadata["manifestPath"] == str(manifest_path)
    assert result.metadata["activeSkills"] == ["my_skill"]

@pytest.mark.asyncio
async def test_materializer_projects_only_selected_skills(tmp_path: Path):
    repo_skill = tmp_path / "repo" / ".agents" / "skills" / "unselected_skill"
    repo_skill.mkdir(parents=True)
    (repo_skill / "SKILL.md").write_text(
        "---\nname: unselected_skill\ndescription: repo\n---\n",
        encoding="utf-8",
    )
    artifact_service = _StaticArtifactService(
        {
            "artifact-alpha": b"---\nname: alpha\ndescription: test\n---\n",
            "artifact-beta": b"---\nname: beta\ndescription: test\n---\n",
        }
    )
    materializer = AgentSkillMaterializer(
        str(tmp_path), artifact_service=artifact_service
    )
    skillset = ResolvedSkillSet(
        snapshot_id="multi_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[
            _skill("alpha", "artifact-alpha"),
            _skill("beta", "artifact-beta"),
        ],
    )

    await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )

    visible_dir = tmp_path / ".agents" / "skills"
    assert sorted(path.name for path in visible_dir.iterdir()) == [
        "_manifest.json",
        "alpha",
        "beta",
    ]
    assert not (visible_dir / "unselected_skill").exists()

@pytest.mark.asyncio
async def test_materializer_extracts_skill_bundle_with_companion_files(
    tmp_path: Path,
):
    artifact_service = _StaticArtifactService(
        {
            "artifact-bundle": _skill_bundle_payload(
                {
                    "SKILL.md": b"# Bundle Skill\n",
                    "bin/run.py": b"print('run')\n",
                }
            )
        }
    )
    materializer = AgentSkillMaterializer(
        str(tmp_path), artifact_service=artifact_service
    )
    skillset = ResolvedSkillSet(
        snapshot_id="bundle_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="bundle_skill",
                version="1.0.0",
                format=AgentSkillFormat.BUNDLE,
                content_ref="artifact-bundle",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.BUILT_IN
                ),
            )
        ],
    )

    await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )

    visible_dir = tmp_path / ".agents" / "skills"
    assert (visible_dir / "bundle_skill" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# Bundle Skill\n"
    assert (visible_dir / "bundle_skill" / "bin" / "run.py").read_text(
        encoding="utf-8"
    ) == "print('run')\n"

@pytest.mark.asyncio
async def test_materializer_rejects_incompatible_agents_skills_path(tmp_path: Path):
    source_dir = tmp_path / ".agents" / "skills"
    source_dir.mkdir(parents=True)
    source_file = source_dir / "SKILL.md"
    source_file.write_text("do not rewrite", encoding="utf-8")
    materializer = AgentSkillMaterializer(str(tmp_path))
    skillset = ResolvedSkillSet(
        snapshot_id="blocked_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[],
    )

    with pytest.raises(RuntimeError) as exc_info:
        await materializer.materialize(
            resolved_skillset=skillset,
            runtime_id="test_runtime",
            mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        )

    message = str(exc_info.value)
    assert str(source_dir) in message
    assert "object kind: directory" in message
    assert "attempted action: project active skill snapshot" in message
    assert "remediation:" in message
    assert source_file.read_text(encoding="utf-8") == "do not rewrite"

@pytest.mark.asyncio
async def test_materializer_preserves_checked_in_skills_until_projection_ready(
    tmp_path: Path,
):
    source_dir = tmp_path / ".agents" / "skills"
    source_skill = source_dir / "repo-skill" / "SKILL.md"
    source_skill.parent.mkdir(parents=True)
    source_skill.write_text("checked-in source input\n", encoding="utf-8")
    materializer = AgentSkillMaterializer(
        str(tmp_path),
        artifact_service=_StaticArtifactService({}),
        source_preservation_root=str(tmp_path / "runtime" / "repo_agents_skills"),
    )
    skillset = ResolvedSkillSet(
        snapshot_id="missing_content_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[_skill("active", "missing-artifact")],
    )

    with pytest.raises(RuntimeError, match="Failed to materialize content"):
        await materializer.materialize(
            resolved_skillset=skillset,
            runtime_id="test_runtime",
            mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        )

    assert source_dir.is_dir()
    assert source_skill.read_text(encoding="utf-8") == "checked-in source input\n"
    assert not (tmp_path / ".agents" / "skills").is_symlink()

@pytest.mark.asyncio
async def test_materializer_refuses_to_clear_symlinked_active_dir(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    backing_link = tmp_path / "skills_active"
    backing_link.symlink_to(outside)
    materializer = AgentSkillMaterializer(str(tmp_path))
    skillset = ResolvedSkillSet(
        snapshot_id="symlink_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[],
    )

    with pytest.raises(RuntimeError, match="refusing to clear symlinked directory"):
        await materializer.materialize(
            resolved_skillset=skillset,
            runtime_id="test_runtime",
            mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"

@pytest.mark.asyncio
async def test_materializer_does_not_block_on_incompatible_gemini_skills_path(
    tmp_path: Path,
):
    gemini_skill = tmp_path / ".gemini" / "skills"
    gemini_skill.mkdir(parents=True)
    (gemini_skill / "SKILL.md").write_text("local gemini skill", encoding="utf-8")
    materializer = AgentSkillMaterializer(str(tmp_path))
    skillset = ResolvedSkillSet(
        snapshot_id="optional_gemini_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[],
    )

    result = await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="codex",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )

    visible_dir = tmp_path / ".agents" / "skills"
    assert visible_dir.is_symlink()
    assert result.workspace_paths == [str(visible_dir)]
    compatibility = result.metadata["compatibilityPaths"]
    assert compatibility["geminiSkillsPath"] == str(gemini_skill)
    assert compatibility["geminiSkillsAvailable"] is False
    assert "existing non-symlink path present" in compatibility["geminiSkillsError"]
    assert (
        gemini_skill / "SKILL.md"
    ).read_text(encoding="utf-8") == "local gemini skill"

@pytest.mark.asyncio
async def test_materializer_hybrid_returns_compact_metadata_without_skill_body(
    tmp_path: Path,
):
    body = b"---\nname: compact_skill\ndescription: test\n---\nFULL BODY CONTENT\n"
    materializer = AgentSkillMaterializer(
        str(tmp_path),
        artifact_service=_StaticArtifactService({"artifact-compact": body}),
    )
    skillset = ResolvedSkillSet(
        snapshot_id="snap_hybrid",
        resolved_at=datetime.now(tz=UTC),
        skills=[_skill("compact_skill", "artifact-compact")],
    )

    result = await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.HYBRID,
    )

    assert result.prompt_index_ref == "index_snap_hybrid"
    serialized = result.model_dump_json()
    assert "compact_skill" in serialized
    assert "FULL BODY CONTENT" not in serialized

@pytest.mark.asyncio
async def test_materializer_prompt_bundle_mode(tmp_path: Path):
    materializer = AgentSkillMaterializer(str(tmp_path))

    skillset = ResolvedSkillSet(
        snapshot_id="snap_prompt",
        resolved_at=datetime.now(tz=UTC),
        skills=[],
    )

    result = await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.PROMPT_BUNDLED,
    )

    assert result.materialization_mode == RuntimeMaterializationMode.PROMPT_BUNDLED
    active_dir = tmp_path / "skills_active"
    assert not active_dir.exists()
    assert result.prompt_index_ref == "index_snap_prompt"

def _skill(name: str, content_ref: str) -> ResolvedSkillEntry:
    return ResolvedSkillEntry(
        skill_name=name,
        version="1.0.0",
        content_ref=content_ref,
        provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.DEPLOYMENT),
    )

def _skill_bundle_payload(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()

class _StaticArtifactService:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads

    async def read(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool,
    ) -> tuple[object, bytes]:
        del principal, allow_restricted_raw
        return object(), self._payloads[artifact_id]
