"""Runtime Skill projection adapter shared with the mature OAuth path."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path
from typing import Any

from moonmind.omnigent.bridge_artifacts import OmnigentArtifactGateway
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_services.workspace import (
    DaemonCommandRunner,
    resolve_daemon_workspace_root,
)
from moonmind.omnigent.settings import OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR
from moonmind.workflows.skills.run_projection import (
    load_resolved_skillset,
    materialize_run_skill_snapshot,
    verify_skill_projection,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    daemon_visible_workspace_path,
)


class _ArtifactAdapter:
    def __init__(self, gateway: OmnigentArtifactGateway) -> None:
        self._gateway = gateway

    async def read(self, *, artifact_id: str, **_kwargs: Any):
        return {}, await self._gateway.read_bytes(artifact_id)


class OmnigentSkillDeliveryService:
    def __init__(
        self,
        *,
        workspace_root: str | Path,
        workspace_volume: str,
        command_runner: DaemonCommandRunner,
        artifact_gateway: OmnigentArtifactGateway,
    ) -> None:
        self._root = Path(workspace_root).resolve()
        self._workspace_volume = workspace_volume
        self._runner = command_runner
        self._artifacts = _ArtifactAdapter(artifact_gateway)

    async def _resolve_authority(
        self,
        resolved_skills: dict[str, Any],
        *,
        owner_ref: str,
    ) -> tuple[Any, Path, Path, dict[str, Any]]:
        """Resolve deterministic projection and cleanup authority without mutation."""

        manifest_ref = str(resolved_skills.get("resolvedSkillSetRef") or "").strip()
        expected_digest = str(
            resolved_skills.get("resolvedSkillSetDigest") or ""
        ).strip()
        if not manifest_ref or not expected_digest:
            raise HarnessPlatformError(
                "resolved Skill authority is missing",
                code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
            )
        resolved = await load_resolved_skillset(self._artifacts, manifest_ref)
        projection_key = hashlib.sha256(
            f"{owner_ref}\0{manifest_ref}\0{expected_digest}".encode("utf-8")
        ).hexdigest()[:24]
        projection_root = (self._root / ".skill-projections" / projection_key).resolve()
        active_snapshot = (
            projection_root / "runtime" / "skills_active" / resolved.snapshot_id
        )
        daemon_root = await resolve_daemon_workspace_root(
            runner=self._runner,
            workspace_volume=self._workspace_volume,
        )
        try:
            daemon_visible = daemon_visible_workspace_path(
                active_snapshot, daemon_root=daemon_root
            )
        except Exception as exc:
            raise HarnessPlatformError(
                "Skill projection cannot be translated to the selected Docker daemon",
                code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
            ) from exc
        attachment = {
            "kind": "bind",
            "sourceRef": str(daemon_visible),
            "targetPath": OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR,
            "accessMode": "read-only",
            "deliveryRef": resolved_skills["skillDeliveryRef"],
            "digest": expected_digest,
            "cleanupRef": f"skill-cleanup:{projection_key}",
            "cleanupSourceRef": str(projection_root),
        }
        return resolved, projection_root, active_snapshot, attachment

    async def anticipated_attachment(
        self,
        resolved_skills: dict[str, Any],
        *,
        owner_ref: str,
    ) -> dict[str, Any]:
        """Return cleanup authority that can be persisted before materialization."""

        _resolved, _root, _snapshot, attachment = await self._resolve_authority(
            resolved_skills, owner_ref=owner_ref
        )
        return attachment

    async def materialize(
        self,
        resolved_skills: dict[str, Any],
        *,
        owner_ref: str,
    ) -> dict[str, Any]:
        resolved, projection_root, active_snapshot, anticipated = (
            await self._resolve_authority(resolved_skills, owner_ref=owner_ref)
        )
        if active_snapshot.exists() or active_snapshot.is_symlink():
            if active_snapshot.is_symlink() or not active_snapshot.is_dir():
                raise HarnessPlatformError(
                    "existing Skill projection is not an owned directory",
                    code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
                )
            metadata = {"visiblePath": str(active_snapshot)}
        else:
            metadata = await materialize_run_skill_snapshot(
                workspace_path=projection_root,
                run_root=projection_root,
                runtime_id="omnigent",
                resolved_skillset=resolved,
                artifact_service=self._artifacts,
                project_adapter_aliases=False,
            )
        await verify_skill_projection(
            materialization_metadata=metadata,
            resolved_skillset=resolved,
        )
        visible = Path(str(metadata["visiblePath"])).resolve()
        daemon_root = await resolve_daemon_workspace_root(
            runner=self._runner, workspace_volume=self._workspace_volume
        )
        try:
            daemon_visible = daemon_visible_workspace_path(
                visible, daemon_root=daemon_root
            )
        except Exception as exc:
            raise HarnessPlatformError(
                "Skill projection cannot be translated to the selected Docker daemon",
                code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
            ) from exc
        if str(daemon_visible) != anticipated["sourceRef"]:
            raise HarnessPlatformError(
                "Skill materialization changed its anticipated attachment authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        return anticipated

    async def cleanup(self, attachment: dict[str, Any]) -> None:
        raw = str(attachment.get("cleanupSourceRef") or "").strip()
        if not raw:
            return
        target = Path(raw).resolve()
        owned_root = (self._root / ".skill-projections").resolve()
        if target == owned_root or not target.is_relative_to(owned_root):
            raise HarnessPlatformError(
                "Skill cleanup target escapes the projection root",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        if target.exists():
            await asyncio.to_thread(shutil.rmtree, target)


__all__ = ["OmnigentSkillDeliveryService"]
