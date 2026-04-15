"""Unit tests for task template catalog/save services."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    TaskStepTemplateRecent,
    TaskTemplateReleaseStatus,
    TaskTemplateScopeType,
)
from api_service.services.task_templates.catalog import (
    ExpandOptions,
    TaskTemplateCatalogService,
    TaskTemplateNotFoundError,
)
from api_service.services.task_templates.save import TaskTemplateSaveService

pytestmark = [pytest.mark.asyncio]


@asynccontextmanager
async def template_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/task_template_catalog.db"
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


def _write_seed_template(seed_dir, seed_data: dict) -> None:
    seed_dir.mkdir(exist_ok=True)
    seed_file = seed_dir / f"{seed_data['slug']}.yaml"
    with open(seed_file, "w") as f:
        yaml.dump(seed_data, f)


async def test_create_and_expand_template_deterministic_ids(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            created = await service.create_template(
                slug="pr-check",
                title="PR Check",
                description="Template for PR checks",
                scope="personal",
                scope_ref=str(user_id),
                tags=["quality"],
                inputs_schema=[
                    {
                        "name": "summary",
                        "label": "Summary",
                        "type": "text",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "title": "Review",
                        "instructions": "Review change: {{ inputs.summary }}",
                        "skill": {
                            "id": "auto",
                            "args": {},
                            "requiredCapabilities": ["docker"],
                        },
                    }
                ],
                annotations={},
                required_capabilities=["codex"],
                created_by=user_id,
            )
            assert created["slug"] == "pr-check"

            expanded = await service.expand_template(
                slug="pr-check",
                scope="personal",
                scope_ref=str(user_id),
                version="1.0.0",
                inputs={"summary": "fix failing tests"},
                context={},
                options=ExpandOptions(should_enforce_step_limit=True),
                user_id=user_id,
            )

    assert len(expanded["steps"]) == 1
    assert expanded["steps"][0]["id"].startswith("tpl:pr-check:1.0.0:01:")
    assert "fix failing tests" in expanded["steps"][0]["instructions"]
    assert set(expanded["capabilities"]) >= {"codex", "docker"}
    assert expanded["appliedTemplate"]["slug"] == "pr-check"
    assert expanded["appliedTemplate"]["version"] == "1.0.0"


async def test_template_recents_declares_unique_user_version_constraint() -> None:
    constraint_names = {
        constraint.name
        for constraint in TaskStepTemplateRecent.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_task_template_recent_user_version" in constraint_names


async def test_save_from_task_rejects_secret_patterns(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateSaveService(session)
            with pytest.raises(Exception) as exc:
                await service.save_from_task(
                    scope="personal",
                    scope_ref=str(user_id),
                    title="Leaky",
                    description="bad",
                    steps=[
                        {
                            "instructions": "Use token=secret123 for API call",
                            "skill": {"id": "auto", "args": {}},
                        }
                    ],
                    suggested_inputs=[],
                    tags=[],
                    created_by=user_id,
                )

    assert "Potential secrets detected" in str(exc.value)


async def test_list_templates_with_favorites_and_recents(tmp_path):
    user_id = uuid4()
    user_str = str(user_id)
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.create_template(
                slug="first-template",
                title="First Template",
                description="One",
                scope="personal",
                scope_ref=user_str,
                tags=["alpha"],
                inputs_schema=[],
                steps=[{"instructions": "step one"}],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )
            await service.create_template(
                slug="second-template",
                title="Second Template",
                description="Two",
                scope="personal",
                scope_ref=user_str,
                tags=["beta"],
                inputs_schema=[],
                steps=[{"instructions": "step two"}],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

            await service.set_favorite(
                user_id=user_id,
                slug="second-template",
                scope="personal",
                scope_ref=user_str,
            )
            await service.expand_template(
                slug="second-template",
                scope="personal",
                scope_ref=user_str,
                version="1.0.0",
                inputs={},
                context={},
                options=ExpandOptions(),
                user_id=user_id,
            )

            listed = await service.list_templates(
                scope="personal",
                scope_ref=user_str,
                favorites_only=True,
                user_id=user_id,
            )

    assert len(listed) == 1
    assert listed[0]["slug"] == "second-template"
    assert listed[0]["isFavorite"] is True
    assert listed[0]["recentAppliedAt"] is not None


async def test_save_from_task_marks_favorite_and_recent(tmp_path):
    user_id = uuid4()
    user_str = str(user_id)
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            saver = TaskTemplateSaveService(session)
            await saver.save_from_task(
                scope="personal",
                scope_ref=user_str,
                title="Saved Preset",
                description="Saved from task",
                steps=[{"instructions": "run the checks"}],
                suggested_inputs=[],
                tags=["saved"],
                created_by=user_id,
            )

            catalog = TaskTemplateCatalogService(session)
            listed = await catalog.list_templates(
                scope="personal",
                scope_ref=user_str,
                favorites_only=True,
                user_id=user_id,
            )

    assert len(listed) == 1
    assert listed[0]["slug"] == "saved-preset"
    assert listed[0]["isFavorite"] is True
    assert listed[0]["recentAppliedAt"] is not None


async def test_recents_trimmed_to_latest_five_rows(tmp_path):
    user_id = uuid4()
    user_str = str(user_id)
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            catalog = TaskTemplateCatalogService(session)
            for index in range(1, 7):
                slug = f"preset-{index}"
                await catalog.create_template(
                    slug=slug,
                    title=f"Preset {index}",
                    description="seed",
                    scope="personal",
                    scope_ref=user_str,
                    tags=[],
                    inputs_schema=[],
                    steps=[{"instructions": f"step {index}"}],
                    annotations={},
                    required_capabilities=[],
                    created_by=user_id,
                )
                await catalog.expand_template(
                    slug=slug,
                    scope="personal",
                    scope_ref=user_str,
                    version="1.0.0",
                    inputs={},
                    context={},
                    options=ExpandOptions(),
                    user_id=user_id,
                )

            count = await session.scalar(
                select(func.count())
                .select_from(TaskStepTemplateRecent)
                .where(TaskStepTemplateRecent.user_id == user_id)
            )

    assert count == 5


async def test_release_status_sets_reviewer_fields(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            catalog = TaskTemplateCatalogService(session)
            await catalog.create_template(
                slug="review-target",
                title="Review Target",
                description="To review",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "review me"}],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            reviewed = await catalog.set_release_status(
                slug="review-target",
                scope="global",
                scope_ref=None,
                version="1.0.0",
                release_status=TaskTemplateReleaseStatus.ACTIVE,
                reviewer_id=user_id,
            )

    assert reviewed["releaseStatus"] == "active"
    assert reviewed["reviewedBy"] == str(user_id)
    assert reviewed["reviewedAt"] is not None


async def test_soft_delete_template_marks_inactive(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.create_template(
                slug="to-be-deleted",
                title="Delete Me",
                description="Template for deletion",
                scope="personal",
                scope_ref=str(user_id),
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "Do nothing"}],
                created_by=user_id,
            )

            await service.soft_delete_template(
                slug="to-be-deleted",
                scope="personal",
                scope_ref=str(user_id),
            )

            with pytest.raises(TaskTemplateNotFoundError, match="Template not found."):
                await service._get_template_for_scope(
                    slug="to-be-deleted",
                    scope=TaskTemplateScopeType.PERSONAL,
                    scope_ref=str(user_id),
                    include_inactive=False,
                )

            template = await service._get_template_for_scope(
                slug="to-be-deleted",
                scope=TaskTemplateScopeType.PERSONAL,
                scope_ref=str(user_id),
                include_inactive=True,
            )
            assert template.is_active is False


async def test_soft_delete_template_not_found(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            with pytest.raises(TaskTemplateNotFoundError, match="Template not found."):
                await service.soft_delete_template(
                    slug="does-not-exist",
                    scope="personal",
                    scope_ref=str(user_id),
                )


async def test_import_seed_templates_success(tmp_path):
    seed_dir = tmp_path / "seeds"
    seed_data = {
        "slug": "seed-test",
        "title": "Seed Test",
        "description": "A test seed template",
        "scope": "global",
        "version": "1.0.0",
        "steps": [{"instructions": "seed step"}],
    }
    _write_seed_template(seed_dir, seed_data)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            created_count = await service.import_seed_templates(seed_dir=seed_dir)

            assert created_count == 1

            template = await service._get_template_for_scope(
                slug="seed-test",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "Seed Test"
            assert template.description == "A test seed template"
            assert len(template.versions) == 1
            assert template.versions[0].version == "1.0.0"


async def test_import_seed_templates_skips_existing(tmp_path):
    seed_dir = tmp_path / "seeds"
    seed_data = {
        "slug": "seed-test-conflict",
        "title": "Seed Test Conflict",
        "scope": "global",
        "version": "1.0.0",
        "steps": [{"instructions": "seed step"}],
    }
    _write_seed_template(seed_dir, seed_data)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)

            # First import should create the template
            created_count_first = await service.import_seed_templates(seed_dir=seed_dir)
            assert created_count_first == 1

            # Second import should skip and return 0
            created_count_second = await service.import_seed_templates(
                seed_dir=seed_dir
            )
            assert created_count_second == 0


async def test_seed_catalog_includes_jira_breakdown_preset(tmp_path):
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "task_step_templates"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="jira-breakdown",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "Jira Breakdown"
            assert template.latest_version is not None
            assert [step["skill"]["id"] for step in template.latest_version.steps] == [
                "moonspec-breakdown",
                "jira-issue-creator",
            ]

            expanded = await service.expand_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
                version="1.0.0",
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_project_key": "TOOL",
                    "jira_issue_type": "Story",
                },
                context={},
            )

            assert len(expanded["steps"]) == 2
            assert expanded["steps"][0]["skill"]["id"] == "moonspec-breakdown"
            assert "docs/Designs/RuntimeTypes.md" in expanded["steps"][0]["instructions"]
            assert expanded["steps"][1]["skill"]["id"] == "jira-issue-creator"
            assert "Jira Story issue in project TOOL" in expanded["steps"][1]["instructions"]
            assert "Source Document path" in expanded["steps"][1]["instructions"]


async def test_sync_seed_templates_creates_missing_seed(tmp_path):
    seed_dir = tmp_path / "seeds"
    seed_data = {
        "slug": "moonspec-orchestrate",
        "title": "MoonSpec Orchestrate",
        "description": "Seeded preset",
        "scope": "global",
        "version": "1.0.0",
        "steps": [
            {
                "title": "Specify",
                "instructions": "Use {{ inputs.feature_request }}",
                "skill": {"id": "moonspec-specify", "args": {}},
            }
        ],
        "inputs": [
            {
                "name": "feature_request",
                "label": "Feature Request",
                "type": "markdown",
                "required": True,
            }
        ],
    }
    _write_seed_template(seed_dir, seed_data)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            result = await service.sync_seed_templates(seed_dir=seed_dir)

            assert result.created == 1
            assert result.updated == 0

            template = await service._get_template_for_scope(
                slug="moonspec-orchestrate",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "MoonSpec Orchestrate"
            assert template.latest_version is not None
            assert template.latest_version.steps[0]["skill"]["id"] == "moonspec-specify"


async def test_sync_seed_templates_updates_existing_seed(tmp_path):
    seed_dir = tmp_path / "seeds"
    seed_data = {
        "slug": "moonspec-orchestrate",
        "title": "MoonSpec Orchestrate",
        "description": "Updated seeded preset",
        "scope": "global",
        "version": "1.0.0",
        "steps": [
            {
                "title": "Specify",
                "instructions": "Translate {{ inputs.feature_request }} into spec artifacts.",
                "skill": {"id": "moonspec-specify", "args": {}},
            },
            {
                "title": "Plan",
                "instructions": "Plan the implementation.",
                "skill": {"id": "moonspec-plan", "args": {}},
            },
        ],
        "inputs": [
            {
                "name": "feature_request",
                "label": "Feature Request",
                "type": "markdown",
                "required": True,
            }
        ],
        "annotations": {"sourceSkill": "moonspec-orchestrate"},
    }
    _write_seed_template(seed_dir, seed_data)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.create_template(
                slug="moonspec-orchestrate",
                title="Legacy Preset",
                description="Old preset",
                scope="global",
                scope_ref=None,
                tags=["legacy"],
                inputs_schema=[],
                steps=[{"instructions": "legacy step"}],
                annotations={"sourceSkill": "legacy-orchestrate"},
                required_capabilities=[],
                created_by=None,
                release_status=TaskTemplateReleaseStatus.ACTIVE,
            )

            result = await service.sync_seed_templates(seed_dir=seed_dir)

            assert result.created == 0
            assert result.updated == 1

            template = await service._get_template_for_scope(
                slug="moonspec-orchestrate",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.description == "Updated seeded preset"
            assert template.latest_version is not None
            assert len(template.latest_version.steps) == 2
            assert template.latest_version.steps[0]["skill"]["id"] == "moonspec-specify"
            assert template.latest_version.annotations["sourceSkill"] == "moonspec-orchestrate"
