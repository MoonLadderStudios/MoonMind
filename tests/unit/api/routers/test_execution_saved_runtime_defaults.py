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
from tests.unit.api.routers.test_executions import (
    _build_execution_record,
    _override_temporal_client,
    _override_user_dependencies,
)


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
