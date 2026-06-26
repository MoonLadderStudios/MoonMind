#!/usr/bin/env python3
"""Backfill preset recents from recent task queue history."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api_service.db.base import get_async_session_context
from api_service.db.models import (
    Preset,
    PresetRecent,
)

_LOOKBACK_DAYS = 30

def _extract_applied_templates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    task = payload.get("task")
    if not isinstance(task, dict):
        return []
    applied = task.get("appliedStepTemplates")
    if not isinstance(applied, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in applied:
        if not isinstance(raw, dict):
            continue
        slug = str(raw.get("slug") or "").strip()
        if not slug:
            continue
        result.append(
            {"slug": slug, "appliedAt": raw.get("appliedAt")}
        )
    return result

async def backfill_recents(*, lookback_days: int = _LOOKBACK_DAYS) -> int:
    threshold = datetime.now(UTC) - timedelta(days=lookback_days)

    async with get_async_session_context() as session:
        preset_rows = (
            await session.execute(
                select(
                    Preset.id,
                    Preset.slug,
                )
            )
        ).all()
        preset_index: dict[str, UUID] = {
            str(slug): preset_id
            for preset_id, slug in preset_rows
        }

        jobs = (
            await session.execute(
                select(
                    queue_models.AgentJob.requested_by_user_id,
                    queue_models.AgentJob.created_by_user_id,
                    queue_models.AgentJob.payload,
                    queue_models.AgentJob.created_at,
                ).where(
                    queue_models.AgentJob.type == "task",
                    queue_models.AgentJob.created_at >= threshold,
                )
            )
        ).all()

        inserted = 0
        for requested_by_user_id, created_by_user_id, payload, created_at in jobs:
            user_id = requested_by_user_id or created_by_user_id
            if user_id is None:
                continue
            if not isinstance(payload, dict):
                continue
            for template_meta in _extract_applied_templates(payload):
                preset_id = preset_index.get(template_meta["slug"])
                if preset_id is None:
                    continue
                applied_at = created_at
                raw_applied = template_meta.get("appliedAt")
                if isinstance(raw_applied, str) and raw_applied.strip():
                    try:
                        applied_at = datetime.fromisoformat(raw_applied)
                    except ValueError:
                        applied_at = created_at
                result = await session.execute(
                    pg_insert(PresetRecent)
                    .values(
                        user_id=user_id,
                        template_id=preset_id,
                        applied_at=applied_at,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["user_id", "template_id"]
                    )
                )
                inserted += int((result.rowcount or 0) > 0)

        await session.commit()
        return inserted

async def _main() -> int:
    inserted = await backfill_recents()
    print(f"Inserted {inserted} template recent rows.")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
