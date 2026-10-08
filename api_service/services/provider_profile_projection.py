"""Admission-time label snapshot shared by execution and schedule producers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import ManagedAgentProviderProfile
from moonmind.workflows.executions.provider_profile_projection import (
    recorded_provider_profile_ids,
)


async def provider_profile_label_snapshot(
    session: AsyncSession, parameters: Mapping[str, Any]
) -> dict[str, str | None]:
    """Capture only recorded IDs' display names in one batched admission read."""

    profile_ids = [
        profile_id for profile_id, _ in recorded_provider_profile_ids(parameters)
    ]
    if not profile_ids:
        return {}
    rows = await session.execute(
        select(
            ManagedAgentProviderProfile.profile_id,
            ManagedAgentProviderProfile.account_label,
            ManagedAgentProviderProfile.provider_label,
        ).where(ManagedAgentProviderProfile.profile_id.in_(profile_ids))
    )
    return {
        str(profile_id): (account_label or provider_label or None)
        for profile_id, account_label, provider_label in rows.all()
    }
