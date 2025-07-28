from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import ManifestRecord
from moonmind.manifest import (
    ManifestChange,
    ManifestRunner,
    compute_content_hash,
    detect_change,
)
from moonmind.schemas import Manifest


class ManifestSyncService:
    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def sync_manifest(
        self, db_session: AsyncSession, name: str, content: str
    ) -> ManifestChange:
        """Sync a single manifest by name."""
        manifest = Manifest.model_validate_yaml(content)

        result = await db_session.execute(
            select(ManifestRecord).where(ManifestRecord.name == name)
        )
        record = result.scalars().first()
        stored_hash = record.content_hash if record else None

        status = detect_change(stored_hash, manifest)

        if status in {ManifestChange.NEW, ManifestChange.MODIFIED}:
            self.logger.info("Running readers for manifest %s", name)
            runner = ManifestRunner(manifest, logger=self.logger)
            runner.run()

            now = datetime.now(timezone.utc)
            new_hash = compute_content_hash(manifest)

            if record is None:
                record = ManifestRecord(
                    name=name,
                    content=content,
                    content_hash=new_hash,
                    last_indexed_at=now,
                )
                db_session.add(record)
            else:
                record.content = content
                record.content_hash = new_hash
                record.last_indexed_at = now
            await db_session.commit()

        return status
