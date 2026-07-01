"""Catalog-boundary tests for the MM-1062 ``batch-workflows`` seed preset.

These exercise the real preset validation + expansion path (the adapter boundary
for presets): the seed YAML must validate, expose the documented batch contract
(Jira project/status source, run capability selector, default issue bindings,
runtimeInheritance=caller, and a shared advanced publish policy),
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

            schema = annotations["inputSchema"]
            assert schema["required"] == [
                "jira_project_key",
                "jira_status",
                "run_ref",
            ]
            for name in (
                "jira_project_key",
                "jira_status",
                "run_ref",
                "max_workflows",
                "constraints",
                "additional_jql",
                "repository",
                "publish_mode",
            ):
                assert name in schema["properties"]
            removed = {
                "source_kind",
                "jira_board_id",
                "jira_column",
                "github_repository",
                "target_preset_slug",
                "target_preset_scope",
                "target_preset_scope_ref",
                "sort",
            }
            assert removed.isdisjoint(schema["properties"])
            assert schema["properties"]["run_ref"]["enum"] == [
                "skill:jira-verify",
                "preset:jira-implement",
            ]

            assert schema["properties"]["publish_mode"]["enum"] == [
                "none",
                "branch",
                "pr",
            ]

            # UI schema uses only registered widgets or advanced flags.
            ui_schema = annotations["uiSchema"]
            assert ui_schema["run_ref"]["widget"] == "select"
            assert ui_schema["constraints"] == {"widget": "textarea", "advanced": True}
            assert ui_schema["additional_jql"] == {
                "widget": "textarea",
                "advanced": True,
            }
            assert ui_schema["repository"]["advanced"] is True
            assert ui_schema["publish_mode"] == {"widget": "select", "advanced": True}

            # Default issue bindings for the known issue presets.
            bindings = annotations["bindings"]
            assert (
                bindings["skill:jira-verify"]["jira_issue"]
                == "{{ target.jiraIssue }}"
            )
            assert (
                bindings["skill:jira-verify"]["jira_issue_key"]
                == "{{ target.jiraIssue.key }}"
            )
            assert (
                bindings["skill:jira-verify"]["repository"]
                == "{{ target.repository }}"
            )
            assert (
                bindings["preset:jira-implement"]["jira_issue"]
                == "{{ target.jiraIssue }}"
            )
            assert (
                bindings["preset:jira-implement"]["jira_issue_key"]
                == "{{ target.jiraIssue.key }}"
            )
            assert (
                bindings["preset:github-issue-implement"]["github_issue"]
                == "{{ target.githubIssue }}"
            )
            assert (
                bindings["preset:github-issue-implement"]["github_issue_ref"]
                == "{{ target.githubIssue.repository }}#{{ target.githubIssue.number }}"
            )

            # Runtime inheritance directive.
            assert annotations["runtimeInheritance"] == "caller"

            exposed_inputs = {item["name"] for item in template.inputs_schema}
            assert exposed_inputs == set(schema["properties"])

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
                    "jira_project_key": "MM",
                    "jira_status": "In Progress",
                    "run_ref": "skill:jira-verify",
                    "publish_mode": "none",
                    "constraints": "Be careful",
                    "additional_jql": "assignee = currentUser()",
                    "repository": "MoonLadderStudios/MoonMind",
                    "max_workflows": "10",
                },
            )

            steps = expanded["steps"]
            assert len(steps) == 1
            step = steps[0]
            assert step["skill"]["id"] == "batch-workflows"

            orchestration = step["batchOrchestration"]
            assert orchestration["source"]["kind"] == "jira_status"
            assert orchestration["source"]["jiraStatus"]["projectKey"] == "MM"
            assert orchestration["source"]["jiraStatus"]["status"] == "In Progress"
            assert (
                orchestration["source"]["jiraStatus"]["additionalJql"]
                == "assignee = currentUser()"
            )
            assert (
                orchestration["source"]["jiraStatus"]["repository"]
                == "MoonLadderStudios/MoonMind"
            )
            assert orchestration["target"]["runRef"] == "skill:jira-verify"
            assert orchestration["publish"]["mode"] == "none"
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


async def test_batch_workflows_expands_repository_from_context_when_not_provided(tmp_path):
    async with _catalog_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            expanded = await service.expand_template(
                slug="batch-workflows",
                scope="global",
                scope_ref=None,
                inputs={
                    "jira_project_key": "MM",
                    "jira_status": "In Progress",
                    "run_ref": "skill:jira-verify",
                    "publish_mode": "none",
                    "constraints": "Be careful",
                    "additional_jql": "assignee = currentUser()",
                    "max_workflows": "10",
                },
                context={"repository": "MoonLadderStudios/MoonMind"},
            )

            orchestration = expanded["steps"][0]["batchOrchestration"]
            assert (
                orchestration["source"]["jiraStatus"]["repository"]
                == "MoonLadderStudios/MoonMind"
            )


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
                    "jira_project_key": "MM",
                    "jira_status": "In Progress",
                    "run_ref": "preset:jira-implement",
                    "target_preset_version": "1.1.0",
                    "publish_mode": "branch",
                },
            )

    orchestration = expanded["steps"][0]["batchOrchestration"]
    assert orchestration["target"]["runRef"] == "preset:jira-implement"
    assert "targetPreset" not in orchestration
