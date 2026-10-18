"""Saved account intent across alternate API and nested step boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers.executions import _get_service, router
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    TemporalExecutionCanonicalRecord,
)
from api_service.services.provider_profile_runtime import (
    resolve_launch_target_profile_selection,
)
from api_service.services.settings_catalog import SettingsCatalogService
from moonmind.config.settings import settings
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.unit.api.routers.test_executions import _override_user_dependencies


@pytest_asyncio.fixture
async def saved_profile_submission(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "artifacts")
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/settings.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with sessions() as session:
            for runtime in ("codex_cli", "claude_code"):
                for name in ("saved", "alternative"):
                    session.add(
                        ManagedAgentProviderProfile(
                            profile_id=f"{name}-{runtime}",
                            runtime_id=runtime,
                            provider_id="minimax",
                            enabled=True,
                            auth_state="connected",
                            is_default=name == "alternative",
                            tags=[name],
                            default_model="test-model",
                            credential_source="secret_ref",
                            secret_refs={"provider_api_key": "env://MINIMAX_API_KEY"},
                            max_parallel_runs=1,
                            cooldown_after_429_seconds=0,
                        )
                    )
            await session.commit()
            app = FastAPI()
            app.include_router(router)
            service = TemporalExecutionService(session)
            service._client_adapter.start_workflow = AsyncMock(
                return_value=SimpleNamespace(run_id="profile-boundary-run")
            )
            app.dependency_overrides[_get_service] = lambda: service
            app.dependency_overrides[get_async_session] = lambda: session
            user = _override_user_dependencies(app, is_superuser=True)
            catalog = SettingsCatalogService(session=session, user_id=user.id)
            await catalog.apply_overrides(
                scope="workspace",
                changes={"workflow.default_provider_profile_ref": "saved-codex_cli"},
            )
            reads = []

            def capture_settings_read(
                _connection, _cursor, statement, parameters, *_args
            ):
                if (
                    statement.lstrip().upper().startswith("SELECT")
                    and "settings_overrides" in statement
                    and "workflow.default_provider_profile_ref" in str(parameters)
                ):
                    reads.append((statement, parameters))

            event.listen(
                engine.sync_engine, "before_cursor_execute", capture_settings_read
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield SimpleNamespace(
                    client=client,
                    session=session,
                    service=service,
                    catalog=catalog,
                    settings_reads=reads,
                )
    finally:
        await engine.dispose()


def raw_request(
    runtime="codex_cli", *, task_key="workflow", parameters_key="initialParameters"
):
    return {
        "workflowType": "MoonMind.UserWorkflow",
        parameters_key: {
            "targetRuntime": runtime,
            task_key: {
                "instructions": "Preserve the saved account.",
                "runtime": {"mode": runtime},
            },
        },
    }


async def stored_parameters(context, response):
    assert response.status_code == 201, response.text
    record = await context.session.get(
        TemporalExecutionCanonicalRecord, response.json()["workflowId"]
    )
    return record.parameters


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
@pytest.mark.parametrize(
    "task_key,parameters_key",
    [("workflow", "initialParameters"), ("task", "initial_parameters")],
)
async def test_raw_create_resolves_saved_profile_once(
    saved_profile_submission, runtime, task_key, parameters_key
):
    context = saved_profile_submission
    await context.catalog.apply_overrides(
        scope="user",
        changes={"workflow.default_provider_profile_ref": f"saved-{runtime}"},
    )
    context.settings_reads.clear()
    response = await context.client.post(
        "/api/executions",
        json=raw_request(runtime, task_key=task_key, parameters_key=parameters_key),
    )
    parameters = await stored_parameters(context, response)
    assert (
        resolve_launch_target_profile_selection(
            {"initialParameters": parameters}
        ).profile_id
        == f"saved-{runtime}"
    )
    assert len(context.settings_reads) == 1
    context.service._client_adapter.start_workflow.assert_awaited_once()


@pytest.mark.asyncio
async def test_raw_create_rejects_missing_saved_profile_before_launch(
    saved_profile_submission,
):
    context = saved_profile_submission
    saved = await context.session.get(ManagedAgentProviderProfile, "saved-codex_cli")
    await context.session.delete(saved)
    await context.session.commit()
    response = await context.client.post("/api/executions", json=raw_request())
    assert response.status_code == 422, response.text
    assert "Provider profile not found" in response.json()["detail"]["message"]
    context.service._client_adapter.start_workflow.assert_not_awaited()
    assert (
        list(
            (
                await context.session.scalars(select(TemporalExecutionCanonicalRecord))
            ).all()
        )
        == []
    )


@pytest.mark.asyncio
async def test_raw_create_rejects_saved_profile_runtime_mismatch(
    saved_profile_submission,
):
    context = saved_profile_submission
    response = await context.client.post(
        "/api/executions", json=raw_request("claude_code")
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["profileId"] == "saved-codex_cli"
    context.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("layer", ["runtime", "task", "parameters"])
@pytest.mark.parametrize(
    "intent",
    [
        "providerProfileRef",
        "profileId",
        "providerProfile",
        "executionProfileRef",
        "profileSelector",
        "agentProfile",
    ],
)
async def test_raw_create_preserves_explicit_account_intent(
    saved_profile_submission, layer, intent
):
    context = saved_profile_submission
    body = raw_request()
    parameters = body["initialParameters"]
    target = {
        "runtime": parameters["workflow"]["runtime"],
        "task": parameters["workflow"],
        "parameters": parameters,
    }[layer]
    target[intent] = (
        {"tagsAll": ["alternative"]}
        if intent == "profileSelector"
        else (
            {
                "profileId": "pinned-agent",
                "version": 1,
                "providerProfileRef": "alternative-codex_cli",
            }
            if intent == "agentProfile"
            else "alternative-codex_cli"
        )
    )
    response = await context.client.post("/api/executions", json=body)
    stored = await stored_parameters(context, response)
    assert "saved-codex_cli" not in str(stored)
    assert not context.settings_reads


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["deleted", "changed"])
async def test_raw_retry_keeps_recorded_profile(saved_profile_submission, change):
    context = saved_profile_submission
    body = raw_request()
    body["idempotencyKey"] = "stable-raw-request"
    first = await context.client.post("/api/executions", json=body)
    first_parameters = await stored_parameters(context, first)
    assert first_parameters["profileId"] == "saved-codex_cli"
    if change == "deleted":
        saved = await context.session.get(
            ManagedAgentProviderProfile, "saved-codex_cli"
        )
        await context.session.delete(saved)
        await context.session.commit()
    else:
        await context.catalog.apply_overrides(
            scope="workspace",
            changes={"workflow.default_provider_profile_ref": "saved-claude_code"},
        )
    context.settings_reads.clear()
    repeated = await context.client.post("/api/executions", json=body)
    parameters = await stored_parameters(context, repeated)
    assert repeated.json()["workflowId"] == first.json()["workflowId"]
    assert parameters["profileId"] == "saved-codex_cli"
    assert not context.settings_reads
    context.service._client_adapter.start_workflow.assert_awaited_once()
