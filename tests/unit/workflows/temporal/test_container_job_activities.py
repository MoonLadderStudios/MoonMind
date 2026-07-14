"""Worker-bound adapter Activity tests for MoonMind#3254."""

from __future__ import annotations

from types import SimpleNamespace
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.workflows.temporal.activities.container_job_activities import ContainerJobActivities


@pytest.mark.asyncio
async def test_execute_validates_real_resolved_plan_shape_and_calls_backend() -> None:
    class Backend:
        async def run(self, plan):
            assert plan.backend_ref == "system"
            assert plan.spec.resources.pids == 64
            return SimpleNamespace(container_id="cid", exit_code=0, stdout=b"ok", stderr=b"", reattached=True)

    payload = {
        "jobId": "container-job:" + "a" * 32,
        "backendKind": "docker-engine",
        "backendRef": "system",
        "resolvedWorkspaceRef": "/work/agent_jobs/j/repo",
        "correlationId": "workflow/run",
        "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "spec": {
            "image": "alpine:3.20",
            "workspaceRef": {"kind": "moonmind-session", "sessionId": "s"},
            "command": ["true"],
            "resources": {"cpuMillis": 1000, "memoryMiB": 512, "pids": 64},
        },
    }
    result = await ContainerJobActivities(Backend()).execute(payload)
    assert result == {"containerId": "cid", "exitCode": 0, "stdout": b"ok", "stderr": b"", "reattached": True}
