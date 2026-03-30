import json
import logging
from pathlib import Path
from typing import Any

from moonmind.schemas.agent_skill_models import (
    ResolvedSkillSet,
    RuntimeMaterializationMode,
    RuntimeSkillMaterialization,
)

logger = logging.getLogger(__name__)


class AgentSkillMaterializer:
    """Materializes a ResolvedSkillSet into a run-scoped directory."""

    def __init__(self, workspace_root: str, artifact_service: Any | None = None) -> None:
        if not workspace_root:
            raise ValueError("workspace_root must be provided")
        self.workspace_root = Path(workspace_root).resolve()
        self._artifact_service = artifact_service

    async def materialize(
        self,
        resolved_skillset: ResolvedSkillSet,
        runtime_id: str,
        mode: RuntimeMaterializationMode,
    ) -> RuntimeSkillMaterialization:
        """Render the snapshot to disk as required by the runtime mode."""
        
        result = RuntimeSkillMaterialization(
            runtime_id=runtime_id,
            materialization_mode=mode,
        )
        
        if mode in (RuntimeMaterializationMode.WORKSPACE_MOUNTED, RuntimeMaterializationMode.HYBRID):
            active_dir = self.workspace_root / ".agents" / "skills_active"
            
            try:
                active_dir.mkdir(parents=True, exist_ok=True)
            except OSError as ex:
                raise RuntimeError(f"Failed to create skills_active directory: {ex}") from ex

            manifest_path = active_dir / "active_manifest.json"
            
            manifest_content = {
                "snapshot_id": resolved_skillset.snapshot_id,
                "resolved_at": resolved_skillset.resolved_at.isoformat(),
                "skills": [
                    {
                        "name": entry.skill_name,
                        "version": entry.version,
                        "source_kind": entry.provenance.source_kind.value,
                    }
                    for entry in resolved_skillset.skills
                ]
            }
            
            try:
                manifest_path.write_text(json.dumps(manifest_content, indent=2), encoding="utf-8")
                result.workspace_paths.append(str(active_dir))
            except OSError as ex:
                raise RuntimeError(f"AgentSkillMaterializer failed to write active_manifest.json: {ex}") from ex
            
            for skill in resolved_skillset.skills:
                if skill.content_ref and self._artifact_service:
                    try:
                        _artifact, payload = await self._artifact_service.read(
                            artifact_id=skill.content_ref,
                            principal="system",
                            allow_restricted_raw=True,
                        )
                        skill_dir = active_dir / skill.skill_name
                        skill_dir.mkdir(parents=True, exist_ok=True)
                        (skill_dir / "SKILL.md").write_bytes(payload)
                    except Exception as ex:
                        logger.warning("Failed to materialize content for skill %s: %s", skill.skill_name, ex)

        # Ensure compatibility paths or index refs are filled depending on mode.
        if mode in (RuntimeMaterializationMode.PROMPT_BUNDLED, RuntimeMaterializationMode.HYBRID):
            # Prompt Index relies largely on string injection, handled at activity level,
            # but we can set a dummy ref or let the activity assign it later.
            result.prompt_index_ref = f"index_{resolved_skillset.snapshot_id}"
            
        return result
