"""Selected enrollment layout survives queued edits and shared execution."""

import pytest

from api_service.db.models import ManagedAgentProviderProfile
from tests.integration.omnigent import test_generic_native_durable_journey as native

session_factory = native.session_factory
native_journey = native.native_journey
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
MOUNT = "/home/app/selected-codex-auth"
SELECTIONS = [
    {"harness": "codex-native", "codexCredentialMount": MOUNT},
    {"harness": "codex-native", "codexCredentialMount": MOUNT,
     "codexEnrollmentPreferences": True, "codexNativeDefault": True, "hiddenNativeDefault": True},
]


def assert_selected_configuration(journey):
    import json
    if journey.selection.get("codexEnrollmentPreferences"):
        configuration = json.loads(journey.docker.launch_env["MOONMIND_CODEX_PROFILE_CONFIGURATION"])
        assert configuration == {"document": {"model_verbosity": "low"}, "privateHomeOnly": True}
        assert "--enrolled-home" in journey.docker.script
        assert MOUNT in journey.docker.script
        from moonmind.omnigent.codex_login import CODEX_CONFIGURATION_READ_PROBE
        probes = [cmd for cmd in journey.docker.commands if CODEX_CONFIGURATION_READ_PROBE in cmd]
        assert probes and all(cmd[-1] == MOUNT for cmd in probes)
    else:
        assert "MOONMIND_CODEX_PROFILE_CONFIGURATION" not in journey.docker.launch_env
    assert MOUNT + "/auth.json" not in journey.docker.mounts


@pytest.mark.parametrize(
    "native_journey",
    SELECTIONS,
    indirect=True,
)
async def test_queued_custom_enrollment_keeps_selected_layout(native_journey):
    journey = native_journey
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.volume_mount_path = "/home/app/later-codex-auth"
        profile.volume_ref = "unselected-enrollment"
        profile.home_path_overrides = {"CODEX_HOME": profile.volume_mount_path}
        profile.file_templates = []
        await session.commit()
    await native.assert_native_turn_saved_and_cleaned(
        journey, expected_credential_target=MOUNT
    )
    assert journey.docker.launch_env["CODEX_HOME"] == MOUNT
    assert journey.docker.volumes == {"journey_oauth": {}}
    assert_selected_configuration(journey)


@pytest.mark.parametrize(
    "native_journey",
    SELECTIONS,
    indirect=True,
)
async def test_ready_custom_enrollment_resumes_without_rereading_profile(
    native_journey, monkeypatch
):
    import asyncio

    from moonmind.omnigent.runtime_bindings import (
        RuntimeBindingState,
        stable_binding_id,
    )

    journey = native_journey
    update = journey.realizer._update_binding

    async def revoke(*args, **kwargs):
        result = await update(*args, **kwargs)
        if kwargs.get("state") is RuntimeBindingState.host_ready:
            raise asyncio.CancelledError()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(journey.realizer, "_update_binding", revoke)
        patch.setattr(
            "moonmind.omnigent.activity_ownership.delivery_was_revoked", lambda: True
        )
        with pytest.raises(asyncio.CancelledError):
            await journey.realizer.execute(journey.request, journey.plan)
    binding = await journey.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=journey.plan.planRef,
            idempotency_key=journey.request.idempotency_key,
        )
    )
    assert binding.state is RuntimeBindingState.host_ready
    assert not journey.server.create_payloads
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.volume_ref = "unselected-enrollment"
        profile.volume_mount_path = "/home/app/later-auth"
        profile.file_templates = []
        profile.home_path_overrides = {"HOME": "{{runtime_support_dir}}/later-home"}
        await session.commit()
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    completed = await journey.services.runtime_binding_store.get(binding.bindingId)
    assert completed.hostLeaseRef == binding.hostLeaseRef
    assert completed.credentialRuntimeHandles == binding.credentialRuntimeHandles
    assert len(journey.server.create_payloads) == 1
    assert journey.docker.launch_env["CODEX_HOME"] == MOUNT
    assert journey.docker.volumes == {"journey_oauth": {}}
    assert_selected_configuration(journey)


@pytest.mark.parametrize(
    "native_journey",
    SELECTIONS,
    indirect=True,
)
async def test_custom_enrollment_rotation_fences_queued_layout(native_journey):
    journey = native_journey
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.credential_generation += 1
        await session.commit()
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    with pytest.raises(HarnessPlatformError, match="credentials rotated"):
        await journey.realizer.execute(journey.request, journey.plan)
    assert not journey.server.create_payloads
    assert journey.docker.create_count == 0


@pytest.mark.parametrize(
    "native_journey",
    SELECTIONS,
    indirect=True,
)
async def test_cold_custom_enrollment_preserves_selected_volume_and_save(
    native_journey,
):
    import hashlib
    import shutil

    from api_service.db.models import ProviderProfileSlotLease
    from moonmind.omnigent.runtime_bindings import stable_binding_id
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    journey = native_journey
    first = await journey.realizer.execute(journey.request, journey.plan)
    assert first.failure_class is None, first
    predecessor = await journey.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=journey.plan.planRef,
            idempotency_key=journey.request.idempotency_key,
        )
    )
    saved = first.metadata["savedWorkspaceCheckpoint"]
    shutil.rmtree(journey.server.workspace)
    payload = journey.request.model_dump(mode="json", by_alias=True, exclude_none=True)
    identity = journey.request.step_execution
    step_id = f"{identity.workflow_id}:{identity.run_id}:{identity.logical_step_id}:execution:2"
    payload["idempotencyKey"] = "custom-home-successor"
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
        idempotencyKey="custom-home-successor",
        agentRunRunId="custom-home-successor-run",
    )
    successor = AgentExecutionRequest.model_validate(payload)
    journey.manager.request = successor
    async with journey.session_factory() as session:
        lease = await session.get(ProviderProfileSlotLease, journey.provider_lease_id)
        lease.step_execution_id = step_id
        lease.idempotency_key = successor.idempotency_key
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.volume_ref = "unselected-enrollment"
        profile.volume_mount_path = "/home/app/later-auth"
        profile.file_templates = []
        profile.home_path_overrides = {"HOME": "{{runtime_support_dir}}/later-home"}
        await session.commit()
    await journey.gateway.admit_execution_plan_inputs(
        request=successor, plan=journey.plan
    )
    observed = {}

    async def inspect():
        observed["saved"] = (journey.server.workspace / native.RESULT_PATH).read_bytes()

    journey.server.on_first_message = inspect
    journey.server.terminal_status, journey.server.status = "idle", "idle"
    journey.server.items, journey.server.events = [], []
    second = await journey.realizer.execute(successor, journey.plan)
    assert second.failure_class is None, second
    assert observed == {"saved": native.GENERATED}
    current = await journey.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=journey.plan.planRef,
            idempotency_key=successor.idempotency_key,
        )
    )
    assert current.hostLeaseRef != predecessor.hostLeaseRef
    for binding in (predecessor, current):
        attachment = next(iter(binding.credentialRuntimeHandles.values()))[
            "attachments"
        ][0]
        assert attachment == {
            "kind": "volume",
            "sourceRef": "journey_oauth",
            "targetPath": MOUNT,
            "accessMode": "read-write",
        }
    assert journey.docker.launch_env["CODEX_HOME"] == MOUNT
    assert journey.docker.volumes == {"journey_oauth": {}}
    assert_selected_configuration(journey)


@pytest.mark.parametrize(
    "native_journey",
    SELECTIONS,
    indirect=True,
)
@pytest.mark.parametrize("conflict", ["workspace", "workspace-child", "second-enrollment"])
async def test_custom_enrollment_refuses_overlapping_host_attachment(
    native_journey, monkeypatch, conflict
):
    from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher

    original = DockerOmnigentHostLauncher.launch

    async def collide(self, **kwargs):
        if conflict.startswith("workspace"):
            spec = kwargs["spec"]
            kwargs["spec"] = spec.model_copy(
                update={
                    "workspaceAttachment": {
                        **spec.workspaceAttachment,
                        "targetPath": MOUNT + ("/settings.json" if conflict == "workspace-child" else ""),
                    },
                }
            )
        else:
            from copy import deepcopy

            sibling = deepcopy(kwargs["credential_handles"][0])
            sibling["attachments"][0]["targetPath"] = "/home/app/.codex"
            sibling["attachments"][0]["sourceRef"] = "unselected-enrollment"
            kwargs["credential_handles"] = [*kwargs["credential_handles"], sibling]
        return await original(self, **kwargs)

    monkeypatch.setattr(DockerOmnigentHostLauncher, "launch", collide)
    with pytest.raises(
        ValueError, match="overlaps another attachment|one selected Codex home"
    ):
        await native_journey.realizer.execute(
            native_journey.request, native_journey.plan
        )
    assert native_journey.docker.create_count == 0
    assert native_journey.docker.volumes == {"journey_oauth": {}}
