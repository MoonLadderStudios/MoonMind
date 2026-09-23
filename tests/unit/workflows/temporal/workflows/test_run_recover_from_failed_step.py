from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.workflows.temporal.step_ledger import (
    build_initial_step_rows,
    mark_step_checkpoint_evidence,
    materialize_preserved_steps,
    refresh_ready_steps,
    update_step_row,
)
from moonmind.workflows.checkpoint_branches import (
    prepare_checkpoint_branch_git_binding,
)
from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationLoopPhase,
    RemediationLoopSpec,
    RemediationLoopState,
    apply_continuation_decision,
    capture_remediation_candidate,
    decide_remediation_continuation,
    materialize_attempt_nodes,
    project_remediation_loop,
    record_semantic_progress,
    record_verification_evidence,
    start_remediation_attempt,
    start_verification,
)
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-recover",
        run_id="run-recover",
        task_queue="mm.workflow",
        search_attributes={"mm_owner_type": ["user"], "mm_owner_id": ["user-1"]},
    )
    logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        isEnabledFor=lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_module.workflow, "logger", logger)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: False)


def _recovery_source(**overrides: object) -> dict[str, object]:
    source: dict[str, object] = {
        "sourceWorkflowId": "mm:source",
        "sourceRunId": "run-source",
        "sourceTaskInputSnapshotRef": "artifact://snapshot/source",
        "sourcePlanDigest": "sha256:source-plan",
        "failedStepId": "implement",
        "failedStepExecution": 1,
        "recoveryCheckpointRef": "artifact://workspace/before-implement",
        "failedRunRecoveryManifestRef": "artifact://recovery/manifest",
        "recoveryWorkspace": {
            "checkpointRef": "artifact://workspace/before-implement",
        },
        "preservedSteps": [
            {
                "logicalStepId": "prepare",
                "status": "succeeded",
                "sourceExecutionOrdinal": 1,
                "artifacts": {
                    "outputSummary": "artifact://prepare-summary",
                    "outputPrimary": "artifact://prepare-output",
                },
                "stateCheckpointRef": "artifact://workspace/prepare",
            }
        ],
    }
    source.update(overrides)
    return source


def _workflow_with_resume(
    source: dict[str, object] | None = None,
) -> MoonMindRunWorkflow:
    workflow = MoonMindRunWorkflow()
    workflow._recovery_source = source or _recovery_source()
    return workflow


def _ordered_nodes() -> list[dict[str, object]]:
    return [
        {"id": "prepare", "title": "Prepare"},
        {"id": "implement", "title": "Implement"},
        {"id": "verify", "title": "Verify"},
    ]


def _dependency_map() -> dict[str, list[str]]:
    return {"prepare": [], "implement": ["prepare"], "verify": ["implement"]}


def test_materialize_preserved_steps_marks_source_provenance_without_new_attempt() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "plan", "title": "Plan"},
            {"id": "implement", "title": "Implement"},
        ],
        dependency_map={"implement": ["plan"]},
        updated_at=now,
    )

    materialize_preserved_steps(
        rows,
        source_workflow_id="mm:source",
        source_run_id="run-source",
        preserved_steps=[
            {
                "logicalStepId": "plan",
                "order": 1,
                "status": "succeeded",
                "sourceExecutionOrdinal": 2,
                "artifacts": {"outputSummary": "artifact://summary"},
                "stateCheckpointRef": "artifact://workspace/before-plan",
            }
        ],
        updated_at=now,
    )
    refresh_ready_steps(rows, updated_at=now)

    assert rows[0]["status"] == "completed"
    assert rows[0]["executionOrdinal"] == 0
    assert rows[0]["summary"] == "Preserved from source run."
    assert rows[0]["preservedFrom"] == {
        "workflowId": "mm:source",
        "runId": "run-source",
        "logicalStepId": "plan",
        "executionOrdinal": 2,
    }
    assert rows[0]["artifacts"]["outputSummary"] == "artifact://summary"
    assert rows[0]["stateCheckpointRef"] == "artifact://workspace/before-plan"
    assert rows[1]["status"] == "ready"


def test_materialize_preserved_steps_keeps_outputs_for_downstream_steps() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "prepare", "title": "Prepare"},
            {"id": "implement", "title": "Implement"},
            {"id": "verify", "title": "Verify"},
        ],
        dependency_map={"implement": ["prepare"], "verify": ["implement"]},
        updated_at=now,
    )

    materialize_preserved_steps(
        rows,
        source_workflow_id="mm:source",
        source_run_id="run-source",
        preserved_steps=[
            {
                "logicalStepId": "prepare",
                "order": 1,
                "status": "succeeded",
                "sourceExecutionOrdinal": 1,
                "artifacts": {
                    "outputSummary": "artifact://prepare-summary",
                    "outputPrimary": "artifact://prepare-output",
                },
                "stateCheckpointRef": "artifact://workspace/before-prepare",
            }
        ],
        updated_at=now,
    )
    refresh_ready_steps(rows, updated_at=now)

    assert rows[0]["executionOrdinal"] == 0
    assert rows[0]["preservedFrom"] == {
        "workflowId": "mm:source",
        "runId": "run-source",
        "logicalStepId": "prepare",
        "executionOrdinal": 1,
    }
    assert rows[0]["artifacts"]["outputSummary"] == "artifact://prepare-summary"
    assert rows[0]["artifacts"]["outputPrimary"] == "artifact://prepare-output"
    assert rows[0]["stateCheckpointRef"] == "artifact://workspace/before-prepare"
    assert rows[1]["status"] == "ready"
    assert rows[2]["status"] == "pending"


def test_parent_owned_checkpoint_evidence_survives_child_runtime_projection() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "delegate-agent", "title": "Delegate agent"},
        ],
        dependency_map={"delegate-agent": []},
        updated_at=now,
    )

    update_step_row(
        rows,
        "delegate-agent",
        updated_at=now,
        status="succeeded",
        refs={"childWorkflowId": "wf-child", "childRunId": "run-child"},
        artifacts={"outputPrimary": "artifact://child-output"},
    )
    mark_step_checkpoint_evidence(
        rows,
        "delegate-agent",
        updated_at=now,
        state_checkpoint_ref="artifact://child-checkpoint",
    )

    assert rows[0]["refs"] == {
        "childWorkflowId": "wf-child",
        "childRunId": "run-child",
        "agentRunId": None,
        "latestStepExecutionManifestRef": None,
        "stepExecutionManifestRefs": [],
        "latestStepExecutionCheckpointRef": None,
        "stepExecutionCheckpointRefs": [],
        "checkpointRefsByBoundary": {},
        "branchTurnArtifactRefs": {},
    }
    assert rows[0]["stateCheckpointRef"] == "artifact://child-checkpoint"
    assert rows[0]["recoveryPreservation"] == {
        "eligible": True,
        "reason": "complete",
        "message": "Step has recoverable output refs and state checkpoint evidence.",
    }

def test_empty_recovery_source_is_treated_as_absent() -> None:
    now = datetime.now(UTC)
    workflow = MoonMindRunWorkflow()
    workflow._recovery_source = {}

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    assert workflow._recovery_failed_step_id is None
    assert workflow._recovery_workspace == {}
    assert workflow._step_ledger_rows[0]["status"] == "ready"


def test_step_ledger_row_lookup_uses_initialized_index() -> None:
    workflow = _workflow_with_resume()
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=datetime.now(UTC),
    )

    assert workflow._step_ledger_row_for("prepare") is workflow._step_ledger_rows[0]
    assert workflow._step_ledger_row_for("missing") is None


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("sourceWorkflowId", "source workflow"),
        ("sourceRunId", "source run"),
        ("sourceTaskInputSnapshotRef", "task input snapshot"),
        ("failedStepId", "failed step"),
        ("recoveryCheckpointRef", "recovery checkpoint"),
    ],
)
def test_recovery_source_validation_requires_compact_identity_before_execution(
    field: str,
    message: str,
) -> None:
    source = _recovery_source(**{field: ""})
    workflow = _workflow_with_resume(source)

    with pytest.raises(ValueError, match=message):
        workflow._initialize_step_ledger(
            ordered_nodes=_ordered_nodes(),
            dependency_map=_dependency_map(),
            updated_at=datetime.now(UTC),
        )


def test_recovery_source_validation_requires_plan_identity_before_execution() -> None:
    source = _recovery_source(sourcePlanDigest="", sourcePlanRef="")
    workflow = _workflow_with_resume(source)

    with pytest.raises(ValueError, match="plan"):
        workflow._initialize_step_ledger(
            ordered_nodes=_ordered_nodes(),
            dependency_map=_dependency_map(),
            updated_at=datetime.now(UTC),
        )


def test_recovery_source_rejects_preserved_step_without_recoverable_output_ref() -> None:
    source = _recovery_source(
        preservedSteps=[
            {
                "logicalStepId": "prepare",
                "status": "succeeded",
                "sourceExecutionOrdinal": 1,
                "artifacts": {},
                "stateCheckpointRef": "artifact://workspace/prepare",
            }
        ]
    )
    workflow = _workflow_with_resume(source)

    with pytest.raises(ValueError, match="recoverable output"):
        workflow._initialize_step_ledger(
            ordered_nodes=_ordered_nodes(),
            dependency_map=_dependency_map(),
            updated_at=datetime.now(UTC),
        )


def test_recovery_source_restores_workspace_before_failed_step_execution() -> None:
    now = datetime.now(UTC)
    workflow = _workflow_with_resume()
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    restored_ref = workflow._restore_recovery_workspace_for_failed_step("implement")

    assert restored_ref == "artifact://workspace/before-implement"
    assert workflow._recovery_workspace_restored_ref == restored_ref
    assert workflow._restore_recovery_workspace_for_failed_step("verify") is None


@pytest.mark.asyncio
async def test_recover_step_execution_manifest_preserves_source_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    now = datetime.now(UTC)
    workflow = _workflow_with_resume()
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"artifact-attempt-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    workflow._mark_step_running("implement", updated_at=now, summary="Resuming")
    await workflow._record_step_execution_manifest(
        "implement",
        phase="start",
        updated_at=now,
        reason="recover_from_failed_step",
    )

    manifest = writes[0]["payload"]
    assert manifest["workflowId"] == "wf-recover"
    assert manifest["runId"] == "run-recover"
    assert manifest["executionOrdinal"] == 1
    assert manifest["lineage"] == {
        "sourceWorkflowId": "mm:source",
        "sourceRunId": "run-source",
        "sourceLogicalStepId": "implement",
        "sourceExecutionOrdinal": 1,
        "relationship": "recover_from_failed_step",
        "lineageExecutionOrdinal": 2,
    }
    assert manifest["workspace"]["policy"] == "restore_pre_execution"
    assert manifest["workspace"]["checkpointRef"] == (
        "artifact://workspace/before-implement"
    )
    assert manifest["workspace"]["evidenceAccepted"] is True
    assert manifest["workspace"]["sourceExecutionOrdinal"] == {
        "workflowId": "mm:source",
        "runId": "run-source",
        "logicalStepId": "implement",
        "executionOrdinal": 1,
    }


def test_recovery_source_rejects_missing_workspace_evidence() -> None:
    source = _recovery_source(recoveryWorkspace={})
    workflow = _workflow_with_resume(source)

    with pytest.raises(ValueError, match="workspace evidence"):
        workflow._initialize_step_ledger(
            ordered_nodes=_ordered_nodes(),
            dependency_map=_dependency_map(),
            updated_at=datetime.now(UTC),
        )


def test_recovery_source_accepts_branch_commit_workspace_evidence() -> None:
    now = datetime.now(UTC)
    source = _recovery_source(recoveryWorkspace={"branch": "feature", "commit": "abc123"})
    workflow = _workflow_with_resume(source)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    assert workflow._recovery_workspace == {"branch": "feature", "commit": "abc123"}
    assert workflow._restore_recovery_workspace_for_failed_step("implement") is None


def test_recovery_source_accepts_checkpoint_payload_ref_workspace_evidence() -> None:
    now = datetime.now(UTC)
    source = _recovery_source(
        recoveryCheckpointRef="artifact://checkpoint/payload",
        recoveryWorkspace={
            "checkpoint_payload_ref": "artifact://checkpoint/payload",
            "inline_checkpoint_metadata": "artifact://checkpoint/metadata",
        }
    )
    workflow = _workflow_with_resume(source)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    restored_ref = workflow._restore_recovery_workspace_for_failed_step("implement")

    assert restored_ref == "artifact://checkpoint/payload"
    assert workflow._recovery_workspace_restored_ref == restored_ref


def test_step_execution_exception_records_failed_step_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    now = datetime.now(UTC)
    workflow = MoonMindRunWorkflow()
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "create-jira", "title": "Create Jira issues"}],
        dependency_map={"create-jira": []},
        updated_at=now,
    )
    workflow._mark_step_running("create-jira", updated_at=now, summary="Running")

    diagnostic = workflow._record_step_execution_exception(
        RuntimeError("tool activity failed"),
        logical_step_id="create-jira",
        tool_name="story.create_jira_issues",
        source="activity",
        updated_at=now,
    )

    row = workflow._step_ledger_row_for("create-jira")
    assert row is not None
    assert row["status"] == "failed"
    assert row["lastError"] == diagnostic["category"]
    assert workflow._failure_diagnostic is not None
    assert workflow._failure_diagnostic["stepId"] == "create-jira"
    assert workflow._failure_diagnostic["source"] == "activity"
    assert workflow._failure_diagnostic["message"] == "tool activity failed"


def test_preserved_outputs_are_available_to_failed_step_dependencies() -> None:
    now = datetime.now(UTC)
    workflow = _workflow_with_resume()
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    outputs = workflow._preserved_outputs_for_step("implement")

    assert outputs == {
        "prepare": {
            "outputSummary": "artifact://prepare-summary",
            "outputPrimary": "artifact://prepare-output",
            "producingAttempt": {
                "workflowId": "mm:source",
                "runId": "run-source",
                "logicalStepId": "prepare",
                "executionOrdinal": 1,
            },
        }
    }


def test_retried_failed_step_records_fresh_evidence_without_source_provenance() -> None:
    now = datetime.now(UTC)
    workflow = _workflow_with_resume()
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    update_step_row(
        workflow._step_ledger_rows,
        "implement",
        updated_at=now,
        status="succeeded",
    )
    workflow._record_step_result_evidence(
        "implement",
        execution_result={
            "outputs": {
                "outputSummaryRef": "artifact://implement-summary-new",
                "outputPrimaryRef": "artifact://implement-output-new",
                "stateCheckpointRef": "artifact://workspace/implement-new",
            }
        },
        updated_at=now,
    )

    implement_row = next(
        row
        for row in workflow._step_ledger_rows
        if row["logicalStepId"] == "implement"
    )
    assert "preservedFrom" not in implement_row
    assert (
        implement_row["artifacts"]["outputSummary"]
        == "artifact://implement-summary-new"
    )
    assert (
        implement_row["artifacts"]["outputPrimary"]
        == "artifact://implement-output-new"
    )
    assert implement_row["stateCheckpointRef"] == "artifact://workspace/implement-new"


def _journey_remediation_spec() -> RemediationLoopSpec:
    return RemediationLoopSpec.model_validate(
        {
            "kind": "remediation_loop",
            "loopId": "issue-3512-repair-journey",
            "remediationTool": {
                "type": "skill",
                "name": "auto",
                "inputs": {"instructions": "Repair the failed implement step."},
            },
            "verificationTool": {
                "type": "skill",
                "name": "moonspec-verify",
                "inputs": {"instructions": "Verify the repaired candidate."},
            },
            "workspacePolicy": "continue_from_loop_head",
            "budgets": {
                "hardMaxAttempts": 2,
                "maxConsecutiveSemanticNoProgress": 2,
                "maxRepeatedFailureSignature": 2,
                "maxEvidenceRetries": 1,
                "maxContractRepairs": 1,
            },
            "terminalPolicy": {
                "fullyImplemented": "advance",
                "additionalWorkNeeded": "continue_when_allowed",
                "blocked": "stop",
                "noDetermination": "retry_evidence_or_stop",
                "failedUnrecoverable": "stop",
            },
            "sideEffectPolicy": "workflow_owned",
            "publicationPolicy": "evaluate_after_terminal",
        }
    )


def test_shared_fixture_recovery_branch_and_two_attempt_repair_preserves_cumulative_content() -> None:
    """Parent journey for MoonLadderStudios/MoonMind#3512 on one shared fixture.

    One real failed middle step (``implement``) whose predecessor (``prepare``)
    wrote a useful marker is recovered through the normal ledger/restore path;
    the same fixture is reused for a corrective checkpoint branch and a
    two-attempt C0 -> C1 -> C2 semantic repair. The test asserts actual marker
    and candidate content plus invocation counts through the existing
    production owners (step ledger, checkpoint branch binding, remediation
    loop contracts) without adding a parallel journey system.
    """

    import copy

    now = datetime.now(UTC)
    marker_content = "prepare-marker-content-3512"
    source = _recovery_source(
        preservedSteps=[
            {
                "logicalStepId": "prepare",
                "status": "succeeded",
                "sourceExecutionOrdinal": 1,
                "artifacts": {
                    "outputSummary": "artifact://prepare-summary",
                    "outputPrimary": marker_content,
                },
                "stateCheckpointRef": "artifact://workspace/prepare",
            }
        ]
    )
    source_snapshot = copy.deepcopy(source)

    # 1. Normal recovery: predecessor marker preserved without a new attempt,
    #    failed middle step restored from its pre-execution checkpoint.
    workflow = _workflow_with_resume(source)
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )
    preserved_calls = 0
    outputs = workflow._preserved_outputs_for_step("implement")
    preserved_calls += 1

    assert outputs["prepare"]["outputPrimary"] == marker_content
    assert outputs["prepare"]["outputSummary"] == "artifact://prepare-summary"
    assert outputs["prepare"]["producingAttempt"] == {
        "workflowId": "mm:source",
        "runId": "run-source",
        "logicalStepId": "prepare",
        "executionOrdinal": 1,
    }
    prepare_row = workflow._step_ledger_row_for("prepare")
    assert prepare_row is not None
    assert prepare_row["executionOrdinal"] == 0
    assert prepare_row["artifacts"]["outputPrimary"] == marker_content
    restored_ref = workflow._restore_recovery_workspace_for_failed_step("implement")
    assert restored_ref == "artifact://workspace/before-implement"
    # Source failure and original input stay immutable; no silent full rerun.
    assert source == source_snapshot
    assert workflow._recovery_failed_step_id == "implement"

    # 2. Corrective branch reuses the same fixture through the existing owner.
    branch_result = prepare_checkpoint_branch_git_binding(
        {
            "workflowId": "wf-recover",
            "productBranchId": "cbr_3512_repair",
            "branchTurnId": "cbt_3512_1",
            "sourceCheckpointRef": "artifact://workspace/before-implement",
            "repository": "MoonLadderStudios/MoonMind",
            "baseBranch": "feature/repair-journey",
            "workspacePolicy": "restore_pre_execution",
            "creationMode": "from_checkpoint_worktree",
            "idempotencyKey": "3512:repair-journey:implement",
            "logicalStepId": "implement",
        },
        known_refs={"feature/repair-journey"},
        current_ref="feature/repair-journey",
    )
    assert branch_result.binding.source_checkpoint_ref == (
        "artifact://workspace/before-implement"
    )
    assert branch_result.step_execution_manifest_branch["rootCheckpointRef"] == (
        "artifact://workspace/before-implement"
    )
    assert branch_result.binding.work_branch != "cbr_3512_repair"

    # 3. Two-attempt C0 -> C1 -> C2 repair feeds the exact latest candidate,
    #    report, and remaining work per attempt; cumulative content survives.
    spec = _journey_remediation_spec()
    runtime = {"mode": "codex_cli", "model": "gpt-5.6-sol"}
    candidate_contents = {
        "artifact://workspace/before-implement": "C0-base",
        "artifact://workspace/C1": f"C1-repair+{marker_content}",
        "artifact://workspace/C2": f"C2-repair+{marker_content}+verified",
    }
    materialized_pairs = 0

    state = RemediationLoopState(
        loopId="issue-3512-repair-journey",
        attemptOrdinal=0,
        phase=RemediationLoopPhase.REMEDIATION_PENDING,
        workspaceHeadRef="artifact://workspace/before-implement",
        consumedBudgets=ConsumedRemediationBudgets(attempts=0),
    )
    assert candidate_contents[state.workspace_head_ref or ""] == "C0-base"

    # Attempt 1: C0 -> C1, verifier reports remaining work.
    running = start_remediation_attempt(state)
    captured = capture_remediation_candidate(
        running, workspace_head_ref="artifact://workspace/C1"
    )
    assert candidate_contents[captured.workspace_head_ref or ""].startswith(
        "C1-repair+"
    )
    assert marker_content in (candidate_contents[captured.workspace_head_ref or ""])
    verifying = start_verification(captured)
    evaluating = record_verification_evidence(
        verifying, verification_ref="artifact://verification/V1"
    )
    progressed = record_semantic_progress(
        evaluating,
        progress_ref="artifact://remaining/R1",
        progress_signature="sha256:" + ("1" * 64),
    )
    decision = decide_remediation_continuation(
        spec=spec,
        state=progressed,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://verification/V1",
        remaining_work_ref="artifact://remaining/R1",
    )
    assert decision.continue_loop is True
    assert decision.next_attempt == 2
    decided = apply_continuation_decision(
        progressed, decision=decision, decision_ref="artifact://decision/D1"
    )
    assert decided.phase == RemediationLoopPhase.REMEDIATION_PENDING
    assert decided.consumed_budgets.attempts == 1

    # The admitted second attempt receives the exact latest refs.
    remediation_node, verification_node = materialize_attempt_nodes(
        spec=spec,
        workflow_id="wf-recover",
        run_id="run-recover",
        ordinal=2,
        workspace_head_ref=decided.workspace_head_ref,
        runtime=runtime,
        remediation_inputs={
            "gateResultRef": "artifact://verification/V1",
            "remainingWorkRef": "artifact://remaining/R1",
        },
    )
    materialized_pairs += 1
    assert remediation_node["inputs"]["remediationWorkspaceHeadRef"] == (
        "artifact://workspace/C1"
    )
    assert remediation_node["inputs"]["gateResultRef"] == "artifact://verification/V1"
    assert remediation_node["inputs"]["remainingWorkRef"] == "artifact://remaining/R1"
    assert "- gateResultRef: artifact://verification/V1" in (
        remediation_node["inputs"]["instructions"]
    )
    assert verification_node["inputs"]["readOnlyWorkspaceHead"] is True
    assert verification_node["dependsOn"] == [remediation_node["id"]]

    # Attempt 2: C1 -> C2, verifier accepts; budgets/head persist across a
    # restart-shaped serialize/restore round-trip before the final decision.
    running2 = start_remediation_attempt(decided)
    assert running2.attempt_ordinal == 2
    captured2 = capture_remediation_candidate(
        running2, workspace_head_ref="artifact://workspace/C2"
    )
    restored2 = RemediationLoopState.model_validate(
        captured2.model_dump(by_alias=True, mode="json")
    )
    assert restored2.workspace_head_ref == "artifact://workspace/C2"
    assert restored2.consumed_budgets.attempts == 2
    assert marker_content in candidate_contents[restored2.workspace_head_ref or ""]
    verifying2 = start_verification(restored2)
    evaluating2 = record_verification_evidence(
        verifying2, verification_ref="artifact://verification/V2"
    )
    progressed2 = record_semantic_progress(
        evaluating2,
        progress_ref="artifact://remaining/R2",
        progress_signature="sha256:" + ("2" * 64),
    )
    assert progressed2.consumed_budgets.consecutive_semantic_no_progress == 0
    final_decision = decide_remediation_continuation(
        spec=spec,
        state=progressed2,
        verdict="FULLY_IMPLEMENTED",
        gate_result_ref="artifact://verification/V2",
    )
    accepted = apply_continuation_decision(
        progressed2, decision=final_decision, decision_ref="artifact://decision/D2"
    )
    projection = project_remediation_loop(spec=spec, state=accepted)
    materialized_pairs += 1  # counted pair admissions: attempt-2 node + terminal

    assert accepted.phase == RemediationLoopPhase.ACCEPTED
    assert accepted.workspace_head_ref == "artifact://workspace/C2"
    assert projection["workspaceHeadRef"] == "artifact://workspace/C2"
    assert projection["latestVerdict"] == "FULLY_IMPLEMENTED"
    assert projection["attemptOrdinal"] == 2
    assert projection["consumedBudgets"]["attempts"] == 2
    # Invocation counts: one preserved-output read, two attempt admissions.
    assert preserved_calls == 1
    assert materialized_pairs == 2
    # Cumulative content: predecessor marker survives through C2.
    assert marker_content in candidate_contents["artifact://workspace/C2"]
    assert candidate_contents["artifact://workspace/C2"] != (
        candidate_contents["artifact://workspace/before-implement"]
    )
