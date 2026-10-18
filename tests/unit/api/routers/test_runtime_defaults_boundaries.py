"""Persisted raw and dedicated schedule settings-to-dispatch boundaries."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from api_service.api.routers.recurring_workflows import (
    _get_service as get_recurring_service,
)
from api_service.api.routers.recurring_workflows import (
    router as recurring_router,
)
from api_service.db.base import get_async_session
from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from api_service.services.settings_catalog import SettingsCatalogService
from moonmind.config.settings import settings
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    agent_requests,
    raw_request,
    stored_parameters,
)
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    saved_profile_submission as _saved_profile_submission,
)
from tests.unit.api.routers.test_executions import _override_user_dependencies
from tests.unit.services.test_recurring_workflows_service import recurring_db

saved_profile_submission = _saved_profile_submission


@pytest.mark.asyncio
@pytest.mark.parametrize("process_default", ["codex_cli", "omnigent"])
async def test_raw_saved_runtime_omission_matches_explicit(
    saved_profile_submission, monkeypatch, process_default
):
    context = saved_profile_submission
    monkeypatch.setattr(settings.workflow, "default_runtime", process_default)
    await context.catalog.reset_override(
        "workflow.default_provider_profile_ref", scope="workspace"
    )
    await context.catalog.apply_overrides(
        scope="workspace", changes={"workflow.default_runtime": "claude_code"}
    )
    body = raw_request("claude_code")
    del body["initialParameters"]["targetRuntime"]
    del body["initialParameters"]["workflow"]["runtime"]
    context.settings_reads.clear()
    omitted = await context.client.post("/api/executions", json=body)
    omitted_parameters = await stored_parameters(context, omitted)
    assert len(context.settings_reads) == 1
    explicit = await context.client.post(
        "/api/executions", json=raw_request("claude_code")
    )
    explicit_parameters = await stored_parameters(context, explicit)
    omitted_agent = next(iter(agent_requests(omitted_parameters).values()))
    explicit_agent = next(iter(agent_requests(explicit_parameters).values()))
    assert (
        omitted_agent.agent_id == explicit_agent.agent_id == "claude_code"
    ), omitted_parameters


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_account", [False, True])
async def test_raw_saved_runtime_retry_uses_recorded_authority(
    saved_profile_submission, monkeypatch, saved_account
):
    context = saved_profile_submission
    if not saved_account:
        await context.catalog.reset_override(
            "workflow.default_provider_profile_ref", scope="workspace"
        )
    await context.catalog.apply_overrides(
        scope="workspace", changes={"workflow.default_runtime": "claude_code"}
    )
    body = raw_request("claude_code")
    del body["initialParameters"]["targetRuntime"]
    del body["initialParameters"]["workflow"]["runtime"]
    body["idempotencyKey"] = "runtime-default-retry"
    first = await context.client.post("/api/executions", json=body)
    first_parameters = copy.deepcopy(await stored_parameters(context, first))
    await context.catalog.apply_overrides(
        scope="workspace",
        changes={
            "workflow.default_runtime": "omnigent",
            "workflow.default_provider_profile_ref": "saved-codex_cli",
        },
    )
    if saved_account:
        from api_service.db.models import ManagedAgentProviderProfile

        saved = await context.session.get(
            ManagedAgentProviderProfile, "saved-codex_cli"
        )
        await context.session.delete(saved)
        await context.session.commit()
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    context.settings_reads.clear()
    repeated = await context.client.post("/api/executions", json=body)
    repeated_parameters = await stored_parameters(context, repeated)
    assert repeated.json()["workflowId"] == first.json()["workflowId"]
    assert repeated_parameters == first_parameters
    assert not context.settings_reads
    context.service._client_adapter.start_workflow.assert_awaited_once()


@pytest_asyncio.fixture
async def dedicated_context(saved_profile_submission):
    context = saved_profile_submission
    await context.catalog.reset_override(
        "workflow.default_provider_profile_ref", scope="workspace"
    )
    await context.catalog.apply_overrides(
        scope="workspace", changes={"workflow.default_runtime": "claude_code"}
    )
    adapter = MagicMock()
    adapter.create_schedule = AsyncMock(return_value="mm-schedule:review")
    adapter.resolve_workflow_task_queue = MagicMock(return_value="mm.workflow.user.v2")
    service = RecurringWorkflowsService(
        context.session, temporal_client_adapter=adapter
    )
    app = FastAPI()
    app.include_router(recurring_router)
    app.dependency_overrides[get_recurring_service] = lambda: service
    app.dependency_overrides[get_async_session] = lambda: context.session
    user = _override_user_dependencies(app, is_superuser=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(
            client=client, adapter=adapter, service=service, context=context, user=user
        )


def dedicated_body():
    return {
        "name": "Review saved defaults",
        "cron": "0 6 * * *",
        "target": {
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {
                "task": {"instructions": "Use my configured defaults"},
                "publishMode": "none",
            },
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "layer,key",
    [
        ("runtime", "mode"),
        ("task", "targetRuntime"),
        ("task", "target_runtime"),
        ("parameters", "targetRuntime"),
        ("parameters", "target_runtime"),
        ("target", "targetRuntime"),
        ("target", "target_runtime"),
        ("parameter_runtime", "mode"),
    ],
)
async def test_dedicated_explicit_runtime_alias_beats_saved_default(
    dedicated_context, layer, key
):
    context = dedicated_context
    body = dedicated_body()
    target = body["target"]
    params = target["initialParameters"]
    task = params["task"]
    source = {
        "runtime": task.setdefault("runtime", {}),
        "task": task,
        "parameters": params,
        "target": target,
        "parameter_runtime": params.setdefault("runtime", {}),
    }[layer]
    # An explicit empty task runtime still owns the field; only parameter-runtime
    # fallback intentionally omits that task-level mapping.
    if layer == "parameter_runtime":
        task.pop("runtime")
    source[key] = "codex_cli"
    context.context.settings_reads.clear()
    response = await context.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 201, response.text
    scheduled = context.adapter.create_schedule.await_args.kwargs["workflow_input"][
        "initial_parameters"
    ]
    assert (
        next(iter(agent_requests(scheduled).values())).agent_id == "codex_cli"
    ), scheduled
    assert len(context.context.settings_reads) == 1


@pytest.mark.asyncio
async def test_dedicated_does_not_stamp_over_conflicting_authored_runtime(
    dedicated_context,
):
    context = dedicated_context
    body = dedicated_body()
    params = body["target"]["initialParameters"]
    params["targetRuntime"] = "codex_cli"
    params["task"]["runtime"] = {"mode": "claude_code", "profileId": "saved-codex_cli"}
    response = await context.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 409, response.text
    context.adapter.create_schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_recorded_schedule_refresh_ignores_new_saved_defaults(dedicated_context):
    from uuid import UUID

    from api_service.db.models import RecurringWorkflowDefinition

    context = dedicated_context
    response = await context.client.post(
        "/api/recurring-workflows", json=dedicated_body()
    )
    assert response.status_code == 201, response.text
    definition = await context.context.session.get(
        RecurringWorkflowDefinition, UUID(response.json()["id"])
    )
    before = copy.deepcopy(definition.target)
    assert (
        next(iter(agent_requests(before["initialParameters"]).values())).agent_id
        == "claude_code"
    )
    await context.context.catalog.apply_overrides(
        scope="workspace",
        changes={
            "workflow.default_runtime": "omnigent",
            "workflow.default_provider_profile_ref": "saved-codex_cli",
        },
    )
    context.context.settings_reads.clear()
    refreshed = await context.service._admit_plan_less_omnigent_target(
        definition, target=copy.deepcopy(definition.target)
    )
    assert refreshed is False
    assert definition.target == before
    assert not context.context.settings_reads
    context.adapter.create_schedule.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters_key", ["initialParameters", "initial_parameters"])
async def test_dedicated_recurring_api_saved_runtime_matches_explicit(
    dedicated_context, parameters_key
):
    context = dedicated_context
    body = dedicated_body()
    parameters = body["target"].pop("initialParameters")
    body["target"][parameters_key] = parameters
    context.context.settings_reads.clear()
    omitted = await context.client.post("/api/recurring-workflows", json=body)
    assert omitted.status_code == 201, omitted.text
    omitted_parameters = context.adapter.create_schedule.await_args.kwargs[
        "workflow_input"
    ]["initial_parameters"]
    assert len(context.context.settings_reads) == 1
    parameters["targetRuntime"] = "claude_code"
    explicit = await context.client.post("/api/recurring-workflows", json=body)
    assert explicit.status_code == 201, explicit.text
    explicit_parameters = context.adapter.create_schedule.await_args.kwargs[
        "workflow_input"
    ]["initial_parameters"]
    assert (
        omitted_parameters["targetRuntime"]
        == explicit_parameters["targetRuntime"]
        == "claude_code"
    )
    assert (
        omitted_parameters["runtimeCutover"]["selectionSource"] == "configured_default"
    )
    assert (
        next(iter(agent_requests(omitted_parameters).values())).agent_id
        == "claude_code"
    )


@pytest.mark.asyncio
async def test_dedicated_recurring_api_rejects_saved_missing_account(
    tmp_path, monkeypatch
):

    from fastapi import HTTPException

    from api_service.db.models import (
        ManagedAgentProviderProfile,
        OmnigentAgentProfile,
        OmnigentAgentProfileVersion,
        ProviderCredentialSource,
        ProviderProfileAuthState,
        RuntimeMaterializationMode,
    )
    from api_service.services.omnigent_agent_profile_selection import (
        resolve_default_agent_profile_snapshot,
    )

    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    selected_accounts = []

    async def capture_resolved_snapshot(*args, **kwargs):
        snapshot = await resolve_default_agent_profile_snapshot(*args, **kwargs)
        selected_accounts.append(snapshot["providerProfileRef"])
        return snapshot

    monkeypatch.setattr(
        "api_service.services.recurring_workflows_service.resolve_default_agent_profile_snapshot",
        capture_resolved_snapshot,
    )
    persist = AsyncMock(
        side_effect=HTTPException(
            418, "Unexpected plan effect after saved-account admission"
        )
    )
    monkeypatch.setattr(
        "api_service.services.omnigent_execution_plan_service.persist_json_artifact",
        persist,
    )
    adapter = MagicMock()
    adapter.create_schedule = AsyncMock()
    async with recurring_db(tmp_path) as maker, maker() as session:
        for profile_id in ("saved-account-a", "alternative-account-b"):
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="codex_cli",
                    provider_id="openai",
                    enabled=True,
                    is_default=profile_id == "alternative-account-b",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    volume_ref="test-codex-oauth",
                    volume_mount_path="/root/.codex",
                    max_parallel_runs=1,
                    cooldown_after_429_seconds=0,
                    command_behavior={"auth_readiness": {"launch_ready": True}},
                    clear_env_keys=[
                        "OPENAI_API_KEY",
                        "OPENAI_BASE_URL",
                        "OPENAI_ORG_ID",
                        "OPENAI_PROJECT",
                        "MINIMAX_API_KEY",
                    ],
                )
            )
        session.add_all(
            [
                OmnigentAgentProfile(
                    profile_id="codex-configuration",
                    display_name="Codex configuration",
                    state="active",
                    active_version=1,
                    default_for_runtime=True,
                ),
                OmnigentAgentProfileVersion(
                    profile_id="codex-configuration",
                    version=1,
                    digest="sha256:" + "a" * 64,
                    document={
                        "endpointRef": "default",
                        "bridgeMode": "proxy",
                        "source": {
                            "bundleArtifactRef": "artifact://test-bundle",
                            "bundleDigest": "sha256:" + "b" * 64,
                        },
                        "harness": "codex-native",
                        "execution": {
                            "defaultExecutionProfileRef": "omnigent-codex@1",
                            "allowedLaunchPolicyRefs": ["codex-on-demand@1"],
                        },
                        "providerRequirements": {
                            "runtimeId": "codex_cli",
                            "credentialSource": "oauth_volume",
                            "materializationMode": "oauth_home",
                            "providerIds": ["openai"],
                        },
                        "policyRef": "default@1",
                    },
                    validation_result={"ready": True},
                    rollout_metadata={
                        "bundleImport": {"upstreamAgent": {"id": "test-codex-agent"}}
                    },
                ),
            ]
        )
        await session.commit()
        await SettingsCatalogService(session=session).apply_overrides(
            scope="workspace",
            changes={
                "workflow.default_runtime": "omnigent",
                "workflow.default_provider_profile_ref": "saved-account-a",
            },
        )
        saved = await session.get(ManagedAgentProviderProfile, "saved-account-a")
        await session.delete(saved)
        await session.commit()
        probe = await resolve_default_agent_profile_snapshot(
            session,
            provider_profile_ref=None,
            launch_policy_ref=None,
            consumer_type="workflow",
            consumer_id="review-probe",
            user=None,
        )
        assert probe["providerProfileRef"] == "alternative-account-b"
        await session.rollback()
        app = FastAPI()
        app.include_router(recurring_router)
        _override_user_dependencies(app, is_superuser=True)
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=adapter, artifact_service=SimpleNamespace()
        )
        app.dependency_overrides[get_recurring_service] = lambda: service
        app.dependency_overrides[get_async_session] = lambda: session
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/recurring-workflows",
                json={
                    "name": "Preserve saved account",
                    "cron": "0 6 * * *",
                    "target": {
                        "workflowType": "MoonMind.UserWorkflow",
                        "initialParameters": {
                            "task": {"instructions": "Use my saved account"},
                            "publishMode": "none",
                        },
                    },
                },
            )
        assert response.status_code == 422, (
            response.status_code,
            response.text,
            selected_accounts,
        )
        persist.assert_not_awaited()
        adapter.create_schedule.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("layer", ["runtime", "task", "parameters", "target"])
@pytest.mark.parametrize("intent", ["execution_profile_ref", "profileSelector"])
async def test_dedicated_preserves_layered_account_intent(
    dedicated_context, layer, intent
):
    context = dedicated_context
    await context.context.catalog.apply_overrides(
        scope="workspace",
        changes={"workflow.default_provider_profile_ref": "saved-codex_cli"},
    )
    body = dedicated_body()
    target = body["target"]
    parameters = target["initialParameters"]
    task = parameters["task"]
    source = {
        "runtime": task.setdefault("runtime", {}),
        "task": task,
        "parameters": parameters,
        "target": target,
    }[layer]
    source[intent] = (
        "alternative-codex_cli"
        if intent == "execution_profile_ref"
        else {"tagsAll": ["alternative"]}
    )
    context.context.settings_reads.clear()
    response = await context.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 201, response.text
    scheduled = context.adapter.create_schedule.await_args.kwargs["workflow_input"][
        "initial_parameters"
    ]
    request = next(iter(agent_requests(scheduled).values()))
    if intent == "execution_profile_ref":
        assert request.agent_id == "codex_cli"
        assert request.execution_profile_ref == "alternative-codex_cli"
    else:
        assert request.agent_id == "claude_code"
        assert request.execution_profile_ref is None
        assert request.profile_selector.tags_all == ["alternative"]
    assert len(context.context.settings_reads) == 1


@pytest.mark.asyncio
async def test_recurring_untouched_runtime_with_saved_account_is_fallback(
    dedicated_context,
):
    context = dedicated_context
    await context.context.catalog.apply_overrides(
        scope="workspace",
        changes={"workflow.default_provider_profile_ref": "saved-claude_code"},
    )
    body = dedicated_body()
    body["target"]["initialParameters"]["task"]["runtime"] = {
        "mode": "codex_cli",
        "authored": False,
    }
    response = await context.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 201, response.text
    scheduled = context.adapter.create_schedule.await_args.kwargs["workflow_input"][
        "initial_parameters"
    ]
    request = next(iter(agent_requests(scheduled).values()))
    assert request.agent_id == "claude_code"
    assert request.execution_profile_ref == "saved-claude_code"


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["saved-profile", "explicit-profile", "selector"])
@pytest.mark.parametrize("default_runtime", ["claude_code", "omnigent"])
async def test_raw_saved_runtime_remains_fallback_below_account_intent(
    saved_profile_submission, intent, default_runtime
):
    context = saved_profile_submission
    await context.catalog.apply_overrides(
        scope="workspace", changes={"workflow.default_runtime": default_runtime}
    )
    body = raw_request()
    params = body["initialParameters"]
    params.pop("targetRuntime")
    runtime = {}
    if intent == "explicit-profile":
        runtime["providerProfileRef"] = "alternative-codex_cli"
    elif intent == "selector":
        runtime["profileSelector"] = {"tagsAll": ["alternative"]}
    params["workflow"]["runtime"] = runtime
    response = await context.client.post("/api/executions", json=body)
    if intent == "selector" and default_runtime == "omnigent":
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "omnigent_product_boundary_required"
        context.service._client_adapter.start_workflow.assert_not_awaited()
        return
    recorded = await stored_parameters(context, response)
    request = next(iter(agent_requests(recorded).values()))
    assert request.agent_id == (
        "claude_code" if intent == "selector" else "codex_cli"
    ), recorded
    assert request.execution_profile_ref == (
        "saved-codex_cli"
        if intent == "saved-profile"
        else "alternative-codex_cli" if intent == "explicit-profile" else None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["saved-profile", "explicit-profile"])
async def test_dedicated_saved_runtime_remains_fallback_below_account_intent(
    dedicated_context, intent
):
    context = dedicated_context
    body = dedicated_body()
    if intent == "saved-profile":
        await context.context.catalog.apply_overrides(
            scope="workspace",
            changes={"workflow.default_provider_profile_ref": "saved-codex_cli"},
        )
    else:
        body["target"]["initialParameters"]["task"]["runtime"] = {
            "providerProfileRef": "alternative-codex_cli"
        }
    response = await context.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 201, response.text
    scheduled = context.adapter.create_schedule.await_args.kwargs["workflow_input"][
        "initial_parameters"
    ]
    request = next(iter(agent_requests(scheduled).values()))
    assert request.agent_id == "codex_cli", scheduled
    assert request.execution_profile_ref == (
        "saved-codex_cli" if intent == "saved-profile" else "alternative-codex_cli"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "dedicated"])
async def test_runtime_default_retirement_applies_before_effects(
    dedicated_context, monkeypatch, entrypoint
):
    context = dedicated_context
    await context.context.catalog.apply_overrides(
        scope="workspace", changes={"workflow.default_runtime": "codex_cli"}
    )
    monkeypatch.setattr(settings.workflow, "default_runtime", "claude_code")
    monkeypatch.setenv("MOONMIND_CODEX_DIRECT_RETIRED_AT", "2020-01-01T00:00:00Z")
    body = dedicated_body()
    if entrypoint == "raw":
        response = await context.context.client.post(
            "/api/executions", json=body["target"]
        )
    else:
        response = await context.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 422, response.text
    assert "retired" in response.json()["detail"]["message"].lower()
    context.adapter.create_schedule.assert_not_awaited()
    context.context.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_raw_saved_omnigent_default_keeps_plan_boundary(dedicated_context):
    context = dedicated_context.context
    await context.catalog.apply_overrides(
        scope="workspace", changes={"workflow.default_runtime": "omnigent"}
    )
    context.settings_reads.clear()
    response = await context.client.post(
        "/api/executions", json=dedicated_body()["target"]
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "omnigent_product_boundary_required"
    assert len(context.settings_reads) == 1
    context.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedicated_mixed_steps_keep_saved_account_and_selected_pool(
    dedicated_context,
):
    context = dedicated_context
    await context.context.catalog.apply_overrides(
        scope="workspace",
        changes={"workflow.default_provider_profile_ref": "saved-codex_cli"},
    )
    body = dedicated_body()
    body["target"]["initialParameters"]["task"]["steps"] = [
        {"id": "saved", "instructions": "Use the saved account"},
        {
            "id": "selected",
            "instructions": "Use the pool",
            "runtime": {"profileSelector": {"tagsAll": ["alternative"]}},
        },
    ]
    response = await context.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 201, response.text
    scheduled = context.adapter.create_schedule.await_args.kwargs["workflow_input"][
        "initial_parameters"
    ]
    requests = list(agent_requests(scheduled).values())
    assert requests[0].execution_profile_ref == "saved-codex_cli"
    assert requests[1].execution_profile_ref is None
    assert requests[1].profile_selector.tags_all == ["alternative"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "layer,key",
    [
        ("task", "targetRuntime"),
        ("task", "target_runtime"),
        ("parameters", "target_runtime"),
        ("runtime", "mode"),
        ("parameter_runtime", "mode"),
    ],
)
async def test_raw_explicit_runtime_alias_reaches_dispatch(
    saved_profile_submission, layer, key
):
    context = saved_profile_submission
    await context.catalog.reset_override(
        "workflow.default_provider_profile_ref", scope="workspace"
    )
    await context.catalog.apply_overrides(
        scope="workspace", changes={"workflow.default_runtime": "codex_cli"}
    )
    body = raw_request()
    params = body["initialParameters"]
    params.pop("targetRuntime")
    task = params["workflow"]
    task.pop("runtime")
    if layer == "runtime":
        source = task.setdefault("runtime", {})
    elif layer == "parameter_runtime":
        source = params.setdefault("runtime", {})
    elif layer == "parameters":
        source = params
    else:
        source = task
    source[key] = "claude_code"
    response = await context.client.post("/api/executions", json=body)
    recorded = await stored_parameters(context, response)
    assert (
        next(iter(agent_requests(recorded).values())).agent_id == "claude_code"
    ), recorded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "layer", ["runtime", "parameter_runtime", "task", "parameters"]
)
@pytest.mark.parametrize("empty_inner_runtime", [False, True])
async def test_raw_explicit_agent_profile_requires_product_boundary(
    saved_profile_submission, layer, empty_inner_runtime
):
    context = saved_profile_submission
    await context.catalog.reset_override(
        "workflow.default_provider_profile_ref", scope="workspace"
    )
    await context.catalog.apply_overrides(
        scope="workspace", changes={"workflow.default_runtime": "claude_code"}
    )
    body = raw_request()
    params = body["initialParameters"]
    params.pop("targetRuntime")
    task = params["workflow"]
    task.pop("runtime")
    # Explicit outer runtime intent remains visible beside an empty task block.
    sources = {
        "runtime": task.setdefault("runtime", {}),
        "parameter_runtime": params.setdefault("runtime", {}),
        "task": task,
        "parameters": params,
    }
    source = sources[layer]
    if not empty_inner_runtime and layer != "runtime":
        task.pop("runtime")
    source["agentProfile"] = {"profileId": "explicit-configuration", "version": 1}
    response = await context.client.post("/api/executions", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "omnigent_product_boundary_required"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "conflict,expected", [("omnigent", 422), ("profile-owner", 409)]
)
async def test_raw_canonicalization_preserves_all_authored_runtime_validation(
    saved_profile_submission, conflict, expected
):
    context = saved_profile_submission
    await context.catalog.reset_override(
        "workflow.default_provider_profile_ref", scope="workspace"
    )
    body = raw_request("claude_code")
    params = body["initialParameters"]
    params["targetRuntime"] = "omnigent" if conflict == "omnigent" else "codex_cli"
    if conflict == "profile-owner":
        params["workflow"]["runtime"]["providerProfileRef"] = "saved-claude_code"
    response = await context.client.post("/api/executions", json=body)
    assert response.status_code == expected, response.text
    context.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["disabled", "disconnected"])
async def test_dedicated_saved_unavailable_pin_matches_explicit_without_substitution(
    dedicated_context, state
):
    from unittest.mock import patch
    from api_service.db.models import (
        ManagedAgentProviderProfile,
        ProviderCredentialSource,
        ProviderProfileAuthState,
        RuntimeMaterializationMode,
    )
    from api_service.services.provider_profile_readiness import (
        provider_profile_launch_ready,
    )
    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        MoonMindProviderProfileManagerWorkflow,
        ProfileSlotState,
    )

    ctx = dedicated_context.context
    rows = []
    for name in ["saved", "alternative"]:
        row = await ctx.session.get(ManagedAgentProviderProfile, f"{name}-codex_cli")
        row.provider_id = "openai"
        row.credential_source = ProviderCredentialSource.OAUTH_VOLUME
        row.runtime_materialization_mode = RuntimeMaterializationMode.OAUTH_HOME
        row.secret_refs = {}
        row.volume_ref = "test-codex-oauth"
        row.volume_mount_path = "/root/.codex"
        row.clear_env_keys = [
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_ORG_ID",
            "OPENAI_PROJECT",
            "MINIMAX_API_KEY",
        ]
        row.command_behavior = {"auth_readiness": {"launch_ready": True}}
        rows.append(row)
    await ctx.session.commit()
    assert all(provider_profile_launch_ready(row) for row in rows)
    if state == "disabled":
        rows[0].enabled = False
    else:
        rows[0].auth_state = ProviderProfileAuthState.DISCONNECTED
    await ctx.session.commit()
    await ctx.catalog.apply_overrides(
        scope="workspace",
        changes={
            "workflow.default_runtime": "claude_code",
            "workflow.default_provider_profile_ref": "saved-codex_cli",
        },
    )
    responses = []
    for explicit in [False, True]:
        task = {"instructions": "Retain account admission semantics"}
        if explicit:
            task["runtime"] = {"providerProfileRef": "saved-codex_cli"}
        body = dedicated_body()
        body["target"]["initialParameters"]["task"] = task
        responses.append(
            await dedicated_context.client.post("/api/recurring-workflows", json=body)
        )
    assert responses[0].status_code == responses[1].status_code, (
        responses[0].text,
        responses[1].text,
    )
    assert responses[0].status_code == 201, responses[0].text
    manager = MoonMindProviderProfileManagerWorkflow()
    manager._runtime_id = "codex_cli"
    manager._profiles = {
        row.profile_id: ProfileSlotState(
            profile_id=row.profile_id,
            provider_id=row.provider_id,
            enabled=row.enabled,
            launch_ready=provider_profile_launch_ready(row),
            is_default=row.is_default,
            tags=row.tags,
            max_parallel_runs=row.max_parallel_runs,
            cooldown_after_429_seconds=0,
            rate_limit_policy="backoff",
        )
        for row in rows
    }
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow.patched",
        return_value=True,
    ):
        assert manager._find_available_profile().profile_id == "alternative-codex_cli"
        for response in responses:
            params = response.json()["target"]["initialParameters"]
            request = next(iter(agent_requests(params).values()))
            assert (
                request.agent_id == "codex_cli"
                and request.execution_profile_ref == "saved-codex_cli"
            ), params
            assert (
                manager._find_available_profile(
                    execution_profile_ref=request.execution_profile_ref
                )
                is None
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("configuration", ["ready", "unready", "pinned-missing"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_raw_configured_profile_keeps_omnigent_product_boundary(
    saved_profile_submission, configuration, explicit
):
    from api_service.db.models import (
        ManagedAgentProviderProfile,
        OmnigentAgentProfile,
        OmnigentAgentProfileVersion,
    )
    from api_service.services.profile_execution_selection import profile_route_runtime

    context = saved_profile_submission
    await context.catalog.apply_overrides(
        scope="workspace", changes={"workflow.default_runtime": "claude_code"}
    )
    profile = await context.session.get(ManagedAgentProviderProfile, "saved-codex_cli")
    if configuration == "pinned-missing":
        profile.execution_configuration = {
            "profileId": "selected-configuration",
            "version": 1,
            "digest": "sha256:" + "a" * 64,
        }
    else:
        context.session.add_all(
            [
                OmnigentAgentProfile(
                    profile_id="selected-configuration",
                    display_name="Selected configuration",
                    state="active",
                    active_version=1,
                ),
                OmnigentAgentProfileVersion(
                    profile_id="selected-configuration",
                    version=1,
                    digest="sha256:" + "a" * 64,
                    document={
                        "harness": "codex-native",
                        "providerRequirements": {
                            "runtimeId": profile.runtime_id,
                            "credentialSource": profile.credential_source.value,
                            "materializationMode": profile.runtime_materialization_mode.value,
                            "providerIds": [profile.provider_id],
                        },
                    },
                    validation_result={"ready": configuration == "ready"},
                ),
            ]
        )
    await context.session.commit()
    assert await profile_route_runtime(context.session, None, profile) == "omnigent"
    body = dedicated_body()["target"]
    if explicit:
        body["initialParameters"]["task"]["providerProfileRef"] = profile.profile_id
    context.settings_reads.clear()
    response = await context.client.post("/api/executions", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "omnigent_product_boundary_required"
    assert len(context.settings_reads) == 1
    context.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "neutral",
    [
        {},
        None,
        {"providerId": None, "tagsAny": [], "tagsAll": []},
        {"provider_id": "  \t"},
    ],
)
async def test_dedicated_outer_pool_survives_neutral_runtime_preview(
    dedicated_context, neutral
):
    context = dedicated_context
    body = dedicated_body()
    target = body["target"]
    target["profileSelector"] = {"tagsAll": ["saved"]}
    target["initialParameters"]["task"]["runtime"] = {"profileSelector": neutral}
    response = await context.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 201, response.text
    parameters = context.adapter.create_schedule.await_args.kwargs["workflow_input"][
        "initial_parameters"
    ]
    request = next(iter(agent_requests(parameters).values()))
    assert request.agent_id == "claude_code"
    assert request.execution_profile_ref is None
    assert request.profile_selector.tags_all == ["saved"], parameters


@pytest.mark.asyncio
async def test_cli_command_uses_saved_runtime_through_real_http_admission(
    saved_profile_submission, monkeypatch
):
    import asyncio

    from typer.testing import CliRunner

    from moonmind import cli
    from moonmind.workflow_cli import WorkflowApiClient

    context = saved_profile_submission
    await context.catalog.reset_override(
        "workflow.default_provider_profile_ref", scope="workspace"
    )
    await context.catalog.apply_overrides(
        scope="workspace", changes={"workflow.default_runtime": "claude_code"}
    )
    loop = asyncio.get_running_loop()
    responses = []

    def forward(request):
        # Only the socket is replaced. The installed command, synchronous HTTP
        # client, API admission, Settings SQL and execution service are real.
        response = asyncio.run_coroutine_threadsafe(
            context.client.request(
                request.method,
                request.url.path,
                content=request.content,
                headers=request.headers,
            ),
            loop,
        ).result(timeout=30)
        responses.append(response)
        return httpx.Response(response.status_code, content=response.content)

    monkeypatch.setattr(
        cli,
        "_workflow_client",
        lambda _base: (
            WorkflowApiClient(
                base_url="http://127.0.0.1:7000", transport=httpx.MockTransport(forward)
            ),
            "http://127.0.0.1:7000",
        ),
    )
    context.settings_reads.clear()
    result = await asyncio.to_thread(
        CliRunner().invoke,
        cli.app,
        [
            "workflow",
            "run",
            "--instructions",
            "Use the saved runtime",
            "--publish-mode",
            "none",
            "--request-id",
            "saved-runtime-cli",
            "--json",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert len(context.settings_reads) == 1
    recorded = await stored_parameters(context, responses[0])
    request = next(iter(agent_requests(recorded).values()))
    assert request.agent_id == "claude_code"
    assert recorded["runtimeCutover"]["selectionSource"] == "configured_default"
    assert settings.workflow.default_runtime == "codex_cli"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["disabled", "disconnected"])
async def test_raw_saved_unavailable_pin_matches_explicit_without_substitution(
    saved_profile_submission, state
):
    from unittest.mock import patch
    from api_service.db.models import (
        ManagedAgentProviderProfile,
        ProviderCredentialSource,
        ProviderProfileAuthState,
        RuntimeMaterializationMode,
    )
    from api_service.services.provider_profile_readiness import (
        provider_profile_launch_ready,
    )
    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        MoonMindProviderProfileManagerWorkflow,
        ProfileSlotState,
    )

    ctx = saved_profile_submission
    rows = []
    for name in ["saved", "alternative"]:
        row = await ctx.session.get(ManagedAgentProviderProfile, f"{name}-codex_cli")
        row.provider_id = "openai"
        row.credential_source = ProviderCredentialSource.OAUTH_VOLUME
        row.runtime_materialization_mode = RuntimeMaterializationMode.OAUTH_HOME
        row.secret_refs = {}
        row.volume_ref = "test-codex-oauth"
        row.volume_mount_path = "/root/.codex"
        row.clear_env_keys = [
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_ORG_ID",
            "OPENAI_PROJECT",
            "MINIMAX_API_KEY",
        ]
        row.command_behavior = {"auth_readiness": {"launch_ready": True}}
        rows.append(row)
    await ctx.session.commit()
    assert all(provider_profile_launch_ready(row) for row in rows)
    if state == "disabled":
        rows[0].enabled = False
    else:
        rows[0].auth_state = ProviderProfileAuthState.DISCONNECTED
    await ctx.session.commit()
    await ctx.catalog.apply_overrides(
        scope="workspace", changes={"workflow.default_runtime": "claude_code"}
    )
    responses = []
    for explicit in [False, True]:
        task = {"instructions": "Retain account admission semantics"}
        if explicit:
            task["runtime"] = {"providerProfileRef": "saved-codex_cli"}
        responses.append(
            await ctx.client.post(
                "/api/executions",
                json={
                    "workflowType": "MoonMind.UserWorkflow",
                    "initialParameters": {"workflow": task},
                },
            )
        )
    assert responses[0].status_code == responses[1].status_code, (
        responses[0].text,
        responses[1].text,
    )
    assert responses[0].status_code == 201, responses[0].text
    manager = MoonMindProviderProfileManagerWorkflow()
    manager._runtime_id = "codex_cli"
    manager._profiles = {
        row.profile_id: ProfileSlotState(
            profile_id=row.profile_id,
            provider_id=row.provider_id,
            enabled=row.enabled,
            launch_ready=provider_profile_launch_ready(row),
            is_default=row.is_default,
            tags=row.tags,
            max_parallel_runs=row.max_parallel_runs,
            cooldown_after_429_seconds=0,
            rate_limit_policy="backoff",
        )
        for row in rows
    }
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow.patched",
        return_value=True,
    ):
        assert manager._find_available_profile().profile_id == "alternative-codex_cli"
        for response in responses:
            params = await stored_parameters(ctx, response)
            request = next(iter(agent_requests(params).values()))
            assert (
                request.agent_id == "codex_cli"
                and request.execution_profile_ref == "saved-codex_cli"
            ), params
            assert (
                manager._find_available_profile(
                    execution_profile_ref=request.execution_profile_ref
                )
                is None
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("lower_runtime", ["omnigent", "codex_cli"])
async def test_dedicated_keeps_all_authored_runtime_authority(
    dedicated_context, lower_runtime
):
    context = dedicated_context
    body = dedicated_body()
    parameters = body["target"]["initialParameters"]
    parameters["targetRuntime"] = lower_runtime
    parameters["task"]["runtime"] = {"mode": "codex_cli"}
    response = await context.client.post("/api/recurring-workflows", json=body)
    # Existing schedule admission owns any authored Omnigent runtime. This
    # fixture has no execution configuration, so that owner rejects before effects.
    expected = 409 if lower_runtime == "omnigent" else 201
    assert response.status_code == expected, response.text
    if expected == 409:
        context.adapter.create_schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedicated_stale_preview_does_not_own_runtime(dedicated_context):
    context = dedicated_context
    body = dedicated_body()
    parameters = body["target"]["initialParameters"]
    parameters["targetRuntime"] = "omnigent"
    parameters["task"]["runtime"] = {"mode": "codex_cli", "authored": False}
    response = await context.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 201, response.text
    parameters = context.adapter.create_schedule.await_args.kwargs["workflow_input"][
        "initial_parameters"
    ]
    assert parameters["targetRuntime"] == "claude_code"
    assert next(iter(agent_requests(parameters).values())).agent_id == "claude_code"


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["omitted", "explicit-omnigent", "mixed"])
@pytest.mark.parametrize("model_intent", ["default", "flat", "nested"])
async def test_ready_configuration_reaches_same_existing_admission_owner(
    dedicated_context, monkeypatch, intent, model_intent
):
    from fastapi import HTTPException
    from api_service.db.models import (
        ManagedAgentProviderProfile,
        OmnigentAgentProfile,
        OmnigentAgentProfileVersion,
        ProviderCredentialSource,
        RuntimeMaterializationMode,
    )
    from api_service.services.provider_profile_readiness import (
        provider_profile_launch_ready,
    )

    ctx = dedicated_context
    db = ctx.context.session
    row = await db.get(ManagedAgentProviderProfile, "saved-codex_cli")
    row.provider_id = "openai"
    row.credential_source = ProviderCredentialSource.OAUTH_VOLUME
    row.runtime_materialization_mode = RuntimeMaterializationMode.OAUTH_HOME
    row.secret_refs = {}
    row.volume_ref = "test-codex-oauth"
    row.volume_mount_path = "/root/.codex"
    row.clear_env_keys = [
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
        "MINIMAX_API_KEY",
    ]
    row.command_behavior = {"auth_readiness": {"launch_ready": True}}
    db.add_all(
        [
            OmnigentAgentProfile(
                profile_id="codex-configuration",
                display_name="Codex configuration",
                state="active",
                active_version=1,
                default_for_runtime=True,
            ),
            OmnigentAgentProfileVersion(
                profile_id="codex-configuration",
                version=1,
                digest="sha256:" + "a" * 64,
                document={
                    "endpointRef": "default",
                    "bridgeMode": "proxy",
                    "source": {
                        "bundleArtifactRef": "artifact://test-bundle",
                        "bundleDigest": "sha256:" + "b" * 64,
                    },
                    "harness": "codex-native",
                    "execution": {
                        "defaultExecutionProfileRef": "omnigent-codex@1",
                        "allowedLaunchPolicyRefs": ["codex-on-demand@1"],
                    },
                    "providerRequirements": {
                        "runtimeId": "codex_cli",
                        "credentialSource": "oauth_volume",
                        "materializationMode": "oauth_home",
                        "providerIds": ["openai"],
                    },
                    "policyRef": "default@1",
                },
                validation_result={"ready": True},
                rollout_metadata={
                    "bundleImport": {"upstreamAgent": {"id": "test-codex-agent"}}
                },
            ),
        ]
    )
    await db.commit()
    assert provider_profile_launch_ready(row)
    await ctx.context.catalog.apply_overrides(
        scope="workspace",
        changes={
            "workflow.default_runtime": "omnigent",
            "workflow.default_provider_profile_ref": "saved-codex_cli",
        },
    )
    effect = AsyncMock(
        side_effect=HTTPException(418, "Reached admitted schedule artifact boundary")
    )
    monkeypatch.setattr(
        "api_service.services.omnigent_execution_plan_service.persist_json_artifact",
        effect,
    )
    from api_service.services.omnigent_agent_profile_selection import (
        resolve_default_agent_profile_snapshot,
    )

    snapshots = []

    async def capture(*args, **kwargs):
        result = await resolve_default_agent_profile_snapshot(*args, **kwargs)
        snapshots.append(result)
        return result

    monkeypatch.setattr(
        "api_service.services.recurring_workflows_service.resolve_default_agent_profile_snapshot",
        capture,
    )
    body = dedicated_body()
    params = body["target"]["initialParameters"]
    if intent != "omitted":
        params["targetRuntime"] = "omnigent"
    if intent == "mixed":
        params["task"]["runtime"] = {"mode": "codex_cli"}
    if model_intent != "default":
        params.update(model="vendor/explicit-test-model", effort="low")
    if model_intent == "nested":
        params["task"].setdefault("runtime", {}).update(
            model="vendor/nested-test-model", effort="medium"
        )
    response = await ctx.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 418, response.text
    assert (
        len(snapshots) == 1 and snapshots[0]["providerProfileRef"] == "saved-codex_cli"
    )
    effect.assert_awaited_once()
    ctx.adapter.create_schedule.assert_not_awaited()
    planned = effect.await_args.kwargs["payload"]["target"]["initialParameters"]
    assert planned["targetRuntime"] == "omnigent"
    assert planned["agentProfileSnapshot"]["providerProfileRef"] == "saved-codex_cli"
    if intent == "mixed":
        assert "runtimeCutover" not in planned

    if model_intent == "flat":
        assert planned["model"] == "vendor/explicit-test-model"
        assert planned["effort"] == "low"
    elif model_intent == "nested":
        assert planned["model"] == "vendor/nested-test-model"
        assert planned["effort"] == "medium"


@pytest.mark.asyncio
@pytest.mark.parametrize("neutral", [None, "", "  \t"])
async def test_dedicated_blank_mode_cannot_restore_process_fallback(
    dedicated_context, neutral
):
    context = dedicated_context
    body = dedicated_body()
    parameters = body["target"]["initialParameters"]
    parameters["targetRuntime"] = "claude_code"
    parameters["task"]["runtime"] = {"mode": neutral}
    response = await context.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 201, response.text
    parameters = context.adapter.create_schedule.await_args.kwargs["workflow_input"][
        "initial_parameters"
    ]
    assert next(iter(agent_requests(parameters).values())).agent_id == "claude_code"


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_mode", ["fix_only", "merge"])
@pytest.mark.parametrize("explicit", [False, True], ids=["saved", "explicit"])
async def test_dedicated_implicit_resolver_keeps_saved_account(
    dedicated_context, monkeypatch, finish_mode, explicit
):
    from moonmind.workflows.temporal.workflows.merge_gate import (
        build_resolver_run_request,
    )
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
    from tests.unit.api.routers.test_execution_account_intent_matrix import choose
    from tests.unit.api.routers.test_execution_implicit_resolver_accounts import (
        _finishing_workflow,
    )

    ctx = dedicated_context
    monkeypatch.setattr(settings.feature_flags, "container_jobs_enabled", True)
    await ctx.context.catalog.apply_overrides(
        scope="workspace",
        changes={"workflow.default_provider_profile_ref": "saved-codex_cli"},
    )
    body = dedicated_body()
    body["target"]["initialParameters"]["task"] = _finishing_workflow(
        finish_mode, explicit=explicit
    )
    ctx.context.settings_reads.clear()
    response = await ctx.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 201, response.text
    recorded = response.json()["target"]["initialParameters"]
    parent = MoonMindRunWorkflow()
    parent._repo = "MoonLadderStudios/MoonMind"
    monkeypatch.setattr(parent, "_workflow_patch_enabled", lambda _patch: True)
    gate = parent._build_merge_gate_start_payload(
        parameters=recorded,
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/831",
        head_sha="a" * 40,
        parent_workflow_id="mm:review-parent",
        parent_run_id="run-review",
    )
    assert gate is not None
    child = build_resolver_run_request(
        parent_workflow_id="mm:review-parent",
        pull_request=gate["pullRequest"],
        jira_issue_key=None,
        merge_method="squash",
        resolver_template=gate["resolverTemplate"],
        finish_mode=finish_mode,
    )
    request = next(iter(agent_requests(child["initial_parameters"]).values()))
    assert request.execution_profile_ref == "saved-codex_cli"
    assert await choose(ctx.context, request) == "saved-codex_cli"
    assert len(ctx.context.settings_reads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_runtime", [False, True])
@pytest.mark.parametrize("finish_mode", [None, "review_only"])
async def test_dedicated_agent_free_omnigent_keeps_plan_admission(
    dedicated_context, monkeypatch, explicit_runtime, finish_mode
):
    from api_service.db.models import ManagedAgentProviderProfile
    from tests.unit.api.routers.test_execution_implicit_resolver_accounts import (
        _finishing_workflow,
    )

    ctx = dedicated_context
    await ctx.context.catalog.apply_overrides(
        scope="workspace",
        changes={"workflow.default_provider_profile_ref": "saved-codex_cli"},
    )
    saved = await ctx.context.session.get(
        ManagedAgentProviderProfile, "saved-codex_cli"
    )
    await ctx.context.session.delete(saved)
    await ctx.context.session.commit()
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    task = _finishing_workflow("review_only", runtime="omnigent")
    if not explicit_runtime:
        task.pop("runtime")
    if finish_mode is None:
        task["publish"].pop("mergeAutomation")
    body = dedicated_body()
    body["target"]["initialParameters"]["task"] = task
    ctx.context.settings_reads.clear()
    response = await ctx.client.post("/api/recurring-workflows", json=body)
    assert response.status_code == 409, response.text
    assert "default Omnigent Agent Profile is unavailable" in response.text
    assert "saved-codex_cli" not in response.text
    assert not ctx.context.settings_reads
    ctx.adapter.create_schedule.assert_not_awaited()
