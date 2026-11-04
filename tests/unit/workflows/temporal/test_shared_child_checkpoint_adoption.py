"""Run adopts only the actual child's scoped save after its worker is gone."""

import json
from copy import deepcopy
from types import SimpleNamespace
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


def _plan_binding(digit):
    return {
        "planRef": "omnigent-execution-plan:sha256:" + digit * 64,
        "planDigest": "sha256:" + digit * 64,
        "planArtifactRef": "artifact://accepted/plan-" + digit,
        "taskInputSnapshotRef": "artifact://accepted/input",
        "taskInputSnapshotDigest": "sha256:" + "c" * 64,
    }


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
@pytest.mark.parametrize("current", [False, True])
@pytest.mark.parametrize("specialized", [False, True])
async def test_before_publication_retains_admitted_input_for_the_verifier(
    saved_child, monkeypatch, tmp_path, current, specialized,
):
    from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
    from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError
    from moonmind.schemas.temporal_models import STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE

    owner, _, result, child_descriptor, _ = saved_child
    provenance_patch = "run-omnigent-step-input-provenance-v1"
    monkeypatch.setattr(
        module.workflow, "patched", lambda marker: current or not marker.startswith(provenance_patch + ":"),
    )
    owner._input_ref = "artifact://authored/workflow-input"
    owner._plan_ref = "artifact://authored/workflow-plan"

    root_binding = _plan_binding("a")
    selected_binding = _plan_binding("b" if specialized else "a")
    parameters = {"omnigentExecutionPlan": root_binding}
    if specialized:
        parameters["omnigentStepExecutionPlans"] = {
            "work": {"omnigentExecutionPlan": selected_binding, "resolvedSkillsetRef": "artifact://skills"},
        }
    request = owner._build_agent_execution_request(
        node_inputs={"runtime": {"mode": "omnigent"}},
        node_id="work", tool_name="omnigent", workflow_parameters=parameters,
    )
    assert request.omnigent_execution_plan.plan_artifact_ref == selected_binding["planArtifactRef"]
    child_descriptor.update(
        planRef=selected_binding["planArtifactRef"], planDigest=selected_binding["planDigest"],
    )
    assert await owner._adopt_shared_child_saved_checkpoint("work", request, result)
    # Later workflow admission cannot rewrite the completed attempt's input.
    owner._input_ref = "artifact://later/workflow-input"
    owner._plan_ref = "artifact://later/workflow-plan"
    successor_binding = {
        **_plan_binding("d"), "taskInputSnapshotRef": "artifact://successor/input",
    }
    owner._step_ledger_row_for("work")["attempt"] = 2
    owner._build_agent_execution_request(
        node_inputs={"runtime": {"mode": "omnigent"}},
        node_id="work", tool_name="omnigent", step_execution=2,
        workflow_parameters={"omnigentExecutionPlan": successor_binding},
    )
    owner._step_ledger_row_for("work")["attempt"] = 1
    monkeypatch.setattr(
        owner, "_capture_canonical_step_checkpoint_workspace",
        AsyncMock(return_value={"workspace": child_descriptor["workspace"], "diagnosticRefs": []}),
    )
    created = {}

    async def create_checkpoint(activity, payload, **kwargs):
        assert activity == "step_checkpoint.create"
        created.update(build_step_checkpoint_payload(
            identity=StepExecutionIdentityModel.model_validate(payload["identity"]),
            boundary=payload["boundary"], task_input_snapshot_ref=payload["taskInputSnapshotRef"],
            workspace=payload["workspace"], created_at=module.workflow.now(),
            plan_ref=payload["planRef"], plan_digest=payload["planDigest"],
        ))
        return {
            "checkpointRef": "artifact://publication/checkpoint",
            "checkpointId": created["checkpointId"],
        }

    monkeypatch.setattr(module.workflow, "execute_activity", create_checkpoint)
    await owner._record_canonical_step_checkpoint(
        "work", boundary="before_publication", updated_at=module.workflow.now(),
    )
    body = json.dumps(created).encode()

    class Artifacts:
        async def get_metadata(self, *, artifact_id, access):
            return SimpleNamespace(content_type=STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE, size_bytes=len(body)), [], False, None

        async def read(self, *, artifact_id, access):
            return None, body

    verifier = owner._build_agent_execution_request(
        node_inputs={"runtime": {"mode": "omnigent"}},
        node_id="verify", tool_name="omnigent", workflow_parameters=parameters,
    )
    port = OmnigentWorkspaceMaterializer(
        command_runner=AsyncMock(), workspace_root=tmp_path, artifact_service=Artifacts(),
    )
    source = SimpleNamespace(kind="checkpoint", checkpoint_ref="artifact://publication/checkpoint", artifact_digest=None)
    plan = SimpleNamespace(payload=SimpleNamespace(authority=SimpleNamespace(
        taskInputSnapshotRef=root_binding["taskInputSnapshotRef"],
    )))
    if not current:
        assert created["taskInputSnapshotRef"] == owner._input_ref
        assert created["planRef"] == owner._plan_ref
        assert created["planDigest"] is None
        with pytest.raises(CheckpointRestoreError, match="CHECKPOINT_SOURCE_IDENTITY_MISMATCH"):
            await port._read_step_input_checkpoint(source, verifier, plan)
        return
    restored = await port._read_step_input_checkpoint(source, verifier, plan)
    assert restored.task_input_snapshot_ref == selected_binding["taskInputSnapshotRef"]
    assert restored.plan_ref == selected_binding["planArtifactRef"]
    assert restored.plan_digest == selected_binding["planDigest"]
    context = request.parameters["metadata"]["moonmind"]["executionContext"]
    projection = owner._step_execution_context_projections[("work", 1)]
    for field in ("taskInputSnapshotRef", "planRef", "planDigest"):
        assert context[field] == projection[field] == created[field]
    assert owner._step_execution_context_projections[("work", 2)]["taskInputSnapshotRef"] == successor_binding["taskInputSnapshotRef"]
    evidence = owner._step_checkpoint_workspace_evidence_by_boundary["work"]
    for boundary in ("after_execution", "before_publication"):
        assert evidence[boundary]["archiveDigest"] == child_descriptor["workspace"]["archiveDigest"]
        assert evidence[boundary]["sourceStepExecutionId"] == request.step_execution.step_execution_id


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    None, "git-index", "source", "foreign-input", "not-adopted",
    "interrupted-verifier", "already-correct", "legacy", "missing-digest", "missing-source",
])
async def test_initial_verifier_recovers_only_an_identical_admitted_child_save(
    saved_child, monkeypatch, fault,
):
    owner, request, result, _, _ = saved_child
    assert await owner._adopt_shared_child_saved_checkpoint("work", request, result)
    owner._step_ledger_row_for("work")["status"] = "completed"
    owner._step_ledger_rows.append({"logicalStepId": "verify", "status": "executing"})
    owner._remediation_loop_spec = object()
    owner._remediation_loop_state = SimpleNamespace(
        phase=module.RemediationLoopPhase.INITIAL_VERIFICATION_PENDING,
    )
    owner._remediation_loop_runtime = {"mode": "omnigent"}
    boundaries = owner._step_checkpoint_workspace_evidence_by_boundary["work"]
    saved = boundaries["after_execution"]
    publication_ref = "artifact://publication/checkpoint"
    publication = {
        **saved, "checkpointRef": publication_ref,
        "taskInputSnapshotRef": "artifact://authored/workflow-input",
    }
    publication.pop("sourceChildStepExecutionId")
    boundaries["before_publication"] = publication
    expected = publication_ref
    if fault is None:
        expected = saved["checkpointRef"]
    elif fault == "git-index":
        # Equal file/workspace digests do not prove equal Git or index state.
        publication["archiveDigest"] = "sha256:" + "0" * 64
    elif fault == "source":
        publication["sourceStepExecutionId"] = "wf-run-1:run-1:work:execution:2"
    elif fault == "missing-source":
        publication.pop("sourceStepExecutionId")
    elif fault == "missing-digest":
        publication.pop("archiveDigest")
    elif fault == "foreign-input":
        saved["taskInputSnapshotRef"] = "artifact://foreign/input"
    elif fault == "not-adopted":
        saved.pop("sourceChildStepExecutionId")
    elif fault == "already-correct":
        publication["taskInputSnapshotRef"] = saved["taskInputSnapshotRef"]
    elif fault == "interrupted-verifier":
        expected = "artifact://verifier/own-save"
    monkeypatch.setattr(
        module.workflow, "patched",
        lambda marker: fault != "legacy" or marker != module.RUN_OMNIGENT_VERIFIER_EQUIVALENT_CHECKPOINT_PATCH,
    )
    verifier = AgentExecutionRequest(
        agentKind="external", agentId="omnigent", idempotencyKey="verify",
        correlationId="wf-run-1",
        omnigentExecutionPlan=_plan_binding("a"),
        workspaceSpec={"workspaceCheckpointRestoreRef": expected if fault == "interrupted-verifier" else publication_ref},
    )
    node = {
        "id": "verify", "tool": {"name": "omnigent"},
        "inputs": {"runtime": {"mode": "omnigent"}, "selectedSkill": "moonspec-verify"},
    }
    original_publication = deepcopy(publication)
    assert owner._initial_omnigent_verification_checkpoint_restore_ref(
        node=node, request=verifier,
    ) == expected
    assert publication == original_publication


def test_later_agent_attempt_admits_provenance_after_a_retained_prefix(
    saved_child, monkeypatch,
):
    owner, _, _, _, _ = saved_child
    owner._input_ref = "artifact://authored/workflow-input"
    owner._plan_ref = "artifact://authored/workflow-plan"
    replaying = True
    decisions = {}
    prefix = module.RUN_OMNIGENT_STEP_INPUT_PROVENANCE_PATCH + ":"

    def patched(marker):
        if marker.startswith(prefix):
            # Match Temporal's per-marker memoization across the replay/live edge.
            return decisions.setdefault(marker, not replaying)
        return True

    monkeypatch.setattr(module.workflow, "patched", patched)
    owner._step_execution_context_kwargs("work", attempt=1)
    assert not decisions, "checkpoint previews cannot freeze a future launch patch"
    retained = owner._build_agent_execution_request(
        node_inputs={"runtime": {"mode": "omnigent"}},
        node_id="work", tool_name="omnigent",
        workflow_parameters={"omnigentExecutionPlan": _plan_binding("a")},
    )
    replaying = False
    owner._step_ledger_row_for("work")["attempt"] = 2
    successor = owner._build_agent_execution_request(
        node_inputs={"runtime": {"mode": "omnigent"}},
        node_id="work", tool_name="omnigent", step_execution=2,
        workflow_parameters={"omnigentExecutionPlan": _plan_binding("b")},
    )
    retained_context = retained.parameters["metadata"]["moonmind"]["executionContext"]
    successor_context = successor.parameters["metadata"]["moonmind"]["executionContext"]
    assert retained_context["taskInputSnapshotRef"] == owner._input_ref
    assert "planDigest" not in retained_context
    assert successor_context["taskInputSnapshotRef"] == _plan_binding("b")["taskInputSnapshotRef"]
    assert successor_context["planRef"] == _plan_binding("b")["planArtifactRef"]
    assert decisions == {prefix + "work:1": False, prefix + "work:2": True}
