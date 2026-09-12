"""Replay c7b23b45 through the worker binding, Git capture, and cold restore."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from moonmind.config.settings import SecuritySettings, settings
from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.schemas.temporal_models import STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.skills.artifact_store import FileArtifactStore
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.runtime.checkpoint_restore import (
    ManagedCheckpointRestoreService,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


class _Artifacts(FileArtifactStore):
    """Local durable transport for the production capture/restore boundaries."""

    @property
    def _meta(self):
        return {
            item["artifact_ref"]: SimpleNamespace(**item)
            for path in self.root.glob("*.meta.json")
            for item in [json.loads(path.read_text())]
        }

    async def put_content_addressed_payload_complete(
        self, *, payload, content_type, **kwargs
    ):
        ref = self.put_bytes(payload, content_type=content_type)
        persisted = self.get_bytes(ref.artifact_ref)
        return (
            SimpleNamespace(
                artifact_id=ref.artifact_ref,
                sha256=hashlib.sha256(persisted).hexdigest(),
                size_bytes=len(persisted),
                content_type=ref.content_type,
                encryption=SimpleNamespace(value="none"),
                status="COMPLETE",
            ),
            False,
        )


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.mark.parametrize("configured_mode", [None, "false", "true"])
@pytest.mark.parametrize(
    "historical_request", [False, True], ids=["defaults", "historical"]
)
async def test_checkpoint_outbound_policy_capture_and_cold_restore(
    tmp_path: Path, monkeypatch, configured_mode, historical_request: bool
) -> None:
    fixture = json.loads(
        (
            Path(__file__).parent
            / "replays/checkpoint-outbound-policy-c7b23b45/manifest.json"
        ).read_text()
    )
    if configured_mode is None:
        monkeypatch.delenv("MOONMIND_HIGH_SECURITY_MODE", raising=False)
    else:
        monkeypatch.setenv("MOONMIND_HIGH_SECURITY_MODE", configured_mode)
    monkeypatch.setattr(settings, "security", SecuritySettings(_env_file=None))

    request = fixture["request"]
    if not historical_request:
        request.pop("capturePolicy")
    capabilities = resolve_runtime_execution_capabilities("codex_cli")
    # Keep the incident's serialized invocation, with today's admitted digest.
    request["capabilityDigest"] = capabilities.capability_digest
    identity = request["identity"]
    agent_run_id = request["workspaceLocator"]["agentRunId"]
    repo = tmp_path / "source" / agent_run_id / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for name, content in fixture["files"].items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "source",
    )
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(repo), str(origin)], check=True)
    (repo / "assessment.json").write_text('{"verdict": "PARTIALLY_IMPLEMENTED"}\n')
    (repo / "binary.bin").write_bytes(b"\x00\xff\x10")
    for name in (".env", ".codex/auth.json", "credentials/runtime.json"):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("must stay outside the export\n")
    before = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")

    runs = ManagedRunStore(tmp_path / "run-store")
    now = datetime.now(UTC)
    runs.save(
        ManagedRunRecord(
            runId=agent_run_id,
            workflowId=f"{identity['workflowId']}:agent:{identity['logicalStepId']}",
            ownerRunId=identity["runId"],
            logicalStepId=identity["logicalStepId"],
            executionOrdinal=identity["executionOrdinal"],
            agentId="codex_cli",
            runtimeId="codex_cli",
            sessionId="replay-session",
            status="completed",
            startedAt=now,
            finishedAt=now,
            workspacePath=str(repo),
        )
    )
    artifacts = _Artifacts(tmp_path / "artifacts")
    activities = TemporalAgentRuntimeActivities(
        run_store=runs, artifact_service=artifacts, client_adapter=object()
    )
    catalog = build_default_activity_catalog()
    capture_catalog = TemporalActivityCatalog(
        activities=tuple(
            item
            for item in catalog.activities
            if item.activity_type == fixture["failedActivityType"]
        ),
        fleets=catalog.fleets,
    )
    binding = next(
        item
        for item in build_activity_bindings(
            capture_catalog,
            agent_runtime_activities=activities,
            fleets=["agent_runtime"],
        )
        if item.activity_type == fixture["failedActivityType"]
    )
    environment = ActivityEnvironment()
    if configured_mode == "true":
        with pytest.raises(ApplicationError) as error:
            await environment.run(binding.handler, request)
        assert error.value.type == fixture["expected"]["strictFailure"]
        assert error.value.non_retryable
        assert not list(artifacts.root.iterdir())
        assert _git(repo, "status", "--porcelain=v1", "--untracked-files=all") == before
        return

    captured = await environment.run(binding.handler, request)
    assert captured["status"] == fixture["expected"]["defaultCapture"]
    saved = artifacts.get_json(captured["savedWorkRef"])
    assert saved["scan"]["disposition"] == fixture["expected"]["defaultScan"]
    assert saved["scan"]["manifestScan"] == "not_scanned"
    assert (
        saved["scan"]["exportScan"]["exportDigest"]
        == captured["workspace"]["archiveDigest"]
    )
    assert saved["scan"]["exportScan"]["coverage"] == "none"
    assert _git(repo, "status", "--porcelain=v1", "--untracked-files=all") == before
    assert await environment.run(binding.handler, request) == captured

    # Cold restore must depend on persisted bytes, never the source process/tree.
    checkpoint_ref = artifacts.put_bytes(
        json.dumps(
            {
                "contentType": STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
                "source": identity,
                "boundary": request["boundary"],
                "workspace": captured["workspace"],
            }
        ).encode(),
        content_type=STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
    ).artifact_ref
    shutil.rmtree(tmp_path / "source")
    runs.delete(agent_run_id)
    del activities, binding, artifacts
    restored_artifacts = _Artifacts(tmp_path / "artifacts")
    restore = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "restored",
        artifact_store=restored_artifacts,
        repository_source_root=origin,
    )
    workspace = captured["workspace"]
    result = await restore.restore(
        {
            "schemaVersion": "v1",
            "recoveryIdentity": {
                **identity,
                "workflowId": "recovery",
                "runId": "recovery-run",
                "executionOrdinal": 2,
            },
            "source": {
                **identity,
                "checkpointRef": checkpoint_ref,
                "checkpointBoundary": request["boundary"],
                "sourceWorkspaceLocator": captured["sourceWorkspaceLocator"],
            },
            "checkpoint": {
                key: workspace[key]
                for key in (
                    "kind",
                    "baseCommit",
                    "archiveRef",
                    "archiveDigest",
                    "manifestRef",
                    "manifestDigest",
                )
            },
            "destination": {
                "runtimeId": "codex_cli",
                "agentRunId": "restored-run",
                "repository": "MoonLadderStudios/MoonMind",
                "relativePath": "repo",
            },
            "workspacePolicy": "restore_pre_execution",
            "resumePhase": "rerun_failed_step",
            "capabilitySetVersion": capabilities.capability_set_version,
            "capabilityDigest": capabilities.capability_digest,
            "idempotencyKey": "replay-restore",
        }
    )
    assert result["status"] == "succeeded"
    destination = tmp_path / "restored/restored-run/repo"
    for name, contents in fixture["files"].items():
        assert (destination / name).read_text() == contents
    assert (
        json.loads((destination / "assessment.json").read_text())["verdict"]
        == "PARTIALLY_IMPLEMENTED"
    )
    assert (destination / "binary.bin").read_bytes() == b"\x00\xff\x10"
    for name in (".env", ".codex", "credentials"):
        assert not (destination / name).exists()
