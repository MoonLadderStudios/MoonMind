"""Align speckit-orchestrate preset with publish-stage handoff strategy."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
import yaml
from alembic import op

revision: str = "202603010001"
down_revision: str | None = "202602260002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _seed_file_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "data"
        / "task_step_templates"
        / "speckit-orchestrate.yaml"
    )


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
    if not slug:
        return

    scope = str(document.get("scope") or "global").strip().lower() or "global"
    scope_ref = document.get("scopeRef") or None
    version = str(document.get("version") or "1.0.0").strip() or "1.0.0"
    required_capabilities = list(document.get("requiredCapabilities") or [])
    steps = list(document.get("steps") or [])
    seed_source = str(_seed_file_path())

    template_id = _template_uuid(slug=slug, scope=scope, scope_ref=scope_ref)
    version_id = _version_uuid(
        slug=slug,
        scope=scope,
        scope_ref=scope_ref,
        version=version,
    )

    bind = op.get_bind()
    templates_table = sa.table(
        "task_step_templates",
        sa.column("id", sa.Uuid()),
        sa.column("required_capabilities", sa.JSON()),
        sa.column("is_active", sa.Boolean()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    versions_table = sa.table(
        "task_step_template_versions",
        sa.column("id", sa.Uuid()),
        sa.column("required_capabilities", sa.JSON()),
        sa.column("steps", sa.JSON()),
        sa.column("seed_source", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    bind.execute(
        sa.update(templates_table)
        .where(templates_table.c.id == template_id)
        .values(
            required_capabilities=required_capabilities,
            is_active=True,
            updated_at=sa.func.current_timestamp(),
        )
    )
    bind.execute(
        sa.update(versions_table)
        .where(versions_table.c.id == version_id)
        .values(
            required_capabilities=required_capabilities,
            steps=steps,
            seed_source=seed_source,
            updated_at=sa.func.current_timestamp(),
        )
    )


def downgrade() -> None:
    """No-op downgrade; this migration only refreshes seeded data content."""

    pass
