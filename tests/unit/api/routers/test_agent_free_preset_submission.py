"""Real preset expansion does not attach agent account intent to tool workflows."""

import pytest

from api_service.db.models import ManagedAgentProviderProfile
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.runtime_intent import provider_profile_ref_from_mapping
from moonmind.workflows.executions.execution_contract import workflow_requires_agent
from moonmind.workflows.executions.preset_expansion import has_unexpanded_task_template
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    agent_requests,
    saved_profile_submission as _saved_profile_submission,
    stored_parameters,
)


saved_profile_submission = _saved_profile_submission


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_profile", [None, "explicit-missing"])
@pytest.mark.parametrize("saved_exists", [True, False], ids=["saved", "missing-saved"])
@pytest.mark.parametrize("runtime", [None, "codex_cli"], ids=["omitted", "explicit"])
async def test_tool_only_preset_ignores_saved_account(
    saved_profile_submission, saved_exists, runtime, explicit_profile
):
    context = saved_profile_submission
    catalog = PresetCatalogService(context.session)
    await catalog.create_template(
        slug="native-only-test",
        title="Native tools only",
        description="Discover documents without launching an agent.",
        scope="global",
        scope_ref=None,
        tags=[],
        inputs_schema=[],
        steps=[
            {
                "type": "tool",
                "title": "Discover",
                "instructions": "Find documents in the current workspace.",
                "tool": {"id": "document.discover", "inputs": {"directory": "."}},
            }
        ],
    )
    if not saved_exists:
        saved = await context.session.get(
            ManagedAgentProviderProfile, "saved-codex_cli"
        )
        await context.session.delete(saved)
        await context.session.commit()
    task = {
        "instructions": "Find documents in the current workspace.",
        "taskTemplate": {"slug": "native-only-test", "scope": "global"},
        "publish": {"mode": "none"},
    }
    if runtime is not None:
        task["runtime"] = {"mode": runtime}
    if explicit_profile:
        task.setdefault("runtime", {})["providerProfileRef"] = explicit_profile
    response = await context.client.post(
        "/api/executions", json={"type": "workflow", "payload": {"workflow": task}}
    )
    if explicit_profile:
        assert response.status_code == 422, response.text
        context.service._client_adapter.start_workflow.assert_not_awaited()
        return
    parameters = await stored_parameters(context, response)
    expanded = parameters["workflow"]
    assert expanded["steps"]
    assert expanded["taskTemplate"]["slug"] == "native-only-test"
    assert not has_unexpanded_task_template(parameters)
    assert not workflow_requires_agent(expanded, parameters)
    assert not agent_requests(parameters)
    assert provider_profile_ref_from_mapping(expanded.get("runtime") or {}) is None
    for step in expanded["steps"]:
        assert provider_profile_ref_from_mapping(step.get("runtime") or {}) is None
