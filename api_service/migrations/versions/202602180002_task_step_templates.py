"""Create task step template catalog tables and enums."""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202602180002"
down_revision: Union[str, None] = "202602180001"


TASK_TEMPLATE_SCOPE_TYPE = postgresql.ENUM(
    "global",
    "team",
    "personal",
    name="tasktemplatescopetype",
    create_type=False,
)

TASK_TEMPLATE_RELEASE_STATUS = postgresql.ENUM(
    "draft",
    "active",
    "inactive",
    name="tasktemplatereleasestatus",
    create_type=False,
)


def _json_variant() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    """Create task template persistence schema."""

    bind = op.get_bind()
    TASK_TEMPLATE_SCOPE_TYPE.create(bind, checkfirst=True)
    TASK_TEMPLATE_RELEASE_STATUS.create(bind, checkfirst=True)

    op.create_table(
        "task_step_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column(
            "scope_type",
            postgresql.ENUM(name="tasktemplatescopetype", create_type=False),
            nullable=False,
        ),
        sa.Column("scope_ref", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tags", _json_variant(), nullable=False),
        sa.Column("required_capabilities", _json_variant(), nullable=False),
        sa.Column("latest_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "slug", "scope_type", "scope_ref", name="uq_task_step_template_slug_scope"
        ),
    )
    op.create_index(
        "ix_task_step_templates_scope",
        "task_step_templates",
        ["scope_type", "scope_ref"],
        unique=False,
    )
    op.create_index(
        "ix_task_step_templates_slug",
        "task_step_templates",
        ["slug"],
        unique=False,
    )
    op.create_index(
        "ix_task_step_templates_active",
        "task_step_templates",
        ["is_active"],
        unique=False,
    )

    op.create_table(
        "task_step_template_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("inputs_schema", _json_variant(), nullable=False),
        sa.Column("steps", _json_variant(), nullable=False),
        sa.Column("annotations", _json_variant(), nullable=True),
        sa.Column("required_capabilities", _json_variant(), nullable=False),
        sa.Column(
            "max_step_count", sa.Integer(), nullable=False, server_default=sa.text("25")
        ),
        sa.Column(
            "release_status",
            postgresql.ENUM(name="tasktemplatereleasestatus", create_type=False),
            nullable=False,
            server_default=sa.text("'draft'::tasktemplatereleasestatus"),
        ),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("seed_source", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["template_id"], ["task_step_templates.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["reviewed_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "template_id", "version", name="uq_task_step_template_version_label"
        ),
    )
    op.create_index(
        "ix_task_step_template_versions_template",
        "task_step_template_versions",
        ["template_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_task_template_latest_version",
        "task_step_templates",
        "task_step_template_versions",
        ["latest_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "task_step_template_favorites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["template_id"], ["task_step_templates.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "template_id", name="uq_task_template_favorite"),
    )
    op.create_index(
        "ix_task_step_template_favorites_user",
        "task_step_template_favorites",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "task_step_template_recents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["task_step_template_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_step_template_recents_user",
        "task_step_template_recents",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop task template persistence schema."""

    op.drop_index(
        "ix_task_step_template_recents_user", table_name="task_step_template_recents"
    )
    op.drop_table("task_step_template_recents")

    op.drop_index(
        "ix_task_step_template_favorites_user",
        table_name="task_step_template_favorites",
    )
    op.drop_table("task_step_template_favorites")

    op.drop_constraint(
        "fk_task_template_latest_version", "task_step_templates", type_="foreignkey"
    )
    op.drop_index(
        "ix_task_step_template_versions_template",
        table_name="task_step_template_versions",
    )
    op.drop_table("task_step_template_versions")

    op.drop_index("ix_task_step_templates_active", table_name="task_step_templates")
    op.drop_index("ix_task_step_templates_slug", table_name="task_step_templates")
    op.drop_index("ix_task_step_templates_scope", table_name="task_step_templates")
    op.drop_table("task_step_templates")

    bind = op.get_bind()
    TASK_TEMPLATE_RELEASE_STATUS.drop(bind, checkfirst=True)
    TASK_TEMPLATE_SCOPE_TYPE.drop(bind, checkfirst=True)
