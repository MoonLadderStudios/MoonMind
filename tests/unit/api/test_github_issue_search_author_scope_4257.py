"""Preset metadata and typed expansion for the author-scope checkbox (#4257)."""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.presets.catalog import (
    PresetCatalogService,
    PresetValidationError,
)

pytestmark = [pytest.mark.asyncio]

PRESET_SLUG = "github-issue-search-and-implement"
SEED_FILE = Path("api_service/data/presets/github-issue-search-and-implement.yaml")
LABEL = "Include issues created by other users"
HELP = "By default, only issues created by the GitHub account used for this search are eligible."


@asynccontextmanager
async def catalog_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/author_scope_catalog.db"
    engine = create_async_engine(db_url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


@asynccontextmanager
async def seeded_catalog(tmp_path):
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir(exist_ok=True)
    shutil.copy(SEED_FILE, seed_dir / SEED_FILE.name)
    async with catalog_db(tmp_path) as maker:
        async with maker() as session:
            catalog = PresetCatalogService(session)
            result = await catalog.sync_seed_templates(seed_dir=seed_dir)
            assert result.created == 1 or result.updated >= 0
            yield catalog


def _document() -> dict:
    return yaml.safe_load(SEED_FILE.read_text(encoding="utf-8"))


def test_yaml_declares_checkbox_with_exact_label_help_and_default():
    document = _document()
    properties = document["annotations"]["inputSchema"]["properties"]
    assert properties["include_all_authors"] == {
        "type": "boolean",
        "title": LABEL,
        "description": HELP,
        "default": False,
    }
    assert document["annotations"]["defaults"]["include_all_authors"] is False
    assert "advanced" not in (document["annotations"].get("uiSchema") or {}).get(
        "include_all_authors", {}
    )


def test_yaml_orders_checkbox_immediately_after_search_with_binding():
    document = _document()
    names = [item["name"] for item in document["inputs"]]
    assert names.index("include_all_authors") == names.index("issue_search") + 1
    entry = next(item for item in document["inputs"] if item["name"] == "include_all_authors")
    assert entry["type"] == "boolean"
    assert entry["required"] is False
    assert entry["default"] is False
    first_tool_inputs = document["steps"][0]["tool"]["inputs"]
    assert first_tool_inputs["includeAllAuthors"] == "{{ inputs.include_all_authors }}"


async def test_expansion_materializes_false_when_omitted(tmp_path):
    async with seeded_catalog(tmp_path) as catalog:
        expanded = await catalog.expand_template(
            slug=PRESET_SLUG, scope="global", scope_ref=None, inputs={}, context={}
        )
    assert expanded["appliedTemplate"]["inputs"]["include_all_authors"] is False
    tool_inputs = expanded["steps"][0]["tool"]["inputs"]
    assert tool_inputs["includeAllAuthors"] is False


async def test_expansion_preserves_explicit_boolean(tmp_path):
    for value in (True, False):
        async with seeded_catalog(tmp_path) as catalog:
            expanded = await catalog.expand_template(
                slug=PRESET_SLUG,
                scope="global",
                scope_ref=None,
                inputs={"include_all_authors": value},
                context={},
            )
        assert expanded["appliedTemplate"]["inputs"]["include_all_authors"] is value
        assert expanded["steps"][0]["tool"]["inputs"]["includeAllAuthors"] is value


@pytest.mark.parametrize("malformed", ["true", "false", 1, 0, None, [], {}, 1.5])
async def test_expansion_rejects_malformed_scope(tmp_path, malformed):
    async with seeded_catalog(tmp_path) as catalog:
        with pytest.raises(PresetValidationError, match="must be a boolean"):
            await catalog.expand_template(
                slug=PRESET_SLUG,
                scope="global",
                scope_ref=None,
                inputs={"include_all_authors": malformed},
                context={},
            )


async def test_expansion_keeps_run_verify_behavior(tmp_path):
    async with seeded_catalog(tmp_path) as catalog:
        expanded = await catalog.expand_template(
            slug=PRESET_SLUG,
            scope="global",
            scope_ref=None,
            inputs={"run_verify": True},
            context={},
        )
    assert expanded["appliedTemplate"]["inputs"]["run_verify"] is True
    assert expanded["appliedTemplate"]["inputs"]["include_all_authors"] is False
