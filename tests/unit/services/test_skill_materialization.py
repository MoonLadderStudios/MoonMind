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
        "_shared",
        "alpha",
        "beta",
    ]
    assert (visible_dir / "_shared" / "publish_evidence.py").is_file()
    assert not (visible_dir / "unselected_skill").exists()


@pytest.mark.asyncio
async def test_materializer_projects_shared_helper_when_repo_skills_are_preserved(
    tmp_path: Path,
):
    workspace = tmp_path / "repo"
    repo_skill = workspace / ".agents" / "skills" / "repo_skill"
    repo_skill.mkdir(parents=True)
    (repo_skill / "SKILL.md").write_text(
        "---\nname: repo_skill\ndescription: repo\n---\n",
        encoding="utf-8",
    )
    artifact_service = _StaticArtifactService(
        {"artifact-alpha": b"---\nname: alpha\ndescription: test\n---\n"}
    )
    materializer = AgentSkillMaterializer(
        str(workspace),
        artifact_service=artifact_service,
    )
    skillset = ResolvedSkillSet(
        snapshot_id="repo_preserved_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[_skill("alpha", "artifact-alpha")],
    )

    result = await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )

    alias_dir = workspace / ".agents" / "skills"
    active_dir = tmp_path / "runtime" / "skills_active" / "repo_preserved_snap"
    assert result.metadata["canonicalAliasAvailable"] is False
    assert result.metadata["canonicalAliasSkippedReason"] == "repo_authored_skills_present"
    assert result.metadata["visiblePath"] == str(active_dir)
    assert (alias_dir / "repo_skill" / "SKILL.md").is_file()
    assert (alias_dir / "_shared" / "publish_evidence.py").is_file()
    assert (active_dir / "alpha" / "SKILL.md").is_file()


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
            "event": "skill_projection_alias_skipped",
            "reason": None,
            "snapshotId": "active_snap",
            "status": "skipped",
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
    assert compatibility["geminiSkillsStatus"] == "skipped"
    assert "geminiSkillsError" not in compatibility
    assert (
        gemini_skill / "SKILL.md"
    ).read_text(encoding="utf-8") == "local gemini skill"


@pytest.mark.asyncio
async def test_materializer_creates_gemini_projection_only_for_gemini_runtime(
    tmp_path: Path,
):
    materializer = AgentSkillMaterializer(str(tmp_path))
    skillset = ResolvedSkillSet(
        snapshot_id="gemini_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[],
    )

    codex_result = await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="codex_cli",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )

    assert not (tmp_path / ".gemini").exists()
    assert codex_result.metadata["compatibilityPaths"]["geminiSkillsStatus"] == "skipped"

    gemini_result = await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="gemini",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )

    gemini_skills = tmp_path / ".gemini" / "skills"
    assert gemini_skills.is_symlink()
    assert gemini_skills.resolve() == (
        tmp_path / "runtime" / "skills_active" / "gemini_snap"
    ).resolve()
    assert gemini_result.metadata["compatibilityPaths"]["geminiSkillsAvailable"] is True

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
async def test_materializer_can_skip_adapter_alias_projection(tmp_path: Path):
    payload = b"---\nname: verifier\ndescription: test\n---\n"
    materializer = AgentSkillMaterializer(
        str(tmp_path),
        artifact_service=_StaticArtifactService({"artifact-verifier": payload}),
        project_adapter_aliases=False,
    )
    skillset = ResolvedSkillSet(
        snapshot_id="snap_projectionless",
        resolved_at=datetime.now(tz=UTC),
        skills=[_skill("verifier", "artifact-verifier")],
    )

    result = await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.HYBRID,
    )

    backing_dir = tmp_path / "runtime" / "skills_active" / "snap_projectionless"
    assert not (tmp_path / ".agents" / "skills").exists()
    assert not (tmp_path / ".gemini" / "skills").exists()
    assert result.metadata["visiblePath"] == str(backing_dir)
    assert result.metadata["canonicalAliasAvailable"] is False
    assert (
        result.metadata["canonicalAliasSkippedReason"]
        == "adapter_alias_projection_disabled"
    )
    manifest = json.loads((backing_dir / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["visible_path"] == str(backing_dir)
    assert (backing_dir / "verifier" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_materializer_accepts_valid_conflict_free_alias_to_selected_snapshot(
    tmp_path: Path,
):
    """A legitimate alias to the selected immutable snapshot passes (issue #4275).

    The pre-existing MoonMind-owned alias to the selected snapshot's backing
    store must be reused (not treated as contamination), the selected helper
    must execute from the alias, and unrelated repo-authored sources must be
    left unchanged.
    """
    repo_source = tmp_path / "repo_source" / "fix-comments" / "SKILL.md"
    repo_source.parent.mkdir(parents=True)
    repo_source.write_text("repo-authored source\n", encoding="utf-8")
    payload = b"---\nname: fix-comments\ndescription: test\n---\n"
    active_dir = tmp_path / "runtime" / "skills_active" / "selected_snap"
    active_dir.mkdir(parents=True)
    (active_dir / "_manifest.json").write_text('{"snapshot_id": "old"}\n', encoding="utf-8")
    alias = tmp_path / ".agents" / "skills"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(active_dir)
    materializer = AgentSkillMaterializer(
        str(tmp_path),
        artifact_service=_StaticArtifactService({"artifact-fix": payload}),
    )
    skillset = ResolvedSkillSet(
        snapshot_id="selected_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="fix-comments",
                content_ref="artifact-fix",
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

    assert alias.is_symlink()
    assert alias.resolve() == active_dir.resolve()
    assert (alias / "fix-comments" / "SKILL.md").read_bytes() == payload
    assert repo_source.read_text(encoding="utf-8") == "repo-authored source\n"
    assert result.metadata["canonicalAliasAvailable"] is True
    assert result.metadata["visiblePath"] == str(alias)


@pytest.mark.asyncio
async def test_materializer_rejects_dangling_alias_with_missing_asset_diagnostic(
    tmp_path: Path,
):
    """A dangling alias (missing snapshot target) fails with a precise diagnostic."""
    alias = tmp_path / ".agents" / "skills"
    alias.parent.mkdir(parents=True)
    missing_target = tmp_path / "elsewhere" / "gone_snap"
    alias.symlink_to(missing_target)
    assert not missing_target.exists()
    materializer = AgentSkillMaterializer(str(tmp_path))
    skillset = ResolvedSkillSet(
        snapshot_id="selected_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[],
    )

    with pytest.raises(RuntimeError) as exc_info:
        await materializer.materialize(
            resolved_skillset=skillset,
            runtime_id="test_runtime",
            mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        )

    message = str(exc_info.value).lower()
    assert "missing" in message
    assert str(alias) in str(exc_info.value)
    assert "object kind: symlink" in str(exc_info.value)
    # The dangling link is left for the workspace/materialization owner to repair.
    assert alias.is_symlink()


@pytest.mark.asyncio
async def test_materializer_unknown_alias_diagnostic_names_owner_and_preserves_work(
    tmp_path: Path,
):
    """Conflicting aliases fail with an owner-specific diagnostic (issue #4275).

    The diagnostic must direct repair through the workspace/materialization
    owner and must not advise deleting repo-authored sources or discarding
    cumulative work.
    """
    external_target = tmp_path / "external-skills"
    external_target.mkdir()
    alias = tmp_path / ".agents" / "skills"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(external_target)
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
    assert "existing symlink does not resolve under a MoonMind-owned active skill root" in message
    assert "workspace" in message.lower() or "materialization owner" in message.lower()
    assert "repo-authored" in message.lower() or "preserve" in message.lower()
    assert "clean reclone" not in message.lower()
    assert alias.is_symlink()
    assert alias.resolve() == external_target.resolve()


def test_nested_asset_change_updates_bundle_identity() -> None:
    """A nested helper change alters bundle identity (issue #4275 R3/A4)."""
    before = _skill_bundle_payload(
        {
            "SKILL.md": b"# Skill\n",
            "bin/run.py": b"print('before')\n",
        }
    )
    after = _skill_bundle_payload(
        {
            "SKILL.md": b"# Skill\n",
            "bin/run.py": b"print('after')\n",
        }
    )
    assert _digest(before) != _digest(after)


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


@pytest.mark.asyncio
async def test_real_bundle_builder_nested_change_alters_digest_and_materializes(
    tmp_path: Path,
):
    """Nested refs/schemas/templates/shared files are inside bundle identity (issue #4275 R3/A3/A4).

    Uses the real snapshot packaging builder
    (AgentSkillsActivities._build_skill_bundle_payload) rather than a
    test-local tar helper: every declared nested asset must be present in the
    payload, a nested-asset change must alter the bundle digest, and each
    nested asset must be readable from the actual materialized snapshot.
    """
    from moonmind.workflows.agent_skills.agent_skills_activities import (
        AgentSkillsActivities,
    )

    def _make_skill_dir(root: Path, *, helper_body: bytes) -> Path:
        skill_dir = root / "moonspec-verify"
        (skill_dir / "references").mkdir(parents=True)
        (skill_dir / "schemas").mkdir(parents=True)
        (skill_dir / "templates").mkdir(parents=True)
        (skill_dir / "_shared").mkdir(parents=True)
        (skill_dir / "bin").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_bytes(b"---\nname: moonspec-verify\n---\n")
        (skill_dir / "references" / "acceptance-policy.md").write_bytes(b"# policy\n")
        (skill_dir / "schemas" / "contract.json").write_bytes(b'{"type": "object"}\n')
        (skill_dir / "templates" / "report.md").write_bytes(b"# report\n")
        (skill_dir / "_shared" / "util.py").write_bytes(b"# shared\n")
        (skill_dir / "bin" / "run.py").write_bytes(helper_body)
        return skill_dir

    before_dir = tmp_path / "before_src"
    after_dir = tmp_path / "after_src"
    before_skill = _make_skill_dir(before_dir, helper_body=b"print('before')\n")
    after_skill = _make_skill_dir(after_dir, helper_body=b"print('after')\n")

    before_payload = AgentSkillsActivities._build_skill_bundle_payload(before_skill)
    after_payload = AgentSkillsActivities._build_skill_bundle_payload(after_skill)
    assert _digest(before_payload) != _digest(after_payload)

    materializer = AgentSkillMaterializer(
        str(tmp_path / "ws"),
        artifact_service=_StaticArtifactService({"artifact-verify": before_payload}),
    )
    skillset = ResolvedSkillSet(
        snapshot_id="nested_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="moonspec-verify",
                format=AgentSkillFormat.BUNDLE,
                content_ref="artifact-verify",
                content_digest=_digest(before_payload),
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.DEPLOYMENT
                ),
            )
        ],
    )
    await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )
    visible = tmp_path / "ws" / ".agents" / "skills" / "moonspec-verify"
    assert (visible / "SKILL.md").is_file()
    assert (visible / "references" / "acceptance-policy.md").read_text(
        encoding="utf-8"
    ) == "# policy\n"
    assert (visible / "schemas" / "contract.json").is_file()
    assert (visible / "templates" / "report.md").is_file()
    assert (visible / "_shared" / "util.py").is_file()
    assert (visible / "bin" / "run.py").read_bytes() == b"print('before')\n"


@pytest.mark.asyncio
async def test_admitted_run_immutability_rejects_tampered_payload(tmp_path: Path):
    """An admitted run keeps its original immutable assets (issue #4275 A4).

    A tampered payload under the same content_ref must fail digest
    verification before any projection switch, and the admitted manifest must
    retain the original digest (no silent re-selection of current source).
    """
    payload = _skill_bundle_payload({"SKILL.md": b"# original\n"})
    tampered = _skill_bundle_payload({"SKILL.md": b"# tampered\n"})
    assert _digest(payload) != _digest(tampered)
    materializer = AgentSkillMaterializer(
        str(tmp_path), artifact_service=_StaticArtifactService({"artifact-x": tampered})
    )
    skillset = ResolvedSkillSet(
        snapshot_id="admitted_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="guarded",
                content_ref="artifact-x",
                content_digest=_digest(payload),
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
    # No projection was switched and no manifest was admitted for the tampered bytes.
    assert not (tmp_path / ".agents" / "skills").exists()


@pytest.mark.asyncio
async def test_resolved_selected_bundle_executes_despite_same_name_builtin_source(
    tmp_path: Path,
):
    """The resolved bundle executes even with a same-name built-in present (issue #4275 R8/A8).

    A stale built-in source file must not substitute the selected payload:
    the materialized helper readable through the alias is the selected
    snapshot's bytes and the repo/built-in source is left unchanged.
    """
    builtin_source = tmp_path / "builtin_source" / "fix-comments" / "SKILL.md"
    builtin_source.parent.mkdir(parents=True)
    builtin_source.write_text("stale built-in implementation\n", encoding="utf-8")
    selected_payload = b"---\nname: fix-comments\ndescription: selected\n---\n"
    assert b"stale" not in selected_payload
    materializer = AgentSkillMaterializer(
        str(tmp_path / "ws"),
        artifact_service=_StaticArtifactService({"artifact-selected": selected_payload}),
    )
    skillset = ResolvedSkillSet(
        snapshot_id="selected_snap_r8",
        resolved_at=datetime.now(tz=UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="fix-comments",
                content_ref="artifact-selected",
                content_digest=_digest(selected_payload),
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.DEPLOYMENT
                ),
            )
        ],
    )
    await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )
    alias = tmp_path / "ws" / ".agents" / "skills"
    assert (alias / "fix-comments" / "SKILL.md").read_bytes() == selected_payload
    assert (
        builtin_source.read_text(encoding="utf-8") == "stale built-in implementation\n"
    )


@pytest.mark.asyncio
async def test_historical_snapshot_reuse_preserves_admitted_digest(tmp_path: Path):
    """Re-materializing an admitted snapshot keeps its digest (issue #4275 A9/R2-rest).

    A later source change produces a new digest (new snapshot identity), but
    the admitted manifest for the original snapshot_id still records the
    original digest: history is not silently re-pointed at current source.
    """
    original = _skill_bundle_payload({"SKILL.md": b"# v1\n"})
    changed = _skill_bundle_payload({"SKILL.md": b"# v2\n"})
    assert _digest(original) != _digest(changed)
    payloads = {"artifact-v1": original}
    materializer = AgentSkillMaterializer(
        str(tmp_path), artifact_service=_StaticArtifactService(payloads)
    )
    skillset = ResolvedSkillSet(
        snapshot_id="reuse_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[_skill("alpha", "artifact-v1")],
    )
    # Give the v1 entry its real digest for the admitted manifest.
    skillset.skills[0] = skillset.skills[0].model_copy(
        update={"content_digest": _digest(original)}
    )
    await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )
    manifest = json.loads(
        (tmp_path / "runtime" / "skills_active" / "reuse_snap" / "_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["skills"][0]["content_digest"] == _digest(original)
    assert manifest["skills"][0]["content_digest"] != _digest(changed)

    # Re-materializing the admitted skillset after the source changed must
    # not silently re-point history: the digest guard rejects the changed
    # bytes and the admitted manifest keeps the original digest.
    payloads["artifact-v1"] = changed
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        await materializer.materialize(
            resolved_skillset=skillset,
            runtime_id="test_runtime",
            mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        )
    reread = json.loads(
        (tmp_path / "runtime" / "skills_active" / "reuse_snap" / "_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert reread["skills"][0]["content_digest"] == _digest(original)


@pytest.mark.asyncio
async def test_restart_rematerialization_preserves_admitted_snapshot(tmp_path: Path):
    """Restart re-materialization is deterministic (issue #4275 A9-rest).

    Materialize snapshot S, drop in-memory state (a new materializer instance
    over the same workspace), re-materialize S, and assert the admitted
    manifest digest is identical, the helper bytes readable through the alias
    are unchanged, and a changed source is a new identity rather than a
    silent re-selection of the admitted run.
    """
    payload = _skill_bundle_payload(
        {"SKILL.md": b"# restart\n", "bin/helper.py": b"# original helper\n"}
    )
    changed = _skill_bundle_payload(
        {"SKILL.md": b"# restart\n", "bin/helper.py": b"# changed helper\n"}
    )
    assert _digest(payload) != _digest(changed)
    artifact_service = _StaticArtifactService({"artifact-restart": payload})
    skillset = ResolvedSkillSet(
        snapshot_id="restart_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="restartable",
                format=AgentSkillFormat.BUNDLE,
                content_ref="artifact-restart",
                content_digest=_digest(payload),
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.DEPLOYMENT
                ),
            )
        ],
    )

    first = AgentSkillMaterializer(str(tmp_path), artifact_service=artifact_service)
    await first.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )
    alias = tmp_path / ".agents" / "skills"
    manifest_path = tmp_path / "runtime" / "skills_active" / "restart_snap" / "_manifest.json"
    first_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_helper = (alias / "restartable" / "bin" / "helper.py").read_bytes()
    assert first_helper == b"# original helper\n"

    # Drop in-memory state: a new instance over the same workspace re-admits S.
    second = AgentSkillMaterializer(str(tmp_path), artifact_service=artifact_service)
    await second.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )
    second_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    second_helper = (alias / "restartable" / "bin" / "helper.py").read_bytes()

    assert alias.is_symlink()
    assert second_manifest["skills"][0]["content_digest"] == first_manifest["skills"][0]["content_digest"]
    assert second_manifest["skills"][0]["content_digest"] == _digest(payload)
    assert second_helper == first_helper == b"# original helper\n"
    # A changed source is a new bundle identity; the admitted run was not
    # silently re-pointed at current source.
    assert second_manifest["skills"][0]["content_digest"] != _digest(changed)
    assert second_helper != b"# changed helper\n"


@pytest.mark.asyncio
async def test_materializer_projects_declared_sibling_closure_from_snapshot(
    tmp_path: Path,
):
    """Declared sibling dependencies are readable from the packaged snapshot (issue #4275 A3/R4).

    An orchestrator closure (selected + required sibling) materializes every
    member from its own bundle artifact; no sibling is silently dropped.
    """
    orchestrator_payload = _skill_bundle_payload({"SKILL.md": b"# orchestrator\n"})
    sibling_payload = _skill_bundle_payload(
        {"SKILL.md": b"# sibling\n", "bin/helper.py": b"# helper\n"}
    )
    materializer = AgentSkillMaterializer(
        str(tmp_path),
        artifact_service=_StaticArtifactService(
            {
                "artifact-orchestrator": orchestrator_payload,
                "artifact-sibling": sibling_payload,
            }
        ),
    )
    skillset = ResolvedSkillSet(
        snapshot_id="sibling_snap",
        resolved_at=datetime.now(tz=UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="orchestrator",
                format=AgentSkillFormat.BUNDLE,
                content_ref="artifact-orchestrator",
                content_digest=_digest(orchestrator_payload),
                selection_reason="selected",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.DEPLOYMENT
                ),
            ),
            ResolvedSkillEntry(
                skill_name="sibling",
                format=AgentSkillFormat.BUNDLE,
                content_ref="artifact-sibling",
                content_digest=_digest(sibling_payload),
                selection_reason="required",
                required_by=["orchestrator"],
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.DEPLOYMENT
                ),
            ),
        ],
    )
    await materializer.materialize(
        resolved_skillset=skillset,
        runtime_id="test_runtime",
        mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
    )
    visible = tmp_path / ".agents" / "skills"
    assert (visible / "orchestrator" / "SKILL.md").is_file()
    assert (visible / "sibling" / "SKILL.md").is_file()
    assert (visible / "sibling" / "bin" / "helper.py").is_file()
