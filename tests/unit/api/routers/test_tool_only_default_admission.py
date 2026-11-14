"""Omitted tool execution does not admit an unused agent default."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, update

from api_service.db.models import ManagedAgentProviderProfile
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.config.settings import settings
from moonmind.workflows.temporal.client import TemporalClientAdapter
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    agent_requests,
    stored_parameters,
)
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    saved_profile_submission as _saved_profile_submission,
)

saved_profile_submission = _saved_profile_submission


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["api", "raw", "recurring"])
@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("preset", [False, True])
@pytest.mark.parametrize("account_state", ["missing", "disabled"])
@pytest.mark.parametrize("default_runtime", ["omnigent", "codex_cli"])
async def test_omitted_tool_selection_ignores_unavailable_agent_accounts(
    saved_profile_submission,
    monkeypatch,
    entrypoint,
    preview,
    preset,
    account_state,
    default_runtime,
):
    context = saved_profile_submission
    monkeypatch.setattr(settings.workflow, "default_runtime", default_runtime)
    if default_runtime == "codex_cli":
        monkeypatch.setenv("MOONMIND_CODEX_DIRECT_RETIRED_AT", "2026-01-01T00:00:00Z")
    await context.session.execute(
        delete(ManagedAgentProviderProfile)
        if account_state == "missing"
        else update(ManagedAgentProviderProfile).values(enabled=False)
    )
    await context.session.commit()
    workflow = {
        "instructions": "Resolve a pull request target without an agent.",
        "steps": [
            {
                "type": "tool",
                "tool": {
                    "id": "github.resolve_pull_request_target",
                    "inputs": {"pullRequest": "350"},
                },
            }
        ],
        "publish": {"mode": "none"},
    }
    if preset:
        await PresetCatalogService(context.session).create_template(
            slug="default-tools",
            title="Tools",
            description="Discover documents",
            tags=[],
            inputs_schema=[],
            steps=[
                {
                    "type": "tool",
                    "instructions": "Discover documents.",
                    "tool": {"id": "document.discover", "inputs": {"directory": "."}},
                }
            ],
        )
        workflow.pop("steps")
        workflow["taskTemplate"] = {"slug": "default-tools"}
    parameters = {"workflow": workflow}
    schedule = AsyncMock(return_value="mm-schedule:tools")
    if entrypoint == "recurring":
        monkeypatch.setattr(TemporalClientAdapter, "create_schedule", schedule)
        monkeypatch.setattr(
            TemporalClientAdapter,
            "resolve_workflow_task_queue",
            lambda *a, **k: "mm.workflow.user.v2",
        )
        parameters["schedule"] = {"mode": "recurring", "cron": "0 6 * * *"}
    if preview:
        parameters["targetRuntime"] = "omnigent"
        workflow["runtime"] = {"mode": "omnigent", "authored": False}
    body = (
        {"type": "workflow", "payload": parameters}
        if entrypoint != "raw"
        else {"workflowType": "MoonMind.UserWorkflow", "initialParameters": parameters}
    )
    response = await context.client.post("/api/executions", json=body)
    if entrypoint == "recurring":
        assert response.status_code == 201, response.text
        parameters = schedule.await_args.kwargs["workflow_input"]["initial_parameters"]
    else:
        parameters = await stored_parameters(context, response)
    assert not agent_requests(parameters)
    assert not parameters.get("agentProfileSnapshot")
    assert not parameters.get("omnigentExecutionPlan")
    assert not parameters.get("profileId")
    if entrypoint != "recurring":
        context.service._client_adapter.start_workflow.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["api", "raw", "recurring"])
@pytest.mark.parametrize(
    "authority",
    [
        {"targetRuntime": "omnigent"},
        {"agentProfile": {"profileId": "missing-custom-profile"}},
        {"profileId": "missing-explicit-profile"},
        {"mergeAutomation": {"enabled": True, "finishMode": "review_only"}},
        {"mergeAutomation": {"enabled": True, "finishMode": "fix_only"}},
        {"mergeAutomation": {"enabled": True, "finishMode": "merge"}},
    ],
)
async def test_tool_authority_is_not_admitted_as_omitted_selection(
    saved_profile_submission, monkeypatch, entrypoint, authority
):
    context = saved_profile_submission
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    await context.session.execute(delete(ManagedAgentProviderProfile))
    await context.session.commit()
    parameters = {
        "workflow": {
            "instructions": "Resolve target",
            "steps": [
                {
                    "type": "tool",
                    "tool": {
                        "id": "github.resolve_pull_request_target",
                        "inputs": {"pullRequest": "350"},
                    },
                }
            ],
            "publish": {"mode": "none"},
        },
        **authority,
    }
    if entrypoint == "recurring":
        parameters["schedule"] = {"mode": "recurring", "cron": "0 6 * * *"}
    response = await context.client.post(
        "/api/executions",
        json=(
            {"workflowType": "MoonMind.UserWorkflow", "initialParameters": parameters}
            if entrypoint == "raw"
            else {"type": "workflow", "payload": parameters}
        ),
    )
    assert response.status_code in {404, 409, 422}, response.text
    context.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters_key", ["initialParameters", "initial_parameters"])
@pytest.mark.parametrize(
    "authority",
    [
        {"inputArtifactRef": "art_input"},
        {"input_artifact_ref": "art_input"},
        {"planArtifactRef": "art_plan"},
        {"plan_artifact_ref": "art_plan"},
        {"manifestArtifactRef": "art_manifest"},
        {"manifest_artifact_ref": "art_manifest"},
        {"manifest_ref": "art_manifest"},
        {"agentProfileSnapshot": {"profileId": "recorded"}},
        {"omnigentExecutionPlan": {"artifactRef": "recorded"}},
        {"runtimeCutover": {"selectionSource": "recorded"}},
        {"runtime": {"model": None, "effort": None}},
        {"runtime": {"profileSelector": {"tagsAll": ["private"]}}},
    ],
)
async def test_nested_schedule_outer_authority_cannot_skip_runtime_admission(
    saved_profile_submission, monkeypatch, parameters_key, authority
):
    from fastapi import HTTPException

    from api_service.api.routers.executions import _resolve_recurring_runtime_selection

    context = saved_profile_submission
    monkeypatch.setenv("MOONMIND_CODEX_DIRECT_RETIRED_AT", "2026-01-01T00:00:00Z")
    request = {
        "workflowType": "MoonMind.UserWorkflow",
        parameters_key: {
            "workflow": {
                "steps": [{"type": "tool", "tool": {"id": "document.discover"}}]
            },
        },
        **authority,
    }
    with pytest.raises(HTTPException) as error:
        await _resolve_recurring_runtime_selection(request, session=context.session)
    assert error.value.status_code == 422
    assert "retir" in str(error.value.detail).lower()
    context.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "artifact_key", ["inputArtifactRef", "planArtifactRef", "manifest_artifact_ref"]
)
async def test_nested_schedule_http_preserves_outer_artifact_admission(
    saved_profile_submission, monkeypatch, artifact_key
):
    from api_service.api.routers import executions
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService,
    )

    context = saved_profile_submission
    monkeypatch.setenv("MOONMIND_CODEX_DIRECT_RETIRED_AT", "2026-01-01T00:00:00Z")
    # Isolate runtime admission after the artifact-access owner has admitted
    # the supplied reference. This test does not replace artifact ACL coverage.
    monkeypatch.setattr(
        executions, "_validate_model_selection_submission", AsyncMock(return_value={})
    )
    create = AsyncMock(
        side_effect=AssertionError("must reject before schedule creation")
    )
    monkeypatch.setattr(RecurringWorkflowsService, "create_definition", create)
    response = await context.client.post(
        "/api/executions",
        json={
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {
                "workflow": {
                    "instructions": "Discover documents.",
                    "steps": [
                        {"type": "tool", "tool": {"id": "document.discover"}},
                    ],
                }
            },
            artifact_key: "art_recorded_authority",
            "schedule": {"mode": "recurring", "cron": "0 6 * * *"},
        },
    )
    assert response.status_code == 422, response.text
    assert "retir" in response.text.lower()
    create.assert_not_awaited()
    context.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authority",
    [
        {"agentProfile": {"profileId": "custom-profile"}},
        {"runtime": {"agentProfile": {"profileId": "custom-profile"}}},
        {"providerProfileRef": "saved-codex_cli"},
        {"profileId": "saved-codex_cli"},
        {"providerProfile": "saved-codex_cli"},
        {"executionProfileRef": "saved-codex_cli"},
        {"execution_profile_ref": "saved-codex_cli"},
        {"profile_id": "saved-codex_cli"},
        {"provider_profile": "saved-codex_cli"},
        {"requestedTargetId": "codex.generic-omnigent"},
        {"requested_target_id": "codex.generic-omnigent"},
    ],
)
async def test_nested_schedule_selection_aliases_reach_ordinary_admission(
    saved_profile_submission, monkeypatch, authority
):
    from fastapi import HTTPException

    from api_service.api.routers import executions

    context = saved_profile_submission

    def admit(**kwargs):
        assert kwargs["unselected_tools"] is False
        raise HTTPException(418, "ordinary agent admission reached")

    monkeypatch.setattr(executions, "resolve_new_work_selection", admit)
    with pytest.raises(HTTPException) as error:
        await executions._resolve_recurring_runtime_selection(
            {
                "workflowType": "MoonMind.UserWorkflow",
                "initialParameters": {
                    "workflow": {
                        "steps": [
                            {"type": "tool", "tool": {"id": "document.discover"}},
                        ]
                    }
                },
                **authority,
            },
            session=context.session,
        )
    assert error.value.status_code == 418
    context.service._client_adapter.start_workflow.assert_not_awaited()
