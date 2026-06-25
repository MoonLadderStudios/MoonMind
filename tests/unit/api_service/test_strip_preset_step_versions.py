"""Regression tests for the MM-912 preset-step version cleanup migration (328).

Covers both the pure sanitization helper and the real expansion boundary: a
preset whose persisted steps still carry the now-removed version fields (as the
327 migration copied them verbatim from ``preset_versions``) must expand once
the version fields are stripped.
"""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    Preset,
    PresetReleaseStatus,
    PresetScopeType,
)
from api_service.services.presets.catalog import (
    ExpandOptions,
    PresetCatalogService,
    PresetValidationError,
)

migration = importlib.import_module(
    "api_service.migrations.versions.328_strip_preset_step_versions"
)
strip_step_versions = migration.strip_step_versions


def test_strip_removes_include_version_top_level() -> None:
    steps = [
        {"type": "Skill", "instructions": "do work", "skill": {"name": "demo"}},
        {
            "kind": "include",
            "slug": "child",
            "alias": "child",
            "scope": "global",
            "version": "1.0.0",
            "inputMapping": {},
        },
    ]
    sanitized, changed = strip_step_versions(steps)
    assert changed is True
    assert "version" not in sanitized[1]
    assert sanitized[1]["slug"] == "child"
    # Non-include step is untouched.
    assert sanitized[0] == steps[0]


def test_strip_removes_preset_payload_versions() -> None:
    steps = [
        {
            "type": "preset",
            "instructions": "run child preset",
            "preset": {"slug": "child", "version": "2.0.0", "presetVersion": "2.0.0"},
        }
    ]
    sanitized, changed = strip_step_versions(steps)
    assert changed is True
    assert "version" not in sanitized[0]["preset"]
    assert "presetVersion" not in sanitized[0]["preset"]
    assert sanitized[0]["preset"]["slug"] == "child"


def test_strip_preserves_tool_version() -> None:
    steps = [
        {
            "type": "Tool",
            "instructions": "call tool",
            "tool": {"name": "do_thing", "version": "3.1.4", "args": {}},
        }
    ]
    sanitized, changed = strip_step_versions(steps)
    assert changed is False
    assert sanitized[0]["tool"]["version"] == "3.1.4"


def test_strip_is_noop_for_clean_and_idempotent() -> None:
    steps = [
        {"type": "Skill", "instructions": "do work", "skill": {"name": "demo"}},
        {"kind": "include", "slug": "child", "alias": "child", "inputMapping": {}},
    ]
    sanitized, changed = strip_step_versions(steps)
    assert changed is False
    again, changed_again = strip_step_versions(sanitized)
    assert changed_again is False
    assert again == sanitized


def test_strip_handles_non_list() -> None:
    assert strip_step_versions(None) == (None, False)


def test_upgrade_strips_versions_in_place(tmp_path) -> None:
    """The migration's upgrade() repairs persisted rows against a live table."""

    import sqlalchemy as sa

    engine = sa.create_engine(f"sqlite:///{tmp_path}/upgrade.db", future=True)
    metadata = sa.MetaData()
    presets = sa.Table(
        "presets",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("steps", sa.JSON()),
    )
    metadata.create_all(engine)

    versioned = [
        {"type": "Skill", "instructions": "do work", "skill": {"name": "demo"}},
        {"kind": "include", "slug": "child", "version": "1.0.0", "inputMapping": {}},
    ]
    clean = [{"type": "Skill", "instructions": "clean", "skill": {"name": "demo"}}]
    with engine.begin() as conn:
        conn.execute(presets.insert(), [{"id": 1, "steps": versioned}, {"id": 2, "steps": clean}])

    class _Op:
        def __init__(self, bind):
            self._bind = bind

        def get_bind(self):
            return self._bind

    with engine.begin() as conn:
        original_op = migration.op
        migration.op = _Op(conn)
        try:
            migration.upgrade()
        finally:
            migration.op = original_op

    with engine.connect() as conn:
        stored = {
            row.id: row.steps
            for row in conn.execute(sa.select(presets.c.id, presets.c.steps))
        }
    assert "version" not in stored[1][1]
    assert stored[2] == clean
    engine.dispose()


@pytest.mark.asyncio
async def test_expansion_repaired_after_stripping_persisted_versions(tmp_path) -> None:
    """A migrated preset with a versioned include fails to expand until cleaned."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/preset_versions_cleanup.db"
    engine = create_async_engine(db_url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        # Persisted definitions exactly as the 327 copy would have left them:
        # the include still carries the now-forbidden version field.
        parent_steps = [
            {"type": "Skill", "instructions": "parent step", "skill": {"name": "demo"}},
            {
                "kind": "include",
                "slug": "child-preset",
                "alias": "child",
                "scope": "global",
                "version": "1.0.0",
                "inputMapping": {},
            },
        ]
        async with maker() as session:
            session.add(
                Preset(
                    slug="child-preset",
                    scope_type=PresetScopeType.GLOBAL,
                    scope_ref=None,
                    title="Child",
                    description="child",
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "type": "Skill",
                            "instructions": "child work",
                            "skill": {"name": "demo"},
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    max_step_count=25,
                    release_status=PresetReleaseStatus.ACTIVE,
                    is_active=True,
                )
            )
            session.add(
                Preset(
                    slug="parent-preset",
                    scope_type=PresetScopeType.GLOBAL,
                    scope_ref=None,
                    title="Parent",
                    description="parent",
                    tags=[],
                    inputs_schema=[],
                    steps=parent_steps,
                    annotations={},
                    required_capabilities=[],
                    max_step_count=25,
                    release_status=PresetReleaseStatus.ACTIVE,
                    is_active=True,
                )
            )
            await session.commit()

        # Before cleanup: expansion is rejected because of the include version.
        async with maker() as session:
            with pytest.raises(PresetValidationError) as exc_info:
                await PresetCatalogService(session).expand_template(
                    slug="parent-preset",
                    scope="global",
                    scope_ref=None,
                    inputs={},
                    context={},
                    options=ExpandOptions(should_enforce_step_limit=True),
                )
            assert "remove version" in str(exc_info.value)

        # Apply the migration's sanitization to the persisted steps.
        async with maker() as session:
            parent = await PresetCatalogService(session)._get_template_for_scope(
                slug="parent-preset", scope=PresetScopeType.GLOBAL, scope_ref=None
            )
            sanitized, changed = strip_step_versions(list(parent.steps or []))
            assert changed is True
            parent.steps = sanitized
            await session.commit()

        # After cleanup: expansion produces the parent + included child steps.
        async with maker() as session:
            expanded = await PresetCatalogService(session).expand_template(
                slug="parent-preset",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
                options=ExpandOptions(should_enforce_step_limit=True),
            )
        assert len(expanded["steps"]) == 2
        assert "version" not in expanded["appliedTemplate"]
    finally:
        await engine.dispose()
