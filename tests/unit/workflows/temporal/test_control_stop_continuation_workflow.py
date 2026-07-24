from unittest.mock import AsyncMock

import pytest

from moonmind.workflows.executions.control_stop_continuation import (
    ControlStopContinuationContract,
)
from moonmind.workflows.temporal.workflows.control_stop_continuation import (
    MoonMindControlStopContinuationWorkflow,
)
from tests.unit.workflows.executions.test_control_stop_continuation import _payload


@pytest.mark.asyncio
async def test_workflow_restores_before_exposing_remediation_state(monkeypatch) -> None:
    contract = ControlStopContinuationContract.model_validate(_payload())
    info = type(
        "Info",
        (),
        {"workflow_id": contract.destination_workflow_id, "run_id": "destination-run"},
    )()
    restore_result = {
            "schemaVersion": "v1",
            "status": "succeeded",
            "checkpointRef": contract.workspace_head_ref,
            "destinationWorkspaceLocator": {
                "kind": "managed_runtime",
                "runtimeId": "codex_cli",
                "agentRunId": contract.destination_workspace_id,
                "relativePath": "repo",
            },
            "restorationEvidenceRef": "artifact://restore/evidence",
            "restorationEvidenceDigest": "sha256:restore",
            "baseCommit": contract.workspace_base_commit,
            "restoredEntryCount": 2,
            "restoredBytes": 20,
            "gitStatusDigest": "sha256:status",
            "idempotencyKey": f"{contract.destination_workflow_id}:restore",
        }
    execute = AsyncMock(
        side_effect=[
            restore_result,
            {
                "outputRefs": ["artifact://remediation/result"],
                "summary": "Remediation attempt completed.",
                "metrics": {"attemptOrdinal": 7},
            },
            {
                "schemaVersion": "v1",
                "status": "captured",
                "checkpointKind": "worktree_archive",
                "workspace": {
                    "kind": "worktree_archive",
                    "baseCommit": contract.workspace_base_commit,
                    "archiveRef": "artifact://workspace/C7",
                    "archiveDigest": f"sha256:{'c' * 64}",
                    "manifestRef": "artifact://workspace/C7/manifest",
                    "manifestDigest": f"sha256:{'d' * 64}",
                },
                "sourceWorkspaceLocator": {
                    "kind": "managed_runtime",
                    "runtimeId": "codex_cli",
                    "agentRunId": contract.destination_workspace_id,
                    "relativePath": "repo",
                },
                "idempotencyKey": (
                    f"{contract.destination_workflow_id}:remediation:7:capture"
                ),
            },
            {
                "outputRefs": ["artifact://gate/7"],
                "summary": "Candidate accepted.",
                "metadata": {
                    "semanticVerdict": "FULLY_IMPLEMENTED",
                    "gateResultRef": "artifact://gate/7",
                },
            },
        ]
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.control_stop_continuation.workflow.info",
        lambda: info,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.control_stop_continuation.workflow.execute_activity",
        execute,
    )

    result = await MoonMindControlStopContinuationWorkflow().run(_payload())

    assert result["status"] == "accepted"
    assert result["nextSemanticOperation"] == "publication_gate"
    assert result["candidateState"] == "accepted_complete"
    assert result["latestWorkspaceHeadRef"] == "artifact://workspace/C7"
    assert result["continuationBudget"]["consumedAttempts"] == 1
    assert result["sideEffects"][0]["disposition"] == "already_performed"
    assert execute.await_args_list[0].args[0] == (
        "agent_runtime.restore_workspace_checkpoint"
    )
    assert execute.await_args_list[1].args[0] == (
        "integration.omnigent.profile_bound_execute"
    )
    assert execute.await_args_list[2].args[0] == (
        "agent_runtime.capture_workspace_checkpoint"
    )
    assert execute.await_args_list[3].args[0] == (
        "integration.omnigent.profile_bound_execute"
    )
    remediation_request = execute.await_args_list[1].args[1]
    assert remediation_request["instructionRef"] == contract.remaining_work_ref
    assert remediation_request["executionProfileRef"] == (
        contract.lane.provider_profile_id
    )
    assert remediation_request["idempotencyKey"].endswith(
        ":remediation:execution:7"
    )
    verification_request = execute.await_args_list[3].args[1]
    assert verification_request["idempotencyKey"].endswith(
        ":verification:execution:7"
    )
    assert verification_request["executionProfileRef"] == (
        contract.lane.provider_profile_id
    )


@pytest.mark.asyncio
async def test_workflow_reads_published_moonspec_verifier_contract(monkeypatch) -> None:
    contract = ControlStopContinuationContract.model_validate(_payload())
    info = type(
        "Info",
        (),
        {"workflow_id": contract.destination_workflow_id, "run_id": "destination-run"},
    )()
    restore_result = {
        "schemaVersion": "v1",
        "status": "succeeded",
        "checkpointRef": contract.workspace_head_ref,
        "destinationWorkspaceLocator": {
            "kind": "managed_runtime",
            "runtimeId": "codex_cli",
            "agentRunId": contract.destination_workspace_id,
            "relativePath": "repo",
        },
        "restorationEvidenceRef": "artifact://restore/evidence",
        "restorationEvidenceDigest": "sha256:restore",
        "baseCommit": contract.workspace_base_commit,
        "restoredEntryCount": 2,
        "restoredBytes": 20,
        "gitStatusDigest": "sha256:status",
        "idempotencyKey": f"{contract.destination_workflow_id}:restore",
    }
    execute = AsyncMock(
        side_effect=[
            restore_result,
            {"outputRefs": ["artifact://remediation/result"], "summary": "done"},
            {
                "schemaVersion": "v1",
                "status": "captured",
                "checkpointKind": "worktree_archive",
                "workspace": {
                    "kind": "worktree_archive",
                    "baseCommit": contract.workspace_base_commit,
                    "archiveRef": "artifact://workspace/C7",
                    "archiveDigest": f"sha256:{'c' * 64}",
                    "manifestRef": "artifact://workspace/C7/manifest",
                    "manifestDigest": f"sha256:{'d' * 64}",
                },
                "sourceWorkspaceLocator": {
                    "kind": "managed_runtime",
                    "runtimeId": "codex_cli",
                    "agentRunId": contract.destination_workspace_id,
                    "relativePath": "repo",
                },
                "idempotencyKey": (
                    f"{contract.destination_workflow_id}:remediation:7:capture"
                ),
            },
            {
                "outputRefs": ["artifact://gate/7"],
                "summary": "Candidate accepted.",
                "metadata": {
                    "moonSpecVerify": {
                        "verdict": "FULLY_IMPLEMENTED",
                        "gateResultRef": "artifact://gate/7",
                    }
                },
            },
        ]
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.control_stop_continuation.workflow.info",
        lambda: info,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.control_stop_continuation.workflow.execute_activity",
        execute,
    )

    result = await MoonMindControlStopContinuationWorkflow().run(_payload())

    assert result["status"] == "accepted"
    assert result["latestVerificationRef"] == "artifact://gate/7"
