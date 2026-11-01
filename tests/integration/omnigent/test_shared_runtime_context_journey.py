"""Native context follows real shared ownership after the old host is gone.

The controlled provider implements Omnigent's fork, label and runner wire
contracts. Storage, planning, leases, credentials, saved-work restoration,
canonical delivery and cleanup use production services. Native transcript
conversion itself is covered separately against upstream's implementation.
"""

import asyncio
import copy
import hashlib
import json
import shutil

import pytest
from aiohttp import web

from moonmind.omnigent.runtime_bindings import RuntimeBindingState, stable_binding_id
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from tests.integration.omnigent import test_generic_native_durable_journey as native
native_journey = native.native_journey
session_factory = native.session_factory

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


class ContextNativeServer(native.NativeTurnServer):
    def __init__(self, harness):
        super().__init__(harness)
        self.session_records = {}
        self.frozen = {}
        self.current_session = None
        self.forks = []
        self.runner_launches = []

    def app(self):
        app = super().app()
        app.router.add_get("/v1/sessions", self.context_sessions)
        app.router.add_post("/v1/sessions/{source_id}/fork", self.context_fork)
        app.router.add_patch("/v1/sessions/{session_id}", self.context_patch)
        app.router.add_post("/v1/hosts/{host_id}/runners", self.context_runner)
        return app

    def snapshot(self, session_id):
        if session_id in self.frozen:
            return copy.deepcopy(self.frozen[session_id])
        payload = super().snapshot(session_id)
        payload.update(self.session_records.get(session_id, {}))
        return payload

    async def create_session(self, request):
        payload = await request.json()
        response = await super().create_session(request)
        session_id = json.loads(response.body)["id"]
        self.current_session = session_id
        self.session_records[session_id] = {
            "title": payload.get("title"),
            "labels": payload.get("labels", {}),
            "host_id": self.host_id,
            "runner_id": "runner-" + session_id,
        }
        return response

    async def post_event(self, request):
        payload = await request.json()
        response = await super().post_event(request)
        if payload.get("type") == "stop_session":
            session_id = request.match_info["session_id"]
            self.frozen[session_id] = copy.deepcopy(self.snapshot(session_id))
            self.frozen[session_id]["runner_online"] = False
        return response

    async def context_sessions(self, request):
        return web.json_response(
            {
                "data": [self.snapshot(item) for item in self.session_ids],
                "has_more": False,
            }
        )

    async def context_fork(self, request):
        source_id, payload = request.match_info["source_id"], await request.json()
        assert source_id in self.frozen
        source = self.snapshot(source_id)
        self.forks.append((source_id, payload))
        self.current_session = "session-" + str(len(self.session_ids) + 1)
        self.session_ids.append(self.current_session)
        self.items = copy.deepcopy(source["items"])
        self.emitted_events = []
        self.status, self.turn_active = "idle", False
        self.session_records[self.current_session] = {
            "title": payload["title"],
            "host_id": None,
            "runner_id": None,
            "labels": {
                **source["labels"],
                "omnigent.fork.source_id": source_id,
                "omnigent.fork.carry_history": "1",
            },
        }
        return web.json_response(self.snapshot(self.current_session), status=201)

    async def context_patch(self, request):
        session_id, payload = request.match_info["session_id"], await request.json()
        assert session_id == self.current_session
        self.session_records[session_id]["labels"].update(payload["labels"])
        return web.json_response(self.snapshot(session_id))

    async def context_runner(self, request):
        host_id, payload = request.match_info["host_id"], await request.json()
        assert payload["session_id"] == self.current_session
        assert host_id == self.host_id
        self.runner_launches.append((host_id, payload))
        runner_id = "runner-" + self.current_session
        self.session_records[self.current_session].update(
            host_id=host_id, runner_id=runner_id, workspace=payload["workspace"]
        )
        return web.json_response({"runner_id": runner_id, "status": "launching"})


@pytest.fixture(autouse=True)
def context_provider(monkeypatch):
    monkeypatch.setattr(native, "NativeTurnServer", ContextNativeServer)


def _next_request(journey, result):
    request = journey.request
    step_id = request.step_execution.workflow_id + ":run:second:execution:1"
    payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload["idempotencyKey"] = "native-context-second"
    payload["stepExecution"].update(
        logicalStepId="second",
        stepExecutionId=step_id,
        runtimeContextPolicy="reuse_session_same_epoch",
        runtimeSessionContext=result.metadata["runtimeSessionContext"],
    )
    payload["admittedProviderCapacity"].update(
        stepExecutionId=step_id,
        idempotencyKey=payload["idempotencyKey"],
        agentRunRunId="agent-context-2",
    )
    payload["workspaceSpec"] = {
        "workspaceCheckpointRestoreRef": result.metadata["savedWorkspaceCheckpoint"][
            "archiveRef"
        ],
        "workspaceLocator": {
            "kind": "sandbox",
            "workspaceId": hashlib.sha256(
                f"{request.step_execution.workflow_id}:{step_id}".encode()
            ).hexdigest()[:24],
            "relativePath": "repo",
        },
    }
    return AgentExecutionRequest.model_validate(payload)


@pytest.mark.parametrize(
    "native_journey", ["codex-native", "codex-native-api-key"], indirect=True
)
async def test_same_epoch_context_survives_host_and_worker_cleanup(native_journey):
    journey = native_journey
    first = await native.assert_native_turn_saved_and_cleaned(journey)
    source_id = journey.server.session_ids[0]
    original_items = copy.deepcopy(journey.server.frozen[source_id]["items"])
    source_ref = first.metadata["runtimeSessionContext"]["runtimeBindingRef"]
    source = await journey.services.runtime_binding_store.get(source_ref)
    successor = _next_request(journey, first)
    await journey.gateway.admit_execution_plan_inputs(
        request=successor, plan=journey.plan
    )
    assert not journey.server.workspace.exists()
    if journey.worker_root.exists():
        shutil.rmtree(journey.worker_root)
    assert not journey.worker_root.exists()
    journey.manager.request = successor
    result = await journey.realizer.execute(successor, journey.plan)
    assert result.failure_class is None, result
    assert len(journey.server.forks) == 1 and len(journey.server.create_payloads) == 1
    assert journey.server.forks[0][0] == source_id
    assert journey.server.items[: len(original_items)] == original_items
    assert len(journey.server.items) > len(original_items)
    assert (
        result.metadata["runtimeSessionContext"]["epoch"]
        == first.metadata["runtimeSessionContext"]["epoch"]
    )
    assert journey.docker.launch_env["MOONMIND_CODEX_REQUIRE_CONTEXT"] == "1"
    assert journey.docker.create_count == 2 and not journey.server.host_alive
    assert await journey.services.runtime_binding_store.get(source_ref) == source
    target_ref = result.metadata["runtimeSessionContext"]["runtimeBindingRef"]
    target = await journey.services.runtime_binding_store.get(target_ref)
    assert (
        target.state is RuntimeBindingState.cleaned
        and target.hostLeaseRef != source.hostLeaseRef
    )
    assert target.omnigentSessionId != source.omnigentSessionId
    before = journey.docker.create_count, len(journey.server.forks)
    journey.manager.request = journey.request
    replay = await journey.realizer.execute(journey.request, journey.plan)
    assert (
        replay.metadata["runtimeSessionContext"]
        == first.metadata["runtimeSessionContext"]
    )
    assert (journey.docker.create_count, len(journey.server.forks)) == before


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
async def test_cancelled_continuation_cleans_only_its_own_fork(
    native_journey, monkeypatch
):
    journey = native_journey
    first = await native.assert_native_turn_saved_and_cleaned(journey)
    source_ref = first.metadata["runtimeSessionContext"]["runtimeBindingRef"]
    source = await journey.services.runtime_binding_store.get(source_ref)
    successor = _next_request(journey, first)
    await journey.gateway.admit_execution_plan_inputs(
        request=successor, plan=journey.plan
    )
    journey.manager.request = successor
    monkeypatch.setattr(
        "moonmind.omnigent.activity_ownership.delivery_was_revoked", lambda: False
    )
    execution = asyncio.create_task(journey.realizer.execute(successor, journey.plan))

    async def cancel():
        execution.cancel()

    journey.server.on_first_message = cancel
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(execution, 30)
    assert len(journey.server.forks) == 1 and journey.docker.create_count == 2
    assert not journey.server.host_alive
    target = await journey.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=journey.plan.planRef,
            idempotency_key=successor.idempotency_key,
        )
    )
    assert target.state is RuntimeBindingState.cleaned
    assert await journey.services.runtime_binding_store.get(source_ref) == source
