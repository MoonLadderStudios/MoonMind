"""Replay the captured admission-epoch conflict against persistent authorities.

AgentRun's signal gateway is controlled; runtime and bridge storage are real
PostgreSQL, and a loopback HTTP endpoint owns the one simulated provider turn.
Worker shutdown against the real Temporal server is covered alongside this in
test_worker_replacement_journey.py.
"""

import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from api_service.db import models
from moonmind.omnigent.bridge_store import (
    OmnigentBridgeSessionStore,
    OmnigentIdempotencyError,
)
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.runtime_bindings import DbRuntimeBindingStore
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.omnigent.test_generic_platform_production_services import _exact_plan
from tests.unit.workflows.temporal.workflows.test_agent_run_omnigent_capacity_admission import (
    _admission,
    _capture_release_signals,
    _configure_workflow_runtime,
    _ExecutingRun,
    _omnigent_request,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize("resume_enabled", [False, True])
async def test_lost_ack_keeps_one_admission_across_runtime_and_bridge(
    monkeypatch, resume_enabled
):
    from moonmind.workflows.temporal.workflows import agent_run as agent_run_module

    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "patched",
        lambda marker: (
            resume_enabled if marker == "omnigent-resume-owned-admission-v1" else True
        ),
    )
    provider_turns = {}

    async def provider(reader, writer):
        try:
            headers = (await reader.readuntil(b"\r\n\r\n")).decode().split("\r\n")
            length = int(
                next(
                    line.split(":", 1)[1]
                    for line in headers
                    if line.lower().startswith("content-length:")
                )
            )
            body = json.loads(await reader.readexactly(length))
            key = body["idempotencyKey"]
            provider_turns.setdefault(
                key,
                {"sessionId": "controlled-session", "summary": "same turn completed"},
            )
            payload = json.dumps(provider_turns[key]).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\n\r\n"
                + payload
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(provider, "127.0.0.1", 0)
    address = "http://127.0.0.1:" + str(server.sockets[0].getsockname()[1])
    tables = [
        models.OmnigentExecutionPlanRecord.__table__,
        models.OmnigentRuntimeBindingRecord.__table__,
        models.OmnigentBridgeSession.__table__,
    ]
    try:
        async with isolated_postgres(tables) as sessions, httpx.AsyncClient() as client:
            plan = _exact_plan("opencode-go/model")
            await DbExecutionPlanStore(sessions).persist(plan)

            class Run(_ExecutingRun):
                async def _execute_routed_activity(self, name, payload=None, **kwargs):
                    if not name.startswith("integration.omnigent."):
                        return await super()._execute_routed_activity(
                            name, payload, **kwargs
                        )
                    self.executions.append(payload)
                    # These owners are reconstructed on every Activity delivery.
                    binding = await DbRuntimeBindingStore(sessions).create_initial(
                        execution_plan_ref=plan.planRef,
                        idempotency_key=payload.idempotency_key,
                        admission_epoch=payload.admitted_provider_capacity.admission_epoch,
                        provider_leases={"selected": {"leaseId": "one-provider-lease"}},
                    )
                    await OmnigentBridgeSessionStore(
                        sessions
                    ).bind_profile_authorization(
                        request=payload,
                        endpoint_ref=address,
                        provider_profile_id="opencode-zen-free",
                        provider_lease_id="one-provider-lease",
                        credential_generation=7,
                        host_binding_ref="one-host",
                        host_lease_ref="one-host-lease",
                        omnigent_host_id="controlled-host",
                        effective_launch_snapshot={
                            "executionPlanRef": plan.planRef,
                            "runtimeBindingRef": binding.bindingId,
                            "executionRealizerRef": "generic-omnigent-host@1",
                        },
                    )
                    response = await client.post(
                        address, json={"idempotencyKey": payload.idempotency_key}
                    )
                    response.raise_for_status()
                    if len(self.executions) == 1:
                        raise TimeoutError(
                            "captured heartbeat acknowledgement loss after provider completion"
                        )
                    return response.json()

            run = Run([])
            _capture_release_signals(monkeypatch, run)

            async def execute():
                return await run._execute_omnigent_with_admitted_capacity(
                    act_name="integration.omnigent.execute",
                    request=_omnigent_request(plan_ref_parameter=plan.planRef),
                    admission=_admission(),
                    parent_info=None,
                    stc_seconds=600,
                    admit_capacity_before_activity=True,
                    execution_plan_admission=True,
                )

            with pytest.raises(TimeoutError):
                await execute()
            if resume_enabled:
                assert not any(name == "release_slot" for name, _ in run.signals)
                result, _ = await execute()
                assert result["summary"] == "same turn completed"
                assert run.executions[0] == run.executions[1]
            else:
                # The unchanged guard reproduces the original production fault.
                with pytest.raises(
                    OmnigentIdempotencyError, match="another launch snapshot"
                ):
                    await execute()
            async with sessions() as session:
                bindings = (
                    (await session.execute(select(models.OmnigentRuntimeBindingRecord)))
                    .scalars()
                    .all()
                )
                bridges = (
                    (await session.execute(select(models.OmnigentBridgeSession)))
                    .scalars()
                    .all()
                )
            assert len(bindings) == (1 if resume_enabled else 2)
            assert len(bridges) == 1
            assert len(provider_turns) == 1
            assert sum(name == "request_slot" for name, _ in run.signals) == (
                1 if resume_enabled else 2
            )
    finally:
        server.close()
        await server.wait_closed()
