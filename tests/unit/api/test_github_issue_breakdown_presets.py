"""Catalog-boundary tests for MM-1063 GitHub Issue breakdown composite presets."""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, Preset, PresetScopeType
from api_service.services.presets.catalog import PresetCatalogService

pytestmark = [pytest.mark.asyncio]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESET_DIR = _REPO_ROOT / "api_service" / "data" / "presets"


@asynccontextmanager
async def _catalog_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/github_issue_breakdown.db"
    engine = create_async_engine(db_url, future=True)
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_session_maker
    finally:
        await engine.dispose()


def _seed_dir(tmp_path) -> Path:
    seed_dir = tmp_path / "presets"
    seed_dir.mkdir()
    for filename in (
        "github-issue-breakdown-implement.yaml",
        "github-issue-breakdown-orchestrate.yaml",
    ):
        shutil.copy(_PRESET_DIR / filename, seed_dir / filename)
    return seed_dir


async def _load_preset(session, slug: str) -> Preset:
    result = await session.execute(
        select(Preset).where(
            Preset.slug == slug,
            Preset.scope_type == PresetScopeType.GLOBAL,
            Preset.scope_ref.is_(None),
        )
    )
    return result.scalar_one()


@pytest.mark.parametrize(
    ("slug", "title", "workflow_skill"),
    [
        (
            "github-issue-breakdown-implement",
            "Breakdown and GitHub Issue Implement",
            "story.create_github_issue_implement_workflows",
        ),
        (
            "github-issue-breakdown-orchestrate",
            "Breakdown and GitHub Issue Orchestrate",
            "story.create_github_issue_orchestrate_workflows",
        ),
    ],
)
async def test_github_issue_breakdown_seed_creates_issues_and_workflows(
    tmp_path,
    slug: str,
    title: str,
    workflow_skill: str,
):
    async with _catalog_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            template = await _load_preset(session, slug)

    assert template.title == title
    assert sorted(template.required_capabilities) == ["gh", "git"]
    assert [item["name"] for item in template.inputs_schema] == [
        "feature_request",
        "source_design_path",
        "github_repository",
        "github_labels",
        "publish_mode",
    ]
    assert [
        (step.get("skill") or step.get("tool"))["id"]
        for step in template.steps
    ] == [
        "moonspec-breakdown",
        "story-reconcile-implementation",
        "story.create_github_issues",
        workflow_skill,
    ]

    create_step = template.steps[2]
    assert create_step["storyOutput"] == {
        "mode": "github",
        "fallback": "fail",
        "github": {
            "repository": "{{ inputs.github_repository }}",
            "labels": "{{ inputs.github_labels }}",
            "dependencyMode": "none",
        },
    }
    downstream_step = template.steps[3]
    assert "workflow execution" in downstream_step["instructions"]
    assert "MoonMind task" not in downstream_step["instructions"]
    assert downstream_step["githubOrchestration"]["task"]["publish"] == {
        "mode": "pr",
        "mergeAutomation": {
            "enabled": "{{ inputs.publish_mode == 'pr_with_merge_automation' }}"
        },
    }


async def test_github_issue_breakdown_implement_expands_downstream_contract(tmp_path):
    async with _catalog_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            expanded = await service.expand_template(
                slug="github-issue-breakdown-implement",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "Split MM-1063 work.",
                    "source_design_path": "",
                    "github_repository": "MoonLadderStudios/MoonMind",
                    "github_labels": "MM-1063",
                    "publish_mode": "pr",
                },
                context={"targetRuntime": "codex"},
            )

    steps = expanded["steps"]
    assert steps[2]["skill"]["id"] == "story.create_github_issues"
    assert steps[2]["storyOutput"]["github"]["repository"] == (
        "MoonLadderStudios/MoonMind"
    )
    assert steps[3]["skill"]["id"] == "story.create_github_issue_implement_workflows"
    assert steps[3]["githubOrchestration"]["task"]["repository"] == (
        "MoonLadderStudios/MoonMind"
    )
    assert steps[3]["githubOrchestration"]["task"]["runtime"]["mode"] == "codex"
