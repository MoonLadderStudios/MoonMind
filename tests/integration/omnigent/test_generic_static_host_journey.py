"""Static Profiles use the real shared plan, lease, native turn and save owners."""

import hashlib
import io
import json
import tarfile

import pytest
from sqlalchemy import select

from api_service.db.models import (
    OmnigentHostLeaseRecordV2,
    OmnigentOAuthHostLeaseRecord,
)
from moonmind.omnigent.runtime_bindings import RuntimeBindingState
from tests.integration.omnigent.test_generic_native_durable_journey import (
    native_journey as _native_journey,
    session_factory as _session_factory,
    GENERATED,
    RESULT_PATH,
)

native_journey = _native_journey
session_factory = _session_factory

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize(
    "native_journey",
    [
        {"harness": "codex-native", "static": True},
        {"harness": "claude-native", "static": True},
    ],
    indirect=True,
)
@pytest.mark.parametrize("terminal", ["idle", "failed"])
async def test_static_native_turn_preserves_physical_state_and_saved_result(
    native_journey, terminal
):
    scene = native_journey
    scene.server.terminal_status = terminal
    result = await scene.realizer.execute(scene.request, scene.plan)
    assert (result.failure_class is None) == (terminal == "idle")
    binding = await scene.services.runtime_binding_store.get(
        result.metadata["runtimeBindingRef"]
    )
    assert binding.state is RuntimeBindingState.cleaned
    lease = await scene.services.host_lease_repository.get(binding.hostLeaseRef)
    assert lease.status == "cleaned"
    assert scene.server.host_alive
    assert scene.server.host_id == scene.docker.retained_identity
    assert scene.server.host_name == "operator-static-host"
    assert scene.docker.volumes[scene.docker.state_volume] == {}
    assert "journey_oauth" in scene.docker.volumes
    assert not any(command[:2] == ("docker", "rm") for command in scene.docker.commands)
    assert not any(
        command[:3] == ("docker", "volume", "rm")
        and command[-1] == scene.docker.state_volume
        for command in scene.docker.commands
    )
    async with scene.session_factory() as session:
        assert (await session.scalars(select(OmnigentHostLeaseRecordV2))).all() == []
        physical = (await session.scalars(select(OmnigentOAuthHostLeaseRecord))).one()
        assert physical.lease_purpose == "execution_generic_static"
        assert physical.status == "stopped"
    saved = result.metadata["savedWorkspaceCheckpoint"]
    archive = await scene.gateway.read_bytes(saved["archiveRef"])
    assert saved["archiveDigest"] == "sha256:" + hashlib.sha256(archive).hexdigest()
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        assert bundle.extractfile(RESULT_PATH).read() == GENERATED
    cleanup = json.loads(
        await scene.gateway.read_text(binding.attestationRefs["cleanupAttestationRef"])
    )
    assert cleanup["results"]["host"]["staticHostDrained"] is True
    replay = await scene.realizer.execute(scene.request, scene.plan)
    assert replay.metadata["savedWorkspaceCheckpoint"] == saved
    assert scene.docker.create_count == 1
    assert len(scene.server.session_ids) == 1


@pytest.mark.parametrize(
    "native_journey",
    [
        {"harness": "codex-native", "static": True},
        {"harness": "claude-native", "static": True},
    ],
    indirect=True,
)
async def test_static_cancel_preserves_the_service_and_starts_no_successor(
    native_journey, monkeypatch
):
    import asyncio
    from moonmind.omnigent.runtime_bindings import stable_binding_id

    scene = native_journey
    monkeypatch.setattr(
        "moonmind.omnigent.activity_ownership.delivery_was_revoked", lambda: False
    )
    execution = asyncio.create_task(scene.realizer.execute(scene.request, scene.plan))

    async def cancel():
        execution.cancel()

    scene.server.on_first_message = cancel
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(execution, 30)
    binding = await scene.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=scene.plan.planRef,
            idempotency_key=scene.request.idempotency_key,
        )
    )
    assert binding.state is RuntimeBindingState.cleaned
    assert scene.server.host_alive
    assert len(scene.server.session_ids) == scene.docker.create_count == 1
    assert "journey_oauth" in scene.docker.volumes
    assert scene.docker.state_volume in scene.docker.volumes


@pytest.mark.parametrize(
    "native_journey",
    [
        {"harness": "codex-native", "static": True},
        {"harness": "claude-native", "static": True},
    ],
    indirect=True,
)
async def test_static_successor_restores_saved_work_and_retains_old_allocation(
    native_journey, monkeypatch
):
    import shutil
    from pathlib import Path
    from moonmind.omnigent.host_services import docker_backend
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    scene = native_journey
    scene.server.terminal_status = "failed"
    failed = await scene.realizer.execute(scene.request, scene.plan)
    first = await scene.services.runtime_binding_store.get(
        failed.metadata["runtimeBindingRef"]
    )
    saved = failed.metadata["savedWorkspaceCheckpoint"]
    shutil.rmtree(scene.worker_root)
    workflow_id = scene.request.step_execution.workflow_id
    step_id = workflow_id + ":run:implement:execution:2"
    payload = scene.request.model_dump(by_alias=True, mode="json", exclude_none=True)
    payload["idempotencyKey"] = "native-turn-2"
    payload["stepExecution"].update(executionOrdinal=2, stepExecutionId=step_id)
    payload["admittedProviderCapacity"].update(
        stepExecutionId=step_id,
        idempotencyKey="native-turn-2",
        agentRunRunId="agent-run-2",
    )
    payload["workspaceSpec"] = {
        "workspaceCheckpointRestoreRef": saved["archiveRef"],
        "workspaceLocator": {
            "kind": "sandbox",
            "workspaceId": hashlib.sha256(
                f"{workflow_id}:{step_id}".encode()
            ).hexdigest()[:24],
            "relativePath": "repo",
        },
    }
    successor = AgentExecutionRequest.model_validate(payload)
    scene.manager.request = successor
    await scene.gateway.admit_execution_plan_inputs(request=successor, plan=scene.plan)
    restored = []
    command = scene.docker.command

    async def observe(argv, **kwargs):
        if list(argv[:2]) == ["docker", "compose"] and "up" in argv:
            definition = next(
                iter(json.loads(kwargs["input_bytes"])["services"].values())
            )
            workspace = next(
                mount["source"]
                for mount in definition["volumes"]
                if mount["target"] == "/workspaces/run"
            )
            restored.append((Path(workspace) / RESULT_PATH).read_bytes())
        return await command(argv, **kwargs)

    monkeypatch.setattr(docker_backend, "run_runtime_command", observe)
    scene.server.events, scene.server.items = [], []
    scene.server.status = scene.server.terminal_status = "idle"
    result = await scene.realizer.execute(successor, scene.plan)
    assert result.failure_class is None, result
    assert restored == [GENERATED]
    current = await scene.services.runtime_binding_store.get(
        result.metadata["runtimeBindingRef"]
    )
    assert current.hostLeaseRef != first.hostLeaseRef
    assert (await scene.services.runtime_binding_store.get(first.bindingId)) == first
    async with scene.session_factory() as session:
        physical = (await session.scalars(select(OmnigentOAuthHostLeaseRecord))).all()
        assert len(physical) == 2
        assert {row.status for row in physical} == {"stopped"}
        assert len({row.provider_lease_id for row in physical}) == 1
    scene.manager.request = scene.request
    replay = await scene.realizer.execute(scene.request, scene.plan)
    assert replay.metadata["savedWorkspaceCheckpoint"] == saved
    assert len(scene.server.session_ids) == scene.docker.create_count == 2


@pytest.mark.parametrize(
    "native_journey", [{"harness": "codex-native", "static": True}], indirect=True
)
async def test_static_janitor_preserves_a_native_sibling_and_reconciles_after_it_exits(
    native_journey,
):
    from moonmind.omnigent.generic_host_janitor import GenericOmnigentHostJanitor
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    scene = native_journey

    async def native_sibling_arrives():
        scene.server.foreign_consumers = [
            {
                "id": "user-native",
                "host_id": scene.server.host_id,
                "status": "running",
                "runner_online": True,
            }
        ]

    scene.server.on_first_message = native_sibling_arrives
    result = await scene.realizer.execute(scene.request, scene.plan)
    assert result.failure_class is None, result
    binding = await scene.services.runtime_binding_store.get(
        result.metadata["runtimeBindingRef"]
    )
    assert binding.state is RuntimeBindingState.cleanup_pending
    assert scene.server.host_alive
    assert "journey_oauth" in scene.docker.volumes

    async def closed(_binding):
        return True

    janitor = GenericOmnigentHostJanitor(
        host_leases=scene.services.host_lease_repository,
        runtime_bindings=scene.services.runtime_binding_store,
        realizer=scene.realizer,
        owner_has_closed=closed,
    )
    with pytest.raises(HarnessPlatformError, match="native session consumer"):
        await janitor.drain_profile("journey-model")
    assert scene.server.foreign_consumers[0]["runner_online"] is True
    scene.server.foreign_consumers = []
    assert await janitor.drain_profile("journey-model") == {"drained": 1}
    assert (
        await scene.services.runtime_binding_store.get(binding.bindingId)
    ).state is RuntimeBindingState.cleaned
    assert scene.server.host_alive
    assert await janitor.drain_profile("journey-model") == {"drained": 0}
