"""Temporal recovery of missing verification, with actual candidate checks.

The Activity represents a controlled test service. The real workflow recovery
controller schedules only verification after its outage; no implementation
callback is available. Other journeys qualify candidate checkpoint restoration.
"""

import json
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.integration.reliability.test_release_routing_journey import connect
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _dynamic_loop_spec_payload,
    _loop_controller_node,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn
class VerifySavedCandidate:
    @workflow.run
    async def run(self, checkpoint: str):
        controller = MoonMindRunWorkflow()
        controller._initialize_remediation_loop_controller(
            ordered_nodes=[_loop_controller_node(_dynamic_loop_spec_payload())],
        )
        controller._remediation_loop_state = (
            controller._remediation_loop_state.model_copy(
                update={"workspace_head_ref": checkpoint},
            )
        )
        controller._step_ledger_rows = []

        async def write_evidence(*args, **kwargs):
            return await workflow.execute_activity(
                "reliability.write_verification_decision",
                {"args": args, "kwargs": kwargs},
                start_to_close_timeout=timedelta(seconds=10),
            )

        controller._write_json_artifact = write_evidence
        source = {
            "id": "verify-original",
            "tool": {"type": "agent_runtime", "name": "omnigent"},
            "inputs": {
                "selectedSkill": "moonspec-verify",
                "instructions": "Verify the candidate.",
                "runtime": {
                    "mode": "omnigent",
                    "executionProfileRef": "selected-profile",
                },
            },
        }
        nodes = [source]
        for ordinal in range(2):
            node = nodes[ordinal]
            outcome = await workflow.execute_activity(
                "reliability.verify_candidate",
                {"ordinal": ordinal, "node": node, "checkpoint": checkpoint},
                start_to_close_timeout=timedelta(seconds=20),
            )
            await controller._evaluate_dynamic_remediation_verification(
                ordered_nodes=nodes,
                verdict=outcome["verdict"],
                gate_result_ref=outcome["ref"],
                remaining_work_ref=None,
                logical_step_id=node["id"],
                current_index=ordinal + 1,
                recoverable_evidence=outcome["verdict"] == "NO_DETERMINATION",
            )
        state = controller._remediation_loop_state
        return {
            "phase": state.phase.value,
            "attempts": state.consumed_budgets.attempts,
            "evidenceRetries": state.consumed_budgets.evidence_retries,
            "checkpoint": state.workspace_head_ref,
            "nodes": len(nodes),
        }


async def test_service_recovery_verifies_exact_head_without_reimplementation(tmp_path):
    client = await connect()
    queue = "verification-retry-" + uuid4().hex
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    subprocess.run(["git", "init", "-q", str(candidate)], check=True)
    (candidate / "acceptance.py").write_text("assert 6 * 7 == 42\n")
    subprocess.run(["git", "-C", str(candidate), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(candidate),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "complete candidate",
        ],
        check=True,
    )

    def head():
        return subprocess.check_output(
            ["git", "-C", str(candidate), "rev-parse", "HEAD"], text=True
        ).strip()

    original_head = head()
    checkpoint = "artifact://candidate/" + original_head
    decisions = []
    checks = []

    @activity.defn(name="reliability.write_verification_decision")
    async def write_decision(payload: dict):
        path = tmp_path / f"decision-{len(decisions)}.json"
        path.write_text(json.dumps(payload))
        decisions.append(json.loads(path.read_text()))
        return "artifact://decision/" + path.stem

    @activity.defn(name="reliability.verify_candidate")
    async def verify(payload: dict):
        assert head() == original_head
        checks.append(payload)
        if payload["ordinal"] == 0:
            return {
                "verdict": "NO_DETERMINATION",
                "ref": "artifact://gate/service-unavailable",
            }
        inputs = payload["node"]["inputs"]
        assert inputs["remediationWorkspaceHeadRef"] == checkpoint
        assert inputs["readOnlyWorkspaceHead"] is True
        assert inputs["runtime"] == {
            "mode": "omnigent",
            "executionProfileRef": "selected-profile",
        }
        result = subprocess.run(
            [sys.executable, "acceptance.py"], cwd=candidate, capture_output=True
        )
        assert result.returncode == 0, result.stderr
        return {"verdict": "FULLY_IMPLEMENTED", "ref": "artifact://gate/check-passed"}

    async with Worker(
        client,
        task_queue=queue,
        workflows=[VerifySavedCandidate],
        activities=[verify, write_decision],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        result = await client.execute_workflow(
            VerifySavedCandidate.run,
            checkpoint,
            id=queue,
            task_queue=queue,
            execution_timeout=timedelta(seconds=60),
        )
    assert result == {
        "phase": "accepted",
        "attempts": 0,
        "evidenceRetries": 1,
        "checkpoint": checkpoint,
        "nodes": 2,
    }
    assert len(checks) == len(decisions) == 2
    assert head() == original_head
    assert (
        subprocess.check_output(["git", "-C", str(candidate), "status", "--porcelain"])
        == b""
    )
