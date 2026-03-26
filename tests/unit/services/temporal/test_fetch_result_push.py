"""Unit tests for _push_workspace_branch and fetch_result push integration."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

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
            else:  # push
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await activities._push_workspace_branch("run-1")
        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "feature/delete-spec-048"
        assert "push_error" not in result

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

        mock_result = MagicMock()
        mock_result.failure_class = None
        mock_result.model_dump.return_value = {
            "summary": "done",
            "metadata": {},
        }

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
            adapter_instance.fetch_result = AsyncMock(return_value=mock_result)

            result = await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude", "publish_mode": "pr"},
            )

        mock_push.assert_called_once_with("run-1", target_branch=None)
        assert result["metadata"]["push_status"] == "pushed"
        assert result["metadata"]["push_branch"] == "my-branch"

    @pytest.mark.asyncio
    async def test_fetch_result_skips_push_when_publish_mode_none(self):
        """No push when publish_mode=none."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)

        mock_result = MagicMock()
        mock_result.failure_class = None
        mock_result.model_dump.return_value = {
            "summary": "done",
            "metadata": {},
        }

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

        mock_result = MagicMock()
        mock_result.failure_class = "execution_error"
        mock_result.model_dump.return_value = {
            "summary": "failed",
            "failureClass": "execution_error",
            "metadata": {},
        }

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

        mock_result = MagicMock()
        mock_result.failure_class = None
        mock_result.model_dump.return_value = {
            "summary": "done",
            "metadata": {},
        }

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
            adapter_instance.fetch_result = AsyncMock(return_value=mock_result)

            result = await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude", "publish_mode": "pr"},
            )

        assert result["metadata"]["push_status"] == "failed"
        assert result["metadata"]["push_error"] == "remote: Permission denied"

    @pytest.mark.asyncio
    async def test_fetch_result_defaults_publish_mode_none(self):
        """When publish_mode not provided, defaults to 'none' (no push)."""
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)

        mock_result = MagicMock()
        mock_result.failure_class = None
        mock_result.model_dump.return_value = {
            "summary": "done",
            "metadata": {},
        }

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
