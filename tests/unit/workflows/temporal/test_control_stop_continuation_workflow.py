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
    execute = AsyncMock(
        return_value={
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

    assert result["status"] == "ready_for_remediation"
    assert result["nextSemanticOperation"] == "remediation"
    assert result["candidateState"] == "recovered_candidate"
    assert result["sideEffects"][0]["disposition"] == "already_performed"
    assert execute.await_args.args[0] == "agent_runtime.restore_workspace_checkpoint"
