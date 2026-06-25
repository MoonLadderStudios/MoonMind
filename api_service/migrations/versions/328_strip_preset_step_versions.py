"""Strip removed version fields from persisted preset steps for MM-912.

Source traceability: MM-912 / MM-901.

MM-912 (327) copied each preset's ``steps`` verbatim from ``preset_versions``
into ``presets.steps``. Those step definitions were authored under the old
versioned schema and still carry ``version`` on ``include`` steps and
``version``/``presetVersion`` inside ``preset`` step payloads. The slug/scope
expansion path now rejects any include or preset payload that carries a version
(``preset_include_version_not_supported`` / ``preset_version_not_supported``),
so every migrated preset that includes another preset fails to expand.

Seed-backed global presets are repaired on startup by re-seeding from the
versionless YAML, but personal presets (and any preset not covered by a seed
file) keep the forbidden version fields and stay broken. This migration removes
those fields from the persisted definitions so the canonical slug/scope
expansion path accepts them. It does not touch ``tool``/``skill`` payloads, so
legitimate executable-tool versions are preserved.

Revision ID: 328_strip_preset_step_versions
Revises: 327_mm912_slug_scope_presets
Create Date: 2026-06-25
"""

from __future__ import annotations

from typing import Any, Union

import sqlalchemy as sa
from alembic import op

revision: str = "328_strip_preset_step_versions"
down_revision: Union[str, None] = "327_mm912_slug_scope_presets"

__all__ = [
    "revision",
    "down_revision",
    "upgrade",
    "downgrade",
    "strip_step_versions",
]

_VERSION_KEYS = ("version", "presetVersion")
_INCLUDE_KIND = "include"


def strip_step_versions(steps: Any) -> tuple[list[Any], bool]:
    """Return ``(sanitized_steps, changed)`` with forbidden version fields removed.

    Removes ``version``/``presetVersion`` from ``include`` steps (top level) and
    from nested ``preset`` payloads. Tool and skill payloads are left untouched
    so executable-tool versions survive.
    """

    if not isinstance(steps, list):
        return steps, False

    changed = False
    sanitized: list[Any] = []
    for step in steps:
        if not isinstance(step, dict):
            sanitized.append(step)
            continue
        new_step = dict(step)

        kind = str(new_step.get("kind") or "step").strip().lower()
        if kind == _INCLUDE_KIND:
            for key in _VERSION_KEYS:
                if key in new_step:
                    new_step.pop(key)
                    changed = True

        preset_payload = new_step.get("preset")
        if isinstance(preset_payload, dict):
            new_preset = dict(preset_payload)
            for key in _VERSION_KEYS:
                if key in new_preset:
                    new_preset.pop(key)
                    changed = True
            if new_preset != preset_payload:
                new_step["preset"] = new_preset

        sanitized.append(new_step)

    return sanitized, changed


def upgrade() -> None:
    conn = op.get_bind()
    # A typed lightweight table so the JSON column is (de)serialized per dialect
    # rather than relying on a Postgres-only ``jsonb`` cast.
    presets = sa.table(
        "presets",
        sa.column("id"),
        sa.column("steps", sa.JSON()),
    )
    rows = conn.execute(sa.select(presets.c.id, presets.c.steps)).fetchall()
    for preset_id, steps in rows:
        sanitized, changed = strip_step_versions(steps)
        if not changed:
            continue
        conn.execute(
            sa.update(presets)
            .where(presets.c.id == preset_id)
            .values(steps=sanitized)
        )


def downgrade() -> None:
    # Version removal is part of the MM-912 slug/scope cutover; the original
    # version values are not recoverable, so the cleanup is forward-only.
    pass
