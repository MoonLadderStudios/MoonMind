"""Batch PR fan-out must prove its effects even when the harness exits cleanly."""

import json

import pytest

from moonmind.workflows.terminal_evidence import evaluate_terminal_evidence

CONTRACT = {
    "contractId": "batch_pr_resolver_fanout.v1",
    "relativePath": "artifacts/batch_pr_resolver_result.json",
    "expectedSchemaVersion": "moonmind.batch-pr-resolver-result.v1",
    "executionRef": "batch:run:node-1:execution:1",
}


def test_helper_blocked_before_execution_cannot_complete(tmp_path):
    result = evaluate_terminal_evidence(CONTRACT, workspace_path=str(tmp_path))
    assert not result.satisfied
    assert result.failure_code == "INCOMPLETE_TERMINAL_CONTRACT"
    assert result.missing_evidence == (CONTRACT["relativePath"],)


@pytest.mark.asyncio
async def test_idle_omnigent_result_fails_at_agent_run_evidence_boundary(tmp_path):
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalAgentRuntimeActivities,
    )

    result = (
        await TemporalAgentRuntimeActivities().agent_runtime_evaluate_terminal_evidence(
            {
                "workspacePath": str(tmp_path),
                "terminalContract": CONTRACT,
                "result": {
                    "summary": "Omnigent session completed",
                    "metadata": {
                        "normalizedStatus": "completed",
                        "omnigentSessionId": "batch-session",
                    },
                },
            }
        )
    )
    assert result.failure_class == "execution_error"
    assert result.provider_error_code == "INCOMPLETE_TERMINAL_CONTRACT"
    assert CONTRACT["relativePath"] in result.summary
    assert result.metadata["terminalContractAuthority"] == "MoonMind.AgentRun"


@pytest.mark.parametrize(
    "status,queued,skipped,errors,expected",
    [
        ("queued", [{"workflowId": "mm:child"}], [{"reason": "fork-pr"}], [], None),
        ("no_op", [], [{"reason": "fork-pr"}], [], None),
        ("no_op", [], [], [], None),
        (
            "partial_failure",
            [{"workflowId": "mm:child"}],
            [],
            [{"error": "403"}],
            "BATCH_FANOUT_PARTIAL_FAILURE",
        ),
        ("failed", [], [], [{"error": "403"}], "BATCH_FANOUT_FAILED"),
        ("queued", [{}], [], [], "INVALID_TERMINAL_EVIDENCE"),
        ("running", [], [], [], "INCOMPLETE_TERMINAL_CONTRACT"),
    ],
)
def test_batch_pr_evidence_preserves_real_outcome(
    tmp_path, status, queued, skipped, errors, expected
):
    path = tmp_path / CONTRACT["relativePath"]
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "schemaVersion": CONTRACT["expectedSchemaVersion"],
                "contractId": CONTRACT["contractId"],
                "executionRef": CONTRACT["executionRef"],
                "status": status,
                "requested": len(queued) + len(skipped) + len(errors),
                "created": len(queued),
                "queued": queued,
                "skipped": skipped,
                "errors": errors,
            }
        )
    )
    result = evaluate_terminal_evidence(CONTRACT, workspace_path=str(tmp_path))
    assert result.satisfied is (expected is None)
    assert result.failure_code == expected
    assert result.metadata["queuedChildren"] == queued

    stale = evaluate_terminal_evidence(
        {**CONTRACT, "executionRef": "different-attempt"}, workspace_path=str(tmp_path)
    )
    assert stale.failure_code == "STALE_TERMINAL_EVIDENCE"
