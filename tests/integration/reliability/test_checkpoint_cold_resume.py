from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.workflows.executions.control_stop_continuation import (
    ControlStopContinuationContract,
)
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.runtime.checkpoint_restore import (
    ManagedCheckpointRestoreService,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.step_ledger import (
    materialize_preserved_steps,
    refresh_ready_steps,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.unit.workflows.executions.test_control_stop_continuation import (
    _payload as control_stop_payload,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


def _git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


@pytest.mark.integration_ci
async def test_managed_codex_checkpoint_cold_resume_uses_only_durable_state(
    tmp_path: Path,
) -> None:
    """Lose the source run and workspace, then restore and resume its failed step."""
    origin = tmp_path / "trusted-repository"
    origin.mkdir()
    _git("init", "-q", cwd=origin)
    _git("config", "user.name", "MoonMind Test", cwd=origin)
    _git("config", "user.email", "test@example.invalid", cwd=origin)
    (origin / "tracked.txt").write_text("base\n", encoding="utf-8")
    (origin / "deleted.txt").write_text("remove me\n", encoding="utf-8")
    (origin / ".gitignore").write_text(".cache/\n", encoding="utf-8")
    _git("add", ".", cwd=origin)
    _git("commit", "-qm", "base", cwd=origin)
    base_commit = _git("rev-parse", "HEAD", cwd=origin)

    source_run_root = tmp_path / "live-source"
    source_root = source_run_root / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(source_root)], check=True)
    (source_root / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (source_root / "deleted.txt").unlink()
    (source_root / "binary.bin").write_bytes(b"\x00\xff\x10")
    executable = source_root / "run.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    (source_root / "safe-link").symlink_to("tracked.txt")
    (source_root / "name with spaces-é.txt").write_text("unicode\n", encoding="utf-8")
    (source_root / ".cache").mkdir()
    (source_root / ".cache" / "excluded").write_text("cache", encoding="utf-8")

    run_store = ManagedRunStore(tmp_path / "managed-run-store")
    run_store.save(
        ManagedRunRecord(
            runId="source-agent-run",
            workflowId="source",
            ownerRunId="source-run",
            logicalStepId="implement",
            executionOrdinal=1,
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(UTC),
            finishedAt=datetime.now(UTC),
            workspacePath=str(source_run_root),
        )
    )
    artifact_store = InMemoryArtifactStore()
    activities = TemporalAgentRuntimeActivities(
        run_store=run_store, artifact_service=object(), client_adapter=object()
    )

    async def put(payload: bytes, content_type: str, _kind: str) -> str:
        return artifact_store.put_bytes(payload, content_type=content_type).artifact_ref

    activities._put_managed_checkpoint_artifact = put
    capabilities = resolve_runtime_execution_capabilities("codex_cli")
    capture_route = build_default_activity_catalog().resolve_activity(
        "agent_runtime.capture_workspace_checkpoint"
    )
    restore_route = build_default_activity_catalog().resolve_activity(
        "agent_runtime.restore_workspace_checkpoint"
    )
    assert (capture_route.task_queue, restore_route.task_queue) == (
        "mm.activity.agent_runtime",
        "mm.activity.agent_runtime",
    )
    capture = await activities.agent_runtime_capture_workspace_checkpoint(
        {
            "schemaVersion": "v1",
            "identity": {
                "workflowId": "source",
                "runId": "source-run",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
            },
            "boundary": "before_execution",
            "checkpointKind": "worktree_archive",
            "workspaceLocator": {
                "kind": "managed_runtime",
                "runtimeId": "codex_cli",
                "agentRunId": "source-agent-run",
                "relativePath": "repo",
            },
            "expectedRuntimeId": "codex_cli",
            "capabilitySetVersion": capabilities.capability_set_version,
            "capabilityDigest": capabilities.capability_digest,
            "artifactNamespace": "cold-resume/implement",
            "idempotencyKey": "cold-resume:capture:implement:1",
            "capturePolicy": {
                "includeTracked": True,
                "includeUntracked": True,
                "includeIgnored": False,
                "redactionProfile": "managed-code-workspace-v1",
            },
        }
    )
    checkpoint = {
        "contentType": "application/vnd.moonmind.step-execution-checkpoint+json;version=1",
        "source": {
            "workflowId": "source",
            "runId": "source-run",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
        },
        "boundary": "before_execution",
        "workspace": capture["workspace"],
    }
    checkpoint_ref = artifact_store.put_bytes(
        json.dumps(checkpoint).encode(),
        content_type="application/vnd.moonmind.step-execution-checkpoint+json;version=1",
    ).artifact_ref

    # This is the defining cold-resume boundary: no source path, runtime object,
    # or managed-run record remains reachable when restoration begins.
    shutil.rmtree(source_run_root)
    run_store.delete("source-agent-run")
    del activities
    assert not source_run_root.exists()
    assert run_store.load("source-agent-run") is None

    restore_service = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "destination-store",
        artifact_store=artifact_store,
        repository_source_root=origin,
    )
    workspace = capture["workspace"]
    restore_request = {
        "schemaVersion": "v1",
        "recoveryIdentity": {
            "workflowId": "recovery",
            "runId": "recovery-run",
            "logicalStepId": "implement",
            "executionOrdinal": 2,
        },
        "source": {
            "workflowId": "source",
            "runId": "source-run",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
            "checkpointRef": checkpoint_ref,
            "checkpointBoundary": "before_execution",
            "sourceWorkspaceLocator": capture["sourceWorkspaceLocator"],
        },
        "checkpoint": {
            "kind": "worktree_archive",
            "baseCommit": base_commit,
            "archiveRef": workspace["archiveRef"],
            "archiveDigest": workspace["archiveDigest"],
            "manifestRef": workspace["manifestRef"],
            "manifestDigest": workspace["manifestDigest"],
        },
        "destination": {
            "runtimeId": "codex_cli",
            "agentRunId": "destination-agent-run",
            "repository": "MoonLadderStudios/MoonMind",
            "relativePath": "repo",
        },
        "workspacePolicy": "restore_pre_execution",
        "resumePhase": "rerun_failed_step",
        "capabilitySetVersion": capabilities.capability_set_version,
        "capabilityDigest": capabilities.capability_digest,
        "idempotencyKey": "cold-resume:restore:implement:2",
    }
    restored = await restore_service.restore(restore_request)
    assert restored == await restore_service.restore(restore_request)
    locator = restored["destinationWorkspaceLocator"]
    assert locator == {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": "destination-agent-run",
        "relativePath": "repo",
    }
    destination = tmp_path / "destination-store" / "destination-agent-run" / "repo"
    assert (destination / "tracked.txt").read_text(encoding="utf-8") == "changed\n"
    assert not (destination / "deleted.txt").exists()
    assert (destination / "binary.bin").read_bytes() == b"\x00\xff\x10"
    assert (destination / "run.sh").stat().st_mode & 0o111
    assert os.readlink(destination / "safe-link") == "tracked.txt"
    assert not (destination / ".cache" / "excluded").exists()

    workflow = MoonMindRunWorkflow()
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {"id": "prepare", "title": "Prepare"},
            {"id": "implement", "title": "Implement"},
            {"id": "verify", "title": "Verify"},
        ],
        dependency_map={
            "prepare": [],
            "implement": ["prepare"],
            "verify": ["implement"],
        },
        updated_at=datetime.now(UTC),
    )
    materialize_preserved_steps(
        workflow._step_ledger_rows,
        source_workflow_id="source",
        source_run_id="source-run",
        preserved_steps=[
            {
                "logicalStepId": "prepare",
                "status": "succeeded",
                "terminalDisposition": "accepted",
                "sourceExecutionOrdinal": 1,
                "artifacts": {"outputSummary": "artifact://prepare/output"},
                "stateCheckpointRef": "artifact://prepare/checkpoint",
            }
        ],
        updated_at=datetime.now(UTC),
    )
    refresh_ready_steps(workflow._step_ledger_rows, updated_at=datetime.now(UTC))
    rows = {row["logicalStepId"]: row for row in workflow._step_ledger_rows}
    assert rows["prepare"]["status"] == "completed"
    assert rows["prepare"]["preservedFrom"]["workflowId"] == "source"
    assert rows["implement"]["status"] == "ready"
    assert rows["verify"]["status"] == "pending"
    assert restored["restorationEvidenceRef"]

    # The control-stop continuation contract consumes the same durable archive
    # after the source workspace and ManagedRun record have been destroyed. Its
    # deterministic destination and idempotency identity must survive a cold
    # process boundary without any source-path fallback.
    control_stop_checkpoint_ref = artifact_store.put_bytes(
        json.dumps(
            {
                "contentType": (
                    "application/vnd.moonmind.step-execution-checkpoint+json;version=1"
                ),
                "source": {
                    "workflowId": "source",
                    "runId": "source-run",
                    "logicalStepId": "verify",
                    "executionOrdinal": 6,
                },
                "boundary": "after_gate",
                "workspace": workspace,
            }
        ).encode(),
        content_type=(
            "application/vnd.moonmind.step-execution-checkpoint+json;version=1"
        ),
    ).artifact_ref
    payload = control_stop_payload()
    payload.update(
        {
            "sourceWorkflowId": "source",
            "checkpointRef": control_stop_checkpoint_ref,
            "checkpointDigest": "checkpoint-envelope-digest",
            "workspaceHeadRef": workspace["archiveRef"],
            "workspaceHeadDigest": workspace["archiveDigest"],
            "workspaceBaseCommit": base_commit,
            "workspaceManifestRef": workspace["manifestRef"],
            "workspaceManifestDigest": workspace["manifestDigest"],
            "restoreCapabilitySetVersion": capabilities.capability_set_version,
            "restoreCapabilityDigest": capabilities.capability_digest,
            "captureCapabilitySetVersion": capabilities.capability_set_version,
            "captureCapabilityDigest": capabilities.capability_digest,
        }
    )
    contract = ControlStopContinuationContract.model_validate(payload)
    control_stop_request = contract.restore_request(
        destination_run_id="control-stop-run"
    )
    control_stop_restored = await restore_service.restore(control_stop_request)

    assert control_stop_restored == await restore_service.restore(control_stop_request)
    assert control_stop_restored["checkpointRef"] == control_stop_checkpoint_ref
    assert control_stop_restored["destinationWorkspaceLocator"] == {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": contract.destination_workspace_id,
        "relativePath": "repo",
    }
    assert not source_run_root.exists()
    assert run_store.load("source-agent-run") is None
