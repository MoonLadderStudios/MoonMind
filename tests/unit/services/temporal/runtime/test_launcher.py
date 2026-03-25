import shutil
import asyncio
import subprocess
from pathlib import Path

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
from moonmind.workflows.temporal.runtime.launcher import (
    ManagedRuntimeLauncher,
)
from moonmind.workflows.temporal.runtime.tmate_session import (
    TmateEndpoints,
    TmateSessionManager,
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




@pytest.mark.asyncio
async def test_launch_spawns_process(tmp_path, monkeypatch):
    # Avoid tmate wrapper when tmate is installed in CI/Docker (would hang on wait-ready).
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request()

    record, process, endpoints, _tmate_manager = await launcher.launch(
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
async def test_launch_injects_secret_passthrough_env_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    monkeypatch.setenv("GH_TOKEN", "ghp-runtime")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-legacy")

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        command_template=["echo", "hello"],
        env_overrides={"MM_SAFE": "1"},
        passthrough_env_keys=["GH_TOKEN", "GITHUB_TOKEN"],
    )
    request = _make_request()

    class _FakeProcess:
        def __init__(self, pid: int = 777) -> None:
            self.pid = pid
            self.returncode = 0

        async def wait(self) -> int:
            return 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    captured_env: dict[str, str] = {}

    async def _fake_create_subprocess_exec(*_args, **kwargs):
        env = kwargs.get("env")
        if isinstance(env, dict):
            captured_env.update(env)
        return _FakeProcess()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    _record, process, endpoints, _tmate_manager = await launcher.launch(
        run_id="run-passthrough-1", request=request, profile=profile
    )
    await process.wait()

    assert endpoints is None
    assert captured_env["MM_SAFE"] == "1"
    assert captured_env["GH_TOKEN"] == "ghp-runtime"
    assert captured_env["GITHUB_TOKEN"] == "ghp-legacy"


@pytest.mark.asyncio
async def test_idempotent_launch_rejects_active(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request()

    # First launch
    record, process, _, _ = await launcher.launch(
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

    record, process, _endpoints, _tmate_manager = await launcher.launch(
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
    """Verify that the launcher delegates to TmateSessionManager correctly.

    Since the launcher now delegates to TmateSessionManager.start(), we mock
    the manager's start() method to verify delegation and endpoint mapping.
    """
    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["gemini", "run"])
    request = _make_request()

    class _FakeProcess:
        def __init__(self, *, pid: int = 9999) -> None:
            self.pid = pid
            self.returncode = None

        async def wait(self) -> int:
            self.returncode = 0
            return 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    fake_process = _FakeProcess()
    fake_endpoints = TmateEndpoints(
        session_name="mm-tmaterun1xxxx",
        socket_path="/tmp/moonmind/tmate/mm-tmaterun1xxxx.sock",
        attach_ro="ssh ro",
        attach_rw="ssh rw",
        web_ro="web ro",
        web_rw="web rw",
    )

    monkeypatch.setattr(
        TmateSessionManager,
        "is_available",
        staticmethod(lambda: True),
    )

    async def _fake_start(self, command=None, *, env=None, cwd=None, exit_code_capture=True, timeout_seconds=30.0):
        self._process = fake_process
        self._endpoints = fake_endpoints
        self._exit_code_path_value = Path("/tmp/moonmind/tmate/mm-tmaterun1xxxx.exit")
        return fake_endpoints

    monkeypatch.setattr(TmateSessionManager, "start", _fake_start)

    record, process, endpoints, _tmate_manager = await launcher.launch(
        run_id="tmate-run-1",
        request=request,
        profile=profile,
    )

    assert record.pid == 9999
    assert process.pid == 9999
    assert endpoints is not None
    assert endpoints["tmate_session_name"] == "mm-tmaterun1xxxx"
    assert endpoints["attach_rw"] == "ssh rw"
    assert endpoints["web_rw"] == "web rw"
    assert endpoints["exit_code_path"].endswith(".exit")



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

    record, process, endpoints, _tmate_manager = await launcher.launch(
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

    _record, process, _endpoints, _tmate_manager = await launcher.launch(
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


def test_build_command_cursor_cli():
    """Test cursor_cli command construction with -p, --output-format, --force."""
    store = ManagedRunStore("/tmp/test-store")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        runtime_id="cursor_cli",
        command_template=["cursor-agent"],
        default_model="claude-4-sonnet",
        default_effort=None,
    )
    request = _make_request(instruction_ref="implement the task")

    cmd = launcher.build_command(profile, request)
    assert cmd[0] == "cursor-agent"
    assert "--model" in cmd
    assert "claude-4-sonnet" in cmd
    assert "-p" in cmd
    assert "implement the task" in cmd
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    assert "--force" in cmd
    # Should NOT have --instruction-ref (that's for non-cursor runtimes)
    assert "--instruction-ref" not in cmd
    # Should NOT have --sandbox when not specified
    assert "--sandbox" not in cmd


def test_build_command_cursor_cli_with_sandbox():
    """Test cursor_cli --sandbox flag from request parameters."""
    store = ManagedRunStore("/tmp/test-store")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        runtime_id="cursor_cli",
        command_template=["cursor-agent"],
        default_model=None,
        default_effort=None,
    )
    request = _make_request(
        instruction_ref="implement the feature",
        parameters={"sandbox_mode": "disabled"},
    )

    cmd = launcher.build_command(profile, request)
    assert cmd[0] == "cursor-agent"
    assert "-p" in cmd
    assert "--sandbox" in cmd
    assert "disabled" in cmd
