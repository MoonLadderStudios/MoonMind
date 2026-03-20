import shutil
import asyncio
from pathlib import Path

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
from moonmind.workflows.temporal.runtime.launcher import (
    TMATE_FOREGROUND_RESTART_OFF,
    ManagedRuntimeLauncher,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


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

    record, process, endpoints = await launcher.launch(
        run_id="run-1", request=request, profile=profile
    )
    await process.wait()

    assert endpoints is None
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
    record, process, _ = await launcher.launch(
        run_id="run-1", request=request, profile=profile
    )
    await process.wait()

    # Second launch with same run_id should raise (record is still "launching")
    with pytest.raises(RuntimeError, match="Active run already exists"):
        await launcher.launch(
            run_id="run-1", request=request, profile=profile
        )


@pytest.mark.asyncio
async def test_tmate_launch_writes_config_and_exit_file_contract(
    tmp_path, monkeypatch
):
    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["gemini", "run"])
    request = _make_request()

    class _FakeProcess:
        def __init__(
            self,
            *,
            pid: int = 4321,
            returncode: int = 0,
            stdout_bytes: bytes = b"",
        ) -> None:
            self.pid = pid
            self.returncode = returncode
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self._stdout_bytes = stdout_bytes

        async def wait(self) -> int:
            return self.returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return self._stdout_bytes, b""

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.shutil.which",
        lambda binary: "/usr/bin/tmate" if binary == "tmate" else None,
    )

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def _fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ("tmate", "-S") and "new-session" in args:
            return _FakeProcess(pid=9999)
        if args[:2] == ("tmate", "-S") and "wait" in args:
            return _FakeProcess()
        if args[:2] == ("tmate", "-S") and "display" in args:
            key = str(args[-1])
            values = {
                "#{tmate_ssh_ro}": b"ssh ro\n",
                "#{tmate_ssh}": b"ssh rw\n",
                "#{tmate_web_ro}": b"web ro\n",
                "#{tmate_web}": b"web rw\n",
            }
            return _FakeProcess(stdout_bytes=values.get(key, b""))
        raise AssertionError(f"Unexpected subprocess call: {args!r}")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    record, process, endpoints = await launcher.launch(
        run_id="tmate-run-1",
        request=request,
        profile=profile,
    )

    assert record.pid == 9999
    assert process.pid == 9999
    assert endpoints is not None

    config_path = Path(endpoints["tmate_config_path"])
    assert config_path.read_text(encoding="utf-8") == (
        TMATE_FOREGROUND_RESTART_OFF
    )
    assert endpoints["exit_code_path"].endswith("tmate-run-1.exit")
    assert endpoints["attach_rw"] == "ssh rw"
    assert endpoints["web_rw"] == "web rw"

    launch_call = next(args for args, _ in calls if "new-session" in args)
    wrapped_command = str(launch_call[-1])
    assert "\"$@\"" in wrapped_command
    assert "MM_EXIT_FILE" in wrapped_command

    launch_env = next(kwargs for args, kwargs in calls if "new-session" in args)[
        "env"
    ]
    assert launch_env["MM_EXIT_FILE"] == endpoints["exit_code_path"]
