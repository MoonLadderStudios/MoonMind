"""Catalog-boundary tests for the MM-885 ``batch-workflows`` seed preset.

These exercise the real preset validation + expansion path (the adapter boundary
for presets): the seed YAML must validate, expose the documented batch contract
(source discriminator, both source field sets, target-preset selector, default
issue bindings, runtimeInheritance=caller, and a single shared publish policy),
and expand into an orchestration step that queues child workflows.
"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, Preset, PresetScopeType
from api_service.services.presets.catalog import (
    PresetCatalogService,
)

pytestmark = [pytest.mark.asyncio]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESET_PATH = _REPO_ROOT / "api_service" / "data" / "presets" / "batch-workflows.yaml"


@asynccontextmanager
async def _catalog_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/batch_workflows.db"
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
    shutil.copy(_PRESET_PATH, seed_dir / "batch-workflows.yaml")
    return seed_dir


async def _load_preset(session) -> Preset:
    result = await session.execute(
        select(Preset)
        .where(
            Preset.slug == "batch-workflows",
            Preset.scope_type == PresetScopeType.GLOBAL,
            Preset.scope_ref.is_(None),
        )
    )
    template = result.scalar_one()
    return template


async def test_batch_workflows_seed_validates_and_exposes_batch_contract(tmp_path):
    async with _catalog_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            template = await _load_preset(session)
            annotations = template.annotations or {}

            assert template.title == "Batch Workflows"
            assert template.scope_type is PresetScopeType.GLOBAL

            # Source discriminator with both options.
            schema = annotations["inputSchema"]
            assert schema["properties"]["source_kind"]["enum"] == [
                "jira_board_column",
                "github_repo_issues",
            ]
            assert "source_kind" in schema["required"]

            # Jira board-column source field set.
            for name in (
                "jira_board_id",
                "jira_column",
                "jira_label_filter",
                "jira_issue_type_filter",
                "jira_assignee_filter",
            ):
                assert name in schema["properties"]
            # GitHub repo issues source field set.
            for name in (
                "github_repository",
                "github_issue_state",
                "github_label_filter",
                "github_assignee_filter",
                "github_milestone_filter",
                "github_search_query",
            ):
                assert name in schema["properties"]

            # Target preset selector (slug/scope/scopeRef).
            for name in (
                "target_preset_slug",
                "target_preset_scope",
                "target_preset_scope_ref",
            ):
                assert name in schema["properties"]
            assert "target_preset_version" not in schema["properties"]
            assert "target_preset_version" not in schema["required"]

            # Single shared publish policy normalized around none/branch/pr.
            assert schema["properties"]["publish_mode"]["enum"] == [
                "none",
                "branch",
                "pr",
            ]

            # Cascading board -> column discriminator wiring in the UI schema.
            ui_schema = annotations["uiSchema"]
            assert ui_schema["source_kind"]["widget"] == "discriminator"
            assert ui_schema["jira_column"]["dependsOn"] == "jira_board_id"
            assert ui_schema["target_preset_slug"]["highlightCompatible"] == [
                "jira-implement",
                "github-issue-implement",
            ]

            # Default issue bindings for the known issue presets.
            bindings = annotations["bindings"]
            assert bindings["jira-implement"]["jira_issue"] == "{{ target.jiraIssue }}"
            assert (
                bindings["jira-implement"]["jira_issue_key"]
                == "{{ target.jiraIssue.key }}"
            )
            assert (
                bindings["github-issue-implement"]["github_issue"]
                == "{{ target.githubIssue }}"
            )
            assert (
                bindings["github-issue-implement"]["github_issue_ref"]
                == "{{ target.githubIssue.repository }}#{{ target.githubIssue.number }}"
            )

            # Runtime inheritance directive.
            assert annotations["runtimeInheritance"] == "caller"

            # The board input keeps the existing jira_board input type.
            board_input = next(
                item
                for item in template.inputs_schema
                if item["name"] == "jira_board_id"
            )
            assert board_input["type"] == "jira_board"

            # Orchestration step references the batch-workflows skill.
            assert len(template.steps) == 1
            step = template.steps[0]
            assert step["skill"]["id"] == "batch-workflows"
            # The parent only orchestrates/queues; it must not hard-require the
            # jira capability or GitHub-only batches would be blocked at launch
            # in environments without a trusted Jira integration. Per-target
            # jira readiness is enforced on the child workflows instead.
            assert sorted(step["skill"]["requiredCapabilities"]) == [
                "gh",
                "git",
            ]


async def test_batch_workflows_expands_orchestration_step(tmp_path):
    async with _catalog_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            expanded = await service.expand_template(
                slug="batch-workflows",
                scope="global",
                scope_ref=None,
                inputs={
                    "source_kind": "jira_board_column",
                    "jira_board_id": "42",
                    "jira_column": "In Progress",
                    "target_preset_slug": "jira-implement",
                    "publish_mode": "pr",
                    "constraints": "Be careful",
                    "max_workflows": "10",
                },
            )

            steps = expanded["steps"]
            assert len(steps) == 1
            step = steps[0]
            assert step["skill"]["id"] == "batch-workflows"

            orchestration = step["batchOrchestration"]
            assert orchestration["source"]["kind"] == "jira_board_column"
            assert orchestration["source"]["jiraBoardColumn"]["boardId"] == "42"
            assert orchestration["source"]["jiraBoardColumn"]["column"] == "In Progress"
            assert orchestration["targetPreset"]["slug"] == "jira-implement"
            assert "version" not in orchestration["targetPreset"]
            assert orchestration["publish"]["mode"] == "pr"
            # Every child inherits the caller runtime.
            assert orchestration["runtime"]["inherit"] == "caller"
            assert orchestration["maxWorkflows"] == "10"
            assert orchestration["sharedInputs"]["constraints"] == "Be careful"
            assert (
                orchestration["summaryArtifact"]
                == "artifacts/batch-workflows-result.json"
            )

            # Parent records a summary artifact that links child workflows.
            assert "artifacts/batch-workflows-result.json" in step["instructions"]
            assert "runtimeInheritance" in step["instructions"]

            assert "git" in expanded["capabilities"]
            assert "gh" in expanded["capabilities"]
            # GitHub-only batches must not be gated on Jira readiness, so the
            # parent preset no longer advertises the jira capability.
            assert "jira" not in expanded["capabilities"]


async def test_batch_workflows_ignores_removed_target_preset_version_input(tmp_path):
    async with _catalog_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            expanded = await service.expand_template(
                slug="batch-workflows",
                scope="global",
                scope_ref=None,
                inputs={
                    "source_kind": "jira_board_column",
                    "jira_board_id": "42",
                    "jira_column": "In Progress",
                    "target_preset_slug": "jira-implement",
                    "target_preset_version": "1.1.0",
                    "publish_mode": "pr",
                },
            )

    orchestration = expanded["steps"][0]["batchOrchestration"]
    assert orchestration["targetPreset"]["slug"] == "jira-implement"
    assert orchestration["targetPreset"]["scope"] == "global"
    assert "version" not in orchestration["targetPreset"]
