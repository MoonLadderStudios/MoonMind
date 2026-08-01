"""Durable artifact boundary for Lore delta checkpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator

from .lore_adapter import LorePreparedWorkspace, LoreRepositoryProviderAdapter

LORE_CHECKPOINT_CONTENT_TYPE = (
    "application/vnd.moonmind.repository-checkpoint+json;version=1"
)
_PRINCIPAL = "service:lore_checkpoint"


class LoreDurableCheckpointService:
    def __init__(
        self, *, adapter: LoreRepositoryProviderAdapter, artifact_service: Any
    ) -> None:
        self._adapter = adapter
        self._artifacts = artifact_service

    async def capture(self, prepared: LorePreparedWorkspace) -> str:
        # capture_checkpoint performs the mandatory external scan before status.
        payload = self._adapter.encode_checkpoint(
            self._adapter.capture_checkpoint(prepared)
        )
        artifact, _ = await self._artifacts.create(
            principal=_PRINCIPAL,
            content_type=LORE_CHECKPOINT_CONTENT_TYPE,
            metadata_json={
                "artifact_kind": "repository_checkpoint",
                "provider": "lore",
            },
        )
        completed = await self._artifacts.write_complete(
            artifact_id=artifact.artifact_id,
            principal=_PRINCIPAL,
            payload=payload,
            content_type=LORE_CHECKPOINT_CONTENT_TYPE,
        )
        return completed.artifact_id

    async def restore(
        self,
        checkpoint_ref: str,
        *,
        repository: str,
        branch: str,
        locator: SandboxWorkspaceLocator,
        authority_path: Path,
        connection_ref: str,
        client_evidence: Mapping[str, str],
    ) -> LorePreparedWorkspace:
        artifact, payload = await self._artifacts.read(
            artifact_id=checkpoint_ref,
            principal=_PRINCIPAL,
            allow_restricted_raw=True,
        )
        if str(artifact.content_type) != LORE_CHECKPOINT_CONTENT_TYPE:
            raise ValueError(
                "Lore checkpoint artifact has an incompatible content type"
            )
        checkpoint = self._adapter.decode_checkpoint(payload)
        return self._adapter.restore_checkpoint(
            checkpoint,
            repository=repository,
            branch=branch,
            locator=locator,
            authority_path=authority_path,
            connection_ref=connection_ref,
            client_evidence=client_evidence,
        )
