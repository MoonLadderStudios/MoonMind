"""Seed global speckit-orchestrate task preset."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
import yaml
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "202602190002"
down_revision: Union[str, None] = "202602190001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SLUG = "speckit-orchestrate"
_SCOPE = "global"
_SCOPE_REF = None
_VERSION = "1.0.0"


def _json_variant() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _seed_file_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "data"
        / "task_step_templates"
        / "speckit-orchestrate.yaml"
    )


def _seed_template_tables() -> tuple[sa.Table, sa.Table]:
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
    return templates_table, versions_table


def _load_seed_document() -> dict[str, object] | None:
    seed_file = _seed_file_path()
    if not seed_file.exists():
        return None
    document = yaml.safe_load(seed_file.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        return None
    return document


def _template_uuid(slug: str, scope: str, scope_ref: str | None) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"task-template:{scope}:{scope_ref}:{slug}")


def _version_uuid(
    slug: str, scope: str, scope_ref: str | None, version: str
) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_DNS,
        f"task-template-version:{scope}:{scope_ref}:{slug}:{version}",
    )


def upgrade() -> None:
    document = _load_seed_document()
    if document is None:
        return

    slug = str(document.get("slug") or "").strip()
    title = str(document.get("title") or "").strip()
    description = str(document.get("description") or "").strip()
    scope = str(document.get("scope") or _SCOPE).strip().lower() or _SCOPE
    scope_ref = document.get("scopeRef")
    version = str(document.get("version") or _VERSION).strip() or _VERSION
    tags = list(document.get("tags") or [])
    inputs = list(document.get("inputs") or [])
    steps = list(document.get("steps") or [])
    required_capabilities = list(document.get("requiredCapabilities") or [])
    annotations = dict(document.get("annotations") or {})
    seed_source = str(_seed_file_path())

    if not slug or not title or not description or not steps:
        return

    template_id = _template_uuid(slug=slug, scope=scope, scope_ref=scope_ref)
    version_id = _version_uuid(
        slug=slug,
        scope=scope,
        scope_ref=scope_ref,
        version=version,
    )

    templates_table, versions_table = _seed_template_tables()
    bind = op.get_bind()
    now = sa.func.now()

    bind.execute(
        pg_insert(templates_table)
        .values(
            id=template_id,
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
        .on_conflict_do_update(
            index_elements=["id"],
            set_={
                "title": title,
                "description": description,
                "tags": tags,
                "required_capabilities": required_capabilities,
                "is_active": True,
                "updated_at": now,
            },
        )
    )

    bind.execute(
        pg_insert(versions_table)
        .values(
            id=version_id,
            template_id=template_id,
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
            seed_source=seed_source,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_={
                "inputs_schema": inputs,
                "steps": steps,
                "annotations": annotations,
                "required_capabilities": required_capabilities,
                "max_step_count": max(1, len(steps)),
                "release_status": "active",
                "seed_source": seed_source,
                "updated_at": now,
            },
        )
    )

    bind.execute(
        sa.text(
            """
            UPDATE task_step_templates
            SET latest_version_id = :version_id,
                is_active = TRUE,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :template_id
            """
        ),
        {
            "template_id": str(template_id),
            "version_id": str(version_id),
        },
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM task_step_templates
            WHERE slug = :slug
              AND scope_type = :scope
              AND scope_ref IS NULL
            """
        ),
        {
            "slug": _SLUG,
            "scope": _SCOPE,
        },
    )
