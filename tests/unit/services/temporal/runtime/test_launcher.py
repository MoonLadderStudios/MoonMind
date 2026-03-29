import asyncio
import subprocess
from pathlib import Path

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
from moonmind.workflows.temporal.runtime.launcher import (
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
    # Gemini CLI does not support --effort
    assert "--effort" not in cmd
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
    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request()

    record, process, endpoints, _cleanup = await launcher.launch(
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

    _record, process, endpoints, _cleanup = await launcher.launch(
        run_id="run-passthrough-1", request=request, profile=profile
    )
    await process.wait()

    assert endpoints is None
    assert captured_env["MM_SAFE"] == "1"
    assert captured_env["GH_TOKEN"] == "ghp-runtime"
    assert captured_env["GITHUB_TOKEN"] == "ghp-legacy"


def test_persist_gh_config_writes_hosts_yml(tmp_path):
    env = {"GITHUB_TOKEN": "ghp_testtoken123", "PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(env, str(tmp_path))
    hosts = tmp_path / ".moonmind" / "gh" / "hosts.yml"
    assert hosts.exists()
    content = hosts.read_text()
    assert "ghp_testtoken123" in content
    assert "github.com" in content
    assert env["GH_CONFIG_DIR"] == str(tmp_path / ".moonmind" / "gh")
    assert (hosts.stat().st_mode & 0o777) == 0o600


def test_persist_gh_config_skips_without_token(tmp_path):
    env = {"PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(env, str(tmp_path))
    assert not (tmp_path / ".moonmind" / "gh").exists()
    assert "GH_CONFIG_DIR" not in env


def test_persist_gh_config_skips_without_workspace():
    env = {"GITHUB_TOKEN": "ghp_test"}
    ManagedRuntimeLauncher._persist_gh_config(env, None)
    assert "GH_CONFIG_DIR" not in env


def test_persist_gh_config_writes_git_credential_helper(tmp_path):
    """When a .git/config exists, _persist_gh_config injects a credential
    helper pointing to a git-credentials store file."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    git_config = git_dir / "config"
    git_config.write_text(
        "[core]\n\trepositoryformatversion = 0\n",
        encoding="utf-8",
    )

    env = {"GITHUB_TOKEN": "ghp_credtest456", "PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(env, str(tmp_path))

    # gh hosts.yml should still be written
    assert (tmp_path / ".moonmind" / "gh" / "hosts.yml").exists()

    # git-credentials store file should contain the token
    cred_store = tmp_path / ".moonmind" / "gh" / "git-credentials"
    assert cred_store.exists()
    cred_content = cred_store.read_text()
    assert "ghp_credtest456" in cred_content
    assert "github.com" in cred_content
    assert (cred_store.stat().st_mode & 0o777) == 0o600

    # .git/config should have the credential helper section
    updated_config = git_config.read_text()
    assert "# moonmind-credential-helper" in updated_config
    assert "[credential]" in updated_config
    assert "store --file=" in updated_config
    assert str(cred_store) in updated_config


def test_persist_gh_config_git_credential_idempotent(tmp_path):
    """Calling _persist_gh_config twice should not duplicate the credential section."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    git_config = git_dir / "config"
    git_config.write_text("[core]\n\tbare = false\n", encoding="utf-8")

    env = {"GITHUB_TOKEN": "ghp_idempotent", "PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(env, str(tmp_path))
    ManagedRuntimeLauncher._persist_gh_config(env, str(tmp_path))

    updated_config = git_config.read_text()
    assert updated_config.count("# moonmind-credential-helper") == 1
    assert updated_config.count("[credential]") == 1


def test_persist_gh_config_skips_git_cred_without_git_dir(tmp_path):
    """When no .git/config exists, only gh hosts.yml should be written."""
    env = {"GITHUB_TOKEN": "ghp_nogitdir", "PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(env, str(tmp_path))

    # gh config should still be written
    assert (tmp_path / ".moonmind" / "gh" / "hosts.yml").exists()
    # But no git-credentials file since there's no .git/config
    assert not (tmp_path / ".moonmind" / "gh" / "git-credentials").exists()


@pytest.mark.asyncio
async def test_idempotent_launch_returns_existing_for_active(tmp_path, monkeypatch):
    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request()

    # First launch
    record, process, _, _cleanup = await launcher.launch(
        run_id="run-1", request=request, profile=profile
    )
    await process.wait()

    # Second launch with same run_id returns existing record (idempotent)
    existing, exc_process, exc_endpoints, _cleanup2 = await launcher.launch(
        run_id="run-1", request=request, profile=profile
    )
    assert existing.run_id == "run-1"
    assert exc_process is None
    assert exc_endpoints is None


@pytest.mark.asyncio
async def test_launch_prepares_workspace_from_existing_repo(tmp_path, monkeypatch):
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
        workspace_spec={"targetBranch": "chore/update-pause-system-docs-16784273446666462405"}
    )

    record, process, _endpoints, _cleanup = await launcher.launch(
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
async def test_launch_prepares_workspace_from_repository_spec(tmp_path, monkeypatch):
    store = ManagedRunStore(tmp_path / "managed_runs")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request(
        workspace_spec={
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "targetBranch": "feature/test",
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

    record, process, endpoints, _cleanup = await launcher.launch(
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
    store = ManagedRunStore(tmp_path / "managed_runs")
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request(
        workspace_spec={
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "targetBranch": "main",
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

    _record, process, _endpoints, _cleanup = await launcher.launch(
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


@pytest.mark.asyncio
async def test_launch_env_overrides_layer_on_top_of_os_environ(tmp_path, monkeypatch):
    """Profile env_overrides should be layered on top of os.environ, not replace it.

    Regression test for the env stripping bug: when profile.env_overrides is
    set, the child process must still inherit essential ambient vars (PATH,
    HOME, etc.) from the parent environment, with the profile-specific values
    taking precedence for any keys that appear in both.
    """
    # Ensure PATH is visible in os.environ so we can assert it propagates.
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/testuser")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-drop-me")

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        command_template=["echo", "hello"],
        # Only ANTHROPIC_BASE_URL is overridden — PATH and HOME must still pass.
        env_overrides={
            "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
            "ANTHROPIC_MODEL": "MiniMax-M2.7",
        },
        clear_env_keys=["OPENAI_API_KEY"],
        passthrough_env_keys=[],
    )
    request = _make_request()

    class _FakeProcess:
        def __init__(self, pid: int = 999) -> None:
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

    _record, process, endpoints, _cleanup = await launcher.launch(
        run_id="run-env-layer-1", request=request, profile=profile
    )
    await process.wait()

    # Profile-specific overrides must be present.
    assert captured_env["ANTHROPIC_BASE_URL"] == "https://api.minimax.io/anthropic"
    assert captured_env["ANTHROPIC_MODEL"] == "MiniMax-M2.7"

    # Ambient environment variables must NOT have been stripped.
    assert "PATH" in captured_env, "PATH was stripped from child env — env bug reintroduced"
    assert "HOME" in captured_env, "HOME was stripped from child env — env bug reintroduced"
    assert captured_env["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    assert captured_env["HOME"] == "/home/testuser"

    # Keys specified in clear_env_keys must be stripped
    assert "OPENAI_API_KEY" not in captured_env, "clear_env_keys was ignored; ambient credential leaked"


@pytest.mark.asyncio
async def test_launch_materializes_managed_api_key_target_env(tmp_path, monkeypatch):
    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        runtime_id="claude_code",
        command_template=["claude", "-p", "hello"],
        env_overrides={
            "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
            "ANTHROPIC_MODEL": "MiniMax-M2.7",
        },
        passthrough_env_keys=[],
        secret_refs={
            "ANTHROPIC_AUTH_TOKEN": "MINIMAX_API_KEY",
        },
    )
    request = _make_request()

    class _FakeProcess:
        def __init__(self, pid: int = 1001) -> None:
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

    async def _fake_resolve(secret_name: str) -> str:
        assert secret_name == "MINIMAX_API_KEY"
        return "resolved-minimax-token"

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_managed_api_key_reference",
        _fake_resolve,
    )

    _record, process, _endpoints, _cleanup = await launcher.launch(
        run_id="run-managed-api-key-1", request=request, profile=profile
    )
    await process.wait()

    assert captured_env["ANTHROPIC_AUTH_TOKEN"] == "resolved-minimax-token"
    assert "MANAGED_API_KEY_REF" not in captured_env
    assert "MANAGED_API_KEY_TARGET_ENV" not in captured_env
