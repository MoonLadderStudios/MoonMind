"""Replay retains dynamic child IDs from the recorded policy Activity result."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.schemas.resilience_policy_models import compile_resilience_policy
from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationLoopPhase,
    RemediationLoopSpec,
    RemediationLoopState,
)
from moonmind.workflows.temporal.workflows import run as run_module


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current_run,recorded_run,expected_run",
    [
        ("original", "original", "original"),
        ("reset", "original", "original"),
        ("continued-reset", "continued", "continued"),
        ("legacy", None, "legacy"),
    ],
)
async def test_recorded_policy_retains_remediation_child_identity(
    monkeypatch: pytest.MonkeyPatch,
    current_run: str,
    recorded_run: str | None,
    expected_run: str,
) -> None:
    now = datetime(2026, 9, 6, tzinfo=UTC)
    info = SimpleNamespace(workflow_id="workflow", run_id=current_run)
    monkeypatch.setattr(run_module.workflow, "info", lambda: info)
    monkeypatch.setattr(run_module.workflow, "now", lambda: now)
    envelope = compile_resilience_policy(
        compiled_at=now,
        workflow_id="workflow",
        run_id=recorded_run,
        attempts={
            "stepMaxAttempts": 3,
            "stepNoProgressLimit": 2,
            "jobSelfHealMaxResets": 1,
        },
        timeouts={"stepTimeoutSeconds": 900, "stepIdleTimeoutSeconds": 300},
        provider_cooldown={"cooldownAfter429Seconds": 900},
        checkpoints={
            "checkpointRequired": True,
            "requiredBoundaries": ["after_execution"],
        },
        idempotency={
            "sideEffectIdempotencyRequired": True,
            "keyStrategy": "step_execution_operation",
        },
        outbound_scanning={"highSecurityMode": False, "blockOnFinding": False},
        observability={
            "liveLogsTimelineEnabled": False,
            "structuredHistoryEnabled": True,
        },
        cost_attribution={"runtimeId": "omnigent"},
    )
    execute = AsyncMock(return_value=envelope.model_dump(by_alias=True, mode="json"))
    monkeypatch.setattr(run_module.workflow, "execute_activity", execute)
    parent = run_module.MoonMindRunWorkflow()
    monkeypatch.setattr(
        parent, "_write_json_artifact", AsyncMock(return_value="art_policy")
    )
    await parent._compile_resilience_policy_envelope_ref(
        provider_profile_id=None, parameters={}, artifact_name="policy.json"
    )
    assert execute.call_args.args[0] == "resilience.compile_policy"
    assert execute.call_args.args[1]["runId"] == current_run
    parent._remediation_loop_spec = RemediationLoopSpec.model_validate(
        {
            "loopId": "issue-implementation-remediation",
            "remediationTool": {
                "type": "skill",
                "name": "auto",
                "inputs": {"instructions": "Fix gaps."},
            },
            "verificationTool": {
                "type": "skill",
                "name": "moonspec-verify",
                "inputs": {"instructions": "Verify candidate."},
            },
            "workspacePolicy": "continue_from_loop_head",
            "budgets": {"hardMaxAttempts": 3},
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
    parent._remediation_loop_state = RemediationLoopState(
        loopId="issue-implementation-remediation",
        phase=RemediationLoopPhase.CONTINUATION_DECIDING,
        consumedBudgets=ConsumedRemediationBudgets(),
        workspaceHeadRef="artifact://art_preserved_candidate",
    )
    parent._remediation_loop_runtime = {"mode": "omnigent"}
    remediation, verification = parent._materialize_remediation_attempt(ordinal=1)
    for node, kind in ((remediation, "remediation"), (verification, "verification")):
        assert (
            node["id"]
            == f"workflow:{expected_run}:issue-implementation-remediation:{kind}:1"
        )
