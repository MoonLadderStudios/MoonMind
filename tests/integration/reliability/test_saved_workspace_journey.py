"""Save through the production artifact gateway, then lose the source worker."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile

import pytest

from api_service.db import models
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    S3TemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from tests.support.isolated_postgres import isolated_postgres

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize("backend", ["local_fs", "s3"])
async def test_saved_candidate_is_readable_after_worker_and_workspace_loss(
    tmp_path, monkeypatch, backend
):
    root = tmp_path / "worker"
    workflow_id = "saved-workflow"
    step_id = f"{workflow_id}:source-run:implement:execution:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = root / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(workspace), *args], text=True
        ).strip()

    git("init", "-q")
    git("config", "user.name", "Qualification")
    git("config", "user.email", "qualification@example.invalid")
    (workspace / "file.txt").write_text("original\n")
    (workspace / ".gitignore").write_text("cache/\n")
    git("add", ".")
    git("commit", "-qm", "base")
    # This commit never reaches a remote repository.
    (workspace / "committed.txt").write_text("unpublished work\n")
    git("add", ".")
    git("commit", "-qm", "candidate")
    head = git("rev-parse", "HEAD")
    (workspace / "file.txt").write_text("staged candidate\n")
    git("add", "file.txt")
    (workspace / "file.txt").write_text("validated candidate\n")
    (workspace / "new.bin").write_bytes(b"\x00\xff\x80")
    (workspace / "cache").mkdir()
    (workspace / "cache" / "ignored-escape").symlink_to(
        tmp_path, target_is_directory=True
    )
    SandboxWorkspaceRecordStore(root).ensure(
        SandboxWorkspaceRecord(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            step_execution_id=step_id,
            relative_path="repo",
        )
    )
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": workflow_id,
            "idempotencyKey": "saved-attempt",
            "workspaceSpec": {
                "repository": "MoonLadderStudios/MoonMind",
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
            },
            "stepExecution": {
                "workflowId": workflow_id,
                "runId": "source-run",
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
    blob_root = tmp_path / "durable-blob-volume"

    def build_store():
        if backend == "local_fs":
            return LocalTemporalArtifactStore(blob_root)
        endpoint = os.environ.get("MOONMIND_TEST_MINIO_ENDPOINT")
        assert (
            endpoint
        ), "Start the isolated reliability Compose stack and set MOONMIND_TEST_MINIO_ENDPOINT"
        return S3TemporalArtifactStore(
            endpoint_url=endpoint,
            bucket="reliability-candidates",
            access_key_id="reliability",
            secret_access_key="reliability-test-only",
            region_name="us-east-1",
            use_ssl=False,
        )

    monkeypatch.setattr(
        TemporalArtifactService, "_build_store_from_settings", staticmethod(build_store)
    )
    async with isolated_postgres(tables) as sessions:
        gateway = TemporalOmnigentArtifactGateway(session_factory=sessions)
        from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding

        input_payload = b'{"objective":"restore exact candidate"}'
        plan_payload = b'{"steps":["implement"]}'
        input_ref = await gateway.write_bytes(
            request=request,
            name="input",
            payload=input_payload,
            content_type="application/json",
            link_type="input",
        )
        plan_ref = await gateway.write_bytes(
            request=request,
            name="plan",
            payload=plan_payload,
            content_type="application/json",
            link_type="input",
        )
        plan_digest = hashlib.sha256(plan_payload).hexdigest()
        request = request.model_copy(
            update={
                "step_execution": request.step_execution.model_copy(
                    update={
                        "omnigent_execution_plan": OmnigentExecutionPlanBinding(
                            planRef="omnigent-execution-plan:sha256:" + plan_digest,
                            planDigest="sha256:" + plan_digest,
                            planArtifactRef=plan_ref,
                            taskInputSnapshotRef=input_ref,
                            taskInputSnapshotDigest="sha256:"
                            + hashlib.sha256(input_payload).hexdigest(),
                        ),
                    }
                )
            }
        )
        service = OmnigentWorkspacePublicationService(root, artifact_gateway=gateway)
        evidence = await service.save_request_workspace(request)
        assert evidence["archiveRef"].startswith("artifact://art_")
        from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError

        # A still-present directory has no authority merely by existing. Both
        # worktree corruption and different index bytes with the same MM status
        # must fail before finalization can publish it.
        (workspace / "file.txt").write_text("corrupted candidate\n")
        with pytest.raises(
            CheckpointRestoreError, match="CHECKPOINT_ENTRY_DIGEST_MISMATCH"
        ):
            await service.restore_saved_request_workspace(request, evidence)
        (workspace / "file.txt").write_text("different staged bytes\n")
        git("add", "file.txt")
        (workspace / "file.txt").write_text("validated candidate\n")
        with pytest.raises(
            CheckpointRestoreError, match="CHECKPOINT_ENTRY_DIGEST_MISMATCH"
        ):
            await service.restore_saved_request_workspace(request, evidence)
        (workspace / "file.txt").write_text("staged candidate\n")
        git("add", "file.txt")
        (workspace / "file.txt").write_text("validated candidate\n")
        git(
            "-c",
            "user.name=Qualification",
            "-c",
            "user.email=qualification@example.invalid",
            "commit",
            "--allow-empty",
            "--only",
            "-m",
            "different head",
        )
        with pytest.raises(
            CheckpointRestoreError, match="CHECKPOINT_BASE_COMMIT_MISMATCH"
        ):
            await service.restore_saved_request_workspace(request, evidence)
        git("reset", "--soft", head)
        surviving = await service.restore_saved_request_workspace(request, evidence)
        assert surviving["restorationEvidenceRef"]
        # Source state is gone. Fresh gateway/service instances must recover
        # solely from PostgreSQL and the operator-owned artifact volume.
        shutil.rmtree(root)
        gateway = TemporalOmnigentArtifactGateway(session_factory=sessions)
        replacement = OmnigentWorkspacePublicationService(
            root, artifact_gateway=gateway
        )
        restored_original = await replacement.restore_saved_request_workspace(
            request, evidence
        )
        assert (
            restored_original["destinationWorkspaceLocator"]["workspaceId"]
            == workspace_id
        )
        assert git("rev-parse", "HEAD") == head
        assert (workspace / "committed.txt").read_text() == "unpublished work\n"
        repeated = await replacement.restore_saved_request_workspace(request, evidence)
        assert repeated == restored_original
        assert git("show", ":file.txt") == "staged candidate"
        archive = await gateway.read_bytes(evidence["archiveRef"])
        assert (
            "sha256:" + hashlib.sha256(archive).hexdigest() == evidence["archiveDigest"]
        )
        manifest = json.loads(await gateway.read_bytes(evidence["manifestRef"]))
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            assert files.extractfile("file.txt").read() == b"validated candidate\n"
            assert files.extractfile("new.bin").read() == b"\x00\xff\x80"
            assert not any(name.startswith("cache/") for name in files.getnames())
        assert manifest["archiveDigest"] == evidence["archiveDigest"]
        from moonmind.workflows.temporal.runtime.checkpoint_restore import (
            ManagedCheckpointRestoreService,
        )

        destination_id = hashlib.sha256(b"recovery:restore-step").hexdigest()[:24]
        SandboxWorkspaceRecordStore(root).ensure(
            SandboxWorkspaceRecord(destination_id, "recovery", "restore-step", "repo")
        )
        restore_request = {
            "schemaVersion": "v1",
            "recoveryIdentity": {
                "workflowId": "recovery",
                "runId": "recovery-run",
                "logicalStepId": "verify",
                "executionOrdinal": 0,
            },
            "source": {
                "workflowId": workflow_id,
                "runId": "source-run",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
                "checkpointRef": evidence["checkpointRef"],
                "checkpointBoundary": "after_execution",
            },
            "checkpoint": {
                key: evidence[key]
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
                "kind": "sandbox",
                "workspaceId": destination_id,
                "stepExecutionId": "restore-step",
                "repository": "MoonLadderStudios/MoonMind",
            },
            "workspacePolicy": "restore_publication_candidate",
            "resumePhase": "resume_publication",
            "capabilitySetVersion": "fixture-v1",
            "capabilityDigest": "sha256:fixture",
            "idempotencyKey": "restore-candidate",
        }
        async with sessions() as session:
            restore = ManagedCheckpointRestoreService(
                authority_root=root,
                artifact_service=TemporalArtifactService(
                    TemporalArtifactRepository(session)
                ),
                repository_source_root=tmp_path / "absent-source",
            )
            result = await restore.restore(
                restore_request, admitted_principal="service:omnigent-generic-host"
            )
        restored = root / "temporal_sandbox" / destination_id / "repo"
        restored_head = subprocess.check_output(
            ["git", "-C", str(restored), "rev-parse", "HEAD"], text=True
        ).strip()
        assert restored_head == head
        assert (restored / "file.txt").read_text() == "validated candidate\n"
        restored_index = subprocess.check_output(
            ["git", "-C", str(restored), "show", ":file.txt"], text=True
        )
        assert restored_index == "staged candidate\n"
        assert (restored / "committed.txt").read_text() == "unpublished work\n"
        assert result["restorationEvidenceRef"].startswith("art_")
