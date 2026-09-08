"""Saved schedule -> workflow -> registered Activity -> current preset catalog."""

from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from api_service.api.routers.executions import _build_recurring_target
from api_service.db import base as db_base
from api_service.db.models import Base, Preset, PresetScopeType
from api_service.services.presets.catalog import (
    PresetCatalogService,
    PresetValidationError,
)
from moonmind.workflows.executions.preset_readiness import saved_preset_capability_check
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalPlanActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.workflows import run as run_module

from .helpers import load_replay

pytestmark = [pytest.mark.asyncio, pytest.mark.reliability_journey]
ACTIVITY = "plan.check_preset_capabilities"
OWNER = "00000000-0000-0000-0000-000000000000"


@pytest_asyncio.fixture
async def boundary(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/catalog.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    seed = yaml.safe_load(
        Path(
            "api_service/data/presets/github-issue-search-and-implement.yaml"
        ).read_text()
    )
    async with sessions() as session:
        session.add(
            Preset(
                slug=seed["slug"],
                scope_type=PresetScopeType.GLOBAL,
                title=seed["title"],
                description=seed["description"],
                required_capabilities=seed["requiredCapabilities"],
                steps=seed["steps"],
                inputs_schema=seed.get("inputs", []),
                annotations=seed.get("annotations", {}),
            )
        )
        await session.commit()

    @asynccontextmanager
    async def session_context():
        async with sessions() as session:
            yield session

    monkeypatch.setattr(db_base, "get_async_session_context", session_context)
    catalog = build_default_activity_catalog()
    selected = TemporalActivityCatalog(
        activities=tuple(a for a in catalog.activities if a.activity_type == ACTIVITY),
        fleets=catalog.fleets,
    )
    (binding,) = build_activity_bindings(
        selected, plan_activities=TemporalPlanActivities(artifact_service=None)
    )
    assert activity._Definition.must_from_callable(binding.handler).name == ACTIVITY
    calls = []

    async def execute(name, payload, **kwargs):
        calls.append(name)
        assert name == ACTIVITY
        assert kwargs["task_queue"] == binding.task_queue
        return await binding.handler(payload)

    monkeypatch.setattr(run_module.workflow, "execute_activity", execute)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(
        run_module.workflow, "info", lambda: SimpleNamespace(continued_run_id=None)
    )
    workflow = run_module.MoonMindRunWorkflow()
    workflow._owner_id = OWNER
    try:
        yield workflow, calls, sessions
    finally:
        await engine.dispose()


def parameters():
    return load_replay("saved-preset-verification-capabilities", "manifest.json")[
        "initialParameters"
    ]


@pytest.mark.parametrize("plan_ref", [None, "art_saved_plan"])
@pytest.mark.parametrize("payload_node", ["workflow", "task"])
async def test_stale_schedule_stops_before_planner_or_agent(
    boundary, monkeypatch, plan_ref, payload_node
):
    workflow, calls, _ = boundary
    planner = AsyncMock()
    monkeypatch.setattr(run_module, "execute_typed_activity", planner)
    saved = parameters()
    saved[payload_node] = saved.pop("task")
    saved = _build_recurring_target(saved)["initialParameters"]
    original = deepcopy(saved)
    with pytest.raises(ApplicationError, match="missing docker") as raised:
        await workflow._run_planning_stage(
            parameters=saved, input_ref=None, plan_ref=plan_ref
        )
    assert raised.value.type == "saved_preset_capabilities_stale"
    assert raised.value.non_retryable
    assert raised.value.details[0]["missingCapabilities"] == ["docker"]
    assert calls == [ACTIVITY]
    planner.assert_not_awaited()
    assert saved == original


@pytest.mark.parametrize(
    "runtime", [None, "auto", "omnigent", "codex_cli", "claude_code", "jules"]
)
async def test_current_preset_requirements_admit_saved_schedule(boundary, runtime):
    workflow, calls, sessions = boundary
    saved = parameters()
    if runtime is None:
        saved.pop("targetRuntime")
    else:
        saved["targetRuntime"] = runtime
    async with sessions() as session:
        catalog = PresetCatalogService(session)
        current = await catalog.get_template(
            slug="github-issue-search-and-implement", scope="global", scope_ref=None
        )
    saved["requiredCapabilities"] = current["requiredCapabilities"]
    original = deepcopy(saved)
    assert (
        await workflow._run_planning_stage(
            parameters=saved, input_ref=None, plan_ref="art_new_plan"
        )
        == "art_new_plan"
    )
    assert calls == [ACTIVITY]
    assert saved == original


@pytest.mark.parametrize("history", ["pre_marker", "continue_as_new", "unscheduled"])
async def test_existing_history_and_continuation_keep_admitted_progress(
    boundary, monkeypatch, history
):
    workflow, calls, _ = boundary
    saved = parameters()
    if history == "pre_marker":
        monkeypatch.setattr(run_module.workflow, "patched", lambda _: False)
    elif history == "continue_as_new":
        monkeypatch.setattr(
            run_module.workflow,
            "info",
            lambda: SimpleNamespace(continued_run_id="old-run"),
        )
    else:
        saved.pop("system")
    assert (
        await workflow._run_planning_stage(
            parameters=saved, input_ref=None, plan_ref="art_saved_plan"
        )
        == "art_saved_plan"
    )
    assert calls == []


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"status": ""},
        {"status": "new_provider_status"},
        {"status": "refresh_required"},
    ],
)
async def test_unknown_readiness_never_launches_work(boundary, monkeypatch, result):
    workflow, _, _ = boundary
    monkeypatch.setattr(
        run_module.workflow, "execute_activity", AsyncMock(return_value=result)
    )
    with pytest.raises(ApplicationError, match="readiness is unavailable"):
        await workflow._run_planning_stage(
            parameters=parameters(), input_ref=None, plan_ref="art_saved_plan"
        )


async def test_malformed_saved_provenance_is_terminal_not_a_workflow_task_failure(
    boundary,
):
    workflow, calls, _ = boundary
    saved = parameters()
    saved["task"]["appliedStepTemplates"] = ["invalid"]
    with pytest.raises(ApplicationError, match="provenance is invalid") as raised:
        await workflow._run_planning_stage(
            parameters=saved, input_ref=None, plan_ref="art_saved_plan"
        )
    assert raised.value.non_retryable
    assert calls == []


async def test_missing_definition_is_actionable_without_source_substitution(boundary):
    workflow, _, _ = boundary
    saved = parameters()
    saved["task"]["appliedStepTemplates"][0]["slug"] = "deleted-preset"
    with pytest.raises(ApplicationError) as raised:
        await workflow._run_planning_stage(
            parameters=saved, input_ref=None, plan_ref="art_saved_plan"
        )
    assert raised.value.details[0]["presets"][0]["reason"] == "preset_unavailable"


async def test_projection_excludes_large_inputs_and_keeps_included_scope():
    saved = parameters()
    saved["task"]["appliedStepTemplates"] = [
        {
            "composition": {
                "slug": "root",
                "scope": "personal",
                "inputs": {"prompt": "private"},
                "includes": [{"slug": "child", "scope": "global"}],
            }
        }
    ]
    check = saved_preset_capability_check(saved, principal=OWNER)
    assert check.presets == [
        {"slug": "root", "scope": "personal"},
        {"slug": "child", "scope": "global"},
    ]
    assert "private" not in check.model_dump_json()


async def test_personal_preset_uses_execution_owner_without_global_fallback(boundary):
    workflow, _, sessions = boundary
    saved = parameters()
    saved["task"]["appliedStepTemplates"][0]["scope"] = "personal"
    async with sessions() as session:
        session.add(
            Preset(
                slug="github-issue-search-and-implement",
                scope_type=PresetScopeType.PERSONAL,
                scope_ref=OWNER,
                title="Personal implementation",
                description="Owner's contract",
                required_capabilities=["git", "gh"],
            )
        )
        await session.commit()
    assert (
        await workflow._run_planning_stage(
            parameters=saved, input_ref=None, plan_ref="art_saved_plan"
        )
        == "art_saved_plan"
    )
    saved["task"]["appliedStepTemplates"][0]["scopeRef"] = "another-owner"
    with pytest.raises(PresetValidationError, match="owner does not match"):
        await workflow._run_planning_stage(
            parameters=saved, input_ref=None, plan_ref="art_saved_plan"
        )


@pytest.mark.parametrize("include_kind", ["include", "preset"])
async def test_new_current_include_capabilities_require_refresh(boundary, include_kind):
    workflow, calls, sessions = boundary
    saved = parameters()
    async with sessions() as session:
        root = (await session.execute(select(Preset))).scalar_one()
        root.required_capabilities = ["git", "gh"]
        root.steps = (
            [{"kind": "include", "slug": "new-child", "alias": "new-child"}]
            if include_kind == "include"
            else [{"type": "preset", "preset": {"slug": "new-child"}}]
        )
        session.add(
            Preset(
                slug="new-child",
                scope_type=PresetScopeType.GLOBAL,
                title="New child",
                description="Added after this schedule was saved",
                steps=[{"kind": "include", "slug": "test-runner", "alias": "tests"}],
            )
        )
        session.add(
            Preset(
                slug="test-runner",
                scope_type=PresetScopeType.GLOBAL,
                title="Test runner",
                description="Required verification capability",
                required_capabilities=["docker"],
            )
        )
        await session.commit()
    with pytest.raises(ApplicationError) as raised:
        await workflow._run_planning_stage(
            parameters=saved, input_ref=None, plan_ref="art_saved_plan"
        )
    assert raised.value.details[0]["missingCapabilities"] == ["docker"]
    assert calls == [ACTIVITY]
    saved["requiredCapabilities"].append("docker")
    assert (
        await workflow._run_planning_stage(
            parameters=saved, input_ref=None, plan_ref="art_refreshed_plan"
        )
        == "art_refreshed_plan"
    )


@pytest.mark.parametrize("invalid", ["missing", "inactive", "cycle", "personal"])
async def test_invalid_current_include_graph_never_launches_work(boundary, invalid):
    workflow, _, sessions = boundary
    async with sessions() as session:
        root = (await session.execute(select(Preset))).scalar_one()
        root.required_capabilities = ["git", "gh"]
        root.steps = [
            {
                "kind": "include",
                "alias": "new-child",
                "slug": root.slug if invalid == "cycle" else "new-child",
                "scope": "personal" if invalid == "personal" else "global",
            }
        ]
        if invalid == "inactive":
            session.add(
                Preset(
                    slug="new-child",
                    scope_type=PresetScopeType.GLOBAL,
                    title="Inactive child",
                    description="Unavailable requirement source",
                    is_active=False,
                )
            )
        await session.commit()
    with pytest.raises(ApplicationError) as raised:
        await workflow._run_planning_stage(
            parameters=parameters(), input_ref=None, plan_ref="art_saved_plan"
        )
    assert raised.value.type == "saved_preset_capabilities_stale"
    assert (
        raised.value.details[0]["presets"][0]["reason"]
        == "preset_composition_unavailable"
    )
