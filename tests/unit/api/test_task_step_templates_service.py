"""Unit tests for task template catalog/save services."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload, sessionmaker

from api_service.db.models import (
    Base,
    TaskStepTemplate,
    TaskStepTemplateRecent,
    TaskTemplateReleaseStatus,
    TaskTemplateScopeType,
)
from api_service.services.task_templates.catalog import (
    ExpandOptions,
    TaskTemplateCatalogService,
    TaskTemplateNotFoundError,
    TaskTemplateValidationError,
)
from api_service.services.task_templates.save import TaskTemplateSaveService
from moonmind.config.settings import settings

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

@pytest.mark.parametrize("slug", ["jira-orchestrate", "moonspec-orchestrate"])
async def test_expand_template_normalizes_legacy_orchestrate_mode_to_runtime(
    tmp_path, slug: str
):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.create_template(
                slug=slug,
                title=slug,
                description="Legacy orchestrate template",
                scope="global",
                scope_ref=None,
                tags=["moonspec"],
                inputs_schema=[
                    {
                        "name": "feature_request",
                        "label": "Feature Request",
                        "type": "markdown",
                        "required": True,
                    },
                    {
                        "name": "orchestration_mode",
                        "label": "Orchestration Mode",
                        "type": "enum",
                        "required": True,
                        "default": "runtime",
                        "options": ["runtime", "docs"],
                    },
                ],
                steps=[
                    {
                        "title": "Specify",
                        "instructions": "Selected mode: {{ inputs.orchestration_mode }}.",
                        "skill": {"id": "moonspec-specify", "args": {}},
                    }
                ],
                annotations={},
                required_capabilities=["git"],
                created_by=user_id,
            )

            expanded = await service.expand_template(
                slug=slug,
                scope="global",
                scope_ref=None,
                version="1.0.0",
                inputs={
                    "feature_request": "Implement MM-600",
                    "orchestration_mode": "docs",
                },
                context={},
            )

    assert expanded["appliedTemplate"]["inputs"]["orchestration_mode"] == "runtime"
    assert "Selected mode: runtime." in expanded["steps"][0]["instructions"]

async def test_expand_template_flattens_pinned_include_with_provenance(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.create_template(
                slug="child-checks",
                title="Child Checks",
                description="Reusable checks",
                scope="global",
                scope_ref=None,
                tags=["checks"],
                inputs_schema=[
                    {
                        "name": "target",
                        "label": "Target",
                        "type": "text",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "title": "Lint target",
                        "instructions": "Lint {{ inputs.target }}",
                        "skill": {
                            "id": "auto",
                            "args": {},
                            "requiredCapabilities": ["docker"],
                        },
                    },
                    {
                        "title": "Test target",
                        "instructions": "Test {{ inputs.target }}",
                    },
                ],
                annotations={},
                required_capabilities=["codex"],
                created_by=None,
            )
            await service.create_template(
                slug="parent-flow",
                title="Parent Flow",
                description="Composed flow",
                scope="global",
                scope_ref=None,
                tags=["composed"],
                inputs_schema=[
                    {
                        "name": "feature",
                        "label": "Feature",
                        "type": "text",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "kind": "include",
                        "slug": "child-checks",
                        "version": "1.0.0",
                        "alias": "quality",
                        "scope": "global",
                        "inputMapping": {"target": "{{ inputs.feature }}"},
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

            expanded = await service.expand_template(
                slug="parent-flow",
                scope="global",
                scope_ref=None,
                version="1.0.0",
                inputs={"feature": "preset composition"},
                context={},
                options=ExpandOptions(should_enforce_step_limit=True),
                user_id=user_id,
            )

    assert [step["title"] for step in expanded["steps"]] == [
        "Lint target",
        "Test target",
    ]
    assert expanded["steps"][0]["id"].startswith("tpl:parent-flow:1.0.0:01:")
    assert expanded["steps"][1]["id"].startswith("tpl:parent-flow:1.0.0:02:")
    assert "preset composition" in expanded["steps"][0]["instructions"]
    assert set(expanded["capabilities"]) >= {"codex", "docker"}
    provenance = expanded["steps"][0]["presetProvenance"]
    assert provenance["root"] == {"slug": "parent-flow", "version": "1.0.0"}
    assert provenance["source"]["slug"] == "child-checks"
    assert provenance["source"]["version"] == "1.0.0"
    assert provenance["alias"] == "quality"
    assert provenance["path"] == [
        "parent-flow@1.0.0",
        "quality:child-checks@1.0.0",
    ]
    assert expanded["composition"]["includes"][0]["alias"] == "quality"
    assert expanded["composition"]["includes"][0]["stepIds"] == [
        step["id"] for step in expanded["steps"]
    ]

async def test_expand_template_normalizes_legacy_orchestrate_mode_for_include(
    tmp_path,
):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.create_template(
                slug="moonspec-orchestrate",
                title="MoonSpec Orchestrate",
                description="Runtime-only child preset",
                scope="global",
                scope_ref=None,
                tags=["moonspec"],
                inputs_schema=[
                    {
                        "name": "feature_request",
                        "label": "Feature Request",
                        "type": "markdown",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "title": "Specify",
                        "instructions": "Selected mode: {{ inputs.orchestration_mode }}.",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )
            await service.create_template(
                slug="parent-flow",
                title="Parent Flow",
                description="Composed flow",
                scope="global",
                scope_ref=None,
                tags=["composed"],
                inputs_schema=[
                    {
                        "name": "feature_request",
                        "label": "Feature Request",
                        "type": "markdown",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "kind": "include",
                        "slug": "moonspec-orchestrate",
                        "version": "1.0.0",
                        "alias": "orchestrate",
                        "scope": "global",
                        "inputMapping": {
                            "feature_request": "{{ inputs.feature_request }}",
                            "orchestration_mode": "docs",
                        },
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

            expanded = await service.expand_template(
                slug="parent-flow",
                scope="global",
                scope_ref=None,
                version="1.0.0",
                inputs={"feature_request": "Implement MM-600"},
                context={},
                user_id=user_id,
            )

    assert "Selected mode: runtime." in expanded["steps"][0]["instructions"]

async def test_create_template_rejects_templated_include_version(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)

            with pytest.raises(
                TaskTemplateValidationError,
                match="include version must be a literal pinned version",
            ):
                await service.create_template(
                    slug="runtime-selected-parent",
                    title="Runtime Selected Parent",
                    description="Invalid dynamic child selection",
                    scope="global",
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[
                        {
                            "name": "child_version",
                            "label": "Child version",
                            "type": "text",
                        }
                    ],
                    steps=[
                        {
                            "kind": "include",
                            "slug": "child-checks",
                            "version": "{{ inputs.child_version }}",
                            "alias": "checks",
                            "scope": "global",
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=None,
                )

async def test_create_template_rejects_unsupported_include_fields(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)

            with pytest.raises(
                TaskTemplateValidationError,
                match="include uses unsupported keys: instructions, skill",
            ):
                await service.create_template(
                    slug="override-parent",
                    title="Override Parent",
                    description="Invalid child overrides",
                    scope="global",
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "kind": "include",
                            "slug": "child-checks",
                            "version": "1.0.0",
                            "alias": "checks",
                            "scope": "global",
                            "instructions": "Override child instructions",
                            "skill": {"id": "moonspec-verify"},
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=None,
                )

async def test_expand_template_rejects_global_parent_personal_include(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.create_template(
                slug="global-parent",
                title="Global Parent",
                description="Cannot include personal presets",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "personal-child",
                        "version": "1.0.0",
                        "alias": "private",
                        "scope": "personal",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            with pytest.raises(
                TaskTemplateValidationError,
                match="Global presets cannot include personal presets.*private:personal-child@1.0.0",
            ):
                await service.expand_template(
                    slug="global-parent",
                    scope="global",
                    scope_ref=None,
                    version="1.0.0",
                    inputs={},
                    context={},
                    options=ExpandOptions(),
                )

async def test_expand_template_rejects_include_cycles_with_path(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.create_template(
                slug="preset-a",
                title="Preset A",
                description="Starts cycle",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "preset-b",
                        "version": "1.0.0",
                        "alias": "b",
                        "scope": "global",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            await service.create_template(
                slug="preset-b",
                title="Preset B",
                description="Completes cycle",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "preset-a",
                        "version": "1.0.0",
                        "alias": "a",
                        "scope": "global",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            with pytest.raises(
                TaskTemplateValidationError,
                match="Preset include cycle detected.*preset-a@1.0.0.*b:preset-b@1.0.0.*a:preset-a@1.0.0",
            ):
                await service.expand_template(
                    slug="preset-a",
                    scope="global",
                    scope_ref=None,
                    version="1.0.0",
                    inputs={},
                    context={},
                    options=ExpandOptions(),
                )

async def test_expand_template_rejects_inactive_and_incompatible_includes(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.create_template(
                slug="inactive-child",
                title="Inactive Child",
                description="Inactive child",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "inactive"}],
                annotations={},
                required_capabilities=[],
                created_by=None,
                release_status=TaskTemplateReleaseStatus.INACTIVE,
            )
            await service.create_template(
                slug="input-child",
                title="Input Child",
                description="Requires input",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[
                    {
                        "name": "topic",
                        "label": "Topic",
                        "type": "text",
                        "required": True,
                    }
                ],
                steps=[{"instructions": "Handle {{ inputs.topic }}"}],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            await service.create_template(
                slug="bad-parent",
                title="Bad Parent",
                description="Invalid children",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "inactive-child",
                        "version": "1.0.0",
                        "alias": "inactive",
                        "scope": "global",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            await service.create_template(
                slug="input-parent",
                title="Input Parent",
                description="Missing child input",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "input-child",
                        "version": "1.0.0",
                        "alias": "requires-topic",
                        "scope": "global",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            with pytest.raises(
                TaskTemplateValidationError,
                match="inactive.*inactive:inactive-child@1.0.0",
            ):
                await service.expand_template(
                    slug="bad-parent",
                    scope="global",
                    scope_ref=None,
                    version="1.0.0",
                    inputs={},
                    context={},
                    options=ExpandOptions(),
                )

            with pytest.raises(
                TaskTemplateValidationError,
                match="requires-topic:input-child@1.0.0.*Missing required template input 'topic'",
            ):
                await service.expand_template(
                    slug="input-parent",
                    scope="global",
                    scope_ref=None,
                    version="1.0.0",
                    inputs={},
                    context={},
                    options=ExpandOptions(),
                )

async def test_expand_template_enforces_flattened_limit_with_include_path(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.create_template(
                slug="two-step-child",
                title="Two Step Child",
                description="Two concrete steps",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {"instructions": "first child step"},
                    {"instructions": "second child step"},
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            await service.create_template(
                slug="limited-parent",
                title="Limited Parent",
                description="Limit should apply after flattening",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "two-step-child",
                        "version": "1.0.0",
                        "alias": "two",
                        "scope": "global",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            template = (
                await session.execute(
                    select(TaskStepTemplate)
                    .where(TaskStepTemplate.slug == "limited-parent")
                    .options(selectinload(TaskStepTemplate.latest_version))
                )
            ).scalar_one()
            assert template.latest_version is not None
            template.latest_version.max_step_count = 1
            await session.commit()

            with pytest.raises(
                TaskTemplateValidationError,
                match="max_step_count=1.*two:two-step-child@1.0.0",
            ):
                await service.expand_template(
                    slug="limited-parent",
                    scope="global",
                    scope_ref=None,
                    version="1.0.0",
                    inputs={},
                    context={},
                    options=ExpandOptions(should_enforce_step_limit=True),
                )

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

async def test_deactivate_templates_marks_matching_rows_inactive(tmp_path, monkeypatch):
    increment_calls: list[tuple[str, int]] = []

    class _FakeMetrics:
        def increment(self, metric: str, value: int = 1) -> None:
            increment_calls.append((metric, value))

    monkeypatch.setattr(
        "api_service.services.task_templates.catalog._METRICS",
        _FakeMetrics(),
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.create_template(
                slug="speckit-orchestrate",
                title="SpecKit Orchestrate",
                description="Legacy preset",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "Legacy orchestration"}],
                created_by=None,
            )
            await service.create_template(
                slug="legacy-checklist",
                title="Legacy Checklist",
                description="Another legacy preset",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "Legacy checklist orchestration"}],
                created_by=None,
            )
            await service.create_template(
                slug="moonspec-orchestrate",
                title="MoonSpec Orchestrate",
                description="Current preset",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "Current orchestration"}],
                created_by=None,
            )

            deactivated = await service.deactivate_templates(
                slugs=["speckit-orchestrate", "legacy-checklist"],
                scope="global",
                scope_ref=None,
            )

            assert deactivated == 2
            delete_calls = [call for call in increment_calls if call[0] == "delete"]
            assert delete_calls == [("delete", 2)]

            with pytest.raises(TaskTemplateNotFoundError, match="Template not found."):
                await service._get_template_for_scope(
                    slug="speckit-orchestrate",
                    scope=TaskTemplateScopeType.GLOBAL,
                    scope_ref=None,
                    include_inactive=False,
                )

            legacy_template = await service._get_template_for_scope(
                slug="speckit-orchestrate",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
                include_inactive=True,
            )
            assert legacy_template.is_active is False

            second_legacy_template = await service._get_template_for_scope(
                slug="legacy-checklist",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
                include_inactive=True,
            )
            assert second_legacy_template.is_active is False

            current_template = await service._get_template_for_scope(
                slug="moonspec-orchestrate",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
                include_inactive=False,
            )
            assert current_template.is_active is True

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
                "story.create_jira_issues",
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
            assert expanded["steps"][1]["skill"]["id"] == "story.create_jira_issues"
            assert "Jira Story issue in project TOOL" in expanded["steps"][1]["instructions"]
            assert "linear_blocker_chain" in expanded["steps"][1]["instructions"]
            assert "ordered blocker chain" in expanded["steps"][1]["instructions"]
            assert "Source Document path" in expanded["steps"][1]["instructions"]
            assert expanded["steps"][1]["storyOutput"] == {
                "mode": "jira",
                "jira": {
                    "projectKey": "TOOL",
                    "issueTypeName": "Story",
                    "boardId": "",
                    "dependencyMode": "linear_blocker_chain",
                },
            }
            assert expanded["appliedTemplate"]["inputs"]["jira_dependency_mode"] == (
                "linear_blocker_chain"
            )

async def test_jira_breakdown_uses_single_allowed_project_as_runtime_default(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", "MM")
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

            template = await service.get_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
                version="1.0.0",
            )
            project_input = next(
                item
                for item in template["inputs"]
                if item["name"] == "jira_project_key"
            )
            assert project_input["default"] == "MM"

            expanded = await service.expand_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
                version="1.0.0",
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "none",
                },
                context={},
            )

            assert "Jira Story issue in project MM" in expanded["steps"][1][
                "instructions"
            ]
            assert "Dependency mode: none." in expanded["steps"][1]["instructions"]
            assert expanded["steps"][1]["storyOutput"]["jira"] == {
                "projectKey": "MM",
                "issueTypeName": "Story",
                "boardId": "",
                "dependencyMode": "none",
            }
            assert expanded["appliedTemplate"]["inputs"]["jira_project_key"] == "MM"

async def test_jira_breakdown_orchestrate_uses_repository_policy_defaults(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", "PLAT,GAME")
    monkeypatch.setattr(
        settings.atlassian.jira,
        "jira_project_defaults_by_repository",
        "ExampleOrg/Platform=PLAT,ExampleOrg/Game=GAME",
    )
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

            template = await service.get_template(
                slug="jira-breakdown-orchestrate",
                scope="global",
                scope_ref=None,
                version="1.0.0",
            )
            assert "orchestration_mode" not in {
                item["name"] for item in template["inputs"]
            }
            project_input = next(
                item
                for item in template["inputs"]
                if item["name"] == "jira_project_key"
            )
            assert project_input["default"] is None

            expanded = await service.expand_template(
                slug="jira-breakdown-orchestrate",
                scope="global",
                scope_ref=None,
                version="1.0.0",
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_project_key": "TOOL",
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "linear_blocker_chain",
                    "publish_mode": "pr",
                    "source_issue_key": "GAME-404",
                },
                context={
                    "repository": "ExampleOrg/Game",
                    "targetRuntime": "gemini_cli",
                },
            )

            assert "Jira Story issue in project GAME" in expanded["steps"][1][
                "instructions"
            ]
            assert expanded["steps"][1]["storyOutput"] == {
                "mode": "jira",
                "fallback": "fail",
                "jira": {
                    "projectKey": "GAME",
                    "issueTypeName": "Story",
                    "boardId": "",
                    "dependencyMode": "linear_blocker_chain",
                },
            }
            assert expanded["steps"][2]["jiraOrchestration"]["task"]["repository"] == (
                "ExampleOrg/Game"
            )
            assert expanded["steps"][2]["jiraOrchestration"]["task"]["runtime"] == {
                "mode": "gemini_cli"
            }
            assert expanded["steps"][2]["jiraOrchestration"]["task"]["publish"] == {
                "mode": "pr",
                "mergeAutomation": {"enabled": False},
            }
            assert expanded["appliedTemplate"]["inputs"]["jira_project_key"] == "GAME"
            assert "repository" not in expanded["appliedTemplate"]["inputs"]
            assert "runtime_mode" not in expanded["appliedTemplate"]["inputs"]

async def test_jira_breakdown_replaces_tool_placeholder_with_single_allowed_project(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", "MM")
    monkeypatch.setattr(
        settings.atlassian.jira,
        "jira_project_defaults_by_repository",
        None,
    )
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

            expanded = await service.expand_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
                version="1.0.0",
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_project_key": "TOOL",
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "none",
                },
                context={},
            )

            assert expanded["steps"][1]["storyOutput"]["jira"] == {
                "projectKey": "MM",
                "issueTypeName": "Story",
                "boardId": "",
                "dependencyMode": "none",
            }
            assert expanded["appliedTemplate"]["inputs"]["jira_project_key"] == "MM"

async def test_jira_breakdown_orchestrate_preserves_explicit_project_input(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", "PLAT,GAME")
    monkeypatch.setattr(
        settings.atlassian.jira,
        "jira_project_defaults_by_repository",
        "ExampleOrg/Platform=PLAT,ExampleOrg/Game=GAME",
    )
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

            expanded = await service.expand_template(
                slug="jira-breakdown-orchestrate",
                scope="global",
                scope_ref=None,
                version="1.0.0",
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_project_key": "PLAT",
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "linear_blocker_chain",
                    "publish_mode": "pr",
                    "source_issue_key": "PLAT-404",
                },
                context={
                    "repository": "ExampleOrg/Game",
                    "targetRuntime": "claude_code",
                },
            )

            assert expanded["steps"][1]["storyOutput"]["jira"]["projectKey"] == "PLAT"
            assert expanded["steps"][2]["jiraOrchestration"]["task"]["repository"] == (
                "ExampleOrg/Game"
            )
            assert expanded["steps"][2]["jiraOrchestration"]["task"]["runtime"] == (
                {"mode": "claude_code"}
            )

async def test_jira_breakdown_requires_project_when_multiple_allowed_without_repo_policy(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", "MM,OPS")
    monkeypatch.setattr(
        settings.atlassian.jira, "jira_project_defaults_by_repository", None
    )
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

            template = await service.get_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
                version="1.0.0",
            )
            project_input = next(
                item
                for item in template["inputs"]
                if item["name"] == "jira_project_key"
            )
            assert project_input["default"] is None

            with pytest.raises(TaskTemplateValidationError):
                await service.expand_template(
                    slug="jira-breakdown",
                    scope="global",
                    scope_ref=None,
                    version="1.0.0",
                    inputs={
                        "feature_request": "docs/Designs/RuntimeTypes.md",
                        "jira_issue_type": "Story",
                        "jira_dependency_mode": "none",
                    },
                    context={},
                )

async def test_seed_catalog_includes_jira_orchestrate_preset(tmp_path):
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
                slug="jira-orchestrate",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "Jira Orchestrate"
            assert template.latest_version is not None
            assert template.latest_version.annotations["jiraWorkflow"] == (
                "implementation-to-code-review"
            )
            template_payload = await service.get_template(
                slug="jira-orchestrate",
                scope="global",
                scope_ref=None,
                version="1.0.0",
            )
            assert "orchestration_mode" not in {
                item["name"] for item in template_payload["inputs"]
            }
            assert [
                (step.get("skill") or step.get("tool"))["id"]
                for step in template.latest_version.steps
            ] == [
                "jira-issue-updater",
                "jira.check_blockers",
                "auto",
                "auto",
                "moonspec-specify",
                "auto",
                "moonspec-plan",
                "moonspec-tasks",
                "moonspec-align",
                "moonspec-implement",
                "moonspec-verify",
                "auto",
                "jira-issue-updater",
            ]
            step_titles = [step["title"] for step in template.latest_version.steps]
            assert "Return Jira orchestration report" not in step_titles

            expanded = await service.expand_template(
                slug="jira-orchestrate",
                scope="global",
                scope_ref=None,
                version="1.0.0",
                inputs={
                    "jira_issue_key": "MM-328",
                    "source_design_path": "",
                    "constraints": "Keep the scope narrow.",
                },
                context={},
            )

            assert len(expanded["steps"]) == 13
            assert expanded["steps"][0]["skill"]["id"] == "jira-issue-updater"
            assert "MM-328" in expanded["steps"][0]["instructions"]
            assert "In Progress" in expanded["steps"][0]["instructions"]
            assert expanded["steps"][1]["title"] == "Check Jira blockers before implementation"
            assert expanded["steps"][1]["type"] == "tool"
            assert expanded["steps"][1]["tool"]["id"] == "jira.check_blockers"
            assert expanded["steps"][1]["targetIssueKey"] == "MM-328"
            assert expanded["steps"][1]["blockerPreflight"] == {
                "targetIssueKey": "MM-328",
                "linkType": "Blocks",
            }
            assert "Jira issue MM-328" in expanded["steps"][1]["instructions"]
            assert "deterministic trusted Jira blocker preflight" in expanded["steps"][1]["instructions"]
            assert "inwardIssue" in expanded["steps"][1]["instructions"]
            assert "outwardIssue" in expanded["steps"][1]["instructions"]
            assert "MUST NOT block this orchestration" in expanded["steps"][1]["instructions"]
            assert "blocker" in expanded["steps"][1]["instructions"]
            assert "Done" in expanded["steps"][1]["instructions"]
            assert "non-blocker" in expanded["steps"][1]["instructions"]
            assert "status cannot be determined" in expanded["steps"][1][
                "instructions"
            ]
            assert "raw Jira credentials" in expanded["steps"][1]["instructions"]
            assert "web scraping" in expanded["steps"][1]["instructions"]
            assert "stop the orchestration immediately" in expanded["steps"][1][
                "instructions"
            ]
            assert "Jira preset brief" in expanded["steps"][2]["instructions"]
            assert "Keep the scope narrow." in expanded["steps"][3]["instructions"]
            assert expanded["steps"][11]["title"] == "Create pull request"
            assert expanded["steps"][11]["annotations"] == {
                "jiraOrchestrateRole": "pull-request-handoff"
            }
            assert "pull request title must include MM-328" in expanded["steps"][11][
                "instructions"
            ]
            assert "parent workflow must use the pull request URL" in expanded["steps"][11][
                "instructions"
            ]
            assert "merge automation" in expanded["steps"][11]["instructions"]
            assert "non-draft pull request" in expanded["steps"][11]["instructions"]
            assert "isDraft value is false" in expanded["steps"][11]["instructions"]
            assert "confirmed non-draft" in expanded["steps"][11]["instructions"]
            assert "artifacts/jira-orchestrate-pr.json" in expanded["steps"][11][
                "instructions"
            ]
            assert expanded["steps"][12]["skill"]["id"] == "jira-issue-updater"
            assert "pull_request_url" in expanded["steps"][12]["instructions"]
            assert "stop without changing Jira" in expanded["steps"][12][
                "instructions"
            ]
            assert "Code Review" in expanded["steps"][12]["instructions"]
            assert all(
                step["title"] != "Return Jira orchestration report"
                for step in expanded["steps"]
            )

async def test_seed_catalog_includes_jira_breakdown_orchestrate_preset(tmp_path):
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
                slug="jira-breakdown-orchestrate",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "Jira Breakdown and Orchestrate"
            assert template.latest_version is not None
            assert template.latest_version.annotations["sourceSkill"] == (
                "jira-breakdown"
            )
            assert template.latest_version.annotations["output"] == (
                "dependent-jira-orchestrate-tasks"
            )
            assert [
                step["skill"]["id"] for step in template.latest_version.steps
            ] == [
                "moonspec-breakdown",
                "story.create_jira_issues",
                "story.create_jira_orchestrate_tasks",
            ]

            expanded = await service.expand_template(
                slug="jira-breakdown-orchestrate",
                scope="global",
                scope_ref=None,
                version="1.0.0",
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_project_key": "MM",
                    "jira_issue_type": "Story",
                    "jira_board_id": "84",
                    "jira_dependency_mode": "linear_blocker_chain",
                    "publish_mode": "pr_with_merge_automation",
                    "source_issue_key": "MM-404",
                },
                context={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                },
            )

            assert len(expanded["steps"]) == 3
            assert expanded["steps"][0]["skill"]["id"] == "moonspec-breakdown"
            assert expanded["steps"][1]["skill"]["id"] == "story.create_jira_issues"
            assert expanded["steps"][1]["storyOutput"]["jira"] == {
                "projectKey": "MM",
                "issueTypeName": "Story",
                "boardId": "84",
                "dependencyMode": "linear_blocker_chain",
            }
            downstream = expanded["steps"][2]
            assert downstream["skill"]["id"] == "story.create_jira_orchestrate_tasks"
            assert "Create one Jira Orchestrate task" in downstream["instructions"]
            assert "dependsOn" in downstream["instructions"]
            assert "MM-404" in downstream["instructions"]
            assert "Selected Jira board ID: 84" in expanded["steps"][1]["instructions"]
            assert downstream["jiraOrchestration"]["task"] == {
                "repository": "MoonLadderStudios/MoonMind",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}},
            }
            assert downstream["jiraOrchestration"]["traceability"]["sourceIssueKey"] == (
                "MM-404"
            )

async def test_seed_catalog_includes_moonspec_orchestrate_without_report_step(
    tmp_path,
):
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
                slug="moonspec-orchestrate",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "MoonSpec Orchestrate"
            assert template.latest_version is not None
            assert [step["skill"]["id"] for step in template.latest_version.steps] == [
                "moonspec-specify",
                "moonspec-plan",
                "moonspec-tasks",
                "moonspec-align",
                "moonspec-implement",
                "moonspec-verify",
            ]
            step_titles = [step["title"] for step in template.latest_version.steps]
            assert (
                "Return orchestration report and defer publish actions"
                not in step_titles
            )
            assert step_titles[-1] == "Verify completion"
            template_payload = await service.get_template(
                slug="moonspec-orchestrate",
                scope="global",
                scope_ref=None,
                version="1.0.0",
            )
            assert "orchestration_mode" not in {
                item["name"] for item in template_payload["inputs"]
            }

            expanded = await service.expand_template(
                slug="moonspec-orchestrate",
                scope="global",
                scope_ref=None,
                version="1.0.0",
                inputs={
                    "feature_request": "MM-366: Simplify Orchestrate Summary",
                    "orchestration_mode": "docs",
                    "source_design_path": "",
                    "constraints": "Keep the scope narrow.",
                },
                context={},
            )

            assert len(expanded["steps"]) == 6
            assert (
                expanded["appliedTemplate"]["inputs"]["orchestration_mode"]
                == "runtime"
            )
            assert "Selected mode" not in expanded["steps"][0]["instructions"]
            assert "runtime implementation workflow" in expanded["steps"][0][
                "instructions"
            ]
            assert expanded["steps"][-1]["title"] == "Verify completion"
            assert "moonspec-verify" == expanded["steps"][-1]["skill"]["id"]
            assert all(
                step["title"] != "Return orchestration report and defer publish actions"
                for step in expanded["steps"]
            )

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

async def test_sync_seed_templates_preserves_default_expansion_limit_for_includes(
    tmp_path,
):
    seed_dir = tmp_path / "seeds"
    seed_data = {
        "slug": "composed-seed",
        "title": "Composed Seed",
        "description": "Seeded preset with child include",
        "scope": "global",
        "version": "1.0.0",
        "steps": [
            {
                "kind": "include",
                "slug": "shared-child",
                "version": "1.0.0",
                "alias": "shared",
                "scope": "global",
            }
        ],
        "inputs": [],
        "annotations": {"sourceSkill": "composed-seed"},
    }
    _write_seed_template(seed_dir, seed_data)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="composed-seed",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
            )

    assert template.latest_version is not None
    assert template.latest_version.max_step_count == 25

async def test_sync_seed_templates_updates_existing_include_limit_default(tmp_path):
    seed_dir = tmp_path / "seeds"
    seed_data = {
        "slug": "composed-seed",
        "title": "Composed Seed",
        "description": "Seeded preset with child include",
        "scope": "global",
        "version": "1.0.0",
        "steps": [
            {
                "kind": "include",
                "slug": "shared-child",
                "version": "1.0.0",
                "alias": "shared",
                "scope": "global",
            }
        ],
        "inputs": [],
        "annotations": {"sourceSkill": "composed-seed"},
    }
    _write_seed_template(seed_dir, seed_data)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            await service.create_template(
                slug="composed-seed",
                title="Composed Seed",
                description="Seeded preset with child include",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=seed_data["steps"],
                annotations={"sourceSkill": "composed-seed"},
                required_capabilities=[],
                created_by=None,
            )
            template = await service._get_template_for_scope(
                slug="composed-seed",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.latest_version is not None
            template.latest_version.max_step_count = 1
            await session.commit()

            result = await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="composed-seed",
                scope=TaskTemplateScopeType.GLOBAL,
                scope_ref=None,
            )

    assert result.updated == 1
    assert template.latest_version is not None
    assert template.latest_version.max_step_count == 25

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

async def test_mm557_accepts_and_expands_jira_transition_tool_step(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            created = await service.create_template(
                slug="jira-transition-tool",
                title="Jira Transition Tool",
                description="Typed Jira transition",
                scope="personal",
                scope_ref=str(user_id),
                tags=["jira"],
                inputs_schema=[],
                steps=[
                    {
                        "type": "tool",
                        "title": "Move Jira issue",
                        "instructions": "Move MM-557 to In Progress.",
                        "tool": {
                            "id": "jira.transition_issue",
                            "version": "1.0.0",
                            "inputs": {
                                "issueKey": "MM-557",
                                "targetStatus": "In Progress",
                            },
                            "requiredAuthorization": "jira",
                            "requiredCapabilities": ["jira"],
                            "sideEffectPolicy": "idempotent-by-transition-target",
                        },
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )
            expanded = await service.expand_template(
                slug="jira-transition-tool",
                scope="personal",
                scope_ref=str(user_id),
                version="1.0.0",
                inputs={},
                context={},
                options=ExpandOptions(should_enforce_step_limit=True),
                user_id=user_id,
            )

    step = created["steps"][0]
    assert step["type"] == "tool"
    assert step["tool"]["id"] == "jira.transition_issue"
    assert step["tool"]["inputs"]["issueKey"] == "MM-557"
    assert step["tool"]["requiredCapabilities"] == ["jira"]
    assert expanded["steps"][0]["type"] == "tool"
    assert expanded["steps"][0]["tool"]["id"] == "jira.transition_issue"

async def test_mm557_accepts_explicit_and_legacy_skill_steps(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)
            created = await service.create_template(
                slug="jira-skill-steps",
                title="Jira Skill Steps",
                description="Agentic Jira steps",
                scope="personal",
                scope_ref=str(user_id),
                tags=["jira"],
                inputs_schema=[],
                steps=[
                    {
                        "type": "skill",
                        "title": "Triage Jira issue",
                        "instructions": "Read MM-557 and decide next action.",
                        "skill": {
                            "id": "jira-triage",
                            "args": {"issueKey": "MM-557"},
                            "requiredCapabilities": ["jira"],
                            "context": {"repository": "MoonLadderStudios/MoonMind"},
                            "permissions": {"jira": "read"},
                            "autonomy": {"mode": "bounded"},
                        },
                    },
                    {
                        "title": "Legacy implementation step",
                        "instructions": "Implement MM-557.",
                        "skill": {"id": "jira-implement", "args": {"issueKey": "MM-557"}},
                    },
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

    explicit, legacy = created["steps"]
    assert explicit["type"] == "skill"
    assert explicit["skill"]["id"] == "jira-triage"
    assert explicit["skill"]["context"]["repository"] == "MoonLadderStudios/MoonMind"
    assert explicit["skill"]["permissions"] == {"jira": "read"}
    assert explicit["skill"]["autonomy"] == {"mode": "bounded"}
    assert legacy["type"] == "skill"
    assert legacy["skill"]["id"] == "jira-implement"

async def test_mm557_rejects_unsupported_or_mixed_step_type_payloads(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)

            with pytest.raises(TaskTemplateValidationError, match="Step 1 type must be one of: tool, skill"):
                await service.create_template(
                    slug="bad-type",
                    title="Bad Type",
                    description="Bad type",
                    scope="global",
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[],
                    steps=[{"type": "command", "instructions": "Run something"}],
                    annotations={},
                    required_capabilities=[],
                    created_by=None,
                )

            with pytest.raises(TaskTemplateValidationError, match="Step 1 Tool step must not include a skill payload"):
                await service.create_template(
                    slug="tool-with-skill",
                    title="Tool With Skill",
                    description="Bad tool",
                    scope="global",
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "type": "tool",
                            "instructions": "Implement MM-557.",
                            "skill": {"id": "jira-implement", "args": {"issueKey": "MM-557"}},
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=None,
                )

            with pytest.raises(TaskTemplateValidationError, match="Step 1 Skill step must not include a tool payload"):
                await service.create_template(
                    slug="skill-with-tool",
                    title="Skill With Tool",
                    description="Bad skill",
                    scope="global",
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "type": "skill",
                            "instructions": "Move MM-557.",
                            "tool": {
                                "id": "jira.transition_issue",
                                "inputs": {"issueKey": "MM-557"},
                            },
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=None,
                )

async def test_mm557_rejects_shell_snippets_unless_bounded_typed_tool(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)

            with pytest.raises(TaskTemplateValidationError, match="forbidden keys: command"):
                await service.create_template(
                    slug="shell-snippet",
                    title="Shell Snippet",
                    description="Bad shell",
                    scope="personal",
                    scope_ref=str(user_id),
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "type": "skill",
                            "instructions": "Run a shell snippet.",
                            "command": "bash deploy.sh",
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=user_id,
                )

            created = await service.create_template(
                slug="bounded-command-tool",
                title="Bounded Command Tool",
                description="Approved typed command",
                scope="personal",
                scope_ref=str(user_id),
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "type": "tool",
                        "instructions": "Run a bounded test command.",
                        "tool": {
                            "name": "command.run_typed",
                            "inputs": {"commandId": "unit-tests"},
                            "requiredCapabilities": ["command-runner"],
                            "sideEffectPolicy": "bounded",
                            "validation": {"schema": "registered"},
                        },
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

    assert created["steps"][0]["tool"]["id"] == "command.run_typed"
    assert created["steps"][0]["tool"]["inputs"] == {"commandId": "unit-tests"}


async def test_mm557_tool_args_survive_empty_schema_inputs(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)

            created = await service.create_template(
                slug="tool-args-fallback",
                title="Tool Args Fallback",
                description="Tool args from API request payload",
                scope="personal",
                scope_ref=str(user_id),
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "type": "tool",
                        "instructions": "Fetch Jira issue.",
                        "tool": {
                            "name": "jira.get_issue",
                            "inputs": {},
                            "args": {"issueKey": "MM-557"},
                        },
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

    assert created["steps"][0]["tool"]["inputs"] == {"issueKey": "MM-557"}


async def test_mm557_command_tool_rejects_empty_policy_metadata(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TaskTemplateCatalogService(session)

            with pytest.raises(
                TaskTemplateValidationError,
                match="command-like Tool steps require bounded inputs and policy metadata",
            ):
                await service.create_template(
                    slug="empty-command-policy",
                    title="Empty Command Policy",
                    description="Command tool with default schema metadata",
                    scope="personal",
                    scope_ref=str(user_id),
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "type": "tool",
                            "instructions": "Run a bounded test command.",
                            "tool": {
                                "name": "command.run_typed",
                                "inputs": {"commandId": "unit-tests"},
                                "validation": {},
                            },
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=user_id,
                )
