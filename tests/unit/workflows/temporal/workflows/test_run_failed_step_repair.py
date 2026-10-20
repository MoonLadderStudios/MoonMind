"""Failed-step evidence and the repair loop that continues a failed step.

These cover the step ledger and remediation pieces a retried run relies on:
failure diagnostics, parent-owned checkpoint evidence, and a two-attempt
repair that builds on the workflow's saved candidate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from moonmind.workflows.checkpoint_branches import prepare_checkpoint_branch_git_binding
from moonmind.workflows.temporal.remediation_loop import (
    RemediationLoopPhase,
    RemediationLoopSpec,
)
from moonmind.workflows.temporal.remediation_workspace_head import (
    RemediationWorkspaceHead,
)
from moonmind.workflows.temporal.step_ledger import (
    build_initial_step_rows,
    mark_step_checkpoint_evidence,
    update_step_row,
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


def _ordered_nodes() -> list[dict[str, object]]:
    return [
        {"id": "prepare", "title": "Prepare"},
        {"id": "implement", "title": "Implement"},
        {"id": "verify", "title": "Verify"},
    ]


def _dependency_map() -> dict[str, list[str]]:
    return {"prepare": [], "implement": ["prepare"], "verify": ["implement"]}


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


def test_step_ledger_row_lookup_uses_initialized_index() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=datetime.now(UTC),
    )

    assert workflow._step_ledger_row_for("prepare") is workflow._step_ledger_rows[0]
    assert workflow._step_ledger_row_for("missing") is None


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


@pytest.mark.asyncio
async def test_branch_binding_and_two_attempt_repair_preserve_cumulative_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Branch binding and two repairs share the workflow's candidate."""

    import copy

    _configure_workflow_runtime(monkeypatch)
    now = datetime.now(UTC)
    monkeypatch.setattr(run_module.workflow, "now", lambda: now)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: (
            patch_id == run_module.RUN_REMEDIATION_EXPLICIT_EVIDENCE_INPUTS_PATCH
        ),
    )
    marker_content = "prepare-marker-content-3512"
    root_ref = "artifact://workspace/before-implement"
    workflow = MoonMindRunWorkflow()
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    branch_result = prepare_checkpoint_branch_git_binding(
        {
            "workflowId": "wf-recover",
            "productBranchId": "cbr_3512_repair",
            "branchTurnId": "cbt_3512_1",
            "sourceCheckpointRef": root_ref,
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
    assert branch_result.binding.source_checkpoint_ref == root_ref
    assert branch_result.step_execution_manifest_branch["rootCheckpointRef"] == root_ref
    assert branch_result.binding.work_branch != "cbr_3512_repair"

    # Only C0 exists initially. The activity-shaped boundary below reads the
    # checkpoint selected by the workflow for each admitted attempt, then writes
    # the next candidate. Predefined C1/C2 bytes would mask a C0 replay.
    checkpoint_bytes = {root_ref: marker_content.encode() + b"|C0"}

    def digest(content: bytes) -> str:
        return "sha256:" + sha256(content).hexdigest()

    head = RemediationWorkspaceHead(
        loopId="issue-3512-repair-journey",
        branchRef="checkpoint-branch:issue-3512-repair-journey",
        rootCheckpointRef=root_ref,
        rootWorkspaceDigest=digest(checkpoint_bytes[root_ref]),
        headCheckpointRef=root_ref,
        headWorkspaceDigest=digest(checkpoint_bytes[root_ref]),
    )
    spec = _journey_remediation_spec()
    runtime = {"mode": "codex_cli", "model": "gpt-5.6-sol"}
    workflow._initialize_remediation_loop_controller(
        ordered_nodes=[
            {
                "id": "initial-verification",
                "annotations": {
                    "remediationLoop": spec.model_dump(by_alias=True, mode="json")
                },
                "inputs": {"runtime": runtime},
            }
        ],
        require_agent_instructions=False,
    )

    decision_artifacts: dict[str, dict[str, Any]] = {}

    async def persist_decision(*, name: str, payload: dict[str, Any]) -> str:
        ref = f"artifact://{name}"
        decision_artifacts[ref] = copy.deepcopy(payload)
        return ref

    workflow._write_json_artifact = AsyncMock(side_effect=persist_decision)

    def persist_candidate(frozen: dict[str, Any], addition: bytes) -> dict[str, Any]:
        parent_ref = frozen["baseCheckpointRef"]
        parent_bytes = checkpoint_bytes[parent_ref]
        assert digest(parent_bytes) == frozen["expectedBaseDigest"]
        ordinal = frozen["attemptOrdinal"]
        candidate_ref = f"artifact://workspace/C{ordinal}"
        checkpoint_bytes[candidate_ref] = parent_bytes + addition
        return {
            "attemptEvidenceRef": f"artifact://attempt/{ordinal}",
            "parentCheckpointRef": parent_ref,
            "parentWorkspaceDigest": digest(parent_bytes),
            "outputCheckpointRef": candidate_ref,
            "outputWorkspaceDigest": digest(checkpoint_bytes[candidate_ref]),
            "checkpointManifestRef": f"artifact://manifest/C{ordinal}",
            "outcome": "candidate_captured",
        }

    materialize_attempts = Mock(wraps=run_module.materialize_attempt_nodes)
    monkeypatch.setattr(run_module, "materialize_attempt_nodes", materialize_attempts)
    ordered_nodes: list[dict[str, Any]] = []
    admitted = await workflow._evaluate_dynamic_remediation_verification(
        ordered_nodes=ordered_nodes,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://verification/V0",
        remaining_work_ref="artifact://remaining/R0",
        workspace_head=head.model_dump(by_alias=True, mode="json"),
    )
    assert admitted is True
    assert len(ordered_nodes) == 2

    for ordinal, addition in ((1, b"|repair-1"), (2, b"|repair-2")):
        remediation, verification = ordered_nodes[-2:]
        assert remediation["annotations"]["moonSpecRemediationAttempt"] == ordinal
        remediation_inputs = dict(remediation["inputs"])
        workflow._inject_remediation_workspace_baseline(
            node=remediation, node_inputs=remediation_inputs
        )
        frozen = remediation_inputs["remediationAttemptInput"]
        expected_parent = root_ref if ordinal == 1 else "artifact://workspace/C1"
        assert frozen["baseCheckpointRef"] == expected_parent
        assert remediation_inputs["gateResultRef"] == (
            f"artifact://verification/V{ordinal - 1}"
        )
        assert remediation_inputs["remainingWorkRef"] == (
            f"artifact://remaining/R{ordinal - 1}"
        )
        output = persist_candidate(frozen, addition)
        workflow._advance_remediation_workspace_head(
            node=remediation,
            node_inputs=remediation_inputs,
            execution_result={"outputs": {"remediationAttemptOutput": output}},
            step_execution_id=f"wf:run:remediation-{ordinal}:execution:1",
        )
        candidate_ref = f"artifact://workspace/C{ordinal}"
        assert workflow._remediation_loop_state.workspace_head_ref == candidate_ref
        assert workflow._remediation_workspace_head.head_checkpoint_ref == candidate_ref
        verification_inputs = dict(verification["inputs"])
        workflow._inject_remediation_verification_baseline(
            node=verification, node_inputs=verification_inputs
        )
        assert verification_inputs["remediationWorkspaceHeadRef"] == candidate_ref
        assert verification_inputs["readOnlyWorkspaceHead"] is True
        expected_bytes = checkpoint_bytes[root_ref] + b"|repair-1"
        if ordinal == 2:
            expected_bytes += b"|repair-2"
        assert (
            checkpoint_bytes[verification_inputs["remediationWorkspaceHeadRef"]]
            == expected_bytes
        )
        if ordinal == 1:
            workflow._original_input_payload = {}
            continuation = workflow._build_remediation_loop_continue_as_new_input(
                ordered_nodes=ordered_nodes
            )["remediation_loop_continuation"]
            assert continuation["workspaceHead"]["headCheckpointRef"] == candidate_ref
            assert continuation["state"]["workspaceHeadRef"] == candidate_ref
        admitted = await workflow._evaluate_dynamic_remediation_verification(
            ordered_nodes=ordered_nodes,
            verdict=("ADDITIONAL_WORK_NEEDED" if ordinal == 1 else "FULLY_IMPLEMENTED"),
            gate_result_ref=f"artifact://verification/V{ordinal}",
            remaining_work_ref="artifact://remaining/R1" if ordinal == 1 else None,
        )
        assert admitted is (ordinal == 1)

    projection = workflow._publish_context["remediationLoop"]
    assert workflow._remediation_loop_state.phase == RemediationLoopPhase.ACCEPTED
    assert projection["workspaceHeadRef"] == "artifact://workspace/C2"
    assert projection["latestVerdict"] == "FULLY_IMPLEMENTED"
    assert projection["attemptOrdinal"] == 2
    assert projection["consumedBudgets"]["attempts"] == 2
    assert marker_content.encode() in checkpoint_bytes["artifact://workspace/C2"]
    assert materialize_attempts.call_count == 2
    assert [
        call.kwargs["workspace_head_ref"]
        for call in materialize_attempts.call_args_list
    ] == [
        root_ref,
        "artifact://workspace/C1",
    ]
    assert len(ordered_nodes) == 4
    assert workflow._write_json_artifact.await_count == 3
    assert [artifact["verdict"] for artifact in decision_artifacts.values()] == [
        "ADDITIONAL_WORK_NEEDED",
        "ADDITIONAL_WORK_NEEDED",
        "FULLY_IMPLEMENTED",
    ]
