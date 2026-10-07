"""Legacy launch reconciliation must be complete and ownership-scoped."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from moonmind.omnigent.session_launch import (
    OmnigentLaunchReconciliationError,
    prepare_workflow_session_create,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentResolvedTarget,
    build_omnigent_selection,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient


def _request():
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="wf",
        idempotencyKey="step",
        parameters={
            "omnigent": {
                "agent": {"agentId": "agent", "harnessOverride": "claude-native"},
                "session": {
                    "hostType": "external",
                    "hostId": "host",
                    "workspace": "/repo",
                },
            }
        },
    )


def _session():
    return {
        "id": "existing",
        "agent_id": "agent",
        "host_id": "host",
        "labels": {"moonmind.correlation_id": "wf", "moonmind.idempotency_key": "step"},
    }


async def _prepare(client, store):
    request = _request()
    return await prepare_workflow_session_create(
        request=request,
        selection=build_omnigent_selection(request),
        target=OmnigentResolvedTarget(agent_id="agent", source="agent_id"),
        client=client,
        bridge=SimpleNamespace(metadata_={}, omnigent_session_id=None),
        run_store=store,
        provider_idempotency_key="canonical-session",
    )


@pytest.mark.asyncio
async def test_reconciliation_finds_match_after_empty_provider_page():
    client = SimpleNamespace(
        list_sessions=AsyncMock(
            side_effect=[
                {"data": [], "last_id": "cursor-1", "has_more": True},
                {"data": [_session()], "last_id": "existing", "has_more": False},
            ]
        ),
        get_session=AsyncMock(return_value=_session()),
    )
    store = SimpleNamespace(freeze_workflow_launch_defaults=AsyncMock())
    payload, recovered = await _prepare(client, store)
    assert recovered == "existing"
    assert payload["idempotency_key"] == "canonical-session"
    assert client.list_sessions.await_args_list[1].kwargs == {
        "agent_id": "agent",
        "after": "cursor-1",
    }
    store.freeze_workflow_launch_defaults.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    [
        "missing_flag",
        "invalid_item",
        "missing_labels",
        "duplicate",
        "wrong_owner",
        "wrong_host",
        "wrong_agent",
        "repeated_cursor",
        "provider_unavailable",
    ],
)
async def test_incomplete_or_conflicting_lookup_does_not_freeze_new_defaults(problem):
    item = _session()
    snapshot = copy.deepcopy(item)
    pages = [{"data": [item], "last_id": "existing", "has_more": False}]
    if problem == "missing_flag":
        pages[0].pop("has_more")
    elif problem == "invalid_item":
        pages[0]["data"] = [None]
    elif problem == "missing_labels":
        item.pop("labels")
    elif problem == "duplicate":
        pages[0]["data"].append({**item, "id": "second"})
    elif problem == "wrong_owner":
        item["labels"]["moonmind.correlation_id"] = "another-workflow"
    elif problem == "wrong_host":
        snapshot["host_id"] = "another-host"
    elif problem == "wrong_agent":
        snapshot["agent_id"] = "another-agent"
    elif problem == "repeated_cursor":
        pages = [{"data": [], "last_id": "stuck", "has_more": True}] * 2
    client = SimpleNamespace(
        list_sessions=AsyncMock(side_effect=pages),
        get_session=AsyncMock(return_value=snapshot),
    )
    if problem == "provider_unavailable":
        client.list_sessions.side_effect = httpx.ConnectError("provider unavailable")
    store = SimpleNamespace(freeze_workflow_launch_defaults=AsyncMock())
    with pytest.raises(OmnigentLaunchReconciliationError):
        await _prepare(client, store)
    store.freeze_workflow_launch_defaults.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_lookup_page_budget_never_treats_truncation_as_absence(
    monkeypatch,
):
    monkeypatch.setattr("moonmind.omnigent.session_launch._MAX_LEGACY_SESSION_PAGES", 1)
    client = SimpleNamespace(
        list_sessions=AsyncMock(
            return_value={"data": [], "last_id": "next", "has_more": True}
        )
    )
    store = SimpleNamespace(freeze_workflow_launch_defaults=AsyncMock())
    with pytest.raises(OmnigentLaunchReconciliationError, match="page budget"):
        await _prepare(client, store)
    store.freeze_workflow_launch_defaults.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_listing_uses_confirmed_provider_pagination_contract():
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200, json={"data": [], "has_more": False, "last_id": None}
        )

    client = OmnigentHttpClient(
        base_url="https://omnigent.test", transport=httpx.MockTransport(respond)
    )
    assert await client.list_sessions(agent_id="agent /1", after="cursor /2") == {
        "data": [],
        "has_more": False,
        "last_id": None,
    }
    assert seen[0].url.path == "/v1/sessions"
    assert dict(seen[0].url.params) == {
        "limit": "1000",
        "kind": "any",
        "include_archived": "true",
        "agent_id": "agent /1",
        "after": "cursor /2",
    }


@pytest.mark.asyncio
async def test_lookup_failure_never_recommends_a_fresh_execution_key(
    monkeypatch, tmp_path
):
    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.omnigent.execute import run_omnigent_execution

    class Client:
        def __init__(self, **kwargs):
            pass

        async def create_session(self, payload):
            raise AssertionError("No create while predecessor authority is unknown")

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", Client)
    monkeypatch.setattr(
        "moonmind.omnigent.execute.prepare_workflow_session_create",
        AsyncMock(
            side_effect=OmnigentLaunchReconciliationError("provider lookup unavailable")
        ),
    )
    result = await run_omnigent_execution(
        _request(), artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path)
    )
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "omnigent_launch_reconciliation_failed"
    assert result.retry_recommendation is None


@pytest.mark.asyncio
async def test_legacy_reconciliation_never_searches_a_replacement_agent():
    request = _request()
    client = SimpleNamespace(list_sessions=AsyncMock())
    store = SimpleNamespace(freeze_workflow_launch_defaults=AsyncMock())
    with pytest.raises(OmnigentLaunchReconciliationError, match="saved agent"):
        await prepare_workflow_session_create(
            request=request,
            selection=build_omnigent_selection(request),
            target=OmnigentResolvedTarget(agent_id="agent", source="agent_id"),
            client=client,
            bridge=SimpleNamespace(
                metadata_={}, omnigent_agent_id="prior-agent", omnigent_session_id=None
            ),
            run_store=store,
            provider_idempotency_key="canonical-session",
        )
    client.list_sessions.assert_not_awaited()
    store.freeze_workflow_launch_defaults.assert_not_awaited()
