import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime, UTC

from moonmind.schemas.agent_skill_models import (
    ResolvedSkillSet,
    ResolvedSkillEntry,
    AgentSkillProvenance,
    AgentSkillSourceKind,
    RuntimeMaterializationMode,
)
from moonmind.services.skill_materialization import AgentSkillMaterializer


@pytest.mark.asyncio
async def test_materializer_writes_files_to_skills_active():
    with tempfile.TemporaryDirectory() as tempdir:
        workspace_root = Path(tempdir)
        materializer = AgentSkillMaterializer(str(workspace_root))

        skillset = ResolvedSkillSet(
            snapshot_id="test_snap_123",
            resolved_at=datetime.now(tz=UTC),
            skills=[
                ResolvedSkillEntry(
                    skill_name="my_skill",
                    version="1.0.0",
                    provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.DEPLOYMENT)
                )
            ]
        )

        result = await materializer.materialize(
            resolved_skillset=skillset,
            runtime_id="test_runtime",
            mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED
        )

        assert result.runtime_id == "test_runtime"
        assert result.materialization_mode == RuntimeMaterializationMode.WORKSPACE_MOUNTED
        
        # Ensure it didn't write to .agents/skills but to .agents/skills_active
        active_dir = workspace_root / ".agents" / "skills_active"
        assert active_dir.exists()
        assert not (workspace_root / ".agents" / "skills").exists()
        
        manifest_path = active_dir / "active_manifest.json"
        assert manifest_path.exists()
        
        content = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert content["snapshot_id"] == "test_snap_123"
        assert len(content["skills"]) == 1
        assert content["skills"][0]["name"] == "my_skill"

@pytest.mark.asyncio
async def test_materializer_prompt_bundle_mode():
    with tempfile.TemporaryDirectory() as tempdir:
        workspace_root = Path(tempdir)
        materializer = AgentSkillMaterializer(str(workspace_root))

        skillset = ResolvedSkillSet(
            snapshot_id="snap_prompt",
            resolved_at=datetime.now(tz=UTC),
            skills=[]
        )

        result = await materializer.materialize(
            resolved_skillset=skillset,
            runtime_id="test_runtime",
            mode=RuntimeMaterializationMode.PROMPT_BUNDLED
        )

        assert result.materialization_mode == RuntimeMaterializationMode.PROMPT_BUNDLED
        active_dir = workspace_root / ".agents" / "skills_active"
        assert not active_dir.exists()
        assert result.prompt_index_ref == "index_snap_prompt"
