import shutil

import pytest

from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


def _make_profile(**overrides) -> ManagedRuntimeProfile:
    defaults = dict(
        runtime_id="codex-cli",
        command_template=["codex", "run"],
        default_model="o4-mini",
        default_effort="medium",
        default_timeout_seconds=3600,
        workspace_mode="tempdir",
        env_overrides={},
    )
    defaults.update(overrides)
    return ManagedRuntimeProfile(**defaults)


def _make_request(**overrides) -> AgentExecutionRequest:
    defaults = dict(
        agent_kind="managed",
        agent_id="agent-1",
        execution_profile_ref="default-managed",
        correlation_id="test-corr-1",
        idempotency_key="run-1",
    )
    defaults.update(overrides)
    return AgentExecutionRequest(**defaults)


def test_build_command_default():
    store = ManagedRunStore("/tmp/test-store")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile()
    request = _make_request()

    cmd = launcher.build_command(profile, request)
    assert cmd[:2] == ["codex", "run"]
    assert "--model" in cmd
    assert "o4-mini" in cmd
    assert "--effort" in cmd
    assert "medium" in cmd


def test_build_command_with_request_overrides():
    store = ManagedRunStore("/tmp/test-store")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile()
    request = _make_request(parameters={"model": "o3", "effort": "high"})

    cmd = launcher.build_command(profile, request)
    assert "o3" in cmd
    assert "high" in cmd


def test_build_command_with_instruction_ref():
    store = ManagedRunStore("/tmp/test-store")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile()
    request = _make_request(instruction_ref="instr-ref-1")

    cmd = launcher.build_command(profile, request)
    assert "--instruction-ref" in cmd
    assert "instr-ref-1" in cmd


def test_build_command_per_runtime():
    store = ManagedRunStore("/tmp/test-store")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        runtime_id="custom-cli",
        command_template=["python", "-m", "myagent"],
        default_model=None,
        default_effort=None,
    )
    request = _make_request()

    cmd = launcher.build_command(profile, request)
    assert cmd[:3] == ["python", "-m", "myagent"]
    # No model/effort flags since both are None and no request overrides
    assert "--model" not in cmd
    assert "--effort" not in cmd


@pytest.mark.asyncio
async def test_launch_spawns_process(tmp_path, monkeypatch):
    # Avoid tmate wrapper when tmate is installed in CI/Docker (would hang on wait-ready).
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request()

    record, process, _endpoints = await launcher.launch(
        run_id="run-1", request=request, profile=profile
    )
    await process.wait()

    assert record.run_id == "run-1"
    assert record.pid == process.pid
    assert record.status == "launching"

    loaded = store.load("run-1")
    assert loaded is not None
    assert loaded.pid == process.pid


@pytest.mark.asyncio
async def test_idempotent_launch_rejects_active(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request()

    # First launch
    record, process, _endpoints = await launcher.launch(
        run_id="run-1", request=request, profile=profile
    )
    await process.wait()

    # Second launch with same run_id should raise (record is still "launching")
    with pytest.raises(RuntimeError, match="Active run already exists"):
        await launcher.launch(
            run_id="run-1", request=request, profile=profile
        )
