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

    assert result["status"] == "remediation_completed"
    assert result["nextSemanticOperation"] == "remediation"
    assert result["candidateState"] == "recovered_candidate"
    assert result["sideEffects"][0]["disposition"] == "already_performed"
    assert execute.await_args_list[0].args[0] == (
        "agent_runtime.restore_workspace_checkpoint"
    )
    assert execute.await_args_list[1].args[0] == (
        "integration.omnigent.profile_bound_execute"
    )
    remediation_request = execute.await_args_list[1].args[1]
    assert remediation_request["instructionRef"] == contract.remaining_work_ref
    assert remediation_request["executionProfileRef"] == (
        contract.lane.provider_profile_id
    )
