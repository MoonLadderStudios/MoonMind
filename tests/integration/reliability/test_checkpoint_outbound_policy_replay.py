"""Replay c7b23b45 through the worker binding, Git capture, and cold restore."""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from api_service.db.models import Base, TemporalArtifact
from moonmind.config.settings import SecuritySettings, settings
from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.schemas.temporal_models import STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
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


@asynccontextmanager
async def _artifacts(root: Path):
    """Reopen the production metadata and blob stores at each cold boundary."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{root}/artifacts.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            yield TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(root / "artifacts"),
            ), session
    finally:
        await engine.dispose()


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
    access = ArtifactAccessContext.for_execution(identity["workflowId"])
    async with _artifacts(tmp_path) as (artifacts, session):
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
            assert not list((tmp_path / "artifacts").rglob("*"))
            assert (
                await session.scalar(select(func.count()).select_from(TemporalArtifact))
                == 0
            )
            assert (
                _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
                == before
            )
            return

        captured = await environment.run(binding.handler, request)
        assert captured["status"] == fixture["expected"]["defaultCapture"]
        _, saved_bytes = await artifacts.read(
            artifact_id=captured["savedWorkRef"], access=access
        )
        saved = json.loads(saved_bytes)
        for ref in (captured["savedWorkRef"], captured["workspace"]["manifestRef"]):
            assert (
                await artifacts.verified_dependencies(
                    root_artifact_id=ref, access=access
                )
                is not None
            )
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
        checkpoint, _ = await artifacts.create(
            principal="system",
            content_type=STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
            link=ExecutionRef(
                namespace=settings.temporal.namespace,
                workflow_id=identity["workflowId"],
                run_id=identity["runId"],
                link_type="output.checkpoint",
            ),
        )
        checkpoint_ref = checkpoint.artifact_id
        await artifacts.write_complete(
            artifact_id=checkpoint_ref,
            principal="system",
            payload=json.dumps(
                {
                    "contentType": STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
                    "source": identity,
                    "boundary": request["boundary"],
                    "workspace": captured["workspace"],
                }
            ).encode(),
            content_type=STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
        )
        # Retained checkpoints predate the seal; restore backfills under source authority.
        assert (
            await artifacts.verified_dependencies(
                root_artifact_id=checkpoint_ref, access=access
            )
            is None
        )
    shutil.rmtree(tmp_path / "source")
    runs.delete(agent_run_id)
    del activities, binding, artifacts, session
    async with _artifacts(tmp_path) as (restored_artifacts, _session):
        restore = ManagedCheckpointRestoreService(
            authority_root=tmp_path / "restored",
            artifact_service=restored_artifacts,
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
            },
            access=access,
        )
        assert (
            await restored_artifacts.verified_dependencies(
                root_artifact_id=checkpoint_ref, access=access
            )
            is not None
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
