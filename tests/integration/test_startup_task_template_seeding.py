from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload, sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base, TaskStepTemplate, TaskTemplateScopeType
from api_service.main import startup_event

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.asyncio
async def test_startup_seeds_default_task_templates(disabled_env_keys, tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with (
        patch("api_service.main._initialize_embedding_model"),
        patch("api_service.main._initialize_vector_store"),
        patch("api_service.main._initialize_contexts"),
        patch("api_service.main._load_or_create_vector_index"),
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(TaskStepTemplate)
            .where(
                TaskStepTemplate.slug == "moonspec-orchestrate",
                TaskStepTemplate.scope_type == TaskTemplateScopeType.GLOBAL,
                TaskStepTemplate.scope_ref.is_(None),
            )
            .options(selectinload(TaskStepTemplate.latest_version))
        )
        template = result.scalar_one_or_none()
        assert template is not None
        assert template.latest_version is not None
        assert template.latest_version.release_status.value == "active"
        seeded_skill_ids = [
            step["skill"]["id"] for step in template.latest_version.steps
        ]
        assert seeded_skill_ids == [
            "moonspec-specify",
            "moonspec-plan",
            "moonspec-tasks",
            "moonspec-align",
            "moonspec-implement",
            "moonspec-verify",
            "auto",
        ]
        seeded_step_titles = [step["title"] for step in template.latest_version.steps]
        assert "Classify request and resume point" not in seeded_step_titles
        assert "Split broad designs when needed" not in seeded_step_titles
        assert seeded_step_titles[0] == "Create or select Moon Spec"
        assert "moonspec-breakdown" not in seeded_skill_ids
        assert "speckit-analyze" not in seeded_skill_ids
        specify_step = template.latest_version.steps[0]
        assert "Before running moonspec-specify, validate" in specify_step[
            "instructions"
        ]
        assert "stop immediately" in specify_step["instructions"]
        assert "routed through moonspec-breakdown" in specify_step["instructions"]
        tasks_step = next(
            step
            for step in template.latest_version.steps
            if step["title"] == "Generate TDD task breakdown"
        )
        assert "/moonspec-verify" in tasks_step["instructions"]
        assert "/speckit.verify" not in tasks_step["instructions"]

        result = await session.execute(
            select(TaskStepTemplate)
            .where(
                TaskStepTemplate.slug == "jira-breakdown",
                TaskStepTemplate.scope_type == TaskTemplateScopeType.GLOBAL,
                TaskStepTemplate.scope_ref.is_(None),
            )
            .options(selectinload(TaskStepTemplate.latest_version))
        )
        jira_template = result.scalar_one_or_none()
        assert jira_template is not None
        assert jira_template.latest_version is not None
        assert [
            step["skill"]["id"] for step in jira_template.latest_version.steps
        ] == ["moonspec-breakdown", "story.create_jira_issues"]

        result = await session.execute(
            select(TaskStepTemplate)
            .where(
                TaskStepTemplate.slug == "jira-orchestrate",
                TaskStepTemplate.scope_type == TaskTemplateScopeType.GLOBAL,
                TaskStepTemplate.scope_ref.is_(None),
            )
            .options(selectinload(TaskStepTemplate.latest_version))
        )
        jira_orchestrate_template = result.scalar_one_or_none()
        assert jira_orchestrate_template is not None
        assert jira_orchestrate_template.latest_version is not None
        jira_orchestrate_steps = [
            step["skill"]["id"]
            for step in jira_orchestrate_template.latest_version.steps
        ]
        assert jira_orchestrate_steps[0] == "jira-issue-updater"
        assert "moonspec-implement" in jira_orchestrate_steps
        assert "moonspec-verify" in jira_orchestrate_steps
        assert jira_orchestrate_steps[-2] == "jira-issue-updater"
        pr_step = next(
            step
            for step in jira_orchestrate_template.latest_version.steps
            if step["title"] == "Create pull request"
        )
        assert "pull request" in pr_step["instructions"]
        assert "task.publish.mode=none" in pr_step["instructions"]
        assert "artifacts/jira-orchestrate-pr.json" in pr_step["instructions"]
        code_review_step = next(
            step
            for step in jira_orchestrate_template.latest_version.steps
            if step["title"] == "Move Jira issue to Code Review"
        )
        assert "Code Review" in code_review_step["instructions"]
        assert "pull_request_url" in code_review_step["instructions"]
