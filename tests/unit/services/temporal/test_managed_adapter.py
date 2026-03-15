import asyncio
import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch, MagicMock

from api_service.services.temporal.adapters.managed import ManagedAgentAdapter
from api_service.services.temporal.workflows.shared import AgentExecutionRequest, AgentRunStatus
from api_service.services.temporal.runtime.store import ManagedRunStore
from api_service.services.temporal.runtime.launcher import ManagedRuntimeLauncher
from api_service.services.temporal.runtime.supervisor import ManagedRunSupervisor
from moonmind.schemas.agent_runtime_models import ManagedRunRecord


def _make_request(**overrides) -> AgentExecutionRequest:
    defaults = dict(
        agent_kind="managed",
        agent_id="test-managed",
        execution_profile_ref="default-managed",
        instruction_ref="managed-instruction",
        input_refs=[],
        workspace_spec={"repo": "test-repo"},
        idempotency_key="managed-key",
    )
    defaults.update(overrides)
    return AgentExecutionRequest(**defaults)


# --- Stub mode tests (no runtime components) ---

@pytest.mark.asyncio
async def test_managed_adapter_start_stub_mode():
    adapter = ManagedAgentAdapter()
    request = _make_request()
    handle = await adapter.start(request)
    assert handle.run_id == "managed-key"
    assert handle.agent_kind == "managed"
    assert handle.status == AgentRunStatus.launching
    assert handle.poll_hint_seconds == 5


@pytest.mark.asyncio
async def test_managed_adapter_status_stub_mode():
    adapter = ManagedAgentAdapter()
    status = await adapter.status("test-run-id")
    assert status == AgentRunStatus.running


@pytest.mark.asyncio
async def test_managed_adapter_fetch_result_stub_mode():
    adapter = ManagedAgentAdapter()
    result = await adapter.fetch_result("test-run-id")
    assert result.summary == "Managed run complete"
    assert result.output_refs == []


@pytest.mark.asyncio
async def test_managed_adapter_cancel_stub_mode():
    adapter = ManagedAgentAdapter()
    await adapter.cancel("test-run-id")


# --- Full flow tests (with runtime components) ---

@pytest.mark.asyncio
async def test_full_start_status_fetch_result_flow(tmp_path):
    store = ManagedRunStore(tmp_path / "store")
    launcher = ManagedRuntimeLauncher(store)

    from moonmind.workflows.agent_queue.storage import AgentQueueArtifactStorage
    from api_service.services.temporal.runtime.log_streamer import RuntimeLogStreamer
    artifact_storage = AgentQueueArtifactStorage(tmp_path / "artifacts")
    log_streamer = RuntimeLogStreamer(artifact_storage)
    supervisor = ManagedRunSupervisor(store, log_streamer)

    adapter = ManagedAgentAdapter(
        store=store, launcher=launcher, supervisor=supervisor
    )

    # Patch command template to use echo
    from api_service.services.temporal.adapters import managed as managed_mod
    original_profiles = managed_mod._DEFAULT_PROFILES.copy()
    from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
    managed_mod._DEFAULT_PROFILES["default-managed"] = ManagedRuntimeProfile(
        runtime_id="test-cli",
        command_template=["echo", "test-output"],
        default_timeout_seconds=30,
    )

    try:
        request = _make_request()
        handle = await adapter.start(request)
        assert handle.run_id == "managed-key"
        assert handle.agent_kind == "managed"
        assert handle.status == AgentRunStatus.launching

        # Let supervision complete
        await asyncio.sleep(1)

        status = await adapter.status("managed-key")
        assert status == AgentRunStatus.completed

        result = await adapter.fetch_result("managed-key")
        assert result.summary == "Managed run complete"
        assert result.diagnostics_ref is not None
    finally:
        managed_mod._DEFAULT_PROFILES.update(original_profiles)


@pytest.mark.asyncio
async def test_idempotent_start(tmp_path):
    store = ManagedRunStore(tmp_path / "store")
    launcher = ManagedRuntimeLauncher(store)

    from moonmind.workflows.agent_queue.storage import AgentQueueArtifactStorage
    from api_service.services.temporal.runtime.log_streamer import RuntimeLogStreamer
    artifact_storage = AgentQueueArtifactStorage(tmp_path / "artifacts")
    log_streamer = RuntimeLogStreamer(artifact_storage)
    supervisor = ManagedRunSupervisor(store, log_streamer)

    adapter = ManagedAgentAdapter(
        store=store, launcher=launcher, supervisor=supervisor
    )

    from api_service.services.temporal.adapters import managed as managed_mod
    original_profiles = managed_mod._DEFAULT_PROFILES.copy()
    from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
    managed_mod._DEFAULT_PROFILES["default-managed"] = ManagedRuntimeProfile(
        runtime_id="test-cli",
        command_template=["sleep", "10"],
        default_timeout_seconds=30,
    )

    try:
        request = _make_request()
        handle1 = await adapter.start(request)
        # Brief wait for store to persist
        await asyncio.sleep(0.1)

        # Second start should return existing handle
        handle2 = await adapter.start(request)
        assert handle2.run_id == handle1.run_id
    finally:
        managed_mod._DEFAULT_PROFILES.update(original_profiles)
        # Clean up background tasks
        await supervisor.cancel("managed-key")


@pytest.mark.asyncio
async def test_cancel_full_flow(tmp_path):
    store = ManagedRunStore(tmp_path / "store")
    launcher = ManagedRuntimeLauncher(store)

    from moonmind.workflows.agent_queue.storage import AgentQueueArtifactStorage
    from api_service.services.temporal.runtime.log_streamer import RuntimeLogStreamer
    artifact_storage = AgentQueueArtifactStorage(tmp_path / "artifacts")
    log_streamer = RuntimeLogStreamer(artifact_storage)
    supervisor = ManagedRunSupervisor(store, log_streamer)

    adapter = ManagedAgentAdapter(
        store=store, launcher=launcher, supervisor=supervisor
    )

    from api_service.services.temporal.adapters import managed as managed_mod
    original_profiles = managed_mod._DEFAULT_PROFILES.copy()
    from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
    managed_mod._DEFAULT_PROFILES["default-managed"] = ManagedRuntimeProfile(
        runtime_id="test-cli",
        command_template=["sleep", "60"],
        default_timeout_seconds=30,
    )

    try:
        request = _make_request()
        handle = await adapter.start(request)
        await asyncio.sleep(0.2)

        await adapter.cancel("managed-key")

        loaded = store.load("managed-key")
        assert loaded.status == "cancelled"
    finally:
        managed_mod._DEFAULT_PROFILES.update(original_profiles)
