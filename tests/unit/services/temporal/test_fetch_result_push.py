"""Unit tests for _push_workspace_branch and fetch_result push integration."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from moonmind.config.settings import settings
from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_store(
    *,
    workspace_path: str | None = "/work/agent_jobs/run-1/repo",
    status: str = "completed",
    failure_class: str | None = None,
    agent_id: str = "claude",
    runtime_id: str = "claude_code",
) -> MagicMock:
    """Build a MagicMock run_store that returns a deterministic record."""
    record = MagicMock()
    record.run_id = "run-1"
    record.agent_id = agent_id
    record.runtime_id = runtime_id
    record.status = status
    record.workspace_path = workspace_path
    record.pid = 12345
    record.exit_code = 0
    record.started_at = datetime.now(tz=UTC)
    record.finished_at = datetime.now(tz=UTC)
    record.last_heartbeat_at = None
    record.log_artifact_ref = None
    record.diagnostics_ref = None
    record.error_message = None
    record.failure_class = failure_class
    store = MagicMock()
    store.load.return_value = record
    return store


def _make_subprocess_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


def test_detect_pr_url_uses_workspace_command_shims(tmp_path):
    store = _make_mock_store(workspace_path=str(tmp_path / "run-1" / "repo"))
    activities = TemporalAgentRuntimeActivities(run_store=store)
    workspace = Path(str(store.load.return_value.workspace_path))
    (workspace.parent / ".moonmind" / "bin").mkdir(parents=True)

    calls: list[dict[str, object]] = []

    def _mock_run(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        if len(calls) == 1:
            return _make_subprocess_result(stdout="feature/test-branch\n")
        if len(calls) == 2:
            return _make_subprocess_result(stdout="https://github.com/o/r.git\n")
        return _make_subprocess_result(stdout='[{"url":"https://github.com/o/r/pull/1"}]\n')

    with patch("subprocess.run", side_effect=_mock_run):
        pr_url = activities._detect_pr_url_from_workspace("run-1")

    assert pr_url == "https://github.com/o/r/pull/1"
    assert len(calls) == 3
    gh_call = calls[2]
    gh_env = gh_call["kwargs"]["env"]
    assert isinstance(gh_env, dict)
    assert gh_env["PATH"].startswith(str(workspace.parent / ".moonmind" / "bin"))


# ---------------------------------------------------------------------------
# _push_workspace_branch
# ---------------------------------------------------------------------------


class TestPushWorkspaceBranch:
    """Tests for TemporalAgentRuntimeActivities._push_workspace_branch."""

    @pytest.mark.asyncio
    async def test_push_skipped_no_store(self):
        activities = TemporalAgentRuntimeActivities(run_store=None)
        result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "skipped"
        assert "no run store" in result.get("push_error", "")

    @pytest.mark.asyncio
    async def test_push_skipped_no_workspace(self):
        store = _make_mock_store(workspace_path=None)
        activities = TemporalAgentRuntimeActivities(run_store=store)
        result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "skipped"
        assert "no workspace" in result.get("push_error", "")

    @pytest.mark.asyncio
    async def test_push_skipped_record_not_found(self):
        store = MagicMock()
        store.load.return_value = None
        activities = TemporalAgentRuntimeActivities(run_store=store)
        result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "skipped"

    @pytest.mark.asyncio
    async def test_push_protected_branch_main(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"main\n", b""))
            proc.returncode = 0
            mock_exec.return_value = proc
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "protected_branch"
        assert result["push_branch"] == "main"

    @pytest.mark.asyncio
    async def test_push_protected_branch_master(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"master\n", b""))
            proc.returncode = 0
            mock_exec.return_value = proc
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "protected_branch"
        assert result["push_branch"] == "master"

    @pytest.mark.asyncio
    async def test_push_protected_branch_head(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"HEAD\n", b""))
            proc.returncode = 0
            mock_exec.return_value = proc
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "protected_branch"

    @pytest.mark.asyncio
    async def test_push_recovers_detached_head_to_explicit_head_branch(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0
        captured_checkout_args = None

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count, captured_checkout_args
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"HEAD\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # checkout -B feature branch
                captured_checkout_args = args
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 4:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch(
                "run-1",
                target_branch="main",
                head_branch="feature/recover-detached-head",
            )

        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "feature/recover-detached-head"
        assert captured_checkout_args is not None
        assert captured_checkout_args[-3:] == (
            "checkout",
            "-B",
            "feature/recover-detached-head",
        )

    @pytest.mark.asyncio
    async def test_push_detached_head_recovery_timeout_kills_checkout(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0
        repair_proc = MagicMock()
        repair_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        repair_proc.wait = AsyncMock(return_value=0)
        repair_proc.kill = MagicMock()

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # rev-parse
                proc = MagicMock()
                proc.communicate = AsyncMock(return_value=(b"HEAD\n", b""))
                proc.returncode = 0
                return proc
            if call_count == 2:  # checkout -B feature branch
                return repair_proc
            raise AssertionError(f"Unexpected subprocess call #{call_count}: {args!r}")

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch(
                "run-1",
                target_branch="main",
                head_branch="feature/recover-detached-head",
            )

        assert result["push_status"] == "failed"
        assert result["push_branch"] == "HEAD"
        assert result["push_error"] == "detached HEAD recovery timed out after 30s"
        repair_proc.kill.assert_called_once_with()
        repair_proc.wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_push_protected_target_branch(self):
        """target_branch is included in the protected set."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"develop\n", b""))
            proc.returncode = 0
            mock_exec.return_value = proc
            result = await activities._push_workspace_branch("run-1", target_branch="develop")
        assert result["push_status"] == "protected_branch"
        assert result["push_branch"] == "develop"

    @pytest.mark.asyncio
    async def test_push_success(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"feature/delete-spec-048\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"2\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "feature/delete-spec-048"
        assert "push_error" not in result

    @pytest.mark.asyncio
    async def test_push_marks_workspace_safe_for_git_commands(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        recorded_calls: list[tuple[object, ...]] = []
        workspace = "/work/agent_jobs/run-1/repo"
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            recorded_calls.append(args)
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"feature/safe-branch\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "pushed"
        assert len(recorded_calls) == 4
        for call in recorded_calls:
            command = list(call)
            assert command[:5] == [
                "git",
                "-c",
                f"safe.directory={workspace}",
                "-C",
                workspace,
            ]

    @pytest.mark.asyncio
    async def test_push_failure(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"feature/delete-spec-048\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:  # push
                proc.communicate = AsyncMock(return_value=(b"", b"remote: Permission denied"))
                proc.returncode = 128
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "failed"
        assert result["push_branch"] == "feature/delete-spec-048"
        assert "Permission denied" in result["push_error"]

    @pytest.mark.asyncio
    async def test_push_branch_detection_failure(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b"fatal: not a git repository"))
            proc.returncode = 1
            mock_exec.return_value = proc
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "failed"
        assert "could not determine branch" in result["push_error"]

    @pytest.mark.asyncio
    async def test_push_exception_returns_failed(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("git not found")):
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "failed"
        assert "git not found" in result["push_error"]

    @pytest.mark.asyncio
    async def test_push_no_commits(self):
        """Push succeeds but branch has no commits over origin/main."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"auto-abc123\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"0\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "no_commits"
        assert result["push_branch"] == "auto-abc123"
        assert result["push_base_ref"] == "origin/main"
        assert result["push_commit_count"] == 0
        assert "push_error" not in result

    @pytest.mark.asyncio
    async def test_push_with_commits(self):
        """Push succeeds and branch has commits over origin/main."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"auto-abc123\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"3\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "auto-abc123"

    @pytest.mark.asyncio
    async def test_push_commits_dirty_workspace_before_push(self):
        """Dirty managed workspaces are committed before publish push."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0
        recorded_calls: list[tuple[object, ...]] = []

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            recorded_calls.append(args)
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"auto-dirty123\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(
                    return_value=(b" M api_service/main.py\n?? tests/new_test.py\n", b"")
                )
                proc.returncode = 0
            elif call_count == 3:  # git add -A with runtime exclusions
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 4:  # staged diff check
                proc.communicate = AsyncMock(
                    return_value=(b"api_service/main.py\ntests/new_test.py\n", b"")
                )
                proc.returncode = 0
            elif call_count == 5:  # commit
                proc.communicate = AsyncMock(return_value=(b"[auto-dirty123 abc123] msg\n", b""))
                proc.returncode = 0
            elif call_count == 6:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch(
                "run-1",
                commit_message="Ship dirty workspace",
            )

        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "auto-dirty123"
        assert result["push_commit_count"] == 1
        assert result["push_commit_message"] == "Ship dirty workspace"
        add_call = recorded_calls[2]
        assert list(add_call[-5:]) == [
            "--",
            ".",
            ":(exclude)CLAUDE.md",
            ":(exclude)live_streams.spool",
            ":(exclude).agents/skills/active",
        ]
        commit_call = next(call for call in recorded_calls if "commit" in call)
        assert list(commit_call[-3:]) == ["commit", "-m", "Ship dirty workspace"]

    @pytest.mark.asyncio
    async def test_push_dirty_workspace_commit_failure_returns_failed(self):
        """Commit failures surface as publish failures instead of false no-ops."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"auto-dirty123\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b" M api_service/main.py\n", b""))
                proc.returncode = 0
            elif call_count == 3:  # git add -A
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 4:  # staged diff check
                proc.communicate = AsyncMock(return_value=(b"api_service/main.py\n", b""))
                proc.returncode = 0
            else:  # commit
                proc.communicate = AsyncMock(
                    return_value=(b"", b"Author identity unknown")
                )
                proc.returncode = 128
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "failed"
        assert "could not commit workspace changes" in result["push_error"]
        assert "Author identity unknown" in result["push_error"]

    @pytest.mark.asyncio
    async def test_push_ignores_runtime_scaffolding_only_changes(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"auto-claude123\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"?? CLAUDE.md\n", b""))
                proc.returncode = 0
            elif call_count == 3:  # git add -A with exclusions
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 4:  # staged diff check
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 5:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"0\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "no_commits"
        assert result["push_branch"] == "auto-claude123"
        assert result["push_commit_count"] == 0

    def test_workspace_command_env_includes_support_gitconfig_and_git_identity(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "run-1" / "repo"
        support_root = workspace.parent / ".moonmind"
        support_bin = support_root / "bin"
        support_bin.mkdir(parents=True)
        gitconfig = support_root / "gitconfig"
        gitconfig.write_text("[safe]\n", encoding="utf-8")
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setattr(settings.workflow, "git_user_name", "MoonMind Bot")
        monkeypatch.setattr(
            settings.workflow, "git_user_email", "moonmind@example.com"
        )

        env = TemporalAgentRuntimeActivities._workspace_command_env(str(workspace))

        assert env["PATH"].startswith(str(support_bin))
        assert env["GIT_CONFIG_GLOBAL"] == str(gitconfig)
        assert env["GIT_AUTHOR_NAME"] == "MoonMind Bot"
        assert env["GIT_COMMITTER_NAME"] == "MoonMind Bot"
        assert env["GIT_AUTHOR_EMAIL"] == "moonmind@example.com"
        assert env["GIT_COMMITTER_EMAIL"] == "moonmind@example.com"

    def test_workspace_command_env_overrides_preexisting_git_identity(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "run-1" / "repo"
        support_root = workspace.parent / ".moonmind"
        support_bin = support_root / "bin"
        support_bin.mkdir(parents=True)
        gitconfig = support_root / "gitconfig"
        gitconfig.write_text("[safe]\n", encoding="utf-8")
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/tmp/external.gitconfig")
        monkeypatch.setenv("GIT_AUTHOR_NAME", "External Author")
        monkeypatch.setenv("GIT_COMMITTER_NAME", "External Committer")
        monkeypatch.setenv("GIT_AUTHOR_EMAIL", "external@example.com")
        monkeypatch.setenv("GIT_COMMITTER_EMAIL", "external@example.com")
        monkeypatch.setattr(settings.workflow, "git_user_name", "MoonMind Bot")
        monkeypatch.setattr(
            settings.workflow, "git_user_email", "moonmind@example.com"
        )

        env = TemporalAgentRuntimeActivities._workspace_command_env(str(workspace))

        assert env["GIT_CONFIG_GLOBAL"] == str(gitconfig)
        assert env["GIT_AUTHOR_NAME"] == "MoonMind Bot"
        assert env["GIT_COMMITTER_NAME"] == "MoonMind Bot"
        assert env["GIT_AUTHOR_EMAIL"] == "moonmind@example.com"
        assert env["GIT_COMMITTER_EMAIL"] == "moonmind@example.com"

    def test_workspace_command_env_bootstraps_git_helper_without_writing_token(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "run-1" / "repo"
        support_root = workspace.parent / ".moonmind"
        support_bin = support_root / "bin"
        gitconfig = support_root / "gitconfig"
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token_value")
        monkeypatch.setattr(settings.workflow, "git_user_name", "MoonMind Bot")
        monkeypatch.setattr(
            settings.workflow, "git_user_email", "moonmind@example.com"
        )

        env = TemporalAgentRuntimeActivities._workspace_command_env(str(workspace))

        helper_path = support_bin / "git-credential-moonmind"
        assert support_bin.is_dir()
        assert gitconfig.is_file()
        assert helper_path.is_file()
        assert env["PATH"].startswith(str(support_bin))
        assert env["GIT_CONFIG_GLOBAL"] == str(gitconfig)

        helper_text = helper_path.read_text(encoding="utf-8")
        gitconfig_text = gitconfig.read_text(encoding="utf-8")
        assert "ghp_test_token_value" not in helper_text
        assert "ghp_test_token_value" not in gitconfig_text
        assert "os.environ.get('GITHUB_TOKEN'" in helper_text
        assert "password={token}" in helper_text
        assert "git-credential-moonmind" in gitconfig_text
        assert str(workspace.resolve()) in gitconfig_text

    def test_workspace_command_env_logs_bootstrap_failures(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "run-1" / "repo"
        monkeypatch.setenv("PATH", "/usr/bin")

        with patch(
            "pathlib.Path.mkdir",
            side_effect=OSError("read-only filesystem"),
        ), patch(
            "moonmind.workflows.temporal.activity_runtime.logger.warning"
        ) as warning_mock:
            env = TemporalAgentRuntimeActivities._workspace_command_env(str(workspace))

        assert env["PATH"] == "/usr/bin"
        assert "GIT_CONFIG_GLOBAL" not in env
        warning_mock.assert_called_once()
        assert warning_mock.call_args.args[1] == str(workspace)

    @pytest.mark.asyncio
    async def test_push_revlist_failure_falls_through(self):
        """When rev-list --count raises, we fall through to 'pushed' (safe default)."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"auto-abc123\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:  # rev-list --count — simulate failure
                raise OSError("git rev-list failed")
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "auto-abc123"
        assert "push_commit_count" not in result

    @pytest.mark.asyncio
    async def test_push_revlist_nonzero_returncode_falls_through(self):
        """Non-zero rev-list returncode falls through to 'pushed' instead of false no_commits."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"auto-abc123\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:  # rev-list --count — non-zero exit with empty stdout
                proc.communicate = AsyncMock(return_value=(b"", b"fatal: bad revision"))
                proc.returncode = 128
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")
        # Should NOT be no_commits; should fall through to pushed
        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "auto-abc123"
        assert "push_commit_count" not in result

    @pytest.mark.asyncio
    async def test_push_revlist_uses_target_branch(self):
        """Rev-list command uses target_branch instead of hardcoded origin/main."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0
        captured_revlist_args = None

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count, captured_revlist_args
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"auto-abc123\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:  # rev-list --count
                captured_revlist_args = args
                proc.communicate = AsyncMock(return_value=(b"5\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch(
                "run-1", target_branch="develop",
            )
        assert result["push_status"] == "pushed"
        # Verify the rev-list range uses origin/develop, not origin/main
        assert captured_revlist_args is not None
        revlist_range = [a for a in captured_revlist_args if ".." in str(a)]
        assert len(revlist_range) == 1
        assert "origin/develop" in revlist_range[0]


# ---------------------------------------------------------------------------
# agent_runtime_fetch_result — push integration
# ---------------------------------------------------------------------------


class TestFetchResultPushIntegration:
    """Tests for publish_mode-aware git push inside fetch_result."""

    @pytest.mark.asyncio
    async def test_fetch_result_pushes_when_publish_mode_pr(self):
        """Push attempted when publish_mode=pr and agent succeeded."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)

        with (
            patch.object(
                activities, "_push_workspace_branch",
                new_callable=AsyncMock,
                return_value={"push_status": "pushed", "push_branch": "my-branch"},
            ) as mock_push,
            patch.object(
                activities, "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
        ):
            adapter_instance = MockAdapter.return_value
            adapter_instance.fetch_result = AsyncMock(return_value=AgentRunResult(
                summary="done",
                failure_class=None,
            ))

            result = await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude", "publish_mode": "pr"},
            )

        mock_push.assert_called_once_with("run-1", target_branch=None)
        assert result.metadata["push_status"] == "pushed"
        assert result.metadata["push_branch"] == "my-branch"

    @pytest.mark.asyncio
    async def test_fetch_result_adds_operator_summary_from_stdout_artifact(self, tmp_path):
        store = _make_mock_store()
        store.load.return_value.stdout_artifact_ref = "art-stdout"
        stdout_path = tmp_path / "stdout.log"
        stdout_path.write_text(
            "noise\n**Final Report**\nThe requested behavior already existed in the repo.\n"
            "Files edited in this run: none.\n\ncodex\n",
            encoding="utf-8",
        )
        artifact_service = MagicMock()
        artifact_service.read_path = AsyncMock(
            return_value=(MagicMock(), stdout_path)
        )
        artifact_service.read_chunks = AsyncMock(
            side_effect=AssertionError("stdout tail should come from the local artifact path")
        )
        artifact_service.read_bytes = AsyncMock(
            side_effect=AssertionError("stdout tail should not require full artifact reads")
        )
        activities = TemporalAgentRuntimeActivities(
            run_store=store,
            artifact_service=artifact_service,
        )

        with (
            patch.object(
                activities,
                "_push_workspace_branch",
                new_callable=AsyncMock,
                return_value={
                    "push_status": "no_commits",
                    "push_branch": "feature/no-op",
                    "push_base_ref": "origin/main",
                    "push_commit_count": 0,
                },
            ),
            patch.object(
                activities, "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
        ):
            adapter_instance = MockAdapter.return_value
            adapter_instance.fetch_result = AsyncMock(
                return_value=AgentRunResult(
                    summary="Completed with status completed",
                    failure_class=None,
                )
            )

            result = await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude", "publish_mode": "pr"},
            )

        assert result.metadata["operator_summary"].startswith(
            "The requested behavior already existed in the repo."
        )
        assert "Files edited in this run: none." in result.metadata["operator_summary"]

    @pytest.mark.asyncio
    async def test_fetch_result_skips_push_when_publish_mode_none(self):
        """No push when publish_mode=none."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)

        mock_result = AgentRunResult(
            summary="done",
            failure_class=None,
        )

        with (
            patch.object(
                activities, "_push_workspace_branch",
            ) as mock_push,
            patch.object(
                activities, "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
        ):
            adapter_instance = MockAdapter.return_value
            adapter_instance.fetch_result = AsyncMock(return_value=mock_result)

            await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude", "publish_mode": "none"},
            )

        mock_push.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_result_skips_push_on_failure(self):
        """No push when agent failed (failure_class set)."""
        store = _make_mock_store(failure_class="execution_error")
        activities = TemporalAgentRuntimeActivities(run_store=store)

        mock_result = AgentRunResult(
            summary="failed",
            failure_class="execution_error",
        )

        with (
            patch.object(
                activities, "_push_workspace_branch",
            ) as mock_push,
            patch.object(
                activities, "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
        ):
            adapter_instance = MockAdapter.return_value
            adapter_instance.fetch_result = AsyncMock(return_value=mock_result)

            await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude", "publish_mode": "pr"},
            )

        mock_push.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_result_reports_push_failure_in_metadata(self):
        """Push failure details propagated to result metadata."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)

        with (
            patch.object(
                activities, "_push_workspace_branch",
                new_callable=AsyncMock,
                return_value={
                    "push_status": "failed",
                    "push_branch": "my-branch",
                    "push_error": "remote: Permission denied",
                },
            ),
            patch.object(
                activities, "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
        ):
            adapter_instance = MockAdapter.return_value
            adapter_instance.fetch_result = AsyncMock(return_value=AgentRunResult(
                summary="done",
                failure_class=None,
            ))

            result = await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude", "publish_mode": "pr"},
            )

        assert result.metadata["push_status"] == "failed"
        assert result.metadata["push_error"] == "remote: Permission denied"

    @pytest.mark.asyncio
    async def test_fetch_result_passes_commit_message_override_to_push(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)

        with (
            patch.object(
                activities,
                "_push_workspace_branch",
                new_callable=AsyncMock,
                return_value={"push_status": "pushed", "push_branch": "my-branch"},
            ) as mock_push,
            patch.object(
                activities, "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
        ):
            adapter_instance = MockAdapter.return_value
            adapter_instance.fetch_result = AsyncMock(return_value=AgentRunResult(
                summary="done",
                failure_class=None,
            ))

            await activities.agent_runtime_fetch_result(
                {
                    "run_id": "run-1",
                    "agent_id": "claude",
                    "publish_mode": "pr",
                    "commit_message": "Use explicit publish commit",
                },
            )

        mock_push.assert_called_once_with(
            "run-1",
            target_branch=None,
            commit_message="Use explicit publish commit",
        )

    @pytest.mark.asyncio
    async def test_fetch_result_defaults_publish_mode_none(self):
        """When publish_mode not provided, defaults to 'none' (no push)."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)

        mock_result = AgentRunResult(
            summary="done",
            failure_class=None,
        )

        with (
            patch.object(
                activities, "_push_workspace_branch",
            ) as mock_push,
            patch.object(
                activities, "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
        ):
            adapter_instance = MockAdapter.return_value
            adapter_instance.fetch_result = AsyncMock(return_value=mock_result)

            # No publish_mode key at all
            await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude"},
            )

        mock_push.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_result_cleans_deferred_support_after_publish(self):
        """Deferred auth helpers are cleaned only after fetch_result finishes."""
        store = _make_mock_store()
        launcher = MagicMock()
        launcher.cleanup_run_support = AsyncMock()
        supervisor = MagicMock()
        activities = TemporalAgentRuntimeActivities(
            run_store=store,
            run_launcher=launcher,
            run_supervisor=supervisor,
        )

        mock_result = AgentRunResult(
            summary="done",
            failure_class=None,
        )

        with (
            patch.object(
                activities, "_push_workspace_branch",
                new_callable=AsyncMock,
                return_value={"push_status": "pushed", "push_branch": "my-branch"},
            ),
            patch.object(
                activities, "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch.object(
                launcher,
                "cleanup_run_support",
                new_callable=AsyncMock,
            ) as mock_cleanup_support,
            patch.object(
                supervisor,
                "cleanup_deferred_run_files",
            ) as mock_cleanup_deferred,
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
        ):
            adapter_instance = MockAdapter.return_value
            adapter_instance.fetch_result = AsyncMock(return_value=mock_result)

            await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude", "publish_mode": "pr"},
            )

        mock_cleanup_support.assert_awaited_once_with("run-1")
        mock_cleanup_deferred.assert_called_once_with("run-1")

    @pytest.mark.asyncio
    async def test_fetch_result_cleans_up_after_push_completes(self):
        store = _make_mock_store()
        launcher = MagicMock()
        launcher.cleanup_run_support = AsyncMock()
        supervisor = MagicMock()
        activities = TemporalAgentRuntimeActivities(
            run_store=store,
            run_launcher=launcher,
            run_supervisor=supervisor,
        )

        mock_result = AgentRunResult(
            summary="done",
            failure_class=None,
        )

        async def _push_side_effect(*_args, **_kwargs):
            launcher.cleanup_run_support.assert_not_awaited()
            supervisor.cleanup_deferred_run_files.assert_not_called()
            return {"push_status": "pushed", "push_branch": "my-branch"}

        with (
            patch.object(
                activities,
                "_push_workspace_branch",
                new_callable=AsyncMock,
                side_effect=_push_side_effect,
            ),
            patch.object(
                activities,
                "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
        ):
            adapter_instance = MockAdapter.return_value
            adapter_instance.fetch_result = AsyncMock(return_value=mock_result)

            await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude", "publish_mode": "pr"},
            )

        launcher.cleanup_run_support.assert_awaited_once_with("run-1")
        supervisor.cleanup_deferred_run_files.assert_called_once_with("run-1")

    @pytest.mark.asyncio
    async def test_fetch_result_cleanup_failures_do_not_override_result(self):
        store = _make_mock_store()
        launcher = MagicMock()
        launcher.cleanup_run_support = AsyncMock(side_effect=RuntimeError("cleanup failed"))
        supervisor = MagicMock()
        supervisor.cleanup_deferred_run_files = MagicMock(side_effect=RuntimeError("deferred failed"))
        activities = TemporalAgentRuntimeActivities(
            run_store=store,
            run_launcher=launcher,
            run_supervisor=supervisor,
        )

        mock_result = AgentRunResult(
            summary="done",
            failure_class=None,
        )

        with (
            patch.object(
                activities, "_push_workspace_branch",
                new_callable=AsyncMock,
                return_value={"push_status": "pushed", "push_branch": "my-branch"},
            ),
            patch.object(
                activities,
                "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
        ):
            adapter_instance = MockAdapter.return_value
            adapter_instance.fetch_result = AsyncMock(return_value=mock_result)

            result = await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude", "publish_mode": "pr"},
            )

        assert result.summary == "done"
        assert result.metadata["push_status"] == "pushed"
        launcher.cleanup_run_support.assert_awaited_once_with("run-1")
        supervisor.cleanup_deferred_run_files.assert_called_once_with("run-1")

    @pytest.mark.asyncio
    async def test_fetch_result_cleanup_runs_when_adapter_fetch_fails(self):
        store = _make_mock_store()
        launcher = MagicMock()
        launcher.cleanup_run_support = AsyncMock()
        supervisor = MagicMock()
        activities = TemporalAgentRuntimeActivities(
            run_store=store,
            run_launcher=launcher,
            run_supervisor=supervisor,
        )

        with patch(
            "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        ) as MockAdapter:
            adapter_instance = MockAdapter.return_value
            adapter_instance.fetch_result = AsyncMock(side_effect=RuntimeError("fetch failed"))

            with pytest.raises(RuntimeError, match="fetch failed"):
                await activities.agent_runtime_fetch_result(
                    {"run_id": "run-1", "agent_id": "claude", "publish_mode": "pr"},
                )

        launcher.cleanup_run_support.assert_awaited_once_with("run-1")
        supervisor.cleanup_deferred_run_files.assert_called_once_with("run-1")
