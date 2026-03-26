"""Unit tests for _push_workspace_branch and fetch_result push integration."""

from __future__ import annotations

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

    def test_push_skipped_no_store(self):
        activities = TemporalAgentRuntimeActivities(run_store=None)
        result = activities._push_workspace_branch("run-1")
        assert result["push_status"] == "skipped"
        assert "no run store" in result.get("push_error", "")

    def test_push_skipped_no_workspace(self):
        store = _make_mock_store(workspace_path=None)
        activities = TemporalAgentRuntimeActivities(run_store=store)
        result = activities._push_workspace_branch("run-1")
        assert result["push_status"] == "skipped"
        assert "no workspace" in result.get("push_error", "")

    def test_push_skipped_record_not_found(self):
        store = MagicMock()
        store.load.return_value = None
        activities = TemporalAgentRuntimeActivities(run_store=store)
        result = activities._push_workspace_branch("run-1")
        assert result["push_status"] == "skipped"

    @patch("subprocess.run")
    def test_push_protected_branch_main(self, mock_run):
        mock_run.return_value = _make_subprocess_result(stdout="main\n")
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        result = activities._push_workspace_branch("run-1")
        assert result["push_status"] == "protected_branch"
        assert result["push_branch"] == "main"

    @patch("subprocess.run")
    def test_push_protected_branch_master(self, mock_run):
        mock_run.return_value = _make_subprocess_result(stdout="master\n")
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        result = activities._push_workspace_branch("run-1")
        assert result["push_status"] == "protected_branch"
        assert result["push_branch"] == "master"

    @patch("subprocess.run")
    def test_push_protected_branch_head(self, mock_run):
        mock_run.return_value = _make_subprocess_result(stdout="HEAD\n")
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        result = activities._push_workspace_branch("run-1")
        assert result["push_status"] == "protected_branch"

    @patch("subprocess.run")
    def test_push_success(self, mock_run):
        # First call: rev-parse returns feature branch
        # Second call: git push succeeds
        mock_run.side_effect = [
            _make_subprocess_result(stdout="feature/delete-spec-048\n"),
            _make_subprocess_result(returncode=0),
        ]
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        result = activities._push_workspace_branch("run-1")
        assert result["push_status"] == "pushed"
        assert result["push_branch"] == "feature/delete-spec-048"
        assert "push_error" not in result

    @patch("subprocess.run")
    def test_push_failure(self, mock_run):
        mock_run.side_effect = [
            _make_subprocess_result(stdout="feature/delete-spec-048\n"),
            _make_subprocess_result(returncode=128, stderr="remote: Permission denied"),
        ]
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        result = activities._push_workspace_branch("run-1")
        assert result["push_status"] == "failed"
        assert result["push_branch"] == "feature/delete-spec-048"
        assert "Permission denied" in result["push_error"]

    @patch("subprocess.run")
    def test_push_branch_detection_failure(self, mock_run):
        mock_run.return_value = _make_subprocess_result(
            returncode=1, stderr="fatal: not a git repository",
        )
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        result = activities._push_workspace_branch("run-1")
        assert result["push_status"] == "failed"
        assert "could not determine branch" in result["push_error"]

    @patch("subprocess.run", side_effect=OSError("git not found"))
    def test_push_exception_returns_failed(self, mock_run):
        store = _make_mock_store()
        activities = TemporalAgentRuntimeActivities(run_store=store)
        result = activities._push_workspace_branch("run-1")
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

        mock_push.assert_called_once_with("run-1")
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

            result = await activities.agent_runtime_fetch_result(
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

            result = await activities.agent_runtime_fetch_result(
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
            result = await activities.agent_runtime_fetch_result(
                {"run_id": "run-1", "agent_id": "claude"},
            )

        mock_push.assert_not_called()
