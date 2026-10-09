"""Checkpoint capture across the durable artifact and Temporal scope boundary."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.workflows.temporal import activity_runtime
from moonmind.workflows.temporal import artifacts as artifact_module
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalCheckpointActivities,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)


@pytest_asyncio.fixture
async def capture_artifacts(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/capture.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    info = SimpleNamespace(
        namespace="default",
        workflow_id="mm:capture-owner",
        workflow_run_id="capture-run",
        activity_type="step_checkpoint.create_v2",
    )
    monkeypatch.setattr(activity_runtime.temporal_activity, "info", lambda: info)
    # Exercise the production machine-principal policy, independent of the
    # host's disabled-local HTTP authentication setting.
    monkeypatch.setattr(artifact_module, "is_disabled_local_mode", lambda: False)
    async with factory() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )

        async def write(payload, *, workflow_id=info.workflow_id):
            artifact, _ = await service.create(
                principal="service:omnigent-generic-host",
                content_type="application/json",
                link=ExecutionRef(
                    namespace=info.namespace,
                    workflow_id=workflow_id,
                    run_id=info.workflow_run_id,
                    link_type="output.omnigent.capture",
                ),
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="service:omnigent-generic-host",
                payload=payload,
                content_type="application/json",
            )
            return artifact.artifact_id

        yield service, write, info
    await engine.dispose()


async def capture_request(
    write,
    *,
    ref_prefix="artifact:",
    first_digest=None,
    evidence_workflow="mm:capture-owner",
):
    external_bytes = json.dumps(
        {
            "omnigentSessionId": "session-1",
            "firstMessage": {
                "digest": first_digest if first_digest is not None else "1" * 64,
                "responseIdentifiers": {"itemId": "message-1"},
            },
            "lastCommittedBridgeEventCursor": "event-9",
        },
        sort_keys=True,
    ).encode()
    external_id = await write(external_bytes, workflow_id=evidence_workflow)
    manifest_bytes = b'{"status":"complete"}'
    manifest_id = await write(manifest_bytes, workflow_id=evidence_workflow)
    workspace_bytes = b"captured workspace bytes"
    workspace_id = await write(workspace_bytes)
    request = {
        "identity": {
            "workflowId": "mm:capture-owner",
            "runId": "capture-run",
            "logicalStepId": "assess",
            "executionOrdinal": 1,
        },
        "boundary": "after_execution",
        "taskInputSnapshotRef": "art_task_input",
        "planRef": "art_plan",
        "workspace": {
            "kind": "worktree_archive",
            "baseCommit": "a" * 40,
            "headCommit": "a" * 40,
            "archiveRef": workspace_id,
            "manifestRef": manifest_id,
            "archiveDigest": "sha256:" + hashlib.sha256(workspace_bytes).hexdigest(),
            "archiveBytes": len(workspace_bytes),
            "createdAt": "2026-10-09T04:18:00Z",
        },
        "omnigentCheckpointCapture": {
            "providerProfileId": "codex",
            "credentialRef": "credential://profile/codex/3",
            "credentialGeneration": 3,
            "providerLeaseRef": "provider-lease-1",
            "hostBindingRef": "binding-1",
            "hostLeaseRef": "host-lease-1",
            "endpointRef": "endpoint-1",
            "omnigentHostId": "host-1",
            "bridgeSessionId": "bridge-1",
            "executionProfileRef": "profile://codex",
            "launchPolicyRef": "policy://default",
            "idempotencyKey": "capture-1",
            "externalStateRef": ref_prefix + external_id,
            "captureManifestRef": ref_prefix + manifest_id,
            "terminalRef": "artifact:art_terminal",
            "diagnosticsRef": "art_diagnostics",
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": "workspace-1",
                "relativePath": "repo",
            },
            "instructionRefs": ["artifact:art_instructions"],
        },
        "createdAt": "2026-10-09T04:18:00Z",
        "idempotencyKey": "capture:after_execution",
    }
    return request, external_bytes, manifest_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("ref_prefix", ["", "artifact:", "artifact://"])
async def test_durable_capture_keeps_verified_bytes_and_execution_scope(
    capture_artifacts,
    ref_prefix,
):
    service, write, info = capture_artifacts
    request, external_bytes, manifest_bytes = await capture_request(
        write, ref_prefix=ref_prefix
    )
    result = await TemporalCheckpointActivities(
        artifact_service=service
    ).step_checkpoint_create(request)
    artifact_id = result["checkpointRef"].removeprefix("artifact://")
    artifact, checkpoint_bytes = await service.read(
        artifact_id=artifact_id,
        principal="workflow:" + info.workflow_id,
    )
    checkpoint = json.loads(checkpoint_bytes)
    identity = checkpoint["omnigentCheckpoint"]
    assert identity["externalStateRef"].startswith("artifact://art_")
    assert (
        identity["externalStateDigest"]
        == "sha256:" + hashlib.sha256(external_bytes).hexdigest()
    )
    assert (
        identity["captureManifestDigest"]
        == "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    )
    assert identity["firstMessageDigest"] == "sha256:" + "1" * 64
    assert (
        identity["workspaceCheckpointRef"]
        == "artifact://" + request["workspace"]["archiveRef"]
    )
    assert identity["terminalRef"] == "artifact://art_terminal"
    assert identity["diagnosticsRef"] == "artifact://art_diagnostics"
    assert identity["instructionRefs"] == ["artifact://art_instructions"]
    assert identity["validation"]["valid"] is True
    links = await service._repository.list_links(artifact.artifact_id)
    assert [(link.workflow_id, link.run_id) for link in links] == [
        (info.workflow_id, info.workflow_run_id)
    ]
    # The persisted capture remains byte-for-byte unchanged by shape adaptation.
    _, original = await service.read(
        artifact_id=request["omnigentCheckpointCapture"]["externalStateRef"]
        .removeprefix("artifact:")
        .removeprefix("//"),
        principal="workflow:" + info.workflow_id,
    )
    assert original == external_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("first_digest", ["sha256:" + "1" * 64, "not-a-digest"])
async def test_capture_digest_adaptation_does_not_accept_malformed_evidence(
    capture_artifacts,
    first_digest,
):
    service, write, info = capture_artifacts
    request, _, _ = await capture_request(write, first_digest=first_digest)
    result = await TemporalCheckpointActivities(
        artifact_service=service
    ).step_checkpoint_create(request)
    _, raw = await service.read(
        artifact_id=result["checkpointRef"].removeprefix("artifact://"),
        principal="workflow:" + info.workflow_id,
    )
    checkpoint = json.loads(raw)
    if first_digest.startswith("sha256:"):
        assert checkpoint["omnigentCheckpoint"]["firstMessageDigest"] == first_digest
    else:
        assert "omnigentCheckpoint" not in checkpoint
        assert (
            checkpoint["stepOutputs"]["omnigentCheckpointValidation"]["valid"] is False
        )


@pytest.mark.asyncio
async def test_capture_cannot_read_another_workflows_artifacts(capture_artifacts):
    service, write, info = capture_artifacts
    request, _, _ = await capture_request(write, evidence_workflow="mm:other-owner")
    result = await TemporalCheckpointActivities(
        artifact_service=service
    ).step_checkpoint_create(request)
    _, raw = await service.read(
        artifact_id=result["checkpointRef"].removeprefix("artifact://"),
        principal="workflow:" + info.workflow_id,
    )
    checkpoint = json.loads(raw)
    assert "omnigentCheckpoint" not in checkpoint
    assert checkpoint["stepOutputs"]["omnigentCheckpointValidation"]["valid"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["workflowId", "runId"])
async def test_capture_rejects_caller_identity_outside_actual_activity(
    capture_artifacts, field
):
    service, write, _ = capture_artifacts
    request, _, _ = await capture_request(write)
    request["identity"][field] = "other-execution"
    with pytest.raises(TemporalActivityRuntimeError, match="checkpoint.*identity"):
        await TemporalCheckpointActivities(
            artifact_service=service
        ).step_checkpoint_create(request)
