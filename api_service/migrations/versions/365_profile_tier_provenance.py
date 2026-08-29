"""Stamp provider profile tier provenance and enforce the model_tiers array shape.

Revision 335 added ``model_tiers`` and backfilled one tier per profile, but it
recorded no provenance and created no array/length check. Deployments stamped at
335 or any later revision will never re-run it, so this forward revision carries
both changes for databases that already applied 335 and for fresh databases
alike.

Revision ID: 365_profile_tier_provenance
Revises: 364_container_job_gpu_obs
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql import column, table

revision: str = "365_profile_tier_provenance"
down_revision: Union[str, None] = "364_container_job_gpu_obs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = [
    "MIGRATED_FROM_ANNOTATION",
    "LEGACY_DEFAULT_MIGRATION_SOURCE",
    "RUNTIME_DEFAULT_MIGRATION_SOURCE",
    "MODEL_TIERS_ARRAY_CHECK",
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "upgrade",
    "downgrade",
]

MIGRATED_FROM_ANNOTATION = "migratedFrom"
LEGACY_DEFAULT_MIGRATION_SOURCE = "default_model_default_effort"
RUNTIME_DEFAULT_MIGRATION_SOURCE = "runtime_default"

MODEL_TIERS_ARRAY_CHECK = (
    "jsonb_typeof(model_tiers) = 'array' AND jsonb_array_length(model_tiers) >= 1"
)

profiles_table = table(
    "managed_agent_provider_profiles",
    column("profile_id", sa.String),
    column("default_model", sa.String),
    column("default_effort", sa.String),
    column("model_tiers", sa.JSON),
)


def _loaded_tiers(raw: Any) -> Any:
    """Return ``model_tiers`` as Python data for JSON and JSONB storage alike."""

    if isinstance(raw, (str, bytes, bytearray)):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


def _revision_335_backfilled_tier(
    tiers: Any,
    *,
    default_model: str | None,
    default_effort: str | None,
) -> tuple[dict[str, Any], str] | None:
    """Return the lone tier revision 335 wrote, plus its provenance source.

    Only the exact un-annotated shape revision 335 produced is rewritten, so
    operator-authored tiers keep their own annotations.
    """

    if not isinstance(tiers, list) or len(tiers) != 1:
        return None
    tier = tiers[0]
    if not isinstance(tier, dict):
        return None
    if tier.get("parameters") != {} or tier.get("annotations") != {}:
        return None

    if default_model is not None or default_effort is not None:
        expected = {
            "label": "Legacy default",
            "model": default_model,
            "effort": default_effort,
            "parameters": {},
            "annotations": {},
        }
        source = LEGACY_DEFAULT_MIGRATION_SOURCE
    else:
        expected = {
            "label": "Runtime default",
            "model": None,
            "effort": None,
            "parameters": {},
            "annotations": {},
        }
        source = RUNTIME_DEFAULT_MIGRATION_SOURCE

    if tier != expected:
        return None
    return tier, source


def upgrade() -> None:
    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.select(
                profiles_table.c.profile_id,
                profiles_table.c.default_model,
                profiles_table.c.default_effort,
                profiles_table.c.model_tiers,
            )
        )
    )
    for row in rows:
        match = _revision_335_backfilled_tier(
            _loaded_tiers(row.model_tiers),
            default_model=row.default_model,
            default_effort=row.default_effort,
        )
        if match is None:
            continue
        tier, source = match
        stamped = dict(tier)
        stamped["annotations"] = {MIGRATED_FROM_ANNOTATION: source}
        bind.execute(
            profiles_table.update()
            .where(profiles_table.c.profile_id == row.profile_id)
            .values(model_tiers=[stamped])
        )

    op.create_check_constraint(
        "ck_provider_profiles_model_tiers_array",
        "managed_agent_provider_profiles",
        MODEL_TIERS_ARRAY_CHECK,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_provider_profiles_model_tiers_array",
        "managed_agent_provider_profiles",
        type_="check",
    )

    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.select(
                profiles_table.c.profile_id,
                profiles_table.c.model_tiers,
            )
        )
    )
    for row in rows:
        tiers = _loaded_tiers(row.model_tiers)
        if not isinstance(tiers, list) or len(tiers) != 1:
            continue
        tier = tiers[0]
        if not isinstance(tier, dict):
            continue
        annotations = tier.get("annotations")
        if not isinstance(annotations, dict):
            continue
        if set(annotations) != {MIGRATED_FROM_ANNOTATION}:
            continue
        if annotations[MIGRATED_FROM_ANNOTATION] not in (
            LEGACY_DEFAULT_MIGRATION_SOURCE,
            RUNTIME_DEFAULT_MIGRATION_SOURCE,
        ):
            continue
        stripped = dict(tier)
        stripped["annotations"] = {}
        bind.execute(
            profiles_table.update()
            .where(profiles_table.c.profile_id == row.profile_id)
            .values(model_tiers=[stripped])
        )
