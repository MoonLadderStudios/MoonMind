"""Lost branch-start acknowledgments retain the admitted result for verification."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.db import models
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
from api_service.services.checkpoint_branch_turn_execution import (
    CheckpointBranchTurnExecutionOwner,
    build_branch_turn_execution_identity,
)
from api_service.services.remediation_actions import TemporalRemediationControlPlane
from moonmind.workflows.temporal.remediation_actions import remediation_parameter_digest
from moonmind.workflows.temporal.remediation_tools import (
    MoonMindControlPlaneRemediationActionExecutor,
    RemediationEvidenceToolService,
)
from tests.unit.workflows.temporal.test_remediation_context import (
    _fast_verification_phase,
    _read_artifact_json,
    temporal_db,
)
from tests.unit.workflows.temporal.test_remediation_issue_3622 import (
    _prepare,
)
from tests.unit.workflows.temporal.test_remediation_issue_3622 import (
    mock_client_adapter as _mock_client_adapter_fixture,
)

mock_client_adapter = _mock_client_adapter_fixture

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize("lost_result_ack", [False, True])
@pytest.mark.parametrize("claim_read_unavailable", [False, True])
async def test_lost_branch_start_ack_preserves_exact_pending_result(
    tmp_path, mock_client_adapter, monkeypatch, claim_read_unavailable, lost_result_ack
):
    async with temporal_db(tmp_path) as session:
        target, remediation, artifacts, authority, guard = await _prepare(
            session,
            tmp_path,
            mock_client_adapter,
            "checkpoint_branch.create_from_remediation_context",
            branch_instruction=b"Preserve the saved repair and resolve the remaining failure.",
        )
        target.state = models.MoonMindWorkflowState.FAILED
        await session.commit()
        source_workflow_id, source_run_id = target.workflow_id, target.run_id
        branches = CheckpointBranchService(session)
        accepted_starts = []
        admitted_params = authority["request"]["params"]

        async def accept_then_lose_ack(**kwargs):
            assert kwargs["input_args"]["instructionRef"] == admitted_params["instructionRef"]
            assert kwargs["input_args"]["agentRequest"]["instructionRef"] == admitted_params["instructionRef"]
            assert kwargs["input_args"]["sourceCheckpointRef"] == admitted_params["checkpointRef"]
            accepted_starts.append(kwargs["workflow_id"])
            raise TimeoutError("Temporal accepted the start; response was lost")

        start_owner = CheckpointBranchTurnExecutionOwner(
            session,
            principal="service:remediation-context",
            client=SimpleNamespace(start_workflow=accept_then_lose_ack),
            artifact_service=artifacts,
        )
        # Start at the production owner's admitted manifest boundary. Keep the
        # real instruction admission, durable claim, and start exception wrapper.
        admitted_identities = []

        class AdmittedBranchOwner:
            async def launch(self, *, workflow_id, branch_id, branch_turn_id, intent):
                branch = await session.get(models.WorkflowCheckpointBranch, branch_id)
                turn = await session.get(models.WorkflowCheckpointBranchTurn, branch_turn_id)
                identity = build_branch_turn_execution_identity(
                    branch_id=branch_id,
                    branch_turn_id=branch_turn_id,
                    logical_step_id=branch.logical_step_id,
                    ordinal=1,
                )
                admitted_identities.append(identity)
                assert turn.instruction_ref == admitted_params["instructionRef"]
                assert turn.instruction_digest == admitted_params["instructionDigest"]
                binding = await session.get(models.WorkflowCheckpointBranchGitBinding, branch_id)
                assert binding.repository == "MoonLadderStudios/MoonMind"
                assert binding.base_branch == "feature/repair-base"
                assert binding.work_branch == "repair/retained-work"
                launch_key = build_branch_turn_launch_idempotency_key(
                    workflow_id=workflow_id,
                    branch_id=branch_id,
                    branch_turn_id=branch_turn_id,
                )
                manifest_ref = await start_owner._write_artifact(
                    content_type="application/json",
                    kind="runtime.branch_turn.step_execution_manifest.json",
                    branch_turn_id=branch_turn_id,
                    payload=json.dumps(
                        {
                            "agentExecutionRequest": {
                                "agentKind": "external",
                                "agentId": "omnigent",
                                "correlationId": branch_turn_id,
                                "idempotencyKey": launch_key,
                                "instructionRef": turn.instruction_ref,
                                "stepExecution": {
                                    "workflowId": identity.workflow_id,
                                    "runId": identity.semantic_run_id,
                                    "logicalStepId": identity.logical_step_id,
                                    "executionOrdinal": identity.execution_ordinal,
                                    "stepExecutionId": identity.step_execution_id,
                                    "reason": "checkpoint_branch",
                                    "runtimeContextPolicy": "fresh_agent_run",
                                    "preparedInputRefs": [turn.instruction_ref],
                                    "branch": {
                                        "branchId": branch_id,
                                        "branchTurnId": branch_turn_id,
                                    },
                                },
                                "workspaceSpec": {"workspaceLocator": {"kind": "sandbox"}},
                            }
                        }
                    ).encode(),
                )
                turn = await branches.claim_turn_execution(
                    workflow_id=workflow_id,
                    branch_id=branch_id,
                    branch_turn_id=branch_turn_id,
                    context_bundle_ref="artifact://context",
                    step_execution_manifest_ref=manifest_ref,
                    diagnostics_ref="artifact://launch-diagnostics",
                    launch_idempotency_key=launch_key,
                    created_step_execution_id=identity.step_execution_id,
                    runtime_agent_run_id=identity.agent_run_workflow_id,
                    agent_request_ref="artifact://request",
                    execution_workflow_id=identity.workflow_id,
                )
                await session.commit()
                await start_owner._start_claimed_turn(
                    branch=await session.get(
                        models.WorkflowCheckpointBranch, branch_id
                    ),
                    turn=turn,
                    binding=await session.get(
                        models.WorkflowCheckpointBranchGitBinding, branch_id
                    ),
                )
                return turn

        if claim_read_unavailable:
            branches.read_branch_graph = AsyncMock(
                side_effect=ConnectionError("claim read temporarily unavailable")
            )
        plane = TemporalRemediationControlPlane(
            checkpoint_branch_service=branches,
            checkpoint_branch_turn_owner=AdmittedBranchOwner(),
        )
        executor = MoonMindControlPlaneRemediationActionExecutor(plane.handlers())

        def tools():
            return RemediationEvidenceToolService(
                session=session,
                artifact_service=artifacts,
                action_executor=executor,
                verification_phase=_fast_verification_phase(session),
            )

        kwargs = {
            "remediation_workflow_id": remediation.workflow_id,
            "authority_result": authority,
            "guard_result": guard,
            "principal": "service:test",
            "admitted_principal": "service:remediation-context",
        }
        first_tools = tools()
        if lost_result_ack:
            publish = first_tools._lifecycle_publisher.publish_json_artifact

            async def lose_result_ack(**publication):
                artifact = await publish(**publication)
                if publication["artifact_type"] == "remediation.action_result":
                    await session.commit()
                    raise RuntimeError("lost result receipt acknowledgment")
                return artifact

            monkeypatch.setattr(
                first_tools._lifecycle_publisher, "publish_json_artifact", lose_result_ack
            )
            with pytest.raises(RuntimeError, match="lost result receipt acknowledgment"):
                await first_tools.execute_action(**kwargs)
            session.expire_all()
            result = await tools().execute_action(**kwargs)
        else:
            result = await first_tools.execute_action(**kwargs)
        assert len(admitted_identities) == 1
        assert accepted_starts == [admitted_identities[0].workflow_id], result
        assert result["verification"]["deliveryStatus"] == "delivery_unknown"
        assert result["verification"]["pending"]
        assert result["verification"]["outcome"] is None
        identity = result["verification"]["resultingIdentity"]
        assert identity["stepExecutionId"] == admitted_identities[0].step_execution_id
        assert identity["workflowId"] == source_workflow_id
        assert identity["runId"] == source_run_id
        request_receipt = await _read_artifact_json(
            artifacts, result["artifactRefs"]["actionRequest"]
        )
        assert request_receipt["params"] == admitted_params
        original_result = await _read_artifact_json(
            artifacts, result["artifactRefs"]["actionResult"]
        )
        assert original_result["status"] == "delivery_unknown"
        assert (
            original_result["resultingIdentity"]["stepExecutionId"]
            == identity["stepExecutionId"]
        )
        assert not original_result.get("branchTurnLaunched")
        if claim_read_unavailable:
            branches.read_branch_graph.assert_not_awaited()

        # Reconstruct the service after losing caller state. Observation retries
        # must not enter the launch owner again or turn uncertainty into a no-op.
        session.expire_all()
        repeated = await tools().execute_action(**kwargs)
        assert repeated["verification"]["pending"]
        assert accepted_starts == [admitted_identities[0].workflow_id]
        monkeypatch.setattr(
            "moonmind.workflows.get_temporal_artifact_service",
            lambda _session: artifacts,
        )
        # The existing terminal owner resumes verification without a browser or
        # another action request. A failed repair stays failed, not no-change.
        await CheckpointBranchService(session).finalize_turn_execution(
            workflow_id=source_workflow_id,
            branch_id=identity["branchId"],
            branch_turn_id=identity["branchTurnId"],
            outcome="failed",
            agent_result_ref="artifact://failed-repair",
            diagnostics_ref="artifact://repair-diagnostics",
        )
        link = await session.get(
            models.TemporalExecutionRemediationLink, kwargs["remediation_workflow_id"]
        )
        await session.refresh(link)
        saved = link.mutation_guard_ledger_state["entries"]["repair-3622"]["execution"]
        assert saved["verificationState"] == "complete"
        assert saved["parameterDigest"] == remediation_parameter_digest(admitted_params)
        assert (
            saved["verificationResponse"]["verification"]["outcome"] == "still_failed"
        )
        assert accepted_starts == [admitted_identities[0].workflow_id]
        source = await session.get(
            models.TemporalExecutionCanonicalRecord, source_workflow_id
        )
        assert source.state == models.MoonMindWorkflowState.FAILED
        assert source.run_id == source_run_id
