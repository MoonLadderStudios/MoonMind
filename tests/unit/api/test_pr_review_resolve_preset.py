"""Catalog-boundary tests for the provider-neutral PR review/resolve preset."""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.presets.catalog import (
    ExpandOptions,
    PresetCatalogService,
    PresetNotFoundError,
)
from moonmind.schemas.temporal_models import MergeAutomationConfigModel

pytestmark = [pytest.mark.asyncio]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESET_DIR = _REPO_ROOT / "api_service" / "data" / "presets"
_SLUG = "pr-review-resolve"


@asynccontextmanager
async def _catalog_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/pr_review_resolve.db", future=True
    )
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


def _seed_dir(tmp_path) -> Path:
    seed_dir = tmp_path / "presets"
    seed_dir.mkdir()
    shutil.copy(_PRESET_DIR / f"{_SLUG}.yaml", seed_dir / f"{_SLUG}.yaml")
    return seed_dir


async def _resolve_workflow_publish(
    tmp_path, inputs: dict[str, Any], context: dict[str, Any] | None = None
):
    async with _catalog_db(tmp_path) as maker:
        async with maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            await session.commit()
            return await service.resolve_workflow_publish(
                slug=_SLUG,
                scope="global",
                scope_ref=None,
                inputs=inputs,
                context=context or {"repository": "MoonLadderStudios/MoonMind"},
            )


async def _expand(tmp_path, inputs: dict[str, Any], context: dict[str, Any] | None = None):
    async with _catalog_db(tmp_path) as maker:
        async with maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            await session.commit()
            return await service.expand_template(
                slug=_SLUG,
                scope="global",
                scope_ref=None,
                inputs=inputs,
                context=context or {"repository": "MoonLadderStudios/MoonMind"},
                options=ExpandOptions(should_enforce_step_limit=True),
            )


async def test_defaults_produce_a_complete_review_loop(tmp_path) -> None:
    """Omitted values must exercise the same production path as explicit ones."""

    expanded = await _expand(tmp_path, {"pull_request": "350"})

    merge_automation = expanded["publish"]["mergeAutomation"]
    assert expanded["publish"]["mode"] == "none"
    assert merge_automation["enabled"] is True
    assert merge_automation["mergeMethod"] == "squash"
    assert merge_automation["checks"] == "required"
    assert merge_automation["automatedReview"] == "required"

    review_loop = merge_automation["reviewLoop"]
    assert review_loop["enabled"] is True
    assert review_loop["provider"] == "codex"
    assert review_loop["requireFreshReviewForEveryHead"] is True
    assert review_loop["requestAfterRemediation"] is True

    # The workflow-level config must validate through the same typed contract
    # the merge-automation workflow consumes.
    config = MergeAutomationConfigModel.model_validate(
        {
            "gate": {
                "github": {
                    "checks": merge_automation["checks"],
                    "automatedReview": merge_automation["automatedReview"],
                }
            },
            "resolver": {"mergeMethod": merge_automation["mergeMethod"]},
            "timeouts": merge_automation["timeouts"],
            "reviewLoop": review_loop,
        }
    )
    assert config.review_loop.enabled is True
    assert config.review_loop.max_cycles == 5
    assert config.review_loop.max_consecutive_no_progress_cycles == 2
    assert config.review_loop.resolved_command() == "@codex review"
    assert config.timeouts.expire_after_seconds == 86400
    assert config.timeouts.fallback_poll_seconds == 60


async def test_operator_overrides_flow_into_the_review_loop(tmp_path) -> None:
    expanded = await _expand(
        tmp_path,
        {
            "pull_request": "https://github.com/MoonLadderStudios/MoonMind/pull/350",
            "merge_method": "rebase",
            "max_review_cycles": "3",
            "expire_after_seconds": "3600",
        },
    )

    merge_automation = expanded["publish"]["mergeAutomation"]
    assert merge_automation["mergeMethod"] == "rebase"

    config = MergeAutomationConfigModel.model_validate(
        {
            "resolver": {"mergeMethod": merge_automation["mergeMethod"]},
            "timeouts": merge_automation["timeouts"],
            "reviewLoop": merge_automation["reviewLoop"],
        }
    )
    assert config.resolver.merge_method == "rebase"
    assert config.review_loop.max_cycles == 3
    assert config.timeouts.expire_after_seconds == 3600


async def test_target_step_uses_the_trusted_resolution_tool(tmp_path) -> None:
    expanded = await _expand(tmp_path, {"pull_request": "350"})

    steps = expanded["steps"]
    assert len(steps) == 1
    tool = steps[0]["tool"]
    assert tool["id"] == "github.resolve_pull_request_target"
    assert tool["inputs"] == {
        "repository": "MoonLadderStudios/MoonMind",
        "pullRequest": "350",
    }
    assert "gh" in tool["requiredCapabilities"]


async def test_explicit_repository_input_wins_over_context(tmp_path) -> None:
    expanded = await _expand(
        tmp_path,
        {"pull_request": "350", "repository": "OtherOrg/OtherRepo"},
        context={"repository": "MoonLadderStudios/MoonMind"},
    )

    assert (
        expanded["steps"][0]["tool"]["inputs"]["repository"] == "OtherOrg/OtherRepo"
    )


async def test_workflow_publish_resolves_server_side_without_the_expanded_steps(
    tmp_path,
) -> None:
    """The gate must survive a client that edits or drops workflow publish policy.

    Steps are expanded and edited in the client, but the merge-automation gate a
    review-and-merge preset owns is re-resolved from the stored template at
    submission time, so it cannot be lost on the way in.
    """

    resolved = await _resolve_workflow_publish(
        tmp_path,
        {"pull_request": "3771", "merge_method": "rebase"},
    )

    assert resolved is not None
    assert resolved["mode"] == "none"
    merge_automation = resolved["mergeAutomation"]
    assert merge_automation["enabled"] is True
    assert merge_automation["mergeMethod"] == "rebase"
    review_loop = merge_automation["reviewLoop"]
    assert review_loop["enabled"] is True
    assert review_loop["provider"] == "codex"
    assert review_loop["requireFreshReviewForEveryHead"] is True
    # The resolved policy is exactly what the task-queue selector reads.
    assert MergeAutomationConfigModel.model_validate(merge_automation)


async def test_workflow_publish_resolution_honors_operator_overrides(
    tmp_path,
) -> None:
    resolved = await _resolve_workflow_publish(
        tmp_path,
        {
            "pull_request": "3771",
            "max_review_cycles": 9,
            "expire_after_seconds": 7200,
        },
    )

    assert resolved is not None
    assert int(resolved["mergeAutomation"]["reviewLoop"]["maxCycles"]) == 9
    assert int(resolved["mergeAutomation"]["timeouts"]["expireAfterSeconds"]) == 7200


async def test_workflow_publish_resolution_rejects_an_unknown_preset(
    tmp_path,
) -> None:
    """An unknown slug must surface, not silently drop the gate."""

    async with _catalog_db(tmp_path) as maker:
        async with maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            await session.commit()
            with pytest.raises(PresetNotFoundError):
                await service.resolve_workflow_publish(
                    slug="not-a-real-preset",
                    scope="global",
                    scope_ref=None,
                    inputs={},
                    context={},
                )


async def test_workflow_publish_resolution_is_none_without_a_publish_annotation(
    tmp_path,
) -> None:
    """A preset that declares no workflow publish policy resolves to nothing."""

    async with _catalog_db(tmp_path) as maker:
        async with maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            await session.commit()
            template = await service._get_template_for_scope(
                slug=_SLUG, scope="global", scope_ref=None
            )
            annotations = dict(template.annotations or {})
            annotations.pop("workflowPublish", None)
            template.annotations = annotations
            await session.commit()
            assert (
                await service.resolve_workflow_publish(
                    slug=_SLUG,
                    scope="global",
                    scope_ref=None,
                    inputs={"pull_request": "3771"},
                    context={},
                )
                is None
            )
