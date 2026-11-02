"""Run adopts only the actual child's scoped save after its worker is gone."""

from copy import deepcopy
import json

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.temporal_models import StepExecutionIdentityModel
from moonmind.workflows.temporal.step_checkpoints import build_step_checkpoint_payload
from moonmind.workflows.temporal.workflows import run as module
from tests.unit.workflows.temporal.workflows.test_run_step_ledger import (
    _configure_workflow_runtime,
)


@pytest.fixture
def saved_child(monkeypatch):
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(module.workflow, "patched", lambda marker: True)
    owner = module.MoonMindUserWorkflow()
    owner._initialize_step_ledger(
        ordered_nodes=[{"id": "work", "inputs": {"title": "Work"}}],
        dependency_map={"work": []}, updated_at=module.workflow.now(),
    )
    owner._mark_step_running("work", updated_at=module.workflow.now(), summary="Work")
    identity = StepExecutionIdentityModel(
        workflowId="wf-run-1", runId="run-1", logicalStepId="work", executionOrdinal=1,
    )
    request = AgentExecutionRequest(
        agentKind="managed", agentId="codex_cli", correlationId="wf-run-1",
        idempotencyKey="accepted", profileSelector={"tagsAll": ["pool"]},
        stepExecution={
            **identity.model_dump(by_alias=True),
            "stepExecutionId": "wf-run-1:run-1:work:execution:1",
            "runtimeContextPolicy": "fresh_agent_run",
        },
    )
    workspace = {
        "kind": "worktree_archive", "baseCommit": "a" * 40,
        "archiveRef": "artifact://accepted/archive", "archiveDigest": "sha256:" + "b" * 64,
        "manifestRef": "artifact://accepted/manifest", "manifestDigest": "sha256:" + "c" * 64,
        "workspaceDigest": "sha256:" + "d" * 64, "workspaceIdentityDigest": "sha256:" + "e" * 64,
        "includesUntracked": True,
    }
    descriptor = build_step_checkpoint_payload(
        identity=identity, boundary="after_execution",
        task_input_snapshot_ref="artifact://accepted/input", plan_ref="artifact://accepted/plan", workspace=workspace,
        created_at=module.workflow.now(),
    )
    result = AgentRunResult(
        summary="Confirmed result", metadata={
            "workPreserved": True,
            "runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "f" * 64,
            "savedWorkspaceCheckpoint": {**workspace, "checkpointRef": "artifact://accepted/root"},
        },
    )
    reads = []

    async def read(activity, payload, **kwargs):
        assert activity == "artifact.read"
        assert payload.artifact_ref == "artifact://accepted/root"
        reads.append(payload.artifact_ref)
        return json.dumps(descriptor).encode()

    monkeypatch.setattr(module, "execute_typed_activity", read)
    return owner, request, result, descriptor, reads


@pytest.mark.asyncio
async def test_remote_child_save_finalizes_without_recapturing_the_deleted_worker(saved_child, monkeypatch):
    owner, request, result, _, reads = saved_child

    async def no_worker(*args, **kwargs):
        raise AssertionError("the disposable worker is already gone")

    monkeypatch.setattr(owner, "_capture_canonical_step_checkpoint_workspace", no_worker)
    owner._record_primary_execution_outcome(
        "work", execution_result={"status": "COMPLETED"},
        result_status="COMPLETED", recorded_at=module.workflow.now(),
    )
    row = owner._step_ledger_row_for("work")
    primary = deepcopy(row["executionOutcome"])
    await owner._finalize_after_execution_checkpoint(
        "work", updated_at=module.workflow.now(), child_saved_checkpoint=(request, result),
    )
    assert reads == ["artifact://accepted/root"]
    assert row["executionOutcome"] == primary
    assert row["finalizationOutcome"]["checkpointRef"] == "artifact://accepted/root"
    assert row["finalizationOutcome"]["status"] == "succeeded"
    assert row["stepCheckpointRef"] == "artifact://accepted/root"
    assert row["refs"]["checkpointRefsByBoundary"]["after_execution"] == "artifact://accepted/root"
    assert owner._step_checkpoint_refs_by_boundary["work"]["after_execution"] == "artifact://accepted/root"
    owner._carry_interrupted_omnigent_step_workspace("work", next_attempt=2)
    assert owner._interrupted_step_restore_refs[owner._step_execution_launch_block_key("work", 2)] == "artifact://accepted/root"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("workflowId", "foreign-workflow"), ("runId", "foreign-run"),
    ("logicalStepId", "foreign-step"), ("executionOrdinal", 2),
])
async def test_foreign_child_identity_cannot_replace_the_checkpoint(saved_child, field, value):
    owner, request, result, descriptor, _ = saved_child
    descriptor["source"][field] = value
    with pytest.raises(ValueError):
        await owner._adopt_shared_child_saved_checkpoint("work", request, result)
    assert not owner._step_checkpoint_refs
    assert not owner._step_checkpoint_workspace_evidence_by_boundary


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("archiveRef", "artifact://foreign/archive"), ("archiveDigest", "sha256:" + "0" * 64),
    ("manifestRef", "artifact://foreign/manifest"), ("manifestDigest", "sha256:" + "0" * 64),
    ("baseCommit", "0" * 40),
])
async def test_child_save_must_match_its_canonical_workspace(saved_child, field, value):
    owner, request, result, _, _ = saved_child
    result.metadata["savedWorkspaceCheckpoint"][field] = value
    with pytest.raises(ValueError, match="differs from its Step Execution"):
        await owner._adopt_shared_child_saved_checkpoint("work", request, result)
    assert not owner._step_checkpoint_refs


@pytest.mark.asyncio
async def test_artifact_failure_preserves_the_confirmed_primary_outcome(saved_child, monkeypatch):
    owner, request, result, _, _ = saved_child
    owner._step_workspace_capture_inputs["work"] = {"criticality": "recoverability_only"}
    owner._record_primary_execution_outcome(
        "work", execution_result={"status": "COMPLETED"}, result_status="COMPLETED",
        recorded_at=module.workflow.now(),
    )
    row = owner._step_ledger_row_for("work")
    primary = deepcopy(row["executionOutcome"])

    async def unavailable(*args, **kwargs):
        raise PermissionError("checkpoint is outside this workflow's artifact scope")

    monkeypatch.setattr(module, "execute_typed_activity", unavailable)
    await owner._finalize_after_execution_checkpoint(
        "work", updated_at=module.workflow.now(), child_saved_checkpoint=(request, result),
    )
    assert row["executionOutcome"] == primary
    assert row["executionOutcome"]["status"] == "succeeded"
    assert row["finalizationOutcome"]["status"] == "degraded"
    assert row["finalizationOutcome"]["failureCode"] == "FINALIZATION_CHECKPOINT_FAILED"
    assert not owner._step_checkpoint_refs


@pytest.mark.asyncio
async def test_after_execution_save_is_not_relabelled_as_before_publication(saved_child, monkeypatch):
    owner, request, result, _, _ = saved_child
    assert await owner._adopt_shared_child_saved_checkpoint("work", request, result)
    captures = []

    async def capture(*args, **kwargs):
        captures.append(kwargs["boundary"])
        return None

    monkeypatch.setattr(owner, "_capture_canonical_step_checkpoint_workspace", capture)
    assert await owner._record_canonical_step_checkpoint("work", boundary="before_publication", updated_at=module.workflow.now()) is None
    assert captures == ["before_publication"]
