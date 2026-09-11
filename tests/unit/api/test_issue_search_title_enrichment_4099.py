"""Boundary tests for MoonLadderStudios/MoonMind#4099.

Pins the opt-in title-enrichment declaration on the
``github-issue-search-and-implement`` seed preset, its preservation through
catalog expansion into the admitted plan snapshot, and the repaired
production ``SetTitle`` workflow registration.
"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio import workflow

from api_service.db.models import Base, Preset, PresetScopeType
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.workflows.executions.preset_expansion import (
    expand_preset_for_child_run,
)
from moonmind.workflows.temporal.boundary_inventory import (
    iter_temporal_boundary_contracts,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

pytestmark = [pytest.mark.asyncio]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESET_DIR = _REPO_ROOT / "api_service" / "data" / "presets"
_SEARCH_PRESET = "github-issue-search-and-implement"


@asynccontextmanager
async def _catalog_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/title_enrichment_4099.db",
        future=True,
    )
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


def _seed_dir(tmp_path) -> Path:
    seed_dir = tmp_path / "presets"
    shutil.copytree(_PRESET_DIR, seed_dir)
    return seed_dir


async def _seeded_annotations(session, tmp_path) -> dict[str, dict]:
    service = PresetCatalogService(session)
    await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
    result = await session.execute(
        select(Preset).where(
            Preset.scope_type == PresetScopeType.GLOBAL,
            Preset.scope_ref.is_(None),
        )
    )
    return {
        template.slug: template.annotations or {}
        for template in result.scalars().all()
    }


def test_search_preset_yaml_declares_opt_in_title_enrichment() -> None:
    document = yaml.safe_load(
        (_PRESET_DIR / f"{_SEARCH_PRESET}.yaml").read_text(encoding="utf-8")
    )
    enrichment = (document.get("annotations") or {}).get("titleEnrichment")
    assert enrichment == {
        "enabled": True,
        "provider": "github",
        "sourceTool": "github.load_issue_preset_brief",
        "targetStyle": "base-colon-hash",
    }
    # The preset label is the frozen base for "<base>: #<number>".
    assert document["title"] == "GitHub Issue Search and Implement"


async def test_only_search_preset_opts_into_title_enrichment(tmp_path) -> None:
    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            annotations_by_slug = await _seeded_annotations(session, tmp_path)

    enabled = sorted(
        slug
        for slug, annotations in annotations_by_slug.items()
        if isinstance(annotations.get("titleEnrichment"), dict)
        and annotations["titleEnrichment"].get("enabled") is True
    )
    assert enabled == [_SEARCH_PRESET]


async def test_expansion_preserves_title_enrichment_in_plan_snapshot(
    tmp_path,
) -> None:
    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            expanded = await service.expand_template(
                slug=_SEARCH_PRESET,
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )
            assert expanded["titleEnrichment"] == {
                "enabled": True,
                "provider": "github",
                "sourceTool": "github.load_issue_preset_brief",
                "targetStyle": "base-colon-hash",
                "presetTitle": "GitHub Issue Search and Implement",
                "presetSlug": _SEARCH_PRESET,
            }

            parameters = await expand_preset_for_child_run(
                session=session,
                initial_parameters={
                    "workflow": {
                        "taskTemplate": {"slug": _SEARCH_PRESET, "scope": "global"},
                        "inputs": {},
                    }
                },
            )
    task_payload = parameters["workflow"]
    assert task_payload["titleEnrichment"] == {
        "enabled": True,
        "provider": "github",
        "sourceTool": "github.load_issue_preset_brief",
        "targetStyle": "base-colon-hash",
        "presetTitle": "GitHub Issue Search and Implement",
        "presetSlug": _SEARCH_PRESET,
    }
    # Metadata-only: steps, not identity/plan/branch/publication, carry it.
    assert isinstance(task_payload["steps"], list) and task_payload["steps"]


def test_production_workflow_registers_canonical_set_title_update() -> None:
    definition = workflow._Definition.must_from_class(MoonMindRunWorkflow)
    assert definition.name == "MoonMind.UserWorkflow"
    updates = dict(getattr(definition, "updates", {}))
    assert "SetTitle" in updates


def test_boundary_inventory_models_set_title_update() -> None:
    contracts = {
        (contract.kind, contract.name, contract.owner)
        for contract in iter_temporal_boundary_contracts()
    }
    assert ("update", "SetTitle", "MoonMind.UserWorkflow") in contracts
