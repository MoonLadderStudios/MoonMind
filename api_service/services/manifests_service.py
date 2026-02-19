"""Service helpers for manifest registry operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import ManifestRecord
from moonmind.workflows.agent_queue import models as queue_models
from moonmind.workflows.agent_queue.job_types import MANIFEST_JOB_TYPE
from moonmind.workflows.agent_queue.manifest_contract import (
    normalize_manifest_job_payload,
)
from moonmind.workflows.agent_queue.service import AgentQueueService


class ManifestRegistryNotFoundError(RuntimeError):
    """Raised when a manifest registry entry does not exist."""


class ManifestsService:
    """Orchestrates manifest registry CRUD and run submission."""

    def __init__(self, session: AsyncSession, queue_service: AgentQueueService) -> None:
        self._session = session
        self._queue_service = queue_service

    async def list_manifests(
        self,
        *,
        limit: int = 50,
        search: str | None = None,
    ) -> list[ManifestRecord]:
        stmt = select(ManifestRecord)
        if search:
            pattern = f"{search.strip()}%"
            stmt = stmt.where(ManifestRecord.name.ilike(pattern))
        stmt = stmt.order_by(ManifestRecord.name.asc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_manifest(self, name: str) -> ManifestRecord | None:
        stmt = select(ManifestRecord).where(ManifestRecord.name == name)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def require_manifest(self, name: str) -> ManifestRecord:
        record = await self.get_manifest(name)
        if record is None:
            raise ManifestRegistryNotFoundError(f"Manifest '{name}' was not found")
        return record

    async def upsert_manifest(
        self,
        *,
        name: str,
        content: str,
    ) -> ManifestRecord:
        normalized = normalize_manifest_job_payload(
            {
                "manifest": {
                    "name": name,
                    "action": "plan",
                    "source": {"kind": "inline", "content": content},
                }
            }
        )
        manifest_hash = normalized["manifestHash"]
        manifest_version = normalized["manifestVersion"]

        now = datetime.now(UTC)
        record = await self.get_manifest(name)
        if record is None:
            record = ManifestRecord(
                name=name,
                content=content,
                content_hash=manifest_hash,
                version=manifest_version,
                created_at=now,
                updated_at=now,
            )
            self._session.add(record)
        else:
            record.content = content
            record.content_hash = manifest_hash
            record.version = manifest_version
            record.updated_at = now
        await self._session.flush()
        await self._session.refresh(record)
        await self._session.commit()
        return record

    async def submit_manifest_run(
        self,
        *,
        name: str,
        action: str,
        options: dict[str, Any] | None,
        user_id: UUID | None,
    ) -> queue_models.AgentJob:
        record = await self.require_manifest(name)

        payload = {
            "manifest": {
                "name": record.name,
                "action": action,
                "source": {
                    "kind": "registry",
                    "name": record.name,
                    "content": record.content,
                },
            }
        }
        if options:
            payload["manifest"]["options"] = options

        job = await self._queue_service.create_job(
            job_type=MANIFEST_JOB_TYPE,
            payload=payload,
            priority=0,
            created_by_user_id=user_id,
            requested_by_user_id=user_id,
        )

        record.last_run_job_id = job.id
        record.last_run_status = job.status.value
        record.last_run_started_at = job.created_at
        record.last_run_finished_at = None
        record.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.commit()
        return job


__all__ = ["ManifestRegistryNotFoundError", "ManifestsService"]
