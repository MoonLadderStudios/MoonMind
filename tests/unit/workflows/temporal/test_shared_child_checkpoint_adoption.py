"""Run adopts only the actual child's scoped save after its worker is gone."""

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.temporal_models import StepExecutionIdentityModel
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    TemporalArtifactActivities,
    TemporalArtifactNotFoundError,
)
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
    retry_safe = await owner._finalize_after_execution_checkpoint(
        "work", updated_at=module.workflow.now(), child_saved_checkpoint=(request, result),
    )
    assert retry_safe is True
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
@pytest.mark.parametrize("reference", ["art_child_save", "artifact://art_child_save"])
async def test_child_checkpoint_read_uses_activity_id_and_retains_successor_uri(
    saved_child,
    monkeypatch,
    reference,
):
    owner, request, result, descriptor, _ = saved_child
    result.metadata["savedWorkspaceCheckpoint"]["checkpointRef"] = reference

    async def read_saved_descriptor(*, artifact_id, access):
        if artifact_id != "art_child_save":
            raise TemporalArtifactNotFoundError(artifact_id)
        return None, json.dumps(descriptor).encode()

    service = AsyncMock()
    service.read.side_effect = read_saved_descriptor
    activities = TemporalArtifactActivities(service)
    environment = ActivityEnvironment()

    async def read_activity(activity, payload, **kwargs):
        assert activity == "artifact.read"
        return await environment.run(activities.artifact_read, payload)

    monkeypatch.setattr(module, "execute_typed_activity", read_activity)
    assert await owner._adopt_shared_child_saved_checkpoint("work", request, result)
    service.read.assert_awaited_once_with(
        artifact_id="art_child_save",
        access=ArtifactAccessContext.for_execution(environment.info.workflow_id),
    )
    assert owner._step_checkpoint_refs["work"] == "artifact://art_child_save"
    owner._carry_interrupted_omnigent_step_workspace("work", next_attempt=2)
    assert owner._interrupted_step_restore_refs[
        owner._step_execution_launch_block_key("work", 2)
    ] == "artifact://art_child_save"


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
@pytest.mark.parametrize("binding", [
    {},
    {"parentWorkflowId": "foreign", "parentRunId": "run-1"},
    {"parentWorkflowId": "wf-run-1", "parentRunId": "run-1", "parentTemporalRunId": "conflict"},
])
async def test_reset_replay_requires_consistent_recorded_parent_authority(
    saved_child, monkeypatch, binding,
):
    owner, request, result, _, _ = saved_child
    monkeypatch.setattr(owner, "_workflow_is_replaying", lambda: True)
    result.metadata.update(binding)
    request.step_execution = request.step_execution.model_copy(update={
        "run_id": "reset-run",
        "step_execution_id": "wf-run-1:reset-run:work:execution:1",
    })
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
    retry_safe = await owner._finalize_after_execution_checkpoint(
        "work", updated_at=module.workflow.now(), child_saved_checkpoint=(request, result),
    )
    assert row["executionOutcome"] == primary
    assert row["executionOutcome"]["status"] == "succeeded"
    assert row["finalizationOutcome"]["status"] == "degraded"
    assert row["finalizationOutcome"]["failureCode"] == "FINALIZATION_CHECKPOINT_FAILED"
    assert retry_safe is False
    assert "artifact://accepted/root" in row["finalizationOutcome"]["message"]
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


@pytest.mark.asyncio
@pytest.mark.parametrize("replaying", [True, False])
@pytest.mark.parametrize("recorded_run_id", ["run-1", "first-reset-run"])
async def test_reset_replay_keeps_the_recorded_child_save_identity(
    saved_child, monkeypatch, replaying, recorded_run_id,
):
    owner, request, result, descriptor, _ = saved_child
    owner._resilience_policy_run_id = "run-1"
    descriptor["source"]["runId"] = recorded_run_id
    descriptor["checkpointId"] = descriptor["checkpointId"].replace(":run-1:", f":{recorded_run_id}:")
    result.metadata.update(
        parentWorkflowId="wf-run-1", parentTemporalRunId=recorded_run_id,
        parentRunId=recorded_run_id,
    )
    monkeypatch.setattr(owner, "_workflow_is_replaying", lambda: replaying)
    info = module.workflow.info()
    info.run_id = "reset-run"
    request.step_execution = request.step_execution.model_copy(update={
        "run_id": "reset-run",
        "step_execution_id": "wf-run-1:reset-run:work:execution:1",
    })

    if not replaying:
        with pytest.raises(ValueError, match="differs from its Step Execution"):
            await owner._adopt_shared_child_saved_checkpoint("work", request, result)
        assert not owner._step_checkpoint_refs
        return

    assert await owner._adopt_shared_child_saved_checkpoint("work", request, result)
    assert descriptor["source"]["runId"] == recorded_run_id
    assert owner._step_checkpoint_workspace_evidence_by_boundary["work"][
        "after_execution"
    ]["sourceChildStepExecutionId"] == f"wf-run-1:{recorded_run_id}:work:execution:1"

    async def no_recapture(*args, **kwargs):
        raise AssertionError("the recorded child worker no longer exists")

    monkeypatch.setattr(owner, "_capture_canonical_step_checkpoint_workspace", no_recapture)
    assert await owner._record_canonical_step_checkpoint(
        "work", boundary="after_execution", updated_at=module.workflow.now(),
    ) == "artifact://accepted/root"

    # Reset replay can only recover the run recorded by the child,
    # never a different workflow, step, attempt, or arbitrary prior run.
    descriptor["source"]["runId"] = "foreign-run"
    descriptor["checkpointId"] = descriptor["checkpointId"].replace(f":{recorded_run_id}:", ":foreign-run:")
    with pytest.raises(ValueError, match="differs from its Step Execution"):
        await owner._adopt_shared_child_saved_checkpoint("work", request, result)
