"""Real managed saved-work capture for publication tests (MoonLadderStudios/MoonMind#4018).

The capture activity runs against a real Git worktree. Artifacts go either to
an in-memory map or, when ``artifact_service`` is given, through the production
checkpoint-artifact path into that service, so publication consumes exactly the
bytes capture produced.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit_all(repo: Path, message: str = "base") -> str:
    git(repo, "add", "-A")
    git(
        repo,
        "-c",
        "user.name=source",
        "-c",
        "user.email=source@example.invalid",
        "commit",
        "-qm",
        message,
    )
    return git(repo, "rev-parse", "HEAD")


@dataclass
class CapturedSavedWork:
    saved_work_ref: str
    saved_work_digest: str
    baseline_commit: str
    source_repo: Path
    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)

    async def read(self, ref: str, content_types: frozenset[str]) -> bytes:
        self.reads.append(ref)
        payload, content_type = self.objects[ref]
        assert content_type in content_types
        return payload

    def manifest(self) -> dict:
        return json.loads(self.objects[self.saved_work_ref][0])

    def remove_source(self) -> None:
        """Remove the original workspace; publication must not need it."""
        shutil.rmtree(self.source_repo)


async def capture_saved_work(
    tmp_path: Path,
    base_files: dict[str, str],
    mutate: Callable[[Path], None],
    *,
    artifact_service: Any = None,
) -> CapturedSavedWork:
    repo = tmp_path / "source-runs" / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "--initial-branch=main")
    for name, text in base_files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    baseline = commit_all(repo)
    mutate(repo)
    now = datetime.now(UTC)
    store = ManagedRunStore(tmp_path / "source-runs" / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1",
            workflowId="mm:source",
            agentId="codex_cli",
            ownerRunId="source-run",
            logicalStepId="implement",
            executionOrdinal=1,
            runtimeId="codex_cli",
            status="completed",
            startedAt=now,
            finishedAt=now,
            workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store,
        artifact_service=artifact_service or SimpleNamespace(link_artifact=AsyncMock()),
        client_adapter=object(),
    )
    objects: dict[str, tuple[bytes, str]] = {}

    async def put(payload: bytes, content_type: str, kind: str, link=None) -> str:
        ref = "artifact://" + hashlib.sha256(payload).hexdigest()
        objects[ref] = (payload, content_type)
        return ref

    if artifact_service is None:
        activities._put_managed_checkpoint_artifact = put
    digest = resolve_runtime_execution_capabilities("codex_cli").capability_digest
    result = await activities.agent_runtime_capture_workspace_checkpoint(
        {
            "schemaVersion": "v1",
            "identity": {
                "workflowId": "mm:source",
                "runId": "source-run",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
            },
            "boundary": "after_execution",
            "checkpointKind": "worktree_archive",
            "workspaceLocator": {
                "kind": "managed_runtime",
                "runtimeId": "codex_cli",
                "agentRunId": "agent-run-1",
                "relativePath": "repo",
            },
            "expectedRuntimeId": "codex_cli",
            "capabilitySetVersion": "runtime-execution-capabilities-v1",
            "capabilityDigest": digest,
            "artifactNamespace": "step-checkpoints/implement",
            "idempotencyKey": "saved-work-4018:capture",
            "capturePolicy": {
                "includeTracked": True,
                "includeUntracked": True,
                "includeIgnored": False,
                "redactionProfile": "managed-code-workspace-v1",
            },
        }
    )
    assert result["status"] == "captured", result
    return CapturedSavedWork(
        saved_work_ref=result["savedWorkRef"],
        saved_work_digest=result["savedWorkDigest"],
        baseline_commit=baseline,
        source_repo=repo,
        objects=objects,
    )
