"""Frozen Claude restrictions survive shared lifecycle and cold restoration.

Production admission, credential/host/workspace owners and HTTP session binding
are real; Docker and provider transport use the existing controlled journey.
"""

import hashlib
import json
import shutil

import pytest

from api_service.db.models import ManagedAgentProviderProfile, ProviderProfileSlotLease
from moonmind.omnigent.runtime_bindings import RuntimeBindingState, stable_binding_id
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from tests.integration.omnigent import test_generic_native_durable_journey as native

session_factory = native.session_factory
native_journey = native.native_journey
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
DENIED = ["Read(.env)", "Bash(curl *)", "WebFetch"]
SETTINGS = {"language": "English", "permissions": {"deny": DENIED}}


def assert_denied(journey, rules):
    args = journey.server.create_payloads[-1]["terminal_launch_args"]
    assert args[args.index("--permission-mode") + 1] == "bypassPermissions"
    assert args.count("--disallowedTools") == 1
    index = args.index("--disallowedTools")
    # Existing runtime restrictions and the frozen Profile restrictions compose.
    assert set(args[index + 1].split(",")) == {"ScheduleWakeup", *rules}
    assert "--settings" not in args
    assert native.SECRET not in json.dumps(args)


@pytest.mark.parametrize(
    "native_journey",
    [
        {"harness": harness, "claudePreferences": True, "sessionSettings": SETTINGS}
        for harness in ("claude-native", "claude-native-api-key")
    ],
    indirect=True,
)
async def test_frozen_claude_deny_survives_profile_edit_and_cold_successor(
    native_journey,
):
    journey = native_journey
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.file_templates = []
        await session.commit()
    first = await journey.realizer.execute(journey.request, journey.plan)
    assert first.failure_class is None, first
    assert_denied(journey, DENIED)
    predecessor = await journey.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=journey.plan.planRef,
            idempotency_key=journey.request.idempotency_key,
        )
    )
    assert predecessor.state is RuntimeBindingState.cleaned
    first_handles = predecessor.credentialRuntimeHandles
    assert next(iter(first_handles.values()))["claudeDeniedTools"] == DENIED
    saved = first.metadata["savedWorkspaceCheckpoint"]
    old_workspace = journey.server.workspace
    # Simulate loss of the cleaned predecessor host and its retained worktree.
    shutil.rmtree(old_workspace)
    assert not old_workspace.exists()
    payload = journey.request.model_dump(mode="json", by_alias=True, exclude_none=True)
    identity = journey.request.step_execution
    step_id = f"{identity.workflow_id}:{identity.run_id}:{identity.logical_step_id}:execution:2"
    payload["idempotencyKey"] = "deny-successor"
    payload["stepExecution"].update(executionOrdinal=2, stepExecutionId=step_id)
    payload["workspaceSpec"] = {
        "workspaceCheckpointRestoreRef": saved["archiveRef"],
        "workspaceLocator": {
            "kind": "sandbox",
            "workspaceId": hashlib.sha256(
                f"{identity.workflow_id}:{step_id}".encode()
            ).hexdigest()[:24],
            "relativePath": "repo",
        },
    }
    payload["admittedProviderCapacity"].update(
        stepExecutionId=step_id,
        idempotencyKey="deny-successor",
        agentRunRunId="deny-successor-run",
    )
    successor = AgentExecutionRequest.model_validate(payload)
    journey.manager.request = successor
    async with journey.session_factory() as session:
        lease = await session.get(ProviderProfileSlotLease, journey.provider_lease_id)
        lease.step_execution_id = step_id
        lease.idempotency_key = successor.idempotency_key
        await session.commit()
    await journey.gateway.admit_execution_plan_inputs(
        request=successor, plan=journey.plan
    )
    observed = {}

    async def inspect_restored():
        observed["saved"] = (journey.server.workspace / native.RESULT_PATH).read_bytes()

    journey.server.on_first_message = inspect_restored
    journey.server.terminal_status, journey.server.status = "idle", "idle"
    journey.server.items, journey.server.events = [], []
    second = await journey.realizer.execute(successor, journey.plan)
    assert second.failure_class is None, second
    assert observed == {"saved": native.GENERATED}
    assert_denied(journey, DENIED)
    current = await journey.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=journey.plan.planRef,
            idempotency_key=successor.idempotency_key,
        )
    )
    assert current.state is RuntimeBindingState.cleaned
    assert current.hostLeaseRef != predecessor.hostLeaseRef
    assert (
        next(iter(current.credentialRuntimeHandles.values()))["claudeDeniedTools"]
        == DENIED
    )
    assert journey.docker.volumes == {"journey_oauth": {}}
    assert native.SECRET not in json.dumps(current.model_dump(mode="json"))


@pytest.mark.parametrize(
    "native_journey",
    [
        {"harness": harness, "claudePreferences": True, "sessionSettings": SETTINGS}
        for harness in ("claude-native", "claude-native-api-key")
    ],
    indirect=True,
)
async def test_fresh_sibling_configuration_does_not_replace_queued_denies(
    native_journey,
):
    from copy import deepcopy

    from api_service.services import omnigent_execution_plan_service as writer
    from moonmind.workflows.temporal.artifacts import (
        ArtifactTransactionOwnership,
        TemporalArtifactRepository,
        TemporalArtifactService,
    )
    from tests.artifact_access import access_for_test

    journey = native_journey
    snapshot = json.loads(
        await journey.gateway.read_admitted_input(
            journey.plan.payload.agentProfileSnapshotRef, request=journey.request
        )
    )
    parameters = json.loads(
        await journey.gateway.read_admitted_input(
            journey.plan.payload.authority.taskInputSnapshotRef, request=journey.request
        )
    )
    sibling_denies = ["Read(sibling.secret)"]
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        templates = deepcopy(profile.file_templates)
        templates[0]["content_template"]["permissions"]["deny"] = sibling_denies
        profile.file_templates = templates
        await session.flush()
        compiled = await writer.compile_frozen_execution_plan(
            db_session=session,
            artifact_service=TemporalArtifactService(
                TemporalArtifactRepository(session),
                transaction_ownership=ArtifactTransactionOwnership.CALLER,
            ),
            workflow_id=journey.request.correlation_id,
            principal="system",
            snapshot=snapshot,
            provider_profile=profile,
            initial_parameters=parameters,
            binding=journey.request.omnigent_execution_plan,
            access=access_for_test(
                "system",
                admitted_principal=f"workflow:{journey.request.correlation_id}",
            ),
            refresh_step_selections=True,
        )
        await session.commit()
    first = await journey.realizer.execute(journey.request, journey.plan)
    assert first.failure_class is None, first
    assert_denied(journey, DENIED)
    old_args = list(journey.server.create_payloads[-1]["terminal_launch_args"])
    payload = journey.request.model_dump(mode="json", by_alias=True, exclude_none=True)
    identity = journey.request.step_execution
    sibling_id = f"{identity.workflow_id}:{identity.run_id}:sibling:execution:1"
    payload["idempotencyKey"] = "deny-sibling"
    payload["omnigentExecutionPlan"] = compiled.binding.model_dump(
        mode="json", by_alias=True
    )
    payload["stepExecution"].update(
        logicalStepId="sibling", executionOrdinal=1, stepExecutionId=sibling_id
    )
    payload["workspaceSpec"]["workspaceLocator"]["workspaceId"] = hashlib.sha256(
        f"{identity.workflow_id}:{sibling_id}".encode()
    ).hexdigest()[:24]
    payload["admittedProviderCapacity"].update(
        stepExecutionId=sibling_id,
        idempotencyKey="deny-sibling",
        executionPlanRef=compiled.envelope.planRef,
        agentRunRunId="sibling-run",
    )
    sibling = AgentExecutionRequest.model_validate(payload)
    journey.manager.request, journey.manager.plan = sibling, compiled.envelope
    async with journey.session_factory() as session:
        lease = await session.get(ProviderProfileSlotLease, journey.provider_lease_id)
        lease.step_execution_id = sibling_id
        lease.idempotency_key = sibling.idempotency_key
        lease.execution_plan_ref = compiled.envelope.planRef
        await session.commit()
    await journey.gateway.admit_execution_plan_inputs(
        request=sibling, plan=compiled.envelope
    )
    journey.server.terminal_status, journey.server.status = "idle", "idle"
    journey.server.items, journey.server.events = [], []
    second = await journey.realizer.execute(sibling, compiled.envelope)
    assert second.failure_class is None, second
    assert_denied(journey, sibling_denies)
    assert journey.server.create_payloads[0]["terminal_launch_args"] == old_args
    assert journey.docker.volumes == {"journey_oauth": {}}


@pytest.mark.parametrize(
    "native_journey",
    [
        {"harness": harness, "claudePreferences": True, "sessionSettings": SETTINGS}
        for harness in ("claude-native", "claude-native-api-key")
    ],
    indirect=True,
)
async def test_ready_host_retry_keeps_frozen_denies_before_session_creation(
    native_journey, monkeypatch
):
    import asyncio

    journey = native_journey
    update = journey.realizer._update_binding

    async def revoke_after_ready(*args, **kwargs):
        binding = await update(*args, **kwargs)
        if kwargs.get("state") is RuntimeBindingState.host_ready:
            raise asyncio.CancelledError()
        return binding

    with monkeypatch.context() as revoked:
        revoked.setattr(journey.realizer, "_update_binding", revoke_after_ready)
        revoked.setattr(
            "moonmind.omnigent.activity_ownership.delivery_was_revoked", lambda: True
        )
        with pytest.raises(asyncio.CancelledError):
            await journey.realizer.execute(journey.request, journey.plan)
    assert journey.server.host_alive
    assert not journey.server.create_payloads
    binding = await journey.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=journey.plan.planRef,
            idempotency_key=journey.request.idempotency_key,
        )
    )
    assert binding.state is RuntimeBindingState.host_ready
    handles = json.loads(json.dumps(binding.credentialRuntimeHandles))
    assert next(iter(handles.values()))["claudeDeniedTools"] == DENIED
    host = await journey.services.host_lease_repository.get(binding.hostLeaseRef)
    # Existing ready receipts omit the projection. Recovery must derive it
    # from the immutable credential owner, not rely on a new receipt format.
    assert "claudeDeniedTools" not in host.cleanupHandle
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.file_templates = []
        await session.commit()
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    assert_denied(journey, DENIED)
    assert len(journey.server.create_payloads) == 1
    completed = await journey.services.runtime_binding_store.get(binding.bindingId)
    assert completed.hostLeaseRef == binding.hostLeaseRef
    assert completed.credentialRuntimeHandles == handles
    assert completed.state is RuntimeBindingState.cleaned


@pytest.mark.parametrize(
    "native_journey",
    [
        {"harness": harness, "claudeOauthFiles": harness == "claude-native", "processHome": True}
        for harness in ("claude-native", "claude-native-api-key")
    ],
    indirect=True,
)
async def test_ready_host_retry_keeps_frozen_process_home_before_session_creation(
    native_journey, monkeypatch
):
    import asyncio

    journey = native_journey
    update = journey.realizer._update_binding

    async def revoke_after_ready(*args, **kwargs):
        binding = await update(*args, **kwargs)
        if kwargs.get("state") is RuntimeBindingState.host_ready:
            raise asyncio.CancelledError()
        return binding

    with monkeypatch.context() as revoked:
        revoked.setattr(journey.realizer, "_update_binding", revoke_after_ready)
        revoked.setattr(
            "moonmind.omnigent.activity_ownership.delivery_was_revoked", lambda: True
        )
        with pytest.raises(asyncio.CancelledError):
            await journey.realizer.execute(journey.request, journey.plan)
    assert journey.server.host_alive
    assert not journey.server.create_payloads
    binding = await journey.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=journey.plan.planRef,
            idempotency_key=journey.request.idempotency_key,
        )
    )
    assert binding.state is RuntimeBindingState.host_ready
    handles = json.loads(json.dumps(binding.credentialRuntimeHandles))
    assert next(iter(handles.values()))["claudeProcessHomeConfiguration"]
    host = await journey.services.host_lease_repository.get(binding.hostLeaseRef)
    # Existing ready receipts omit the projection. Recovery must derive it
    # from the immutable credential owner, not rely on a new receipt format.
    assert "processHome" not in host.cleanupHandle
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.file_templates = []
        profile.home_path_overrides = {}
        await session.commit()
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    assert journey.server.create_payloads[-1]["process_home"] == (
        "/home/app/.omnigent/moonmind/runtime-context/process-home"
    )
    assert len(journey.server.create_payloads) == 1
    completed = await journey.services.runtime_binding_store.get(binding.bindingId)
    assert completed.hostLeaseRef == binding.hostLeaseRef
    assert completed.credentialRuntimeHandles == handles
    assert completed.state is RuntimeBindingState.cleaned
