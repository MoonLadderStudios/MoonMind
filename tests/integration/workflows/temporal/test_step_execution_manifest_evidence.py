from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from moonmind.workflows.executions.prepared_context import (
    branch_turn_step_execution_manifest_projection,
    build_branch_turn_context_bundle,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-boundary",
        run_id="run-boundary",
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
    monkeypatch.setattr(
        run_module.workflow,
        "now",
        lambda: datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: False)


async def test_step_execution_manifest_refs_are_append_only_for_reexecution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

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
        return f"artifact-execution-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "implement",
                "tool": {"type": "agent_runtime", "name": "codex"},
                "inputs": {"title": "Implement"},
            }
        ],
        dependency_map={"implement": []},
        updated_at=now,
    )

    workflow._mark_step_running("implement", updated_at=now, summary="Initial")
    await workflow._record_step_execution_manifest(
        "implement",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )
    workflow._mark_step_terminal(
        "implement",
        status="failed",
        updated_at=now,
        summary="Failed gate",
    )
    workflow._mark_step_running("implement", updated_at=now, summary="Retry")
    await workflow._record_step_execution_manifest(
        "implement",
        phase="start",
        updated_at=now,
        reason="quality_gate_failed",
    )

    step = workflow.get_step_ledger()["steps"][0]
    assert step["refs"]["latestStepExecutionManifestRef"] == "artifact-execution-2"
    assert step["refs"]["stepExecutionManifestRefs"] == [
        "artifact-execution-1",
        "artifact-execution-2",
    ]
    assert writes[0]["payload"]["workspace"]["policy"] == "fresh_branch_from_source"
    assert writes[0]["payload"]["workspace"]["evidenceAccepted"] is True
    assert writes[1]["payload"]["workspace"]["policy"] == "continue_from_previous_execution"
    assert writes[1]["payload"]["workspace"]["evidenceAccepted"] is False
    assert writes[1]["payload"]["workspace"]["rejectionReason"] == (
        "missing_required_checkpoint_evidence"
    )
    assert writes[1]["payload"]["status"] == "blocked"
    assert writes[1]["payload"]["terminalDisposition"] == "blocked"
    assert writes[1]["payload"]["outputs"]["summary"] == (
        "Workspace policy rejected before launch."
    )
    assert writes[1]["payload"]["workspace"]["sourceExecutionOrdinal"] == {
        "workflowId": "wf-boundary",
        "runId": "run-boundary",
        "logicalStepId": "implement",
        "executionOrdinal": 1,
    }
    assert step["status"] == "failed"
    assert step["lastError"] == "missing_required_checkpoint_evidence"


async def test_recovery_step_execution_manifest_carries_lineage_without_large_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._recovery_source = {
        "sourceWorkflowId": "wf-source",
        "sourceRunId": "run-source",
        "sourceTaskInputSnapshotRef": "artifact://snapshot/source",
        "sourcePlanDigest": "sha256:source-plan",
        "failedStepId": "implement",
        "failedStepExecution": 2,
        "recoveryCheckpointRef": "artifact://workspace/before-implement",
        "failedRunRecoveryManifestRef": "artifact://resume/manifest",
        "recoveryWorkspace": {
            "checkpointRef": "artifact://workspace/before-implement",
            "workspacePolicy": "start_from_last_passed_commit",
        },
        "preservedSteps": [],
    }
    writes: list[dict[str, Any]] = []
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

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
        return f"artifact-execution-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "title": "Implement"}],
        dependency_map={"implement": []},
        updated_at=now,
    )

    workflow._mark_step_running("implement", updated_at=now, summary="Resume")
    await workflow._record_step_execution_manifest(
        "implement",
        phase="start",
        updated_at=now,
        reason="recover_from_failed_step",
    )

    manifest = writes[0]["payload"]
    assert manifest["lineage"]["sourceWorkflowId"] == "wf-source"
    assert manifest["lineage"]["sourceRunId"] == "run-source"
    assert manifest["lineage"]["sourceExecutionOrdinal"] == 2
    assert manifest["lineage"]["lineageExecutionOrdinal"] == 3
    assert manifest["recoverySource"]["sourceWorkflowId"] == "wf-source"
    assert manifest["recoverySource"]["sourceRunId"] == "run-source"
    assert manifest["recoverySource"]["failedRunRecoveryManifestRef"] == (
        "artifact://resume/manifest"
    )
    assert manifest["recoverySource"]["preservedSteps"] == []
    assert manifest["workspace"]["policy"] == "start_from_last_passed_commit"
    assert manifest["workspace"]["checkpointRef"] == (
        "artifact://workspace/before-implement"
    )
    assert manifest["workspace"]["evidenceAccepted"] is True
    assert "payload" not in manifest


async def test_terminal_manifest_aggregates_required_side_effect_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

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
        return f"artifact-execution-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "publish", "title": "Publish"}],
        dependency_map={"publish": []},
        updated_at=now,
    )
    workflow._mark_step_running("publish", updated_at=now, summary="Publishing")
    await workflow._record_step_execution_manifest(
        "publish",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )
    workflow._record_step_result_evidence(
        "publish",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "outputSummaryRef": "artifact://summary/publish",
                "stdoutArtifactRef": "artifact://stdout/publish",
            },
        },
        updated_at=now,
    )
    for record in (
        {
            "effect_class": "external_idempotent",
            "operation": "jira.comment",
            "target": "MM-821",
            "idempotency_key": "wf-boundary:publish:external",
        },
        {
            "effect_class": "artifact_write",
            "operation": "artifact.write",
            "target": "artifact://manifest",
        },
        {
            "effect_class": "publication",
            "operation": "github.comment",
            "target": "PR-1",
            "idempotency_key": "wf-boundary:publish:publication",
        },
        {
            "effect_class": "external_non_idempotent",
            "effect_kind": "compensation",
            "operation": "compensate:jira.transition",
            "target": "MM-821",
            "idempotency_key": "wf-boundary:publish:compensation",
        },
        {
            "effect_class": "memory_update",
            "operation": "memory.write",
            "target": "memory://run",
        },
        {
            "effect_class": "retrieval_index_update",
            "operation": "retrieval.index",
            "target": "retrieval://run",
        },
    ):
        workflow._record_step_side_effect(
            "publish",
            workflow_state_accepted=True,
            **record,
        )
    workflow._mark_step_terminal(
        "publish",
        status="completed",
        updated_at=now,
        summary="Published",
    )

    await workflow._record_step_execution_manifest(
        "publish",
        phase="terminal",
        updated_at=now,
        reason="initial_execution",
        status="completed",
        terminal_disposition="accepted",
    )

    terminal = writes[-1]["payload"]
    assert terminal["terminalDisposition"] == "accepted"
    assert terminal["outputs"]["summaryRef"] == "artifact://summary/publish"
    assert terminal["workspace"]["gitEffect"]["disposition"] == "accepted"
    assert terminal["sideEffects"]["summary"]["categories"] == {
        "git": 1,
        "external": 2,
        "artifact": 1,
        "publication": 1,
        "compensation": 1,
        "memory": 1,
        "retrieval": 1,
        "record": 6,
    }
    assert len(terminal["sideEffects"]["records"]) == 6


async def test_terminal_manifest_projects_step_gate_result_check_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append({"name": name, "payload": payload})
        return f"artifact-execution-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "verify", "title": "Verify"}],
        dependency_map={"verify": []},
        updated_at=now,
    )
    workflow._mark_step_running("verify", updated_at=now, summary="Verifying")
    await workflow._record_step_execution_manifest(
        "verify",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )
    workflow._upsert_step_check(
        "verify",
        kind="approval_policy",
        status="failed",
        summary="Structured gate did not approve advancement",
        retry_count=1,
        artifact_ref="artifact://gate/result-1",
        metadata={
            "gateResultRef": "artifact://gate/result-1",
            "gateVerdict": "ADDITIONAL_WORK_NEEDED",
            "confidence": "medium",
            "validatedRefs": {"testReportRef": "artifact://tests/report-1"},
            "remainingWorkRef": "artifact://remaining/work-1",
            "targetLogicalStepId": "implement",
            "workspacePolicyRecommendation": (
                "apply_previous_execution_diff_to_clean_baseline"
            ),
            "recommendedNextAction": "reattempt_current_step",
            "invalid": False,
            "degraded": False,
        },
    )
    workflow._mark_step_terminal(
        "verify",
        status="failed",
        updated_at=now,
        summary="Verification gaps remain",
    )

    await workflow._record_step_execution_manifest(
        "verify",
        phase="terminal",
        updated_at=now,
        reason="quality_gate_failed",
        status="failed",
        terminal_disposition="failed_with_remaining_work",
        budget={
            "gate": "approval_policy",
            "maxAttempts": 2,
            "attemptsConsumed": 2,
            "remainingExecutions": 0,
            "gateVerdict": "ADDITIONAL_WORK_NEEDED",
            "recommendedNextAction": "reattempt_current_step",
        },
    )

    terminal = writes[-1]["payload"]
    check = terminal["checks"][0]
    assert check["artifactRef"] == "artifact://gate/result-1"
    assert check["gateResultRef"] == "artifact://gate/result-1"
    assert check["gateVerdict"] == "ADDITIONAL_WORK_NEEDED"
    assert check["confidence"] == "medium"
    assert check["validatedRefs"] == {"testReportRef": "artifact://tests/report-1"}
    assert check["remainingWorkRef"] == "artifact://remaining/work-1"
    assert check["targetLogicalStepId"] == "implement"
    assert check["workspacePolicyRecommendation"] == (
        "apply_previous_execution_diff_to_clean_baseline"
    )
    assert check["recommendedNextAction"] == "reattempt_current_step"
    assert check["invalid"] is False
    assert check["degraded"] is False
    assert terminal["budget"]["gateVerdict"] == "ADDITIONAL_WORK_NEEDED"
    assert terminal["budget"]["recommendedNextAction"] == "reattempt_current_step"


async def test_terminal_manifest_projects_structured_memory_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append({"name": name, "payload": payload})
        return f"artifact-execution-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "title": "Implement"}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running("implement", updated_at=now, summary="Implementing")
    await workflow._record_step_execution_manifest(
        "implement",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )
    source = {
        "workflowId": "wf-boundary",
        "runId": "run-boundary",
        "logicalStepId": "implement",
        "executionOrdinal": 1,
    }
    workflow._record_step_side_effect(
        "implement",
        effect_class="memory_update",
        operation="memory.accept_run_context",
        target="memory://run",
        workflow_state_accepted=True,
        memory_effect={
            "state": "accepted_for_run_context",
            "target": "memory://run",
            "reason": "policy_approved_for_later_attempts",
            "proposalRef": "artifact://memory/proposal-1",
            "decisionRef": "artifact://memory/decision-1",
            "applicationResultRef": "artifact://memory/application-1",
            "source": source,
        },
    )
    workflow._mark_step_terminal(
        "implement",
        status="completed",
        updated_at=now,
        summary="Implemented",
    )

    await workflow._record_step_execution_manifest(
        "implement",
        phase="terminal",
        updated_at=now,
        reason="initial_execution",
        status="completed",
        terminal_disposition="accepted",
    )

    memory = writes[-1]["payload"]["sideEffects"]["memory"]
    assert memory == [
        {
            "state": "accepted_for_run_context",
            "target": "memory://run",
            "reason": "policy_approved_for_later_attempts",
            "proposalRef": "artifact://memory/proposal-1",
            "decisionRef": "artifact://memory/decision-1",
            "applicationResultRef": "artifact://memory/application-1",
            "source": source,
            "privilegedAction": None,
        }
    ]


async def test_failed_step_memory_candidate_remains_proposed_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append({"name": name, "payload": payload})
        return f"artifact-execution-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "title": "Implement"}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running("implement", updated_at=now, summary="Implementing")
    await workflow._record_step_execution_manifest(
        "implement",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )
    workflow._record_step_side_effect(
        "implement",
        effect_class="memory_update",
        operation="memory.propose",
        target="repo://AGENTS.md",
        workflow_state_accepted=False,
        memory_effect={
            "state": "proposed",
            "target": "repo://AGENTS.md",
            "reason": "terminal_disposition_not_accepted",
            "proposalRef": "artifact://memory/proposal-failed",
            "decisionRef": "artifact://memory/decision-blocked",
            "applicationResultRef": None,
            "source": {
                "workflowId": "wf-boundary",
                "runId": "run-boundary",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
            },
        },
    )
    workflow._mark_step_terminal(
        "implement",
        status="failed",
        updated_at=now,
        summary="Failed",
    )

    await workflow._record_step_execution_manifest(
        "implement",
        phase="terminal",
        updated_at=now,
        reason="initial_execution",
        status="failed",
        terminal_disposition="retryable",
    )

    memory = writes[-1]["payload"]["sideEffects"]["memory"][0]
    assert memory["state"] == "proposed"
    assert memory["target"] == "repo://AGENTS.md"
    assert "privilegedAction" not in memory or memory["privilegedAction"] is None


async def test_repo_memory_write_projects_privileged_candidate_when_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append({"name": name, "payload": payload})
        return f"artifact-execution-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "publish", "title": "Publish"}],
        dependency_map={"publish": []},
        updated_at=now,
    )
    workflow._mark_step_running("publish", updated_at=now, summary="Publishing")
    await workflow._record_step_execution_manifest(
        "publish",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )
    workflow._record_step_side_effect(
        "publish",
        effect_class="memory_update",
        operation="memory.apply_repo",
        target="repo://AGENTS.md",
        workflow_state_accepted=True,
        memory_effect={
            "state": "applied_to_repo",
            "target": "repo://AGENTS.md",
            "reason": "accepted_disposition_and_publication_gate_passed",
            "proposalRef": "artifact://memory/proposal-1",
            "decisionRef": "artifact://memory/decision-1",
            "applicationResultRef": "artifact://memory/application-1",
            "source": {
                "workflowId": "wf-boundary",
                "runId": "run-boundary",
                "logicalStepId": "publish",
                "executionOrdinal": 1,
            },
            "privilegedAction": {
                "actor": "workflow://wf-boundary",
                "action": "memory.apply_repo",
                "target": "repo://AGENTS.md",
                "reason": "approved_repo_application",
                "decision": "approve_repo_application",
                "evidenceRefs": ["artifact://memory/decision-1"],
            },
        },
    )
    workflow._mark_step_terminal(
        "publish",
        status="completed",
        updated_at=now,
        summary="Published",
    )

    await workflow._record_step_execution_manifest(
        "publish",
        phase="terminal",
        updated_at=now,
        reason="initial_execution",
        status="completed",
        terminal_disposition="accepted",
    )

    memory = writes[-1]["payload"]["sideEffects"]["memory"][0]
    assert memory["state"] == "applied_to_repo"
    assert memory["privilegedAction"]["action"] == "memory.apply_repo"


async def test_branch_turn_manifest_projection_carries_prepared_git_baseline() -> None:
    workspace_baseline = {
        "repository": "MoonLadderStudios/MoonMind",
        "baseBranch": "feature/mm-1101-source",
        "baseCommit": "abc1234",
        "resolvedBaseCommit": "abc1234",
        "workBranch": "mm/wf-boundary/implement/cbr-1101",
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "creationMode": "from_checkpoint_patch",
        "sourceCheckpointRef": "artifact://checkpoints/after-implement",
        "sourceCheckpointDigest": "sha256:checkpoint",
        "productBranchId": "cbr_1101",
        "branchTurnId": "cbt_1101",
        "idempotencyKey": "mm-1101:create",
    }
    bundle = build_branch_turn_context_bundle(
        workflow_id="wf-boundary",
        run_id="run-boundary",
        logical_step_id="implement",
        execution_ordinal=2,
        branch_id="cbr_1101",
        branch_turn_id="cbt_1101",
        source_checkpoint={
            "workflowId": "wf-boundary",
            "runId": "run-boundary",
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoints/after-implement",
            "checkpointDigest": "sha256:checkpoint",
        },
        instruction_artifact_ref="artifact://instructions/mm-1101",
        instruction_digest="sha256:instructions",
        runtime_context_policy="fresh_agent_run",
        workspace_policy="apply_previous_execution_diff_to_clean_baseline",
        workspace_baseline=workspace_baseline,
        git_work_branch="mm/wf-boundary/implement/cbr-1101",
    )

    projection = branch_turn_step_execution_manifest_projection(bundle)

    assert projection["branch"]["workspacePolicy"] == (
        "apply_previous_execution_diff_to_clean_baseline"
    )
    assert projection["branch"]["creationMode"] == "from_checkpoint_patch"
    assert projection["branch"]["repository"] == "MoonLadderStudios/MoonMind"
    assert projection["branch"]["workspaceBaseline"] == workspace_baseline
