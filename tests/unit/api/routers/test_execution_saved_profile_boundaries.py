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
@pytest.mark.parametrize(
    "layer", ["runtime", "task", "parameters", "parameter_runtime"]
)
@pytest.mark.parametrize(
    "intent",
    [
        "providerProfileRef",
        "profileId",
        "providerProfile",
        "executionProfileRef",
        "execution_profile_ref",
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
    if layer == "parameter_runtime":
        parameters["runtime"] = parameters["workflow"].pop("runtime")
    target = {
        "runtime": parameters["workflow"].get("runtime"),
        "task": parameters["workflow"],
        "parameters": parameters,
        "parameter_runtime": parameters.get("runtime"),
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
    assert len(context.settings_reads) == 1
    if intent != "agentProfile":
        request = next(iter(agent_requests(stored).values()))
        if intent == "profileSelector":
            assert request.execution_profile_ref is None
            assert request.profile_selector.tags_all == ["alternative"]
        else:
            assert request.execution_profile_ref == "alternative-codex_cli"


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


def agent_requests(parameters, *, selector_patch=True):
    from datetime import UTC, datetime
    from unittest.mock import patch

    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    plan = _build_runtime_planner()(
        inputs={},
        parameters=parameters,
        snapshot=SimpleNamespace(
            digest="reg:sha256:test", artifact_ref="art_registry_test"
        ),
    )
    workflow = MoonMindRunWorkflow()
    info = SimpleNamespace(
        namespace="default",
        workflow_id="mm:profile-boundary",
        run_id="run-1",
        parent=None,
        task_queue="test",
        start_time=datetime.now(UTC),
        search_attributes={},
        typed_search_attributes={},
    )
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info", return_value=info
    ), patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda name: (
            selector_patch
            if name == "run-step-profile-selection-inheritance-v1"
            else True
        ),
    ):
        return {
            node["id"]: workflow._build_agent_execution_request(
                node_inputs=node["inputs"],
                node_id=node["id"],
                tool_name=node["tool"]["name"],
                workflow_parameters=parameters,
            )
            for node in plan["nodes"]
            if node["tool"]["type"] == "agent_runtime"
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "api", "recurring"])
@pytest.mark.parametrize(
    "intent",
    [
        "profileSelector",
        "profileSelectorTier",
        "providerProfileRef",
        "profileId",
        "providerProfile",
        "executionProfileRef",
        "execution_profile_ref",
    ],
)
async def test_mixed_steps_preserve_independent_account_intent(
    saved_profile_submission, monkeypatch, entrypoint, intent
):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    context = saved_profile_submission
    selector = {"providerId": "minimax", "tagsAll": ["alternative"]}
    selects_pool = intent.startswith("profileSelector")
    child_runtime = (
        {"profileSelector": selector}
        if selects_pool
        else {intent: "alternative-codex_cli"}
    )
    if intent == "profileSelectorTier":
        child_runtime["modelTier"] = 1
    workflow = {
        "instructions": "Use each step's selected account.",
        "runtime": {"mode": "codex_cli"},
        "steps": [
            {"id": "saved", "instructions": "Use saved account."},
            {
                "id": "selected",
                "instructions": "Use chosen account.",
                "runtime": child_runtime,
            },
        ],
    }
    if entrypoint == "raw":
        body = raw_request()
        body["initialParameters"]["workflow"] = workflow
    else:
        body = {"type": "workflow", "payload": {"workflow": workflow}}
    create_definition = AsyncMock(
        return_value=SimpleNamespace(
            id=uuid4(),
            name="Selected accounts",
            cron="0 * * * *",
            timezone="UTC",
            next_run_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    if entrypoint == "recurring":
        monkeypatch.setattr(
            "api_service.services.recurring_workflows_service.RecurringWorkflowsService.create_definition",
            create_definition,
        )
        body["payload"]["schedule"] = {"mode": "recurring", "cron": "0 * * * *"}
    response = await context.client.post("/api/executions", json=body)
    assert response.status_code == 201, response.text
    parameters = (
        create_definition.await_args.kwargs["target"]["initialParameters"]
        if entrypoint == "recurring"
        else await stored_parameters(context, response)
    )
    requests = agent_requests(parameters)
    assert requests["saved"].execution_profile_ref == "saved-codex_cli"
    selected = requests["selected"]
    # Exercise the actual pool matcher with the persisted Profile rows. The
    # selected step reaches B; its independently omitted sibling stays on A.
    from unittest.mock import patch

    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        MoonMindProviderProfileManagerWorkflow,
        ProfileSlotState,
    )

    manager = MoonMindProviderProfileManagerWorkflow()
    manager._runtime_id = "codex_cli"
    profiles = await context.session.scalars(
        select(ManagedAgentProviderProfile).where(
            ManagedAgentProviderProfile.runtime_id == "codex_cli"
        )
    )
    manager._profiles = {
        profile.profile_id: ProfileSlotState(
            profile_id=profile.profile_id,
            provider_id=profile.provider_id,
            enabled=profile.enabled,
            is_default=profile.is_default,
            tags=profile.tags,
            max_parallel_runs=profile.max_parallel_runs,
            cooldown_after_429_seconds=profile.cooldown_after_429_seconds,
            rate_limit_policy="backoff",
        )
        for profile in profiles
    }
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow.patched",
        return_value=True,
    ):
        for step_id, expected in (
            ("saved", "saved-codex_cli"),
            ("selected", "alternative-codex_cli"),
        ):
            request = requests[step_id]
            profile = manager._find_available_profile(
                selector=request.profile_selector.model_dump(
                    by_alias=True, exclude_none=True
                ),
                execution_profile_ref=request.execution_profile_ref,
            )
            assert profile is not None
            assert profile.profile_id == expected
    if selects_pool:
        assert (
            selected.profile_selector.model_dump(by_alias=True, exclude_defaults=True)
            == selector
        )
        assert selected.execution_profile_ref is None
        assert "inheritedProfileId" not in parameters["workflow"]["steps"][1]["runtime"]
    else:
        assert selected.execution_profile_ref == "alternative-codex_cli"


@pytest.mark.asyncio
async def test_raw_account_alias_precedence_matches_admission(saved_profile_submission):
    context = saved_profile_submission
    body = raw_request()
    body["initialParameters"]["workflow"]["runtime"].update(
        providerProfileRef="alternative-codex_cli",
        executionProfileRef="saved-codex_cli",
    )
    response = await context.client.post("/api/executions", json=body)
    parameters = await stored_parameters(context, response)
    admitted = resolve_launch_target_profile_selection(
        {"initialParameters": parameters}
    )
    request = next(iter(agent_requests(parameters).values()))
    assert admitted.profile_id == "alternative-codex_cli"
    assert request.execution_profile_ref == admitted.profile_id


@pytest.mark.asyncio
async def test_raw_empty_task_runtime_owns_parameter_runtime_fallback(
    saved_profile_submission,
):
    context = saved_profile_submission
    body = raw_request()
    body["initialParameters"]["workflow"]["runtime"] = {}
    body["initialParameters"]["runtime"] = {"profileId": "alternative-codex_cli"}
    response = await context.client.post("/api/executions", json=body)
    parameters = await stored_parameters(context, response)
    request = next(iter(agent_requests(parameters).values()))
    assert request.execution_profile_ref == "saved-codex_cli"


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_runtime", ["codex_cli", "claude_code"])
async def test_nested_alias_precedence_matches_ownership_and_dispatch(
    saved_profile_submission, selected_runtime
):
    context = saved_profile_submission
    selected_id = f"alternative-{selected_runtime}"
    child_runtime = {
        "providerProfileRef": selected_id,
        "profileId": "saved-codex_cli",
    }
    response = await context.client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Use the selected step account.",
                    "runtime": {"mode": "codex_cli"},
                    "steps": [
                        {"id": "saved", "instructions": "Use saved account."},
                        {
                            "id": "selected",
                            "instructions": "Use explicit account.",
                            "runtime": child_runtime,
                        },
                    ],
                }
            },
        },
    )
    if selected_runtime == "claude_code":
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["profileId"] == selected_id
        context.service._client_adapter.start_workflow.assert_not_awaited()
    else:
        parameters = await stored_parameters(context, response)
        requests = agent_requests(parameters)
        assert requests["saved"].execution_profile_ref == "saved-codex_cli"
        assert requests["selected"].execution_profile_ref == selected_id
