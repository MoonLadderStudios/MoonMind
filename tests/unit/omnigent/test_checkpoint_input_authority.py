"""Saved workflow input must resolve through the admitted product snapshot."""

import dataclasses
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.testing import ActivityEnvironment

from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import OmnigentArtifactError
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError
from moonmind.schemas.temporal_models import (
    STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
    StepExecutionIdentityModel,
)
from moonmind.workflows.executions.repository_contract import RepositoryIdentity
from moonmind.workflows.temporal.activity_runtime import (
    TemporalCheckpointActivities,
    TemporalSandboxActivities,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    TemporalArtifactNotFoundError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from moonmind.workflows.temporal.step_checkpoints import build_step_checkpoint_payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "input_kind,fault",
    [
        ("direct", None),
        ("schedule", None),
        ("draft", None),
        ("schedule", "wrong-input"),
        ("draft", "wrong-input"),
        ("schedule", "metadata-digest"),
        ("schedule", "bytes-digest"),
        ("schedule", "incomplete"),
        ("schedule", "unavailable"),
        ("schedule", "invalid-json"),
        ("schedule", "missing-digest"),
        ("schedule", "missing-definition"),
    ],
)
async def test_saved_step_input_follows_immutable_admitted_snapshot(
    tmp_path, input_kind, fault
):
    """A normal implementation -> verification handoff keeps its saved source."""
    source_identity = StepExecutionIdentityModel(
        workflowId="workflow",
        runId="run",
        logicalStepId="implement",
        executionOrdinal=1,
    )
    saved = build_step_checkpoint_payload(
        identity=source_identity,
        boundary="before_publication",
        task_input_snapshot_ref="artifact:art_workflow_input",
        workspace={"kind": "git_commit", "baseCommit": "a" * 40},
        created_at=datetime.now(UTC),
        plan_ref="artifact:art_workflow_plan",
    )
    saved_body = json.dumps(saved).encode()
    frozen = (
        {
            "source": {"kind": "schedule", "definitionId": "schedule-1"},
            "target": {"inputArtifactRef": "art_workflow_input"},
        }
        if input_kind == "schedule"
        else {
            "source": {"kind": "manual"},
            "draft": {"inputArtifactRef": "artifact://art_workflow_input"},
        }
    )
    if fault == "wrong-input":
        frozen["target" if input_kind == "schedule" else "draft"][
            "inputArtifactRef"
        ] = "artifact:art_other_input"
    if fault == "missing-definition":
        frozen["source"].pop("definitionId")
    frozen_body = (
        b"invalid-json" if fault == "invalid-json" else json.dumps(frozen).encode()
    )
    reads = []

    class Artifacts:
        async def get_metadata(self, *, artifact_id, access):
            assert artifact_id == "art_saved"
            assert access.execution_principal == "workflow:workflow"
            return (
                SimpleNamespace(
                    content_type=STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
                    size_bytes=len(saved_body),
                ),
                [],
                False,
                None,
            )

        async def read(self, *, artifact_id, access):
            reads.append(artifact_id)
            if artifact_id == "art_saved":
                return object(), saved_body
            assert artifact_id == "art_product_input"
            assert (
                access.execution_principal == "workflow:workflow"
                and not access.is_operator
            )
            if fault == "unavailable":
                raise TemporalArtifactNotFoundError("input snapshot unavailable")
            return SimpleNamespace(
                sha256=(
                    "0" * 64
                    if fault == "metadata-digest"
                    else hashlib.sha256(frozen_body).hexdigest()
                ),
                status="pending" if fault == "incomplete" else "complete",
            ), (b"{}" if fault == "bytes-digest" else frozen_body)

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="claude",
        correlationId="workflow",
        idempotencyKey="verification",
        stepExecution={
            "workflowId": "workflow",
            "runId": "run",
            "logicalStepId": "verify",
            "executionOrdinal": 1,
            "runtimeContextPolicy": "fresh_agent_run",
            "stepExecutionId": "workflow:run:verify:execution:1",
        },
        # Mutable metadata cannot replace a different immutable input pointer.
        parameters={
            "metadata": {
                "moonmind": {
                    "executionContext": {
                        "taskInputSnapshotRef": "artifact:art_workflow_input"
                    }
                }
            }
        },
    )
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            authority=SimpleNamespace(
                taskInputSnapshotRef=(
                    "artifact:art_workflow_input"
                    if input_kind == "direct"
                    else "artifact:art_product_input"
                ),
                taskInputSnapshotDigest=(
                    None
                    if fault == "missing-digest"
                    else "sha256:" + hashlib.sha256(frozen_body).hexdigest()
                ),
            )
        )
    )
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=None, workspace_root=tmp_path, artifact_service=Artifacts()
    )

    async def read_checkpoint():
        return await materializer._read_step_input_checkpoint(
            SimpleNamespace(
                kind="checkpoint",
                checkpoint_ref="artifact:art_saved",
                artifact_digest=None,
            ),
            request,
            plan,
        )

    if fault:
        error = (
            CheckpointRestoreError
            if fault in {"wrong-input", "missing-digest", "missing-definition"}
            else (
                TemporalArtifactStateError
                if fault == "incomplete"
                else OmnigentArtifactError
            )
        )
        message = {
            "wrong-input": "not bound",
            "missing-digest": "no digest",
            "missing-definition": "definition authority",
            "metadata-digest": "authority mismatch",
            "bytes-digest": "digest mismatch",
            "incomplete": "not readable",
            "unavailable": "cannot be read",
            "invalid-json": "snapshot is invalid",
        }[fault]
        with pytest.raises(error, match=message):
            await read_checkpoint()
        assert not list(
            tmp_path.iterdir()
        ), "Unverified inputs cannot create a workspace"
        return
    checkpoint = await read_checkpoint()
    assert checkpoint.source == source_identity
    assert checkpoint.task_input_snapshot_ref == "artifact:art_workflow_input"
    assert reads == (
        ["art_saved"] if input_kind == "direct" else ["art_saved", "art_product_input"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("verifier_run_id", ["run-1", "retry-run"])
async def test_scheduled_verifier_restores_the_implementation_save(
    tmp_path, monkeypatch, verifier_run_id
):
    """Replay the scheduled implement -> verify handoff through real artifacts.

    A recurring run's admitted snapshot wraps the schedule's workflow input,
    while the implementation step saves that inner input. The verifier step
    restores the implementation's saved Git candidate into its own sandbox
    through the production capture, checkpoint, and artifact authority, both
    in the original run and after "Retry from failed step" resets it.
    """

    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "blobs")
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    workflow_id = "mm:schedule-1-2026-11-04T04:35:00Z"
    run_id = "run-1"
    implement = StepExecutionIdentityModel(
        workflowId=workflow_id,
        runId=run_id,
        logicalStepId="implement",
        executionOrdinal=1,
    )
    implement_step = f"{workflow_id}:{run_id}:implement:execution:1"
    verify_step = f"{workflow_id}:{verifier_run_id}:verify:execution:1"
    workspaces = tmp_path / "workspaces"

    def sandbox_id(step):
        return hashlib.sha256(f"{workflow_id}:{step}".encode()).hexdigest()[:24]

    def git(path, *args):
        return subprocess.check_output(
            ["git", "-C", str(path), *args], text=True
        ).strip()

    source = workspaces / "temporal_sandbox" / sandbox_id(implement_step) / "repo"
    source.mkdir(parents=True)
    git(source, "init", "-q")
    git(source, "config", "user.email", "test@example.com")
    git(source, "config", "user.name", "Test")
    (source / "tracked.txt").write_text("base\n")
    git(source, "add", ".")
    git(source, "commit", "-q", "-m", "base")
    (source / "tracked.txt").write_text("implemented\n")
    git(source, "commit", "-q", "-am", "implementation")
    head = git(source, "rev-parse", "HEAD")
    # Capture and checkpoint creation link their outputs to the Activity's run.
    activity = ActivityEnvironment()
    activity.info = dataclasses.replace(
        activity.info, workflow_id=workflow_id, workflow_run_id=run_id
    )
    try:
        async with sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session))

            async def operator_input(payload, *, content_type, metadata):
                operator = "00000000-0000-0000-0000-000000000000"
                artifact, _ = await service.create(
                    principal=operator,
                    content_type=content_type,
                    size_bytes=len(payload),
                    metadata_json=metadata,
                    link=ExecutionRef(
                        namespace="default",
                        workflow_id=workflow_id,
                        run_id=run_id,
                        link_type="input.execution_plan",
                    ),
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal=operator,
                    payload=payload,
                    content_type=content_type,
                )
                return artifact.artifact_id

            workflow_input = await operator_input(
                b'{"title": "Scheduled search"}',
                content_type="application/json; charset=utf-8",
                metadata={"label": "Submitted Workflow Input"},
            )
            snapshot_body = json.dumps(
                {
                    "snapshotVersion": 1,
                    "source": {"kind": "schedule", "definitionId": "schedule-1"},
                    "target": {"inputArtifactRef": workflow_input},
                }
            ).encode()
            snapshot = await operator_input(
                snapshot_body,
                content_type="application/json",
                metadata={"artifact_class": "original_task_input_snapshot"},
            )
            capture = await activity.run(
                TemporalSandboxActivities(
                    workspace_root=workspaces, artifact_service=service
                ).workspace_capture_checkpoint,
                {
                    "identity": implement.model_dump(by_alias=True),
                    "boundary": "before_publication",
                    "kind": "worktree_archive",
                    "workspacePath": str(source),
                    "artifactNamespace": "checkpoint",
                    "idempotencyKey": "capture",
                    "includeUntracked": True,
                },
            )
            saved = await activity.run(
                TemporalCheckpointActivities(
                    artifact_service=service
                ).step_checkpoint_create,
                {
                    "identity": implement.model_dump(by_alias=True),
                    "boundary": "before_publication",
                    "taskInputSnapshotRef": workflow_input,
                    "workspace": capture["workspace"],
                    "createdAt": datetime.now(UTC).isoformat(),
                    "planRef": await operator_input(
                        b'{"nodes": []}',
                        content_type="application/json",
                        metadata={"label": "Workflow Plan"},
                    ),
                    "idempotencyKey": "save",
                },
            )

            class RepositoryCredentials:
                async def admitted_repository_identity(self, **kwargs):
                    assert kwargs["role"] == "source_read"
                    return RepositoryIdentity(
                        endpoint="https://github.com",
                        canonicalRemote="https://github.com/acme/repo.git",
                        displayName="acme/repo",
                    )

            attachment = await OmnigentWorkspaceMaterializer(
                command_runner=None,
                workspace_root=workspaces,
                artifact_service=service,
                repository_credential_service=RepositoryCredentials(),
            ).materialize(
                AgentExecutionRequest(
                    agentKind="external",
                    agentId="omnigent",
                    executionProfileRef="codex_openai_oauth",
                    correlationId=workflow_id,
                    idempotencyKey=f"{verify_step}:agent_execute",
                    stepExecution={
                        "workflowId": workflow_id,
                        "runId": verifier_run_id,
                        "logicalStepId": "verify",
                        "executionOrdinal": 1,
                        "stepExecutionId": verify_step,
                        "runtimeContextPolicy": "external_provider_continuation",
                    },
                    workspaceSpec={
                        "repository": "acme/repo",
                        "workspaceCheckpointRestoreRef": "artifact://"
                        + saved["checkpointRef"].removeprefix("artifact:").lstrip("/"),
                        "workspaceLocator": {
                            "kind": "sandbox",
                            "workspaceId": sandbox_id(verify_step),
                            "relativePath": "repo",
                        },
                    },
                ),
                plan=SimpleNamespace(
                    planRef="omnigent-execution-plan:sha256:" + "a" * 64,
                    payload=SimpleNamespace(
                        authority=SimpleNamespace(
                            taskInputSnapshotRef=snapshot,
                            taskInputSnapshotDigest="sha256:"
                            + hashlib.sha256(snapshot_body).hexdigest(),
                        ),
                        resolvedTools={
                            "repositoryAccess": {"source": {"snapshotRef": "source"}}
                        },
                    ),
                ),
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
            )
    finally:
        await engine.dispose()

    restored = workspaces / "temporal_sandbox" / sandbox_id(verify_step) / "repo"
    assert attachment["accessMode"] == "read-write"
    assert git(restored, "rev-parse", "HEAD") == head
    assert (restored / "tracked.txt").read_text() == "implemented\n"
    assert git(restored, "status", "--porcelain") == ""
