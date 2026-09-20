"""Integrated catalog qualification (MoonLadderStudios/MoonMind#4264, #4278).

Executable regression coverage for skill preset delivery:

- every checked-in ``api_service/data/presets/*.yaml`` definition parses and
  seeds through the existing catalog service (compatible delivery through
  existing refresh mechanisms);
- repo-native skill trigger descriptions stay harness-neutral: they must not
  name one specific invoking agent/harness. Vendored MoonSpec skills
  (``moonspec-*``, owned upstream via ``tools/sync_moonspec.py``) are
  excluded here. Opaque runtime/account/model/effort selections and
  legitimate service identifiers elsewhere in skill bodies or preset inputs
  are preserved and are explicitly out of scope here (a naive global string
  ban is forbidden by the epic constraint).

Skill/preset inventory and counts are documentation, not executable
behavior, and are intentionally not asserted here per AGENTS.md.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.presets.catalog import PresetCatalogService

pytestmark = [pytest.mark.asyncio]

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
PRESET_DIR = REPO_ROOT / "api_service" / "data" / "presets"

# Vendored MoonSpec skills are owned upstream (see tools/sync_moonspec.py)
# and must not be pinned by repo-native inventory or wording checks.
VENDORED_SKILL_PREFIX = "moonspec-"

# Harness/agent names that must not appear as the addressed invoker in a skill
# trigger description. Descriptions route task intent; naming one harness
# contradicts the single-runtime direction and the epic's model-neutrality
# constraint. "MoonMind" (the product itself) stays allowed.
_HARNESS_NAME_PATTERN = re.compile(
    r"\b(codex|claude code|opencode|gemini)\b", re.IGNORECASE
)

_FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def _repo_native_skills() -> list[str]:
    return sorted(
        d.name
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name != "_shared" and not d.name.startswith(VENDORED_SKILL_PREFIX)
    )


def _preset_slugs() -> list[str]:
    return sorted(p.stem for p in PRESET_DIR.glob("*.yaml"))


def _frontmatter(skill: str) -> dict:
    text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    match = _FRONTMATTER_PATTERN.match(text)
    assert match is not None, f"{skill}: SKILL.md has no YAML frontmatter"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), f"{skill}: frontmatter is not a mapping"
    return data


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


def test_skill_trigger_descriptions_are_harness_neutral():
    offenders = {}
    for skill in _repo_native_skills():
        description = str(_frontmatter(skill).get("description", ""))
        hit = _HARNESS_NAME_PATTERN.search(description)
        if hit:
            offenders[skill] = hit.group(0)
    assert not offenders, (
        "skill descriptions name a specific invoking harness; reword to describe "
        f"task intent instead: {offenders}"
    )
