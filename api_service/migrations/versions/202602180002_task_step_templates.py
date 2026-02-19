"""Create task step template catalog tables and enums."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Union

import sqlalchemy as sa
import yaml
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

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


def _seed_catalog_defaults(bind) -> None:
    seed_dir = Path(__file__).resolve().parents[2] / "data" / "task_step_templates"
    if not seed_dir.exists():
        return

    templates_table = sa.table(
        "task_step_templates",
        sa.column("id", sa.Uuid()),
        sa.column("slug", sa.String()),
        sa.column(
            "scope_type",
            postgresql.ENUM(name="tasktemplatescopetype", create_type=False),
        ),
        sa.column("scope_ref", sa.String()),
        sa.column("title", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("tags", _json_variant()),
        sa.column("required_capabilities", _json_variant()),
        sa.column("latest_version_id", sa.Uuid()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_by", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    versions_table = sa.table(
        "task_step_template_versions",
        sa.column("id", sa.Uuid()),
        sa.column("template_id", sa.Uuid()),
        sa.column("version", sa.String()),
        sa.column("inputs_schema", _json_variant()),
        sa.column("steps", _json_variant()),
        sa.column("annotations", _json_variant()),
        sa.column("required_capabilities", _json_variant()),
        sa.column("max_step_count", sa.Integer()),
        sa.column(
            "release_status",
            postgresql.ENUM(name="tasktemplatereleasestatus", create_type=False),
        ),
        sa.column("reviewed_by", sa.Uuid()),
        sa.column("reviewed_at", sa.DateTime(timezone=True)),
        sa.column("notes", sa.Text()),
        sa.column("seed_source", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    for seed_file in sorted(seed_dir.glob("*.yaml")):
        document = yaml.safe_load(seed_file.read_text(encoding="utf-8")) or {}
        if not isinstance(document, dict):
            continue
        slug = str(document.get("slug") or "").strip()
        title = str(document.get("title") or "").strip()
        description = str(document.get("description") or "").strip()
        scope = str(document.get("scope") or "global").strip().lower() or "global"
        scope_ref = document.get("scopeRef")
        version = str(document.get("version") or "1.0.0").strip() or "1.0.0"
        tags = list(document.get("tags") or [])
        inputs = list(document.get("inputs") or [])
        steps = list(document.get("steps") or [])
        required_capabilities = list(document.get("requiredCapabilities") or [])
        annotations = dict(document.get("annotations") or {})

        if not slug or not title or not description or not steps:
            continue
        template_uuid = uuid.uuid5(
            uuid.NAMESPACE_DNS, f"task-template:{scope}:{scope_ref}:{slug}"
        )
        version_uuid = uuid.uuid5(
            uuid.NAMESPACE_DNS,
            f"task-template-version:{scope}:{scope_ref}:{slug}:{version}",
        )
        now = sa.func.now()
        template_inserted = bind.execute(
            pg_insert(templates_table)
            .values(
                id=template_uuid,
                slug=slug,
                scope_type=scope,
                scope_ref=scope_ref,
                title=title,
                description=description,
                tags=tags,
                required_capabilities=required_capabilities,
                latest_version_id=None,
                is_active=True,
                created_by=None,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        version_inserted = bind.execute(
            pg_insert(versions_table)
            .values(
                id=version_uuid,
                template_id=template_uuid,
                version=version,
                inputs_schema=inputs,
                steps=steps,
                annotations=annotations,
                required_capabilities=required_capabilities,
                max_step_count=max(1, len(steps)),
                release_status="active",
                reviewed_by=None,
                reviewed_at=None,
                notes=None,
                seed_source=str(seed_file),
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        if (template_inserted.rowcount or 0) > 0 or (
            version_inserted.rowcount or 0
        ) > 0:
            bind.execute(
                sa.text(
                    """
                    UPDATE task_step_templates
                    SET latest_version_id = :version_id
                    WHERE id = :template_id
                    """
                ),
                {
                    "template_id": str(template_uuid),
                    "version_id": str(version_uuid),
                },
            )


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
    op.create_index(
        "uq_task_step_template_slug_global",
        "task_step_templates",
        ["slug", "scope_type"],
        unique=True,
        postgresql_where=sa.text("scope_ref IS NULL"),
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
        sa.UniqueConstraint(
            "user_id",
            "template_version_id",
            name="uq_task_template_recent_user_version",
        ),
    )
    op.create_index(
        "ix_task_step_template_recents_user",
        "task_step_template_recents",
        ["user_id"],
        unique=False,
    )
    _seed_catalog_defaults(bind)


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
    op.drop_index("uq_task_step_template_slug_global", table_name="task_step_templates")
    op.drop_index("ix_task_step_templates_slug", table_name="task_step_templates")
    op.drop_index("ix_task_step_templates_scope", table_name="task_step_templates")
    op.drop_table("task_step_templates")

    bind = op.get_bind()
    TASK_TEMPLATE_RELEASE_STATUS.drop(bind, checkfirst=True)
    TASK_TEMPLATE_SCOPE_TYPE.drop(bind, checkfirst=True)
