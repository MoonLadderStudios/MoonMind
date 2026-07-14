from __future__ import annotations

import hashlib
import os
import subprocess
import tarfile
from datetime import UTC, datetime
from io import BytesIO

import pytest
from pydantic import ValidationError

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.schemas.managed_checkpoint_models import ManagedWorkspaceCheckpointCaptureInput
from moonmind.workflows.executions.runtime_capabilities import resolve_runtime_execution_capabilities
from moonmind.workflows.temporal.activity_catalog import AGENT_RUNTIME_FLEET, build_default_activity_catalog
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


def _request(*, digest: str) -> dict[str, object]:
    return {
        "schemaVersion": "v1",
        "identity": {"workflowId": "wf-1", "runId": "run-1", "logicalStepId": "implement", "executionOrdinal": 1},
        "boundary": "after_execution",
        "checkpointKind": "worktree_archive",
        "workspaceLocator": {"kind": "managed_runtime", "runtimeId": "codex_cli", "agentRunId": "agent-run-1", "relativePath": "repo"},
        "expectedRuntimeId": "codex_cli",
        "capabilitySetVersion": "runtime-execution-capabilities-v1",
        "capabilityDigest": digest,
        "artifactNamespace": "step-checkpoints/implement",
        "idempotencyKey": "checkpoint-1:capture",
        "capturePolicy": {"includeTracked": True, "includeUntracked": True, "includeIgnored": False, "redactionProfile": "managed-code-workspace-v1"},
    }


def test_managed_capture_contract_and_catalog_reject_other_authorities() -> None:
    digest = resolve_runtime_execution_capabilities("codex_cli").capability_digest
    ManagedWorkspaceCheckpointCaptureInput.model_validate(_request(digest=digest))
    invalid = _request(digest=digest)
    invalid["workspaceLocator"] = {"kind": "sandbox", "workspaceId": "sandbox-1"}
    with pytest.raises(ValidationError):
        ManagedWorkspaceCheckpointCaptureInput.model_validate(invalid)
    route = build_default_activity_catalog().resolve_activity(
        "agent_runtime.capture_workspace_checkpoint"
    )
    assert route.fleet == AGENT_RUNTIME_FLEET
    assert route.task_queue == "mm.activity.agent_runtime"


@pytest.mark.asyncio
async def test_managed_capture_is_binary_safe_and_idempotent(tmp_path) -> None:
    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    executable = repo / "tracked.sh"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    subprocess.run(["git", "add", "tracked.sh"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        cwd=repo,
        check=True,
    )
    (repo / "binary.bin").write_bytes(b"\x00\xff\x01")
    os.symlink("tracked.sh", repo / "safe-link")
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1", workflowId="wf-1", agentId="codex_cli",
            ownerRunId="run-1", logicalStepId="implement", executionOrdinal=1,
            runtimeId="codex_cli", status="completed", startedAt=datetime.now(UTC),
            finishedAt=datetime.now(UTC), workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )
    artifacts: dict[str, bytes] = {}

    async def put(payload: bytes, _content_type: str, _kind: str) -> str:
        ref = "artifact://" + hashlib.sha256(payload).hexdigest()
        artifacts[ref] = payload
        return ref

    activities._put_managed_checkpoint_artifact = put
    request = _request(
        digest=resolve_runtime_execution_capabilities("codex_cli").capability_digest
    )
    first = await activities.agent_runtime_capture_workspace_checkpoint(request)
    (repo / "binary.bin").write_bytes(b"changed after capture")
    assert await activities.agent_runtime_capture_workspace_checkpoint(request) == first
    with tarfile.open(fileobj=BytesIO(artifacts[first["workspace"]["archiveRef"]]), mode="r:") as archive:
        assert archive.extractfile("binary.bin").read() == b"\x00\xff\x01"
        assert archive.getmember("tracked.sh").mode & 0o111
        assert archive.getmember("safe-link").issym()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ownerRunId", "other-run"),
        ("logicalStepId", "other-step"),
        ("executionOrdinal", 2),
    ],
)
async def test_managed_capture_rejects_step_execution_mismatch(
    tmp_path, field: str, value: object
) -> None:
    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "base"],
        cwd=repo,
        check=True,
    )
    record = {
        "runId": "agent-run-1", "workflowId": "wf-1", "ownerRunId": "run-1",
        "logicalStepId": "implement", "executionOrdinal": 1, "agentId": "codex_cli",
        "runtimeId": "codex_cli", "status": "completed", "startedAt": datetime.now(UTC),
        "workspacePath": str(repo),
    }
    record[field] = value
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(ManagedRunRecord(**record))
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )
    with pytest.raises(Exception, match="source Step Execution"):
        await activities.agent_runtime_capture_workspace_checkpoint(
            _request(digest=resolve_runtime_execution_capabilities("codex_cli").capability_digest)
        )
