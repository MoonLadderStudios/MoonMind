import json
import io
import hashlib
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
    payload = b"---\nname: my_skill\ndescription: test\n---\n"
    artifact_service = _StaticArtifactService({"artifact-my-skill": payload})
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
                content_digest=_digest(payload),
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
    backing_dir = tmp_path / "runtime" / "skills_active" / "test_snap_123"
    manifest_path = backing_dir / "_manifest.json"

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
                "content_digest": _digest(payload),
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
    assert result.metadata["canonicalAliasAvailable"] is True
    assert result.metadata["canonicalAliasPath"] == ".agents/skills"
    assert result.metadata["canonicalAliasSkippedReason"] is None
    assert result.metadata["manifestPath"] == str(manifest_path)
    assert result.metadata["activeSkills"] == ["my_skill"]
    assert result.metadata["materializationVerified"] is True
    assert result.metadata["activationTiming"] == "atomic"

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
async def test_materializer_preserves_existing_agents_skills_directory_on_success(
    tmp_path: Path,
):
    source_dir = tmp_path / ".agents" / "skills"
    source_skill = source_dir / "repo-skill" / "SKILL.md"
    source_skill.parent.mkdir(parents=True)
    source_skill.write_text("do not rewrite", encoding="utf-8")
    artifact_service = _StaticArtifactService(
        {"artifact-active": b"---\nname: active\ndescription: active\n---\n"}
    )
    materializer = AgentSkillMaterializer(
        str(tmp_path),
        artifact_service=artifact_service,
        source_preservation_root=str(tmp_path / "runtime" / "repo_agents_skills"),
    )
    skillset = ResolvedSkillSet(
        snapshot_id="active_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[_skill("active", "artifact-active")],
    )

    result = await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )

    backing_dir = tmp_path / "runtime" / "skills_active" / "active_snap"
    assert source_dir.is_dir()
    assert not source_dir.is_symlink()
    assert source_skill.read_text(encoding="utf-8") == "do not rewrite"
    assert not (tmp_path / "runtime" / "repo_agents_skills").exists()
    assert result.metadata["visiblePath"] == str(backing_dir)
    assert result.metadata["canonicalAliasAvailable"] is False
    assert (
        result.metadata["canonicalAliasSkippedReason"]
        == "repo_authored_skills_present"
    )
    assert result.metadata["repoSkillSourcePreserved"] is True
    assert (backing_dir / "active" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_materializer_reports_structured_alias_projection_diagnostics(
    tmp_path: Path,
):
    source_dir = tmp_path / ".agents" / "skills"
    source_skill = source_dir / "repo-skill" / "SKILL.md"
    source_skill.parent.mkdir(parents=True)
    source_skill.write_text("repo authored\n", encoding="utf-8")
    artifact_service = _StaticArtifactService(
        {"artifact-active": b"---\nname: active\ndescription: active\n---\n"}
    )
    materializer = AgentSkillMaterializer(
        str(tmp_path),
        artifact_service=artifact_service,
    )
    skillset = ResolvedSkillSet(
        snapshot_id="active_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[_skill("active", "artifact-active")],
    )

    result = await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )

    diagnostics = result.metadata["projectionDiagnostics"]
    assert diagnostics == [
        {
            "activeVisiblePath": str(
                tmp_path / "runtime" / "skills_active" / "active_snap"
            ),
            "aliasPath": str(source_dir),
            "event": "skill_projection_alias_skipped",
            "reason": "repo_authored_skills_present",
            "snapshotId": "active_snap",
            "status": "skipped",
            "workspace": str(tmp_path),
        },
        {
            "activeVisiblePath": str(
                tmp_path / "runtime" / "skills_active" / "active_snap"
            ),
            "aliasPath": str(tmp_path / ".gemini" / "skills"),
            "event": "skill_projection_alias_created",
            "reason": None,
            "snapshotId": "active_snap",
            "status": "created",
            "workspace": str(tmp_path),
        },
    ]


def test_materializer_does_not_expose_preserve_and_link_helper_surface() -> None:
    assert not hasattr(AgentSkillMaterializer, "_move_visible_source_to_preservation_root")
    assert not hasattr(AgentSkillMaterializer, "_restore_preserved_visible_source")
    assert not hasattr(AgentSkillMaterializer, "_should_preserve_visible_source_dir")


@pytest.mark.asyncio
async def test_materializer_rejects_checksum_mismatch_before_projection_switch(
    tmp_path: Path,
):
    active_dir = tmp_path / "runtime" / "skills_active" / "active_snap"
    old_skill = active_dir / "old-skill" / "SKILL.md"
    old_skill.parent.mkdir(parents=True)
    old_skill.write_text("old active skill\n", encoding="utf-8")
    alias = tmp_path / ".agents" / "skills"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(active_dir)
    payload = b"---\nname: active\ndescription: new\n---\n"
    materializer = AgentSkillMaterializer(
        str(tmp_path),
        artifact_service=_StaticArtifactService({"artifact-active": payload}),
    )
    skillset = ResolvedSkillSet(
        snapshot_id="active_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="active",
                version="1.0.0",
                content_ref="artifact-active",
                content_digest="sha256:does-not-match",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.DEPLOYMENT
                ),
            )
        ],
    )

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        await materializer.materialize(
            resolved_skillset=skillset,
            runtime_id="test_runtime",
            mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        )

    assert old_skill.read_text(encoding="utf-8") == "old active skill\n"
    assert alias.is_symlink()
    assert alias.resolve() == active_dir.resolve()
    assert not (active_dir / "active").exists()


@pytest.mark.asyncio
async def test_materializer_preserves_previous_projection_on_bundle_failure(
    tmp_path: Path,
):
    active_dir = tmp_path / "runtime" / "skills_active" / "active_snap"
    old_skill = active_dir / "old-skill" / "SKILL.md"
    old_skill.parent.mkdir(parents=True)
    old_skill.write_text("old active skill\n", encoding="utf-8")
    alias = tmp_path / ".agents" / "skills"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(active_dir)
    payload = _skill_bundle_payload({"../evil": b"nope"})
    materializer = AgentSkillMaterializer(
        str(tmp_path),
        artifact_service=_StaticArtifactService({"artifact-bundle": payload}),
    )
    skillset = ResolvedSkillSet(
        snapshot_id="active_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="active",
                version="1.0.0",
                format=AgentSkillFormat.BUNDLE,
                content_ref="artifact-bundle",
                content_digest=_digest(payload),
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.DEPLOYMENT
                ),
            )
        ],
    )

    with pytest.raises(RuntimeError, match="unsafe path"):
        await materializer.materialize(
            resolved_skillset=skillset,
            runtime_id="test_runtime",
            mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        )

    assert old_skill.read_text(encoding="utf-8") == "old active skill\n"
    assert alias.is_symlink()
    assert alias.resolve() == active_dir.resolve()


@pytest.mark.asyncio
async def test_materializer_refuses_unknown_agents_skills_symlink(tmp_path: Path):
    source_dir = tmp_path / ".agents" / "skills"
    external_target = tmp_path / "external-skills"
    external_target.mkdir()
    source_dir.parent.mkdir(parents=True)
    source_dir.symlink_to(external_target)
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
    assert "object kind: symlink" in message
    assert "attempted action: project active skill snapshot" in message
    assert "existing symlink does not resolve under a MoonMind-owned active skill root" in message

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
    backing_link = tmp_path / "runtime" / "skills_active" / "symlink_snap"
    backing_link.parent.mkdir(parents=True)
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
    active_dir = tmp_path / "runtime" / "skills_active" / "snap_prompt"
    assert not active_dir.exists()
    assert result.prompt_index_ref == "index_snap_prompt"

def _skill(name: str, content_ref: str) -> ResolvedSkillEntry:
    return ResolvedSkillEntry(
        skill_name=name,
        version="1.0.0",
        content_ref=content_ref,
        provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.DEPLOYMENT),
    )

def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()

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
