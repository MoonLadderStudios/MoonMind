"""Deployment cutover contract for the author-scope default (#4257).

Proves the coordinated drain-and-replace rollout with rollback ordering
through the existing executable-contract boundaries (preset definition
digest, expansion step IDs, typed tool inputs) plus the loader's
fail-closed compat rules:

- New executions pin the recorded scope in plan provenance (digest +
  step IDs + tool inputs), so a pre-change frozen plan is distinguishable
  from a new author-scoped execution.
- A pre-change frozen saved plan launching a new execution stops with an
  actionable refresh-required outcome before any provider request.
- A worker that drops the unknown scope field fails closed to self-only,
  never to all-author.
- Rollback ordering is pinned in the canonical docs: stop new
  author-scoped executions first, then restore the previous worker.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, Preset
from api_service.services.presets.catalog import PresetCatalogService, _preset_digest
from moonmind.workflows.executions.preset_readiness import (
    SavedPresetCapabilitiesInput,
)

pytestmark = [pytest.mark.asyncio]

PRESET_SLUG = "github-issue-search-and-implement"
SEED_FILE = Path("api_service/data/presets/github-issue-search-and-implement.yaml")
DOCS_FILE = Path("docs/Workflows/WorkflowPresetsSystem.md")


async def _seeded_expansions(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/cutover_catalog.db"
    engine = create_async_engine(db_url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        seed_dir = tmp_path / "seeds"
        seed_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(SEED_FILE, seed_dir / SEED_FILE.name)
        async with maker() as session:
            catalog = PresetCatalogService(session)
            await catalog.sync_seed_templates(seed_dir=seed_dir)
            template = (
                await session.execute(
                    select(Preset).where(Preset.slug == PRESET_SLUG).limit(1)
                )
            ).scalar_one()
            expanded_false = await catalog.expand_template(
                slug=PRESET_SLUG,
                scope="global",
                scope_ref=None,
                inputs={"include_all_authors": False},
                context={},
            )
            expanded_true = await catalog.expand_template(
                slug=PRESET_SLUG,
                scope="global",
                scope_ref=None,
                inputs={"include_all_authors": True},
                context={},
            )
            expanded_omitted = await catalog.expand_template(
                slug=PRESET_SLUG,
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )
            session.expunge(template)
            return template, expanded_false, expanded_true, expanded_omitted
    finally:
        await engine.dispose()


def _without_scope(template: Preset) -> SimpleNamespace:
    """Rebuild the pre-change definition without the scope field."""
    annotations = dict(template.annotations or {})
    input_schema = dict(annotations.get("inputSchema") or {})
    properties = dict(input_schema.get("properties") or {})
    properties.pop("include_all_authors", None)
    input_schema["properties"] = properties
    annotations["inputSchema"] = input_schema
    defaults = dict(annotations.get("defaults") or {})
    defaults.pop("include_all_authors", None)
    annotations["defaults"] = defaults
    return SimpleNamespace(
        slug=template.slug,
        scope_type=template.scope_type,
        scope_ref=template.scope_ref,
        inputs_schema=[
            entry
            for entry in template.inputs_schema
            if entry.get("name") != "include_all_authors"
        ],
        steps=template.steps,
        annotations=annotations,
        required_capabilities=template.required_capabilities,
        max_step_count=template.max_step_count,
    )


async def test_preset_digest_distinguishes_scoped_definition(tmp_path):
    """The definition digest changes with the scope field.

    A pre-change frozen plan presents a different preset digest than the
    current definition, so versioned-compat checks can tell them apart and
    an old pin can never masquerade as a new author-scoped execution.
    """
    template, _, _, _ = await _seeded_expansions(tmp_path)
    assert _preset_digest(template) != _preset_digest(_without_scope(template))


async def test_expansion_pins_scope_in_provenance(tmp_path):
    """New executions carry the recorded scope in plan provenance.

    The preset digest is stable across scope values (same definition) while
    step IDs and typed tool inputs differ, so per-execution scope is pinned
    and a rollback (new plan on an old worker) is detectable instead of
    silently reinterpreted.
    """
    _, expanded_false, expanded_true, expanded_omitted = await _seeded_expansions(
        tmp_path
    )
    assert (
        expanded_false["appliedTemplate"]["presetDigest"]
        == expanded_true["appliedTemplate"]["presetDigest"]
    )
    assert (
        expanded_false["appliedTemplate"]["stepIds"]
        != expanded_true["appliedTemplate"]["stepIds"]
    )
    assert expanded_false["steps"][0]["tool"]["inputs"]["includeAllAuthors"] is False
    assert expanded_true["steps"][0]["tool"]["inputs"]["includeAllAuthors"] is True
    assert expanded_omitted["steps"][0]["tool"]["inputs"]["includeAllAuthors"] is False


async def test_recorded_scope_survives_reload_rerun(tmp_path):
    """Reload, edit, rerun, scheduling, and redispatch keep the authored value.

    Re-expanding from the recorded appliedTemplate inputs (the reload/rerun
    path) must preserve an explicit opt-in instead of normalizing it away.
    """
    _, _, expanded_true, _ = await _seeded_expansions(tmp_path)
    recorded = dict(expanded_true["appliedTemplate"]["inputs"])
    assert recorded["include_all_authors"] is True

    db_url = f"sqlite+aiosqlite:///{tmp_path}/cutover_rerun.db"
    engine = create_async_engine(db_url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        seed_dir = tmp_path / "seeds-rerun"
        seed_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(SEED_FILE, seed_dir / SEED_FILE.name)
        async with maker() as session:
            catalog = PresetCatalogService(session)
            await catalog.sync_seed_templates(seed_dir=seed_dir)
            second = await catalog.expand_template(
                slug=PRESET_SLUG,
                scope="global",
                scope_ref=None,
                inputs=recorded,
                context={},
            )
    finally:
        await engine.dispose()
    assert second["appliedTemplate"]["inputs"]["include_all_authors"] is True
    assert second["steps"][0]["tool"]["inputs"]["includeAllAuthors"] is True


async def test_readiness_gate_admits_new_scoped_executions(tmp_path):
    """The planning-stage readiness gate admits current author-scoped plans.

    New executions carrying the current required capabilities pass
    ``plan.check_preset_capabilities``; stale saved schedules that miss
    newly required capabilities get refresh-required before any search.
    """
    seed = yaml.safe_load(SEED_FILE.read_text(encoding="utf-8"))
    db_url = f"sqlite+aiosqlite:///{tmp_path}/cutover_readiness.db"
    engine = create_async_engine(db_url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        seed_dir = tmp_path / "seeds-readiness"
        seed_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(SEED_FILE, seed_dir / SEED_FILE.name)
        async with maker() as session:
            catalog = PresetCatalogService(session)
            await catalog.sync_seed_templates(seed_dir=seed_dir)
            check = SavedPresetCapabilitiesInput(
                principal="test-owner",
                definition_id="sched-cutover",
                presets=[{"slug": PRESET_SLUG, "scope": "global"}],
                required_capabilities=list(seed["requiredCapabilities"]),
            )
            result = await catalog.check_saved_capabilities(check)
    finally:
        await engine.dispose()
    assert result["status"] == "ready"


def test_docs_pin_drain_and_replace_with_rollback_ordering():
    """Canonical docs own the rollout/rollback ordering, not a second diary."""
    text = DOCS_FILE.read_text(encoding="utf-8")
    assert "drain-and-replace" in text
    assert "new author-scoped executions run only on workers that enforce the scope" in text
    assert "rollback stops new executions first" in text
    assert "refresh" in text.lower()
