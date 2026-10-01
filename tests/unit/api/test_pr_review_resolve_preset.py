"""Catalog-boundary tests for the provider-neutral fix-and-review-loop preset."""

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
    seed_dir.mkdir(exist_ok=True)
    shutil.copy(_PRESET_DIR / f"{_SLUG}.yaml", seed_dir / f"{_SLUG}.yaml")
    return seed_dir


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


async def _template(tmp_path) -> dict[str, Any]:
    async with _catalog_db(tmp_path) as maker:
        async with maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            await session.commit()
            return await service.get_template(
                slug=_SLUG, scope="global", scope_ref=None
            )


async def test_preset_is_titled_fix_and_review_loop(tmp_path) -> None:
    template = await _template(tmp_path)

    assert template["title"] == "Fix and Review Loop"


async def test_defaults_produce_a_complete_review_loop_without_merging(tmp_path) -> None:
    """Omitted values must exercise the same production path as explicit ones."""

    expanded = await _expand(tmp_path, {"pull_request": "350"})

    merge_automation = expanded["publish"]["mergeAutomation"]
    assert expanded["publish"]["mode"] == "none"
    assert merge_automation["enabled"] is True
    assert merge_automation["checks"] == "required"
    assert merge_automation["automatedReview"] == "required"
    # Merging is opt-in; the default loop stops at the first clean review.
    assert merge_automation["finishMode"] == "fix_only"

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
            "finishMode": merge_automation["finishMode"],
            "timeouts": merge_automation["timeouts"],
            "reviewLoop": review_loop,
        }
    )
    assert config.finish_mode == "fix_only"
    assert config.review_loop.enabled is True
    assert config.review_loop.max_cycles == 5
    assert config.review_loop.max_consecutive_no_progress_cycles == 2
    assert config.review_loop.resolved_command() == "@codex review"
    assert config.timeouts.expire_after_seconds == 86400
    assert config.timeouts.fallback_poll_seconds == 60


async def test_finish_with_pr_resolver_enables_the_final_merge_pass(tmp_path) -> None:
    expanded = await _expand(
        tmp_path,
        {"pull_request": "350", "finish_with_pr_resolver": True},
    )

    merge_automation = expanded["publish"]["mergeAutomation"]
    assert merge_automation["finishMode"] == "merge"

    config = MergeAutomationConfigModel.model_validate(
        {
            "resolver": {"mergeMethod": merge_automation["mergeMethod"]},
            "finishMode": merge_automation["finishMode"],
            "timeouts": merge_automation["timeouts"],
            "reviewLoop": merge_automation["reviewLoop"],
        }
    )
    assert config.finish_mode == "merge"


async def test_existing_pr_adoption_preserves_explicit_merge_method(tmp_path) -> None:
    expanded = await _expand(
        tmp_path,
        {
            "pull_request": "350",
            "finish_with_pr_resolver": True,
            "merge_method": "rebase",
        },
    )

    assert expanded["publish"]["mergeAutomation"]["mergeMethod"] == "rebase"


@pytest.mark.parametrize("payload_key", ["task", "workflow"])
async def test_batch_adoption_disables_review_and_preserves_resolver_budget(
    tmp_path, payload_key
):
    import runpy

    from moonmind.workflows.temporal.workflows.merge_gate import (
        build_resolver_run_request,
    )
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    helper = runpy.run_path(
        str(_REPO_ROOT / ".agents/skills/batch-pr-resolver/bin/batch_pr_resolver.py")
    )
    request = helper["_build_queue_request"](
        "owner/repo",
        42,
        "saved-branch",
        runtime=helper["RuntimeSelection"](
            mode="codex_cli",
            model="chosen-model",
            effort="high",
            provider_profile="chosen-profile",
        ),
        merge_method="rebase",
        max_iterations=7,
        priority=0,
        max_attempts=3,
    )
    template = request["payload"]["task"]["taskTemplate"]
    expanded = await _expand(tmp_path, template["inputs"])
    policy = expanded["publish"]["mergeAutomation"]
    assert policy["automatedReview"] == "disabled"
    assert policy["reviewLoop"]["enabled"] is False
    assert policy["finishMode"] == "merge"
    workflow = MoonMindRunWorkflow()
    workflow._workflow_patch_enabled = lambda _patch: True
    workflow._repo = "owner/repo"
    workflow._publish_context.update({"branch": "saved-branch", "baseRef": "main"})
    payload = workflow._build_merge_gate_start_payload(
        parameters={
            "publishMode": "none",
            "targetRuntime": "codex_cli",
            payload_key: {
                "publish": expanded["publish"],
                "runtime": request["payload"]["task"]["runtime"],
            },
        },
        pull_request_url="https://github.com/owner/repo/pull/42",
        head_sha="abcdef1",
        parent_workflow_id="mm:batch-child",
        parent_run_id="run-1",
    )
    assert payload is not None
    child = build_resolver_run_request(
        parent_workflow_id="merge-owner",
        pull_request=payload["pullRequest"],
        jira_issue_key=None,
        merge_method=payload["mergeAutomationConfig"]["resolver"]["mergeMethod"],
        resolver_template=payload["resolverTemplate"],
        finish_mode="merge",
    )
    inputs = child["initial_parameters"]["task"]["skill"]["args"]
    assert inputs["mergeMethod"] == "rebase"
    assert inputs["maxIterations"] == 7
    assert inputs["returnToGate"] is True
    assert inputs["finishMode"] == "merge"
    assert "requireFreshReview" not in inputs
    runtime = child["initial_parameters"]["task"]["runtime"]
    assert runtime["executionProfileRef"] == "chosen-profile"
    assert runtime["model"] == "chosen-model"
    assert runtime["effort"] == "high"


async def test_operator_overrides_flow_into_the_review_loop(tmp_path) -> None:
    expanded = await _expand(
        tmp_path,
        {
            "pull_request": "https://github.com/MoonLadderStudios/MoonMind/pull/350",
            "max_review_cycles": "3",
            "expire_after_seconds": "3600",
        },
    )

    merge_automation = expanded["publish"]["mergeAutomation"]
    config = MergeAutomationConfigModel.model_validate(
        {
            "resolver": {"mergeMethod": merge_automation["mergeMethod"]},
            "finishMode": merge_automation["finishMode"],
            "timeouts": merge_automation["timeouts"],
            "reviewLoop": merge_automation["reviewLoop"],
        }
    )
    assert config.resolver.merge_method == "squash"
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
