"""Selected Codex session settings survive shared-runtime recovery."""

import asyncio
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
SETTINGS = [
    ("web_search", "disabled", "live"),
    ("tool_output_token_limit", 1024, 0),
]
SELECTIONS = [
    pytest.param(
        {**auth, "sessionSettings": {setting: original}},
        id=f"{auth_id}-{setting}",
    )
    for auth_id, auth in (
        ("oauth", {"harness": "codex-native", "oauthPrivateConfiguration": True}),
        ("api-key", {"harness": "codex-native-api-key", "privateProfileConfiguration": True}),
    )
    for setting, original, _edited in SETTINGS
]
LIVE_VALUES = {setting: edited for setting, _original, edited in SETTINGS}


async def edit_live_profile(journey):
    setting, = journey.selection["sessionSettings"]
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.file_templates = [{
            "path": "{{runtime_support_dir}}/codex-home/config.toml", "format": "toml",
            "content_template": {setting: LIVE_VALUES[setting]}, "merge_strategy": "deep_merge",
        }]
        await session.commit()


def assert_frozen_configuration(journey):
    configuration = (
        journey.docker.codex_configuration if journey.api_key else
        json.loads(journey.docker.launch_env["MOONMIND_CODEX_PROFILE_CONFIGURATION"])
    )
    assert configuration["document"] == journey.selection["sessionSettings"]
    assert configuration["privateHomeOnly"] is True
    assert "codex-configuration.py prepare" in journey.docker.script
    if journey.api_key:
        provider = journey.docker.auth_json["providers"]["moonmind"]["openai"]
        assert provider["api_key"] == native.SECRET
        assert provider["base_url"] == "https://api.openai.com/v1"
    else:
        assert journey.docker.auth_json is None
        assert journey.docker.volumes == {"journey_oauth": {}}
    assert native.SECRET not in json.dumps(journey.docker.commands)


@pytest.mark.parametrize("native_journey", SELECTIONS, indirect=True)
async def test_codex_session_setting_queued_profile_edit_keeps_admitted_configuration(native_journey):
    journey = native_journey
    await edit_live_profile(journey)
    await native.assert_native_turn_saved_and_cleaned(journey)
    assert_frozen_configuration(journey)


@pytest.mark.parametrize("native_journey", SELECTIONS, indirect=True)
async def test_codex_session_setting_ready_host_retry_keeps_configuration(native_journey, monkeypatch):
    journey = native_journey
    update = journey.realizer._update_binding

    async def revoke(*args, **kwargs):
        result = await update(*args, **kwargs)
        if kwargs.get("state") is RuntimeBindingState.host_ready:
            raise asyncio.CancelledError()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(journey.realizer, "_update_binding", revoke)
        patch.setattr("moonmind.omnigent.activity_ownership.delivery_was_revoked", lambda: True)
        with pytest.raises(asyncio.CancelledError):
            await journey.realizer.execute(journey.request, journey.plan)
    binding_id = stable_binding_id(execution_plan_ref=journey.plan.planRef,
                                   idempotency_key=journey.request.idempotency_key)
    binding = await journey.services.runtime_binding_store.get(binding_id)
    assert binding.state is RuntimeBindingState.host_ready
    assert not journey.server.create_payloads
    await edit_live_profile(journey)
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    current = await journey.services.runtime_binding_store.get(binding_id)
    assert current.hostLeaseRef == binding.hostLeaseRef
    assert current.credentialRuntimeHandles == binding.credentialRuntimeHandles
    assert len(journey.server.create_payloads) == 1
    assert_frozen_configuration(journey)


@pytest.mark.parametrize("native_journey", SELECTIONS, indirect=True)
async def test_codex_session_setting_cold_successor_preserves_save_and_selected_identity(native_journey):
    journey = native_journey
    first = await native.assert_native_turn_saved_and_cleaned(journey)
    assert_frozen_configuration(journey)
    predecessor = await journey.services.runtime_binding_store.get(stable_binding_id(
        execution_plan_ref=journey.plan.planRef, idempotency_key=journey.request.idempotency_key,
    ))
    saved = first.metadata["savedWorkspaceCheckpoint"]
    if journey.server.workspace.exists():
        shutil.rmtree(journey.server.workspace)
    assert not journey.server.workspace.exists()
    await edit_live_profile(journey)
    payload = journey.request.model_dump(mode="json", by_alias=True, exclude_none=True)
    identity = journey.request.step_execution
    step_id = f"{identity.workflow_id}:{identity.run_id}:{identity.logical_step_id}:execution:2"
    payload["idempotencyKey"] = "session-setting-successor"
    payload["stepExecution"].update(executionOrdinal=2, stepExecutionId=step_id)
    payload["workspaceSpec"] = {
        "workspaceCheckpointRestoreRef": saved["archiveRef"],
        "workspaceLocator": {"kind": "sandbox", "workspaceId": hashlib.sha256(
            f"{identity.workflow_id}:{step_id}".encode()).hexdigest()[:24], "relativePath": "repo"},
    }
    payload["admittedProviderCapacity"].update(
        stepExecutionId=step_id, idempotencyKey="session-setting-successor",
        agentRunRunId="session-setting-successor-run",
    )
    successor = AgentExecutionRequest.model_validate(payload)
    journey.manager.request = successor
    async with journey.session_factory() as session:
        lease = await session.get(ProviderProfileSlotLease, journey.provider_lease_id)
        lease.step_execution_id = step_id
        lease.idempotency_key = successor.idempotency_key
        await session.commit()
    await journey.gateway.admit_execution_plan_inputs(request=successor, plan=journey.plan)
    observed = {}

    async def inspect():
        observed["saved"] = (journey.server.workspace / native.RESULT_PATH).read_bytes()

    journey.server.on_first_message = inspect
    journey.server.terminal_status, journey.server.status = "idle", "idle"
    journey.server.items, journey.server.events = [], []
    result = await journey.realizer.execute(successor, journey.plan)
    assert result.failure_class is None, result
    assert observed == {"saved": native.GENERATED}
    current = await journey.services.runtime_binding_store.get(stable_binding_id(
        execution_plan_ref=journey.plan.planRef, idempotency_key=successor.idempotency_key,
    ))
    assert current.hostLeaseRef != predecessor.hostLeaseRef
    for binding in (predecessor, current):
        handle = next(iter(binding.credentialRuntimeHandles.values()))
        assert handle["providerProfileRef"] == native.PROFILE
        assert handle["credentialGeneration"] == 3
        if not journey.api_key:
            assert handle["attachments"] == [{"kind": "volume", "sourceRef": "journey_oauth",
                "targetPath": "/home/app/.codex", "accessMode": "read-write"}]
    assert_frozen_configuration(journey)
