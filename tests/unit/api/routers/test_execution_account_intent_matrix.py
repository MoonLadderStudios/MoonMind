"""Independent account selector matrix, real HTTP/SQLite/services to pool matcher."""

from itertools import combinations
from unittest.mock import AsyncMock, patch

from moonmind.runtime_intent import provider_profile_ref_from_mapping
import pytest
import pytest_asyncio
from sqlalchemy import select
from api_service.db.models import ManagedAgentProviderProfile
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
)
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    saved_profile_submission as _saved_profile_submission,
    stored_parameters,
    agent_requests,
)

saved_profile_submission = _saved_profile_submission

ALIASES = [
    "providerProfileRef",
    "profileId",
    "providerProfile",
    "executionProfileRef",
    "execution_profile_ref",
    "profile_id",
    "provider_profile",
]


@pytest_asyncio.fixture
async def matrix_context(saved_profile_submission, monkeypatch):
    ctx = saved_profile_submission
    schedule = AsyncMock(return_value="mm-schedule:matrix")
    monkeypatch.setattr(TemporalClientAdapter, "create_schedule", schedule)
    monkeypatch.setattr(
        TemporalClientAdapter,
        "resolve_workflow_task_queue",
        lambda *a, **k: "mm.workflow.user.v2",
    )
    ctx.schedule = schedule
    return ctx


def body_for(entrypoint, task):
    parameters = {"workflow": task, "targetRuntime": "codex_cli", "publishMode": "none"}
    if entrypoint == "raw":
        return {
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": parameters,
        }
    if entrypoint == "recurring":
        parameters["schedule"] = {"mode": "recurring", "cron": "0 6 * * *"}
    return {"type": "workflow", "payload": parameters}


async def submit(ctx, entrypoint, task):
    response = await ctx.client.post("/api/executions", json=body_for(entrypoint, task))
    assert response.status_code == 201, response.text
    if entrypoint == "recurring":
        return ctx.schedule.await_args.kwargs["workflow_input"]["initial_parameters"]
    return await stored_parameters(ctx, response)


async def choose(ctx, request):
    manager = MoonMindProviderProfileManagerWorkflow()
    manager._runtime_id = "codex_cli"
    profiles = await ctx.session.scalars(
        select(ManagedAgentProviderProfile).where(
            ManagedAgentProviderProfile.runtime_id == "codex_cli"
        )
    )
    manager._profiles = {
        p.profile_id: ProfileSlotState(
            profile_id=p.profile_id,
            provider_id=p.provider_id,
            enabled=p.enabled,
            is_default=p.is_default,
            tags=p.tags,
            max_parallel_runs=p.max_parallel_runs,
            cooldown_after_429_seconds=p.cooldown_after_429_seconds,
            rate_limit_policy="backoff",
        )
        for p in profiles
    }
    with patch(
        "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow.patched",
        return_value=True,
    ):
        p = manager._find_available_profile(
            selector=request.profile_selector.model_dump(
                by_alias=True, exclude_none=True
            ),
            execution_profile_ref=request.execution_profile_ref,
        )
    return None if p is None else p.profile_id


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "api", "recurring"])
@pytest.mark.parametrize("alias", ALIASES)
async def test_root_alias_reaches_pool(matrix_context, entrypoint, alias):
    ctx = matrix_context
    recorded = await submit(
        ctx,
        entrypoint,
        {
            "instructions": "Select explicit account",
            "runtime": {"mode": "codex_cli", alias: "alternative-codex_cli"},
        },
    )
    request = next(iter(agent_requests(recorded).values()))
    assert request.execution_profile_ref == "alternative-codex_cli", recorded
    assert await choose(ctx, request) == "alternative-codex_cli"


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "api", "recurring"])
@pytest.mark.parametrize("alias", ALIASES)
@pytest.mark.parametrize(
    "child_selector",
    [None, {"tagsAll": ["alternative"]}],
    ids=["pin-only", "pin-and-selector"],
)
async def test_child_pin_replaces_parent_selector(
    matrix_context, entrypoint, alias, child_selector
):
    ctx = matrix_context
    child = {alias: "alternative-codex_cli"}
    if child_selector is not None:
        child["profileSelector"] = child_selector
    recorded = await submit(
        ctx,
        entrypoint,
        {
            "instructions": "Keep account selection local",
            "runtime": {"mode": "codex_cli", "profileSelector": {"tagsAll": ["saved"]}},
            "steps": [
                {"id": "omitted", "instructions": "Inherit parent pool"},
                {"id": "selected", "instructions": "Use exact B", "runtime": child},
            ],
        },
    )
    requests = agent_requests(recorded)
    assert (
        requests["selected"].execution_profile_ref == "alternative-codex_cli"
    ), recorded
    assert requests["selected"].profile_selector.tags_all == (
        [] if child_selector is None else ["alternative"]
    ), recorded
    assert await choose(ctx, requests["selected"]) == "alternative-codex_cli"
    assert requests["omitted"].execution_profile_ref is None, recorded
    assert requests["omitted"].profile_selector.tags_all == ["saved"]
    assert await choose(ctx, requests["omitted"]) == "saved-codex_cli"


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "api", "recurring"])
async def test_agent_free_ignores_missing_saved_account(matrix_context, entrypoint):
    ctx = matrix_context
    saved = await ctx.session.get(ManagedAgentProviderProfile, "saved-codex_cli")
    await ctx.session.delete(saved)
    await ctx.session.commit()
    ctx.settings_reads.clear()
    task = {
        "instructions": "Resolve a pull request without agent work",
        "runtime": {"mode": "codex_cli"},
        "publish": {"mode": "none"},
        "steps": [
            {
                "id": "lookup",
                "type": "tool",
                "repositoryOperation": "read",
                "tool": {
                    "id": "github.resolve_pull_request_target",
                    "inputs": {"pullRequest": "831"},
                },
            }
        ],
    }
    recorded = await submit(ctx, entrypoint, task)
    assert not agent_requests(recorded), recorded
    assert "saved-codex_cli" not in str(recorded)
    assert len(ctx.settings_reads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "api", "recurring"])
@pytest.mark.parametrize("alias", ALIASES)
async def test_root_alias_ownership_is_consistent(matrix_context, entrypoint, alias):
    ctx = matrix_context
    response = await ctx.client.post(
        "/api/executions",
        json=body_for(
            entrypoint,
            {
                "instructions": "Reject foreign runtime account",
                "runtime": {"mode": "codex_cli", alias: "alternative-claude_code"},
            },
        ),
    )
    assert response.status_code == 409, response.text
    ctx.service._client_adapter.start_workflow.assert_not_awaited()
    ctx.schedule.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "api", "recurring"])
@pytest.mark.parametrize(
    "selector",
    [
        {},
        None,
        {
            "providerId": None,
            "tagsAny": [],
            "tagsAll": [],
            "runtimeMaterializationMode": None,
        },
    ],
    ids=["empty", "null", "normalized-empty"],
)
async def test_empty_child_selector_preserves_parent_selection(
    matrix_context, entrypoint, selector
):
    ctx = matrix_context
    recorded = await submit(
        ctx,
        entrypoint,
        {
            "instructions": "Keep unchanged parent criteria",
            "runtime": {
                "mode": "codex_cli",
                "profileId": "saved-codex_cli",
                "profileSelector": {"tagsAll": ["saved"]},
            },
            "steps": [
                {"id": "omitted", "instructions": "Inherited"},
                {
                    "id": "selected",
                    "instructions": "Still inherited",
                    "runtime": {"profileSelector": selector},
                },
            ],
        },
    )
    requests = agent_requests(recorded)
    for request in requests.values():
        assert request.execution_profile_ref == "saved-codex_cli", recorded
        assert request.profile_selector.tags_all == ["saved"], recorded
        assert await choose(ctx, request) == "saved-codex_cli"


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "api", "recurring"])
@pytest.mark.parametrize("alias", ALIASES)
@pytest.mark.parametrize("layer", ["task", "parameters"])
async def test_outer_account_alias_reaches_manager(
    matrix_context, entrypoint, alias, layer
):
    ctx = matrix_context
    task = {
        "instructions": "Select exact account at supported layer",
        "runtime": {"mode": "codex_cli"},
    }
    body = body_for(entrypoint, task)
    parameters = body.get("initialParameters", body.get("payload"))
    (task if layer == "task" else parameters)[alias] = "alternative-codex_cli"
    response = await ctx.client.post("/api/executions", json=body)
    assert response.status_code == 201, response.text
    recorded = (
        ctx.schedule.await_args.kwargs["workflow_input"]["initial_parameters"]
        if entrypoint == "recurring"
        else await stored_parameters(ctx, response)
    )
    request = next(iter(agent_requests(recorded).values()))
    assert request.execution_profile_ref == "alternative-codex_cli"
    assert await choose(ctx, request) == "alternative-codex_cli"
    assert len(ctx.settings_reads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "api", "recurring"])
@pytest.mark.parametrize("alias", ALIASES)
async def test_step_alias_ownership_matches_existing_admission_boundary(
    matrix_context, entrypoint, alias
):
    ctx = matrix_context
    task = {
        "instructions": "Reject foreign account",
        "runtime": {"mode": "codex_cli"},
        "steps": [
            {"id": "one", "instructions": "Omitted account"},
            {
                "id": "two",
                "instructions": "Wrong account",
                "runtime": {alias: "alternative-claude_code"},
            },
        ],
    }
    response = await ctx.client.post("/api/executions", json=body_for(entrypoint, task))
    # Raw and recurring steps defer exact-account matching to dispatch;
    # immediate envelope creation additionally validates step ownership.
    if entrypoint != "api":
        assert response.status_code == 201, response.text
        recorded = (
            ctx.schedule.await_args.kwargs["workflow_input"]["initial_parameters"]
            if entrypoint == "recurring"
            else await stored_parameters(ctx, response)
        )
        requests = agent_requests(recorded)
        assert requests["two"].execution_profile_ref == "alternative-claude_code"
        assert await choose(ctx, requests["two"]) is None
        assert await choose(ctx, requests["one"]) == "saved-codex_cli"
    else:
        assert response.status_code == 409, response.text
        ctx.service._client_adapter.start_workflow.assert_not_awaited()
        ctx.schedule.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "api", "recurring"])
async def test_child_pool_replaces_parent_exact_and_pool(matrix_context, entrypoint):
    ctx = matrix_context
    recorded = await submit(
        ctx,
        entrypoint,
        {
            "instructions": "Choose a child pool",
            "runtime": {
                "mode": "codex_cli",
                "profileId": "saved-codex_cli",
                "profileSelector": {"tagsAll": ["saved"]},
            },
            "steps": [
                {"id": "omitted", "instructions": "Keep parent"},
                {
                    "id": "selected",
                    "instructions": "Choose pool",
                    "runtime": {"profileSelector": {"tagsAll": ["alternative"]}},
                },
            ],
        },
    )
    requests = agent_requests(recorded)
    assert requests["selected"].execution_profile_ref is None
    assert await choose(ctx, requests["selected"]) == "alternative-codex_cli"
    assert requests["omitted"].execution_profile_ref == "saved-codex_cli"
    assert await choose(ctx, requests["omitted"]) == "saved-codex_cli"


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "api", "recurring"])
async def test_agent_free_omnigent_default_retains_product_admission(
    matrix_context, monkeypatch, entrypoint
):
    from moonmind.config.settings import settings

    ctx = matrix_context
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    saved = await ctx.session.get(ManagedAgentProviderProfile, "saved-codex_cli")
    await ctx.session.delete(saved)
    await ctx.session.commit()
    task = {
        "instructions": "Direct tool only",
        "publish": {"mode": "none"},
        "steps": [
            {
                "id": "lookup",
                "type": "tool",
                "repositoryOperation": "read",
                "tool": {
                    "id": "github.resolve_pull_request_target",
                    "inputs": {"pullRequest": "831"},
                },
            }
        ],
    }
    body = body_for(entrypoint, task)
    body.get("initialParameters", body.get("payload")).pop("targetRuntime")
    response = await ctx.client.post("/api/executions", json=body)
    assert "saved-codex_cli" not in response.text
    if entrypoint == "raw":
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "omnigent_product_boundary_required"
    else:
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "default Omnigent Agent Profile is unavailable"
        )
    assert len(ctx.settings_reads) == 1
    ctx.schedule.assert_not_awaited()
    ctx.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "layer", ["task", "runtime", "parameters", "parameter_runtime"]
)
async def test_agent_free_raw_explicit_agent_profile_still_uses_product_boundary(
    matrix_context, layer
):
    ctx = matrix_context
    task = {
        "instructions": "Direct tool with explicit agent authority",
        "steps": [
            {
                "type": "tool",
                "tool": {
                    "id": "github.resolve_pull_request_target",
                    "inputs": {"pullRequest": "831"},
                },
            }
        ],
    }
    parameters = {"task": task, "targetRuntime": "omnigent"}
    source = {
        "task": task,
        "parameters": parameters,
        "runtime": task.setdefault("runtime", {}),
        "parameter_runtime": parameters.setdefault("runtime", {}),
    }[layer]
    source["agentProfile"] = {"profileId": "explicit-agent", "version": 1}
    response = await ctx.client.post(
        "/api/executions",
        json={"workflowType": "MoonMind.UserWorkflow", "initialParameters": parameters},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "omnigent_product_boundary_required"
    ctx.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("authority", ["agentProfileSnapshot", "omnigentExecutionPlan"])
@pytest.mark.parametrize("value", [None, {}, "invalid"])
async def test_agent_free_raw_authority_does_not_bypass_product_boundary(
    matrix_context, authority, value
):
    ctx = matrix_context
    parameters = {
        "targetRuntime": "omnigent",
        authority: value,
        "task": {
            "instructions": "Direct tool",
            "steps": [
                {
                    "type": "tool",
                    "tool": {
                        "id": "github.resolve_pull_request_target",
                        "inputs": {"pullRequest": "831"},
                    },
                }
            ],
        },
    }
    response = await ctx.client.post(
        "/api/executions",
        json={"workflowType": "MoonMind.UserWorkflow", "initialParameters": parameters},
    )
    assert response.status_code == 422, response.text
    ctx.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.parametrize("preferred,lower", list(combinations(ALIASES, 2)))
def test_all_alias_pairs_choose_preferred(preferred, lower):
    assert provider_profile_ref_from_mapping({lower: "A", preferred: "B"}) == "B"
    assert (
        provider_profile_ref_from_mapping({lower: "A", preferred: "missing-preferred"})
        == "missing-preferred"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("preferred,lower", list(zip(ALIASES, ALIASES[1:])))
@pytest.mark.parametrize("preferred_exists", [True, False], ids=["valid", "missing"])
async def test_alias_priority_survives_admission_and_matching(
    matrix_context, preferred, lower, preferred_exists
):
    ctx = matrix_context
    chosen = "alternative-codex_cli" if preferred_exists else "missing-preferred"
    task = {
        "instructions": "Respect exact authored alias order",
        "runtime": {"mode": "codex_cli", lower: "saved-codex_cli", preferred: chosen},
    }
    response = await ctx.client.post("/api/executions", json=body_for("api", task))
    if not preferred_exists:
        assert response.status_code == 422, response.text
        assert "missing-preferred" in response.text
        ctx.service._client_adapter.start_workflow.assert_not_awaited()
        return
    recorded = await stored_parameters(ctx, response)
    request = next(iter(agent_requests(recorded).values()))
    assert request.execution_profile_ref == chosen, recorded
    assert await choose(ctx, request) == chosen
