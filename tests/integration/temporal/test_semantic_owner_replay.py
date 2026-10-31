"""Immutable commands across the extracted Skill/publication/request boundaries."""

import asyncio
import gzip
import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import ExecutionBudgetContext
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/temporal/semantic_owners"


@workflow.defn(name="SemanticOwnerBoundary")
class SemanticOwnerBoundary:
    @workflow.run
    async def run(self, source: dict) -> dict:
        owner = MoonMindRunWorkflow()
        # These flags model retained command payloads, not runtime settings.
        if source.get("retained"):
            original = owner._workflow_patch_enabled
            owner._workflow_patch_enabled = lambda patch: (
                False if patch in {
                    "run-step-profile-selection-inheritance-v1",
                    "run-account-intent-normalization-v2",
                } else original(patch)
            )
        outputs = source["outputs"]
        owner._record_moonspec_verify_gate(node_id="verify", outputs=outputs)
        request = owner._build_agent_execution_request(
            node_inputs={
                "instructions": "Preserve admitted scope and saved work.",
                "runtime": {
                    "mode": "codex_cli", "providerProfileRef": "authored-account",
                    "executionProfileRef": "recorded-account",
                },
            },
            node_id="implement", tool_name="codex_cli", step_execution=2,
            workflow_parameters={"profileId": "parent-account"},
        )
        request = request.model_copy(update={
            "execution_budget": ExecutionBudgetContext.model_validate(source["budget"]),
        })
        branches = owner._resolve_native_pr_branches(
            parameters={}, agent_outputs=outputs, workspace_spec={"startingBranch": "main"},
            last_node_inputs={}, publish_payload={"prBaseBranch": "release"},
        )
        decision = {
            "gate": owner._publish_context["moonSpecGate"],
            "publication": owner._publication_feasibility({"outputs": outputs}),
            "branches": list(branches),
            "request": request.model_dump(mode="json", by_alias=True),
            "savedOutputs": source["savedOutputs"],
            "budget": source["budget"],
        }
        return await workflow.execute_activity(
            "semantic_owner.capture", decision, start_to_close_timeout=timedelta(seconds=30),
            task_queue="semantic-effects",
        )


def boundary_input(*, retained=False):
    return {
        "retained": retained,
        "outputs": {
            "verification" if retained else "moonSpecVerify": {
                "verdict": "ADDITIONAL_WORK_NEEDED", "recommendedNextAction": "blocked",
                "gateResultRef": "art_saved_gate", "remainingWorkRef": "art_saved_gaps",
                "operator_summary": "Original verifier diagnosis.",
                "diagnosticsRef": "art_original_diagnostics",
            },
            "accepted_repository_evidence" if retained else "acceptedRepositoryEvidence": {
                "publicationAuthorized": False, "commitsAheadOfBase": 2,
                "candidateDiffRef": "art_saved_diff", "evidenceRef": "art_receipt",
            },
            "push_branch": "saved-candidate", "push_base_branch": "main",
        },
        "savedOutputs": {"prepare": "art_confirmed_prepare", "implement": "art_saved_output"},
        "budget": ExecutionBudgetContext.model_validate({
            "budget": {"baseSeconds": 10, "maxSeconds": 10, "progressStallSeconds": 5},
            "logicalStepId": "implement", "attemptId": "attempt-2",
            "admittedAt": "2026-10-31T00:00:00Z", "consumedSeconds": 7,
            "lastProgressSeconds": 6, "recoveryCount": 1,
        }).model_dump(mode="json", by_alias=True),
    }


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.integration_ci
@pytest.mark.temporal_boundary
@pytest.mark.parametrize("name", ["canonical", "retained"])
async def test_original_semantic_boundary_commands_replay(name):
    provenance = json.loads((FIXTURES / "provenance.json").read_text())
    raw = (FIXTURES / f"{name}.json.gz").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == provenance["histories"][name]
    history = WorkflowHistory.from_json(name, gzip.decompress(raw).decode())
    await Replayer(
        workflows=[SemanticOwnerBoundary], workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.integration_ci
@pytest.mark.temporal_boundary
@pytest.mark.parametrize("cancel", [False, True], ids=["worker-replacement", "cancellation"])
async def test_prepared_decisions_survive_lost_acknowledgement(cancel):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    @activity.defn(name="semantic_owner.capture")
    async def capture(decision):
        calls.append(decision)
        started.set()
        await release.wait()
        return decision

    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="semantic-effects", activities=[capture]):
            # Keep the Activity on a separate worker while replacing orchestration.
            async with Worker(
                env.client, task_queue="semantic-boundary", workflows=[SemanticOwnerBoundary],
                workflow_runner=UnsandboxedWorkflowRunner(),
                graceful_shutdown_timeout=timedelta(seconds=0),
            ):
                handle = await env.client.start_workflow(
                    SemanticOwnerBoundary.run, boundary_input(), id="semantic-restart",
                    task_queue="semantic-boundary",
                )
                await asyncio.wait_for(started.wait(), 10)
                before = await handle.fetch_history()
                # The command is durable but the workflow has no acknowledgement.
                if cancel:
                    await handle.cancel()
            await Replayer(
                workflows=[SemanticOwnerBoundary], workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(before)
            async with Worker(
                env.client, task_queue="semantic-boundary", workflows=[SemanticOwnerBoundary],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                release.set()
                if cancel:
                    from temporalio.client import WorkflowFailureError
                    with pytest.raises(WorkflowFailureError):
                        await asyncio.wait_for(handle.result(), timeout=30)
                else:
                    decision = await asyncio.wait_for(handle.result(), timeout=30)
                    assert decision == calls[0]
            assert len(calls) == 1
            decision = calls[0]
            source = boundary_input()
            assert decision["savedOutputs"] == source["savedOutputs"]
            assert decision["budget"] == source["budget"]
            assert decision["request"]["executionBudget"] == source["budget"]
            assert decision["gate"]["gateResultRef"] == "art_saved_gate"
            assert decision["gate"]["diagnosticsRef"] == "art_original_diagnostics"
            assert decision["publication"]["reason"] == "publication_unauthorized"
            assert ":implement:execution:2:agent_execute" in decision["request"]["idempotencyKey"]
