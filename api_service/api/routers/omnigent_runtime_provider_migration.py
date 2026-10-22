"""Operator-visible runtime-provider migration status API.

Source issue: MoonLadderStudios/MoonMind#3833 (required work 10).

One read-only route projects the versioned rollout policy, its per-combination
state and generation, the exact support dimensions, evidence provenance and age,
recent bounded outcomes, rollback availability, and compatibility-path status.

The projection never exposes credentials, provider-session ids, raw host paths,
host image digests, or internal endpoint authority.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api_service.auth_providers import get_instance_access
from api_service.services.settings_catalog import has_settings_permission
from moonmind.omnigent.runtime_provider_migration_status import (
    RuntimeProviderMigrationStatus,
    build_runtime_provider_migration_status,
)

router = APIRouter(
    prefix="/api/omnigent/runtime-provider-migration",
    tags=["Omnigent Runtime Provider Migration"],
)


def _require_read(user: Any) -> None:
    if not has_settings_permission(user, "settings.catalog.read"):
        raise HTTPException(
            403,
            "Missing required permission to read runtime-provider migration "
            "status: settings.catalog.read.",
        )


@router.get(
    "",
    response_model=RuntimeProviderMigrationStatus,
    response_model_by_alias=True,
)
async def get_runtime_provider_migration_status(
    user: Any = Depends(get_instance_access),
) -> Any:
    """Return the current runtime-provider migration state per combination."""

    _require_read(user)
    return build_runtime_provider_migration_status()
