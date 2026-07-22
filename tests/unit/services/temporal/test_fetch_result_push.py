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
from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
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

def test_detect_pr_url_uses_workspace_command_shims_and_resolved_token(tmp_path):
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
        pr_url = activities._detect_pr_url_from_workspace(
            "run-1",
            github_token="resolved-token",
        )

    assert pr_url == "https://github.com/o/r/pull/1"
    assert len(calls) == 3
    gh_call = calls[2]
    gh_env = gh_call["kwargs"]["env"]
    assert isinstance(gh_env, dict)
    assert gh_env["PATH"].startswith(str(workspace.parent / ".moonmind" / "bin"))
    assert gh_env["GITHUB_TOKEN"] == "resolved-token"
    assert gh_env["GH_TOKEN"] == "resolved-token"

def test_parse_git_status_paths_handles_nul_delimited_non_ascii_and_renames() -> None:
    status_output = (
        b'M  "quoted-looking.txt"\0'
        b'?? na\xc3\xafve.txt\0'
        b"R  renamed path.txt\0old path.txt\0"
    )

    paths = TemporalAgentRuntimeActivities._parse_git_status_paths(status_output)

    assert paths == (
        '"quoted-looking.txt"',
        "naïve.txt",
        "renamed path.txt",
        "old path.txt",
    )

def test_parse_git_status_paths_rejects_truncated_rename_record() -> None:
    with pytest.raises(ValueError, match="missing original path"):
        TemporalAgentRuntimeActivities._parse_git_status_paths(b"R  renamed path.txt\0")

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

    def test_normalize_workspace_git_alternates_rewrites_missing_tmp_path_to_relative_sibling(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "mm:run-1" / "repo"
        alternates_path = workspace / ".git" / "objects" / "info" / "alternates"
        local_alternate = workspace / ".git" / "objects_app"
        alternates_path.parent.mkdir(parents=True)
        local_alternate.mkdir(parents=True)
        alternates_path.write_text(
            "/tmp/tacticsrepo/.git/objects_app\n",
            encoding="utf-8",
        )

        TemporalAgentRuntimeActivities._normalize_workspace_git_alternates(
            str(workspace)
        )

        assert alternates_path.read_text(encoding="utf-8") == "../objects_app\n"

    def test_normalize_workspace_git_alternates_rewrites_workspace_absolute_path_with_colon(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "mm:run-1" / "repo"
        alternates_path = workspace / ".git" / "objects" / "info" / "alternates"
        local_alternate = workspace / ".git" / "objects_app"
        alternates_path.parent.mkdir(parents=True)
        local_alternate.mkdir(parents=True)
        alternates_path.write_text(f"{local_alternate}\n", encoding="utf-8")

        TemporalAgentRuntimeActivities._normalize_workspace_git_alternates(
            str(workspace)
        )

        assert alternates_path.read_text(encoding="utf-8") == "../objects_app\n"

    def test_normalize_workspace_git_alternates_removes_only_missing_entries(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "run-1" / "repo"
        alternates_path = workspace / ".git" / "objects" / "info" / "alternates"
        alternates_path.parent.mkdir(parents=True)
        alternates_path.write_text(
            "/tmp/missing-object-store\n",
            encoding="utf-8",
        )

        TemporalAgentRuntimeActivities._normalize_workspace_git_alternates(
            str(workspace)
        )

        assert not alternates_path.exists()

    def test_normalize_workspace_git_alternates_skips_self_objects_directory(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "run-1" / "repo"
        objects_dir = workspace / ".git" / "objects"
        alternates_path = objects_dir / "info" / "alternates"
        alternates_path.parent.mkdir(parents=True)
        alternates_path.write_text(f"{objects_dir}\n.\n", encoding="utf-8")

        TemporalAgentRuntimeActivities._normalize_workspace_git_alternates(
            str(workspace)
        )

        assert not alternates_path.exists()

    def test_normalize_workspace_git_alternates_deduplicates_entries(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "run-1" / "repo"
        alternates_path = workspace / ".git" / "objects" / "info" / "alternates"
        local_alternate = workspace / ".git" / "objects_app"
        alternates_path.parent.mkdir(parents=True)
        local_alternate.mkdir(parents=True)
        alternates_path.write_text(
            "../objects_app\n../objects_app\n",
            encoding="utf-8",
        )

        TemporalAgentRuntimeActivities._normalize_workspace_git_alternates(
            str(workspace)
        )

        assert alternates_path.read_text(encoding="utf-8") == "../objects_app\n"

    def test_recover_orphan_object_stores_registers_sibling_with_loose_objects(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "mm:run-1" / "repo"
        (workspace / ".git" / "objects" / "info").mkdir(parents=True)
        sibling = tmp_path / "mm:run-1" / "git-objects"
        (sibling / "6d").mkdir(parents=True)
        # 38-char hex blob filename mimics the real loose-object layout.
        (sibling / "6d" / "93a9ea32d2cf97dca48c5bf139829fc28516c5").write_bytes(b"x")

        TemporalAgentRuntimeActivities._recover_orphan_workspace_object_stores(
            str(workspace)
        )

        alternates_path = workspace / ".git" / "objects" / "info" / "alternates"
        assert alternates_path.is_file()
        contents = alternates_path.read_text(encoding="utf-8").splitlines()
        # Path must be relative so that ':' in run ids does not break parsing.
        assert contents == ["../../../git-objects"]

    def test_recover_orphan_object_stores_accepts_sha256_loose_objects(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "mm:run-1" / "repo"
        (workspace / ".git" / "objects" / "info").mkdir(parents=True)
        sibling = tmp_path / "mm:run-1" / "git-objects-sha256"
        (sibling / "6d").mkdir(parents=True)
        (sibling / "6d" / ("9" * 62)).write_bytes(b"x")

        TemporalAgentRuntimeActivities._recover_orphan_workspace_object_stores(
            str(workspace)
        )

        alternates_path = workspace / ".git" / "objects" / "info" / "alternates"
        assert alternates_path.is_file()
        assert (
            alternates_path.read_text(encoding="utf-8").splitlines()
            == ["../../../git-objects-sha256"]
        )

    def test_recover_orphan_object_stores_appends_without_duplicating(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "mm:run-1" / "repo"
        objects_info = workspace / ".git" / "objects" / "info"
        objects_info.mkdir(parents=True)
        alternates_path = objects_info / "alternates"
        alternates_path.write_text("../../../git-objects\n", encoding="utf-8")
        sibling = tmp_path / "mm:run-1" / "git-objects"
        (sibling / "6d").mkdir(parents=True)
        (sibling / "6d" / "93a9ea32d2cf97dca48c5bf139829fc28516c5").write_bytes(b"x")

        TemporalAgentRuntimeActivities._recover_orphan_workspace_object_stores(
            str(workspace)
        )

        assert (
            alternates_path.read_text(encoding="utf-8").splitlines()
            == ["../../../git-objects"]
        )

    def test_recover_orphan_object_stores_ignores_non_object_siblings(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "mm:run-1" / "repo"
        (workspace / ".git" / "objects" / "info").mkdir(parents=True)
        # A sibling that exists but is not a git object store.
        (tmp_path / "mm:run-1" / "artifacts").mkdir()
        (tmp_path / "mm:run-1" / "artifacts" / "notes.txt").write_text("hi")

        TemporalAgentRuntimeActivities._recover_orphan_workspace_object_stores(
            str(workspace)
        )

        alternates_path = workspace / ".git" / "objects" / "info" / "alternates"
        assert not alternates_path.exists()

    def test_recover_orphan_object_stores_accepts_pack_only_sibling(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "mm:run-1" / "repo"
        (workspace / ".git" / "objects" / "info").mkdir(parents=True)
        sibling = tmp_path / "mm:run-1" / "shared-objects"
        (sibling / "pack").mkdir(parents=True)
        (sibling / "pack" / "pack-abc.pack").write_bytes(b"\x00")
        (sibling / "pack" / "pack-abc.idx").write_bytes(b"\x00")

        TemporalAgentRuntimeActivities._recover_orphan_workspace_object_stores(
            str(workspace)
        )

        alternates_path = workspace / ".git" / "objects" / "info" / "alternates"
        assert alternates_path.is_file()
        assert (
            alternates_path.read_text(encoding="utf-8").splitlines()
            == ["../../../shared-objects"]
        )

    def test_recover_orphan_object_stores_registers_git_agent_objects(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "mm:run-1" / "repo"
        (workspace / ".git" / "objects" / "info").mkdir(parents=True)
        agent_objects = workspace / ".git" / "agent-objects"
        (agent_objects / "a5").mkdir(parents=True)
        (agent_objects / "a5" / "894cc4848aec211f7257dad36030019d13596f").write_bytes(
            b"x"
        )

        TemporalAgentRuntimeActivities._recover_orphan_workspace_object_stores(
            str(workspace)
        )

        alternates_path = workspace / ".git" / "objects" / "info" / "alternates"
        assert alternates_path.is_file()
        assert (
            alternates_path.read_text(encoding="utf-8").splitlines()
            == ["../agent-objects"]
        )

    @pytest.mark.asyncio
    async def test_push_recovers_orphan_object_store_before_branch_detection(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "mm:run-1" / "repo"
        (workspace / ".git" / "objects" / "info").mkdir(parents=True)
        sibling = tmp_path / "mm:run-1" / "git-objects"
        (sibling / "6d").mkdir(parents=True)
        (sibling / "6d" / "93a9ea32d2cf97dca48c5bf139829fc28516c5").write_bytes(b"x")
        alternates_path = workspace / ".git" / "objects" / "info" / "alternates"
        store = _make_mock_store(workspace_path=str(workspace))
        activities = TemporalAgentRuntimeActivities(run_store=store)

        async def _mock_exec(*args, **kwargs):
            # The orphan-object recovery must have already written alternates
            # by the time any git subprocess is invoked.
            assert alternates_path.is_file()
            contents = alternates_path.read_text(encoding="utf-8").splitlines()
            assert contents == ["../../../git-objects"]
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"main\n", b""))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "protected_branch"

    @pytest.mark.asyncio
    async def test_push_normalizes_git_alternates_before_branch_detection(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "mm:run-1" / "repo"
        alternates_path = workspace / ".git" / "objects" / "info" / "alternates"
        local_alternate = workspace / ".git" / "objects_app"
        alternates_path.parent.mkdir(parents=True)
        local_alternate.mkdir(parents=True)
        alternates_path.write_text(
            "/tmp/tacticsrepo/.git/objects_app\n",
            encoding="utf-8",
        )
        store = _make_mock_store(workspace_path=str(workspace))
        activities = TemporalAgentRuntimeActivities(run_store=store)

        async def _mock_exec(*args, **kwargs):
            assert alternates_path.read_text(encoding="utf-8") == "../objects_app\n"
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"main\n", b""))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "protected_branch"

    @pytest.mark.asyncio
    async def test_commit_fetches_current_branch_after_missing_head_object(self):
        activities = TemporalAgentRuntimeActivities(run_store=None)
        workspace = "/work/agent_jobs/mm:run-1/repo"
        calls: list[tuple[object, ...]] = []
        envs: list[dict[str, str]] = []
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            calls.append(args)
            envs.append(dict(kwargs.get("env") or {}))
            proc = AsyncMock()
            if call_count == 1:  # initial status --porcelain
                proc.communicate = AsyncMock(
                    return_value=(b"", b"fatal: bad object HEAD")
                )
                proc.returncode = 128
            elif call_count == 2:  # fetch current branch to recover the object
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # verify HEAD is now present
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 4:  # retry status after repair
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:
                raise AssertionError(f"Unexpected subprocess call #{call_count}: {args!r}")
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._commit_workspace_changes_if_needed(
                workspace,
                run_id="mm:run-1",
                env={"GIT_CONFIG_GLOBAL": "workspace-gitconfig"},
                auth_env={
                    "GIT_CONFIG_GLOBAL": "workspace-gitconfig",
                    "GITHUB_TOKEN": "fake-token",
                },
                head_branch="feature/missing-head-object",
            )

        assert result == {}
        assert list(calls[0][-4:]) == [
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ]
        assert list(calls[1][-3:]) == [
            "fetch",
            "origin",
            "refs/heads/feature/missing-head-object",
        ]
        assert envs[1]["GITHUB_TOKEN"] == "fake-token"
        assert list(calls[2][-3:]) == ["cat-file", "-e", "HEAD^{commit}"]
        assert list(calls[3][-4:]) == [
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ]

    @pytest.mark.asyncio
    async def test_commit_repairs_git_ownership_and_runs_as_managed_agent(self):
        activities = TemporalAgentRuntimeActivities(run_store=None)
        workspace = "/work/agent_jobs/mm:run-1/repo"
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0

        with (
            patch.object(
                activity_runtime_module,
                "_normalize_managed_git_ownership",
            ) as normalize_ownership,
            patch.object(
                activity_runtime_module,
                "_managed_agent_subprocess_kwargs",
                return_value={"user": 1000, "group": 1000},
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=proc,
            ) as mock_exec,
        ):
            result = await activities._commit_workspace_changes_if_needed(
                workspace,
                run_id="mm:run-1",
            )

        assert result == {}
        normalize_ownership.assert_called_once_with(workspace)
        assert mock_exec.await_args.kwargs["user"] == 1000
        assert mock_exec.await_args.kwargs["group"] == 1000

    def test_git_ownership_repair_includes_files_without_following_symlinks(self):
        git_stat = MagicMock(st_mode=0o040000)

        with (
            patch.object(
                activity_runtime_module,
                "_managed_agent_subprocess_kwargs",
                return_value={"user": 1000, "group": 1000},
            ),
            patch.object(activity_runtime_module.os, "lstat", return_value=git_stat),
            patch.object(activity_runtime_module.os, "O_DIRECTORY", 0x10000, create=True),
            patch.object(activity_runtime_module.os, "O_NOFOLLOW", 0x20000, create=True),
            patch.object(activity_runtime_module.os, "open", return_value=99),
            patch.object(activity_runtime_module.os, "fchown", create=True) as fchown,
            patch.object(
                activity_runtime_module.os,
                "fwalk",
                return_value=[
                    (".", ["objects"], ["COMMIT_EDITMSG"], 101),
                    ("logs", [], ["HEAD"], 102),
                ],
                create=True,
            ),
            patch.object(activity_runtime_module.os, "chown", create=True) as chown,
            patch.object(activity_runtime_module.os, "close") as close,
        ):
            activity_runtime_module._normalize_managed_git_ownership(
                "/work/agent_jobs/mm:run-1/repo"
            )

        fchown.assert_called_once_with(99, 1000, 1000)
        assert [ownership_call.args[0] for ownership_call in chown.call_args_list] == [
            "objects",
            "COMMIT_EDITMSG",
            "HEAD",
        ]
        assert [ownership_call.kwargs["dir_fd"] for ownership_call in chown.call_args_list] == [
            101,
            101,
            102,
        ]
        assert all(
            ownership_call.kwargs["follow_symlinks"] is False
            for ownership_call in chown.call_args_list
        )
        close.assert_called_once_with(99)

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
    @pytest.mark.parametrize("starting_branch", ["main", "feature/live-task"])
    async def test_push_recovers_workspace_to_requested_pr_head_branch(
        self, starting_branch: str
    ):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        calls: list[tuple[object, ...]] = []
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            del kwargs
            call_count += 1
            calls.append(args)
            proc = AsyncMock()
            if call_count == 1:  # rev-parse --abbrev-ref HEAD
                proc.communicate = AsyncMock(
                    return_value=(f"{starting_branch}\n".encode(), b"")
                )
                proc.returncode = 0
            elif call_count == 2:  # checkout -B requested head branch
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 4:  # remote branch sha before push
                proc.communicate = AsyncMock(return_value=(b"remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 5:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 6:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"head-sha\n", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch(
                "run-1",
                target_branch="main",
                head_branch="feature/recovered-publish",
            )

        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "feature/recovered-publish"
        assert result["push_base_branch"] == "main"
        assert result["push_head_sha"] == "head-sha"
        assert any(
            call[-3:] == ("checkout", "-B", "feature/recovered-publish")
            for call in calls
        )

    @pytest.mark.asyncio
    async def test_push_redacts_checkout_failure_for_requested_pr_head_branch(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)

        async def _mock_exec(*args, **kwargs):
            del args, kwargs
            proc = AsyncMock()
            if _mock_exec.call_count == 0:
                proc.communicate = AsyncMock(return_value=(b"main\n", b""))
                proc.returncode = 0
            else:
                proc.communicate = AsyncMock(
                    return_value=(
                        b"",
                        b"fatal: token=ghp_checkoutfailure1234567890abc leaked",
                    )
                )
                proc.returncode = 1
            _mock_exec.call_count += 1
            return proc

        _mock_exec.call_count = 0

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch(
                "run-1",
                target_branch="main",
                head_branch="feature/recovered-publish",
            )

        assert result["push_status"] == "failed"
        assert "ghp_checkoutfailure1234567890abc" not in result["push_error"]
        assert "[REDACTED]" in result["push_error"]

    @pytest.mark.asyncio
    async def test_push_kills_checkout_process_on_timeout_for_requested_pr_head_branch(
        self,
    ):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        checkout_proc = AsyncMock()
        checkout_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        checkout_proc.kill = MagicMock()
        checkout_proc.wait = AsyncMock(return_value=0)

        async def _mock_exec(*args, **kwargs):
            del args, kwargs
            if _mock_exec.call_count == 0:
                proc = AsyncMock()
                proc.communicate = AsyncMock(return_value=(b"main\n", b""))
                proc.returncode = 0
            else:
                proc = checkout_proc
            _mock_exec.call_count += 1
            return proc

        _mock_exec.call_count = 0

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch(
                "run-1",
                target_branch="main",
                head_branch="feature/recovered-publish",
            )

        assert result["push_status"] == "failed"
        checkout_proc.kill.assert_called_once()
        checkout_proc.wait.assert_awaited_once()

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
    async def test_push_refuses_detached_head_even_with_explicit_head_branch(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)

        async def _mock_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"HEAD\n", b""))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec) as mock_exec:
            result = await activities._push_workspace_branch(
                "run-1",
                target_branch="main",
                head_branch="feature/recover-detached-head",
            )

        assert result["push_status"] == "protected_branch"
        assert result["push_branch"] == "HEAD"
        assert mock_exec.call_count == 1

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
    async def test_push_allows_target_branch_for_branch_publish(self):
        """branch publish may update an existing non-hard-protected branch in place."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"develop\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # pre-push rev-list --count
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            elif call_count == 4:  # remote branch sha before push
                proc.communicate = AsyncMock(return_value=(b"develop-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 5:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 6:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"abc123head\n", b""))
                proc.returncode = 0
            else:
                raise AssertionError(f"Unexpected subprocess call #{call_count}: {args!r}")
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch(
                "run-1",
                target_branch="develop",
                allow_target_branch_push=True,
            )

        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "develop"
        assert result["push_base_ref"] == "origin/develop"
        assert result["push_commit_count"] == 1
        assert result["push_head_sha"] == "abc123head"

    @pytest.mark.asyncio
    async def test_push_keeps_target_branch_protected_when_head_differs(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"release\n", b""))
            proc.returncode = 0
            mock_exec.return_value = proc
            result = await activities._push_workspace_branch(
                "run-1",
                target_branch="release",
                head_branch="feature/work",
                allow_target_branch_push=True,
            )

        assert result["push_status"] == "protected_branch"
        assert result["push_branch"] == "release"

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
            elif call_count == 3:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/trunk\n", b""))
                proc.returncode = 0
            elif call_count == 4:  # remote branch sha before push
                proc.communicate = AsyncMock(return_value=(b"feature-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 5:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 6:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"pushed-head-sha\n", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"2\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "feature/delete-spec-048"
        assert result["push_base_branch"] == "trunk"
        assert result["push_base_ref"] == "origin/trunk"
        assert result["push_head_sha"] == "pushed-head-sha"
        assert "push_error" not in result

    @pytest.mark.asyncio
    async def test_push_blocks_high_security_scan_before_git_push(self, monkeypatch):
        monkeypatch.setattr(settings.security, "high_security_mode", True)
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        recorded_calls: list[tuple[object, ...]] = []
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            del kwargs
            call_count += 1
            recorded_calls.append(args)
            command = list(args)
            assert "push" not in command
            proc = AsyncMock()
            if call_count == 1:  # rev-parse --abbrev-ref HEAD
                proc.communicate = AsyncMock(return_value=(b"feature/scan-block\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif call_count == 4:  # remote branch sha before scan/push
                proc.communicate = AsyncMock(return_value=(b"remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 5:  # fetch remote branch object before scan
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 6:  # commit metadata
                proc.communicate = AsyncMock(
                    return_value=(
                        b"commit local-sha\nsubject MM-813 scanned push\n",
                        b"",
                    )
                )
                proc.returncode = 0
            elif call_count == 7:  # changed file list
                proc.communicate = AsyncMock(return_value=(b"app/config.py\n", b""))
                proc.returncode = 0
            elif call_count == 8:  # per-file changed content
                proc.communicate = AsyncMock(
                    return_value=(b"+password=do-not-print-this-value\n", b"")
                )
                proc.returncode = 0
            else:
                raise AssertionError(f"Unexpected subprocess call #{call_count}: {args!r}")
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "blocked"
        assert result["diagnostic_kind"] == "outbound_scan_blocked"
        assert "git.push.diff:app/config.py" in result["push_error"]
        assert "do-not-print-this-value" not in result["push_error"]
        assert not any("push" in call for call in recorded_calls)

    @pytest.mark.asyncio
    async def test_push_allows_clean_high_security_scan(self, monkeypatch):
        monkeypatch.setattr(settings.security, "high_security_mode", True)
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        recorded_calls: list[tuple[object, ...]] = []
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            del kwargs
            call_count += 1
            recorded_calls.append(args)
            proc = AsyncMock()
            if call_count == 1:  # rev-parse --abbrev-ref HEAD
                proc.communicate = AsyncMock(return_value=(b"feature/scan-allow\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif call_count == 4:  # remote branch sha before scan/push
                proc.communicate = AsyncMock(return_value=(b"remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 5:  # fetch remote branch object before scan
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 6:  # commit metadata
                proc.communicate = AsyncMock(
                    return_value=(
                        b"commit local-sha\nsubject MM-813 clean scanned push\n",
                        b"",
                    )
                )
                proc.returncode = 0
            elif call_count == 7:  # changed file list
                proc.communicate = AsyncMock(return_value=(b"app/service.py\n", b""))
                proc.returncode = 0
            elif call_count == 8:  # per-file changed content
                proc.communicate = AsyncMock(
                    return_value=(b"+return 'ordinary value'\n", b"")
                )
                proc.returncode = 0
            elif call_count == 9:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 10:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"pushed-head-sha\n", b""))
                proc.returncode = 0
            elif call_count == 11:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            else:
                raise AssertionError(f"Unexpected subprocess call #{call_count}: {args!r}")
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "feature/scan-allow"
        assert result["push_head_sha"] == "pushed-head-sha"
        assert any("push" in call for call in recorded_calls)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("raw_ref", [b"origin/\n", b"origin/HEAD\n", b"HEAD\n"])
    async def test_resolve_workspace_default_branch_rejects_non_branch_refs(
        self,
        raw_ref: bytes,
    ):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)

        async def _mock_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(raw_ref, b""))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._resolve_workspace_default_branch(
                workspace="/work/agent_jobs/run-1/repo",
                run_id="run-1",
                env={},
            )

        assert result == "main"

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
            elif call_count == 3:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif call_count == 4:  # remote branch sha before push
                proc.communicate = AsyncMock(return_value=(b"safe-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 5:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 6:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"safe-head-sha\n", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "pushed"
        assert result["push_head_sha"] == "safe-head-sha"
        assert len(recorded_calls) == 7
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
    async def test_push_uses_ls_remote_when_tracking_ref_is_missing(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        recorded_calls: list[tuple[object, ...]] = []
        call_count = 0

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            recorded_calls.append(args)
            proc = AsyncMock()
            if call_count == 1:  # rev-parse --abbrev-ref HEAD
                proc.communicate = AsyncMock(return_value=(b"feature/no-tracking\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif call_count == 4:  # missing local tracking ref
                proc.communicate = AsyncMock(
                    return_value=(b"", b"fatal: Needed a single revision")
                )
                proc.returncode = 128
            elif call_count == 5:  # remote branch exists
                proc.communicate = AsyncMock(
                    return_value=(
                        b"remote-branch-sha\trefs/heads/feature/no-tracking\n",
                        b"",
                    )
                )
                proc.returncode = 0
            elif call_count == 6:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 7:  # final HEAD
                proc.communicate = AsyncMock(return_value=(b"local-head-sha\n", b""))
                proc.returncode = 0
            elif call_count == 8:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            else:
                raise AssertionError(f"Unexpected subprocess call #{call_count}: {args!r}")
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "feature/no-tracking"
        assert any(
            list(call[-3:]) == ["ls-remote", "origin", "refs/heads/feature/no-tracking"]
            for call in recorded_calls
        )
        assert any(
            "--force-with-lease=refs/heads/feature/no-tracking:remote-branch-sha"
            in call
            for call in recorded_calls
            if "push" in call
        )

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
    async def test_push_rebases_and_retries_once_after_lease_conflict(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0
        recorded_calls: list[tuple[object, ...]] = []

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            recorded_calls.append(args)
            proc = AsyncMock()
            if call_count == 1:  # rev-parse --abbrev-ref HEAD
                proc.communicate = AsyncMock(return_value=(b"feature/retry-push\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif call_count == 4:  # remote branch sha before first push
                proc.communicate = AsyncMock(return_value=(b"old-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 5:  # first push loses its lease
                proc.communicate = AsyncMock(
                    return_value=(
                        b"",
                        b"! [rejected] feature/retry-push -> feature/retry-push (stale info)",
                    )
                )
                proc.returncode = 1
            elif call_count == 6:  # fetch updated remote branch
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 7:  # local HEAD after fetch
                proc.communicate = AsyncMock(return_value=(b"local-head-before-rebase\n", b""))
                proc.returncode = 0
            elif call_count == 8:  # updated remote tracking sha
                proc.communicate = AsyncMock(return_value=(b"new-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 9:  # rebase onto updated remote branch
                proc.communicate = AsyncMock(return_value=(b"Successfully rebased\n", b""))
                proc.returncode = 0
            elif call_count == 10:  # retry push with updated lease
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 11:  # final HEAD
                proc.communicate = AsyncMock(return_value=(b"rebased-head-sha\n", b""))
                proc.returncode = 0
            elif call_count == 12:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"2\n", b""))
                proc.returncode = 0
            else:
                raise AssertionError(f"Unexpected subprocess call #{call_count}: {args!r}")
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "feature/retry-push"
        assert result["push_retry_count"] == 1
        assert result["fetch_status"] == "fetched"
        assert result["rebase_status"] == "rebased"
        assert result["push_commit_count"] == 2
        assert result["push_head_sha"] == "rebased-head-sha"

        push_calls = [call for call in recorded_calls if "push" in call]
        assert len(push_calls) == 2
        assert any(
            "--force-with-lease=refs/heads/feature/retry-push:old-remote-sha" in call
            for call in push_calls
        )
        assert any(
            "--force-with-lease=refs/heads/feature/retry-push:new-remote-sha" in call
            for call in push_calls
        )
        assert any(
            list(call[-3:]) == [
                "fetch",
                "origin",
                "+refs/heads/feature/retry-push:refs/remotes/origin/feature/retry-push",
            ]
            for call in recorded_calls
        )
        assert any(
            list(call[-2:]) == ["rebase", "refs/remotes/origin/feature/retry-push"]
            for call in recorded_calls
        )

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
            elif call_count == 3:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif call_count == 4:  # remote branch sha before push
                proc.communicate = AsyncMock(return_value=(b"auto-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 5:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 6:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"no-commit-head-sha\n", b""))
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
        assert result["push_head_sha"] == "no-commit-head-sha"
        assert "push_error" not in result

    @pytest.mark.asyncio
    async def test_resolve_workspace_head_sha_timeout_kills_process(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        proc = MagicMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await activities._resolve_workspace_head_sha(
                workspace="/work/agent_jobs/run-1/repo",
                run_id="run-1",
                env={},
            )

        assert result is None
        proc.kill.assert_called_once_with()
        proc.wait.assert_awaited_once()

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
                    return_value=(b" M api_service/main.py\0?? tests/new_test.py\0", b"")
                )
                proc.returncode = 0
            elif call_count == 3:  # git add -u for tracked changes
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 4:  # git add -A for untracked changes
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 5:  # staged diff check
                proc.communicate = AsyncMock(
                    return_value=(b"api_service/main.py\ntests/new_test.py\n", b"")
                )
                proc.returncode = 0
            elif call_count == 6:  # commit
                proc.communicate = AsyncMock(return_value=(b"[auto-dirty123 abc123] msg\n", b""))
                proc.returncode = 0
            elif call_count == 7:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif call_count == 8:  # remote branch sha before push
                proc.communicate = AsyncMock(return_value=(b"dirty-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 9:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 10:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"dirty-head-sha\n", b""))
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
        assert result["push_head_sha"] == "dirty-head-sha"
        assert result["push_commit_message"] == "Ship dirty workspace"
        tracked_add_call = recorded_calls[2]
        assert list(tracked_add_call[-3:]) == [
            "-u",
            "--",
            "api_service/main.py",
        ]
        untracked_add_call = recorded_calls[3]
        assert list(untracked_add_call[-3:]) == [
            "-A",
            "--",
            "tests/new_test.py",
        ]
        commit_call = next(call for call in recorded_calls if "commit" in call)
        assert list(commit_call[-5:]) == [
            "-m",
            "Ship dirty workspace",
            "--",
            "api_service/main.py",
            "tests/new_test.py",
        ]

    @pytest.mark.asyncio
    async def test_push_stages_tracked_artifact_under_ignored_parent_with_update(self):
        """Tracked handoff files under ignored artifact dirs must not fail staging."""
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
                proc.communicate = AsyncMock(return_value=(b"auto-artifact123\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(
                    return_value=(b" M artifacts/jira-orchestrate-pr.json\0", b"")
                )
                proc.returncode = 0
            elif call_count == 3:  # git add -u for tracked ignored-parent file
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 4:  # staged diff check
                proc.communicate = AsyncMock(
                    return_value=(b"artifacts/jira-orchestrate-pr.json\n", b"")
                )
                proc.returncode = 0
            elif call_count == 5:  # commit
                proc.communicate = AsyncMock(
                    return_value=(b"[auto-artifact123 abc123] msg\n", b"")
                )
                proc.returncode = 0
            elif call_count == 6:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif call_count == 7:  # remote branch sha before push
                proc.communicate = AsyncMock(return_value=(b"artifact-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 8:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 9:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"artifact-head-sha\n", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "pushed"
        add_calls = [call for call in recorded_calls if "add" in call]
        assert len(add_calls) == 1
        assert list(add_calls[0][-3:]) == [
            "-u",
            "--",
            "artifacts/jira-orchestrate-pr.json",
        ]

    @pytest.mark.asyncio
    async def test_push_commits_already_staged_add_delete_without_restaging(self):
        """Already-staged add/delete changes should commit without pathspec failures."""
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
                proc.communicate = AsyncMock(return_value=(b"auto-staged123\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(
                    return_value=(
                        b"A  tests/unit/tools/test_link_moonspec_submodule.py\0"
                        b"D  tests/unit/tools/test_sync_moonspec_submodule.py\0",
                        b"",
                    )
                )
                proc.returncode = 0
            elif call_count == 3:  # staged diff check
                proc.communicate = AsyncMock(
                    return_value=(
                        b"tests/unit/tools/test_link_moonspec_submodule.py\n"
                        b"tests/unit/tools/test_sync_moonspec_submodule.py\n",
                        b"",
                    )
                )
                proc.returncode = 0
            elif call_count == 4:  # commit
                proc.communicate = AsyncMock(
                    return_value=(b"[auto-staged123 abc123] msg\n", b"")
                )
                proc.returncode = 0
            elif call_count == 5:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif call_count == 6:  # remote branch sha before push
                proc.communicate = AsyncMock(return_value=(b"staged-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 7:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 8:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"staged-head-sha\n", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch(
                "run-1",
                commit_message="Ship staged workspace",
            )

        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "auto-staged123"
        assert result["push_commit_count"] == 1
        assert result["push_head_sha"] == "staged-head-sha"
        assert result["push_commit_message"] == "Ship staged workspace"
        assert all("add" not in call for call in recorded_calls)
        commit_call = next(call for call in recorded_calls if "commit" in call)
        assert list(commit_call[-5:]) == [
            "-m",
            "Ship staged workspace",
            "--",
            "tests/unit/tools/test_link_moonspec_submodule.py",
            "tests/unit/tools/test_sync_moonspec_submodule.py",
        ]

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
                proc.communicate = AsyncMock(return_value=(b" M api_service/main.py\0", b""))
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
        recorded_calls: list[tuple[object, ...]] = []

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            recorded_calls.append(args)
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"auto-claude123\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"?? CLAUDE.md\0", b""))
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
        assert result["push_branch"] == "auto-claude123"
        assert result["push_commit_count"] == 0
        assert all("add" not in call for call in recorded_calls)

    @pytest.mark.asyncio
    async def test_push_stages_only_porcelain_reported_paths(self):
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
                    return_value=(
                        b" M moonmind/workflows/temporal/activity_runtime.py\0",
                        b"",
                    )
                )
                proc.returncode = 0
            elif call_count == 3:  # git add -A for changed paths only
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 4:  # staged diff check
                proc.communicate = AsyncMock(
                    return_value=(b"moonmind/workflows/temporal/activity_runtime.py\n", b"")
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
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "pushed"
        add_call = recorded_calls[2]
        assert "." not in add_call
        assert "live_streams.spool" not in add_call
        assert list(add_call[-2:]) == [
            "--",
            "moonmind/workflows/temporal/activity_runtime.py",
        ]

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
        assert env["GITHUB_TOKEN"] == "ghp_test_token_value"
        assert env["GH_TOKEN"] == "ghp_test_token_value"
        assert env["GIT_TERMINAL_PROMPT"] == "0"

        helper_text = helper_path.read_text(encoding="utf-8")
        gitconfig_text = gitconfig.read_text(encoding="utf-8")
        assert "ghp_test_token_value" not in helper_text
        assert "ghp_test_token_value" not in gitconfig_text
        assert "os.environ.get('GITHUB_TOKEN'" in helper_text
        assert "password={token}" in helper_text
        assert "git-credential-moonmind" in gitconfig_text
        assert str(workspace.resolve()) in gitconfig_text

    def test_workspace_command_env_uses_resolved_github_token(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "run-1" / "repo"
        support_root = workspace.parent / ".moonmind"
        support_bin = support_root / "bin"
        gitconfig = support_root / "gitconfig"
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)

        env = TemporalAgentRuntimeActivities._workspace_command_env(
            str(workspace),
            github_token="resolved-token",
        )

        helper_path = support_bin / "git-credential-moonmind"
        assert env["GITHUB_TOKEN"] == "resolved-token"
        assert env["GH_TOKEN"] == "resolved-token"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_CONFIG_GLOBAL"] == str(gitconfig)
        assert helper_path.is_file()
        assert "resolved-token" not in helper_path.read_text(encoding="utf-8")
        assert "resolved-token" not in gitconfig.read_text(encoding="utf-8")

    def test_workspace_command_env_logs_bootstrap_failures(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "run-1" / "repo"
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)

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
            elif call_count == 3:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif call_count == 4:  # remote branch sha before push
                proc.communicate = AsyncMock(return_value=(b"auto-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 5:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 6:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"auto-head-sha\n", b""))
                proc.returncode = 0
            else:  # rev-list --count -- simulate failure
                raise OSError("git rev-list failed")
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "auto-abc123"
        assert "push_commit_count" not in result

    @pytest.mark.asyncio
    async def test_push_resolves_github_token_and_injects_push_env(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        call_count = 0
        captured_push_env: dict[str, str] | None = None

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count, captured_push_env
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:  # rev-parse
                proc.communicate = AsyncMock(return_value=(b"auto-abc123\n", b""))
                proc.returncode = 0
            elif call_count == 2:  # status --porcelain
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 3:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif call_count == 4:  # remote branch sha before push
                proc.communicate = AsyncMock(return_value=(b"auto-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 5:  # push
                captured_push_env = kwargs["env"]
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 6:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"auto-head-sha\n", b""))
                proc.returncode = 0
            else:  # rev-list --count
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            return proc

        resolved = MagicMock()
        resolved.token = "resolved-push-token"
        with (
            patch.object(
                TemporalAgentRuntimeActivities,
                "_detect_repo_from_workspace",
                return_value="owner/repo",
            ) as detect_repo,
            patch(
                "moonmind.auth.github_credentials.resolve_github_credential",
                new_callable=AsyncMock,
                return_value=resolved,
            ) as resolve_github,
            patch("asyncio.create_subprocess_exec", side_effect=_mock_exec),
        ):
            result = await activities._push_workspace_branch("run-1")

        detect_repo.assert_called_once_with("/work/agent_jobs/run-1/repo")
        resolve_github.assert_awaited_once_with(repo="owner/repo")
        assert result["push_status"] == "pushed"
        assert captured_push_env is not None
        assert captured_push_env["GITHUB_TOKEN"] == "resolved-push-token"
        assert captured_push_env["GH_TOKEN"] == "resolved-push-token"
        assert captured_push_env["GIT_TERMINAL_PROMPT"] == "0"

    @pytest.mark.asyncio
    async def test_push_does_not_expose_resolved_token_to_commit_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        captured_commit_env: dict[str, str] | None = None
        captured_push_env: dict[str, str] | None = None

        async def _mock_exec(*args, **kwargs):
            nonlocal captured_commit_env, captured_push_env
            command = [str(arg) for arg in args]
            proc = AsyncMock()
            if "rev-parse" in command and "--abbrev-ref" in command:
                proc.communicate = AsyncMock(
                    return_value=(b"feature/token-scope\n", b"")
                )
                proc.returncode = 0
            elif "status" in command:
                proc.communicate = AsyncMock(return_value=(b"M  changed.txt\0", b""))
                proc.returncode = 0
            elif "add" in command:
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif "diff" in command and "--cached" in command:
                proc.communicate = AsyncMock(return_value=(b"changed.txt\n", b""))
                proc.returncode = 0
            elif "commit" in command:
                captured_commit_env = kwargs["env"]
                proc.communicate = AsyncMock(return_value=(b"[feature abc] msg\n", b""))
                proc.returncode = 0
            elif "symbolic-ref" in command:
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif "rev-parse" in command and "--verify" in command:
                proc.communicate = AsyncMock(return_value=(b"", b"missing ref"))
                proc.returncode = 1
            elif "ls-remote" in command:
                proc.communicate = AsyncMock(
                    return_value=(
                        b"remote-sha\trefs/heads/feature/token-scope\n",
                        b"",
                    )
                )
                proc.returncode = 0
            elif "push" in command:
                captured_push_env = kwargs["env"]
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif "rev-parse" in command and "HEAD" in command:
                proc.communicate = AsyncMock(return_value=(b"head-sha\n", b""))
                proc.returncode = 0
            elif "rev-list" in command:
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            else:
                raise AssertionError(f"unexpected command: {command!r}")
            return proc

        with (
            patch.object(
                TemporalAgentRuntimeActivities,
                "_resolve_workspace_push_github_token",
                new_callable=AsyncMock,
                return_value="resolved-push-token",
            ),
            patch("asyncio.create_subprocess_exec", side_effect=_mock_exec),
        ):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "pushed"
        assert captured_commit_env is not None
        assert "GITHUB_TOKEN" not in captured_commit_env
        assert "GH_TOKEN" not in captured_commit_env
        assert captured_push_env is not None
        assert captured_push_env["GITHUB_TOKEN"] == "resolved-push-token"
        assert captured_push_env["GH_TOKEN"] == "resolved-push-token"

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
            elif call_count == 3:  # remote default branch
                proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))
                proc.returncode = 0
            elif call_count == 4:  # remote branch sha before push
                proc.communicate = AsyncMock(return_value=(b"auto-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 5:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 6:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"auto-head-sha\n", b""))
                proc.returncode = 0
            else:  # rev-list --count -- non-zero exit with empty stdout
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
            elif call_count == 3:  # remote branch sha before push
                proc.communicate = AsyncMock(return_value=(b"develop-remote-sha\n", b""))
                proc.returncode = 0
            elif call_count == 4:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count == 5:  # rev-parse HEAD
                proc.communicate = AsyncMock(return_value=(b"develop-head-sha\n", b""))
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
        assert result["push_head_sha"] == "develop-head-sha"
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
            patch.object(
                activities,
                "_resolve_workspace_push_github_token",
                new_callable=AsyncMock,
                return_value="resolved-token",
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

        mock_push.assert_called_once_with(
            "run-1",
            github_token="resolved-token",
            target_branch=None,
        )
        assert result.metadata["push_status"] == "pushed"
        assert result.metadata["push_branch"] == "my-branch"

    @pytest.mark.asyncio
    async def test_fetch_result_allows_same_target_branch_for_branch_publish(self):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)

        with (
            patch.object(
                activities,
                "_push_workspace_branch",
                new_callable=AsyncMock,
                return_value={
                    "push_status": "pushed",
                    "push_branch": "feature/existing",
                },
            ) as mock_push,
            patch.object(
                activities,
                "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch.object(
                activities,
                "_resolve_workspace_push_github_token",
                new_callable=AsyncMock,
                return_value="resolved-token",
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
                {
                    "run_id": "run-1",
                    "agent_id": "claude",
                    "publish_mode": "branch",
                    "target_branch": "feature/existing",
                    "head_branch": "feature/existing",
                },
            )

        mock_push.assert_called_once_with(
            "run-1",
            github_token="resolved-token",
            target_branch="feature/existing",
            allow_target_branch_push=True,
            head_branch="feature/existing",
        )
        assert result.metadata["push_status"] == "pushed"
        assert result.metadata["push_branch"] == "feature/existing"

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
            patch.object(
                activities,
                "_resolve_workspace_push_github_token",
                new_callable=AsyncMock,
                return_value="resolved-token",
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
                activities, "_normalize_workspace_git_alternates",
            ) as mock_normalize,
            patch.object(
                activities, "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch.object(
                activities,
                "_resolve_workspace_push_github_token",
                new_callable=AsyncMock,
                return_value="resolved-token",
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
        mock_normalize.assert_called_once_with("/work/agent_jobs/run-1/repo")

    @pytest.mark.asyncio
    async def test_fetch_result_publishes_controlled_failure_to_recovery_branch(self):
        """Controlled failure preserves work without replacing the failure."""
        store = _make_mock_store(failure_class="execution_error")
        activities = TemporalAgentRuntimeActivities(run_store=store)

        mock_result = AgentRunResult(
            summary="failed",
            failure_class="execution_error",
        )

        with (
            patch.object(
                activities,
                "_push_workspace_branch",
                new_callable=AsyncMock,
                return_value={
                    "push_status": "pushed",
                    "push_branch": "mm/run-1/workflow/cp-123/terminal-recovered-work",
                    "push_head_sha": "abc123",
                    "push_base_branch": "main",
                    "remote_verified": True,
                },
            ) as mock_push,
            patch.object(
                activities, "_detect_pr_url_from_workspace",
                return_value=None,
            ),
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
            patch.object(
                activities,
                "_resolve_workspace_push_github_token",
                new_callable=AsyncMock,
                return_value="resolved-token",
            ),
        ):
            adapter_instance = MockAdapter.return_value
            adapter_instance.fetch_result = AsyncMock(return_value=mock_result)

            result = await activities.agent_runtime_fetch_result(
                {
                    "run_id": "run-1",
                    "agent_id": "claude",
                    "publish_mode": "pr",
                    "terminal_checkpoint_publication_enabled": True,
                },
            )

        mock_push.assert_called_once()
        push_kwargs = mock_push.call_args.kwargs
        assert push_kwargs["head_branch"].startswith("mm/run-1/workflow/cp-")
        assert push_kwargs["allow_target_branch_push"] is False
        assert "MoonLadderStudios/MoonMind#3229" in push_kwargs["commit_message"]
        assert result.failure_class == "execution_error"
        assert result.metadata["terminalPublication"]["remoteVerified"] is True
        assert result.metadata["terminalPublication"]["headSha"] == "abc123"

    @pytest.mark.asyncio
    async def test_fetch_result_does_not_publish_system_error(self):
        store = _make_mock_store(failure_class="system_error")
        activities = TemporalAgentRuntimeActivities(run_store=store)
        with (
            patch.object(activities, "_push_workspace_branch") as mock_push,
            patch.object(
                activities,
                "_resolve_workspace_push_github_token",
                new_callable=AsyncMock,
                return_value="resolved-token",
            ),
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
        ):
            MockAdapter.return_value.fetch_result = AsyncMock(
                return_value=AgentRunResult(
                    summary="infrastructure lost",
                    failure_class="system_error",
                )
            )
            result = await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude", "publish_mode": "pr"},
            )

        mock_push.assert_not_called()
        assert result.failure_class == "system_error"
        assert "terminalPublication" not in result.metadata

    @pytest.mark.asyncio
    async def test_terminal_publication_adopts_verified_existing_head(self):
        """Equivalent remote evidence is adopted without another push."""
        store = _make_mock_store(failure_class="execution_error")
        activities = TemporalAgentRuntimeActivities(run_store=store)
        with (
            patch.object(
                activities,
                "_resolve_workspace_push_github_token",
                new_callable=AsyncMock,
                return_value="resolved-token",
            ),
            patch.object(
                activities,
                "_resolve_workspace_remote_branch_sha",
                new_callable=AsyncMock,
                return_value="abc123",
            ),
            patch.object(
                activities, "_push_workspace_branch", new_callable=AsyncMock
            ) as mock_push,
        ):
            result = await activities.agent_runtime_publish_terminal_checkpoint(
                {
                    "runId": "run-1",
                    "agentId": "claude",
                    "failureClass": "execution_error",
                    "targetBranch": "main",
                    "existingBranch": "mm/run-1/recovered-work",
                    "existingHeadSha": "abc123",
                    "existingPrUrl": "https://github.com/org/repo/pull/1",
                    "idempotencyKey": "terminal-checkpoint-v1:run-1",
                }
            )

        mock_push.assert_not_awaited()
        assert result.status == "already_published"
        assert result.remote_verified is True
        assert result.branch_name == "mm/run-1/recovered-work"
        assert result.pr_url == "https://github.com/org/repo/pull/1"

    @pytest.mark.asyncio
    async def test_terminal_publication_no_remote_writes_is_typed_skip(self):
        activities = TemporalAgentRuntimeActivities(run_store=_make_mock_store())
        with patch.object(
            activities, "_push_workspace_branch", new_callable=AsyncMock
        ) as mock_push:
            result = await activities.agent_runtime_publish_terminal_checkpoint(
                {
                    "runId": "run-1",
                    "agentId": "claude",
                    "failureClass": "execution_error",
                    "noRemoteWrites": True,
                    "idempotencyKey": "terminal-checkpoint-v1:run-1",
                }
            )

        mock_push.assert_not_awaited()
        assert result.status == "skipped"
        assert result.reason_code == "no_remote_writes"
        assert result.attempted is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("policy_field", "reason_code"),
        [
            ("publicationEnabled", "policy_disabled"),
            ("readOnly", "read_only"),
            ("dryRun", "dry_run"),
            ("workspaceAuthoritative", "workspace_unavailable"),
            ("runtimeCapabilitySupported", "runtime_capability_unsupported"),
        ],
    )
    async def test_terminal_publication_policy_returns_typed_skip(
        self, policy_field, reason_code
    ):
        activities = TemporalAgentRuntimeActivities(run_store=_make_mock_store())
        request = {
            "runId": "run-1",
            "agentId": "claude",
            "failureClass": "execution_error",
            "idempotencyKey": "terminal-checkpoint-v1:run-1",
            policy_field: False if policy_field in {
                "publicationEnabled",
                "workspaceAuthoritative",
                "runtimeCapabilitySupported",
            } else True,
        }
        with patch.object(
            activities, "_push_workspace_branch", new_callable=AsyncMock
        ) as mock_push:
            result = await activities.agent_runtime_publish_terminal_checkpoint(
                request
            )

        mock_push.assert_not_awaited()
        assert result.status == "skipped"
        assert result.reason_code == reason_code
        assert result.attempted is False

    @pytest.mark.asyncio
    async def test_terminal_publication_exception_preserves_primary_failure(self):
        store = _make_mock_store(failure_class="execution_error")
        activities = TemporalAgentRuntimeActivities(run_store=store)

        with (
            patch.object(
                activities,
                "_push_workspace_branch",
                new_callable=AsyncMock,
                side_effect=RuntimeError("credential helper unavailable"),
            ),
            patch.object(
                activities,
                "_resolve_workspace_push_github_token",
                new_callable=AsyncMock,
                return_value="resolved-token",
            ),
            patch(
                "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
            ) as MockAdapter,
        ):
            MockAdapter.return_value.fetch_result = AsyncMock(
                return_value=AgentRunResult(
                    summary="original controlled failure",
                    failure_class="execution_error",
                )
            )
            result = await activities.agent_runtime_fetch_result(
                {
                    "run_id": "run-1",
                    "agent_id": "claude",
                    "publish_mode": "pr",
                    "terminal_checkpoint_publication_enabled": True,
                },
            )

        assert result.failure_class == "execution_error"
        assert result.summary == "original controlled failure"
        publication = result.metadata["terminalPublication"]
        assert publication["status"] == "failed"
        assert publication["remoteVerified"] is False

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
            patch.object(
                activities,
                "_resolve_workspace_push_github_token",
                new_callable=AsyncMock,
                return_value="resolved-token",
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
            github_token="resolved-token",
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
