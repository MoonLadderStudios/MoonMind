import io
import json
import hashlib
import shutil
import tarfile
from pathlib import Path
from typing import Any

from moonmind.schemas.agent_skill_models import (
    AgentSkillFormat,
    ResolvedSkillSet,
    RuntimeSkillProjectionDiagnostic,
    RuntimeMaterializationMode,
    RuntimeSkillMaterialization,
)
from moonmind.workflows.skills.workspace_links import (
    SkillWorkspaceError,
    ensure_shared_skill_links,
    is_moonmind_owned_projection,
)

_CANONICAL_ALIAS = ".agents/skills"

class AgentSkillMaterializer:
    """Materializes a ResolvedSkillSet into a run-scoped directory."""

    def __init__(
        self,
        workspace_root: str,
        artifact_service: Any | None = None,
        backing_root: str | None = None,
        source_preservation_root: str | None = None,
    ) -> None:
        if not workspace_root:
            raise ValueError("workspace_root must be provided")
        self.workspace_root = Path(workspace_root).resolve()
        self._artifact_service = artifact_service
        self.backing_root = Path(backing_root).resolve() if backing_root else None
        self.source_preservation_root = (
            Path(source_preservation_root).resolve()
            if source_preservation_root
            else None
        )

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
        
        if mode in (
            RuntimeMaterializationMode.WORKSPACE_MOUNTED,
            RuntimeMaterializationMode.HYBRID,
        ):
            active_dir = self._active_backing_dir(resolved_skillset.snapshot_id)
            alias_dir = self.workspace_root / ".agents" / "skills"
            staging_dir = active_dir.parent / f".{active_dir.name}.staging"
            manifest_path = active_dir / "_manifest.json"
            alias_available = False
            alias_skipped_reason = None

            manifest_content = {
                "backing_path": str(active_dir),
                "materialization_mode": mode.value,
                "resolved_at": resolved_skillset.resolved_at.isoformat(),
                "runtime_id": runtime_id,
                "skills": [
                    self._manifest_skill_entry(entry)
                    for entry in resolved_skillset.skills
                ],
                "snapshot_id": resolved_skillset.snapshot_id,
                "visible_path": str(active_dir),
            }

            try:
                self._preflight_projection(alias_dir, active_dir=active_dir)
                active_dir.parent.mkdir(parents=True, exist_ok=True)
                if active_dir.is_symlink():
                    raise RuntimeError(f"refusing to clear symlinked directory: {active_dir}")
                if staging_dir.exists() or staging_dir.is_symlink():
                    self._remove_directory_path(staging_dir)
                staging_dir.mkdir(parents=True)
            except (OSError, RuntimeError) as ex:
                if "existing symlink does not resolve" in str(ex):
                    raise RuntimeError(
                        self._projection_error_message(alias_dir, cause=str(ex))
                    ) from ex
                raise RuntimeError(f"Failed to prepare skills_active directory: {ex}") from ex

            try:
                for skill in resolved_skillset.skills:
                    if not skill.content_ref:
                        raise RuntimeError(
                            "resolved skill snapshot cannot be materialized: "
                            f"skill '{skill.skill_name}' has no content_ref"
                        )
                    if not self._artifact_service:
                        raise RuntimeError(
                            "resolved skill snapshot cannot be materialized: "
                            "artifact service is required for skill content refs"
                        )
                    _artifact, payload = await self._artifact_service.read(
                        artifact_id=skill.content_ref,
                        principal="system",
                        allow_restricted_raw=True,
                    )
                    self._verify_payload_digest(skill, payload)
                    skill_dir = staging_dir / skill.skill_name
                    skill_dir.mkdir(parents=True, exist_ok=True)
                    if skill.format == AgentSkillFormat.BUNDLE:
                        self._extract_skill_bundle(payload, skill_dir)
                    else:
                        (skill_dir / "SKILL.md").write_bytes(payload)

                (staging_dir / "_manifest.json").write_text(
                    json.dumps(manifest_content, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            except Exception as ex:
                if staging_dir.exists() or staging_dir.is_symlink():
                    self._remove_directory_path(staging_dir)
                raise RuntimeError(
                    f"Failed to materialize content for skill snapshot {resolved_skillset.snapshot_id}: {ex}"
                ) from ex

            try:
                if active_dir.exists() or active_dir.is_symlink():
                    self._remove_directory_path(active_dir)
                staging_dir.replace(active_dir)
            except (OSError, RuntimeError) as ex:
                if staging_dir.exists() or staging_dir.is_symlink():
                    self._remove_directory_path(staging_dir)
                raise RuntimeError(
                    f"Failed to activate prepared skills_active directory: {ex}"
                ) from ex

            try:
                require_agents_link = not self._is_repo_authored_skills_dir(alias_dir)
                links = ensure_shared_skill_links(
                    run_root=self.workspace_root,
                    skills_active_path=active_dir,
                    require_agents_link=require_agents_link,
                    require_gemini_link=False,
                    owned_roots=(active_dir.parent, active_dir),
                )
                alias_available = links.agents_skills_available
                if alias_available:
                    visible_path = links.agents_skills_path
                else:
                    visible_path = active_dir
                    alias_skipped_reason = (
                        "repo_authored_skills_present"
                        if self._is_repo_authored_skills_dir(alias_dir)
                        else links.agents_skills_error or "canonical_alias_unavailable"
                    )
            except (OSError, SkillWorkspaceError) as ex:
                raise RuntimeError(
                    self._projection_error_message(alias_dir, cause=str(ex))
                ) from ex

            manifest_content["visible_path"] = str(visible_path)
            try:
                manifest_path.write_text(
                    json.dumps(manifest_content, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            except OSError as ex:
                raise RuntimeError(
                    f"AgentSkillMaterializer failed to update _manifest.json: {ex}"
                ) from ex

            result.workspace_paths.append(str(visible_path))
            compatibility_paths = {
                "agentsSkillsAvailable": links.agents_skills_available,
                "agentsSkillsPath": str(links.agents_skills_path),
                "agentsSkillsStatus": links.agents_skills_status,
                "geminiSkillsAvailable": links.gemini_skills_available,
                "geminiSkillsPath": str(links.gemini_skills_path),
                "geminiSkillsStatus": links.gemini_skills_status,
            }
            if links.agents_skills_error:
                compatibility_paths["agentsSkillsError"] = links.agents_skills_error
            if links.gemini_skills_error:
                compatibility_paths["geminiSkillsError"] = links.gemini_skills_error
            projection_diagnostics = [
                self._alias_projection_diagnostic(
                    alias_path=links.agents_skills_path,
                    status=links.agents_skills_status,
                    visible_path=visible_path,
                    snapshot_id=resolved_skillset.snapshot_id,
                    reason=alias_skipped_reason or links.agents_skills_error,
                ),
                self._alias_projection_diagnostic(
                    alias_path=links.gemini_skills_path,
                    status=links.gemini_skills_status,
                    visible_path=visible_path,
                    snapshot_id=resolved_skillset.snapshot_id,
                    reason=links.gemini_skills_error,
                ),
            ]
            result.metadata.update(
                {
                    "activationTiming": (
                        "atomic" if alias_available else "next_turn"
                    ),
                    "activeSkills": [
                        skill.skill_name for skill in resolved_skillset.skills
                    ],
                    "backingPath": str(active_dir),
                    "canonicalAliasAvailable": alias_available,
                    "canonicalAliasPath": _CANONICAL_ALIAS,
                    "canonicalAliasSkippedReason": alias_skipped_reason,
                    "compatibilityPaths": compatibility_paths,
                    "manifestPath": str(manifest_path),
                    "materializationVerified": True,
                    "projectionDiagnostics": projection_diagnostics,
                    "repoSkillSourcePreserved": self._repo_skill_source_preserved(alias_dir),
                    "visiblePath": str(visible_path),
                }
            )

        # Ensure compatibility paths or index refs are filled depending on mode.
        if mode in (RuntimeMaterializationMode.PROMPT_BUNDLED, RuntimeMaterializationMode.HYBRID):
            # Prompt Index relies largely on string injection, handled at activity level,
            # but we can set a dummy ref or let the activity assign it later.
            result.prompt_index_ref = f"index_{resolved_skillset.snapshot_id}"
            
        return result

    def _active_backing_dir(self, snapshot_id: str) -> Path:
        if self.backing_root is not None:
            return self.backing_root
        if self.workspace_root.name == "repo":
            return self.workspace_root.parent / "runtime" / "skills_active" / snapshot_id
        return self.workspace_root / "runtime" / "skills_active" / snapshot_id

    @staticmethod
    def _manifest_skill_entry(entry: Any) -> dict[str, Any]:
        payload = {
            "content_digest": entry.content_digest,
            "content_ref": entry.content_ref,
            "name": entry.skill_name,
            "source_kind": entry.provenance.source_kind.value,
            "version": entry.version,
        }
        if entry.required_by:
            payload["required_by"] = entry.required_by
        if entry.selection_reason:
            payload["selection_reason"] = entry.selection_reason
        return payload

    @staticmethod
    def _is_repo_authored_skills_dir(visible_dir: Path) -> bool:
        return visible_dir.exists() and visible_dir.is_dir() and not visible_dir.is_symlink()

    @staticmethod
    def _repo_skill_source_preserved(visible_dir: Path) -> bool:
        if visible_dir.is_symlink():
            return False
        return not visible_dir.exists() or visible_dir.is_dir()

    @staticmethod
    def _verify_payload_digest(entry: Any, payload: bytes) -> None:
        expected = entry.content_digest
        if not expected:
            return
        actual = "sha256:" + hashlib.sha256(payload).hexdigest()
        if actual != expected:
            raise RuntimeError(
                f"checksum mismatch for skill {entry.skill_name}: expected {expected}, got {actual}"
            )

    @staticmethod
    def _preflight_projection(alias_dir: Path, *, active_dir: Path) -> None:
        if not alias_dir.exists() and not alias_dir.is_symlink():
            return
        if alias_dir.is_symlink() and is_moonmind_owned_projection(
            alias_dir,
            target=active_dir,
            owned_roots=(active_dir.parent, active_dir),
        ):
            return
        if alias_dir.is_dir() and not alias_dir.is_symlink():
            return
        raise RuntimeError(
            "existing symlink does not resolve under a MoonMind-owned active skill root"
        )

    def _alias_projection_diagnostic(
        self,
        *,
        alias_path: Path,
        status: str,
        visible_path: Path,
        snapshot_id: str,
        reason: str | None,
    ) -> dict[str, Any]:
        diagnostic = RuntimeSkillProjectionDiagnostic(
            active_visible_path=str(visible_path),
            alias_path=str(alias_path),
            event=f"skill_projection_alias_{status}",
            reason=reason,
            snapshot_id=snapshot_id,
            status=status,
            workspace=str(self.workspace_root),
        )
        return diagnostic.model_dump(mode="json", by_alias=True)

    @staticmethod
    def _clear_directory(path: Path) -> None:
        if path.is_symlink():
            raise RuntimeError(f"refusing to clear symlinked directory: {path}")
        if not path.is_dir():
            raise RuntimeError(f"refusing to clear non-directory path: {path}")
        for child in path.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()

    @staticmethod
    def _remove_directory_path(path: Path) -> None:
        if path.is_symlink():
            raise RuntimeError(f"refusing to remove symlinked directory: {path}")
        if path.is_dir():
            shutil.rmtree(path)
            return
        path.unlink()

    @staticmethod
    def _extract_skill_bundle(payload: bytes, skill_dir: Path) -> None:
        root = skill_dir.resolve()
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            for member in archive.getmembers():
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise RuntimeError(
                        f"skill bundle contains unsafe path: {member.name}"
                    )
                target = (root / member_path).resolve()
                if target != root and root not in target.parents:
                    raise RuntimeError(
                        f"skill bundle extracts outside skill directory: {member.name}"
                    )
                if not member.isfile() and not member.isdir():
                    raise RuntimeError(
                        f"skill bundle contains unsupported entry: {member.name}"
                    )
            archive.extractall(root, filter="data")
        if not (skill_dir / "SKILL.md").is_file():
            raise RuntimeError("skill bundle did not contain SKILL.md")

    def _validate_projection_path(
        self,
        visible_dir: Path,
        *,
        allow_existing_visible_dir: bool = False,
    ) -> None:
        agents_dir = visible_dir.parent
        if agents_dir.exists() and not agents_dir.is_dir():
            raise RuntimeError(self._projection_error_message(agents_dir))
        if (
            visible_dir.exists()
            and not visible_dir.is_symlink()
            and not (allow_existing_visible_dir and visible_dir.is_dir())
        ):
            raise RuntimeError(self._projection_error_message(visible_dir))

    @staticmethod
    def _path_kind(path: Path) -> str:
        if path.is_symlink():
            return "symlink"
        if path.is_dir():
            return "directory"
        if path.is_file():
            return "file"
        if path.exists():
            return "special"
        return "missing"

    def _projection_error_message(self, path: Path, *, cause: str | None = None) -> str:
        message = (
            "skill projection failed before runtime launch: "
            f"path: {path}; "
            f"object kind: {self._path_kind(path)}; "
            "attempted action: project active skill snapshot; "
            "remediation: remove or relocate the existing path so MoonMind can "
            "create the canonical .agents/skills projection to the run-scoped "
            "active skill backing store"
        )
        if cause:
            message += f"; cause: {cause}"
        return message
