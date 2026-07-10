"""Catalog-boundary tests for the Batch GitHub Workflows seed preset."""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.presets.catalog import PresetCatalogService

pytestmark = [pytest.mark.asyncio]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESET_PATH = (
    _REPO_ROOT / "api_service/data/presets/batch-github-workflows.yaml"
)


@asynccontextmanager
async def _catalog_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/batch_github_workflows.db",
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
    seed_dir.mkdir()
    shutil.copy(_PRESET_PATH, seed_dir / _PRESET_PATH.name)
    return seed_dir


async def test_batch_github_workflows_seed_and_expansion_contract(tmp_path):
    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            expanded = await service.expand_template(
                slug="batch-github-workflows",
                scope="global",
                scope_ref=None,
                inputs={
                    "issue_range": "3142-3150",
                    "run_ref": "preset:github-issue-orchestrate",
                    "repository": "MoonLadderStudios/MoonMind",
                    "publish_mode": "pr_with_merge_automation",
                    "max_workflows": "10",
                    "constraints": "Keep changes focused",
                    "run_verify": True,
                },
            )

    assert expanded["title"] == "Batch GitHub Workflows"
    assert expanded["publish"] == {"mode": "none"}
    assert sorted(expanded["capabilities"]) == ["gh", "git"]
    assert len(expanded["steps"]) == 1
    step = expanded["steps"][0]
    orchestration = step["batchOrchestration"]
    assert orchestration["source"] == {
        "kind": "github_issue_range",
        "githubIssueRange": {
            "range": "3142-3150",
            "repository": "MoonLadderStudios/MoonMind",
        },
    }
    assert orchestration["target"]["runRef"] == (
        "preset:github-issue-orchestrate"
    )
    assert orchestration["publish"]["mode"] == "pr_with_merge_automation"
    assert orchestration["runtime"]["inherit"] == "caller"
    assert "--run-verify" in step["instructions"]
