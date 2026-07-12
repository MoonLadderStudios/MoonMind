import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.asyncio
async def test_default_cleanup_deletes_terminal_workspace_and_preserves_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "agent_jobs"
    terminal_root = runtime_root / "terminal-run"
    active_root = runtime_root / "active-run"
    terminal_root.joinpath("repo").mkdir(parents=True)
    active_root.joinpath("repo").mkdir(parents=True)
    old = datetime.now(tz=UTC) - timedelta(days=45)
    for root in (terminal_root, active_root):
        os.utime(root, (old.timestamp(), old.timestamp()))

    run_store = ManagedRunStore(runtime_root / "managed_runs")
    for run_id, status, root in (
        ("terminal-run", "completed", terminal_root),
        ("active-run", "running", active_root),
    ):
        run_store.save(
            ManagedRunRecord(
                runId=run_id,
                workflowId=f"mm:{run_id}",
                agentId="agent-1",
                runtimeId="codex-cli",
                status=status,
                startedAt=old - timedelta(hours=1),
                finishedAt=old if status == "completed" else None,
                workspacePath=str(root / "repo"),
            )
        )

    class _Controller:
        async def collect_managed_runtime_cleanup_docker_references(self):
            return {"activeContainerRefs": [], "activeMountPaths": []}

    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(runtime_root))
    monkeypatch.delenv("MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED", raising=False)
    monkeypatch.delenv("MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN", raising=False)
    result = await TemporalAgentRuntimeActivities(
        run_store=run_store,
        session_controller=_Controller(),
    ).agent_runtime_cleanup_managed_runtime_files({})

    assert result["disabled"] is False
    assert result["dryRun"] is False
    assert result["deletedRoots"] == 1
    assert not terminal_root.exists()
    assert active_root.exists()
