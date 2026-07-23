from unittest.mock import AsyncMock

import pytest

from moonmind.workflows.executions.control_stop_continuation import (
    ControlStopContinuationContract,
)
from moonmind.workflows.temporal.workflows.control_stop_continuation import (
    MoonMindControlStopContinuationWorkflow,
)
from tests.unit.workflows.executions.test_control_stop_continuation import _payload


def _restore(contract: ControlStopContinuationContract) -> dict:
    return {
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


def _capture(contract: ControlStopContinuationContract, ordinal: int) -> dict:
    return {
        "schemaVersion": "v1",
        "status": "captured",
        "checkpointKind": "worktree_archive",
        "workspace": {
            "kind": "worktree_archive",
            "baseCommit": "abc123",
            "archiveRef": f"artifact://workspace/C{ordinal}",
            "archiveDigest": f"sha256:{'c' * 64}",
            "manifestRef": f"artifact://workspace/C{ordinal}/manifest",
            "manifestDigest": f"sha256:{'d' * 64}",
        },
        "sourceWorkspaceLocator": {
            "kind": "managed_runtime",
            "runtimeId": "codex_cli",
            "agentRunId": contract.destination_workspace_id,
            "relativePath": "repo",
        },
        "idempotencyKey": (
            f"{contract.destination_workflow_id}:attempt:{ordinal}:capture"
        ),
    }


@pytest.mark.asyncio
async def test_workflow_advances_candidate_and_accepts(monkeypatch) -> None:
    contract = ControlStopContinuationContract.model_validate(_payload())
    info = type(
        "Info",
        (),
        {"workflow_id": contract.destination_workflow_id, "run_id": "destination-run"},
    )()
    execute = AsyncMock(
        side_effect=[
            _restore(contract),
            {"outputRefs": ["artifact://remediation/7"]},
            _capture(contract, 7),
            {
                "outputRefs": ["artifact://verification/7"],
                "metadata": {
                    "controlStopVerification": {
                        "verdict": "FULLY_IMPLEMENTED",
                        "verificationRef": "artifact://verification/7",
                        "progress": True,
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
    assert result["candidateState"] == "accepted_complete"
    assert result["latestCandidateRef"] == "artifact://workspace/C7"
    assert result["continuationBudget"]["consumedAttempts"] == 1
    assert [call.args[0] for call in execute.await_args_list] == [
        "agent_runtime.restore_workspace_checkpoint",
        "integration.omnigent.profile_bound_execute",
        "agent_runtime.capture_workspace_checkpoint",
        "integration.omnigent.profile_bound_execute",
    ]
    assert execute.await_args_list[1].args[1]["instructionRef"] == (
        contract.remaining_work_ref
    )


@pytest.mark.asyncio
async def test_workflow_preserves_latest_candidate_at_new_control_stop(
    monkeypatch,
) -> None:
    payload = _payload()
    payload["continuationBudget"]["maxAttempts"] = 1
    contract = ControlStopContinuationContract.model_validate(payload)
    info = type(
        "Info",
        (),
        {"workflow_id": contract.destination_workflow_id, "run_id": "destination-run"},
    )()
    execute = AsyncMock(
        side_effect=[
            _restore(contract),
            {"outputRefs": ["artifact://remediation/7"]},
            _capture(contract, 7),
            {
                "metadata": {
                    "controlStopVerification": {
                        "verdict": "ADDITIONAL_WORK_NEEDED",
                        "verificationRef": "artifact://verification/7",
                        "remainingWorkRef": "artifact://remaining/7",
                        "progress": False,
                    }
                }
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

    result = await MoonMindControlStopContinuationWorkflow().run(payload)

    assert result["status"] == "control_stop"
    assert result["latestCandidateRef"] == "artifact://workspace/C7"
    assert result["remainingWorkRef"] == "artifact://remaining/7"
    assert result["stopReason"] == "continuation_attempt_budget_exhausted"
