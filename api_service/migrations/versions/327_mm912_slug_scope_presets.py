"""Convert presets to slug/scope current definitions for MM-912.

Source traceability: MM-901.

Revision ID: 327_mm912_slug_scope_presets
Revises: 326_profile_effort
Create Date: 2026-06-25
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "327_mm912_slug_scope_presets"
down_revision: Union[str, None] = "326_profile_effort"

__all__ = ["revision", "down_revision", "upgrade", "downgrade"]


def upgrade() -> None:
    op.add_column("presets", sa.Column("inputs_schema", sa.JSON(), nullable=True))
    op.add_column("presets", sa.Column("steps", sa.JSON(), nullable=True))
    op.add_column("presets", sa.Column("annotations", sa.JSON(), nullable=True))
    op.add_column(
        "presets",
        sa.Column("max_step_count", sa.Integer(), nullable=False, server_default="25"),
    )
    op.add_column(
        "presets",
        sa.Column("release_status", sa.Enum("draft", "active", "inactive", name="presetreleasestatus"), nullable=False, server_default="draft"),
    )
    op.add_column("presets", sa.Column("reviewed_by", sa.Uuid(), nullable=True))
    op.add_column("presets", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("presets", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("presets", sa.Column("seed_source", sa.String(length=255), nullable=True))

    op.execute(
        """
        update presets p
           set inputs_schema = coalesce(v.inputs_schema, '[]'::jsonb),
               steps = coalesce(v.steps, '[]'::jsonb),
               annotations = v.annotations,
               max_step_count = coalesce(v.max_step_count, 25),
               release_status = coalesce(v.release_status, 'draft'),
               reviewed_by = v.reviewed_by,
               reviewed_at = v.reviewed_at,
               notes = v.notes,
               seed_source = v.seed_source
          from preset_versions v
         where v.id = p.latest_version_id
        """
    )
    op.execute("update presets set inputs_schema = '[]'::jsonb where inputs_schema is null")
    op.execute("update presets set steps = '[]'::jsonb where steps is null")
    op.alter_column("presets", "inputs_schema", nullable=False)
    op.alter_column("presets", "steps", nullable=False)

    op.add_column("preset_recents", sa.Column("template_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        update preset_recents r
           set template_id = v.template_id
          from preset_versions v
         where v.id = r.template_version_id
        """
    )
    op.alter_column("preset_recents", "template_id", nullable=False)
    op.drop_constraint("uq_preset_recent_user_version", "preset_recents", type_="unique")
    op.create_unique_constraint(
        "uq_preset_recent_user_template",
        "preset_recents",
        ["user_id", "template_id"],
    )
    op.drop_constraint(
        "preset_recents_template_version_id_fkey",
        "preset_recents",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "preset_recents_template_id_fkey",
        "preset_recents",
        "presets",
        ["template_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("preset_recents", "template_version_id")

    op.drop_constraint("fk_preset_latest_version", "presets", type_="foreignkey")
    op.drop_column("presets", "latest_version_id")
    op.drop_index("ix_preset_versions_template", table_name="preset_versions")
    op.drop_table("preset_versions")


def downgrade() -> None:
    raise RuntimeError("MM-912 removes preset semantic versions; downgrade is unsupported.")
