"""The unified setting pair reaches real authoring and account consumers."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from api_service.api.routers.workflow_console_view_model import build_runtime_config
from api_service.db.models import ManagedAgentProviderProfile
from moonmind.workflow_cli import build_execution_payload
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
)
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    agent_requests,
    stored_parameters,
)
from tests.unit.api.routers.test_settings_generations import (
    PROFILE,
    RUNTIME,
    catalog,
    save,
)
from tests.unit.api.routers.test_settings_generations import (
    defaults_context as _defaults_context,
)
from tests.unit.api.routers.test_settings_generations import (
    saved_profile_submission as _saved_profile_submission,
)

saved_profile_submission = _saved_profile_submission
defaults_context = _defaults_context


async def _selected_account(context, request, runtime):
    manager = MoonMindProviderProfileManagerWorkflow()
    manager._runtime_id = runtime
    profiles = await context.session.scalars(
        select(ManagedAgentProviderProfile).where(
            ManagedAgentProviderProfile.runtime_id == runtime
        )
    )
    manager._profiles = {
        row.profile_id: ProfileSlotState(
            profile_id=row.profile_id,
            provider_id=row.provider_id,
            enabled=row.enabled,
            is_default=row.is_default,
            tags=row.tags,
            max_parallel_runs=row.max_parallel_runs,
            cooldown_after_429_seconds=row.cooldown_after_429_seconds,
            rate_limit_policy="backoff",
        )
        for row in profiles
    }
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow.patched",
        return_value=True,
    ):
        selected = manager._find_available_profile(
            selector=request.profile_selector.model_dump(
                by_alias=True, exclude_none=True
            ),
            execution_profile_ref=request.execution_profile_ref,
        )
    return selected.profile_id if selected else None


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
@pytest.mark.parametrize("selection", ["profile", "preferred"])
@pytest.mark.parametrize("entrypoint", ["api", "raw", "cli", "recurring"])
async def test_saved_pair_reaches_create_and_launch_consumers(
    defaults_context, monkeypatch, runtime, selection, entrypoint
):
    context = defaults_context
    profile = f"saved-{runtime}" if selection == "profile" else None
    response = await save(context, runtime, profile)
    assert response.status_code == 200, response.text
    defaults = await context.catalog.workflow_creation_defaults()
    assert defaults.default_runtime == runtime
    assert defaults.provider_profile_ref == profile
    config = build_runtime_config(
        "/workflows/create",
        default_runtime_override=defaults.default_runtime,
        default_provider_profile_ref=defaults.provider_profile_ref,
    )
    assert config["system"]["defaultRuntime"] == runtime
    assert config["system"]["providerProfiles"].get("defaultProfileRef") == profile

    task = {
        "instructions": "Use the saved new-work default.",
        "publish": {"mode": "none"},
    }
    if entrypoint == "cli":
        body = build_execution_payload(
            instructions=task["instructions"], publish_mode="none"
        )
    elif entrypoint == "raw":
        body = {
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {"workflow": task},
        }
    else:
        body = {"type": "workflow", "payload": {"workflow": task}}
    schedule = AsyncMock(return_value="mm-schedule:unified-default")
    if entrypoint == "recurring":
        monkeypatch.setattr(TemporalClientAdapter, "create_schedule", schedule)
        monkeypatch.setattr(
            TemporalClientAdapter,
            "resolve_workflow_task_queue",
            lambda *args, **kwargs: "mm.workflow.user.v2",
        )
        body["payload"]["schedule"] = {"mode": "recurring", "cron": "0 6 * * *"}

    response = await context.client.post("/api/executions", json=body)
    assert response.status_code == 201, response.text
    parameters = (
        schedule.await_args.kwargs["workflow_input"]["initial_parameters"]
        if entrypoint == "recurring"
        else await stored_parameters(context, response)
    )
    assert parameters["targetRuntime"] == runtime
    requests = agent_requests(parameters)
    assert len(requests) == 1
    request = next(iter(requests.values()))
    expected = profile or f"alternative-{runtime}"
    assert await _selected_account(context, request, runtime) == expected


@pytest.mark.asyncio
async def test_retained_mismatch_is_read_without_migration_until_explicit_pair_save(
    defaults_context,
):
    context = defaults_context
    result = await save(context, "omnigent", "saved-claude_code")
    assert result.status_code == 200, result.text
    before = await catalog(context)
    defaults = await context.catalog.workflow_creation_defaults()
    assert defaults.default_runtime == "omnigent"
    assert defaults.provider_profile_ref == "saved-claude_code"
    config = build_runtime_config(
        "/workflows/create",
        default_runtime_override=defaults.default_runtime,
        default_provider_profile_ref=defaults.provider_profile_ref,
    )
    assert config["system"]["defaultRuntime"] == "omnigent"
    assert (
        config["system"]["providerProfiles"].get("defaultProfileRef")
        == "saved-claude_code"
    )
    assert await catalog(context) == before
    aligned = await save(
        context,
        "claude_code",
        "saved-claude_code",
        versions={key: before[key]["value_version"] for key in (RUNTIME, PROFILE)},
    )
    assert aligned.status_code == 200, aligned.text
    after = await catalog(context)
    assert after[RUNTIME]["effective_value"] == "claude_code"
    assert after[PROFILE]["effective_value"] == "saved-claude_code"


@pytest.mark.asyncio
async def test_opencode_choice_clears_profile_in_same_versioned_save(defaults_context):
    context = defaults_context
    result = await save(context, "omnigent", None)
    assert result.status_code == 200, result.text
    assert set(result.json()["values"]) == {RUNTIME, PROFILE}
    defaults = await context.catalog.workflow_creation_defaults()
    assert defaults.default_runtime == "omnigent"
    assert defaults.provider_profile_ref is None
