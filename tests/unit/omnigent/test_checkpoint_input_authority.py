"""Saved workflow input must resolve through the admitted product snapshot."""

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from moonmind.omnigent.bridge_artifacts import OmnigentArtifactError
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError
from moonmind.schemas.temporal_models import (
    STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
    StepExecutionIdentityModel,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactNotFoundError,
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
