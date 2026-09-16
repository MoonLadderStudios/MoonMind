"""Integrated catalog qualification (MoonLadderStudios/MoonMind#4264, #4278).

Outcome-based regression coverage for the complete audited catalog from the
September 11 skills/presets audit: the 35 checked-in
``.agents/skills/*/SKILL.md`` entrypoints and the 19
``api_service/data/presets/*.yaml`` definitions. This covers the #4278 slice
that can proceed immediately per the epic delivery table (coverage design,
scenario fixtures, catalog-compatibility delivery checks):

- every expected skill entrypoint row is present with name/description
  frontmatter (additions or removals fail loudly so they get an explicit,
  evidence-backed owner disposition instead of silent drift);
- every expected preset row parses and seeds through the existing catalog
  service (compatible delivery through existing refresh mechanisms);
- skill trigger descriptions stay harness-neutral: they must not name one
  specific invoking agent/harness. Opaque runtime/account/model/effort
  selections and legitimate service identifiers elsewhere in skill bodies or
  preset inputs are preserved and are explicitly out of scope here (a naive
  global string ban is forbidden by the epic constraint).
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

EXPECTED_SKILLS = frozenset(
    {
        "batch-dependabot-resolver",
        "batch-github-workflows",
        "batch-pr-resolver",
        "batch-workflows",
        "code-improvement-proposal",
        "document-author",
        "document-health-remediate",
        "document-health-review",
        "document-update",
        "fix-ci",
        "fix-comments",
        "fix-merge-conflicts",
        "github-issue-to-jira",
        "github-issue-verify",
        "jira-implement",
        "jira-issue-creator",
        "jira-issue-updater",
        "jira-pr-verify",
        "jira-verify",
        "moonspec-align",
        "moonspec-assess",
        "moonspec-breakdown",
        "moonspec-doc-reconcile",
        "moonspec-implement",
        "moonspec-orchestrate",
        "moonspec-plan",
        "moonspec-specify",
        "moonspec-tasks",
        "moonspec-verify",
        "pr-resolver",
        "queue-moonmind-workflows",
        "remediate-issue",
        "story-reconcile-implementation",
        "tactics-test",
        "update-moonmind",
    }
)

EXPECTED_PRESETS = frozenset(
    {
        "batch-github-workflows",
        "batch-workflows",
        "document-author",
        "document-health-update",
        "document-update-orchestrate",
        "github-issue-breakdown-implement",
        "github-issue-breakdown-orchestrate",
        "github-issue-implement",
        "github-issue-orchestrate",
        "github-issue-search-and-implement",
        "issue-implement-assessment",
        "issue-implement-work-pr",
        "jira-breakdown-implement",
        "jira-breakdown-orchestrate",
        "jira-breakdown",
        "jira-implement",
        "jira-orchestrate",
        "moonspec-orchestrate",
        "pr-review-resolve",
    }
)

# Harness/agent names that must not appear as the addressed invoker in a skill
# trigger description. Descriptions route task intent; naming one harness
# contradicts the single-runtime direction and the epic's model-neutrality
# constraint. "MoonMind" (the product itself) stays allowed.
_HARNESS_NAME_PATTERN = re.compile(
    r"\b(codex|claude code|opencode|gemini)\b", re.IGNORECASE
)

_FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


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


def test_skill_catalog_inventory_matches_audit_baseline():
    actual = frozenset(
        d.name for d in SKILLS_DIR.iterdir() if d.is_dir() and d.name != "_shared"
    )
    assert actual == EXPECTED_SKILLS, (
        f"skill catalog drift: missing={sorted(EXPECTED_SKILLS - actual)} "
        f"extra={sorted(actual - EXPECTED_SKILLS)}"
    )
    assert len(EXPECTED_SKILLS) == 35
    for skill in sorted(EXPECTED_SKILLS):
        assert (SKILLS_DIR / skill / "SKILL.md").is_file(), f"{skill}: no SKILL.md"
        frontmatter = _frontmatter(skill)
        assert str(frontmatter.get("name", "")).strip(), f"{skill}: empty name"
        assert str(frontmatter.get("description", "")).strip(), (
            f"{skill}: empty description"
        )


def test_preset_catalog_inventory_matches_audit_baseline():
    actual = frozenset(p.stem for p in PRESET_DIR.glob("*.yaml"))
    assert actual == EXPECTED_PRESETS, (
        f"preset catalog drift: missing={sorted(EXPECTED_PRESETS - actual)} "
        f"extra={sorted(actual - EXPECTED_PRESETS)}"
    )
    assert len(EXPECTED_PRESETS) == 19
    for slug in sorted(EXPECTED_PRESETS):
        raw = yaml.safe_load((PRESET_DIR / f"{slug}.yaml").read_text(encoding="utf-8"))
        assert isinstance(raw, dict), f"{slug}: preset is not a mapping"
        assert raw.get("slug") == slug, f"{slug}: slug mismatch"
        assert str(raw.get("title", "")).strip(), f"{slug}: empty title"
        assert isinstance(raw.get("steps", []), list) and raw["steps"], (
            f"{slug}: no steps"
        )


async def test_preset_catalog_seeds_through_existing_service(tmp_path):
    async with catalog_service(tmp_path) as service:
        templates = await service.list_templates()
    seeded = {str(item["slug"]) for item in templates}
    missing = {slug for slug in EXPECTED_PRESETS if slug not in seeded}
    assert not missing, f"presets missing after seed: {sorted(missing)}"


def test_skill_trigger_descriptions_are_harness_neutral():
    offenders = {}
    for skill in sorted(EXPECTED_SKILLS):
        description = str(_frontmatter(skill).get("description", ""))
        hit = _HARNESS_NAME_PATTERN.search(description)
        if hit:
            offenders[skill] = hit.group(0)
    assert not offenders, (
        "skill descriptions name a specific invoking harness; reword to describe "
        f"task intent instead: {offenders}"
    )
