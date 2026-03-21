import shutil
import asyncio
import subprocess
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
    assert "--prompt" in cmd
    assert "instr-ref-1" in cmd


def test_build_command_codex_cli():
    """Codex CLI uses `codex exec -m MODEL [PROMPT]` — no --effort or --prompt flags."""
    store = ManagedRunStore("/tmp/test-store")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        runtime_id="codex_cli",
        command_template=["codex", "exec", "--full-auto"],
        default_model="gpt-5.3-codex",
        default_effort="high",
    )
    request = _make_request(
        instruction_ref="Fix the bug",
        parameters={"model": "o3"},
    )

    cmd = launcher.build_command(profile, request)
    assert cmd[:3] == ["codex", "exec", "--full-auto"]
    assert "-m" in cmd
    assert "o3" in cmd
    # Codex does not support --effort or --model (long form)
    assert "--effort" not in cmd
    assert "--model" not in cmd
    # Prompt is positional, not via --prompt
    assert "--prompt" not in cmd
    assert "Fix the bug" in cmd


def test_build_command_gemini_cli():
    """Gemini CLI uses `gemini --yolo --prompt PROMPT --model MODEL`."""
    store = ManagedRunStore("/tmp/test-store")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        runtime_id="gemini_cli",
        command_template=["gemini"],
        default_model="gemini-2.5-pro",
        default_effort="high",
    )
    request = _make_request(instruction_ref="Fix the bug")

    cmd = launcher.build_command(profile, request)
    assert cmd[0] == "gemini"
    assert "--model" in cmd
    assert "--yolo" in cmd
    assert "--prompt" in cmd
    assert "--effort" in cmd
    assert "Fix the bug" in cmd


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


def test_build_tmate_wrapper_script_kills_session_on_exit():
    script = ManagedRuntimeLauncher._build_tmate_wrapper_script(
        ["gemini", "--model", "gemini-3.1-pro-preview", "--prompt", "hi"],
        socket_path="/tmp/moonmind/tmate/run-1.sock",
        session_name="mm-run1",
    )
    assert "tmate -S /tmp/moonmind/tmate/run-1.sock kill-session -t mm-run1" in script
    assert "exit \"$mm_rc\"" in script


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
async def test_launch_prepares_workspace_from_existing_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))

    existing_repo = tmp_path / "workspaces" / "existing-run" / "repo"
    existing_repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init"],
        cwd=existing_repo,
        check=True,
        capture_output=True,
    )
    (existing_repo / "README.md").write_text("workspace seed\n", encoding="utf-8")

    store = ManagedRunStore(tmp_path / "managed_runs")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        command_template=["pwd"],
        default_model=None,
        default_effort=None,
    )
    request = _make_request(
        workspace_spec={"newBranch": "chore/update-pause-system-docs-16784273446666462405"}
    )

    record, process, _endpoints = await launcher.launch(
        run_id="run-2",
        request=request,
        profile=profile,
    )
    stdout, _stderr = await process.communicate()

    expected_workspace = tmp_path / "workspaces" / "run-2" / "repo"
    assert record.workspace_path == str(expected_workspace)
    assert expected_workspace.exists()
    assert str(expected_workspace) in stdout.decode("utf-8", errors="replace")
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
    monkeypatch.setattr(launcher, "_find_existing_workspace_repo", lambda **kwargs: None)

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
    # The wrapped command should contain the actual CLI command inline
    # (not "$@" positional args, which don't work with tmate's sh -c)
    assert "gemini" in wrapped_command
    assert "MM_EXIT_FILE" in wrapped_command

    launch_env = next(kwargs for args, kwargs in calls if "new-session" in args)[
        "env"
    ]
    assert launch_env["MM_EXIT_FILE"] == endpoints["exit_code_path"]


@pytest.mark.asyncio
async def test_launch_prepares_workspace_from_repository_spec(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    store = ManagedRunStore(tmp_path / "managed_runs")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request(
        workspace_spec={
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "newBranch": "feature/test",
        }
    )

    class _FakeProcess:
        def __init__(
            self,
            *,
            pid: int = 4321,
            returncode: int = 0,
            stdout_bytes: bytes = b"",
            stderr_bytes: bytes = b"",
        ) -> None:
            self.pid = pid
            self.returncode = returncode
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self._stdout_bytes = stdout_bytes
            self._stderr_bytes = stderr_bytes

        async def wait(self) -> int:
            return self.returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return self._stdout_bytes, self._stderr_bytes

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def _fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ("git", "clone"):
            repo_path = Path(str(args[-1]))
            repo_path.mkdir(parents=True, exist_ok=True)
            return _FakeProcess(pid=1001)
        if args[:2] == ("git", "-C") and args[3:] == (
            "checkout",
            "feature/test",
        ):
            return _FakeProcess(
                pid=1002,
                returncode=1,
                stderr_bytes=(
                    b"error: pathspec 'feature/test' did not match any "
                    b"file(s) known to git"
                ),
            )
        if args[:2] == ("git", "-C") and args[3:] == (
            "checkout",
            "-b",
            "feature/test",
        ):
            return _FakeProcess(pid=1003)
        if args[:2] == ("echo", "hello"):
            return _FakeProcess(pid=2001)
        raise AssertionError(f"Unexpected subprocess call: {args!r}")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    record, process, endpoints = await launcher.launch(
        run_id="workspace-run-1",
        request=request,
        profile=profile,
    )
    await process.wait()

    assert endpoints is None
    expected_workspace = str(
        (tmp_path / "workspaces" / "workspace-run-1" / "repo").resolve()
    )
    assert record.workspace_path == expected_workspace
    assert process.pid == 2001

    clone_call = next(args for args, _ in calls if args[:2] == ("git", "clone"))
    assert "--branch" in clone_call
    assert "main" in clone_call
    assert "--single-branch" in clone_call
    assert "https://github.com/MoonLadderStudios/MoonMind.git" in clone_call

    checkout_call = next(
        args
        for args, _ in calls
        if args[:2] == ("git", "-C")
        and args[3:] == ("checkout", "-b", "feature/test")
    )
    assert checkout_call[-2:] == ("-b", "feature/test")

    launch_kwargs = next(kwargs for args, kwargs in calls if args[:2] == ("echo", "hello"))
    assert launch_kwargs.get("cwd") == expected_workspace


@pytest.mark.asyncio
async def test_launch_reuses_existing_new_branch_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    store = ManagedRunStore(tmp_path / "managed_runs")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request(
        workspace_spec={
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "newBranch": "main",
        }
    )

    class _FakeProcess:
        def __init__(
            self,
            *,
            pid: int = 4321,
            returncode: int = 0,
            stdout_bytes: bytes = b"",
            stderr_bytes: bytes = b"",
        ) -> None:
            self.pid = pid
            self.returncode = returncode
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self._stdout_bytes = stdout_bytes
            self._stderr_bytes = stderr_bytes

        async def wait(self) -> int:
            return self.returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return self._stdout_bytes, self._stderr_bytes

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def _fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ("git", "clone"):
            repo_path = Path(str(args[-1]))
            repo_path.mkdir(parents=True, exist_ok=True)
            return _FakeProcess(pid=1001)
        if args[:2] == ("git", "-C") and args[3:] == (
            "checkout",
            "main",
        ):
            return _FakeProcess(pid=1003)
        if args[:2] == ("echo", "hello"):
            return _FakeProcess(pid=2001)
        raise AssertionError(f"Unexpected subprocess call: {args!r}")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    _record, process, _endpoints = await launcher.launch(
        run_id="workspace-run-existing-branch",
        request=request,
        profile=profile,
    )
    await process.wait()

    checkout_call = next(
        args
        for args, _ in calls
        if args[:2] == ("git", "-C") and args[3:] == ("checkout", "main")
    )
    assert checkout_call[-1] == "main"
    assert "-b" not in checkout_call


@pytest.mark.asyncio
async def test_launch_raises_when_workspace_clone_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    store = ManagedRunStore(tmp_path / "managed_runs")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request(
        workspace_spec={"repository": "MoonLadderStudios/DoesNotExist"}
    )

    class _FakeProcess:
        def __init__(
            self,
            *,
            returncode: int = 0,
            stderr_bytes: bytes = b"",
        ) -> None:
            self.pid = 3333
            self.returncode = returncode
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self._stderr_bytes = stderr_bytes

        async def wait(self) -> int:
            return self.returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", self._stderr_bytes

    async def _fake_create_subprocess_exec(*args, **kwargs):
        if args[:2] == ("git", "clone"):
            return _FakeProcess(
                returncode=128,
                stderr_bytes=b"fatal: repository not found",
            )
        raise AssertionError(f"Unexpected subprocess call: {args!r}")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    with pytest.raises(RuntimeError, match="Command failed with exit code"):
        await launcher.launch(
            run_id="workspace-run-fail",
            request=request,
            profile=profile,
        )
    assert store.load("workspace-run-fail") is None
