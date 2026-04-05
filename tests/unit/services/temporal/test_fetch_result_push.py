"""Unit tests for _push_workspace_branch and fetch_result push integration."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

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
            elif call_count == 2:  # push
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

        async def _mock_exec(*args, **kwargs):
            recorded_calls.append(args)
            proc = AsyncMock()
            command = list(args)
            if "rev-parse" in command:
                proc.communicate = AsyncMock(return_value=(b"feature/safe-branch\n", b""))
                proc.returncode = 0
            elif "push" in command:
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:
                proc.communicate = AsyncMock(return_value=(b"1\n", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")

        assert result["push_status"] == "pushed"
        assert len(recorded_calls) == 3
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
            elif call_count == 2:  # push
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
            elif call_count == 2:  # push
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
            elif call_count == 2:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:  # rev-list --count — simulate failure
                raise OSError("git rev-list failed")
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "auto-abc123"

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
            elif call_count == 2:  # push
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
            elif call_count == 2:  # push
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
    async def test_fetch_result_adds_operator_summary_from_stdout_artifact(self):
        store = _make_mock_store()
        store.load.return_value.stdout_artifact_ref = "art-stdout"
        artifact_service = MagicMock()
        artifact_service.read_bytes = AsyncMock(
            return_value=(
                MagicMock(),
                (
                    b"noise\n**Final Report**\nThe requested behavior already existed in the repo.\n"
                    b"Files edited in this run: none.\n\ncodex\n"
                ),
            )
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
