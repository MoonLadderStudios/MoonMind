import asyncio
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
        command_template=["codex", "exec"],
        default_model="gpt-5.3-codex",
        default_effort="high",
    )
    request = _make_request(
        instruction_ref="Fix the bug",
        parameters={"model": "o3"},
    )

    cmd = launcher.build_command(profile, request)
    assert cmd[:2] == ["codex", "exec"]
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

    record, process, _cleanup, _deferred_cleanup = await launcher.launch(
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
async def test_launch_keeps_workflow_id_none_as_null(tmp_path):
    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request()

    record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-none-workflow",
        workflow_id=None,
        request=request,
        profile=profile,
    )
    await process.wait()

    assert record.workflow_id is None
    loaded = store.load("run-none-workflow")
    assert loaded is not None
    assert loaded.workflow_id is None


@pytest.mark.asyncio
async def test_launch_injects_secret_passthrough_env_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-runtime")
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)

    async def _fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.resolve_github_token_for_launch",
        _fake_resolve,
    )

    profile = _make_profile(
        command_template=["echo", "hello"],
        env_overrides={"MM_SAFE": "1"},
        passthrough_env_keys=["GITHUB_TOKEN"],
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

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-passthrough-1", request=request, profile=profile
    )
    await process.wait()

    assert captured_env["MM_SAFE"] == "1"
    assert captured_env["GITHUB_TOKEN"] == "ghp-runtime"


@pytest.mark.asyncio
async def test_launch_registers_generated_support_dir_for_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)

    async def _fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.resolve_github_token_for_launch",
        _fake_resolve,
    )

    class _FakeProcess:
        def __init__(self, pid: int = 778) -> None:
            self.pid = pid
            self.returncode = 0
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()

        async def wait(self) -> int:
            return 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeProcess()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    profile = _make_profile(
        runtime_id="codex_cli",
        command_template=["codex", "exec"],
        secret_refs={"provider_api_key": "env://OPENROUTER_API_KEY"},
        env_template={"OPENROUTER_API_KEY": {"from_secret_ref": "provider_api_key"}},
        file_templates=[
            {
                "path": "{{runtime_support_dir}}/codex-home/config.toml",
                "format": "toml",
                "mergeStrategy": "replace",
                "contentTemplate": {"model_provider": "openrouter"},
            }
        ],
        home_path_overrides={"CODEX_HOME": "{{runtime_support_dir}}/codex-home"},
    )
    request = _make_request()

    _record, process, cleanup_paths, _deferred_cleanup = await launcher.launch(
        run_id="run-support-dir-cleanup",
        request=request,
        profile=profile,
    )
    await process.wait()

    cleanup_path_set = set(cleanup_paths)
    support_dirs = [
        Path(path)
        for path in cleanup_path_set
        if Path(path).name.startswith("mm_profile_support_")
    ]

    assert len(support_dirs) == 1
    support_dir = support_dirs[0]
    assert support_dir.exists()
    assert str(support_dir / "codex-home" / "config.toml") in cleanup_path_set


@pytest.mark.asyncio
@patch("moonmind.rag.context_injection.ContextInjectionService")
async def test_launch_builds_codex_command_after_workspace_preparation(
    mock_service_class,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    mock_service = mock_service_class.return_value
    mock_service.inject_context = AsyncMock()

    async def _fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.resolve_github_token_for_launch",
        _fake_resolve,
    )

    class _FakeProcess:
        def __init__(self, pid: int = 889) -> None:
            self.pid = pid
            self.returncode = 0
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()

        async def wait(self) -> int:
            return 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    captured_args: tuple[object, ...] = ()

    async def _fake_create_subprocess_exec(*args, **_kwargs):
        nonlocal captured_args
        captured_args = args
        return _FakeProcess()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    profile = _make_profile(
        runtime_id="codex_cli",
        command_template=["codex", "exec"],
        default_model="qwen/qwen3.6-plus",
    )
    request = _make_request(instruction_ref="Do work")

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-codex-note",
        request=request,
        profile=profile,
        workspace_path=workspace,
    )
    await process.wait()

    assert captured_args[:2] == ("codex", "exec")
    assert any(
        isinstance(arg, str) and "Managed Codex CLI note:" in arg
        for arg in captured_args
    )


@pytest.mark.asyncio
async def test_launch_resets_stale_live_log_spool(tmp_path, monkeypatch):
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spool_path = workspace / "live_streams.spool"
    spool_path.write_text("stale prior run output\n", encoding="utf-8")

    async def _fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.resolve_github_token_for_launch",
        _fake_resolve,
    )

    class _FakeProcess:
        def __init__(self, pid: int = 888) -> None:
            self.pid = pid
            self.returncode = 0

        async def wait(self) -> int:
            return 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeProcess()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request()

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-reset-spool",
        request=request,
        profile=profile,
        workspace_path=workspace,
    )
    await process.wait()

    assert not spool_path.exists()


def test_persist_gh_config_writes_broker_helpers_without_plaintext_token(tmp_path):
    env = {"GITHUB_TOKEN": "ghp_testtoken123", "PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(
        env,
        str(tmp_path),
        github_socket_path="/tmp/github-auth.sock",
        real_gh_path="/usr/bin/gh",
    )
    gh_wrapper = tmp_path / ".moonmind" / "bin" / "gh"
    git_helper = tmp_path / ".moonmind" / "bin" / "git-credential-moonmind"
    gitconfig = tmp_path / ".moonmind" / "gitconfig"
    assert gh_wrapper.exists()
    assert git_helper.exists()
    assert gitconfig.exists()
    assert env["GIT_CONFIG_GLOBAL"] == str(gitconfig)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["PATH"].startswith(str(tmp_path / ".moonmind" / "bin"))
    assert "GH_CONFIG_DIR" not in env
    assert (gh_wrapper.stat().st_mode & 0o777) == 0o700
    assert (git_helper.stat().st_mode & 0o777) == 0o700
    gitconfig_text = gitconfig.read_text(encoding="utf-8")
    assert "[safe]" in gitconfig_text
    assert "[credential]" in gitconfig_text
    assert f'\tdirectory = "{tmp_path.resolve()}"' in gitconfig_text
    assert "ghp_testtoken123" not in gh_wrapper.read_text(encoding="utf-8")
    assert "ghp_testtoken123" not in git_helper.read_text(encoding="utf-8")
    assert "ghp_testtoken123" not in gitconfig_text


def test_persist_gh_config_writes_safe_directory_without_token(tmp_path):
    env = {"PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(env, str(tmp_path))
    gitconfig = tmp_path / ".moonmind" / "gitconfig"
    assert gitconfig.exists()
    assert "[safe]" in gitconfig.read_text(encoding="utf-8")
    assert f'\tdirectory = "{tmp_path.resolve()}"' in gitconfig.read_text(
        encoding="utf-8"
    )
    assert "GH_CONFIG_DIR" not in env
    assert env["GIT_CONFIG_GLOBAL"] == str(gitconfig)
    assert "[credential]" not in gitconfig.read_text(encoding="utf-8")


def test_persist_gh_config_skips_without_workspace():
    env = {"PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(env, None)
    assert "GH_CONFIG_DIR" not in env
    assert "GIT_CONFIG_GLOBAL" not in env


def test_build_github_socket_path_stays_short_for_long_workspace_paths(tmp_path):
    support_root = tmp_path / ("nested-" * 12) / ("workspace-" * 8)
    socket_path = ManagedRuntimeLauncher._build_github_socket_path(
        run_id="run-github-secret-ref-1",
        support_root=str(support_root),
    )

    assert len(socket_path.encode("utf-8")) < 80
    assert str(support_root) not in socket_path
    assert socket_path.endswith(".sock")


def test_persist_gh_config_uses_support_root_for_repo_workspace(tmp_path):
    run_root = tmp_path / "run-1"
    repo_root = run_root / "repo"
    git_dir = repo_root / ".git"
    git_dir.mkdir(parents=True)
    git_config = git_dir / "config"
    git_config.write_text(
        "[core]\n\trepositoryformatversion = 0\n",
        encoding="utf-8",
    )

    env = {"PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(
        env,
        str(repo_root),
        support_root=str(run_root),
        github_socket_path="/tmp/github-auth.sock",
        real_gh_path="/usr/bin/gh",
    )

    gh_wrapper = run_root / ".moonmind" / "bin" / "gh"
    git_helper = run_root / ".moonmind" / "bin" / "git-credential-moonmind"
    gitconfig = run_root / ".moonmind" / "gitconfig"
    assert gh_wrapper.exists()
    assert git_helper.exists()
    assert gitconfig.exists()
    assert not (repo_root / ".moonmind").exists()
    assert env["GIT_CONFIG_GLOBAL"] == str(gitconfig)
    assert env["PATH"].startswith(str(run_root / ".moonmind" / "bin"))

    updated_config = git_config.read_text()
    assert str(git_helper) in updated_config
    assert f'\tdirectory = "{repo_root.resolve()}"' in gitconfig.read_text(
        encoding="utf-8"
    )


def test_persist_gh_config_writes_git_credential_helper(tmp_path):
    """When a .git/config exists, _persist_gh_config injects a broker helper."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    git_config = git_dir / "config"
    git_config.write_text(
        "[core]\n\trepositoryformatversion = 0\n",
        encoding="utf-8",
    )

    env = {"PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(
        env,
        str(tmp_path),
        github_socket_path="/tmp/github-auth.sock",
    )

    git_helper = tmp_path / ".moonmind" / "bin" / "git-credential-moonmind"
    assert git_helper.exists()
    assert (git_helper.stat().st_mode & 0o777) == 0o700

    # .git/config should have the credential helper section
    updated_config = git_config.read_text()
    assert "# moonmind-credential-helper" in updated_config
    assert "[credential]" in updated_config
    assert str(git_helper) in updated_config


def test_persist_gh_config_git_credential_idempotent(tmp_path):
    """Calling _persist_gh_config twice should not duplicate the credential section."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    git_config = git_dir / "config"
    git_config.write_text("[core]\n\tbare = false\n", encoding="utf-8")

    env = {"PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(
        env,
        str(tmp_path),
        github_socket_path="/tmp/github-auth.sock",
    )
    ManagedRuntimeLauncher._persist_gh_config(
        env,
        str(tmp_path),
        github_socket_path="/tmp/github-auth.sock",
    )

    updated_config = git_config.read_text()
    assert updated_config.count("# moonmind-credential-helper") == 1
    assert updated_config.count("[credential]") == 1


def test_persist_gh_config_skips_git_cred_without_git_dir(tmp_path):
    """When no .git/config exists, global git config still carries the helper."""
    env = {"PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(
        env,
        str(tmp_path),
        github_socket_path="/tmp/github-auth.sock",
    )

    assert (tmp_path / ".moonmind" / "bin" / "git-credential-moonmind").exists()
    gitconfig = tmp_path / ".moonmind" / "gitconfig"
    assert "[credential]" in gitconfig.read_text(encoding="utf-8")


def test_persist_gh_config_quotes_helper_path_when_store_has_spaces(tmp_path):
    run_root = tmp_path / "run root"
    git_dir = run_root / "repo" / ".git"
    git_dir.mkdir(parents=True)
    git_config = git_dir / "config"
    git_config.write_text("[core]\n\tbare = false\n", encoding="utf-8")

    env = {"PATH": "/usr/bin"}
    ManagedRuntimeLauncher._persist_gh_config(
        env,
        str(run_root / "repo"),
        support_root=str(run_root),
        github_socket_path="/tmp/github-auth.sock",
    )

    updated_config = git_config.read_text(encoding="utf-8")
    assert "helper = !" in updated_config
    assert str(run_root / ".moonmind" / "bin" / "git-credential-moonmind") in updated_config


def test_persist_gh_config_writes_git_identity_to_global_config(tmp_path):
    env = {
        "PATH": "/usr/bin",
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_AUTHOR_EMAIL": "test@example.com",
    }

    ManagedRuntimeLauncher._persist_gh_config(env, str(tmp_path))

    gitconfig = tmp_path / ".moonmind" / "gitconfig"
    text = gitconfig.read_text(encoding="utf-8")
    assert "[user]" in text
    assert "\tname = Test User" in text
    assert "\temail = test@example.com" in text


@pytest.mark.asyncio
async def test_idempotent_launch_returns_existing_for_active(tmp_path, monkeypatch):
    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(command_template=["echo", "hello"])
    request = _make_request()

    # First launch
    record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-1", request=request, profile=profile
    )
    await process.wait()

    # Second launch with same run_id returns existing record (idempotent)
    existing, exc_process, _cleanup2, _deferred_cleanup2 = await launcher.launch(
        run_id="run-1", request=request, profile=profile
    )
    assert existing.run_id == "run-1"
    assert exc_process is None


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

    record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-2",
        request=request,
        profile=profile,
    )
    stdout, _stderr = await process.communicate()

    expected_workspace = tmp_path / "workspaces" / "run-2" / "repo"
    assert record.workspace_path == str(expected_workspace)
    assert record.live_stream_capable is True
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

    record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="workspace-run-1",
        request=request,
        profile=profile,
    )
    await process.wait()

    expected_workspace = str(
        (tmp_path / "workspaces" / "workspace-run-1" / "repo").resolve()
    )
    assert record.workspace_path == expected_workspace
    assert record.live_stream_capable is True
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
async def test_launch_emits_workspace_preparation_applied_annotation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    class _RecorderLogStreamer:
        def __init__(self) -> None:
            self.emissions: list[dict[str, object]] = []

        def emit_system_annotation(self, **kwargs: object) -> None:
            self.emissions.append(kwargs)

    log_streamer = _RecorderLogStreamer()
    store = ManagedRunStore(tmp_path / "managed_runs")
    launcher = ManagedRuntimeLauncher(store, log_streamer=log_streamer)
    profile = _make_profile(
        runtime_id="claude_code",
        command_template=["claude", "-p", "hello"],
    )
    request = _make_request(instruction_ref="Run task")
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    class _FakeProcess:
        def __init__(self, pid: int = 1001, returncode: int = 0) -> None:
            self.pid = pid
            self.returncode = returncode
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()

        async def wait(self) -> int:
            return self.returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeProcess()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-applied",
        request=request,
        profile=profile,
        workspace_path=str(workspace_path),
    )

    await process.wait()
    assert process is not None
    assert record.workspace_path == str(workspace_path)
    assert (workspace_path / "CLAUDE.md").read_text(encoding="utf-8") == "Run task"
    assert any(
        emission.get("annotation_type") == "workspace_preparation_applied"
        for emission in log_streamer.emissions
    )


@pytest.mark.asyncio
async def test_launch_emits_workspace_preparation_skipped_annotation_for_existing_file(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    class _RecorderLogStreamer:
        def __init__(self) -> None:
            self.emissions: list[dict[str, object]] = []

        def emit_system_annotation(self, **kwargs: object) -> None:
            self.emissions.append(kwargs)

    log_streamer = _RecorderLogStreamer()
    store = ManagedRunStore(tmp_path / "managed_runs")
    launcher = ManagedRuntimeLauncher(store, log_streamer=log_streamer)
    profile = _make_profile(
        runtime_id="claude_code",
        command_template=["claude", "-p", "hello"],
    )
    request = _make_request(instruction_ref="Run task")
    workspace_path = tmp_path / "workspace-skipped"
    workspace_path.mkdir()
    (workspace_path / "CLAUDE.md").write_text("already there", encoding="utf-8")

    class _FakeProcess:
        def __init__(self, pid: int = 1002, returncode: int = 0) -> None:
            self.pid = pid
            self.returncode = returncode
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()

        async def wait(self) -> int:
            return self.returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeProcess()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-skipped",
        request=request,
        profile=profile,
        workspace_path=str(workspace_path),
    )

    await process.wait()
    assert process is not None
    assert record.workspace_path == str(workspace_path)
    assert (workspace_path / "CLAUDE.md").read_text(encoding="utf-8") == "already there"
    assert any(
        emission.get("annotation_type") == "workspace_preparation_skipped"
        for emission in log_streamer.emissions
    )


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

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
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
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

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

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
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
async def test_launch_filters_ambient_jira_credentials_from_child_env(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/testuser")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("ATLASSIAN_API_KEY", "atl-secret-token")
    monkeypatch.setenv("ATLASSIAN_API_KEY_SECRET_REF", "atlassian-api-key")
    monkeypatch.setenv("ATLASSIAN_EMAIL", "bot@example.com")
    monkeypatch.setenv("ATLASSIAN_EMAIL_SECRET_REF", "atlassian-email")
    monkeypatch.setenv("ATLASSIAN_SITE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("ATLASSIAN_SITE_URL_SECRET_REF", "atlassian-site-url")

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        command_template=["echo", "hello"],
        env_overrides={"MM_SAFE": "1"},
        passthrough_env_keys=[],
    )
    request = _make_request()

    class _FakeProcess:
        def __init__(self, pid: int = 1000) -> None:
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

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-jira-env-filter-1", request=request, profile=profile
    )
    await process.wait()

    assert captured_env["MM_SAFE"] == "1"
    assert captured_env["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    assert captured_env["HOME"] == "/home/testuser"
    assert not any(key.startswith("ATLASSIAN_") for key in captured_env)


@pytest.mark.asyncio
async def test_launch_materializes_managed_api_key_target_env(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)

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
        "moonmind.workflows.temporal.runtime.launcher.shutil.which",
        lambda command: "/usr/bin/gh" if command == "gh" else None,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_managed_api_key_reference",
        _fake_resolve,
    )

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-managed-api-key-1", request=request, profile=profile
    )
    await process.wait()

    assert captured_env["ANTHROPIC_AUTH_TOKEN"] == "resolved-minimax-token"
    assert "MANAGED_API_KEY_REF" not in captured_env
    assert "MANAGED_API_KEY_TARGET_ENV" not in captured_env


@pytest.mark.asyncio
async def test_launch_resolves_github_token_from_secret_ref_setting(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        runtime_id="claude_code",
        command_template=["claude", "-p", "hello"],
        env_overrides={},
        passthrough_env_keys=[],
        secret_refs={},
    )
    request = _make_request(
        workspace_spec={"repository": str(tmp_path / "source-repo")},
    )

    source_repo = tmp_path / "source-repo"
    subprocess.run(["git", "init", str(source_repo)], check=True, capture_output=True)

    class _FakeProcess:
        def __init__(self, pid: int = 1002) -> None:
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
        assert secret_name == "db://github-pat"
        return "resolved-github-token"

    from moonmind.config.settings import settings as app_settings

    monkeypatch.setattr(
        app_settings.github,
        "github_token_secret_ref",
        "db://github-pat",
    )
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.shutil.which",
        lambda command: "/usr/bin/gh" if command == "gh" else None,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_managed_api_key_reference",
        _fake_resolve,
    )

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-github-secret-ref-1", request=request, profile=profile
    )
    await process.wait()

    run_root = store.store_root.parent / "workspaces" / "run-github-secret-ref-1"
    assert captured_env["GIT_TERMINAL_PROMPT"] == "0"
    assert "GITHUB_TOKEN" not in captured_env
    assert captured_env["PATH"].startswith(str(run_root / ".moonmind" / "bin"))
    gitconfig = Path(captured_env["GIT_CONFIG_GLOBAL"])
    assert gitconfig.exists()
    assert "[credential]" in gitconfig.read_text(encoding="utf-8")
    assert "resolved-github-token" not in gitconfig.read_text(encoding="utf-8")
    assert (run_root / ".moonmind" / "bin" / "gh").exists()


@pytest.mark.asyncio
async def test_launch_resolves_github_token_from_managed_secrets_store_without_profile_ref(
    tmp_path, monkeypatch
):
    """Managed secret slug GITHUB_TOKEN (Settings) supplies gh without profile secret_refs."""
    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        runtime_id="claude_code",
        command_template=["claude", "-p", "hello"],
        env_overrides={},
        passthrough_env_keys=[],
        secret_refs={},
    )
    request = _make_request(
        workspace_spec={"repository": str(tmp_path / "source-repo")},
    )

    source_repo = tmp_path / "source-repo"
    subprocess.run(["git", "init", str(source_repo)], check=True, capture_output=True)

    class _FakeProcess:
        def __init__(self, pid: int = 1003) -> None:
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

    async def _fake_store_token() -> str:
        return "resolved-from-managed-secrets-table"

    from moonmind.config.settings import settings as app_settings

    monkeypatch.setattr(app_settings.github, "github_token_secret_ref", None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.shutil.which",
        lambda command: "/usr/bin/gh" if command == "gh" else None,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_managed_github_token_from_store",
        _fake_store_token,
    )

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-github-managed-store-1", request=request, profile=profile
    )
    await process.wait()

    run_root = store.store_root.parent / "workspaces" / "run-github-managed-store-1"
    assert captured_env["GIT_TERMINAL_PROMPT"] == "0"
    assert "GITHUB_TOKEN" not in captured_env
    assert captured_env["PATH"].startswith(str(run_root / ".moonmind" / "bin"))
    assert (run_root / ".moonmind" / "bin" / "gh").exists()


@pytest.mark.asyncio
async def test_launch_keeps_direct_github_env_for_codex_cli_managed_runs(
    tmp_path, monkeypatch
):
    """Codex CLI managed runs keep token env because nested shell tools may bypass wrappers."""

    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    profile = _make_profile(
        runtime_id="codex_cli",
        command_template=["codex", "exec", "hello"],
        env_overrides={},
        passthrough_env_keys=[],
        secret_refs={},
    )
    request = _make_request(
        workspace_spec={"repository": str(tmp_path / "source-repo")},
    )

    source_repo = tmp_path / "source-repo"
    subprocess.run(["git", "init", str(source_repo)], check=True, capture_output=True)

    class _FakeProcess:
        def __init__(self, pid: int = 1004) -> None:
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

    async def _fake_store_token() -> str:
        return "resolved-from-managed-secrets-table"

    from moonmind.config.settings import settings as app_settings

    monkeypatch.setattr(app_settings.github, "github_token_secret_ref", None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.shutil.which",
        lambda command: "/usr/bin/gh" if command == "gh" else None,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_managed_github_token_from_store",
        _fake_store_token,
    )

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-github-managed-store-codex-1", request=request, profile=profile
    )
    await process.wait()

    run_root = store.store_root.parent / "workspaces" / "run-github-managed-store-codex-1"
    assert captured_env["GIT_TERMINAL_PROMPT"] == "0"
    assert captured_env["GITHUB_TOKEN"] == "resolved-from-managed-secrets-table"
    assert captured_env["PATH"].startswith(str(run_root / ".moonmind" / "bin"))
    assert (run_root / ".moonmind" / "bin" / "gh").exists()


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_launch_privilege_drop_for_claude_code_as_root(tmp_path, monkeypatch):
    """When launched as root for claude_code runtime, the process should:
    1. chown the full run workspace root to app:app so the app user can write
       both repo files and support artifacts
    2. Use runuser -u app -- with an app login-shaped env block via env=
    """
    captured_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _FakeProcess:
        def __init__(self, pid: int = 7777) -> None:
            self.pid = pid
            self.returncode = 0

        async def wait(self) -> int:
            return 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def _fake_create_subprocess_exec(*args, **kwargs):
        captured_calls.append((args, kwargs))
        return _FakeProcess()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    chown_calls: list[tuple[object, ...]] = []
    config_creation_calls: list[tuple[object, ...]] = []

    async def _fake_run_checked_command(self, *cmd, **kw):
        # Capture chown calls so we can verify the workspace ownership transfer
        if cmd[:2] == ("chown", "-R"):
            chown_calls.append(cmd)
        # Capture config creation runuser calls
        if cmd[:3] == ("runuser", "-u", "app") and "python3" in cmd:
            config_creation_calls.append(cmd)
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.ManagedRuntimeLauncher._run_checked_command",
        _fake_run_checked_command,
    )

    # Mock Path.exists to simulate the config file not existing initially
    original_exists = Path.exists

    def _mock_exists(self):
        path_str = str(self)
        # Simulate .claude.json doesn't exist so config creation is triggered
        if path_str == "/home/app/.claude.json":
            return False
        # For is_file() checks after creation, simulate success
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", _mock_exists)

    # Mock Path.is_file to simulate successful creation
    original_is_file = Path.is_file

    def _mock_is_file(self):
        path_str = str(self)
        if path_str == "/home/app/.claude.json":
            return True  # Pretend it was created successfully
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", _mock_is_file)

    # Simulate running as root (euid == 0)
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)

    # Create a workspace with a .git directory to satisfy chown
    workspace_root = tmp_path / "workspaces" / "root-run" / "repo"
    workspace_root.mkdir(parents=True)
    (workspace_root / ".git").mkdir()

    profile = _make_profile(
        runtime_id="claude_code",
        command_template=["claude", "-p", "hello"],
        env_overrides={"MY_CUSTOM_VAR": "test-value"},
        passthrough_env_keys=[],
    )
    request = _make_request()

    _, _, _, _ = await launcher.launch(
        run_id="root-run",
        request=request,
        profile=profile,
        workspace_path=str(workspace_root),
    )

    run_root = workspace_root.parent

    # Verify chown was called on the full run root, not just repo/
    assert len(chown_calls) == 1, f"Expected 1 chown call, got {len(chown_calls)}"
    chown_call = chown_calls[0]
    assert "app:app" in chown_call
    assert str(run_root) in chown_call

    # Verify config creation was attempted via runuser
    assert len(config_creation_calls) == 1, (
        f"Expected 1 config creation call, got {len(config_creation_calls)}"
    )
    config_call_str = " ".join(str(arg) for arg in config_creation_calls[0])
    assert "python3" in config_call_str
    assert "pathlib" in config_call_str

    # Verify runuser was used instead of direct subprocess exec
    # Filter to find the final launch runuser (not the config creation one)
    launch_runuser_calls = [
        args for args, _ in captured_calls
        if args[0] == "runuser" and "python3" not in args
    ]
    assert len(launch_runuser_calls) > 0, "runuser was not used for launching claude"
    runuser_call = launch_runuser_calls[0]
    assert runuser_call[1:4] == ("-u", "app", "--"), f"Unexpected runuser args: {runuser_call[1:4]}"

    runuser_kwargs = next(
        (kwargs for args, kwargs in captured_calls if args and args[0] == "runuser"),
        None,
    )
    assert runuser_kwargs is not None, "runuser kwargs were not captured"

    env_kwargs = runuser_kwargs.get("env")
    assert isinstance(env_kwargs, dict)
    assert env_kwargs["MY_CUSTOM_VAR"] == "test-value"
    assert env_kwargs["HOME"] == "/home/app"
    assert env_kwargs["USER"] == "app"
    assert env_kwargs["LOGNAME"] == "app"

    # Verify the original command follows the runuser prefix (model/effort added by build_command)
    cmd_start_idx = runuser_call.index("claude")
    actual_cmd = runuser_call[cmd_start_idx:]
    assert actual_cmd[0] == "claude"
    assert "-p" in actual_cmd or "--dangerously-skip-permissions" in actual_cmd


@pytest.mark.asyncio
async def test_launch_privilege_drop_chowns_repo_only_for_external_workspace(tmp_path, monkeypatch):
    captured_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _FakeProcess:
        def __init__(self, pid: int = 8888) -> None:
            self.pid = pid
            self.returncode = 0

        async def wait(self) -> int:
            return 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def _fake_create_subprocess_exec(*args, **kwargs):
        captured_calls.append((args, kwargs))
        return _FakeProcess()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    chown_calls: list[tuple[object, ...]] = []

    async def _fake_run_checked_command(self, *cmd, **kw):
        if cmd[:2] == ("chown", "-R"):
            chown_calls.append(cmd)
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.ManagedRuntimeLauncher._run_checked_command",
        _fake_run_checked_command,
    )

    # Mock Path.exists to simulate the config file not existing initially
    original_exists = Path.exists

    def _mock_exists(self):
        path_str = str(self)
        if path_str == "/home/app/.claude.json":
            return False
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", _mock_exists)

    # Mock Path.is_file to simulate successful creation
    original_is_file = Path.is_file

    def _mock_is_file(self):
        path_str = str(self)
        if path_str == "/home/app/.claude.json":
            return True
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", _mock_is_file)

    monkeypatch.setattr(os, "geteuid", lambda: 0)

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)

    shared_root = tmp_path / "shared-workspaces"
    workspace_root = shared_root / "repo"
    workspace_root.mkdir(parents=True)
    (workspace_root / ".git").mkdir()

    profile = _make_profile(
        runtime_id="claude_code",
        command_template=["claude", "-p", "hello"],
        env_overrides={"MY_CUSTOM_VAR": "test-value"},
        passthrough_env_keys=[],
    )
    request = _make_request()

    await launcher.launch(
        run_id="root-run",
        request=request,
        profile=profile,
        workspace_path=str(workspace_root),
    )

    assert len(chown_calls) == 1
    assert chown_calls[0][-1] == str(workspace_root)
