"""Replay d8540454 through actual checkpoint capture and next-step admission."""

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from api_service.db import models
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from tests.integration.reliability.helpers import load_replay
from tests.support.isolated_postgres import isolated_postgres

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize(
    "source_mode", ["historical", "repository_overlay", "default", "explicit"]
)
async def test_next_step_restores_checkpoint_with_excluded_link_targets(
    tmp_path, monkeypatch, source_mode
):
    fixture = load_replay("omnigent-checkpoint-dangling-links", "manifest.json")
    workflow_id = "checkpoint-link-replay"
    step_id = f"{workflow_id}:run:implement:execution:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    root = tmp_path / "worker"
    workspace = root / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    for name, content in fixture["files"].items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    for name, target in fixture["links"].items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
        assert path.exists()

    def git(*args):
        subprocess.run(
            ["git", "-C", str(workspace), *args], check=True, capture_output=True
        )

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=Replay",
        "-c",
        "user.email=replay@example.invalid",
        "commit",
        "-qm",
        "candidate",
    )
    baseline = tmp_path / "repository-baseline"
    if source_mode == "repository_overlay":
        subprocess.run(
            ["git", "clone", "-q", str(workspace), str(baseline)], check=True
        )
    SandboxWorkspaceRecordStore(root).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": workflow_id,
            "idempotencyKey": "capture",
            "workspaceSpec": {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                }
            },
            "stepExecution": {
                "workflowId": workflow_id,
                "runId": "run",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
                "stepExecutionId": step_id,
                "runtimeContextPolicy": "fresh_agent_run",
            },
        }
    )
    tables = [
        models.TemporalArtifact.__table__,
        models.TemporalArtifactLink.__table__,
        models.TemporalArtifactPin.__table__,
        models.TemporalArtifactUseClaim.__table__,
        models.TemporalArtifactDeletionIntent.__table__,
    ]
    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(tmp_path / "blobs")),
    )
    async with isolated_postgres(tables) as sessions:
        gateway = TemporalOmnigentArtifactGateway(session_factory=sessions)
        saved = await OmnigentWorkspacePublicationService(
            root,
            artifact_gateway=gateway,
        ).save_request_workspace(request)
        archive = await gateway.read_bytes(saved["archiveRef"])
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            assert not any(n.startswith(".agents/skills") for n in bundle.getnames())
            for name, target in fixture["links"].items():
                assert bundle.getmember(name).linkname == target

        # A fresh worker receives only durable evidence and its own step identity.
        shutil.rmtree(root)
        step_id = f"{workflow_id}:run:verify:execution:1"
        workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[
            :24
        ]
        spec = {
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            }
        }
        if source_mode in {"historical", "repository_overlay"}:
            spec["workspaceCheckpointRestoreRef"] = saved["archiveRef"]
            if source_mode == "repository_overlay":
                # Production prepares the authorized repository before applying
                # this historical base-plus-checkpoint request. Recreate that
                # baseline with real Git, independently of the lost worker.
                destination = root / "temporal_sandbox" / workspace_id / "repo"
                destination.parent.mkdir(parents=True)
                subprocess.run(
                    ["git", "clone", "-q", str(baseline), str(destination)], check=True
                )
                (destination / "candidate.txt").write_text("Baseline content\n")
                spec.update(
                    repository="MoonLadderStudios/Tactics", startingBranch="main"
                )
        else:
            spec["workspaceSource"] = {
                "kind": "checkpoint",
                "checkpointRef": saved["archiveRef"],
                "checkpointDigest": saved["archiveDigest"],
                "restoreContract": "moonmind.worktree-archive.v1",
            }
            if source_mode == "explicit":
                spec["overlayPolicy"] = "authoritative_restore"
        next_request = AgentExecutionRequest.model_validate(
            {
                "agentKind": "external",
                "agentId": "omnigent",
                "correlationId": workflow_id,
                "idempotencyKey": step_id,
                "workspaceSpec": spec,
            }
        )

        def materializer():
            return OmnigentWorkspaceMaterializer(
                command_runner=None,
                workspace_root=root,
                artifact_service=TemporalOmnigentArtifactGateway(
                    session_factory=sessions
                ),
            )

        mounted = await materializer().materialize(
            next_request,
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
        restored = Path(mounted["sourceRef"])
        assert (restored / "candidate.txt").read_text() == fixture["files"][
            "candidate.txt"
        ]
        assert (restored / ".agents/skills").exists() == (
            source_mode == "repository_overlay"
        )
        for name, target in fixture["links"].items():
            assert (restored / name).is_symlink()
            assert os.readlink(restored / name) == target
        assert (restored / ".github/skills").exists() == (
            source_mode == "repository_overlay"
        )
        assert (restored / "CLAUDE.md").read_text() == fixture["files"]["AGENTS.md"]
        assert SandboxWorkspaceRecordStore(root).is_materialized(workspace_id)

        # Redelivery reuses the admitted workspace without overwriting new work.
        (restored / "candidate.txt").write_text("Verifier progress\n")
        repeated = await materializer().materialize(
            next_request,
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
        assert repeated == mounted
        assert (restored / "candidate.txt").read_text() == "Verifier progress\n"
