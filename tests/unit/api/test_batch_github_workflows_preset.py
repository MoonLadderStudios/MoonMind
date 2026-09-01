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
                    "run_verify": True,
                },
            )

    assert expanded["appliedTemplate"]["slug"] == "batch-github-workflows"
    assert expanded["publish"] == {"mode": "none"}
    assert sorted(expanded["capabilities"]) == ["gh", "git"]
    assert len(expanded["steps"]) == 1
    step = expanded["steps"][0]
    assert step["skill"]["requiredCapabilities"] == ["git", "gh"]
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
    assert '--github-issue-range "3142-3150"' in step["instructions"]
    assert (
        '--github-repository "MoonLadderStudios/MoonMind"'
        in step["instructions"]
    )
    assert "only for open Issue objects returned by GitHub" in step["instructions"]
    assert (
        "$MOONMIND_ACTIVE_SKILLS_DIR/batch-github-workflows/bin/batch_workflows.py"
        in step["instructions"]
    )
    assert (
        "--targets artifacts/batch-workflows-targets.json"
        not in step["instructions"]
    )
    assert '--constraints "' not in step["instructions"]
    assert (
        "--constraints-file artifacts/batch-workflows-constraints.txt"
        in step["instructions"]
    )
    assert "constraints" not in orchestration["sharedInputs"]
    assert "constraints" not in expanded["appliedTemplate"]["inputs"]


async def test_batch_github_workflows_drops_constraints_input(tmp_path):
    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            template = await service.get_template(
                slug="batch-github-workflows",
                scope="global",
                scope_ref=None,
            )

            expanded = await service.expand_template(
                slug="batch-github-workflows",
                scope="global",
                scope_ref=None,
                inputs={
                    "issue_range": "3142-3150",
                    "run_ref": "preset:github-issue-implement",
                    "repository": "MoonLadderStudios/MoonMind",
                    "constraints": "Stale client value",
                },
            )

    assert "constraints" not in template["inputSchema"]["properties"]
    assert "constraints" not in template["uiSchema"]
    assert "constraints" not in template["defaults"]
    assert all(
        definition["name"] != "constraints" for definition in template["inputs"]
    )
    for binding in template["annotations"]["bindings"].values():
        assert "constraints" not in binding

    assert "constraints" not in expanded["appliedTemplate"]["inputs"]
    step = expanded["steps"][0]
    assert '--constraints "' not in step["instructions"]
    assert "Stale client value" not in step["instructions"]
    assert "constraints" not in step["batchOrchestration"]["sharedInputs"]


async def test_batch_github_workflows_forwards_step_instruction_constraints(
    tmp_path,
):
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
                    "run_ref": "preset:github-issue-implement",
                    "repository": "MoonLadderStudios/MoonMind",
                },
            )

    instructions = expanded["steps"][0]["instructions"]
    assert (
        "--constraints-file artifacts/batch-workflows-constraints.txt"
        in instructions
    )
    assert "artifacts/batch-workflows-constraints.txt" in instructions
    assert "this Instructions box is the" in instructions


async def test_batch_github_workflows_uses_repository_context(tmp_path):
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
                    "run_ref": "preset:github-issue-implement",
                },
                context={"repository": "MoonLadderStudios/MoonMind"},
            )

    source = expanded["steps"][0]["batchOrchestration"]["source"]
    assert source["githubIssueRange"]["repository"] == (
        "MoonLadderStudios/MoonMind"
    )
    assert expanded["steps"][0]["skill"]["id"] == "batch-github-workflows"
