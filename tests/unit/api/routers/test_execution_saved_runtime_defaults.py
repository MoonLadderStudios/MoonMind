"""Saved workspace defaults at the real settings-to-submission boundary."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers.executions import _get_service, router
from api_service.db.base import get_async_session
from api_service.db.models import Base, ManagedAgentProviderProfile
from api_service.services.settings_catalog import SettingsCatalogService
from moonmind.config.settings import settings
from moonmind.workflow_cli import build_execution_payload
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    agent_requests,
    saved_profile_submission as _saved_profile_submission,
    stored_parameters,
)
from tests.unit.api.routers.test_executions import (
    _build_execution_record,
    _override_temporal_client,
    _override_user_dependencies,
)


saved_profile_submission = _saved_profile_submission


@pytest_asyncio.fixture
async def saved_runtime_submission(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/settings.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    service.create_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    user = _override_user_dependencies(app, is_superuser=True)

    async def session_dependency():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_async_session] = session_dependency
    create_definition = AsyncMock(
        return_value=SimpleNamespace(
            id=uuid4(),
            name="Saved default",
            cron="0 * * * *",
            timezone="UTC",
            next_run_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    monkeypatch.setattr(
        "api_service.services.recurring_workflows_service.RecurringWorkflowsService.create_definition",
        create_definition,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        for runtime in ("codex_cli", "claude_code"):
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=f"pool-{runtime}",
                    runtime_id=runtime,
                    provider_id="minimax",
                    enabled=True,
                    auth_state="connected",
                    is_default=True,
                    tags=["team"],
                    default_model=f"{runtime}-model",
                    default_model_tier=1,
                    model_tiers=[],
                    credential_source="secret_ref",
                    secret_refs={"provider_api_key": "env://MINIMAX_API_KEY"},
                    max_parallel_runs=1,
                    cooldown_after_429_seconds=0,
                )
            )
        await session.commit()
        saved = await SettingsCatalogService(
            session=session, user_id=user.id
        ).apply_overrides(
            scope="workspace", changes={"workflow.default_runtime": "claude_code"}
        )
        effective = saved.values["workflow.default_runtime"]
        assert effective.value == "claude_code"
        assert effective.apply_mode == "next_workflow"
        assert not effective.requires_process_restart
        assert settings.workflow.default_runtime == "codex_cli"
    try:
        settings_reads = []

        def capture_settings_read(_connection, _cursor, statement, parameters, *_args):
            if (
                statement.lstrip().upper().startswith("SELECT")
                and "settings_overrides" in statement
            ):
                settings_reads.append((statement, parameters))

        event.listen(engine.sync_engine, "before_cursor_execute", capture_settings_read)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield SimpleNamespace(
                client=client,
                service=service,
                sessions=sessions,
                user=user,
                create_definition=create_definition,
                settings_reads=settings_reads,
            )
    finally:
        await engine.dispose()


def _request(entrypoint, runtime):
    if entrypoint == "cli":
        body = build_execution_payload(instructions="Use the saved workspace runtime.")
        body["payload"]["task"]["runtime"] = runtime
    else:
        body = {
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Use the saved workspace runtime.",
                    "runtime": runtime,
                }
            },
        }
    if entrypoint == "recurring":
        body["payload"]["schedule"] = {"mode": "recurring", "cron": "0 * * * *"}
    return body


def _parameters(context, entrypoint):
    if entrypoint == "recurring":
        context.service.create_execution.assert_not_awaited()
        return context.create_definition.await_args.kwargs["target"][
            "initialParameters"
        ]
    return context.service.create_execution.await_args.kwargs["initial_parameters"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["api", "cli", "recurring"])
@pytest.mark.parametrize(
    "selector", [None, {"providerId": "minimax", "tagsAll": ["team"]}]
)
@pytest.mark.parametrize("explicit", [False, True])
async def test_saved_runtime_matches_explicit_default(
    saved_runtime_submission, entrypoint, selector, explicit
):
    context = saved_runtime_submission
    runtime = {"mode": "claude_code"} if explicit else {}
    if selector:
        runtime["profileSelector"] = selector
        async with context.sessions() as session:
            await SettingsCatalogService(
                session=session, user_id=context.user.id
            ).apply_overrides(
                scope="workspace",
                changes={"workflow.default_provider_profile_ref": "pool-codex_cli"},
            )
    context.settings_reads.clear()
    response = await context.client.post(
        "/api/executions", json=_request(entrypoint, runtime)
    )
    assert response.status_code == 201, response.text
    parameters = _parameters(context, entrypoint)
    assert parameters["targetRuntime"] == "claude_code"
    assert parameters["runtimeCutover"]["selectionSource"] == (
        "authored" if explicit else "configured_default"
    )
    assert parameters["workflow"]["runtime"]["mode"] == "claude_code"
    if selector:
        assert parameters["workflow"]["runtime"]["profileSelector"] == selector
        assert parameters.get("profileId") is None
    assert settings.workflow.default_runtime == "codex_cli"
    # Runtime and Profile defaults share one real SettingsOverride query.
    assert len(context.settings_reads) == 1
    query_values = context.settings_reads[0][1]
    assert "workflow.default_runtime" in query_values
    assert "workflow.default_provider_profile_ref" in query_values


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["api", "cli", "recurring"])
@pytest.mark.parametrize(
    "intent", ["runtime", "target-runtime", "profile", "saved-profile"]
)
async def test_saved_runtime_is_only_a_fallback(
    saved_runtime_submission, entrypoint, intent
):
    context = saved_runtime_submission
    runtime = {}
    if intent == "runtime":
        runtime["mode"] = "codex_cli"
    elif intent == "profile":
        runtime["providerProfileRef"] = "pool-codex_cli"
    elif intent == "saved-profile":
        async with context.sessions() as session:
            await SettingsCatalogService(
                session=session, user_id=context.user.id
            ).apply_overrides(
                scope="workspace",
                changes={"workflow.default_provider_profile_ref": "pool-codex_cli"},
            )
    body = _request(entrypoint, runtime)
    if intent == "target-runtime":
        body["payload"]["targetRuntime"] = "codex_cli"
    response = await context.client.post("/api/executions", json=body)
    assert response.status_code == 201, response.text
    parameters = _parameters(context, entrypoint)
    assert parameters["targetRuntime"] == "codex_cli"
    assert parameters["runtimeCutover"]["selectionSource"] == (
        "authored" if intent in {"runtime", "target-runtime"} else "profile"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("preset_kind", ["goal", "template"])
@pytest.mark.parametrize("step_runtime", [None, "codex_cli"])
async def test_saved_runtime_snapshot_survives_preset_expansion(
    saved_runtime_submission, monkeypatch, preset_kind, step_runtime
):
    from moonmind.workflows.executions.preset_goal_scheduler import GoalPresetSchedule

    context = saved_runtime_submission
    captured = {}

    class Catalog:
        def __init__(self, session):
            self.session = session

        async def expand_template(self, **kwargs):
            captured.update(kwargs)
            await SettingsCatalogService(session=self.session).apply_overrides(
                scope="workspace",
                changes={
                    "workflow.default_runtime": "codex_cli",
                    "workflow.default_provider_profile_ref": "pool-codex_cli",
                },
            )
            step = {"id": "step-1", "instructions": "Run"}
            if step_runtime:
                step["runtime"] = {"mode": step_runtime}
            return {"steps": [step]}

    monkeypatch.setattr(
        "api_service.services.presets.catalog.PresetCatalogService", Catalog
    )
    monkeypatch.setattr(
        "api_service.api.routers.executions.schedule_preset_from_goal",
        lambda goal: GoalPresetSchedule(
            goal=goal, slug="goal-preset", inputs={}, reason="test", issue_key=None
        ),
    )
    workflow = (
        {"goal": "Run a preset"}
        if preset_kind == "goal"
        else {"taskTemplate": {"slug": "explicit-preset", "scope": "global"}}
    )
    response = await context.client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {"workflow": workflow},
        },
    )
    assert response.status_code == 201, response.text
    parameters = _parameters(context, "api")
    assert captured["context"]["targetRuntime"] == "claude_code"
    assert parameters["targetRuntime"] == "claude_code"
    assert parameters["runtimeCutover"]["selectionSource"] == "configured_default"
    assert parameters.get("profileId") is None
    if step_runtime:
        assert parameters["workflow"]["steps"][0]["runtime"]["mode"] == step_runtime

    # A later submission sees the change, without changing process settings.
    response = await context.client.post("/api/executions", json=_request("api", {}))
    assert response.status_code == 201, response.text
    assert _parameters(context, "api")["targetRuntime"] == "codex_cli"
    assert settings.workflow.default_runtime == "codex_cli"


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["api", "cli", "recurring"])
async def test_saved_runtime_cannot_bypass_retired_default_admission(
    saved_runtime_submission, monkeypatch, entrypoint
):
    context = saved_runtime_submission
    async with context.sessions() as session:
        await SettingsCatalogService(session=session).apply_overrides(
            scope="workspace", changes={"workflow.default_runtime": "codex_cli"}
        )
    monkeypatch.setattr(settings.workflow, "default_runtime", "claude_code")
    monkeypatch.setenv("MOONMIND_CODEX_DIRECT_RETIRED_AT", "2020-01-01T00:00:00Z")
    response = await context.client.post(
        "/api/executions", json=_request(entrypoint, {})
    )
    assert response.status_code == 422, response.text
    assert "retired" in response.json()["detail"]["message"].lower()
    context.service.create_execution.assert_not_awaited()
    context.create_definition.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["api", "cli", "recurring"])
async def test_untouched_runtime_remains_a_saved_default_fallback(
    saved_runtime_submission, entrypoint
):
    context = saved_runtime_submission
    response = await context.client.post(
        "/api/executions",
        json=_request(entrypoint, {"mode": "codex_cli", "authored": False}),
    )
    assert response.status_code == 201, response.text
    parameters = _parameters(context, entrypoint)
    assert parameters["targetRuntime"] == "claude_code"
    assert parameters["runtimeCutover"]["selectionSource"] == "configured_default"


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", ["codex_cli", "omnigent"])
@pytest.mark.parametrize("runtime_layer", ["workflow", "task", "parameters"])
@pytest.mark.parametrize(
    "selection", ["saved-runtime", "saved-profile", "selected-profile"]
)
async def test_raw_runtime_preview_uses_effective_selection_through_dispatch(
    saved_profile_submission, preview, runtime_layer, selection
):
    context = saved_profile_submission
    selected_profile = (
        "alternative-claude_code" if selection == "selected-profile" else None
    )
    await context.catalog.apply_overrides(
        scope="workspace",
        changes={
            "workflow.default_runtime": (
                "claude_code" if selection == "saved-runtime" else "codex_cli"
            ),
            "workflow.default_provider_profile_ref": (
                "saved-claude_code"
                if selection == "saved-profile"
                else ("saved-codex_cli" if selection == "selected-profile" else "")
            ),
        },
    )
    task_key = "task" if runtime_layer == "task" else "workflow"
    runtime = {
        "mode": preview,
        "authored": False,
        "model": "operator-model",
        "effort": "high",
    }
    if selected_profile:
        runtime["providerProfileRef"] = selected_profile
    task = {
        "instructions": "Use the effective runtime, not the untouched preview.",
        "targetRuntime": preview,
        "target_runtime": preview,
    }
    parameters = {
        "targetRuntime": preview,
        "target_runtime": preview,
        task_key: task,
    }
    if runtime_layer == "parameters":
        parameters["runtime"] = runtime
    else:
        task["runtime"] = runtime
    context.settings_reads.clear()
    response = await context.client.post(
        "/api/executions",
        json={"workflowType": "MoonMind.UserWorkflow", "initialParameters": parameters},
    )
    stored = await stored_parameters(context, response)
    # Persistence canonicalizes the accepted task envelope to workflow.
    for source in (stored, stored["workflow"]):
        assert source["targetRuntime"] == "claude_code"
        assert source["target_runtime"] == "claude_code"
    stored_runtime = (
        stored["runtime"]
        if runtime_layer == "parameters"
        else stored["workflow"]["runtime"]
    )
    assert stored_runtime == {**runtime, "mode": "claude_code"}
    assert stored["runtimeCutover"]["selectionSource"] == (
        "configured_default" if selection == "saved-runtime" else "profile"
    )
    request = next(iter(agent_requests(stored).values()))
    assert request.agent_id == "claude_code"
    assert request.execution_profile_ref == (
        selected_profile
        or ("saved-claude_code" if selection == "saved-profile" else None)
    )
    assert len(context.settings_reads) == 1
    context.service._client_adapter.start_workflow.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime_layer,alias",
    [
        ("runtime", "mode"),
        ("parameter-runtime", "mode"),
        ("workflow", "targetRuntime"),
        ("workflow", "target_runtime"),
        ("parameters", "targetRuntime"),
        ("parameters", "target_runtime"),
    ],
)
@pytest.mark.parametrize("runtime", ["claude_code", "omnigent"])
@pytest.mark.parametrize("authored", [None, True])
async def test_raw_authored_alias_keeps_profile_and_product_boundaries(
    saved_profile_submission, runtime_layer, alias, runtime, authored
):
    context = saved_profile_submission
    task = {"instructions": "Honor explicitly authored runtime aliases."}
    parameters = {"workflow": task}
    target = {
        "runtime": task.setdefault("runtime", {}),
        "parameter-runtime": parameters.setdefault("runtime", {}),
        "workflow": task,
        "parameters": parameters,
    }[runtime_layer]
    if runtime_layer == "parameter-runtime":
        task.pop("runtime")
    target[alias] = runtime
    if authored is not None:
        owner = parameters if runtime_layer == "parameter-runtime" else task
        owner["runtime"]["authored"] = authored
    response = await context.client.post(
        "/api/executions",
        json={"workflowType": "MoonMind.UserWorkflow", "initialParameters": parameters},
    )
    assert response.status_code == (
        422 if runtime == "omnigent" else 409
    ), response.text
    assert response.json()["detail"]["code"] == (
        "omnigent_product_boundary_required"
        if runtime == "omnigent"
        else "provider_profile_runtime_mismatch"
    )
    context.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_raw_empty_nested_runtime_preserves_authored_alias_over_outer_preview(
    saved_profile_submission,
):
    context = saved_profile_submission
    response = await context.client.post(
        "/api/executions",
        json={
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {
                "targetRuntime": "claude_code",
                "runtime": {"mode": "omnigent", "authored": False},
                "workflow": {"instructions": "Keep nested ownership.", "runtime": {}},
            },
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "provider_profile_runtime_mismatch"
    context.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_raw_preview_retry_keeps_admitted_runtime_and_profile(
    saved_profile_submission,
):
    context = saved_profile_submission
    await context.catalog.apply_overrides(
        scope="workspace",
        changes={"workflow.default_provider_profile_ref": "saved-claude_code"},
    )
    body = {
        "workflowType": "MoonMind.UserWorkflow",
        "idempotencyKey": "stable-preview-request",
        "initialParameters": {
            "workflow": {
                "instructions": "Keep the admitted selection on retry.",
                "runtime": {"mode": "omnigent", "authored": False},
            },
        },
    }
    first = await context.client.post("/api/executions", json=body)
    original = await stored_parameters(context, first)
    assert original["targetRuntime"] == "claude_code"
    await context.catalog.apply_overrides(
        scope="workspace", changes={"workflow.default_runtime": "omnigent"}
    )
    saved = await context.session.get(ManagedAgentProviderProfile, "saved-claude_code")
    await context.session.delete(saved)
    await context.session.commit()
    context.settings_reads.clear()
    repeated = await context.client.post("/api/executions", json=body)
    stored = await stored_parameters(context, repeated)
    assert repeated.json()["workflowId"] == first.json()["workflowId"]
    assert stored == original
    assert not context.settings_reads
    context.service._client_adapter.start_workflow.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", ["codex_cli", "omnigent"])
@pytest.mark.parametrize(
    "selection", ["saved-runtime", "saved-profile", "agent-profile"]
)
async def test_raw_preview_cannot_bypass_effective_omnigent_boundary(
    saved_profile_submission, preview, selection
):
    context = saved_profile_submission
    await context.catalog.apply_overrides(
        scope="workspace",
        changes={
            "workflow.default_runtime": (
                "omnigent" if selection == "saved-runtime" else "codex_cli"
            ),
            "workflow.default_provider_profile_ref": (
                "saved-codex_cli" if selection == "saved-profile" else ""
            ),
        },
    )
    runtime = {"mode": preview, "authored": False}
    if selection == "saved-profile":
        provider = await context.session.get(
            ManagedAgentProviderProfile, "saved-codex_cli"
        )
        # A pinned configuration keeps its Omnigent route even when unavailable.
        provider.execution_configuration = {
            "profileId": "pinned-configuration",
            "version": 1,
            "digest": "sha256:" + "a" * 64,
        }
        await context.session.commit()
    elif selection == "agent-profile":
        runtime["agentProfile"] = {"profileId": "selected-agent", "version": 1}
    response = await context.client.post(
        "/api/executions",
        json={
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {
                "workflow": {
                    "instructions": "Use the effective Omnigent selection.",
                    "runtime": runtime,
                },
            },
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "omnigent_product_boundary_required"
    context.service._client_adapter.start_workflow.assert_not_awaited()
