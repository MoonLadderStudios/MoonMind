"""Integrated catalog qualification (MoonLadderStudios/MoonMind#4264, #4278).

Every checked-in ``api_service/data/presets/*.yaml`` definition parses and
seeds through the existing catalog service (compatible delivery through
existing refresh mechanisms).

Skill/preset inventory, counts, and description wording are documentation,
not executable behavior, and are intentionally not asserted here per AGENTS.md.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.presets.catalog import PresetCatalogService

pytestmark = [pytest.mark.asyncio]

REPO_ROOT = Path(__file__).resolve().parents[3]
PRESET_DIR = REPO_ROOT / "api_service" / "data" / "presets"


def _preset_slugs() -> list[str]:
    return sorted(p.stem for p in PRESET_DIR.glob("*.yaml"))


@asynccontextmanager
async def catalog_service(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/catalog_4278.db")
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=PRESET_DIR)
            yield service
    finally:
        await engine.dispose()


async def test_preset_catalog_seeds_through_existing_service(tmp_path):
    slugs = _preset_slugs()
    assert slugs, "no preset definitions found to seed"
    async with catalog_service(tmp_path) as service:
        templates = await service.list_templates()
    seeded = {str(item["slug"]) for item in templates}
    missing = {slug for slug in slugs if slug not in seeded}
    assert not missing, f"presets missing after seed: {sorted(missing)}"
